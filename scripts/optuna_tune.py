"""
Optuna Hyperparameter Tuning for 10-Model Ensemble — Tier 3 #32

Run offline (on VPS or local) to auto-tune model hyperparameters.
Usage: python scripts/optuna_tune.py

Tunes: n_estimators, max_depth, learning_rate, min_samples_split for each model.
Objective: minimize Brier score on temporal validation split.
"""
import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from structlog import get_logger

logger = get_logger()


def create_study():
    try:
        import optuna
    except ImportError:
        print("optuna not installed. Run: pip install optuna")
        sys.exit(1)

    study = optuna.create_study(
        direction="minimize",
        study_name="polymarket_ensemble_v1",
        storage=None,  # In-memory; use sqlite for persistence
    )
    return study


def objective(trial):
    """Optuna objective: train ensemble with trial params, return Brier score."""
    import numpy as np

    # Hyperparameters to tune
    params = {
        "rf_n_estimators": trial.suggest_int("rf_n_estimators", 50, 300),
        "rf_max_depth": trial.suggest_int("rf_max_depth", 3, 15),
        "xgb_n_estimators": trial.suggest_int("xgb_n_estimators", 50, 300),
        "xgb_learning_rate": trial.suggest_float("xgb_learning_rate", 0.01, 0.3, log=True),
        "xgb_max_depth": trial.suggest_int("xgb_max_depth", 3, 10),
        "lgbm_n_estimators": trial.suggest_int("lgbm_n_estimators", 50, 300),
        "lgbm_learning_rate": trial.suggest_float("lgbm_learning_rate", 0.01, 0.3, log=True),
        "lgbm_num_leaves": trial.suggest_int("lgbm_num_leaves", 15, 63),
        "hgb_max_iter": trial.suggest_int("hgb_max_iter", 50, 300),
        "hgb_max_depth": trial.suggest_int("hgb_max_depth", 3, 12),
        "knn_n_neighbors": trial.suggest_int("knn_n_neighbors", 3, 15),
        "ensemble_blend": trial.suggest_float("ensemble_blend", 0.5, 0.95),
    }

    try:
        from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
        from sklearn.ensemble import HistGradientBoostingClassifier, ExtraTreesClassifier
        from sklearn.linear_model import LogisticRegression, RidgeClassifier
        from sklearn.neighbors import KNeighborsClassifier
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import brier_score_loss

        # Load cached training data
        import pickle
        cache_path = Path(__file__).resolve().parent.parent / "data" / "training_data_cache.pkl"
        if not cache_path.exists():
            logger.warning("No training data cache found at %s — run main.py first to generate", cache_path)
            return 1.0

        with open(cache_path, "rb") as f:
            data = pickle.load(f)

        X = np.array(data.get("X", []))
        y = np.array(data.get("y", []))

        if len(X) < 100:
            return 1.0

        # Temporal split (80/20)
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        scaler = StandardScaler()
        X_train = scaler.fit_transform(X_train)
        X_test = scaler.transform(X_test)

        # Build models with trial params
        models = {
            "rf": RandomForestClassifier(
                n_estimators=params["rf_n_estimators"],
                max_depth=params["rf_max_depth"],
                class_weight="balanced", n_jobs=-1, random_state=42,
            ),
            "xgb": None,  # Conditional import
            "hgb": HistGradientBoostingClassifier(
                max_iter=params["hgb_max_iter"],
                max_depth=params["hgb_max_depth"],
                class_weight="balanced", random_state=42,
            ),
            "knn": KNeighborsClassifier(
                n_neighbors=params["knn_n_neighbors"],
                weights="distance",
            ),
        }

        try:
            import xgboost as xgb
            models["xgb"] = xgb.XGBClassifier(
                n_estimators=params["xgb_n_estimators"],
                learning_rate=params["xgb_learning_rate"],
                max_depth=params["xgb_max_depth"],
                eval_metric="logloss", use_label_encoder=False,
                random_state=42, n_jobs=-1,
            )
        except ImportError:
            pass

        try:
            import lightgbm as lgb
            models["lgbm"] = lgb.LGBMClassifier(
                n_estimators=params["lgbm_n_estimators"],
                learning_rate=params["lgbm_learning_rate"],
                num_leaves=params["lgbm_num_leaves"],
                is_unbalance=True, random_state=42, n_jobs=-1, verbose=-1,
            )
        except ImportError:
            pass

        # Train and predict
        predictions = []
        for name, model in models.items():
            if model is None:
                continue
            try:
                model.fit(X_train, y_train)
                if hasattr(model, "predict_proba"):
                    pred = model.predict_proba(X_test)[:, 1]
                else:
                    pred = np.full(len(X_test), 0.5)
                predictions.append(pred)
            except Exception:
                continue

        if not predictions:
            return 1.0

        # Ensemble average
        ensemble_pred = np.mean(predictions, axis=0)
        brier = brier_score_loss(y_test, ensemble_pred)

        return brier

    except Exception as e:
        logger.warning("Optuna trial failed: %s", e)
        return 1.0


def main():
    study = create_study()
    n_trials = 50
    print(f"Running {n_trials} Optuna trials...")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    print(f"\nBest Brier score: {study.best_value:.4f}")
    print(f"Best params: {study.best_params}")

    # Save best params
    import json
    out_path = Path(__file__).resolve().parent.parent / "data" / "optuna_best_params.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"best_brier": study.best_value, "best_params": study.best_params}, f, indent=2)
    print(f"Best params saved to {out_path}")


if __name__ == "__main__":
    main()

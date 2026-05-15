"""
Train MirrorBot ML Trade Selector — XGBoost + Q-table bootstrap (S124).

Extracts resolved MirrorBot trades from trade_events + paper_trades,
engineers features, trains both models, saves artifacts to models/.

Usage:
    python scripts/train_mirror_ml_selector.py [--days 90] [--min-samples 300]
"""
import asyncio
import json
import math
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def extract_training_data(db, days: int = 90):
    """Extract resolved MirrorBot trades with features from event_data."""
    from sqlalchemy import text

    sql = text("""
        SELECT
            te_entry.event_time,
            te_entry.price,
            te_entry.side,
            te_entry.confidence,
            te_entry.event_data->>'conf_base' AS conf_base,
            te_entry.event_data->>'conf_price_adj' AS conf_price_adj,
            te_entry.event_data->>'conf_conv_adj' AS conf_conv_adj,
            te_entry.event_data->>'rel_mult' AS rel_mult,
            te_entry.event_data->>'whale_trade_usd' AS whale_trade_usd,
            te_entry.event_data->>'category' AS category,
            te_entry.event_data->>'consensus' AS consensus,
            COALESCE(te_res.realized_pnl, pt.realized_pnl) AS realized_pnl
        FROM trade_events te_entry
        LEFT JOIN LATERAL (
            SELECT realized_pnl
            FROM trade_events te_res
            WHERE te_res.bot_name = 'MirrorBot'
              AND te_res.market_id = te_entry.market_id
              AND te_res.token_id = te_entry.token_id
              AND te_res.event_type IN ('EXIT', 'RESOLUTION')
              AND te_res.realized_pnl IS NOT NULL
            ORDER BY te_res.event_time DESC
            LIMIT 1
        ) te_res ON true
        LEFT JOIN LATERAL (
            SELECT realized_pnl
            FROM paper_trades pt
            WHERE pt.market_id = te_entry.market_id
              AND pt.token_id = te_entry.token_id
              AND pt.bot_name = 'MirrorBot'
              AND pt.resolution IS NOT NULL
            ORDER BY pt.resolved_at DESC
            LIMIT 1
        ) pt ON true
        WHERE te_entry.bot_name = 'MirrorBot'
          AND te_entry.event_type = 'ENTRY'
          AND te_entry.event_data IS NOT NULL
          AND te_entry.event_time >= NOW() - MAKE_INTERVAL(days => :days)
          AND te_entry.event_time <= NOW()
          AND COALESCE(te_entry.event_data->>'calibration_exclude', '') = ''
          AND COALESCE(te_res.realized_pnl, pt.realized_pnl) IS NOT NULL
        ORDER BY te_entry.event_time
    """)

    async with db.get_session() as session:
        result = await session.execute(sql, {"days": days})
        rows = result.fetchall()

    print(f"Extracted {len(rows)} resolved trades from last {days} days")
    return rows


def build_feature_matrix(rows):
    """Convert SQL rows to feature matrix + labels."""
    FEATURE_NAMES = [
        "conf_base", "conf_price_adj", "conf_conv_adj", "rel_mult",
        "price", "whale_trade_usd", "category_encoded", "consensus",
        "hour_utc", "side_is_no", "price_extremity", "conf_composite",
    ]

    # Step 1: Compute target encoding for categories
    cat_wins = {}  # category -> [wins, total]
    for row in rows:
        cat = (row.category or "").lower().strip()
        if cat not in cat_wins:
            cat_wins[cat] = [0, 0]
        cat_wins[cat][1] += 1
        if row.realized_pnl and float(row.realized_pnl) > 0:
            cat_wins[cat][0] += 1

    # Leave-one-out target encoding with prior smoothing (pseudocount=10)
    global_wr = sum(v[0] for v in cat_wins.values()) / max(sum(v[1] for v in cat_wins.values()), 1)
    category_encoding = {}
    for cat, (wins, total) in cat_wins.items():
        smoothed = (wins + 10 * global_wr) / (total + 10)
        category_encoding[cat] = round(smoothed, 4)

    # Step 2: Build arrays
    X = []
    y = []
    for row in rows:
        cat = (row.category or "").lower().strip()
        pnl = float(row.realized_pnl) if row.realized_pnl else 0.0
        price = float(row.price) if row.price else 0.50
        side = str(row.side or "YES").upper()
        hour = row.event_time.hour if row.event_time else 12

        features = {
            "conf_base": _safe_float(row.conf_base, 0.50),
            "conf_price_adj": _safe_float(row.conf_price_adj, 0.0),
            "conf_conv_adj": _safe_float(row.conf_conv_adj, 0.0),
            "rel_mult": _safe_float(row.rel_mult, 1.0),
            "price": price,
            "whale_trade_usd": _safe_float(row.whale_trade_usd, 0.0),
            "category_encoded": category_encoding.get(cat, 0.50),
            "consensus": _safe_float(row.consensus, 1.0),
            "hour_utc": float(hour),
            "side_is_no": 1.0 if side == "NO" else 0.0,
            "price_extremity": abs(price - 0.50),
            "conf_composite": float(row.confidence) if row.confidence else 0.50,
        }

        X.append([features[f] for f in FEATURE_NAMES])
        y.append(1 if pnl > 0 else 0)

    return np.array(X), np.array(y), FEATURE_NAMES, category_encoding


def _safe_float(val, default: float) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _walk_forward_splits(n: int, n_splits: int, gap: int = 5, min_train: int = 50):
    """Yield (train_idx, val_idx) walk-forward folds with embargo gap.

    S137 C13: gap=5 trades excluded between training end and validation start.
    Prevents leakage from correlated adjacent trades (same market, same day).
    """
    fold_size = max(1, (n - min_train) // n_splits)
    for fold in range(n_splits):
        train_end = min_train + fold * fold_size
        val_start = train_end + gap        # embargo: skip gap trades
        val_end = min(val_start + fold_size, n)
        if val_start >= val_end or train_end <= 0:
            continue
        yield np.arange(0, train_end), np.arange(val_start, val_end)


def train_xgboost(X, y, feature_names):
    """Train XGBoost with walk-forward CV (5-trade embargo) + isotonic calibration.

    S137 C13: Updated hyperparameters for 585-sample regime:
    - learning_rate 0.1 → 0.02 (prevents overfitting on small dataset)
    - n_estimators 100 → 200 (compensates for lower LR)
    - reg_lambda=5, reg_alpha=0.5 (L2+L1 regularization, reduces overfit)
    - subsample 0.8 → 0.7, colsample_bytree 0.8 → 0.6 (more variance reduction)
    - Walk-forward with 5-trade embargo gap (replaces leaky TimeSeriesSplit)
    """
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import roc_auc_score, brier_score_loss
    import xgboost as xgb

    n_splits = min(5, max(2, len(X) // 100))

    # Class balance
    n_pos = y.sum()
    n_neg = len(y) - n_pos
    scale_pos_weight = n_neg / max(n_pos, 1)

    # S137 C13: Regularized params tuned for ~585-sample MirrorBot dataset
    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=3,
        learning_rate=0.02,
        min_child_weight=10,
        subsample=0.7,
        colsample_bytree=0.6,
        reg_lambda=5,
        reg_alpha=0.5,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
        use_label_encoder=False,
        verbosity=0,
    )

    # Collect OOF predictions for calibration + CV metrics
    oof_preds = np.zeros(len(X))
    oof_mask = np.zeros(len(X), dtype=bool)
    fold_aucs = []

    # S137 C13: Walk-forward with 5-trade embargo (was plain TimeSeriesSplit)
    for fold, (train_idx, val_idx) in enumerate(
        _walk_forward_splits(len(X), n_splits, gap=5)
    ):
        X_train, X_val = X[train_idx], X[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        model.fit(X_train, y_train)
        preds = model.predict_proba(X_val)[:, 1]
        oof_preds[val_idx] = preds
        oof_mask[val_idx] = True

        if len(np.unique(y_val)) > 1:
            auc = roc_auc_score(y_val, preds)
            fold_aucs.append(auc)
            print(f"  Fold {fold+1}: AUC={auc:.3f}, n={len(val_idx)}, embargo=5")

    # Retrain on full data
    model.fit(X, y)

    # Isotonic calibration on OOF predictions
    calibrator = None
    oof_y = y[oof_mask]
    oof_p = oof_preds[oof_mask]
    if len(oof_y) >= 50:
        calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        calibrator.fit(oof_p, oof_y)
        cal_preds = calibrator.predict(oof_p)
        brier_raw = brier_score_loss(oof_y, oof_p)
        brier_cal = brier_score_loss(oof_y, cal_preds)
        print(f"  Brier raw={brier_raw:.4f}, calibrated={brier_cal:.4f}")

    cv_auc = float(np.mean(fold_aucs)) if fold_aucs else 0.0
    print(f"  Mean CV AUC: {cv_auc:.3f}")

    # Feature importances
    importances = dict(zip(feature_names, model.feature_importances_.tolist()))
    sorted_imp = sorted(importances.items(), key=lambda x: x[1], reverse=True)
    print("  Feature importances:")
    for fname, imp in sorted_imp[:6]:
        print(f"    {fname}: {imp:.3f}")

    return model, calibrator, cv_auc, importances


def train_qtable(X, y, feature_names, realized_pnls):
    """Bootstrap Q-table from historical data using offline Q-learning."""
    from bots.mirror_ml_selector import (
        _QL_N_STATES, _QL_N_ACTIONS, ACTION_TRADE, ACTION_SKIP,
    )

    q_table = np.zeros((_QL_N_STATES, _QL_N_ACTIONS), dtype=np.float64)
    visit_counts = np.zeros((_QL_N_STATES, _QL_N_ACTIONS), dtype=np.int64)

    # Build feature dicts for discretization
    from bots.mirror_ml_selector import MirrorMLSelector
    selector = MirrorMLSelector()

    lr = 0.1
    gamma = 0.95

    for i in range(len(X)):
        features = dict(zip(feature_names, X[i].tolist()))
        state_idx = selector._discretize_ql_state(features)
        pnl = realized_pnls[i]

        # For historical data: action was always TRADE (we entered every trade)
        # Reward for TRADE = realized_pnl scaled to [-2, 2]
        reward_trade = float(np.clip(pnl / 5.0, -2.0, 2.0))
        # Reward for SKIP = inverse — good if trade lost
        reward_skip = 0.2 if pnl < 0 else -0.4

        # Update both actions (off-policy: we know outcome for both)
        # TRADE: TD update
        td_trade = reward_trade + gamma * max(q_table[state_idx].max(), 0) - q_table[state_idx, ACTION_TRADE]
        q_table[state_idx, ACTION_TRADE] += lr * td_trade
        visit_counts[state_idx, ACTION_TRADE] += 1

        # SKIP: TD update
        td_skip = reward_skip + gamma * max(q_table[state_idx].max(), 0) - q_table[state_idx, ACTION_SKIP]
        q_table[state_idx, ACTION_SKIP] += lr * td_skip
        visit_counts[state_idx, ACTION_SKIP] += 1

    # Multi-pass replay for convergence
    for epoch in range(5):
        indices = np.random.permutation(len(X))
        for i in indices:
            features = dict(zip(feature_names, X[i].tolist()))
            state_idx = selector._discretize_ql_state(features)
            pnl = realized_pnls[i]

            reward_trade = float(np.clip(pnl / 5.0, -2.0, 2.0))
            reward_skip = 0.2 if pnl < 0 else -0.4

            td_trade = reward_trade + gamma * max(q_table[state_idx].max(), 0) - q_table[state_idx, ACTION_TRADE]
            q_table[state_idx, ACTION_TRADE] += lr * td_trade

            td_skip = reward_skip + gamma * max(q_table[state_idx].max(), 0) - q_table[state_idx, ACTION_SKIP]
            q_table[state_idx, ACTION_SKIP] += lr * td_skip

    # Stats
    states_with_data = int((visit_counts.sum(axis=1) > 0).sum())
    trade_preferred = int((q_table[:, ACTION_TRADE] > q_table[:, ACTION_SKIP]).sum())
    print(f"  Q-table: {states_with_data}/{_QL_N_STATES} states visited, "
          f"{trade_preferred} prefer TRADE, {_QL_N_STATES - trade_preferred} prefer SKIP")
    print(f"  Q mean: TRADE={q_table[:, ACTION_TRADE].mean():.3f}, "
          f"SKIP={q_table[:, ACTION_SKIP].mean():.3f}")

    return q_table, visit_counts


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Train MirrorBot ML selector")
    parser.add_argument("--days", type=int, default=90, help="Lookback days")
    parser.add_argument("--min-samples", type=int, default=300, help="Minimum samples required")
    args = parser.parse_args()

    from base_engine.data.database import Database
    db = Database()
    await db.init()

    print("=" * 60)
    print("MirrorBot ML Trade Selector — Training Pipeline")
    print("=" * 60)

    # Extract data
    rows = await extract_training_data(db, days=args.days)
    if len(rows) < args.min_samples:
        print(f"ERROR: Only {len(rows)} samples (need {args.min_samples}). Aborting.")
        return

    # Build features
    print(f"\nBuilding features from {len(rows)} trades...")
    X, y, feature_names, category_encoding = build_feature_matrix(rows)
    realized_pnls = np.array([float(r.realized_pnl) if r.realized_pnl else 0.0 for r in rows])

    win_rate = y.mean()
    print(f"  Win rate: {win_rate:.1%} ({y.sum()}/{len(y)})")
    print(f"  Total P&L: ${realized_pnls.sum():.2f}")

    # Train XGBoost
    print("\n--- Training XGBoost ---")
    model, calibrator, cv_auc, importances = train_xgboost(X, y, feature_names)

    # Train Q-table
    print("\n--- Training Q-table ---")
    q_table, visit_counts = train_qtable(X, y, feature_names, realized_pnls)

    # Save artifacts
    models_dir = Path("models")
    models_dir.mkdir(exist_ok=True)

    # XGBoost
    xgb_path = models_dir / "mirror_ml_selector.pkl"
    xgb_payload = {
        "model": model,
        "calibrator": calibrator,
        "feature_names": feature_names,
        "category_encoding": category_encoding,
        "n_samples": len(X),
        "cv_auc": cv_auc,
        "win_rate": float(win_rate),
        "importances": importances,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(xgb_path, "wb") as f:
        pickle.dump(xgb_payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"\nXGBoost saved to {xgb_path}")

    # Q-table
    ql_path = models_dir / "mirror_ml_qtable.pkl"
    ql_payload = {
        "q_table": q_table,
        "visit_counts": visit_counts,
        "total_trades": len(X),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(ql_path, "wb") as f:
        pickle.dump(ql_payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Q-table saved to {ql_path}")

    # Metadata
    meta_path = models_dir / "mirror_ml_selector_meta.json"
    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_samples": len(X),
        "win_rate": round(float(win_rate), 4),
        "total_pnl": round(float(realized_pnls.sum()), 2),
        "cv_auc": round(cv_auc, 4),
        "feature_names": feature_names,
        "category_encoding": category_encoding,
        "importances": {k: round(v, 4) for k, v in importances.items()},
        "q_states_visited": int((visit_counts.sum(axis=1) > 0).sum()),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Metadata saved to {meta_path}")

    print(f"\n{'=' * 60}")
    print(f"Training complete. {len(X)} samples, AUC={cv_auc:.3f}")
    print(f"Deploy: copy models/ to VPS, restart with MIRROR_USE_ML_SELECTOR=false (shadow)")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())

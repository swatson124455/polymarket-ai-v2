"""
Deep diagnostic: Why are zero paper trades being created?
Tests each layer of the trade pipeline independently.
"""
import asyncio
import os
import sys
import pickle
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings, settings

async def main():
    print("=" * 60)
    print("PAPER TRADE PIPELINE DIAGNOSTIC")
    print("=" * 60)

    # ---------- Layer 1: Settings ----------
    print("\n--- Layer 1: Settings ---")
    print(f"  SIMULATION_MODE: {settings.SIMULATION_MODE}")
    print(f"  LIVE_TRADING: {settings.LIVE_TRADING}")
    print(f"  ENSEMBLE_MIN_CONFIDENCE: {settings.ENSEMBLE_MIN_CONFIDENCE}")
    print(f"  RISK_MIN_EDGE_PCT: {settings.RISK_MIN_EDGE_PCT}")
    ens_cool = getattr(settings, "ENSEMBLE_WS_COOLDOWN_SECONDS", "N/A")
    print(f"  ENSEMBLE_WS_COOLDOWN_SECONDS: {ens_cool}")
    ws_pct = getattr(settings, "ENSEMBLE_WS_PRICE_CHANGE_PCT", "N/A")
    print(f"  ENSEMBLE_WS_PRICE_CHANGE_PCT: {ws_pct}")

    # ---------- Layer 2: Model Cache ----------
    print("\n--- Layer 2: Model Cache ---")
    cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "model_cache.pkl")
    if not os.path.exists(cache_path):
        print("  BLOCKER: model_cache.pkl does NOT exist — no trained models!")
        return

    age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
    print(f"  Cache exists: {os.path.getsize(cache_path):,} bytes, {age_hours:.1f}h old")

    try:
        with open(cache_path, "rb") as f:
            cache = pickle.load(f)
        print(f"  Cache type: {type(cache).__name__}")
        if isinstance(cache, dict):
            print(f"  Cache keys: {list(cache.keys())}")
            models = cache.get("models", {})
            print(f"  Models loaded: {len(models)}")
            for name, model in models.items():
                print(f"    - {name}: {type(model).__name__}")
            feature_cols = cache.get("feature_columns", [])
            print(f"  Feature columns: {len(feature_cols)}")
            if feature_cols:
                print(f"    First 5: {feature_cols[:5]}")
            scaler = cache.get("scaler")
            print(f"  Scaler: {type(scaler).__name__ if scaler else 'None'}")
            model_weights = cache.get("model_weights", {})
            print(f"  Model weights: {model_weights}")
            brier = cache.get("brier_score")
            print(f"  Brier score: {brier}")
            version = cache.get("version")
            print(f"  Version: {version}")
        else:
            print(f"  WARNING: cache is not a dict, it's {type(cache).__name__}")
    except Exception as e:
        print(f"  BLOCKER: Cannot load model cache: {e}")
        return

    # ---------- Layer 3: DB Connection ----------
    print("\n--- Layer 3: Database ---")
    from base_engine.data.database import Database
    db = Database()
    try:
        await db.init()
        print("  DB connection: OK")
    except Exception as e:
        print(f"  BLOCKER: DB connection failed: {e}")
        return

    # ---------- Layer 4: Prediction Engine ----------
    print("\n--- Layer 4: Prediction Engine ---")
    try:
        from base_engine.prediction.prediction_engine import PredictionEngine
        pe = PredictionEngine(db)
        await pe.init()
        print(f"  pe.initialized: {pe.initialized}")
        print(f"  pe.models: {len(pe.models) if pe.models else 'None'}")
        if pe.models:
            for name in pe.models:
                print(f"    - {name}")
        print(f"  pe.feature_columns: {len(pe.feature_columns) if pe.feature_columns else 'None'}")
        print(f"  pe.scaler: {'yes' if pe.scaler else 'no'}")

        if not pe.initialized or not pe.models:
            print("  BLOCKER: Prediction engine not ready!")
    except Exception as e:
        print(f"  ERROR initializing prediction engine: {e}")
        import traceback
        traceback.print_exc()

    # ---------- Layer 5: Test prediction on a real market ----------
    print("\n--- Layer 5: Test Prediction ---")
    try:
        from sqlalchemy import text
        session_ctx = db.get_session()
        async with session_ctx as session:
            # Get an active market
            result = await session.execute(text(
                "SELECT id, question, condition_id FROM markets "
                "WHERE active = true AND closed = false "
                "ORDER BY volume DESC LIMIT 3"
            ))
            rows = result.fetchall()
            if not rows:
                print("  BLOCKER: No active markets in DB!")
            else:
                for row in rows:
                    market_id = str(row[0])
                    question = row[1][:60] if row[1] else "?"
                    print(f"\n  Testing market: {market_id} ({question}...)")

                    try:
                        pred = await pe.predict(market_id, price=0.5)
                        if pred:
                            prob = pred.get("predicted_probability", "?")
                            conf = pred.get("confidence", "?")
                            model_preds = pred.get("model_predictions", {})
                            print(f"    predicted_probability: {prob}")
                            print(f"    confidence: {conf}")
                            print(f"    model_predictions: {len(model_preds)} models")
                            for mn, mp in model_preds.items():
                                print(f"      {mn}: {mp}")

                            # Check if this would pass EnsembleBot's gate
                            if isinstance(conf, (int, float)):
                                threshold = settings.ENSEMBLE_MIN_CONFIDENCE
                                if conf >= threshold:
                                    print(f"    WOULD PASS confidence gate ({conf:.4f} >= {threshold})")
                                else:
                                    print(f"    BLOCKED by confidence gate ({conf:.4f} < {threshold})")
                        else:
                            print(f"    predict() returned None/empty!")
                    except Exception as e:
                        print(f"    predict() FAILED: {e}")
    except Exception as e:
        print(f"  ERROR testing predictions: {e}")
        import traceback
        traceback.print_exc()

    # ---------- Layer 6: Paper Trading Engine ----------
    print("\n--- Layer 6: Paper Trading Engine ---")
    try:
        from base_engine.execution.paper_trading import PaperTradingEngine
        pte = PaperTradingEngine(db=db)
        print(f"  enabled: {pte.enabled}")
        print(f"  cash: {pte.cash}")
        print(f"  positions: {len(pte.positions)}")
        if not pte.enabled:
            print("  BLOCKER: PaperTradingEngine is NOT enabled!")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ---------- Summary ----------
    print("\n" + "=" * 60)
    print("SUMMARY OF BLOCKERS")
    print("=" * 60)

    try:
        await db.close()
    except Exception:
        pass

if __name__ == "__main__":
    asyncio.run(main())

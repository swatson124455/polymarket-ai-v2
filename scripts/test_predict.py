"""Test if prediction engine can actually predict on a live market."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from base_engine.data.database import Database
from base_engine.prediction.prediction_engine import PredictionEngine
from base_engine.learning.learning_engine import LearningEngine

async def main():
    print("=== PREDICTION ENGINE TEST ===")

    db = Database()
    await db.init()
    print(f"DB: OK")

    le = LearningEngine(db)
    pe = PredictionEngine(db, le)
    await pe.init()
    print(f"PE initialized: {pe.initialized}")
    print(f"PE models: {len(pe.models) if pe.models else 'None'}")
    print(f"PE feature_columns: {len(pe.feature_columns)} cols")
    if pe.feature_columns:
        print(f"  Columns: {pe.feature_columns}")

    if not pe.initialized or not pe.models:
        print("BLOCKER: Prediction engine not ready!")
        await db.close()
        return

    # Get some active markets from DB
    from sqlalchemy import text
    async with db.get_session() as session:
        result = await session.execute(text(
            "SELECT id, question, condition_id FROM markets "
            "WHERE active = true "
            "ORDER BY volume DESC NULLS LAST LIMIT 5"
        ))
        rows = result.fetchall()

    if not rows:
        print("No active markets found in DB!")
        await db.close()
        return

    print(f"\nTesting predictions on {len(rows)} markets:\n")

    for row in rows:
        market_id = str(row[0])
        question = (row[1] or "?")[:60]
        print(f"Market: {market_id}")
        print(f"  Q: {question}")
        try:
            pred = await pe.predict(market_id, "", 0.5)
            if pred:
                prob = pred.get("predicted_probability", "?")
                conf = pred.get("confidence", "?")
                model_preds = pred.get("model_predictions", {})
                print(f"  predicted_probability: {prob}")
                print(f"  confidence: {conf}")
                print(f"  models: {len(model_preds)}")
                for mn, mp in model_preds.items():
                    print(f"    {mn}: {mp:.4f}")

                threshold = settings.ENSEMBLE_MIN_CONFIDENCE
                if isinstance(conf, (int, float)):
                    if conf >= threshold:
                        print(f"  PASS: confidence {conf:.4f} >= {threshold}")
                    else:
                        print(f"  FAIL: confidence {conf:.4f} < {threshold}")
            else:
                print(f"  predict() returned None!")
        except Exception as e:
            print(f"  predict() ERROR: {e}")
        print()

    await db.close()

asyncio.run(main())

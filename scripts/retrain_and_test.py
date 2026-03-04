"""Retrain models, save cache, then test predictions across diverse markets."""
import asyncio
import io
import os
import sys
import time

# Force UTF-8 on Windows console BEFORE any library imports
os.environ["SIMULATION_MODE"] = "true"
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np


async def main():
    from config.settings import settings
    from base_engine.data.database import Database
    from base_engine.prediction.prediction_engine import PredictionEngine
    from base_engine.learning.learning_engine import LearningEngine

    db = Database()
    await db.init()

    le = LearningEngine(db)
    pe = PredictionEngine(db, le)

    # Step 1: Load or train models
    loaded = pe._load_models_from_file()
    if loaded:
        print(f"Loaded {len(pe.models)} models with {len(pe.feature_columns)} features from cache")
    else:
        print("=" * 80)
        print("Training models (this takes 2-3 minutes)...")
        print("=" * 80)
        t0 = time.time()
        try:
            await pe._train_models()
            pe._save_models_to_file()
            print(f"Training complete in {time.time()-t0:.1f}s")
        except Exception as e:
            import traceback
            print(f"Training FAILED: {e}")
            traceback.print_exc()
            return

    print(f"Models: {list(pe.models.keys())}")
    print(f"Features ({len(pe.feature_columns)})")
    pe.initialized = True

    # Step 2: Test predictions across ALL distinct markets with price data
    from sqlalchemy import text

    print(f"\n{'='*80}")
    print("Testing predictions across ALL markets with price data")
    print(f"{'='*80}")

    async with db.get_session() as session:
        # market_prices.market_id may be m.id (historical ingestion) or m.condition_id (WS streaming)
        r = await session.execute(
            text(
                "SELECT DISTINCT ON (m.id) m.id, mp.price, m.question "
                "FROM market_prices mp "
                "JOIN markets m ON (mp.market_id = m.id OR mp.market_id = m.condition_id) "
                "WHERE m.active = true AND mp.price > 0.05 AND mp.price < 0.95 "
                "ORDER BY m.id, mp.timestamp DESC "
            )
        )
        rows = r.fetchall()
        print(f"Found {len(rows)} markets with prices + market records")

    results = []
    for i, row in enumerate(rows):
        market_id, price, question = row[0], float(row[1]), ""
        try:
            # Safely handle question text for display
            raw_q = row[2] or ""
            question = raw_q[:55].encode("ascii", "replace").decode("ascii")
        except Exception:
            question = "(encoding error)"

        try:
            features = await pe._extract_features(market_id, price, user_address="")
            if features is None:
                print(f"[{i+1:2d}] {question} -> NO FEATURES")
                results.append({"ensemble": None, "error": "no features"})
                continue

            features_scaled = pe.scaler.transform([features])
            preds = {}
            for name, model in pe.models.items():
                try:
                    prob = model.predict_proba(features_scaled)[0]
                    preds[name] = float(prob[1]) if len(prob) >= 2 else float(prob[0])
                except Exception:
                    preds[name] = 0.5

            ens = float(np.mean(list(preds.values())))
            conf = abs(ens - 0.5) * 2 * 100
            std_dev = float(np.std(list(preds.values())))

            # Count non-zero fe_* features
            fe_start = pe.feature_columns.index("fe_current_price") if "fe_current_price" in pe.feature_columns else -1
            fe_nonzero = sum(1 for f in features[fe_start:fe_start + 14] if abs(f) > 1e-9) if fe_start >= 0 else 0

            print(f"[{i+1:2d}] Price={price:.2f} Ens={ens:.4f} Conf={conf:5.1f}% Spread={std_dev:.4f} FE={fe_nonzero:2d}/14 | {question}")

            results.append({
                "market": question, "price": price, "ensemble": ens,
                "confidence": conf, "spread": std_dev, "fe_nonzero": fe_nonzero,
                "preds": preds,
            })
        except Exception as e:
            err_msg = str(e).encode("ascii", "replace").decode("ascii")
            print(f"[{i+1:2d}] {question} -> ERROR: {err_msg[:80]}")
            results.append({"market": question, "ensemble": None, "error": err_msg})

    # Summary
    valid = [r for r in results if r.get("ensemble") is not None]
    errors = [r for r in results if r.get("error")]
    print(f"\n{'='*80}")
    print(f"SUMMARY: {len(valid)} successful / {len(errors)} errors / {len(results)} total")
    print(f"{'='*80}")
    if valid:
        ensembles = [r["ensemble"] for r in valid]
        confidences = [r["confidence"] for r in valid]
        spreads = [r["spread"] for r in valid]
        fe_counts = [r.get("fe_nonzero", 0) for r in valid]
        print(f"  Ensemble range:    {min(ensembles):.4f} - {max(ensembles):.4f}  (mean={np.mean(ensembles):.4f})")
        print(f"  Confidence range:  {min(confidences):.1f}% - {max(confidences):.1f}%  (mean={np.mean(confidences):.1f}%)")
        print(f"  Model spread:      {min(spreads):.4f} - {max(spreads):.4f}  (mean={np.mean(spreads):.4f})")
        print(f"  FE features filled: {min(fe_counts)}/14 - {max(fe_counts)}/14  (mean={np.mean(fe_counts):.1f})")

        bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        print(f"\n  Confidence distribution:")
        for lo, hi in zip(bins, bins[1:]):
            count = sum(1 for c in confidences if lo <= c < hi)
            if count > 0:
                bar = "#" * count
                print(f"    {lo:3d}-{hi:3d}%: {bar} ({count})")

        unique_ens = len(set(round(e, 4) for e in ensembles))
        if unique_ens == 1:
            print(f"\n  FAIL: All {len(valid)} markets have IDENTICAL ensemble prediction!")
        elif unique_ens < len(valid) * 0.5:
            print(f"\n  WARN: Only {unique_ens} unique predictions out of {len(valid)} markets")
        else:
            print(f"\n  PASS: {unique_ens} unique predictions across {len(valid)} markets")

        all_same_side = all(e > 0.5 for e in ensembles) or all(e < 0.5 for e in ensembles)
        if all_same_side and len(valid) > 3:
            side = "YES" if ensembles[0] > 0.5 else "NO"
            print(f"  NOTE: All predictions lean {side} (base rate bias: 66% positive in training data)")
    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for r in errors[:5]:
            print(f"    {r.get('market', '?')[:50]}: {r.get('error', 'unknown')[:60]}")


if __name__ == "__main__":
    asyncio.run(main())

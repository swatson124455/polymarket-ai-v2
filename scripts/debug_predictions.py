"""Debug: verify diverse confidence ranges across different markets."""
import asyncio
import os
import sys
import numpy as np

os.environ["SIMULATION_MODE"] = "true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from config.settings import settings
    from base_engine.data.database import Database
    from base_engine.prediction.prediction_engine import PredictionEngine
    from base_engine.learning.learning_engine import LearningEngine

    db = Database()
    await db.init()

    le = LearningEngine(db)
    pe = PredictionEngine(db, le)
    loaded = pe._load_models_from_file()
    print(f"Cache loaded: {loaded}")
    if not loaded:
        print("ERROR: No model cache found. Run main.py first to train models.")
        return
    print(f"Models: {list(pe.models.keys())}")
    print(f"Features ({len(pe.feature_columns)}): {pe.feature_columns}")
    pe.initialized = True

    from sqlalchemy import text

    # Get 20 DISTINCT active markets with recent prices at varied price levels
    async with db.get_session() as session:
        r = await session.execute(
            text(
                "SELECT DISTINCT ON (mp.market_id) mp.market_id, mp.price, m.question "
                "FROM market_prices mp "
                "JOIN markets m ON m.condition_id = mp.market_id "
                "WHERE m.active = true AND mp.price > 0.05 AND mp.price < 0.95 "
                "ORDER BY mp.market_id, mp.timestamp DESC "
                "LIMIT 20"
            )
        )
        rows = r.fetchall()

    print(f"\n{'='*80}")
    print(f"Testing {len(rows)} distinct markets")
    print(f"{'='*80}")

    results = []
    for i, row in enumerate(rows):
        market_id, price, question = row[0], float(row[1]), (row[2] or "")[:60]
        try:
            features = await pe._extract_features(market_id, price, user_address="")
            if features is None:
                print(f"\n[{i+1:2d}] {question}...")
                print(f"     -> _extract_features returned None!")
                results.append({"market": question, "price": price, "ensemble": None, "error": "no features"})
                continue

            features_scaled = pe.scaler.transform([features])
            preds = {}
            for name, model in pe.models.items():
                try:
                    prob = model.predict_proba(features_scaled)[0]
                    p1 = prob[1] if len(prob) >= 2 else prob[0]
                    preds[name] = p1
                except Exception as e:
                    preds[name] = 0.5  # fallback

            ens = np.mean(list(preds.values()))
            conf = abs(ens - 0.5) * 2 * 100
            std = np.std(list(preds.values()))

            # Count non-zero fe_* features
            fe_start = pe.feature_columns.index("fe_current_price") if "fe_current_price" in pe.feature_columns else -1
            fe_nonzero = 0
            if fe_start >= 0:
                fe_nonzero = sum(1 for f in features[fe_start:fe_start+14] if abs(f) > 1e-9)

            print(f"\n[{i+1:2d}] {question}...")
            print(f"     Price: {price:.2f} | Ensemble: {ens:.4f} | Confidence: {conf:.1f}% | Spread: {std:.4f} | FE features: {fe_nonzero}/14")
            top3 = sorted(preds.items(), key=lambda x: x[1], reverse=True)[:3]
            bot3 = sorted(preds.items(), key=lambda x: x[1])[:3]
            print(f"     Highest: {', '.join(f'{n}={v:.3f}' for n,v in top3)}")
            print(f"     Lowest:  {', '.join(f'{n}={v:.3f}' for n,v in bot3)}")

            results.append({
                "market": question, "price": price, "ensemble": ens,
                "confidence": conf, "spread": std, "fe_nonzero": fe_nonzero,
                "preds": preds,
            })
        except Exception as e:
            print(f"\n[{i+1:2d}] {question}...")
            print(f"     ERROR: {type(e).__name__}: {e}")
            results.append({"market": question, "price": price, "ensemble": None, "error": str(e)})

    # Summary statistics
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

        # Distribution: how many at different confidence levels
        bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        print(f"\n  Confidence distribution:")
        for lo, hi in zip(bins, bins[1:]):
            count = sum(1 for c in confidences if lo <= c < hi)
            bar = "#" * count
            print(f"    {lo:3d}-{hi:3d}%: {bar} ({count})")

        # Check: are all predictions the same?
        unique_ens = len(set(round(e, 4) for e in ensembles))
        if unique_ens == 1:
            print(f"\n  WARNING: All {len(valid)} markets have IDENTICAL ensemble prediction!")
        elif unique_ens < len(valid) * 0.5:
            print(f"\n  WARNING: Only {unique_ens} unique predictions out of {len(valid)} markets")
        else:
            print(f"\n  OK: {unique_ens} unique predictions across {len(valid)} markets")
    if errors:
        print(f"\n  Errors:")
        for r in errors:
            print(f"    {r['market'][:50]}: {r.get('error', 'unknown')}")


if __name__ == "__main__":
    asyncio.run(main())

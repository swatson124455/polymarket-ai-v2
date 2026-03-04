import asyncio, sys, math
sys.path.insert(0, '/opt/polymarket-ai-v2')
import os; os.chdir('/opt/polymarket-ai-v2')

async def main():
    from dotenv import load_dotenv
    load_dotenv('/opt/polymarket-ai-v2/.env')
    import warnings
    warnings.filterwarnings('ignore')
    import structlog
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(40))

    from base_engine.data.database import Database
    from base_engine.learning.learning_engine import LearningEngine
    from base_engine.prediction.prediction_engine import PredictionEngine

    db = Database()
    await db.init()
    le = LearningEngine(db)
    await le.init()
    pe = PredictionEngine(db, le)
    await pe.init()

    # Get top markets directly from DB
    from sqlalchemy import select, func
    from base_engine.data.database import Market
    async with db.get_session() as session:
        result = await session.execute(
            select(Market).where(
                Market.active == True,
                Market.yes_price > 0.03,
                Market.yes_price < 0.97,
            ).order_by(Market.volume.desc()).limit(10)
        )
        markets = result.scalars().all()

    print("Top 10 tradeable markets (5-95% range):")
    for m in markets:
        print("")
        print("Market " + str(m.id) + ": yes_price=" + str(round(m.yes_price or 0, 4)) + " volume=" + str(round(float(m.volume or 0), 0)))
        yes_tid = str(m.yes_token_id or '')
        no_tid = str(m.no_token_id or '')
        yes_p = float(m.yes_price or 0)
        no_p = float(m.no_price or 0)

        for tid, price, side in [(yes_tid, yes_p, 'YES'), (no_tid, no_p, 'NO')]:
            if not tid or not tid.strip() or price <= 0:
                continue
            try:
                pred = await pe.predict(market_id=str(m.id), token_id=tid, current_price=price)
                if pred:
                    mp = pred.get('model_predictions', {})
                    wp = pred.get('prediction', 0)
                    conf = pred.get('confidence', 0)
                    # Compute what ensemble_bot would do
                    ensemble_pred = float(wp)
                    if side == 'NO':
                        ensemble_pred = 1.0 - ensemble_pred
                    print("  " + side + " price=" + str(round(price,4)))
                    print("    model_pred=" + str(round(float(wp),4)) + "  conf=" + str(round(float(conf),4)) + "  ensemble_side_conf=" + str(round(ensemble_pred,4)) + "  n_models=" + str(len(mp)))
                    if mp:
                        vals = [float(v) for v in mp.values() if v is not None and not math.isnan(float(v))]
                        if vals:
                            print("    model_values: min=" + str(round(min(vals),3)) + " max=" + str(round(max(vals),3)) + " mean=" + str(round(sum(vals)/len(vals),3)))
                else:
                    print("  " + side + ": prediction returned None")
            except Exception as e:
                import traceback
                print("  " + side + ": ERROR " + str(e))
                traceback.print_exc()

    await db.close()

asyncio.run(main())

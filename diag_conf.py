import asyncio, sys, math
sys.path.insert(0, '/opt/polymarket-ai-v2')
import os; os.chdir('/opt/polymarket-ai-v2')

async def main():
    from dotenv import load_dotenv
    load_dotenv('/opt/polymarket-ai-v2/.env')
    import structlog
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(30))

    from base_engine.data.database import Database
    from base_engine.learning.learning_engine import LearningEngine
    from base_engine.prediction.prediction_engine import PredictionEngine
    from base_engine.base_engine import BaseEngine

    db = Database()
    await db.init()
    le = LearningEngine(db)
    await le.init()
    pe = PredictionEngine(db, le)
    await pe.init()
    be = BaseEngine(db)
    be.prediction_engine = pe
    be.learning_engine = le

    markets = await be.get_all_tradeable_markets()
    markets = markets[:5]

    for m in markets:
        mid = str(m.get('id'))
        yes_tid = m.get('yes_token_id') or m.get('yesTokenId') or ''
        no_tid = m.get('no_token_id') or m.get('noTokenId') or ''
        yes_p = float(m.get('yes_price') or 0)
        no_p = float(m.get('no_price') or 0)
        print("")
        print("Market " + mid + ": YES=" + str(round(yes_p,4)) + ", NO=" + str(round(no_p,4)))

        for tid, price, side in [(yes_tid, yes_p, 'YES'), (no_tid, no_p, 'NO')]:
            if not tid or price <= 0:
                continue
            try:
                pred = await be.get_predictions(market_id=mid, token_id=tid, price=price)
                if pred:
                    mp = pred.get('model_predictions', {})
                    wp = pred.get('weighted_prediction') or pred.get('prediction', 0)
                    conf = pred.get('confidence', 0)
                    print("  " + side + " price=" + str(round(price,4)))
                    print("    raw_pred=" + str(round(float(wp),4)) + "  conf=" + str(round(float(conf),4)) + "  n_models=" + str(len(mp)))
                    if mp:
                        vals = [float(v) for v in mp.values() if v is not None and not math.isnan(float(v))]
                        if vals:
                            print("    min=" + str(round(min(vals),3)) + " max=" + str(round(max(vals),3)) + " mean=" + str(round(sum(vals)/len(vals),3)))
                else:
                    print("  " + side + ": prediction returned None")
            except Exception as e:
                print("  " + side + ": ERROR " + str(e))

    await db.close()

asyncio.run(main())

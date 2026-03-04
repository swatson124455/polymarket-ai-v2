import asyncio, sys, math
sys.path.insert(0, '/opt/polymarket-ai-v2')
import os; os.chdir('/opt/polymarket-ai-v2')

async def main():
    from dotenv import load_dotenv
    load_dotenv('/opt/polymarket-ai-v2/.env')
    import warnings; warnings.filterwarnings('ignore')
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

    # Query top markets by liquidity (what the bot scans)
    from sqlalchemy import text as sa_text
    async with db.get_session() as session:
        result = await session.execute(sa_text(
            "SELECT m.id, m.yes_token_id, m.no_token_id, m.yes_price, m.no_price, m.liquidity, LEFT(m.question, 60) "
            "FROM markets m "
            "WHERE m.active = true AND m.resolved = FALSE "
            "AND ((m.yes_token_id IS NOT NULL AND m.yes_token_id != '') OR (m.no_token_id IS NOT NULL AND m.no_token_id != '')) "
            "AND COALESCE(m.liquidity, 0) >= 100 "
            "ORDER BY COALESCE(m.liquidity, 0) DESC LIMIT 10"
        ))
        top10 = result.fetchall()

    print("Top 10 markets by LIQUIDITY (what the bot actually scans):")
    print("")
    for row in top10:
        mid, yes_tid, no_tid, yes_p, no_p, liq, q = row
        yes_p = float(yes_p or 0)
        no_p = float(no_p or 0)
        liq = float(liq or 0)
        print("Market " + str(mid) + ": YES=" + str(round(yes_p,4)) + " NO=" + str(round(no_p,4)) + " liq=" + str(round(liq)))
        print("  Q: " + str(q))

        for tid, price, side in [(yes_tid, yes_p, 'YES'), (no_tid, no_p, 'NO')]:
            if not tid or not tid.strip() or price <= 0:
                continue
            try:
                pred = await pe.predict(market_id=str(mid), token_id=str(tid), price=price)
                if pred:
                    mp = pred.get('model_predictions', {})
                    wp = float(pred.get('prediction', 0) or 0)
                    conf = float(pred.get('confidence', 0) or 0)
                    # In ensemble_bot: for NO, flip: ensemble_side_conf = 1 - wp
                    side_conf = (1.0 - wp) if side == 'NO' else wp
                    print("  " + side + ": model_pred=" + str(round(wp,4)) + " conf=" + str(round(conf,4)) + " side_conf=" + str(round(side_conf,4)) + " n_models=" + str(len(mp)))
                    if mp:
                        vals = [float(v) for v in mp.values() if v is not None and not math.isnan(float(v))]
                        if vals:
                            print("    model_values min=" + str(round(min(vals),3)) + " max=" + str(round(max(vals),3)) + " mean=" + str(round(sum(vals)/len(vals),3)))
                else:
                    print("  " + side + ": prediction returned None/empty")
            except Exception as e:
                print("  " + side + ": ERROR " + str(e)[:80])
        print("")

    await db.close()

asyncio.run(main())

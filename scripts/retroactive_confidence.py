"""Retroactive S130 confidence backtest — apply new formula to all historical entries."""
import asyncio

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        # Pull all ENTRY event_data + matching RESOLUTION P&L
        r = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    (e.event_data->>'conf_upstream')::float as upstream,"
            "    (e.event_data->>'conf_base')::float as old_base,"
            "    (e.event_data->>'confidence')::float as old_final,"
            "    COALESCE(e.event_data->>'category', 'unknown') as category,"
            "    (e.event_data->>'entry_price')::float as price,"
            "    (e.event_data->>'whale_trade_usd')::float as whale_usd,"
            "    e.event_data->>'side' as trade_side,"
            "    e.event_time"
            "  FROM trade_events e"
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl"
            "  FROM trade_events"
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
            ") SELECT e.*, r.realized_pnl"
            " FROM entries e"
            " JOIN resolutions r ON r.market_id = e.market_id AND r.side = e.side"
            " ORDER BY e.event_time"
        ))
        rows = r.fetchall()
        print(f"Total resolved entries with event_data: {len(rows)}")
        print()

        # Check how many have conf_upstream
        has_upstream = sum(1 for r in rows if r.upstream is not None)
        no_upstream = sum(1 for r in rows if r.upstream is None)
        print(f"Have conf_upstream: {has_upstream}, missing: {no_upstream}")
        print()

        # For entries missing conf_upstream, we can't retroactively apply.
        # For those with it, apply S130 formula.
        # We need category WR per trader — but we don't have trader in this query.
        # Simpler approach: use the stored old_base and old_final to see what
        # the S130 formula would produce. We have conf_upstream as the floor.
        # cat_adj = old_base - 0.50 (the Bayesian part, which IS the category adjustment)
        # But S130 changes how cat_adj is computed... we need to approximate.
        #
        # Actually the simplest backtest: bucket by conf_upstream (the efficiency score
        # that S130 now preserves as base) and see if THAT predicts P&L.
        # This tells us: "if we had used upstream as the base all along, would the
        # gradient predict winners?"

        # Bucket by conf_upstream
        buckets = {}
        for row in rows:
            up = row.upstream
            if up is None:
                up = row.old_base  # fallback to old_base if no upstream stored
            if up is None:
                continue
            if up < 0.53:
                b = '<0.53'
            elif up < 0.55:
                b = '0.53-0.54'
            elif up < 0.57:
                b = '0.55-0.56'
            elif up < 0.59:
                b = '0.57-0.58'
            elif up < 0.61:
                b = '0.59-0.60'
            elif up < 0.65:
                b = '0.61-0.64'
            elif up < 0.70:
                b = '0.65-0.69'
            else:
                b = '>=0.70'
            if b not in buckets:
                buckets[b] = {'n': 0, 'wins': 0, 'pnl': 0.0}
            buckets[b]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0:
                buckets[b]['wins'] += 1
            if row.realized_pnl:
                buckets[b]['pnl'] += float(row.realized_pnl)

        print("=== UPSTREAM EFFICIENCY vs RESOLUTION P&L ===")
        print("(This is what S130 uses as base — does the gradient predict winners?)")
        print(f"{'Bucket':<12} {'N':>6} {'Wins':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        order = ['<0.53', '0.53-0.54', '0.55-0.56', '0.57-0.58', '0.59-0.60', '0.61-0.64', '0.65-0.69', '>=0.70']
        for b in order:
            if b in buckets:
                d = buckets[b]
                wr = round(100.0 * d['wins'] / d['n'], 1) if d['n'] > 0 else 0
                avg = round(d['pnl'] / d['n'], 2) if d['n'] > 0 else 0
                print(f"{b:<12} {d['n']:>6} {d['wins']:>6} {wr:>7} {round(d['pnl'], 2):>12} {avg:>10}")
        print()

        # Now simulate S130 formula on each entry
        # S130: confidence = max(0.35, min(0.99, upstream + cat_adj + price_adj + conv_adj))
        # We can reconstruct price_adj from stored price and side
        # cat_adj: we approximate from old data. old S110 _base = 0.50 + shrinkage*(cat_wr-0.50)
        #   so cat_wr deviation = (_base - 0.50) / shrinkage. But shrinkage depends on cat_n
        #   which we don't have here. Simplification: use (old_base - 0.50) * 0.3 as cat_adj
        #   (the S130 scaling factor)
        # conv_adj: we don't have avg trade size, set to 0

        s130_buckets = {}
        for row in rows:
            up = row.upstream if row.upstream is not None else 0.55  # default
            price = row.price if row.price is not None else 0.50
            side = (row.trade_side or row.side or 'YES').upper()
            old_base = row.old_base if row.old_base is not None else 0.50

            # Reconstruct cat_adj (approximate)
            # S110 stored _base = 0.50 + shrinkage*(cat_wr - 0.50)
            # The deviation from 0.50 IS the Bayesian adjustment
            # S130 scales this by 0.3x
            cat_adj = (old_base - 0.50) * 0.3

            # Price adjustment (exact S130 formula)
            price_dev = abs(price - 0.50)
            is_contrarian = ((side == "YES" and price < 0.45) or
                             (side == "NO" and price < 0.45))
            if is_contrarian:
                price_adj = price_dev * 0.15
            else:
                price_adj = -(price_dev * 0.15 * 0.3)

            # S130 final
            s130_conf = max(0.35, min(0.99, up + cat_adj + price_adj))

            # Bucket
            if s130_conf < 0.50:
                b = '<0.50'
            elif s130_conf < 0.55:
                b = '0.50-0.54'
            elif s130_conf < 0.58:
                b = '0.55-0.57'
            elif s130_conf < 0.61:
                b = '0.58-0.60'
            elif s130_conf < 0.65:
                b = '0.61-0.64'
            elif s130_conf < 0.70:
                b = '0.65-0.69'
            elif s130_conf < 0.80:
                b = '0.70-0.79'
            else:
                b = '>=0.80'

            if b not in s130_buckets:
                s130_buckets[b] = {'n': 0, 'wins': 0, 'pnl': 0.0}
            s130_buckets[b]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0:
                s130_buckets[b]['wins'] += 1
            if row.realized_pnl:
                s130_buckets[b]['pnl'] += float(row.realized_pnl)

        print("=== SIMULATED S130 CONFIDENCE vs RESOLUTION P&L ===")
        print("(Retroactive: what if S130 had been running all along?)")
        print(f"{'Bucket':<12} {'N':>6} {'Wins':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        order2 = ['<0.50', '0.50-0.54', '0.55-0.57', '0.58-0.60', '0.61-0.64', '0.65-0.69', '0.70-0.79', '>=0.80']
        for b in order2:
            if b in s130_buckets:
                d = s130_buckets[b]
                wr = round(100.0 * d['wins'] / d['n'], 1) if d['n'] > 0 else 0
                avg = round(d['pnl'] / d['n'], 2) if d['n'] > 0 else 0
                print(f"{b:<12} {d['n']:>6} {d['wins']:>6} {wr:>7} {round(d['pnl'], 2):>12} {avg:>10}")
        print()

        # What-if: if we had gated at S130 conf >= 0.58, what trades would we have skipped?
        gated = {'kept': {'n': 0, 'wins': 0, 'pnl': 0.0}, 'skipped': {'n': 0, 'wins': 0, 'pnl': 0.0}}
        for row in rows:
            up = row.upstream if row.upstream is not None else 0.55
            price = row.price if row.price is not None else 0.50
            side = (row.trade_side or row.side or 'YES').upper()
            old_base = row.old_base if row.old_base is not None else 0.50
            cat_adj = (old_base - 0.50) * 0.3
            price_dev = abs(price - 0.50)
            is_contrarian = ((side == "YES" and price < 0.45) or (side == "NO" and price < 0.45))
            price_adj = price_dev * 0.15 if is_contrarian else -(price_dev * 0.15 * 0.3)
            s130_conf = max(0.35, min(0.99, up + cat_adj + price_adj))

            bucket = 'kept' if s130_conf >= 0.58 else 'skipped'
            gated[bucket]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0:
                gated[bucket]['wins'] += 1
            if row.realized_pnl:
                gated[bucket]['pnl'] += float(row.realized_pnl)

        print("=== WHAT-IF: GATE AT S130 >= 0.58 ===")
        for label in ['kept', 'skipped']:
            d = gated[label]
            wr = round(100.0 * d['wins'] / d['n'], 1) if d['n'] > 0 else 0
            avg = round(d['pnl'] / d['n'], 2) if d['n'] > 0 else 0
            print(f"{label:<10} N={d['n']:>5}  Wins={d['wins']:>5}  WR={wr:>5}%  PnL={round(d['pnl'], 2):>12}  Avg={avg:>10}")

        # Try other gates
        print()
        print("=== GATE SWEEP ===")
        print(f"{'Gate':>6} {'Kept':>6} {'KeptWR':>7} {'KeptPnL':>12} {'Skipped':>8} {'SkipWR':>7} {'SkipPnL':>12}")
        for gate in [0.53, 0.55, 0.57, 0.58, 0.60, 0.62, 0.65]:
            k = {'n': 0, 'w': 0, 'p': 0.0}
            s = {'n': 0, 'w': 0, 'p': 0.0}
            for row in rows:
                up = row.upstream if row.upstream is not None else 0.55
                price = row.price if row.price is not None else 0.50
                sd = (row.trade_side or row.side or 'YES').upper()
                ob = row.old_base if row.old_base is not None else 0.50
                ca = (ob - 0.50) * 0.3
                pd2 = abs(price - 0.50)
                ic = ((sd == "YES" and price < 0.45) or (sd == "NO" and price < 0.45))
                pa = pd2 * 0.15 if ic else -(pd2 * 0.15 * 0.3)
                c = max(0.35, min(0.99, up + ca + pa))
                t = k if c >= gate else s
                t['n'] += 1
                if row.realized_pnl and row.realized_pnl > 0:
                    t['w'] += 1
                if row.realized_pnl:
                    t['p'] += float(row.realized_pnl)
            kwr = round(100.0 * k['w'] / k['n'], 1) if k['n'] > 0 else 0
            swr = round(100.0 * s['w'] / s['n'], 1) if s['n'] > 0 else 0
            print(f"{gate:>6.2f} {k['n']:>6} {kwr:>6.1f}% {round(k['p'], 2):>12} {s['n']:>8} {swr:>6.1f}% {round(s['p'], 2):>12}")

    await db.close()

asyncio.run(main())

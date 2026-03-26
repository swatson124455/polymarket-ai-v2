"""Factor-level analysis: which confidence components predict P&L?"""
import asyncio

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        r = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side,"
            "    (e.event_data->>'conf_upstream')::float as upstream,"
            "    (e.event_data->>'conf_base')::float as old_base,"
            "    (e.event_data->>'confidence')::float as old_final,"
            "    (e.event_data->>'conf_price_adj')::float as price_adj,"
            "    (e.event_data->>'conf_conv_adj')::float as conv_adj,"
            "    (e.event_data->>'rel_mult')::float as rel_mult,"
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
        print(f"Total resolved: {len(rows)}")
        print()

        # ── Factor 1: Upstream efficiency (elite watchlist score) ──
        print("=== FACTOR: UPSTREAM EFFICIENCY ===")
        up_buckets = {}
        for row in rows:
            v = row.upstream
            if v is None:
                continue
            if v < 0.55: b = '<0.55'
            elif v < 0.56: b = '0.55'
            elif v < 0.57: b = '0.56'
            elif v < 0.58: b = '0.57'
            elif v < 0.60: b = '0.58-0.59'
            else: b = '>=0.60'
            up_buckets.setdefault(b, {'n':0,'w':0,'p':0.0})
            up_buckets[b]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0: up_buckets[b]['w'] += 1
            if row.realized_pnl: up_buckets[b]['p'] += float(row.realized_pnl)
        print(f"{'Bucket':<12} {'N':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        for b in ['<0.55','0.55','0.56','0.57','0.58-0.59','>=0.60']:
            if b in up_buckets:
                d = up_buckets[b]
                wr = round(100*d['w']/d['n'],1) if d['n'] else 0
                print(f"{b:<12} {d['n']:>6} {wr:>6.1f}% {round(d['p'],2):>12} {round(d['p']/d['n'],2):>10}")
        print()

        # ── Factor 2: Old Bayesian base (S110 category WR signal) ──
        print("=== FACTOR: BAYESIAN BASE (category WR proxy) ===")
        base_buckets = {}
        for row in rows:
            v = row.old_base
            if v is None: continue
            if v < 0.50: b = '<0.50'
            elif v < 0.51: b = '0.50'
            elif v < 0.52: b = '0.51'
            elif v < 0.53: b = '0.52'
            elif v < 0.55: b = '0.53-0.54'
            elif v < 0.60: b = '0.55-0.59'
            elif v < 0.70: b = '0.60-0.69'
            elif v < 0.80: b = '0.70-0.79'
            else: b = '>=0.80'
            base_buckets.setdefault(b, {'n':0,'w':0,'p':0.0})
            base_buckets[b]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0: base_buckets[b]['w'] += 1
            if row.realized_pnl: base_buckets[b]['p'] += float(row.realized_pnl)
        print(f"{'Bucket':<12} {'N':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        for b in ['<0.50','0.50','0.51','0.52','0.53-0.54','0.55-0.59','0.60-0.69','0.70-0.79','>=0.80']:
            if b in base_buckets:
                d = base_buckets[b]
                wr = round(100*d['w']/d['n'],1) if d['n'] else 0
                print(f"{b:<12} {d['n']:>6} {wr:>6.1f}% {round(d['p'],2):>12} {round(d['p']/d['n'],2):>10}")
        print()

        # ── Factor 3: Price adjustment ──
        print("=== FACTOR: PRICE ADJUSTMENT ===")
        pa_buckets = {}
        for row in rows:
            v = row.price_adj
            if v is None: continue
            if v < -0.03: b = '<-0.03'
            elif v < -0.01: b = '-0.03 to -0.01'
            elif v < 0.01: b = '-0.01 to +0.01'
            elif v < 0.03: b = '+0.01 to +0.03'
            else: b = '>=+0.03'
            pa_buckets.setdefault(b, {'n':0,'w':0,'p':0.0})
            pa_buckets[b]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0: pa_buckets[b]['w'] += 1
            if row.realized_pnl: pa_buckets[b]['p'] += float(row.realized_pnl)
        print(f"{'Bucket':<18} {'N':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        for b in ['<-0.03','-0.03 to -0.01','-0.01 to +0.01','+0.01 to +0.03','>=+0.03']:
            if b in pa_buckets:
                d = pa_buckets[b]
                wr = round(100*d['w']/d['n'],1) if d['n'] else 0
                print(f"{b:<18} {d['n']:>6} {wr:>6.1f}% {round(d['p'],2):>12} {round(d['p']/d['n'],2):>10}")
        print()

        # ── Factor 4: Reliability multiplier ──
        print("=== FACTOR: RELIABILITY MULTIPLIER ===")
        rel_buckets = {}
        for row in rows:
            v = row.rel_mult
            if v is None: continue
            if v < 0.5: b = '<0.50'
            elif v < 1.0: b = '0.50-0.99'
            elif v < 1.5: b = '1.00-1.49'
            elif v < 2.0: b = '1.50-1.99'
            else: b = '>=2.00'
            rel_buckets.setdefault(b, {'n':0,'w':0,'p':0.0})
            rel_buckets[b]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0: rel_buckets[b]['w'] += 1
            if row.realized_pnl: rel_buckets[b]['p'] += float(row.realized_pnl)
        print(f"{'Bucket':<12} {'N':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        for b in ['<0.50','0.50-0.99','1.00-1.49','1.50-1.99','>=2.00']:
            if b in rel_buckets:
                d = rel_buckets[b]
                wr = round(100*d['w']/d['n'],1) if d['n'] else 0
                print(f"{b:<12} {d['n']:>6} {wr:>6.1f}% {round(d['p'],2):>12} {round(d['p']/d['n'],2):>10}")
        print()

        # ── Factor 5: Whale trade size ──
        print("=== FACTOR: WHALE TRADE USD ===")
        wh_buckets = {}
        for row in rows:
            v = row.whale_usd
            if v is None: continue
            if v < 10: b = '<$10'
            elif v < 100: b = '$10-99'
            elif v < 500: b = '$100-499'
            elif v < 1000: b = '$500-999'
            elif v < 5000: b = '$1k-5k'
            else: b = '>=$5k'
            wh_buckets.setdefault(b, {'n':0,'w':0,'p':0.0})
            wh_buckets[b]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0: wh_buckets[b]['w'] += 1
            if row.realized_pnl: wh_buckets[b]['p'] += float(row.realized_pnl)
        print(f"{'Bucket':<12} {'N':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        for b in ['<$10','$10-99','$100-499','$500-999','$1k-5k','>=$5k']:
            if b in wh_buckets:
                d = wh_buckets[b]
                wr = round(100*d['w']/d['n'],1) if d['n'] else 0
                print(f"{b:<12} {d['n']:>6} {wr:>6.1f}% {round(d['p'],2):>12} {round(d['p']/d['n'],2):>10}")
        print()

        # ── Factor 6: Entry price (token cost) ──
        print("=== FACTOR: ENTRY PRICE ===")
        ep_buckets = {}
        for row in rows:
            v = row.price
            if v is None: continue
            if v < 0.10: b = '<0.10'
            elif v < 0.20: b = '0.10-0.19'
            elif v < 0.35: b = '0.20-0.34'
            elif v < 0.50: b = '0.35-0.49'
            elif v < 0.65: b = '0.50-0.64'
            elif v < 0.80: b = '0.65-0.79'
            elif v < 0.90: b = '0.80-0.89'
            else: b = '>=0.90'
            ep_buckets.setdefault(b, {'n':0,'w':0,'p':0.0})
            ep_buckets[b]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0: ep_buckets[b]['w'] += 1
            if row.realized_pnl: ep_buckets[b]['p'] += float(row.realized_pnl)
        print(f"{'Bucket':<12} {'N':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        for b in ['<0.10','0.10-0.19','0.20-0.34','0.35-0.49','0.50-0.64','0.65-0.79','0.80-0.89','>=0.90']:
            if b in ep_buckets:
                d = ep_buckets[b]
                wr = round(100*d['w']/d['n'],1) if d['n'] else 0
                print(f"{b:<12} {d['n']:>6} {wr:>6.1f}% {round(d['p'],2):>12} {round(d['p']/d['n'],2):>10}")
        print()

        # ── Factor 7: Category x Side interaction ──
        print("=== FACTOR: CATEGORY x SIDE ===")
        cs_buckets = {}
        for row in rows:
            cat = row.category or 'unknown'
            side = (row.trade_side or row.side or 'YES').upper()
            b = f"{cat}:{side}"
            cs_buckets.setdefault(b, {'n':0,'w':0,'p':0.0})
            cs_buckets[b]['n'] += 1
            if row.realized_pnl and row.realized_pnl > 0: cs_buckets[b]['w'] += 1
            if row.realized_pnl: cs_buckets[b]['p'] += float(row.realized_pnl)
        print(f"{'CatxSide':<22} {'N':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        sorted_cs = sorted(cs_buckets.items(), key=lambda x: x[1]['p'])
        for b, d in sorted_cs:
            if d['n'] >= 5:
                wr = round(100*d['w']/d['n'],1) if d['n'] else 0
                print(f"{b:<22} {d['n']:>6} {wr:>6.1f}% {round(d['p'],2):>12} {round(d['p']/d['n'],2):>10}")
        print()

        # ── Optimal weight search (brute force) ──
        print("=== OPTIMAL WEIGHT SEARCH ===")
        print("Testing: conf = upstream*W_up + cat_dev*W_cat + price_adj*W_pa")
        print("Cat_dev = (old_base - 0.50), scanning weight combos...")
        print()

        best = None
        results = []
        for w_cat in [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]:
            for w_pa in [0.5, 1.0, 1.5, 2.0, 3.0]:
                for gate in [0.58, 0.60, 0.62, 0.65]:
                    kept = {'n':0,'w':0,'p':0.0}
                    for row in rows:
                        up = row.upstream if row.upstream is not None else 0.55
                        ob = row.old_base if row.old_base is not None else 0.50
                        pa = row.price_adj if row.price_adj is not None else 0.0
                        cat_dev = ob - 0.50
                        conf = up + cat_dev * w_cat + pa * w_pa
                        if conf >= gate:
                            kept['n'] += 1
                            if row.realized_pnl and row.realized_pnl > 0: kept['w'] += 1
                            if row.realized_pnl: kept['p'] += float(row.realized_pnl)
                    if kept['n'] >= 20:
                        wr = round(100*kept['w']/kept['n'],1)
                        avg = round(kept['p']/kept['n'],2)
                        results.append((w_cat, w_pa, gate, kept['n'], wr, round(kept['p'],2), avg))
                        if best is None or kept['p'] > best[5]:
                            best = (w_cat, w_pa, gate, kept['n'], wr, round(kept['p'],2), avg)

        # Show top 10 by total P&L
        results.sort(key=lambda x: x[5], reverse=True)
        print(f"{'W_cat':>6} {'W_pa':>6} {'Gate':>6} {'N':>6} {'WR%':>7} {'TotalPnL':>12} {'AvgPnL':>10}")
        for r in results[:15]:
            print(f"{r[0]:>6.1f} {r[1]:>6.1f} {r[2]:>6.2f} {r[3]:>6} {r[4]:>6.1f}% {r[5]:>12} {r[6]:>10}")
        print()
        if best:
            print(f"BEST: W_cat={best[0]}, W_pa={best[1]}, Gate={best[2]} → N={best[3]}, WR={best[4]}%, PnL={best[5]}")

    await db.close()

asyncio.run(main())

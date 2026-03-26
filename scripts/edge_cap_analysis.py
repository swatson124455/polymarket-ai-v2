#!/usr/bin/env python3
"""Analyze edge cap rejections — cross-reference with resolution outcomes.

Usage: PYTHONPATH=/opt/polymarket-ai-v2 python3 scripts/edge_cap_analysis.py
"""
import subprocess
import re
import asyncio
import sys
from collections import defaultdict

def extract_caps():
    """Extract all edge cap rejections from journalctl."""
    result = subprocess.run(
        ['journalctl', '-u', 'polymarket-ai', '--no-pager', '--since', '2026-03-01'],
        capture_output=True, text=True, timeout=120
    )

    caps = []
    for line in result.stdout.split('\n'):
        if 'weatherbot_edge_cap' not in line:
            continue
        m_edge = re.search(r'edge=([\d.]+)', line)
        m_lead = re.search(r'lead_time_h=([\d.]+)', line)
        m_market = re.search(r'market_id=(\S+)', line)
        m_max = re.search(r'max_edge=([\d.]+)', line)
        m_ts = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', line)
        if m_edge and m_lead and m_market and m_max and m_ts:
            caps.append({
                'ts': m_ts.group(1),
                'market_id': m_market.group(1),
                'edge': float(m_edge.group(1)),
                'max_edge': float(m_max.group(1)),
                'lead_time_h': float(m_lead.group(1)),
            })
    return caps


async def db_lookup(market_ids):
    """Look up resolution outcomes and prediction log data."""
    import asyncpg
    conn = await asyncpg.connect(database='polymarket', user='postgres')

    # Resolution data from traded_markets
    rows = await conn.fetch("""
        SELECT condition_id, resolved, resolved_outcome, resolved_at
        FROM traded_markets
        WHERE condition_id = ANY($1::text[])
    """, market_ids)
    resolved_map = {}
    for r in rows:
        resolved_map[r['condition_id']] = {
            'resolved': r['resolved'],
            'outcome': r['resolved_outcome'],
            'resolved_at': str(r['resolved_at']) if r['resolved_at'] else None,
        }

    # Check prediction_log for side/confidence info on these markets
    pred_rows = await conn.fetch("""
        SELECT market_id, predicted_outcome, model_confidence, market_price,
               model_name, trade_executed
        FROM prediction_log
        WHERE market_id = ANY($1::text[]) AND model_name LIKE '%eather%'
        ORDER BY created_at DESC
    """, market_ids)
    pred_map = {}
    for r in pred_rows:
        if r['market_id'] not in pred_map:
            pred_map[r['market_id']] = {
                'side': r['predicted_outcome'],
                'confidence': float(r['model_confidence']) if r['model_confidence'] else None,
                'market_price': float(r['market_price']) if r['market_price'] else None,
                'traded': r['trade_executed'],
            }

    # Check if any were actually traded despite cap (positions)
    pos_rows = await conn.fetch("""
        SELECT market_id, side, entry_price, status, unrealized_pnl, size
        FROM positions
        WHERE market_id = ANY($1::text[]) AND bot_id = 'WeatherBot'
    """, market_ids)
    pos_map = {}
    for r in pos_rows:
        pos_map[r['market_id']] = {
            'side': r['side'],
            'entry_price': float(r['entry_price']),
            'status': r['status'],
            'upnl': float(r['unrealized_pnl']) if r['unrealized_pnl'] else 0,
            'size': float(r['size']),
        }

    # Get trade_events for resolved markets to compute hypothetical P&L
    # We need to know what actually happened — did YES win or NO win?
    te_rows = await conn.fetch("""
        SELECT market_id, side, entry_price, size, pnl, event_type
        FROM trade_events
        WHERE market_id = ANY($1::text[]) AND bot_name = 'WeatherBot'
          AND event_type = 'RESOLUTION'
    """, market_ids)
    resolution_pnl_map = {}
    for r in te_rows:
        resolution_pnl_map[r['market_id']] = {
            'side': r['side'],
            'entry_price': float(r['entry_price']),
            'size': float(r['size']),
            'pnl': float(r['pnl']),
        }

    await conn.close()
    return resolved_map, pred_map, pos_map, resolution_pnl_map


def compute_hypo_pnl(side, entry_price, size, outcome):
    """Compute hypothetical P&L for a trade that was never placed."""
    if side == 'YES':
        if outcome == 'YES':
            return (1.0 - entry_price) * size  # WIN
        else:
            return -entry_price * size  # LOSS
    else:  # NO side
        if outcome == 'NO':
            return (1.0 - entry_price) * size  # WIN (bought NO token)
        else:
            return -entry_price * size  # LOSS


def main():
    caps = extract_caps()
    print(f"Total edge cap rejection log entries: {len(caps)}")

    # Deduplicate to unique market_ids (keep first rejection)
    seen = {}
    for c in caps:
        if c['market_id'] not in seen:
            seen[c['market_id']] = c
    unique_caps = list(seen.values())
    print(f"Unique markets rejected: {len(unique_caps)}")

    # DB lookup
    market_ids = [c['market_id'] for c in unique_caps]
    resolved_map, pred_map, pos_map, resolution_pnl_map = asyncio.run(db_lookup(market_ids))

    in_traded = sum(1 for c in unique_caps if c['market_id'] in resolved_map)
    resolved = sum(1 for c in unique_caps if c['market_id'] in resolved_map and resolved_map[c['market_id']]['resolved'])
    has_pred = sum(1 for c in unique_caps if c['market_id'] in pred_map)
    has_pos = sum(1 for c in unique_caps if c['market_id'] in pos_map)

    print(f"In traded_markets table: {in_traded}")
    print(f"Resolved: {resolved}")
    print(f"Has prediction_log entry: {has_pred}")
    print(f"Had position anyway: {has_pos}")

    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("BREAKDOWN BY LEAD TIME TIER")
    print("=" * 70)
    tiers = [
        ('<6h',  0, 6,   0.70),
        ('<12h', 6, 12,  0.50),
        ('<24h', 12, 24, 0.40),
        ('<48h', 24, 48, 0.30),
        ('>48h', 48, 9999, 0.25),
    ]
    for tier_name, lo, hi, cap in tiers:
        tier_caps = [c for c in unique_caps if lo <= c['lead_time_h'] < hi]
        if not tier_caps:
            continue
        total = len(tier_caps)
        resolved_count = sum(
            1 for c in tier_caps
            if c['market_id'] in resolved_map and resolved_map[c['market_id']]['resolved']
        )
        avg_edge = sum(c['edge'] for c in tier_caps) / total
        max_e = max(c['edge'] for c in tier_caps)
        min_e = min(c['edge'] for c in tier_caps)
        # How many are just barely over vs way over cap
        barely = sum(1 for c in tier_caps if c['edge'] <= cap * 1.2)
        way_over = sum(1 for c in tier_caps if c['edge'] > cap * 1.5)
        print(f"\n  {tier_name} (cap={cap}):")
        print(f"    Rejections: {total} unique markets, {resolved_count} resolved")
        print(f"    Edge range: [{min_e:.3f}, {max_e:.3f}], avg={avg_edge:.3f}")
        print(f"    Barely over (<1.2x cap): {barely} ({barely/total*100:.0f}%)")
        print(f"    Way over (>1.5x cap): {way_over} ({way_over/total*100:.0f}%)")

    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("BREAKDOWN BY EDGE MAGNITUDE")
    print("=" * 70)
    edge_bins = [
        ('0.25-0.30', 0.25, 0.30),
        ('0.30-0.40', 0.30, 0.40),
        ('0.40-0.50', 0.40, 0.50),
        ('0.50-0.60', 0.50, 0.60),
        ('0.60-0.70', 0.60, 0.70),
        ('0.70-0.80', 0.70, 0.80),
        ('0.80+',     0.80, 2.00),
    ]
    for name, lo, hi in edge_bins:
        bin_caps = [c for c in unique_caps if lo <= c['edge'] < hi]
        if not bin_caps:
            continue
        resolved_count = sum(
            1 for c in bin_caps
            if c['market_id'] in resolved_map and resolved_map[c['market_id']]['resolved']
        )
        print(f"  {name}: {len(bin_caps)} rejections, {resolved_count} resolved")

    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("HYPOTHETICAL P&L ANALYSIS (resolved markets with prediction data)")
    print("=" * 70)

    hypo_pnl_total = 0.0
    hypo_wins = 0
    hypo_losses = 0
    hypo_details = []

    for c in unique_caps:
        mid = c['market_id']
        if mid not in resolved_map or not resolved_map[mid]['resolved']:
            continue
        if mid not in pred_map:
            continue

        outcome = resolved_map[mid].get('outcome', '')
        if not outcome:
            continue
        outcome = outcome.upper()
        if outcome not in ('YES', 'NO'):
            continue

        pred = pred_map[mid]
        side = pred.get('side', '').upper()
        if side not in ('YES', 'NO'):
            continue

        market_price = pred.get('market_price')
        if market_price is None:
            continue

        # Hypothetical entry price
        entry_price = market_price if side == 'YES' else (1.0 - market_price)

        # Hypothetical size: use a standard $50 to normalize
        hypo_size = 50.0 / entry_price if entry_price > 0 else 0

        pnl = compute_hypo_pnl(side, entry_price, hypo_size, outcome)
        hypo_pnl_total += pnl
        if pnl > 0:
            hypo_wins += 1
        else:
            hypo_losses += 1

        hypo_details.append({
            'market_id': mid[:20],
            'side': side,
            'edge': c['edge'],
            'lead_h': c['lead_time_h'],
            'outcome': outcome,
            'entry': entry_price,
            'pnl': pnl,
            'won': pnl > 0,
        })

    total_hypo = hypo_wins + hypo_losses
    if total_hypo > 0:
        wr = hypo_wins / total_hypo * 100
        print(f"\n  Resolved markets with prediction data: {total_hypo}")
        print(f"  Wins: {hypo_wins} ({wr:.1f}%)")
        print(f"  Losses: {hypo_losses} ({100-wr:.1f}%)")
        print(f"  Hypothetical P&L (@ $50/trade): ${hypo_pnl_total:+.2f}")
        print(f"  Avg P&L per trade: ${hypo_pnl_total/total_hypo:+.2f}")

        # By side
        yes_trades = [d for d in hypo_details if d['side'] == 'YES']
        no_trades = [d for d in hypo_details if d['side'] == 'NO']

        print(f"\n  BY SIDE:")
        if yes_trades:
            yes_wins = sum(1 for d in yes_trades if d['won'])
            yes_pnl = sum(d['pnl'] for d in yes_trades)
            print(f"    YES: {len(yes_trades)} trades, {yes_wins} wins ({yes_wins/len(yes_trades)*100:.0f}%), PnL=${yes_pnl:+.2f}")
        if no_trades:
            no_wins = sum(1 for d in no_trades if d['won'])
            no_pnl = sum(d['pnl'] for d in no_trades)
            print(f"    NO:  {len(no_trades)} trades, {no_wins} wins ({no_wins/len(no_trades)*100:.0f}%), PnL=${no_pnl:+.2f}")

        # By lead time
        print(f"\n  BY LEAD TIME:")
        for tier_name, lo, hi, cap in tiers:
            tier_trades = [d for d in hypo_details if lo <= d['lead_h'] < hi]
            if not tier_trades:
                continue
            t_wins = sum(1 for d in tier_trades if d['won'])
            t_pnl = sum(d['pnl'] for d in tier_trades)
            print(f"    {tier_name}: {len(tier_trades)} trades, {t_wins} wins ({t_wins/len(tier_trades)*100:.0f}%), PnL=${t_pnl:+.2f}")

        # By edge magnitude
        print(f"\n  BY EDGE SIZE:")
        for name, lo, hi in edge_bins:
            bin_trades = [d for d in hypo_details if lo <= d['edge'] < hi]
            if not bin_trades:
                continue
            b_wins = sum(1 for d in bin_trades if d['won'])
            b_pnl = sum(d['pnl'] for d in bin_trades)
            print(f"    {name}: {len(bin_trades)} trades, {b_wins} wins ({b_wins/len(bin_trades)*100:.0f}%), PnL=${b_pnl:+.2f}")
    else:
        print("\n  No resolved markets with prediction data to analyze.")
        print("  (Edge cap log doesn't record side/price; prediction_log may not cover all)")

    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("REJECTIONS BY DATE")
    print("=" * 70)
    by_date = defaultdict(int)
    by_date_all = defaultdict(int)
    for c in unique_caps:
        by_date[c['ts'][:10]] += 1
    for c in caps:
        by_date_all[c['ts'][:10]] += 1
    print(f"  {'Date':<12} {'Unique':>8} {'Total(w/dupes)':>15}")
    for d in sorted(set(list(by_date.keys()) + list(by_date_all.keys()))):
        print(f"  {d:<12} {by_date.get(d,0):>8} {by_date_all.get(d,0):>15}")

    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("EDGE OVERSHOOT DISTRIBUTION")
    print("=" * 70)
    for c in unique_caps:
        c['overshoot'] = c['edge'] / c['max_edge']

    buckets = [
        ('1.0-1.1x cap', 1.0, 1.1),
        ('1.1-1.2x cap', 1.1, 1.2),
        ('1.2-1.5x cap', 1.2, 1.5),
        ('1.5-2.0x cap', 1.5, 2.0),
        ('2.0-3.0x cap', 2.0, 3.0),
        ('3.0x+ cap',    3.0, 100.0),
    ]
    for name, lo, hi in buckets:
        count = sum(1 for c in unique_caps if lo <= c['overshoot'] < hi)
        if count:
            print(f"  {name}: {count}")


if __name__ == '__main__':
    main()

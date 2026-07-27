#!/usr/bin/env python3
"""M3 — UNDERLYING MAP. How many INDEPENDENT bets does the venue actually offer?

READ-ONLY, public endpoints, no auth, no orders. Safe while parked.

WHY: diversification is what makes a maker's income dominate idiosyncratic fill losses, and
that only works across UNCORRELATED underlyings. `ladder_pairing` already handles correlation
INSIDE one event. Nothing groups ACROSS events, so a book of gas-daily + gas-weekly +
gas-monthly reads as three independent positions and is one bet on the price of gas.

This maps live liquidity programs to an inferred UNDERLYING and reports the pool concentration
per underlying — the diversification ceiling any allocator is bounded by.

THE INFERENCE IS A MODEL AND IS REPORTED AS ONE. Series tickers are grouped by longest-prefix
match against an explicit table below; anything unmatched falls back to its own series and is
counted as its own underlying, which OVERSTATES independence. The unmatched share is printed
so the overstatement is visible rather than hidden. Do not treat this as ground truth — it is
a first cut to be corrected as families are confirmed.

R1 pool canon: period_reward/10000 IS ALREADY the daily per-market pool. Do NOT divide by
window length.
"""
import collections, datetime, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import maker_kalshi_quoter as Q
from maker_kalshi_quoter import public_get

# longest-prefix wins. Each entry: (series prefix, underlying label)
FAMILIES = (
    ("KXAAAGASMIN", "us_retail_gasoline"), ("KXAAAGASMAX", "us_retail_gasoline"),
    ("KXAAAGASD", "us_retail_gasoline"), ("KXAAAGASW", "us_retail_gasoline"),
    ("KXAAAGASM", "us_retail_gasoline"), ("KXAAAGASED", "us_retail_gasoline"),
    ("KXAAAGAS", "us_retail_gasoline"), ("KXGASOS", "us_retail_gasoline"),
    ("KXSUSPENDGASTAX", "us_gas_policy"),
    ("KXNATGAS", "natural_gas"), ("KXNGAS", "natural_gas"),
    ("KXUSGASCPI", "us_cpi"), ("KXCPI", "us_cpi"), ("KXINFL", "us_cpi"),
    ("KXTEMP", "us_weather_temp"), ("KXRAIN", "us_weather_precip"),
    ("KXHIGH", "us_weather_temp"), ("KXSNOW", "us_weather_precip"),
    ("KXFED", "us_fed"), ("KXRATE", "us_fed"),
    ("KXCLUBF", "club_football"), ("KXJOINCLUB", "club_football"),
    ("KXLIUK", "club_football"),
    # GPU restock: one underlying (supply of Nvidia accelerators), many cadences.
    # MS/WS/MON/MAX are daily/weekly/monthly/max variants of the SAME product line.
    ("KXH100", "gpu_restock"), ("KXH200", "gpu_restock"),
    ("KXB200", "gpu_restock"), ("KXA100", "gpu_restock"), ("KXRTX", "gpu_restock"),
    ("KXCOINBASE", "crypto"), ("KXBTC", "crypto"), ("KXETH", "crypto"),
    ("KXTRUMP", "us_politics_trump"), ("KXMUSK", "musk"),
    ("KXRT", "box_office_reviews"), ("KXBOX", "box_office_reviews"),
    # Single-name equities move together with each other and the tape; grouping them is the
    # CONSERVATIVE direction here (over-grouping understates independence, which is the safe
    # error for a diversification ceiling).
    ("KXMETA", "us_equities"), ("KXBA", "us_equities"), ("KXSBUX", "us_equities"),
    ("KXNCLH", "us_equities"), ("KXAMZN", "us_equities"), ("KXRBLX", "us_equities"),
    ("KXCVNA", "us_equities"), ("KXRDDT", "us_equities"), ("KXAC", "us_equities"),
    ("KXHOOD", "us_equities"), ("KXHOODA", "us_equities"),
    ("KXCOMPANYACTIONRDDT", "us_equities"),
    # Chipotle: the stock and the burrito-count market share one company.
    ("KXCMG", "chipotle"), ("KXCHIPBURRITO", "chipotle"),
    ("KXINXHUD", "macro_index_fx"), ("KXNDQHUD", "macro_index_fx"),
    ("KXDXYDUD", "macro_index_fx"), ("KXEURUSDAW", "macro_index_fx"),
    ("KXUSGDPSHARE", "us_macro"),
    ("KXNEXTTEAMNBA", "nba"), ("KXNBANEXTCONTRACT", "nba"),
    ("KXNBANEXTTEAMOUTLET", "nba"), ("KXNBARETIRE", "nba"),
    ("KXMLBALMOTY", "mlb"), ("KXNEXTTEAMMLB", "mlb"), ("KXNEXTMANAGERMLB", "mlb"),
    ("KXGTA6SONGS", "gta6"), ("KXGTATRAILER", "gta6"),
    ("KXMAMDANIEO", "us_politics"), ("KXAPRPOTUS", "us_politics"),
    ("KXGENERICBALLOTVOTEHUB", "us_politics"), ("KXTRUTHSOCIAL", "us_politics"),
    ("KXTARIFFBILL", "us_politics"), ("KXCLARITYVOTE", "us_politics"),
    ("KXWCTEAMS", "world_cup"), ("KXWCCAREERGOALS", "world_cup"),
    ("KXINTLPLAYAGAIN", "world_cup"),
)


def underlying(series):
    best = None
    for pre, lab in FAMILIES:
        if series.startswith(pre) and (best is None or len(pre) > len(best[0])):
            best = (pre, lab)
    return (best[1], True) if best else (f"UNMAPPED:{series}", False)


def iso(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    Q.READ_BUDGET_PER_CYCLE = 10 ** 9
    Q.REQ_SPACING_S = 0.03
    now = datetime.datetime.now(datetime.timezone.utc)
    print("M3 UNDERLYING MAP\nREAD_AT_UTC", now.isoformat())

    rows, seen, cur = [], set(), None
    while True:
        p = "/trade-api/v2/incentive_programs?limit=1000&status=active"
        if cur:
            p += f"&cursor={cur}"
        j = public_get(p)
        b = j.get("incentive_programs") or []
        fresh = [x for x in b if x["id"] not in seen]
        rows += fresh
        seen |= {x["id"] for x in b}
        cur = j.get("next_cursor") or None
        if not cur or not b or not fresh:
            break
    live = [p for p in rows
            if (p.get("incentive_type") == "liquidity"
                and iso(p["start_date"]) <= now <= iso(p["end_date"]))]
    print(f"live liquidity programs: {len(live)}")

    pool = collections.defaultdict(float)
    mkts = collections.Counter()
    series_of = collections.defaultdict(set)
    mapped_pool = unmapped_pool = 0.0
    for p in live:
        s = (p.get("market_ticker") or "").split("-")[0]
        u, is_mapped = underlying(s)
        d = float(p.get("period_reward") or 0) / 10000.0
        pool[u] += d
        mkts[u] += 1
        series_of[u].add(s)
        if is_mapped:
            mapped_pool += d
        else:
            unmapped_pool += d
    total = mapped_pool + unmapped_pool
    n_unmapped = sum(1 for u in pool if u.startswith("UNMAPPED:"))

    print(f"\nTOTAL live daily pool ${total:,.2f}")
    print(f"  mapped to a known underlying : ${mapped_pool:,.2f} "
          f"({100.0*mapped_pool/total:.1f}%) across "
          f"{len(pool)-n_unmapped} underlyings")
    print(f"  UNMAPPED (each counted as its own underlying -> OVERSTATES independence): "
          f"${unmapped_pool:,.2f} ({100.0*unmapped_pool/total:.1f}%) across {n_unmapped} series")

    print("\nTOP 20 UNDERLYINGS BY DAILY POOL")
    print(f"  {'underlying':<26} {'$/day':>10} {'%venue':>7} {'mkts':>6} {'series':>7}")
    for u, v in sorted(pool.items(), key=lambda x: -x[1])[:20]:
        print(f"  {u:<26} {v:>10,.2f} {100.0*v/total:>6.1f}% {mkts[u]:>6} "
              f"{len(series_of[u]):>7}")

    ranked = sorted(pool.values(), reverse=True)
    for n in (1, 3, 5, 10, 20):
        if len(ranked) >= n:
            print(f"  top {n:>2} underlyings = {100.0*sum(ranked[:n])/total:>5.1f}% of venue pool")

    out = {"read_at_utc": now.isoformat(), "n_live": len(live),
           "total_pool_day": total, "mapped_pool_day": mapped_pool,
           "unmapped_pool_day": unmapped_pool,
           "pool_by_underlying": dict(sorted(pool.items(), key=lambda x: -x[1])),
           "markets_by_underlying": dict(mkts)}
    with open("m3_underlying_map.json", "w") as f:
        json.dump(out, f, indent=1)
    print("\nwrote m3_underlying_map.json")


if __name__ == "__main__":
    sys.exit(main())

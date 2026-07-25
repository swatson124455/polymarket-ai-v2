#!/usr/bin/env python3
"""VENUE-WIDE OPPORTUNITY SCAN — family-agnostic. READ-ONLY.

We quote 8 of 147 live series (6.0% of a $174,403/day venue). Temp and gas are simply where we
happened to quote; they are not where the money is. This ranks the WHOLE active universe by what a
20-contract join AT REFERENCE would actually capture, using the same R4 walk the quoter uses —
pool size alone is known NOT to separate paid from zero.

Capture model per market: per side, our score = DF^0 * 20 = 20 at reference; the reward denominator
once we rest is book_df_total + our_score. R3: BOTH sides must reach Target Size or the snapshot
pays nobody. Two-sided snapshot share = (share_yes + share_no)/2, times the daily per-market pool
(period_reward/10000 -- ALREADY daily, do NOT divide by window length).

MODEL, not receipt: the capture model is known to over-predict 2-6x (M7). Use it to RANK, never as
a $ forecast.
"""
import collections
import importlib.util
import json
import os
import statistics as st
import sys
import time

KL = (r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2"
      r"\02f270fe-27ab-42e6-8906-2ebc25f6df3b\scratchpad\kalshi-wt\kalshi_live")
sys.path.insert(0, KL)
_cwd = os.getcwd()
os.chdir(KL)
_s = importlib.util.spec_from_file_location("mq", os.path.join(KL, "maker_kalshi_quoter.py"))
q = importlib.util.module_from_spec(_s)
sys.modules["mq"] = q
_s.loader.exec_module(q)
os.chdir(_cwd)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venue_scan.json")
JOIN_CT = 20.0
TOP_SERIES = 30          # by daily pool
MKTS_PER_SERIES = 3      # near-money sample
SPACING_S = 0.35         # be a polite neighbour to the live bot's read budget

ALLOW = {"KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH", "KXAAAGASD",
         "KXAAAGASW", "KXB200MON", "KXAMSAVO", "KXH100MON", "KXMUSKNW", "KXCHIPBURRITO",
         "KXTRUMPENDORSEMENTS", "KXGENERICBALLOTVOTEHUB"}


def pull_active():
    progs, cur = [], ""
    for _ in range(8):
        d = q.public_get("/trade-api/v2/incentive_programs?status=active&limit=1000"
                         + (("&cursor=" + cur) if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    return [p for p in progs if (p.get("incentive_type") or "liquidity") == "liquidity"]


def pool(p):
    return float(p.get("period_reward") or 0) / 10000.0      # ALREADY daily per market


def target_of(p):
    for k in ("target_size", "target", "target_contracts"):
        if p.get(k):
            return float(p[k])
    return 1000.0


def df_of(p):
    b = p.get("discount_factor_bps")
    return (float(b) / 10000.0) if b else 0.5


def capture(yl, nl, target, df, usd_day):
    """What a JOIN_CT join at reference captures. Returns (usd_day_capture, y_book, n_book, ok)."""
    ty, cy, ry, _, qy = q._qualifying_breakdown(yl, target, df)
    tn, cn, rn, _, qn = q._qualifying_breakdown(nl, target, df)
    if not (qy and qn):
        return 0.0, ty, tn, False                     # R3: pays nobody
    sy = JOIN_CT / (ty + JOIN_CT)
    sn = JOIN_CT / (tn + JOIN_CT)
    return ((sy + sn) / 2.0) * usd_day, ty, tn, True


def main():
    progs = pull_active()
    by_series = collections.defaultdict(list)
    for p in progs:
        t = p.get("market_ticker") or ""
        if t:
            by_series[t.split("-")[0]].append(p)
    venue = sum(pool(p) for p in progs)
    ranked = sorted(by_series.items(), key=lambda kv: -sum(pool(p) for p in kv[1]))
    print(f"active liquidity programs={len(progs)}  series={len(by_series)}  "
          f"venue=${venue:,.0f}/day", flush=True)

    results = []
    for si, (ser, plist) in enumerate(ranked[:TOP_SERIES]):
        ser_pool = sum(pool(p) for p in plist)
        # sample the mid of the strike list (near-money tends to sit centrally in nested ladders)
        plist_sorted = sorted(plist, key=lambda p: p.get("market_ticker") or "")
        mid = len(plist_sorted) // 2
        idxs = sorted({max(0, mid - 1), mid, min(len(plist_sorted) - 1, mid + 1)})
        caps, rows = [], []
        for i in idxs[:MKTS_PER_SERIES]:
            p = plist_sorted[i]
            t = p["market_ticker"]
            try:
                ob = q.public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
            except Exception as e:
                rows.append({"ticker": t, "err": repr(e)[:80]})
                continue
            finally:
                time.sleep(SPACING_S)
            yl, _ = q._levels(ob.get("yes_dollars") or [])
            nl, _ = q._levels(ob.get("no_dollars") or [])
            up = pool(p)
            c, ty, tn, ok = capture(yl, nl, target_of(p), df_of(p), up)
            caps.append(c)
            rows.append({"ticker": t, "usd_day": round(up, 2), "target": target_of(p),
                         "y_book_df": round(ty, 1), "n_book_df": round(tn, 1),
                         "r3_ok": ok, "capture_usd_day": round(c, 3)})
        med = st.median(caps) if caps else 0.0
        results.append({"series": ser, "series_pool_usd_day": round(ser_pool, 1),
                        "n_markets": len(plist), "quoted_by_us": ser in ALLOW,
                        "median_capture_usd_day": round(med, 3),
                        "max_capture_usd_day": round(max(caps), 3) if caps else 0.0,
                        "samples": rows})
        print(f"[{si+1:2d}/{TOP_SERIES}] {ser:24s} pool=${ser_pool:8,.0f}/d "
              f"n={len(plist):4d} med_capture=${med:7.3f}/d "
              f"{'<< OURS' if ser in ALLOW else ''}", flush=True)

    results.sort(key=lambda r: -r["median_capture_usd_day"])
    json.dump({"venue_usd_day": round(venue, 1), "n_series": len(by_series),
               "n_programs": len(progs), "join_ct": JOIN_CT, "results": results},
              open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}", flush=True)
    print("\n=== RANKED BY MODELLED CAPTURE (20ct join at reference) ===", flush=True)
    for r in results[:20]:
        print(f"  ${r['median_capture_usd_day']:7.3f}/d  pool=${r['series_pool_usd_day']:8,.0f}/d  "
              f"{r['series']:24s} {'OURS' if r['quoted_by_us'] else ''}", flush=True)


if __name__ == "__main__":
    sys.exit(main())

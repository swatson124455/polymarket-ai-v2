#!/usr/bin/env python3
"""PER-SERIES REWARD-DENSITY CENSUS for the 14 allowlisted series — READ-ONLY, public API,
no keys, NEVER trades. R1 ($/day normalisation) + R3 (two-sided MARKET-level exclusion) +
maker-fee status + structure/toxicity classification.

NEW FILE. Does NOT import maker_kalshi_quoter.py. Replicates:
  * the DEPLOYED select_footprint late-life gate: cutoff_min = min(120, max(45, 0.6*life_min));
    drop a program if end < now + cutoff_min  (task-documented live config this session).
  * the canon R3 qualifying_walk (copied verbatim from scripts/maker_kalshi_recorder.py:111,
    the same function the live quoter/recorder price with): walk best-price-down, cumulative
    resting size >= target_size_fp, reference price must exist and be < $1.00. A MARKET is
    two-sided iff BOTH the yes book and the no book pass. A one-sided snapshot pays NOBODY
    (R3), so EARNABLE $/day counts ONLY two-sided contracts.

Because the post-gate universe of the 9 live series is small (~120 contracts total), this
fetches EVERY contract's book — the two-sided fraction is an exact census at this instant,
NOT a sample. Caveats printed with the numbers (ONE instant; temp dark; capture model is an
upper bound; queue/fill/adverse-selection invisible)."""
import json
import os
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
FEE_TYPES_PATH = os.path.join(HERE, "series_fee_types.json")
OUT = os.path.join(HERE, "allowlist_density_census.json")
SPACING_S = 0.32
_last = [0.0]

ALLOW = ["KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH",
         "KXAAAGASD", "KXAAAGASW", "KXB200MON", "KXAMSAVO", "KXH100MON",
         "KXMUSKNW", "KXCHIPBURRITO", "KXTRUMPENDORSEMENTS", "KXGENERICBALLOTVOTEHUB"]
TEMP = {"KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH"}
# DEPLOYED late-life gate (task-verified live config)
WIND_DOWN_MIN = 45
LATE_LIFE_FRAC = 0.6
MAX_ENTRY_CUTOFF_MIN = 120
LADDER_STRIKES = ("greater", "less", "greater_or_equal", "less_or_equal",
                  "greater_than", "less_than", "between")


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PUB + path,
                                 headers={"User-Agent": "kalshi-allowlist-density-census/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# --- canon R3 walk, verbatim from scripts/maker_kalshi_recorder.py:111 ------------------
def qualifying_walk(levels, target):
    if not levels or target <= 0:
        return None, [], 0.0
    lv = sorted(((float(p), float(s)) for p, s in levels if float(s) > 0),
                key=lambda x: -x[0])
    if not lv or lv[0][0] >= 1.0:
        return None, [], 0.0
    ref = lv[0][0]
    q, tot = [], 0.0
    for price, size in lv:
        q.append((price, size))
        tot += size
        if tot >= target:
            return ref, q, tot
    return None, [], 0.0


def levels(raw):
    out = []
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out.append((p, s))
    return out


def fetch_programs():
    progs, cur, seen = [], "", set()
    for _ in range(20):
        d = get("/incentive_programs?status=active&limit=10000"
                + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur or cur in seen:
            break
        seen.add(cur)
    else:
        if cur:
            print(f"  !! TRUNCATED at {len(progs)} programs — counts are LOWER BOUNDS")
    return progs


def fee_status(series, fee_tbl):
    e = fee_tbl.get(series)
    ft = (e or {}).get("fee_type")
    if not ft:
        return "UNKNOWN", None
    return ("CHARGES" if ft == "quadratic_with_maker_fees" else "FREE"), ft


def main():
    now = datetime.now(timezone.utc)
    fee_tbl = json.load(open(FEE_TYPES_PATH))
    progs = fetch_programs()

    # ---- gather post-gate programs per allowlist series --------------------------------
    want = set(ALLOW)
    per = defaultdict(list)          # series -> list of program dicts (post-gate)
    raw_ct = defaultdict(int)
    dropped_gate = defaultdict(int)
    for p in progs:
        t = p.get("market_ticker") or ""
        s = t.split("-")[0]
        if s not in want:
            continue
        raw_ct[s] += 1
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        if p.get("target_size_fp") is None or p.get("discount_factor_bps") is None:
            continue
        try:
            st, en = parse_iso(p["start_date"]), parse_iso(p["end_date"])
        except Exception:
            continue
        life_min = max((en - st).total_seconds() / 60.0, 1.0)
        cutoff = min(MAX_ENTRY_CUTOFF_MIN, max(WIND_DOWN_MIN, LATE_LIFE_FRAC * life_min))
        if en < now + timedelta(minutes=cutoff):
            dropped_gate[s] += 1
            continue
        days = max((en - st).total_seconds() / 86400.0, 1 / 24.0)
        p["_usd_day"] = ((p.get("period_reward") or 0) / 10000.0) / days
        p["_days"] = days
        p["_ticker"] = t
        per[s].append(p)

    all_tickers = [p["_ticker"] for s in ALLOW for p in per[s]]
    print(f"census @ {now.isoformat()}")
    print(f"venue: {len(progs)} active programs total; allowlist post-gate contracts: "
          f"{len(all_tickers)}")
    print(f"fetching {len(all_tickers)} order books (exact R3 census, no sampling)...\n")

    rows = []
    for s in ALLOW:
        ps = per[s]
        uds = [p["_usd_day"] for p in ps]
        # ---- R3: fetch every book, test two-sided ------------------------------------
        two_ct = 0
        earn_ud = 0.0
        book_err = 0
        strikes, ev_ticker = [], None
        two_detail = []
        for p in ps:
            t = p["_ticker"]
            tgt = float(p.get("target_size_fp") or 0)
            try:
                ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
            except Exception:
                book_err += 1
                continue
            yl = levels(ob.get("yes_dollars"))
            nl = levels(ob.get("no_dollars"))
            two = (qualifying_walk(yl, tgt)[0] is not None
                   and qualifying_walk(nl, tgt)[0] is not None)
            if two:
                two_ct += 1
                earn_ud += p["_usd_day"]
            two_detail.append({"ticker": t, "usd_day": round(p["_usd_day"], 3),
                               "two_sided": two, "target": tgt,
                               "yes_pass": qualifying_walk(yl, tgt)[0] is not None,
                               "no_pass": qualifying_walk(nl, tgt)[0] is not None})
        # ---- structure metadata (one representative market + its event) --------------
        structure = "dark" if not ps else "unknown"
        mut_ex = category = None
        if ps:
            try:
                d = get(f"/markets/{ps[0]['_ticker']}")
                mk = d.get("market") or d
                strikes = [mk.get("strike_type") or ""]
                ev_ticker = mk.get("event_ticker")
            except Exception:
                pass
            # sample strike_types across a few contracts to catch mixed ladders
            for p in ps[1:6]:
                try:
                    d = get(f"/markets/{p['_ticker']}")
                    mk = d.get("market") or d
                    strikes.append(mk.get("strike_type") or "")
                except Exception:
                    pass
            if ev_ticker:
                try:
                    e = get(f"/events/{ev_ticker}")
                    e = e.get("event") or e
                    mut_ex = e.get("mutually_exclusive")
                    category = e.get("category")
                except Exception:
                    pass
            st = [x for x in strikes if x]
            ladder = bool(st) and all(x in LADDER_STRIKES for x in st)
            if mut_ex is True:
                structure = "categorical"
            elif ladder and mut_ex is False:
                structure = "ladder"
            elif ladder:
                structure = "ladder?"
            elif st:
                structure = f"other({'/'.join(sorted(set(st)))})"

        fee, fee_type = fee_status(s, fee_tbl)
        scored = two_ct + (len(ps) - two_ct - book_err)   # books successfully fetched
        n_books = len(ps) - book_err
        rows.append({
            "series": s,
            "active_progs": len(ps),
            "raw_progs": raw_ct[s],
            "dropped_late_life": dropped_gate[s],
            "best_usd_day": max(uds) if uds else 0.0,
            "series_usd_day": sum(uds) if uds else 0.0,
            "median_usd_day": statistics.median(uds) if uds else 0.0,
            "n_books": n_books,
            "two_sided_ct": two_ct,
            "two_sided_pct": (100.0 * two_ct / n_books) if n_books else None,
            "earnable_usd_day": round(earn_ud, 3),
            "maker_fee": fee, "fee_type": fee_type,
            "structure": structure, "mutually_exclusive": mut_ex,
            "strike_types": sorted(set(x for x in strikes if x)),
            "category": category,
            "dark": s in TEMP and not ps,
            "book_err": book_err,
            "two_detail": two_detail,
        })
        r = rows[-1]
        print(f"  {s:24s} prog {len(ps):>3}  bestUD {r['best_usd_day']:>8.2f}  "
              f"sumUD {r['series_usd_day']:>8.2f}  2s {two_ct}/{n_books}"
              f"  earnUD {r['earnable_usd_day']:>8.2f}  {fee:6s} {structure}")

    json.dump({"generated_utc": now.isoformat(), "venue_programs": len(progs),
               "gate": {"wind_down_min": WIND_DOWN_MIN, "late_life_frac": LATE_LIFE_FRAC,
                        "max_entry_cutoff_min": MAX_ENTRY_CUTOFF_MIN},
               "rows": rows}, open(OUT, "w"), indent=1)

    # ---- rankings ----------------------------------------------------------------------
    print(f"\n{'='*80}\nRANK BY EARNABLE $/day (two-sided-adjusted, R3):")
    for i, r in enumerate(sorted(rows, key=lambda x: -x["earnable_usd_day"]), 1):
        print(f"  {i:>2}. {r['series']:24s} earnUD {r['earnable_usd_day']:>8.2f}  "
              f"(pool {r['series_usd_day']:>8.2f}, 2s {r['two_sided_pct'] or 0:5.1f}%)")
    print(f"\nRANK BY POOL-ONLY $/day (R3-blind, for the gap):")
    for i, r in enumerate(sorted(rows, key=lambda x: -x["series_usd_day"]), 1):
        print(f"  {i:>2}. {r['series']:24s} poolUD {r['series_usd_day']:>8.2f}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

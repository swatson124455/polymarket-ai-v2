#!/usr/bin/env python3
"""SKEW COST STUDY - sandbox, READ-ONLY, NO KEYS, NO MONEY, NEVER TRADES.

THE QUESTION: every classic inventory tool moves quotes away from the touch. Under the
CFTC-filed Kalshi LIP formula an order's credit is `DF^N x size` with DF = 0.50 on our
series, so one tick of skew is nominally -50% of that order's reward credit. But the
qualifying walk is a HARD BOUNDARY: if the depth at the reference price alone already
meets Target Size, the walk stops there and an order one tick back is OUTSIDE the scored
set entirely -> credit ZERO, not halved. So the cost of skew may be a CLIFF, not a slope.

This measures which it is, on real books, using the recorder's real scoring core
(scripts/maker_kalshi_recorder.py: qualifying_walk / side_share - imported, never edited).

FOUR DELIVERABLES
  Q1  DF-cliff:   share(N ticks) / share(0) per side, N = 0..4. Cliff rate vs smooth rate.
  Q2  Breakeven:  reward $ lost per tick vs adverse selection $ avoided (from our own
                  receipts, kalshi_transactions_2026-07-23.csv).
  Q3  Asymmetric: R4 says the two sides score independently, so skewing only the
                  ACCUMULATING side keeps the reducing side's full credit. Quantify
                  (a) symmetric skew, (b) accumulating pulled, (c) accumulating floored
                  at MIN_QUOTE_CT, (d) accumulating skewed only.
  Q4  Size vs price: score is LINEAR in size, EXPONENTIAL in price offset. Which is the
                  cheaper unit of inventory control? Fill exposure is modelled from real
                  taker SWEEPS (grouped trades) against real book depth.

SCOPE / WHAT THIS DOES NOT COVER
  * Reward side is measured; the fill side is MODELLED (sweep volume vs depth ahead). The
    model assumes sweep size is independent of book state and that we join at the BACK of
    the queue at our price. Both are approximations and are stated with every number.
  * Queue position within a level is not observable. We assume worst case (behind all
    external size at our price), which is what a joining maker actually gets.
  * Our own resting orders and our own fills are inside the public tape. Not removable.
  * Instantaneous books. Payout integrates over every snapshot in a Time Period.

Run:
  python kalshi_skew_cost_study.py --sample 12     # fresh unfiltered books -> jsonl
  python kalshi_skew_cost_study.py --sweeps        # taker sweep depth/size distribution
  python kalshi_skew_cost_study.py --report        # all four tables
"""
import json
import math
import os
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "kalshi-skew-cost-study/1.0 (read-only measurement)"}
SPACING_S = 0.35

FROZEN = os.path.join(HERE, "concentration_samples.jsonl")   # md5 e920bf99850279099897a79e8ad78dec
FRESH = os.path.join(HERE, "skew_samples.jsonl")
SWEEPS = os.path.join(HERE, "skew_sweeps.json")
TXN = os.path.join(HERE, "kalshi_transactions_2026-07-23.csv")

ALLOW = ("KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH",
         "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH")

TICK = 0.01
JOIN_CT = 20.0        # KALSHI_JOIN_SIZE, deployed
MIN_QUOTE_CT = 2.0    # KALSHI_MIN_QUOTE_CT, deployed (the reduce-only floor)
MAXN = 4              # ticks of skew to evaluate

_last = [0.0]


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PUB + path, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def _load_scoring():
    """Import the recorder's PURE scoring functions. READ-ONLY dependency."""
    import importlib.util
    cand = os.path.join(HERE, "..", "scripts", "maker_kalshi_recorder.py")
    spec = importlib.util.spec_from_file_location("_rec", cand)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


REC = _load_scoring()


# ------------------------------------------------------------------ sampling

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


def active_programs():
    progs, cur = [], ""
    for _ in range(6):
        d = get("/incentive_programs?status=active&limit=10000" + (f"&cursor={cur}" if cur else ""))
        ps = d.get("incentive_programs") or []
        progs += ps
        cur = d.get("next_cursor") or ""
        if not cur or not ps:
            break
    return progs


def sample_once():
    ours = [p for p in active_programs()
            if (p.get("market_ticker") or "").split("-")[0] in ALLOW]
    rows = []
    for p in ours:
        t = p.get("market_ticker")
        target = float(p.get("target_size_fp") or 0)
        df = float(p.get("discount_factor_bps") or 0) / 10000.0
        pool = float(p.get("period_reward") or 0) / 10000.0
        if target <= 0 or df <= 0 or pool <= 0:
            continue
        try:
            ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
        except Exception:
            continue
        rows.append({"t": t, "target": target, "df": df, "pool": pool,
                     "start": p.get("start_date"), "end": p.get("end_date"),
                     "yl": levels(ob.get("yes_dollars")), "nl": levels(ob.get("no_dollars"))})
    return rows


def do_sample(n):
    for i in range(n):
        rows = sample_once()
        if rows:
            ts = datetime.now(timezone.utc).isoformat()
            with open(FRESH, "a") as fh:
                fh.write(json.dumps({"ts": ts, "keep_onesided": True, "rows": rows},
                                    separators=(",", ":")) + "\n")
            one = sum(1 for r in rows if not r["yl"] or not r["nl"])
            print(f"{ts[11:19]}Z snapshot {i+1}/{n}: {len(rows)} contracts ({one} one-sided)")
        if i < n - 1:
            time.sleep(25)
    return 0


# -------------------------------------------------- taker sweep distribution

def do_sweeps():
    """Group the public tape into TAKER ORDERS and measure how deep each one walked.

    A taker order that walks the book appears as several trades sharing (ticker,
    created_time, taker_side). Kalshi's tape carries the price of every level consumed,
    so the sweep's own price range IS its penetration depth -- no book history needed.
    taker_side='no'  fills resting YES bids (price = yes_price_dollars)
    taker_side='yes' fills resting NO  bids (price = no_price_dollars)
    """
    progs = [p for p in active_programs()
             if (p.get("market_ticker") or "").split("-")[0] in ALLOW]
    tickers = sorted({p["market_ticker"] for p in progs})
    sweeps = []
    per_ticker = {}
    for t in tickers:
        cur, got = "", []
        for _ in range(4):                      # <= 4k trades/contract
            try:
                d = get(f"/markets/trades?ticker={t}&limit=1000" + (f"&cursor={cur}" if cur else ""))
            except Exception as e:
                print(f"  {t}: {e!r}")
                break
            tr = d.get("trades") or []
            got += tr
            cur = d.get("cursor") or ""
            if not cur or not tr:
                break
        per_ticker[t] = len(got)
        grp = defaultdict(list)
        for x in got:
            side = x.get("taker_side")
            px = x.get("yes_price_dollars") if side == "no" else x.get("no_price_dollars")
            try:
                px = float(px)
                ct = float(x.get("count_fp"))
            except (TypeError, ValueError):
                continue
            grp[(x.get("created_time"), side)].append((px, ct))
        for (ts, side), fills in grp.items():
            top = max(p for p, _ in fills)      # taker hits best first -> top == then-best bid
            vol = sum(c for _, c in fills)
            bydepth = defaultdict(float)
            for p, c in fills:
                bydepth[int(round((top - p) / TICK))] += c
            sweeps.append({"t": t, "series": t.split("-")[0], "ts": ts, "side": side,
                           "vol": vol, "maxdepth": max(bydepth),
                           "bydepth": {str(k): v for k, v in sorted(bydepth.items())}})
        print(f"  {t}: {len(got)} trades -> {sum(1 for s in sweeps if s['t']==t)} taker orders")
    with open(SWEEPS, "w") as fh:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(),
                   "n_tickers": len(tickers), "trades_per_ticker": per_ticker,
                   "sweeps": sweeps}, fh)
    print(f"\n{len(sweeps)} taker orders written to {os.path.basename(SWEEPS)}")
    return 0


# ------------------------------------------------------------------- scoring

def window_days(row):
    try:
        a = datetime.fromisoformat(row["start"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(row["end"].replace("Z", "+00:00"))
        d = (b - a).total_seconds() / 86400.0
        return d if d > 0 else None
    except Exception:
        return None


def side_eval(lv, target, df, price, ct):
    """(share, our_in_qualifying_set) for one side with our order at `price`."""
    if ct <= 0:
        return 0.0, False
    if price is None or price < TICK - 1e-9 or price >= 1.0:
        return 0.0, False
    share, ref, _tot, our_in = REC.side_share(lv, [(round(price, 4), ct)], target, df, TICK)
    return share, our_in


def best_bid(lv):
    return max((p for p, s in lv if s > 0), default=None)


def depth_at(lv, price):
    return sum(s for p, s in lv if abs(p - price) < TICK / 2)


def load_snaps(path):
    out = []
    try:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("rows"):
                out.append(d)
    except FileNotFoundError:
        pass
    return out


def q1_cliff(snaps, label):
    """Per-side share ratio at N ticks of skew vs at reference."""
    rows = []          # one per (snapshot, contract, side) where the side is quotable
    for s in snaps:
        for r in s["rows"]:
            for side, lv in (("yes", r["yl"]), ("no", r["nl"])):
                b = best_bid(lv)
                if b is None or b >= 1.0:
                    continue
                s0, in0 = side_eval(lv, r["target"], r["df"], b, JOIN_CT)
                if s0 <= 0:
                    # our order at reference earns nothing (book fails Target Size ->
                    # R3 exclusion). Skew cannot make it worse; excluded from the ratio
                    # population but counted so the denominator is declared.
                    rows.append({"t": r["t"], "side": side, "base0": False})
                    continue
                rec = {"t": r["t"], "series": r["t"].split("-")[0], "side": side,
                       "base0": True, "s0": s0,
                       "depth_ref": depth_at(lv, b), "target": r["target"],
                       "ref_meets_target": depth_at(lv, b) >= r["target"]}
                for n in range(1, MAXN + 1):
                    sn, inn = side_eval(lv, r["target"], r["df"], round(b - n * TICK, 4), JOIN_CT)
                    rec[f"s{n}"] = sn
                    rec[f"in{n}"] = inn
                    rec[f"r{n}"] = sn / s0
                rows.append(rec)
    live = [r for r in rows if r["base0"]]
    print(f"\n{'='*94}\nQ1  THE DF CLIFF - {label}")
    print(f"{'='*94}")
    print(f"  population: {len(rows)} quotable contract-sides over {len(snaps)} snapshots; "
          f"{len(live)} earn > 0 at reference ({100.0*len(live)/max(len(rows),1):.1f}%).")
    print(f"  The other {len(rows)-len(live)} are R3 failures at reference already "
          f"(book misses Target Size) - skew is irrelevant there.")
    if not live:
        return rows
    print(f"\n  theory: DF=0.50 -> each tick should multiply credit by 0.50 exactly.")
    print(f"\n  {'N':>2} {'mean ratio':>11} {'median':>8} {'ZEROED':>8} {'~0.50':>8} "
          f"{'>0.50':>8} {'other':>7}")
    print(f"  {'-'*2} {'-'*11} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*7}")
    for n in range(1, MAXN + 1):
        rs = [r[f"r{n}"] for r in live]
        zero = sum(1 for x in rs if x <= 1e-12)
        theo = 0.5 ** n
        near = sum(1 for x in rs if abs(x - theo) <= 0.02 * theo)
        above = sum(1 for x in rs if x > theo * 1.02)
        other = len(rs) - zero - near - above
        print(f"  {n:>2} {statistics.mean(rs):>11.4f} {statistics.median(rs):>8.4f} "
              f"{zero:>4}/{len(rs):<3} {near:>8} {above:>8} {other:>7}")
    # the mechanism
    print(f"\n  MECHANISM - is the zero explained by 'reference level alone meets Target Size'?")
    tt = sum(1 for r in live if r["ref_meets_target"])
    z1 = sum(1 for r in live if r["r1"] <= 1e-12)
    both = sum(1 for r in live if r["ref_meets_target"] and r["r1"] <= 1e-12)
    zonly = sum(1 for r in live if (not r["ref_meets_target"]) and r["r1"] <= 1e-12)
    print(f"    depth AT reference >= Target Size : {tt}/{len(live)} = {100.0*tt/len(live):.1f}%")
    print(f"    1-tick skew ZEROES our credit     : {z1}/{len(live)} = {100.0*z1/len(live):.1f}%")
    print(f"    both together                     : {both}   (zeroed but ref<target: {zonly})")
    # survival: how many ticks before the credit dies
    print(f"\n  SURVIVAL - how many ticks of skew before credit hits zero:")
    surv = Counter()
    for r in live:
        d = next((n for n in range(1, MAXN + 1) if r[f"r{n}"] <= 1e-12), MAXN + 1)
        surv[d] += 1
    for d in sorted(surv):
        lab = f"dies at N={d}" if d <= MAXN else f"survives all {MAXN}"
        print(f"    {lab:24} {surv[d]:5}  {100.0*surv[d]/len(live):5.1f}%")
    return rows


def portfolio_perday(snaps, place):
    """$/day across the whole footprint under a placement policy.

    `place(row, side, best)` -> (price, ct). Payout per contract-snapshot is
    pool x (yes_share + no_share) / 2 (R4), normalised to $/day by the Time Period
    length (R1), then averaged over snapshots and summed over contracts.
    The $1.00 R2 floor is applied to the PERIOD payout before normalising.
    """
    acc = defaultdict(list)
    for s in snaps:
        for r in s["rows"]:
            by = best_bid(r["yl"])
            bn = best_bid(r["nl"])
            ys = ns = 0.0
            if by is not None:
                p, c = place(r, "yes", by)
                ys, _ = side_eval(r["yl"], r["target"], r["df"], p, c)
            if bn is not None:
                p, c = place(r, "no", bn)
                ns, _ = side_eval(r["nl"], r["target"], r["df"], p, c)
            pay = r["pool"] * (ys + ns) / 2.0
            wd = window_days(r)
            acc[r["t"]].append((pay, wd))
    raw = floored = 0.0
    for t, xs in acc.items():
        mp = statistics.mean(x[0] for x in xs)
        wd = next((x[1] for x in xs if x[1]), None)
        if not wd:
            continue
        raw += mp / wd
        floored += (mp if mp >= 1.0 else 0.0) / wd
    return raw, floored, len(acc)


def q1b_dollars(snaps, label):
    print(f"\n{'='*94}\nQ1b  WHAT THE CLIFF COSTS IN DOLLARS - {label}")
    print(f"{'='*94}")
    print(f"  Whole-footprint $/day, {JOIN_CT:.0f} ct/side, both sides skewed N ticks.")
    print(f"  (R2 $1.00 floor applied to the Time-Period payout before the $/day "
          f"normalisation.)\n")
    print(f"  {'N':>2} {'$/day raw':>10} {'$/day R2-floored':>17} {'vs N=0':>9} "
          f"{'theory DF^N':>12}")
    print(f"  {'-'*2} {'-'*10} {'-'*17} {'-'*9} {'-'*12}")
    base = None
    for n in range(0, MAXN + 1):
        def place(r, side, best, _n=n):
            return round(best - _n * TICK, 4), JOIN_CT
        raw, fl, nm = portfolio_perday(snaps, place)
        if base is None:
            base = raw
        print(f"  {n:>2} {raw:>10.2f} {fl:>17.2f} {raw/base if base else 0:>9.3f} "
              f"{0.5**n:>12.3f}")
    print(f"\n  contracts scored: {nm}")


def q3_asymmetric(snaps, label):
    """R4: the two sides score independently and additively, so the reducing side can
    stay at reference (full credit) while only the accumulating side is controlled.

    Which side is 'accumulating' depends on inventory we do not have in a book snapshot,
    so BOTH assignments are evaluated and averaged - the result is symmetric by
    construction and does not depend on that choice.
    """
    print(f"\n{'='*94}\nQ3  ASYMMETRIC CONTROL - keep the reducing side at reference - {label}")
    print(f"{'='*94}")
    print(f"  Baseline = both sides {JOIN_CT:.0f} ct AT reference. Reported as the fraction of")
    print(f"  baseline snapshot score retained (score = yes_share + no_share, R4).\n")
    tot = defaultdict(float)
    n_obs = 0
    for s in snaps:
        for r in s["rows"]:
            by, bn = best_bid(r["yl"]), best_bid(r["nl"])
            if by is None or bn is None:
                continue
            sy0, _ = side_eval(r["yl"], r["target"], r["df"], by, JOIN_CT)
            sn0, _ = side_eval(r["nl"], r["target"], r["df"], bn, JOIN_CT)
            base = sy0 + sn0
            if base <= 0:
                continue
            n_obs += 1
            # both assignments of "which side accumulates", averaged
            for acc_lv, acc_b, red_s0 in ((r["yl"], by, sn0), (r["nl"], bn, sy0)):
                accs = {}
                for n in range(1, MAXN + 1):
                    accs[f"skew{n}"], _ = side_eval(acc_lv, r["target"], r["df"],
                                                    round(acc_b - n * TICK, 4), JOIN_CT)
                accs["pull"] = 0.0
                for ct in (MIN_QUOTE_CT, 5.0, 10.0):
                    accs[f"floor{int(ct)}"], _ = side_eval(acc_lv, r["target"], r["df"],
                                                           acc_b, ct)
                a0, _ = side_eval(acc_lv, r["target"], r["df"], acc_b, JOIN_CT)
                # (a) SYMMETRIC skew: both sides moved
                for n in range(1, MAXN + 1):
                    ry, _ = side_eval(r["yl"], r["target"], r["df"], round(by - n * TICK, 4), JOIN_CT)
                    rn, _ = side_eval(r["nl"], r["target"], r["df"], round(bn - n * TICK, 4), JOIN_CT)
                    tot[f"(a) symmetric skew {n}t"] += (ry + rn) / base / 2.0
                for k, v in accs.items():
                    if k.startswith("skew"):
                        lab = f"(d) accumulating skewed {k[4:]}t only"
                    elif k == "pull":
                        lab = "(b) accumulating PULLED (0 ct)"
                    else:
                        lab = f"(c) accumulating floored {k[5:]} ct at ref"
                    tot[lab] += (v + red_s0) / base / 2.0
                tot["__acc_base__"] += a0 / base / 2.0
    order = (["(b) accumulating PULLED (0 ct)"]
             + [f"(c) accumulating floored {c} ct at ref" for c in (2, 5, 10)]
             + [f"(d) accumulating skewed {n}t only" for n in range(1, MAXN + 1)]
             + [f"(a) symmetric skew {n}t" for n in range(1, MAXN + 1)])
    print(f"  {'policy':44} {'reward kept':>12}  {'reward lost':>11}")
    print(f"  {'-'*44} {'-'*12}  {'-'*11}")
    print(f"  {'both sides at reference (baseline)':44} {1.0:>12.3f}  {0.0:>11.3f}")
    for k in order:
        if k in tot:
            v = tot[k] / n_obs
            print(f"  {k:44} {v:>12.3f}  {1-v:>11.3f}")
    print(f"\n  n = {n_obs} two-sided contract-snapshots (each scored under both "
          f"accumulating-side assignments).")
    print(f"  Note the accumulating side is worth {tot['__acc_base__']/n_obs:.3f} of the "
          f"baseline on its own - pulling it cannot cost more than that.")


def q4_size_vs_price(snaps, label):
    """Reward cost of SIZE (linear) vs PRICE (exponential/cliff), matched on the
    modelled reduction in accumulating-side fills."""
    sw = None
    if os.path.exists(SWEEPS):
        sw = json.load(open(SWEEPS))
    print(f"\n{'='*94}\nQ4  SIZE vs PRICE - cost per unit of inventory control - {label}")
    print(f"{'='*94}")

    # --- reward side (measured) ---
    rows = []
    for s in snaps:
        for r in s["rows"]:
            for side, lv in (("yes", r["yl"]), ("no", r["nl"])):
                b = best_bid(lv)
                if b is None or b >= 1.0:
                    continue
                s0, _ = side_eval(lv, r["target"], r["df"], b, JOIN_CT)
                if s0 <= 0:
                    continue
                rec = {"lv": lv, "b": b, "target": r["target"], "df": r["df"], "s0": s0}
                for ct in (1.0, 2.0, 5.0, 10.0, 15.0):
                    rec[f"size{int(ct)}"] = side_eval(lv, r["target"], r["df"], b, ct)[0] / s0
                for n in range(1, MAXN + 1):
                    rec[f"px{n}"] = side_eval(lv, r["target"], r["df"],
                                              round(b - n * TICK, 4), JOIN_CT)[0] / s0
                # depth ahead of us at each price offset (we join at the BACK of our level)
                rec["ahead"] = [sum(sz for p, sz in lv if p >= round(b - n * TICK, 4) - 1e-9)
                                for n in range(0, MAXN + 1)]
                rows.append(rec)
    if not rows:
        print("  no scorable sides")
        return
    print(f"\n  REWARD SIDE (measured, n={len(rows)} contract-sides):")
    print(f"    {'lever':34} {'reward kept':>12}")
    print(f"    {'-'*34} {'-'*12}")
    print(f"    {'size 20 ct at reference (base)':34} {1.0:>12.3f}")
    for ct in (15, 10, 5, 2, 1):
        print(f"    {'size ' + str(ct) + ' ct at reference':34} "
              f"{statistics.mean(r[f'size{ct}'] for r in rows):>12.3f}")
    for n in range(1, MAXN + 1):
        print(f"    {'20 ct at reference - ' + str(n) + ' tick':34} "
              f"{statistics.mean(r[f'px{n}'] for r in rows):>12.3f}")

    if not sw:
        print("\n  (no sweep file - run --sweeps for the fill-side half)")
        return

    # --- fill side (modelled from real taker sweeps) ---
    vols = defaultdict(list)
    for x in sw["sweeps"]:
        vols[x["series"]].append(x["vol"])
    allv = [x["vol"] for x in sw["sweeps"]]
    print(f"\n  FILL SIDE (modelled). Taker-order size distribution from the public tape:")
    print(f"    n = {len(allv)} taker orders across {sw['n_tickers']} allowlist contracts")
    print(f"    median {statistics.median(allv):.0f} ct . mean {statistics.mean(allv):.0f} ct . "
          f"p90 {sorted(allv)[int(0.9*len(allv))]:.0f} ct . max {max(allv):.0f} ct")
    dd = Counter(x["maxdepth"] for x in sw["sweeps"])
    n_sw = len(sw["sweeps"])
    print(f"    penetration depth of a taker order (ticks below the touch it consumed):")
    for d in sorted(dd)[:6]:
        print(f"      {d} tick{'s' if d != 1 else ' '}: {dd[d]:5} = {100.0*dd[d]/n_sw:5.1f}%")
    deeper = sum(v for k, v in dd.items() if k >= 1)
    print(f"      >= 1 tick deep: {deeper}/{n_sw} = {100.0*deeper/n_sw:.1f}% of taker orders")

    print(f"\n  EXPECTED ADVERSE FILLS per taker order hitting our side")
    print(f"    fill = min(our_size, max(0, sweep_vol - external_depth_ahead_of_us))")
    print(f"    averaged over every (contract-side x taker order) pair "
          f"[{len(rows)} x {len(allv)} = {len(rows)*len(allv):,}]\n")

    def exposure(price_n, size):
        tot = 0.0
        cnt = 0
        for r in rows:
            ahead = r["ahead"][price_n]
            for v in allv:
                tot += min(size, max(0.0, v - ahead))
                cnt += 1
        return tot / cnt

    base_e = exposure(0, JOIN_CT)
    print(f"    {'lever':34} {'fills/order':>12} {'control':>9} {'reward kept':>12} "
          f"{'cost/control':>13}")
    print(f"    {'-'*34} {'-'*12} {'-'*9} {'-'*12} {'-'*13}")
    print(f"    {'20 ct at reference (base)':34} {base_e:>12.4f} {0.0:>9.3f} "
          f"{1.0:>12.3f} {'-':>13}")
    out = []
    for ct in (15, 10, 5, 2, 1):
        e = exposure(0, float(ct))
        ctrl = 1 - e / base_e if base_e else 0.0
        kept = statistics.mean(r[f"size{ct}"] for r in rows)
        out.append((f"size {ct} ct at reference", e, ctrl, kept))
    for n in range(1, MAXN + 1):
        e = exposure(n, JOIN_CT)
        ctrl = 1 - e / base_e if base_e else 0.0
        kept = statistics.mean(r[f"px{n}"] for r in rows)
        out.append((f"20 ct at reference - {n} tick", e, ctrl, kept))
    for lab, e, ctrl, kept in out:
        cpc = (1 - kept) / ctrl if ctrl > 1e-9 else float("inf")
        print(f"    {lab:34} {e:>12.4f} {ctrl:>9.3f} {kept:>12.3f} "
              f"{(f'{cpc:.3f}' if math.isfinite(cpc) else 'inf'):>13}")
    print(f"\n    cost/control = (reward lost) / (fill reduction). LOWER IS BETTER.")
    print(f"    A value of 1.00 means the lever costs exactly as much reward as the "
          f"inventory it stops.")
    return out


def q5_deployed_throttle(snaps, label):
    """What the CURRENTLY DEPLOYED throttle costs, and what THROTTLE_SMART=1 would save.

    maker_kalshi_quoter._throttled_quote (read, not edited):
        shrunk = max(MIN_QUOTE_CT, int(cnt * (1 - over)))
        if THROTTLE_SMART and step > 0 and depth_at_best >= target:
            return best, max(MIN_QUOTE_CT, int(shrunk * (1 - over)))   # stay AT ref
        return best - TICK * step, shrunk                              # step inside
    Frozen live.env: KALSHI_THROTTLE_SMART unset (= OFF), THROTTLE_STEP_TICKS=1,
    INV_SOFT_CT=15, INV_HARD_CT=60, JOIN_SIZE=20, MIN_QUOTE_CT=2.
    `over` = (|inv| - SOFT) / (HARD - SOFT), clamped to [0, 1].
    """
    print(f"\n{'='*94}\nQ5  WHAT THE DEPLOYED THROTTLE COSTS - {label}")
    print(f"{'='*94}")
    print(f"  Accumulating side only (the reducing side stays at reference either way).")
    print(f"  Reported as the fraction of that side's AT-REFERENCE credit retained.\n")
    print(f"  {'|inv| ct':>8} {'over':>5} {'size':>5} | {'SMART=0 (LIVE)':>15} "
          f"{'SMART=1':>9} {'size-only@ref':>14} | {'SMART gain':>11}")
    print(f"  {'-'*8} {'-'*5} {'-'*5} | {'-'*15} {'-'*9} {'-'*14} | {'-'*11}")
    for inv in (16, 20, 30, 45, 59):
        over = min(1.0, max(0.0, (inv - 15.0) / (60.0 - 15.0)))
        shrunk = max(MIN_QUOTE_CT, int(JOIN_CT * (1 - over)))
        smart_ct = max(MIN_QUOTE_CT, int(shrunk * (1 - over)))
        a = b = c = 0.0
        n = 0
        for s in snaps:
            for r in s["rows"]:
                for lv in (r["yl"], r["nl"]):
                    bb = best_bid(lv)
                    if bb is None or bb >= 1.0:
                        continue
                    s0, _ = side_eval(lv, r["target"], r["df"], bb, JOIN_CT)
                    if s0 <= 0:
                        continue
                    n += 1
                    deep = depth_at(lv, bb) >= r["target"] > 0
                    a += side_eval(lv, r["target"], r["df"],
                                   round(bb - TICK, 4), shrunk)[0] / s0
                    if deep:
                        b += side_eval(lv, r["target"], r["df"], bb, smart_ct)[0] / s0
                    else:
                        b += side_eval(lv, r["target"], r["df"],
                                       round(bb - TICK, 4), shrunk)[0] / s0
                    c += side_eval(lv, r["target"], r["df"], bb, shrunk)[0] / s0
        if not n:
            continue
        a, b, c = a / n, b / n, c / n
        print(f"  {inv:>8} {over:>5.2f} {shrunk:>5.0f} | {a:>15.3f} {b:>9.3f} "
              f"{c:>14.3f} | {b/a - 1 if a else 0:>10.1%}")
    print(f"\n  n = {n} scorable contract-sides per row.")
    print(f"  'size-only@ref' = same shrink, NO price step - the pure-size alternative.")


# --------------------------------------------------------------- receipts / Q2

def receipts():
    """Adverse selection per contract, from our own transaction export."""
    import csv
    rows = list(csv.DictReader(open(TXN)))
    tr = [r for r in rows if r["type"] == "trade"]
    def f(r, k):
        try:
            return float(r[k] or 0)
        except ValueError:
            return 0.0
    out = {}
    def _mk(r):
        return f(r, "open_fees_dollars") == 0 and f(r, "close_fees_dollars") == 0
    for name, sel in (("ALL", lambda r: True),
                      ("GAS", lambda r: r["market_ticker"].startswith("KXAAAGAS")),
                      ("TEMP", lambda r: r["market_ticker"].startswith("KXTEMP"))):
        for mk, msel in (("all", lambda r: True),
                         ("maker-only", _mk),
                         ("maker 07-21..22", lambda r: _mk(r)
                          and r["close_timestamp"][:10] > "2026-07-20")):
            g = [r for r in tr if sel(r) and msel(r)]
            if not g:
                continue
            qty = sum(f(r, "quantity_fp") for r in g)
            pnl = sum(f(r, "realized_pnl_without_fees_dollars") for r in g)
            notional = sum(f(r, "quantity_fp") * f(r, "entry_price_dollars") for r in g)
            out[(name, mk)] = {"n": len(g), "qty": qty, "pnl": pnl, "notional": notional,
                               "per_ct": pnl / qty if qty else 0.0,
                               "pct_notional": 100.0 * pnl / notional if notional else 0.0}
    return out


def q2_breakeven(sizeprice_rows, snaps):
    print(f"\n{'='*94}\nQ2  BREAKEVEN - how much adverse selection must a tick of skew prevent?")
    print(f"{'='*94}")
    rc = receipts()
    print(f"\n  ADVERSE SELECTION from our own receipts "
          f"({os.path.basename(TXN)}, trades 07-20..22):")
    print(f"    {'slice':22} {'trades':>7} {'contracts':>10} {'P&L $':>10} "
          f"{'$/contract':>11} {'% notional':>11}")
    print(f"    {'-'*22} {'-'*7} {'-'*10} {'-'*10} {'-'*11} {'-'*11}")
    for k in (("ALL", "all"), ("ALL", "maker-only"), ("ALL", "maker 07-21..22"),
              ("GAS", "all"), ("GAS", "maker-only"), ("GAS", "maker 07-21..22"),
              ("TEMP", "all"), ("TEMP", "maker-only"), ("TEMP", "maker 07-21..22")):
        if k not in rc:
            continue
        v = rc[k]
        print(f"    {k[0]+' '+k[1]:22} {v['n']:>7} {v['qty']:>10.0f} {v['pnl']:>10.2f} "
              f"{v['per_ct']:>11.4f} {v['pct_notional']:>11.2f}")
    print(f"\n    !! 'all' includes the 07-20 operator-directed IOC flatten (canon ?M13);")
    print(f"      'maker-only' (zero fees both legs) is the strategy's own steady state.")

    # reward loss per contract-side per day of a 1-tick skew
    raw0, _, nm = portfolio_perday(snaps, lambda r, s, b: (b, JOIN_CT))
    raw1, _, _ = portfolio_perday(snaps, lambda r, s, b: (round(b - TICK, 4), JOIN_CT))
    # asymmetric: only one side skewed -> half the two-sided delta, per R4 additivity
    loss_sym = raw0 - raw1
    print(f"\n  REWARD COST of skew, whole footprint ({nm} contracts, measured above):")
    print(f"    both sides at reference        ${raw0:.2f}/day")
    print(f"    both sides 1 tick back         ${raw1:.2f}/day   (-${loss_sym:.2f}/day, "
          f"-{100*loss_sym/raw0 if raw0 else 0:.1f}%)")
    print(f"    ONE side 1 tick back           ${raw0-loss_sym/2:.2f}/day  "
          f"(-${loss_sym/2:.2f}/day, R4 additivity)")

    print(f"\n  BREAKEVEN - contracts of adverse fill a 1-tick skew must prevent PER DAY")
    print(f"  to pay for itself, at each measured adverse-selection rate:")
    print(f"    {'adverse rate used':34} {'$/contract':>11} {'ct/day (1 side)':>17} "
          f"{'ct/day (both)':>15}")
    print(f"    {'-'*34} {'-'*11} {'-'*17} {'-'*15}")
    for k in (("GAS", "maker 07-21..22"), ("GAS", "maker-only"), ("GAS", "all"),
              ("TEMP", "maker 07-21..22"), ("TEMP", "maker-only"), ("TEMP", "all"),
              ("ALL", "maker 07-21..22"), ("ALL", "maker-only")):
        if k not in rc:
            continue
        pc = rc[k]["per_ct"]
        if pc >= -1e-9:
            print(f"    {k[0]+' '+k[1]:34} {pc:>11.4f} {'n/a (not a loss)':>17} "
                  f"{'n/a':>15}")
            continue
        print(f"    {k[0]+' '+k[1]:34} {pc:>11.4f} {loss_sym/2/abs(pc):>17.1f} "
              f"{loss_sym/abs(pc):>15.1f}")
    tot_ct = rc[("ALL", "all")]["qty"]
    print(f"\n    For scale: our ENTIRE export is {tot_ct:.0f} contracts over 3 days = "
          f"{tot_ct/3:.0f} ct/day across the whole book.")
    return rc


# ---------------------------------------------------------------------- main

def report():
    frozen = load_snaps(FROZEN)
    fresh = load_snaps(FRESH)
    print("SKEW COST STUDY - reward cost of moving a quote off reference")
    print(f"generated {datetime.now(timezone.utc).isoformat()}")
    print(f"scoring core: scripts/maker_kalshi_recorder.py (qualifying_walk / side_share), "
          f"unmodified")
    print(f"deployed shape assumed: JOIN {JOIN_CT:.0f} ct/side, MIN_QUOTE_CT "
          f"{MIN_QUOTE_CT:.0f}, tick ${TICK:.2f}")

    for snaps, label in ((frozen, "FROZEN dataset (concentration_samples.jsonl)"),
                         (fresh, "FRESH dataset (skew_samples.jsonl)")):
        if not snaps:
            print(f"\n[skip] {label}: no data")
            continue
        ser = Counter(r["t"].split("-")[0] for s in snaps for r in s["rows"])
        dfs = Counter(r["df"] for s in snaps for r in s["rows"])
        tgs = Counter(r["target"] for s in snaps for r in s["rows"])
        print(f"\n\n{'#'*94}\n# {label}")
        print(f"# {len(snaps)} snapshots {snaps[0]['ts'][:19]}Z..{snaps[-1]['ts'][:19]}Z . "
              f"{sum(len(s['rows']) for s in snaps)} contract-snapshots")
        print(f"# series {dict(ser)} . DF {dict(dfs)} . Target {dict(tgs)}\n{'#'*94}")
        q1_cliff(snaps, label)
        q1b_dollars(snaps, label)
        q3_asymmetric(snaps, label)
        q5_deployed_throttle(snaps, label)
        q4_size_vs_price(snaps, label)

    use = fresh or frozen
    if use:
        q2_breakeven(None, use)
    return 0


if __name__ == "__main__":
    if "--sample" in sys.argv:
        i = sys.argv.index("--sample")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 8
        sys.exit(do_sample(n))
    if "--sweeps" in sys.argv:
        sys.exit(do_sweeps())
    sys.exit(report())

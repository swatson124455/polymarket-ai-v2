#!/usr/bin/env python3
"""KALSHI MARKET MENU -- READ-ONLY. Which live LIP series should the clean maker be quoting?

Reward-per-contract is highest where the pool is large relative to competitor qualifying depth.
For each series we compute:
  pool_usd_day   -- R1: sum over strikes of (period_reward/10000)/window_days
  comp_depth_ct  -- DF-weighted competitor qualifying depth on the THINNER side, median over strikes
  share@Q        -- our achievable two-sided reward share resting size Q on BOTH sides of each strike
  earnable$/day  -- sum over qualifying strikes of pool_strike * two_sided_snap(Q)
Flags fee-free (series_fee_types.json) and toxicity (name + mutex threshold-ladder heuristic).
GETs only. No trades, no config, no writes to live state.
"""
import sys, json, datetime as dt, statistics
from collections import defaultdict
import kalshi_attribution_ledger as L

TICK = 0.01
SIZES = [20.0, 100.0, 300.0]
MAX_STRIKES = 12          # bound orderbook reads per series
MIN_HOURS_LEFT = 12.0
TOX_KEYWORDS = ("MENTION", "HUR", "STORM", "CANE", "ELIM", "FIGHT", "NW")

ALLOW_GASTEMP = ["KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH",
                 "KXTEMPNYCH", "KXTEMPCHIH", "KXB200MON", "KXH100MON"]
# candidate series to score (top scanner hits + allowlist gas/temp)
CANDS = ["KXFUNDRAISING", "KXRT", "KXH200MS", "KXWINGA", "KXWING", "KXDPZ",
         "KXDWTSCAST", "KXB200MS", "KXCOINBASE", "KXPRIMARYMOV", "KXVOTEPRIMARY"] + ALLOW_GASTEMP


def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def qual_depth(bids, target, df):
    """DF-weighted competitor qualifying depth + whether book reaches target.
    ref = highest bid (<1.0); walk down accumulating raw size to target; weight = DF^N * size."""
    bids = sorted(((p, s) for p, s in bids if s > 0 and p < 1.0), key=lambda x: -x[0])
    if not bids:
        return 0.0, False, None
    ref = bids[0][0]
    cum = wtot = 0.0
    for price, size in bids:
        n = round((ref - price) / TICK)
        wtot += (df ** n) * size
        cum += size
        if cum >= target:
            return wtot, True, ref
    return wtot, False, ref   # book can't reach target -> pays nobody


def share_at(comp_weighted, Q):
    """We join at BBO (N=0, weight=Q). share = Q / (comp_weighted + Q)."""
    return Q / (comp_weighted + Q) if (comp_weighted + Q) > 0 else 0.0


def main():
    now = dt.datetime.now(dt.timezone.utc)
    progs = L.get(L.P + "/incentive_programs?status=active&limit=10000").get("incentive_programs") or []
    agg = defaultdict(lambda: {"tickers": [], "min_end": None, "usd_day": 0.0})
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        t = p.get("market_ticker")
        if not t:
            continue
        s = t.split("-")[0]
        if s not in CANDS:
            continue
        try:
            st = dt.datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
            en = dt.datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
        except Exception:
            continue
        days = max((en - st).total_seconds() / 86400, 1 / 24)
        a = agg[s]
        pool = (_f(p.get("period_reward")) / 10000) / days
        a["usd_day"] += pool
        a["tickers"].append({
            "t": t, "pool": pool,
            "tgt": _f(p.get("target_size_fp")) or 1000.0,
            "df": (_f(p.get("discount_factor_bps")) / 10000) or 0.5,
        })
        a["min_end"] = en if a["min_end"] is None else min(a["min_end"], en)

    try:
        fees = json.load(open("series_fee_types.json"))
    except Exception:
        fees = {}

    rows = []
    for s in CANDS:
        a = agg.get(s)
        if not a or not a["tickers"]:
            rows.append({"series": s, "missing": True})
            continue
        hrs = (a["min_end"] - now).total_seconds() / 3600 if a["min_end"] else 0
        # sample strikes -- prefer highest-pool strikes
        strikes = sorted(a["tickers"], key=lambda x: -x["pool"])[:MAX_STRIKES]
        comp_thin = []            # thinner-side comp depth per strike
        earn = {q: 0.0 for q in SIZES}
        n_qual = 0
        for st_ in strikes:
            ob = L.get(L.P + f"/markets/{st_['t']}/orderbook").get("orderbook_fp", {}) or {}
            yb = [(_f(p), _f(sz)) for p, sz in (ob.get("yes_dollars") or [])]
            nb = [(_f(p), _f(sz)) for p, sz in (ob.get("no_dollars") or [])]
            yw, yok, _ = qual_depth(yb, st_["tgt"], st_["df"])
            nw, nok, _ = qual_depth(nb, st_["tgt"], st_["df"])
            if yok and nok:
                n_qual += 1
                comp_thin.append(max(yw, nw))   # thinner side = the one with MORE competitor weight (worse for us)
            # scale pool to per-day-per-strike over full window fraction already in pool
            for q in SIZES:
                ys = share_at(yw, q) if yok else 0.0
                ns = share_at(nw, q) if nok else 0.0
                snap = (ys + ns) / 2 if (yok and nok) else 0.0
                earn[q] += st_["pool"] * snap
        # extrapolate earnable to full strike count (we sampled top MAX_STRIKES of N)
        n_total = len(a["tickers"])
        scale = n_total / len(strikes) if strikes else 1.0
        med_comp = statistics.median(comp_thin) if comp_thin else None
        fee = fees.get(s, {})
        fee_free = (fee.get("fee_type") == "quadratic") if fee else None
        # toxicity: name keywords; allowlist gas/temp are known non-toxic threshold ladders
        toxic = any(k in s.upper() for k in TOX_KEYWORDS)
        rows.append({
            "series": s, "pool_usd_day": round(a["usd_day"], 1),
            "n_strikes": n_total, "n_qual_sampled": n_qual, "sampled": len(strikes),
            "hrs_left": round(hrs, 1), "med_comp_thin_ct": round(med_comp, 0) if med_comp is not None else None,
            "share20": round(share_at(med_comp, 20) * 100, 1) if med_comp is not None else None,
            "share100": round(share_at(med_comp, 100) * 100, 1) if med_comp is not None else None,
            "share300": round(share_at(med_comp, 300) * 100, 1) if med_comp is not None else None,
            "earn20": round(earn[20.0] * scale, 1), "earn100": round(earn[100.0] * scale, 1),
            "earn300": round(earn[300.0] * scale, 1),
            "fee_free": fee_free, "toxic": toxic,
        })

    rows_ok = [r for r in rows if not r.get("missing")]
    rows_ok.sort(key=lambda r: -r["earn100"])
    print(f"MARKET MENU @ {now.strftime('%Y-%m-%dT%H:%MZ')}  (share@Q = two-sided reward share resting Q ct/side; DF-weighted)")
    hdr = f"{'series':<18}{'pool$/d':>8}{'strk':>5}{'qual':>5}{'hrsL':>7}{'compCt':>7}{'sh20':>6}{'sh100':>6}{'sh300':>6}{'earn20':>8}{'earn100':>8}{'earn300':>8}{'fee':>6}{'tox':>4}"
    print(hdr)
    for r in rows_ok:
        print(f"{r['series']:<18}{r['pool_usd_day']:8.1f}{r['n_strikes']:5d}"
              f"{r['n_qual_sampled']:>3}/{r['sampled']:<1}{r['hrs_left']:7.1f}"
              f"{(r['med_comp_thin_ct'] if r['med_comp_thin_ct'] is not None else -1):7.0f}"
              f"{(r['share20'] or 0):5.1f}%{(r['share100'] or 0):5.1f}%{(r['share300'] or 0):5.1f}%"
              f"{r['earn20']:8.1f}{r['earn100']:8.1f}{r['earn300']:8.1f}"
              f"{('FREE' if r['fee_free'] else ('CHRG' if r['fee_free'] is False else '?')):>6}"
              f"{('Y' if r['toxic'] else '-'):>4}")
    missing = [r['series'] for r in rows if r.get('missing')]
    if missing:
        print("\nno active LIP programs right now: " + ", ".join(missing))
    print("\n[earn = sum over strikes pool_strike * two_sided_snap(Q), extrapolated to full strike count. "
          "compCt = median DF-weighted competitor qualifying depth on the WORSE side. "
          "share = Q/(comp+Q) joining at BBO. Divide earn by ~2-6x for M7 model-vs-credit calibration.]")
    json.dump(rows, open("market_menu.json", "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())

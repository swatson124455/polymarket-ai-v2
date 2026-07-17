#!/usr/bin/env python3
"""MAKER CANON — the ONLY source of Maker quantities. RUN THIS to get a number;
never quote a Maker figure from memory or a prior message.

Each quantity has ONE definition and ONE source. Estimates are TAGGED as
estimates with their method. If a value here contradicts something said
earlier, THIS is right and the earlier statement was a guess.

Usage:  python maker_canon.py            # pull the live quantities, sampled
        python maker_canon.py --defs     # print definitions only (no network)
"""
import json, sys, urllib.request, time
UA = {"User-Agent": "pa2-maker-canon/1.0"}

DEFS = """
CANONICAL DEFINITIONS (source of truth — do not redefine anywhere else):

  MIN_BET (min capital to hold a two-sided min quote)
    = rewardsMinSize  [gamma market field]  in USD.
    WHY $ = shares: to two-side quote you split N pUSD into N YES + N NO;
    a YES/NO pair always mints for exactly $1, so N shares of two-sided
    inventory costs $N. NOT minSize x price (that was the recurring bug).

  POOL  = sum(clobRewards[].rewardsDailyRate)  [gamma]  in $/day.
          The TOTAL paid to ALL makers in that market daily. It is the
          CEILING on payout, not your payout.

  BAND  = rewardsMaxSpread  [gamma]  in cents. Quotes must sit within BAND
          of midpoint to score.

  SHARE = our_score / (our_score + competitor_score),  official quadratic
          S(v,s)=((v-s)/v)^2 * size, computed from the LIVE book.
          *** ESTIMATE / SNAPSHOT ***. Time-averaged share (what you actually
          earn) is ~5x LOWER — competitors fill empty bands over the day.
          NEVER quote snapshot share as realized income.

  PAYOUT (per market, per day) = SHARE * POOL.  *** ESTIMATE *** (inherits the
          snapshot caveat). Real payout is only known from actual on-chain
          reward receipts to our wallet (pilot).

  CAPTURE (own-algo, MEASURED) = recorder-arm accrual, clean-era only.
          Current: ~$1.0-1.1K/day across ~140 markets (v2/v3/v5-P0 converge).
          Source: arm state.json 'acc', clean era. This is a MODEL estimate
          (share x pool summed), not receipts.

  COHORT_INCOME = real REWARD+MAKER_REBATE payment records, chain-verified.
          ~$513K/30d across 66 wallets. *** EXISTENCE PROOF OF THE POOL ONLY.
          NEVER a forecast — those wallets run other algorithms. ***

  TOXICITY (per sector) = fraction of fills moving >2pt AGAINST us within
          30min, fill-side inferred, resolution-jumps stripped.
          Source: mm_toxicity_all_sectors.py (re-run for current values).

RULE: quote a Maker number ONLY by (a) running this / an in-session pull, or
      (b) citing a specific in-session measurement WITH its method tag. Never
      from memory, never from a prior message. A number that contradicts an
      earlier one MUST be flagged as a correction, not slipped in.
"""

def get(u):
    for _ in range(3):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20))
        except Exception: time.sleep(1)
    return None

def S(v, s, size): return ((v - s) / v) ** 2 * size if v > 0 and 0 <= s < v else 0.0

def pull(n=12):
    cands = []
    for page in range(20):
        d = get("https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&offset=%d&order=volume24hr&ascending=false" % (page*100))
        if not d: break
        for m in d:
            try:
                pool = sum(float(r.get("rewardsDailyRate") or 0) for r in (m.get("clobRewards") or []))
                msz = float(m.get("rewardsMinSize") or 0); v = float(m.get("rewardsMaxSpread") or 0)/100.0
                toks = json.loads(m.get("clobTokenIds") or "[]")
            except Exception: continue
            if pool > 0 and msz > 0 and v > 0 and len(toks) >= 2:
                cands.append((m, pool, msz, v, toks))
        if len(d) < 100: break
    print("MAKER CANON — LIVE PULL %s  (%d rewarded markets seen)\n" % (time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()), len(cands)))
    print("%-34s %8s %9s %6s %7s %8s %7s" % ("market", "MIN_BET$", "POOL$/d", "BAND", "share*", "payout*", "ret%*"))
    for m, pool, msz, v, toks in sorted(cands, key=lambda x: -x[1])[:n]:
        yb = get("https://clob.polymarket.com/book?token_id=" + str(toks[0]))
        nb = get("https://clob.polymarket.com/book?token_id=" + str(toks[1]))
        share = 0.0
        if yb and nb:
            def lv(b, side):
                o = {}
                for x in (b.get(side) or []):
                    try:
                        p, z = float(x["price"]), float(x["size"])
                        if 0 < p < 1 and z > 0: o[p] = o.get(p, 0) + z
                    except Exception: pass
                return o
            yb_, ya_ = lv(yb, "bids"), lv(yb, "asks"); nb_, na_ = lv(nb, "bids"), lv(nb, "asks")
            if yb_ and ya_:
                bb, ba = max(yb_), min(ya_)
                if 0 < bb < ba <= 1:
                    mid = (bb+ba)/2
                    q1 = sum(S(v, abs(p-mid), z) for p, z in yb_.items()) + sum(S(v, abs(p-(1-mid)), z) for p, z in na_.items())
                    q2 = sum(S(v, abs(p-mid), z) for p, z in ya_.items()) + sum(S(v, abs(p-(1-mid)), z) for p, z in nb_.items())
                    qc = max(min(q1, q2), max(q1, q2)/3.0) if 0.10 <= mid <= 0.90 else min(q1, q2)
                    st = (ba-bb)/2; qm = S(v, max(st, v/2), msz)
                    share = qm/(qm+qc) if qm > 0 else 0.0
        payout = share * pool
        print("%-34s %8.0f %9.0f %5.1fc %6.1f%% %8.2f %6.0f%%" % (
            (m.get("question") or "?")[:34], msz, pool, v*100, 100*share, payout, payout/msz*100 if msz else 0))
    print("\n* share/payout/ret are SNAPSHOT ESTIMATES — time-averaged is ~5x lower. See --defs.")

if __name__ == "__main__":
    if "--defs" in sys.argv:
        print(DEFS)
    else:
        print(DEFS); pull()

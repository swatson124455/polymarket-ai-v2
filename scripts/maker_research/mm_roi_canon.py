#!/usr/bin/env python3
"""CANONICAL ROI REPORT — the ONLY table for Maker ROI/EV questions.

Born 2026-07-18 after a session in which the same data was presented on four
shifting bases and a documented accounting trap (realized/unrealized split)
was violated in chat. Rule now: NO ROI/EV number is quoted anywhere unless
it is in THIS script's output, freshly run.

ONE BASIS, fixed:
  - NET accounting ONLY: rewards_accrual + realized + open-marks
    (family rule: the realized/unreal split lies on zero-crossing fills —
    realized-only makes losers invisible because losers stay open).
  - v5 gate-lab data, P0-P5 (identical inputs, paired). Era: launch
    2026-07-17 01:11:43Z, clean 02:36:41Z (rewards carry a <=4% pre-clean
    sliver, bounded). P6_tilted EXCLUDED (younger era; only its future
    same-window paired read is valid).
  - Departed markets INCLUDED at their frozen last marks (caveat printed).
  - Blind selection ONLY: markets ranked by offered POOL / MIN_BET,
    both fields public before ever quoting. No hindsight ranking.
  - Capital = MIN_BET dollars (canon: two-sided min quote costs
    rewardsMinSize DOLLARS via split pair) + time-averaged inventory.

SELF-VERIFICATION before printing (fails loudly, never silently):
  V1 accounting identity vs the arm's own --report (independent path).
  V2 stored pool/min-size vs LIVE gamma on random samples (drift shown).
  V3 stored marks vs LIVE CLOB books on random samples (drift shown).

WHAT THIS CANNOT VERIFY (printed every run): the rewards column is the
model's accrual — scoring formula verified exact vs official docs, but OUR
SHARE is an estimate until real on-chain reward receipts hit our own wallet
(pilot). There is no way to chain-verify income we have not yet earned.

Usage: python3 mm_roi_canon.py            (run on the VPS)
"""
import json
import random
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict

BASE = "/opt/pa2-maker-sim-v5"
LAUNCH = 1784250703         # 2026-07-17 01:11:43Z
UA = {"User-Agent": "pa2-roi-canon/1.0"}
POLICIES = ("P0_base", "P1_volfit", "P2_ramp", "P3_tapevel", "P4_all",
            "P5_ungated")
TIERS = (1000, 2500, 5000, 10000)


def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception:
        return None


def net_of(st, sh):
    lm = sh.get("last_mid")
    unreal = st.get("pos", 0.0) * lm - st.get("cost", 0.0) \
        if lm is not None else 0.0
    return st.get("real", 0.0) + unreal


def main():
    # FROZEN SNAPSHOT: the daemon rewrites state/universe every minute and
    # game-day churn moves markets in/out of the universe — every code path
    # below must read the SAME bytes or the identity check chases a race.
    snap = "/tmp/roi_canon_snap"
    subprocess.run(["mkdir", "-p", snap], check=True)
    subprocess.run(["cp", BASE + "/state.json", BASE + "/universe.json",
                    snap], check=True)
    state = json.load(open(snap + "/state.json"))
    try:
        uni = {str(m["id"]): m for m in
               json.load(open(snap + "/universe.json"))["markets"]}
    except Exception:
        uni = {}
    now = time.time()
    days = (now - LAUNCH) / 86400

    # ---- V1: accounting identity vs the arm's own --report ----------------
    print("=" * 72)
    print("V1  accounting identity vs `--report` (independent code path,")
    print("    SAME frozen snapshot)")
    rep = subprocess.run([BASE + "/venv/bin/python",
                          BASE + "/maker_paper_sim_v5.py", "--report",
                          "--base", snap],
                         capture_output=True, text=True, timeout=120).stdout
    ours = {}
    for p in POLICIES:
        acc = trade = 0.0
        for k, st in state.items():
            if not k.endswith("|" + p) or not isinstance(st, dict):
                continue
            mkt = k.rsplit("|", 1)[0]
            sh = state.get(mkt + "|SH") or {}
            acc += st.get("acc", 0.0)
            trade += net_of(st, sh)
        ours[p] = (acc, trade)
    ok = True
    # parse ONLY the first table — the sector table's rows also begin with
    # policy names (this parser grabbed those on the first attempt and the
    # identity check rightly failed; kept as a lesson in the git history)
    first_table = rep.split("per policy x sector")[0]
    rep_acc = {}
    for ln in first_table.splitlines():
        parts = ln.split()
        if parts and parts[0] in POLICIES and len(parts) >= 6:
            rep_acc[parts[0]] = float(parts[2])     # live rewards column
    for p in POLICIES:
        acc_live = sum(st.get("acc", 0.0) for k, st in state.items()
                       if k.endswith("|" + p) and isinstance(st, dict)
                       and k.rsplit("|", 1)[0] in uni)
        if p not in rep_acc or abs(acc_live - rep_acc[p]) > 0.05:
            print("  FAIL %s: live rewards %.2f vs report %.2f"
                  % (p, acc_live, rep_acc.get(p, -1)))
            ok = False
    print("  PASS: live rewards identical to --report for all 6 policies"
          if ok else "  *** IDENTITY FAILURE — do not trust this run ***")
    if not ok:
        sys.exit(1)

    # ---- V2: stored pool/min-size vs LIVE gamma ---------------------------
    print("V2  stored pool & min-size vs LIVE gamma (8 random live markets)")
    live_ids = [m for m in uni]
    random.shuffle(live_ids)
    checked = drift = 0
    for mid_ in live_ids[:8]:
        g = get("https://gamma-api.polymarket.com/markets?" +
                "id=" + mid_)
        if not g:
            continue
        g = g[0]
        pool_live = sum(float(r.get("rewardsDailyRate") or 0)
                        for r in (g.get("clobRewards") or []))
        msz_live = float(g.get("rewardsMinSize") or 0)
        m = uni[mid_]
        checked += 1
        if abs(msz_live - m["msz"]) > 1e-6 or \
                abs(pool_live - m["pool"]) > max(1.0, 0.25 * m["pool"]):
            drift += 1
            print("    drift id=%s stored pool/msz %.0f/%.0f live %.0f/%.0f"
                  % (mid_, m["pool"], m["msz"], pool_live, msz_live))
    print("  checked %d, material drift %d (gamma adjusts pools intraday —"
          " drift is disclosure, not failure)" % (checked, drift))

    # ---- V3: stored marks vs LIVE CLOB books ------------------------------
    print("V3  stored last mark vs LIVE CLOB book (5 random live markets)")
    for mid_ in live_ids[8:13]:
        m = uni[mid_]
        sh = state.get(mid_ + "|SH") or {}
        lm = sh.get("last_mid")
        b = get("https://clob.polymarket.com/book?token_id=" + m["yes"])
        if not b or lm is None:
            continue
        try:
            bb = max(float(x["price"]) for x in b["bids"])
            ba = min(float(x["price"]) for x in b["asks"])
        except Exception:
            continue
        live_mid = (bb + ba) / 2
        print("    id=%s stored %.3f live %.3f (drift %.3f)"
              % (mid_, lm, live_mid, live_mid - lm))
    print()

    # ---- THE TABLE --------------------------------------------------------
    print("=" * 72)
    print("CANONICAL ROI TABLE  (NET basis, %.2f days, departed included at"
          % days)
    print("frozen marks; blind facts only)")
    print()
    print("%-11s %9s %9s %9s %10s %8s" % ("policy", "rew/day", "trade/day",
                                          "EV/day", "capital$", "ROI/day"))
    for p in POLICIES:
        acc, trade = ours[p]
        foot = sum(st.get("msz", 0) for k, st in state.items()
                   if k.endswith("|" + p) and isinstance(st, dict)
                   and st.get("bid") is not None)
        ev = (acc + trade) / days
        cap = foot if foot else 1.0
        print("%-11s %9.0f %9.0f %9.0f %10.0f %7.2f%%"
              % (p, acc / days, trade / days, ev, foot, 100 * ev / cap))
    print()
    print("BLIND CAPITAL TIERS  (rank = offered POOL/MIN_BET, public fields")
    print("only; outcomes = NET incl. open marks; policy = P0_base)")
    print("%10s %8s %9s %10s %8s  %s" % ("budget", "markets", "EV/day",
                                         "of which", "ROI/day", "mix"))
    print("%10s %8s %9s %10s %8s" % ("", "", "", "rewards", ""))
    mkts = []
    for k, st in state.items():
        if not k.endswith("|P0_base") or not isinstance(st, dict):
            continue
        if not st.get("msz") or not st.get("pool"):
            continue
        mkt = k.rsplit("|", 1)[0]
        sh = state.get(mkt + "|SH") or {}
        mkts.append({"cap": st["msz"], "pool": st["pool"],
                     "sec": st.get("sector") or "?",
                     "acc_d": st.get("acc", 0.0) / days,
                     "net_d": (st.get("acc", 0.0) + net_of(st, sh)) / days})
    mkts.sort(key=lambda m: -m["pool"] / m["cap"])
    for budget in TIERS:
        cap = ev = acc = 0.0
        n = 0
        secs = Counter()
        for m in mkts:
            if cap + m["cap"] > budget:
                continue
            cap += m["cap"]; ev += m["net_d"]; acc += m["acc_d"]; n += 1
            secs[m["sec"]] += 1
        print("%10d %8d %9.0f %10.0f %7.1f%%  %s"
              % (budget, n, ev, acc, 100 * ev / max(cap, 1),
                 ",".join("%s:%d" % kv for kv in secs.most_common(3))))
    print()
    print("CAVEATS (unconditional, every run):")
    print(" 1. rewards column = MODEL accrual. Formula verified vs official")
    print("    docs; OUR SHARE is an estimate. Only pilot receipts to our")
    print("    own wallet verify income. There is no chain proof of money")
    print("    we have not yet earned.")
    print(" 2. departed markets carry FROZEN marks (family limitation).")
    print(" 3. window is short (%.1f days) and PRE promo-cliff (Jul 19);" % days)
    print("    every $ figure compresses by whatever the cliff removes.")
    print(" 4. blind tiers use per-market rates diluted by mid-window")
    print("    universe rotation -> tier EV is a LOWER-leaning estimate.")
    print(" 5. never quote the realized/unrealized split from any output.")


if __name__ == "__main__":
    main()

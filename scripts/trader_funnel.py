#!/usr/bin/env python3
"""TRADER FUNNEL - the one-table trader review (operator "build it",
2026-08-25). Every roster trader on one line, every line in one of four
states, sorted so the top of the table is the decision:

    TRIAL   accruing under the anytime-valid e-process (sorted by e desc)
    PASSED  locked QUALIFIES (a proposal - composition still operator-gated)
    FAILED  locked (DOES NOT QUALIFY / futility / NOT DEMONSTRATED)
    OBS     watched with NO registered per-trader test (diagnostic only)

READ-ONLY: this is a VIEW. It writes no locks and changes no test - the
verdict-writer remains cohort5_qualification.py, and this script IMPORTS
that module's own groups, epochs and primitives so every number here is
the grader's number, never a re-implementation (MEASUREMENT_CANON
consumption rule).

    DATABASE_URL=... PYTHONPATH=<mb_readout> python scripts/trader_funnel.py
    ... --self-test
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az  # noqa: E402
import band_tracker as bt  # noqa: E402
import cohort5_qualification as cq  # noqa: E402  (groups/epochs/bars: theirs)
import mb_allocator as mal  # noqa: E402  (envelope layer, operator fracs)
import mb_canon as mc  # noqa: E402
import mb_sizer as msz  # noqa: E402  (pre-registered sizing rule, read-only)
import shadow_readout as sr  # noqa: E402

AUDIT = "/opt/pa2-shared/mb_copyable_data/chain_audit.json"


def days_since(epoch: float) -> int:
    return int((datetime.now(timezone.utc).timestamp() - epoch) / 86400)


def crack_census(review_dirs: list, roster: set, locks: set) -> list:
    """Reviewed-but-untracked addresses (operator '2 ok' 2026-08-30): a
    crack = a 0x*.json verdict file in ANY review dir whose address is
    neither on the roster nor locked, and whose verdict is not REJECT in
    every dir it appears (REJECT everywhere = deliberate exclusion, not a
    crack). Unknown/corrupt verdicts COUNT as cracks - unknown is the
    alarm (class-not-instance rule)."""
    seen = {}
    for d in review_dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not (f.startswith("0x") and f.endswith(".json")):
                continue
            a = f[:-5].lower()
            if len(a) != 42:
                continue
            try:
                v = str(json.load(open(os.path.join(d, f))).get(
                    "verdict", "UNKNOWN"))
            except (ValueError, OSError):
                v = "CORRUPT"
            seen.setdefault(a, []).append(v)
    return sorted(a for a, vs in seen.items()
                  if a not in roster and a not in locks
                  and not all(v == "REJECT" for v in vs))


def peak_concurrency(t_recs: list, res_at: dict) -> int:
    """This trader's measured peak simultaneous open positions (OK
    first-buys, one per market, exit at resolved_at else still open)."""
    now = datetime.now(timezone.utc).timestamp()
    entries = {}
    for r in t_recs:
        if not (r.get("first_buy") and r.get("verdict") == "OK"):
            continue
        tok = str(r.get("token_id"))
        ts = float(r.get("detect_ts") or 0)
        if tok and ts and tok not in entries:
            entries[tok] = ts
    events = []
    for tok, ts in entries.items():
        events.append((ts, +1))
        t_end = res_at.get(tok) or now
        if t_end > ts:
            events.append((t_end, -1))
    cur = peak = 0
    for _, d in sorted(events):
        cur += d
        peak = max(peak, cur)
    return peak


def trader_row(a: str, epoch: float, recs: list, outcomes: dict,
               frm: dict, fee_map: dict, cfg, res_at: dict) -> dict:
    """One trader's numbers in their forward window on the RULED BASIS
    (conversion 2026-09-06, operator go): atoms = per-WAGER ROI
    (ladder-aware), e = mc.roi_e_value, LCB = mc.roi_lcb — the SAME
    primitives the converted grader uses. The 'edge' key now carries
    MEAN ROI (dollars returned per dollar staked); the table header
    says roi."""
    gfwd = cq.forward_records(recs, epoch)
    t_recs = [r for r in gfwd if str(r.get("trader", "")).lower() == a]
    # correlated-atom fix (operator "fix go" 2026-09-06): evidence =
    # ONE atom per market (ladder position ROI); wagers shown for
    # transparency via n_wagers.
    seq = mc.market_position_rois(t_recs, outcomes, frm or {},
                                  fee_map or {}, epoch=epoch)
    rois = [x for _, _, x, _ in seq]
    n_wagers = sum(k for _, _, _, k in seq)
    res = sr.cohort_readout(gfwd, outcomes, epoch, a, cfg)
    # median OK first-buy fill (+ its token) = the display reference point
    # for the sizer column; trade-time sizing uses the live quote instead
    fills = sorted((float(r["shadow_fill"]), str(r.get("token_id")))
                   for r in t_recs
                   if r.get("first_buy") and r.get("verdict") == "OK"
                   and isinstance(r.get("shadow_fill"), (int, float))
                   and 0 < r["shadow_fill"] < 1)
    med = fills[len(fills) // 2] if fills else None
    return {
        "n": len(rois),
        "n_wagers": n_wagers,
        "e": mc.roi_e_value(rois, 0.0) if rois else None,
        "edge": (sum(rois) / len(rois)) if rois else None,  # mean ROI
        "ok": res.get("ok_rate"),
        "first_buys": res.get("first_buys"),
        "lcb": mc.roi_lcb(rois) if rois else None,
        "med_fill": med,
        "peak_conc": peak_concurrency(t_recs, res_at),
    }


def tier_of(a: str, locks: dict, groups_all: set) -> str:
    """Allocator tier for one roster address (operator ruling 2026-09-06:
    fractions proven:0.50 confirming:0.10, reserve uncommitted).
    proven = locked QUALIFIES; confirming = unlocked with a registered
    per-trader test (incl. an ACTIVE retrial); everything else (OBS,
    FAILED locks) = 'untiered' — the allocator gives unknown tiers $0 +
    a flag by design. Retrial verdicts under #r1 outrank the immutable
    original lock for TIER purposes only (operator go 2026-09-06)."""
    if a + "#r1" in locks:
        v = str(locks[a + "#r1"].get("verdict", ""))
        return "proven" if v.startswith("QUALIFIES") else "untiered"
    if a in getattr(cq, "RETRIAL_R1", ()) and a in locks:
        return "confirming"          # active retrial
    if a in locks:
        v = str(locks[a].get("verdict", ""))
        return "proven" if v.startswith("QUALIFIES") else "untiered"
    return "confirming" if a in groups_all else "untiered"


def alloc_params(a: str, sz, envelopes):
    """Sizer params for one trader under the envelope layer. envelopes
    None (env unset) -> full-bankroll legacy params; $0 envelope -> None
    (display shows no stake; the sizer's bankroll>0 guard would refuse).
    Pure - the run loop calls this verbatim."""
    if envelopes is None or sz is None:
        return sz
    env_a = envelopes[a]["envelope"]
    return dict(sz, bankroll=env_a) if env_a > 0.0 else None


def sizer_params_from_env():
    """All four operator parameters or None - the sizer has NO defaults and
    the funnel does not invent them (zero-base rule)."""
    names = ("MB_SIZER_BANKROLL", "MB_SIZER_KELLY_MULT",
             "MB_SIZER_CONCURRENCY", "MB_SIZER_MIN_VIABLE")
    vals = [os.environ.get(n) or None for n in names]   # "" = unset
    if any(v is None for v in vals):
        return None
    return {"bankroll": float(vals[0]), "kelly_mult": float(vals[1]),
            "concurrency": int(vals[2]), "min_viable": float(vals[3])}


def display_stake(r: dict, params, frm: dict, fee_map: dict):
    """Read-only display stake at the trader's median recorded fill.
    Book depth is a TRADE-TIME input, not knowable per-trader here, so the
    display passes a non-binding depth and says so in the header."""
    if params is None or not r.get("med_fill") or r.get("lcb") is None:
        return None
    fill, tok = r["med_fill"]
    fee, _src = mc.canon_fee(tok, fill, frm or {}, fee_map or {})
    # BASIS CONVERSION 2026-09-06: r['lcb'] is per-DOLLAR ROI; the
    # sizer's exact binary Kelly takes the per-SHARE edge. Exact map at
    # the display fill: edge = roi x fill (roi = edge/fill by
    # construction), so k = roi*fill/(1 - fill - fee).
    r = dict(r, lcb=r["lcb"] * fill)
    # divisor = the trader's own MEASURED peak concurrency; the env value
    # is a global FLOOR (conservative: larger divisor = smaller stake)
    p = dict(params)
    p["concurrency"] = max(int(r.get("peak_conc") or 1), 1,
                           p["concurrency"])
    return msz.recommend_stake_from_lcb(
        r["lcb"], fill, fee, book_depth_usd=1e12, **p)


def load_boards(firehose: str, roster: str) -> dict:
    """{wallet: board row}. The ROSTER row wins when it carries a holdout
    LCB (OUR recorded fills + real gate verdicts, never fill-modeled - the
    most realistic evidence for a roster wallet; re-review B2); otherwise
    the firehose row (their fill + haircut, fill-modeled). Tolerant of an
    absent/torn file (empty dict). Same rule as the tailable list."""
    def _load(path):
        d: dict = {}
        if path and os.path.exists(path):
            for r in az.load_records(path):
                w = str(r.get("w", "")).lower()
                if w:
                    d[w] = r
        return d
    fh, ro = _load(firehose), _load(roster)
    out = dict(fh)
    for w, r in ro.items():
        if r.get("ho_roi_lcb") is not None or w not in out:
            out[w] = r
    return out


def evidence_lcb(fwd_lcb, board_row) -> tuple:
    """(lcb, source) for the stake + $algo/day (operator ruling 2026-09-09
    ~00:3xZ 'yes 3': BACKTEST ADMITS, so the money evidence for a wallet
    with a board row is its holdout ROI LCB; forward data is the drop-off
    tripwire only). No board row -> the forward LCB, labeled 'fwd'. Pure."""
    if board_row is not None and board_row.get("ho_roi_lcb") is not None:
        return float(board_row["ho_roi_lcb"]), "bt"
    return fwd_lcb, "fwd"


def evidence_conc(r: dict, board_row) -> int:
    """Concurrency divisor from the SAME source as the evidence: the
    board's replay peak when the board supplies the LCB (the board's $algo
    used exactly that), else the forward-measured peak (re-review A5)."""
    if (board_row is not None and board_row.get("ho_roi_lcb") is not None
            and board_row.get("peak_conc_replay") is not None):
        return int(board_row["peak_conc_replay"])
    return int(r.get("peak_conc") or 0)


def evidence_rate(r: dict, days, board_row) -> tuple:
    """(n, days) for the $/day rate from the same source as the evidence:
    board holdout markets/days when a board row exists, else forward."""
    if (board_row is not None and board_row.get("ho_roi_lcb") is not None
            and board_row.get("ho_n_holdout") and board_row.get("ho_holdout_days")):
        return int(board_row["ho_n_holdout"]), float(board_row["ho_holdout_days"])
    return r.get("n"), days


def algo_day(r: dict, stake, days) -> float | None:
    """$algo/day (operator ruling 2026-09-08 #2, "track roi based on our
    wager algo not 100 flatrate"): LCB ROI x OUR sizer's stake at the
    trader's median fill x resolved-markets/day - the $100-reference
    formula with the reference replaced by the sizer's stake. $0 stake
    (unproven forward LCB) -> $0.00, never None, so the column reads
    "the algo would bet nothing yet". None only when there is no LCB /
    no rate. HYPOTHETICAL. Pure."""
    if (r.get("lcb") is None or not days or days <= 0 or not r.get("n")
            or stake is None):
        return None
    return r["lcb"] * float(stake) * (r["n"] / days)


def fmt(v, spec, dash="   -"):
    if v is None or (isinstance(v, float) and v != v):   # None or NaN
        return dash
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return dash


async def run(args) -> int:
    audit = json.load(open(args.audit))
    clean = sorted({str(a).lower() for a in audit.get("clean", [])})
    if not clean:
        print("[funnel] FATAL: empty roster - refusing to print an empty "
              "table as 'no traders'")
        return 2
    recs = az.load_records(args.log)
    assert recs, "EMPTY shadow log - ABORT"
    tokens = sorted({str(r["token_id"]) for r in recs if r.get("token_id")})
    db = await sr.fresh_outcomes(tokens)
    supp = sr.supplement_outcomes(args.supplement, tokens)
    outcomes = sr.merge_outcomes(db, supp)
    # resolved_at per token (for per-trader peak concurrency)
    res_at = {}
    try:
        graw = json.load(open(args.supplement))
        for _cid, m in graw.items():
            if not (isinstance(m, dict) and m.get("resolved_at")):
                continue
            try:
                t_end = datetime.fromisoformat(
                    str(m["resolved_at"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            for k in ("yes_token_id", "no_token_id"):
                if m.get(k):
                    res_at[str(m[k])] = t_end
    except (ValueError, OSError):
        print("[funnel] WARN: supplement unreadable for resolved_at - "
              "peak concurrency treats everything as still open")
    frm = {}
    if os.path.exists(args.fee_rate_map):
        frm = json.load(open(args.fee_rate_map))
    fee_map = {}
    if os.path.exists(args.fee_map):
        fee_map = json.load(open(args.fee_map))
    from types import SimpleNamespace as NS
    cfg = NS(max_chase=0.02, max_spread=0.05, fee=0.02,
             econ_floor=cq.EDGE_BAR, p_min=cq.P_BAR,
             min_markets=cq.N_BAR, fee_map_data=fee_map)
    locks = sr.load_locks(args.locks)
    sz = sizer_params_from_env()
    # BACKTEST ADMITS (ruling 2026-09-09 'yes 3'): the boards are the stake
    # evidence for every wallet that has a row; forward = drop-off only
    boards = load_boards(getattr(args, "firehose_board", None),
                         getattr(args, "roster_board", None))

    # group + epoch per trader, straight from the grader's module
    originals = set(cq.eligible_admits(args.deep_dive, args.rereview))
    c1 = set(cq.C1_UNTESTED)
    probes12 = set(cq.INSUFF_PROBES)
    sweep2 = set(cq.SWEEP2_ADMITS)
    cracks_grp = set(cq.CRACK_ADMITS)
    insuff57 = set(cq.SWEEP2_INSUFF)
    # cross-trader envelopes (operator ruling 2026-09-06: MB_ALLOC_TIER_FRACS
    # proven:0.50,confirming:0.10). Unset env = legacy full-bankroll display,
    # disclosed. One implementation: mb_allocator does the split.
    groups_all = c1 | probes12 | originals | sweep2 | cracks_grp | insuff57
    # ROSTER-ADMITTED groups (P2, operator ruling 2026-09-08 #1): the
    # grader's own registration rule - dive ADMIT + roster, own clock -
    # so a cohort5 wallet is a TRIAL here, not OBS, from its admission.
    roster_reg = {}
    for g in cq.roster_admit_groups(args.audit, args.admit_dirs,
                                    groups_all | set(cq.RETRIAL_R1)):
        for a in g["addresses"]:
            roster_reg[a] = (g["epoch"], f"roster-{g['name']}")
    groups_all = groups_all | set(roster_reg)
    alloc_spec = os.environ.get("MB_ALLOC_TIER_FRACS", "").strip()
    envelopes = None
    if alloc_spec and sz is not None:
        fracs = mal.parse_tier_fracs(alloc_spec)
        envelopes = mal.allocate_envelopes(
            sz["bankroll"],
            [{"key": a, "tier": tier_of(a, locks, groups_all)}
             for a in clean], fracs)
    rows = []
    retrials = set(cq.RETRIAL_R1)
    for a in clean:
        # RETRIALS (operator go 2026-09-06): a FAILED-locked trader with a
        # registered retrial shows as an ACTIVE trial until the #r1 lock
        # lands; the original lock is immutable and stays in the note.
        if a in retrials and (a + "#r1") in locks:
            lk = locks[a + "#r1"]
            v = str(lk.get("verdict", ""))
            state = "PASSED" if v.startswith("QUALIFIES") else "FAILED"
            rows.append({"a": a, "state": state, "n": lk.get("resolved"),
                         "e": None, "edge": lk.get("roi"), "ok": None,
                         "days": None,
                         "note": f"r1 locked {lk.get('locked_at')}: {v[:26]}"})
            continue
        if a in retrials and a in locks:
            r = trader_row(a, cq.BASIS_EPOCH, recs, outcomes, frm, fee_map,
                           cfg, res_at)
            ev, ev_src = evidence_lcb(r.get("lcb"), boards.get(a))
            srec = display_stake(dict(r, lcb=ev,
                                      peak_conc=evidence_conc(r, boards.get(a))),
                                 alloc_params(a, sz, envelopes), frm, fee_map)
            days = days_since(cq.BASIS_EPOCH)
            dday = None
            if r.get("lcb") is not None and days and days > 0 and r["n"]:
                dday = r["lcb"] * 100.0 * (r["n"] / days)
            stk = None if srec is None else srec["stake"]
            en, ed = evidence_rate(r, days, boards.get(a))
            rows.append({"a": a, "state": "TRIAL", "n": r["n"], "e": r["e"],
                         "edge": r["edge"], "ok": r["ok"], "lcb": r["lcb"],
                         "stake": stk,
                         "aday": algo_day({"lcb": ev, "n": en}, stk, ed),
                         "dday": dday, "days": days,
                         "note": f"retrial-r1 [{ev_src}]"})
            continue
        if a in locks:
            lk = locks[a]
            v = str(lk.get("verdict", ""))
            state = "PASSED" if v.startswith("QUALIFIES") else "FAILED"
            rows.append({"a": a, "state": state, "n": lk.get("resolved"),
                         "e": None, "edge": lk.get("edge"), "ok": None,
                         "days": None,
                         "note": f"locked {lk.get('locked_at')}: {v[:34]}"})
            continue
        if a in c1:
            epoch, grp = cq.C1_FWD_EPOCH, "c1-untested"
        elif a in probes12:
            epoch, grp = cq.REREG_EPOCH, "insuff-probe"
        elif a in originals:
            epoch, grp = cq.REREG_EPOCH, "orig-rereg"
        elif a in sweep2:
            epoch, grp = cq.SWEEP2_EPOCH, "sweep2-admit"
        elif a in cracks_grp:
            epoch, grp = cq.CRACK_EPOCH, "crack-admit"
        elif a in insuff57:
            epoch, grp = cq.INSUFF57_EPOCH, "insuff57"
        elif a in roster_reg:
            epoch, grp = roster_reg[a]
        else:
            # watched, no registered per-trader test (e.g. cohort4, fbfd
            # probe) - diagnostic only, honestly labeled
            r = trader_row(a, 0.0, recs, outcomes, frm, fee_map, cfg,
                           res_at)
            rows.append({"a": a, "state": "OBS", "n": r["n"], "e": None,
                         "edge": r["edge"], "ok": r["ok"], "days": None,
                         "note": "no per-trader test registered - "
                                 "diagnostic only"})
            continue
        # BASIS CONVERSION 2026-09-06 (operator go): scoring epoch =
        # the ONE fresh conversion epoch for every PRE-conversion trial
        # (group epochs above = provenance/labels only). PER-GROUP CLOCK
        # 2026-09-08 (ruling 1, D1 fix): a group admitted AFTER the
        # conversion runs its own clock - the grader's effective_epoch,
        # so the funnel's days/rates match the grader's exactly.
        epoch = cq.effective_epoch(epoch)
        r = trader_row(a, epoch, recs, outcomes, frm, fee_map, cfg, res_at)
        ev, ev_src = evidence_lcb(r.get("lcb"), boards.get(a))
        srec = display_stake(dict(r, lcb=ev,
                                  peak_conc=evidence_conc(r, boards.get(a))),
                             alloc_params(a, sz, envelopes), frm, fee_map)
        days = days_since(epoch)
        # OPERATOR HARDCODE 2026-09-06 ($/day is the test): LCB dollars/day
        # at the $100/market REFERENCE stake = lcb x 100 x resolved-rate.
        # HYPOTHETICAL by standing rule; rate denominator = resolved
        # markets/day (lags entry rate - disclosed in the header). This is
        # the RANKING number; the sizer $stake stays the money gate.
        dday = None
        if r.get("lcb") is not None and days and days > 0 and r["n"]:
            dday = r["lcb"] * 100.0 * (r["n"] / days)
        stk = None if srec is None else srec["stake"]
        en, ed = evidence_rate(r, days, boards.get(a))
        rows.append({"a": a, "state": "TRIAL", "n": r["n"], "e": r["e"],
                     "edge": r["edge"], "ok": r["ok"], "lcb": r["lcb"],
                     "stake": stk,
                     "aday": algo_day({"lcb": ev, "n": en}, stk, ed),
                     "dday": dday,
                     "days": days, "note": f"{grp} [{ev_src}]"})

    order = {"TRIAL": 0, "PASSED": 1, "OBS": 2, "FAILED": 3}
    # primary sort = the money metric (operator hardcode) at OUR stake
    # ($algo/day, ruling 2026-09-08 #2); the $100 reference breaks ties
    # ($0 stakes tie at zero), then e
    rows.sort(key=lambda x: (order[x["state"]],
                             -(x.get("aday") if x.get("aday") is not None
                               else -1e18),
                             -(x.get("dday") if x.get("dday") is not None
                               else -1e18),
                             -(x["e"] if x["e"] is not None else -1)))
    now = datetime.now(timezone.utc)
    n_trial = sum(1 for x in rows if x["state"] == "TRIAL")
    n_pass = sum(1 for x in rows if x["state"] == "PASSED")
    n_fail = sum(1 for x in rows if x["state"] == "FAILED")
    print(f"===== {now:%Y-%m-%dT%H:%MZ} TRADER FUNNEL - roster {len(clean)} "
          f"| TRIAL {n_trial} | PASSED {n_pass} | FAILED {n_fail} "
          f"(PASS = LCB net winnings >= ${cq.WEEKLY_FLOOR_USD:.0f}/week @ "
          f"$100/WAGER ref, ladder-aware ROI basis [conversion "
          f"2026-09-06]; futility 1wk time-based) =====")
    if sz is None:
        print("[sizer] stakes unset - set MB_SIZER_BANKROLL / "
              "MB_SIZER_KELLY_MULT / MB_SIZER_CONCURRENCY / "
              "MB_SIZER_MIN_VIABLE (operator values; sizer has no defaults)")
    else:
        print(f"[sizer] bankroll ${sz['bankroll']:.0f} x mult "
              f"{sz['kelly_mult']} / conc = max(trader's measured peak, "
              f"floor {sz['concurrency']}) @ each trader's median recorded "
              f"fill; book depth is trade-time, not applied here")
    if envelopes is not None and sz is not None:
        n_prov = sum(1 for e in envelopes.values() if e["tier"] == "proven")
        n_conf = sum(1 for e in envelopes.values()
                     if e["tier"] == "confirming")
        e_conf = next((e["envelope"] for e in envelopes.values()
                       if e["tier"] == "confirming"), 0.0)
        print(f"[alloc] {alloc_spec} (operator 2026-09-06; remainder = "
              f"uncommitted reserve) | proven {n_prov} trader(s), "
              f"confirming {n_conf} -> ${e_conf:.2f} envelope each "
              f"(down-only; stakes above use envelope, not full bankroll)")
    elif sz is not None:
        print("[alloc] MB_ALLOC_TIER_FRACS unset - stakes shown at FULL "
              "bankroll per trader (allocator built, env not sourced)")
    cr = crack_census([args.deep_dive, args.rereview, args.scout_dir],
                      set(clean), set(locks))
    if cr:
        print(f"[cracks] ALARM - {len(cr)} reviewed-but-untracked "
              f"address(es): " + ", ".join(a[:12] + ".." for a in cr))
    else:
        print("[cracks] 0 - every reviewed non-REJECT address is on the "
              "roster or locked")
    n_bt = sum(1 for x in rows if x["state"] == "TRIAL"
               and str(x.get("note", "")).endswith("[bt]"))
    print(f"[evidence] BACKTEST ADMITS (ruling 2026-09-09): $stake + $algo/day "
          f"use the board's holdout ROI LCB + holdout rate where a board row "
          f"exists ([bt], {n_bt} trial rows; boards {len(boards)} wallets), "
          f"else the forward LCB ([fwd]); forward data = drop-off tripwire "
          f"only, never the money gate")
    print("[$/day] HYPOTHETICAL - $algo/day = LCB ROI x OUR sizer stake x "
          "resolved-rate (ruling 2026-09-08 #2: our wager algo, not a flat "
          "$100; $0 when the evidence LCB is not > 0); $ref100/day = the "
          "$100/wager comparison at the FORWARD lcb x forward rate; sorted by "
          "$algo/day then $ref100/day")
    print(f"{'TRADER':<14} {'STATE':<7} {'$algo/day':>9} {'$ref/day':>9} "
          f"{'n':>4} {'e':>7} {'roi':>8} {'lcb':>8} {'$stake':>7} {'ok%':>4} "
          f"{'days':>4}  note")
    for x in rows:
        print(f"{x['a'][:12]+'..':<14} {x['state']:<7} "
              f"{fmt(x.get('aday'), '+.2f'):>9} "
              f"{fmt(x.get('dday'), '+.2f'):>9} "
              f"{fmt(x['n'], 'd'):>4} {fmt(x['e'], '.2f'):>7} "
              f"{fmt(x['edge'], '+.4f'):>8} "
              f"{fmt(x.get('lcb'), '+.4f'):>8} "
              f"{fmt(x.get('stake'), '.2f'):>7} "
              f"{fmt(None if x['ok'] is None or x['ok'] != x['ok'] else x['ok']*100, '.0f'):>4} "
              f"{fmt(x['days'], 'd'):>4}  {x['note']}")
    if n_pass:
        print(f"  >> {n_pass} PASSED = PROPOSAL(S) - composition is an "
              f"operator gate")
    return 0


def _self_test() -> int:
    print("SELF-TEST - trader funnel (offline)\n")
    ok = True
    ok1 = not (set(cq.C1_UNTESTED) & set(cq.INSUFF_PROBES)) \
        and not (set(cq.SWEEP2_ADMITS) & (set(cq.C1_UNTESTED)
                                          | set(cq.INSUFF_PROBES)))
    print(f"  [groups] registered groups disjoint : {ok1}"); ok &= ok1
    # pattern-completeness (2026-08-30 defect: sweep2 admits fell to OBS
    # as "no test registered"): EVERY address-list group the grader
    # defines must be consulted by name somewhere in this file
    import inspect as _i
    src0 = _i.getsource(sys.modules[__name__])
    groups = [n for n, v in vars(cq).items()
              if isinstance(v, (list, tuple, set)) and v
              and all(isinstance(x, str) and x.startswith("0x") for x in v)]
    missing = [n for n in groups if f"cq.{n}" not in src0]
    ok1b = bool(groups) and not missing
    print(f"  [groups] funnel consults every grader group "
          f"({len(groups)} found{', MISSING: ' + str(missing) if missing else ''}) : {ok1b}")
    ok &= ok1b
    ok2 = fmt(None, ".2f") == "   -" and fmt(1.5, ".2f") == "1.50"
    print(f"  [fmt] None renders as dash, never fake zero : {ok2}"); ok &= ok2
    order = {"TRIAL": 0, "PASSED": 1, "OBS": 2, "FAILED": 3}
    rows = [{"state": "FAILED", "e": None}, {"state": "TRIAL", "e": 2.0},
            {"state": "TRIAL", "e": 9.0}, {"state": "PASSED", "e": None}]
    rows.sort(key=lambda x: (order[x["state"]],
                             -(x["e"] if x["e"] is not None else -1)))
    ok3 = [r["state"] for r in rows] == ["TRIAL", "TRIAL", "PASSED", "FAILED"] \
        and rows[0]["e"] == 9.0
    print(f"  [sort] highest-e TRIAL first, FAILED last : {ok3}"); ok &= ok3
    import inspect
    src = inspect.getsource(sys.modules[__name__])
    # the probe must not match its own definition line: count call sites of
    # the lock-writer pattern; exactly zero may exist outside this check
    ok4 = src.count("sr." + "write_lock(") == 0
    print(f"  [read-only] funnel never writes locks : {ok4}"); ok &= ok4
    saved = {n: os.environ.pop(n, None) for n in
             ("MB_SIZER_BANKROLL", "MB_SIZER_KELLY_MULT",
              "MB_SIZER_CONCURRENCY", "MB_SIZER_MIN_VIABLE")}
    try:
        ok5 = sizer_params_from_env() is None
        os.environ.update({"MB_SIZER_BANKROLL": "500",
                           "MB_SIZER_KELLY_MULT": "0.25",
                           "MB_SIZER_CONCURRENCY": "20",
                           "MB_SIZER_MIN_VIABLE": "1"})
        p = sizer_params_from_env()
        ok5 = ok5 and p == {"bankroll": 500.0, "kelly_mult": 0.25,
                            "concurrency": 20, "min_viable": 1.0}
    finally:
        for n, v in saved.items():
            os.environ.pop(n, None)
            if v is not None:
                os.environ[n] = v
    print(f"  [sizer] env foursome all-or-nothing, no defaults : {ok5}")
    ok &= ok5
    r_neg = {"med_fill": (0.50, "tok_x"), "lcb": -0.10}
    p = {"bankroll": 500.0, "kelly_mult": 0.25, "concurrency": 20,
         "min_viable": 1.0}
    s_neg = display_stake(r_neg, p, {}, {})
    ok6 = (display_stake(r_neg, None, {}, {}) is None
           and s_neg is not None and s_neg["stake"] == 0.0
           and display_stake({"med_fill": None, "lcb": 0.1}, p, {}, {})
           is None)
    print(f"  [sizer] unproven lcb -> $0; no params -> no column : {ok6}")
    ok &= ok6
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        A = "0x" + "a" * 40   # INSUFFICIENT, untracked -> crack
        B = "0x" + "b" * 40   # REJECT everywhere -> not a crack
        C = "0x" + "c" * 40   # INSUFFICIENT but on roster -> not a crack
        D = "0x" + "d" * 40   # corrupt file, untracked -> crack (alarm)
        for a, v in ((A, "INSUFFICIENT-EVIDENCE"), (B, "REJECT"),
                     (C, "INSUFFICIENT-EVIDENCE")):
            json.dump({"verdict": v}, open(os.path.join(td, a + ".json"),
                                           "w"))
        open(os.path.join(td, D + ".json"), "w").write("{not json")
        got = crack_census([td, td + "-missing"], {C}, set())
        ok7 = got == sorted([A, D])
    print(f"  [cracks] census: INSUFF+corrupt in, REJECT/rostered out : "
          f"{ok7}")
    ok &= ok7
    locks_t = {"0xq": {"verdict": "QUALIFIES"},
               "0xf": {"verdict": "NOT DEMONSTRATED (futility)"}}
    grp = {"0xt"}
    ok8b = (tier_of("0xq", locks_t, grp) == "proven"
            and tier_of("0xf", locks_t, grp) == "untiered"
            and tier_of("0xt", locks_t, grp) == "confirming"
            and tier_of("0xo", locks_t, grp) == "untiered")
    env_t = mal.allocate_envelopes(
        500.0, [{"key": k, "tier": tier_of(k, locks_t, grp)}
                for k in ("0xq", "0xf", "0xt", "0xo")],
        mal.parse_tier_fracs("proven:0.50,confirming:0.10"))
    ok8b = (ok8b and abs(env_t["0xq"]["envelope"] - 250.0) < 1e-9
            and abs(env_t["0xt"]["envelope"] - 50.0) < 1e-9
            and env_t["0xf"]["envelope"] == 0.0
            and env_t["0xo"]["envelope"] == 0.0)
    p_full = {"bankroll": 500.0, "kelly_mult": 0.25, "concurrency": 20,
              "min_viable": 1.0}
    # lcb is per-DOLLAR ROI post-conversion; 0.5 keeps the full-
    # bankroll stake above min_viable while the envelope zeroes
    r_pos = {"med_fill": (0.50, "tok_x"), "lcb": 0.50, "peak_conc": 1}
    s_full = display_stake(r_pos, p_full, {}, {})
    p_t = alloc_params("0xt", p_full, env_t)      # $50 envelope applied
    s_env = display_stake(r_pos, p_t, {}, {})
    ok8b = (ok8b and p_t["bankroll"] == 50.0
            and s_env["stake"] < s_full["stake"]
            and alloc_params("0xt", p_full, None) is p_full   # env unset
            and alloc_params("0xf", p_full, env_t) is None)   # $0 envelope
    print(f"  [alloc] tier map + envelope split + down-only display : "
          f"{ok8b}")
    ok &= ok8b

    def _fr(tok, ts):
        return {"first_buy": True, "verdict": "OK", "token_id": tok,
                "detect_ts": ts}
    recs = [_fr("t1", 100), _fr("t2", 110), _fr("t3", 120),
            _fr("t1", 130),                       # dup token: no re-entry
            {"first_buy": False, "verdict": "OK", "token_id": "t9",
             "detect_ts": 105}]                   # not first-buy: ignored
    # t1 resolves at 115 -> overlap profile: {t1},{t1,t2},{t2},{t2,t3}
    ok8 = peak_concurrency(recs, {"t1": 115.0}) == 2 \
        and peak_concurrency(recs, {}) == 3 \
        and peak_concurrency([], {}) == 0
    print(f"  [conc] per-trader peak: overlap 2, all-open 3, empty 0 : "
          f"{ok8}")
    ok &= ok8
    # BASIS-CONVERSION pins (2026-09-06)
    import inspect as _i2
    tsrc = _i2.getsource(trader_row) + _i2.getsource(display_stake)
    src_run2 = _i2.getsource(run)
    okc = ("market_position_rois" in tsrc and "roi_lcb" in tsrc
           and "mc.wager_rois(" not in tsrc  # evidence = market atoms
           and "per_market_edges" not in tsrc
           and 'lcb=r["lcb"] * fill' in tsrc
           and "epoch = cq.effective_epoch(epoch)" in src_run2
           and "epoch = cq.BASIS_EPOCH\n" not in src_run2)  # the D1
    # hardcode is gone; effective_epoch == BASIS_EPOCH for every
    # pre-conversion group, so nothing registered today moves
    print(f"  [basis] funnel on ROI atoms + per-group clock (effective_"
          f"epoch) + sizer roi->share map : {okc}")
    ok &= okc
    # ROSTER REGISTRATION surfaced (P2): a roster-admitted wallet is a
    # TRIAL on its own clock and 'confirming' for the allocator, never OBS
    okd = ("elif a in roster_reg:" in src_run2
           and "epoch, grp = roster_reg[a]" in src_run2
           and "groups_all = groups_all | set(roster_reg)" in src_run2
           and "cq.roster_admit_groups(args.audit, args.admit_dirs" in src_run2
           and src_run2.index("cq.roster_admit_groups(")
           < src_run2.index("for a in clean:")
           and cq.effective_epoch(cq.BASIS_EPOCH + 1.0) == cq.BASIS_EPOCH + 1.0)
    print(f"  [roster-reg] roster-admitted wallets are TRIAL rows on their "
          f"own clock and count as registered for tiering : {okd}")
    ok &= okd
    # $ALGO FIRST (P3, ruling 2026-09-08 #2): the money column at OUR
    # sizer's stake leads the table and the sort; $100 ref = comparison
    r_ = {"lcb": 0.2, "n": 10}
    oke = (algo_day(r_, 5.0, 5) == 0.2 * 5.0 * 2
           and algo_day(r_, 0.0, 5) == 0.0            # unproven -> $0.00
           and algo_day(r_, None, 5) is None
           and algo_day({"lcb": None, "n": 10}, 5.0, 5) is None
           and algo_day(r_, 5.0, 0) is None
           and src_run2.count('"aday": algo_day({"lcb": ev, "n": en}, stk, ed)') == 2
           and src_run2.index("'$algo/day'") < src_run2.index("'$ref/day'")
           and src_run2.index("fmt(x.get('aday')") < src_run2.index("fmt(x.get('dday')")
           and src_run2.index('x.get("aday")') < src_run2.index('x.get("dday")'))
    print(f"  [algo] $algo/day = lcb x sizer stake x rate, $0 when unproven;"
          f" both TRIAL rows carry it; column + sort lead with it : {oke}")
    ok &= oke
    # BACKTEST ADMITS (ruling 2026-09-09 'yes 3'): evidence = board holdout
    # LCB + holdout rate when a row exists; forward otherwise; both TRIAL
    # row kinds route through it; boards loaded firehose-wins
    okf = (evidence_lcb(0.1, {"ho_roi_lcb": 0.7}) == (0.7, "bt")
           and evidence_lcb(0.1, {"ho_roi_lcb": None}) == (0.1, "fwd")
           and evidence_lcb(0.1, None) == (0.1, "fwd")
           and evidence_lcb(None, None) == (None, "fwd")
           and evidence_rate({"n": 3}, 2, {"ho_roi_lcb": 0.7, "ho_n_holdout": 40,
                                          "ho_holdout_days": 6.5}) == (40, 6.5)
           and evidence_rate({"n": 3}, 2, None) == (3, 2)
           and src_run2.count("ev, ev_src = evidence_lcb(r.get(\"lcb\"), boards.get(a))") == 2
           and src_run2.count("display_stake(dict(r, lcb=ev,") == 2
           and src_run2.count("peak_conc=evidence_conc(r, boards.get(a))") == 2
           and evidence_conc({"peak_conc": 3}, {"ho_roi_lcb": 0.2, "peak_conc_replay": 18}) == 18
           and evidence_conc({"peak_conc": 3}, {"ho_roi_lcb": None, "peak_conc_replay": 18}) == 3
           and evidence_conc({"peak_conc": 3}, None) == 3
           and src_run2.count('algo_day({"lcb": ev, "n": en}, stk, ed)') == 2
           and "boards = load_boards(" in src_run2
           and src_run2.index("boards = load_boards(") < src_run2.index("for a in clean:"))
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _d:
        fp, rp = os.path.join(_d, "f.jsonl"), os.path.join(_d, "r.jsonl")
        open(rp, "w").write(json.dumps({"w": "0xA", "ho_roi_lcb": 0.2}) + "\n"
                            + json.dumps({"w": "0xB", "ho_roi_lcb": 0.3}) + "\n")
        open(fp, "w").write(json.dumps({"w": "0xa", "ho_roi_lcb": 0.9}) + "\n"
                            + json.dumps({"w": "0xc", "ho_roi_lcb": 0.5}) + "\n")
        open(rp, "a").write(json.dumps({"w": "0xc", "ho_roi_lcb": None}) + "\n")
        b = load_boards(fp, rp)
        # roster row wins WHEN it has an LCB (0xa: 0.2 over firehose 0.9);
        # roster row without an LCB yields to firehose (0xc: 0.5)
        okf = okf and b["0xa"]["ho_roi_lcb"] == 0.2 and b["0xb"]["ho_roi_lcb"] == 0.3 \
            and b["0xc"]["ho_roi_lcb"] == 0.5 \
            and load_boards(os.path.join(_d, "none"), None) == {}
    print(f"  [evidence] board holdout LCB + rate + replay-peak divisor drive "
          f"stake/$algo when a row exists, forward otherwise; both TRIAL rows;"
          f" roster row wins when it has an LCB : {okf}")
    ok &= okf
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="one-table trader review (view)")
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    ap.add_argument("--audit", default=AUDIT)
    ap.add_argument("--deep-dive", dest="deep_dive",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive")
    ap.add_argument("--rereview",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive_rereview")
    ap.add_argument("--scout-dir", dest="scout_dir",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive_scout")
    ap.add_argument("--supplement",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "gamma_resolutions.json")
    ap.add_argument("--fee-map", dest="fee_map",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "fee_map.json")
    ap.add_argument("--fee-rate-map", dest="fee_rate_map",
                    default="/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                            "fee_rate_map.json")
    ap.add_argument("--locks",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive/"
                            "cohort5_qual_locks.json")
    ap.add_argument("--admit-dirs", dest="admit_dirs", nargs="*",
                    default=cq.ADMIT_DIRS_DEFAULT,
                    help="dive dossier dirs for roster registration "
                         "(grader's list; newest dossier decides ADMIT)")
    ap.add_argument("--firehose-board", dest="firehose_board",
                    default="/opt/pa2-shared/mb_copyable_data/backtest/"
                            "leaderboard_firehose.jsonl")
    ap.add_argument("--roster-board", dest="roster_board",
                    default="/opt/pa2-shared/mb_copyable_data/backtest/"
                            "leaderboard_roster.jsonl")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else asyncio.run(run(a)))

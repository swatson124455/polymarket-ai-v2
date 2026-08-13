#!/usr/bin/env python3
"""R1 FLOOR PROBE — money ladder rung 1 (roadmap 2026-08-13 §3). STANDALONE, not the quoter.

THE QUESTION (econ-T1, the zero-payer floor): W10 canon says a book that never reaches
the program Target pays NOBODY. This probe rests MIN-SIZE paired quotes in 1-2 quiet
pool markets for 48h and reads the venue estimates feed at +24h/+48h.
  accrual DELTA > 0 -> the floor reading is REFUTED; breadth-at-min-size is reinstated.
  accrual DELTA = 0 -> floor CONFIRMED; concentration-at-target (R2) is the only shape.
  (DELTA from the baseline snapshotted at place time — the feed is CUMULATIVE per
  program; a lifetime total would false-refute. Unmapped programs are VOID, not zero.)
Secondary: WHO fills a lone quote in a quiet book (econ-T6) — every fill is tape.

SAFETY MODEL (all binding, roadmap §4; hardened by the 4-reviewer 8-angle pass
2026-08-13 — 4 BLOCKERs + 8 MAJORs fixed pre-commit, see R1_PROBE_REVIEW doc):
  - The FIRST live order after the 08-12 wind-down is a RELIGHT-CLASS event: `place`
    refuses without --operator-go GO, on top of the client's KALSHI_LIVE_ARMED lock.
  - `place` RE-VALIDATES every plan row against the constants at the mutation boundary
    (prices in [PRICE_MIN, PRICE_MAX], y+n < 1, count == PROBE_CT, recomputed total
    <= COLLATERAL_CAP_USD). The plan file's own flags are never trusted.
  - post_only=True everywhere: this script can never take liquidity. Self-cross is
    impossible via the y+n < 1 invariant (our NO rests at yes-scale 1-n, strictly
    above our YES bid y), enforced in validate_orders at the mutation boundary.
  - State is written ATOMICALLY (tmp+fsync+replace) BEFORE the first order (plan
    tickers included), so a crash mid-place can never orphan an order out of sight of
    `halt`/`status` (probe tickers = placed ∪ planned).
  - `halt` cancels ONLY known probe tickers; with no/corrupt state it REFUSES rather
    than blanket-cancel (the account-wide kill switch is flatten_kalshi.py, by name).
  - Halt gauge: day EQUITY loss (windowed position-aware cash + inventory marked at
    book mid), never bare fill cash (Defect-13 class: cash-deployed != loss). When no
    mark exists the HEADLINE is "mark UNKNOWN" (Rule 12), never a number.
  - Entry: close_time in [36h, 8d] (LOCKED close<=8d rule); candidates with ANY
    existing position, resting order, or historical fill are refused (replay-from-flat
    contract + clean science).
  - Anchor rule: two-sided book mid preferred; a bare last_price (no timestamp) anchor
    caps BOTH quote prices at STALE_ANCHOR_PX_CAP. Never self-referential.
  - Quoter mutual exclusion FAILS CLOSED: only an explicit `inactive`/`failed` unit
    state passes; any anomaly (unknown unit, no systemd, wrong host) refuses.

Subcommands:
  plan   READ-ONLY. Verify candidates (R0b census or --tickers), anchored prices,
         worst case; writes r1_probe_plan.json.
  place  MUTATING (gated as above). Places the planned orders; snapshots the
         estimates baseline; writes r1_probe_state.json. Exit 4 = PARTIAL placement.
  status READ-ONLY. Resting/fills/marks/accrual-delta + halt check; appends to
         r1_probe_log.jsonl. Exit 3 = halt breach; exit 2 = cannot verify (freeze).
  halt   MUTATING (cancel-only). Cancel probe-ticker orders, confirmed re-read;
         reports inventory LOUDLY (never auto-sells).
"""
import argparse
import datetime
import glob
import json
import os
import subprocess
import sys

DATA = os.environ.get("KALSHI_DATA_DIR", "/opt/pa2-maker-kalshi-live")
sys.path.insert(0, DATA)

COLLATERAL_CAP_USD = 20.00
HALT_DAY_LOSS_USD = 10.00
PROBE_CT = 8                     # contracts per side (min-size presence)
MAX_MARKETS = 2
MARGIN = 0.05                    # y+n <= 1 - 2*MARGIN pair edge before clamps
PRICE_MIN, PRICE_MAX = 0.01, 0.60   # 0.45->0.60 2026-08-13: ALL quiet sub-target
                                     # books are skewed (0 candidates fit 0.45,
                                     # census-measured); PROBE_CT 10->8 keeps the
                                     # at-caps worst case $19.20 <= the $20 cap
STALE_ANCHOR_PX_CAP = 0.20       # both sides, when the anchor is a ts-less last_price
CLOSE_MIN_S = 36 * 3600          # probe needs 48h of accrual before close
CLOSE_MAX_S = 8 * 86400          # LOCKED entry rule
PLAN_MAX_AGE_S = 1800
PLAN_F = os.path.join(DATA, "r1_probe_plan.json")
STATE_F = os.path.join(DATA, "r1_probe_state.json")
LOG_F = os.path.join(DATA, "r1_probe_log.jsonl")
STOP_F = os.path.join(DATA, "STOP")
GO_WORD = "GO"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_iso(s):
    dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def atomic_write_json(path, obj):
    """Torn-file class root-fixed elsewhere twice (recorder save_map, ledger
    save_state) — same tmp+fsync+replace here. A torn state file would blind
    status AND mis-scope halt."""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def quoter_inactive():
    """Mutual-exclusion gate — FAIL CLOSED (review MAJOR: 'unknown unit', degraded
    systemd, or the wrong host must refuse, not pass)."""
    try:
        r = subprocess.run(["systemctl", "is-active",
                            "polymarket-maker-kalshi-ws.service"],
                           capture_output=True, text=True)
    except FileNotFoundError:
        print("REFUSED: systemctl not found — wrong host? The quoter gate cannot "
              "verify, failing closed.", file=sys.stderr)
        return False
    out = (r.stdout or "").strip()
    if r.returncode in (0, 3) and out in ("inactive", "failed"):
        return True
    print(f"REFUSED: quoter unit state {out!r} (rc={r.returncode}) is not "
          "inactive/failed — failing closed.", file=sys.stderr)
    return False


def client(mode):
    from maker_kalshi_client import KalshiOrderClient
    return KalshiOrderClient(mode=mode)


def resting_orders(cl):
    """Cursor-exhausted resting read via the client's OWN wrapper (review BLOCKER:
    a raw _get_paginated call had the wrong path AND iterated the dict)."""
    return (cl.get_orders(status="resting") or {}).get("orders") or []


def latest_estimates_snapshot():
    """Newest non-empty estimates file (falls back one file across a rollover)."""
    files = sorted(glob.glob(os.path.join(DATA, "estimates-*.jsonl")))
    for fp in reversed(files[-2:] if len(files) >= 2 else files):
        last = None
        with open(fp) as fh:
            for ln in fh:
                if ln.strip():
                    last = ln
        if last:
            return json.loads(last)
    return {}


def estimates_for_programs(snapshot, program_ids):
    """{program_id: cumulative accrued $} for the given ids from ONE snapshot."""
    out = {}
    for e in (snapshot or {}).get("estimates") or []:
        pid = str(e.get("program_id"))
        if pid in program_ids:
            out[pid] = out.get(pid, 0.0) + float(e.get("reward_centicents") or 0) / 10000.0
    return out


def probe_program_ids(kal, tickers):
    """{ticker: program_id} read DIRECTLY from the venue at call time (no dependence
    on the recorder's map file — review MAJOR: a map gap must be VOID, not zero)."""
    d = kal.get(f"{kal.P}/incentive_programs?status=active&limit=10000")
    out = {}
    for p in d.get("incentive_programs") or []:
        t = p.get("market_ticker")
        if t in tickers:
            out[t] = str(p.get("id") or p.get("program_id"))
    return out


def validate_orders(orders):
    """The mutation-boundary re-validation (review BLOCKER: the plan file is DATA,
    not authority — every constant is re-checked here, flags in the file ignored).
    Returns (ok, why)."""
    if not orders:
        return False, "no orders"
    if len(orders) > MAX_MARKETS:
        return False, f"{len(orders)} markets > MAX_MARKETS={MAX_MARKETS}"
    total = 0.0
    for o in orders:
        try:
            y, n, ct = float(o["y_price"]), float(o["n_price"]), int(o["count"])
        except (KeyError, TypeError, ValueError):
            return False, f"malformed order row {o!r}"
        if ct != PROBE_CT:
            return False, f"count {ct} != PROBE_CT={PROBE_CT}"
        for p in (y, n):
            if not (PRICE_MIN - 1e-9 <= p <= PRICE_MAX + 1e-9):
                return False, f"price {p} outside [{PRICE_MIN},{PRICE_MAX}]"
        if y + n >= 1.0:
            return False, f"pair y+n={y + n:.2f} >= 1 (self-cross / negative edge)"
        total += (y + n) * ct
    if total > COLLATERAL_CAP_USD + 1e-9:
        return False, f"recomputed worst case ${total:.2f} > cap ${COLLATERAL_CAP_USD:.2f}"
    return True, f"ok worst=${total:.2f}"


def book_refs(cl, ticker):
    """(best_yes_bid, best_no_bid) from the rival book, or (None, None, 'why')."""
    try:
        ob = (cl.get_orderbook(ticker) or {}).get("orderbook_fp") or {}
    except Exception:
        return None, None, "book_read_failed"
    def best(key):
        best_p = None
        for r in ob.get(key) or []:
            try:
                p, c = float(r[0]), float(r[1])
            except (TypeError, ValueError, IndexError):
                continue
            if c > 0 and (best_p is None or p > best_p):
                best_p = p
        return best_p
    return best("yes_dollars"), best("no_dollars"), "ok"


def last_price_anchor(m):
    """Bare last_price in [0.03, 0.97] or None. No recency field exists on the
    market payload, so callers MUST cap prices at STALE_ANCHOR_PX_CAP."""
    lp = m.get("last_price_dollars")
    if lp is None and m.get("last_price") is not None:
        lp = float(m["last_price"]) / 100.0
    if lp:
        lp = float(lp)
        if 0.03 <= lp <= 0.97:
            return lp
    return None


def side_price(ref, anchor, stale):
    """Price for ONE side (its own outcome scale). SCORE DISCIPLINE (real-run
    2026-08-13): reward score is DF^N x size, N = ticks below the side's
    reference — a clamped price many ticks under ref scores ~0 and would fake a
    'floor CONFIRMED'. So:
      rival ref exists : JOIN AT REF (N=0, we measure size share cleanly) — but
                         only if ref fits the risk caps; else (None, why).
      side empty       : WE become ref (N=0 by construction) — price from the
                         anchor minus MARGIN, capped (stale-anchor cap applies).
    Returns (price or None, label)."""
    if ref is not None:
        if ref > PRICE_MAX + 1e-9:
            return None, f"ref_{ref:.2f}_above_cap"
        return max(PRICE_MIN, ref), "join_ref"
    if anchor is None:
        return None, "empty_side_no_anchor"
    cap = STALE_ANCHOR_PX_CAP if stale else PRICE_MAX
    return max(PRICE_MIN, min(cap, round(anchor - MARGIN, 2))), "set_ref_from_anchor"


def build_plan(args):
    cl = client("dry_run")           # reads only
    import kalshi_attribution_ledger as kal
    now = utcnow()
    if args.tickers:
        tickers = args.tickers.split(",")
    else:
        census = json.load(open(args.census))
        cands = [c for c in census.get("candidates") or [] if c.get("usd_day_target")]
        # anchor-bearing (two-sided, however thin) candidates FIRST: a fully empty
        # side usually has no price anchor and gets refused anyway (real-run
        # 2026-08-13: the 12 quietest census rows yielded 0 placeable). Then
        # quietest rival book, then biggest pool. Scan up to 40 (bounds API reads).
        cands.sort(key=lambda c: (
            bool(c.get("anchor_needed")),
            (c.get("book") or {}).get("yes_rival_q", 1e9)
            + (c.get("book") or {}).get("no_rival_q", 1e9), -c["pool"]))
        tickers = [c["ticker"] for c in cands[:40]]
    # candidates must be FLAT and UNTRADED for us (replay-from-flat + clean science)
    positions = {p.get("ticker") for p in
                 kal.get_paginated(f"{kal.P}/portfolio/positions", "market_positions")
                 if float(p.get("position_fp") or p.get("position") or 0) != 0}
    our_fill_tickers = {(f.get("ticker") or f.get("market_ticker")) for f in
                        kal.get_paginated(f"{kal.P}/portfolio/fills", "fills")}
    plan, refused = [], []
    for t in tickers:
        if len(plan) >= MAX_MARKETS:
            break
        if t in positions or t in our_fill_tickers:
            refused.append({"ticker": t, "why": "existing_position_or_fill_history"})
            continue
        d = kal.get(f"{kal.P}/markets?tickers={t}")
        m = (d.get("markets") or [{}])[0]
        if not m or (m.get("status") or "").lower() not in ("active", "open"):
            refused.append({"ticker": t, "why": "not_open"})
            continue
        ct = m.get("close_time")
        tt = (parse_iso(ct) - now).total_seconds() if ct else -1
        if not (CLOSE_MIN_S <= tt <= CLOSE_MAX_S):
            refused.append({"ticker": t, "why": f"close_window_fail_{tt / 3600:.0f}h"})
            continue
        yb, nb, why = book_refs(cl, t)
        if why != "ok":
            refused.append({"ticker": t, "why": why})
            continue
        # anchor for any EMPTY side: two-sided mid if both refs exist (then unused),
        # else the other side's ref is NOT enough on its own (spread unknown) —
        # require a last_price anchor for the empty side. Stale cap applies to
        # last_price-derived prices only.
        lp = last_price_anchor(m)
        anchor_yes = ((yb + (1.0 - nb)) / 2.0 if (yb is not None and nb is not None)
                      else lp)
        stale = (yb is None or nb is None)          # empty side priced off last_price
        y, y_lbl = side_price(yb, anchor_yes, stale)
        n, n_lbl = side_price(
            nb, (1.0 - anchor_yes) if anchor_yes is not None else None, stale)
        if y is None or n is None:
            refused.append({"ticker": t, "why": f"yes:{y_lbl} no:{n_lbl}"})
            continue
        if y + n >= 1.0:
            refused.append({"ticker": t, "why": f"pair_crosses y={y} n={n}"})
            continue
        plan.append({"ticker": t,
                     "anchor": round(anchor_yes, 4) if anchor_yes is not None else None,
                     "pricing": {"yes": y_lbl, "no": n_lbl},
                     "y_price": round(y, 2), "n_price": round(n, 2),
                     "count": PROBE_CT,
                     "worst_case_usd": round((y + n) * PROBE_CT, 2),
                     "close_time": ct})
    ok, why = validate_orders(plan) if plan else (False, "no candidates")
    out = {"generated": now.isoformat(), "orders": plan, "refused": refused,
           "validation": why, "valid": ok}
    atomic_write_json(PLAN_F, out)
    print(json.dumps(out, indent=1))
    if not ok:
        print(f"NO PLACEABLE PLAN: {why}", file=sys.stderr)
        return 1
    return 0


def place(args):
    if args.operator_go != GO_WORD:
        print("REFUSED: relight-class event — needs --operator-go GO "
              "(operator one-word authorization, in-session).", file=sys.stderr)
        return 2
    if os.path.exists(STOP_F):
        print(f"REFUSED: STOP present at {STOP_F} — clear it by name first.",
              file=sys.stderr)
        return 2
    if not quoter_inactive():
        return 2
    if os.path.exists(STATE_F):
        print(f"REFUSED: {STATE_F} exists — a probe is (or was) live. "
              "Run halt + archive the state file first.", file=sys.stderr)
        return 2
    plan = json.load(open(PLAN_F))
    age_s = (utcnow() - parse_iso(plan["generated"])).total_seconds()
    if age_s > PLAN_MAX_AGE_S:
        print(f"REFUSED: plan is {age_s / 60:.0f} min old "
              f"(>{PLAN_MAX_AGE_S // 60}) — books move; re-run plan.", file=sys.stderr)
        return 2
    ok, why = validate_orders(plan.get("orders") or [])
    if not ok:
        print(f"REFUSED: plan re-validation failed at the mutation boundary: {why}",
              file=sys.stderr)
        return 2
    import kalshi_attribution_ledger as kal
    from maker_kalshi_client import KalshiOrderClient as _KOC
    print(f"armed with client module: {sys.modules['maker_kalshi_client'].__file__}")
    print(f"STOP checked at: {STOP_F}")
    tickers = {o["ticker"] for o in plan["orders"]}
    prog_ids = probe_program_ids(kal, tickers)
    baseline = estimates_for_programs(latest_estimates_snapshot(),
                                      set(prog_ids.values()))
    t0 = utcnow().isoformat()      # BEFORE the loop: a snipe fill 1s in is in-window
    state = {"t0": t0, "orders": [], "plan": plan,
             "program_ids": prog_ids, "estimates_baseline": baseline,
             "read_at": ["+24h", "+48h"]}
    atomic_write_json(STATE_F, state)     # BEFORE first order: no orphan window
    cl = client("live")            # raises unless KALSHI_LIVE_ARMED phrase is set
    partial = False
    try:
        for o in plan["orders"]:
            if os.path.exists(STOP_F):
                print("STOP appeared mid-place — stopping placement.", file=sys.stderr)
                partial = True
                break
            for outcome, px in (("yes", o["y_price"]), ("no", o["n_price"])):
                try:
                    r = cl.create_quote(o["ticker"], outcome, px, o["count"],
                                        post_only=True,
                                        client_order_id=f"r1probe-{o['ticker']}-{outcome}")
                except Exception as e:
                    print(f"PARTIAL PLACEMENT: {o['ticker']} {outcome}@{px} failed "
                          f"({e}) — already-placed orders REST. Run "
                          f"`r1_floor_probe.py halt` unless continuing by name.",
                          file=sys.stderr)
                    partial = True
                    break
                oid = ((r.get("order") or {}) if isinstance(r, dict) else {}).get(
                    "order_id") or (r.get("order_id") if isinstance(r, dict) else None)
                state["orders"].append(
                    {"ticker": o["ticker"], "outcome": outcome, "price": px,
                     "count": o["count"], "order_id": oid,
                     "ts": utcnow().isoformat()})
                print(f"placed {o['ticker']} {outcome}@{px} x{o['count']} id={oid}")
            if partial:
                break
    finally:
        atomic_write_json(STATE_F, state)
    if partial:
        return 4
    print(f"probe LIVE: {len(state['orders'])} orders; state -> {STATE_F}")
    return 0


def probe_tickers():
    """(tickers, state, condition). Tickers = PLACED ∪ PLANNED (review BLOCKER: an
    ambiguous write failure can leave a live order on a planned ticker that never
    made it into orders[]). condition: 'ok' | 'absent' | 'corrupt'."""
    if not os.path.exists(STATE_F):
        return [], {}, "absent"
    try:
        st = json.load(open(STATE_F))
        placed = {o["ticker"] for o in st.get("orders") or []}
        planned = {o["ticker"] for o in (st.get("plan") or {}).get("orders") or []}
        return sorted(placed | planned), st, "ok"
    except Exception as e:
        print(f"STATE FILE CORRUPT ({STATE_F}): {e} — fix or archive it by hand; "
              "refusing to guess.", file=sys.stderr)
        return [], {}, "corrupt"


def mark_value(kal, ticker, signed_pos):
    """Inventory marked at rival book mid. Returns (value$ or None, label)."""
    try:
        ob = kal.get(f"{kal.P}/markets/{ticker}/orderbook").get("orderbook_fp") or {}
    except Exception:
        return None, "book_read_failed"
    def best(key):
        vals = []
        for r in ob.get(key) or []:
            try:
                p, c = float(r[0]), float(r[1])
            except (TypeError, ValueError, IndexError):
                continue
            if c > 0:
                vals.append(p)
        return max(vals) if vals else None
    yb, nb = best("yes_dollars"), best("no_dollars")
    if yb is None or nb is None:
        return None, "mark UNKNOWN (one-sided/empty book)"
    mid = (yb + (1.0 - nb)) / 2.0
    val = signed_pos * mid if signed_pos > 0 else -signed_pos * (1.0 - mid)
    return val, f"mid {mid:.3f}"


def status(args):
    import kalshi_attribution_ledger as kal
    tickers, st, cond = probe_tickers()
    if cond == "corrupt":
        return 2
    if not tickers:
        print("no probe state; nothing to report")
        return 0
    now = utcnow()
    try:
        resting = [o for o in kal.get_paginated(f"{kal.P}/portfolio/orders", "orders",
                                                extra="&status=resting")
                   if o.get("ticker") in tickers]
        # FULL tape, replay from flat (ledger contract), THEN window the events.
        all_fills = kal.get_paginated(f"{kal.P}/portfolio/fills", "fills")
        events, positions = kal.replay_fills(
            [f for f in all_fills
             if (f.get("ticker") or f.get("market_ticker")) in tickers])
        snap = latest_estimates_snapshot()
    except Exception as e:
        print(f"COULD NOT VERIFY (venue/tape read failed: {e}) — treat as "
              "FREEZE-AND-HOLD, not as 'no breach'.", file=sys.stderr)
        return 2
    t0 = parse_iso(st["t0"])
    day_lo = now - datetime.timedelta(days=1)
    def ev_ts(e):
        return parse_iso(e["fill"].get("created_time"))
    win = [e for e in events if ev_ts(e) >= t0]
    day = [e for e in win if ev_ts(e) >= day_lo]
    inv_val, inv_labels, mark_unknown = 0.0, {}, False
    for tk, pos in positions.items():
        if abs(pos) < 1e-9:
            continue
        v, lbl = mark_value(kal, tk, pos)
        inv_labels[tk] = f"pos {pos:+g} @ {lbl}"
        if v is None:
            mark_unknown = True
        else:
            inv_val += v
    day_cash = sum(e["cash"] for e in day)
    win_cash = sum(e["cash"] for e in win)
    # equity-basis day loss: cash deployed today + what today's inventory is worth.
    day_equity = (day_cash + inv_val) if not mark_unknown else None
    prog_ids = st.get("program_ids") or {}
    accr_now = estimates_for_programs(snap, set(prog_ids.values()))
    base = st.get("estimates_baseline") or {}
    accr_delta = {pid: round(accr_now.get(pid, 0.0) - base.get(pid, 0.0), 4)
                  for pid in set(prog_ids.values())}
    missing_prog = sorted(t for t in tickers if t not in prog_ids)
    unmapped_now = sorted(pid for pid in set(prog_ids.values()) if pid not in accr_now)
    accrual_basis = "VOID" if (missing_prog or not prog_ids) else (
        "PARTIAL" if unmapped_now else "DELTA_OK")
    row = {"ts": now.isoformat(), "t0": st.get("t0"),
           "hours_in": round((now - t0).total_seconds() / 3600, 1),
           "resting": len(resting), "fills_window": len(win),
           "day_cash_deployed": round(day_cash, 4),
           "window_cash_deployed": round(win_cash, 4),
           "inventory": inv_labels,
           "inventory_value": None if mark_unknown else round(inv_val, 4),
           "day_equity_pnl": None if day_equity is None else round(day_equity, 4),
           "valuation": "mark UNKNOWN" if mark_unknown else "book-mid",
           "accrual_basis": accrual_basis,
           "accrual_delta_by_program": accr_delta,
           "accrual_delta_total": round(sum(accr_delta.values()), 4),
           "programs_missing_for_tickers": missing_prog,
           "programs_absent_from_snapshot": unmapped_now,
           "estimates_snapshot_ts": (snap or {}).get("ts"),
           "stop_present": os.path.exists(STOP_F)}
    with open(LOG_F, "a") as fh:
        fh.write(json.dumps(row) + "\n")
    print(json.dumps(row, indent=1))
    if accrual_basis != "DELTA_OK":
        print(f"ALARM: accrual reading is {accrual_basis} — a zero here is "
              "plumbing, NOT a floor confirmation.", file=sys.stderr)
    est_ts = (snap or {}).get("ts")
    stale = (not est_ts) or (now - parse_iso(est_ts)).total_seconds() > 3 * 3600
    if stale:
        print("ALARM: estimates feed STALE/absent — FREEZE-AND-HOLD (no action "
              "taken).", file=sys.stderr)
    if row["stop_present"]:
        print("HALT: STOP present — run `r1_floor_probe.py halt` now.", file=sys.stderr)
        return 3
    if day_equity is not None and day_equity <= -HALT_DAY_LOSS_USD:
        print("HALT BREACH (day equity): run `r1_floor_probe.py halt` now.",
              file=sys.stderr)
        return 3
    if day_equity is None and day_cash <= -HALT_DAY_LOSS_USD:
        # mark UNKNOWN: cash alone cannot prove a loss, but this much deployment
        # with no valuation is itself a freeze condition (Rule 12: headline the
        # blindness). Cancel-and-assess beats flying blind.
        print("HALT (mark UNKNOWN + day cash beyond halt budget): run halt, then "
              "assess inventory by hand.", file=sys.stderr)
        return 3
    return 0


def halt(args):
    tickers, st, cond = probe_tickers()
    if cond != "ok" or not tickers:
        print("REFUSED: no (or corrupt) probe state — this tool cancels PROBE "
              "tickers only. For an account-wide kill switch run flatten_kalshi.py "
              "by name.", file=sys.stderr)
        return 2
    try:
        cl = client("live")
    except RuntimeError as e:
        print(f"REFUSED: {e}\nRemediation: source live.env (KALSHI_LIVE_ARMED must "
              "hold the arm phrase), then re-run halt. Cancels need prod auth.",
              file=sys.stderr)
        return 2
    mine = [o for o in resting_orders(cl) if o.get("ticker") in tickers]
    print(f"cancelling {len(mine)} probe orders on {sorted({o.get('ticker') for o in mine})}")
    for o in mine:
        try:
            cl.cancel_order(o.get("order_id"))
            print(f"  cancel {o.get('ticker')} {o.get('order_id')}: ok")
        except Exception as e:
            print(f"  cancel {o.get('ticker')} {o.get('order_id')}: FAIL {e}",
                  file=sys.stderr)
    left = [o for o in resting_orders(cl) if o.get("ticker") in tickers]
    print(f"AFTER: probe resting={len(left)}")
    pos = (cl.get_positions() or {}).get("market_positions") or []
    inv = [p for p in pos if p.get("ticker") in tickers
           and float(p.get("position_fp") or p.get("position") or 0) != 0]
    for p in inv:
        print(f"!!! INVENTORY {p.get('ticker')} "
              f"{p.get('position_fp') or p.get('position')} — NOT auto-sold; "
              "decide by name.", file=sys.stderr)
    return 1 if left else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--census", default="/tmp/R0B_VENUE_CENSUS.json")
    p_plan.add_argument("--tickers", default="",
                        help="comma list overriding census selection")
    p_place = sub.add_parser("place")
    p_place.add_argument("--operator-go", default="",
                         help="the operator's one-word GO (relight-class gate)")
    sub.add_parser("status")
    sub.add_parser("halt")
    args = ap.parse_args()
    return {"plan": build_plan, "place": place, "status": status, "halt": halt}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

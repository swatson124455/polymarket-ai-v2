#!/usr/bin/env python3
"""KALSHI ALLOCATOR v1 — measured-accrual, $1-cliff capital allocation.

Built 2026-09-07 per operator "build the effective size gate and allocator now", to the
approved spec KALSHI_ALLOCATOR_V1_SPEC_2026-09-01.md (each requirement cites its ACDG
review finding). The principle (selection review §4, operator-ratified): allocate capital
to maximize projected CREDITED dollars per period, computed from MEASURED accrual
(est-feed tape), with the $1 cliff explicit — the model never sizes anything; measurement
(or licensed series inheritance, D8 ruling) earns size.

OFFLINE, NO ORDER AUTHORITY. Reads: venue incentive_programs + orderbooks (public GETs),
the est-feed tape (estimates-*.jsonl), the program map, quoter_state, live.env knobs,
fill-cost feed. Writes: the footprint file (atomic tmp+os.replace + flock, spec §4) and a
coverage report bucketing EVERY allowlist-family pool dollar (spec §5; class-not-instance).

Design constants (derived/cited at build — operator veto line each):
  DILUTION_BUFFER = 0.25  — measured decrease distribution over the full Aug+Sep tape
        (21 programs w/ decreases, 27 events; dec/peak p50 .004 / p75 .032 / p90 .226 /
        max .967 where the >0.9 rows are 30cc-peak dust). Covers p90 with margin. [B1]
  CLIFF_BAR = 1.50        — cliff canon design law (enter only what projects >= $1.50).
  HOLD_THRESHOLD = 0.50   — incumbent with accrued >= this keeps its slot to period end
        (eviction mid-accrual strands banked share, ACDG C2). [INFERRED value: half-way
        to the cliff; operator may re-rule.]
  STALE_H = 26            — quoter fail-closes on a footprint file older than this
        (nightly cadence + 2h slack). [C7]

Identity: (program_id, period) — accrued NEVER summed across a ticker's programs (C6);
concluded programs read from tape history, never the live feed (B3). Series-level per-ct
rate inheritance is LICENSED for cold-start (operator D8 ruling), labeled INHERITED.

Usage (on box, under sudo, live.env sourced):
  ./venv/bin/python kalshi_allocator.py            # dry run: plan + coverage, no writes
  ./venv/bin/python kalshi_allocator.py --write    # also write the footprint file
"""
import argparse
import datetime as _dt
import json
import os
import sys

LIVE = "/opt/pa2-maker-kalshi-live"

DILUTION_BUFFER = float(os.environ.get("KALSHI_ALLOC_DILUTION_BUFFER", "0.25"))
CLIFF_BAR = float(os.environ.get("KALSHI_ALLOC_CLIFF_BAR", "1.50"))
HOLD_THRESHOLD = float(os.environ.get("KALSHI_ALLOC_HOLD_THRESHOLD", "0.50"))
RATE_WINDOW_H = float(os.environ.get("KALSHI_ALLOC_RATE_WINDOW_H", "24"))
FEED_STALE_H = float(os.environ.get("KALSHI_ALLOC_FEED_STALE_H", "3"))
DAILY_ENTRIES_MAX = int(os.environ.get("KALSHI_ALLOC_DAILY_ENTRIES_MAX", "2"))
FOOTPRINT_OUT = os.environ.get("KALSHI_FOOTPRINT_FILE",
                               os.path.join(LIVE, "kalshi_footprint.json"))
COVERAGE_OUT = os.path.join(LIVE, "kalshi_coverage_report.json")


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def parse_iso(s):
    return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


# ---------------------------------------------------------------------------------------
# PURE LOGIC (unit-tested; no I/O)
# ---------------------------------------------------------------------------------------

def rate_hat_cc_min(samples, now_ts, window_h=RATE_WINDOW_H):
    """Trailing accrual rate in cc/min for ONE (program_id, period) from its feed samples
    [(epoch_s, cc), ...] time-ordered. Spec §1: staleness-decayed = only the trailing
    window counts; a DECREASE in-window flags DILUTING and caps the rate at the
    post-drop slope (never the optimistic pre-drop slope). Returns (rate, diluting).
    None rate = insufficient data (fewer than 2 in-window samples)."""
    lo = now_ts - window_h * 3600.0
    win = [(t, v) for t, v in samples if t >= lo]
    if len(win) < 2:
        return None, False
    drop_i = None
    for i in range(1, len(win)):
        if win[i][1] < win[i - 1][1]:
            drop_i = i
    if drop_i is not None and drop_i < len(win) - 1:
        seg = win[drop_i:]
    elif drop_i is not None:
        seg = win[max(0, drop_i - 1):]
    else:
        seg = win
    dt_min = (seg[-1][0] - seg[0][0]) / 60.0
    if dt_min <= 0:
        return None, drop_i is not None
    rate = (seg[-1][1] - seg[0][1]) / dt_min
    return max(0.0, rate), drop_i is not None


def project_credited_usd(accrued_cc, rate_cc_min, time_left_min,
                         dilution_buffer=DILUTION_BUFFER):
    """Spec §1: proj = accrued x (1 - DILUTION_BUFFER) + rate x time_left. Dollars."""
    acc = max(0.0, float(accrued_cc)) * (1.0 - dilution_buffer)
    rate = max(0.0, float(rate_cc_min or 0.0))
    return (acc + rate * max(0.0, float(time_left_min))) / 10000.0


def scale_rate_to_size(rate_cc_min, measured_ct, planned_ct):
    """R3 linear-in-size — the ONE licensed extrapolation. Unknown measured size -> no
    scaling (conservative: never inflate a rate whose basis size is unknown)."""
    if not rate_cc_min or not measured_ct or measured_ct <= 0 or not planned_ct:
        return rate_cc_min
    return rate_cc_min * (float(planned_ct) / float(measured_ct))


def greedy_allocate(cands, total_budget_usd, family_cap_usd):
    """Spec §3: greedy by rank_key desc under MAX_TOTAL_CAPITAL at effective committed-$,
    family cap per series, concentration-first. `cands` = list of dicts with keys
    ticker, series, rank_key, committed_usd, proj_usd, incumbent_hold (bool).
    Incumbent-holds are seated FIRST unconditionally (C2 hysteresis), then the rest by
    rank. Returns (selected list in seat order, skipped list of (cand, reason))."""
    sel, skipped = [], []
    fam = {}
    spent = 0.0
    ordered = ([c for c in cands if c.get("incumbent_hold")]
               + sorted([c for c in cands if not c.get("incumbent_hold")],
                        key=lambda c: (-c["rank_key"], c["ticker"])))
    for c in ordered:
        cost = float(c["committed_usd"])
        if spent + cost > total_budget_usd:
            skipped.append((c, "budget"))
            continue
        f = fam.get(c["series"], 0.0)
        if f + cost > family_cap_usd:
            skipped.append((c, "family_cap"))
            continue
        sel.append(c)
        spent += cost
        fam[c["series"]] = f + cost
    return sel, skipped


def build_footprint_doc(selected, now_iso):
    """Spec §4 file contract. Priority = seat order (1 = first seated = highest)."""
    return {
        "version": 1,
        "generated_utc": now_iso,
        "rows": [{"ticker": c["ticker"], "program_id": c.get("program_id", ""),
                  "mode": c.get("mode", "real"), "max_ct": int(c["max_ct"]),
                  "priority": i + 1}
                 for i, c in enumerate(selected)],
    }


def coverage_bucket(cand):
    """Spec §5 / ACDG D3: EARNING only when selected AND proj >= cliff; a sub-cliff
    accruer is EXCLUDED(cliff), never green."""
    if cand.get("selected") and cand.get("proj_usd", 0.0) >= CLIFF_BAR:
        return "EARNING"
    if cand.get("skip_reason"):
        return f"EXCLUDED({cand['skip_reason']})"
    if cand.get("proj_usd") is not None and cand["proj_usd"] < CLIFF_BAR:
        return "EXCLUDED(cliff)"
    if cand.get("no_data"):
        return "UNKNOWN"
    return "EXCLUDED(unranked)"


def atomic_write_json(path, doc):
    """Spec §4: tmp + os.replace (atomic) + best-effort flock single-writer."""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            pass                     # non-POSIX / contended: atomic replace still holds
        json.dump(doc, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------------------
# DATA LAYER (box/venue reads; not exercised by unit tests)
# ---------------------------------------------------------------------------------------

def load_program_universe(client):
    """Active liquidity programs for the allowlist family, cursor-paged (D4 tripwire)."""
    allow = set((os.environ.get("KALSHI_SERIES_ALLOW") or "").split(","))
    progs, cursor = [], ""
    for _ in range(8):
        page = client._request(
            "GET", "/trade-api/v2/incentive_programs?status=active&limit=10000"
            + (f"&cursor={cursor}" if cursor else ""), authed=False)
        progs += page.get("incentive_programs") or []
        cursor = page.get("cursor") or page.get("next_cursor") or ""
        if not cursor:
            break
    out = []
    for p in progs:
        t = p.get("market_ticker") or ""
        if t.split("-")[0] not in allow:
            continue
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        if p.get("target_size_fp") is None:
            continue
        out.append(p)
    return out


def load_feed_series(paths, wanted_ids):
    """{program_id: [(epoch_s, cc), ...]} from the estimates tape, tape HISTORY (B3)."""
    hist = {p: [] for p in wanted_ids}
    for fn in paths:
        try:
            fh = open(fn)
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    ts = parse_iso(r["ts"]).timestamp()
                except Exception:
                    continue
                for e in r.get("estimates", []):
                    p = e.get("program_id")
                    if p in hist:
                        hist[p].append((ts, int(e.get("reward_centicents") or 0)))
    return hist


def series_rate_per_ct(hist, prog_meta, quoted_ct_by_ticker):
    """D8 inheritance input: median measured cc/min/ct across a series' programs that
    have a computable rate and a known quoted size. {series: rate_per_ct}."""
    per = {}
    now_ts = _now().timestamp()
    for pid, samples in hist.items():
        meta = prog_meta.get(pid)
        if not meta:
            continue
        rate, _ = rate_hat_cc_min(samples, now_ts, window_h=1e9)   # whole-history rate
        ct = quoted_ct_by_ticker.get(meta["ticker"])
        if rate and ct:
            per.setdefault(meta["ticker"].split("-")[0], []).append(rate / ct)
    return {s: sorted(v)[len(v) // 2] for s, v in per.items() if v}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="write the footprint file (default: dry run, print only)")
    a = ap.parse_args()
    sys.path.insert(0, LIVE)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from maker_kalshi_client import KalshiOrderClient
    import maker_kalshi_quoter as q          # N5-audited: import is read-only-safe

    now = _now()
    now_ts = now.timestamp()
    c = KalshiOrderClient()
    print(f"ALLOCATOR v1  {now.isoformat()}  (write={a.write})")

    progs = load_program_universe(c)
    print(f"universe: {len(progs)} active allowlist-family programs")
    prog_meta = {}
    for p in progs:
        pid = p.get("program_id") or p.get("id") or ""
        try:
            end = parse_iso(p["end_date"])
            start = parse_iso(p["start_date"])
        except Exception:
            continue
        prog_meta[pid] = {"ticker": p["market_ticker"], "end": end, "start": start,
                          "pool_usd_day": (p.get("period_reward") or 0) / 10000.0,
                          "target": float(p["target_size_fp"])}

    # historical tape incl. concluded programs (for inheritance) + live accruals
    months = sorted({fn for fn in os.listdir(LIVE) if fn.startswith("estimates-2026")})
    paths = [os.path.join(LIVE, fn) for fn in months][-2:]
    pmap = {}
    try:
        pmap = json.load(open(os.path.join(LIVE, "kalshi_program_map.json")))
    except Exception:
        pass
    all_ids = set(prog_meta) | set(pmap)
    hist = load_feed_series(paths, all_ids)
    hist_meta = dict(prog_meta)
    for pid, v in pmap.items():
        hist_meta.setdefault(pid, {"ticker": v.get("market_ticker", ""),
                                   "end": None, "start": None,
                                   "pool_usd_day": (v.get("period_reward") or 0) / 10000.0,
                                   "target": 1000.0})

    # planned effective size per ticker: what the quoter would rest steady-state.
    # Replica of the sizing chain at current knobs: JOIN caps (per-side $), widebook cap,
    # near-money daily clamp, new-series clamp (proven series ride full rungs).
    fb = {}
    try:
        fb = json.load(open(os.path.join(LIVE, "kalshi_credit_feedback.json"))).get("series", {})
    except Exception:
        pass

    def planned_ct(ticker, life_min, best_y=0.5):
        top = q.D3_RUNGS[-1]
        series = ticker.split("-")[0]
        row = fb.get(series)
        proven = isinstance(row, dict) and (row.get("credits_n") or 0) > 0
        if q.D3_NEWSERIES_MAX_RUNG >= 0 and not proven:
            top = min(top, q.D3_RUNGS[min(q.D3_NEWSERIES_MAX_RUNG, len(q.D3_RUNGS) - 1)])
        cap_ct = int((q.MAX_MARKET_CAPITAL / 2.0) / max(best_y, 1e-6))
        eff = min(top, q.JOIN_SIZE if q.JOIN_SIZE > 0 else top, cap_ct, int(q.INV_HARD_CT))
        if q.WIDEBOOK_MODE:
            eff = min(eff, int(q.WIDEBOOK_MAX_CT))
        if q.NEARMONEY_DAILY_MAX_CT > 0 and life_min and life_min <= q.NEARMONEY_DAILY_LIFE_H * 60:
            eff = min(eff, int(q.NEARMONEY_DAILY_MAX_CT))
        return max(1, eff)

    inherit = series_rate_per_ct(hist, hist_meta, {})   # v1: per-ct via known 5ct floor runs
    # v1 note: quoted size during the measured window came from the caprank/quotes tape in
    # principle; the only measured window at fixed size is the 09-06/07 floored session
    # (5ct). Use it as the inheritance basis, labeled.
    inherit_5ct = {}
    for pid, samples in hist.items():
        meta = hist_meta.get(pid)
        if not meta or not meta.get("ticker"):
            continue
        lo = parse_iso("2026-09-06T21:05:00+00:00").timestamp()
        hi = parse_iso("2026-09-07T00:30:00+00:00").timestamp()
        win = [(t, v) for t, v in samples if lo <= t <= hi]
        if len(win) >= 2 and win[-1][1] > win[0][1]:
            dt_min = (win[-1][0] - win[0][0]) / 60.0
            if dt_min > 0:
                inherit_5ct.setdefault(meta["ticker"].split("-")[0], []).append(
                    ((win[-1][1] - win[0][1]) / dt_min) / 5.0)
    inherit = {s: sorted(v)[len(v) // 2] for s, v in inherit_5ct.items() if v}
    print(f"inheritance basis (cc/min/ct, 09-06 floored window): "
          + json.dumps({k: round(v, 4) for k, v in inherit.items()}))

    cands = []
    for pid, meta in prog_meta.items():
        t = meta["ticker"]
        life_min = None
        if meta["end"] and meta["start"]:
            life_min = (meta["end"] - meta["start"]).total_seconds() / 60.0
        time_left_min = max(0.0, (meta["end"] - now).total_seconds() / 60.0) if meta["end"] else 0.0
        samples = hist.get(pid) or []
        accrued = samples[-1][1] if samples else 0
        fresh = bool(samples) and (now_ts - samples[-1][0]) <= FEED_STALE_H * 3600.0
        rate, diluting = rate_hat_cc_min(samples, now_ts) if samples else (None, False)
        pct = planned_ct(t, life_min)
        mode = "real"
        basis = "MEASURED"
        if rate is None:
            rpc = inherit.get(t.split("-")[0])
            if rpc:
                rate = rpc * pct                 # INHERITED at planned size (D8)
                basis = "INHERITED"
            else:
                cands.append({"ticker": t, "program_id": pid, "series": t.split("-")[0],
                              "no_data": True, "proj_usd": None,
                              "pool_usd_day": meta["pool_usd_day"]})
                continue
        else:
            rate = scale_rate_to_size(rate, 5.0, pct)   # measured window ran at the 5ct floor
        proj = project_credited_usd(accrued if fresh else accrued, rate, time_left_min)
        committed = pct * 1.0                    # both-sides reservation ~ $1/pair-ct bound
        cands.append({"ticker": t, "program_id": pid, "series": t.split("-")[0],
                      "proj_usd": proj, "rate_basis": basis, "diluting": diluting,
                      "max_ct": pct, "committed_usd": committed,
                      "rank_key": proj / max(committed, 1e-6),
                      "accrued_usd": accrued / 10000.0,
                      "incumbent_hold": (accrued / 10000.0) >= HOLD_THRESHOLD,
                      "pool_usd_day": meta["pool_usd_day"], "mode": mode})

    eligible = [c for c in cands if c.get("proj_usd") is not None
                and c["proj_usd"] >= CLIFF_BAR]
    # D8: cap INHERITED-basis daily entries per run
    inh = [c for c in eligible if c.get("rate_basis") == "INHERITED"]
    for c in inh[DAILY_ENTRIES_MAX:]:
        eligible.remove(c)
        c["skip_reason"] = "daily_entry_cap"
    sel, skipped = greedy_allocate(
        eligible, float(os.environ.get("KALSHI_MAX_TOTAL_CAPITAL", "240")),
        float(os.environ.get("KALSHI_SERIES_MAX_USD", "200")))
    for cand, why in skipped:
        cand["skip_reason"] = why
    for cand in sel:
        cand["selected"] = True

    doc = build_footprint_doc(sel, now.isoformat())
    total_pool = sum(c.get("pool_usd_day") or 0.0 for c in cands)
    buckets = {}
    for cand in cands:
        b = coverage_bucket(cand)
        buckets[b] = buckets.get(b, 0.0) + (cand.get("pool_usd_day") or 0.0)
    coverage = {"generated_utc": now.isoformat(), "family_pool_usd_day": round(total_pool, 2),
                "buckets_usd_day": {k: round(v, 2) for k, v in sorted(buckets.items())},
                "n_candidates": len(cands), "n_selected": len(sel)}

    print("--- PLAN ---")
    for i, cand in enumerate(doc["rows"]):
        src = sel[i]
        print(f"  {cand['priority']}. {cand['ticker']} max_ct={cand['max_ct']} "
              f"proj=${src['proj_usd']:.2f} basis={src['rate_basis']} "
              f"accrued=${src['accrued_usd']:.2f}")
    if not doc["rows"]:
        print("  (empty — nothing projects >= $%.2f at plannable size; the quoter" % CLIFF_BAR)
        print("   fail-closes on this file = quotes NOTHING, by design)")
    print("--- COVERAGE (every family pool $/day) ---")
    print(json.dumps(coverage, indent=1))
    if a.write:
        atomic_write_json(FOOTPRINT_OUT, doc)
        atomic_write_json(COVERAGE_OUT, coverage)
        print(f"WROTE {FOOTPRINT_OUT} + {COVERAGE_OUT}")
    else:
        print("dry run — nothing written")


if __name__ == "__main__":
    main()

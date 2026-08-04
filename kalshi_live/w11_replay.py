#!/usr/bin/env python3
"""W11 — OFFLINE SELECTION-REPLAY HARNESS (operator-raised 2026-08-04).

STOP halts the live quoter only; this replays SELECTION over the recorded
caprank-YYYYMMDD.jsonl telemetry (one JSON per quoter cycle: ts, the ACTUAL selected
list, and per-variant candidate `components` with the features the ranker saw at that
moment: kind, base_usd_day, ref, commit_usd, cost_usd_day, cap_score).

WHAT IT CAN DO (honest scope):
  * replay any ranking POLICY over the recorded per-cycle candidate features and compare
    the selections it would have made against (a) what the live bot actually selected and
    (b) which series were EVENTUALLY credited (ground truth: the frozen credit_history
    from the W10 snapshot, joined with no lookahead — a policy at cycle time T may use
    only credits with created_at <= T).
  * score D2 candidate rankings against the scale plan's proof criteria, with the
    validation sets DERIVED from frozen data, not hand-listed:
      never_paid  = series with real fill contracts and zero credits ever
      zero_fill_earners = series with credits and zero fill contracts
WHAT IT CANNOT DO (printed on every run): fills, queue position, and venue payout are
not replayable — there is no historical depth-by-price series. This validates SELECTION
ONLY, never fill P&L.

Policies are pure functions: components(list[dict]), ctx(dict) -> ranked list[str].
`ctx` carries ts, credited_series_to_date (no lookahead), fills_by_series (frozen).

Read-only over frozen/recorded files; writes only under --outdir.
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys

CREDIT_PREFIX = "Liquidity Incentive for event "


def series_of(ticker_or_event):
    return (ticker_or_event or "").split("-")[0]


def iter_cycles(path):
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def credits_timeline(credits_rows):
    """[(created_at_dt, series, usd)] sorted, from raw credit_history rows."""
    out = []
    for r in credits_rows:
        reason = r.get("reason") or ""
        if not reason.startswith(CREDIT_PREFIX):
            continue
        ev = reason[len(CREDIT_PREFIX):].strip()
        try:
            ts = dt.datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
        except Exception:
            continue
        out.append((ts, series_of(ev), float(r.get("amount_cents") or 0) / 100.0))
    out.sort(key=lambda x: x[0])
    return out


def fills_by_series(orders_rows):
    out = collections.defaultdict(float)
    for o in orders_rows:
        t = o.get("ticker")
        if not t:
            continue
        try:
            out[series_of(t)] += float(o.get("fill_count_fp") or 0)
        except (TypeError, ValueError):
            pass
    return dict(out)


def validation_sets(credits_rows, orders_rows):
    """Derived, rule-stated validation sets (no hand lists)."""
    credited = {s for _, s, _ in credits_timeline(credits_rows)}
    fills = fills_by_series(orders_rows)
    never_paid = sorted(s for s, ct in fills.items() if ct > 0 and s not in credited)
    zero_fill_earners = sorted(s for s in credited if fills.get(s, 0.0) == 0.0)
    return never_paid, zero_fill_earners


# ---------------------------------------------------------------- policies
def policy_recorded(components, ctx):
    """The live bot's own ranking as recorded (cap_score order)."""
    return [c["ticker"] for c in
            sorted(components, key=lambda c: (-c.get("cap_score", 0.0), c["ticker"]))]


def make_policy_credit_feedback(bonus=2.0, never_paid_mult=0.5):
    """D2-shaped candidate: multiply cap_score by `bonus` for series credited to date,
    and by `never_paid_mult` for series with fills but no credit to date. Zero-fill
    series keep their score untouched (the proof criterion: not deranked)."""
    def policy(components, ctx):
        credited = ctx["credited_series_to_date"]
        fills = ctx["fills_by_series"]
        def key(c):
            s = series_of(c["ticker"])
            v = c.get("cap_score", 0.0)
            if s in credited:
                v *= bonus
            elif fills.get(s, 0.0) > 0:
                v *= never_paid_mult
            return v
        return [c["ticker"] for c in
                sorted(components, key=lambda c: (-key(c), c["ticker"]))]
    return policy


POLICIES = {
    "recorded": policy_recorded,
    "d2_feedback": make_policy_credit_feedback(),
}


# ---------------------------------------------------------------- evaluation
def replay(caprank_paths, credits_rows, orders_rows, top_n=40, variant="env"):
    tl = credits_timeline(credits_rows)
    fills = fills_by_series(orders_rows)
    never_paid, zero_fill = validation_sets(credits_rows, orders_rows)
    credited_ever = {s for _, s, _ in tl}

    stats = {name: collections.defaultdict(lambda: {
        "cycles": 0, "sel_slots": 0, "payer_slots": 0, "neverpaid_slots": 0,
        "zerofill_slots": 0, "np_ranks": [], "payer_ranks": []})
        for name in POLICIES}

    for path in caprank_paths:
        day = os.path.basename(path).replace("caprank-", "").replace(".jsonl", "")
        for cyc in iter_cycles(path):
            try:
                ts = dt.datetime.fromisoformat(cyc["ts"])
            except Exception:
                continue
            comp = None
            for v in cyc.get("variants") or []:
                if v.get("name") == variant:
                    comp = v.get("components")
                    break
            if not comp:
                continue
            credited_now = {s for t, s, _ in tl if t <= ts}
            ctx = {"ts": ts, "credited_series_to_date": credited_now,
                   "fills_by_series": fills}
            for name, pol in POLICIES.items():
                ranked = pol(comp, ctx)
                sel = ranked[:top_n]
                d = stats[name][day]
                d["cycles"] += 1
                d["sel_slots"] += len(sel)
                for t in sel:
                    s = series_of(t)
                    if s in credited_ever:
                        d["payer_slots"] += 1
                    if s in never_paid:
                        d["neverpaid_slots"] += 1
                    if s in zero_fill:
                        d["zerofill_slots"] += 1
                for i, t in enumerate(ranked):
                    s = series_of(t)
                    if s in never_paid:
                        d["np_ranks"].append(i)
                    elif s in credited_ever:
                        d["payer_ranks"].append(i)
    return stats, never_paid, zero_fill


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/tmp/w11")
    ap.add_argument("--snapshot", required=True, help="W10 frozen snapshot (credits+orders)")
    ap.add_argument("--caprank", nargs="+", required=True)
    ap.add_argument("--top-n", type=int, default=40)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    raw = json.load(open(a.snapshot))

    stats, never_paid, zero_fill = replay(a.caprank, raw["credits"], raw["orders"],
                                          top_n=a.top_n)
    print("HONEST LIMIT: selection-only replay. Fills, queue position and venue payout "
          "are NOT backtestable from this telemetry.")
    print(f"validation sets (derived from frozen snapshot read {raw['read_ts']}):")
    print(f"  never_paid ({len(never_paid)}): {', '.join(never_paid)}")
    print(f"  zero_fill_earners ({len(zero_fill)}): {', '.join(zero_fill)}")

    import statistics as st
    doc = {"schema": 1, "read_ts": raw["read_ts"], "top_n": a.top_n,
           "never_paid": never_paid, "zero_fill_earners": zero_fill, "days": {}}
    for name, days in stats.items():
        print(f"\nPOLICY {name}:")
        for day, d in sorted(days.items()):
            pf = d["payer_slots"] / d["sel_slots"] if d["sel_slots"] else 0.0
            nf = d["neverpaid_slots"] / d["sel_slots"] if d["sel_slots"] else 0.0
            zf = d["zerofill_slots"] / d["sel_slots"] if d["sel_slots"] else 0.0
            npr = st.median(d["np_ranks"]) if d["np_ranks"] else None
            pr = st.median(d["payer_ranks"]) if d["payer_ranks"] else None
            print(f"  {day}: cycles={d['cycles']:5d} payer_slot_frac={pf:.3f} "
                  f"neverpaid_slot_frac={nf:.3f} zerofill_slot_frac={zf:.3f} "
                  f"median_rank never_paid={npr} payers={pr}")
            doc["days"].setdefault(day, {})[name] = {
                "cycles": d["cycles"], "payer_slot_frac": round(pf, 4),
                "neverpaid_slot_frac": round(nf, 4),
                "zerofill_slot_frac": round(zf, 4),
                "median_rank_never_paid": npr, "median_rank_payers": pr}
    path = os.path.join(a.outdir, "w11_replay_report.json")
    json.dump(doc, open(path, "w"), indent=1, sort_keys=True)
    print("\nwrote", path)


if __name__ == "__main__":
    main()

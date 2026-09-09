#!/usr/bin/env python3
"""MB CANDIDATE PIPELINE - discovery -> verification queue (docs/
MB_TAILABLE_PLAN.md P4; operator deliverable 2026-09-08: "... a set up to
monitor them and new possible candidates").

Runs daily AFTER the tailable list (cron stage 12). Reads the list's
machine output (tailable_list.json - every board QUALIFIES with $>0 is
already a row there, with its eligibility read) and decides who needs a
chain deep-dive next:

  NEW DIVE    tier PENDING, eligibility PASS, no dive dossier anywhere
  RE-DIVE     newest dossier INSUFFICIENT with span_days < RE_DIVE_SPAN_D
              and (span_days + days since that dossier) >= RE_DIVE_SPAN_D
              (the dive's own 60-day hire bar - e.g. 0x1c010e69db, span 58d
              on 2026-09-07 -> re-dive on/after 2026-09-10)

and appends them to the queue file (one address per line, dedup, existing
order kept). A separate serial runner (scripts/vps_jobs/
tailable_dive_runner.sh; PID self-exclusion, secret read inside) drains
the queue into deep_dive_pipeline/ - ADMITs then surface on the list as
VERIFIED automatically (the list's dossier scan covers that dir).

ROSTER ADD STAYS AN OPERATOR RULING: this prints the VERIFIED-but-not-
rostered rows as PROPOSALS and changes nothing else. Nothing here is a
lock, a roster edit or an order. Never removes anything.

    python mb_candidate_pipeline.py            # cron stage 12
    python mb_candidate_pipeline.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DAY_S = 86400.0
RE_DIVE_SPAN_D = 30      # chain_deep_dive --min-span-days (ruling 2026-09-09 B:
#                          = the operator's 30-day eligibility bar; was 60)
# one-time: every INSUFFICIENT dossier written under the OLD bar (60d span /
# P>=0.9 gate) whose span now clears 30d gets ONE fresh dive; a re-dive that
# comes back INSUFFICIENT carries a newer mtime and is not re-queued by this
# rule (terminates)
OLD_BAR_RETIRED_UTC = "2026-09-09T01:00:00Z"
BASE = "/opt/pa2-shared/mb_copyable_data"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def read_queue(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    out = []
    for ln in open(path):
        a = ln.strip().lower()
        if a.startswith("0x") and len(a) == 42 and a not in out:
            out.append(a)
    return out


def write_queue(path: str, addrs: list[str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("".join(a + "\n" for a in addrs))
    os.replace(tmp, path)


def decide(rows: list[dict], now_ts: float, queued: set,
           re_dive_span_d: int = RE_DIVE_SPAN_D,
           old_bar_retired_ts: float | None = None) -> dict:
    """PURE. rows = tailable_list.json rows. Returns
    {"new": [(addr, why)], "redive": [(addr, why)], "proposals": [addr],
     "skipped": [(addr, why)]} - every candidate that is NOT queued says
    why (nothing silent)."""
    new, redive, proposals, skipped = [], [], [], []
    if old_bar_retired_ts is None:
        old_bar_retired_ts = parse_iso(OLD_BAR_RETIRED_UTC)
    for r in rows:
        a = str(r.get("wallet", "")).lower()
        if not a:
            continue
        tier = r.get("tier")
        dive = str(r.get("dive_verdict") or "")
        if tier == "VERIFIED" and not r.get("on_roster"):
            proposals.append(a)
        if a in queued:
            skipped.append((a, "already queued"))
            continue
        if tier == "PENDING" and r.get("elig_status") == "PASS" and not dive:
            new.append((a, "PENDING, eligibility PASS, no dossier"))
            continue
        if dive.startswith("REJECT"):
            skipped.append((a, "dive REJECT (deliberate exclusion) - never re-dived"))
            continue
        if dive.startswith("INSUFFICIENT"):
            span = r.get("dive_span_days")
            dts = parse_iso(r.get("dive_utc"))
            if span is None and "raised" in str(r.get("dive_reason") or ""):
                # an ERRORED dive ("deep dive raised: ...") writes a dossier
                # without span - retry it, never park it (re-review C2)
                redive.append((a, "dive ERRORED (dossier has no span) - retry"))
                continue
            if span is None or dts is None:
                skipped.append((a, "INSUFFICIENT but span/date unknown"))
                continue
            span_now = float(span) + (now_ts - dts) / DAY_S
            if float(span) < re_dive_span_d <= span_now:
                redive.append((a, f"INSUFFICIENT span {float(span):.0f}d at "
                                  f"dive, ~{span_now:.0f}d now >= "
                                  f"{re_dive_span_d}d"))
            elif dts < old_bar_retired_ts and span_now >= re_dive_span_d:
                redive.append((a, f"INSUFFICIENT under the OLD bar (dossier "
                                  f"{r.get('dive_utc')}, 60d/P>=0.9 retired "
                                  f"{OLD_BAR_RETIRED_UTC}); span ~{span_now:.0f}d "
                                  f">= {re_dive_span_d}d - one fresh dive"))
            else:
                skipped.append((a, f"INSUFFICIENT span {float(span):.0f}d "
                                   f"(~{span_now:.0f}d now) - not a span "
                                   f"re-dive"))
            continue
        if tier == "PENDING":
            skipped.append((a, "PENDING: " + "; ".join(r.get("tier_reasons")
                                                         or ["?"])))
    return {"new": new, "redive": redive, "proposals": proposals,
            "skipped": skipped}


def run(args) -> int:
    try:
        blob = json.load(open(args.list))
    except (OSError, ValueError) as e:
        print(f"[pipeline] FATAL: tailable_list.json unreadable ({e!r}) - "
              f"refusing to queue from nothing")
        return 2
    rows = blob.get("rows") or []
    if not rows:
        print("[pipeline] FATAL: tailable list has 0 rows - refusing")
        return 2
    gen = blob.get("generated_utc")
    now_ts = getattr(args, "now_ts", None)      # tests pin the clock
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()
    queue = read_queue(args.queue)
    d = decide(rows, now_ts, set(queue),
               old_bar_retired_ts=getattr(args, "old_bar_retired_ts", None))
    added = [a for a, _ in d["new"]] + [a for a, _ in d["redive"]]
    os.makedirs(os.path.dirname(args.queue) or ".", exist_ok=True)
    write_queue(args.queue, queue + added)
    # state file: why each address is (still) queued
    st = {}
    if os.path.exists(args.state):
        try:
            st = json.load(open(args.state))
        except (OSError, ValueError):
            st = {}
    for a, why in d["new"] + d["redive"]:
        st[a] = {"queued_utc": now_iso(), "why": why, "list_generated": gen}
    for a in list(st):
        if a not in queue + added:
            st.pop(a)   # drained by the runner
    tmp = args.state + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1, sort_keys=True)
    os.replace(tmp, args.state)
    print(f"===== {now_iso()} MB CANDIDATE PIPELINE (list generated {gen}; "
          f"{len(rows)} rows) =====")
    print(f"[pipeline] queued NEW {len(d['new'])} | RE-DIVE {len(d['redive'])}"
          f" | queue now {len(queue) + len(added)} (was {len(queue)}) -> "
          f"{args.queue}")
    for a, why in d["new"] + d["redive"]:
        print(f"  {a[:12]}..  QUEUED: {why}")
    for a, why in d["skipped"]:
        print(f"  {a[:12]}..  not queued: {why}")
    if d["proposals"]:
        print("[pipeline] ROSTER PROPOSALS (VERIFIED, not on roster; roster "
              "add = OPERATOR RULING): "
              + ", ".join(a[:12] + ".." for a in d["proposals"]))
    else:
        print("[pipeline] roster proposals: none (every VERIFIED wallet is "
              "rostered)")
    return 0


def _self_test() -> int:
    print("SELF-TEST - mb_candidate_pipeline (offline)\n")
    ok = True
    now = 1_800_000_000.0
    iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    A, B, C, D, E, F, G = ("0x" + ch * 40 for ch in "abcdefg")
    rows = [
        {"wallet": A, "tier": "PENDING", "elig_status": "PASS",
         "dive_verdict": None, "tier_reasons": ["dive none"]},          # NEW
        {"wallet": B, "tier": "PENDING", "elig_status": "FAIL",
         "dive_verdict": None, "tier_reasons": ["eligibility FAIL"]},   # no
        {"wallet": C, "tier": "PENDING", "elig_status": "PASS",
         "dive_verdict": "INSUFFICIENT-EVIDENCE", "dive_span_days": 57,
         "dive_utc": iso(now - 4 * DAY_S)},                             # RE-DIVE (61)
        {"wallet": D, "tier": "PENDING", "elig_status": "PASS",
         "dive_verdict": "INSUFFICIENT-EVIDENCE", "dive_span_days": 40,
         "dive_utc": iso(now - 4 * DAY_S)},                             # no (44)
        {"wallet": E, "tier": "PENDING", "elig_status": "PASS",
         "dive_verdict": "INSUFFICIENT-EVIDENCE", "dive_span_days": 144,
         "dive_utc": iso(now - 1 * DAY_S)},                             # no (>=60 at dive)
        {"wallet": F, "tier": "VERIFIED", "elig_status": "PASS",
         "dive_verdict": "ADMIT", "on_roster": False},                   # PROPOSAL
        {"wallet": G, "tier": "PENDING", "elig_status": "PASS",
         "dive_verdict": None, "tier_reasons": ["dive none"]},          # already queued
    ]
    # errored dive -> retry; REJECT -> named skip (re-review C2 / A-lower)
    dx = decide([{"wallet": A, "tier": "PENDING", "elig_status": "PASS",
                  "dive_verdict": "INSUFFICIENT-EVIDENCE", "dive_span_days": None,
                  "dive_utc": iso(now - 1), "dive_reason": "deep dive raised: RPC"},
                 {"wallet": B, "tier": "EXCLUDED", "elig_status": "PASS",
                  "dive_verdict": "REJECT"}], now, set(),
                old_bar_retired_ts=now - 2 * DAY_S)
    okx = ([a for a, _ in dx["redive"]] == [A] and "ERRORED" in dx["redive"][0][1]
           and [a for a, _ in dx["skipped"]] == [B] and "REJECT" in dx["skipped"][0][1])
    print(f"  [decide] errored dossier (no span) retried; REJECT skipped by name : {okx}")
    ok &= okx
    # bar = 30 (ruling B): C (57d at dive, ~61 now) is NOT a span crossing any
    # more; D (40d, ~44 now) and E (144d) are old-bar INSUFFICIENTs -> ONE
    # fresh dive each; C too (old-bar). Pass old_bar_retired_ts = now-2d so
    # the 4-day-old dossiers (C, D) and the 1-day-old E are 'old' (E) or not:
    # E's dossier (now-1d) is NEWER than the retirement -> not re-queued.
    d = decide(rows, now, {G}, old_bar_retired_ts=now - 2 * DAY_S)
    ok1 = ([a for a, _ in d["new"]] == [A]
           and sorted(a for a, _ in d["redive"]) == [C, D]
           and all("OLD bar" in w for a, w in d["redive"])
           and d["proposals"] == [F]
           and sorted(a for a, _ in d["skipped"]) == [B, E, G]
           and "already queued" in dict(d["skipped"])[G]
           and "not a span re-dive" in dict(d["skipped"])[E])
    # span crossing under the 30 bar: 27d at dive, ~31d now -> re-dive
    d2 = decide([{"wallet": D, "tier": "PENDING", "elig_status": "PASS",
                  "dive_verdict": "INSUFFICIENT-EVIDENCE", "dive_span_days": 27,
                  "dive_utc": iso(now - 4 * DAY_S)}], now, set(),
                old_bar_retired_ts=now - 10 * DAY_S)
    ok1 = ok1 and [a for a, _ in d2["redive"]] == [D] and RE_DIVE_SPAN_D == 30
    print(f"  [decide] NEW = PENDING+PASS+no dossier; RE-DIVE = INSUFF "
          f"crossing 30d or an old-bar INSUFFICIENT (one fresh dive); "
          f"FAIL/newer-INSUFF/already-queued out with reasons; VERIFIED "
          f"off-roster = proposal : {ok1}")
    ok &= ok1
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        q = os.path.join(t, "q.txt")
        write_queue(q, [G, "junk", G])
        ok2 = read_queue(q) == [G]
        lp = os.path.join(t, "list.json")
        json.dump({"generated_utc": "2026-09-09T11:50:00Z", "rows": rows},
                  open(lp, "w"))
        from types import SimpleNamespace as NS
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run(NS(list=lp, queue=q, state=os.path.join(t, "st.json"),
                    now_ts=now, old_bar_retired_ts=now - 2 * DAY_S))
        qq = read_queue(q)
        st = json.load(open(os.path.join(t, "st.json")))
        ok3 = (rc == 0 and qq == [G, A, C, D] and set(st) == {A, C, D}
               and "ROSTER PROPOSALS" in buf.getvalue()
               and F[:12] in buf.getvalue())
        # second run: idempotent (nothing re-queued), state kept
        with contextlib.redirect_stdout(io.StringIO()):
            run(NS(list=lp, queue=q, state=os.path.join(t, "st.json"),
                    now_ts=now, old_bar_retired_ts=now - 2 * DAY_S))
        ok4 = read_queue(q) == [G, A, C, D]
        # runner drained A (queue line gone, dossier now exists -> the next
        # list shows a verdict): A is not re-queued, its state entry goes
        write_queue(q, [G, C, D])
        rows2 = [dict(r, dive_verdict="ADMIT", tier="VERIFIED",
                      on_roster=False) if r["wallet"] == A else r
                 for r in rows]
        json.dump({"generated_utc": "2026-09-10T11:50:00Z", "rows": rows2},
                  open(lp, "w"))
        with contextlib.redirect_stdout(io.StringIO()):
            run(NS(list=lp, queue=q, state=os.path.join(t, "st.json"),
                    now_ts=now, old_bar_retired_ts=now - 2 * DAY_S))
        ok5 = (read_queue(q) == [G, C, D]
               and set(json.load(open(os.path.join(t, "st.json")))) == {C, D})
        # a drained wallet WITHOUT a dossier (dive crashed) is re-queued -
        # the queue never silently loses a candidate
        write_queue(q, [G, C, D])
        json.dump({"generated_utc": "2026-09-10T11:50:00Z", "rows": rows},
                  open(lp, "w"))
        with contextlib.redirect_stdout(io.StringIO()):
            run(NS(list=lp, queue=q, state=os.path.join(t, "st.json"),
                    now_ts=now, old_bar_retired_ts=now - 2 * DAY_S))
        ok5 = ok5 and read_queue(q) == [G, C, D, A]
        with contextlib.redirect_stdout(io.StringIO()):
            rc2 = run(NS(list=os.path.join(t, "none.json"), queue=q,
                         state=os.path.join(t, "st.json"), now_ts=now,
                         old_bar_retired_ts=now - 2 * DAY_S))
        print(f"  [queue] dedup+validate; append keeps order; idempotent; "
              f"drained entries leave the state; missing list = rc 2 : "
              f"{ok2 and ok3 and ok4 and ok5 and rc2 == 2}")
        ok &= ok2 and ok3 and ok4 and ok5 and rc2 == 2
    import inspect
    src = inspect.getsource(run) + inspect.getsource(decide)
    ok6 = ("OPERATOR RULING" in src and "write_lock" not in src
           and "chain_audit" not in src and RE_DIVE_SPAN_D == 30
           and parse_iso(OLD_BAR_RETIRED_UTC) is not None)
    print(f"  [pins] no locks, no roster edits, proposals labeled operator "
          f"ruling; re-dive bar = 30d (ruling 2026-09-09) : {ok6}")
    ok &= ok6
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MB candidate pipeline: list "
                                             "-> dive queue (proposals only)")
    ap.add_argument("--list", default=f"{BASE}/tailable/tailable_list.json")
    ap.add_argument("--queue", default=f"{BASE}/tailable/dive_queue.txt")
    ap.add_argument("--state", default=f"{BASE}/tailable/dive_queue_state.json")
    ap.add_argument("--self-test", dest="self_test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else run(a))

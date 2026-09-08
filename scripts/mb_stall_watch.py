#!/usr/bin/env python3
"""[stall] watchdog — catches a job that is ALIVE BUT DOING NOTHING.

WHY (operator "add a watcher in parallel", 2026-09-08). The existing [chain]
watchdog grades the 11:40Z cron AFTER it finishes: it answers "did each stage
run and not crash". It is structurally blind to the failure that actually cost
us a day — a background job that is running, consuming no CPU, producing no
output, and will never finish:

  measured 2026-09-08: the promotion dive queue sat for 15h38m at 0.0% CPU with
  a 0-byte log. Its wait loop ran `pgrep -f "chain_deep_div[e].py"`, and the
  wrapper's OWN cmdline contained the literal `chain_deep_dive.py` it was about
  to run — so the bracket trick did not exclude self and it waited on itself
  forever. Nothing alarmed, because nothing was crashed, missing, or late to a
  schedule. It was simply asleep.

THE TEST THIS APPLIES: liveness is not progress. A watched job is healthy only
if its OUTPUT is advancing. Process-alive plus stale output = STALLED, alarmed.

DESIGN RULES, each earned:
  * SELF-EXCLUSION BY PID, NEVER BY PATTERN. This script's own cmdline contains
    every pattern it searches for. It excludes its own pid, its parent, and its
    children explicitly; a pattern-only exclusion is what wedged the dive.
  * CONFIG LIVES OUTSIDE ANY GIT CLONE (default /opt/pa2-shared/mb_stall_watch.json)
    — the 2026-09-08 revert showed that a watchdog whose definition of "what to
    watch" sits inside a `git reset --hard` target can be silently narrowed.
  * UNKNOWN IS AN ALARM, NEVER AN OK. Missing config, unreadable log, or
    unresolvable pid state all report loudly rather than defaulting to healthy.
  * READ-ONLY. It never kills anything. A human decides what to do with a stall.

    python scripts/mb_stall_watch.py [--config ...] [--now-ts N]
    ... --self-test     # offline, no processes, no files
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

CONFIG_DEFAULT = "/opt/pa2-shared/mb_stall_watch.json"


# ── pure core ────────────────────────────────────────────────────────────────
def classify(alive: bool, idle_s: float | None, max_idle_s: float) -> str:
    """State of one watched job. Pure.

    alive   — a matching process exists (self already excluded by the caller)
    idle_s  — seconds since its output last advanced; None = output unreadable

    STALLED : alive but output has not advanced within max_idle_s  <-- the
              15h38m failure; the whole reason this file exists
    NO-OUTPUT: alive and we cannot even read its output — never call that OK
    RUNNING : alive and advancing
    IDLE    : not alive (finished or never started) — not an alarm here; the
              [chain] watchdog owns "did the scheduled thing run"
    """
    if not alive:
        return "IDLE"
    if idle_s is None:
        return "NO-OUTPUT"
    return "STALLED" if idle_s > max_idle_s else "RUNNING"


def parse_config(text: str) -> list[dict]:
    """Config JSON -> [{name, pattern, log, max_idle_s}]. Raises on anything
    malformed: a watchdog that silently watches FEWER jobs than intended is
    the exact defect class this file exists to prevent. Pure."""
    blob = json.loads(text)
    rows = blob.get("jobs") if isinstance(blob, dict) else blob
    if not isinstance(rows, list) or not rows:
        raise ValueError("stall-watch config lists no jobs")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError(f"job entry is not an object: {r!r}")
        name, pattern = r.get("name"), r.get("pattern")
        log, idle = r.get("log"), r.get("max_idle_s")
        if not (isinstance(name, str) and name
                and isinstance(pattern, str) and pattern
                and isinstance(log, str) and log):
            raise ValueError(f"job missing name/pattern/log: {r!r}")
        if not isinstance(idle, (int, float)) or idle <= 0:
            raise ValueError(f"job {name!r} has a non-positive max_idle_s")
        out.append({"name": name, "pattern": pattern, "log": log,
                    "max_idle_s": float(idle)})
    return out


def render(states: list[tuple[str, str, float | None]]) -> tuple[str, int]:
    """([stall] line, alarm count). STALLED and NO-OUTPUT are alarms; the
    line starts with '!!' when any is present. Pure."""
    bits, bad = [], 0
    for name, state, idle_s in states:
        age = "" if idle_s is None else f"({idle_s / 60.0:.0f}m)"
        bits.append(f"{name}={state}{age}")
        if state in ("STALLED", "NO-OUTPUT"):
            bad += 1
    prefix = "[stall] !!" if bad else "[stall]"
    tail = (f" - {bad} job(s) ALIVE BUT NOT PROGRESSING - investigate before "
            f"trusting anything downstream" if bad else " - all watched jobs "
            f"progressing or idle")
    return prefix + " " + " ".join(bits) + tail, bad


# ── I/O shell ────────────────────────────────────────────────────────────────
def _self_pids() -> set[int]:
    """This process, its parent, and its children — the pids a pattern match
    must never count. Pattern-based self-exclusion is what wedged the dive."""
    pids = {os.getpid(), os.getppid()}
    try:
        r = subprocess.run(["pgrep", "-P", str(os.getpid())],
                           capture_output=True, text=True, timeout=10)
        pids |= {int(x) for x in r.stdout.split() if x.isdigit()}
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return pids


def matching_pids(pattern: str, exclude: set[int]) -> list[int]:
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = {int(x) for x in r.stdout.split() if x.isdigit()}
    return sorted(pids - exclude)


def output_idle_s(path: str, now: float) -> float | None:
    """Seconds since the job's output last advanced; None if unreadable."""
    try:
        return max(0.0, now - os.path.getmtime(path))
    except OSError:
        return None


def run(args) -> int:
    now = float(args.now_ts) if args.now_ts else time.time()
    try:
        jobs = parse_config(open(args.config).read())
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"[stall] !! CONFIG UNUSABLE ({e!r}) - NOTHING IS BEING "
              f"WATCHED; this is an alarm, not a quiet day")
        return 1
    mine = _self_pids()
    states = []
    for j in jobs:
        pids = matching_pids(j["pattern"], mine)
        idle = output_idle_s(j["log"], now) if pids else None
        states.append((j["name"], classify(bool(pids), idle,
                                           j["max_idle_s"]), idle))
    line, bad = render(states)
    print(line)
    return 0 if bad == 0 else 1


# ── self-test (offline) ──────────────────────────────────────────────────────
def _self_test() -> int:
    print("SELF-TEST - mb_stall_watch (offline)\n")
    ok = True
    # [classify] the 15h38m failure: alive, output frozen -> STALLED
    ok1 = (classify(True, 15 * 3600, 1800) == "STALLED"
           and classify(True, 60, 1800) == "RUNNING"
           and classify(False, 99999, 1800) == "IDLE"
           and classify(True, None, 1800) == "NO-OUTPUT")
    print(f"  [classify] alive+frozen=STALLED; unreadable != OK : {ok1}")
    ok &= ok1
    # [classify] boundary: exactly at the threshold is still RUNNING
    ok2 = (classify(True, 1800, 1800) == "RUNNING"
           and classify(True, 1800.1, 1800) == "STALLED")
    print(f"  [classify] threshold boundary exact : {ok2}")
    ok &= ok2
    # [render] alarms are loud and counted; a clean board is quiet
    line, bad = render([("dive", "STALLED", 56280.0), ("cron", "RUNNING", 30.0)])
    ok3 = (bad == 1 and line.startswith("[stall] !!")
           and "dive=STALLED(938m)" in line and "cron=RUNNING(0m)" in line)
    line2, bad2 = render([("dive", "IDLE", None)])
    ok3 = ok3 and bad2 == 0 and line2.startswith("[stall] ") \
        and "!!" not in line2
    line3, bad3 = render([("dive", "NO-OUTPUT", None)])
    ok3 = ok3 and bad3 == 1 and line3.startswith("[stall] !!")
    print(f"  [render] STALLED+NO-OUTPUT alarm; IDLE quiet : {ok3}")
    ok &= ok3
    # [config] good shapes parse; every malformed shape RAISES rather than
    # yielding a shorter watch list
    good = ('{"jobs":[{"name":"a","pattern":"p","log":"/l","max_idle_s":60}]}')
    ok4 = len(parse_config(good)) == 1
    for junk in ('[]', '{"jobs":[]}', '[{"name":"a"}]',
                 '[{"name":"a","pattern":"p","log":"/l"}]',
                 '[{"name":"a","pattern":"p","log":"/l","max_idle_s":0}]',
                 '[{"name":"","pattern":"p","log":"/l","max_idle_s":5}]',
                 '"nope"'):
        try:
            parse_config(junk)
            ok4 = False
        except (ValueError, json.JSONDecodeError):
            pass
    print(f"  [config] valid parses; malformed/empty RAISE : {ok4}")
    ok &= ok4
    # [self-exclusion] our own pid is never counted as a watched job. This is
    # THE lesson: this script's cmdline contains every pattern it searches.
    mine = _self_pids()
    ok5 = os.getpid() in mine and os.getppid() in mine
    hits = matching_pids("mb_stall_watch", mine)
    ok5 = ok5 and os.getpid() not in hits
    print(f"  [self] own pid excluded by PID, never by pattern : {ok5}")
    ok &= ok5
    # [idle] unreadable output is None (-> NO-OUTPUT), never 0 (-> RUNNING)
    ok6 = output_idle_s("/definitely/not/a/real/path", 1000.0) is None
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        p = fh.name
    try:
        got = output_idle_s(p, os.path.getmtime(p) + 120.0)
        ok6 = ok6 and got is not None and abs(got - 120.0) < 1.0
    finally:
        os.unlink(p)
    print(f"  [idle] missing log -> None (alarm), not 0 (healthy) : {ok6}")
    ok &= ok6
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="alarm on jobs that are alive but not progressing")
    ap.add_argument("--config", default=CONFIG_DEFAULT,
                    help="job list, kept OUTSIDE any git clone so a reset "
                         "cannot silently shorten it")
    ap.add_argument("--now-ts", type=float, default=None,
                    help="override clock (testing)")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    a = ap.parse_args()
    sys.exit(_self_test() if a.self_test else run(a))

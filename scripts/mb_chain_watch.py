#!/usr/bin/env python3
"""[chain] watchdog — GO-precondition #2 (operator "build all 5", 2026-09-06).

The grader crashed silently on 7/7 daily runs 2026-08-26..09-01 because
nothing alarmed on a mid-chain traceback. The [grader] heartbeat closed
that one stage; THIS closes the class: it parses TODAY's section of the
11:40Z cron log and prints one [chain] line grading EVERY stage:

    OK      — stage header present today, no Traceback in its section
    CRASHED — header present but its section contains a Traceback
    MISSING — expected header absent from today's log entirely

Any non-OK stage makes the line start with "[chain] !!" — loud by
position, and MISSING is distinct from CRASHED (a stage deleted from the
cron would otherwise vanish silently — the empty-set false-pass).
Runs LAST in the cron; grades everything before it. Read-only.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

# Stage markers as they appear in label_and_fee_refresh_cron.sh output.
# ADD new stages here when the cron gains them - the self-test pins the
# count so a cron/watchlist drift shows up in review.
EXPECTED = [
    ("labels", "label supplement"),
    ("fees", "fee map"),
    ("grader", "cohort5 qualification"),
    ("band", "band 0.65-0.85 forward test"),
    ("scoreboard", "MB SCOREBOARD"),
    ("canon", "canon verification"),
    ("funnel", "trader funnel"),
    ("hypo", "hypothetical dollar ledger"),
    ("backtest", "backtest daily leaderboard"),
]


def grade_chain(log_text: str, today: str) -> tuple[str, int]:
    """(printable [chain] line, count of non-OK stages). Pure."""
    lines = log_text.splitlines()
    # index of each header line for TODAY (headers carry the UTC date)
    starts = {}
    for i, ln in enumerate(lines):
        if ln.startswith("=====") and today in ln:
            for key, marker in EXPECTED:
                if marker in ln and key not in starts:
                    starts[key] = i
    results = []
    bad = 0
    for key, _marker in EXPECTED:
        if key not in starts:
            results.append(f"{key}=MISSING")
            bad += 1
            continue
        i = starts[key]
        j = len(lines)
        for k in range(i + 1, len(lines)):
            if lines[k].startswith("====="):
                j = k
                break
        section = "\n".join(lines[i + 1:j])
        if "Traceback (most recent call last)" in section:
            results.append(f"{key}=CRASHED")
            bad += 1
        else:
            results.append(f"{key}=OK")
    prefix = "[chain] !!" if bad else "[chain]"
    line = (f"{prefix} {today} {' '.join(results)}"
            + (f" - {bad} stage(s) NOT OK - read the cron log sections above"
               if bad else " - all stages ran clean"))
    return line, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive/"
                            "label_fee_refresh.log")
    ap.add_argument("--date", default=None,
                    help="UTC date YYYY-MM-DD (default: today)")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    today = a.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        text = open(a.log, errors="replace").read()
    except OSError as e:
        print(f"[chain] !! LOG UNREADABLE ({e!r}) - treat the whole chain "
              f"as unverified")
        return 1
    line, bad = grade_chain(text, today)
    print(line)
    return 0 if bad == 0 else 1


def _mk_log(day: str, stages, crash_in=None) -> str:
    out = []
    for key, marker in stages:
        out.append(f"===== {day}T11:41:00Z {marker} =====")
        out.append("some ordinary output")
        if key == crash_in:
            out.append("Traceback (most recent call last):")
            out.append("  boom")
    return "\n".join(out)


def _self_test() -> int:
    print("SELF-TEST - mb_chain_watch (offline)\n")
    ok = True
    day = "2026-09-06"
    full = _mk_log(day, EXPECTED)
    line, bad = grade_chain(full, day)
    ok1 = bad == 0 and line.startswith("[chain] 2026") and "!!" not in line \
        and line.count("=OK") == len(EXPECTED)
    print(f"  [green] all stages present+clean -> no alarm, {len(EXPECTED)}"
          f" OKs : {ok1}")
    ok &= ok1
    line, bad = grade_chain(_mk_log(day, EXPECTED, crash_in="grader"), day)
    ok2 = bad == 1 and line.startswith("[chain] !!") and "grader=CRASHED" in line
    print(f"  [crash] traceback inside one section -> that stage CRASHED,"
          f" loud : {ok2}")
    ok &= ok2
    line, bad = grade_chain(_mk_log(day, EXPECTED[:-1]), day)
    ok3 = bad == 1 and "backtest=MISSING" in line \
        and line.startswith("[chain] !!")
    print(f"  [missing] absent stage reported MISSING, never silently OK :"
          f" {ok3}")
    ok &= ok3
    line, bad = grade_chain(_mk_log("2026-09-05", EXPECTED), day)
    ok4 = bad == len(EXPECTED) and line.startswith("[chain] !!")
    print(f"  [stale] yesterday's sections do NOT count for today : {ok4}")
    ok &= ok4
    ok5 = len(EXPECTED) == 9
    print(f"  [pin] watchlist covers the 9 cron stages (update BOTH on cron"
          f" change; backtest joined 2026-09-06) : {ok5}")
    ok &= ok5
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

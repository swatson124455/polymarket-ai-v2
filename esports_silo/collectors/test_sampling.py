#!/usr/bin/env python3
"""Self-contained tests for the closing-line sampling scheduler — stdlib asserts only.

Run:  python -m esports_silo.collectors.test_sampling   (exit 0 = all pass)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.pop("SAMPLE_OFFSETS", None)  # test the defaults

try:
    from . import sampling as s
except ImportError:
    import sampling as s  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


START = datetime(2026, 7, 10, 18, 0, 0, tzinfo=timezone.utc)


def at(**kw):
    return START - timedelta(**kw)


# --- thresholds_crossed ----------------------------------------------------------------
check("far out (>24h) -> none crossed", s.thresholds_crossed(START, at(hours=30)) == [])
check("25h->24h bucket only... 23h in -> [86400]", s.thresholds_crossed(START, at(hours=23)) == [86400])
check("5h out -> 24h+6h crossed", s.thresholds_crossed(START, at(hours=5)) == [86400, 21600])
check("10m out -> all four crossed", len(s.thresholds_crossed(START, at(minutes=10))) == 4)
check("post-start -> none (pre-match bot)", s.thresholds_crossed(START, START + timedelta(minutes=1)) == [])
check("exactly at start -> none", s.thresholds_crossed(START, START) == [])

# --- should_poll -----------------------------------------------------------------------
# no samples yet, 23h out -> due, governed by the 24h threshold
due, b = s.should_poll(START, at(hours=23), [])
check("no samples + 24h crossed -> due@86400", due is True and b == 86400)
# sample taken at 23h30m satisfies the 24h bucket -> not due at 20h
due, b = s.should_poll(START, at(hours=20), [at(hours=23, minutes=30)])
check("24h bucket already sampled -> not due", due is False and b == 86400)
# ...but once 6h threshold crosses, due again (old sample is before the 6h boundary)
due, b = s.should_poll(START, at(hours=5), [at(hours=23, minutes=30)])
check("6h crossed, only old sample -> due@21600", due is True and b == 21600)
# closing: 10m out with samples through 1h -> due for the 15m bucket
due, b = s.should_poll(START, at(minutes=10),
                       [at(hours=23), at(hours=5), at(minutes=50)])
check("15m bucket due at T-10m", due is True and b == 900)
# fully sampled through close -> not due
due, b = s.should_poll(START, at(minutes=5), [at(minutes=12)])
check("close already sampled -> not due", due is False)
# collector was DOWN from 24h..20m: missed thresholds collapse -> ONE catch-up poll,
# governed by the most recent CROSSED threshold (at T-20m that's the 1h bucket; the 15m
# bucket hasn't crossed yet and will trigger its own poll at T-15m).
due, b = s.should_poll(START, at(minutes=20), [])
check("missed thresholds collapse to one poll@3600", due is True and b == 3600)
due, b = s.should_poll(START, at(minutes=14), [at(minutes=20)])
check("15m bucket still fires after catch-up", due is True and b == 900)
# post-start: never due
check("post-start never due", s.should_poll(START, START + timedelta(hours=1), [])[0] is False)
# None line_times tolerated
due, _ = s.should_poll(START, at(hours=23), [None, at(hours=23, minutes=30)])
check("None line_times skipped", due is False)

# --- env-overridable offsets -----------------------------------------------------------
os.environ["SAMPLE_OFFSETS"] = "3600,600"
check("env offsets parsed desc", s.sample_offsets() == [3600, 600])
check("env offsets used", s.thresholds_crossed(START, at(minutes=30), s.sample_offsets()) == [3600])
os.environ["SAMPLE_OFFSETS"] = "garbage"
check("bad env falls back to defaults", s.sample_offsets() == [86400, 21600, 3600, 900])
os.environ.pop("SAMPLE_OFFSETS", None)

if __name__ == "__main__":
    import sys
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)

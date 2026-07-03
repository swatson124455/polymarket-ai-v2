#!/usr/bin/env python3
"""esports_silo — closing-line sampling scheduler for the odds collector.

Polling every fixture on every timer run is quota-infeasible (measured: ~357k req/mo at
15-min cadence vs a ~17-25k target) and pointless — the signal cares about the line at
decision-relevant moments, above all the CLOSE. So each fixture is sampled at fixed
time-to-start thresholds instead:

    T-24h   opening-ish line (day-before reference)
    T-6h    morning-of drift
    T-1h    late line
    T-15m   effective CLOSING line (is_closing=True window is 30m; this lands inside it)

Statelessness: the append-only `odds_raw` IS the sampling ledger. A fixture's existing
`line_time`s tell us which thresholds are already covered — no sampler state to persist,
no state to corrupt, restarts are free. (Missed thresholds collapse into the most recent
one, so a downed collector self-heals with a single catch-up poll.)

Pure functions only — no DB, no network, fully unit-tested. The collector wires them in.

Budget at measured volume (~120 fixtures/day est. across 4 games, 15-min timer):
  discovery 4 calls/run x 96 runs = 11.5k/mo + odds 120 x 4 samples = 14.4k/mo  ≈ 26k/mo
  (2 games verified-volume: ≈ 13k/mo). Typical run: ~9 calls ≈ 50s at the 5.5s cooldown.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple

# (label, seconds before start). Ordered far → near. Env-overridable as comma-separated
# seconds, e.g. SAMPLE_OFFSETS="86400,21600,3600,900".
_DEFAULT_OFFSETS = [86400, 21600, 3600, 900]  # 24h, 6h, 1h, 15m


def sample_offsets() -> List[int]:
    raw = os.getenv("SAMPLE_OFFSETS", "")
    if not raw.strip():
        return list(_DEFAULT_OFFSETS)
    try:
        vals = sorted({int(x) for x in raw.split(",") if x.strip()}, reverse=True)
        return vals if vals else list(_DEFAULT_OFFSETS)
    except ValueError:
        return list(_DEFAULT_OFFSETS)


def thresholds_crossed(start_time: datetime, now: datetime,
                       offsets: Optional[List[int]] = None) -> List[int]:
    """Offsets whose sampling moment (start - offset) is already in the past, pre-match only."""
    if now >= start_time:
        return []  # match started — pre-match bot, nothing left to sample
    offs = offsets if offsets is not None else sample_offsets()
    return [o for o in offs if now >= start_time - timedelta(seconds=o)]


def should_poll(start_time: datetime, now: datetime,
                existing_line_times: Iterable[datetime],
                offsets: Optional[List[int]] = None) -> Tuple[bool, Optional[int]]:
    """Decide whether this fixture is due a poll right now.

    Due iff: pre-match, at least one threshold is crossed, and NO existing sample was taken
    at-or-after the most recent crossed threshold (missed earlier thresholds collapse into
    it — one catch-up poll, not N).

    Returns (due, offset_seconds_of_the_governing_threshold).
    """
    crossed = thresholds_crossed(start_time, now, offsets)
    if not crossed:
        return False, None
    latest = min(crossed)  # smallest offset = closest to start = most recent threshold
    boundary = start_time - timedelta(seconds=latest)
    for lt in existing_line_times or []:
        if lt is None:
            continue
        if lt >= boundary:
            return False, latest  # this threshold already satisfied
    return True, latest

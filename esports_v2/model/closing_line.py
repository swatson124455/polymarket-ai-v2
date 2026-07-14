"""Reduce forward-collected PinnOdds snapshots to a CLOSING line per match.

The forward-collector (``esports_v2/scripts/collect_pinnodds.py``) appends one
timestamped snapshot per match per run to a JSONL log. Over the hours/days before
a match, those snapshots capture the sharp line's movement. For the sharp-line
backtest we need ONE line per match: the CLOSING line — defined here, with no
look-ahead, as **the last snapshot whose ``captured_at`` is at-or-before the
match ``starts`` time**. That is the sharpest, most-informed price the book
showed before the match began.

This module is PURE (stdlib only): it consumes snapshot dicts and returns a
``match_key -> ClosingLine`` lookup in exactly the ``(odds_a, odds_b)`` shape the
sharp-line core already eats (``sharp_reference.enrich_with_sharp_prob``). No
network, no DB — fully unit-testable offline.

CORRECT-OR-ABSENT (matches the rest of the sharp-line core): a match is only
emitted when it has at least one snapshot with valid decimal odds AND a
``captured_at`` at-or-before a parseable ``starts``. A match with no pre-start
snapshot, unparseable timestamps, or degenerate odds is DROPPED (never a guessed
closing line). Post-start snapshots are ignored (they are look-ahead).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse a timestamp string to a timezone-AWARE UTC datetime, or None.

    Accepts the shapes the collector / PinnOdds emit: ISO-8601 with a ``Z`` or
    numeric offset (``2026-07-10T18:00:00Z``), ISO with a space instead of ``T``
    (``2026-07-10 18:00:00``), and date-only (``2026-07-10``). A naive timestamp
    (no offset) is INTERPRETED AS UTC — both the collector's ``captured_at``
    (``datetime.now(timezone.utc).isoformat()``) and PinnOdds ``starts`` are UTC.
    Returns None on anything unparseable (correct-or-absent).
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Normalize to something datetime.fromisoformat handles on 3.11.
    norm = s.replace("Z", "+00:00").replace("z", "+00:00")
    if "T" not in norm and " " in norm:
        norm = norm.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(norm)
    except ValueError:
        # Last resort: date-only.
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _coerce_odds(v) -> Optional[float]:
    """Decimal odds must be a number strictly greater than 1.0, else None.

    Mirrors ``PinnOddsLoader._coerce_odds`` so the reducer applies the same
    validity gate the collector applied at capture time.
    """
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return f if f > 1.0 else None


def _coerce_price(v) -> Optional[float]:
    """A Polymarket price is a probability in the OPEN interval (0, 1). Anything
    at/beyond the bounds or unparseable -> None (correct-or-absent). Mirrors
    ``pm_market_index._coerce_price`` without importing the data layer (keeps this
    module pure/stdlib)."""
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return f if 0.0 < f < 1.0 else None


def _coerce_size(v) -> Optional[float]:
    """A touch size is a positive share count, else None (correct-or-absent)."""
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return f if f > 0.0 else None


@dataclass
class ClosingLine:
    """The closing sharp line for one match, plus line-movement diagnostics."""

    match_key: str
    home: str
    away: str
    starts: str                 # raw starts string from the snapshot
    league_name: Optional[str]
    odds_a: float               # closing decimal odds, home/'A' side
    odds_b: float               # closing decimal odds, away/'B' side
    captured_at: str            # captured_at of the CLOSING snapshot (raw)
    n_snapshots: int            # total valid snapshots seen for this match_key
    n_before_start: int         # how many were at-or-before starts (>=1 here)
    open_odds_a: float          # earliest pre-start valid odds (line-move / CLV)
    open_odds_b: float
    # GAP B: the matched Polymarket ref from the CLOSING snapshot (bet-time price).
    # None when the collector captured no PM match at close (or on old snapshots
    # that predate GAP B) — correct-or-absent.
    condition_id: Optional[str] = None
    yes_token_id: Optional[str] = None
    yes_outcome: Optional[str] = None
    market_price: Optional[float] = None
    # GAP C (2026-07-13): executable prices from the CLOSING snapshot's live
    # CLOB book (market_price is the MID by construction). None on snapshots
    # that predate GAP C or whose book fetch failed/was empty.
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None


def reduce_to_closing_lines(
    snapshots: Iterable[dict],
) -> Dict[str, "ClosingLine"]:
    """Reduce a stream of snapshot dicts to ``match_key -> ClosingLine``.

    Each snapshot must carry the fields the collector writes: ``match_key``,
    ``home``, ``away``, ``starts``, ``league_name``, ``odds_a``, ``odds_b``,
    ``captured_at`` (``event_type`` is ignored here). The closing line for a
    match_key is the valid snapshot with the LATEST ``captured_at`` that is
    at-or-before ``starts`` (no look-ahead). ``open_odds_*`` are the EARLIEST
    pre-start valid odds, so callers can measure line movement.

    Correct-or-absent: snapshots with missing/degenerate odds or an unparseable
    ``captured_at`` are skipped; a match_key with an unparseable ``starts`` or
    zero pre-start snapshots is omitted entirely.
    """
    # Group valid observations by match_key.
    grouped: Dict[str, List[dict]] = {}
    starts_raw: Dict[str, str] = {}
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        key = snap.get("match_key")
        if not key:
            continue
        cap = parse_ts(snap.get("captured_at"))
        if cap is None:
            continue
        oa = _coerce_odds(snap.get("odds_a"))
        ob = _coerce_odds(snap.get("odds_b"))
        if oa is None or ob is None:
            continue
        grouped.setdefault(key, []).append({
            "cap_dt": cap,
            "odds_a": oa,
            "odds_b": ob,
            "raw": snap,
        })

    out: Dict[str, ClosingLine] = {}
    for key, obs in grouped.items():
        # The start time DRIFTS across snapshots when matches are delayed
        # (measured 2026-07-12: 235 same-day drift rows across 104 matches —
        # e.g. 08:00 -> 08:15). Use the LATEST-captured snapshot's ``starts``:
        # the most current schedule knowledge. First-seen (the old rule) used
        # the STALE time as the cutoff, discarding genuinely pre-start
        # snapshots for every delayed match (stale closing line); latest-seen
        # also correctly EXCLUDES look-ahead if a match ever moves earlier.
        latest = max(obs, key=lambda o: o["cap_dt"])
        starts_raw[key] = str(latest["raw"].get("starts") or "")
        starts_dt = parse_ts(starts_raw.get(key))
        if starts_dt is None:
            continue  # cannot define a no-look-ahead closing line without start
        pre = [o for o in obs if o["cap_dt"] <= starts_dt]
        if not pre:
            continue  # only post-start observations — uncovered
        pre.sort(key=lambda o: o["cap_dt"])
        closing = pre[-1]
        opening = pre[0]
        craw = closing["raw"]
        out[key] = ClosingLine(
            match_key=key,
            home=str(craw.get("home") or "").strip(),
            away=str(craw.get("away") or "").strip(),
            starts=starts_raw.get(key, ""),
            league_name=craw.get("league_name"),
            odds_a=closing["odds_a"],
            odds_b=closing["odds_b"],
            captured_at=str(craw.get("captured_at") or ""),
            n_snapshots=len(obs),
            n_before_start=len(pre),
            open_odds_a=opening["odds_a"],
            open_odds_b=opening["odds_b"],
            condition_id=(str(craw.get("condition_id")) if craw.get("condition_id") else None),
            yes_token_id=(str(craw.get("yes_token_id")) if craw.get("yes_token_id") else None),
            yes_outcome=(str(craw.get("yes_outcome")) if craw.get("yes_outcome") else None),
            market_price=_coerce_price(craw.get("market_price")),
            best_bid=_coerce_price(craw.get("best_bid")),
            best_ask=_coerce_price(craw.get("best_ask")),
            bid_size=_coerce_size(craw.get("bid_size")),
            ask_size=_coerce_size(craw.get("ask_size")),
        )
    return out


def iter_snapshots_jsonl(path: str | Path) -> Iterator[dict]:
    """Yield snapshot dicts from a JSONL file. Skips blank/malformed lines."""
    p = Path(path)
    if not p.exists():
        return
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def closing_lines_to_odds_lookup(
    closing: Dict[str, "ClosingLine"],
) -> Dict[str, tuple]:
    """Project ``match_key -> ClosingLine`` down to the ``match_key ->
    (odds_a, odds_b)`` lookup that ``enrich_with_sharp_prob`` consumes."""
    return {k: (cl.odds_a, cl.odds_b) for k, cl in closing.items()}

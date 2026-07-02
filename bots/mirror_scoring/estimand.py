"""Estimand layer: turn a trader's raw trade rows into what MB can capture.

Two verified facts (2026-07-02 audit) drive this module:
  1. MB's guards key on condition_id — one bet per condition_id, first
     signal wins. So the scoreable unit is the trader's FIRST BUY per
     condition_id, not their volume-averaged re-entries.
  2. SELL rows in the trades table are the trader's OWN EXITS (the elite
     activity ingest persists side unfiltered). Existing analytics misread
     SELL as a NO-side bet — this module reads them correctly as exits.

Input rows are plain dicts (from SQL or fixtures):
  {user_address, condition_id, token_id, side (BUY/SELL), price, size,
   timestamp (datetime), resolution ('YES'/'NO'/None),
   yes_token_id, no_token_id}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Entry:
    """The first mirrorable entry of a trader in one market (= one cluster)."""

    trader: str
    condition_id: str
    token_id: str
    side: str                    # BUY (always — entries are buys of a token)
    price: float                 # p_i, what the trader paid
    timestamp: datetime          # t_i
    size: float
    resolution: Optional[str]    # market resolution YES/NO, None if unresolved
    token_won: Optional[bool]    # did the BOUGHT token win (o_i)
    # Their own exit, if reconstructed from a later SELL (FIFO pairing):
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    extra: dict = field(default_factory=dict)

    @property
    def o_res(self) -> Optional[float]:
        return None if self.token_won is None else (1.0 if self.token_won else 0.0)

    @property
    def edge_res(self) -> Optional[float]:
        """e_i = o_i - p_i at resolution."""
        o = self.o_res
        return None if o is None else o - self.price

    @property
    def hold_seconds(self) -> Optional[float]:
        if self.exit_time is None:
            return None
        return (self.exit_time - self.timestamp).total_seconds()


def token_outcome(
    token_id: str, resolution: Optional[str], yes_token_id: Optional[str],
    no_token_id: Optional[str],
) -> Optional[bool]:
    """Did the bought token win? None when unmappable (label UNVERIFIED upstream)."""
    if resolution not in ("YES", "NO"):
        return None
    if yes_token_id and token_id == yes_token_id:
        return resolution == "YES"
    if no_token_id and token_id == no_token_id:
        return resolution == "NO"
    return None


def select_first_entries(rows: list[dict]) -> list[Entry]:
    """First BUY per (trader, condition_id) — the mirrorable entry.

    SELL rows never create entries (they are exits, not NO bets).
    Rows must be pre-filtered to sane prices upstream.
    """
    rows_sorted = sorted(rows, key=lambda r: r["timestamp"])
    first: dict[tuple[str, str], Entry] = {}
    for r in rows_sorted:
        if str(r.get("side", "")).upper() != "BUY":
            continue
        key = (r["user_address"], r["condition_id"])
        if key in first:
            continue
        first[key] = Entry(
            trader=r["user_address"],
            condition_id=r["condition_id"],
            token_id=r.get("token_id") or "",
            side="BUY",
            price=float(r["price"]),
            timestamp=r["timestamp"],
            size=float(r.get("size") or 0.0),
            resolution=r.get("resolution"),
            token_won=token_outcome(
                r.get("token_id") or "", r.get("resolution"),
                r.get("yes_token_id"), r.get("no_token_id"),
            ),
        )
    return list(first.values())


def pair_exits(entries: list[Entry], rows: list[dict]) -> list[Entry]:
    """Attach each entry's own exit: earliest SELL by the same trader on the
    same token AFTER the entry (FIFO; one SELL closes one entry).

    Mutates and returns `entries`. SELLs with no prior tracked entry are
    ignored (partial-history guard: we never invent an entry from an exit).
    """
    sells: dict[tuple[str, str], list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["timestamp"]):
        if str(r.get("side", "")).upper() != "SELL":
            continue
        sells.setdefault((r["user_address"], r.get("token_id") or ""), []).append(r)

    for e in sorted(entries, key=lambda x: x.timestamp):
        pool = sells.get((e.trader, e.token_id), [])
        for s in pool:
            if s["timestamp"] > e.timestamp and not s.get("_used"):
                s["_used"] = True
                e.exit_time = s["timestamp"]
                e.exit_price = float(s["price"])
                break
    return entries


def split_events_by_resolution(
    entries: list[Entry], cutoff: datetime, resolved_at: dict[str, datetime]
) -> tuple[list[Entry], list[Entry]]:
    """Event-blocked calendar split: a whole market (cluster) goes to train
    if it RESOLVED at/before the cutoff, to test if after. Markets without a
    resolution timestamp are dropped from the split (no straddling, no leak).
    """
    train, test = [], []
    for e in entries:
        rt = resolved_at.get(e.condition_id)
        if rt is None:
            continue
        (train if rt <= cutoff else test).append(e)
    return train, test

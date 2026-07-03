"""One-bet-per-market guard set — the binding CLAUDE.md constraint, fail-closed.

"Market" means condition_id (per binary market, NOT per event — CLAUDE.md,
verified 2026-07-02). The v2 guard inventory was corrected this week:
mirrored_trades was write-only bookkeeping masquerading as guard #1. v3
implements the REAL four scopes as one explicit class:

  1. same-side dedup while a position is OPEN        (v2: _open_positions scan)
  2. opposing-side block while a position is OPEN     (v2: mirror_opposing_side_blocked)
  3. cross-session historical: same side re-entry     (v2: _entered_market_sides)
  4. cross-session historical: opposing side          (v2: ..._historical)

Net effect: at most ONE bet per condition_id within guard memory, ever.

FAIL-CLOSED DESIGN (fixes the v2 _state_restored ordering bug at the
structural level): can_enter() refuses EVERYTHING until mark_restored() is
called, and mark_restored() is only callable by the restore orchestrator
AFTER all guard state is loaded (mirror_v3/state_restore.py). There is no
window where a half-restored guard silently approves entries.

Pure in-memory; no DB in the hot path. Persistence/restoration is the
orchestrator's job.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class Side(str, Enum):
    YES = "YES"
    NO = "NO"

    @property
    def opposite(self) -> "Side":
        return Side.NO if self is Side.YES else Side.YES


class BlockReason(str, Enum):
    NOT_RESTORED = "guards_not_restored"          # fail-closed pre-restore
    OPEN_SAME_SIDE = "open_position_same_side"    # scope 1
    OPEN_OPPOSING = "open_position_opposing"      # scope 2
    HISTORICAL_SAME = "historical_same_side"      # scope 3
    HISTORICAL_OPPOSING = "historical_opposing"   # scope 4


class OneBetPerMarketGuards:
    def __init__(self) -> None:
        self._restored = False
        # (condition_id, side) of every entry within guard memory (cross-session).
        self._entered_sides: set[tuple[str, Side]] = set()
        # condition_id -> side of the currently OPEN position (this process).
        self._open: dict[str, Side] = {}

    # ── restore protocol (state_restore.py is the only intended caller) ──

    @property
    def restored(self) -> bool:
        return self._restored

    def load_entered(self, condition_id: str, side: Side | str) -> None:
        if self._restored:
            raise RuntimeError("load_entered() after mark_restored() — restore is one-shot")
        self._entered_sides.add((condition_id, Side(side)))

    def load_open(self, condition_id: str, side: Side | str) -> None:
        if self._restored:
            raise RuntimeError("load_open() after mark_restored() — restore is one-shot")
        s = Side(side)
        self._open[condition_id] = s
        # An open position is definitionally an entered side.
        self._entered_sides.add((condition_id, s))

    def mark_restored(self) -> None:
        self._restored = True

    # ── hot path ─────────────────────────────────────────────────────────

    def can_enter(self, condition_id: str, side: Side | str) -> tuple[bool, Optional[BlockReason]]:
        """All four scopes + the fail-closed pre-restore refusal."""
        if not self._restored:
            return False, BlockReason.NOT_RESTORED
        s = Side(side)
        open_side = self._open.get(condition_id)
        if open_side is not None:
            return False, (
                BlockReason.OPEN_SAME_SIDE if open_side is s else BlockReason.OPEN_OPPOSING
            )
        if (condition_id, s) in self._entered_sides:
            return False, BlockReason.HISTORICAL_SAME
        if (condition_id, s.opposite) in self._entered_sides:
            return False, BlockReason.HISTORICAL_OPPOSING
        return True, None

    def record_entry(self, condition_id: str, side: Side | str) -> None:
        """Call ONLY after a confirmed fill. Registers scopes 1-4 state."""
        if not self._restored:
            raise RuntimeError("record_entry() before restore — guards are fail-closed")
        s = Side(side)
        self._open[condition_id] = s
        self._entered_sides.add((condition_id, s))

    def record_close(self, condition_id: str) -> None:
        """Position exited/resolved: scope 1/2 release. Historical scopes (3/4)
        deliberately PERSIST — one bet per market means no re-entry after exit."""
        self._open.pop(condition_id, None)

    # ── introspection (logs/metrics) ─────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            "restored": self._restored,
            "open_positions": len(self._open),
            "entered_sides": len(self._entered_sides),
        }

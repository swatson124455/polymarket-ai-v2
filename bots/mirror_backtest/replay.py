"""Signal replay: run a candidate rule over historical signals with honest fills.

Rule interface (the algo lane's contract, MB_REBUILD_PLAN.md §2):
    rule(signal: dict) -> Optional[Decision]
A rule is a PURE function of the signal dict — it must not look at outcome
fields. `replay_signals` enforces that by stripping resolution/outcome keys
before the rule sees the signal.

Outcome model v1 is hold-to-resolution (o in {0,1} for the bought token).
Exit-ladder replay (bots/mirror_scoring/exit_replay.py, verified a faithful
port of the live ladder) plugs in as `exit_fn` once that branch merges —
the seam exists now so results are comparable across v1/v2.

Signals come from mirror_rejected_signals cleaned per the salvage recipe
(dedup to first signal per (trader, market, side); resolution IS NOT NULL;
labels temporally guarded upstream). This module takes plain dicts so unit
tests run without a DB; data_access.py owns the SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from bots.mirror_backtest.fill_models import (
    COARSE, PRECISE, FillResult, coarse_fill, precise_fill,
)

# Keys a rule must never see (outcome leakage guard).
_OUTCOME_KEYS = ("resolution", "resolved_at", "token_won", "outcome", "o_res")


@dataclass
class Decision:
    size_usd: float


@dataclass
class ReplayedSignal:
    market_id: str
    trader: str
    model: str                 # PRECISE | COARSE
    fill_price: float
    fill_fraction: float
    outcome: float             # 1.0 bought token won, 0.0 lost
    edge_net: float            # per-$ net edge after fees
    caveats: list = field(default_factory=list)


@dataclass
class ReplayReport:
    replayed: list             # list[ReplayedSignal]
    n_signals: int             # rule fired + outcome known
    n_uncovered: int           # rule fired but no fill source at signal time
    n_skipped: int             # rule declined
    n_precise: int
    n_coarse: int


def _token_outcome(signal: dict) -> Optional[float]:
    """1.0 if the signal's side won at resolution, else 0.0; None unmappable.
    mirror_rejected_signals carries side + market resolution (YES/NO)."""
    res = signal.get("resolution")
    side = str(signal.get("side") or "").upper()
    if res not in ("YES", "NO") or side not in ("YES", "NO"):
        return None
    return 1.0 if res == side else 0.0


def replay_signals(
    signals: list,
    rule: Callable[[dict], Optional[Decision]],
    fee_roundtrip: float = 0.02,
    exit_fn: Optional[Callable] = None,   # future: exit-ladder replay seam
) -> ReplayReport:
    """Replay `rule` over signal dicts. Each signal dict carries:
      market_id, trader_address, side (YES/NO), price (whale print),
      resolution, and fill sources when available:
        book_snapshot : per-level ladder at signal time (-> precise)
        ob_row        : orderbook_snapshots aggregate row (-> coarse)
    Precise is preferred when both exist. Signals with neither are counted
    uncovered (never silently dropped — no-silent-caps rule)."""
    out: list[ReplayedSignal] = []
    n_unc = n_skip = 0
    for sig in signals:
        outcome = _token_outcome(sig)
        if outcome is None:
            continue
        visible = {k: v for k, v in sig.items() if k not in _OUTCOME_KEYS}
        decision = rule(visible)
        if decision is None or decision.size_usd <= 0:
            n_skip += 1
            continue
        whale_px = float(sig.get("price") or 0.0)
        fill: Optional[FillResult] = None
        ladder = sig.get("book_snapshot")
        if ladder:
            size_shares = (decision.size_usd / whale_px) if whale_px > 0 else 0.0
            whale_sh = float(sig.get("whale_size_shares") or 0.0)
            fill = precise_fill(ladder, "BUY", size_shares, whale_sh)
        if fill is None and sig.get("ob_row"):
            fill = coarse_fill(sig["ob_row"], "BUY", decision.size_usd)
        if fill is None:
            n_unc += 1
            continue
        o_x = outcome
        if exit_fn is not None:
            try:
                o_x = float(exit_fn(sig, fill))
            except Exception:
                o_x = outcome  # exit replay is an overlay, never a crash source
        edge = (o_x - fill.price) * fill.fill_fraction - fee_roundtrip
        out.append(ReplayedSignal(
            market_id=str(sig.get("market_id")),
            trader=str(sig.get("trader_address") or ""),
            model=fill.model, fill_price=fill.price,
            fill_fraction=fill.fill_fraction, outcome=outcome,
            edge_net=edge, caveats=list(fill.caveats),
        ))
    return ReplayReport(
        replayed=out, n_signals=len(out), n_uncovered=n_unc, n_skipped=n_skip,
        n_precise=sum(1 for r in out if r.model == PRECISE),
        n_coarse=sum(1 for r in out if r.model == COARSE),
    )

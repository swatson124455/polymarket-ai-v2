"""Startup state restoration — the CLAUDE.md net-counter tier, ordering-safe.

v2's latent bug (salvage pattern.state_restore_netcounter, verified): it set
_state_restored=True at mirror_bot.py:496 BEFORE the _entered_market_sides
rebuild at :562-592 — a silent partial-restore window where the dedup guard
approved entries it should have blocked. v3 makes that impossible
structurally: guards start fail-closed, and this orchestrator calls
guards.mark_restored() ONLY after every loader has completed. Any loader
exception leaves the guards closed (bot scans but cannot enter) and the
failure is the caller's to surface loudly.

Loaders are injected (async callables returning plain rows) so this module is
DB-agnostic and unit-testable without Postgres:

  fetch_entered(bot_name) -> [(condition_id, side), ...]
      v2 source: trade_events ENTRY rows for this bot, SAME execution mode
      only (the S244 mode filter — paper history must not block live and
      vice versa). v3 keeps that contract.
  fetch_open(bot_name)    -> [(condition_id, side), ...]
      v2 source: positions table, status='open', is_paper matching the mode
      (the S228 is_paper filter). v3 keeps that contract.
  fetch_daily_exposure(bot_name) -> float
      v2 source: trade_events SUM(ENTRY) - SUM(EXIT) for today (net counter —
      never a stored scalar; CLAUDE.md State Persistence Decision Tree).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable, Tuple

from mirror_v3.guards import OneBetPerMarketGuards

Rows = Iterable[Tuple[str, str]]


@dataclass
class RestoreResult:
    restored: bool
    entered_loaded: int
    open_loaded: int
    daily_exposure_usd: float
    error: str | None = None


async def restore_state(
    guards: OneBetPerMarketGuards,
    fetch_entered: Callable[[], Awaitable[Rows]],
    fetch_open: Callable[[], Awaitable[Rows]],
    fetch_daily_exposure: Callable[[], Awaitable[float]],
) -> RestoreResult:
    """Run ALL loaders, then — and only then — open the guards.

    On any loader failure: guards stay fail-closed, result.restored=False and
    result.error carries the cause. The caller decides whether to retry, halt,
    or run scan-only; it must NOT trade around a closed guard set.
    """
    entered_n = open_n = 0
    exposure = 0.0
    try:
        for cid, side in await fetch_entered():
            guards.load_entered(cid, side)
            entered_n += 1
        for cid, side in await fetch_open():
            guards.load_open(cid, side)
            open_n += 1
        exposure = float(await fetch_daily_exposure())
    except Exception as e:  # noqa: BLE001 — any partial restore must stay closed
        return RestoreResult(
            restored=False, entered_loaded=entered_n, open_loaded=open_n,
            daily_exposure_usd=exposure, error=f"{type(e).__name__}: {e}",
        )
    # Every loader succeeded — single, final, atomic open.
    guards.mark_restored()
    return RestoreResult(
        restored=True, entered_loaded=entered_n, open_loaded=open_n,
        daily_exposure_usd=exposure,
    )

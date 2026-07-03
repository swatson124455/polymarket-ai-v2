"""MB exit ladder as a pure function, replayable against a price series.

Port of MirrorBot._check_and_execute_exits (bots/mirror_bot.py:1274-1441,
S99/S137/S140/S146/S150/S158/S168) — verified 2026-07-02 that the live
ladder is NOT extractable (live instance state), so this is a faithful
re-implementation. Defaults mirror the live settings names and values:

  MIRROR_STOP_LOSS_PCT=0.15 (72h+), MIRROR_TAKE_PROFIT_PCT=0.25,
  MIRROR_STOP_LOSS_TIGHTEN_24H=-0.06, _48H=-0.12, _72H=-0.15,
  MIRROR_STOP_LOSS_NEAR_RES_HOURS=24, MIRROR_STOP_LOSS_NEAR_RES_PCT=-0.05,
  MIRROR_MAX_HOLD_FRACTION=0.80, MIRROR_FORCE_EXIT_HOURS=96,
  MIRROR_STOP_LOSS_GRACE_HOURS=2.0, edge decay -0.02/day halves stop <0.50.

Evaluation order per price tick matches live code exactly:
  hard stop -> take profit -> trader-sell (>0.5h) -> force exit ->
  max-hold-fraction -> grace-period skip -> graduated stop (+edge decay).

If the ladder never fires, the position holds to resolution (o in {0,1}).
Changing any default is a Tier-1 config change (state impact + rollback).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ExitParams:
    base_stop_pct: float = 0.15          # MIRROR_STOP_LOSS_PCT (72h+)
    take_profit_pct: float = 0.25        # MIRROR_TAKE_PROFIT_PCT
    stop_24h: float = 0.06               # |MIRROR_STOP_LOSS_TIGHTEN_24H|
    stop_48h: float = 0.12               # |MIRROR_STOP_LOSS_TIGHTEN_48H|
    stop_72h: float = 0.15               # |MIRROR_STOP_LOSS_TIGHTEN_72H|
    near_res_hours: float = 24.0         # MIRROR_STOP_LOSS_NEAR_RES_HOURS
    near_res_stop: float = 0.05          # |MIRROR_STOP_LOSS_NEAR_RES_PCT|
    max_hold_fraction: float = 0.80      # MIRROR_MAX_HOLD_FRACTION
    force_exit_hours: float = 96.0       # MIRROR_FORCE_EXIT_HOURS
    grace_hours: float = 2.0             # MIRROR_STOP_LOSS_GRACE_HOURS
    trader_sell_min_hold_h: float = 0.5  # S168 guard
    conf_decay_per_day: float = 0.02     # S150
    conf_floor: float = 0.50             # S150: below => halve stop
    hard_stop_pct: Optional[float] = None  # shared risk_manager hard stop;
                                           # None = not replayed (bot-agnostic
                                           # shared limit, operator-set)


@dataclass
class ReplayResult:
    o_x: float                 # per-dollar outcome under MB exits (exit price
                               # if exited, else 1/0 at resolution)
    exit_reason: str           # take_profit / stop_loss / force_exit /
                               # max_hold_fraction / trader_sell_exit /
                               # hard_stop / resolution
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    hours_held: float


def _effective_stop(
    hours_held: float, ttr_hours: Optional[float], entry_confidence: float,
    p: ExitParams,
) -> float:
    if ttr_hours is not None and ttr_hours < p.near_res_hours:
        stop = p.near_res_stop
    elif hours_held >= 72:
        stop = p.base_stop_pct
    elif hours_held >= 48:
        stop = p.stop_72h
    elif hours_held >= 24:
        stop = p.stop_48h
    else:
        stop = p.stop_24h
    decayed = entry_confidence - p.conf_decay_per_day * (hours_held / 24.0)
    if decayed < p.conf_floor:
        stop *= 0.50
    return stop


def replay_exit(
    entry_price: float,
    entry_time: datetime,
    price_series: list[tuple[datetime, float]],
    resolution_time: datetime,
    resolution_outcome: float,          # 1.0 if the held token won else 0.0
    entry_confidence: float = 0.55,
    trader_sell_times: Optional[list[datetime]] = None,
    params: Optional[ExitParams] = None,
) -> ReplayResult:
    """Replay MB's ladder over a chronological (ts, price) series for the
    HELD token. Series ticks after resolution_time are ignored.
    """
    p = params or ExitParams()
    entry = max(float(entry_price), 1e-6)
    sells = sorted(trader_sell_times or [])

    def result(reason: str, ts: datetime, px: float) -> ReplayResult:
        held = (ts - entry_time).total_seconds() / 3600.0
        return ReplayResult(o_x=float(px), exit_reason=reason, exit_time=ts,
                            exit_price=float(px), hours_held=held)

    for ts, price in sorted(price_series, key=lambda x: x[0]):
        if ts <= entry_time or ts > resolution_time:
            continue
        hours_held = (ts - entry_time).total_seconds() / 3600.0
        pnl_pct = (float(price) - entry) / entry
        ttr_hours = (resolution_time - ts).total_seconds() / 3600.0

        # 1. Shared inviolable hard stop (S172 D7) — only if configured.
        if p.hard_stop_pct is not None and pnl_pct <= -abs(p.hard_stop_pct):
            return result("hard_stop", ts, price)
        # 2. Take-profit (S99).
        if pnl_pct >= p.take_profit_pct:
            return result("take_profit", ts, price)
        # 3. Trader-sell exit (S168), only after min hold.
        if hours_held > p.trader_sell_min_hold_h and any(
            s <= ts for s in sells
        ):
            return result("trader_sell_exit", ts, price)
        # 4. Absolute force-exit (S140) — regardless of TTR.
        if hours_held >= p.force_exit_hours:
            return result("force_exit", ts, price)
        # 5. Resolution-relative max-hold (S137 C11).
        if hours_held > 0:
            total = hours_held + max(ttr_hours, 0.0)
            if hours_held / max(total, 1.0) >= p.max_hold_fraction:
                return result("max_hold_fraction", ts, price)
        # 6. Grace period (S158): skip stop-loss unless near resolution.
        if hours_held < p.grace_hours and ttr_hours >= p.near_res_hours:
            continue
        # 7. Graduated stop-loss (S137 C10 / S146) with edge decay (S150).
        stop = _effective_stop(hours_held, ttr_hours, entry_confidence, p)
        if pnl_pct <= -stop:
            return result("stop_loss", ts, price)

    held = (resolution_time - entry_time).total_seconds() / 3600.0
    return ReplayResult(
        o_x=float(resolution_outcome), exit_reason="resolution",
        exit_time=None, exit_price=None, hours_held=held,
    )

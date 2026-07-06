"""A3 — dual exit-policy tailability (docs/ASSUMPTION_AUDIT_2026-07-06.md).

The replayed ladder is the DEAD bot's never-validated exit policy; TailScore
must therefore also report the same entries under hold-to-resolution so the
policies can be compared before anything gates on either.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import bots.mirror_scoring.tailability as T
from bots.mirror_scoring.config import ScoringConfig
from bots.mirror_scoring.estimand import Entry

T0 = datetime(2026, 6, 1, 12, 0, 0)


def _entry(cid, won=True):
    return Entry(
        trader="0xaaa0000000000000000000000000000000000001",
        condition_id=cid, token_id=f"tok-{cid}", side="BUY",
        price=0.50, timestamp=T0, size=100.0,
        resolution="YES" if won else "NO", token_won=won,
    )


@pytest.mark.asyncio
async def test_a3_ladder_loss_vs_hold_win_both_reported(monkeypatch):
    """Price crashes (ladder stops out at a loss) then the token WINS at
    resolution. The ladder metric must be negative, the hold metric positive —
    and both must be visible on the TailScore."""

    async def fake_price_at(db, token_id, at_time, max_staleness_s=30):
        return 0.50, at_time  # fill at 0.50 for every entry

    async def fake_price_series(db, token_id, start, end, max_rows=20000):
        # crash to 0.30 three hours in: past the 2h grace, pnl -40% <= stop
        return [(start + timedelta(hours=3), 0.30)]

    monkeypatch.setattr(T, "price_at", fake_price_at)
    monkeypatch.setattr(T, "price_series", fake_price_series)

    entries = [_entry("0xc1", won=True), _entry("0xc2", won=True)]
    resolved = {"0xc1": T0 + timedelta(hours=100),
                "0xc2": T0 + timedelta(hours=100)}
    ts = await T.score_tailability(
        db=None, trader=entries[0].trader, entries=entries,
        resolved_time=resolved, cfg=ScoringConfig(),
    )
    fee = ScoringConfig().FEE_ROUNDTRIP
    assert ts.n_covered == 2
    assert ts.mu_net == pytest.approx(0.30 - 0.50 - fee)       # ladder: -0.22
    assert ts.mu_net_hold == pytest.approx(1.00 - 0.50 - fee)  # hold:  +0.48
    assert ts.mu_net_hold > ts.mu_net
    assert ts.delta_exit == pytest.approx(0.30 - 1.00)         # exit cost vs hold


@pytest.mark.asyncio
async def test_a3_policies_agree_when_ladder_never_fires(monkeypatch):
    """No tick ever trips the ladder -> position holds to resolution -> the
    two policies are the same number."""

    async def fake_price_at(db, token_id, at_time, max_staleness_s=30):
        return 0.50, at_time

    async def fake_price_series(db, token_id, start, end, max_rows=20000):
        return [(start + timedelta(hours=3), 0.52)]  # small drift, no trigger

    monkeypatch.setattr(T, "price_at", fake_price_at)
    monkeypatch.setattr(T, "price_series", fake_price_series)

    entries = [_entry("0xc1", won=True), _entry("0xc2", won=False)]
    resolved = {"0xc1": T0 + timedelta(hours=100),
                "0xc2": T0 + timedelta(hours=100)}
    ts = await T.score_tailability(
        db=None, trader=entries[0].trader, entries=entries,
        resolved_time=resolved, cfg=ScoringConfig(),
    )
    assert ts.mu_net == pytest.approx(ts.mu_net_hold)
    assert ts.l_net == pytest.approx(ts.l_net_hold)


@pytest.mark.asyncio
async def test_a3_uncovered_trader_has_hold_defaults(monkeypatch):
    async def none_price_at(db, token_id, at_time, max_staleness_s=30):
        return None  # no usable price sample -> nothing covered

    monkeypatch.setattr(T, "price_at", none_price_at)
    ts = await T.score_tailability(
        db=None, trader="0xaaa0000000000000000000000000000000000001",
        entries=[_entry("0xc1")], resolved_time={"0xc1": T0 + timedelta(hours=10)},
        cfg=ScoringConfig(),
    )
    assert ts.n_covered == 0 and ts.watch_only
    assert ts.l_net_hold == float("-inf")

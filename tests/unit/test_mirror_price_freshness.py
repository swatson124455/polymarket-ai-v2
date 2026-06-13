"""S244: MirrorBot price-freshness helper tests (`_fresh_side_price`).

The DB index price (markets.yes_price/no_price) was ~92% >7d stale (median ~57d),
yet it was the slippage anchor AND the recorded entry/cost-basis. `_fresh_side_price`
replaces it with: live midpoint of the traded token → fresh-DB (≤ staleness window)
→ skip. These tests pin that ordering, the skip-on-stale safety, and side-correctness.

The live key is the `token_id` param (the RTDS asset_id — the EXACT token being
traded, side-correct by construction). `_fresh_side_price` touches only
`self.base_engine.{client,db}`, so `__new__` + a mock engine is sufficient and avoids
the heavy bot/coordinator construction.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bots.mirror_bot import MirrorBot


# ── Helpers ──────────────────────────────────────────────────────────────────

def _row(yes_price=None, no_price=None, age_s=None):
    """A markets row as the helper reads it (attribute access on yes/no/age_s)."""
    r = MagicMock()
    r.yes_price = yes_price
    r.no_price = no_price
    r.age_s = age_s
    return r


def _make_bot(*, midpoint=None, midpoint_raises=False, db_row=None,
              has_client=True, has_db=True):
    bot = MirrorBot.__new__(MirrorBot)
    engine = MagicMock()

    # Live source: client.get_token_midpoint
    if has_client:
        engine.client = MagicMock()
        if midpoint_raises:
            engine.client.get_token_midpoint = AsyncMock(side_effect=RuntimeError("clob down"))
        else:
            engine.client.get_token_midpoint = AsyncMock(return_value=midpoint)
    else:
        engine.client = None

    # Fallback source: db.get_session → row
    if has_db:
        engine.db = MagicMock()
        engine.db.session_factory = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        ctx.execute = AsyncMock(
            return_value=MagicMock(fetchone=MagicMock(return_value=db_row)))
        engine.db.get_session = MagicMock(return_value=ctx)
    else:
        engine.db = None

    bot.base_engine = engine
    return bot


# ── 1. Live midpoint of the traded token is the primary source ───────────────

@pytest.mark.asyncio
async def test_live_midpoint_is_primary():
    bot = _make_bot(midpoint=0.42)
    price, src = await bot._fresh_side_price("0xcid", "YES", "tok-yes")
    assert price == 0.42
    assert src == "live_midpoint"
    # side-correct: the traded token was passed straight to the live fetch
    bot.base_engine.client.get_token_midpoint.assert_awaited_once_with("tok-yes")


@pytest.mark.asyncio
async def test_side_correctness_no_uses_traded_token():
    bot = _make_bot(midpoint=0.58)
    price, src = await bot._fresh_side_price("0xcid", "NO", "tok-no")
    assert price == 0.58 and src == "live_midpoint"
    bot.base_engine.client.get_token_midpoint.assert_awaited_once_with("tok-no")


@pytest.mark.asyncio
async def test_token_id_fallback_to_market_data():
    # empty token_id → resolve {side}_token_id from market_data
    bot = _make_bot(midpoint=0.40)
    price, src = await bot._fresh_side_price(
        "0xcid", "NO", "", {"yes_token_id": "y", "no_token_id": "tok-no"})
    assert price == 0.40 and src == "live_midpoint"
    bot.base_engine.client.get_token_midpoint.assert_awaited_once_with("tok-no")


# ── 2. DB fallback only when live unavailable AND fresh ──────────────────────

@pytest.mark.asyncio
async def test_db_fallback_when_live_none_and_fresh():
    bot = _make_bot(midpoint=None, db_row=_row(yes_price=0.33, no_price=0.67, age_s=60.0))
    price, src = await bot._fresh_side_price("0xcid", "YES", "tok-yes")
    assert price == 0.33 and src == "db_fresh"


@pytest.mark.asyncio
async def test_db_fallback_side_correct_no():
    bot = _make_bot(midpoint=None, db_row=_row(yes_price=0.33, no_price=0.67, age_s=60.0))
    price, src = await bot._fresh_side_price("0xcid", "NO", "tok-no")
    assert price == 0.67 and src == "db_fresh"


@pytest.mark.asyncio
async def test_live_exception_falls_through_to_fresh_db():
    bot = _make_bot(midpoint_raises=True,
                    db_row=_row(yes_price=0.40, no_price=0.60, age_s=10.0))
    price, src = await bot._fresh_side_price("0xcid", "YES", "tok-yes")
    assert price == 0.40 and src == "db_fresh"


@pytest.mark.asyncio
async def test_live_extreme_price_rejected_then_db():
    # live 0.0 = market at resolution → rejected → fresh DB used
    bot = _make_bot(midpoint=0.0, db_row=_row(yes_price=0.45, no_price=0.55, age_s=30.0))
    price, src = await bot._fresh_side_price("0xcid", "YES", "tok-yes")
    assert price == 0.45 and src == "db_fresh"


# ── 3. Skip-on-stale: NEVER return a >threshold-stale price ───────────────────

@pytest.mark.asyncio
async def test_skip_when_live_none_and_db_stale():
    # DB age 999999s ≫ 300s window → skip rather than use the stale price
    bot = _make_bot(midpoint=None, db_row=_row(yes_price=0.33, no_price=0.67, age_s=999999.0))
    price, src = await bot._fresh_side_price("0xcid", "YES", "tok-yes")
    assert price is None and src == "stale"


@pytest.mark.asyncio
async def test_skip_when_live_none_and_no_db_row():
    bot = _make_bot(midpoint=None, db_row=None)
    price, src = await bot._fresh_side_price("0xcid", "YES", "tok-yes")
    assert price is None and src == "stale"


@pytest.mark.asyncio
async def test_skip_when_live_exception_then_stale_db():
    bot = _make_bot(midpoint_raises=True,
                    db_row=_row(yes_price=0.40, no_price=0.60, age_s=100000.0))
    price, src = await bot._fresh_side_price("0xcid", "YES", "tok-yes")
    assert price is None and src == "stale"


@pytest.mark.asyncio
async def test_skip_when_db_age_null():
    # age_s NULL (no updated_at) is treated as not-fresh → skip
    bot = _make_bot(midpoint=None, db_row=_row(yes_price=0.33, no_price=0.67, age_s=None))
    price, src = await bot._fresh_side_price("0xcid", "YES", "tok-yes")
    assert price is None and src == "stale"


# ── 4. Slippage anchor is the FRESH price, not the stale fill ─────────────────

def test_slippage_anchor_is_fresh_price():
    """Production (mirror_bot.py): `price = _fresh_side_price(...)` then
    `_slip_pct = abs(price - _old_price) / _old_price`. The anchor `price` is now
    the live midpoint, not the ~57d-stale DB value. This replicates that formula
    (pattern: test_mirror_bot_dust_gate.py) to pin the relationship.
    """
    whale_fill = 0.50  # _old_price (the whale's recorded fill)
    fresh_price = 0.56  # live midpoint returned by _fresh_side_price
    stale_db_price = 0.95  # a 57d-stale value the index could have returned

    slip_fresh = abs(fresh_price - whale_fill) / whale_fill
    slip_stale = abs(stale_db_price - whale_fill) / whale_fill

    # Fresh slippage is the REAL 12%; the stale anchor fabricated a 90% gap that
    # would have crushed wf_slippage to 0 (the cliff) — a spurious veto.
    assert round(slip_fresh, 4) == 0.12
    assert round(slip_stale, 4) == 0.90
    assert slip_fresh != slip_stale


# ── 5. Degenerate inputs ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_token_anywhere_skips_live_uses_fresh_db():
    # no token_id param AND no market_data → live fetch skipped (no token) → fresh DB
    bot = _make_bot(midpoint=0.42, db_row=_row(yes_price=0.5, no_price=0.5, age_s=10.0))
    price, src = await bot._fresh_side_price("0xcid", "YES", "")
    assert src == "db_fresh"
    bot.base_engine.client.get_token_midpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_client_no_db_skips():
    bot = _make_bot(has_client=False, has_db=False)
    price, src = await bot._fresh_side_price("0xcid", "YES", "tok-yes")
    assert price is None and src == "stale"


@pytest.mark.asyncio
async def test_bad_side_returns_none():
    bot = _make_bot(midpoint=0.42)
    price, src = await bot._fresh_side_price("0xcid", "MAYBE", "tok-yes")
    assert price is None and src == "bad_side"

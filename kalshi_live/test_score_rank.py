"""Pins for SCORE-BASED RANKING (KALSHI_SCORE_RANK) — rank on measured capture, not pool alone.

THE POINT, stated precisely because it is easy to state wrongly: the POOL MATTERS. It is a linear
multiplier in the reward (reward = share x pool) and is never dropped. It is a poor RANK KEY only
because it is one of two terms and the one that varies less — measured over 30 series / 165
book-side depth readings (venue_scan.json 2026-07-25), pool spans 6x while rival qualifying depth,
which sets `share`, spans 71,330x. The key used is therefore the PRODUCT, at full pool weight.

  T1 POOL IS NOT DISCARDED  — with equal share, the bigger pool still wins. Non-negotiable.
  T2 CAPTURE BEATS POOL     — a small-pool empty book outranks a big-pool crowded one.
  T3 COLD CACHE IS LEGACY   — no scores => byte-for-byte the pool ordering.
  T4 EXPLORATION            — unscored markets are guaranteed slots, else the venue is never swept.
  T5 DECAY                  — a stale score blends back to the pool prior; it cannot pin the bot.
  T6 SWING PENALTY          — a market whose reference price whips around is discounted.
  T7 FAILS OPEN             — corrupt/garbage cache never raises, never blocks.
  T8 SHIPS OFF              — installing it changes nothing until switched on.
"""
import json

import kalshi_market_scores as ks
from test_live_hardening import q


def _row(t, pool):
    return {"ticker": t, "usd_day": pool}


def test_pool_is_not_discarded_equal_share_bigger_pool_wins():
    """reward = share x pool. With identical measured capture-per-pool, the bigger pool MUST rank
    higher. A ranker that ignored pool would tie these — that would be wrong, not conservative."""
    m = {}
    now = 1000.0
    # same share (10%), different pools -> capture scales WITH the pool
    ks.update(m, "BIG", 0.10 * 10000, 0.50, now=now)
    ks.update(m, "SMALL", 0.10 * 1000, 0.50, now=now)
    out = ks.rank(m, [_row("SMALL", 1000), _row("BIG", 10000)], now=now, explore=0)
    assert [r["ticker"] for r in out] == ["BIG", "SMALL"]


def test_capture_beats_pool_when_the_big_pool_is_crowded():
    """The measured case: KXFUNDRAISING $10,470/day models to $5.65/day (crowded);
    KXVOGUECOVER $1,800/day models to $42.03/day (nearly empty)."""
    m = {}
    now = 1000.0
    ks.update(m, "KXFUNDRAISING-X", 5.652, 0.50, now=now)
    ks.update(m, "KXVOGUECOVER-X", 42.027, 0.50, now=now)
    out = ks.rank(m, [_row("KXFUNDRAISING-X", 10470), _row("KXVOGUECOVER-X", 1800)],
                  now=now, explore=0)
    assert [r["ticker"] for r in out][0] == "KXVOGUECOVER-X"


def test_cold_cache_is_exactly_the_legacy_pool_order():
    rows = [_row("A", 100), _row("B", 900), _row("C", 500)]
    out = ks.rank({}, rows, now=1000.0, explore=0)
    assert [r["ticker"] for r in out] == ["B", "C", "A"]


def test_exploration_guarantees_unscored_markets_get_sampled():
    """Without this the bot converges on whatever it read first and never discovers anything
    better — the read budget only covers ~200 of 2,271 active programs per cycle."""
    m = {}
    ks.update(m, "KNOWN", 999.0, 0.50, now=1000.0)      # a huge measured winner
    rows = [_row("KNOWN", 100), _row("NEW1", 1), _row("NEW2", 1)]
    out = ks.rank(m, rows, now=1000.0, explore=2)
    assert [r["ticker"] for r in out[:2]] == ["NEW1", "NEW2"], "unscored get the reserved slots"
    assert out[2]["ticker"] == "KNOWN"
    assert len(out) == 3 and len({id(r) for r in out}) == 3, "no market duplicated or dropped"


def test_stale_scores_decay_back_toward_the_pool_prior():
    m = {}
    ks.update(m, "OLD", 500.0, 0.50, now=1000.0)
    fresh, _ = ks.score(m, "OLD", 10.0, now=1000.0)
    aged, _ = ks.score(m, "OLD", 10.0, now=1000.0 + 10 * ks.HALF_LIFE_S)
    assert fresh > aged, "an ageing score must lose weight"
    assert abs(aged - 10.0) < 1.0, "and blend back to the pool prior, not to zero"


def test_swing_penalty_discounts_a_whipping_reference_price():
    """A market whose reference price moves between cycles fills us adversely — that is how a maker
    hands the rewards back. Two markets, identical capture, one stable and one moving 5c a cycle."""
    m = {}
    for now, ref in ((1000.0, 0.50), (1060.0, 0.50), (1120.0, 0.50)):
        ks.update(m, "STABLE", 20.0, ref, now=now)
    for now, ref in ((1000.0, 0.50), (1060.0, 0.55), (1120.0, 0.60)):
        ks.update(m, "SWINGY", 20.0, ref, now=now)
    s_stable, _ = ks.score(m, "STABLE", 100.0, now=1120.0)
    s_swingy, _ = ks.score(m, "SWINGY", 100.0, now=1120.0)
    assert s_stable > s_swingy
    assert m["SWINGY"]["ref_move"] > m["STABLE"]["ref_move"] == 0.0


def test_cache_fails_open_on_garbage(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    assert ks.load(str(p)) == {}
    p.write_text(json.dumps({"schema": 999, "markets": {"X": {"capture": 5}}}))
    assert ks.load(str(p)) == {}, "wrong schema must fail open, not be trusted"
    assert ks.load(str(tmp_path / "nope.json")) == {}


def test_roundtrip_save_load(tmp_path):
    p = str(tmp_path / "s.json")
    m = {}
    ks.update(m, "T", 12.5, 0.42, now=1000.0)
    ks.save(p, m)
    got = ks.load(p)
    assert got["T"]["capture"] == 12.5 and got["T"]["ref"] == 0.42


def test_ships_off_and_defaults_are_sane():
    assert q.SCORE_RANK == 0, "installing this must change nothing until switched on"
    assert q.SCORE_EXPLORE > 0, "zero exploration would freeze the bot on its first sample"


def test_flag_off_emits_no_score_keys(monkeypatch, tmp_path):
    from test_live_hardening import MockClient, _run, _cfg
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "SCORE_RANK", 0)
    monkeypatch.setattr(q, "select_footprint", lambda p, n: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00+00:00"}])
    row = _run(monkeypatch, MockClient(mode="live"), str(tmp_path))
    assert "scored_markets" not in row

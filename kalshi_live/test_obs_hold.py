"""OBS_HOLD — per-ticker observability hold on the D3 ramp (Proposal A, built DARK).

The loss class this exists for (M-1 census, 2026-08-11, frozen offline_results.json):
16/16 post-tape sized new-allowlist tickers were BLIND at size-up — the bot always sizes
a brand-new market before the venue estimates feed can see whether it pays; the proven-
SERIES exemption (W7) lets a fresh ticker of a paying series walk the rung ladder on age
alone (KXTRUMPTIME-26AUG15: 5 strikes sized cycle-1, episode realized -$5.5078).

Pins:
  * flag ships OFF (KALSHI_OBS_HOLD=0) — behavior byte-identical to legacy
  * ON: a FRESH ticker (first-seen age < OBS_HOLD_FRESH_S) holds at rung
    OBS_HOLD_MAX_RUNG (=0 -> 5ct) until the est feed COVERS it AND accrued >=
    OBS_HOLD_MIN_USD — even when its series is proven
  * BLOCKING READ ONLY: the hold can only LOWER the rung; it never lifts the W7
    new-series clamp and never floors/admits anything (no EST_FEED semantics)
  * stale/missing feed -> held (fails CLOSED toward smaller size, D3 doctrine)
  * a ticker older than OBS_HOLD_FRESH_S is EXEMPT — a dead recorder can never
    deflate an established book
  * _d3_est_ct mirrors the hold (budget walk must not over-charge held tickers)
  * qstats surfaces obs_hold_bound when the hold binds
"""
import pytest

import maker_kalshi_quoter as q

PAID = {"KXOLD": {"verdict": "paid", "credits_n": 3, "due_filled_events": 2}}
T = "KXOLD-26AUG15-H1"          # fresh ticker of a PROVEN series — the exact hole


@pytest.fixture
def hold_on(monkeypatch):
    monkeypatch.setattr(q, "OBS_HOLD", 1)
    monkeypatch.setattr(q, "OBS_HOLD_MIN_USD", 1.20)
    monkeypatch.setattr(q, "OBS_HOLD_FRESH_S", 86400.0)
    monkeypatch.setattr(q, "OBS_HOLD_MAX_RUNG", 0)
    yield


def _feed(monkeypatch, table):
    monkeypatch.setattr(q, "_est_feed_cached", lambda now_ts, max_age_s=120.0: table)


def test_flag_ships_off():
    assert q.OBS_HOLD == 0


def test_flag_off_byte_identical(monkeypatch):
    """OFF: proven fresh ticker walks the ladder exactly as legacy, feed never consulted."""
    def _boom(*a, **k):
        raise AssertionError("est feed consulted with OBS_HOLD off")
    monkeypatch.setattr(q, "_est_feed_cached", _boom)
    fs = {T: 0.0}
    assert q._d3_ramp_ct(T, 1800.0, fs, PAID) == 50


def test_fresh_proven_ticker_held_when_feed_blind(hold_on, monkeypatch):
    """THE FAILING-BEFORE PIN: proven series, fresh ticker, feed does not cover it —
    age alone must NOT release size (this is the KXTRUMPTIME cycle-1 shape)."""
    _feed(monkeypatch, {})
    fs = {T: 0.0}
    assert q._d3_ramp_ct(T, 1800.0, fs, PAID) == 5     # held at rung 0, not 50


def test_covered_at_floor_releases(hold_on, monkeypatch):
    _feed(monkeypatch, {T: 1.20})
    fs = {T: 0.0}
    assert q._d3_ramp_ct(T, 1800.0, fs, PAID) == 50    # observability met -> normal ramp


def test_covered_below_floor_holds(hold_on, monkeypatch):
    _feed(monkeypatch, {T: 1.19})
    fs = {T: 0.0}
    assert q._d3_ramp_ct(T, 1800.0, fs, PAID) == 5


def test_old_ticker_exempt(hold_on, monkeypatch):
    """Age past OBS_HOLD_FRESH_S: feed state is irrelevant — established books are
    untouchable by a recorder outage."""
    _feed(monkeypatch, {})
    fs = {T: 0.0}
    assert q._d3_ramp_ct(T, 86400.0 + 1800.0, fs, PAID) == 50


def test_hold_never_lifts_new_series_clamp(hold_on, monkeypatch):
    """BLOCKING READ ONLY: an unproven series stays at the W7 clamp even when the est
    feed shows accrued >= floor — observability releases the HOLD, never the CLAMP."""
    _feed(monkeypatch, {"KXNEW-26AUG15-H1": 5.0})
    fs = {"KXNEW-26AUG15-H1": 0.0}
    assert q._d3_ramp_ct("KXNEW-26AUG15-H1", 1800.0, fs, PAID) == 10


def test_est_ct_mirrors_hold(hold_on, monkeypatch):
    """Budget walk parity: _d3_est_ct must estimate a held ticker at held size, or the
    select-budget walk over-charges (the exact D1/W6 over-read class)."""
    _feed(monkeypatch, {})
    monkeypatch.setattr(q, "_D3_FIRST_SEEN", {T: 0.0})
    assert q._d3_est_ct(T, 1800.0) == 5


def test_qstats_counter(hold_on, monkeypatch):
    _feed(monkeypatch, {})
    fs = {T: 0.0}
    stats = {}
    q._d3_ramp_ct(T, 1800.0, fs, PAID, qstats=stats)
    assert stats.get("obs_hold_bound") == 1


def test_qstats_not_counted_when_released(hold_on, monkeypatch):
    _feed(monkeypatch, {T: 2.0})
    fs = {T: 0.0}
    stats = {}
    q._d3_ramp_ct(T, 1800.0, fs, PAID, qstats=stats)
    assert "obs_hold_bound" not in stats

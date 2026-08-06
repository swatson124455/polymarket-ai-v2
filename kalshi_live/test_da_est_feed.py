"""D-A (operator-ruled 2026-08-06, "yes but verify if our tool has any value") —
failing-before tests: the venue's PER-USER reward-estimate feed (kalshi_estimates_recorder
snapshots) becomes an input to the $1-floor gate, flag-gated KALSHI_EST_FEED (default 0).

Semantics caveat (documented, OPEN): the venue calls the number a live estimate that "can
move up or down"; observed today it climbed like an accrual. The gate therefore uses
max(model, venue_est) — the venue's own number can only REDUCE false refusals (the audit's
spread-thin-earn-zero defect class), never admit less than the model would. The est→credit
truth checkpoint is the KXAPRPOTUS program ending 2026-08-07T15:00Z.
"""
import datetime as dt
import json

import maker_kalshi_quoter as q


def _now():
    return dt.datetime(2026, 8, 6, 12, 0, 0, tzinfo=dt.timezone.utc)


def _mkt(ticker="KXESTFEED-26AUG08-T1", usd_day=1.0, life_min=1440.0):
    return {"ticker": ticker, "target": 50.0, "df": 0.5, "usd_day": usd_day,
            "end": (_now() + dt.timedelta(days=1)).isoformat(), "life_min": life_min}


def _write_feed(tmp_path, monkeypatch, rows, map_rows, age_s=60.0):
    snap = tmp_path / f"estimates-{_now():%Y%m}.jsonl"
    snap.write_text(json.dumps(
        {"ts": (_now() - dt.timedelta(seconds=age_s)).isoformat(), "estimates": rows}) + "\n")
    (tmp_path / "kalshi_program_map.json").write_text(json.dumps(map_rows))
    monkeypatch.setattr(q, "DATA_DIR", str(tmp_path))
    q._EST_FEED_CACHE.update({"ts": 0.0, "table": {}})     # bust the cache


def test_flag_off_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "EST_FEED", 0)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "PRESENCE_TABLE", {})
    monkeypatch.setattr(q, "PRESENCE_DEFAULT", 1.0)
    monkeypatch.setattr(q, "D3_RAMP", 0)
    _write_feed(tmp_path, monkeypatch,
                [{"program_id": "p1", "reward_centicents": 50000}],
                {"p1": {"market_ticker": "KXESTFEED-26AUG08-T1"}})
    yl = [(0.30, 5.0)]      # near-worthless book -> model expectation ~0
    nl = [(0.30, 5.0)]
    exp, ideal, _ = q._expected_credit_usd(_mkt(), yl, nl, 0.30, 0.30, 50.0, _now())
    assert exp < 1.0, "flag OFF must ignore the feed (byte-legacy)"


def test_flag_on_venue_estimate_clears_floor(tmp_path, monkeypatch):
    """$5.00 venue estimate on a market whose model expectation is under the floor:
    the gate must see at least the venue's own number."""
    monkeypatch.setattr(q, "EST_FEED", 1)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "PRESENCE_TABLE", {})
    monkeypatch.setattr(q, "PRESENCE_DEFAULT", 1.0)
    monkeypatch.setattr(q, "D3_RAMP", 0)
    _write_feed(tmp_path, monkeypatch,
                [{"program_id": "p1", "reward_centicents": 50000}],
                {"p1": {"market_ticker": "KXESTFEED-26AUG08-T1"}})
    yl = [(0.30, 5.0)]
    nl = [(0.30, 5.0)]
    exp, ideal, _ = q._expected_credit_usd(_mkt(), yl, nl, 0.30, 0.30, 50.0, _now())
    assert exp >= 5.0, f"venue est $5.00 must floor the expectation, got {exp}"


def test_flag_on_never_reduces_model_expectation(tmp_path, monkeypatch):
    """A tiny venue estimate must not DROP a market the model already admits —
    max(model, est), never a substitution."""
    monkeypatch.setattr(q, "EST_FEED", 1)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "PRESENCE_TABLE", {})
    monkeypatch.setattr(q, "PRESENCE_DEFAULT", 1.0)
    monkeypatch.setattr(q, "D3_RAMP", 0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 1e9)
    _write_feed(tmp_path, monkeypatch,
                [{"program_id": "p1", "reward_centicents": 1}],
                {"p1": {"market_ticker": "KXESTFEED-26AUG08-T1"}})
    m = _mkt(usd_day=100.0)
    yl = [(0.30, 100.0)]
    nl = [(0.30, 100.0)]
    monkeypatch.setattr(q, "EST_FEED", 0)
    exp_model, _, _ = q._expected_credit_usd(m, yl, nl, 0.30, 0.30, 50.0, _now())
    q._EST_FEED_CACHE.update({"ts": 0.0, "table": {}})
    monkeypatch.setattr(q, "EST_FEED", 1)
    exp_both, _, _ = q._expected_credit_usd(m, yl, nl, 0.30, 0.30, 50.0, _now())
    assert exp_both >= exp_model


def test_stale_or_missing_feed_fails_open_to_model(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "EST_FEED", 1)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "PRESENCE_TABLE", {})
    monkeypatch.setattr(q, "PRESENCE_DEFAULT", 1.0)
    monkeypatch.setattr(q, "D3_RAMP", 0)
    # snapshot 3h old -> beyond EST_FEED_MAX_AGE_S -> ignored
    _write_feed(tmp_path, monkeypatch,
                [{"program_id": "p1", "reward_centicents": 50000}],
                {"p1": {"market_ticker": "KXESTFEED-26AUG08-T1"}},
                age_s=3 * 3600.0)
    yl = [(0.30, 5.0)]
    nl = [(0.30, 5.0)]
    exp, _, _ = q._expected_credit_usd(_mkt(), yl, nl, 0.30, 0.30, 50.0, _now())
    assert exp < 1.0, "stale feed must not inflate the gate"

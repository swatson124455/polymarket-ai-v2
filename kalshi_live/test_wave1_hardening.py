"""Wave-1 hardening (operator "1 yes 2 go", 2026-08-06): ramp-aware budget est +
identity-review B-1/B-2/B-3 (past-close predicate, close-cache persistence + probe
refusal for close-unknown rows, positive-entry TTL).

Measured basis: select_budget_used 208.4/210.25 vs real committed $16.85 (plans
00:52:08Z) — est charged full size (~$45-50) for ramp-capped (5-10ct actual) markets,
making the walk the binding pilot constraint and blocking CHIPBURRITO ($990/day,
drop_budget_full=6). B-1/B-2: 3/5 probe slots measured on beyond-horizon markets
(00:52Z, one closing 2028) via the restart warmup fail-open; KXEOWEEK-26JUL25 held a
slot with close in the PAST.

Pins:
  T1 walk est is capped at the D3 ramp ct (fresh ticker -> rung0 $5, not $40)
  T2 selection drops a market whose close is PAST (close_past_selected counter)
  T3 probe slots refuse close-UNKNOWN candidates (footprint fail-open unchanged)
  T4 positive close-cache entries expire after CLOSE_CACHE_POS_TTL_S
  T5 close cache persists to state and restores
"""
import datetime as dt
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_live_hardening import MockClient, _cfg, _run, q  # noqa: E402

NOW = dt.datetime(2026, 8, 6, 1, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _restore_total_cap_eff():
    prev = q._TOTAL_CAP_EFF[0]
    yield
    q._TOTAL_CAP_EFF[0] = prev


def test_t1_walk_est_capped_at_ramp(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=20, mktcap=40, totcap=100)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "SELECT_BUDGET", 1)
    monkeypatch.setattr(q, "SELECT_BUDGET_MARGIN", 0.0)
    monkeypatch.setattr(q, "SERIES_MAX_USD", 0.0)
    monkeypatch.setattr(q, "D3_RAMP", 1)
    monkeypatch.setattr(q, "_D3_FIRST_SEEN", {})     # every ticker fresh -> rung 0 = 5ct
    fp = [{"ticker": f"KXS{i:02d}-26AUG-A", "usd_day": 100.0 - i, "target": 1,
           "end": "2099-01-01T00:00:00Z"} for i in range(5)]
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: fp)
    row = _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
    # pre-fix: est $40/row -> keep 2, used 80. post-fix: est min($40, rung0 5ct=$5)=5
    assert row.get("select_budget_used") == 25.0, row.get("select_budget_used")
    assert "drop_budget_full" not in row, "all five must fit at ramp-true est"


def test_t2_selection_drops_past_close(monkeypatch):
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXDEAD"])
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 0)
    past = (NOW - dt.timedelta(hours=2)).isoformat()
    monkeypatch.setattr(q, "_close_cache_get", lambda t: past)
    monkeypatch.setattr(q, "_vol24_cache_get", lambda t: 0.0)
    prog = {"market_ticker": "KXDEAD-26AUG05-T1", "incentive_type": "liquidity",
            "target_size_fp": "100.00", "discount_factor_bps": 5000,
            "period_reward": 1000000,
            "start_date": (NOW - dt.timedelta(days=1)).isoformat(),
            "end_date": (NOW + dt.timedelta(days=1)).isoformat()}
    rows = q.select_footprint([prog], NOW)
    assert not rows, "a market whose close is PAST must never be selected"
    assert dict(q.FP_DROPS).get("close_past_selected") == 1


def test_t3_probe_slots_refuse_close_unknown(monkeypatch):
    q.FP_DROPS.clear()
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXALLOW"])
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 1)
    monkeypatch.setattr(q, "PROBE_MAX_SLOTS", 2)
    monkeypatch.setattr(q, "_PROBE_GATE_REFUSED", {})
    known = (NOW + dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(q, "_close_cache_get",
                        lambda t: known if "KNOWN" in t else None)
    rows = [{"ticker": "KXMYSTERY-26AUG-A", "usd_day": 999, "explore": True},
            {"ticker": "KXKNOWN-26AUG-A", "usd_day": 10, "explore": True},
            {"ticker": "KXALLOW-26AUG-A", "usd_day": 50}]
    kept = q._cap_probe_slots(list(rows), q.FP_DROPS)
    tickers = [r["ticker"] for r in kept]
    assert "KXKNOWN-26AUG-A" in tickers
    assert "KXMYSTERY-26AUG-A" not in tickers, \
        "a close-unknown row must not hold a probe slot (B-2 warmup class)"
    assert "KXALLOW-26AUG-A" in tickers, "allowlist fail-open untouched"
    assert dict(q.FP_DROPS).get("probe_close_unknown") == 1


def test_t4_positive_close_entries_expire(monkeypatch):
    q._CLOSE_TIME_CACHE.clear()
    q._close_cache_put("KXT-1", "2026-09-01T00:00:00Z")
    assert q._close_cache_get("KXT-1") == "2026-09-01T00:00:00Z"
    ct, stamp = q._CLOSE_TIME_CACHE["KXT-1"]
    q._CLOSE_TIME_CACHE["KXT-1"] = (ct, stamp - q.CLOSE_CACHE_POS_TTL_S - 1)
    assert q._close_cache_get("KXT-1") is None, \
        "an aged positive entry must force a re-read (venue can amend close_time)"


def test_t6_first_seen_ensured_before_walk(monkeypatch):
    """Review C-1: the walk must see REAL rungs on the first post-restart cycle, not
    rung-0 for everything (over-admission + false backstop alarms every restart)."""
    monkeypatch.setattr(q, "_D3_FIRST_SEEN", None)
    out = q._d3_first_seen_ensure({"d3_first_seen": {"KXT-9": 123.0}})
    assert out == {"KXT-9": 123.0}
    out2 = q._d3_first_seen_ensure({"d3_first_seen": {"OTHER": 1.0}})
    assert out2 == {"KXT-9": 123.0}, "idempotent — never reloads over a live map"
    src = open(q.__file__, encoding="utf-8", errors="replace").read()
    i = src.index("_d3_first_seen_ensure(st)")
    assert i < src.index("_limit9 = _total_cap()"), \
        "restore must precede the walk's est loop"


def test_t7_belt_mirrors_past_close():
    src = open(q.__file__, encoding="utf-8", errors="replace").read()
    assert "close_past_belt" in src, "review C-3: the belt drops past-close rows too"


def test_t5_close_cache_persist_restore():
    q._CLOSE_TIME_CACHE.clear()
    q._close_cache_put("KXT-2", "2026-09-01T00:00:00Z")
    q._close_cache_put("KXT-3", None)          # negative -> never persisted
    saved = q._close_cache_snapshot()
    assert saved == {"KXT-2": "2026-09-01T00:00:00Z"}
    q._CLOSE_TIME_CACHE.clear()
    q._close_cache_restore(saved)
    assert q._close_cache_get("KXT-2") == "2026-09-01T00:00:00Z"

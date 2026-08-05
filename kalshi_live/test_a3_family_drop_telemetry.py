"""A3 (logic audit 2026-08-05, operator-ruled: 'do rec' — telemetry now): the walk's
drop_family_budget was a bare COUNT (14/cycle live on 08-05, the gas-ladder tail) with
no record of WHICH tickers were evicted — so "is the eviction landing on the right
(lowest-priority) siblings" was unanswerable from telemetry, exactly the question the
A3 audit raised (same-pool siblings tie-break alphabetically). Additive plan key
family_dropped_tickers (capped list), nothing else changes."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_live_hardening import MockClient, _cfg, _run, q  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_total_cap_eff():
    """_run cycles write the mock equity into q._TOTAL_CAP_EFF[0] IN PLACE (a module
    global monkeypatch never touches). The other _run users (test_live_hardening,
    test_select_budget, ...) sort AFTER every consumer of _total_cap(), so the leak
    never fired before; this file sorts before test_alloc_incumbent_first and broke
    its cap arithmetic (3 order-dependent failures, full-suite run 2026-08-05).
    Restore the slot around every test here."""
    prev = q._TOTAL_CAP_EFF[0]
    yield
    q._TOTAL_CAP_EFF[0] = prev


def _arm(monkeypatch):
    _cfg(monkeypatch, join=20, mktcap=40, totcap=100)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "SELECT_BUDGET", 1)
    monkeypatch.setattr(q, "SELECT_BUDGET_MARGIN", 0.0)
    monkeypatch.setattr(q, "SERIES_MAX_USD", 63.0)
    fp = [{"ticker": f"KXFAM-26AUG-{i}", "usd_day": 100.0 - i, "target": 1,
           "end": "2099-01-01T00:00:00Z"} for i in range(5)]
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: fp)


def test_family_drops_name_their_tickers(monkeypatch, tmp_path):
    _arm(monkeypatch)
    row = _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
    assert row.get("drop_family_budget") == 4
    assert sorted(row.get("family_dropped_tickers") or []) == \
        [f"KXFAM-26AUG-{i}" for i in (1, 2, 3, 4)], \
        "every family-evicted ticker must be named in the plan row"


def test_no_family_drops_no_key(monkeypatch, tmp_path):
    _arm(monkeypatch)
    monkeypatch.setattr(q, "SERIES_MAX_USD", 0.0)      # family cap off
    row = _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
    assert "family_dropped_tickers" not in row

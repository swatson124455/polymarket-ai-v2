"""OPTION C — SELECT-TO-BUDGET (operator-named 2026-08-02, commit 2). The footprint is
walked in allocation-priority order accumulating est_commit_usd and capped near
_total_cap()*(1+MARGIN), with a per-family budget = _series_cap(). Contract under test:
- default OFF (KALSHI_SELECT_BUDGET=0) is a provable no-op;
- held markets are NEVER budget-gated; explore probes are charged probe cost;
- family budget bounds sibling multiplicity (the D6 bound);
- drops are counted (drop_budget_full / drop_family_budget) and the cap_desired backstop
  alarms (budget_backstop_fired) when it still fires;
- any walk fault fails OPEN to the unfiltered footprint (select_budget_fail)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_live_hardening import MockClient, _cfg, _run, q  # noqa: E402


def _fp(n, series="KXS"):
    return [{"ticker": f"{series}{i:02d}-26AUG-A", "usd_day": 100.0 - i, "target": 1,
             "end": "2099-01-01T00:00:00Z"} for i in range(n)]


def _arm(monkeypatch, footprint, on=1, margin=0.0):
    _cfg(monkeypatch, join=20, mktcap=40, totcap=100)   # mock equity $100 -> _total_cap 100
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_DOWN_HALT_USD", 1e9)
    monkeypatch.setattr(q, "SELECT_BUDGET", on)
    monkeypatch.setattr(q, "SELECT_BUDGET_MARGIN", margin)
    monkeypatch.setattr(q, "SERIES_MAX_USD", 0.0)       # family cap off unless a test sets it
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: footprint)


# unknown ref -> est_commit 0.5/0.5 at mktcap=40: per_side 20 -> 40ct @0.50 each side = $40


def test_off_is_noop(monkeypatch, tmp_path):
    _arm(monkeypatch, _fp(5), on=0)
    row = _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
    assert "select_budget_used" not in row and "drop_budget_full" not in row


def test_budget_caps_the_footprint_and_counts(monkeypatch, tmp_path):
    # 5 markets x $40 est vs limit $100 -> keep 2, drop 3
    _arm(monkeypatch, _fp(5))
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("select_budget_limit") == 100.0
    assert row.get("select_budget_used") == 80.0
    assert row.get("drop_budget_full") == 3
    assert len({o["ticker"] for o in c.created}) == 2, "only funded markets quote"


def test_priority_order_decides_who_fits(monkeypatch, tmp_path):
    # alloc_prio defaults to usd_day (ALLOC_KEY off in tests) -> highest pools kept
    _arm(monkeypatch, _fp(5))
    c = MockClient(mode="live", positions=[])
    _run(monkeypatch, c, str(tmp_path))
    kept = {o["ticker"] for o in c.created}
    assert kept == {"KXS00-26AUG-A", "KXS01-26AUG-A"}, kept


def test_held_market_never_gated(monkeypatch, tmp_path):
    _arm(monkeypatch, _fp(5))
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    # held position on the LOWEST-priority market: budget-exempt, still gets its quotes
    c = MockClient(mode="live", positions=[
        {"ticker": "KXS04-26AUG-A", "position_fp": "10", "market_exposure_dollars": "5.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert any(o["ticker"] == "KXS04-26AUG-A" for o in c.created), \
        "held market must survive the budget walk"
    assert row.get("select_budget_used") == 80.0, "held commit not double-counted"
    # the held market must stay IN the footprint (budget-exempt), not merely get a
    # strand-path exit — footprint = 2 funded + 1 held. (Mutation pin: gating held
    # markets drops footprint to 2 while the strand quote masks the created-orders check.)
    assert row.get("footprint") == 3, row.get("footprint")


def test_explore_probe_charged_probe_cost(monkeypatch, tmp_path):
    # 2 full markets fill the $80/$100 budget; a third EXPLORE market costs 5, fits
    fp = _fp(3)
    fp[2]["explore"] = True
    _arm(monkeypatch, fp)
    monkeypatch.setattr(q, "EXPLORE_PROBE_CT", 5)
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("select_budget_used") == 85.0
    assert "drop_budget_full" not in row
    assert any(o["ticker"] == "KXS02-26AUG-A" and o["count"] <= 5 for o in c.created)


def test_family_budget_bounds_siblings(monkeypatch, tmp_path):
    # one family, $63 family cap, $40/sibling -> 1 kept, 4 family-dropped (D6 bound)
    _arm(monkeypatch, _fp(5))
    monkeypatch.setattr(q, "SERIES_MAX_USD", 63.0)
    fp_one_family = [{"ticker": f"KXFAM-26AUG-{i}", "usd_day": 100.0 - i, "target": 1,
                      "end": "2099-01-01T00:00:00Z"} for i in range(5)]
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: fp_one_family)
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("drop_family_budget") == 4
    assert len({o["ticker"] for o in c.created}) == 1


def test_walk_fault_fails_open(monkeypatch, tmp_path):
    _arm(monkeypatch, _fp(2))
    import kalshi_capital_rank as kcr
    monkeypatch.setattr(kcr, "est_commit_usd",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    before = q._SILENT["select_budget_fail"]
    c = MockClient(mode="live", positions=[])
    _run(monkeypatch, c, str(tmp_path))
    assert q._SILENT["select_budget_fail"] == before + 1
    assert len({o["ticker"] for o in c.created}) == 2, "fault -> unfiltered footprint"


def test_backstop_alarm_pinned(monkeypatch):
    import inspect
    src = inspect.getsource(q)
    i = src.index('plan["budget_backstop_fired"]')
    assert "if SELECT_BUDGET and capped_markets:" in src[i - 400:i]


def test_knobs_hot_reloadable():
    import inspect
    src = inspect.getsource(q._refresh_safety_knobs)
    assert '"KALSHI_SELECT_BUDGET": ("SELECT_BUDGET", int)' in src
    assert '"KALSHI_SELECT_BUDGET_MARGIN": ("SELECT_BUDGET_MARGIN", float)' in src

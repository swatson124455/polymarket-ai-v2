"""Governor no-brainers (1.1 review 2026-07-31, operator-named 2026-08-01):
Gov-D5  — a market banned at the $5 rung THIS cycle must probe-size its series siblings
          THIS cycle, not next (the MLABELSHARE burn window: siblings churned full-size
          for one extra cycle because the L3 clamp ran at selection time against last
          cycle's mkt_out).
Gov-D10 — fresh process + lost state + failed mark read left the portfolio-tracking
          equity None: the SERIES_PCT family cap silently degraded to static-only (or
          fully OFF pure-PCT, since cap_desired treats cap<=0 as no gate). The mark-fail
          path now seeds cost-basis equity when no last-good value exists.
Gov-D9  — four knobs look hot-reloadable (same env file) but are import-time only;
          changed-but-not-applied is now loud every cycle instead of a silent no-op."""
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# test_live_hardening loads the quoter via importlib and REPLACES the sys.modules entry —
# importing it any other way here would patch a different module instance than _run drives.
from test_live_hardening import MockClient, _cfg, _run, q  # noqa: E402


# ---- Gov-D5: fresh ban clamps siblings same-cycle ----
def test_fresh_ban_probes_siblings_same_cycle(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    monkeypatch.setattr(q, "MKT_OUT_LOSS_USD", 5.0)
    monkeypatch.setattr(q, "EXPLORE_PROBE_CT", 5)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "KXFAM-26AUG-A", "usd_day": 100.0, "target": 1,
         "end": "2099-01-01T00:00:00Z"}])
    day = q.utcnow().strftime("%Y-%m-%d")
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"mkt_realized_day": day,
                   "mkt_realized_base": {"KXFAM-26AUG-B": 0.0}}, fh)
    # sibling B burns through the $5 rung THIS cycle (all-traded feed, flat position)
    c = MockClient(mode="live", positions=[],
                   traded=[{"ticker": "KXFAM-26AUG-B", "realized_pnl_dollars": "-6.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("mkt_out") == 1, "the burn must land in the permanent OUT set"
    assert row.get("series_probe", 0) >= 1, "sibling must be probe-clamped SAME cycle"
    assert c.created, "the sibling still quotes — probe-sized, not banned"
    assert all(o["count"] <= 5 for o in c.created), c.created


def test_no_ban_no_reclamp(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    monkeypatch.setattr(q, "EXPLORE_PROBE_CT", 5)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "KXFAM-26AUG-A", "usd_day": 100.0, "target": 1,
         "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert not row.get("series_probe"), "healthy family -> full-size, no probe clamp"
    assert any(o["count"] > 5 for o in c.created)


# ---- Gov-D10: family-cap denominator survives state loss + mark failure ----
def test_series_cap_static_only_when_equity_unknown(monkeypatch):
    monkeypatch.setattr(q, "SERIES_PCT", 0.25)
    monkeypatch.setattr(q, "SERIES_MAX_USD", 100.0)
    _saved = q._TOTAL_CAP_EFF[0]
    try:
        q._TOTAL_CAP_EFF[0] = None
        assert q._series_cap() == 100.0     # dynamic part gone -> static only
        q._TOTAL_CAP_EFF[0] = 200.0
        assert q._series_cap() == 50.0      # min(static, 25% of equity)
        # the pure-PCT config is the dangerous one: None -> 0 -> cap_desired gates nothing
        monkeypatch.setattr(q, "SERIES_MAX_USD", 0.0)
        q._TOTAL_CAP_EFF[0] = None
        assert q._series_cap() == 0.0
    finally:
        q._TOTAL_CAP_EFF[0] = _saved


def test_mark_fail_seeds_cost_basis_only_when_blind(self=None):
    src = inspect.getsource(q)
    i = src.index('plan["mark_failed"]')
    window = src[i:i + 1400]
    assert "_TOTAL_CAP_EFF[0] is None" in window, "seed must be gated on having NO last-good"
    assert "_TOTAL_CAP_EFF[0] = _equity" in window
    assert "total_cap_seeded_cost_basis" in window, "the degraded cycle must be visible"


# ---- Gov-D9: restart-only knobs warn instead of silently no-opping ----
def _knobfile(tmp_path, text):
    p = tmp_path / "live.env"
    p.write_text(text)
    return str(p)


def test_restart_only_knob_warns_and_does_not_apply(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KALSHI_ENV_FILE",
                       _knobfile(tmp_path, "KALSHI_SWEEP_VETO_TICKS=9\n"
                                           "KALSHI_HELD_MAX_USD=123\n"))
    monkeypatch.setattr(q, "SWEEP_VETO_TICKS", 3)
    monkeypatch.setattr(q, "HELD_MAX_USD", 40.0)
    q._refresh_safety_knobs()
    out = capsys.readouterr().out
    assert "RESTART-ONLY knob SWEEP_VETO_TICKS" in out and "NOT applied" in out
    assert q.SWEEP_VETO_TICKS == 3, "restart-only knob must never live-apply"
    assert q.HELD_MAX_USD == 123.0, "hot knobs in the same file still apply"


def test_restart_only_knob_matching_value_is_silent(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KALSHI_ENV_FILE",
                       _knobfile(tmp_path, "KALSHI_SERIES_MAX_USD=100\n"))
    monkeypatch.setattr(q, "SERIES_MAX_USD", 100.0)
    q._refresh_safety_knobs()
    assert "RESTART-ONLY" not in capsys.readouterr().out


def test_all_four_reviewed_knobs_are_covered():
    src = inspect.getsource(q._refresh_safety_knobs)
    for k in ("SWEEP_VETO_TICKS", "EXPLORE_PROBE_CT", "SERIES_MAX_USD",
              "FILLCOST_REFRESH_S"):
        assert k in src, k


def test_d5_reclamp_is_isolated_in_its_own_guard():
    # Blind review 2026-08-01 (lens A #5 / B #4): a fault in the telemetry-grade sibling
    # re-clamp must land in its OWN counter, never the governor fail-streak (3 strikes of
    # which flips the whole book reduce-only). Pin the isolation: the reclamp body sits in
    # a nested try whose except bumps its own named counter, and the ticker split is
    # str()-hardened. (A corrupt mixed-type mkt_out entry still breaks the governor at the
    # PRE-EXISTING sorted() call — that path predates this fix and is out of its scope.)
    src = inspect.getsource(q)
    i = src.index('_SILENT["series_probe_reclamp_fail"] += 1')
    window = src[i - 900:i]
    assert "except Exception:" in window
    assert "str(_t7).split" in window, "ticker split must be str()-hardened"

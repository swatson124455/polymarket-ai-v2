"""EOD 2026-07-31 blind-review fix batch (session 1.1 — review BEFORE deploy):
D1 fam_held gate matches _series_cap() activation (SERIES_PCT-only configs count held $),
D2 known-ban enforcement outside the governor try (a body fault must not fail bans open),
D3 realized last-good snapshot persisted (survives daemon restart during a feed outage),
D4 amnesty-guard re-application is LOUD (operator un-ban procedure failure visible),
RF1 fam_top_pct rides the SAME capital basis the 25% family cap uses."""
import json
import os

from test_live_hardening import q, MockClient, _run, _cfg


def _pos(t="T1", pos="0.50", realized="0.00"):
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": "0.25",
            "realized_pnl_dollars": realized}


def _fp1(monkeypatch, tickers=("T1",)):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": t, "usd_day": 100.0 - i, "target": 1, "end": "2099-01-01T00:00:00Z"}
        for i, t in enumerate(tickers)])


def _state(tmp_path):
    p = os.path.join(str(tmp_path), "quoter_state.json")
    return json.load(open(p)) if os.path.exists(p) else {}


class TestD1FamHeldGate:
    def test_series_pct_only_config_builds_fam_held(self, monkeypatch, tmp_path):
        """SERIES_MAX_USD=0 + SERIES_PCT>0 must STILL seed families with held dollars —
        the deployed gate keyed on SERIES_MAX_USD alone (latent live: env has 100)."""
        _cfg(monkeypatch)
        _fp1(monkeypatch)
        monkeypatch.setattr(q, "SERIES_MAX_USD", 0.0)
        monkeypatch.setattr(q, "SERIES_PCT", 0.25)
        seen = {}
        orig = q.cap_desired
        def spy(desired, usd_day, incumbents=None, fam_held=None):
            seen["fam_held"] = fam_held
            return orig(desired, usd_day, incumbents=incumbents, fam_held=fam_held)
        monkeypatch.setattr(q, "cap_desired", spy)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(pos="6.00")]),
             str(tmp_path))
        assert seen.get("fam_held") is not None, \
            "pure-SERIES_PCT config left fam_held=None — fills reopen family headroom"
        assert seen["fam_held"].get("T1") == 6.0

    def test_both_knobs_zero_still_skips_fam_held(self, monkeypatch, tmp_path):
        _cfg(monkeypatch)
        _fp1(monkeypatch)
        monkeypatch.setattr(q, "SERIES_MAX_USD", 0.0)
        monkeypatch.setattr(q, "SERIES_PCT", 0.0)
        seen = {}
        orig = q.cap_desired
        def spy(desired, usd_day, incumbents=None, fam_held=None):
            seen["fam_held"] = fam_held
            return orig(desired, usd_day, incumbents=incumbents, fam_held=fam_held)
        monkeypatch.setattr(q, "cap_desired", spy)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(pos="6.00")]),
             str(tmp_path))
        assert seen.get("fam_held") is None, "cap fully OFF must stay byte-identical"


class TestD2KnownBanEnforcementOutsideTry:
    def test_governor_body_fault_does_not_fail_bans_open(self, monkeypatch, tmp_path):
        """A corrupt state shape faults the governor body BEFORE the exit-only union.
        Permanent bans must be enforced anyway (previously: full-size quoting for up
        to 2 cycles until the fail-closed streak)."""
        _cfg(monkeypatch)
        _fp1(monkeypatch)
        monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
        import datetime as _dt
        _today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_out": ["T1"], "mkt_realized_day": _today,
                       "mkt_realized_base": "corrupt-not-a-dict"}, fh)
        c = MockClient(mode="live", positions=[_pos(realized="0.00")])
        row = _run(monkeypatch, c, str(tmp_path))
        assert c.created == [], \
            "governor body fault let a permanently-OUT market quote full size"
        assert _state(tmp_path).get("gov_fail_streak", 0) >= 1, \
            "the body fault itself must still count toward fail-closed"

    def test_today_day_latch_enforced_through_body_fault(self, monkeypatch, tmp_path):
        _cfg(monkeypatch)
        _fp1(monkeypatch)
        monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
        import datetime as _dt
        _today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_realized_day": _today, "mkt_loss_tripped": ["T1"],
                       "mkt_realized_base": "corrupt-not-a-dict"}, fh)
        c = MockClient(mode="live", positions=[_pos(realized="0.00")])
        _run(monkeypatch, c, str(tmp_path))
        assert c.created == [], "today's day-latch must survive a governor body fault"

    def test_stale_day_latch_not_enforced_after_roll(self, monkeypatch, tmp_path):
        """The pre-union must NOT change day-roll semantics: yesterday's latches reopen."""
        _cfg(monkeypatch)
        _fp1(monkeypatch)
        monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 5.0)
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_realized_day": "2020-01-01", "mkt_loss_tripped": ["T1"],
                       "mkt_realized_base": {"T1": 0.0}}, fh)
        c = MockClient(mode="live", positions=[_pos(realized="-50.00")])
        row = _run(monkeypatch, c, str(tmp_path))
        assert row.get("loss_exitonly") == 0, "a new UTC day starts clean"
        assert len(c.created) > 0


class TestD3LastGoodPersisted:
    def test_healthy_cycle_persists_snapshot(self, monkeypatch, tmp_path):
        _cfg(monkeypatch)
        _fp1(monkeypatch)
        monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-1.25")]),
             str(tmp_path))
        assert _state(tmp_path).get("realized_last_good") == {"T1": -1.25}

    def test_restart_during_outage_uses_persisted_snapshot(self, monkeypatch, tmp_path):
        """Daemon restart wipes the in-memory last-good; a feed outage right after must
        fall back to the PERSISTED snapshot (which still carries the burn), not the
        flat-dropping open-positions side channel."""
        _cfg(monkeypatch)
        _fp1(monkeypatch)
        monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
        monkeypatch.setattr(q, "MKT_OUT_LOSS_USD", 5.0)
        # cycle 1 healthy at 0.00 -> baseline {T1: 0.0} persisted
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        # simulate: burn read by the OLD process' last healthy cycle, then restart
        st = _state(tmp_path)
        st["realized_last_good"] = {"T1": -6.00}
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump(st, fh)
        q._REALIZED_LAST_GOOD.clear()                    # the restart
        # cycle 2: feed DOWN, position flat (side channel would drop T1 entirely)
        c2 = MockClient(mode="live", positions=[], get_realized_raises=True)
        row2 = _run(monkeypatch, c2, str(tmp_path))
        assert row2.get("realized_feed_fallback") == 1
        assert "T1" in (_state(tmp_path).get("mkt_out") or []), \
            "persisted last-good must carry the burn across a restart (delta -6 vs base 0)"
        assert c2.created == []


class TestD4AmnestyLoud:
    def test_backup_reapply_is_printed(self, monkeypatch, tmp_path, capsys):
        _cfg(monkeypatch)
        _fp1(monkeypatch)
        monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
        with open(os.path.join(str(tmp_path), "mkt_out_backup.json"), "w") as fh:
            json.dump(["T1"], fh)
        c = MockClient(mode="live", positions=[_pos(realized="0.00")])
        _run(monkeypatch, c, str(tmp_path))
        out = capsys.readouterr().out
        assert "amnesty guard RE-APPLIED" in out and "T1" in out
        assert c.created == [], "the re-applied ban must also be enforced"

    def test_no_warning_when_state_and_backup_agree(self, monkeypatch, tmp_path, capsys):
        _cfg(monkeypatch)
        _fp1(monkeypatch)
        monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
        with open(os.path.join(str(tmp_path), "mkt_out_backup.json"), "w") as fh:
            json.dump(["T1"], fh)
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_out": ["T1"]}, fh)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        assert "amnesty guard RE-APPLIED" not in capsys.readouterr().out


class TestRF1FamPctBasis:
    def test_denom_supplied_pct_uses_it(self):
        desired = {"KXFAM-26AUG01-A": [{"price_dollars": 0.50, "count": 20}],
                   "KXOTHER-26AUG01-B": [{"price_dollars": 0.50, "count": 20}]}
        s, usd, pct = q._fam_concentration(desired, denom=100.0)
        assert usd == 10.0 and pct == 10.0, "pct must be of the capital basis, not the book"

    def test_legacy_no_denom_unchanged(self):
        desired = {"KXFAM-26AUG01-A": [{"price_dollars": 0.50, "count": 20}],
                   "KXOTHER-26AUG01-B": [{"price_dollars": 0.50, "count": 20}]}
        s, usd, pct = q._fam_concentration(desired)
        assert pct == 50.0

    def test_zero_or_none_denom_falls_back_to_book_total(self):
        desired = {"KXFAM-26AUG01-A": [{"price_dollars": 0.50, "count": 20}]}
        assert q._fam_concentration(desired, denom=0)[2] == 100.0
        assert q._fam_concentration(desired, denom=None)[2] == 100.0

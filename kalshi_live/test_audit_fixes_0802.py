"""Double-blind audit fix batch (operator-named 2026-08-02): the $1-minimum now covers
the activate path; categorical detection covers alphanumeric outcome codes; budget-walk
refs age out; telemetry/fold faults and malformed restart-only knob values are loud."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402

M = {"ticker": "KXVOID-01", "target": 30, "end": "2099-01-01T00:00:00Z",
     "usd_day": 100.0, "df": 0.5, "ramp_min": 0.0, "life_min": 1440.0}
THIN_Y = [["0.50", "5"]]        # depth 5 << target 30 on both sides -> void/activate;
THIN_N = [["0.49", "5"]]        # activate cost ~25ct/side x ~$0.50 = ~$24.75 < $40 cap


def _quotes(monkeypatch, gate, credit):
    monkeypatch.setattr(q, "JOIN_SIZE", 0)   # size by target gap, not the join floor
    monkeypatch.setattr(q, "PRESENCE_GATE", gate)
    monkeypatch.setattr(q, "MIN_CREDIT_USD", 1.20)
    monkeypatch.setattr(q, "STANDDOWN", 0)
    monkeypatch.setattr(q, "NETEV_GATE", 0)
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 40.0)
    monkeypatch.setattr(q, "_expected_credit_usd",
                        lambda *a, **k: (credit, credit, 1.0))
    stats = {}
    out = q.desired_quotes(M, THIN_Y, THIN_N, q.utcnow(), stats=stats)
    return out, stats


class TestActivateCreditFloor:
    def test_subdollar_activate_never_opens(self, monkeypatch):
        quotes, stats = _quotes(monkeypatch, gate=1, credit=0.80)
        assert quotes == []
        assert stats.get("gate_activate_credit") == 1

    def test_earning_activate_still_opens(self, monkeypatch):
        quotes, stats = _quotes(monkeypatch, gate=1, credit=5.00)
        assert quotes, "a book that clears the floor must still activate"
        assert "gate_activate_credit" not in stats

    def test_gate_off_is_legacy(self, monkeypatch):
        quotes, stats = _quotes(monkeypatch, gate=0, credit=0.80)
        assert quotes and "gate_activate_credit" not in stats


class TestAlnumCategorical:
    def test_alphanumeric_outcome_codes_are_categorical(self):
        for t in ("KXTRUMPENDORSEMENTS-26AUG01-A5", "KXTRUMPTIME-26AUG01-H2",
                  "KXTOPMODEL-26AUG03-CLAU5", "KXSENATEADJOURN-27-26AUG08"):
            stats = {}
            assert q._strike_of(t, stats) is None
            assert stats.get("strike_categorical") == 1, t
            assert "strike_parse_failed" not in stats, t

    def test_mangled_numeric_stays_structural(self):
        stats = {}
        assert q._strike_of("KXBAD-26JUL23-4..0.5", stats) is None
        assert stats.get("strike_parse_failed") == 1


class TestWalkRefAgeBar:
    def test_refs_age_out_at_the_alloc_bar(self):
        src = inspect.getsource(q)
        i = src.index("_rts9 = _r9.get(\"ts\")")
        assert "ALLOC_PCAP_MAX_AGE_S" in src[i:i + 400], \
            "walk refs must use the same freshness bar as the alloc prospective feed"


class TestLoudness:
    def test_telemetry_fold_fault_is_counted(self):
        src = inspect.getsource(q)
        i = src.index('_SILENT["mkt_telemetry_fail"] += 1')
        assert "except Exception:" in src[i - 400:i]

    def test_malformed_restart_only_value_warns(self, monkeypatch, tmp_path, capsys):
        p = tmp_path / "live.env"
        p.write_text("KALSHI_SERIES_MAX_USD=banana\n")
        monkeypatch.setenv("KALSHI_ENV_FILE", str(p))
        q._refresh_safety_knobs()
        out = capsys.readouterr().out
        assert "malformed value" in out and "SERIES_MAX_USD" in out

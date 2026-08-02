"""Root fix (operator-named 2026-08-02): persisted ban/trip sets are operator-editable
JSON with no type enforcement at the load boundary. A single non-string entry made the
governor's sorted() raise a mixed-type TypeError every cycle — gov_fail_streak poisoned,
whole book reduce-only (fail-closed). _ban_set() now coerces LOUDLY at every read point
and never drops: a mangled entry stays banned under its string form."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_live_hardening import MockClient, _cfg, _run, q  # noqa: E402


class TestBanSetHelper:
    def test_coerces_and_counts_never_drops(self):
        before = q._SILENT["ban_entry_coerced"]
        out = q._ban_set([12345, "KXA-1", None, 6.5])
        assert out == {"12345", "KXA-1", "None", "6.5"}
        assert q._SILENT["ban_entry_coerced"] == before + 3

    def test_clean_input_counts_nothing(self):
        before = q._SILENT["ban_entry_coerced"]
        assert q._ban_set(["KXA-1"]) == {"KXA-1"}
        assert q._ban_set(None) == set()
        assert q._SILENT["ban_entry_coerced"] == before


class TestGovernorSurvivesCorruptState:
    def test_mixed_type_mkt_out_no_longer_poisons_the_streak(self, monkeypatch, tmp_path):
        _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
        monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
        monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
        monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
        monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
            {"ticker": "KXOK-26AUG-A", "usd_day": 100.0, "target": 1,
             "end": "2099-01-01T00:00:00Z"}])
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_out": [12345, "KXBAD-26AUG-B"]}, fh)   # the corrupt entry
        c = MockClient(mode="live", positions=[])
        row = _run(monkeypatch, c, str(tmp_path))
        st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
        assert not st.get("gov_fail_streak"), "corrupt entry must not fault the governor"
        assert not row.get("governor_fail_reduce_only")
        assert "12345" in (st.get("mkt_out") or []), "the mangled ban SURVIVES as a string"
        assert "KXBAD-26AUG-B" in (st.get("mkt_out") or [])
        assert c.created, "the healthy market still quotes"

    def test_corrupt_backup_file_also_coerced(self, monkeypatch, tmp_path):
        q.DATA_DIR = str(tmp_path)
        with open(os.path.join(str(tmp_path), "mkt_out_backup.json"), "w") as fh:
            json.dump([999, "KXBAD-26AUG-B"], fh)
        merged = q._mkt_out_backup_union({"KXNEW-26AUG-C"})
        assert merged == {"999", "KXBAD-26AUG-B", "KXNEW-26AUG-C"}
        # and the rewritten backup file is sortable (all strings)
        assert sorted(json.load(open(os.path.join(str(tmp_path), "mkt_out_backup.json"))))

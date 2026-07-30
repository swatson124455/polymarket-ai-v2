"""Ops hardening batch 2026-07-30 evening: family-concentration gauge + presence-table
mtime reload. (Peak plan-row key is telemetry-only, exercised via run_once live; disclosed.)"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


def _q(price, ct):
    return {"side": "yes", "price_dollars": price, "count": ct}


class TestFamConcentration:
    def test_top_family(self):
        d = {"A-1": [_q(0.5, 100)], "A-2": [_q(0.5, 100)], "B-1": [_q(0.5, 100)]}
        fam, usd, pct = q._fam_concentration(d)
        assert fam == "A" and usd == 100.0 and round(pct) == 67

    def test_empty(self):
        assert q._fam_concentration({}) == (None, 0.0, 0.0)

    def test_single_family_is_100pct(self):
        d = {"A-1": [_q(0.2, 50)]}
        fam, usd, pct = q._fam_concentration(d)
        assert fam == "A" and pct == 100.0


class TestPresenceReload:
    def test_reload_on_mtime_change(self, tmp_path, monkeypatch):
        p = tmp_path / "pt.json"
        p.write_text("{}")
        monkeypatch.setattr(q, "PRESENCE_GATE", 1)
        monkeypatch.setattr(q, "PRESENCE_TABLE_PATH", str(p))
        monkeypatch.setattr(q, "PRESENCE_TABLE", {"old": 1})
        monkeypatch.setattr(q, "_load_presence_table", lambda: {"new": 2})
        q._PRESENCE_MTIME[0] = 0.0
        q._presence_table_refresh()
        assert q.PRESENCE_TABLE == {"new": 2}

    def test_no_reload_when_unchanged(self, tmp_path, monkeypatch):
        p = tmp_path / "pt.json"
        p.write_text("{}")
        monkeypatch.setattr(q, "PRESENCE_GATE", 1)
        monkeypatch.setattr(q, "PRESENCE_TABLE_PATH", str(p))
        monkeypatch.setattr(q, "PRESENCE_TABLE", {"old": 1})
        calls = []
        monkeypatch.setattr(q, "_load_presence_table", lambda: calls.append(1) or {"new": 2})
        q._PRESENCE_MTIME[0] = os.path.getmtime(str(p)) + 10
        q._presence_table_refresh()
        assert not calls and q.PRESENCE_TABLE == {"old": 1}

    def test_broken_load_keeps_last_good(self, tmp_path, monkeypatch):
        p = tmp_path / "pt.json"
        p.write_text("{}")
        monkeypatch.setattr(q, "PRESENCE_GATE", 1)
        monkeypatch.setattr(q, "PRESENCE_TABLE_PATH", str(p))
        monkeypatch.setattr(q, "PRESENCE_TABLE", {"good": 1})
        monkeypatch.setattr(q, "_load_presence_table", lambda: {})
        q._PRESENCE_MTIME[0] = 0.0
        q._presence_table_refresh()
        assert q.PRESENCE_TABLE == {"good": 1}

    def test_gate_off_noop(self, tmp_path, monkeypatch):
        p = tmp_path / "pt.json"
        p.write_text("{}")
        monkeypatch.setattr(q, "PRESENCE_GATE", 0)
        monkeypatch.setattr(q, "PRESENCE_TABLE_PATH", str(p))
        monkeypatch.setattr(q, "PRESENCE_TABLE", {"x": 1})
        monkeypatch.setattr(q, "_load_presence_table", lambda: {"new": 2})
        q._PRESENCE_MTIME[0] = 0.0
        q._presence_table_refresh()
        assert q.PRESENCE_TABLE == {"x": 1}      # gate off => never reloads

    def test_missing_file_noop(self, monkeypatch):
        monkeypatch.setattr(q, "PRESENCE_GATE", 1)
        monkeypatch.setattr(q, "PRESENCE_TABLE_PATH", "/nonexistent/pt.json")
        monkeypatch.setattr(q, "PRESENCE_TABLE", {"x": 1})
        q._presence_table_refresh()
        assert q.PRESENCE_TABLE == {"x": 1}

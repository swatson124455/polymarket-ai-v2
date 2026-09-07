"""Pins for ALLOCATOR FILE MODE (KALSHI_FOOTPRINT_FILE, v1 spec §4; operator 'build ...
allocator now' 2026-09-07). File drives selection through the intact safety funnel;
fail-CLOSED on missing/corrupt/stale (never falls open to proxy selection, ACDG C7);
priority replaces the alloc ordering (B7); flag unset = byte-identical legacy.
"""
import json
import time

from test_live_hardening import q


def _progs():
    import datetime
    e = (q.utcnow() + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = (q.utcnow() - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    def p(t, pool=1000000):
        return {"market_ticker": t, "incentive_type": "liquidity",
                "target_size_fp": "1000.00", "discount_factor_bps": 5000,
                "period_reward": pool, "start_date": s, "end_date": e}
    return [p("KXFPA-EV-T1"), p("KXFPA-EV-T2"), p("KXFPB-EV-T1"), p("KXZZZ-EV-T1")]


def _cfg(monkeypatch, tmp_path, file_doc=None, gen_age_h=1.0):
    monkeypatch.setattr(q, "SERIES_ALLOW", {"KXFPA", "KXFPB"})
    monkeypatch.setattr(q, "SERIES_DENY", [])
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0)      # skip network prefilter
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 0)
    monkeypatch.setattr(q, "SCORE_RANK", 0)
    monkeypatch.setattr(q, "PIVOT_SELECT", 0)
    monkeypatch.setattr(q, "ALLOC_KEY", 0)
    monkeypatch.setattr(q, "UPTIME_RANK", 0)
    monkeypatch.setattr(q, "FOOTPRINT_TOP", 1)          # would truncate legacy selection
    monkeypatch.setattr(q, "PER_SERIES_CAP", 3)
    monkeypatch.setattr(q, "MACRO_PROBE_TICKERS", set())
    fp = ""
    if file_doc is not None:
        import datetime
        if "generated_utc" not in file_doc:
            file_doc["generated_utc"] = (
                q.utcnow() - datetime.timedelta(hours=gen_age_h)).isoformat()
        f = tmp_path / "fp.json"
        f.write_text(json.dumps(file_doc))
        fp = str(f)
    monkeypatch.setattr(q, "FOOTPRINT_FILE", fp)
    monkeypatch.setattr(q, "FOOTPRINT_STALE_H", 26.0)
    monkeypatch.setattr(q, "_FOOTPRINT_CACHE", {"ts": 0.0, "rows": None})


def _doc(rows):
    return {"version": 1, "rows": rows}


def test_ff1_flag_unset_legacy_selection(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, file_doc=None)
    picked = q.select_footprint(_progs(), q.utcnow())
    assert len(picked) == 1                              # FOOTPRINT_TOP=1 honored (legacy)
    assert "file_max_ct" not in picked[0]


def test_ff2_file_drives_selection_priority_order_top_ignored(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, file_doc=_doc([
        {"ticker": "KXFPB-EV-T1", "max_ct": 40, "priority": 2},
        {"ticker": "KXFPA-EV-T2", "max_ct": 25, "priority": 1},
    ]))
    picked = q.select_footprint(_progs(), q.utcnow())
    assert [r["ticker"] for r in picked] == ["KXFPA-EV-T2", "KXFPB-EV-T1"]  # priority order
    assert picked[0]["file_max_ct"] == 25 and picked[1]["file_max_ct"] == 40
    assert len(picked) == 2                              # FOOTPRINT_TOP=1 ignored (spec §4)
    assert q._FOOTPRINT_CAPS == {"KXFPB-EV-T1": 40, "KXFPA-EV-T2": 25}


def test_ff3_stale_file_fail_closed(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, gen_age_h=27.0, file_doc=_doc(
        [{"ticker": "KXFPA-EV-T1", "max_ct": 25, "priority": 1}]))
    picked = q.select_footprint(_progs(), q.utcnow())
    assert picked == []
    assert q.FP_DROPS.get("footprint_file_failclosed") == 1


def test_ff4_corrupt_file_fail_closed(monkeypatch, tmp_path):
    f = tmp_path / "fp.json"
    f.write_text("{not json")
    _cfg(monkeypatch, tmp_path, file_doc=None)
    monkeypatch.setattr(q, "FOOTPRINT_FILE", str(f))
    monkeypatch.setattr(q, "_FOOTPRINT_CACHE", {"ts": 0.0, "rows": None})
    assert q.select_footprint(_progs(), q.utcnow()) == []
    assert q.FP_DROPS.get("footprint_file_failclosed") == 1


def test_ff5_missing_file_fail_closed(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, file_doc=None)
    monkeypatch.setattr(q, "FOOTPRINT_FILE", str(tmp_path / "nope.json"))
    monkeypatch.setattr(q, "_FOOTPRINT_CACHE", {"ts": 0.0, "rows": None})
    assert q.select_footprint(_progs(), q.utcnow()) == []
    assert q.FP_DROPS.get("footprint_file_failclosed") == 1


def test_ff6_file_cannot_bypass_safety_funnel(monkeypatch, tmp_path):
    """The file can only NARROW: a ticker the allowlist refuses stays refused."""
    _cfg(monkeypatch, tmp_path, file_doc=_doc([
        {"ticker": "KXZZZ-EV-T1", "max_ct": 25, "priority": 1},     # not allowlisted
        {"ticker": "KXFPA-EV-T1", "max_ct": 25, "priority": 2},
    ]))
    picked = q.select_footprint(_progs(), q.utcnow())
    assert [r["ticker"] for r in picked] == ["KXFPA-EV-T1"]
    assert q.FP_DROPS.get("file_ticker_ineligible") == 1


def test_ff7_priority_map_feeds_alloc_ordering(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, file_doc=_doc([
        {"ticker": "KXFPA-EV-T2", "max_ct": 25, "priority": 1},
        {"ticker": "KXFPB-EV-T1", "max_ct": 40, "priority": 2},
    ]))
    q.select_footprint(_progs(), q.utcnow())             # loads the file into _FOOTPRINT_PRIO
    prio = q._alloc_priority([], q.utcnow(), {"ignored": 1.0})
    assert prio["KXFPA-EV-T2"] > prio["KXFPB-EV-T1"]     # B7: file order wins at the margin

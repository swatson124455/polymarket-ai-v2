"""Latency-path tests: wave-parallel gamma paging + concurrent collector legs.

The 2026-07-11 latency change must lose NOTHING: identical index contents,
identical fail-to-empty contracts, coverage >= the old sequential paging (which
dropped everything after the first failed page), and the two collector legs
(PinnOdds odds, PM index) genuinely overlapping in time. Overlap is asserted
via recorded intervals, never wall-clock bounds (CI-safe).
"""
import importlib.util
import json
import threading
import time
from pathlib import Path

from esports_v2.data.pm_market_index import build_pm_index

_STANDALONE = (Path(__file__).resolve().parents[2]
               / "deploy" / "vps" / "collect_pinnodds_standalone.py")


def _load_standalone():
    spec = importlib.util.spec_from_file_location("eb_standalone_lat", _STANDALONE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mw(i):
    """A minimal parseable match-winner gamma market dict."""
    return {
        "question": f"T{i}a vs T{i}b",
        "conditionId": f"0xc{i}",
        "outcomes": json.dumps([f"T{i}a", f"T{i}b"]),
        "clobTokenIds": json.dumps([f"tok{i}y", f"tok{i}n"]),
        "outcomePrices": json.dumps(["0.5", "0.5"]),
        "gameStartTime": "2026-08-01 12:00:00+00",
    }


class _PageServer:
    """Thread-safe scripted page store; counts calls per page."""

    def __init__(self, pages, fail_once=(), fail_always=()):
        self.pages = pages                    # page_index -> list[dict]
        self.fail_once = set(fail_once)
        self.fail_always = set(fail_always)
        self.calls = []
        self._lock = threading.Lock()

    def fetch(self, offset, limit):
        page = offset // 100
        with self._lock:
            self.calls.append(page)
            if page in self.fail_always:
                return None
            if page in self.fail_once:
                self.fail_once.discard(page)
                return None
        return self.pages.get(page, [])


def _full_page(start):
    return [_mw(start * 100 + j) for j in range(100)]


def test_wave_identical_to_sequential():
    pages = {0: _full_page(0), 1: _full_page(1), 2: [_mw(999)]}
    a = build_pm_index(fetch_page=_PageServer(pages).fetch)
    b = build_pm_index(fetch_page=_PageServer(pages).fetch, workers=1)
    assert {r.condition_id for r in a} == {r.condition_id for r in b}
    assert len(a) == 201


def test_wave_stops_scheduling_past_end():
    pages = {0: _full_page(0), 1: _full_page(1), 2: [_mw(999)]}
    srv = _PageServer(pages)
    build_pm_index(fetch_page=srv.fetch, workers=8, max_pages=30)
    # page 0 + one wave (pages 1..8); never a second wave
    assert max(srv.calls) <= 8
    assert len(srv.calls) <= 9


def test_page0_failure_returns_empty():
    srv = _PageServer({0: _full_page(0)}, fail_always=(0,))
    assert build_pm_index(fetch_page=srv.fetch) == []


def test_transient_mid_hole_recovered_by_retry():
    pages = {0: _full_page(0), 1: _full_page(1), 2: _full_page(2), 3: [_mw(999)]}
    srv = _PageServer(pages, fail_once=(2,))
    refs = build_pm_index(fetch_page=srv.fetch, workers=8)
    assert len(refs) == 301                      # nothing lost
    assert srv.calls.count(2) == 2               # exactly one retry


def test_persistent_mid_hole_keeps_later_pages():
    # old sequential behavior lost pages 3.. forever; waves must keep them
    pages = {0: _full_page(0), 1: _full_page(1), 2: _full_page(2), 3: [_mw(999)]}
    srv = _PageServer(pages, fail_always=(2,))
    refs = build_pm_index(fetch_page=srv.fetch, workers=8)
    got = {r.condition_id for r in refs}
    assert "0xc999" in got and "0xc101" in got   # later + earlier pages kept
    assert "0xc200" not in got and "0xc250" not in got   # hole page absent


# ── concurrent legs (canonical collect) ──────────────────────────────────────


def test_canonical_collect_legs_overlap(tmp_path, monkeypatch):
    import esports_v2.scripts.collect_pinnodds as cp

    spans = {}

    def slow_refs():
        spans["refs"] = [time.monotonic()]
        time.sleep(0.25)
        spans["refs"].append(time.monotonic())
        return []

    class _FakeLoader:
        def fetch_rows(self, event_types=None):
            spans["rows"] = [time.monotonic()]
            time.sleep(0.25)
            spans["rows"].append(time.monotonic())
            return [{"match_key": "k", "home": "A", "away": "B",
                     "starts": "2026-08-01T12:00:00Z", "league_name": "L",
                     "odds_a": 1.5, "odds_b": 2.6, "event_type": "prematch"}]

    monkeypatch.setattr(cp, "_safe_build_pm_refs", slow_refs)
    monkeypatch.setattr(cp.PinnOddsLoader, "from_env",
                        staticmethod(lambda: _FakeLoader()))
    out = cp.collect(tmp_path / "s.jsonl")
    assert out["written"] == 1
    a, b = spans["refs"], spans["rows"]
    assert a[0] < b[1] and b[0] < a[1], f"legs did not overlap: {spans}"


# ── standalone parity ────────────────────────────────────────────────────────


def test_standalone_main_legs_overlap(tmp_path, monkeypatch):
    sa = _load_standalone()
    envf = tmp_path / ".env"
    envf.write_text("PINNACLE_ODDS_API_KEY=fake\n")
    sa.ENV, sa.SNAP = str(envf), str(tmp_path / "snaps.jsonl")
    sa.ALIASES = str(tmp_path / "nope.json")

    spans = {}

    def slow_index():
        spans["pm"] = [time.monotonic()]
        time.sleep(0.25)
        spans["pm"].append(time.monotonic())
        return []

    def slow_fetch(k):
        spans["odds"] = [time.monotonic()]
        time.sleep(0.25)
        spans["odds"].append(time.monotonic())
        return {"events": [{"home": "A", "away": "B",
                            "starts": "2026-08-01T12:00:00Z",
                            "league_name": "L",
                            "periods": {"num_0": {"money_line":
                                                  {"home": 1.5, "away": 2.6}}}}]}

    monkeypatch.setattr(sa, "pm_index", slow_index)
    monkeypatch.setattr(sa, "fetch", slow_fetch)
    sa.main()
    rows = [json.loads(l) for l in open(sa.SNAP)]
    assert len(rows) == 1 and rows[0]["odds_a"] == 1.5
    a, b = spans["pm"], spans["odds"]
    assert a[0] < b[1] and b[0] < a[1], f"legs did not overlap: {spans}"


def test_standalone_wave_paging_matches_flat(monkeypatch):
    sa = _load_standalone()
    pages = {0: _full_page(0), 1: [_mw(777)]}
    monkeypatch.setattr(sa, "gamma_page",
                        lambda off: pages.get(off // 100, []) or
                        (pages.get(off // 100) if off // 100 in pages else []))
    refs = sa.pm_index()
    assert len(refs) == 101
    assert any(r[0] == "0xc777" for r in refs)


# ── GAP C parity: canonical parse_book == standalone best_level ─────────────


def test_standalone_touch_quote_parity_with_canonical():
    from esports_v2.data.pm_market_index import parse_book
    sa = _load_standalone()
    books = [
        {"bids": [{"price": "0.60", "size": "50"}, {"price": "0.64", "size": "10"}],
         "asks": [{"price": "0.70", "size": "5"}, {"price": "0.66", "size": "80"}]},
        {"bids": [], "asks": [{"price": "0.92", "size": "7"}]},
        {"bids": [{"price": "junk", "size": "5"}, {"price": "1.5", "size": "1"}],
         "asks": []},
        {"bids": [], "asks": []},
    ]
    for b in books:
        can = parse_book(b)
        sbb, sbs = sa.best_level(b.get("bids"), "bid")
        sba, sas = sa.best_level(b.get("asks"), "ask")
        if can is None:
            assert sbb is None and sba is None
        else:
            assert (can.best_bid, can.bid_size) == (sbb, sbs)
            assert (can.best_ask, can.ask_size) == (sba, sas)


def test_standalone_touch_quotes_dedup_and_isolation(monkeypatch):
    sa = _load_standalone()
    calls = []
    def fake_book(tid):
        calls.append(tid)
        if tid == "boom":
            raise RuntimeError("x")
        return {"bids": [{"price": "0.40", "size": "3"}], "asks": []}
    monkeypatch.setattr(sa, "clob_book", fake_book)
    out = sa.touch_quotes(["a", "a", "boom", "b", ""], workers=2)
    assert sorted(calls) == ["a", "b", "boom"]
    assert set(out) == {"a", "b"}
    assert out["a"] == (0.40, None, 3.0, None)


def test_standalone_pm_index_flattens_event_pages(monkeypatch):
    sa = _load_standalone()
    ev = {"title": "E", "markets": [
        _mw(1),
        dict(_mw(2), closed=True),      # resolved nested market -> skipped
        dict(_mw(3), active=False),     # inactive nested market -> skipped
    ]}
    monkeypatch.setattr(sa, "gamma_pages", lambda: {0: [ev]})
    refs = sa.pm_index()
    assert len(refs) == 1
    assert refs[0][0] == _mw(1)["conditionId"]

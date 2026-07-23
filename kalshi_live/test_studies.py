"""PIN TESTS for the three measurement defects in the sandbox STUDY scripts.

These are pins, not coverage: every test in here was written BEFORE the fix and
RUN AGAINST THE PRE-FIX CODE to confirm it fails. A test that passed both before
and after would not be a pin and is marked as such in the docstring.

  D1  kalshi_concentration_study.py — `if not yl or not nl: continue` silently
      DROPPED one-sided books, making canon §M2's "86.1% two-sided" conditional
      on both books being non-empty and §M1's capture figures conditional too.
  D2  kalshi_series_scan.py — `limit=1000` across an 8-page loop is a hard 8,000
      program ceiling on the census. The live quoter uses 10000 and is correct.
      ⚠ MEASURED 2026-07-23: page size changes NOTHING at the current venue size
      — limit=1000 and limit=10000 both return 2,298 programs / 160 series, and
      KXRT shows 70 programs under BOTH. This is a LATENT-ceiling fix. It is NOT
      the cause of any "KXRT = 0 programs" reading; do not claim that it was.
      The truncation pin below therefore runs against a SYNTHETIC oversized
      venue, which is the only way to exercise the ceiling today.
  D3  kalshi_series_scan.py — markets within a series were ranked by per-program
      reward, which is CONSTANT when a series shares one pool, so "top N" was
      arbitrary API order. That is what produced §M5's "100% two-sided / $7.42"
      off an n=4 head-of-list slice.

Run: python -m pytest test_studies.py -q   (from kalshi_live/)

NO NETWORK. Every venue call is monkeypatched. Nothing here touches the frozen
datasets (concentration_samples.jsonl, kalshi_transactions_2026-07-23.csv).
"""
import importlib.util
import json
import os
import sys
import urllib.parse

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(n):
    s = importlib.util.spec_from_file_location(n, os.path.join(HERE, f"{n}.py"))
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


CS = _load("kalshi_concentration_study")
SS = _load("kalshi_series_scan")


# ----------------------------------------------------------------- fake venue

def prog(ticker, reward=1000000, target=1000, df_bps=5000,
         start="2026-07-23T00:00:00Z", end="2026-07-24T00:00:00Z"):
    """One /incentive_programs row. reward is in 1e-4 dollars (1000000 = $100)."""
    return {"market_ticker": ticker, "period_reward": reward,
            "target_size_fp": target, "discount_factor_bps": df_bps,
            "start_date": start, "end_date": end}


TWO_SIDED = {"yes_dollars": [[0.50, 1200.0]], "no_dollars": [[0.49, 1200.0]]}
NO_SIDE_EMPTY = {"yes_dollars": [[0.50, 1200.0]], "no_dollars": []}
YES_SIDE_EMPTY = {"yes_dollars": [], "no_dollars": [[0.49, 1200.0]]}


def fake_get(programs, books, seen=None, default_book=None):
    """Paginating stand-in for the module-level `get()`. HONOURS `limit` and
    `cursor` exactly like the venue does — which is what makes the D2 page-size
    truncation observable instead of hypothetical."""
    def _get(path):
        if seen is not None:
            seen.append(path)
        if path.startswith("/incentive_programs"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            limit = int(qs.get("limit", ["100"])[0])
            cur = int(qs.get("cursor", ["0"])[0])
            chunk = programs[cur:cur + limit]
            nxt = cur + limit
            return {"incentive_programs": chunk,
                    "next_cursor": str(nxt) if nxt < len(programs) else ""}
        if path.startswith("/markets/"):
            t = path.split("/")[2]
            return {"orderbook_fp": books.get(
                t, default_book if default_book is not None else TWO_SIDED)}
        if path.startswith("/series/"):
            return {"series": {"ticker": path.split("/")[2], "fee_type": "quadratic"}}
        raise AssertionError("unexpected path " + path)
    return _get


# =========================================================== D1 — SELECTION BIAS

def test_d1_sampler_records_one_sided_books(monkeypatch):
    """PIN. Pre-fix the sampler `continue`d past a contract whose no-book was
    empty, so it never entered the dataset at all."""
    progs = [prog("KXAAAGASD-26JUL23-A"), prog("KXAAAGASD-26JUL23-B")]
    books = {"KXAAAGASD-26JUL23-A": TWO_SIDED,
             "KXAAAGASD-26JUL23-B": NO_SIDE_EMPTY}
    monkeypatch.setattr(CS, "get", fake_get(progs, books))
    monkeypatch.setattr(CS, "KEEP_ONESIDED", True)
    rows = CS.sample_once()
    assert len(rows) == 2, "one-sided contract was dropped from the sample"
    b = [r for r in rows if r["t"].endswith("-B")][0]
    assert b["nl"] == [] and b["yl"], "one-sided book not recorded verbatim"


def test_d1_legacy_filter_is_still_reachable(monkeypatch):
    """PIN (capability). The frozen dataset was produced WITH the filter; the
    old behaviour has to stay reproducible or the frozen numbers become
    un-regenerable. Pre-fix there was no flag at all."""
    progs = [prog("KXAAAGASD-26JUL23-A"), prog("KXAAAGASD-26JUL23-B")]
    books = {"KXAAAGASD-26JUL23-A": TWO_SIDED,
             "KXAAAGASD-26JUL23-B": NO_SIDE_EMPTY}
    monkeypatch.setattr(CS, "get", fake_get(progs, books))
    monkeypatch.setattr(CS, "KEEP_ONESIDED", False)
    rows = CS.sample_once()
    assert len(rows) == 1 and rows[0]["t"].endswith("-A")


def test_d1_score_market_treats_empty_side_as_r3_failure():
    """PIN. Pre-fix `score_market` did `max(p for p, _ in nl)` on an empty list
    and raised ValueError — the only reason it never blew up in production is
    that the sampler had already dropped these rows."""
    row = {"t": "X", "target": 1000.0, "df": 0.5, "pool": 100.0,
           "yl": [(0.50, 1200.0)], "nl": []}
    pay, ct, ys, ns, binding = CS.score_market(row, 15.0)
    assert pay == 0.0 and ct == 0.0 and ys == 0.0 and ns == 0.0
    assert binding == "r3_empty"


def test_d1_score_snapshot_survives_one_sided_rows():
    """PIN. score_snapshot's capital-used accounting also did max() over the
    raw book and raised on an empty side."""
    rows = [{"t": "A", "target": 1000.0, "df": 0.5, "pool": 100.0,
             "yl": [(0.50, 1200.0)], "nl": [(0.49, 1200.0)],
             "start": "2026-07-23T00:00:00Z", "end": "2026-07-24T00:00:00Z"},
            {"t": "B", "target": 1000.0, "df": 0.5, "pool": 100.0,
             "yl": [(0.50, 1200.0)], "nl": [],
             "start": "2026-07-23T00:00:00Z", "end": "2026-07-24T00:00:00Z"}]
    out = CS.score_snapshot(rows, 2, mode="asis")
    assert out is not None
    assert out["two_sided"] == 1 and out["scored"] == 2


def test_d1_two_sided_rate_is_unconditional_when_data_is_unfiltered():
    """PIN. The whole point of D1: with one-sided books recorded, the R3
    market-level two-sided rate is 50% here, not the 100% a pre-filtered
    dataset would report. Pre-fix `two_sided_stats` did not exist."""
    two = {"t": "A", "target": 1000.0, "df": 0.5, "pool": 100.0,
           "yl": [(0.50, 1200.0)], "nl": [(0.49, 1200.0)]}
    one = {"t": "B", "target": 1000.0, "df": 0.5, "pool": 100.0,
           "yl": [(0.50, 1200.0)], "nl": []}
    snaps = [{"ts": "t0", "rows": [two, one]}]
    ok, n, empty, thin = CS.two_sided_stats(snaps)
    assert (ok, n, empty) == (1, 2, 1)
    assert abs(100.0 * ok / n - 50.0) < 1e-9
    # and the pre-filtered view of the same venue reports 100%
    ok2, n2, _, _ = CS.two_sided_stats([{"ts": "t0", "rows": [two]}])
    assert (ok2, n2) == (1, 1)


def test_d1_below_target_depth_is_not_two_sided():
    """PIN. R3 is a TARGET SIZE test, not a non-empty test: 5 contracts on the
    no side is a one-sided book for payout purposes."""
    thin = {"t": "C", "target": 1000.0, "df": 0.5, "pool": 100.0,
            "yl": [(0.50, 1200.0)], "nl": [(0.49, 5.0)]}
    ok, n, empty, thin_ct = CS.two_sided_stats([{"ts": "t", "rows": [thin]}])
    assert (ok, n, empty, thin_ct) == (0, 1, 0, 1)


def test_d1_report_states_dataset_provenance(tmp_path):
    """PIN. §M6b says the committed rates must never be quoted as
    unconditional. The report now has to SAY which kind of dataset it loaded.
    Pre-fix `dataset_provenance` did not exist and the report said nothing."""
    two = {"t": "A", "target": 1000.0, "df": 0.5, "pool": 100.0,
           "yl": [(0.50, 1200.0)], "nl": [(0.49, 1200.0)]}
    one = {"t": "B", "target": 1000.0, "df": 0.5, "pool": 100.0,
           "yl": [(0.50, 1200.0)], "nl": []}
    # legacy dataset: no flag, no one-sided rows -> provenance UNKNOWN, conditional
    assert CS.dataset_provenance([{"ts": "t", "rows": [two]}]).startswith("UNKNOWN")
    # explicitly filtered
    assert CS.dataset_provenance(
        [{"ts": "t", "keep_onesided": False, "rows": [two]}]).startswith("PRE-FILTERED")
    # unfiltered, one-sided rows present
    assert CS.dataset_provenance(
        [{"ts": "t", "keep_onesided": True, "rows": [two, one]}]).startswith("UNFILTERED")


def test_d1_sampler_stamps_provenance_on_every_snapshot(tmp_path, monkeypatch):
    """PIN. Provenance has to be recorded at SAMPLE time; inferring it later
    from 'no one-sided rows present' is exactly the ambiguity that made the
    frozen dataset un-diagnosable."""
    out = tmp_path / "s.jsonl"
    progs = [prog("KXAAAGASD-26JUL23-A")]
    monkeypatch.setattr(CS, "get", fake_get(progs, {"KXAAAGASD-26JUL23-A": TWO_SIDED}))
    monkeypatch.setattr(CS, "OUT", str(out))
    monkeypatch.setattr(CS, "KEEP_ONESIDED", True)
    monkeypatch.setattr(CS.time, "sleep", lambda *_: None)
    CS.main(0.001)
    recs = [json.loads(x) for x in open(out) if x.strip()]
    assert recs and all(r.get("keep_onesided") is True for r in recs)


def test_d1_frozen_dataset_is_untouched_and_reads_as_prefiltered():
    """MIXED: the md5 half is not a pin (passes before and after, it guards the
    evidence file); the provenance half IS a pin. The committed dataset must keep
    its md5 AND must be reported as conditional, never as a venue-wide rate."""
    p = os.path.join(HERE, "concentration_samples.jsonl")
    if not os.path.exists(p):
        pytest.skip("frozen dataset not present")
    import hashlib
    assert hashlib.md5(open(p, "rb").read()).hexdigest() == \
        "e920bf99850279099897a79e8ad78dec"
    snaps = [json.loads(x) for x in open(p) if x.strip()]
    assert CS.dataset_provenance(snaps).startswith("UNKNOWN")


# ================================================= D2 — TRUNCATING PAGE SIZE

def _big_venue(n_filler=8000):
    """A venue whose program census EXCEEDS the pre-fix reach of 8 pages x 1000.
    KXRT sits past that boundary — the exact shape of the real defect."""
    progs = [prog(f"KXFILLER{i}-26JUL23-T{i}", reward=10000) for i in range(n_filler)]
    progs += [prog(f"KXRT-26JUL23-T{i}", reward=5000000) for i in range(6)]
    return progs


def test_d2_program_fetch_requests_10000_per_page(monkeypatch, tmp_path):
    """PIN. The live quoter uses limit=10000 (maker_kalshi_quoter.py:773); the
    scan used 1000."""
    seen = []
    progs = _big_venue(10)
    monkeypatch.setattr(SS, "get", fake_get(progs, {}, seen=seen))
    monkeypatch.setattr(SS, "OUT", str(tmp_path / "o.json"))
    SS.main(1, 2)
    ip = [p for p in seen if p.startswith("/incentive_programs")]
    assert ip and all("limit=10000" in p for p in ip), ip


def test_d2_census_is_not_truncated(monkeypatch, tmp_path):
    """PIN — the EFFECT, not just the constant. Pre-fix this venue yields 8000
    of 8006 programs and KXRT vanishes entirely (reported as 0 programs)."""
    progs = _big_venue()
    monkeypatch.setattr(SS, "get", fake_get(progs, {}))
    monkeypatch.setattr(SS, "OUT", str(tmp_path / "o.json"))
    rows = SS.main(1, 2)
    assert rows, "nothing scanned"
    assert rows[0]["series"] == "KXRT"
    assert rows[0]["programs"] == 6


# ================================================== D3 — DEGENERATE RANKING

def _tied_series(order):
    """One series, 8 contracts, IDENTICAL per-program reward -> the pre-fix sort
    key is constant and `ps[:4]` is whatever order the API happened to return."""
    return [prog(f"KXRT-26JUL23-T{i}") for i in order]


def _scanned_tickers(monkeypatch, tmp_path, progs, **kw):
    seen = []
    monkeypatch.setattr(SS, "get", fake_get(progs, {}, seen=seen))
    monkeypatch.setattr(SS, "OUT", str(tmp_path / "o.json"))
    SS.main(1, 4, **kw)
    return {p.split("/")[2] for p in seen if p.startswith("/markets/")}


def test_d3_sample_is_independent_of_api_order(monkeypatch, tmp_path):
    """PIN. Pre-fix: forward order sampled T0..T3, reversed order sampled
    T7..T4 — two disjoint 'top 4' sets from the same venue."""
    fwd = _scanned_tickers(monkeypatch, tmp_path, _tied_series(range(8)))
    rev = _scanned_tickers(monkeypatch, tmp_path, _tied_series(list(reversed(range(8)))))
    assert fwd == rev, f"selection is API-order dependent: {sorted(fwd)} vs {sorted(rev)}"


def test_d3_census_mode_takes_every_contract(monkeypatch, tmp_path):
    """PIN. A full census is the honest answer to a degenerate ranking; pre-fix
    there was no mode at all."""
    seen = []
    progs = _tied_series(range(8))
    monkeypatch.setattr(SS, "get", fake_get(progs, {}, seen=seen))
    monkeypatch.setattr(SS, "OUT", str(tmp_path / "o.json"))
    rows = SS.main(1, 4, sample_mode="census")
    assert rows[0]["sampled"] == 8 and rows[0]["programs"] == 8
    assert rows[0]["coverage_pct"] == 100.0


def test_d3_row_surfaces_sample_provenance(monkeypatch, tmp_path):
    """PIN. §M5 printed a 100% two-sided rate off n=4 at one instant without
    saying so. The row now carries mode, seed, coverage, denominator and a
    `thin` flag."""
    progs = _tied_series(range(8))
    monkeypatch.setattr(SS, "get", fake_get(progs, {}))
    monkeypatch.setattr(SS, "OUT", str(tmp_path / "o.json"))
    rows = SS.main(1, 4, sample_mode="random", seed=7)
    r = rows[0]
    for k in ("sample_mode", "seed", "coverage_pct", "two_sided_ct", "thin", "instants"):
        assert k in r, f"missing {k}"
    assert r["sample_mode"] == "random" and r["seed"] == 7
    assert r["sampled"] == 4 and r["coverage_pct"] == 50.0
    assert r["thin"] is True and r["instants"] == 1


def test_d3_random_sample_is_reproducible_under_seed(monkeypatch, tmp_path):
    """PIN. Random must not mean unrepeatable — a study you cannot re-run is
    not evidence."""
    a = _scanned_tickers(monkeypatch, tmp_path, _tied_series(range(8)),
                         sample_mode="random", seed=3)
    b = _scanned_tickers(monkeypatch, tmp_path, _tied_series(list(reversed(range(8)))),
                         sample_mode="random", seed=3)
    assert a == b and len(a) == 4


def test_d3_head_mode_still_available(monkeypatch, tmp_path):
    """PIN (capability). The legacy slice has to stay reachable to reproduce
    the committed §M5 numbers."""
    got = _scanned_tickers(monkeypatch, tmp_path, _tied_series(range(8)),
                           sample_mode="head")
    assert got == {f"KXRT-26JUL23-T{i}" for i in range(4)}


def test_d3_ranking_tie_break_is_deterministic():
    """PIN. Direct unit test of the ordering key: equal reward-per-day must fall
    back to the ticker, never to list position."""
    ps = _tied_series([5, 2, 9, 1])
    assert [p["market_ticker"] for p in SS.rank_programs(ps)] == \
        sorted(p["market_ticker"] for p in ps)


def test_d3_unequal_rewards_still_rank_by_reward_per_day():
    """PIN (guards the fix): the tie-break must not displace the primary key."""
    ps = [prog("KXRT-A", reward=10000), prog("KXRT-B", reward=9000000)]
    assert [p["market_ticker"] for p in SS.rank_programs(ps)] == ["KXRT-B", "KXRT-A"]


# ============================================ MAKER-FEE ANNOTATION (canon §M10)

def _need_fee_table():
    if not os.path.exists(SS.FEE_TYPES_PATH):
        pytest.skip("series_fee_types.json not present (regenerated from /series)")


def test_fee_status_from_committed_table():
    """PIN. Canon §M10: of the series carrying active LIP programs exactly one
    charges maker fees — KXAAAGASM. Pre-fix the scan had no fee_type wiring at
    all and every non-allowlist series was flagged 'fee UNKNOWN'."""
    _need_fee_table()
    assert SS.fee_status("KXAAAGASM", fetch=False)[0] == "CHARGES"
    for s in ("KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXPM", "KXRT",
              "KXINTC", "KXFUNDRAISING", "KXCLAUDE"):
        assert SS.fee_status(s, fetch=False)[0] == "FREE", s


def test_fee_status_unknown_series_is_not_guessed(monkeypatch):
    monkeypatch.setattr(SS, "_FEE_CACHE", {})
    assert SS.fee_status("KXNOSUCHSERIES", fetch=False)[0] == "UNKNOWN"


def test_fee_status_falls_back_to_series_endpoint(monkeypatch):
    """PIN. If the table is missing a series the scan regenerates that entry
    from GET /series/{ticker}.fee_type rather than reporting UNKNOWN."""
    calls = []

    def _get(path):
        calls.append(path)
        return {"series": {"ticker": "KXNEW", "fee_type": "quadratic_with_maker_fees"}}

    monkeypatch.setattr(SS, "_FEE_CACHE", {})
    monkeypatch.setattr(SS, "get", _get)
    assert SS.fee_status("KXNEW", fetch=True)[0] == "CHARGES"
    assert calls == ["/series/KXNEW"]
    # cached: no second call
    assert SS.fee_status("KXNEW", fetch=True)[0] == "CHARGES"
    assert calls == ["/series/KXNEW"]


def test_scan_row_carries_maker_fee_annotation(monkeypatch, tmp_path):
    """PIN. Every candidate row must be annotated FREE/CHARGES/UNKNOWN."""
    _need_fee_table()
    progs = [prog(f"KXAAAGASM-26JUL-T{i}") for i in range(3)]
    monkeypatch.setattr(SS, "get", fake_get(progs, {}))
    monkeypatch.setattr(SS, "OUT", str(tmp_path / "o.json"))
    rows = SS.main(1, 3, sample_mode="census")
    assert rows[0]["maker_fee"] == "CHARGES"
    assert rows[0]["fee_type"] == "quadratic_with_maker_fees"

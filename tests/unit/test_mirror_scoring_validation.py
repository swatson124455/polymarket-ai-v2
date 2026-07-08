"""validate_ranking contract tests (review fixes F1/F3/F4, 2026-07-06).

The kill criterion runs against a faked DB returning canned
mirror_rejected_signals rows; no migration or network needed.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from bots.mirror_scoring.config import ScoringConfig
from bots.mirror_scoring.q_score import TraderScore
from bots.mirror_scoring.validation import (
    placebo_validate_ranking, validate_ranking,
)

CUTOFF = datetime(2026, 6, 1)


# ── fakes ────────────────────────────────────────────────────────────────────

class _Row:
    def __init__(self, d):
        self._mapping = d


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Session:
    """Returns canned rows for the rejected-signals SELECT; swallows SET LOCAL."""

    def __init__(self, rows):
        self._rows = rows
        self.seen_sql: list[str] = []
        self.seen_params: list[dict] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.seen_sql.append(sql)
        if params is not None:
            self.seen_params.append(params)
        if "SET LOCAL" in sql:
            return _Result([])
        return _Result([_Row(dict(r)) for r in self._rows])


class _DB:
    def __init__(self, rows):
        self.session = _Session(rows)

    def get_session(self):
        db = self

        class _Ctx:
            async def __aenter__(self):
                return db.session

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def _score(trader, admitted, condition_ids=()):
    return TraderScore(
        trader=trader, n_entries=20, n_events=12, n_adverse=3,
        avg_price=0.5, edge_mean=0.05, edge_se=0.01, edge_lb_t=0.02,
        edge_lb_jeffreys=0.01, p_holdout=0.01, train_edge=0.05,
        test_edge=0.05, admitted=admitted,
        condition_ids=list(condition_ids),
    )


def _sig(trader, market, side="YES", res="YES", price=0.40):
    return {
        "trader_address": trader, "market_id": market, "token_id": None,
        "side": side, "price": price, "event_time": datetime(2026, 6, 10),
        "resolution": res, "yes_token_id": None, "no_token_id": None,
    }


ADMITTED = "0xaaa0000000000000000000000000000000000001"
OTHER = "0xbbb0000000000000000000000000000000000002"


# ── F1: scored (trader, market) pairs are excluded from validation ───────────

@pytest.mark.asyncio
async def test_f1_overlap_pairs_excluded_and_counted():
    """A market that fed the trader's admission score must not also validate
    them — the row is dropped and surfaced in n_excluded_overlap."""
    rows = [
        _sig(ADMITTED, "0xoverlap"),      # fed the admission score -> excluded
        _sig(ADMITTED, "0xfresh1"),       # genuinely out-of-sample
        _sig(ADMITTED, "0xfresh2"),
        _sig(OTHER, "0xfresh1", res="NO"),
        _sig(OTHER, "0xfresh2", res="NO"),
    ]
    scores = [
        _score(ADMITTED, admitted=True, condition_ids=["0xoverlap"]),
        _score(OTHER, admitted=False),
    ]
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.n_excluded_overlap == 1
    assert report.n_admitted_signals == 2   # overlap row NOT counted
    assert report.n_other_signals == 2


@pytest.mark.asyncio
async def test_f1_no_overlap_nothing_excluded():
    rows = [
        _sig(ADMITTED, "0xfresh1"),
        _sig(OTHER, "0xfresh2", res="NO"),
    ]
    scores = [
        _score(ADMITTED, admitted=True, condition_ids=["0xelsewhere"]),
        _score(OTHER, admitted=False),
    ]
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.n_excluded_overlap == 0
    assert report.n_admitted_signals == 1
    assert report.n_other_signals == 1


@pytest.mark.asyncio
async def test_f1_edges_computed_from_surviving_rows_only():
    """Each admitted trader's overlap WIN is excluded; the trader-weighted
    admitted edge must reflect only the kept (losing) rows, not the winners."""
    rows, scores = [], []
    for i in range(3):                       # 3 admitted traders
        a = _addr(i)
        rows.append(_sig(a, "0xoverlap", res="YES", price=0.10))       # +0.90 excluded
        rows.append(_sig(a, f"{a}f", res="NO", side="YES", price=0.40))  # -0.40 kept
        scores.append(_score(a, admitted=True, condition_ids=["0xoverlap"]))
    for i in range(3, 6):                     # 3 other traders
        a = _addr(i)
        rows.append(_sig(a, f"{a}f", res="NO", side="YES", price=0.40))  # -0.40
        scores.append(_score(a, admitted=False))
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.n_excluded_overlap == 3                     # one per admitted trader
    assert report.admitted_edge == pytest.approx(-0.40)       # winners excluded, not +
    assert report.other_edge == pytest.approx(-0.40)


# ── F6/2026-07-08: trader-block permutation kill criterion ───────────────────

def _addr(i):
    return f"0x{i:040x}"


def _trader_rows(addr, n_win, n_loss, price=0.50):
    """Signals for one trader; per-trader mean edge = 0.5*(win-loss)/(win+loss)
    at price 0.50 (YES/res=YES = +0.5, YES/res=NO = -0.5). Distinct markets."""
    rows = []
    for i in range(n_win):
        rows.append(_sig(addr, f"{addr}w{i}", res="YES", side="YES", price=price))
    for i in range(n_loss):
        rows.append(_sig(addr, f"{addr}l{i}", res="NO", side="YES", price=price))
    return rows


def _universe(n_adm, adm_wl, n_oth, oth_wl):
    """(rows, scores) for n_adm admitted traders (win,loss)=adm_wl and n_oth
    others=oth_wl. Enough traders for the permutation test to resolve."""
    rows, scores, k = [], [], 0
    for _ in range(n_adm):
        a = _addr(k); k += 1
        rows += _trader_rows(a, *adm_wl)
        scores.append(_score(a, admitted=True))
    for _ in range(n_oth):
        a = _addr(k); k += 1
        rows += _trader_rows(a, *oth_wl)
        scores.append(_score(a, admitted=False))
    return rows, scores


@pytest.mark.asyncio
async def test_perm_genuine_separation_passes():
    """15 admitted traders (mean +0.20) vs 15 others (mean 0): the trader-block
    permutation test clears ALPHA and PASSes."""
    rows, scores = _universe(15, (7, 3), 15, (5, 5))
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.spread > 0
    assert report.p_value < 0.05
    assert report.passed is True


@pytest.mark.asyncio
async def test_perm_no_separation_fails():
    """Both groups mean 0 -> high p -> FAIL (was the anti-conservative case)."""
    rows, scores = _universe(15, (5, 5), 15, (5, 5))
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.passed is False
    assert report.p_value > 0.05


@pytest.mark.asyncio
async def test_perm_too_few_traders_is_not_a_pass():
    """Two traders can't be significant — must NOT pass (the old bootstrap did)."""
    rows = _trader_rows(_addr(1), 20, 0) + _trader_rows(_addr(2), 0, 20)
    scores = [_score(_addr(1), admitted=True), _score(_addr(2), admitted=False)]
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.passed is False
    assert "too few traders" in report.detail


# ── A6: placebo calibration (test-of-the-test) ───────────────────────────────

@pytest.mark.asyncio
async def test_a6_placebo_calibrated_on_null_data():
    """No group truly differs -> shuffled rankings pass ~ALPHA, calibration OK."""
    rows, scores = _universe(15, (5, 5), 15, (5, 5))
    pr = await placebo_validate_ranking(
        _DB(rows), scores, CUTOFF, ScoringConfig(), n_placebo=20
    )
    assert pr.n_runs == 20
    assert pr.pass_rate <= 0.10          # calibrated band
    assert "calibration OK" in pr.detail


@pytest.mark.asyncio
async def test_a6_placebo_deterministic_and_does_not_mutate_scores():
    rows, scores = _universe(8, (6, 2), 8, (4, 4))
    admitted_before = [t.admitted for t in scores]
    pr1 = await placebo_validate_ranking(
        _DB(rows), scores, CUTOFF, ScoringConfig(), n_placebo=8
    )
    pr2 = await placebo_validate_ranking(
        _DB(rows), scores, CUTOFF, ScoringConfig(), n_placebo=8
    )
    assert pr1.p_values == pr2.p_values                      # seeded
    assert [t.admitted for t in scores] == admitted_before   # never mutated


@pytest.mark.asyncio
async def test_a6_placebo_flags_suspect_when_verdict_always_passes(monkeypatch):
    """The suspect flag must fire if the verdict machinery hallucinates. Force
    every shuffle to 'pass' by stubbing _verdict -> pass_rate 1.0 -> SUSPECT."""
    import bots.mirror_scoring.validation as V

    def _always_pass(row_dicts, admitted, scored_pairs, cfg, signal_stream):
        return V.ValidationReport(
            passed=True, n_admitted_signals=1, n_other_signals=1,
            admitted_edge=1.0, other_edge=0.0, spread=1.0, p_value=0.0,
            detail="stub", signal_stream=signal_stream,
        )

    monkeypatch.setattr(V, "_verdict", _always_pass)
    rows, scores = _universe(4, (6, 2), 4, (4, 4))
    pr = await placebo_validate_ranking(
        _DB(rows), scores, CUTOFF, ScoringConfig(), n_placebo=10
    )
    assert pr.n_passed == 10
    assert "CALIBRATION SUSPECT" in pr.detail


# ── F4: address matching is case-insensitive end to end ──────────────────────

@pytest.mark.asyncio
async def test_f4_mixed_case_scores_still_classify_rows():
    """Scores carry checksummed (mixed-case) addresses; DB rows are lowercase.
    Pre-fix this classified every row as 'other' (or matched zero rows)."""
    admitted_checksummed = "0xAaA0000000000000000000000000000000000001"
    rows = [
        _sig(ADMITTED, "0xfresh1"),               # lowercase in DB
        _sig(OTHER, "0xfresh2", res="NO"),
    ]
    scores = [
        _score(admitted_checksummed, admitted=True),
        _score(OTHER, admitted=False),
    ]
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.n_admitted_signals == 1          # matched despite case diff
    assert report.n_other_signals == 1


@pytest.mark.asyncio
async def test_f4_sql_params_and_query_are_lowercased():
    admitted_checksummed = "0xAaA0000000000000000000000000000000000001"
    db = _DB([_sig(ADMITTED, "0xm1"), _sig(OTHER, "0xm2", res="NO")])
    scores = [
        _score(admitted_checksummed, admitted=True),
        _score(OTHER, admitted=False),
    ]
    await validate_ranking(db, scores, CUTOFF, ScoringConfig())
    select_sql = next(s for s in db.session.seen_sql if "SET LOCAL" not in s)
    assert "LOWER(r.trader_address) = ANY(:traders)" in select_sql
    sent = next(p for p in db.session.seen_params if "traders" in p)
    assert all(t == t.lower() for t in sent["traders"])


@pytest.mark.asyncio
async def test_f4_overlap_exclusion_matches_across_case():
    """F1's exclusion must also hold when score/row casings differ."""
    admitted_checksummed = "0xAaA0000000000000000000000000000000000001"
    rows = [
        _sig(ADMITTED, "0xoverlap"),               # lowercase row
        _sig(ADMITTED, "0xfresh"),
        _sig(OTHER, "0xother", res="NO"),
    ]
    scores = [
        _score(admitted_checksummed, admitted=True, condition_ids=["0xoverlap"]),
        _score(OTHER, admitted=False),
    ]
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.n_excluded_overlap == 1
    assert report.n_admitted_signals == 1


# ── F3: explicit stream selection (legacy vs v3 collector rows) ──────────────

@pytest.mark.asyncio
async def test_f3_default_stream_is_legacy_and_filters_v3_rows():
    db = _DB([_sig(ADMITTED, "0xm1"), _sig(OTHER, "0xm2", res="NO")])
    scores = [_score(ADMITTED, admitted=True), _score(OTHER, admitted=False)]
    report = await validate_ranking(db, scores, CUTOFF, ScoringConfig())
    assert report.signal_stream == "legacy"
    select_sql = next(s for s in db.session.seen_sql if "SET LOCAL" not in s)
    assert "COALESCE(r.metadata->>'source', '') <> 'mirror_v3'" in select_sql


@pytest.mark.asyncio
async def test_f3_v3_stream_selects_only_v3_rows():
    db = _DB([_sig(ADMITTED, "0xm1"), _sig(OTHER, "0xm2", res="NO")])
    scores = [_score(ADMITTED, admitted=True), _score(OTHER, admitted=False)]
    report = await validate_ranking(
        db, scores, CUTOFF, ScoringConfig(), signal_stream="v3"
    )
    assert report.signal_stream == "v3"
    select_sql = next(s for s in db.session.seen_sql if "SET LOCAL" not in s)
    assert "r.metadata->>'source' = 'mirror_v3'" in select_sql


@pytest.mark.asyncio
async def test_f3_all_stream_has_no_source_clause():
    db = _DB([_sig(ADMITTED, "0xm1"), _sig(OTHER, "0xm2", res="NO")])
    scores = [_score(ADMITTED, admitted=True), _score(OTHER, admitted=False)]
    report = await validate_ranking(
        db, scores, CUTOFF, ScoringConfig(), signal_stream="all"
    )
    assert report.signal_stream == "all"
    select_sql = next(s for s in db.session.seen_sql if "SET LOCAL" not in s)
    assert "mirror_v3" not in select_sql


@pytest.mark.asyncio
async def test_f3_unknown_stream_raises_before_db():
    with pytest.raises(ValueError, match="signal_stream"):
        await validate_ranking(
            _DB([]), [_score(ADMITTED, admitted=True), _score(OTHER, admitted=False)],
            CUTOFF, ScoringConfig(), signal_stream="bogus",
        )


@pytest.mark.asyncio
async def test_f1_empty_condition_ids_backward_compatible():
    """Scores without recorded condition_ids exclude nothing (legacy shape)."""
    rows = [
        _sig(ADMITTED, "0xany"),
        _sig(OTHER, "0xany2", res="NO"),
    ]
    scores = [
        _score(ADMITTED, admitted=True),
        _score(OTHER, admitted=False),
    ]
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.n_excluded_overlap == 0
    assert report.n_admitted_signals == 1

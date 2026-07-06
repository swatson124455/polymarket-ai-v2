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
    """The excluded overlap row (a win) must not inflate the admitted edge."""
    rows = [
        _sig(ADMITTED, "0xoverlap", res="YES", price=0.10),  # +0.90, excluded
        _sig(ADMITTED, "0xfresh", res="NO", price=0.40),     # -0.40, kept
        _sig(OTHER, "0xfresh2", res="NO", price=0.40),       # -0.40
    ]
    scores = [
        _score(ADMITTED, admitted=True, condition_ids=["0xoverlap"]),
        _score(OTHER, admitted=False),
    ]
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.admitted_edge == pytest.approx(-0.40)
    assert report.n_excluded_overlap == 1


# ── F6: kill criterion passes on genuine separation (two-sample bootstrap) ───

@pytest.mark.asyncio
async def test_f6_genuine_separation_passes():
    """Across 30 markets, admitted signals win at cheap prices while others
    lose — the two-sample cluster bootstrap must clear ALPHA and PASS."""
    rows = []
    for i in range(30):
        rows.append(_sig(ADMITTED, f"0xm{i}", res="YES", price=0.40))   # +0.60
        rows.append(_sig(OTHER, f"0xm{i}", res="YES", price=0.40, side="NO"))  # -0.40
    scores = [_score(ADMITTED, admitted=True), _score(OTHER, admitted=False)]
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.spread == pytest.approx(1.0)
    assert report.p_value < 0.05
    assert report.passed is True


@pytest.mark.asyncio
async def test_f6_no_separation_fails():
    """Identical outcomes for both groups -> spread 0 -> FAIL, p=1."""
    rows = []
    for i in range(10):
        rows.append(_sig(ADMITTED, f"0xm{i}", res="YES", price=0.50))
        rows.append(_sig(OTHER, f"0xn{i}", res="YES", price=0.50))
    scores = [_score(ADMITTED, admitted=True), _score(OTHER, admitted=False)]
    report = await validate_ranking(_DB(rows), scores, CUTOFF, ScoringConfig())
    assert report.passed is False
    assert report.p_value == 1.0


# ── A6: placebo calibration (test-of-the-test) ───────────────────────────────

@pytest.mark.asyncio
async def test_a6_placebo_zero_passes_on_symmetric_data():
    """All traders identical outcomes -> every shuffled ranking has spread 0
    -> zero placebo passes, calibration OK."""
    rows = []
    traders = [ADMITTED, OTHER,
               "0xccc0000000000000000000000000000000000003",
               "0xddd0000000000000000000000000000000000004"]
    for i, t in enumerate(traders):
        for m in range(5):
            rows.append(_sig(t, f"0x{i}m{m}", res="YES", price=0.50))
    scores = [_score(t, admitted=(t == ADMITTED)) for t in traders]
    pr = await placebo_validate_ranking(
        _DB(rows), scores, CUTOFF, ScoringConfig(), n_placebo=10
    )
    assert pr.n_runs == 10
    assert pr.n_passed == 0
    assert "calibration OK" in pr.detail


@pytest.mark.asyncio
async def test_a6_placebo_deterministic_and_does_not_mutate_scores():
    rows = [_sig(ADMITTED, "0xm1"), _sig(OTHER, "0xm2", res="NO")]
    scores = [_score(ADMITTED, admitted=True), _score(OTHER, admitted=False)]
    pr1 = await placebo_validate_ranking(
        _DB(rows), scores, CUTOFF, ScoringConfig(), n_placebo=8
    )
    pr2 = await placebo_validate_ranking(
        _DB(rows), scores, CUTOFF, ScoringConfig(), n_placebo=8
    )
    assert pr1.p_values == pr2.p_values          # seeded, reproducible
    assert scores[0].admitted is True            # never mutated
    assert scores[1].admitted is False


@pytest.mark.asyncio
async def test_a6_placebo_flags_suspect_calibration():
    """If shuffled rankings 'pass' too often, the report must scream. Force it
    with 2 traders where every shuffle that picks the winner passes (~50%)."""
    rows = []
    for i in range(30):
        rows.append(_sig(ADMITTED, f"0xm{i}", res="YES", price=0.40))
        rows.append(_sig(OTHER, f"0xm{i}", res="YES", price=0.40, side="NO"))
    scores = [_score(ADMITTED, admitted=True), _score(OTHER, admitted=False)]
    pr = await placebo_validate_ranking(
        _DB(rows), scores, CUTOFF, ScoringConfig(), n_placebo=12
    )
    # With only 2 traders, ~half the shuffles reproduce the true (separating)
    # labels — a structurally tiny universe SHOULD trip the suspect flag.
    assert pr.n_passed > 0
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

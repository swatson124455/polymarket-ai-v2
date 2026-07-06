"""validate_ranking contract tests (review fixes F1/F3/F4, 2026-07-06).

The kill criterion runs against a faked DB returning canned
mirror_rejected_signals rows; no migration or network needed.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from bots.mirror_scoring.config import ScoringConfig
from bots.mirror_scoring.q_score import TraderScore
from bots.mirror_scoring.validation import validate_ranking

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

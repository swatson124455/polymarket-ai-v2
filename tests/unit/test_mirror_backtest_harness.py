"""Acceptance-gate harness tests (bots/mirror_backtest) — no DB, fixtures only."""
from __future__ import annotations

import numpy as np
import pytest

from bots.mirror_backtest.fill_models import (
    COARSE, PRECISE, coarse_fill, precise_fill,
)
from bots.mirror_backtest.gate import GateConfig, evaluate_gate
from bots.mirror_backtest.replay import Decision, replay_signals


# ── fill models ──────────────────────────────────────────────────────────


class TestPreciseFill:
    def test_walks_ladder_vwap(self):
        book = [{"price": 0.50, "size": 100}, {"price": 0.55, "size": 100}]
        r = precise_fill(book, "BUY", 150)
        # 100 @ .50 + 50 @ .55 => vwap ~ .5167
        assert r is not None and r.model == PRECISE
        assert r.price == pytest.approx((100 * 0.50 + 50 * 0.55) / 150, abs=1e-6)
        assert r.fill_fraction == pytest.approx(1.0)

    def test_whale_consumes_first(self):
        book = [{"price": 0.50, "size": 100}, {"price": 0.55, "size": 100}]
        r = precise_fill(book, "BUY", 100, whale_size_shares=100)
        # whale ate the .50 level; we pay .55
        assert r.price == pytest.approx(0.55, abs=1e-6)

    def test_empty_book_none(self):
        assert precise_fill([], "BUY", 10) is None


class TestCoarseFill:
    ROW = {"mid_price": 0.50, "best_ask": 0.51, "best_bid": 0.49,
           "ask_depth_1pct": 200.0, "ask_depth_5pct": 1000.0,
           "bid_depth_1pct": 200.0, "bid_depth_5pct": 1000.0}

    def test_fits_1pct_bucket_charges_worst_of_bucket(self):
        r = coarse_fill(self.ROW, "BUY", 100.0)
        # worst of (best_ask=.51, mid*1.01=.505) => .51 (pessimistic)
        assert r is not None and r.model == COARSE
        assert r.price == pytest.approx(0.51, abs=1e-6)
        assert r.fill_fraction == pytest.approx(1.0)

    def test_fits_5pct_bucket(self):
        r = coarse_fill(self.ROW, "BUY", 500.0)
        assert r.price == pytest.approx(0.50 * 1.05, abs=1e-6)

    def test_partial_beyond_5pct(self):
        r = coarse_fill(self.ROW, "BUY", 2000.0)
        assert r.fill_fraction == pytest.approx(0.5)
        assert "partial_fill" in r.caveats

    def test_sell_mirrors_bid_side(self):
        r = coarse_fill(self.ROW, "SELL", 100.0)
        # worst of (best_bid=.49, mid*0.99=.495) => .49
        assert r.price == pytest.approx(0.49, abs=1e-6)

    def test_unusable_row_none(self):
        assert coarse_fill({"mid_price": 0.0}, "BUY", 100.0) is None
        assert coarse_fill({}, "BUY", 100.0) is None

    def test_pessimism_never_beats_mid_on_buy(self):
        r = coarse_fill(self.ROW, "BUY", 100.0)
        assert r.price >= self.ROW["mid_price"]


# ── replay ───────────────────────────────────────────────────────────────


def _sig(market, res, side="YES", price=0.5, ladder=None, ob=None, trader="0xT"):
    d = {"market_id": market, "trader_address": trader, "side": side,
         "price": price, "resolution": res, "event_time": None}
    if ladder:
        d["book_snapshot"] = ladder
    if ob:
        d["ob_row"] = ob
    return d


_LADDER = [{"price": 0.50, "size": 1000}]
_OB = TestCoarseFill.ROW


class TestReplay:
    def test_outcome_fields_hidden_from_rule(self):
        seen = {}

        def rule(sig):
            seen.update(sig)
            return Decision(size_usd=10.0)

        replay_signals([_sig("m1", "YES", ladder=_LADDER)], rule)
        assert "resolution" not in seen

    def test_precise_preferred_over_coarse(self):
        rep = replay_signals(
            [_sig("m1", "YES", ladder=_LADDER, ob=_OB)],
            lambda s: Decision(size_usd=10.0),
        )
        assert rep.n_precise == 1 and rep.n_coarse == 0

    def test_uncovered_counted_not_dropped(self):
        rep = replay_signals([_sig("m1", "YES")], lambda s: Decision(size_usd=10.0))
        assert rep.n_uncovered == 1 and rep.n_signals == 0

    def test_edge_math_win_and_loss(self):
        rep = replay_signals(
            [_sig("m1", "YES", ladder=_LADDER), _sig("m2", "NO", ladder=_LADDER)],
            lambda s: Decision(size_usd=10.0), fee_roundtrip=0.02,
        )
        edges = {r.market_id: r.edge_net for r in rep.replayed}
        assert edges["m1"] == pytest.approx(1.0 - 0.5 - 0.02)   # won
        assert edges["m2"] == pytest.approx(0.0 - 0.5 - 0.02)   # lost

    def test_rule_decline_counted(self):
        rep = replay_signals([_sig("m1", "YES", ladder=_LADDER)], lambda s: None)
        assert rep.n_skipped == 1


# ── gate ─────────────────────────────────────────────────────────────────


def _report_with(edges_by_market, model_ladder=True):
    sigs = []
    for i, (mkt, won) in enumerate(edges_by_market):
        sigs.append(_sig(mkt, "YES" if won else "NO",
                         ladder=_LADDER if model_ladder else None,
                         ob=None if model_ladder else _OB))
    return replay_signals(sigs, lambda s: Decision(size_usd=10.0))


class TestGate:
    def test_insufficient_precise_markets_blocks(self):
        rep = _report_with([(f"m{i}", True) for i in range(5)])
        res = evaluate_gate(rep, GateConfig(MIN_PRECISE_MARKETS=30))
        assert not res.admitted
        assert any("insufficient_precise_markets" in r for r in res.reasons)

    def test_strong_edge_admits(self):
        # 40 markets, 90% winners at fill .50 => clearly positive edge
        rng = np.random.default_rng(0)
        rep = _report_with([(f"m{i}", bool(rng.random() < 0.9)) for i in range(40)])
        res = evaluate_gate(rep, GateConfig(MIN_PRECISE_MARKETS=30))
        assert res.admitted, res.reasons

    def test_zero_edge_fails(self):
        # 50/50 outcomes at .50 fill minus fees => negative expectation
        rep = _report_with([(f"m{i}", i % 2 == 0) for i in range(40)])
        res = evaluate_gate(rep, GateConfig(MIN_PRECISE_MARKETS=30))
        assert not res.admitted

    def test_coarse_alone_cannot_admit(self):
        # all-coarse fills, even perfect record: no precise markets => blocked
        rep = _report_with([(f"m{i}", True) for i in range(40)], model_ladder=False)
        assert rep.n_precise == 0 and rep.n_coarse == 40
        res = evaluate_gate(rep, GateConfig(MIN_PRECISE_MARKETS=30))
        assert not res.admitted
        assert any("insufficient_precise_markets" in r for r in res.reasons)

    def test_result_labeled_unverified(self):
        rep = _report_with([(f"m{i}", True) for i in range(40)])
        assert evaluate_gate(rep).label == "UNVERIFIED"

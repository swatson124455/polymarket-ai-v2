"""S230 Bug 14: block ALL neg-risk markets (not just multi-outcome).

Bug history:
  - order_gateway._can_exit had `if neg_risk AND outcome_count > 2: return False`.
    This filter conflated "multi-outcome" with "neg-risk" — but those are
    orthogonal concepts.
  - S230 smoke test (2026-05-26): 3 live BUYs rejected, 2 were binary
    neg-risk markets (Knicks NBA Finals, Spurs NBA Western per live Gamma
    `neg_risk=True, outcome_count=2`). DB had stale `neg_risk=false` for
    both. The `outcome_count > 2` filter never caught them because NegRisk
    submarkets are usually binary.

Fix shape:
  - order_gateway._can_exit: drop the `outcome_count > 2` qualifier. Block
    any market with `neg_risk=True` regardless of outcome count.

Operator clarification (recorded in code comment + memory feedback file
feedback_negrisk_routing_distinction.md):
  - Neg-risk is the CONTRACT-ROUTING mechanism (NegRiskExchange V2 vs
    Exchange V2), NOT a market structure.
  - The bot ALREADY supports multi-outcome markets — constraint is one
    position per market, enforced via _entered_market_sides + opposing-
    side guards.
  - The block exists because NegRiskExchange V2 routing exercises
    untested codepaths (exit confirmation, resolution backfill,
    redemption). Until full-cycle validated, block the routing.

Cross-bot blast radius:
  - order_gateway.py: shared infra. All bots that call OrderGateway
    .place_order now reject neg-risk markets at BUY time. WeatherBot
    weather markets aren't neg-risk; EsportsBot esports binary matches
    are typically not neg-risk individual either (tournament-level
    grouping is the typical neg-risk shape). Minimal impact expected.
  - MirrorBot: directly blocked at place_order time.
  - Performance: O(1) dict lookup per place_order call. No new HTTP.

These tests are structural — verify the filter is tight and the
operator clarification is documented in the source comment.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from base_engine.execution.order_gateway import OrderGateway


class TestBug14CanExitFilterTightened:
    """_can_exit blocks any neg_risk market, not just multi-outcome."""

    def _make_gateway_with_index(self, market_index):
        """Build a minimal OrderGateway with just the _market_index populated."""
        gw = OrderGateway.__new__(OrderGateway)  # bypass __init__
        gw._market_index = market_index
        return gw

    def test_blocks_binary_neg_risk(self):
        """Binary NegRisk submarket (outcome_count=2) must now be blocked."""
        gw = self._make_gateway_with_index({
            "0xKNICKS": {"neg_risk": True, "outcome_count": 2},
        })
        assert gw._can_exit("0xKNICKS") is False, (
            "Binary neg-risk market must be blocked. Bug 14 closes the "
            "gap where outcome_count=2 NegRisk submarkets passed through "
            "the old `outcome_count > 2` filter."
        )

    def test_blocks_multi_outcome_neg_risk(self):
        """Multi-outcome NegRisk still blocked (regression check on old behavior)."""
        gw = self._make_gateway_with_index({
            "0xELECTION": {"neg_risk": True, "outcome_count": 5},
        })
        assert gw._can_exit("0xELECTION") is False

    def test_allows_non_neg_risk_binary(self):
        """Non-neg-risk binary markets pass through unchanged."""
        gw = self._make_gateway_with_index({
            "0xMETS": {"neg_risk": False, "outcome_count": 2},
        })
        assert gw._can_exit("0xMETS") is True, (
            "Non-neg-risk binary markets must NOT be blocked. The block "
            "is about contract routing (NegRiskExchange V2), not market "
            "structure. Binary non-neg-risk markets route through "
            "Exchange V2 which is fully validated."
        )

    def test_allows_multi_outcome_non_neg_risk(self):
        """Non-neg-risk multi-outcome (rare but possible) passes through."""
        gw = self._make_gateway_with_index({
            "0xMULTI": {"neg_risk": False, "outcome_count": 4},
        })
        assert gw._can_exit("0xMULTI") is True, (
            "Multi-outcome alone does NOT trigger the block. Per operator "
            "clarification (S230), bot supports multi-outcome via one-"
            "position-per-market guards. Block is about neg-risk contract "
            "routing, not market structure."
        )

    def test_allows_when_market_not_in_index(self):
        """Unknown market = allow (conservative, doesn't block cold-start)."""
        gw = self._make_gateway_with_index({})  # empty index
        assert gw._can_exit("0xUNKNOWN") is True

    def test_handles_camelcase_neg_risk_alias(self):
        """`negRisk` is the gamma-API camelCase variant; must also trigger block."""
        gw = self._make_gateway_with_index({
            "0xCAMEL": {"negRisk": True, "outcome_count": 2},
        })
        assert gw._can_exit("0xCAMEL") is False


class TestBug14SourceCommentDocumentsOperatorClarification:
    """Source comment for _can_exit must record operator clarification so future
    sessions don't re-conflate 'multi-outcome' with 'neg-risk'."""

    def test_comment_mentions_operator_clarification(self):
        src = inspect.getsource(OrderGateway._can_exit)
        assert "OPERATOR CLARIFICATION" in src or "Operator clarification" in src, (
            "Source comment must include operator clarification (S230) that "
            "neg-risk is NOT about multi-outcome — it's contract routing. "
            "Without this anchor, future sessions will re-conflate the terms."
        )

    def test_comment_mentions_one_position_per_market(self):
        src = inspect.getsource(OrderGateway._can_exit)
        # The clarification should reference the existing per-market guard
        # so future sessions know multi-outcome IS supported.
        assert "one position per market" in src.lower(), (
            "Comment must mention that the bot already supports multi-outcome "
            "via one-position-per-market enforcement. Without it, future "
            "sessions might assume the block is about multi-outcome support."
        )

    def test_comment_references_negrisk_exchange_v2_routing(self):
        src = inspect.getsource(OrderGateway._can_exit)
        assert "NegRiskExchange V2" in src or "NegRiskExchange" in src, (
            "Comment must name the specific contract (NegRiskExchange V2) "
            "so the underlying mechanism is clear — block is about THIS "
            "contract routing being untested, not market metadata."
        )

    def test_comment_mentions_old_filter_was_broken(self):
        src = inspect.getsource(OrderGateway._can_exit)
        # The comment should document WHY the old `outcome_count > 2` filter
        # never fired in practice.
        assert "outcome_count > 2" in src, (
            "Comment must document the OLD filter so future sessions can "
            "see what was removed and why. Aids in code-archaeology when "
            "investigating future regressions."
        )

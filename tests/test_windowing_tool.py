"""S199 windowing-tool tests — bot_pnl.py + edge_verification.py CLI extensions.

Covers the offline-only logic: deploy-stamp parsing, argparse backward compat,
and v7 verdict mapping. DB-touching paths (the actual SQL execution) are not
exercised here — they are validated by running the scripts against the prod
database during the Phase 7 gate evaluation.

Pinned because:
  - parse_deploy_timestamp is consumed by both scripts; format drift would
    silently mis-window queries (every event would compare to a wrong epoch).
  - argparse positional defaults are the pre-S199 invocation contract; breakage
    would silently change behavior for `bot_pnl.py BotName` and
    `edge_verification.py BotName` callers.
  - v7_verdict thresholds and ordering encode the gate decision from
    S172_CONSOLIDATED_PLAN.md:441-446 — a regression here would mis-classify
    Phase 7 elevation readiness.
"""
from datetime import datetime

import pytest

from scripts import bot_pnl, edge_verification


class TestParseDeployTimestamp:
    """Both scripts parse the YYYYMMDD_HHMMSS deploy-stamp identically."""

    def test_day2_deploy_stamp(self):
        # The canonical post-fix window referenced in S172_CONSOLIDATED_PLAN.md:441
        ts = bot_pnl.parse_deploy_timestamp("20260414_132211")
        assert ts == datetime(2026, 4, 14, 13, 22, 11)

    def test_edge_verification_uses_same_format(self):
        ts1 = bot_pnl.parse_deploy_timestamp("20260101_000000")
        ts2 = edge_verification.parse_deploy_timestamp("20260101_000000")
        assert ts1 == ts2 == datetime(2026, 1, 1, 0, 0, 0)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            bot_pnl.parse_deploy_timestamp("2026-04-14T13:22:11")
        with pytest.raises(ValueError):
            bot_pnl.parse_deploy_timestamp("not-a-stamp")


class TestBotPnlArgs:
    """bot_pnl._parse_args preserves pre-S199 positional invocation."""

    def test_defaults(self):
        a = bot_pnl._parse_args([])
        assert a.bot_name == "WeatherBot"
        assert a.hours == 24
        assert a.since is None

    def test_positional_bot_only(self):
        a = bot_pnl._parse_args(["EsportsBot"])
        assert a.bot_name == "EsportsBot"
        assert a.hours == 24
        assert a.since is None

    def test_positional_bot_and_hours(self):
        a = bot_pnl._parse_args(["MirrorBot", "8"])
        assert a.bot_name == "MirrorBot"
        assert a.hours == 8
        assert a.since is None

    def test_since_flag_alone(self):
        a = bot_pnl._parse_args(["MirrorBot", "--since", "20260414_132211"])
        assert a.bot_name == "MirrorBot"
        assert a.hours == 24
        assert a.since == datetime(2026, 4, 14, 13, 22, 11)

    def test_since_with_hours(self):
        a = bot_pnl._parse_args(["MirrorBot", "24", "--since", "20260414_132211"])
        assert a.hours == 24
        assert a.since == datetime(2026, 4, 14, 13, 22, 11)


class TestEdgeVerificationArgs:
    """edge_verification._parse_args preserves pre-S199 positional invocation."""

    def test_defaults(self):
        a = edge_verification._parse_args([])
        assert a.bot_name is None
        assert a.since is None
        assert a.clean is False

    def test_positional_bot(self):
        a = edge_verification._parse_args(["MirrorBot"])
        assert a.bot_name == "MirrorBot"
        assert a.since is None
        assert a.clean is False

    def test_phase7_gate_invocation(self):
        a = edge_verification._parse_args([
            "MirrorBot", "--since", "20260414_132211", "--clean"
        ])
        assert a.bot_name == "MirrorBot"
        assert a.since == datetime(2026, 4, 14, 13, 22, 11)
        assert a.clean is True

    def test_clean_alone(self):
        a = edge_verification._parse_args(["EsportsBot", "--clean"])
        assert a.clean is True
        assert a.since is None


class TestV7Verdict:
    """v7_verdict encodes the Phase 7 gate decision from S172:441-446."""

    def test_thresholds_match_plan(self):
        assert edge_verification.V7_PROCEED_THRESHOLD == 0.30
        assert edge_verification.V7_INVESTIGATE_THRESHOLD == 0.10
        assert edge_verification.V7_MIN_SAMPLE == 500

    def test_insufficient_sample_blocks_high_p_edge(self):
        # Even a P(edge>0)=0.99 below n=500 must return INSUFFICIENT SAMPLE —
        # the gate is not yet evaluable. This is the load-bearing rule from
        # S172:444 ("Minimum sample: 500+ closed trades").
        verdict, _ = edge_verification.v7_verdict(0.99, 100)
        assert verdict == "INSUFFICIENT SAMPLE"

    def test_insufficient_sample_blocks_low_p_edge(self):
        verdict, _ = edge_verification.v7_verdict(0.01, 499)
        assert verdict == "INSUFFICIENT SAMPLE"

    def test_proceed_above_threshold(self):
        verdict, _ = edge_verification.v7_verdict(0.50, 1000)
        assert verdict == "PROCEED"

    def test_proceed_at_threshold_inclusive(self):
        # Boundary: 0.30 is PROCEED, not AMBIGUOUS.
        verdict, _ = edge_verification.v7_verdict(0.30, 500)
        assert verdict == "PROCEED"

    def test_ambiguous_band(self):
        verdict, _ = edge_verification.v7_verdict(0.20, 500)
        assert verdict == "AMBIGUOUS"

    def test_ambiguous_at_lower_boundary_inclusive(self):
        # Boundary: 0.10 is AMBIGUOUS, not INVESTIGATE.
        verdict, _ = edge_verification.v7_verdict(0.10, 500)
        assert verdict == "AMBIGUOUS"

    def test_investigate_below_floor(self):
        verdict, _ = edge_verification.v7_verdict(0.05, 1000)
        assert verdict == "INVESTIGATE"

    def test_investigate_at_zero(self):
        verdict, _ = edge_verification.v7_verdict(0.0, 1000)
        assert verdict == "INVESTIGATE"

    def test_min_sample_boundary(self):
        # n=500 is the minimum; n=499 is below.
        assert edge_verification.v7_verdict(0.50, 500)[0] == "PROCEED"
        assert edge_verification.v7_verdict(0.50, 499)[0] == "INSUFFICIENT SAMPLE"

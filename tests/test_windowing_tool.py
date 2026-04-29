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


class TestBlock4Split:
    """S200 block 4 architectural split — whole-history integrity vs windowed counts.

    Regression: pre-S200 block 4 applied `--since` at the per-row aggregation
    layer, so a market with pre-window ENTRY + in-window RESOLUTION was scored
    as `entry_sz=0, disposal>0` and falsely flagged as a violation. The 32-
    market MB cohort that anchored Bug A diagnostic through S196→S199 was this
    artifact (AGENT_HANDOFF_S200_CLOSE.md §2.2). The split below pins both
    invariants so a future edit cannot silently re-introduce the windowing
    contamination on the integrity check, and cannot silently turn the
    windowed event-count diagnostic back into an integrity comparison.
    """

    def test_integrity_sql_constant_exported(self):
        # Both constants are module-level so the test can pin the SQL shape
        # offline (DB execution is verified during prod runs).
        assert hasattr(bot_pnl, "_INTEGRITY_SQL_ALL_TIME")
        assert hasattr(bot_pnl, "_WINDOWED_EVENT_COUNT_SQL")

    def test_integrity_sql_is_whole_history_no_since_filter(self):
        # The load-bearing assertion: integrity SQL must not filter by
        # event_time. If this fails, the S196→S199 cohort artifact is back.
        sql = bot_pnl._INTEGRITY_SQL_ALL_TIME
        assert "event_time" not in sql, (
            "integrity SQL must not filter by event_time — windowing at the "
            "per-row level produces false positives for markets with "
            "pre-window ENTRY + in-window RESOLUTION"
        )
        assert ":since_ts" not in sql, (
            "integrity SQL must not bind :since_ts — it is whole-history"
        )

    def test_integrity_sql_keeps_disposal_vs_entry_check(self):
        # The integrity check itself must remain — the fix is to apply it
        # whole-history, not to remove it.
        sql = bot_pnl._INTEGRITY_SQL_ALL_TIME
        assert "HAVING" in sql
        assert "* 1.001" in sql, "1.001 disposal-vs-entry tolerance must be preserved"
        assert "EXIT" in sql and "RESOLUTION" in sql and "ENTRY" in sql

    def test_integrity_sql_takes_only_bot_family_param(self):
        # No leak of windowing-era parameters. S203: param renamed from
        # :bot to :bot_family to support EsportsBot/EsportsBotV2 union.
        sql = bot_pnl._INTEGRITY_SQL_ALL_TIME
        assert ":bot_family" in sql
        # Single bound name expected; sanity-check by counting placeholder
        # tokens. :since_ts must NOT be present — block 4a is whole-history.
        assert ":since_ts" not in sql
        assert sql.count(":") == sql.count(":bot_family"), (
            "integrity SQL should bind only :bot_family"
        )

    def test_windowed_event_count_filters_by_since(self):
        # The windowed diagnostic IS time-bounded — that's its whole purpose.
        sql = bot_pnl._WINDOWED_EVENT_COUNT_SQL
        assert "event_time >= :since_ts" in sql

    def test_windowed_event_count_has_no_integrity_comparison(self):
        # No `* 1.001` tolerance. No HAVING that compares disposal-sum to
        # entry-sum. (A simple `HAVING n_event > 0` filter would still be
        # OK, but the current SQL drops HAVING entirely; both the tolerance
        # token and the disposal-vs-entry SUM compare must be absent.)
        sql = bot_pnl._WINDOWED_EVENT_COUNT_SQL
        assert "1.001" not in sql, (
            "windowed event-count diagnostic must not perform integrity "
            "comparison — that is block 4a's job"
        )

    def test_windowed_event_count_uses_count_not_size(self):
        # The windowed diagnostic counts events; it does not aggregate `size`.
        # Mixing the two would tempt a future edit to reintroduce the
        # entry-vs-disposal compare via size sums.
        sql = bot_pnl._WINDOWED_EVENT_COUNT_SQL
        assert "THEN 1 ELSE 0" in sql, "expected count-style aggregation"
        assert "CAST(size AS DOUBLE PRECISION)" not in sql, (
            "windowed event-count must not aggregate size — counts only"
        )

    def test_windowed_event_count_bounded_output(self):
        # LIMIT keeps console output usable even on high-volume windows.
        sql = bot_pnl._WINDOWED_EVENT_COUNT_SQL
        assert "LIMIT" in sql

    def test_block4_regression_cohort_signature(self):
        # End-to-end shape check on the bug we eliminated:
        # a market with one pre-window ENTRY and one in-window RESOLUTION
        # had `entry_sz=0, res_sz>0` under the old block 4, falsely matching
        # `disposal > entry * 1.001`. Under the split, that pattern is
        # whole-history-balanced (entry == disposal) so block 4a does NOT
        # match. Block 4b reports the in-window event volume only.
        integrity = bot_pnl._INTEGRITY_SQL_ALL_TIME
        windowed = bot_pnl._WINDOWED_EVENT_COUNT_SQL
        # The two queries must address different concerns — the simplest
        # statement of that is: integrity has the tolerance, windowed does
        # not; windowed has the time bound, integrity does not.
        assert "1.001" in integrity and "1.001" not in windowed
        assert "event_time" not in integrity and "event_time" in windowed


class TestExpandBotFamily:
    """S203 EB family-union: bot_pnl.py treats EsportsBot+EsportsBotV2 as one
    cohort because v1 stops trading at the v2 flag flip. Other bots map to
    themselves. This test class pins the routing rule offline (DB execution
    is verified during prod runs).

    Why pinned: this is the load-bearing rule that prevents post-flip silent
    cohort-split when an operator runs `bot_pnl.py EsportsBot` and EB v2 has
    rows in trade_events. Without this rule, the canonical Protocol 6/11
    P&L source would mis-report. See S203_EB_ROUTING_AUDIT.md §3.1.
    """

    def test_esports_v1_expands_to_family(self):
        family = bot_pnl._expand_bot_family("EsportsBot")
        assert family == ["EsportsBot", "EsportsBotV2"]

    def test_esports_v2_expands_to_family(self):
        # Symmetric: querying with v2 name returns the same union, so the
        # operator gets the family regardless of which name they pass.
        family = bot_pnl._expand_bot_family("EsportsBotV2")
        assert family == ["EsportsBot", "EsportsBotV2"]

    def test_weatherbot_is_singleton(self):
        family = bot_pnl._expand_bot_family("WeatherBot")
        assert family == ["WeatherBot"]

    def test_mirrorbot_is_singleton(self):
        family = bot_pnl._expand_bot_family("MirrorBot")
        assert family == ["MirrorBot"]

    def test_unknown_bot_is_singleton(self):
        # Default branch: any bot not in the family map maps to itself.
        # Prevents silent omission for new bots added to BOT_REGISTRY.
        family = bot_pnl._expand_bot_family("FutureBotV3")
        assert family == ["FutureBotV3"]

    def test_returns_new_list_each_call(self):
        # Must NOT return a shared reference — caller could mutate the
        # canonical family list otherwise. The helper returns list(...) of
        # the cached entry; assert that two calls produce distinct objects.
        a = bot_pnl._expand_bot_family("EsportsBot")
        b = bot_pnl._expand_bot_family("EsportsBot")
        assert a == b
        assert a is not b

    def test_sql_uses_any_bot_family_not_exact_match(self):
        # The integrity SQL must use ANY(:bot_family) — exact-match
        # `bot_name = :bot` is the pre-S203 shape and would silently drop
        # cross-family rows.
        sql = bot_pnl._INTEGRITY_SQL_ALL_TIME
        assert "ANY(:bot_family)" in sql
        assert "bot_name = :bot " not in sql
        assert "bot_name = :bot\n" not in sql

    def test_windowed_sql_uses_any_bot_family(self):
        sql = bot_pnl._WINDOWED_EVENT_COUNT_SQL
        assert "ANY(:bot_family)" in sql

"""S201 backfill_pre_ledger_entries — offline structural pins.

Covers the SQL-shape invariants and module-level constants. The actual DB
execution is verified by running the script against the prod database with
--dry-run, then live (mirror of S200's verification pattern at
scripts/backfill_shadow_entry_markets.py).

Pinned because:
  - The cohort definition is load-bearing: a regression that drops the
    ENTRY=0 condition would mass-emit phantom ENTRYs for every market a
    bot has ever traded.
  - The derived_size formula (entry_cost/entry_price) is the truthful-
    original-size choice from the script's design rationale (see docstring).
    Switching to positions.size or RESOLUTION.size would silently change
    the meaning of the backfilled events.
  - The bot_id-OR-source_bot join handles the historical column duplication
    (bot_id pre-S125, source_bot post-S125). Dropping either branch would
    silently miss markets.
"""
from scripts import backfill_pre_ledger_entries as backfill


class TestModuleConstants:
    """Module-level constants are stable identifiers used downstream
    (event_data marker queries, audit resolution_note grep)."""

    def test_backfill_source_marker(self):
        assert backfill._BACKFILL_SOURCE == "S201_pre_ledger"

    def test_resolution_note_mentions_orphan_resolution(self):
        # The audit triage workflow greps resolution_note to find
        # programmatic closures vs human ACKs.
        note = backfill._RESOLUTION_NOTE
        assert "S201" in note
        assert "ORPHAN_RESOLUTION" in note
        assert "SIZE_INVARIANT" in note  # warns operator the residue is intentional

    def test_cohort_sql_constant_exported(self):
        assert hasattr(backfill, "_COHORT_SQL")
        assert isinstance(backfill._COHORT_SQL, str)


class TestCohortSql:
    """The cohort detection SQL must match the Bug A signature precisely.

    A drift here is the highest-blast-radius failure mode for this script:
    too-loose cohort = phantom ENTRYs for live-traded markets; too-tight
    cohort = leaves Bug A markers stranded forever.
    """

    def test_cohort_signature_entry_zero_disposal_positive(self):
        sql = backfill._COHORT_SQL
        # Both halves of the Bug A signature must be present.
        assert "SUM(CASE WHEN event_type = 'ENTRY' THEN 1 ELSE 0 END) = 0" in sql
        assert "SUM(CASE WHEN event_type IN ('EXIT','RESOLUTION')" in sql
        assert "> 0" in sql

    def test_cohort_groups_by_bot_and_market(self):
        sql = backfill._COHORT_SQL
        # Bug A is per-(bot, market). A regression to GROUP BY market_id
        # alone would conflate cross-bot activity on shared markets.
        assert "GROUP BY bot_name, market_id" in sql

    def test_cohort_joins_positions_on_both_bot_columns(self):
        sql = backfill._COHORT_SQL
        # Historical positions rows split between bot_id (legacy) and
        # source_bot (post-S125). The join must accept either; dropping
        # one column silently misses the corresponding subset.
        assert "p.bot_id = b.bot_name" in sql
        assert "p.source_bot = b.bot_name" in sql

    def test_cohort_filters_zero_entry_cost_and_price(self):
        sql = backfill._COHORT_SQL
        # entry_cost / entry_price must both be > 0 — division-by-zero
        # protection AND a guard against backfilling positions that never
        # had a real cost basis (zombie positions, kill-switch artifacts).
        assert "p.entry_price IS NOT NULL AND p.entry_price > 0" in sql
        assert "p.entry_cost IS NOT NULL AND p.entry_cost > 0" in sql

    def test_cohort_restricts_side_to_yes_no(self):
        sql = backfill._COHORT_SQL
        # The trade_events check constraint allows YES/NO/SELL; backfill
        # only emits valid ENTRY sides (SELL is an EXIT-only artifact).
        assert "p.side IN ('YES','NO')" in sql

    def test_cohort_derives_size_from_cost_over_price(self):
        sql = backfill._COHORT_SQL
        # The truthful-original-size formula. A change here (e.g., to
        # p.size or RESOLUTION size) changes the semantics of the backfill
        # — see docstring "What this resolves" / "What this does NOT
        # resolve" for the design tradeoff.
        assert "p.entry_cost / p.entry_price" in sql

    def test_cohort_does_not_filter_by_bot_name(self):
        # Cohort SQL is bot-agnostic — handles WB, MB, EB sub-bugs in one
        # pass. A regression that hardcodes bot_name = 'WeatherBot' would
        # leave 10/73 markets stranded.
        sql = backfill._COHORT_SQL
        assert "bot_name = 'WeatherBot'" not in sql
        assert "bot_name = 'MirrorBot'" not in sql
        assert "bot_name = 'EsportsBot'" not in sql


class TestSafetyInvariants:
    """Invariants that protect against the script being weaponized as a
    general-purpose ENTRY emitter."""

    def test_cohort_sql_has_having_clause(self):
        # Without HAVING, the inner query becomes "every (bot, market)" —
        # backfill would fire on live-traded markets too.
        assert "HAVING" in backfill._COHORT_SQL

    def test_resolution_note_warns_size_invariant_residue(self):
        # The script intentionally leaves SIZE_INVARIANT detections OPEN
        # as historical-inflation markers. The note records this so a
        # future operator inspecting the resolution audit trail knows
        # those breaks are not stale.
        assert "intentional" in backfill._RESOLUTION_NOTE.lower()


class TestInsertEntrySql:
    """S203 hygiene fix: side-aware NOT EXISTS guard so dual-sided markets
    get both their YES and NO ENTRY events. Pre-S203, the guard checked
    only `bot_name + market_id + event_type='ENTRY'`, which silently
    dropped the second-side INSERT after the first one landed.

    Pinned because: the rule that lets the script handle dual-sided
    markets correctly is exactly the rule that makes idempotency work
    on re-runs. A regression to side-blind would re-introduce the S202
    drop; a regression away from idempotent would mass-emit duplicate
    ENTRY events on every re-run.
    """

    def test_insert_sql_constant_exported(self):
        assert hasattr(backfill, "_INSERT_ENTRY_SQL")
        assert isinstance(backfill._INSERT_ENTRY_SQL, str)

    def test_insert_uses_not_exists_with_side(self):
        # The S203 fix: AND side = :side inside the NOT EXISTS clause.
        sql = backfill._INSERT_ENTRY_SQL
        assert "NOT EXISTS" in sql
        assert "AND side = :side" in sql, (
            "S203 side discriminator must be on the NOT EXISTS guard — "
            "without it, dual-sided markets lose the second-side ENTRY"
        )

    def test_insert_event_type_is_entry(self):
        # Defense-in-depth: only ENTRY events are emitted. Other types
        # would be wildly out-of-scope for this script.
        sql = backfill._INSERT_ENTRY_SQL
        assert "'ENTRY'" in sql
        assert "'EXIT'" not in sql
        assert "'RESOLUTION'" not in sql

    def test_insert_execution_mode_is_paper(self):
        # Paper trading is canonical for this codebase (CLAUDE.md
        # 'PAPER TRADING IS PRODUCTION'); the historical positions
        # being backfilled were all paper.
        sql = backfill._INSERT_ENTRY_SQL
        assert "'paper'" in sql

    def test_insert_keeps_on_conflict_do_nothing(self):
        # Defense-in-depth: even if the NOT EXISTS guard misses an edge
        # case, the unique-index ON CONFLICT silently swallows it.
        sql = backfill._INSERT_ENTRY_SQL
        assert "ON CONFLICT DO NOTHING" in sql

    def test_insert_realized_pnl_is_null(self):
        # ENTRY events do not realize P&L; only EXIT and RESOLUTION do.
        # A regression that wrote a non-null realized_pnl on ENTRY would
        # silently change the meaning of P&L sums downstream.
        sql = backfill._INSERT_ENTRY_SQL
        assert "NULL" in sql  # realized_pnl
        assert "0," in sql.replace(" ", "").replace("\n", "")  # fees=0

    def test_not_exists_guards_required_keys(self):
        # The NOT EXISTS predicate is the load-bearing idempotency rule.
        # Removing any key would silently break re-run safety or open
        # the door to phantom emissions.
        sql = backfill._INSERT_ENTRY_SQL
        assert "WHERE bot_name = :bot_name" in sql
        assert "AND market_id = :market_id" in sql
        assert "AND event_type = 'ENTRY'" in sql
        assert "AND side = :side" in sql


class TestPostFlightAssertSql:
    """S203 hygiene fix: post-flight assertion uses unnest of in_scope
    (bot, market) tuples instead of the cross-product of cohort bots and
    in_scope markets. Pre-S203 the cross-product fired on cross-bot-share
    markets where one bot had positions and another did not (e.g. market
    0xed49... in cohort under both EB+MB; EB joinable, MB not).

    Pinned because: the assertion is the script's success-or-fail signal.
    Pre-S203 it fired on a clean run; post-S203 it fires only when the
    backfill itself failed to land an ENTRY for a (bot, market) we
    actually had positions for.
    """

    def test_assert_sql_constant_exported(self):
        assert hasattr(backfill, "_POST_FLIGHT_ASSERT_SQL")
        assert isinstance(backfill._POST_FLIGHT_ASSERT_SQL, str)

    def test_assert_uses_unnest_targets(self):
        # The targets CTE is built from parallel arrays — the S203 narrowing.
        sql = backfill._POST_FLIGHT_ASSERT_SQL
        assert "WITH targets AS" in sql
        assert "unnest(:tgt_bots::text[], :tgt_markets::text[])" in sql

    def test_assert_checks_entry_existence_per_target(self):
        # Each target must have an ENTRY event after the run. The check
        # uses NOT EXISTS so the assertion counts targets that are STILL
        # missing an ENTRY — the failure-mode semantics.
        sql = backfill._POST_FLIGHT_ASSERT_SQL
        assert "NOT EXISTS" in sql
        assert "te.event_type = 'ENTRY'" in sql

    def test_assert_does_not_use_cohort_cross_product(self):
        # Pre-S203 used `bot_name = ANY(:bots) AND market_id = ANY(:ids)`
        # which produces the cross-product. The S203 fix replaces that
        # with the unnest-pairs pattern. A regression here re-introduces
        # the cross-bot-share false positive.
        sql = backfill._POST_FLIGHT_ASSERT_SQL
        assert "bot_name = ANY(:bots)" not in sql

    def test_assert_does_not_re_test_disposal_signature(self):
        # The pre-S203 query repeated the cohort-detection HAVING clause
        # in the assertion. Post-S203 we just check ENTRY existence per
        # target — the cohort signature is a discovery-time concern, not
        # a post-flight concern.
        sql = backfill._POST_FLIGHT_ASSERT_SQL
        assert "EXIT" not in sql
        assert "RESOLUTION" not in sql
        assert "HAVING" not in sql

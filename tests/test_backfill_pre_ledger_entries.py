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

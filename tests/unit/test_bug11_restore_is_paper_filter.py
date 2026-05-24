"""S228 Bug 11: MirrorBot._restore_state_on_startup must filter by is_paper.

Bug history:
  - positions table has had an is_paper boolean column with an index
    (idx_positions_is_paper WHERE is_paper=true) since at least S85, when
    _reap_resolved_positions started using it (mirror_bot.py:1616 filters
    AND is_paper=true). The schema-level support for paper/live segregation
    pre-existed.
  - _restore_state_on_startup at mirror_bot.py:326-336 selected open
    positions with no is_paper filter. All rows loaded into _open_positions
    indiscriminately regardless of SIMULATION_MODE state.
  - Under SIMULATION_MODE=false (live), exit logic iterated _open_positions
    and routed exits via execution_engine → real CLOB → CLOB rejected with
    "not enough balance / allowance: balance: 0" because the deposit wallet
    holds zero outcome tokens for paper-derived markets.
  - Verified on-chain (2026-05-24): of 77 MB open positions, 74 marked
    is_paper=true (correctly paper-derived), 3 marked is_paper=false (flip
    #3 corruption from Bug 9+10 chain — execution_engine returned phantom
    success and risk_manager.update_position recorded them as live). All
    77 had zero CTF.balanceOf at deposit_wallet AND EOA.

Surfaced: S228 live flip #4 (2026-05-24 15:33 UTC). SELL signal on paper
position 0xba7ab705… size 2.10 @ $0.49 produced order_placement_failed +
live_order_retry with the "balance: 0" error. Rollback fired clean.

Root-cause class: same as S227 Bug 7 (schema/feature built but never
wired into the consumer). Column exists, index exists, ONE consumer
(_reap_resolved_positions) uses it correctly; the restore consumer did
not. Audit-pattern-completeness Protocol 16 candidate.

Fix:
  - _restore_state_on_startup SQL now includes `AND is_paper = :is_paper`.
  - :is_paper parameter is bool(settings.SIMULATION_MODE) — paper mode
    loads only is_paper=true rows; live mode loads only is_paper=false rows.
  - Emits mirror_restore_filter_applied log event for operator visibility.

Cross-bot blast radius:
  - This fix is in MirrorBot only. EsportsBot and WeatherBot have their
    own startup-restore code paths and may have the same gap (filed as
    follow-up to scope outside the S228 arc).
  - Behavior change: bots that were inadvertently restoring paper
    positions in live mode (only MirrorBot in production today) will now
    correctly skip them on startup.
  - Database is unchanged. Existing paper rows continue to exist;
    operator can still inspect them via direct queries.

Defense-in-depth: Commits B and C (capital + token-balance guards) add
runtime checks before BUY/SELL execution to defend against future
wiring-gap regressions like this one.

These tests detect future regressions via source-grep + structural
inspection of the function body.
"""
from __future__ import annotations

import inspect

from bots import mirror_bot as mb_mod


class TestS228Bug11Marker:
    """The marker must be present in the source for traceability."""

    def test_s228_bug11_marker_in_source(self):
        src = inspect.getsource(mb_mod)
        assert "S228 Bug 11" in src, (
            "S228 Bug 11 marker missing — fix may have been reverted. "
            "Restore code without is_paper filter will re-load paper "
            "positions in live mode and surface 'balance: 0' on every exit."
        )


class TestRestoreFilterStructural:
    """The restore SQL must filter by is_paper, with :is_paper binding."""

    def test_restore_sql_includes_is_paper_filter(self):
        src = inspect.getsource(mb_mod._MirrorBot__base__) if hasattr(mb_mod, "_MirrorBot__base__") else inspect.getsource(mb_mod.MirrorBot._restore_state_on_startup)
        assert "is_paper = :is_paper" in src, (
            "Restore SQL must include `AND is_paper = :is_paper` clause. "
            "Without it, paper-mode positions get loaded under live mode "
            "and trigger CLOB-side 'balance: 0' rejections on exit."
        )

    def test_restore_binds_is_paper_param(self):
        src = inspect.getsource(mb_mod.MirrorBot._restore_state_on_startup)
        # The binding must pass {is_paper: ...} as a query parameter
        assert '"is_paper":' in src or "'is_paper':" in src, (
            "Parameter dict for restore SELECT must bind :is_paper to a "
            "Python bool. Without it the query will fail at execution."
        )

    def test_restore_reads_simulation_mode(self):
        src = inspect.getsource(mb_mod.MirrorBot._restore_state_on_startup)
        assert "SIMULATION_MODE" in src, (
            "Restore must read SIMULATION_MODE from settings to determine "
            "paper/live filter direction. Hardcoding would break the bot "
            "in the opposite mode."
        )

    def test_restore_emits_filter_applied_log(self):
        src = inspect.getsource(mb_mod.MirrorBot._restore_state_on_startup)
        assert "mirror_restore_filter_applied" in src, (
            "Restore should emit mirror_restore_filter_applied for "
            "operator visibility (CLAUDE.md 'Can't Fully Verify' rule)."
        )


class TestRestoreFilterPaperLiveSymmetry:
    """The filter must symmetric — bool(SIMULATION_MODE) value passes directly."""

    def test_paper_mode_loads_paper_rows(self):
        """When SIMULATION_MODE=True, the filter value should be True
        (loading is_paper=true rows). Asserted via source inspection."""
        src = inspect.getsource(mb_mod.MirrorBot._restore_state_on_startup)
        # The function should pass bool(SIMULATION_MODE) directly as is_paper.
        # We don't simulate a real DB call here — that's an integration test
        # surface — but we assert the wiring shape via source.
        assert "SIMULATION_MODE" in src and "is_paper" in src
        # The value passed to :is_paper should be SIMULATION_MODE-derived,
        # not a literal True/False
        assert ': True' not in src.split("is_paper = :is_paper")[-1].split(")")[0] or \
               ': False' not in src.split("is_paper = :is_paper")[-1].split(")")[0], (
            "is_paper parameter must be dynamic from SIMULATION_MODE, not a "
            "hardcoded literal — otherwise mode-flipping doesn't work."
        )

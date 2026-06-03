#!/usr/bin/env python3
"""
Bot P&L Report — Canonical calculation from trade_events + positions.

Usage:
    python scripts/bot_pnl.py                              # WeatherBot, last 24h
    python scripts/bot_pnl.py EsportsBot                   # Specific bot, last 24h
    python scripts/bot_pnl.py WeatherBot 8                 # Specific bot, last 8h
    python scripts/bot_pnl.py MirrorBot --since 20260414_132211   # Post-fix windowed totals
    python scripts/bot_pnl.py MirrorBot --mode live        # WI-4: live trades only
    python scripts/bot_pnl.py MirrorBot --mode paper       # WI-4: paper/simulation only

EXIT side transition (S163, deployed 2026-04-08):
  - Before 2026-04-08T16:01:40Z: EXIT events have side='SELL' (hardcoded)
  - After  2026-04-08T16:01:40Z: EXIT events have side='YES' or 'NO' (token-outcome)
  - Integrity checks should NOT group by side for EXIT events. Use event_type only.

P&L math rules (uniform for YES and NO):
  - entry_price and current_price are ALWAYS token-specific prices
  - cost_basis = entry_price * size (for BOTH YES and NO)
  - unrealized_pnl = (current_price - entry_price) * size (for BOTH YES and NO)
  - realized_pnl on EXIT = (exit_price - entry_price) * size - fees
  - realized_pnl on RESOLUTION = (resolution_value - entry_price) * size - fees
    where resolution_value = 1.0 if your side wins, 0.0 if it loses

NEVER invert the formula for NO positions. Prices are already side-specific.
The position_manager uses (current - entry) * size uniformly.

S199: --since DEPLOY_TIMESTAMP windows blocks 3, 3b (all-time RAW, all-time
CLEAN totals) to event_time >= the parsed timestamp. Required for formal
Phase 7 elevation gate evaluation per S172_CONSOLIDATED_PLAN.md:441-446
(post-fix window is 20260414_132211 onwards). Block 1 (open positions) and
block 2 (last N hours) are unaffected by --since.

S200: block 4 is split into 4a (whole-history structural integrity — always
all-time, never windowed) and 4b (windowed event-count diagnostic — only
present when --since is set, no entry-vs-disposal compare). The original
`--since`-windowed block 4 produced false positives for markets with
pre-window ENTRY and in-window RESOLUTION (the cohort artifact that
mis-anchored Bug A through S196→S199 — see AGENT_HANDOFF_S200_CLOSE.md §2.2).

S203 hygiene #12: block 5 (WB-specific per-side / per-city / per-lead-time /
side x lead-time breakdowns) now honors `--since` and `--clean`. Pre-S203
block 5 was all-time only, which made the post-Day-2 CLEAN per-side
breakdown that drove the S203 Track 5 hypothesis-test a non-bot_pnl.py
output (Protocol 11 citation gap). The new flags wire block 5 into the
same windowing+contamination semantics as block 3b. Operators evaluating
WB Phase 6 NO-side-calibration H0' should run:
    python scripts/bot_pnl.py WeatherBot --since 20260414_132211 --clean
"""
import argparse
import asyncio
from datetime import datetime
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()


def parse_deploy_timestamp(ts: str) -> datetime:
    """Parse a deploy-stamp string `YYYYMMDD_HHMMSS` into a naive UTC datetime.

    Deploy timestamps in this codebase are UTC by convention (e.g.
    `20260414_132211` is Day 2 deploy, S172_CONSOLIDATED_PLAN.md:441).
    Returned naive datetime compares correctly against `event_time` columns.
    """
    return datetime.strptime(ts, "%Y%m%d_%H%M%S")


# S203: EsportsBot and EsportsBotV2 are the same logical bot family for P&L
# reporting — v1 stops trading at the v2 flag flip and v2 takes over the same
# capital allocation. Querying by either name returns the union so the
# operator's "how is the EB family doing" question survives the transition
# without silent cohort-split. See S203_EB_ROUTING_AUDIT.md §3.1.
_BOT_FAMILIES = {
    "EsportsBot": ["EsportsBot", "EsportsBotV2"],
    "EsportsBotV2": ["EsportsBot", "EsportsBotV2"],
}


def _expand_bot_family(bot_name: str) -> list[str]:
    """Map a bot_name to its query-family list.

    Returns a list with at least one element. Other bots map to themselves.
    Use the result with `WHERE bot_name = ANY(:bot_family)` so the SQL
    handles the singleton and family cases uniformly.
    """
    return list(_BOT_FAMILIES.get(bot_name, [bot_name]))


# Contamination CTE body — single source of truth for "which markets are
# size-invariant-violating across their whole history." Used by block 3b
# CLEAN total and (S203 hygiene #12) by block 5 WB-specific breakdowns
# when --clean is set. Whole-history scope is deliberate: contamination
# is a property of a market's whole lifetime, not a windowed slice (a
# market contaminated pre-deploy stays excluded even if its post-deploy
# events look healthy, because realized_pnl on those events still depends
# on the diverged cost-basis recorded earlier).
_CONTAMINATION_CTE_BODY = """
    SELECT market_id
    FROM trade_events
    WHERE bot_name = ANY(:bot_family)
      AND event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
      AND size IS NOT NULL
    GROUP BY market_id
    HAVING SUM(CASE WHEN event_type IN ('EXIT', 'RESOLUTION')
                    THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
         > SUM(CASE WHEN event_type = 'ENTRY'
                    THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) * 1.001
"""


# 4a. Whole-history structural integrity — aggregates over ALL trade_events for
# the bot, never windowed. Integrity is a property of a market's full lifetime:
# `event_time >=` filtering inside the per-market HAVING aggregation strips
# pre-window ENTRYs, so a market with pre-window ENTRY + in-window RESOLUTION
# evaluates to `entry_sz=0, disposal>0` and falsely trips. Same design rationale
# as block 3b's contamination CTE (whole-history, no `--since`).
_INTEGRITY_SQL_ALL_TIME = """
    SELECT market_id,
           SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS entry_sz,
           SUM(CASE WHEN event_type = 'EXIT' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS exit_sz,
           SUM(CASE WHEN event_type = 'RESOLUTION' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS res_sz
    FROM trade_events
    WHERE bot_name = ANY(:bot_family)
    GROUP BY market_id
    HAVING SUM(CASE WHEN event_type IN ('EXIT', 'RESOLUTION') THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
         > SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) * 1.001
"""

# 4b. Windowed event-count diagnostic — counts in-window events per market by
# type. Only runs when --since is set. Operational visibility for "what
# happened in the evaluation window"; does NOT compare entry vs disposal sums
# (no `* 1.001` tolerance, no integrity verdict). Markets with zero in-window
# events are excluded by GROUP BY semantics; LIMIT 50 keeps console output
# bounded for high-volume windows.
_WINDOWED_EVENT_COUNT_SQL = """
    SELECT market_id,
           SUM(CASE WHEN event_type = 'ENTRY' THEN 1 ELSE 0 END) AS n_entry,
           SUM(CASE WHEN event_type = 'EXIT' THEN 1 ELSE 0 END) AS n_exit,
           SUM(CASE WHEN event_type = 'RESOLUTION' THEN 1 ELSE 0 END) AS n_res
    FROM trade_events
    WHERE bot_name = ANY(:bot_family)
      AND event_time >= :since_ts
    GROUP BY market_id
    ORDER BY (SUM(CASE WHEN event_type IN ('EXIT', 'RESOLUTION') THEN 1 ELSE 0 END)) DESC,
             market_id
    LIMIT 50
"""


async def bot_pnl(bot_name: str, hours: int = 24, since: datetime | None = None,
                  clean: bool = False, mode: str = 'all', clob_check: bool = False):
    db = Database()
    await db.init()

    # S203: union v1+v2 for the EB family so post-flag-flip queries return a
    # single cohort regardless of which name the operator passes. Singletons
    # for all other bots — see _expand_bot_family docstring.
    bot_family = _expand_bot_family(bot_name)

    # WI-4: per-bot segmentation — filter by execution mode (paper/live/all).
    # positions.is_paper (BOOLEAN, migration 016) segments block 1.
    # trade_events.execution_mode (TEXT 'paper'|'live'|'backtest', migration 050)
    # segments blocks 2-5. Default 'all' preserves pre-WI-4 behavior.
    _valid_modes = ('paper', 'live', 'all')
    if mode not in _valid_modes:
        raise ValueError(f"mode must be one of {_valid_modes}, got {mode!r}")
    mode_pos_clause = {
        'paper': 'AND p.is_paper = TRUE',
        'live':  'AND p.is_paper = FALSE',
        'all':   '',
    }[mode]
    mode_exec_clause = {
        'paper': "AND execution_mode = 'paper'",
        'live':  "AND execution_mode = 'live'",
        'all':   '',
    }[mode]
    # Block 5/5b queries use `r` as the trade_events alias; qualify the column.
    mode_exec_clause_r = {
        'paper': "AND r.execution_mode = 'paper'",
        'live':  "AND r.execution_mode = 'live'",
        'all':   '',
    }[mode]
    mode_label = f" [{mode.upper()} only]" if mode != 'all' else ''

    async with db.get_session() as s:
        from sqlalchemy import text

        # Build the optional time-window clause shared by blocks 3, 3b. Block
        # 4a is whole-history (no since filter); block 4b uses :since_ts
        # directly when --since is set. When --since is None, behavior is
        # identical to pre-S199 (all-time).
        since_clause = "AND event_time >= :since_ts" if since is not None else ""
        since_params = {"since_ts": since} if since is not None else {}
        window_label = f"POST-{since.strftime('%Y%m%d_%H%M%S')}" if since is not None else "ALL-TIME"

        # S203 hygiene #12: optional CLEAN scope for block 5 WB-specific
        # breakdowns. Block 3b already always-emits a CLEAN total alongside
        # RAW; block 5 (per-side / per-city / per-lead-time / cross-tab)
        # was previously all-time-only and could not honor --since or
        # --clean. The hygiene fix wires both into block 5 via the prefix
        # (CTE), since clause (event_time filter), and clean clause (market
        # exclusion). r5/r6/r7/r9 reference `r.event_time` so the since
        # filter applies at disposal time, matching block 3's semantic.
        block5_clean_prefix = (
            f"WITH contaminated AS ({_CONTAMINATION_CTE_BODY}) "
            if clean else ""
        )
        block5_clean_clause = (
            "AND r.market_id NOT IN (SELECT market_id FROM contaminated)"
            if clean else ""
        )
        block5_since_clause = "AND r.event_time >= :since_ts" if since is not None else ""

        # 1. Open positions — mark-to-market
        r1 = await s.execute(text(f"""
            SELECT p.market_id, p.side, p.size, p.entry_price, p.current_price,
                   p.unrealized_pnl, p.opened_at
            FROM positions p
            WHERE (p.bot_id = ANY(:bot_family) OR p.source_bot = ANY(:bot_family))
              AND p.status = 'open'
              {mode_pos_clause}
            ORDER BY p.opened_at DESC
        """), {"bot_family": bot_family})
        positions = r1.fetchall()

        print(f"=== {bot_name} P&L Report (last {hours}h){mode_label} ===")
        if bot_family != [bot_name]:
            print(f"    [family-union: querying {bot_family}]")
        print()

        total_cost = 0.0
        total_upnl = 0.0
        total_mkt_value = 0.0
        print(f"OPEN POSITIONS ({len(positions)}):")
        print(f"{'Market':<14} {'Side':>4} {'Shares':>8} {'Entry':>7} {'Curr':>7} {'Cost':>9} {'Value':>9} {'uPnL':>9}")
        print("-" * 80)
        for p in positions:
            mid = p[0][:12] + ".."
            side = p[1]
            sz = float(p[2] or 0)
            entry = float(p[3] or 0)
            cur = float(p[4] or 0)
            # UNIFORM formula — same for YES and NO (prices are token-specific)
            cost = entry * sz
            mkt_val = cur * sz
            upnl = float(p[5]) if p[5] is not None else (cur - entry) * sz
            total_cost += cost
            total_mkt_value += mkt_val
            total_upnl += upnl
            print(f"{mid:<14} {side:>4} {sz:>8.1f} {entry:>7.4f} {cur:>7.4f} ${cost:>8.2f} ${mkt_val:>8.2f} ${upnl:>+8.2f}")
        print("-" * 80)
        print(f"{'TOTAL':<14} {'':>4} {'':>8} {'':>7} {'':>7} ${total_cost:>8.2f} ${total_mkt_value:>8.2f} ${total_upnl:>+8.2f}")

        # 2. Trade events in window
        r2 = await s.execute(text(f"""
            SELECT event_type, market_id, side, size, price, fees,
                   realized_pnl, event_time, correlation_id
            FROM trade_events
            WHERE bot_name = ANY(:bot_family)
              AND event_time > NOW() - INTERVAL '1 hour' * :hours
              AND event_time <= NOW()
              {mode_exec_clause}
            ORDER BY event_time DESC
        """), {"bot_family": bot_family, "hours": hours})
        events = r2.fetchall()

        entries = [e for e in events if e[0] == 'ENTRY']
        exits = [e for e in events if e[0] == 'EXIT']
        resolutions = [e for e in events if e[0] == 'RESOLUTION']

        print(f"\nTRADE EVENTS (last {hours}h):")
        print(f"  Entries: {len(entries)}")
        for e in entries:
            print(f"    {e[7].strftime('%H:%M')} {e[1][:12]}.. {e[2]} sz={float(e[3] or 0):.1f} @ {float(e[4] or 0):.4f} fee=${float(e[5] or 0):.2f}")

        realized_exit = 0.0
        print(f"  Exits: {len(exits)}")
        for e in exits:
            rpnl = float(e[6] or 0)
            realized_exit += rpnl
            print(f"    {e[7].strftime('%H:%M')} {e[1][:12]}.. sz={float(e[3] or 0):.1f} @ {float(e[4] or 0):.4f} pnl=${rpnl:+.2f}")

        realized_res = 0.0
        print(f"  Resolutions: {len(resolutions)}")
        for e in resolutions:
            rpnl = float(e[6] or 0)
            realized_res += rpnl
            print(f"    {e[7].strftime('%H:%M')} {e[1][:12]}.. pnl=${rpnl:+.2f}")

        # 2b. Day-by-day rollup over the same window. Operator question
        # "show me last N days day-by-day" was previously unanswerable from
        # block 2 (which only prints HH:MM per event). Sums realized_pnl per
        # UTC day for ENTRY/EXIT/RESOLUTION; net P&L per day = exit_pnl + res_pnl.
        r2b = await s.execute(text(f"""
            SELECT DATE(event_time) AS day,
                   event_type,
                   COUNT(*) AS cnt,
                   COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0) AS rpnl
            FROM trade_events
            WHERE bot_name = ANY(:bot_family)
              AND event_time > NOW() - INTERVAL '1 hour' * :hours
              AND event_time <= NOW()
              AND event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
              {mode_exec_clause}
            GROUP BY DATE(event_time), event_type
            ORDER BY day DESC, event_type
        """), {"bot_family": bot_family, "hours": hours})
        day_rows = r2b.fetchall()
        if day_rows:
            from collections import defaultdict
            day_agg = defaultdict(lambda: {"entries": 0, "exits": 0, "resolutions": 0,
                                           "exit_pnl": 0.0, "res_pnl": 0.0})
            for d, etype, cnt, rpnl in day_rows:
                bucket = day_agg[d]
                if etype == "ENTRY":
                    bucket["entries"] = int(cnt)
                elif etype == "EXIT":
                    bucket["exits"] = int(cnt)
                    bucket["exit_pnl"] = float(rpnl)
                elif etype == "RESOLUTION":
                    bucket["resolutions"] = int(cnt)
                    bucket["res_pnl"] = float(rpnl)
            print(f"\nDAY-BY-DAY ROLLUP (last {hours}h):")
            print(f"  {'Day':<12} {'Entries':>8} {'Exits':>6} {'Resols':>7} "
                  f"{'Exit P&L':>10} {'Res P&L':>10} {'Net P&L':>10}")
            print(f"  {'-'*72}")
            total_net = 0.0
            for d in sorted(day_agg.keys(), reverse=True):
                a = day_agg[d]
                net = a["exit_pnl"] + a["res_pnl"]
                total_net += net
                print(f"  {str(d):<12} {a['entries']:>8} {a['exits']:>6} "
                      f"{a['resolutions']:>7} ${a['exit_pnl']:>+9.2f} "
                      f"${a['res_pnl']:>+9.2f} ${net:>+9.2f}")
            print(f"  {'-'*72}")
            print(f"  {'TOTAL':<12} {'':>8} {'':>6} {'':>7} {'':>10} {'':>10} "
                  f"${total_net:>+9.2f}")

        # 3. All-time from trade_events (RAW — includes any contaminated markets)
        r3 = await s.execute(text(f"""
            SELECT event_type,
                   COUNT(*),
                   COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0),
                   COALESCE(SUM(CAST(fees AS DOUBLE PRECISION)), 0)
            FROM trade_events
            WHERE bot_name = ANY(:bot_family)
              {since_clause}
              {mode_exec_clause}
            GROUP BY event_type
            ORDER BY event_type
        """), {"bot_family": bot_family, **since_params})
        stats = r3.fetchall()
        print(f"\n{window_label} TRADE EVENTS (raw — includes contaminated markets):")
        total_realized = 0.0
        total_fees = 0.0
        for st in stats:
            rpnl = float(st[2])
            fees = float(st[3])
            total_realized += rpnl
            total_fees += fees
            print(f"  {st[0]:<12} count={st[1]:<5} realized=${rpnl:>+10.2f}  fees=${fees:>8.2f}")
        print(f"  {'TOTAL':<12} {'':5} realized=${total_realized:>+10.2f}  fees=${total_fees:>8.2f}")

        # 3b. CLEAN totals — excludes markets with size-invariant violations.
        # S196 forward-audit found that some EXIT and RESOLUTION events have
        # inflated `size` (positions.size diverged from trade_events ENTRY truth
        # pre-Phase-4b-alt fix). Their realized_pnl is over-stated proportionally.
        # CLEAN excludes any (bot, market) whose all-time SUM(EXIT+RESOLUTION size)
        # exceeds SUM(ENTRY size) — same threshold the audit's size_invariant_check
        # uses (1.001 tolerance). Use the CLEAN total for downstream analysis
        # (Phase 7 elevation gate, etc.).
        #
        # S199: contamination is a property of the market's whole history, so
        # the CTE intentionally does NOT apply --since. A market contaminated
        # pre-deploy stays excluded even if its post-deploy events look healthy,
        # because realized_pnl on those events still depends on the diverged
        # cost-basis recorded earlier. The outer SELECT applies --since so totals
        # reflect only post-fix activity on whole-history-clean markets.
        r3_clean = await s.execute(text(f"""
            WITH contaminated AS ({_CONTAMINATION_CTE_BODY})
            SELECT event_type,
                   COUNT(*),
                   COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0),
                   COALESCE(SUM(CAST(fees AS DOUBLE PRECISION)), 0)
            FROM trade_events
            WHERE bot_name = ANY(:bot_family)
              AND market_id NOT IN (SELECT market_id FROM contaminated)
              {since_clause}
              {mode_exec_clause}
            GROUP BY event_type
            ORDER BY event_type
        """), {"bot_family": bot_family, **since_params})
        stats_clean = r3_clean.fetchall()
        # Count contaminated markets (whole-history, no --since filter — see
        # comment above on why CTE deliberately ignores --since).
        r3_excluded = await s.execute(text(f"""
            SELECT COUNT(*) FROM ({_CONTAMINATION_CTE_BODY}) c
        """), {"bot_family": bot_family})
        excluded_count = int(r3_excluded.scalar() or 0)
        print(f"\n{window_label} TRADE EVENTS (clean — {excluded_count} contaminated markets excluded):")
        total_realized_clean = 0.0
        total_fees_clean = 0.0
        for st in stats_clean:
            rpnl = float(st[2])
            fees = float(st[3])
            total_realized_clean += rpnl
            total_fees_clean += fees
            print(f"  {st[0]:<12} count={st[1]:<5} realized=${rpnl:>+10.2f}  fees=${fees:>8.2f}")
        print(f"  {'TOTAL':<12} {'':5} realized=${total_realized_clean:>+10.2f}  fees=${total_fees_clean:>8.2f}")
        if excluded_count > 0:
            print(f"  ↑ Use this CLEAN total for downstream analysis "
                  f"(Phase 7 elevation gate, retune evaluations, etc.).")

        # 4a. Whole-history structural integrity check (S120 guardrail; SQL at
        # _INTEGRITY_SQL_ALL_TIME). S163: group by market_id only (not side) —
        # historical EXIT events used side='SELL' while ENTRYs used YES/NO,
        # causing false positives on per-side matching. event_type is the
        # correct discriminator. S200: no `--since` filter at this layer (see
        # constant docstring).
        # WI-4: build a mode-filtered copy of the integrity SQL when needed.
        # _INTEGRITY_SQL_ALL_TIME is a module-level constant (imported by tests)
        # and must not be mutated; we build a local copy here instead.
        _local_integrity_sql = (
            _INTEGRITY_SQL_ALL_TIME.replace(
                "WHERE bot_name = ANY(:bot_family)",
                f"WHERE bot_name = ANY(:bot_family)\n      {mode_exec_clause}",
                1,  # replace only the first occurrence
            ) if mode_exec_clause else _INTEGRITY_SQL_ALL_TIME
        )
        r4 = await s.execute(text(_local_integrity_sql), {"bot_family": bot_family})
        violations = r4.fetchall()
        if violations:
            print(f"\n{'!'*50}")
            print(f"WHOLE-HISTORY DATA INTEGRITY WARNINGS ({len(violations)}):")
            print(f"{'!'*50}")
            for v in violations:
                mid = v[0][:14] + ".." if len(v[0]) > 14 else v[0]
                print(f"  {mid}: entry={float(v[1]):.1f} exit={float(v[2]):.1f} res={float(v[3]):.1f} "
                      f"(disposal {float(v[2]) + float(v[3]):.1f} > entry {float(v[1]):.1f})")
            print(f"{'!'*50}")

        # 4b. Windowed event-count diagnostic (only when --since is set). SQL
        # at _WINDOWED_EVENT_COUNT_SQL. Operational visibility for in-window
        # event volume per market — NOT an integrity check.
        if since is not None:
            _local_windowed_sql = (
                _WINDOWED_EVENT_COUNT_SQL.replace(
                    "WHERE bot_name = ANY(:bot_family)",
                    f"WHERE bot_name = ANY(:bot_family)\n      {mode_exec_clause}",
                    1,
                ) if mode_exec_clause else _WINDOWED_EVENT_COUNT_SQL
            )
            r4b = await s.execute(
                text(_local_windowed_sql),
                {"bot_family": bot_family, "since_ts": since},
            )
            in_window = r4b.fetchall()
            if in_window:
                print(f"\n{window_label} EVENT-COUNT DIAGNOSTIC (top 50 by disposal volume):")
                print(f"  {'Market':<16} {'#ENTRY':>7} {'#EXIT':>7} {'#RES':>7}")
                print(f"  {'-'*42}")
                for r in in_window:
                    mid = r[0][:14] + ".." if len(r[0]) > 14 else r[0]
                    print(f"  {mid:<16} {int(r[1]):>7} {int(r[2]):>7} {int(r[3]):>7}")

        # Summary
        print(f"\n{'='*50}")
        print(f"SUMMARY")
        print(f"{'='*50}")
        print(f"  Open positions:     {len(positions)}")
        print(f"  Total cost basis:   ${total_cost:.2f}")
        print(f"  Total mkt value:    ${total_mkt_value:.2f}")
        print(f"  Unrealized P&L:     ${total_upnl:+.2f}")
        print(f"  Realized (exits):   ${realized_exit:+.2f}  (last {hours}h)")
        print(f"  Realized (resol):   ${realized_res:+.2f}  (last {hours}h)")
        _label = window_label.lower()
        print(f"  {_label} realized (raw):    ${total_realized:+.2f}")
        if excluded_count > 0:
            print(f"  {_label} realized (clean):  ${total_realized_clean:+.2f}  "
                  f"← canonical for downstream analysis")
        print(f"  Net P&L (window):           ${total_upnl + realized_exit + realized_res:+.2f}")

        # 5. WeatherBot dimensional breakdowns (S159; --since/--clean wired S203 hygiene #12)
        if bot_name == "WeatherBot":
            _block5_scope_label = (
                f"{window_label.lower()}, "
                f"{'CLEAN' if clean else 'RAW'}, "
                f"resolution+exit"
            )
            # Per-side breakdown: JOIN resolution/exit to their ENTRY for side.
            # WB-16: DISTINCT ON picks latest ENTRY side — if market re-entered on
            # opposite side, earlier exits misattributed. Rare for WeatherBot.
            r5 = await s.execute(text(f"""
                {block5_clean_prefix}
                SELECT e_entry.side,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id, side
                    FROM trade_events
                    WHERE bot_name = ANY(:bot_family) AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = ANY(:bot_family)
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  {block5_since_clause}
                  {block5_clean_clause}
                  {mode_exec_clause_r}
                GROUP BY e_entry.side
                ORDER BY e_entry.side
            """), {"bot_family": bot_family, **since_params})
            side_rows = r5.fetchall()
            if side_rows:
                print(f"\n{'='*50}")
                print(f"PER-SIDE BREAKDOWN ({_block5_scope_label})")
                print(f"{'='*50}")
                print(f"  {'Side':<6} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*40}")
                for sr in side_rows:
                    wr = (float(sr[2]) / float(sr[1]) * 100) if sr[1] > 0 else 0
                    print(f"  {sr[0]:<6} {sr[1]:>6} {sr[2]:>6} {wr:>6.1f}% ${float(sr[3]):>+11.2f}")

            # Per-city breakdown
            r6 = await s.execute(text(f"""
                {block5_clean_prefix}
                SELECT e_entry.event_data->>'city' AS city,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id, event_data
                    FROM trade_events
                    WHERE bot_name = ANY(:bot_family) AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = ANY(:bot_family)
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  AND e_entry.event_data->>'city' IS NOT NULL
                  {block5_since_clause}
                  {block5_clean_clause}
                  {mode_exec_clause_r}
                GROUP BY e_entry.event_data->>'city'
                ORDER BY total_pnl DESC
            """), {"bot_family": bot_family, **since_params})
            city_rows = r6.fetchall()
            if city_rows:
                print(f"\n{'='*50}")
                print(f"PER-CITY BREAKDOWN ({_block5_scope_label})")
                print(f"{'='*50}")
                print(f"  {'City':<20} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*55}")
                for cr in city_rows:
                    wr = (float(cr[2]) / float(cr[1]) * 100) if cr[1] > 0 else 0
                    print(f"  {(cr[0] or 'unknown'):<20} {cr[1]:>6} {cr[2]:>6} {wr:>6.1f}% ${float(cr[3]):>+11.2f}")

            # Per-lead-time bucket
            r7 = await s.execute(text(f"""
                {block5_clean_prefix}
                SELECT CASE
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 24 THEN '<24h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 48 THEN '24-48h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 72 THEN '48-72h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 120 THEN '72-120h'
                         ELSE '>=120h'
                       END AS bucket,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id, event_data
                    FROM trade_events
                    WHERE bot_name = ANY(:bot_family) AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = ANY(:bot_family)
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  AND e_entry.event_data->>'lead_time_hours' IS NOT NULL
                  {block5_since_clause}
                  {block5_clean_clause}
                  {mode_exec_clause_r}
                GROUP BY bucket
                ORDER BY MIN((e_entry.event_data->>'lead_time_hours')::float)
            """), {"bot_family": bot_family, **since_params})
            lt_rows = r7.fetchall()
            if lt_rows:
                print(f"\n{'='*50}")
                print(f"PER-LEAD-TIME BREAKDOWN ({_block5_scope_label})")
                print(f"{'='*50}")
                print(f"  {'Bucket':<10} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*45}")
                for lr in lt_rows:
                    wr = (float(lr[2]) / float(lr[1]) * 100) if lr[1] > 0 else 0
                    print(f"  {lr[0]:<10} {lr[1]:>6} {lr[2]:>6} {wr:>6.1f}% ${float(lr[3]):>+11.2f}")

            # S162: Side x Lead-time cross-tabulation
            r9 = await s.execute(text(f"""
                {block5_clean_prefix}
                SELECT e_entry.side,
                       CASE
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 24 THEN '<24h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 48 THEN '24-48h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 72 THEN '48-72h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 120 THEN '72-120h'
                         ELSE '>=120h'
                       END AS bucket,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id, side, event_data
                    FROM trade_events
                    WHERE bot_name = ANY(:bot_family) AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = ANY(:bot_family)
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  AND e_entry.event_data->>'lead_time_hours' IS NOT NULL
                  {block5_since_clause}
                  {block5_clean_clause}
                  {mode_exec_clause_r}
                GROUP BY e_entry.side, bucket
                ORDER BY e_entry.side, MIN((e_entry.event_data->>'lead_time_hours')::float)
            """), {"bot_family": bot_family, **since_params})
            xt_rows = r9.fetchall()
            if xt_rows:
                print(f"\n{'='*60}")
                print(f"SIDE x LEAD-TIME CROSS-TAB ({_block5_scope_label})")
                print(f"{'='*60}")
                print(f"  {'Side':<5} {'Bucket':<10} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*50}")
                for xr in xt_rows:
                    wr = (float(xr[3]) / float(xr[2]) * 100) if xr[2] > 0 else 0
                    print(f"  {xr[0]:<5} {xr[1]:<10} {xr[2]:>6} {xr[3]:>6} {wr:>6.1f}% ${float(xr[4]):>+11.2f}")

            # WB per-confidence-bin breakdown (windowed by `hours`).
            # Uses `trade_events.confidence` directly — WB populates this column
            # natively at ENTRY (bots/weather_bot.py:887). Same DISTINCT ON
            # latest-ENTRY join pattern as MB block 5b. Disposal events are
            # filtered by event_time so the breakdown reflects the same
            # window as block 2 (e.g. `WeatherBot 168` = last 7 days).
            r_wb_conf = await s.execute(text("""
                SELECT CASE
                         WHEN e_entry.confidence IS NULL THEN 'NULL'
                         WHEN e_entry.confidence < 0.50 THEN '<0.50'
                         WHEN e_entry.confidence < 0.55 THEN '0.50-0.54'
                         WHEN e_entry.confidence < 0.60 THEN '0.55-0.59'
                         WHEN e_entry.confidence < 0.65 THEN '0.60-0.64'
                         WHEN e_entry.confidence < 0.70 THEN '0.65-0.69'
                         WHEN e_entry.confidence < 0.75 THEN '0.70-0.74'
                         WHEN e_entry.confidence < 0.80 THEN '0.75-0.79'
                         WHEN e_entry.confidence < 0.85 THEN '0.80-0.84'
                         WHEN e_entry.confidence < 0.90 THEN '0.85-0.89'
                         ELSE '0.90+'
                       END AS conf_bin,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id, confidence
                    FROM trade_events
                    WHERE bot_name = ANY(:bot_family) AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = ANY(:bot_family)
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  AND r.event_time > NOW() - INTERVAL '1 hour' * :hours
                  AND r.event_time <= NOW()
                  {mode_exec_clause_r}
                GROUP BY conf_bin
                ORDER BY MIN(e_entry.confidence)
            """), {"bot_family": bot_family, "hours": hours})
            wb_conf_rows = r_wb_conf.fetchall()
            if wb_conf_rows:
                print(f"\n{'='*60}")
                print(f"PER-CONFIDENCE-BIN BREAKDOWN (last {hours}h, resolution+exit)")
                print(f"  Confidence source: trade_events.confidence (WB writes natively)")
                print(f"{'='*60}")
                print(f"  {'Bin':<11} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*48}")
                for wcr in wb_conf_rows:
                    win_pct = (float(wcr[2]) / float(wcr[1]) * 100) if wcr[1] > 0 else 0
                    print(f"  {(wcr[0] or 'unknown'):<11} {wcr[1]:>6} {wcr[2]:>6} "
                          f"{win_pct:>6.1f}% ${float(wcr[3]):>+11.2f}")

            # S159: Calibrator status from system_kv
            r8 = await s.execute(text(
                "SELECT value FROM system_kv WHERE key = 'weatherbot_cal_fit_history'"
            ))
            _cal_row = r8.scalar_one_or_none()
            if _cal_row:
                import json as _json
                _cal_hist = _json.loads(_cal_row)
                if _cal_hist:
                    _latest = _cal_hist[-1]
                    print(f"\n{'='*50}")
                    print(f"CALIBRATOR STATUS (latest fit)")
                    print(f"{'='*50}")
                    print(f"  n_no={_latest.get('n_no','?')}  n_yes={_latest.get('n_yes','?')}  "
                          f"holdout={'valid' if _latest.get('holdout_valid') else 'invalid'}  "
                          f"yes_widened={_latest.get('yes_widened', False)}")
                    _tb = _latest.get('train_brier')
                    _ob = _latest.get('oos_brier')
                    _rob = _latest.get('raw_oos_brier')
                    print(f"  train_brier={_tb}  oos_brier={_ob}  raw_oos_brier={_rob}")
                    print(f"  fitted={_latest.get('fitted')}  ts={_latest.get('ts','?')}")

        # 5b. MirrorBot per-confidence-bin breakdown (S213 follow-up).
        # S213 Bug 3 documents three confidence sources in trade_events;
        # COALESCE(confidence, conf_base + adjustments) is the formula
        # mirror_conf_charts.py:53-57 uses to reconstruct the value the
        # bot acted on at entry-decision time. Same JOIN pattern as WB
        # block 5 — DISTINCT ON (market_id) latest ENTRY for the
        # confidence reading; r.event_time honors --since.
        if bot_name == "MirrorBot":
            _block5_scope_label = (
                f"{window_label.lower()}, "
                f"{'CLEAN' if clean else 'RAW'}, "
                f"resolution+exit"
            )
            r_mb_conf = await s.execute(text(f"""
                {block5_clean_prefix}
                SELECT CASE
                         WHEN e_entry.final_conf IS NULL THEN 'NULL'
                         WHEN e_entry.final_conf < 0.50 THEN '<0.50'
                         WHEN e_entry.final_conf < 0.55 THEN '0.50-0.54'
                         WHEN e_entry.final_conf < 0.60 THEN '0.55-0.59'
                         WHEN e_entry.final_conf < 0.65 THEN '0.60-0.64'
                         WHEN e_entry.final_conf < 0.70 THEN '0.65-0.69'
                         WHEN e_entry.final_conf < 0.75 THEN '0.70-0.74'
                         WHEN e_entry.final_conf < 0.80 THEN '0.75-0.79'
                         WHEN e_entry.final_conf < 0.85 THEN '0.80-0.84'
                         WHEN e_entry.final_conf < 0.90 THEN '0.85-0.89'
                         ELSE '0.90+'
                       END AS conf_bin,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id,
                           COALESCE(confidence,
                                    (event_data->>'conf_base')::float
                                    + COALESCE((event_data->>'conf_cat_adj')::float, 0)
                                    + COALESCE((event_data->>'conf_price_adj')::float, 0)
                                    + COALESCE((event_data->>'conf_conv_adj')::float, 0)
                           ) AS final_conf
                    FROM trade_events
                    WHERE bot_name = ANY(:bot_family) AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = ANY(:bot_family)
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  {block5_since_clause}
                  {block5_clean_clause}
                  {mode_exec_clause_r}
                GROUP BY conf_bin
                ORDER BY MIN(e_entry.final_conf)
            """), {"bot_family": bot_family, **since_params})
            mb_conf_rows = r_mb_conf.fetchall()
            if mb_conf_rows:
                print(f"\n{'='*60}")
                print(f"PER-CONFIDENCE-BIN BREAKDOWN ({_block5_scope_label})")
                print(f"  Confidence source: COALESCE(top-level confidence, "
                      f"conf_base + conf_cat_adj + conf_price_adj + conf_conv_adj)")
                print(f"  See S213 Bug 3 for source-of-truth rationale.")
                print(f"{'='*60}")
                print(f"  {'Bin':<11} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*48}")
                for mr in mb_conf_rows:
                    wr = (float(mr[2]) / float(mr[1]) * 100) if mr[1] > 0 else 0
                    print(f"  {(mr[0] or 'unknown'):<11} {mr[1]:>6} {mr[2]:>6} {wr:>6.1f}% ${float(mr[3]):>+11.2f}")

            # Confidence-threshold aggregate — single canonical line per side
            # of the 0.60 split. Avoids requiring downstream arithmetic on the
            # per-bin output for common operator questions like "what's the
            # P&L on high-confidence trades."
            r_mb_thresh = await s.execute(text(f"""
                {block5_clean_prefix}
                SELECT CASE WHEN e_entry.final_conf >= 0.60 THEN '>=0.60' ELSE '<0.60' END AS conf_split,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id,
                           COALESCE(confidence,
                                    (event_data->>'conf_base')::float
                                    + COALESCE((event_data->>'conf_cat_adj')::float, 0)
                                    + COALESCE((event_data->>'conf_price_adj')::float, 0)
                                    + COALESCE((event_data->>'conf_conv_adj')::float, 0)
                           ) AS final_conf
                    FROM trade_events
                    WHERE bot_name = ANY(:bot_family) AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = ANY(:bot_family)
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  AND e_entry.final_conf IS NOT NULL
                  {block5_since_clause}
                  {block5_clean_clause}
                  {mode_exec_clause_r}
                GROUP BY conf_split
                ORDER BY conf_split DESC
            """), {"bot_family": bot_family, **since_params})
            mb_thresh_rows = r_mb_thresh.fetchall()
            if mb_thresh_rows:
                print(f"\n  CONFIDENCE-THRESHOLD AGGREGATE (split at 0.60):")
                print(f"  {'Split':<11} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*48}")
                for tr in mb_thresh_rows:
                    wr = (float(tr[2]) / float(tr[1]) * 100) if tr[1] > 0 else 0
                    print(f"  {tr[0]:<11} {tr[1]:>6} {tr[2]:>6} {wr:>6.1f}% ${float(tr[3]):>+11.2f}")

    # 6. (Phase-1, opt-in --clob-check) DB-vs-CLOB resolution cross-reference.
    #    Gated behind the flag so default runs stay DB-only / offline / fast and
    #    byte-identical to pre-Phase-1 behavior. Own session, isolated from the
    #    report above. On-chain/CLOB is canonical-by-construction (see
    #    LIVE_ONCHAIN_RECONCILIATION_2026-06-03.md §8) — a mismatch flags ledger drift.
    if clob_check:
        async with db.get_session() as cs:
            await _clob_resolution_crosscheck(cs, bot_family, since, mode)

    await db.close()


# Cap on markets cross-checked per run (each is one CLOB HTTP fetch). Operator
# narrows scope with --since; capping is announced, never silent (no-silent-caps).
_CLOB_CHECK_MARKET_CAP = 200


async def _clob_resolution_crosscheck(session, bot_family, since, mode,
                                      cap: int = _CLOB_CHECK_MARKET_CAP):
    """Phase-1 (opt-in --clob-check): cross-reference each resolved market's DB
    resolution against the CLOB/on-chain resolution and flag mismatches.

    On-chain/CLOB is canonical-by-construction (LIVE_ONCHAIN_RECONCILIATION_
    2026-06-03.md §8); a DB-vs-CLOB disagreement means the DB ledger's resolution
    drifted from reality, so any realized P&L derived from it is suspect.

    Read-only and fail-soft: a CLOB fetch failure annotates the market
    CLOB=unavailable and never raises. Reuses the existing CLOB fetch
    (_fetch_market_by_condition_id + _clob_to_market_format) already used by
    reconcile_live_onchain.py — no new client. In-process cache per condition_id;
    capped at `cap` markets with a logged notice if more exist."""
    from sqlalchemy import text
    from base_engine.data.resolution_backfill import (
        _fetch_market_by_condition_id, _clob_to_market_format,
    )

    _since_clause = "AND te.event_time >= :since_ts" if since is not None else ""
    _since_params = {"since_ts": since} if since is not None else {}
    _mode_clause = {
        "paper": "AND te.execution_mode = 'paper'",
        "live": "AND te.execution_mode = 'live'",
        "all": "",
    }.get(mode, "")

    # Resolved markets this bot traded, in scope. condition_id is the CLOB API key;
    # fall back to market_id when it's already a 0x condition id.
    rows = (await session.execute(text(f"""
        SELECT DISTINCT te.market_id,
               COALESCE(m.condition_id, te.market_id) AS condition_id,
               m.resolution
        FROM trade_events te
        JOIN markets m ON (CAST(m.id AS TEXT) = te.market_id
                           OR m.condition_id = te.market_id)
        WHERE te.bot_name = ANY(:bot_family)
          AND m.resolved = TRUE
          AND m.resolution IN ('YES', 'NO')
          {_since_clause}
          {_mode_clause}
        ORDER BY te.market_id
        LIMIT :cap_plus
    """), {"bot_family": bot_family, "cap_plus": cap + 1, **_since_params})).fetchall()

    print("\nRESOLUTION CROSS-CHECK (DB vs CLOB):")
    if not rows:
        print("  (no resolved markets in scope)")
        return
    if len(rows) > cap:
        rows = rows[:cap]
        print(f"  NOTE: capped at {cap} markets; more resolved markets exist in "
              f"scope — re-run with a tighter --since to cover the rest.")

    _cache: dict = {}
    matches = mismatches = unavailable = 0
    mismatch_lines = []
    for mid, cond_id, db_res in rows:
        db_res_u = str(db_res).upper() if db_res else None
        if cond_id in _cache:
            clob_res = _cache[cond_id]
        else:
            clob_res = None
            try:
                clob_mkt = await _fetch_market_by_condition_id(cond_id)
                if clob_mkt:
                    _fmt = _clob_to_market_format(clob_mkt, cond_id) or {}
                    _r = _fmt.get("resolution")
                    clob_res = str(_r).upper() if _r else None
            except Exception:
                clob_res = None
            _cache[cond_id] = clob_res

        if clob_res is None:
            unavailable += 1
        elif clob_res == db_res_u:
            matches += 1
        else:
            mismatches += 1
            mismatch_lines.append(
                f"  {str(mid)[:14]}.. DB={db_res_u} CLOB={clob_res}  ⚠ MISMATCH")

    for line in mismatch_lines:
        print(line)
    print(f"  checked={matches + mismatches + unavailable}  match={matches}  "
          f"MISMATCH={mismatches}  clob_unavailable={unavailable}")
    if mismatches:
        print("  ⚠ DB resolution disagrees with CLOB on the markets above — the DB "
              "ledger has drifted from on-chain truth; do not trust their realized "
              "P&L until reconciled.")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI parser. Preserves pre-S199 positional invocation: `bot_pnl.py [bot] [hours]`."""
    p = argparse.ArgumentParser(description="Bot P&L Report (canonical)")
    p.add_argument("bot_name", nargs="?", default="WeatherBot",
                   help="Bot name (default: WeatherBot)")
    p.add_argument("hours", nargs="?", type=int, default=24,
                   help="Window for block 2 'recent events' display, in hours (default: 24)")
    p.add_argument("--since", type=parse_deploy_timestamp, default=None,
                   metavar="YYYYMMDD_HHMMSS",
                   help="Filter all-time RAW/CLEAN totals to event_time >= this UTC stamp. "
                        "Format matches deploy timestamps (e.g., 20260414_132211).")
    p.add_argument("--clean", action="store_true", default=False,
                   help="Apply CLEAN (whole-history-contamination-excluded) scope to "
                        "block 5 WB-specific breakdowns (per-side, per-city, per-lead-time, "
                        "side x lead-time). S203 hygiene #12. Mirrors edge_verification.py "
                        "--clean semantic. Block 3b (CLEAN total) always emits regardless.")
    p.add_argument("--mode", choices=("paper", "live", "all"), default="all",
                   help="WI-4 execution-mode segmentation. 'paper': simulation trades only "
                        "(positions.is_paper=TRUE, trade_events.execution_mode='paper'). "
                        "'live': real-capital trades only (is_paper=FALSE, execution_mode='live'). "
                        "'all': no filter (default — preserves pre-WI-4 behavior).")
    p.add_argument("--clob-check", action="store_true", default=False,
                   help="Phase-1: cross-reference each resolved market's DB resolution "
                        "against the CLOB/on-chain resolution and flag mismatches. OFF by "
                        "default — default runs stay DB-only/offline. Makes one CLOB HTTP "
                        "fetch per resolved market in scope (cached, capped, fail-soft); "
                        "narrow scope with --since.")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(bot_pnl(args.bot_name, args.hours, since=args.since, clean=args.clean,
                        mode=args.mode, clob_check=args.clob_check))

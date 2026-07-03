"""MirrorBot v3 entrypoint — silo scaffold.

Boot order is the contract:
  1. env_guard.assert_safe_env()   — BEFORE any engine import/construction.
  2. Database.init()               — fail fast if session_factory is None.
  3. state_restore.restore_state() — guards open ONLY on full success;
                                     any failure exits 1 (systemd restarts).
  4. Heartbeat idle loop           — the strategy slot is EMPTY by design
                                     (MB_REBUILD_PLAN.md §2: nothing trades
                                     until a rule passes the acceptance gate).

What this scaffold does NOT yet do (deliberate, gated milestones):
entry execution, exits, RTDS consumption, rejection logging, sizing.
It boots, restores guard state under the new identity, and proves the
silo's safety spine end-to-end in paper.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mirror_v3 import BOT_NAME
from mirror_v3.env_guard import assert_safe_env

HEARTBEAT_S = 60


def _mode() -> str:
    return "paper" if os.environ.get("SIMULATION_MODE", "").lower() in ("true", "1", "yes") else "live"


async def main() -> int:
    # 1. Safety trio or refuse to boot (raises EnvGuardError with all violations).
    summary = assert_safe_env()
    print(f"[{BOT_NAME}] env guard OK: {summary}", flush=True)

    # Imports AFTER the guard so a poisoned env can't even construct engines.
    from sqlalchemy import text
    from base_engine.data.database import Database
    from mirror_v3.guards import OneBetPerMarketGuards
    from mirror_v3.state_restore import restore_state

    db = Database()
    await db.init()
    try:
        if db.session_factory is None:
            print(f"[{BOT_NAME}] DB init failed — DATABASE_URL invalid", file=sys.stderr)
            return 3

        mode = _mode()
        guards = OneBetPerMarketGuards()

        # v2-contract loaders (exact SQL shapes from the verified v2 restore,
        # new identity). S244: entered-sides uses SAME-MODE history only.
        async def fetch_entered():
            async with db.get_session() as s:
                rows = await s.execute(text(
                    "SELECT DISTINCT te.market_id, te.side FROM trade_events te "
                    "JOIN markets m ON m.condition_id = te.market_id "
                    "WHERE te.bot_name = :bot AND te.event_type = 'ENTRY' "
                    "AND te.side IN ('YES', 'NO') AND m.resolved = false "
                    "AND te.execution_mode = :mode"
                ), {"bot": BOT_NAME, "mode": mode})
                return [(r.market_id, r.side) for r in rows.fetchall()]

        async def fetch_open():
            async with db.get_session() as s:
                rows = await s.execute(text(
                    "SELECT market_id, side FROM positions "
                    "WHERE (source_bot = :bot OR bot_id = :bot) "
                    "AND status = 'open' AND is_paper = :paper "
                    "AND side IN ('YES', 'NO')"
                ), {"bot": BOT_NAME, "paper": mode == "paper"})
                return [(r.market_id, r.side) for r in rows.fetchall()]

        async def fetch_daily_exposure():
            async with db.get_session() as s:
                row = await s.execute(text(
                    "SELECT "
                    "  COALESCE(SUM(CASE WHEN event_type = 'ENTRY' "
                    "    THEN CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION) ELSE 0 END), 0) "
                    "  - COALESCE(SUM(CASE WHEN event_type = 'EXIT' "
                    "    THEN CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION) ELSE 0 END), 0) "
                    "FROM trade_events "
                    "WHERE bot_name = :bot AND event_time >= CURRENT_DATE"
                ), {"bot": BOT_NAME})
                return float(row.scalar() or 0.0)

        result = await restore_state(guards, fetch_entered, fetch_open, fetch_daily_exposure)
        if not result.restored:
            # Fail-closed AND fail-loud: no silent partial restore (the v2 bug).
            print(f"[{BOT_NAME}] RESTORE FAILED — guards stay closed, exiting for "
                  f"systemd restart: {result.error}", file=sys.stderr)
            return 1
        print(f"[{BOT_NAME}] restored: entered={result.entered_loaded} "
              f"open={result.open_loaded} daily_exposure=${result.daily_exposure_usd:.2f} "
              f"mode={mode}", flush=True)

        # 4. Idle heartbeat — strategy slot intentionally empty behind the gate.
        while True:
            print(f"[{BOT_NAME}] heartbeat {guards.snapshot()} mode={mode} "
                  f"strategy=EMPTY(gated)", flush=True)
            await asyncio.sleep(HEARTBEAT_S)
    finally:
        await db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

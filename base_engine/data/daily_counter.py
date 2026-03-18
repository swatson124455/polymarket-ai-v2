"""
Write-through persistence for purely-additive daily counters.

Usage contract (see CLAUDE.md state persistence decision tree):
- Use for counters that are ONLY incremented (never decremented) within a day.
- Do NOT use for net counters (up+down) — use paper_trades SUM on startup instead.
- Do NOT use for multi-day accumulators — those need explicit expiry columns.

Counters reset automatically at UTC midnight because the table is keyed by
(bot_id, counter_date, counter_name) and counter_date = CURRENT_DATE.

Current users:
- EsportsBot: _game_exposure persisted as counter_name="game_{game}" keys.
"""
from typing import Dict

from sqlalchemy import text as _sa_text


async def increment_counter(db, bot_id: str, name: str, amount: float) -> None:
    """Upsert: add amount to today's counter for (bot_id, name).

    Must be called with await — do not use asyncio.create_task (fire-and-forget
    risks in-memory/DB divergence on DB errors, undermining the write-through guarantee).
    """
    async with db.get_session() as sess:
        await sess.execute(
            _sa_text("""
                INSERT INTO daily_counters (bot_id, counter_date, counter_name, counter_value)
                VALUES (:bot_id, CURRENT_DATE, :name, :amount)
                ON CONFLICT (bot_id, counter_date, counter_name)
                DO UPDATE SET
                    counter_value = daily_counters.counter_value + :amount,
                    updated_at    = NOW()
            """),
            {"bot_id": bot_id, "name": name, "amount": amount},
        )
        await sess.commit()


async def restore_counters(db, bot_id: str) -> Dict[str, float]:
    """Read today's counters for bot_id. Returns {counter_name: value}.

    Returns empty dict if no counters exist (new day or first run).
    """
    async with db.get_session() as sess:
        rows = await sess.execute(
            _sa_text("""
                SELECT counter_name, counter_value
                FROM daily_counters
                WHERE bot_id = :bot_id AND counter_date = CURRENT_DATE
            """),
            {"bot_id": bot_id},
        )
        return {r.counter_name: float(r.counter_value) for r in rows.fetchall()}

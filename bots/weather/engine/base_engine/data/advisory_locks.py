"""S195 §8A — PostgreSQL advisory-lock helpers.

Used by the open-or-modify code paths in `positions` to serialise racing
bots on a per-market basis. Adopting bots wrap their open path:

    from bots.weather.engine.base_engine.data.advisory_locks import advisory_lock_for_market
    ...
    async with db.get_session() as session:
        async with advisory_lock_for_market(session, market_id):
            # any subsequent bot taking the same lock on the same
            # market_id blocks here until our session commits/rollbacks.
            ...

`pg_advisory_xact_lock` releases automatically at transaction end. Without
an active transaction the lock is meaningless, so the helper requires
the session to already be inside one (the SQLAlchemy AsyncSession default
when used as an `async with` context).

`hashtext(market_id::text)` reduces the variable-length market id to a
stable `int4`, which casts to `bigint` for the lock argument. Hash
collisions (~1 in 2^32) cause two unrelated markets to serialise
unnecessarily — never an incorrect result.

Design doc: docs/8A_position_registry_design.md §3.2.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext(:market_id))")


@asynccontextmanager
async def advisory_lock_for_market(
    session: AsyncSession, market_id: str
) -> AsyncIterator[None]:
    """Take a per-market advisory lock for the duration of the txn.

    Args:
        session: an AsyncSession already inside an active transaction.
            The lock binds to this transaction and releases on commit
            or rollback.
        market_id: the market identifier (typically a 0x-prefixed hex
            string). Empty / None raises ValueError — silently locking
            on the empty string would collapse all racers onto one slot.

    The lock is taken via `pg_advisory_xact_lock(hashtext(market_id))`.
    Two callers passing the same market_id serialise: the second blocks
    until the first's transaction ends. Different market_ids do not
    block each other (modulo hashtext collisions).

    This helper takes only the lock — it does NOT begin or commit a
    transaction. Caller controls those boundaries. The helper does not
    suppress exceptions raised inside the `with` body; on exception the
    transaction is rolled back by the surrounding session manager and
    the lock is released as part of that rollback.
    """
    if not market_id:
        raise ValueError(
            "advisory_lock_for_market requires a non-empty market_id"
        )
    await session.execute(_LOCK_SQL, {"market_id": market_id})
    yield

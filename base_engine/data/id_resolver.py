"""
Resolve raw market identifiers (condition_id, slug, or id) to canonical market id.
Enables storing trades with canonical m.id only, simplifying JOINs from
  t.market_id = m.id OR t.market_id = m.condition_id OR t.market_id = m.slug
to t.market_id = m.id.
"""
from typing import Dict, List, Optional

from sqlalchemy import select, or_, text
from structlog import get_logger

from base_engine.data.database import Market

logger = get_logger()


async def resolve_market_id(db, raw_id: str) -> Optional[str]:
    """
    Resolve raw market identifier (id, condition_id, or slug) to canonical market id.
    Returns m.id if found, None otherwise.
    """
    if not raw_id or not db or not getattr(db, "session_factory", None):
        return None
    raw_id = str(raw_id).strip()
    if not raw_id:
        return None
    async with db.get_raw_session() as session:
        result = await session.execute(
            select(Market.id).where(
                or_(
                    Market.id == raw_id,
                    Market.condition_id == raw_id,
                    Market.slug == raw_id,
                )
            ).limit(1)
        )
        row = result.scalar_one_or_none()
        return str(row) if row is not None else None


async def resolve_market_ids_batch(db, raw_ids: List[str]) -> Dict[str, str]:
    """
    Resolve multiple raw identifiers to canonical ids. Returns dict mapping raw_id -> canonical_id.
    Skips None/empty; only includes resolved mappings.
    """
    if not raw_ids or not db or not getattr(db, "session_factory", None):
        return {}
    seen = set()
    unique = [x for x in (str(i).strip() for i in raw_ids if i) if x and x not in seen and not seen.add(x)]
    if not unique:
        return {}
    out: Dict[str, str] = {}
    async with db.get_raw_session() as session:
        # S177: Server-side timeout replaces asyncio.wait_for (which corrupts asyncpg state)
        await session.execute(text("SET LOCAL statement_timeout = '15000'"))
        result = await session.execute(
            select(Market.id, Market.condition_id, Market.slug).where(
                or_(
                    Market.id.in_(unique),
                    Market.condition_id.in_(unique),
                    Market.slug.in_(unique),
                )
            )
        )
        for row in result.all():
            mid = str(row[0]) if row[0] else None
            if not mid:
                continue
            for val in (row[0], row[1], row[2]):
                if val and str(val).strip() in unique:
                    out[str(val).strip()] = mid
    return out

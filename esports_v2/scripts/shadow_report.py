"""
Shadow mode gate evaluation report.

Reads esports_predictions (mode='shadow') and evaluates Gate 5v2-C criteria.

Usage:
    python -m esports_v2.scripts.shadow_report

Requires DATABASE_URL environment variable.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    # Convert postgres:// to postgresql+asyncpg://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from esports_v2.shadow.db import get_shadow_stats
    from esports_v2.shadow.metrics import compute_shadow_gate, format_gate_report

    async with async_session() as session:
        stats = await get_shadow_stats(session)

    await engine.dispose()

    if stats.get("n_total", 0) == 0:
        print("No shadow predictions found in esports_predictions.")
        print("Run the EsportsBotV2 in shadow mode first.")
        sys.exit(0)

    passed, metrics, failures = compute_shadow_gate(stats)
    print(format_gate_report(passed, metrics, failures))


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Signal quality report: accuracy of signals vs market resolution.

Learn what works (or that signals suck). Run from project root:
  python scripts/signal_quality_report.py
"""
import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


async def main() -> int:
    from base_engine.data.database import Database, Signal
    from sqlalchemy import select, func, and_

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized. Set DATABASE_URL.")
        return 1

    async with db.get_session() as session:
        total = (await session.execute(select(func.count(Signal.id)))).scalar() or 0
        with_outcome = (await session.execute(
            select(func.count(Signal.id)).where(Signal.outcome_correct.isnot(None))
        )).scalar() or 0
        correct = (await session.execute(
            select(func.count(Signal.id)).where(Signal.outcome_correct == True)
        )).scalar() or 0
        incorrect = (await session.execute(
            select(func.count(Signal.id)).where(Signal.outcome_correct == False)
        )).scalar() or 0

        # By source_type
        from sqlalchemy import case
        by_source = await session.execute(
            select(
                Signal.source_type,
                Signal.source_name,
                func.count(Signal.id).label("n"),
                func.sum(case((Signal.outcome_correct == True, 1), else_=0)).label("correct"),
            )
            .where(Signal.outcome_correct.isnot(None))
            .group_by(Signal.source_type, Signal.source_name)
        )
        rows = by_source.all()

    print("=" * 60)
    print("SIGNAL QUALITY REPORT")
    print("=" * 60)
    print(f"  Total signals:        {total:,}")
    print(f"  With outcome:         {with_outcome:,} (evaluated after market resolved)")
    print(f"  Correct:              {correct:,}")
    print(f"  Incorrect:            {incorrect:,}")
    if with_outcome > 0:
        acc = 100.0 * correct / with_outcome
        print(f"  Overall accuracy:     {acc:.1f}%")
        if acc < 50:
            print("  --> Signals worse than random. Consider deprioritizing or improving.")
        elif acc < 55:
            print("  --> Marginal. More data may help.")
        else:
            print("  --> Positive edge. Worth using.")
    else:
        print("  Overall accuracy:     N/A (no resolved signals yet)")

    if rows:
        print()
        print("By source:")
        print("-" * 60)
        for st, sn, n, c in rows:
            c = c or 0
            acc = 100.0 * c / n if n else 0
            print(f"  {st}/{sn}: {n} evaluated, {acc:.1f}% correct")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

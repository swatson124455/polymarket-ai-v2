"""
One-shot script: seed historical training data for 6 new esports games.

Collects 90 days of match history from PandaScore for dota2, valorant, cod,
r6, sc2, rl. Stores Glicko-2 metadata in esports_training_data table.

Run on VPS:
    cd /opt/polymarket-ai-v2
    sudo -u ubuntu venv/bin/python -m scripts.seed_esports_data

Rate limit: ~200 API requests total, sequential with built-in 4s page sleep.
"""
from __future__ import annotations

import asyncio
import sys

from structlog import get_logger

logger = get_logger()

NEW_GAMES = ["dota2", "valorant", "cod", "r6", "sc2", "rl"]


async def main() -> None:
    from config.settings import settings
    from base_engine.data.database import Database
    from esports.data.pandascore_client import PandaScoreClient
    from esports.models.esports_trainer import EsportsModelTrainer

    api_key = getattr(settings, "PANDASCORE_API_KEY", None)
    if not api_key:
        print("ERROR: PANDASCORE_API_KEY not set in .env")
        sys.exit(1)

    # Initialize DB
    db = Database()
    await db.init()
    if db.engine is None:
        print("ERROR: DATABASE_URL not set or DB unreachable")
        sys.exit(1)

    # Initialize PandaScore client
    ps = PandaScoreClient(api_key=api_key)
    await ps.init()

    trainer = EsportsModelTrainer(pandascore_client=ps)

    results = {}
    for game in NEW_GAMES:
        print(f"\n{'='*50}")
        print(f"Collecting data for: {game}")
        print(f"{'='*50}")
        try:
            result = await trainer.train_game(
                game=game,
                db=db,
                collect_if_empty=True,
                days_back=90,
            )
            results[game] = result
            print(f"  Samples: {result.get('samples', 0)}")
            print(f"  Error:   {result.get('error', 'none')}")
        except Exception as exc:
            results[game] = {"error": str(exc), "samples": 0}
            print(f"  FAILED: {exc}")

    # Summary
    print(f"\n{'='*50}")
    print("SEED RESULTS")
    print(f"{'='*50}")
    for game, res in results.items():
        samples = res.get("samples", 0)
        error = res.get("error", "")
        status = "OK" if samples > 0 and not error else "FAIL"
        print(f"  {game:12s}  {status:4s}  samples={samples}  {error or ''}")

    # Cleanup
    await ps.close()
    if db.engine is not None:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

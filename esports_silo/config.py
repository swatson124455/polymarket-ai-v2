"""esports_silo configuration — env-driven, no shared-module imports.

Loads from environment (and a local .env if python-dotenv is installed). Keeps
the silo self-contained so it can be lifted into its own repo unchanged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

try:  # optional — .env is convenience, env vars are the source of truth
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _split(name: str, default: str) -> List[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    database_url: str = os.getenv("DATABASE_URL", "")

    oddspapi_api_key: str = os.getenv("ODDSPAPI_API_KEY", "")
    pandascore_api_key: str = os.getenv("PANDASCORE_API_KEY", "")
    riot_api_key: str = os.getenv("RIOT_API_KEY", "")

    polymarket_gamma_api: str = os.getenv(
        "POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com"
    )
    polymarket_clob_api: str = os.getenv(
        "POLYMARKET_CLOB_API", "https://clob.polymarket.com"
    )

    sharp_books: List[str] = field(
        default_factory=lambda: _split("SHARP_BOOKS", "pinnacle,circa,asian")
    )
    games: List[str] = field(
        default_factory=lambda: _split("ESPORTS_GAMES", "cs2,lol,dota2,valorant")
    )

    # Safety: default HALTED. Flip only after the skill gates pass.
    entry_halt: bool = os.getenv("SILO_ENTRY_HALT", "true").lower() in (
        "true",
        "1",
        "yes",
    )


CONFIG = Config()

# OddsPapi game -> sport_id  (verified from the prior client, esports/data/oddspapi_client.py)
ODDSPAPI_SPORT_IDS = {
    "dota2": 16,
    "cs2": 17,
    "lol": 18,
    "cod": 56,
    "rl": 59,
    "valorant": 61,
}

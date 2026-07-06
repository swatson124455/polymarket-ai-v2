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

    # pinnodds — self-serve Pinnacle wrapper; the odds source (replaces OddsPapi,
    # which gates pinnacle to B2B). LIVE-VERIFIED 2026-07-06 on the box: base
    # /kit/v1, header `x-portal-apikey`, esports = sport_id 11 (one /markets call
    # returns every esports event with raw money_line odds; ~20 req/window limit).
    pinnodds_api_key: str = os.getenv("PINNODDS_API_KEY", "")
    pinnodds_base: str = os.getenv("PINNODDS_BASE", "https://pinnodds.com/kit/v1")

    polymarket_gamma_api: str = os.getenv(
        "POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com"
    )
    polymarket_clob_api: str = os.getenv(
        "POLYMARKET_CLOB_API", "https://clob.polymarket.com"
    )

    # Sharp book(s) written to odds_raw. D3 FINAL (2026-07-06): source = pinnodds,
    # book = `pinnacle` (raw). OddsPapi is abandoned — it gated pinnacle to B2B, and
    # singbet/sbobet on it were unverified. pinnodds delivers pinnacle self-serve, so
    # the set returns to the single sharpest benchmark. Consensus over 1 book = raw
    # pinnacle, which is exactly the intended signal (no de-vig, Cmd 2).
    sharp_books: List[str] = field(
        default_factory=lambda: _split("SHARP_BOOKS", "pinnacle")
    )
    games: List[str] = field(
        default_factory=lambda: _split("ESPORTS_GAMES", "cs2,lol,dota2,valorant")
    )

    # Fitted-calibrator artifact (written by scripts/fit_calibrator.py; read by the
    # runner's --predict pass). Missing/unfitted artifact => p_model stays None (Cmd 4).
    calibrator_path: str = os.getenv(
        "CALIBRATOR_PATH", "esports_silo/artifacts/calibrator_sharp_consensus_v1.json"
    )

    # Safety: default HALTED. Flip only after the skill gates pass.
    entry_halt: bool = os.getenv("SILO_ENTRY_HALT", "true").lower() in (
        "true",
        "1",
        "yes",
    )


CONFIG = Config()

# pinnodds: esports is a SINGLE sport_id (11); the game lives in league_name, not a
# per-game sport_id. LIVE-VERIFIED 2026-07-06 (id 11 = "Esports", 28 prematch events).
PINNODDS_ESPORTS_SPORT_ID = 11

# OddsPapi game -> sport_id — RETAINED for the (now-abandoned) OddsPapi collector /
# historical-backfill scripts only. Not used by the pinnodds path.
ODDSPAPI_SPORT_IDS = {
    "dota2": 16,
    "cs2": 17,
    "lol": 18,
    "cod": 56,
    "rl": 59,
    "valorant": 61,
}

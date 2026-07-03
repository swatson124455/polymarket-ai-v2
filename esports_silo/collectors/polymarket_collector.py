#!/usr/bin/env python3
"""Append-only Polymarket snapshot collector for esports_silo → `polymarket_snapshots`.

Records live CLOB observations of esports markets: price (team_a = YES token) and 24h volume.
INSERT-only — every observation is a NEW row, so price/volume movement is preserved and nothing
is ever mutated (mirrors the odds collector).

Enforced here:
  * price/volume are taken from the LIVE API only. The prior bot's STORED liquidity/volume
    columns read $0 on liquid markets and are banned as a source (schema.sql note).
  * game membership is decided by CONTENT, never the `category` tag (Cmd 5 — the old
    `category='esports'` was ~60% politics). `classify_game()` filters by keyword; a market
    whose game can't be identified from its text is SKIPPED, not guessed.
  * per-game coverage is logged EVERY run — a game returning zero markets is a loud WARNING.

Run:
  python -m esports_silo.collectors.polymarket_collector --once
  python -m esports_silo.collectors.polymarket_collector --once --dry-run   # probe only, no writes

SEAM (could not be verified from a network-isolated session): the exact Gamma markets endpoint
path/params and the JSON field names. `_fetch_markets` logs the first raw payload so you confirm
the mapping (`_parse_market` reads several candidate keys defensively). Until confirmed, treat
parsed values as provisional. The parsing logic itself is pure and unit-tested.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# aiohttp + asyncpg are imported lazily inside the fetch/run functions so the PURE parsing
# helpers (classify_game / parse_market / …) import and test with zero network/DB deps.
try:
    import structlog
    log = structlog.get_logger()
except ImportError:  # structlog is a runtime dep; keep the parser importable without it
    log = logging.getLogger("polymarket_collector")

try:
    from ..config import CONFIG
except ImportError:  # allow running as a loose script
    try:
        from config import CONFIG  # type: ignore
    except ImportError:  # parser-only import (no config needed for the pure helpers)
        CONFIG = None  # type: ignore

# Content keywords per game (Cmd 5: classify by content, not the category tag). Conservative:
# an unmatched market is skipped, never guessed into a game.
_GAME_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "cs2": ("cs2", "counter-strike", "counter strike", "csgo", "cs:go", "cs go"),
    "lol": ("league of legends", "lol", "lck", "lpl", "lec", "lcs", "worlds", "msi"),
    "dota2": ("dota", "the international", " ti "),
    "valorant": ("valorant", "vct", "champions tour"),
}


def classify_game(text: str) -> Optional[str]:
    """Best esports game for a market's text, or None if not clearly an esports game.

    Word-ish boundary check so short tokens ('lol', 'ti') don't match inside other words.
    First game with a keyword hit wins (keyword order within a game is longest-first-ish).
    """
    t = f" {(text or '').lower()} "
    for game, kws in _GAME_KEYWORDS.items():
        for kw in kws:
            if kw in t:
                # guard the very short/ambiguous tokens with surrounding non-alnum
                if len(kw.strip()) <= 3:
                    idx = t.find(kw)
                    before = t[idx - 1] if idx > 0 else " "
                    after = t[idx + len(kw)] if idx + len(kw) < len(t) else " "
                    if before.isalnum() or after.isalnum():
                        continue
                return game
    return None


def _as_list(v: Any) -> List[Any]:
    """Gamma returns outcomes/prices as either a JSON string or a real list — normalize."""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_outcomes_prices(market: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[float]]:
    """(team_a, team_b, yes_price) from a market payload. team_a/yes_price = the FIRST outcome.

    SEAM: outcome/price field names confirmed against a live payload. `yes_price` is the price
    of the team_a token (schema: "price of the team_a = YES token").
    """
    outcomes = _as_list(market.get("outcomes"))
    prices = _as_list(market.get("outcomePrices") or market.get("outcome_prices"))
    team_a = str(outcomes[0]).strip() if len(outcomes) >= 1 else None
    team_b = str(outcomes[1]).strip() if len(outcomes) >= 2 else None
    yes_price = _to_float(prices[0]) if len(prices) >= 1 else None
    return team_a, team_b, yes_price


def parse_market(market: Dict[str, Any], snapshot_time: str) -> Optional[Dict[str, Any]]:
    """Parse a Gamma market payload into a `polymarket_snapshots` record, or None if it is
    not a classifiable esports market (Cmd 5) or is missing an id.

    `snapshot_time` is passed in (never `datetime.now()` inside the parser) so the function is
    pure and deterministic for testing.
    """
    market_id = market.get("id") or market.get("conditionId") or market.get("condition_id")
    if not market_id:
        return None
    question = market.get("question") or market.get("title") or ""
    team_a, team_b, yes_price = parse_outcomes_prices(market)
    # classify by content: question + team names (NOT the category tag).
    game = classify_game(" ".join(str(x) for x in (question, team_a or "", team_b or "")))
    if game is None:
        return None  # not a recognizable esports game — skip, do not guess
    volume_24h = _to_float(
        market.get("volume24hr")
        if market.get("volume24hr") is not None
        else market.get("volume_24h") if market.get("volume_24h") is not None
        else market.get("volume24Hr")
    )
    return {
        "market_id": str(market_id),
        "question": str(question) or None,
        "game": game,
        "team_a": team_a,
        "team_b": team_b,
        "yes_price": yes_price,
        "volume_24h": volume_24h,
        "snapshot_time": snapshot_time,
    }


def _ts(v):
    """str → datetime for asyncpg binds (TIMESTAMPTZ rejects strings — same fix as the
    importer's _parse_ts; verified against the real schema in a rolled-back txn)."""
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


_logged_shape = False


async def _fetch_markets(session) -> List[Dict[str, Any]]:
    """SEAM: fetch active markets from the Gamma API. Logs the first raw payload.

    Endpoint path/params UNVERIFIED from the silo — confirm against a live response.
    """
    import aiohttp  # lazy: keeps the pure parser importable without the network dep

    global _logged_shape
    url = f"{CONFIG.polymarket_gamma_api}/markets"
    params = {"active": "true", "closed": "false", "limit": 500}  # VERIFY param names
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                log.warning("gamma non-200", status=r.status)
                return []
            data = await r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("gamma request failed", error=str(e))
        return []
    if not _logged_shape:
        log.info("first raw markets payload — CONFIRM field mapping", sample=str(data)[:800])
        _logged_shape = True
    if isinstance(data, dict):
        data = data.get("data") or data.get("markets") or []
    return data if isinstance(data, list) else []


async def run_once(dry_run: bool) -> None:
    import aiohttp  # lazy network/DB deps (see module header)

    snapshot_time = datetime.now(timezone.utc).isoformat()
    coverage: Dict[str, int] = {}
    pool = None
    if not dry_run:
        import asyncpg
        if not CONFIG.database_url:
            raise SystemExit("DATABASE_URL not set (or use --dry-run)")
        pool = await asyncpg.create_pool(CONFIG.database_url, min_size=1, max_size=2)

    async with aiohttp.ClientSession() as session:
        markets = await _fetch_markets(session)
        log.info("markets fetched", n=len(markets))
        for m in markets:
            rec = parse_market(m, snapshot_time)
            if rec is None:
                continue
            coverage[rec["game"]] = coverage.get(rec["game"], 0) + 1
            if dry_run:
                continue
            async with pool.acquire() as con:
                # APPEND-ONLY: plain INSERT, never UPSERT. snapshot_time parsed to datetime
                # for the bind (the record keeps the ISO string; parser stays pure).
                await con.execute(
                    """INSERT INTO polymarket_snapshots
                       (market_id, question, game, team_a, team_b, yes_price, volume_24h, snapshot_time)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                    rec["market_id"], rec["question"], rec["game"], rec["team_a"],
                    rec["team_b"], rec["yes_price"], rec["volume_24h"], _ts(rec["snapshot_time"]),
                )
    if pool:
        await pool.close()

    # coverage report — a game with zero esports markets is made observable every run.
    log.info("=== coverage (game) -> esports markets ===")
    for game in CONFIG.games:
        n = coverage.get(game, 0)
        (log.info if n else log.warning)("coverage", game=game, markets=n)
    missing = [g for g in CONFIG.games if not coverage.get(g)]
    if missing:
        log.warning("GAMES WITH ZERO MARKETS — verify Polymarket esports coverage", missing=missing)


def main() -> None:
    ap = argparse.ArgumentParser(description="esports_silo append-only Polymarket snapshot collector")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--dry-run", action="store_true", help="probe coverage, no DB writes")
    args = ap.parse_args()
    if not args.once:
        raise SystemExit("only --once is implemented in the scaffold; schedule via cron/timer")
    asyncio.run(run_once(dry_run=args.dry_run))


if __name__ == "__main__":
    main()

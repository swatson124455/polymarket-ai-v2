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


# ── Tag-based esports discovery (LIVE-VERIFIED 2026-07-06 on the box) ───────────────
# Polymarket files esports under numeric tag IDs, reachable via /events?tag_id=. The IDs
# are volatile, so the LOOKUP KEY is the slug (stable public handle), resolved to the live
# numeric ID at RUNTIME from /tags by EXACT match (deterministic, not a keyword guess). An
# unresolved slug is logged loudly and skipped — never approximated. run_once also LOGS the
# resolved id map every pass, so a silent ID change is always visible in the journal.
#
# VERIFIED slug -> id (RECORDED for reference/regression — NOT the lookup, the slug is; if a
# live id differs from this, the tag was renumbered — investigate, don't panic):
#   CS2: counter-strike-2=100780 (the tag the MATCH events actually carry) · cs2=100677 (21
#        events) · counter-strike=100602 + csgo=100635 = legacy TOPIC tags (Valve/meta props
#        only, no matches) — kept as harmless extra coverage. VERIFIED 2026-07-07/08: CS2
#        head-to-heads (TYLOO vs 9z, FaZe vs BetBoom, NAVI Junior, …) live under
#        `counter-strike-2`; adding it took CS2 coverage 0 -> 26 match markets. The earlier
#        counter-strike/csgo-only query MISSED all of them.
#   league-of-legends=65   lol-worlds=401   lec=102164   (2026-07-06)
#   dota-2=102366   ·   valorant=101672   vct=101682     (2026-07-06)
# (Out of our 4-game scope: starcraft-2=103064, starcraft-1=103065.)
POLYMARKET_TAG_SLUGS: Dict[str, Tuple[str, ...]] = {
    "cs2": ("counter-strike-2", "cs2", "counter-strike", "csgo"),
    "lol": ("league-of-legends", "lol-worlds", "lec"),
    "dota2": ("dota-2",),
    "valorant": ("valorant", "vct"),
}

# Recorded verified ids. run_once cross-checks the live resolution against these and warns on any
# drift, so a renumbered tag surfaces instead of silently changing coverage.
POLYMARKET_TAG_IDS_VERIFIED: Dict[str, int] = {
    "counter-strike-2": 100780, "cs2": 100677, "counter-strike": 100602, "csgo": 100635,
    "league-of-legends": 65, "lol-worlds": 401, "lec": 102164,
    "dota-2": 102366, "valorant": 101672, "vct": 101682,
}
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


async def _get_json(session, path: str, params: Dict[str, Any]):
    """GET Gamma JSON, or None on any non-200/error (logged). Never raises."""
    import aiohttp
    url = f"{CONFIG.polymarket_gamma_api}{path}"
    try:
        async with session.get(url, params=params, headers={"user-agent": _UA},
                               timeout=aiohttp.ClientTimeout(total=25)) as r:
            if r.status != 200:
                log.warning("gamma non-200", path=path, status=r.status)
                return None
            return await r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("gamma request failed", path=path, error=str(e))
        return None


async def _resolve_tag_ids(session) -> Dict[str, List[Any]]:
    """game -> [tag_id...] by EXACT slug match against the live /tags list. No guessing: a
    slug that isn't present is logged and skipped, never approximated to a nearby tag."""
    tags: List[Dict[str, Any]] = []
    for page in range(60):
        arr = await _get_json(session, "/tags", {"limit": 100, "offset": page * 100})
        if not isinstance(arr, list) or not arr:
            break
        tags.extend(arr)
        if len(arr) < 100:
            break
    slug_to_id: Dict[str, Any] = {}
    for t in tags:
        s = str(t.get("slug") or "").lower()
        if s and s not in slug_to_id and t.get("id") is not None:
            slug_to_id[s] = t.get("id")
    out: Dict[str, List[Any]] = {}
    for game, slugs in POLYMARKET_TAG_SLUGS.items():
        ids: List[Any] = []
        for s in slugs:
            tid = slug_to_id.get(s)
            if tid is None:
                log.warning("polymarket tag slug not resolved (skipped, not guessed)",
                            game=game, slug=s)
                continue
            # drift check against the recorded verified id — a mismatch means Polymarket
            # renumbered the tag; surface it loudly rather than silently following it.
            want = POLYMARKET_TAG_IDS_VERIFIED.get(s)
            if want is not None and str(tid) != str(want):
                log.warning("polymarket tag id DRIFT from recorded verified value",
                            slug=s, live_id=tid, recorded_id=want)
            ids.append(tid)
        out[game] = ids
    return out


async def _fetch_events_by_tag(session, tag_id) -> List[Dict[str, Any]]:
    """All OPEN events for a tag (paginated)."""
    events: List[Dict[str, Any]] = []
    for page in range(20):
        arr = await _get_json(session, "/events",
                              {"tag_id": tag_id, "closed": "false", "limit": 100, "offset": page * 100})
        if not isinstance(arr, list) or not arr:
            break
        events.extend(arr)
        if len(arr) < 100:
            break
    return events


def _is_yes_no(outcomes: List[Any]) -> bool:
    return {str(o).strip().lower() for o in outcomes} == {"yes", "no"}


def pick_series_market(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The match-winner market of an event: two TEAM outcomes (not Yes/No) whose question is
    the event's base series line. Exact title match wins; otherwise the longest question that
    is a PREFIX of the title (some events append ' - <league>' to the title but not to the
    series-market question, e.g. '... (BO3)' vs '... (BO3) - LPL Group Ascend'). Sub-game
    ('Game N'/'Map N'/'Match Result'/'1x2') and outright Yes/No markets are excluded — those
    never == or prefix the title. Returns None for non-match events (season outrights, props)."""
    title = str(event.get("title") or "").strip()
    if not title:
        return None
    best = None
    best_len = -1
    for m in (event.get("markets") or []):
        q = str(m.get("question") or "").strip()
        outs = _as_list(m.get("outcomes"))
        if len(outs) != 2 or _is_yes_no(outs) or not q:
            continue
        if q == title:
            return m  # exact base line — done
        if title.startswith(q) and len(q) > best_len:
            best, best_len = m, len(q)
    return best


def event_record(event: Dict[str, Any], game: str, market: Dict[str, Any],
                 snapshot_time: str) -> Optional[Dict[str, Any]]:
    """polymarket_snapshots record from a match event + its series market. yes_price = the
    team_a (first outcome) token price. RAW live values; nothing guessed."""
    market_id = market.get("id") or market.get("conditionId")
    outs = _as_list(market.get("outcomes"))
    if not market_id or len(outs) < 2:
        return None
    prices = _as_list(market.get("outcomePrices"))
    return {
        "market_id": str(market_id),
        "question": str(market.get("question") or event.get("title") or "") or None,
        "game": game,
        "team_a": str(outs[0]).strip(),
        "team_b": str(outs[1]).strip(),
        "yes_price": _to_float(prices[0]) if len(prices) >= 1 else None,
        "volume_24h": _to_float(market.get("volume24hr")),
        "snapshot_time": snapshot_time,
    }


async def run_once(dry_run: bool) -> None:
    import aiohttp  # lazy network/DB deps (see module header)

    snapshot_time = datetime.now(timezone.utc).isoformat()
    coverage: Dict[str, int] = {}
    misses: List[str] = []      # events that look like matches but yielded no series market
    settled_skipped = 0         # resolved matches (price pinned to 0/1) — not a live line
    seen: set = set()
    printed: List[Any] = []
    pool = None
    if not dry_run:
        import asyncpg
        if not CONFIG.database_url:
            raise SystemExit("DATABASE_URL not set (or use --dry-run)")
        pool = await asyncpg.create_pool(CONFIG.database_url, min_size=1, max_size=2)

    async with aiohttp.ClientSession() as session:
        tag_ids = await _resolve_tag_ids(session)
        log.info("resolved esports tag ids", tags=tag_ids)
        for game, ids in tag_ids.items():
            for tid in ids:
                for ev in await _fetch_events_by_tag(session, tid):
                    m = pick_series_market(ev)
                    if m is None:
                        title = str(ev.get("title") or "")
                        # only a real miss if it's a head-to-head with no derivative marker
                        low = title.lower()
                        if " vs " in low and not any(
                                k in low for k in ("match result", "1x2", " - game ", " - map ")):
                            misses.append(title)
                        continue
                    rec = event_record(ev, game, m, snapshot_time)
                    if rec is None or rec["market_id"] in seen:
                        continue
                    seen.add(rec["market_id"])
                    # settled matches sit pinned at exactly 0 or 1 — that's a result, not a
                    # tradeable pre-match line; keep them out of the snapshot ledger.
                    if rec["yes_price"] in (0.0, 1.0):
                        settled_skipped += 1
                        continue
                    coverage[game] = coverage.get(game, 0) + 1
                    if dry_run:
                        printed.append((game, rec["team_a"], rec["team_b"], rec["yes_price"]))
                        continue
                    async with pool.acquire() as con:
                        # APPEND-ONLY: plain INSERT, never UPSERT.
                        await con.execute(
                            """INSERT INTO polymarket_snapshots
                               (market_id, question, game, team_a, team_b, yes_price, volume_24h, snapshot_time)
                               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                            rec["market_id"], rec["question"], rec["game"], rec["team_a"],
                            rec["team_b"], rec["yes_price"], rec["volume_24h"], _ts(rec["snapshot_time"]),
                        )
    if pool:
        await pool.close()

    if dry_run and printed:
        print("\n=== current Polymarket esports MATCH markets (price = team_a token) ===")
        print(f"{'game':<9}{'match':<48}{'price':>7}")
        for game, a, b, p in sorted(printed, key=lambda r: r[0]):
            ps = f"{p:.3f}" if isinstance(p, float) else "-"
            print(f"{game:<9}{(a + ' vs ' + b)[:46]:<48}{ps:>7}")
    # coverage report — a game with zero MATCH markets is made observable every run.
    log.info("=== coverage (game) -> match markets ===")
    for game in POLYMARKET_TAG_SLUGS:
        n = coverage.get(game, 0)
        (log.info if n else log.warning)("coverage", game=game, matches=n)
    if settled_skipped:
        log.info("settled matches skipped (price pinned to 0/1, not a live line)", n=settled_skipped)
    if misses:
        log.info("match-looking events with no series market (review, not written)",
                 n=len(misses), sample=misses[:8])
    missing = [g for g in POLYMARKET_TAG_SLUGS if not coverage.get(g)]
    if missing:
        log.warning("GAMES WITH ZERO MATCH MARKETS right now", missing=missing)


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

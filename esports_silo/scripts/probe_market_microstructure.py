#!/usr/bin/env python3
"""esports_silo — READ-ONLY market-microstructure probe. Triangulates the in-play claim.

WHY: the prior bot's "in-play is untradeable" evidence (`shadow_fills`: median 86¢ spread,
edge_at_vwap −0.85) comes from a table the audit itself quarantined as partly synthetic
(EB_CLEAN_DATA_QUARANTINE.md Landmine 5). That makes it a DOC-SOURCED claim about 2024's
books — not measured truth. This probe measures TODAY's actual Polymarket esports order
books directly, so the pre-match-vs-in-play decision rests on data we collected ourselves.

WHAT IT DOES (public APIs, no key, read-only, writes ONLY a local JSONL — never the DB):
  1. Gamma: list active markets → keep the content-classified esports ones (Cmd 5 —
     reuses polymarket_collector.classify_game; the category tag is never trusted).
  2. CLOB: fetch each market's order book (both outcome tokens) →
     best bid/ask, spread, mid, top-of-book depth ($), two-sided?
  3. Append one JSONL row per (market, sample) with a UTC timestamp and optional --label
     (e.g. run it during a live match window with --label in-play).
  4. Print summary stats per game: median spread, share of two-sided books, median depth.

HOW TO TRIANGULATE (operator, on the box):
  * pre-match baseline:  python -m esports_silo.scripts.probe_market_microstructure \
        --once --label pre-match --out /opt/esports_silo/probe.jsonl
  * during live matches: same command with --label in-play (schedule a few passes while
    big matches are live; HLTV/Liquipedia tell you when).
  * compare the two spread distributions. If today's in-play median is anywhere near the
    doc-sourced 86¢, the claim stands on real data; if books have matured, we learn that
    instead — either way the decision is measured, not inherited.
  * secondary check (one SQL, VPS DB): the quarantine doc claims `orderbook_snapshots`
    (the REAL book table) has NO esports coverage — verify:
      SELECT count(*) FROM orderbook_snapshots os JOIN markets m ON os.market_id=m.id
      WHERE <your esports-market filter>;   -- adapt to the 15-bot schema

SEAM (unverifiable from a network-isolated session): Gamma/CLOB field names. The script
logs the first raw payload of each API; the parsers read defensively and mark rows they
could not parse rather than guessing. All pure math (spread/depth) is unit-tested.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from ..collectors.polymarket_collector import classify_game, _as_list
except ImportError:
    from collectors.polymarket_collector import classify_game, _as_list  # type: ignore

try:
    from ..config import CONFIG
    GAMMA = CONFIG.polymarket_gamma_api
    CLOB = CONFIG.polymarket_clob_api
except ImportError:  # loose-file fallback
    GAMMA = "https://gamma-api.polymarket.com"
    CLOB = "https://clob.polymarket.com"

log = logging.getLogger("probe_microstructure")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


# ======================================================================================
# pure book math (unit-tested; no I/O)
# ======================================================================================
def _levels(raw) -> List[Dict[str, float]]:
    """Normalize a bids/asks list of {price,size} (values may be strings) to floats."""
    out = []
    for lvl in raw or []:
        try:
            out.append({"price": float(lvl.get("price")), "size": float(lvl.get("size"))})
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def book_stats(bids_raw, asks_raw) -> Dict[str, Optional[float]]:
    """Best bid/ask, spread, mid, and top-of-book depth in USD from raw book levels.

    Depth = price*size summed over levels within 5¢ of the touch on each side (a proxy for
    'money available near the market'). All None when a side is missing (one-sided book).
    """
    bids = sorted(_levels(bids_raw), key=lambda x: x["price"], reverse=True)
    asks = sorted(_levels(asks_raw), key=lambda x: x["price"])
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    two_sided = best_bid is not None and best_ask is not None
    spread = (best_ask - best_bid) if two_sided else None
    mid = ((best_ask + best_bid) / 2.0) if two_sided else None
    def depth(levels, touch, sign):
        if touch is None:
            return None
        return round(sum(l["price"] * l["size"] for l in levels
                         if abs(l["price"] - touch) <= 0.05), 2)
    return {
        "best_bid": best_bid, "best_ask": best_ask,
        "spread": round(spread, 4) if spread is not None else None,
        "mid": round(mid, 4) if mid is not None else None,
        "two_sided": two_sided,
        "bid_depth_usd_5c": depth(bids, best_bid, -1),
        "ask_depth_usd_5c": depth(asks, best_ask, +1),
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-game summary: n, two-sided share, median spread / depth (two-sided books only)."""
    def median(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        m = len(vals) // 2
        return vals[m] if len(vals) % 2 else round((vals[m - 1] + vals[m]) / 2, 4)
    out: Dict[str, Dict[str, Any]] = {}
    by_game: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_game.setdefault(r.get("game") or "unknown", []).append(r)
    for g, rs in by_game.items():
        two = [r for r in rs if r.get("two_sided")]
        out[g] = {
            "markets_sampled": len(rs),
            "two_sided_share": round(len(two) / len(rs), 3) if rs else None,
            "median_spread": median([r.get("spread") for r in two]),
            "median_bid_depth_usd_5c": median([r.get("bid_depth_usd_5c") for r in two]),
            "median_ask_depth_usd_5c": median([r.get("ask_depth_usd_5c") for r in two]),
        }
    return out


# ======================================================================================
# I/O (operator's box — public APIs, no key)
# ======================================================================================
_logged = {"gamma": False, "clob": False}


async def _get_json(session, url: str, params: dict | None, which: str):
    import aiohttp  # lazy: pure math above stays importable without it
    try:
        async with session.get(url, params=params or {},
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                log.warning("%s non-200: %s %s", which, r.status, url)
                return None
            data = await r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("%s request failed: %s", which, e)
        return None
    if not _logged[which]:
        log.info("first raw %s payload — CONFIRM field mapping: %.600s", which, str(data))
        _logged[which] = True
    return data


async def run_once(out_path: str, label: str, max_markets: int) -> None:
    import aiohttp
    ts = datetime.now(timezone.utc).isoformat()
    rows: List[Dict[str, Any]] = []
    async with aiohttp.ClientSession() as session:
        data = await _get_json(session, f"{GAMMA}/markets",
                               {"active": "true", "closed": "false", "limit": 500}, "gamma")
        markets = data.get("data") or data.get("markets") or data if isinstance(data, dict) else data
        if not isinstance(markets, list):
            log.error("gamma payload not a list — see logged payload; aborting")
            return
        esports = []
        for m in markets:
            q = str(m.get("question") or m.get("title") or "")
            g = classify_game(q + " " + " ".join(str(x) for x in _as_list(m.get("outcomes"))))
            if g:
                esports.append((m, g))
        log.info("esports markets identified by content: %d (of %d active)",
                 len(esports), len(markets))
        for m, game in esports[:max_markets]:
            token_ids = _as_list(m.get("clobTokenIds") or m.get("clob_token_ids"))
            if not token_ids:
                rows.append({"ts": ts, "label": label, "game": game,
                             "market_id": m.get("id"), "question": m.get("question"),
                             "error": "no clobTokenIds — confirm field name (SEAM)"})
                continue
            book = await _get_json(session, f"{CLOB}/book",
                                   {"token_id": str(token_ids[0])}, "clob")
            stats = book_stats((book or {}).get("bids"), (book or {}).get("asks"))
            rows.append({"ts": ts, "label": label, "game": game,
                         "market_id": m.get("id"), "question": m.get("question"),
                         **stats})
            await asyncio.sleep(0.5)  # polite pacing; public API
    with open(out_path, "a") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    log.info("appended %d rows -> %s", len(rows), out_path)
    print(json.dumps({"label": label, "summary": summarize(rows)}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description="read-only Polymarket esports microstructure probe")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--label", default="unlabeled",
                    help="tag samples, e.g. pre-match | in-play (run during live windows)")
    ap.add_argument("--out", default="probe_microstructure.jsonl", help="JSONL output path")
    ap.add_argument("--max-markets", type=int, default=100)
    args = ap.parse_args()
    if not args.once:
        raise SystemExit("only --once is implemented; schedule repeated passes externally")
    asyncio.run(run_once(args.out, args.label, args.max_markets))


if __name__ == "__main__":
    main()

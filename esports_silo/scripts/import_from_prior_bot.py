#!/usr/bin/env python3
"""Import surviving data from the prior esports bot into the esports_silo schema.

Shapes + the winner-mapping transform were pulled surgically from the prior code
(no sample data needed):
  * esports_matches DDL          -> schema/migrations/072_esports_v2.sql
  * esports_matches_bulk.jsonl   -> esports_v2/scripts/load_matches_to_db.py
  * esports_team_aliases DDL     -> schema/migrations/074_esports_team_aliases.sql
  * winner (team name) -> a/b    -> esports_v2/data/normalizer.raw_to_match_result

This script only MOVES existing, verified data — it does not fabricate or model.

Sources (pick any subset):
  --matches-from-db      SELECT esports_matches  from $SOURCE_DATABASE_URL
  --matches-from-jsonl F read NDJSON file F (same shape as the bulk loader)
  --aliases-from-db      SELECT esports_team_aliases from $SOURCE_DATABASE_URL

Targets the silo DB at $DATABASE_URL. Idempotent (ON CONFLICT DO NOTHING).
Run with --dry-run to count without writing.

  SOURCE_DATABASE_URL=postgresql://... DATABASE_URL=postgresql://...silo \
      python -m esports_silo.scripts.import_from_prior_bot --matches-from-db --aliases-from-db
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

try:
    from ..config import CONFIG
except ImportError:
    from config import CONFIG  # type: ignore


def map_winner(winner, team_a, team_b):
    """Team-name winner -> 'team_a' | 'team_b' | None.

    ADAPTED (not a verbatim port) from esports_v2/data/normalizer.raw_to_match_result.
    Same intent (strip + exact match + substring fallback), but per COMMANDMENT 3 we do
    NOT reproduce the source's silent guess: the source defaults unresolved/missing
    winners to 'a' (contaminates labels) — here they return None. Also substring-checks
    BOTH teams, not just team_b. Flagged DEVIATION; see COMMANDMENTS.md.
    """
    if not winner:
        return None
    w = str(winner).strip()
    a = str(team_a or "").strip()
    b = str(team_b or "").strip()
    if w == a:
        return "team_a"
    if w == b:
        return "team_b"
    # substring fallback (matches the prior logic's intent)
    wl = w.lower()
    if b and wl in b.lower():
        return "team_b"
    if a and wl in a.lower():
        return "team_a"
    return None  # unresolved — better NULL than a guessed label


async def _insert_matches(dst, rows, dry_run):
    imported = skipped = unresolved = 0
    for r in rows:
        match_id = r.get("match_id")
        if not match_id:
            skipped += 1
            continue
        winner = map_winner(r.get("winner"), r.get("team_a"), r.get("team_b"))
        if r.get("winner") and winner is None:
            unresolved += 1
        if dry_run:
            imported += 1
            continue
        raw = r.get("raw_data")
        if raw is None:
            raw = "{}"
        elif not isinstance(raw, str):        # asyncpg may hand back a dict; JSONB wants text
            raw = json.dumps(raw, default=str)
        status = await dst.execute(
            """INSERT INTO matches
                 (match_id, game, event_tier, team_a, team_b, winner,
                  score_a, score_b, best_of, start_time, patch, source, raw_data)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
               ON CONFLICT (match_id) DO NOTHING""",
            str(match_id), r.get("game") or "unknown", r.get("event_tier"),
            r.get("team_a") or "?", r.get("team_b") or "?", winner,
            r.get("score_a"), r.get("score_b"), r.get("best_of"),
            r.get("match_date") or r.get("start_time"),
            r.get("patch"), r.get("source") or "prior_bot", raw,
        )
        imported += 1 if status.endswith("1") else 0
        skipped += 0 if status.endswith("1") else 1
    return imported, skipped, unresolved


async def run(args):
    if not CONFIG.database_url:
        raise SystemExit("DATABASE_URL (silo) not set")
    dst = None if args.dry_run else await asyncpg.connect(CONFIG.database_url)
    src = None
    if args.matches_from_db or args.aliases_from_db:
        src_url = os.getenv("SOURCE_DATABASE_URL")
        if not src_url:
            raise SystemExit("SOURCE_DATABASE_URL not set (needed for --*-from-db)")
        src = await asyncpg.connect(src_url)

    try:
        if args.matches_from_jsonl:
            with open(args.matches_from_jsonl) as f:
                rows = [json.loads(ln) for ln in f if ln.strip()]
            imp, skp, unres = await _insert_matches(dst, rows, args.dry_run)
            print(f"[jsonl] matches: +{imp} skipped {skp} unresolved-winner {unres}")

        if args.matches_from_db:
            rows = [dict(r) for r in await src.fetch(
                "SELECT match_id, game, event_tier, team_a, team_b, winner, "
                "score_a, score_b, best_of, match_date, patch, source, raw_data "
                "FROM esports_matches")]
            imp, skp, unres = await _insert_matches(dst, rows, args.dry_run)
            print(f"[db] matches: +{imp} skipped {skp} unresolved-winner {unres}")

        if args.aliases_from_db:
            arows = await src.fetch(
                "SELECT canonical_name, alias, game FROM esports_team_aliases")
            imp = coll = 0
            for a in arows:
                if args.dry_run:
                    imp += 1
                    continue
                status = await dst.execute(
                    """INSERT INTO team_aliases (alias, canonical, game)
                       VALUES ($1,$2,$3) ON CONFLICT (alias, game) DO NOTHING""",
                    a["alias"], a["canonical_name"], a["game"] or "",
                )
                if status.endswith("1"):
                    imp += 1
                else:
                    coll += 1  # alias already mapped for this game (ambiguous source)
            print(f"[db] aliases: +{imp} collisions {coll} "
                  f"(collisions = alias mapped to >1 canonical for a game — review)")
    finally:
        if src:
            await src.close()
        if dst:
            await dst.close()


def main():
    ap = argparse.ArgumentParser(description="Import prior-bot data into esports_silo")
    ap.add_argument("--matches-from-db", action="store_true")
    ap.add_argument("--matches-from-jsonl", metavar="FILE")
    ap.add_argument("--aliases-from-db", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="count only, no writes")
    args = ap.parse_args()
    if not (args.matches_from_db or args.matches_from_jsonl or args.aliases_from_db):
        raise SystemExit("nothing to do — pick a --*-from-* source")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

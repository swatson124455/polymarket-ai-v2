#!/usr/bin/env python3
"""S195: seed esports_team_aliases from PandaScore matches + Polymarket markets.

Two passes:

  1. **Identity pass.** For every distinct (team_name, game) appearing in
     `esports_matches`, register a canonical_name → itself row with
     source='pandascore'. Establishes the canonical-name set.

  2. **Fuzzy-link pass.** For every PandaScore canonical name and every
     active Polymarket esports market question:
       - Skip if the canonical name is already a substring of the question
         (the matcher's stage-1 substring path will already win).
       - Otherwise compute rapidfuzz.token_set_ratio(canonical, question).
       - If score >= MIN_FUZZY_LINK and the question contains a candidate
         variant (any capitalized run of 2+ words within the question),
         add `(canonical_name, candidate_variant, source='fuzzy_link', confidence=score/100)`.
     The variant extraction is deliberately conservative — the seed table
     is for high-confidence variants only. Lower-confidence near-misses
     should land in `esports_unmatched_predictions` and get reviewed by a
     human, not auto-promoted to aliases.

Idempotent: bulk_upsert_team_aliases uses ON CONFLICT DO NOTHING on the
unique constraint (canonical_name, alias, game). Safe to re-run.

Usage::

    PYTHONPATH=/opt/polymarket-ai-v2 \
      /opt/pa2-shared/venv/bin/python3 scripts/seed_esports_team_aliases.py

    # Dry-run (count rows without writing)
    PYTHONPATH=/opt/polymarket-ai-v2 \
      /opt/pa2-shared/venv/bin/python3 scripts/seed_esports_team_aliases.py --dry-run

    # Restrict to one game
    PYTHONPATH=/opt/polymarket-ai-v2 \
      /opt/pa2-shared/venv/bin/python3 scripts/seed_esports_team_aliases.py --game cs2
"""
import argparse
import asyncio
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv

load_dotenv()

# Tunables
MIN_FUZZY_LINK = 85.0   # rapidfuzz token_set_ratio threshold for auto-aliases
MIN_TEAM_NAME_LEN = 2   # skip suspicious 1-char team names

# Variant extractors — multiple patterns target the major team-name
# shapes Polymarket uses in question text:
#   (a) Multi-word capitalized orgs:  "Hanwha Life Esports", "KT Rolster",
#                                     "Aalborg Esport", "Fire Flux Esports"
#   (b) All-caps acronyms (2-6 chars, with digits): "T1", "G2", "DRX",
#                                                   "FNC", "GSE", "C9"
#   (c) Dotted names:                 "Gen.G", "devils.one", "OG.Seed"
# Pattern (b) and (c) added in Bug 2 fix — pre-fix the script missed
# single-word and dotted team names entirely (LoL "T1" never matched
# any variant; the canonical was extracted but no Polymarket-side form
# was learned). Per-pattern shape is conservative; common-word false
# positives like "USA"/"PRO" survive extraction but are filtered downstream
# by the MIN_FUZZY_LINK threshold against canonical PandaScore names.
_VARIANT_PATTERNS = [
    re.compile(r"\b(?:[A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+){1,4})\b"),  # (a)
    re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b"),                             # (b)
    re.compile(r"\b[A-Za-z][a-zA-Z0-9]+\.[A-Za-z][a-zA-Z0-9]*\b"),    # (c)
]


async def _load_pandascore_teams(db, game: Optional[str]) -> Dict[str, Set[str]]:
    """Distinct team names per game from `esports_matches`."""
    from sqlalchemy import text

    teams: Dict[str, Set[str]] = {}
    async with db.get_session() as s:
        if game:
            r = await s.execute(text("""
                SELECT game, team_a FROM esports_matches WHERE game = :g
                UNION
                SELECT game, team_b FROM esports_matches WHERE game = :g
            """), {"g": game})
        else:
            r = await s.execute(text("""
                SELECT game, team_a FROM esports_matches
                UNION
                SELECT game, team_b FROM esports_matches
            """))
        for g, name in r.fetchall():
            if not name or len(name) < MIN_TEAM_NAME_LEN:
                continue
            teams.setdefault(g, set()).add(name.strip())
    return teams


async def _load_polymarket_esports_questions(
    db, game: Optional[str],
) -> List[str]:
    """Active Polymarket esports market questions.

    Polymarket miscategorizes esports markets as 'sports', 'crypto', and
    other tags — the runtime matcher in esports/markets/esports_market_service.py
    works around this by filtering on a keyword set rather than category.
    The seed loader must mirror that universe; otherwise ~half the
    esports-keyword-matching markets (sports/crypto-tagged) never reach
    the variant extractor and we under-seed aliases.

    KEEP IN SYNC with esports_market_service.py:178-198. If the matcher's
    keyword list changes, update this WHERE clause too.
    """
    from sqlalchemy import text

    async with db.get_session() as s:
        if game:
            # Best-effort filter — pull all esports questions and let the
            # caller game-filter via the keyword set in the scanner.
            r = await s.execute(text("""
                SELECT question FROM markets
                WHERE active = TRUE
                  AND question IS NOT NULL
                  AND (
                    question ILIKE '%esports%'
                    OR question ILIKE '%league of legends%'
                    OR question ILIKE '%counter-strike%'
                    OR question ILIKE '%cs2%'
                    OR question ILIKE '%csgo%'
                    OR question ILIKE '%blast premier%'
                    OR question ILIKE '%dota%'
                    OR question ILIKE '%the international%'
                    OR question ILIKE '%valorant%'
                    OR question ILIKE '%champions tour%'
                    OR question ILIKE '%call of duty%'
                    OR question ILIKE '%rainbow six%'
                    OR question ILIKE '%six invitational%'
                    OR question ILIKE '%starcraft%'
                    OR question ILIKE '%sc2%'
                    OR question ILIKE '%brood war%'
                    OR question ILIKE '%rocket league%'
                    OR question ILIKE '%rlcs%'
                    OR question ~* '\\y(lol|lck|lec|lpl|lcs|msi|esl|pgl|iem|dpc|cdl|gsl|asl|vct|r6|cod|ti)\\y'
                  )
            """))
        else:
            r = await s.execute(text("""
                SELECT question FROM markets
                WHERE active = TRUE
                  AND question IS NOT NULL
                  AND (
                    question ILIKE '%esports%'
                    OR question ILIKE '%league of legends%'
                    OR question ILIKE '%counter-strike%'
                    OR question ILIKE '%cs2%'
                    OR question ILIKE '%csgo%'
                    OR question ILIKE '%blast premier%'
                    OR question ILIKE '%dota%'
                    OR question ILIKE '%the international%'
                    OR question ILIKE '%valorant%'
                    OR question ILIKE '%champions tour%'
                    OR question ILIKE '%call of duty%'
                    OR question ILIKE '%rainbow six%'
                    OR question ILIKE '%six invitational%'
                    OR question ILIKE '%starcraft%'
                    OR question ILIKE '%sc2%'
                    OR question ILIKE '%brood war%'
                    OR question ILIKE '%rocket league%'
                    OR question ILIKE '%rlcs%'
                    OR question ~* '\\y(lol|lck|lec|lpl|lcs|msi|esl|pgl|iem|dpc|cdl|gsl|asl|vct|r6|cod|ti)\\y'
                  )
            """))
        return [str(row[0]) for row in r.fetchall()]


def _extract_variants(question: str) -> List[str]:
    """Return candidate alias variants from a question.

    Runs every shape-pattern in _VARIANT_PATTERNS over the question and
    deduplicates while preserving discovery order. The patterns target
    multi-word org names, all-caps acronyms, and dotted names — see the
    _VARIANT_PATTERNS comment for shape examples. Common-word false
    positives are filtered downstream by the MIN_FUZZY_LINK threshold.
    """
    if not question:
        return []
    seen_set: Set[str] = set()
    out: List[str] = []
    for pattern in _VARIANT_PATTERNS:
        for v in pattern.findall(question):
            if v not in seen_set:
                seen_set.add(v)
                out.append(v)
    return out


def _build_identity_rows(
    pandascore_teams: Dict[str, Set[str]],
) -> List[Dict[str, Any]]:
    """Identity rows: every PandaScore name maps to itself."""
    rows = []
    for game, names in pandascore_teams.items():
        for name in names:
            rows.append({
                "canonical_name": name,
                "alias": name,
                "source": "pandascore",
                "confidence": 1.0,
                "game": game,
            })
    return rows


def _build_fuzzy_link_rows(
    pandascore_teams: Dict[str, Set[str]],
    polymarket_questions: List[str],
) -> List[Dict[str, Any]]:
    """Cross-link rows: high-confidence alias variants from question text."""
    try:
        from rapidfuzz import fuzz
    except ImportError:
        print("rapidfuzz not available — skipping fuzzy-link pass", file=sys.stderr)
        return []

    rows: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()  # (canonical, alias, game)

    for game, names in pandascore_teams.items():
        for canonical in names:
            canonical_lc = canonical.lower()
            for q in polymarket_questions:
                q_lc = q.lower()
                # Skip the easy substring case — matcher handles it directly.
                if canonical_lc in q_lc:
                    continue
                score = fuzz.token_set_ratio(canonical_lc, q_lc)
                if score < MIN_FUZZY_LINK:
                    continue
                # Pull candidate variants from the question and pick the
                # one with the highest fuzzy score against the canonical.
                variants = _extract_variants(q)
                if not variants:
                    continue
                best_variant = None
                best_variant_score = 0.0
                for v in variants:
                    s = fuzz.token_set_ratio(canonical_lc, v.lower())
                    if s > best_variant_score:
                        best_variant_score = s
                        best_variant = v
                if not best_variant or best_variant_score < MIN_FUZZY_LINK:
                    continue
                key = (canonical, best_variant, game)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "canonical_name": canonical,
                    "alias": best_variant,
                    "source": "fuzzy_link",
                    "confidence": round(best_variant_score / 100.0, 4),
                    "game": game,
                })
    return rows


async def main(args: argparse.Namespace) -> int:
    from base_engine.data.database import Database

    db = Database()
    await db.init()

    print("Loading PandaScore teams from esports_matches…")
    pandascore_teams = await _load_pandascore_teams(db, args.game)
    total_teams = sum(len(s) for s in pandascore_teams.values())
    print(f"  found {total_teams} distinct teams across {len(pandascore_teams)} games")

    print("Loading active Polymarket esports questions…")
    questions = await _load_polymarket_esports_questions(db, args.game)
    print(f"  found {len(questions)} questions")

    print("Building identity rows (canonical → self)…")
    identity_rows = _build_identity_rows(pandascore_teams)
    print(f"  {len(identity_rows)} identity rows")

    if args.no_fuzzy:
        print("Skipping fuzzy-link pass (--no-fuzzy)")
        fuzzy_rows = []
    else:
        print(f"Building fuzzy-link rows (token_set_ratio >= {MIN_FUZZY_LINK})…")
        fuzzy_rows = _build_fuzzy_link_rows(pandascore_teams, questions)
        print(f"  {len(fuzzy_rows)} fuzzy-link rows")

    if args.dry_run:
        print("\n--dry-run — not writing.")
        # Show a sample of fuzzy rows so the operator can spot bogus links
        for r in fuzzy_rows[:20]:
            print(f"  [{r['game']}] {r['canonical_name']!r} → "
                  f"{r['alias']!r}  conf={r['confidence']}")
        await db.close()
        return 0

    print("\nUpserting…")
    n1 = await db.bulk_upsert_team_aliases(identity_rows)
    print(f"  identity inserted: {n1}")
    n2 = await db.bulk_upsert_team_aliases(fuzzy_rows)
    print(f"  fuzzy_link inserted: {n2}")
    print(f"  total new rows: {n1 + n2}")
    await db.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", default=None,
                        help="Restrict to one game (lol, cs2, dota2, valorant, …)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute rows but don't write to DB")
    parser.add_argument("--no-fuzzy", action="store_true",
                        help="Skip the fuzzy-link pass — identity rows only. "
                             "Recommended for initial seed; fuzzy can be re-run "
                             "later with manual review of borderline matches.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args)))

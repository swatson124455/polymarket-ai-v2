#!/usr/bin/env python3
"""esports_silo — read-only DATA-QUALITY BATTERY (the Commandment-4 MASTER GATE).

Per COMMANDMENTS.md §4 (QUARANTINE BY DEFAULT): every carried asset is excluded
from training, features, and decisions until it proves itself clean **on real data**
by passing this battery. This script IS that proof. Nothing here mutates state — it
runs every check inside a READ ONLY transaction and only ever SELECTs.

  ⛔ This silo has NO DB/API/network access. This script must be run BY THE OPERATOR
     on the box that holds the silo DB. The author has NOT run it and claims no result.

Two input modes (both read-only):
  * DB mode (default): battery against the silo Postgres ($DATABASE_URL) — the master
    gate. Certifies the carried `matches` + `team_aliases` and any populated
    forward tables (`odds_raw`, `polymarket_snapshots`, `predictions`).
  * File mode (--jsonl FILE): pre-import vetting of a raw NDJSON matches dump
    (same shape as scripts/import_from_prior_bot.py) before it ever reaches the DB.

Checks (task-scoped): null-rate · duplicate match_id · temporal/look-ahead integrity ·
winner-resolvability · cross-source winner agreement · quarantine-leak.

Verdicts:
  PASS   check ran; invariant holds.
  WARN   check ran; a non-fatal concern is surfaced (does NOT block the gate, but the
         number is reported so the operator judges it).
  FAIL   check ran; invariant violated → data STAYS quarantined.
  EMPTY  a *carried* table has nothing to certify → cannot clear it → blocks the gate.
  SKIP   a *forward* table is empty/absent → nothing carried to gate there yet (not a fail).
  ERROR  the check could not run (missing table, bad SQL) → blocks the gate ("unsure = out").

GATE = PASS  →  exit 0  →  the checked data may LEAVE quarantine.
GATE = QUARANTINE (any FAIL/EMPTY/ERROR)  →  exit 1  →  everything stays excluded.

Exit codes: 0 gate pass · 1 gate quarantine · 2 could not run (no DATABASE_URL / no asyncpg / bad file).

Usage:
  DATABASE_URL=postgresql://…/silo  python -m esports_silo.scripts.verify_data_quality
  DATABASE_URL=…  python -m esports_silo.scripts.verify_data_quality --json
  python -m esports_silo.scripts.verify_data_quality --jsonl data/esports_matches_bulk.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# --- silo-internal imports only (Cmd 3: never import from the 15-bot system) ----------
try:  # winner-name -> 'team_a'|'team_b'|None (the documented DEVIATION mapper)
    from .import_from_prior_bot import map_winner  # noqa: F401
except Exception:  # pragma: no cover - allow running as a loose file
    try:
        from import_from_prior_bot import map_winner  # type: ignore
    except Exception:
        # self-contained fallback mirroring scripts/import_from_prior_bot.map_winner.
        # provenance: esports_silo/scripts/import_from_prior_bot.py (same silo).
        def map_winner(winner, team_a, team_b):  # type: ignore
            if not winner:
                return None
            w = str(winner).strip()
            a = str(team_a or "").strip()
            b = str(team_b or "").strip()
            if w == a:
                return "team_a"
            if w == b:
                return "team_b"
            wl = w.lower()
            if b and wl in b.lower():
                return "team_b"
            if a and wl in a.lower():
                return "team_a"
            return None

# ---------------------------------------------------------------------------
# Thresholds — explicit and env-overridable. These are the ONLY judgment knobs;
# every other check asserts a hard invariant. Defaults are conservative.
# ---------------------------------------------------------------------------
# Fraction of *past* matches (start_time older than the grace window) allowed to
# have a NULL winner before winner-resolvability FAILs. Unresolved labels are the
# training signal; too many missing = the label set can't be trusted.
WINNER_NULL_MAX_PAST = float(os.getenv("VDQ_WINNER_NULL_MAX_PAST", "0.35"))
# A match older than this many days with no winner is treated as "should be settled".
SETTLE_GRACE_DAYS = int(os.getenv("VDQ_SETTLE_GRACE_DAYS", "1"))
# Sample size printed for offending rows (full counts are always reported).
SAMPLE = int(os.getenv("VDQ_SAMPLE", "10"))

# Carried assets: empty == cannot certify == blocks the gate.
CARRIED_TABLES = ("matches", "team_aliases")
# Forward-collected assets: empty == nothing to gate yet == SKIP (not a failure).
FORWARD_TABLES = ("odds_raw", "polymarket_snapshots", "predictions")
ALL_TABLES = CARRIED_TABLES + FORWARD_TABLES


# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    name: str
    verdict: str = "PASS"           # PASS|WARN|FAIL|EMPTY|SKIP|ERROR
    summary: str = ""
    details: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "verdict": self.verdict,
            "summary": self.summary,
            "details": self.details,
            "metrics": self.metrics,
        }


# Verdicts that keep data quarantined (block the gate).
_BLOCKING = {"FAIL", "EMPTY", "ERROR"}


def _gate_pass(results: List[CheckResult]) -> bool:
    return not any(r.verdict in _BLOCKING for r in results)


# ===========================================================================
# DB mode
# ===========================================================================
async def _table_exists(conn, table: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass($1) IS NOT NULL", f"public.{table}"
    ))


async def _count(conn, table: str) -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table}"))


async def check_schema_presence(conn) -> CheckResult:
    r = CheckResult("schema_presence")
    missing = [t for t in ALL_TABLES if not await _table_exists(conn, t)]
    r.metrics["missing"] = missing
    if not missing:
        r.summary = f"all {len(ALL_TABLES)} expected tables present"
        return r
    # A missing CARRIED table is fatal; a missing forward table is only a note.
    fatal = [t for t in missing if t in CARRIED_TABLES]
    r.verdict = "ERROR" if fatal else "WARN"
    r.summary = f"missing tables: {', '.join(missing)}"
    r.details.append(
        f"carried tables missing (fatal): {fatal or 'none'}; "
        f"forward tables missing: {[t for t in missing if t in FORWARD_TABLES] or 'none'}"
    )
    return r


async def check_row_counts(conn) -> CheckResult:
    r = CheckResult("row_counts")
    counts = {}
    for t in ALL_TABLES:
        counts[t] = await _count(conn, t) if await _table_exists(conn, t) else None
    r.metrics["counts"] = counts
    r.summary = " · ".join(f"{t}={counts[t]}" for t in ALL_TABLES)
    empty_carried = [t for t in CARRIED_TABLES if counts.get(t) == 0]
    if empty_carried:
        r.verdict = "EMPTY"
        r.details.append(
            f"carried table(s) empty — nothing to certify, so they cannot leave "
            f"quarantine: {', '.join(empty_carried)}"
        )
    for t in FORWARD_TABLES:
        if counts.get(t) == 0:
            r.details.append(f"{t} empty — forward-collected, nothing to gate yet (SKIP)")
    return r


async def check_null_rate(conn) -> CheckResult:
    """Null-rate on required columns. Schema marks several NOT NULL — verify reality."""
    r = CheckResult("null_rate")
    if await _count(conn, "matches") == 0:
        r.verdict = "EMPTY"
        r.summary = "matches empty"
        return r
    row = await conn.fetchrow(
        """SELECT count(*) AS total,
                  count(*) FILTER (WHERE game       IS NULL) AS game_null,
                  count(*) FILTER (WHERE team_a     IS NULL) AS team_a_null,
                  count(*) FILTER (WHERE team_b     IS NULL) AS team_b_null,
                  count(*) FILTER (WHERE start_time IS NULL) AS start_time_null,
                  count(*) FILTER (WHERE source     IS NULL) AS source_null,
                  count(*) FILTER (WHERE winner     IS NULL) AS winner_null
             FROM matches"""
    )
    m = dict(row)
    total = m["total"]
    r.metrics["matches"] = m
    # Required columns must be 100% populated.
    required = ("game_null", "team_a_null", "team_b_null", "start_time_null", "source_null")
    violated = {k: m[k] for k in required if m[k]}
    if violated:
        r.verdict = "FAIL"
        r.details.append(f"NOT NULL columns with NULLs (schema-violating): {violated}")
    # winner NULL is allowed (unresolved) — report as an informational rate.
    wr = m["winner_null"] / total if total else 0.0
    r.metrics["winner_null_rate"] = round(wr, 4)
    r.details.append(f"winner NULL rate = {wr:.1%} ({m['winner_null']}/{total}) "
                     f"[allowed; judged in winner_resolvability]")

    # team_aliases required columns.
    if await _table_exists(conn, "team_aliases") and await _count(conn, "team_aliases") > 0:
        arow = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE alias     IS NULL OR alias='')     AS alias_bad,
                      count(*) FILTER (WHERE canonical IS NULL OR canonical='') AS canon_bad
                 FROM team_aliases"""
        )
        r.metrics["team_aliases"] = dict(arow)
        if arow["alias_bad"] or arow["canon_bad"]:
            r.verdict = "FAIL"
            r.details.append(f"team_aliases blank alias/canonical: {dict(arow)}")

    # odds_raw price nulls (if populated) — WARN only (a book may not price a side).
    if await _table_exists(conn, "odds_raw") and await _count(conn, "odds_raw") > 0:
        orow = await conn.fetchrow(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE team_a_odds IS NULL) AS a_null,
                      count(*) FILTER (WHERE team_b_odds IS NULL) AS b_null,
                      count(*) FILTER (WHERE line_time   IS NULL) AS lt_null
                 FROM odds_raw"""
        )
        r.metrics["odds_raw"] = dict(orow)
        if orow["lt_null"]:
            r.verdict = "FAIL"
            r.details.append(f"odds_raw rows with NULL line_time (look-ahead anchor): {orow['lt_null']}")
        if orow["a_null"] or orow["b_null"]:
            if r.verdict == "PASS":
                r.verdict = "WARN"
            r.details.append(f"odds_raw NULL prices: a={orow['a_null']} b={orow['b_null']} "
                             f"of {orow['total']} [a book may omit a side]")

    r.summary = f"required cols {'clean' if r.verdict in ('PASS','WARN') else 'VIOLATED'}; " \
                f"winner NULL {wr:.1%}"
    return r


async def check_duplicate_match_id(conn) -> CheckResult:
    r = CheckResult("duplicate_match_id")
    if await _count(conn, "matches") == 0:
        r.verdict = "EMPTY"
        r.summary = "matches empty"
        return r
    # Exact PK duplicates — must be 0 (match_id is PRIMARY KEY). >0 == corruption.
    dups = await conn.fetch(
        "SELECT match_id, count(*) c FROM matches GROUP BY match_id HAVING count(*) > 1 "
        f"ORDER BY c DESC LIMIT {SAMPLE}"
    )
    total_dup = int(await conn.fetchval(
        "SELECT count(*) FROM (SELECT match_id FROM matches GROUP BY match_id "
        "HAVING count(*) > 1) q"
    ))
    r.metrics["exact_dup_match_ids"] = total_dup
    if total_dup:
        r.verdict = "FAIL"
        r.details.append(f"{total_dup} match_id value(s) appear >1x (PK invariant broken): "
                         + ", ".join(f"{d['match_id']}×{d['c']}" for d in dups))
    # Logical near-duplicates: same game+teams+day under different match_ids. WARN
    # (could double-count a match into training) — sources/ids differ legitimately too.
    logical = await conn.fetch(
        """SELECT game, start_time::date AS d,
                  LEAST(lower(team_a), lower(team_b))    AS t1,
                  GREATEST(lower(team_a), lower(team_b)) AS t2,
                  count(DISTINCT match_id) AS ids
             FROM matches
            GROUP BY game, start_time::date,
                     LEAST(lower(team_a), lower(team_b)),
                     GREATEST(lower(team_a), lower(team_b))
           HAVING count(DISTINCT match_id) > 1
            ORDER BY ids DESC
            LIMIT %d""" % SAMPLE
    )
    logical_total = int(await conn.fetchval(
        """SELECT count(*) FROM (
             SELECT 1 FROM matches
              GROUP BY game, start_time::date,
                       LEAST(lower(team_a), lower(team_b)),
                       GREATEST(lower(team_a), lower(team_b))
             HAVING count(DISTINCT match_id) > 1) q"""
    ))
    r.metrics["logical_dup_groups"] = logical_total
    if logical_total:
        if r.verdict == "PASS":
            r.verdict = "WARN"
        r.details.append(
            f"{logical_total} logical group(s) (game+teams+day) span >1 match_id — "
            f"possible double-count; review. e.g. "
            + "; ".join(f"{g['game']}/{g['t1']}-{g['t2']} {g['d']} ×{g['ids']}" for g in logical)
        )
    r.summary = f"exact dup match_id={total_dup}; logical dup groups={logical_total}"
    return r


async def check_temporal_lookahead(conn) -> CheckResult:
    """Look-ahead defense: no fact may be 'known' before it could exist."""
    r = CheckResult("temporal_lookahead")
    checked = False

    if await _count(conn, "matches") > 0:
        checked = True
        # A future-dated match cannot already have a winner.
        future_win = int(await conn.fetchval(
            "SELECT count(*) FROM matches WHERE start_time > now() AND winner IS NOT NULL"
        ))
        r.metrics["future_match_with_winner"] = future_win
        if future_win:
            r.verdict = "FAIL"
            r.details.append(f"{future_win} match(es) start in the future yet already carry a "
                             f"winner (result-before-event contamination)")

    # odds observed AFTER the match started are not pre-match signal.
    if await _table_exists(conn, "odds_raw") and await _count(conn, "odds_raw") > 0:
        checked = True
        post = int(await conn.fetchval(
            """SELECT count(*) FROM odds_raw o JOIN matches m USING (match_id)
                WHERE o.line_time > m.start_time"""
        ))
        r.metrics["odds_after_match_start"] = post
        if post:
            r.verdict = "FAIL"
            r.details.append(f"{post} odds_raw row(s) have line_time AFTER the match start "
                             f"(cannot be a pre-match line)")

    # a forecast whose ingest_time is at/after match start could have seen the result.
    if await _table_exists(conn, "predictions") and await _count(conn, "predictions") > 0:
        checked = True
        peek = int(await conn.fetchval(
            "SELECT count(*) FROM predictions WHERE ingest_time > event_time"
        ))
        r.metrics["predictions_made_after_event"] = peek
        if peek:
            r.verdict = "FAIL"
            r.details.append(f"{peek} prediction(s) were ingested AFTER their event_time "
                             f"(look-ahead: forecast could see the outcome)")
        mism = int(await conn.fetchval(
            """SELECT count(*) FROM predictions p JOIN matches m USING (match_id)
                WHERE p.event_time <> m.start_time"""
        ))
        r.metrics["prediction_event_time_mismatch"] = mism
        if mism:
            if r.verdict == "PASS":
                r.verdict = "WARN"
            r.details.append(f"{mism} prediction(s) have event_time != the match start_time "
                             f"(look-ahead anchor drift; verify)")

    if not checked:
        r.verdict = "SKIP"
        r.summary = "no temporal rows to check (matches/odds/predictions empty)"
    else:
        r.summary = ("no look-ahead detected" if r.verdict in ("PASS", "WARN")
                     else "LOOK-AHEAD CONTAMINATION")
    return r


async def check_winner_resolvability(conn) -> CheckResult:
    """Labels must be resolvable, valid, and consistent with the score."""
    r = CheckResult("winner_resolvability")
    total = await _count(conn, "matches")
    if total == 0:
        r.verdict = "EMPTY"
        r.summary = "matches empty"
        return r
    # 1) winner domain must be {'team_a','team_b',NULL}.
    bad_domain = int(await conn.fetchval(
        "SELECT count(*) FROM matches WHERE winner IS NOT NULL "
        "AND winner NOT IN ('team_a','team_b')"
    ))
    r.metrics["invalid_winner_domain"] = bad_domain
    if bad_domain:
        r.verdict = "FAIL"
        sample = await conn.fetch(
            f"SELECT match_id, winner FROM matches WHERE winner IS NOT NULL "
            f"AND winner NOT IN ('team_a','team_b') LIMIT {SAMPLE}"
        )
        r.details.append(f"{bad_domain} row(s) with winner outside {{team_a,team_b,NULL}}: "
                         + ", ".join(f"{s['match_id']}={s['winner']!r}" for s in sample))
    # 2) winner must agree with the series score when both scores are present.
    contra = await conn.fetch(
        f"""SELECT match_id, winner, score_a, score_b FROM matches
             WHERE score_a IS NOT NULL AND score_b IS NOT NULL
               AND ((winner='team_a' AND score_a <= score_b)
                 OR (winner='team_b' AND score_b <= score_a))
             LIMIT {SAMPLE}"""
    )
    contra_total = int(await conn.fetchval(
        """SELECT count(*) FROM matches
             WHERE score_a IS NOT NULL AND score_b IS NOT NULL
               AND ((winner='team_a' AND score_a <= score_b)
                 OR (winner='team_b' AND score_b <= score_a))"""
    ))
    r.metrics["winner_score_contradiction"] = contra_total
    if contra_total:
        r.verdict = "FAIL"
        r.details.append(f"{contra_total} row(s) where winner contradicts the score: "
                         + ", ".join(f"{c['match_id']}(w={c['winner']},{c['score_a']}-{c['score_b']})"
                                     for c in contra))
    # 3) resolvability rate on matches that SHOULD be settled (past the grace window).
    past = int(await conn.fetchval(
        f"SELECT count(*) FROM matches WHERE start_time < now() - interval '{SETTLE_GRACE_DAYS} days'"
    ))
    past_null = int(await conn.fetchval(
        f"SELECT count(*) FROM matches WHERE start_time < now() - interval '{SETTLE_GRACE_DAYS} days' "
        f"AND winner IS NULL"
    ))
    rate = (past_null / past) if past else 0.0
    r.metrics["past_matches"] = past
    r.metrics["past_unresolved"] = past_null
    r.metrics["past_unresolved_rate"] = round(rate, 4)
    r.details.append(f"unresolved winner on settled-eligible matches: {past_null}/{past} "
                     f"= {rate:.1%} (threshold {WINNER_NULL_MAX_PAST:.0%})")
    if past and rate > WINNER_NULL_MAX_PAST:
        r.verdict = "FAIL"
        r.details.append(f"unresolved rate {rate:.1%} exceeds {WINNER_NULL_MAX_PAST:.0%} — "
                         f"label set too sparse to trust")
    elif past_null:
        if r.verdict == "PASS":
            r.verdict = "WARN"
    r.summary = (f"domain-bad={bad_domain}, score-contradiction={contra_total}, "
                 f"past-unresolved={rate:.1%}")
    return r


async def check_cross_source_winner(conn) -> CheckResult:
    """Same logical match from >1 row/source must not disagree on WHO won."""
    r = CheckResult("cross_source_winner_agreement")
    resolved = int(await conn.fetchval(
        "SELECT count(*) FROM matches WHERE winner IN ('team_a','team_b')"
    ))
    if resolved == 0:
        r.verdict = "SKIP"
        r.summary = "no resolved matches to cross-check"
        return r
    # Map winner enum back to the winning TEAM NAME, group by logical match, and flag
    # groups where the resolved winning team differs across rows/sources.
    rows = await conn.fetch(
        f"""WITH labeled AS (
                SELECT game, start_time::date AS d,
                       LEAST(lower(team_a), lower(team_b))    AS t1,
                       GREATEST(lower(team_a), lower(team_b)) AS t2,
                       lower(CASE winner WHEN 'team_a' THEN team_a ELSE team_b END) AS win_team,
                       source, match_id
                  FROM matches
                 WHERE winner IN ('team_a','team_b')
            )
            SELECT game, d, t1, t2,
                   count(DISTINCT win_team) AS distinct_winners,
                   count(*)                 AS rows,
                   array_agg(DISTINCT source)  AS sources,
                   array_agg(DISTINCT win_team) AS winners
              FROM labeled
             GROUP BY game, d, t1, t2
            HAVING count(DISTINCT win_team) > 1
             ORDER BY rows DESC
             LIMIT {SAMPLE}"""
    )
    total = int(await conn.fetchval(
        """SELECT count(*) FROM (
             SELECT 1 FROM (
               SELECT game, start_time::date AS d,
                      LEAST(lower(team_a), lower(team_b))    AS t1,
                      GREATEST(lower(team_a), lower(team_b)) AS t2,
                      lower(CASE winner WHEN 'team_a' THEN team_a ELSE team_b END) AS win_team
                 FROM matches WHERE winner IN ('team_a','team_b')
             ) l
             GROUP BY game, d, t1, t2
             HAVING count(DISTINCT win_team) > 1) q"""
    ))
    r.metrics["conflicting_groups"] = total
    if total:
        r.verdict = "FAIL"
        for g in rows:
            r.details.append(f"CONFLICT {g['game']}/{g['t1']}-{g['t2']} {g['d']}: "
                             f"winners={list(g['winners'])} sources={list(g['sources'])}")
        r.summary = f"{total} logical match(es) with disagreeing winners across rows/sources"
    else:
        r.summary = "all resolved logical matches agree on the winner"
    return r


async def check_quarantine_leak(conn) -> CheckResult:
    """No decision/forward row may be anchored to absent data, and no banned pattern
    (contaminated model_version, price-blind bet) may be present."""
    r = CheckResult("quarantine_leak")
    checked = False

    # Orphan odds — a line pointing at a match that isn't in `matches`.
    if await _table_exists(conn, "odds_raw") and await _count(conn, "odds_raw") > 0:
        checked = True
        orphan = int(await conn.fetchval(
            "SELECT count(*) FROM odds_raw o LEFT JOIN matches m USING (match_id) "
            "WHERE m.match_id IS NULL"
        ))
        r.metrics["orphan_odds"] = orphan
        if orphan:
            r.verdict = "FAIL"
            r.details.append(f"{orphan} odds_raw row(s) reference a match_id absent from matches")

    if await _table_exists(conn, "predictions") and await _count(conn, "predictions") > 0:
        checked = True
        orphan_p = int(await conn.fetchval(
            "SELECT count(*) FROM predictions p LEFT JOIN matches m USING (match_id) "
            "WHERE p.match_id IS NOT NULL AND m.match_id IS NULL"
        ))
        r.metrics["orphan_predictions"] = orphan_p
        if orphan_p:
            r.verdict = "FAIL"
            r.details.append(f"{orphan_p} prediction(s) reference a match_id absent from matches")
        # Known banned landmine: contaminated model versions must never feed anything.
        contaminated = int(await conn.fetchval(
            "SELECT count(*) FROM predictions WHERE model_version ILIKE '%contaminat%'"
        ))
        r.metrics["contaminated_model_version"] = contaminated
        if contaminated:
            r.verdict = "FAIL"
            r.details.append(f"{contaminated} prediction(s) carry a *contaminated* model_version "
                             f"(banned landmine)")
        # The banned price-blind decision rule: a bet with no market_price recorded.
        blind = int(await conn.fetchval(
            "SELECT count(*) FROM predictions WHERE decision IN ('bet_a','bet_b') "
            "AND market_price IS NULL"
        ))
        r.metrics["price_blind_bets"] = blind
        if blind:
            r.verdict = "FAIL"
            r.details.append(f"{blind} bet decision(s) made with NULL market_price "
                             f"(price-deferring rule violated)")

    if not checked:
        r.verdict = "SKIP"
        r.summary = "no forward rows present — nothing could leak yet"
    else:
        r.summary = ("no quarantine leak detected" if r.verdict in ("PASS", "WARN")
                     else "QUARANTINE LEAK")
    return r


DB_CHECKS = [
    check_schema_presence,
    check_row_counts,
    check_null_rate,
    check_duplicate_match_id,
    check_temporal_lookahead,
    check_winner_resolvability,
    check_cross_source_winner,
    check_quarantine_leak,
]


async def run_db(database_url: str) -> List[CheckResult]:
    import asyncpg  # imported here so --jsonl mode needs no driver

    conn = await asyncpg.connect(database_url)
    results: List[CheckResult] = []
    try:
        # READ ONLY txn: a hard guarantee this battery cannot mutate the silo.
        tx = conn.transaction(readonly=True)
        await tx.start()
        try:
            for check in DB_CHECKS:
                try:
                    results.append(await check(conn))
                except Exception as e:  # a check that can't run leaves data quarantined
                    results.append(CheckResult(
                        check.__name__.replace("check_", ""),
                        verdict="ERROR",
                        summary=f"{type(e).__name__}: {e}",
                    ))
        finally:
            await tx.rollback()  # nothing was written; make it explicit
    finally:
        await conn.close()
    return results


# ===========================================================================
# File mode (--jsonl) — pre-import vetting of a raw NDJSON matches dump.
# ===========================================================================
def run_jsonl(path: str) -> List[CheckResult]:
    rows: List[dict] = []
    parse_errors = 0
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                parse_errors += 1
    results: List[CheckResult] = []

    load = CheckResult("jsonl_load", summary=f"{len(rows)} rows, {parse_errors} parse-error(s)")
    load.metrics = {"rows": len(rows), "parse_errors": parse_errors}
    if parse_errors:
        load.verdict = "FAIL"
        load.details.append(f"{parse_errors} line(s) were not valid JSON")
    if not rows:
        load.verdict = "EMPTY"
    results.append(load)
    if not rows:
        return results

    # null-rate on required source fields.
    req = ("match_id", "game", "team_a", "team_b")
    nulls = {k: sum(1 for r in rows if not r.get(k)) for k in req}
    time_null = sum(1 for r in rows if not (r.get("match_date") or r.get("start_time")))
    nr = CheckResult("null_rate", metrics={"nulls": nulls, "start_time_null": time_null})
    if any(nulls.values()) or time_null:
        nr.verdict = "FAIL"
        nr.details.append(f"missing required fields: {nulls}, start_time_null={time_null}")
    nr.summary = "required fields " + ("clean" if nr.verdict == "PASS" else "MISSING")
    results.append(nr)

    # duplicate match_id (import uses ON CONFLICT DO NOTHING, so dupes silently vanish).
    seen: Dict[str, int] = {}
    for r in rows:
        mid = r.get("match_id")
        if mid is not None:
            seen[str(mid)] = seen.get(str(mid), 0) + 1
    dups = {k: v for k, v in seen.items() if v > 1}
    dc = CheckResult("duplicate_match_id", metrics={"dup_count": len(dups)})
    if dups:
        dc.verdict = "WARN"  # silently deduped on import — surfaced, not fatal
        ex = list(dups.items())[:SAMPLE]
        dc.details.append(f"{len(dups)} duplicated match_id(s) (import keeps first): "
                          + ", ".join(f"{k}×{v}" for k, v in ex))
    dc.summary = f"{len(dups)} duplicate match_id(s)"
    results.append(dc)

    # winner-resolvability via the silo mapper (unresolved winner -> NULL label).
    unresolved = 0
    contra = 0
    for r in rows:
        w = map_winner(r.get("winner"), r.get("team_a"), r.get("team_b"))
        if r.get("winner") and w is None:
            unresolved += 1
        sa, sb = r.get("score_a"), r.get("score_b")
        if w and sa is not None and sb is not None:
            if (w == "team_a" and sa <= sb) or (w == "team_b" and sb <= sa):
                contra += 1
    has_winner = sum(1 for r in rows if r.get("winner"))
    urate = (unresolved / has_winner) if has_winner else 0.0
    wr = CheckResult("winner_resolvability", metrics={
        "with_winner": has_winner, "unresolved": unresolved,
        "unresolved_rate": round(urate, 4), "winner_score_contradiction": contra})
    wr.details.append(f"{unresolved}/{has_winner} stated winners do not map to either team "
                      f"({urate:.1%})")
    if contra:
        wr.verdict = "FAIL"
        wr.details.append(f"{contra} row(s) where mapped winner contradicts the score")
    elif has_winner and urate > WINNER_NULL_MAX_PAST:
        wr.verdict = "FAIL"
        wr.details.append(f"unmappable-winner rate {urate:.1%} exceeds {WINNER_NULL_MAX_PAST:.0%}")
    elif unresolved:
        wr.verdict = "WARN"
    wr.summary = f"unmappable winners {urate:.1%}, score-contradiction={contra}"
    results.append(wr)

    # cross-source winner agreement within the file (same logical match, differing winner).
    groups: Dict[tuple, set] = {}
    for r in rows:
        w = map_winner(r.get("winner"), r.get("team_a"), r.get("team_b"))
        if not w:
            continue
        ta, tb = str(r.get("team_a") or "").lower(), str(r.get("team_b") or "").lower()
        day = str(r.get("match_date") or r.get("start_time") or "")[:10]
        key = (str(r.get("game") or ""), day, tuple(sorted((ta, tb))))
        win_team = ta if w == "team_a" else tb
        groups.setdefault(key, set()).add(win_team)
    conflicts = {k: v for k, v in groups.items() if len(v) > 1}
    cs = CheckResult("cross_source_winner_agreement", metrics={"conflicting_groups": len(conflicts)})
    if conflicts:
        cs.verdict = "FAIL"
        for k, v in list(conflicts.items())[:SAMPLE]:
            cs.details.append(f"CONFLICT {k[0]}/{k[2][0]}-{k[2][1]} {k[1]}: winners={sorted(v)}")
    cs.summary = f"{len(conflicts)} logical match(es) with disagreeing winners"
    results.append(cs)

    return results


# ===========================================================================
# Reporting
# ===========================================================================
def _print_report(results: List[CheckResult], mode: str, source: str) -> None:
    print(f"esports_silo data-quality battery — mode={mode} source={source}")
    print("=" * 88)
    print(f"{'check':<32} {'verdict':<8} summary")
    print("-" * 88)
    for r in results:
        print(f"{r.name:<32} {r.verdict:<8} {r.summary}")
    print("-" * 88)
    any_detail = any(r.details for r in results)
    if any_detail:
        print("details:")
        for r in results:
            for d in r.details:
                print(f"  [{r.name}] {d}")
        print("-" * 88)


def _gate_line(results: List[CheckResult]) -> str:
    blocking = [r.name for r in results if r.verdict in _BLOCKING]
    if not blocking:
        return "GATE: PASS — checked data is CLEARED to leave quarantine."
    return ("GATE: QUARANTINE — the following check(s) block clearance, so ALL checked "
            "data stays excluded (Cmd 4): " + ", ".join(blocking))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="esports_silo read-only data-quality battery (Cmd-4 master gate)")
    ap.add_argument("--jsonl", metavar="FILE",
                    help="vet a raw NDJSON matches dump instead of the DB (pre-import)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    if args.jsonl:
        try:
            results = run_jsonl(args.jsonl)
        except FileNotFoundError:
            print(f"file not found: {args.jsonl}", file=sys.stderr)
            return 2
        mode, source = "jsonl", args.jsonl
    else:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
        db = os.getenv("DATABASE_URL", "")
        if not db:
            print("DATABASE_URL not set — cannot run the DB battery. "
                  "Set it (silo DB) or use --jsonl FILE.", file=sys.stderr)
            return 2
        try:
            import asyncpg  # noqa: F401
        except Exception:
            print("asyncpg not installed — `pip install asyncpg` (or use --jsonl FILE).",
                  file=sys.stderr)
            return 2
        results = asyncio.run(run_db(db))
        mode, source = "db", "$DATABASE_URL"

    if args.json:
        blocking = [r.name for r in results if r.verdict in _BLOCKING]
        print(json.dumps({
            "mode": mode,
            "gate": "PASS" if not blocking else "QUARANTINE",
            "blocking": blocking,
            "checks": [r.to_dict() for r in results],
        }, indent=2, default=str))
    else:
        _print_report(results, mode, source)
        print(_gate_line(results))

    return 0 if _gate_pass(results) else 1


if __name__ == "__main__":
    sys.exit(main())

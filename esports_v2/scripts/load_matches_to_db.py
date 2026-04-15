"""
Bulk-load esports_matches from JSONL file to PostgreSQL.

Reads NDJSON (one JSON object per line) and INSERT INTO esports_matches.
Uses ON CONFLICT DO NOTHING to skip existing matches.

Usage:
    python -m esports_v2.scripts.load_matches_to_db data/esports_matches_bulk.jsonl

Requires DATABASE_URL environment variable.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m esports_v2.scripts.load_matches_to_db <jsonl_file>")
        sys.exit(1)

    filepath = sys.argv[1]
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        print("ERROR: psycopg2 not installed")
        sys.exit(1)

    # Read JSONL
    rows = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    print(f"Loaded {len(rows)} match records from {filepath}")

    # Parse dates
    for r in rows:
        md = r.get("match_date")
        if isinstance(md, str) and md:
            try:
                r["match_date"] = datetime.fromisoformat(md.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                r["match_date"] = None

    # Bulk insert
    insert_sql = """
        INSERT INTO esports_matches (
            match_id, game, event_name, event_tier,
            team_a, team_b, winner, score_a, score_b,
            best_of, match_date, is_lan, source
        ) VALUES %s
        ON CONFLICT (match_id) DO NOTHING
    """

    values = [
        (
            r.get("match_id"),
            r.get("game"),
            r.get("event_name"),
            r.get("event_tier"),
            r.get("team_a"),
            r.get("team_b"),
            r.get("winner"),
            r.get("score_a"),
            r.get("score_b"),
            r.get("best_of"),
            r.get("match_date"),
            r.get("is_lan", False),
            r.get("source", "bulk_load"),
        )
        for r in rows
    ]

    conn = psycopg2.connect(db_url)
    with conn:
        with conn.cursor() as cur:
            execute_values(cur, insert_sql, values, page_size=1000)
    conn.close()

    print(f"Inserted {len(values)} matches (ON CONFLICT DO NOTHING for existing)")


if __name__ == "__main__":
    main()

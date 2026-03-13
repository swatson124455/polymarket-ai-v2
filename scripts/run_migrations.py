#!/usr/bin/env python3
"""
Run schema migrations in order. Tracks applied migrations in schema_migrations table.
Prevents double-runs and wrong ordering. Run before first use and after deploying new migrations.

Usage:
  python scripts/run_migrations.py
  python scripts/run_migrations.py --check   # List applied/pending without running
"""
import argparse
import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")


def _split_sql(sql: str):
    """Split SQL on ';' while respecting dollar-quoted blocks ($$, $body$, etc.)."""
    import re
    _dollar_re = re.compile(r'\$[a-zA-Z_]*\$')  # Match $$, $body$, $func$, etc.

    parts = []
    current = []
    in_dollar = False
    dollar_tag = None  # Track which tag opened the block (e.g. '$$' or '$body$')

    for line in sql.splitlines():
        stripped = line.strip()

        # Check for dollar-quoting tags
        tags = _dollar_re.findall(stripped)
        if tags:
            if not in_dollar:
                # Opening a dollar block
                in_dollar = True
                dollar_tag = tags[0]
                # Check if same tag appears twice (open+close on same line)
                if stripped.count(dollar_tag) >= 2:
                    in_dollar = False
                    dollar_tag = None
                    # Closed on same line — check for trailing ';'
                    if stripped.endswith(';'):
                        before_semi = line.rstrip().rstrip(';')
                        current.append(before_semi)
                        stmt_text = "\n".join(current).strip()
                        stmt_text = "\n".join(
                            l for l in stmt_text.splitlines()
                            if l.strip() and not l.strip().startswith("--")
                        ).strip()
                        if stmt_text:
                            parts.append(stmt_text)
                        current = []
                    else:
                        current.append(line)
                else:
                    current.append(line)
                continue
            elif dollar_tag in tags:
                # Closing the dollar block
                in_dollar = False
                dollar_tag = None
                # Check for trailing ';' on this line
                if stripped.endswith(';'):
                    before_semi = line.rstrip().rstrip(';')
                    current.append(before_semi)
                    stmt_text = "\n".join(current).strip()
                    stmt_text = "\n".join(
                        l for l in stmt_text.splitlines()
                        if l.strip() and not l.strip().startswith("--")
                    ).strip()
                    if stmt_text:
                        parts.append(stmt_text)
                    current = []
                else:
                    current.append(line)
                continue
            else:
                # Different tag inside dollar block — treat as content
                current.append(line)
                continue

        if in_dollar:
            current.append(line)
            continue
        # Skip full-line comments (they may contain ';' in text)
        if stripped.startswith("--"):
            current.append(line)
            continue
        # Outside dollar block: split on ';'
        if ';' in stripped:
            before, _, after = line.partition(';')
            current.append(before)
            stmt_text = "\n".join(current).strip()
            # Strip comment-only lines
            stmt_text = "\n".join(
                l for l in stmt_text.splitlines()
                if l.strip() and not l.strip().startswith("--")
            ).strip()
            if stmt_text:
                parts.append(stmt_text)
            current = [after] if after.strip() else []
        else:
            current.append(line)
    # Remaining
    if current:
        stmt_text = "\n".join(current).strip()
        stmt_text = "\n".join(
            l for l in stmt_text.splitlines()
            if l.strip() and not l.strip().startswith("--")
        ).strip()
        if stmt_text:
            parts.append(stmt_text)
    return parts


async def main(check_only: bool = False) -> int:
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized. Set DATABASE_URL.")
        return 1

    migrations_dir = _project_root / "schema" / "migrations"
    if not migrations_dir.exists():
        print(f"ERROR: Migrations dir not found: {migrations_dir}")
        return 1

    # Collect migration files in order (001, 002, 003, ...)
    migration_files = sorted(f for f in migrations_dir.glob("*.sql") if f.name[0].isdigit())
    if not migration_files:
        print("No migration files found.")
        return 0

    applied: set = set()
    # Use engine.begin() for bootstrap to guarantee commit visibility
    async with db.engine.begin() as conn:
        r = await conn.execute(text("""
            SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'schema_migrations')
        """))
        has_table = r.scalar() or False

        if not has_table:
            try:
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        applied_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
                    )
                """))
                await conn.execute(text("INSERT INTO schema_migrations (name) VALUES ('001_schema_migrations.sql') ON CONFLICT (name) DO NOTHING"))
                applied = {"001_schema_migrations.sql"}
            except Exception as e:
                print(f"ERROR: Bootstrap failed: {e}")
                return 1
        else:
            r = await conn.execute(text("SELECT name FROM schema_migrations"))
            applied = {row[0] for row in r.fetchall()}

    for mf in migration_files:
        name = mf.name
        if name in applied:
            print(f"  [skip] {name} (already applied)")
            continue
        if check_only:
            print(f"  [pending] {name}")
            continue

        print(f"  [run] {name}")
        try:
            sql = mf.read_text(encoding="utf-8")
            stmts = _split_sql(sql)
            # Use engine.begin() for reliable single-connection transaction (PgBouncer safe)
            async with db.engine.begin() as conn:
                await conn.execute(text("SET LOCAL statement_timeout = '600s'"))
                for s in stmts:
                    if not s:
                        continue
                    await conn.execute(text(s))
                await conn.execute(
                    text("INSERT INTO schema_migrations (name) VALUES (:n) ON CONFLICT (name) DO NOTHING"),
                    {"n": name}
                )
        except Exception as e:
            print(f"ERROR: Migration {name} failed: {e}")
            return 1

    await db.close()
    if not check_only and migration_files:
        print("Migrations complete.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run schema migrations")
    parser.add_argument("--check", action="store_true", help="List applied/pending without running")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(check_only=args.check)))

# One-time (re-runnable, idempotent) dump of the esports_team_aliases DB table
# to /home/ubuntu/eb-odds/aliases.json for the sharp-line pipeline's
# alias_expand injection (collector PM matching + audit results join).
# Read-only on the DB. Format: {"groups": [["Natus Vincere","NAVI"], ...]}
# consumed by esports_v2/data/alias_file.py and the standalone collector.
set -e
export DATABASE_URL="$(grep -m1 '^DATABASE_URL=' /opt/pa2-shared/.env | cut -d= -f2- | tr -d '"\r')"
if [ -z "$DATABASE_URL" ]; then echo "no DATABASE_URL in /opt/pa2-shared/.env"; exit 1; fi
/opt/polymarket-ai-v2-esports/venv/bin/python3 - <<'PY'
import asyncio, json, os
import asyncpg

async def main():
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "SELECT canonical_name, alias FROM esports_team_aliases")
    finally:
        await conn.close()
    groups = {}
    for r in rows:
        c = (r["canonical_name"] or "").strip()
        a = (r["alias"] or "").strip()
        if not c:
            continue
        g = groups.setdefault(c, {c})
        if a:
            g.add(a)
    # identity-only groups (canonical==alias) add nothing to matching — drop
    out = {"groups": sorted(sorted(g) for g in groups.values() if len(g) >= 2)}
    path = "/home/ubuntu/eb-odds/aliases.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"aliases.json: db_rows={len(rows)} canonicals={len(groups)} "
          f"non-identity groups={len(out['groups'])} -> {path}")

asyncio.run(main())
PY

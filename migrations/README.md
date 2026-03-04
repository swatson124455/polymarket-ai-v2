# Migrations

**Canonical migrations** live in `schema/migrations/` and are applied with:

```bash
python scripts/run_migrations.py
```

To list applied/pending without running:

```bash
python scripts/run_migrations.py --check
```

Do not run SQL files from this folder manually. The file that was here (`bot_improvements_schema.sql`) was a duplicate of `schema/migrations/005_bot_improvements.sql`; all bot-improvement and later migrations are in `schema/migrations/` (001–009).

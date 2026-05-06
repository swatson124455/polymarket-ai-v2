# Database Migration Ownership — Known Issue and Durable Fix

**Surfaced:** S215 Batch 3 deploy (2026-05-06)  
**Symptom:** Migration 076 failed with `InsufficientPrivilegeError: must be owner of table shadow_fills`  
**Root cause:** Most tables in the `polymarket` database are owned by the `postgres` superuser,
not the `polymarket` app user that the migration runner (`run_migrations.py`) uses.

---

## Affected scope

96 of ~97 tables in `public` schema are owned by `postgres`. The `polymarket` user can INSERT,
UPDATE, SELECT, and DELETE on these tables (via grants), but cannot ALTER their structure.
Any migration using `ALTER TABLE` on these tables will fail at deploy time.

```sql
-- Check ownership balance:
SELECT tableowner, COUNT(*) FROM pg_tables WHERE schemaname = 'public' GROUP BY tableowner;
```

---

## Current workaround (used for migration 076)

When a migration fails with `InsufficientPrivilegeError: must be owner of table X`:

1. Run the DDL manually as postgres superuser on the VPS:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0
sudo -u postgres psql -d polymarket -c "<paste DDL here>"
```

2. Mark the migration as applied in `schema_migrations`:
```sql
INSERT INTO schema_migrations (name) VALUES ('076_your_migration.sql') ON CONFLICT (name) DO NOTHING;
```
Note: `schema_migrations` has columns `(id, name, applied_at)` only — no checksum. The runner
matches solely on `name`. This insert is equivalent to what the runner would write.

3. Re-deploy normally. The migration will be skipped (already in `schema_migrations`).

**Limitation:** The migration file as committed is not replay-safe on a fresh database setup.
The DDL must also be run as postgres in that scenario.

---

## Durable fix (recommended — run once per environment)

Transfer ownership of all public-schema tables to `polymarket`. Run as postgres:

```bash
sudo -u postgres psql -d polymarket -c "
DO \$\$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public' AND tableowner != 'polymarket'
    LOOP
        EXECUTE 'ALTER TABLE public.' || quote_ident(r.tablename) || ' OWNER TO polymarket';
    END LOOP;
END \$\$;
"
```

After running this, the migration runner can `ALTER TABLE` freely. All subsequent migrations
will run without the superuser workaround.

**Verify:**
```sql
SELECT tableowner, COUNT(*) FROM pg_tables WHERE schemaname = 'public' GROUP BY tableowner;
-- Should show: polymarket | 97 (or current count), postgres | 0
```

**Safety:** This only transfers table ownership, not function/sequence ownership. Grants
already in place (INSERT, SELECT, UPDATE, DELETE via role) are unaffected.

---

## Why this wasn't caught earlier

Tables created by the initial database setup scripts ran as `postgres`. The migration runner
runs as `polymarket`. Until migration 076, all migrations used `CREATE TABLE IF NOT EXISTS`
(which works regardless of ownership) rather than `ALTER TABLE`. Migration 076 was the first
to ALTER an existing table, revealing the ownership gap.

---

## Pre-migration checklist for future ALTER TABLE migrations

Before committing any migration that uses `ALTER TABLE`:

1. Check if the target table is owned by `polymarket`:
   ```sql
   SELECT tableowner FROM pg_tables WHERE tablename = 'your_table_name' AND schemaname = 'public';
   ```

2. If owned by `postgres`: either run the durable fix above first, or plan for the manual
   workaround at deploy time.

3. After the durable fix is applied, this check is no longer needed.

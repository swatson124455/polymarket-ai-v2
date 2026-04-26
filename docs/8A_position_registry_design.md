# Phase 8A — Central Position Registry: Design Doc

**Bots affected**: all 15 (registry is shared)
**Status**: DESIGN ONLY — Day 3 of 5 ships infrastructure (advisory lock helper) + this doc; index landing + cache + caller wiring deferred behind design review
**Author**: 2026-04-26
**Supersedes**: nothing; first design pass on a structural cross-bot coordination layer
**Parent plan**: S172 §S195 forward audit, Days 3–5 calendar
**Scope boundary**: this doc captures the four 8A pieces, prod-data findings that change the rollout shape, and a phased rollout that does not change runtime behavior in the first commit landing.

---

## 1. Current State

The `positions` table (`base_engine/data/database.py:291-316`) has the following uniqueness contract today:

```python
__table_args__ = (
    UniqueConstraint("bot_id", "market_id", "side", name="uq_positions_bot_market_side"),
    Index("idx_positions_bot_id", "bot_id"),
    Index("idx_positions_market_id", "market_id"),
    Index("idx_positions_status", "status"),
)
```

**Per-bot uniqueness is enforced.** Per-bot, no two rows share `(market_id, side)`. **Cross-bot overlap is not enforced** — two bots can independently hold OPEN positions on the same `(market_id, side)`.

`status` values are `'open' | 'reserving' | 'closed'` (column comment). Only the OPEN+RESERVING set is operationally live; CLOSED rows are historical.

---

## 2. Data Feasibility Check — Cross-Bot Overlap

**Verdict: cross-bot OPEN overlap is not present TODAY but has been present HISTORICALLY.** This makes the proposed cross-bot partial unique index a behavior-changing constraint, not a tautological tightening.

**Evidence (verified against prod 2026-04-26):**

1. **Currently OPEN duplicates on `(market_id, side) WHERE status = 'OPEN'`**: zero. Across all 15 bots, no two OPEN positions share a market+side pair right now. The proposed index could be added to the live table without backfill rejection.
2. **All-time duplicates on `(market_id, side)` regardless of status**: present, recurring, dominated by `{EsportsBot, MirrorBot}` pairs. Sample of 10 markets returned by `SELECT market_id, side, COUNT(DISTINCT bot_id), ARRAY_AGG(DISTINCT bot_id) FROM positions GROUP BY market_id, side HAVING COUNT(DISTINCT bot_id) > 1 LIMIT 10`:

   ```
   market_id                                                       | side | n_bots | bots
   0x06f6534...                                                    | SELL | 2      | {EsportsBot,MirrorBot}
   0x1fa114f...                                                    | SELL | 2      | {EsportsBot,MirrorBot}
   0x269a752...                                                    | SELL | 2      | {EsportsBot,MirrorBot}
   0x269a752...                                                    | YES  | 2      | {EsportsBot,MirrorBot}
   0x2dff48e...                                                    | SELL | 2      | {EsportsBot,MirrorBot}
   ...
   ```

   These rows are closed today, so they don't conflict with a partial index `WHERE status='OPEN'`. But the historical pattern shows that EsportsBot and MirrorBot HAVE simultaneously held OPEN positions on the same market+side in the past. The proposed cross-bot index would block the second bot's OPEN insert in any future occurrence of this pattern.

3. **Side-column anomaly (filed for §S195 hygiene backlog).** The query above returned `side='SELL'` rows in the `positions` table. Per `CLAUDE.md`: *"BUY/SELL vs YES/NO: BaseBot.place_order() expects side='YES' or side='NO'. Never pass 'BUY'/'SELL'."* The presence of `'SELL'` in `positions.side` indicates a write path bypassing that contract — likely legacy data or an older code path. Out of 8A scope; tracked as a separate hygiene item.

**Implication for the partial unique index.** The proposed index `CREATE UNIQUE INDEX ... ON positions (market_id, side) WHERE status = 'OPEN'` would change runtime behavior the next time two bots want to open on the same market+side concurrently — the second bot's INSERT raises `IntegrityError`. Whether that is the intended outcome is a design decision (§3.2 below), not a tautological tightening. Therefore Day 3 ships infrastructure that does not enforce the constraint; the index lands separately in a PR that wires up the caller-side handling for the new failure mode.

---

## 3. The Four 8A Pieces

### 3.1 Partial unique index — DEFERRED behind design review

```sql
CREATE UNIQUE INDEX CONCURRENTLY uq_positions_market_side_open
    ON positions (market_id, side)
    WHERE status = 'OPEN';
```

`CONCURRENTLY` is required to avoid blocking writes during creation on a busy table. The current homegrown migration runner runs each statement inside `engine.begin()` (a transaction); `CREATE INDEX CONCURRENTLY` cannot run inside a transaction (PG error `25001`). The index migration therefore needs either:

- (a) a one-off operator command to apply via direct `psql` (outside the runner),
- (b) the migration-runner upgrade scheduled for Week 2 (sqlparse + ability to mark statements as transaction-incompatible),
- (c) a non-CONCURRENT index acceptance with a brief write lock on `positions` during creation.

Recommendation: defer until (b) lands. Operator command can be staged immediately if Day 4–5 work prioritises the index over the runner upgrade.

**Caller-side risk surface.** With the index in place, every code path that opens a new position must handle `IntegrityError` on duplicate `(market_id, side, status='OPEN')`. Audit of write paths to `positions`:

| Caller | File:Line | Behavior on IntegrityError today | Required behavior after index |
|---|---|---|---|
| `base_engine/data/database.py:Position` direct insert | (any path that constructs and saves `Position(...)`) | propagates as ORM exception | catch + log + treat as "already-OPEN by another bot, route to modify path" |
| `bots/mirror_bot.py` open path | grep `INSERT INTO positions` / `Position(` in mirror_bot | TBD per audit before index lands | same |
| `bots/esports_bot.py` + `esports_bot_v2.py` | TBD | TBD | same |
| Other bots | TBD | TBD | same |

A complete write-path audit must precede the index landing. Estimated 3–4 hours.

### 3.2 Advisory lock around the open-or-modify path — INFRASTRUCTURE SHIPS DAY 3, CALLERS WIRED LATER

**Mechanism.** `pg_advisory_xact_lock(hashtext(market_id::text))` taken at the start of any open-or-modify code path serialises racing bots on a per-market basis. Lock is released at transaction end (commit or rollback).

**Why hashtext.** `pg_advisory_xact_lock` takes a `bigint`. `hashtext` reduces an arbitrary-length `text` (the market_id) to a stable `int4`, which casts implicitly to `bigint`. Collisions are possible (~1 in 2^32) but harmless: a collision causes two unrelated markets to serialise unnecessarily, never an incorrect result.

**Why `xact` and not `pg_advisory_lock`.** Session-level advisory locks must be released explicitly (`pg_advisory_unlock`); on a connection drop or unhandled exception, the lock can persist for the remainder of the session. `pg_advisory_xact_lock` releases automatically at transaction end — safer pattern for our async asyncpg+SQLAlchemy stack where exceptions are common during retries.

**Day 3 deliverable** (`base_engine/data/advisory_locks.py`): an async context manager that takes a session + market_id, opens a SAVEPOINT-protected sub-transaction, takes the lock, yields, then releases via transaction end. No callers wired yet — voluntary opt-in for any code path that wants the serialisation contract during Day 4–5 work.

### 3.3 LISTEN/NOTIFY hot-cache — DEFERRED to Day 4–5

**Mechanism.** A trigger on `positions` (INSERT / UPDATE / DELETE) emits `NOTIFY positions_changed, '<market_id>:<side>'`. Each bot maintains an in-process `dict[(market_id, side), Position]` view of OPEN positions, refreshed at startup from the table and invalidated incrementally on NOTIFY.

**Why now.** Without the cache, every open-or-modify decision becomes an extra `SELECT` round-trip. With the cross-bot constraint in place, that extra SELECT becomes hot-path. The cache trades a small memory footprint per bot (~5 KB per OPEN position × hundreds = single MB per bot) for sub-millisecond reads.

**Why not now.** LISTEN/NOTIFY plumbing is a separate engineering surface (asyncpg's `add_listener` API, structured payload schema, replay-on-reconnect, dropped-notification recovery). Worth its own Day 4–5 commit pair.

### 3.4 Contract tests — DEFERRED until 3.1 + 3.2 are wired

The plan calls for tests exercising three race triangles:

1. **Two bots opening simultaneously** on the same `(market_id, side)`: exactly one INSERT must succeed, the other must see `IntegrityError` and route to the modify path.
2. **One bot closing while another modifies**: the modify must serialise after the close, never observe a partially-updated row.
3. **Recovery after a partial-write crash**: a process killed mid-INSERT must leave no `'reserving'` or partial `'open'` rows that block subsequent operations.

Plus the **17-day-bug-class consistency test**: assert that for any `(market_id, side, time_window)` tuple, registry state is identical regardless of which bot writes it. Same shape as the bot_pnl.py windowed-consistency check from S195 §plan-hygiene; the registry version checks state, not P&L.

These tests need real concurrent transactions against a real DB (testcontainers Postgres), which is its own scaffolding effort (already on the Week 2 backlog per S195 plan).

---

## 4. Phased Rollout

| Phase | Scope | Behavior change | Day in calendar |
|---|---|---|---|
| **A — Day 3 (this commit pair)** | Design doc + advisory-lock helper module + helper unit tests. No callers, no schema change. | None. | Today |
| **B — Day 4** | Open-path audit across all 15 bots. Document each write site's current behavior on conflict. Decide opt-in vs require for the advisory lock. | None (audit only). | Tomorrow |
| **C — Day 4–5** | Wire advisory lock around the 2–4 most-active open paths (MirrorBot, EsportsBot v2, paper_trading bridge). Add structured logging for "lock waited" and "lock immediately acquired" events. Verify no regression via existing test suite. | Lock-acquired logs appear; behavior identical otherwise. | Day 4–5 |
| **D — separate PR after migration runner upgrade** | Land partial unique index + caller-side IntegrityError → modify-path routing across the audited write sites. Backfill index with `CONCURRENTLY` via the upgraded runner. | Cross-bot duplicate OPEN now rejected at schema level. | Week 2 |
| **E — separate PR** | LISTEN/NOTIFY trigger + asyncpg listener + per-bot in-process cache + cache invalidation tests. | Open-path SELECTs replaced by cache reads. | Week 2 |
| **F — separate PR** | Contract tests (3 race triangles + consistency test) against testcontainers Postgres. | Test-only. | Week 2 |

**Rollback story.** Phases A–C are safe to revert via `git revert` with no schema cleanup. Phase D requires `DROP INDEX uq_positions_market_side_open`. Phase E requires dropping the trigger + restarting bots. Phase F is test-only.

---

## 5. Out-of-scope Findings (for §S195 / future hygiene)

- **`positions.side='SELL'` anomaly.** Indicates a write path bypassing the `place_order()` contract that mandates `'YES' | 'NO'`. Recommend: grep all writers to `positions.side`, find the 'SELL' source, normalise.
- **Phase D index migration runner blocker.** `CREATE INDEX CONCURRENTLY` requires the migration-runner upgrade. Couples 8A Phase D to the Week 2 runner work; Phase D cannot ship in isolation.
- **`side='YES'` paired with `side='NO'` for the same market_id is legitimate** (different binary-outcome positions). The proposed partial unique index is on `(market_id, side)` not `(market_id)` — it allows YES + NO simultaneously, only forbids same-side duplicates.
- **Reserving status semantics.** Phase D needs to decide whether `status='reserving'` rows count for the partial unique index. Current proposal: `WHERE status = 'OPEN'` only. If `reserving` rows can also represent live exposure that should block a duplicate open, the predicate widens to `WHERE status IN ('OPEN','reserving')`.

---

## 6. Evidence of Origin

S195 forward-audit, Days 3–5 calendar (`memory/project_s172_consolidated_plan.md` + the user-supplied Day-1 plan). Prod-data feasibility verified 2026-04-26 via direct `psql` queries through the Lightsail bastion. No commits land 8A semantic-shift today; this doc + the helper module are the Day 3 deliverables.

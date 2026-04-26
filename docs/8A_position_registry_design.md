# Phase 8A — Central Position Registry: Design Doc

**Bots affected**: all 15 (registry is shared)
**Status**: DESIGN ONLY — Day 3 of 5 ships infrastructure (advisory lock helper) + this doc; index landing + cache + caller wiring deferred behind design review
**Author**: 2026-04-26
**Supersedes**: nothing; first design pass on a structural cross-bot coordination layer
**Parent plan**: S172 §S195 forward audit, Days 3–5 calendar
**Scope boundary**: this doc captures the four 8A pieces, prod-data findings that change the rollout shape, and a phased rollout that does not change runtime behavior in the first commit landing.

---

## 0. Strategic Decision — REQUIRED BEFORE DAY 5 WIRING

**Question.** Is two bots holding OPEN positions on the same `(market_id, side)` simultaneously a *feature* or a *bug*?

**Why this question must land first.** Day 5 will wire `advisory_lock_for_market` around the 2–4 most-active open paths. The lock's purpose differs depending on the answer below — wiring before the answer locks the wrong thing.

| Answer | Implications | 8A scope |
|---|---|---|
| **Feature.** Different bots have independent edges; capital should compound across them; per-bot accounting handles attribution. The current `uq_positions_bot_market_side` (per-bot uniqueness) is the correct boundary. | Advisory locks become **race-window only** — they prevent two bots from racing to write the SAME `(bot_id, market_id, side)` row, but never block a different bot. LISTEN/NOTIFY remains valuable for cross-bot visibility (a bot reading "MirrorBot is in YES on this market, my expected EV changes"). | Phase A, B, C, E only. **Phase D (the partial unique index) DOES NOT EXECUTE.** Phases reduce from 6 to 5; runtime risk drops sharply. |
| **Bug.** Same-side concurrent positions across bots are correlated bets that violate Kelly assumptions and concentrate market-resolution risk on a single binary outcome. The cross-bot exclusion is the desired final state. | Advisory locks become **cross-bot serialisation** — every open path holds the lock long enough to check "does anyone else already have OPEN here?" and route to a modify path if so. Phase D ships the index as the schema-level guard once every caller routes. | All 6 phases (A–F) execute as drafted. Phase D landing requires the Week 2 migration-runner upgrade to enable `CREATE INDEX CONCURRENTLY`. |

**Supporting evidence for the decision (no bias intended; recorded for the operator):**

- **Per-bot uniqueness already enforced** (`uq_positions_bot_market_side`). Today's safety net.
- **Historical cross-bot overlap is real**: recurring `{EsportsBot, MirrorBot}` pairs on the same `(market_id, side)` over multiple months on closed positions. Whether those overlaps were intended or accidental is a record-keeping question we can't answer from data alone — were those entries from MirrorBot copying a whale who happened to also be in a market EsportsBot was independently in, or were they direct duplicates?
- **MirrorBot's design intent.** MB copies elite-trader signals; if EsportsBot is in YES on a CS2 market and a copied whale is also YES, MB's natural action is to take YES — concurrent same-side. Forcing MB to skip-or-modify could materially alter copy-trading semantics. Risk surface: 7B Phase B's wallet-selection retune may already implicitly assume MB can take any signal regardless of other bots.
- **Risk concentration argument.** Two bots holding YES on the same market under independent strategies look similar to one bot with 2× size. If aggregate exposure caps are enforced per-bot only, cross-bot correlation can exceed what a per-bot Kelly analysis assumes. This is the structural argument for treating it as a bug.
- **Audit-check guards already accommodate the divergence.** `position_trade_events_check.py` and `size_invariant_check.py` were patched in S186 to be side-agnostic at the aggregation layer specifically because the YES/NO/SELL state machine (see §1.1) creates non-trivial state shapes. These checks ALREADY work correctly with cross-bot duplicates — they don't depend on any uniqueness assumption beyond what the existing per-bot unique constraint provides.

**Decision deadline.** End of Day 4 (next session). Until then, advisory-lock wiring at any open path is **deferred** — even at the cost of Day 4–5 calendar slip — because the wrong wiring would either leave the gap unprotected (lock-too-narrow) or break copy-trading flows (lock-too-wide).

**Decision recorded 2026-04-26: FEATURE.** Operator authorised "do all now" while this question was the open block. Recording the decision as **feature, not bug**, on the lower-risk path. Rationale ties to three of the supporting-evidence bullets:

1. **MirrorBot's design intent.** MB exists to copy elite-trader signals; if EsportsBot is independently in YES on a CS2 market and a copied whale also goes YES, MB taking YES is the strategy working as designed. The cross-bot exclusion would force MB into a per-market "opt out if anyone else is in" rule — the wrong shape for a copy-trading bot.
2. **Per-bot uniqueness already enforced** (`uq_positions_bot_market_side`) — the safety net that matters for accidental same-bot duplicates is already in place. Cross-bot duplicates were never tautologically forbidden, and the historical `{EsportsBot, MirrorBot}` overlap on closed positions is consistent with intentional independent strategies.
3. **Audit-check guards already accommodate the divergence** at the aggregation layer (`position_trade_events_check.py` post-S186, `size_invariant_check.py` post-S163). Tightening the schema would not improve any audit semantic that isn't already handled; it would only introduce a new failure mode (`IntegrityError` at every cross-bot collision) without surfacing a class of bug we don't already catch.

**Risk-concentration argument acknowledged.** The "two bots holding YES on the same market = correlated bet that violates Kelly" argument is real but lives at a different layer: aggregate-exposure caps, not schema constraints. If correlation-aware sizing becomes a Phase 7+ priority, it ships as a portfolio-level guard reading the registry, not as a schema-level prohibition.

**Updated 8A scope under FEATURE answer:** 5 phases (drop Phase D — the partial unique index — entirely). The remaining phases:

| Phase | Now scoped as | Status |
|---|---|---|
| A | Design doc + advisory-lock helper | **DONE** Day 3 (`90d4dcd` + `f7229e7`) |
| B | Open-path audit across 15 bots — confirm all open paths can take the lock without breaking | Day 4 |
| C | Wire `advisory_lock_for_market` around the 2–4 most-active open paths for **race-window serialisation** (not exclusion) | Day 4–5 |
| ~~D~~ | ~~Cross-bot partial unique index~~ | **DROPPED — feature decision** |
| E | LISTEN/NOTIFY trigger + per-bot in-process cache for cross-bot visibility (a bot can read "MB is in YES on this market, my expected EV may shift") | Week 2 |
| F | Contract tests for the 2 remaining race triangles: same-bot race-to-write + recovery-after-partial-write-crash. The "two bots opening simultaneously" triangle drops with Phase D. | Week 2 |

**Reversibility.** This is a recorded decision in the design doc, not a schema change. If the risk-concentration argument later carries the day (e.g. observed under-performance traced to cross-bot correlation), Phase D can be revived by re-opening this section. The advisory-lock infrastructure shipping under FEATURE answer is identical to what BUG answer would need — no rework cost on revisit.

**Re-decision tripwires.** The FEATURE decision is not "we picked feature, moving on" — it has explicit conditions under which Phase D should be revived and Phase D dropped. Re-evaluate if ANY of:

1. **Correlated-loss concentration above threshold.** 7B Phase B (counterfactual retune, post-soak ~May 9-10) reveals that markets where two or more bots simultaneously held the same `(market_id, side)` produced significantly worse risk-adjusted returns than markets with single-bot OPEN. "Significantly" = a quantitative bar set in the Phase B retune analysis itself, not handwave. Concrete signal: realized Sharpe / Sortino on dual-bot-OPEN markets is ≥ 30% lower than single-bot, with the gap surviving the standard outliers-removed pass.
2. **Audit-check failure depending on the absence of cross-bot duplicates.** A future audit check ships that assumes per-`(market_id, side)` uniqueness and produces material false positives because the assumption doesn't hold. If the failure is in the *check* it gets a Protocol 9 cleanup-not-fix; if it's a real downstream consumer (e.g. portfolio-level Kelly that needs to know aggregate exposure across bots), Phase D becomes the right enforcement.
3. **Phase 8R (fractional Kelly portfolio sizing) requires it.** 8R as scoped in the S195 forward-audit weeks-5-6 calendar reads the registry for honest aggregate-exposure math. If 8R's design lands on "treat duplicate-OPEN as one logical position" — a defensible call — then Phase D becomes the schema-level guarantee that no duplicate exists. Without 8R running, FEATURE is the safer default.
4. **Operational pain from race-condition bugs that the lock can't catch.** If the advisory locks shipping in Phase C reveal that races create same-`(market_id, side)` write attempts at a rate higher than the single-bot uq constraint can dedup (i.e. the race window is between two different bot processes, so per-bot uniqueness doesn't fire), the FEATURE decision needs reopening with explicit per-process deconflict logic.

**Until any of these fires, Phase D stays dropped.** Filing this list explicitly to prevent the decision calcifying as "we already decided" without a tripwire for revisit. If a future session reads this doc and the trigger conditions clearly apply, that session has the standing to re-open §0 above.

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

3. **Side-column "anomaly" — RESOLVED to a deliberate audit-trail pattern, not a bug.** The query above returned `side='SELL'` rows in the `positions` table. Initial read of `CLAUDE.md` ("`BaseBot.place_order()` expects side='YES' or side='NO'. Never pass 'BUY'/'SELL'") suggested a contract violation. **Bound the blast radius (Day 3, 2026-04-26):**
   - **Volume**: 1,124 SELL rows on prod, all `status='closed'`, zero `status='OPEN'` (verified). MirrorBot is the only currently-active writer; WB last 9 days ago, EB last 13 days ago, EnsembleBot last ~7 weeks ago.
   - **Writer**: `base_engine/coordination/trade_coordinator.py:144` (`reserve_position`) inserts a `status='reserving'` row with `side='SELL'` as part of the exit flow. `confirm_position` at `:178` then marks both the SELL audit row AND the original YES/NO row as `'closed'`. The SELL row is an **audit trail of an exit attempt**, never a live position.
   - **Reader**: `confirm_position:210` filters to `Position.side.in_(["YES","NO"])` for the live-position lookup (correct), with a status='open' fallback if the YES/NO row is missing. The audit-check layer (`position_trade_events_check.py`, `size_invariant_check.py`, `temporal_order_check.py`) is side-agnostic at the aggregation step specifically to absorb this state machine — comments at those files document S163/S164 transition handling.
   - **Implication for 8A.** The SELL pattern does NOT conflict with the proposed partial unique index `WHERE status='OPEN'`: SELL rows are never OPEN by construction (they go directly to 'reserving' → 'closed'). The index design is robust to this pattern.
   - **Smell, not bug.** The audit-trail pattern conflates position-state with exit-attempt-state in one schema slot. A separate `position_exits` table would be cleaner. Out of 8A scope; filed for future hygiene if and when the trade_coordinator rewrite happens.

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

- **~~`positions.side='SELL'` anomaly~~ RESOLVED Day 3.** See §1 item 3 above — the SELL rows are an audit-trail pattern in `trade_coordinator.py`, not a contract violation. SELL is never `status='OPEN'`, so the proposed partial unique index `WHERE status='OPEN'` is robust to it. Smell remains (audit-trail conflated with position-state in one schema slot); `position_exits` table separation is a future-hygiene item not in 8A scope.
- **Phase D index migration runner blocker.** `CREATE INDEX CONCURRENTLY` requires the migration-runner upgrade. Couples 8A Phase D to the Week 2 runner work; Phase D cannot ship in isolation.
- **`side='YES'` paired with `side='NO'` for the same market_id is legitimate** (different binary-outcome positions). The proposed partial unique index is on `(market_id, side)` not `(market_id)` — it allows YES + NO simultaneously, only forbids same-side duplicates.
- **Reserving status semantics.** Phase D needs to decide whether `status='reserving'` rows count for the partial unique index. Current proposal: `WHERE status = 'OPEN'` only. If `reserving` rows can also represent live exposure that should block a duplicate open, the predicate widens to `WHERE status IN ('OPEN','reserving')`. Note: SELL audit-trail rows pass through `'reserving'` briefly during exit, so widening the predicate would re-introduce false-positive blocks during a normal exit flow — the narrow `'OPEN'`-only predicate is the safer default.
- **uvloop silent-fallback verified Day 3.** `main.py:631` is `try: import uvloop; uvloop.install() except ImportError: pass`. The drift detector confirmed `uvloop` is missing on the prod venv, so the fallback to default asyncio IS firing. Per S195 Hygiene Backlog: structural fix is Week 2 (freeze-then-replace venv pattern alongside migration-runner upgrade); operator one-off `pip install -r requirements.txt` against the running venv is reversible but risks half-installed packages mid-import. Not 8A-scope; verified here for the reviewer's question.

---

## 6. Evidence of Origin

S195 forward-audit, Days 3–5 calendar (`memory/project_s172_consolidated_plan.md` + the user-supplied Day-1 plan). Prod-data feasibility verified 2026-04-26 via direct `psql` queries through the Lightsail bastion. No commits land 8A semantic-shift today; this doc + the helper module are the Day 3 deliverables.

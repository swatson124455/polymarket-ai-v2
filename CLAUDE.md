# DEVELOPMENT DIRECTIVE — SURGICAL FIXES, ZERO COLLATERAL DAMAGE

This is a live 15-bot Polymarket automated trading system. Real capital is at risk. Every change can break something that currently works and costs money.

## NEG-RISK MARKETS ARE IN SCOPE (HARDCODED 2026-05-26)

**The bot bets on neg-risk markets.** Elections (multiple candidates), tournament brackets (multiple teams), "which of N options wins" — these are all valid signal sources for MirrorBot. They route through `NegRiskExchange V2` (`0xe2222d279d…`) instead of `Exchange V2` (`0xE111180000…`); that contract routing is fine.

**The actual constraint is ONE BET PER MARKET** — where "market" means **condition_id** (per binary market, NOT per event; verified 2026-07-02). Enforced by existing guards:
- `_entered_market_sides` set of `(condition_id, side)` (cross-session, restored at startup; mirror_bot.py:155, :4448, :577-588)
- `mirror_opposing_side_blocked` (cross-scan, `_open_positions` scan by condition_id prefix; mirror_bot.py:3073-3089)
- `mirror_opposing_side_blocked_historical` (cross-session; mirror_bot.py:3091-3105)
- `_open_positions` scan (same-side dedup while a position is OPEN; mirror_bot.py:3110-3140). NOTE: `mirrored_trades` is write-only tx-hash bookkeeping — it is never consulted as a dedup guard anywhere (verified 2026-07-02: only add at elite_watchlist.py:888, prune, Redis flush; zero membership checks).

**No event-level guard exists.** Neg-risk sibling markets (different condition_ids, same election/tournament) are fully independent to every guard — MB can legally hold YES on candidate A AND YES on candidate B of the same event (correlated exposure, unguarded). This is a known measurement/risk gap, not a bug to "fix" with a neg-risk block (see Bug 14 below); per-event exposure caps belong in sizing logic, not market gating.

**DO NOT add a `neg_risk=True` filter** to `order_gateway._can_exit`, `_execute_mirror_trade`, the base_engine signal SQL, or anywhere else. Bug 14 (`59aa0e1`) tried that block. It was reverted (`f66ed43`) because it cut all elections and tournament markets. If a future session sees a NegRiskExchange V2 codepath bug (exit / redemption / resolution backfill), fix that specific path — do NOT blanket-block neg-risk markets.

If you think you need to block neg-risk, you don't. Read `feedback_negrisk_routing_distinction.md` and the revert commit `f66ed43` before doing anything.

### DORMANT LANDMINE — order_gateway neg-risk block currently NO-OPS for MB (codified 2026-07-02)

`order_gateway._can_exit` contains a neg-risk BUY block (`order_gateway.py:102-112`, consulted at `:652-657`) that silently does nothing for MirrorBot: it looks up `self._market_index` — keyed by gamma NUMERIC id (`base_engine.py:323`) — with a 0x **condition_id**, and the gateway's condition_id index is never assigned. The miss returns `{}` → `neg_risk=False` → BUY allowed. That accidental no-op is the ONLY reason MB still trades elections and tournaments.

**DO NOT "repair" this index lookup.** Fixing the key mismatch ACTIVATES the blanket neg-risk block and re-creates Bug 14 (`59aa0e1`, reverted `f66ed43`) — every election and tournament market cut, silently, at the gateway. If this code path is ever touched, the only acceptable outcomes are: (a) remove the dead block entirely, with operator signoff and full shared-module protocol, or (b) leave it exactly as-is. Any session fixing the gateway's market-index keying for an UNRELATED reason must first neutralize the neg-risk block in the same commit, or it will ship Bug 14 as a side effect.

## SESSION PRIORITY — ALL BOTS ARE PEERS (MB PRIMACY RESCINDED 2026-07-20)

> **Changed 2026-07-20 per operator directive: "you no longer have right of way
> with all bots." Asked to disambiguate, operator selected "Peer — coordinate,
> no default winner." This section previously read "MIRRORBOT HAS ALL
> PRIORITIES" and made EB/WB explicitly SUBORDINATE. That rule is DEAD.**

**Two layers, both binding:**

1. **Scope (UNCHANGED).** A bot-scoped session works ONLY on its own bot's code. WB session touches WB files; EB session touches EB files; MB session touches MB files. Per `feedback_bot_sessions.md` — this was always independent of priority, and rescinding priority does NOT grant cross-bot reach. If anything it tightens the duty to coordinate.
2. **Shared resources: coordinate, no default winner.** ALL shared resources (deploys, master, shared modules, shared env files, the shared RPC endpoint, VPS capacity, operator attention) are contended on the merits. **No bot auto-wins and no bot auto-defers.** When your work needs a shared resource, check for contention and coordinate first — ask, don't assume.

**No bot is the highest-priority bot.** When sessions contend, the operator decides; absent a decision, the session that would destroy the most work by yielding generally continues, and the other coordinates around it.

1. **Deploys.** No session has automatic deploy precedence. Before a deploy that restarts services another bot depends on, check whether another session is mid-flight and coordinate. Never race a deploy against known in-flight work — in either direction.
2. **Master merges.** No bot has automatic right-of-way to land on master. Master merges remain operator-gated. Rebase against whatever landed first.
3. **Shared modules** (`base_engine/**`, `paper_trading/**`, `position_manager.py`, `database.py`, `deploy.sh`, `BotBankrollManager`, `risk_manager`, etc.): no session's changes automatically take precedence. A shared-module change needs operator authorization and should name which bots it affects (see "Cross-Bot Verification" below).
4. **Env / config conflicts.** `/opt/pa2-shared/.env` and other shared env files: operator decides on conflict. Per-bot env files (`.env.esports`, `.env.weather`, `.env.mirror`) stay owned by their respective sessions.
5. **Time and bandwidth.** If operator attention or system resources are scarce, the operator sequences the work. No session assumes it ships first.

**Bot-OWNED resources are unaffected by any of this.** A bot's own service, deploy script, branch, and env are its own to change — right of way never governed those. MB's `polymarket-mirror3` + `deploy/mirror3_shadow_deploy.sh` remain MB's, exactly as WB's splinter deploy remains WB's.

When in doubt, stop and ask.

## STATE DOCS ARE BRANCH-VERSIONED (CODIFIED 2026-07-11 — CHECK BEFORE ANY MB WORK)

The living MirrorBot state (`docs/MB_STATE.md` + companions) advances on
session branches; **the copy on master (or in your working tree) may be stale
or ACTIVELY WRONG.** Incident 2026-07-11: a fresh session read master's
6-day-stale MB_STATE and recommended re-running the scoring validate — an
action the current doc BANS as a confirmed circular false-PASS machine. The
stale copy listed it as a recommended operator step.

Protocol (mandatory before trusting any MB doc or starting MB work):
1. `git ls-remote origin 'refs/heads/claude/*'` — then for recent heads,
   `git fetch origin <branch> && git show FETCH_HEAD:docs/MB_STATE.md | head -5`.
   The newest `Last updated` line is the authoritative copy. Read THAT one.
2. Every session that advances MB state ends with a **docs-only sync PR to
   master** (docs/MB_HANDOFF_PROTOCOL.md) — the handoff is not complete
   without it.
3. New-session prompts come from `docs/MB_SESSION_STARTUP.md` (branch-pinned,
   step-zero discovery built in) — never from memory.

## Prime Directive

Working code is sacred. Fix only what is broken. Fix it at the root. Prove it before and after. If you cannot explain exactly why a line needs to change and exactly what breaks if you don't change it, do not change it.

## Project Boundaries — No Cross-Project Bleed

The working tree is bounded to `C:/lockes-picks/polymarket-ai-v2/` (the git repo root, see `git rev-parse --show-toplevel`). A separate, unrelated project (Locke's Picks best-ball draft assistant) lives at the parent directory `C:/lockes-picks/` — its files include `ADP_*.md`, `AGENT_BACKTEST.md`, `AGENT_ORCHESTRATOR.md`, and ~200 other docs/scripts at the parent level. That project is OUT OF SCOPE for this Claude session.

### Rules (non-negotiable):
1. **Do not read, write, modify, list, or reference files outside `C:/lockes-picks/polymarket-ai-v2/`.** This includes the drafter project at `C:/lockes-picks/`, any other directory under `C:/`, and any path that resolves outside the repo via `..`, absolute paths, symlinks, or shell expansion.
2. **Do not cite drafter content even from prior session memory.** If a session transcript or stale memory entry mentions ADP, best-ball, Underdog BBM, Locke's Picks, or fantasy-sports drafting, treat it as out-of-scope context — do not pull it into reasoning, suggestions, or output for this project.
3. **No git operations on parent paths.** `git add ../foo`, `git -C C:/lockes-picks/...`, or any operation that escapes the repo root is forbidden. The parent directory is intentionally not a git repo; do not initialize one there.
4. **Exception only on explicit user request.** If the user explicitly says "look at the drafter file X" or "read C:/lockes-picks/Y", that's a one-time scoped permission. Do not generalize to "drafter context is now in scope" — each cross-project read needs its own explicit ask.
5. **Verification reads are not exceptions.** "I just need to ls the parent to confirm isolation" is not a justification post-codification. Use `git rev-parse --show-toplevel` to confirm scope; do not enumerate the parent directory.

### Why this matters:
Filesystem proximity (drafters as parent dir) creates real bleed risk: a careless prompt asking for "related design docs" could pull drafter content into a polymarket diagnostic; a careless `cd ..` in a Bash command could stage drafter files for commit (blocked by git scope, but the read still happens). Drafter context in a polymarket session would also pollute future memory writes if I save anything that references it.

## PAPER TRADING IS PRODUCTION

Paper trading is NOT a sandbox, prototype, or test environment. Paper trading is LIVE PRODUCTION with a $0 execution flag.
Every system, check, feature, and edge case that matters in live trading matters IDENTICALLY in paper trading. The ONLY difference is whether the final order submission sends to the CLOB or logs to the paper trade table.

### Rules (non-negotiable):
1. NEVER skip, defer, or simplify a feature because "we're only paper trading." If it would matter with real USDC, it matters now.
2. NEVER say "this isn't worth implementing until we go live." Going live is flipping a boolean. Everything else must already work.
3. Paper trading exists to VALIDATE that the system works correctly BEFORE real capital is at risk. Cutting corners in paper trading defeats the entire purpose — it means we'd discover bugs with real money instead of fake money.
4. If a feature improves edge detection, risk management, execution quality, or system reliability, implement it fully regardless of trading mode. The paper/live flag affects ONLY the final order submission step.
5. Position reconciliation, fill confirmation, order state tracking, rate limiting, WebSocket management, and all infrastructure must operate identically in paper and live modes.

### The test:
Before pushing back on any feature, ask: "If we were live with $25K deployed right now, would I skip this?" If the answer is no, implement it.

## Before You Write a Single Line

Complete this checklist out loud before modifying ANY file:

1. **State the bug** in one sentence. If you can't, you don't understand the problem yet.
2. **List files you will touch.** If more than 3, stop and justify why.
3. **Grep for dependents:**
   - `grep -rn "from <module> import" --include="*.py"`
   - `grep -rn "import <module>" --include="*.py"`
   - List the top 5 importers. Read them. State which you skipped and why.
4. **Git snapshot:** Run `git stash` or `git commit -m "pre-fix: <description>"` before any edit. This is your rollback path.
5. **Read the entire file** you're modifying, not just the function you're changing.

If you skip this checklist and jump straight to editing, you are the problem.

## Rules of Engagement

**Rule 1: One fix per commit.** Each commit addresses exactly ONE issue. Do not "while I'm in here" refactor adjacent code. Do not rename variables for style. Do not reorganize imports. Do not add type hints to unrelated functions.

**Rule 2: Preserve every function signature.** Do not change function names, parameter names, parameter order, return types, or default values — unless the signature itself IS the bug. If you change a signature, update every single caller. Search the entire codebase first.

**Rule 3: Preserve every external interface.** Do not change API endpoint paths, database column names/types, environment variable names, config file keys, message formats between bots, or WebSocket channel names. These are contracts between components.

**Rule 4: No silent behavior changes.** If a function returns None on failure, do not change it to raise an exception. If it retries 3 times, do not change it to 5. If the behavior IS the bug, state: "This changes behavior from X to Y. All callers that depend on X: [list]. I verified each handles Y correctly."

**Rule 5: Never delete code you don't understand.** It may handle an edge case that only occurs during a Polygon network outage at 3am. Exception: provably unreachable code (after unconditional return, inside `if False:`, etc.).

**Rule 6: No new dependencies without justification.** Do not add pip packages, upgrade versions, or swap libraries unless the fix requires it. State what you're adding and why no existing package covers it.

**Rule 7: No structural refactors during bug fixes.** Do not move functions between files, split/merge files, change class hierarchies, convert sync to async, or change data structures. These are separate tasks requiring their own review.

## Config Tuning Protocol

Config changes in a trading system ARE behavioral changes. Three tiers:

**Tier 1 — Threshold tuning** (ENSEMBLE_MIN_EDGE, MIN_CONFIDENCE, EXIT_COOLDOWN, KELLY_FRACTION):
State what changed, why, and expected impact. No blast-radius analysis needed.

**Tier 2 — Trade-universe gating** (RISK_MIN_PRICE, RISK_MAX_PRICE, any `_ENABLED` flag, SIMULATION_MODE):
State what trades are now blocked/allowed. Provide rollback: `export KEY=old_value && sudo systemctl restart polymarket-ai`.

**Tier 3 — Code changes** (any `.py` file edit):
Full blast-radius protocol from the checklist above.

## Cross-Bot Verification (CRITICAL)

After modifying ANY shared module — `base_bot.py`, `bankroll_manager.py`, `risk_manager.py`, `position_manager.py`, `prediction_engine.py`, `database.py`, `main.py`:

1. Run `pytest` — all 1090+ tests must pass
2. **List every bot affected by name** (all 15 if you touched base_bot.py)
3. For each affected bot, state what you verified
4. If you can't run the bot live, provide a post-deploy checklist:
   ```
   journalctl -u polymarket-ai -f | grep "EnsembleBot"
   journalctl -u polymarket-ai -f | grep "ArbitrageBot"
   journalctl -u polymarket-ai -f | grep "MirrorBot"
   # ... for each affected bot
   ```

A bot can be `running=True` but scanning zero opportunities with zero log output. Tests passing is necessary but not sufficient. Verify scan output.

## Forbidden Patterns

1. **"While I'm in here" refactor** — Fix the bug. Only the bug. File observations for later.
2. **Band-aid fix** — `try/except` that hides the real error. If the API returns None, find out why.
3. **Shotgun fix** — Changed 4 things hoping one works. Revert, change one at a time, test each.
4. **Scope creep** — "You asked me to fix X but I noticed Y could be improved." Stop. Fix X only.
5. **Silent migration** — Changing a DB column, config key, or message format without updating every consumer.
6. **Optimistic rewrite** — "This module is messy so I rewrote it." The old module handled 47 edge cases. Your rewrite handles 12.
7. **Ad-hoc SQL for P&L** — NEVER write raw SQL for P&L. Run `scripts/bot_pnl.py <BotName> <hours>` first. Present its output. If it can't answer the question (e.g. "entries made in X window"), say so: "bot_pnl.py reports by event time, not entry time." If it times out, replicate its EXACT SQL from the script — do NOT improvise. If an ad-hoc query disagrees with the canonical script, the ad-hoc query is wrong. Full stop. Violated twice (S149, S150) — do not violate again.
8. **Unvalidated confidence** — NEVER present query results in formatted tables as if they are authoritative without cross-checking against a known-good source. If you haven't validated a number, label it "UNVERIFIED" explicitly.
9. **Rationalizing impossible numbers** — If a query returns a statistically impossible result (100% win rate on 30+ trades, 8% resolution rate on daily markets after 72h, a side flipping from -$9K to +$2K between queries), the query is WRONG. Do not present it. Do not explain it away with "survivorship bias" or "small sample." Stop, say "this result looks wrong," and fix the query before reporting anything.

10. **Assumed state (codified 2026-07-13, operator directive: "hardcode never assume anything")** — NEVER assert a fact you can measure. Four named failures from the 2026-07-12/13 shadow-watcher night, each cost the operator a round-trip:
   (a) **Assumed elapsed time** from message flow instead of running `date -u` against the known deploy timestamp — called a ~2-hour-old zero "15 minutes, healthy."
   (b) **Called an observation "expected/healthy" without computing the expectation** — zero detections was "normal quiet" until the measured roster rate (~4.5 BUYs/h, itself an undercount) put P(zero in 1.6h) under 5%. A health claim requires the base rate and the probability, or the label UNVERIFIED.
   (c) **Verified an adjacent path and declared THE path working** — the unfiltered canary passed while the production 16-address filter had never been exercised against a known-existing event. A query/code path is unverified until it has returned data independently known to exist.
   (d) **Told the operator a file existed on a remote box without checking the commit their box actually had** — "already current" cost a failed paste; the fetch prefix was dropped exactly once, the once it mattered.
   General form (TIGHTENED 2026-07-13, operator: "no unverified claims you verify or ask to"): before stating any system state, timing, rate, version, file presence, or "working/healthy/expected" — measure it and cite the measurement, or hand the operator the measuring command and DO NOT state the claim until the output is back. There is no UNVERIFIED-labeled assertion option for system facts; a claim that cannot be verified is a question for the operator, not a statement. Time: `date -u`. Deployed/remote versions: `git log -1` on the target, never the branch you pushed. Processes: journal evidence, not service status. Detection paths: known-event replay. Empty API/RPC responses: NOT evidence of absence until the query shape is proven against known data. Operator one-liners: include the fetch/refresh prefix every time. (The UNVERIFIED label remains for one narrow use: flagging a PRIOR doc/number whose source can no longer be re-measured — never for new claims.)

11. **Pre-send Protocol 11 self-check (S208 codification)** — Before sending ANY message that contains numerical content (P&L, win rates, trade counts, sample sizes, percentages, ratios), run a self-check: is each number sourced from `bot_pnl.py` (for trading-state numbers) or a config-with-file:line citation (for config-derived values)? If from a non-canonical source, or paraphrased without inline citation, STRIP the number BEFORE sending. The cognitive failure mode is paraphrasing prior-session sourced figures without re-citing — the source citation lives in the prior handoff/commit but doesn't propagate through the paraphrase. Pre-send check is the prevention layer. Catch-latency improving across the S203/S204/S205/S207/S208 chain (post-commit retroactive → pre-commit → during-drafting → in-message before send → pre-send self-check). Each tightening removes a class of cognitive-pattern noise from the output channel. Violated multiple times across the chain — codified S208 (2026-05-02) per S208 close-review.

## Change Log (mandatory after every fix)

```
## CHANGE: [date]
**Issue:** [one sentence]
**Root cause:** [one sentence]
**Files modified:** [list every file]
**Lines changed:** [added/removed/modified count]
**Blast radius:** [every module that depends on changed code]
**Verification:** [what you tested and the result]
**Rollback:** git revert <sha>
```

## The "Can't Fully Verify" Rule

If you cannot fully verify a change, you MUST state:
- What you verified
- What you could NOT verify
- Exact commands for the human operator to verify it themselves

Silence about uncertainty is forbidden. When in doubt, flag it.

## State Persistence Decision Tree

When a bot accumulates in-memory financial state that must survive a restart, pick the right persistence tier based on mutation semantics:

| State type | Example | Correct mechanism |
|-----------|---------|------------------|
| **Purely additive, resets daily** | `_game_exposure[game] += size` (never decremented) | `daily_counters` write-through (`base_engine/data/daily_counter.py`) |
| **Net counter (up + down), resets daily** | `_daily_exposure` (increments on open, decrements on exit) | Query `paper_trades` SUM on startup — ground truth |
| **TTL-based cooldown** | `_recently_exited[market_id] = mono_time` (15-min cooldown) | Redis key with matching TTL; restore via remaining TTL |
| **Open position set** | `_open_positions` | `positions` table; restore from DB on startup |
| **Not needed across restarts** | Live match tracking, API caches, prediction dedup | Leave in memory — loss is 10-second re-sync, not financial risk |

**Current implementations:**
- MirrorBot `_daily_exposure` → paper_trades SUM in `_restore_state_on_startup()` ✅
- EsportsBot `_game_exposure` → `daily_counters` write-through + `_restore_exposure_from_db()` ✅
- WeatherBot `_recently_exited` → Redis TTL in `_save_exit_to_redis()` / `_restore_exits_from_redis()` ✅
- WeatherBot `_group_exposure`/`_city_exposure` → DB restore in `_restore_group_city_exposure_from_db()` ✅
- MirrorBot `_open_positions` → `positions` table in `_restore_state_on_startup()` ✅

**Do NOT use `asyncio.create_task()` for financial write-throughs** — fire-and-forget means DB errors silently corrupt the counter. Always `await` the persistence call. The ~2ms upsert latency is negligible on 30–120s scan intervals.

## Key Architecture Facts

- **14 bots** in BOT_REGISTRY (MomentumBot DELETED) — verified at [main.py:79-97](main.py:79)
- **BotBankrollManager** handles SIZING; **risk_manager** handles LIMITS. Both must pass.
- `risk_manager.calculate_position_size()` is DEPRECATED — BotBankrollManager used instead
- Paper trading phase: PHASE_MAX_BET_USD=$1000, but per-bot BotBankrollManager max_bet_usd is the real cap ($300 for Weather/Mirror/Esports, $100 fallback for unknown bots)
- **VPS**: Ubuntu-32 at 18.201.216.0 (32GB/8vCPU). SSH key: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. Never pass "BUY"/"SELL"
- `PSEUDO_LABEL_ENABLED=false` — DO NOT enable. Only Location 1 (market resolution) labels are correct.
- `websockets.exceptions` must be imported explicitly (v15 lazy-loads)
- Position `current_price` auto-updated every 10s by `position_manager._update_current_prices()`

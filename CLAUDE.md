# DEVELOPMENT DIRECTIVE — SURGICAL FIXES, ZERO COLLATERAL DAMAGE

This is a live 15-bot Polymarket automated trading system. Real capital is at risk. Every change can break something that currently works and costs money.

## Prime Directive

Working code is sacred. Fix only what is broken. Fix it at the root. Prove it before and after. If you cannot explain exactly why a line needs to change and exactly what breaks if you don't change it, do not change it.

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

- **15 bots** in BOT_REGISTRY (MomentumBot DELETED)
- **BotBankrollManager** handles SIZING; **risk_manager** handles LIMITS. Both must pass.
- `risk_manager.calculate_position_size()` is DEPRECATED — BotBankrollManager used instead
- Paper trading phase: PHASE_MAX_BET_USD=$1000, but BotBankrollManager max_bet_usd=$100 is the real cap
- **VPS**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU). SSH key: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. Never pass "BUY"/"SELL"
- `PSEUDO_LABEL_ENABLED=false` — DO NOT enable. Only Location 1 (market resolution) labels are correct.
- `websockets.exceptions` must be imported explicitly (v15 lazy-loads)
- Position `current_price` auto-updated every 10s by `position_manager._update_current_prices()`

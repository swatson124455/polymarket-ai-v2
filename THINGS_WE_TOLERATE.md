# Things We Tolerate — Registry

**Purpose:** Track known-tolerated conditions so each session doesn't re-investigate them from scratch. Owned by whichever session next investigates an entry.

**Convention:**
- Item description (specific, measurable)
- When last measured (date)
- Owner session (who investigates next if condition changes)
- Threshold for action (what would elevate this from "tolerated" → "fix")
- Notes / context

**Filed:** S230 (2026-05-26) per Guardrail C of `feedback_infra_hygiene_guardrails.md`.

---

## Active toleration entries

### CLOSE-WAIT sockets post-S230 restart

| Field | Value |
|---|---|
| Item | TCP CLOSE-WAIT count per polymarket bot process |
| Last measured | 2026-05-26 16:30 UTC (post-restart): EB=1, MB=7, WB=3, ingestion=0 |
| Pre-restart baseline | 2026-05-25: EB=103, MB=7, WB=3, ingestion=0 (91% concentrated on EB pid=95495) |
| Owner | EB session (per `EB_COORDINATION_CLOSE_WAIT_LEAK.md`) |
| Threshold for action | EB count >25 OR growth rate >5/day |
| Re-check by | 2026-06-02 (7 days post-restart) and weekly thereafter |
| Notes | S230 4-service restart at 2026-05-26 16:15:25 UTC flushed the EB-accumulated leaked sockets but did NOT fix the underlying leak code path. Re-accumulation rate is the diagnostic signal: if EB hits 25+ in 7 days, the leak is still active at the same rate as pre-restart. Coordination doc filed for next EB session pickup. |

### /opt/polymarket-ai orphan tree (relocated to pa2-backups)

| Field | Value |
|---|---|
| Item | `/opt/pa2-backups/orphan_polymarket-ai_20260526` (6.1 GB, relocated from `/opt/polymarket-ai`) |
| Last measured | 2026-05-26 14:25 UTC — size 6.1 GB, polymarket-ai.service masked+inactive, no active references in `/etc/systemd/` after S230 cleanup |
| Owner | MB session (any) |
| Threshold for action | Final deletion 7+ days after relocation (i.e., on/after 2026-06-02) if no rollback needed |
| Re-check by | 2026-06-02 — confirm no operational issues; then `rm -rf` to reclaim disk |
| Notes | Pre-v2 install path. Service is masked/dead, no inbound references after dashboard unit cleanup (S230). Reversible mv to /opt/pa2-backups gives 7-day safety net. |

### /opt/polymarket-ai-v2_pre_migration orphan

| Field | Value |
|---|---|
| Item | `/opt/pa2-backups/orphan_polymarket-ai-v2_pre_migration_20260526` (192 MB) |
| Last measured | 2026-05-26 14:25 UTC |
| Owner | MB session (any) |
| Threshold for action | Same as above — final delete on/after 2026-06-02 |
| Notes | Migration leftover. Same 7-day safety window. |

### Pool-near-exhaustion CRITICAL warnings (silenced by threshold bump)

| Field | Value |
|---|---|
| Item | `pool near exhaustion` CRITICAL events in journalctl |
| Pre-S230 rate | ~7/day per service (verified MB journal 24h sample, 2026-05-25) |
| Threshold change | DB_EFFECTIVE_POOL_SIZE 60→75 (S230, 2026-05-26 16:15 UTC) raises the warning threshold from 54 to 67.5 |
| Owner | MB session (any) |
| Threshold for action | If post-bump rate >2/day per service in any 7-day window after 2026-06-02, the bump didn't actually move the metric — investigate (could mean threshold change was wrong, OR the underlying pool sum vs PgBouncer ceiling is hitting actual saturation, OR third cause) |
| Notes | This is a NOISE-INDICATOR fix, not a capacity expansion. PgBouncer ceiling is still 60+5=65. Actual structural over-subscription (sum-of-pools=71) remains until WB pool right-size (`WB_COORDINATION_POOL_RIGHTSIZE.md`) lands. After WB right-size, DB_EFFECTIVE_POOL_SIZE could return to 60 with no false warnings. |

### Streamlit dashboard uptime (drift hygiene)

| Field | Value |
|---|---|
| Item | `polymarket-dashboard` process uptime |
| Last restart | 2026-05-26 14:28:51 UTC (S230 hygiene restart, fresh PID 131200) |
| Owner | MB session (any) |
| Threshold for action | Uptime >60 days OR memory usage >400 MB (against 512 MB cap) |
| Notes | Restart every ~30 days as preventive hygiene. Streamlit + long-running Python can drift; cheap to restart. |

### USDC.e idle balances (deposit wallet + EOA)

| Field | Value |
|---|---|
| Item | $20 USDC.e on DEPOSIT, $16 USDC.e on EOA — bot only uses pUSD; these sit idle |
| Last measured | 2026-05-26 (per WALLET_LEDGER.md Current state) |
| Owner | Operator decision |
| Threshold for action | If operator wants to convert to pUSD or withdraw, document in WALLET_LEDGER.md as pending move |
| Notes | Origin tx hashes older than RPC retention (~28h). Polygonscan API key would close trace gaps. Not actively harmful — just unused capital. |

### CTF tokens — 3 resolved-and-lost (dormant)

| Field | Value |
|---|---|
| Item | 3 CTF outcome tokens (DB ids 187436, 187437, 187439) for resolved-losing positions |
| Last measured | 2026-05-26 — all 3 markets on-chain `payoutNumerator=0` for YES side, dormant in deposit wallet |
| Owner | Operator decision |
| Threshold for action | None — ERC-1155 standard doesn't auto-burn; redemption-to-burn costs gas + returns $0; no financial reason to act |
| Notes | Will sit in wallet forever as zero-value entries. Polymarket UI shows them as resolved-and-lost portfolio history. Documented in WALLET_LEDGER.md "Resolved — losing tickets, dormant" section. |

---

## Removed entries (resolved or no longer applicable)

(none yet)

---

## How to add a new entry

When a session investigates something and concludes "tolerated":
1. Add a row to the appropriate section
2. Fill all fields — especially `Threshold for action` (the condition under which a future session should re-investigate)
3. Cite source/measurement command
4. Set `Re-check by` date if applicable

When a session resolves a tolerated condition (e.g., EB leak gets fixed):
1. Move entry to "Removed entries" with date + how resolved
2. Update related coordination docs to reflect closure

---

## Safety-mechanism terminal states (WI-7 audit, S235 2026-05-31)

### CircuitBreaker — auto-escalation every 30 min (pre-WI-7)

| Field | Value |
|---|---|
| Item | `CircuitBreaker` in `base_engine/execution/execution_engine.py` — consecutive escalation auto-clears after 30 min; no prior ceiling on escalation count |
| **Status as of S235** | **FIXED (WI-7):** `permanently_halted=True` after `max_consecutive_escalations=3` consecutive escalations without a success. Only cleared by explicit `reset()`. |
| WI-7 fix | `consecutive_escalation_count` + `permanently_halted` flag + `reset()` method in `execution_engine.py:CircuitBreaker` (S235) |
| Last verified | S235 2026-05-31 |
| Verification method | `grep -n "permanently_halted\|consecutive_escalation" base_engine/execution/execution_engine.py` |
| Threshold for action | Any CRITICAL log containing `PERMANENT_HALT` requires immediate operator response — do not auto-clear without diagnosing the root cause |

### BotKillSwitch — 60-minute auto-reset (tolerated, not production-active)

| Field | Value |
|---|---|
| Item | `BotKillSwitch.is_killed()` auto-resets after 60 min (`_auto_reset_minutes=60`) with no consecutive-kill ceiling |
| Status | TOLERATED — `BotKillSwitch.kill_bot()` is not called by any production code path (only defined in class, as of S235 audit). The auto-reset anti-pattern is real but inert. |
| Owner | MB session (any) |
| Threshold for action | If `kill_bot()` is wired into production, add consecutive-kill counter + PERMANENT_HALT before enabling it |
| Last verified | S235 2026-05-31 — `grep -rn "\.kill_bot(" base_engine/ bots/ --include="*.py"` returned zero production callsites |

### KillSwitch (base) — requires explicit disengage ✅

| Field | Value |
|---|---|
| Item | `KillSwitch` in `base_engine/coordination/kill_switch.py` |
| Status | ACCEPTABLE — `engage()` persists in DB (`system_config.kill_switch='true'`) until explicit `disengage()` call. No auto-clear. Terminal state = engaged until operator resets. |
| Last verified | S235 2026-05-31 — code read `base_engine/coordination/kill_switch.py` |

### PortfolioKillSwitch / SystemKillSwitch — delegate to base ✅

| Field | Value |
|---|---|
| Item | Both classes in `multi_kill_switch.py` delegate `is_engaged()` to base `KillSwitch` |
| Status | ACCEPTABLE — `PortfolioKillSwitch._auto_reset_hours=24` is stored but never used in `is_engaged()` (no auto-reset code). Requires explicit `disengage()`. |
| Note | `SystemKillSwitch._system_killed` flag is in-memory; a restart clears it. But restarting also re-reads `system_config.kill_switch` from DB — if the base is still engaged, the system remains halted. Effective terminal state = engaged until DB is cleared. |
| Last verified | S235 2026-05-31 — code read `multi_kill_switch.py` |

### Slippage backoff — max 15-min retry cadence (tolerated with Bug 21)

| Field | Value |
|---|---|
| Item | `mirror_bot._slippage_backoff`: exponential backoff capped at 900s (15 min). No absolute ceiling on retry count. |
| Status | TOLERATED — Bug 21 (S233) handles the "terminal" failure class (resolved/delisted markets: token pinned to 0.001/0.999). For structural failures on active markets, max cadence is 15 min. |
| Owner | MB session |
| Threshold for action | If a position has been in slippage backoff for >24h without clearing, investigate. WI-6 (position lifecycle module) is the structural fix. |
| Last verified | S235 2026-05-31 — `grep -n "_slippage_backoff\|_slippage_fail_count" bots/mirror_bot.py` |

### mid-runtime mode-flip guard (Bug 12) — permanent guard ✅

| Field | Value |
|---|---|
| Item | Bug 12 in `mirror_bot.py` detects SIMULATION_MODE flip mid-runtime and skips exits/entries (`bug12_mode_flip_detected_*` logs) |
| Status | ACCEPTABLE — permanent guard, not a periodically-firing mechanism. Fires once when flip is detected; requires restart to clear (which reinitializes SIMULATION_MODE from env). |
| Last verified | S235 2026-05-31 — `grep -n "bug12_mode_flip" bots/mirror_bot.py` |

---

## Review checkpoints (WI-12, S235 2026-05-31)

Operator: add recurring calendar items for the following.

| Checkpoint | Cadence | Trigger for action |
|---|---|---|
| Safety-mechanism terminal states (WI-7) | Quarterly | Any PERMANENT_HALT log in journalctl; OR new safety mechanism added to codebase |
| BotKillSwitch auto-reset | On any code change that wires `kill_bot()` to production | Immediate: add consecutive-kill counter + PERMANENT_HALT before enabling |
| DB constraints (WI-8 migration 078) | After any host rebuild or DB migration | `SELECT COUNT(*) FROM pg_constraint WHERE conrelid='positions'::regclass AND conname LIKE 'chk_positions_%'` must return 4; deploy.sh preflight warns if not |
| WI-11 audit discrepancy log | Monthly | `journalctl -u polymarket-mirror | grep "position_audit_discrepancy"` — if >3 consecutive discrepancies for the same market, CRITICAL log should have already fired; investigate root cause |
| THINGS_WE_TOLERATE review | Monthly | Elevate any tolerated condition that has hit its threshold-for-action |
| WI-2b deploy-block log | After every deploy block | Update `WORK_PROGRAM.md` deploy-block log; if >1 MB block/month × 2 consecutive months, promote WI-17 |
| Wallet LEDGER.md | After each trade-significant event (deposit, withdrawal, redemption) | `python scripts/update_wallet_ledger.py` on VPS; verify balance is consistent |

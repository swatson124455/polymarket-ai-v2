# EB Session Handoff — 2026-05-26

**Date:** 2026-05-26 (single EB-session day, follow-on to AGENT_HANDOFF_EB_2026-05-25.md)
**Branch:** `eb/main` (HEAD `07df9a2` + this handoff commit)
**Worktree:** `C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main/`
**Master HEAD:** `75d5a90` (cherry-pick of `9043aea` resolution-backfill fix)
**Active VPS releases:**
- Master: `/opt/pa2-releases/20260526_120918`
- EB splinter: `/opt/pa2-esports-releases/20260526_115538`

**Status at close:** Resolution-mismatch bug class fixed forward (Layer 1 live in master); historical cleanup landed via manual SQL but the committed cleanup script is broken; 9 of 10 markets pending phase4b re-emission cycles; one critical pre-existing ingestion bug flagged.

---

## §1 — What landed this session

### Commits on `eb/main` (9 new)

| Commit | Layer | Purpose |
|---|---|---|
| `9043aea` | Layer 1 | `_clob_to_market_format` + `run_resolution_backfill:426` prioritize outcome_prices over text fields. 71 targeted tests passed. |
| `40d119d` | CLOSE-WAIT | `EsportsLiveBot.stop()` closes `_market_service`. 43 targeted tests passed. |
| `ea0b762` | CLOSE-WAIT | `EsportsBot v1 stop()` closes `_riot_client`. 87 targeted tests passed. |
| `b07e2d7` | Layer 3 | `scripts/cleanup_eb_resolution_mismatches_2026_05_26.py` — **broken** (see §3 carry-forward). |
| `fc01947` | Splinter | `deploy/polymarket-esports-ingestion.service` systemd unit (NOT enabled — filter logic pending). |
| `a8f8b65` | Doc | `EB_COORDINATION_RESOLUTION_VS_CHAIN.md` initial draft. |
| `07df9a2` | Doc | Coordination memo updated with all-time findings + next-session steps. |

(Earlier commit `0752852` from 2026-05-25 zero-stake-skip log was deployed today via EB splinter.)

### Commits on `master` (1 new, cherry-pick)

| Commit | Source | Purpose |
|---|---|---|
| `75d5a90` | cherry-pick of `9043aea` | Layer 1 resolution-backfill fix landed on master and deployed. |

### Deploys

| Stamp | Path | What |
|---|---|---|
| `20260526_115538` | EB splinter `/opt/pa2-esports-releases/` | Carries `0752852` zero-stake log + `9043aea` resolver fix + `40d119d` + `ea0b762` CLOSE-WAIT fixes + `b07e2d7` cleanup script + `fc01947` systemd unit. Restart polymarket-esports only. HEALTH_WARN on Gate 3 (expected VPS infra). |
| `20260526_120918` | Master `/opt/pa2-releases/` | Carries cherry-pick `75d5a90`. Restarted **all 4 services** (polymarket-weather, polymarket-mirror, polymarket-esports, polymarket-ingestion) per explicit operator authorization. HEALTH_WARN on Gate 3. |

---

## §2 — Bug class fixed: resolution-vs-chain mismatch

### Discovery

Operator P&L sanity check ("how do I know this is correct?") on 2026-05-26 14-day window prompted chain verification. Used `clob.polymarket.com/markets/{condition_id}` `outcome_prices` and `tokens[].price` as ground truth.

### All-time verification quantification

Background agent verified all 379 EB-family RESOLUTION rows (count source: `bot_pnl.py EsportsBotV2 336` canonical all-time RESOLUTION = 378 raw + 1 NON_POLYMARKET_ID `577295`).

| Category | Count |
|---|---|
| MATCH | 368 |
| MISMATCH PHANTOM_GAIN | 3 |
| MISMATCH PHANTOM_LOSS | 5 |
| MISMATCH PHANTOM_ZERO | 2 |
| NON_POLYMARKET_ID skipped | 1 |

Bug class is **bidirectional**. Same writer used by MirrorBot and WeatherBot — they almost certainly have proportional mismatches. See §3 carry-forward #18.

### Root cause

`base_engine/data/resolution_backfill.py`:
1. `_clob_to_market_format` (line ~80) derived `resolution` from `tokens[].winner` flag iteration. Observed missing/wrong on some markets at the resolution window.
2. `run_resolution_backfill` (line ~426) trusted gamma-api text fields (`m.get("resolution") or m.get("outcome") or m.get("resolutionPrice")`) FIRST, with `_infer_resolution_from_outcome_prices` only as fallback. Polymarket gamma-api returns `null` or stale `"Pending - market scheduled for..."` strings on some settled markets.

### Layer 1 fix (shipped in `9043aea` + cherry-pick `75d5a90`)

Both call sites now prefer the numeric `outcome_prices` array FIRST. Text fields are fallback only, with stale-text guards (`"pending"`, `"tbd"`, `"scheduled"`, etc. coerced to None).

**Verification path for next session:**

```bash
KEY="~/.ssh/LightsailDefaultKey-eu-west-1.pem"
# Confirm fix is on master path (line moved post-cherry-pick; grep for marker text)
ssh -i "$KEY" ubuntu@18.201.216.0 "grep -n 'outcome_prices (numeric, reliable) is now PRIMARY' /opt/polymarket-ai-v2/base_engine/data/resolution_backfill.py"
# Confirm fix is on EB splinter
ssh -i "$KEY" ubuntu@18.201.216.0 "grep -n 'outcome_prices (numeric, reliable)' /opt/polymarket-ai-v2-esports/base_engine/data/resolution_backfill.py"
```

### Layer 3 historical cleanup (DB state)

10 known-bad markets had `markets.resolution` AND `paper_trades.resolution` updated to chain truth + their bad `trade_events.RESOLUTION` rows DELETEd. Two SQL transactions used `SET LOCAL session_replication_role = 'replica'` to override the `trg_trade_events_immutable` and `paper_trades` mutation triggers — single transaction each, triggers fully restored after.

**State at close:**
- All 10 markets' `markets.resolution` and `paper_trades.resolution` match chain truth.
- 1 of 10 RESOLUTION rows re-emitted by phase4b with chain-correct values.
- 9 of 10 pending phase4b cycles (LIMIT 500 / backlog queue).
- The committed cleanup script (`b07e2d7`) does NOT touch `paper_trades.resolution` — **broken for future re-use**. See §3 #14.

---

## §3 — Carry-forward (tasks 13-18 filed this session, plus #8)

| # | Task | Why |
|---|---|---|
| **#8** | Splinter `polymarket-esports-ingestion.service` filter logic | Multi-hour: `INGESTION_BOT_FILTER_{MODE,LIST}` env-respect in `resolution_backfill.py` Phase 2a+2b + `database.py` phase4b + phase4b-alt SQL queries. Less urgent now that master carries the Layer 1 fix. Systemd unit on disk (`fc01947`); do NOT enable until filter is implemented (race with master ingestion). |
| **#13** | Fix `endDate` vs `endDateISO` field-name mismatch in `data_ingestion.py` | CRITICAL ingestion health flag in journalctl: 89% of markets (173588/195942) have `end_date_iso=NULL`. Pre-existing, not introduced by today's fix. |
| **#14** | Fix `cleanup_eb_resolution_mismatches_2026_05_26.py` to UPDATE paper_trades.resolution | Script as shipped is broken — only touches `markets.resolution`. The manual corrective SQL did the right thing out-of-band. Re-run cases need the script fixed. |
| **#15** | Verify remaining 9 of 10 markets re-emit | Phase4b queue-limited. Check `trade_events` for the 10 condition_ids in 15-30 min; then `bot_pnl.py EsportsBotV2 720` for corrected canonical totals. |
| **#16** | Investigate `ingest_everything` traceback | 1 Python traceback at `data_ingestion.py:2223` since master deploy. Layer 1 fix did not touch that line — likely pre-existing. |
| **#17** | CLOSE-WAIT regrowth observation 24-48h post-deploy | Per `EB_COORDINATION_CLOSE_WAIT_LEAK.md`: count CLOSE-WAITs on new polymarket-esports PID. Expected <10 if both fixes effective. |
| **#18** | MB/WB own historical resolution-bug-class quantification | Same writer. Out of EB scope. |

### List of 10 cleaned-up condition_ids (for verification re-runs)

```
0x5bcc52fb0270567e273a77d5446835621639596ab45c30552a3e950a0b81b909   chain=YES (was wrong NO)
0xeb1c74502bbfed5e3fd95997fe2ceefc53e8cbf12f266b892766f923f297c41c   chain=NO  (already correct)
0x1ddb3154b132640aca2454cdb27b5e5a84d4f8b57afbce7c7ccc5f62d776805b   chain=YES (was wrong NO)
0x73d8e486ccd4bcda76aae2054bbbb9d5db54a750f783208a98d429c63e0f3807   chain=YES (was wrong NO)
0x7abae048de3adaa94b32c9cd19f4003cbabf8219ce790a05681e8027c8902e5f   chain=YES (was wrong NO)
0x9ed21dd7e558c5ca10944cf5c9942373ee4e140d02e4e8bcf4b330cd49b79e79   chain=YES (was wrong NO)
0x49e0e5ddc6a0c2d0cc8b6223d3d8687a4d590d9a1d0b7ea650dfd5882feba775   chain=YES (was wrong NO)
0x1b4aab46cc77c13dd304f74a308a7fca668b7e593174915469075a88d2d25bdc   chain=YES (was wrong NO)
0x39c58e4ddea0d1a8d213e00fd8604bd92719249f6d2aeeca594f1179ba333ee1   chain=YES (was wrong NO)
0x2bb3c938279b17cd13efb728740100cbfcfbc1c699774480d42fa6d83dd4fa36   chain=YES (was wrong NO)
```

---

## §4 — CLOSE-WAIT leak fixes (Tasks #11)

Per `EB_COORDINATION_CLOSE_WAIT_LEAK.md` (filed by MB session S230). Pre-restart measurement: 103 sockets on EB pid 95495. Investigation identified two root causes, both shipped:

1. **`EsportsLiveBot.stop()`** never called `self._market_service.close()`. The EB-Live bot extends BaseBot (not EsportsBot) so `super().stop()` didn't know about it. Service holds a persistent `httpx.AsyncClient` + background refresh task. Fixed in `40d119d`.
2. **`EsportsBot v1 stop()`** never closed the `RiotApiClient` held via `PatchDriftDetector`. With v1 reactivated post-S223, the client was leaking each restart. Fixed in `ea0b762` (promoted local `riot_client` to `self._riot_client` storage, added close in `stop()`).

Both attrs pre-initialized to `None` in `__init__()` for stop()-before-start() safety.

**Next session verify (24-48h post-deploy `20260526_115538`):**

```bash
KEY="~/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@18.201.216.0 "
PID=\$(systemctl show -p MainPID --value polymarket-esports)
sudo ss -tnp state close-wait 2>/dev/null | grep -c \"pid=\$PID,\"
"
# Expected: stays below 10 sustained
```

---

## §5 — Coordination memos shipped

| File | Path (master root via eb/main worktree) |
|---|---|
| `EB_COORDINATION_RESOLUTION_VS_CHAIN.md` | Filed for MB session visibility per the WB/EB precedent. Documents Layer 1+2+3 plan and current ship state. |

Pre-existing coordination memos at master root (unchanged this session):
- `EB_COORDINATION_CLOSE_WAIT_LEAK.md` (S230) — now satisfied by today's fixes; could be marked complete after §4 24-48h verify.
- `WB_COORDINATION_POOL_RIGHTSIZE.md` (S230) — out of EB scope.

---

## §6 — Next-session entry protocol

```bash
# Worktree silo
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main
git rev-parse --abbrev-ref HEAD                     # must print: eb/main

# VPS health gate before any trading-related work
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@18.201.216.0 "mpstat 1 1 | tail -2"
# %steal < 10% → proceed; > 25% → defer

# Carry-forward verifications (in priority order):
# 1) re-emission status for the 10 markets — task #15
# 2) CLOSE-WAIT count post-deploy — task #17
# 3) endDate/endDateISO fix scope analysis — task #13
# 4) cleanup script paper_trades patch — task #14
```

---

## §7 — Splinter charter status (unchanged)

EB session is autonomous per the 2026-05-24 operator directive. Today's master cherry-pick was an explicit one-time deviation authorized for the resolution-bug fast path. Going forward, EB session continues splinter-only unless operator authorizes another master touch.

The unfinished `polymarket-esports-ingestion.service` (task #8) is the remaining architectural work. Master's `polymarket-ingestion.service` now carries the Layer 1 fix, so the splinter ingestion is functionally non-urgent — finish when a fresh session has full context budget.

---

## §8 — Operator decisions still open

| Decision | Owner |
|---|---|
| `BOT_ENABLED_ESPORTS_LIVE` flag (still `false` per `.env.esports`) | Operator |
| Whether to enable polymarket-esports-ingestion.service eventually | Operator + future EB session |
| Whether MB/WB sessions take ownership of their historical resolution mismatches | MB/WB sessions |

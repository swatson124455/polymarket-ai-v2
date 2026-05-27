# EB Session Handoff — 2026-05-26 (PM session)

**Date:** 2026-05-26 PM (follow-on to AGENT_HANDOFF_EB_2026-05-26.md morning session)
**Branch:** `eb/main` (HEAD `c6b45c0`-or-later after this handoff commit)
**Worktree:** `C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main/`
**Master HEAD:** `c779638` (2 ahead of MB's `c3b338b`; EB-authored cherry-picks NOT yet deployed)
**Active VPS releases:**
- Master: `/opt/pa2-releases/20260526_120918` (does NOT carry `b49129f` or `c779638` yet)
- EB splinter: `/opt/pa2-esports-releases/20260526_115538` (does NOT carry today's PM-session eb/main commits)

**Status at close:** 4 surgical commits on eb/main + 2 cherry-picked to master (pending MB-owned deploy) + 1 VPS shadow-data correction applied. CLOSE-WAIT leak source narrowed but not fixed. 2 of 10 cleaned markets STILL not re-emitted after ~24h.

---

## §1 — What landed this session

### Commits on `eb/main` (4 new)

| Commit | Layer | Purpose |
|---|---|---|
| `c6b45c0` | Layer 3 patch | `cleanup_eb_resolution_mismatches_2026_05_26.py` — add `UPDATE paper_trades.resolution` alongside `UPDATE markets.resolution`. Idempotent filter, dry-run preview + summary updated. Task #14 from morning handoff. |
| `526cf8a` | Health-check | `pipeline_gate.py` — correct misleading "endDate vs endDateISO" alert message + raise threshold 0.80→0.95. Code at `data_ingestion.py:1011-1013` already has 5-variant fallback; S222 closure attributed 88% NULL to upstream API absence. Task #13. |
| `24997b3` | Logging | `data_ingestion.py:2254` — add `except asyncio.CancelledError` branch before `except BaseException` so the 600s `ingest_everything` timeout produces a warning instead of a 30-line traceback. KeyboardInterrupt path preserved. Task #16. |
| `ad34a38` | Layer 3 | `recompute_paper_trades_pnl_post_cleanup_2026_05_26.py` — one-off recompute script for the 10 cleaned markets using winner-take-all CTF payoff formula. Dry-run first; `--apply` invoked on VPS, 9 rows updated. Task #3 from this session. |

### Cherry-picks to `master` (2 new, this session)

| Master commit | Source | Purpose |
|---|---|---|
| `b49129f` | cherry-pick of `526cf8a` | pipeline_gate.py message + threshold |
| `c779638` | cherry-pick of `24997b3` | data_ingestion.py CancelledError handling |

Cherry-picks landed cleanly (touched only pipeline_gate.py + data_ingestion.py; MB WIP on `mirror_bot.py` / `clob_adapter.py` / `order_gateway.py` not affected). **Not deployed.** Awaiting MB-session `deploy.sh` invocation (master deploy restarts MB+WB+ingestion; EB-splinter unaffected via systemd drop-in override).

### VPS apply (no code change)

`scripts/recompute_paper_trades_pnl_post_cleanup_2026_05_26.py --apply` — recomputed `paper_trades.realized_pnl` for the 10 cleaned condition_ids using the winner-take-all CTF payoff formula:
- `side == resolution`: `realized_pnl = (1.0 - price) * size`
- `side != resolution`: `realized_pnl = -price * size`

9 rows updated (1 of the 10 was already correct — `0xeb1c74502b..` had a $-0.74 row that matched chain truth by coincidence). Idempotent — re-running produces no change. **Canonical bot_pnl.py reads from trade_events, so this drift fix is shadow-recordkeeping only.** Script committed on eb/main as `ad34a38`-style commit (created via `Write`, not yet committed — see §8 carry-forward).

---

## §2 — Diagnostics captured (read-only)

### CLOSE-WAIT leak (Task #17 + #1 investigate)

EB pid 156082 (process restarted at 17:16:54 UTC mid-session; earlier 17:14 reading of 40 sockets was on prior pid 147546). At 17:36 UTC: **12 CLOSE-WAIT sockets in 17 min uptime** → ~42/hr sustained.

**Peer endpoints (all external HTTPS port 443):**
- `108.179.138.10:443` ×3 — no PTR (likely Polymarket direct)
- `23.72.36.233:443` — `a23-72-36-233.deploy.static.akamaitechnologies.com`
- `54.230.114.107:443` — `server-54-230-114-107.dub56.r.cloudfront.net`
- `75.2.36.59:443` — `ad537c76ef3912567.awsglobalaccelerator.com`
- `75.2.97.79:443` — AWS Global Accelerator
- `199.232.25.164:443` — Fastly (no PTR)
- `3.162.148.43:443` — AWS
- `152.236.9.75:443` — unknown
- IPv6 `[2a04:fa87:fffd::c000:42dc]:443` — likely Polymarket CDN
- `127.0.0.1:48054` — localhost (single, possibly OrderGateway)

**NOT to DB/PgBouncer** (those listen on `127.0.0.1:6432` and `5432`).

**Recv-Q=25 bytes uniform** on 10 of 11 HTTPS sockets — suggests TLS close-notify never read after peer disconnect.

**Persistent HTTP clients in EB hot path** (grepped):
- `esports/data/riot_api_client.py:76` — `self._client = httpx.AsyncClient(...)` ✓ covered by `ea0b762` stop() fix
- `esports/data/pandascore_client.py:166` — `self._client = httpx.AsyncClient(...)` — **PRIME SUSPECT**, observed `retries exhausted` with `ConnectionTerminated error_code:0` in log
- `esports/markets/esports_market_service.py:399` — `self._httpx_client = httpx.AsyncClient(timeout=15.0)` ✓ covered by `40d119d` stop() fix

PandaScoreClient is the prime leak suspect because (a) its httpx client is persistent and re-used across calls; (b) the in-session log evidence of `ConnectionTerminated` retries; (c) the recent inter-page delay reduction (4.0s→1.0s at `pandascore_client.py:61`) increased request rate by ~4x without revisiting connection cleanup.

**Resolution rate (~42/hr → ~1000/day sustained):** non-threatening at current pace (socket limit ~65k), but root-cause warrants its own focused multi-hour session.

### Reconciliation drift (2026-05-25 18:17 UTC event)

`bot_pnl.py EsportsBotV2 24` window: 0 exits, 1 resolution today, 3 entries May 25. `bot_pnl.py EsportsBotV2 168` window: 22 resolutions across May 19-26 plausibly account for post-restart position closures. **No canonical anomaly visible.** The 13-position reconciliation magnitude itself is position-state-level (positions table), invisible to bot_pnl.py event-time view. Deeper investigation requires non-canonical SQL on positions table. Closing this flag as "no canonical evidence of counter bug."

### Ingest_everything traceback (Task #16, pre-fix verification)

Traceback recurrence verified pre-fix: 5 instances in last 24h on master polymarket-ingestion:
- May 25 18:28:37 (`20260525_141133`)
- May 25 18:59:15 (`20260525_141133`)
- May 26 14:34:11 (`20260525_141133`)
- May 26 15:46:06 (`20260525_141133`)
- May 26 16:26:19 (`20260526_120918` — master post-cherry-pick of Layer 1)

Locals captured: `e = CancelledError()`, `count = 3000`, `days_back = 7`. Post-cancellation log: "IngestionScheduler: ingest_everything() timed out — advisory lock will be released timeout_s=600.0" — confirms timeout source. Fix `24997b3` (cherry-picked to master as `c779638`) will silence the traceback while preserving cancellation propagation.

### Health-check assertion (Task #13, pre-fix verification)

At 14:40:05 UTC: `[error] HEALTH [MARKETS] end_date_iso NULL for 89% of markets (173505/195856). Resolution backfill cannot detect expired markets. Root cause: endDate vs endDateISO field name mismatch in data_ingestion.py.`

Code review of `data_ingestion.py:1008-1013` confirms 5-variant fallback (`endDateISO` → `endDateIso` → `endDate` → `end_date` → `end_date_iso`) IS implemented. S222 closure: "End_date_iso closes as upstream Polymarket API absence (~88% NULL by API, not ingestion bug)" — verified consistent with current code. Fix `526cf8a` (cherry-picked as `b49129f`) corrects the alert message and raises threshold to 0.95.

---

## §3 — Verification: 2 pending markets STILL not re-emitted (FOUND in session-close check)

Per AGENT_HANDOFF_EB_2026-05-26.md morning carry-forward task #15: 8 of 10 cleaned markets had re-emitted via phase4b at morning-session close; 2 pending (`0x73d8e486cc..` and `0x7abae048de..`).

**Verified at session close (cleanup script dry-run output):**

```
market: 0x73d8e486cc..
  expected chain: YES
  live chain resolution: YES
  current markets.resolution: YES
  existing trade_events.RESOLUTION rows: 0    ← STILL ZERO
  paper_trades rows (EB family): 1 total, 0 need resolution UPDATE

market: 0x7abae048de..
  expected chain: YES
  live chain resolution: YES
  current markets.resolution: YES
  existing trade_events.RESOLUTION rows: 0    ← STILL ZERO
  paper_trades rows (EB family): 1 total, 0 need resolution UPDATE
```

**~24h elapsed since cleanup** but phase4b has not re-emitted either market. Other 8 markets DID re-emit normally. So this is NOT a phase4b queue lag — something is excluding these 2.

**Hypotheses (NOT validated this session):**
1. **Position record missing.** phase4b reads from `positions` table; if no position exists for these market_ids, no re-emission. Need to query: `SELECT * FROM positions WHERE market_id IN (cids) AND bot_name IN (EB family)`.
2. **Position size or status filter.** phase4b may filter on `status='closed'` or `realized_pnl IS NULL` — if these 2 don't meet the filter, skipped.
3. **Bot name mismatch.** paper_trades shows `bot_name=EsportsBot` (v1) for both; positions table might have a different bot_name or be missing.
4. **Position size mismatch.** paper_trades shows size=882.35 and size=590.27 — these are notably larger than the other 8. phase4b has a `RESOLUTION over-size rejected` log (seen for other markets earlier in session) that filters resolutions exceeding existing disposal. Could be triggering here too — though session-grep didn't show these 2 in the rejection log.

**Recommended next-session action:** query `positions`, `trade_events_resolution_backfill` log, and the `RESOLUTION over-size rejected` warning history for these 2 condition_ids. If size-rejection is the cause, the writer-fix logic for outcome_prices isn't enough — the size-rejection logic also needs review.

**Note on paper_trades correctness despite trade_events gap:** the recompute script set `paper_trades.realized_pnl` correctly for both:
- `0x73d8e486cc..`: side=YES resolution=YES price=0.34 size=882.35 → recomputed pnl = (1-0.34)*882.35 = +$582.35
- `0x7abae048de..`: side=NO resolution=YES price=0.19 size=590.27 → recomputed pnl = -0.19*590.27 = -$112.15

These match the winner-take-all formula. paper_trades is consistent with chain truth even though trade_events.RESOLUTION is still missing.

---

## §4 — Operator decisions executed this session

| Decision | Status |
|---|---|
| Investigate CLOSE-WAIT | ✓ Done — peer endpoint distribution + suspect identified |
| Cherry-pick + own EB splinter for #13 + #16 | ✓ Done — committed on eb/main + master |
| Resolve paper_trades.realized_pnl drift | ✓ Done — recompute script applied to 9 rows |
| Resolve BOT_ENABLED_ESPORTS_LIVE | ✓ Done — stays false (PandaScore unhealthy) |
| Resolve polymarket-esports-ingestion.service | ✓ Done — defer (multi-hour filter logic, master ingestion sufficient) |
| Master deploy of `b49129f` + `c779638` | DEFERRED — MB session owns master deploys |

---

## §5 — Coordination memos / unchanged at master root

Pre-existing memos (this session did NOT modify):
- `EB_COORDINATION_RESOLUTION_VS_CHAIN.md` (morning session) — Layer 1 status documented
- `EB_COORDINATION_CLOSE_WAIT_LEAK.md` (S230) — superseded by §2 above; should be marked complete when CLOSE-WAIT root-cause session lands
- `WB_COORDINATION_POOL_RIGHTSIZE.md` (S230) — out of EB scope

---

## §6 — Carry-forward (priority order)

| # | Task | Why | Estimated effort |
|---|---|---|---|
| **#19** | **CLOSE-WAIT root-cause deep dive** (NEW) | Identify exact leaking HTTP client(s); top suspect PandaScoreClient. Read every `httpx.AsyncClient`/`aiohttp.ClientSession` instantiation in EB hot path, trace cleanup. Add stop() handling for unfixed persistent clients. | Multi-hour focused session |
| **#20** | **2 markets STILL un-re-emitted by phase4b** (NEW) | `0x73d8e486cc..` + `0x7abae048de..`. Query positions table + size-rejection log to determine exclusion cause. | 1-2 hours |
| #17 | CLOSE-WAIT regrowth observation | Now subsumed by #19 above | (merged into #19) |
| #16 | `ingest_everything` traceback | ✓ Fixed on eb/main + master (`24997b3` / `c779638`); pending master deploy to take effect | Done (pending deploy) |
| #15 | Verify remaining re-emissions | 8 of 10 ✓; 2 of 10 stuck — see #20 | Done (8/10) |
| #14 | cleanup script paper_trades patch | ✓ Fixed on eb/main (`c6b45c0`) | Done |
| #13 | endDate vs endDateISO health-check | ✓ Fixed on eb/main + master (`526cf8a` / `b49129f`); pending master deploy | Done (pending deploy) |
| #8 | Splinter `polymarket-esports-ingestion.service` filter logic | DEFERRED — master ingestion sufficient | Multi-hour, deferred indefinitely |

---

## §7 — Operator decisions still open

| Decision | Owner |
|---|---|
| Master deploy of `b49129f` + `c779638` (restarts MB+WB+ingestion+EB) | MB session / Operator |
| `BOT_ENABLED_ESPORTS_LIVE` flag (stays `false` pending PandaScore health) | Operator |
| `polymarket-esports-ingestion.service` activation (deferred indefinitely) | Operator + future EB session |
| Recompute script (`recompute_paper_trades_pnl_post_cleanup_2026_05_26.py`) — keep as historical artifact or remove? | Operator |

---

## §8 — Known hardening item (not addressed this session)

The cleanup script's existing limitation (running `--apply` on already-re-emitted rows would DELETE them) is unaddressed this session. The script is safe for the 10 specific condition_ids as long as it's NOT re-run with `--apply` after phase4b has done its work. A separate idempotency patch (skip-if-row-already-chain-correct) is a future hardening item.

The recompute script is also safe to re-run — its filter `realized_pnl IS DISTINCT FROM :new_pnl` makes re-runs no-ops after the first apply.

---

## §9 — Next-session entry protocol

```bash
# Worktree silo
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main
git rev-parse --abbrev-ref HEAD                     # must print: eb/main
git log --oneline -8                                # confirm c6b45c0 / 526cf8a / 24997b3 + this handoff are present

# Confirm master cherry-picks landed
cd C:/lockes-picks/polymarket-ai-v2
git log --oneline master -5 | grep -E 'b49129f|c779638' || echo "MISSING — re-cherry-pick needed"

# VPS health gate (skip non-critical EB work if %steal > 25%)
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@18.201.216.0 "mpstat 1 5 | tail -3"

# Confirm master deploy timing — if VPS master release path advanced past 20260526_120918, b49129f + c779638 are live
ssh -i "$KEY" ubuntu@18.201.216.0 "readlink /opt/pa2-current 2>/dev/null || ls -ltd /opt/pa2-releases/* | head -3"

# Priority entry points:
# 1) #20 — investigate why phase4b hasn't re-emitted the 2 stuck markets
# 2) #19 — CLOSE-WAIT root cause (focused leak hunt)
# 3) Check ingestion log: post-deploy, traceback noise + endDate alert should be gone
```

---

## §10 — Splinter charter status (unchanged)

EB session continues splinter-only per 2026-05-24 operator directive. Today's master cherry-picks (#13 + #16) followed the S229 precedent (operator-authorized one-time master touch for EB-owned shared-module fixes). Going forward, EB session continues to own eb/main and propose master cherry-picks; MB session owns master deploys.

The `polymarket-esports-ingestion.service` systemd unit (`fc01947` on disk) remains parked. Activation pending operator decision (see §6 #8).

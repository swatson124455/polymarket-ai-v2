# EB → MB Cherry-Pick Proposal — Remove the `scan_and_trade()` `wait_for` (`base_bot.py:811/852`)

**From:** EB session (eb/main)
**To:** MB session / operator (shared-module owner)
**Filed:** 2026-05-31
**Status:** PROPOSAL — needs MB action. EB authored the read-only audit; the fix is a shared module (`bots/base_bot.py`, all 14 bots) and lands via MB per RULE ONE / RULE ONE-A.
**Companion:** `EB_COORDINATION_SCAN_STALL_DBLOAD.md` → "CRITICAL ADDITION" (the investigation request this answers).

> **Do NOT hot-patch from an EB/WB session.** This is the C2 FIX in shared code; removing it touches every bot. The point of this memo is to hand MB a verified coverage map + a safe sequence so the removal does not re-introduce the event-loop-hang the C2 FIX was added to prevent.

---

## §1 — TL;DR

`base_bot.py:811` (and its twin `:852`) wrap `await asyncio.wait_for(self.scan_and_trade(), timeout=300s)`. When it fires, `CancelledError` lands in a mid-flight asyncpg op → protocol-state corruption (`cannot switch to state N; another operation in progress`) → poisoned pooled connection → fleet-wide `set_statement_timeout_failed` cascade (S162 / RULE ZERO rule 6).

The audit verdict: **the EsportsBotV2 paper path of `scan_and_trade()` is already fully covered by the 30s server-side `statement_timeout`** — so the outer `wait_for` is removable *in principle*. **But it is NOT yet safe to remove**, because removing it in isolation leaves an equivalent corruption source live on the entry path:

- **BLOCKER — `order_gateway.py:882`:** `await asyncio.wait_for(reserve_position(...), timeout=5s)` (15s for SELL, `:870`). Reached on **every BUY entry**. Its 5s client cancel undercuts the 30s `statement_timeout`, so on a slow `positions` write it fires first and corrupts the connection — the exact failure we'd be removing from `base_bot`. **Fix this first.**

So: **fix `order_gateway.py:882` → classify two more sites → then remove `base_bot.py:811`+`:852`.** Details below.

---

## §2 — Corrections to the prior framing (verified this session)

The "CRITICAL ADDITION" memo and the S235 handoff had a few values slightly off; corrected here so the fix is scoped right:

| Claim in prior docs | Verified value | Source |
|---|---|---|
| statement_timeout = 15s | **30s** for bots (15s is a different guard — the semaphore-acquire `wait_for`) | `DB_STATEMENT_TIMEOUT_MS=30000` — `config/settings.py:57`; `SET statement_timeout` — `base_engine/data/database.py:203` |
| `BOT_SCAN_TIMEOUT_SECONDS` default = 60s | **300s** (the `60` at `base_bot.py:808` is only the inline fallback if the setting were missing) | `config/settings.py:962` |
| Only `base_bot.py:811` | **`:811` (main scan) AND `:852` (burst scan, gated on `USE_SCAN_JITTER`, default false)** — same wrapped target | `bots/base_bot.py:811`, `:852` |
| `get_raw_session()` bypasses the timeout | `get_raw_session()` drops only the **semaphore** — it still runs the same `SET statement_timeout` block, so raw sessions ARE covered | `base_engine/data/database.py:1312` + `:203` |

Net: the 30s `statement_timeout` fires well before either the 300s outer `wait_for` or a single hung query — so at the *per-query* level the C2 event-loop-hang concern is already addressed, which is what makes removal viable once the blockers below are cleared.

---

## §3 — Coverage map (EsportsBotV2 `scan_and_trade()`, paper path)

Every reachable asyncpg call routes through `_SemaphoreSession` (via `get_session()` or `get_raw_session()`), which sets `statement_timeout=30s` on session creation (`database.py:203`). Verdict: **fully covered.** Representative sites:

| Call site | Reaches DB via | Covered |
|---|---|---|
| `esports_bot_v2.py:632/725` → `shadow_db.prediction_exists` / `insert_match` / `insert_prediction` | `db.get_session()` | YES |
| `esports_bot_v2.py:683` → matcher → `market_service.get_tradeable_esports_markets` (`esports_market_service.py:169`) | `get_session()` (S235 removed its inner `wait_for`) | YES |
| matcher → `load_esports_team_aliases` / `log_unmatched_prediction` (`database.py:3447/3484`) | `get_session()` | YES |
| `place_order` → `kill_switch.check_kill_status` (`kill_switch.py:44`) | `get_raw_session()` (semaphore-free; stmt_timeout still set) | YES |
| risk → `PipelineGate.check_before_risk` freshness reads ×5 (`pipeline_gate.py`) | `get_session()` | YES |
| `order_gateway` → `trade_coordinator.reserve/confirm/release_position` | `get_session()` | YES (but reserve is wrapped in a client `wait_for` — see §4) |
| paper_trading → `insert_paper_trade` / `insert_trade_event` / `insert_shadow_fill` (`database.py:3967/5505/5800`) | `get_session()` | YES |

Non-DB (not stmt_timeout concerns, verified): PandaScore + Polymarket + CLOB are HTTP; matcher result cache is an in-memory dict; drawdown/adverse/position-tracking are in-memory. The **live-CLOB execution sub-tree** (`order_gateway.py:1294`, `SIMULATION_MODE=false`) is **UNKNOWN/inactive** — EB runs paper; trace it before relying on stmt_timeout there if EB ever flips live.

Full per-call map: ask EB session (audit output, 2026-05-31).

---

## §4 — Blockers (fix BEFORE removing `:811`/`:852`) — Protocol 16 enumeration

These are the same `wait_for`-on-a-covered-but-slow-asyncpg-coroutine anti-pattern in shared code. Removing `base_bot:811`/`:852` without clearing these just relocates the corruption source.

| # | Site | Timeout | Reachability | DB coverage of inner queries | Action |
|---|---|---|---|---|---|
| B1 | `order_gateway.py:882` — `wait_for(reserve_position(...), _coord_timeout)` (`:870` = 5s BUY / 15s SELL) | 5s/15s < 30s | **Every BUY/SELL entry** (all bots that reserve) | `reserve_position` inner SELECT/INSERT on `positions` use `get_session()` → 30s covered | **MUST FIX.** Remove the client `wait_for` (rely on the 30s stmt_timeout, same shape as the S235 scanner fix) OR raise `_coord_timeout` well above 30s. MB to confirm reserve/confirm/release coverage end-to-end. |
| B2 | `base_bot.py:370` — `wait_for(store_pending_trade_signals(...), 2.0)` | 2s < 30s | Gated on `self._pending_signal_meta[market_id]` non-empty. **EsportsBotV2 never triggers it** (returns at `:196-198` before any DB call). **Live for MB / EnsembleBot** (they populate signal metadata). | body uses `get_session()` → covered | MB DECISION (it's MB/Ensemble-reachable, not EB). Same treatment as B1. |
| B3 | `base_bot.py:539-542` — `gather(wait_for(_signals_mult_tracked, 5s), wait_for(_flow_mult_tracked, 5s), wait_for(_trends_mult_tracked, 5s), return_exceptions=True)` (`_svc_timeout` = `SIGNAL_SERVICE_TIMEOUT_SECONDS`, default 5, `:508`) | 5s each | Sizing-multiplier path (which bots reach it depends on signal wiring) | **UNKNOWN** — these multiplier services were not traced; need classification (do any touch asyncpg, or are they HTTP/compute?) | CLASSIFY in MB's full base_bot sweep. `return_exceptions=True` swallows the TimeoutError but the `CancelledError` still hits whatever is mid-flight — so if any wraps a DB coroutine it is a live corruption source. |

---

## §5 — The removal (the actual ask)

Once B1 is fixed (and B2/B3 decided), remove the outer `wait_for` at **both** `base_bot.py:811` and `:852`, e.g. replace:

```python
await asyncio.wait_for(self.scan_and_trade(), timeout=_scan_timeout)
```
with a direct `await self.scan_and_trade()` — keeping the surrounding `_idle_event.clear()/.set()` and the `except asyncio.TimeoutError` branch removed/adjusted. The per-query 30s `statement_timeout` becomes the sole (and safe) bound; no client-side cancellation reaches asyncpg.

All-14-bots blast radius — this is why it's MB's to land. Each bot's `scan_and_trade()` should ideally get the same coverage confirmation EsportsBotV2 got (§3), but EB's is the most DB-intensive, so it's the worst case; the others are likely a subset.

---

## §6 — Verification plan (post-fix)

```bash
KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem; H=ubuntu@18.201.216.0
# 1. The corruption signature should stop appearing fleet-wide:
ssh -i $KEY $H 'for s in esports mirror weather ingestion; do echo -n "$s: "; journalctl -u polymarket-$s --since "2 hours ago" | grep -c "cannot switch to state"; done'   # expect 0s
ssh -i $KEY $H 'journalctl -u polymarket-ingestion --since "2 hours ago" | grep -c "Can.t reconnect"'   # expect 0 (was >0 while :811 active)
# 2. No scan-loop hang regression (the C2 concern): scans keep completing
ssh -i $KEY $H 'journalctl -u polymarket-esports --since "30 min ago" | grep -c "Scan cycle done"'   # expect > 0
# 3. EB self-restart rate drops (was ~1/day driven by this mechanism)
ssh -i $KEY $H 'journalctl -u polymarket-esports --since today | grep -c scan_stall_self_restart'
```

A controlled repro harness for the corruption itself is specified in the S235 handoff §4 ("Scanner rule-6 harness") — building it first would let MB confirm the mechanism before/after rather than relying on production log deltas.

---

## §7 — Scope / ownership

- EB authored the **read-only** audit on eb/main; no shared-module edits were made from the EB session.
- B1/B2/B3 + the `:811`/`:852` removal are **shared modules** (`base_engine/execution/order_gateway.py`, `bots/base_bot.py`) → **MB lands them**, EB does not.
- Do NOT bundle this with the EB `matched=0` fix (`eb/main 09ecf91`) — that one is EsportsBotV2-only and already committed on the splinter.
- RULE TWO reminder: none of this touches neg-risk gating; do not add a `neg_risk=True` filter anywhere in these paths.

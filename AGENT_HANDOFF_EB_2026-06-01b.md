# EB Session Handoff — 2026-06-01b (matched=0 root fix shipped)

**Branch:** `eb/main` (HEAD `46e2119`)
**Worktree:** `C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main/`
**EB splinter VPS:** `20260601_193156` → `/opt/pa2-esports-releases/20260601_193156` (DEPLOYED this session)
**Master VPS:** `20260531_203534` (unchanged — not touched)

**One-line status:** Root-caused why EsportsBotV2 makes 0 trades (predicts matches before their Polymarket market exists, caches them, never re-checks) and shipped the fix. base_bot:811 audit complete → cherry-pick proposal filed for MB. Fix's *effect* (trades) is deferred — needs a predict→market-appears cycle.

---

## §1 — What happened

Picked up the prior handoff (`AGENT_HANDOFF_EB_2026-06-01.md`) carry-forward. Two items actioned: the P1 `matched=0` investigation (which became the de-facto P0 — it's what keeps EB at zero trades and is fully EB-owned) and the P0 `base_bot:811` audit (read-only, → MB proposal).

**`matched=0` root cause (proven, not inferred):** EsportsBotV2 predicts matches up to `_UPCOMING_HOURS=48h` ahead, writes a one-shot shadow prediction + caches `match_id`, then skips the match on every later scan (`esports_bot_v2.py:629/633`). Polymarket lists the head-to-head market **hours-to-days later**, so the late-created market is never matched or traded. Verified against the live DB: of H2H markets matching an unmatched team-pair, **15/15 sampled had `markets.created_at` AFTER the prediction's `esports_unmatched_predictions.event_time`**. Ruled out: contention (6 clean scans, matched=0 on all), matcher-logic bug (the scorer is substring-based — `esports_market_scanner.py:157` — and *would* match the present markets), the freshness/abstain gates (one scan reached singletons=1). Market universe at investigation time: 66 tradeable esports markets, ~20 H2H / ~42 season-outright (the bot only bets H2H, correctly ignores outrights).

---

## §2 — What shipped (complete, do not redo)

| Commit | What |
|--------|------|
| `09ecf91` | **matched=0 fix** — `bots/esports_bot_v2.py` + tests. New `_awaiting_market` watch-dict + `_recheck_awaiting_markets()`; queue gains `market_price is not None` guard. 6 new tests in `TestAwaitingMarketRecheck`; 51 esports tests pass. |
| `46e2119` | **base_bot:811 cherry-pick proposal** — `EB_COORDINATION_BASE_BOT_811_CHERRYPICK.md` (docs only, for MB). |

**Deploy (EB splinter `20260601_193156`):** preflight ran full suite (2965 passed, 44 skipped, 6 xfailed); health-check green at 70s. **Isolation verified** — only `polymarket-esports` restarted (PID 482700→483981, watchdog armed); mirror/weather/ingestion PIDs unchanged (421445 / 429380 / 421447); master symlink unchanged (`20260531_203534`).

---

## §3 — The matched=0 fix (how it works + how to verify)

`bots/esports_bot_v2.py`:
- `_awaiting_market` (dict in `__init__`): holds tradeable (singleton) predictions made while no market existed yet — `{match_id: {match, pipeline_result, game, created_at}}`.
- `_recheck_awaiting_markets(now)` (called at the top of `_predict_upcoming_matches`, after the counter reset): re-runs the matcher for each entry; when a market appears, recomputes sizing and queues the trade **once**, then drops the entry; ages entries out past the lookahead window. Per-entry failures are logged + isolated (never abort the scan).
- Queue condition (`:757`) gained `market_price is not None` — market-less stub-edge items no longer occupy dead queue slots (they were skipped in `_execute_trades` anyway; outcome-preserving).

**Safety property:** provably no double-trade. Only NEW in-session predictions are ever added to `_awaiting_market`; on restart it's empty, so a forced restart (watchdog) can never re-queue/re-trade.

**Verify (the fix's effect is deferred — needs a predict→market-appears cycle, hours):**
```bash
KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem; H=ubuntu@18.201.216.0
ssh -i $KEY $H 'journalctl -u polymarket-esports --since "6 hours ago" | grep -E "esports_v2_awaiting_market_queued|esports_v2_trade_attempt"'
ssh -i $KEY $H 'journalctl -u polymarket-esports --since today | grep esports_v2_scan_funnel | tail -5'   # watch matched/queued go > 0
```

---

## §4 — base_bot:811 audit → MB proposal (do NOT hot-patch from EB)

Full memo: `EB_COORDINATION_BASE_BOT_811_CHERRYPICK.md`. Verdict: EsportsBotV2 `scan_and_trade()` paper path is **fully covered by the 30s statement_timeout**, so removing the `wait_for` is viable — **but not in isolation**:
- **BLOCKER `order_gateway.py:882`** — `wait_for(reserve_position, 5s/15s)` undercuts the 30s timeout on every entry → same corruption. Fix first.
- `base_bot.py:370` — same pattern, dormant for EB (early-returns), live for MB/Ensemble.
- `base_bot.py:539-542` — 3 sizing-mult `wait_for`s, coverage UNKNOWN, classify.
- Sequence: fix B1 → decide B2/B3 → remove `base_bot.py:811` **and `:852`** (twin, burst path — prior docs missed it).
- Corrections to prior framing: statement_timeout **30s** (not 15s), `BOT_SCAN_TIMEOUT_SECONDS` **300s** (not 60s), `get_raw_session()` IS covered.

Shared module (all 14 bots) → **MB lands it, EB does not.**

---

## §5 — Open carry-forward (priority order)

| # | Item | Notes |
|---|------|-------|
| **P1** | matched=0 fix — confirm trades materialize | Watch the §3 commands over ~a day. If still empty after markets appear → the cross-restart gap (below) is dominant. |
| **P1** | matched=0 **cross-restart recovery** (follow-up to `09ecf91`) | `_awaiting_market` is in-memory → a match predicted-then-unmatched before a restart is skipped at `:633` forever. Long-lead cases (market appears >1 restart later, e.g. the 2-day Team Liquid case) are NOT caught. Fix: re-seed `_awaiting_market` on startup from `esports_predictions` (predicted, upcoming, untraded). More invasive — needs a DB-backed "already traded?" dedup to stay double-trade-safe. |
| **P1** | base_bot:811 → **MB to action** the proposal | `EB_COORDINATION_BASE_BOT_811_CHERRYPICK.md`. EB's part (audit) is done. |
| P1 | Scan-progress watchdog | Fires on scan-START age; under sustained contention scans restart-but-never-complete → never fires. Add a "completion" signal (zero `Scan cycle done` in 30 min → exit). Defer until base_bot:811 lands. |
| P1 | Scanner rule-6 harness | Spec in prior handoff §4. Build before any base_bot:811 removal. |
| P2 | Fix B completeness | `conn.invalidate()` after broken-session close. Low urgency. |
| P3 | CLOSE-WAIT leak | `EB_COORDINATION_CLOSE_WAIT_LEAK.md`. Re-measure regrowth first. |
| P3 | Calibrator anomalies | valorant/dota2 silent; LoL n=0. Untouched. (Note: live games are `cs2,lol` per `.env.esports`.) |

---

## §6 — Can't-fully-verify

**Verified:** root cause (live DB created_at vs event_time, 15/15); fix logic (6 unit tests + full suite preflight 2965 passed); deploy isolation (sister PIDs unchanged, master untouched, esports on new release + scanning per health check).

**NOT yet verified (deferred, not failures):**
- That the fix produces an actual trade — needs a predict→market-appears cycle (hours). Watch §3.
- Whether the within-session fix is sufficient or the cross-restart gap dominates — answered by §3 over ~a day.
- base_bot:811 removal end-to-end — that's MB's to verify after fixing B1.

---

## §7 — Entry commands for next session

```bash
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main
git rev-parse --abbrev-ref HEAD        # eb/main
git log --oneline -4                   # expect 46e2119 on top

KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem; H=ubuntu@18.201.216.0
ssh -i $KEY $H 'echo "esports: $(readlink /opt/polymarket-ai-v2-esports)"'   # expect 20260601_193156
# Did the matched=0 fix produce trades yet?
ssh -i $KEY $H 'journalctl -u polymarket-esports --since "12 hours ago" | grep -cE "esports_v2_awaiting_market_queued|esports_v2_trade_attempt"'
```

---

## §8 — Scope / isolation note

All code this session: `bots/esports_bot_v2.py` + `tests/unit/test_esports_bot_v2.py` (EB-owned). The `base_bot:811` work was **read-only audit** → a docs proposal for MB; no shared-module edits from the EB session. Deploy was EB-splinter-only (`/opt/pa2-esports-releases/`, `polymarket-esports` restart only); MB/WB/ingestion PIDs verified unchanged. No `bots/mirror_bot.py`, no neg-risk filter (RULE TWO), no master touch.

**Pending (not done this session):** `MEMORY.md` is over its load limit — a pointer to this handoff should replace a superseded entry (S209–S216 candidates per prior handoff §5), not just append.

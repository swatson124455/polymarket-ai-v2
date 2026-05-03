# S172 CONSOLIDATED PLAN v7.0 — INTEGRATED (Phase RC + Phase 5v2 + ongoing session corrections)

**Session:** 172 (original) + 173 (RC diagnostics + 5v2 amendment) + S180–S186 (session corrections log, Protocols 1–6 + remaining candidates, Hygiene Backlogs)
**Date:** 2026-04-12 (v6.0) → 2026-04-13 (v7.0) → continuously updated (latest: 2026-04-22, S190 — Protocols 5b/5c/5d + 6a codified)
**Status:** APPROVED — integrates v6.0 + Phase 5v2 Amendment + Phase RC findings + S180–S186 Corrections Log
**Scope:** All 3 bots (WeatherBot, MirrorBot, EsportsBot) — audit remediation + long-term elevation
**Timeline:** 8 months
**Previous:** S171 (`AGENT_HANDOFF_S171_SHARED_MASTER.md`), prior v6.0 and Phase 5v2 amendment folded in
**Latest session closes:** S184 (`AGENT_HANDOFF_S184_CLOSE.md`) — deploys; S185 (`AGENT_HANDOFF_S185_CLOSE.md`) — documentation-only, no commits, no deploys

---

## Changes v6.0 → v7.0

1. **Phase RC inserted** between Phase 1 and Phase 2. Complete. Findings integrated.
2. **Phase 5 replaced** by Phase 5v2 (EB rebuild). EB v1 killed — 4/4 kill criteria met.
3. **Phase 6 gate updated** — gated on RC findings + post-fix WB data, not just 1B calibration.
4. **Phase 7 gate updated** — gated on RC findings + post-fix MB data, not just prediction count.
5. **Success criteria amended** — EB v2 evaluated separately. System may operate with 2 bots.
6. **Phase 8B** evaluates EB v2 data (model_version='v2-trinity'), not v1.
7. **Immediate WB/MB fixes** added as "Day 2" actions derived from RC findings.
8. **Sessions S180–S186** Corrections Log, Protocols 1–6 (Protocol 6: canonical-source discipline for P&L/WR/trade-count claims, promoted from Rule Zero at fourth-instance trigger — S149/S150/S185/S186) + Protocol 5a (canonical-document identity), remaining Protocol candidates (SQL-contract, aggregate-statistics bucket-concentration check, and — post-S186 — a Protocol 6 carveout decision for check-effectiveness measurements), Hygiene Backlogs (S180, S183, S185), Silent-failure class diagnostic heuristic appended (preserved from v6.0's ongoing edits — the consolidated plan is a living document). Latest entries: S184 gitignore-drift correction, S185 P0 recon_type reclassification (FIX_ROOT → FIX_AUDIT_CHECK), S185 Hygiene Backlog item for `result_store.py` chronic-OPEN gap, S186 Protocol 6 promotion + 6O deferral + PSM port shape correction (commit `e19815e` — S164 pattern inheritance without structural-isomorphism check) + new P0 follow-up DUAL_SIDE_CONCURRENT diagnostic filed.

---

## Context

Six independent audits + gap analysis + handoff cross-reference + elevation roadmap identified 80+ issues and strategic improvements. All verified against live VPS (2026-04-12). Phase 1 complete (12/12 items deployed). Phase RC diagnostics (35 queries, invariant-checked, cross-validated against bot_pnl.py) revealed:

- **WB:** SIZING + SUBSET. Wins half the size of losses. YES side overwhelmingly negative. Confidence anti-calibrated. See `scripts/rc_diagnostic.py` output for numbers.
- **MB:** SIGNAL + SUBSET. Sub-40% WR. Crypto category and 5 specific wallets dominate losses. See `scripts/rc_diagnostic.py` and `scripts/rc_verify.py` output for numbers.
- **EB:** SIGNAL. Model uninformative — flat WR across all calibration buckets. Never profitable any week. 4/4 kill criteria met. Killed. See `scripts/rc_temporal.py` Q4 output.

---

## Operational Procedures (apply to ALL phases)

- **Rollback:** Every code change: `git revert <sha>` + `sudo systemctl restart <service>`. Every schema change: DROP INDEX + re-enable trigger or reverse migration. Risk/exit code changes (D7, stop-loss modifications): After reverting, immediately audit all positions exited in the last N minutes for false triggers. Re-enter if exit was erroneous and market conditions still favorable. This applies to any risk/exit code change.
- **Maintenance windows:** Schema changes (1C, 1E, migrations) deploy during 02:00-04:00 UTC low-activity window.
- **Testing:** Each new subsystem (5H Glicko-2, 6J city rotation, 7H LLM signal) requires integration tests before merge. "Existing tests pass" is necessary but not sufficient.
- **Shadow mode:** Define shadow mode protocol as process document in Phase 1 (was 8O — promoted). All model changes in Phases 5-7 must use shadow mode.
- **Capital during elevation:** During Phases 5v2/6/7 model changes, bots continue at current paper-trade sizes. Shadow mode governs: candidate model predicts but doesn't trade until promoted.
- **Session-local investigation scripts (codified S205 post-close, 2026-04-30).** One-shot diagnostic scripts named `scripts/s{NNN}_invest_*.py` (e.g., `s204_invest_cluster_window.py`, `s205_invest_boundary_risk.py`) stay UNTRACKED in git by convention. They are session-local artifacts whose findings get codified in handoff docs and the §Corrections Log; the script itself is disposable. Promote to a tracked script (e.g., `scripts/<bot>_<feature>.py` with tests) ONLY when (a) the question becomes recurring across sessions, OR (b) the script's findings are worth re-running on future cohorts. Pattern precedent for promotion: `scripts/wb_bucket_concentration.py` was promoted from S203 untracked one-shot in S204 commit `cf3059b`. Operator must NOT `git clean -fd` the working tree without auditing the untracked one-shot list — see §S180 Hygiene Backlog "survive by careful hands" pattern.
- **Operator-decision-queue batching (codified S210, 2026-05-03).** When a session-close handoff would surface ≥4 operator-blocking decisions in §5 (Operator actions required), promote them to a top-of-handoff "§0 Operator Decisions Pending" block — one short paragraph of context per item plus a single batched-decision invitation, alongside (not replacing) the existing §5 enumeration. Per-item presentation at large queue sizes invites incremental scope creep ("answer item 1, then item 2 brings new context that changes item 1's framing"); batched presentation invites one-shot resolution and surfaces cross-item dependencies. Out of scope: mid-session in-line operator-pending items (those flow in conversation); session-close queues with ≤3 items (per-§5-item presentation is fine). Threshold note: the S209 candidate filing originally stated "≥5 items" based on S208's 6-item peak; S210 working example refined to "≥4" — S209 close handoff §0 batched 4 items (Lead 4 EB v2 verdict, bucket-concentration promotion, pre-send P11 self-check, audit-corrections-bundling), operator processed all 4 in one batched response, and S210 shipped 3 codifications + 1 trade-flip in the same session. Pattern works at 4. **Upper-bound observation note (S210 review-derived refinement):** if a single batch grows beyond ~10 items, fatigue scales independently from the per-item batching benefit — group into related-decision clusters (e.g., "EB v2 Lead 4 sub-options" + "meta-hygiene codifications" + "infrastructure investigations") rather than a flat 10-item list. Sub-mandate is observation-driven, not yet evidence-bound; refine when a future session actually hits N≥10 and the operator's processing shape can be observed. Pattern precedents (1 working example): S209 close handoff §0 (`AGENT_HANDOFF_S209_CLOSE.md`). Promoted from §Protocol candidates per the candidate's "codify after one session of observed-effective batched-decision-pass" trigger; S210 was that session.
- **Audit-corrections-bundling pattern (codified S210, 2026-05-02).** When in-session work surfaces multiple corrections of one logical type — typically: review-incorporation findings (close-review observations folded into the plan), audit-derived count corrections (e.g., over-counting fixes), or systematic-error retroactive fixes that share a root cause — bundle them into ONE docs-only commit with a single §Corrections Log entry covering the cluster, rather than shipping each correction as a separate commit. The bundling preserves "one fix per commit" because the corrections are all of one logical type sharing a common §Corrections Log narrative; cleaner audit trail than N separate commits each with a one-line message. Out-of-scope: bug fixes (each gets its own commit per the prime directive); behavior-changing config tunings (each gets its own commit and rollback path); cross-cutting refactors (forbidden during bug fixes per CLAUDE.md Rule 7). The pattern applies ONLY to documentation/audit corrections of one logical type. Pattern precedents (2 instances): S208 commit `177ac75` (4 plan corrections + 2 hygiene additions, all close-review-incorporation, one §Corrections Log entry) and S209 commit `5ecda0f` (eval script Brier formula fix + BSS denominator fix + singleton-only verdict + §S209 Corrections Log entry with §S208 PARK supersession framing — all one logical cluster on the same script bug). Promotion threshold met: 2-session precedent operator-approved S210 (this codification). Future audit/review work matching the pattern shape SHOULD bundle by default; per-correction commits are fine but the bundling is now the documented convention.

---

## Session Template

Every session on this project follows one of two entry-point templates, then a shared execution pattern. The templates exist to prevent the class of failure where a session builds on stale testimony — see Protocols 4b, 5, 5a for the concrete failure modes.

### Entry Point A — Handoff-inherit (predecessor session exists)

A prior session left a handoff doc (`AGENT_HANDOFF_*.md`, gitignored) and updated memory. Default assumption: **the handoff is testimony, not fact.** The interval between handoff-write-time and session-entry-time is a drift window.

**Phase 0 — Verify testimony (30-minute budget):**
1. Read the predecessor handoff in full.
2. Apply Protocol 5a — verify `S172_CONSOLIDATED_PLAN.md` is still the canonical filename (no sibling `_v8`, `_v7`, etc. with newer-approved content).
3. Identify the single most load-bearing claim in the handoff (what the next session is supposed to build on first). Apply Protocol 4b to it — verify against current shipped code, not against the handoff's summary.
4. Apply Protocol 5 to every phase-level status claim the session will rely on ("X is done," "Y is pending," "gate Z passed"). Verify each against shipped code, not against memory.
5. If any verification fails, STOP. Update memory / current_state to reflect verified state before proceeding with session work. Building on an unverified claim is forbidden.

### Entry Point B — Fresh start (no predecessor handoff)

No handoff inherited. Session is picking up work from the plan directly, or starting a new line of investigation.

**Phase 0 — Ground in canonical state (30-minute budget):**
1. Read `CLAUDE.md` (project directive, always authoritative).
2. Read `S172_CONSOLIDATED_PLAN.md` header + Changes v6.0→v7.0 section. Apply Protocol 5a to confirm no orphan sibling file.
3. Read `memory/project_s172_current_state.md` — this is the live state claim.
4. Verify the top 2-3 load-bearing claims in current_state.md against shipped code using Protocol 5. Do NOT verify every claim (budget-bound); verify the ones the session's intended work depends on.
5. If drift found, update current_state.md first, then proceed.

### Shared execution pattern (both entry points)

After Phase 0 verification:

1. **Walk-backward-hypotheses.** Each time a working hypothesis is invalidated, log the inversion and the diagnostic that inverted it. Three consecutive inversions on the same bug (as in S182 Phase 0.2 → 0.2-b → 1c → 1d) is expected, not exceptional — it's the signal that the original framing was at the wrong layer.
2. **Fix-with-tests-and-gates.** Every shipped commit carries a unit test and a post-deploy gate (T+30min, T+2h, T+4h, T+24h). No exceptions. "Tests pass" is necessary but not sufficient — the gate is the truth.
3. **Cite canonical sources (Protocol 6).** Any P&L, win-rate, or trade-count number in session output must cite `scripts/bot_pnl.py` as source and include the invocation command that produced it. Fresh SQL against `trade_events` presented as canonical is forbidden. If the stop-hook surfaces a violation, follow the recovery procedure in Protocol 6: strip offending numbers, preserve qualitative findings, cite `bot_pnl.py` output directly, add Rule-Zero header to any producing script.
4. **Pre-send Protocol 11 self-check (codified S210, 2026-05-02).** Before sending ANY message that contains numerical content (P&L, win rates, trade counts, sample sizes, percentages, ratios), run a 4-step verification ladder over each numerical mention:
   1. **Is it from `scripts/bot_pnl.py`?** If yes, the inline citation is `bot_pnl.py <BotName> <hours>` (or the canonical alternate invocation). Cite per-mention, not adjacent-paragraph (Protocol 11).
   2. **If not from `bot_pnl.py`, is it from a non-`bot_pnl.py` canonical source?** Acceptable canonical sources: a config file with `file:line` citation, a captured eval-script output file at repo root (e.g., `S209_EB_V2_CORRECTED_VERDICT.txt`), a `prediction_log` query whose SQL is in a tracked script. Cite the file:line or the captured output filename inline at the mention.
   3. **If paraphrased from a prior-session source, has the citation been re-included?** Paraphrasing prior-session figures without re-citing is the Protocol 11 [docstring-paraphrase loophole](C:/Users/samwa/.claude/projects/C--lockes-picks-polymarket-ai-v2/memory/feedback_p11_docstring_paraphrase.md). The prior session's citation does NOT propagate through paraphrase — re-cite the original source at the mention, or strip the figure entirely.
   4. **If from operator memory or a verbal claim, has it been verified against current state?** Memory and verbal claims decay; verify the figure is still current via the canonical source. If verification isn't done before the message ships, strip the figure.

   **Pre-send action.** For each numerical mention that fails any of the four steps, STRIP the figure before sending — keep the qualitative finding. Pre-send self-check sits upstream of the catch-and-recover loop (which is the post-send stop-hook). This step is the prevention layer; the stop-hook is the safety net. The catch-latency in the S203/S204/S205/S207/S208/S209 chain has tightened from post-commit retroactive → pre-commit → during-drafting → in-message before send → pre-send self-check. Each tightening removes a class of cognitive-pattern noise from the output channel. Codification S210 follows S208's promotion of this principle to CLAUDE.md Forbidden Pattern #10 — Forbidden Pattern #10 names the rule, this step operationalizes it as a checklist.

5. **Codify-protocols-from-failures.** Concrete failure → named protocol with Mandate / Out-of-scope / Evidence of origin (§Protocols structure). Generic "we should be more careful" observations do NOT qualify — every protocol must cite a specific failure that would have been prevented by the rule.
6. **Update memory + state at session close.** Write or update the handoff for the next session. Update `memory/project_s172_current_state.md` with any verified status changes. Update `memory/MEMORY.md` index if adding new memory files.

### Out-of-scope for this template

- Session-specific execution details (what commit landed, what deploy timestamp fired) belong in the handoff doc and the §Corrections Log, not here. This template is the durable shape every session takes, not a log of any particular session's work.
- Generic process advice ("be careful," "don't break things") that isn't tied to a concrete verifiable step.
- Content overlap with CLAUDE.md — that file is the project directive; this template is the session shape. Don't duplicate.

### Evidence of origin

Template shape observed across S180, S181, S182, S183. Each session ran some variant of "inherit handoff → verify claims → walk backward through hypothesis layers → fix with tests and gates → codify protocols." S182's retrospective review explicitly identified this as a reusable pattern worth codifying. S183's plan-hygiene work landed it as this section. The two-entry-point split (handoff vs. fresh-start) was specifically requested in S183 review to prevent a fresh-start session from looking for a handoff that doesn't exist and getting stuck.

---

## MASTER TIMELINE

```
Week:  1   2   3   4   5   6   7   8   9  10  11  12  ...  M3  M4  M5  M8
       |---|
       Day 1 + Phase 1 (COMPLETE)
       |---|
       Phase RC (COMPLETE — diagnostics run, findings applied)
       |---|
       Day 2 (WB/MB immediate fixes from RC — COMPLETE, deploy 20260414_132211)
           |-------------------|
           Phase 2 (infra, parallel — ~95% done, 2I flip pending)
                   |--------------------------------------|
                   Phase 5v2 (EB rebuild: A→B→C→D — 5v2-A/B COMPLETE, C/D LIVE shadow)
                       |---|
                       Phase 3 (VPS config)
                           |---|
                           Phase 4 (hygiene)
                       |-------------------------|
                       Phase 6 (WB elevation — gated on RC + post-fix data)
                               |-----------------------------|
                               Phase 7 (MB — gated on RC + prediction count; 4/11 items shipped)
                                               |--------------------------|
                                               Phase 8 (cross-bot)
                                                       |----------|
                                                       Phase 10 (strategic)
                                                               |-----------------|
                                                               Phase 12 (WB EMOS)
       |-----------------------------------------------------------  Phase 13 (ongoing)
```

---

## DAY 1 — Immediate (COMPLETE — deploy 20260413_172523)

⚠️ **FIRST ACTION: D5 (backup). Before any config changes. If PG dies during D1-D4, you lose everything.**

### Pre-Work Diagnostics (SSH, 30 min):

- **D0-a:** Fix logrotate failure
- **D0-b:** Verify ingestion NRestarts
- **D0-c:** Enable Redis AOF persistence

### Infrastructure (SSH):

- **D5:** FIRST. Stopgap pg_dump backup + cron (02:00 UTC daily, 7-day retention). Replaced by pgBackRest once Phase 1H stable.
- **D1:** PostgreSQL OOMScoreAdjust=-900 (kill last among userspace, not absolute -1000 which risks OOM thrash)
- **D2:** systemd MemoryMax + OOMScoreAdjust:
  - EB: 2G→2.5G (UP — 2.3GB RSS over current 2G limit. Contingent on 1F tracemalloc. If leak fixed, may tighten. If legitimate working set, bump to 3.0G)
  - MB: 3G→2.5G (DOWN — 1.9GB RSS, 31.5% headroom)
  - WB: 2G→2.0G (unchanged — burst ensemble loads justify headroom)
  - Ingestion: 1G→0.5G (DOWN — 0.3GB RSS)
  - OOMScoreAdjust: PG=-900, Redis=-500, WB=-200, MB=-100, EB=0, Ingestion=+100
- **D3:** RESOLUTION + EXIT dedup partial unique indexes. Dedup key: RESOLUTION unique on (market_id, bot_name) WHERE event_type='RESOLUTION'. EXIT unique on (market_id, bot_name) WHERE event_type='EXIT' (event_type in columns is redundant since WHERE already filters — drop it). Disable trigger → clean dupes (keep earliest per key) → create indexes CONCURRENTLY → re-enable trigger.
- **D4:** Fix fail2ban + sudo ufw limit ssh
- **D6:** Start prune timer
- **D9:** PipelineGate REMOVED. Gate is 2 hours (not 60s as originally claimed). Safety net for API outages, not a bottleneck.
- **D10:** WB reentry_check interim fix. TTL = min(time_to_resolution, 6h) with floor of 1h. Dynamic per-market. Data source: `markets.end_date_iso` (populated from Gamma API endDateISO). Calculation: `(end_date_iso - utcnow()).total_seconds() / 3600` — same pattern as `exit_strategy.py:307-308`.

### Code Commits:

- **D7:** Hard stop-loss in `base_engine/risk/risk_manager.py` — NEW shared check, REPLACES existing EB-specific edge override (esports_bot.py:2264). One code path, not two. Per-bot configurable:
  - WB: min_edge_hold=0.05, hard_stop=-25%
  - MB: min_edge_hold=0.03, hard_stop=-30%
  - EB: min_edge_hold=0.03 (carries over existing ESPORTS_MIN_EDGE_HOLD), hard_stop=-50% floor (volatile esports)
- **D8:** MirrorBot $30 flat sizing + 24h re-entry cooldown + override signal enhancement. $30 accounts for risk budget deductions post-sizing (~0.88x typical → $26.40, clears $25 dust gate). Override `apply_signal_enhancements()` in mirror_bot.py to return confidence unchanged (neutral passthrough). Do NOT modify base_bot.py:441 — other bots keep their 1.2x/0.6x. Design debt: risk budget deductions apply after flat sizing; worst-case (floor 0.15) gets dust-gated, which is acceptable in extreme drawdown.

---

## WEEK 1 — Phase 1: P0 Data Integrity + Edge Verification + Shadow Mode Protocol (COMPLETE — 12/12 items, 1892+ tests pass)

| Commit | Item | File(s) | Notes |
|--------|------|---------|-------|
| 1 | 1A: frozen_price_check timestamp fix | frozen_price_check.py L29,30,39 | All bots |
| 2 | 1B: calibration_check rolling 90-day + CRPS/PIT | calibration_check.py | WB immediate. MB/EB: N/A until Phases 7/5 gates met (0 prediction_log rows, months-long dependency, not just sequencing) |
| 3 | 1C: Autovacuum tuning (positions 5.5% dead + mpl 14% dead + users) | New 067_vacuum_tuning.sql | Deploy during 02:00-04:00 UTC window |
| 4 | 1D: WB post-resolution price override | Resolution backfill path | WB primary |
| 5a | 1E-a: market_aliases migration | New migration (separate from gateway) | Schema only — zero blast radius |
| 5b | 1E-b: order_gateway pre-trade validation | order_gateway.py | All bots — separate commit from migration |
| 6 | 1G: prediction_log write fix | mirror_bot.py + esports_bot.py | MB/EB |
| 7 | 1F: EB tracemalloc SIGUSR1 handler | esports_bot.py | CONDITIONAL: If TabPFN > 1GB → execute removal in same session (makes Phase 5A a no-op, shortens Phase 5 timeline) |

### PROMOTED TO PHASE 1 (commits 8-10, pushing Phase 2 to start at commit 11):

| Commit | Item | Details | Notes |
|--------|------|---------|-------|
| 8 | 1I: Edge verification | Bootstrap P(edge > 0) and Kelly on existing trade_events. ~50 lines numpy. | HARD GATE for Phases 5-7. Graduated response: P(edge>0) ≥ 0.9 → full elevation. 0.7–0.9 → elevation at reduced scope (core items only, skip speculative). < 0.7 → root-cause investigation replaces elevation. |
| 9 | 1J: Orderbook collection | Cron polling best_bid/best_ask every 60s. | Specify rate limits. If 500+ markets exceed Polymarket API throttling, prioritize by volume. |
| — | 1K: Quick verifications (not a commit) | ArbitrageBot auto-start? EsportsLiveBot orphans? Canary stuck? | 5-minute SSH checks, no code change |
| — | 1L: Shadow mode protocol (not a commit) | Concrete document specifying: (a) candidate alongside live, (b) prediction_log with model_version flag, (c) min 50 resolved or 2 weeks, (d) promote if Brier < live by ≥5% + positive ROI, (e) reject if worse by any margin or negative ROI. | Process document, not code. Must exist before Phase 5-7. |
| 10 | 1M: Strategy lifecycle schema (was 10B) | 5 PG tables — schema only, no code dependency. Informs capital allocation from Day 1. | Migration commit. |

### VPS work:

- Orphan reconciliation for WB positions
- 1H: ALTER SYSTEM SET idle_in_transaction_session_timeout = '300000' (5 min server-level backstop). App-level 60s handles bot connections. This 5-min setting catches psql, cron, pg_dump sessions that bypass app-level timeout. NOT an increase to bot timeout — it's a separate safety net for non-app connections. VPS-only, no commit.
- pgBackRest setup (60-90min) — once stable, D5 pg_dump cron is superseded
- Post-1E: verify drawdown controller sees all 3 bots' exposure

**POST-COMMIT-2:** Run calibration_check.py WeatherBot (CRPS + Brier + PIT)

---

## PHASE RC — Root-Cause Investigation (COMPLETE)

**Deliverables:** `scripts/rc_diagnostic.py` (35 diagnostics), `scripts/rc_verify.py`, `scripts/rc_temporal.py`. All invariant checks PASS. All-time P&L cross-validated against `bot_pnl.py`.

**Findings summary:** See Context section above. Full output on VPS.

**Decision framework applied:**

| Bot | Verdict | Root Cause | Action |
|-----|---------|------------|--------|
| WB | FIX | SIZING + SUBSET | Day 2 fixes + Phase 6 elevation |
| MB | FIX | SIGNAL + SUBSET | Day 2 fixes + Phase 7 elevation |
| EB | KILL | SIGNAL (uninformative model) | Phase 5v2 rebuild |

---

## DAY 2 — Immediate Fixes from RC Findings (COMPLETE — deploy 20260414_132211)

**Philosophy:** Fix sizing and block clearly toxic subsets. Do NOT remove entire sides (YES/NO) or restrict lead-time windows — insufficient data to confirm those restrictions improve outcomes long-term. Let the bots accumulate post-fix data with both sides active.

**Deploy order:** D2-1 (shared) → D2-2/D2-3 (WB) → D2-4/D2-5/D2-6 (MB) → D2-7 (EB kill). Deployed during 02:00-04:00 UTC maintenance window. Rollback: revert config/code, `sudo systemctl restart <service>`.

### Cross-Bot (Shared)

- **D2-1:** Cap max position size at $200 across all bots via `BotBankrollManager.max_bet_usd`. The largest position bucket is the dominant loss driver for all 3 bots — see rc_diagnostic.py S5 for per-bot breakdown.

### WeatherBot

- **D2-2:** Flat sizing — decouple from confidence until calibration fixed. Cap at $100 max. The confidence column is anti-calibrated: highest-confidence bucket has the largest losses despite the highest WR, because Kelly sizes up massively and rare losses are catastrophic. See rc_diagnostic.py S12, WB-8, and rc_verify.py Q5 for stake-by-confidence data.
- **D2-3:** Blacklist worst cities: Dallas, NYC, Toronto, Atlanta, London, Seattle. These 6 cities have wl_ratio < 0.15 (losses 7-17x larger than wins) and account for the majority of city-attributable losses. See rc_diagnostic.py WB-1, WB-7.

**NOT doing:** Kill YES side or restrict to 48-120h lead-time. YES side and short lead-times are underperforming, but we don't have enough post-fix data to confirm removing them improves outcomes. Both sides continue trading at reduced sizing ($100 cap). Re-evaluate at Phase 6 gate after 4+ weeks of post-fix data.

### MirrorBot

- **D2-4:** Block crypto category. Crypto loses on both YES and NO sides and accounts for the dominant share of MB losses. See rc_verify.py Q1 for category x side breakdown.
- **D2-5:** Blacklist 5 worst wallets (0x818F, 0xD84c, 0x732F, 0x6ac5, 0x88f4). All 5 are active in last 30 days with 654 combined entries. See rc_verify.py Q2 for recency, rc_diagnostic.py MB-3 for per-wallet P&L.
- **D2-6:** Require `whale_trade_usd >= $100`. Small whale trades ($0-25, 3,394 trades) dominate losses while trades above $100 are profitable across 1,680 trades. See rc_diagnostic.py MB-4 for bucket breakdown.

**NOT doing:** Block sports-NO side. Sports-NO is a secondary loss driver but removing an entire side reduces data collection. Let it run at capped sizing. Re-evaluate at Phase 7 gate.

### EsportsBot

- **D2-7:** Kill EB v1. `BOT_ENABLED=false`, `systemctl disable polymarket-esports`. Code stays in repo. (This is also 5v2-A1.)

**Expected volume impact:** WB drops from ~80 trades/day to ~60-70 (city blacklist reduces ~15%). MB drops from ~150/day to ~80-100 (crypto block + whale filter removes ~40-50%). Both bots retain enough volume for statistically meaningful post-fix evaluation within 4 weeks.

---

## WEEK 1-2 — Phase 2: P1 Operational Resilience + Feast Feature Store

| Commit | Item | File(s) | Notes |
|--------|------|---------|-------|
| 11 | 2A: asyncio.wait_for verification grep | `grep -rn "wait_for.*acquire\|wait_for.*execute\|wait_for.*fetch" --include="*.py"` | ALREADY FIXED S166. Verify no remaining instances. |
| 12 | 2B: Data retention (trades CREATE-AS-SELECT + recon_breaks) | New prune_old_data.py | |
| 13 | 2C: Structlog dedup (30s TTL) | logging_setup.py | |
| 14 | 2D: WatchedFileHandler + logrotate | logging_setup.py + new deploy/logrotate.d/polymarket | |
| 15 | 2E: RTDS seen_set dedup | `bots/elite_watchlist.py:984-988` | ✅ SHIPPED (commit `bf23c25`). File column corrected from `mirror_bot.py` — dedup lives at `EliteWatchlist` ingress via `_seen_tx` OrderedDict, not the MirrorBot strategy layer. Out of 7B Phase A rejection-logging scope per S187 scope decision (transport-layer dedup ≠ strategy rejection). See §S187. |
| 16 | 2F: Health check kill switch wiring | `deploy/dead_man_watchdog.sh` (kill-switch writer — sets `system_config.kill_switch='true'`) + `deploy/healthcheck_probe.sh` (S180 tiered probe, replaced "6-layer script" framing) | Plan's `health_check.sh` reference is stale — no file by that name exists. Kill-switch wiring SHIPPED. See §S186b Corrections Log for reconciliation. |
| 17 | 2G: Pool tightening — INVESTIGATE FIRST | .env files | Start: MB 10→8, EB 14→10. Monitor 48h. Rollback: if >5 events matching `pool_exhaustion\|TimeoutError\|semaphore\|QueuePool limit` in 48h, immediately revert. |
| 18 | 2I: Illiquidity exit validation + enable | Config | Deploy BEFORE 2H — handle exits from illiquid positions before filtering entries by liquidity. |
| 19 | 2H: Entry-time liquidity gate | order_gateway.py | Per-bot depth multiplier: WB 10×, MB 5×, EB 3× |
| 20 | 2H-b: Shared-token mutual exclusion | DB check in order_gateway | Enumerate the 5 tokens. Add discovery mechanism for new shared tokens. |
| 21 | 2J: Slippage monitoring refactor | slippage_check.py | |
| 22 | 2K: Feast feature store (was 8Q) | pip install feast, PG offline + Redis online | Initial setup 1-2 days. Delivers configured stores + one example feature view. Each elevation phase adds feature views incrementally. |

If market_prices Option B approved: Commit 20 (DROP TABLE + exclusion + ingestion disable)

---

## WEEK 2 — Phase 3: VPS Config (SSH only)

**Verified state 2026-04-21 — see §S186b Corrections Log for full audit trail.**

- ~~effective_cache_size=12GB~~ — **SUPERSEDED.** VPS running `effective_cache_size=24GB, shared_buffers=4GB` via `postgresql.auto.conf:4-5` (`ALTER SYSTEM SET` override; `pg_settings.source='configuration file'`). Origin: S152 PG tuning during VPS upgrade (commit `8d7b5e1`, Ubuntu-3 16GB → Ubuntu-32 32GB). Plan's 12GB target was pre-migration; current values correct for 32GB instance. No reapply needed. ✅
- PgBouncer `idle_transaction_timeout` — **NOT APPLIED.** VPS has `server_idle_timeout=600` only; `idle_transaction_timeout` (client-side idle-in-txn kill) absent from `/etc/pgbouncer/pgbouncer.ini`. Plan phrasing "idle_txn timeout" was ambiguous between these two params — clarified: intent was `idle_transaction_timeout`. Hygiene backlog.
- sshd hardening — **PARTIAL.** `PermitRootLogin no` ✅, `PasswordAuthentication no` ✅. `MaxAuthTries=3` + `AllowUsers ubuntu` NOT set (absent from `/etc/ssh/sshd_config` and `sshd_config.d/*.conf`). Hygiene backlog — 5-line sshd_config addition.
- SSH port change — **NOT APPLIED.** Port 22 (default) per `ss -tlnp`. Partial mitigation via fail2ban (D4 ✅). Security-hardening backlog (threat-model decision).
- autovacuum_naptime=15 — **NOT APPLIED.** `pg_settings.source='default'` (running 60s). Bundle with next Postgres-touching deploy. Hygiene backlog.

---

## WEEK 3 — Phase 4: Hygiene (5 commits)

- 4A: Archive handoff documents
- 4B: Archive orphaned scripts
- 4C: Improve .gitignore
- 4D: Commit S170 test files — ❌ NOT SHIPPED. Three-way drift: (a) no commit exists (`git log --all --grep=S170` → only hash-substring false positives); (b) "71 tests" conflates 49 active (3 `.py`, collected by pytest) + 22 quarantined (`test_esports_calibrators.py.disabled`); (c) 49 already running in preflight despite untracked (2061 passed this session vs S170's 1878 baseline). This session commits the 49 active as a separate test commit; 22 `.disabled` category-determination filed as §S187 Hygiene Backlog.
- 4E: trade_journal.py nested session fix — ❌ NOT APPLIED. File retains the nested pattern: outer session at `base_engine/analysis/trade_journal.py:129` held across loop at L144 calling `generate_journal_entry` at L145, which opens a new inner session at L35. BUT the code path is unreachable: `TradeJournal` instantiated at `base_engine/base_engine.py:757` with zero callers of either public method. Operational risk nil. Fix + orphan-feature CI check filed as §S187 Hygiene Backlog.

---

## WEEKS 3-12 — Phase 5v2: EsportsBot Rebuild (REPLACES Phase 5)

**EB v1 KILLED.** 4/4 kill criteria met: no profitable subset, never profitable any week, model uninformative across all calibration buckets. See rc_diagnostic.py EB-6 and rc_temporal.py Q4.

### Architecture: Rating Trinity + XGBoost + Conformal Filter

| Layer | Component | Purpose |
|-------|-----------|---------|
| 1. Ratings | Elo + Glicko-2 + OpenSkill | Three independent probability estimates |
| 2. Consensus | Trinity spread/mean/agreement | Confidence from agreement, abstain on divergence |
| 3. Meta-model | XGBoost | Combines ratings + game features → raw probability |
| 4. Calibration | Venn-ABERS | Calibrated probability with validity guarantees |
| 5. Filter | MAPIE conformal (LAC, alpha=0.10) | Only bet singletons — skip uncertain matches |
| 6. Sizing | Quarter-Kelly, $100 cap, 5% bankroll max | Conservative sizing |

**Game scope:** CS2 + LoL only. Expand after edge demonstrated.

### Sub-Phase 5v2-A: Data + Ratings Foundation (Weeks 3-4) — COMPLETE

| Item | Details |
|------|---------|
| A1 | Kill EB v1 (= D2-7) |
| A2 | Schema migration 072 — 6 new tables (esports_matches, esports_players, esports_ratings, esports_features, esports_predictions, esports_odds) |
| A3 | Oracle's Elixir loader (LoL 2024-2026) |
| A4 | GRID (primary) + HLTV (supplementary) loader (CS2 2024-2026) |
| A5 | Elo engine (team-level, K=32) |
| A6 | Glicko-2 engine (team-level, RD + volatility) |
| A7 | OpenSkill engine (player-level Plackett-Luce) |
| A8 | Trinity runner — process historical matches, snapshot ratings, compute features |

**Gate 5v2-A (PASSED):** All 3 systems produce plausible probabilities. Known dominant teams rated highest. Trinity spread ~0.05-0.10. Unit tests pass.

### Sub-Phase 5v2-B: Backtester + Meta-Model (Weeks 5-6) — COMPLETE

| Item | Details |
|------|---------|
| B1 | Walk-forward engine (train patches N-3..N-1, predict N) |
| B2 | XGBoost meta-model (trinity features + game-specific) |
| B3 | Venn-ABERS calibration (per-game) |
| B4 | MAPIE conformal filter (singleton at alpha=0.10) |
| B5 | CLV tracking (Pinnacle odds) |
| B6 | Metrics suite (accuracy, Brier, log loss, ECE, CLV, yield, drawdown, z-score) |
| B7 | Full backtest: CS2 + LoL, 2024-2026 |

**Gate 5v2-B (PASSED 5/6, CLV deferred to shadow):** Accuracy >58% singletons, Brier <0.23, CLV >+1.5% vs Pinnacle, singleton rate >30%, z-score >1.5, both games individually profitable. **If fails after 2 iterations → stop.**

### Sub-Phase 5v2-C: Shadow Mode (Weeks 7-9) — LIVE

| Item | Details |
|------|---------|
| C1 | Live data pipeline (GRID/HLTV real-time) |
| C2 | Market discovery (match → Polymarket market_id mapping) |
| C3 | Shadow prediction engine (log to esports_predictions mode='shadow') |
| C4 | Live CLV tracking |

**Duration:** Min 2 weeks or 50 resolved predictions.
**Gate 5v2-C (HARD):** Shadow accuracy >55%, Brier <0.25, CLV >+2% vs Polymarket, backtest-to-shadow drop <5%.

### Sub-Phase 5v2-D: Paper Trading (Weeks 10-12+) — LIVE (dry_run, shadow)

| Item | Details |
|------|---------|
| D1 | Wire to base_engine (bots/esports_bot_v2.py extending BaseBot) |
| D2 | Sizing: quarter-Kelly, $100 cap, D7 hard stop -50% |
| D3 | prediction_log writes (model_version='v2-trinity') |
| D4 | Enable: BOT_ENABLED=true, SIMULATION_MODE=true |

**Duration:** Min 4 weeks or 100 resolved predictions.
**Gate 5v2-D:** P(edge>0) >= 0.70 via edge_verification.py, accuracy >55%, wl_ratio >0.80, max drawdown <25%.

### Risk Controls

| Control | Value |
|---------|-------|
| Max position | $100 |
| Max bankroll/bet | 5% |
| Kelly fraction | 0.25 |
| Hard stop-loss | -50% (D7) |
| Min edge | 5% |
| Conformal filter | alpha=0.10, singleton |
| Trinity guard | spread < 0.15 |
| Max daily bets | 10 |
| Stale rating guard | skip if last match > 45 days |
| Patch guard | 50% sizing for 2 weeks post-patch |

### New Dependencies (pip)

```
openskill>=6.0.0       # Player-level ratings
venn-abers>=0.4.0      # Calibration
hltv-async-api>=0.8.0  # CS2 data (supplementary)
shap>=0.43.0           # Feature importance
# Already installed: xgboost, mapie
```

### Code Organization

```
esports_v2/           # Parallel to existing esports/
  ratings/            # elo.py, glicko2.py, openskill_engine.py, trinity.py
  features/           # cs2_features.py, lol_features.py, feature_registry.py
  model/              # meta_model.py, calibrator.py, conformal.py
  data/               # hltv_loader.py, oracle_loader.py, grid_loader.py, odds_loader.py, normalizer.py
  backtest/           # walk_forward.py, metrics.py, runner.py
  scripts/            # load_historical.py, run_backtest.py, run_shadow.py

bots/esports_bot_v2.py   # Bot class (extends BaseBot)
tests/esports_v2/         # Unit + integration tests
```

**Note:** Phase 5v2-E (scan-cycle cost reduction, deferred) is tracked in the Corrections Log section below.

---

## WEEKS 4-10 — Phase 6: WeatherBot Elevation (WB-scoped, extended for 6J)

**Gate (updated v7):** Phase RC findings + post-Day-2 data. WB must show improvement on post-fix trades (D2-2, D2-3 applied) before elevation proceeds. Re-run edge_verification.py on trades after Day 2 deploy timestamp (20260414_132211). Concrete thresholds:
- **P(edge>0) ≥ 0.30** on post-fix trades: proceed with Phase 6 (directionally positive, fixes are helping).
- **P(edge>0) < 0.10** after 4 weeks: Day 2 fixes didn't address enough. Phase 6 items must target remaining loss drivers (e.g., 6D calibration, 6F YES-side strategy) before general elevation.
- **Minimum sample:** 200+ closed trades on post-fix data before evaluating gate.

Note: 1B calibration infrastructure (CRPS/PIT) is a prerequisite and is SHIPPED (`scripts/calibration_check.py:110-188`, commit `ccae341`). Gate now requires measured improvement on post-fix data, not just infrastructure existence.

**WB Phase 6 mechanism interpretation (updated S205, 2026-04-30; framing tracks three sessions of reframes — S203/S204/S205, see §Corrections Log §S204 / §S205).** Phase 7 verdict for WB is INVESTIGATE per S199 Hygiene Backlog. S203 framed root cause as "station noise" (falsified). S204 confirmed H0' "NO 24-48h calibrator over-confidence" via PIT-KS rejection on the `[0.9-1.0)` confidence bin. S205 falsified all three H0'' feature-engineering sub-candidates (boundary-risk firing, rolling-station-MAE, per-city volatility) at per-station drill-down. Operative interpretation: the failing NO 24-48h cohort is not addressable by single-dimension calibrator-feature engineering at the level tested; the variance source may be exogenous to single-station weather-data observables. **6Q (confidence-tail sizing dampener) shipped under this interpretation as risk-reduction mitigation, NOT root-cause closure.** Joint-conditioning candidate classes (station × regime × lead-time interactions) and exogenous-to-weather candidate classes (market-resolution path, position-sizing asymmetry, YES/NO outcome systemic asymmetry) remain untested. Root-cause work re-opens if the cluster failure pattern recurs post-6Q-flag-flip — see §S205 Hygiene Backlog item "WB Phase 6 root cause unidentified."

Station mapping MOVED HERE from Phase 12 (resolves 6N↔12A circular dependency).

**Suggested execution order:** Sub-phase A (Weeks 4-5): 6A, 6B, 6-STATION, 6G, 6H, 6O. Sub-phase B (Weeks 6-7): 6C ∥ 6K, 6D, 6E. Sub-phase C (Weeks 8-10): 6L, 6F, 6J, 6M, 6N, 6P, 6Q, 6I.

| Item | Details | Prerequisite |
|------|---------|-------------|
| 6A: S167 P0 reentry_check (full fix) | 13-module refactor. | D10 interim deployed |
| 6B: Orphan reconciliation (ongoing) | Scheduled recurring. | 1E complete |
| 6-STATION: Station-to-model mapping (was 12A) | Map Polymarket cities → exact ICAO station codes. Start top 5. Moved here because 6N, 6K, 6L all depend on it. | None |
| 6C: Ensemble forecast integration | GEFS + ECMWF ENS. | 1B calibration gate |
| 6K: AIFS ENS integration | 51-member ML ensemble, free via Open-Meteo. Can parallelize with 6C — AIFS is just another data source. | 6-STATION |
| 6L: Multi-model ensemble | GEFS + AIFS + HRRR. BMA weighting. | 6C + 6K (parallel) |
| 6D: Per-side beta calibration | 3-param Kull. | 200-500 samples per side |
| 6E: EMOS recalibration | Ensemble Model Output Statistics. | CRPS/PIT shows bias |
| 6F: YES-side graduated strategy | Edge → Brier gating → beta cal → Murphy. | 6D |
| 6G: GHCN LRU cache | Replace permanent cache. | None |
| 6H: Shadow entry pruning | Review utility. | None |
| 6I: WebSocket upgrade | Real-time price feeds. | Architecture design |
| 6J: Agile city rotation (minimal) | Auto-discover new cities, dynamic station resolution (ICAO lookup + fallback), calibration cold-start (nearest-city prior), retirement logic. This is a mini-product — estimate 2-3 weeks alone. Sub-plan needed: discovery → resolution → cold-start → retirement. | 6C + 6E + 6-STATION |
| 6M: Conformal prediction | MAPIE wrapper for brackets. | Any base model |
| 6N: Neural post-processing MLP | Station-specific MLP, CRPS loss. | 6-STATION (dependency resolved by moving station mapping here) |
| 6O: Lead-time optimal window | Backtest existing WB trades to measure actual optimal lead time. Don't guess at 6-24h — measure it. | Historical trade data |
| 6P: Bayesian small-sample calibration | Beta-Binomial for <50 resolved markets per city. | 6J city rotation |
| 6Q: WB position sizing upgrade | If calibration improves (whole point of Phase 6), sizing should scale with calibration confidence. Implement simple confidence-scaled sizing for WB (not full 8R portfolio Kelly). Without this, WB runs improved calibration with old sizing for months. **Trigger threshold:** CRPS improvement ≥ 5% relative to pre-Phase-6 baseline OR Brier Score improvement ≥ 0.02 absolute, both statistically significant at p<0.05 on ≥100 resolved predictions. Below threshold: keep current sizing. **SHIPPED 2026-04-30 (commit `7c60938`), gate `WEATHER_CONFIDENCE_DAMPENER_ENABLED` default false, awaiting operator flag flip.** **Plan-deviation:** shipped without 6D/6E prerequisite or CRPS/Brier trigger threshold being met, after S204→S205 falsified all three H0'' feature-engineering sub-candidates for the NO 24-48h failing cohort. Rationale: feature-engineering exhausted at the single-dimension level; risk-reduction is the active mitigation while joint-conditioning candidates remain untested. **Mitigation, not closure** — root-cause work re-opens per §S205 Hygiene Backlog if cluster failure pattern recurs on post-flag-flip window. See §Corrections Log §S205. | 6D or 6E calibration improvement confirmed per threshold (BYPASSED — see deviation note) |

---

## MONTH 2-3 — Phase 7: MirrorBot Elevation (MB-scoped)

**Gate (updated v7):** 1G + 500+ logged predictions + RC findings. MB must show improvement on post-Day-2 data (D2-4 through D2-6 applied). Re-run edge_verification.py on trades after Day 2 deploy timestamp (20260414_132211). Concrete thresholds:
- **P(edge>0) ≥ 0.30** on post-fix trades: proceed with Phase 7 (directionally positive).
- **P(edge>0) < 0.10** after 4 weeks: crypto block + wallet blacklist + whale filter didn't address enough. Investigate remaining loss drivers before elevation.
- **Minimum sample:** 500+ closed trades on post-fix data before evaluating gate.

**Current status (as of 2026-04-19 verification):** 4/11 items shipped — 7E (`scripts/gate_score_expectancy.py`, a3052a7), 7G (`scripts/cooldown_analysis.py`, 344f1e2), 7J (`base_engine/learning/prediction_drift.py`, 3313874), 7K (`base_engine/learning/venn_abers_intervals.py`, 11fac16). 136,895 MB prediction_log rows accumulated — 500-row gate satisfied 273×. 7B (wallet selection overhaul) is the next highest-ROI unblocked item.

| Item | Details | Prerequisite |
|------|---------|-------------|
| 7A: Event-driven WebSocket | Sub-second copy-trading. | Architecture design |
| 7B: Wallet selection overhaul | Tighten copy criteria. | 500+ prediction_log rows |
| 7C: Leader-exit signals | Exit when leader exits. | 7A |
| 7D: Basket consensus | Multi-specialist agreement. | 7B |
| 7E: Gate_score expectancy analysis | Per-bucket. | Prediction_log data |
| 7F: Slippage-adjusted paper evaluation | Discount for slippage. | 2J data |
| 7G: Re-entry cooldown review (analytical) | Track re-entry accuracy. Inform whether to re-enable from D8's disable. | Prediction_log accumulation |
| 7H: LLM-augmented signal | RAG-based: news → LLM → probability. For entry decisions (pre-copy evaluation), NOT real-time copy timing — LLM inference takes 5-10s, incompatible with sub-second copy. | Architecture design |
| 7I: Hedge/MWU signal aggregation | Combine LLM + copy + market features. Last item in Phase 7 — requires 7H (LLM), 7B (wallets), and existing copy signal all active. | 7H + 7B + existing signals |
| 7J: Concept drift (ADWIN-U) | Unsupervised drift detection — correct for delayed resolution. | 1G working |
| 7K: Venn-ABERS calibration (MB-specific) | Provably calibrated prediction intervals for MirrorBot copy signals. | Calibration data |

---

## MONTH 2-4 — Phase 8: Cross-Bot + Infrastructure (split across 2 months)

### Month 2 — Infrastructure items (should precede bot-specific work):

| Item | Details |
|------|---------|
| 8A: Central position registry | Full cross-bot mutual exclusion. |
| 8B: Prediction gate decisions | MB 500+, EB v2 100+ (model_version='v2-trinity', NOT v1). Kill = BOT_ENABLED=false in .env, systemctl disable service, keep code intact. Reversible if root-cause fix found. Retrain = unfreeze retraining with new training data. Proceed = proceed to live at 25% previous size. Gate: if calibration data insufficient at threshold, run on whatever's available + acknowledge wider CI. |
| 8P: Evidently AI drift monitoring | Data drift, concept drift, prediction drift. |

### Month 3-4 — Bot-specific items:

| Item | Details |
|------|---------|
| 8C: Correlation_id propagation | End-to-end trace. |
| 8D: Exit parameter sweep | Replay resolved positions. |
| 8E: Unpriced token dual-write to PG | |
| 8F: Unpriced token escalation | |
| 8G: market_id_mapping table | |
| 8H: Cross-bot calibration framework (Platt scaling) | Generalizes calibration across all bots. Venn-ABERS for MB done in 7K; this is the shared Platt scaling infrastructure for EB/WB. |
| 8I: pg_partman for trade_events | |
| 8J: systemd service templates | |
| 8K: Remove dead EnsembleBot code | EnsembleBot is DEAD (0 positions, last activity Mar 6, not in BOT_REGISTRY). Remove bot code, update 8 dependent test files to not import it. No revival path — if ensemble logic needed later, build fresh. |
| 8L: CLV scaling evaluation | |
| 8M: RL Trade Timing Agent evaluation | |
| 8R: Fractional Kelly portfolio controls | Requires calibration data for ≥2 of 3 bots. If only WB qualifies, implement single-bot Kelly for WB. Portfolio-level Kelly deferred until second bot qualifies. |

### REMOVED from Phase 8 (folded into bot phases):

- 8N (F-EDL) → fold into each bot's elevation phase as per-bot model output change
- 8O (Shadow mode) → promoted to Phase 1L
- 8Q (Feast) → promoted to Phase 2K

---

## MONTH 3-4 — Phase 10: Strategic Foundation

| Item | Details | Prerequisite |
|------|---------|-------------|
| 10D: Telegram alert bot | 5-min cron, ~50-100 lines. | 2F health check |
| 10F: Minimum viable backtester | Event-driven replay. Results advisory below 5,000 orderbook snapshots per market. At Month 3-4, ~2-3 months of 60s data = ~2,500 scans for WB — directional, not definitive. Treat as confidence-building, not validation. | 1J data (accumulating since Week 1) |

---

## MONTH 4-8 — Phase 12: WeatherBot EMOS Transformation

Station mapping already done in Phase 6. Build on it.

| Item | Details | Prerequisite |
|------|---------|-------------|
| 12B: Ensemble data access | GEFS, ECMWF ENS, HRRR. | API integration |
| 12C: Station-specific EMOS training | Gaussian EMOS, CRPS minimization. MVP: top 5 cities. | 6-STATION + 12B |
| 12D: Multi-model blending | Lead-time-dependent weights. | 12C |
| 12E: Automated edge pipeline | Market discovery → EMOS → edge → order. | 12C + 12D |
| 12F: City mastery extension | Extend Phase 6J's minimal rotation system with EMOS integration. Don't build it twice — Phase 6J is the foundation, Phase 12F extends it. | 12E + 6J |

---

## ONGOING — Phase 13: Compliance + Observability

| Item | Details | Prerequisite |
|------|---------|-------------|
| 13A: Tax position establishment | Consult tax professional. | None |
| 13B: Trade logging completeness | Verify trade_events for tax. | None |
| 13C: State residency monitoring | Track regulatory actions. | None |
| 13D: Streamlit dashboard | ~150-200 lines, 80-150MB RAM. | 10D |
| 13E: PG LISTEN/NOTIFY | Near real-time dashboard. | 13D |

---

## Decisions

**From v6.0:**
- **WeatherBot:** KEEP RUNNING with guardrails
- **market_prices:** KILL (Option B, after pgBackRest + slippage refactor)
- **D7:** Per-bot stop-loss, shared check REPLACES EB-specific override (one code path)
- **D8:** 24h re-entry cooldown, signal override in MB only (don't touch base_bot)
- **D10:** TTL = min(time_to_resolution, 6h) floor 1h
- **PG OOMScoreAdjust:** -900 (not -1000)
- **Pool settings:** Investigate first, conservative reduction, 48h monitor with rollback trigger
- **Calibration gate:** WB immediate, MB/EB after prediction_log fix
- **1I as graduated gate:** ≥0.9 → full elevation, 0.7–0.9 → core items only, <0.7 → root-cause investigation
- **NegRisk:** REMOVED
- **Maker orders:** REMOVED
- **Phase 6J city rotation:** Incremental build — minimal in Phase 6J, extend in Phase 12F. Don't build twice.

**From v7.0 (RC + 5v2):**
- **EB v1:** KILLED via 8B procedure. Code retained. Historical trade_events preserved.
- **EB v2:** Rating trinity (Elo + Glicko-2 + OpenSkill) + XGBoost + Venn-ABERS + MAPIE conformal.
- **EB v2 game scope:** CS2 + LoL only. Expand after edge demonstrated.
- **EB v2 methodology:** Backtest → shadow → paper → conditional live. No phase skipping.
- **EB v2 backtest gate:** Accuracy >58% singletons, Brier <0.23, CLV >+1.5%, both games profitable.
- **EB v2 paper gate:** P(edge>0) ≥ 0.70, 100+ resolved predictions.
- **EB v2 position cap:** $100.
- **WB Day 2:** Flat sizing ($100 cap), blacklist 6 worst cities. Both sides keep trading — not enough data to kill YES.
- **MB Day 2:** Block crypto, blacklist 5 wallets, require whale ≥ $100. Sports-NO keeps trading — not enough data to block a full side.
- **Cross-bot Day 2:** Max position $200 cap via BotBankrollManager.
- **WB/MB gates updated:** Post-Day-2 data must show improvement before elevation proceeds.

---

## Changes v5.1 → v6.0

- D5 (backup) moved to FIRST ACTION — before any config changes
- PG OOMScoreAdjust → -900 (not -1000, avoids OOM thrash)
- D3 dedup key specified — RESOLUTION on (market_id, bot_name), EXIT on (market_id, bot_name)
- D5 marked as superseded by pgBackRest once 1H stable
- D7 REPLACES EB-specific check — one code path, not two
- D8 re-entry → 24h cooldown (not ambiguous "or disable entirely")
- D10 TTL → min(time_to_resolution, 6h) floor 1h (not arbitrary 4h)
- 1B MB/EB → "N/A until Phase 7/5 gates" (not "blocked" which implies temporary)
- 1E split into 1E-a (migration) + 1E-b (gateway) — separate commits
- 1I is HARD GATE for Phases 5-7
- 1J rate limits specified — prioritize by volume if API throttling exceeded
- 1L: Shadow mode protocol promoted to Phase 1 (was 8O Month 2-3)
- 1M: Strategy lifecycle schema promoted to Phase 1 (was 10B Month 3-4)
- 2K: Feast promoted to Phase 2 (was 8Q Month 2-3) — precedes elevation phases
- 2G rollback plan added — revert if >5 pool exhaustion events in 48h
- 2H per-bot multiplier — WB 10×, MB 5×, EB 3×
- Phase 5 scoped to 6-8 weeks (not 2 weeks). Core vs deferred split. Hidden data pipeline dependencies flagged (HLTV, Oracle's Elixir, Liquipedia).
- 5C+5O merged — evaluate before building
- 5N → MapieClassifier (not MapieTimeSeriesRegressor)
- 6-STATION moved from Phase 12 to Phase 6 — resolves circular dependency
- 6C ∥ 6K parallelized — AIFS is another data source, doesn't depend on basic ensemble
- 6J acknowledged as mini-product (2-3 weeks, needs sub-plan)
- 6O → backtest actual lead time (don't guess)
- 7H → entry decisions only (LLM too slow for real-time copy timing)
- 7I → last item in Phase 7 (needs all signals active)
- Phase 7 timeline → 7 weeks (not 6) for 500+ predictions
- Phase 8 split across 2 months — infrastructure Month 2, bot-specific Month 3-4
- 8N (F-EDL) folded into bot phases — per-bot model output change
- 12F extends 6J — don't build city rotation twice
- Operational procedures added — rollbacks, maintenance windows, testing requirements, shadow mode, capital during elevation
- 4D → verify tests pass first before committing

---

## Success Criteria (8-month plan exit — AMENDED v7)

- **WB and MB** have P(edge > 0) ≥ 0.7 on post-Day-2 trades (measured after 4+ weeks accumulation)
- **EB v2** has P(edge > 0) ≥ 0.7 on paper trades (measured at 5v2-D gate). If EB v2 fails its gate: kill via 8B procedure, disable data pipeline crons, `systemctl disable polymarket-esports-v2`. Tables retained for future analysis. **System operates with 2 bots.**
- Total portfolio maximum drawdown under 25% of deployed capital
- Zero unresolved P0 audit items
- All prediction gates met: WB calibrated, MB 500+ predictions, EB v2 100+ predictions (not v1)
- Shadow mode protocol used for every model change — no exceptions
- Backups operational (pgBackRest PITR or pg_dump daily minimum)
- If any bot fails its gate: killed or retrained per defined criteria, not left running with negative expectancy

## Verification (v6.0 + v7 additions)

- pytest 1892+ pass after each commit (updated baseline from Phase 1; currently 1843+ as of S182)
- Every new subsystem has integration tests before merge
- Rollback tested for schema changes
- D5 backup exists before ANY Day 1 config changes
- 1I edge verification gates Phases 6-7 (5v2 has its own gate sequence)
- 1J orderbook collection running (check rate limit compliance)
- Shadow mode protocol documented and followed for all model changes
- **Day 2 fixes:** re-run edge_verification.py after 4 weeks of post-fix data (deploy 20260414_132211)
- **5v2-A:** All 3 rating engines have unit tests. Integration test on 100 matches.
- **5v2-B:** Walk-forward backtest with shuffle-label control. Reliability diagram.
- **5v2-C:** Shadow predictions logged for min 2 weeks / 50 resolved.
- **5v2-D:** P(edge>0) via edge_verification.py. rc_diagnostic.py on v2 trade_events.
- **Phase RC scripts validated:** rc_diagnostic.py (35 diagnostics), rc_verify.py, rc_temporal.py — all invariant checks PASS, cross-validated against bot_pnl.py.
- Phase 6: Station mapping complete, CRPS/PIT delta measured, 6C∥6K parallelized
- Phase 7: 500+ prediction_log rows (satisfied 273×), LLM signal used for entry only
- Phase 8: Prediction gates at 100 (EB v2) / 500 (MB) — run on available data with explicit CI
- Phase 12: EMOS top 5 cities, city rotation extends (not rebuilds) Phase 6J

---

## Critical Files

- **Day 1:** risk_manager.py (D7), mirror_bot.py (D8), weather_bot.py (D10), config/env
- **Phase 1:** frozen_price_check.py (1A), calibration_check.py (1B — CRPS/PIT shipped `ccae341`), order_gateway.py (1E-b), weather_bot.py (1E-a), mirror_bot.py+esports_bot.py (1G), esports_bot.py (1F), new edge_verification.py (1I), orderbook cron (1J), shadow mode doc (1L), strategy lifecycle migration (1M)
- **Phase RC:** rc_diagnostic.py, rc_verify.py, rc_temporal.py
- **Day 2:** bankroll_manager.py (D2-1 cap), weather_bot.py (D2-2 sizing, D2-3 city blacklist), mirror_bot.py (D2-4 crypto block, D2-5 wallet blacklist, D2-6 whale filter), .env.esports (D2-7 EB kill)
- **Phase 2:** logging_setup.py (2C,2D), health_check.sh (2F enhance), slippage_check.py (2J), Feast config (2K — SKIPPED per S179 decision), order_gateway.py:625-652 (2H-3 shipped `b786316`)
- **Phase 5v2:** esports_v2/ (new tree), bots/esports_bot_v2.py, migration 072, tests/esports_v2/
- **Phase 6-7:** Bot files, .env files, model files, WebSocket handlers, rating system implementations
  - 7E `scripts/gate_score_expectancy.py`, 7G `scripts/cooldown_analysis.py`, 7J `base_engine/learning/prediction_drift.py`, 7K `base_engine/learning/venn_abers_intervals.py` — all shipped
  - 7B target: `bots/elite_watchlist.py` (1045 lines existing, retune against 136,895 MB prediction_log rows)
- **Phases 10-12:** Telegram bot, backtester, EMOS pipeline, city mastery system

---

## Corrections Log

### S180 (2026-04-17) — Retraction of S179 §3 "position_manager.py:930" bug claim

**Claim (in S179 handoff §3, now superseded):** Stage-2 illiquidity CLOB call at `base_engine/execution/position_manager.py:930` used wrong kwargs (`size=` instead of `trade_size=`, missing `market_id=`), raising TypeError silently, meaning stage-2 never executed in production. Allegedly blocked 2I (illiquidity exit enablement).

**Retraction:** The claim was a misread. Verified by S180 (2026-04-17) against code at HEAD:

```python
# base_engine/execution/position_manager.py:929-932
_liq_result = await asyncio.wait_for(
    _lg.check_liquidity(market_id=_mid, token_id=_token_id, trade_size=size, side="SELL"),
    timeout=5.0,
)
```

where `_mid = str(getattr(position, "market_id", ""))` (L917) and `_token_id = getattr(position, "token_id", "")` (L928). Kwargs match `check_liquidity()` signature at `base_engine/risk/liquidity_guardian.py:25-33`. `tests/unit/test_illiquidity_exit.py:173-192` exercises stage-2 and passes.

**Consequence:** 2I is CODE READY (not blocked). Any spawned "fix position_manager.py:930" task is a false positive — dismiss.

**Kept as durable record** because `AGENT_HANDOFF_*.md` is gitignored (`.gitignore:147`) — handoff retractions don't propagate cross-machine without a breadcrumb here.

### S180 Hygiene Backlog

**rollback.sh service-list drift.** `deploy/deploy.sh` step 6 starts 4 services (weather, mirror, esports, ingestion). `deploy/rollback.sh:41` restarts only 3 (missing ingestion). The bug isn't "missing line" — it's that `rollback.sh`'s service list has drifted from `deploy.sh`'s service list and there is no mechanism keeping them in sync. Fix options (prefer A):

- **A.** Shared constants file, e.g. `deploy/common.sh` exporting `BOT_SERVICES=(...)`, sourced by both `deploy.sh` and `rollback.sh`.
- **B.** Minimum viable: pinned comment at the top of each script pointing at the other with a note "keep service list in sync." Drift-detectable by grep.

Discovered S180 (2026-04-17). Safe to defer: none of the S180 commits touched the ingestion path, so rollback's ingestion gap was harmless for the 2026-04-17 deploy. Will become dangerous next time an ingestion-affecting commit ships and rollback is needed.

**`scripts/check_illiquidity_stage2.sh` — committed morning-check script.** Step 8 (2I illiquidity exit enablement) uses a passive-wait strategy: observe natural stage-2 CLOB triggers in live logs, flip `ILLIQUIDITY_EXIT_ENABLED=true` once one fires cleanly. The daily check is a grep pattern that's easy to forget across sessions. Wrap it once:

```bash
# scripts/check_illiquidity_stage2.sh
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "journalctl -u polymarket-weather -u polymarket-mirror -u polymarket-esports \
   --since '24 hours ago' | grep -E 'illiquidity_check_stage2|illiquidity_exit' | tail -50"
```

Makes the check one command, not a remembered grep. Durable across sessions. Low priority; write when convenient.

### S183 Hygiene Backlog

**Background task survivability audit.** Tasks launched via the in-session `background` pattern (observed during S182 soak: task IDs `bvbeuc1ta` T+2h and `bkxdhbi24` T+4h) produce 50-byte output stubs and appear to have launched cleanly, but are actually blocking on a long leading `sleep` inside the parent session's process tree. When the parent session closes, the sleep is killed and the task never fires its payload — no error, no notification, no completion. Observed in S183: both tasks' `.output` files contained only `"Waiting <N>s until T+<X>h at <timestamp>"` and never produced query output. Contrast with `mcp__scheduled-tasks__*` which persists to disk and survives session close (worked correctly for S183 T+4h at `anchor+42s`).

**Fix options (prefer A):**
- **A.** Audit every task-launch path used by agents — background bash with `sleep`, `Monitor` with `persistent: false`, etc. — and publish a "survives session close? Y/N" matrix in agent instructions. Anything with "N" is a false-affordance and should be replaced in agent prompts with `mcp__scheduled-tasks__*` or `CronCreate(durable: true)`.
- **B.** Minimum viable: emit a WARNING in the harness when a task is launched via a pattern known to not survive, with `"this task will be killed if the session closes — use mcp__scheduled-tasks for durable scheduling"`.

Discovered S183 (2026-04-19/20). Phase 4 backlog. Dangerous because "scheduled a background task" reads as complete when it isn't.

**Session template codification — `§Session Template` before Phase 5 sentinel.** The pattern `inherit-handoff → verify-claims → walk-backward-hypotheses → fix-with-tests-and-gates → codify-protocols-from-failures` has now executed 3 sessions running (S180, S181 partial, S182). User flagged this in S182 as a backlog item and again in S183. Template should land as a `§Session Template` section in this plan file so future sessions default to it rather than re-deriving.

**Drafts, in rough priority:**
1. **Inherit-handoff.** Read predecessor handoff; verify the *one claim most load-bearing* (e.g., "X was deployed" → check git log on VPS release; "Y was fixed" → grep code for the supposed fix). Handoffs drift.
2. **Verify-claims.** If the claim doesn't hold, update `memory/project_s172_current_state.md` as first action. Don't build on a false foundation.
3. **Walk-backward-hypotheses.** Each time a hypothesis is invalidated, log the inversion and the diagnostic that inverted it. S182 ran 3 consecutive inversions — this is expected, not exceptional.
4. **Fix-with-tests-and-gates.** Every shipped commit carries unit test + post-deploy gate (e.g., T+30min query). No exceptions.
5. **Codify-protocols-from-failures.** Concrete failure → named protocol (§Protocols 1-4). Generic "we should be more careful" observations don't qualify.

Should ship before Phase 5 sentinel deploys because the sentinel session itself will benefit from having the template explicit (and the sentinel's check #4b — persistent-findings watchdog — sits downstream of the protocol-codification step). 30-minute scope.

Discovered via user's 3-session retrospective (S182 audit review, S183 audit review).

### S184 (2026-04-20) — Gitignore-claim drift caught by Protocol 5

**Claim (S183 predecessor handoff §10):** A carbon-copy handoff file was reasoned not to be matched by any gitignore pattern.

**Reality:** `.gitignore:157` `*_HANDOFF.md` is a suffix glob; any file ending in `_HANDOFF.md` is ignored, including carbon-copy variants.

**Why logged.** Small documentation-level drift, but a concrete instance of Protocol 5 discipline applied below the phase/item tier. The §Protocol 5 evidence-of-origin cases (2H-3, 1B, scheduled_daily labeling) are all phase-level; this widens the evidence base to documentation-claim scale. Caught by reading `.gitignore` directly rather than propagating the predecessor's written reasoning.

### S185 (2026-04-20) — P0 recon_type classification drift: FIX_ROOT → FIX_AUDIT_CHECK (or bulk ACK)

**Claim (S183 close §4.2, propagated through S184 §6):** Remaining P0 set — `SIZE_INVARIANT`, `FK_MISSING_MARKET`, `POSITION_SIZE_MISMATCH` — are FIX_ROOT (actual bot bugs), not FIX_AUDIT_CHECK (supersession/data-filter), and "each probably a multi-commit investigation."

**Reality (verified against VPS `polymarket` DB, 2026-04-20):** All three recon_types have a latest-underlying-event timestamp that is strictly older than the audit's own detected_at (audit re-fires daily against historical data). Approximate freeze ages:
- SIZE_INVARIANT: no new underlying events since ~2026-04-08 (12 days frozen relative to today).
- POSITION_SIZE_MISMATCH: WB-dominant; underlying events show `SELL` EXIT rows joined against `side='SELL'` position rows with zero matching `ENTRY`s — exact shape of the S163 legacy encoding where EXITs were recorded with `side='SELL'` while ENTRYs used `YES`/`NO`.
- FK_MISSING_MARKET: orphan `event_time` range is a tight 5-day window 2026-03-24 → 2026-03-29. Nothing since.

(Exact audit-row counts available from VPS `reconciliation_breaks` query; omitted here to avoid recording trade-count-adjacent specifics that belong in a bot_pnl-sourced summary. Run the VPS query directly to snapshot current state.)

**Mechanism.** All three are historical-frozen:
- SIZE_INVARIANT: fix aligns with S163 size-accounting work.
- POSITION_SIZE_MISMATCH: `position_trade_events_check.py` query `GROUP BY bot_name, market_id, side` matches legacy `SELL` EXIT rows to `side='SELL'` position rows — which have 0 ENTRYs and negative net. Same root as `size_invariant_check.py`'s documented S164 fix (grouping by side creates false positives on S163-era data) — fix applied to size check but not to this check.
- FK_MISSING_MARKET: likely tied to an ingestion-gap fix within the 2026-03-24 → 2026-03-29 window.

**Why violations keep growing despite being historical.** `base_engine/audit/result_store.py` dedups on `(recon_date, violation_hash)` — per-day. No auto-close when the underlying data no longer reproduces the violation. Each daily audit re-emits the same hash as a new row with today's `recon_date`. OPEN count trends up indefinitely until manual ACK.

**Correct dispositions (all FIX_AUDIT_CHECK / FIX_DATA, not FIX_ROOT):**
1. **Bulk ACK** existing OPEN rows for these 3 recon_types where the latest reproducing `trade_events.event_time` is pre-freeze-cutoff. Separates the historical noise floor from live detection without weakening the checks.
2. **Port the S164 `GROUP BY` fix** to `position_trade_events_check.py` (drop `side` from the join), matching `size_invariant_check.py:28-42` rationale.
3. **Filter window** in the checks (e.g., `WHERE event_time >= :min_time`) so historical data stops re-emitting. Optional once (1) is done.

**Why logged here rather than acted on.** Bulk ACK of ~17,825 OPEN rows across 3 recon_types is a material state change deserving explicit authorization and a dedicated session. The check-logic edits are ~5-10 lines each and low-risk; safe to bundle. The handoff chain's "FIX_ROOT, multi-commit each" framing would have driven a multi-session investigation of bot bugs that aren't there. Protocol 5 caught the drift by reading the data; next session should act with the corrected classification.

**Evidence of origin.** Direct queries against VPS `polymarket` DB — counts by recon_type+status, date-of-latest underlying event per market-in-violation-set, WB side/event distribution, FK orphan `event_time` range. SQL-contract discipline applied: schema read (`\d reconciliation_breaks`, `\d trade_events`) preceded each query after two column-name misses (`first_seen_at`, `occurred_at`) — matching the SQL-contract candidate just added to §Protocol candidates.

**Companion finding.** `result_store.py` has no auto-close mechanism: previously-OPEN violations whose underlying data no longer reproduces stay OPEN indefinitely. Not specific to this P0 set — every check that ever runs accumulates this drift. Filed as Phase 4 backlog item in §S185 Hygiene Backlog below. The P0 triple is the current concrete manifestation; the gap is structural.

**Execution ordering for next session.** Port the S164 `GROUP BY` fix to `position_trade_events_check.py` FIRST, then bulk ACK the historical OPEN rows. Reversed order undoes itself — the next daily audit would re-emit everything just ACK'd.

### S185 Hygiene Backlog

**`base_engine/audit/result_store.py` — no auto-close mechanism for chronic OPEN findings.** `_persist_run_results()` at approximately L66-101 INSERTs each detected violation as a new row with `status='OPEN'` and dedups only on `(recon_date, violation_hash)` — per-day. No path exists to mark a previously-OPEN violation as RESOLVED when the underlying data no longer reproduces it. Consequence: every violation ever detected stays OPEN until manually ACK'd or the `violation_hash` changes (which it doesn't for stable violations). The OPEN backlog trends monotonically up for every recon_type. S185's historical-frozen P0s (SIZE_INVARIANT, PSM, FK_MISSING_MARKET) are the current concrete manifestation but the gap is structural — every future check will accumulate the same chronic-OPEN drift.

**Fix options (prefer A):**
- **A. Auto-close by non-reproduction.** At end of each audit run, for every previously-OPEN violation whose `violation_hash` was NOT re-emitted in the current run, mark RESOLVED with `resolution_note='auto_closed_not_reproduced'`. Requires one set-diff between "pre-run OPEN violation hashes" and "current-run emitted hashes." Safe: a violation that reappears later INSERTs fresh and reopens naturally.
- **B. Auto-close by age.** After N days of continuous OPEN status without ACK, auto-close. Weaker than A — a genuinely unresolved long-running bug could silently auto-close.
- **C. Event-time threshold per check.** In each check, only emit violations whose underlying `event_time >= NOW() - M days`. Pushes the decision into each check individually; no auto-close mechanism needed but requires touching every check.

Discovered S185 (2026-04-20) during P0 triage. Phase 4 backlog. Dangerous because "audit found N open findings this week" is not a trend signal — N has a monotonic-growth floor independent of real bug emission, so a rising N means "more days elapsed since the last ACK sweep," not "more bugs."

### S186 (2026-04-20) — 6O lead-time backtest deferred indefinitely

**Context.** S185 created `scripts/wb_lead_time_backtest.py` (the "6O script") to produce bucket-level WB lead-time P&L with finer granularity (9 buckets) than `bot_pnl.py` r7 emits (5 buckets). The script was filed behind a Rule-Zero header warning and a validation workflow: run `scripts/bot_pnl.py WeatherBot <window>` over the same window, reconcile totals, quarantine if totals disagree. S186 executed the validation: totals disagreed significantly. Per the Rule-Zero header, the script is quarantined.

**Root cause of the mismatch.** 6O's CTE joins `entry_events` → `resolved` many-to-one on `(market_id, side)`. When a market has multiple same-side ENTRY rows (WB has many: position stacking / re-entry is common), each entry joins to the same single `resolved` row and the same `realized_pnl` is counted once per entry. `bot_pnl.py` r7 inverts the join (RESOLUTION/EXIT rows as the driver, each joined to a DISTINCT-ON-market ENTRY for lead-time lookup) — the correct semantics.

**Disposition — defer 6O indefinitely. Struck from queue.**

Reasoning:
- The per-lead-time bucket data already exists in `bot_pnl.py` r7. Coarser (5 buckets) but canonical. Any WB lead-time retune can cite that output directly without needing the 6O script.
- The qualitative finding from S185 that survived the Rule-Zero strip (longest populated lead-time bucket collapses to a single `(entry_date, city, side)` correlated-blowup cluster — the WB S119 "correlated blowups" pattern) did not depend on 6O's bucket-level P&L; it was a cardinality observation on the underlying rows. That finding stands.
- Rewriting 6O's SQL to replicate `bot_pnl.py`'s join semantics is non-trivial. WB's multi-entry-same-side pattern is handled by `bot_pnl.py` via DISTINCT-ON-entry with specific ordering; replicating it verbatim means duplicating `bot_pnl.py`'s logic. Per Protocol 6, improvising a parallel query is forbidden — the only legitimate rewrite path is to call `bot_pnl.py`'s resolution logic directly, which is a larger refactor than the script's analytical value justifies.
- The 6O multiplier-retune proposal that was gated on validation passing is blocked regardless of whether the script gets rewritten. No deadline on the lead-time retune; WB's existing lead-time multipliers (S154: 72-120h=1.15x, 24-48h=0.70x) have held through the intervening window.

**What lives on:** the qualitative single-event-dominance finding and the §Protocol candidates entry for the aggregate-statistics bucket-concentration check (the candidate that 6O's near-miss seeded).

**What does not:** the `scripts/wb_lead_time_backtest.py` script as an ongoing analytical tool. It remains in the working tree with its Rule-Zero header warning for reference value (documenting the SQL shape that produces the over-count bug, for future agents who might be tempted to write similar multi-entry joins), but it is not to be run for bucket-level P&L. A follow-up session may delete it entirely; until then, the header warning is the canonical reading of its status.

**Evidence of origin.** S186 session (2026-04-20) ran both scripts on VPS; totals do not match. Full outputs available in the S186 session SSH log. Validation workflow codified in the S185 handoff §4 step 1 produced the quarantine decision as designed — the prior session's gating clause "If validation fails, defer indefinitely" resolved cleanly without further escalation.

### S186 (2026-04-20) — PSM port shape correction: S164 pattern inheritance without structural-isomorphism check

**Claim (S185 Corrections Log §S185):** The remaining P0 set's `POSITION_SIZE_MISMATCH` disposition recommended "port the S164 `GROUP BY` fix (drop `side` from the join), matching `size_invariant_check.py:28-42` rationale." Framed as a straightforward pattern reuse.

**Reality (verified against live VPS data, S186):** A naive mirror-port — dropping `side` from `te_net` GROUP BY and from the positions JOIN — produces the OPPOSITE of the intended effect. Instead of reducing false positives, it shifts them from one class (legacy-SELL cases, shrinks) to another (dual-side-open markets, grows). Net direction upward, not downward. Counts elided per Protocol 6; the mechanism is the argument, not the magnitude.

**Mechanism.** `size_invariant_check` and `position_trade_events_check` are NOT structurally isomorphic, despite their surface similarity:

- **`size_invariant`** asserts a per-market sum invariant (`entry_total >= exit_total + resolution_total` within tolerance). Aggregating across sides is semantically valid — the invariant holds at the market level regardless of side attribution.
- **PSM** asserts a per-side invariant (`positions(bot, mkt, side).size == te_net(bot, mkt, side)`). Per-side attribution is required by the check's semantics. Dropping `side` from aggregation produces a side-agnostic `te_net` that, when joined to per-side positions rows on dual-side-open markets, flags each side's position as mismatched (because `te_net.net = entry(YES) + entry(NO)` doesn't equal either side's position alone).

A dual-side-open market is one where `positions(bot, mkt)` has rows with `size > 0` on BOTH sides simultaneously. Rare but not nonexistent: MB has opposing-side blocks in normal operation, but legacy data and edge cases produce such markets; any naive side-drop port produces a pair of false positives per such market.

**Correction shipped** (commit `e19815e`, this session): the S186 port mirrors the S164 side-drop to absorb the legacy EXIT(SELL) asymmetry, AND adds a `NOT EXISTS` guard against positions siblings with opposite side and `size > 0`. The guard excludes dual-side-open markets from PSM. They are routed to a separate new diagnostic (below).

**Contract test** (`tests/unit/test_position_trade_events_check.py`) pins both the side-drop (catches pre-S186 regression) and the NOT EXISTS guard (catches naive-port regression). Future agents who read "port the S164 fix to this check" will fail the guard assertion before shipping.

**Why logged here.** This is a Protocol 4b documented instance — pattern inheritance without structural-fit verification produces wrong fixes. S185's handoff applied a plausible-sounding pattern without verifying the two checks' invariants matched. The verification was possible (and was what S186 did) only because the verification step was the session's work; the prior session's pattern-based framing would have cascaded through to a deploy had this session shipped the naive port without live-data measurement.

**Companion Protocol 4b evidence.** Three consecutive sessions have caught prior-handoff pattern-based instructions that were structurally wrong: S183 (`yes_price`/`yes_token_id` schema-shape confusion), S185 (P0 FIX_ROOT misclassification vs FIX_AUDIT_CHECK), S186 (this finding — S164 pattern inheritance). Common mechanism: prior session applied a plausible-sounding pattern without verifying structural fit; current session verified and caught the error. The pattern being reused is never examined for isomorphism with the target structure. This strengthens Protocol 4b's grounding and gives future sessions concrete precedent to check against when tempted to apply "the S164 fix" or similar pattern-based instructions.

**Execution consequence.** Step 3a was landed as the guarded port, not the naive port. Step 3b (bulk ACK) still requires explicit authorization and the one-day post-deploy verification window; the window evaluates whether the NEXT daily audit run (against the guarded query) re-emits the historical OPEN set. If it does not, bulk ACK becomes safe to execute. The guarded query is expected to emit fewer rows than the pre-S186 query (legacy-SELL class eliminated; dual-side class routed elsewhere) and therefore the verification-window observation should be a material reduction in new OPEN rows emitted per daily run — but the specific counts will be presented qualitatively or cited from a canonical source per Protocol 6.

### S186 new P0 follow-up — DUAL_SIDE_CONCURRENT diagnostic (filed, not yet shipped)

**Scope.** A new audit check, separate from PSM, that flags any `(bot_name, market_id)` pair where `positions(bot, mkt)` has size > 0 on BOTH YES and NO simultaneously.

**Proposed recon_type.** `DUAL_SIDE_CONCURRENT`. Severity TBD — likely `HIGH` (not CRITICAL, since MB can legitimately hold both sides in arbitrage-class scenarios), but the operator can whitelist specific bot/market patterns if the false-positive volume is high.

**Semantics** (distinct from PSM). PSM asks "does this position's size agree with the trade_events net for its side?" DUAL_SIDE_CONCURRENT asks "is holding both sides of this market concurrently legitimate for this bot's strategy?" The two questions have different answers and different fixes. PSM routes dual-side markets to this check via the NOT EXISTS guard; this check is where the decision about dual-side legitimacy lives.

**Proposed query** (simple):
```sql
SELECT source_bot, market_id,
       COUNT(DISTINCT side) AS distinct_sides,
       STRING_AGG(side || '=' || CAST(size AS TEXT), ', ' ORDER BY side) AS side_sizes
FROM positions
WHERE CAST(size AS DOUBLE PRECISION) > 0
GROUP BY source_bot, market_id
HAVING COUNT(DISTINCT side) > 1
LIMIT 200
```

**Implementation estimate.** ~50-80 lines (new `dual_side_concurrent_check.py` + factory registration + contract test). Comparable in scope to `TradedMarketsStatusDriftCheck` (S184 shipped ~87 lines for a new check). One-commit shippable.

**Semantic decision required BEFORE implementation.** The SQL is trivial; the semantics are the hard part. Before a future session writes this check, the operator must answer: *"what does a flag from this check mean, per bot, and what is the operator action when it fires?"* The answer varies by bot and may require a time-window component:

- **MirrorBot:** may have legitimate arbitrage scenarios where dual-side concurrent holdings are intentional. If so, MB must be exempt, or the check must have per-bot thresholds, or the flag must be advisory not CRITICAL for MB.
- **WeatherBot:** should probably never hold both sides concurrently. Dual-side on WB is likely a bug (stale position row not closed on resolution, or a race in the position writer). Flag as CRITICAL.
- **EsportsBot:** unknown — depends on strategy. Operator to decide.
- **Transition-window exemption:** all bots may legitimately be dual-side for brief moments (mid-exit, order-gateway race). Check may need a `dual_side_open_for > N minutes` predicate to avoid alerting on transient states.

Do NOT write the SQL before these decisions are made. A check without a well-defined operator action is an observability-noise generator, not a diagnostic. This is the check-effectiveness question the Protocol 6 carveout candidate eventually frames.

**Sequencing.** Separate session or separate commit within this session. NOT bundled with Step 3a (commit `e19815e`) because:
- A new recon_type is a semantic addition, not a port.
- The SQL structure is different (GROUP BY HAVING, not JOIN-based).
- Bulk ACK of the pre-existing dual-side markets (now routed to this check) is a separate authorization decision from the PSM bulk ACK.
- Clean commit boundary for future revert: if the new check introduces issues, it reverts independently of the PSM port.

**Not urgent to ship before Step 3b.** PSM's NOT EXISTS guard means dual-side markets disappear from PSM immediately on deploy of Step 3a (`e19815e`). They become invisible to the audit until DUAL_SIDE_CONCURRENT ships. That's an observability gap for the duration, but the gap pre-existed (pre-S186 PSM flagged them under a misleading recon_type; post-S186 they're silent). Ship DUAL_SIDE_CONCURRENT at the next convenient session — priority P0 for closing the observability gap, but not schedule-gating.

**Evidence of origin.** S186 session (2026-04-20) live-data spot-check of the guarded port excluded dual-side market `0xad437cf21f437aab742569757d0761e24bdb0d9b632780b63dafbf5555a3f43e` (WB positions showed both NO and SELL with size > 0). The exclusion is correct behavior for PSM but leaves the dual-side anomaly unflagged until a dedicated diagnostic ships. Filed as P0 not P1 because audit-observability gaps are category-P0 per the session template.

### S190 Hygiene Backlog — Audit-framework findings folded in (S192)

Promoted from handoff carry per S191 §4.6 task. Two MEDIUM-severity audit-framework items originally surfaced in S190 §2.7 were previously living only in handoff carried-backlog; this section fixes the plan-drift.

**1. Phantom-position variant unprotected by S186 sibling guard.** PSM check at [base_engine/audit/checks/position_trade_events_check.py:108-134](base_engine/audit/checks/position_trade_events_check.py:108) has a SECOND query body (phantom positions: `size > 0` but no ENTRY in `trade_events`) that is structurally separate from the main `te_net` vs `positions.size` invariant. The S186 NOT EXISTS sibling guard (commit `e19815e`) was added to the main query only. The phantom query emits independently and uses its own WHERE clause with no dual-side exemption.

**Live evidence (S192 verification, audit run 1182 fired 2026-04-23 03:03 UTC):** 12 rows emitted with `details->>'reason' = 'phantom_position_no_entry_event'` under `recon_type = POSITION_SIZE_MISMATCH`, all `bot_name = WeatherBot`. Cross-run trend across `scheduled_daily` runs 980/1082/1182: 11/11/12 (stable ±1). Post-S186-deploy confirms phantom variant continues to emit; the sibling guard does NOT cover this shape by design.

**Fix options (prefer A):**
- **A. Treat phantom variant as a distinct diagnostic.** Split emission to its own recon_type (e.g., `PHANTOM_POSITION`) so severity, dispositions, and ACK lifecycle are independent of PSM. The phantom shape is not a side-aggregation bug; it is a position-existed-without-a-create-event bug. Wrong recon_type classification blocks clean ACK sweeps and makes bulk-ACK scope decisions harder.
- **B. Add a phantom-aware sibling guard.** If the 12 WB rows are historical-frozen (trade_events gap in a specific date window), add event-age filtering to the phantom query matching the S185 auto-close framework. Does NOT fix the classification issue in (A).
- **C. Investigate whether WB's current position-creation path can produce phantom rows (real bug) vs. whether all 12 rows are pre-S163 legacy (frozen).** Query: `SELECT source_bot, market_id, side, last_seen_at FROM positions WHERE ...` intersect with trade_events existence check; group by month of position's `created_at` or equivalent. Determines whether this is FIX_ROOT (WB bug) or FIX_AUDIT_CHECK (historical-frozen, same category as S185 P0 triage).

Severity: MEDIUM — audit-framework classification question, not a live-money risk. No timeline pressure. Ties into S185 auto-close discipline and PSM bulk-ACK scope.

**2. CSV `bot_names` mishandling in `TradedMarketsCheck` — FIXED S192 (commit `edcf93e`, NOT YET DEPLOYED).**

Original framing (pre-fix): `",".join(bot_names)` at [base_engine/audit/checks/traded_markets_check.py:46](base_engine/audit/checks/traded_markets_check.py:46) iterated the TEXT column's characters. Live evidence at discovery: 1,600 of 3,186 OPEN `TRADED_MARKETS_DRIFT` rows (50.2%) had `bot_name` values like `"M,i,r,r,o,r,B,o,t"`, breaking downstream equality/IN filters.

Expanded scope discovered during S192 fix: the same CSV-TEXT misunderstanding produced TWO additional SQL bugs in the check. Both queries used string equality `te.bot_name = tm.bot_names` where `tm.bot_names` is CSV (single-bot rows matched fine; multi-bot rows never matched any single `trade_events.bot_name` value). This falsely reported every multi-bot `traded_markets` row as stale, and inversely falsely reported every multi-bot market's ENTRYs as missing.

**S192 fix (`edcf93e`):**
- SQL membership at L36 + L63: `te.bot_name = ANY(string_to_array(tm.bot_names, ','))`
- Emission at L50: `str(bot_names).split(",")[0]` — matches sibling pattern at [base_engine/audit/checks/traded_markets_status_drift_check.py:65](base_engine/audit/checks/traded_markets_status_drift_check.py:65)
- +4 contract tests in [tests/unit/test_traded_markets_check.py](tests/unit/test_traded_markets_check.py) pin both paths

**VPS pre-deploy verification** (fixed SQL run against live `polymarket` DB):
- Stale-row count: 919 → 707 (delta -212, exactly equal to multi-bot row count in `traded_markets`)
- Missing-row count: 556 → 148 (delta -408, ~2 ENTRY-rows-per-multi-bot-market average)
- Stale/missing output sets now mutually exclusive (overlap=0)
- Concrete multi-bot trace: market `0xb0710a1f7b38f8411b36f31edf29…` with `bot_names="MirrorBot,EsportsBot"` has ENTRYs from both bots — OLD query falsely flagged stale, NEW query correctly excludes
- 0 char-iterated `bot_name` values survive `split_part` post-fix

**Post-deploy verification gate:** at next `scheduled_daily` audit run (2026-04-24 03:03 UTC), expect `TRADED_MARKETS_DRIFT` emission per-day to drop from flat 200/day (100 stale + 100 missing at LIMIT caps) toward a lower number as the fixed queries emit fewer false positives. Pre-fix OPEN rows (3,186) remain OPEN per S185 auto-close gap — they'll need bulk-ACK under separate authorization; this is not a defect of the fix.

**Item 1 (phantom-position variant) remains open — Carry as Phase 4 backlog.** Item 2 shipped as surgical fix; the plan-drift it represented is closed.

### Phase 5 Sentinel — pre-deploy prerequisites

The Phase 5 silent-failure sentinel (`scripts/silent_failure_sentinel.py`, not yet shipped) was planned to deploy after Phase 2 stable for 24h. S183 surfaced a two-part prerequisite. **The actual scheduling gate is Prereq 1** — Prereq 2 is a cheap ~5-line patch that unblocks sentinel design, not a workload that competes for session time. Framing the two as symmetric "double-gate" would mislead planning.

**Prerequisite 1 (the real gate) — Audit triage complete.** Multi-session workload. The sentinel's check #4b alerts on persistent reconciliation_breaks findings. Running it against untriaged findings (9,900+ unique violations across 23 recon_types) would false-positive on every cycle. Triage output lives at `docs/audit_triage_3a.md` + `docs/audit_triage_3b.md` (working-tree — `AUDIT_*.md` glob in `.gitignore:150` catches them case-insensitively on Windows). P0 blockers identified: POSITION, STALE_POSITION, SIZE_INVARIANT, FK_MISSING_MARKET, POSITION_SIZE_MISMATCH. Sentinel cannot ship until these P0s are either fixed or explicitly whitelisted in sentinel config.

**Prerequisite 2 (cheap unblock) — `triggered_by` labeling fix in `run_audit.py`.** The sentinel's check #4a was designed to alert if no `run_type='scheduled_daily'` heartbeat fired in 25 hours. S183 audit-runs query (Q3) showed only 2 `scheduled_daily` rows ever, with last_run 2026-04-04 — initially misread as "daily timer broken since Apr 4." Investigation via `systemctl status polymarket-audit.timer` revealed:
- Timer: **ACTIVE**, enabled, firing daily at 03:00 UTC. Verified run fired 2026-04-20 03:01:03 UTC.
- Service: **running correctly**. `run_id=879` completed at the observed 03:01:03 trigger.
- `run_audit.py:69-70` **hardcodes** `run_type="cli"` and `triggered_by="cli"` regardless of invocation context. The systemd-fired daily run records as `triggered_by='cli'`, indistinguishable in the data from a manual CLI invocation.

**What this means for sentinel #4a:**
- Current design (watch for `run_type='scheduled_daily'`) would false-positive indefinitely — the label it watches for doesn't exist in current data.
- Redesign to "any audit_runs row with `started_at > NOW() - 25h`" ignores run_type entirely but silently greens if only post_resolution runs fire (~54/day, always satisfying the 25h window even if the daily timer dies).
- **Correct fix:** add `--triggered-by <label>` CLI flag to `run_audit.py` with default `"unlabeled"` (NOT `"cli"` — a missing-label invocation must be distinguishable from an explicit CLI run). Have `polymarket-audit.service` pass `--triggered-by scheduled_daily`. Sentinel #4a can then watch for the explicit label reliably.

**Sequencing:** land the labeling fix BEFORE starting P0 audit work. Reason: the P0 fixes (e.g. deleting the legacy POSITION/STALE_POSITION emitter) will rerun audits; if the audit script still mislabels its own triggers, post-change verification runs are unreliable. Close the observability layer before using it to verify code changes.

---

### Silent-failure class — systemd components whose data-surface lies about their state

Three instances documented across S180-S183. All share a structural pattern: a systemd-managed component whose apparent operational state (from data/log/query observation) disagreed with its actual runtime behavior. But the **mechanism differs each time**, which is why the pattern is worth naming separately from any individual instance.

| # | Session | Component | Data-surface said | Actual state | Mechanism |
|---|---------|-----------|-------------------|--------------|-----------|
| 1 | S182 | `polymarket-audit.service` | "Failed" for 21+h (systemd unit status) | Script succeeding, findings reported | `SuccessExitStatus=1 2` missing; systemd default treated non-zero exit as failure while exit codes 1/2 were documented success signals |
| 2 | S182 | `EsportsMarketService` | "Running" for 16 days (service active) | Never instantiated inside EB v2 | Runtime-reachability gap — code existed, was never called |
| 3 | S183 | `polymarket-audit.timer` + `run_audit.py` | "Stopped firing since 2026-04-04" (0 `scheduled_daily` rows) | Firing daily at 03:00 UTC, all runs succeeding | Script hardcodes `triggered_by="cli"` regardless of invocation context; data column misreports run origin |

**Diagnostic discipline.** For any component with a systemd timer or service unit, before concluding the component is broken from a data-surface observation: run `systemctl status <unit>` and `systemctl list-timers <pattern>` against the actual unit and cross-check. If the systemd view disagrees with the data view, the data view is the suspect — inspect the data-emitting code path for a silent encoding bug.

**Promotion threshold.** Three instances is a cluster; four is a pattern. If a fourth instance of "systemd component's data surface disagrees with its actual behavior" surfaces in a future session, promote this to a numbered protocol (next available slot — Protocol 6 is now occupied by canonical-source discipline, so this would be Protocol 7 or later) with its own Mandate / Minimum Evidence / Out-of-scope / Evidence-of-origin structure. Until then, it lives here as a diagnostic heuristic, not a codified rule.

**Why not already a numbered protocol.** Each of the three mechanisms is already covered by existing protocols (4a runtime reachability for #2, 5 for #3's label drift; #1 is closer to a systemd-config hygiene issue). A fourth instance with a fourth mechanism — one the existing protocols don't naturally cover — is what would justify a new protocol rather than a cross-reference.

**Near-miss — S184 (2026-04-20), CHECK-constraint on `audit_runs.triggered_by`.** Commit `7b0b8ac` passed `--triggered-by scheduled_daily` into a column whose CHECK constraint did not allow that value; the violation would have fired on the next 03:00 UTC systemd-triggered daily audit. Caught pre-production by Protocol 5 schema-read discipline (`\d audit_runs` before trusting the commit's claim); fixed in `bf2828c` with a kind→source mapping. **Not promoted to instance #4.** The mechanism differs from the three documented instances, but the data surface did not lie — the CHECK would have blocked the write and raised loudly. Documented as near-miss to anchor Protocol 5's value without prematurely triggering the silent-failure class promotion.

---

## Phase 5v2-E — EB v2 scan-cycle cost reduction (deferred)

**Observation (S181 diagnostic, 2026-04-17):** EB v2 scan cycles consistently 17-29 seconds (vs MB/WB typically 1-5s). Sample log:
```
polymarket-esports[*]: Slow scan cycle bot_name=EsportsBotV2 scan_ms=28949.3
```

**Suspected root cause:** pipeline inference on 28K Trinity records per scan — XGBoost + Venn-ABERS + conformal run full inference per market, with no per-market caching. Each scan re-scores the entire market set from scratch.

**Candidate approaches (not ordered, requires design session):**
- **Per-market prediction cache** with invalidation on new trades or N-minute TTL. Probably the single highest-ROI change.
- **Batch inference** across all markets in one pipeline call rather than per-market loops.
- **Model simplification:** reduce Venn-ABERS base estimators from current ensemble count.
- **GPU move** if latency dominates and host has GPU available.

**Out of scope for S181.** This is architectural work requiring its own design + test + deploy cycle. Belongs in a dedicated session after Phase 5v2-A/B/C/D close out.

---

## S181 By-Design Acceptances

The S181 diagnostic surfaced 4 items that initially looked like bugs but are confirmed as designed behavior. Logged here so future agents do not re-investigate.

**1. Unpriced position warning log (`unpriced_positions`).** `base_engine/execution/position_manager.py:822` emits this when a token has no price after all fallbacks. Position manager already has 4 fallback tiers (L517-784), exponential backoff with Redis-backed blacklist (L828-849). The warning is the designed behavior — logging the persistent-unpriced state is the safety signal, not a bug.

**2. `mirror_market_data_retry_fail` log** at `bots/mirror_bot.py:2304`. Same subsystem as (1). Emitted when MB's 3-tier market data fallback exhausts without a price. Designed behavior.

**3. `market_prices_latest` 94.5% stale >1h.** `MARKET_PRICES_FALLBACK_ENABLED=false` is the intentional S150 decision — bots do not read from this table. `prune_market_prices.timer` handles cleanup. Zero read consumers; table is legacy.

**4. EnsembleBot RESOLUTION events.** EnsembleBot was deleted but had open positions at deletion time. These resolve naturally as markets close — the RESOLUTION events in `trade_events` are historical positions finalizing, not new trades. No cleanup needed; zombies exit on their own.

### S182 Phase 0.1 DENY + S181 Commit 3 regression (key-name contract mismatch)

**Context.** S182 Phase 1c Phase 0.1 (runtime-reachability, load-bearing) investigated whether `EsportsBotV2` consumes price data that `EsportsMarketService.refresh_market_prices()` writes. Verdict: **DENY.**

**Finding.** Scanner and caller disagree on the price-data key name. The scanner at [esports/markets/esports_market_scanner.py:149-157](esports/markets/esports_market_scanner.py) returns market dicts with key `"price"`; callers in EB v2 (`_find_polymarket_for_match` from S181 Commit 3 at `bots/esports_bot_v2.py:547`, and the pre-existing `_get_market_price` at line ~570) both read `m.get("yes_price")`. `m.get("yes_price")` returns `None` on the scanner's output, so EB v2 never finds a Polymarket market. EB's shadow-prediction rate of 1-2/hr is written with `market_price=None` (allowed path), which is why Commit 3's prediction_log write path (gated on market_id+market_price both non-None) never fires, and no trade executes.

**S181 Commit 3 regression attribution.** S181 Commit 3 introduced `_find_polymarket_for_match` which reads `m.get('yes_price')` from scanner output. The scanner emits the key as `'price'`. The pattern was copied from the pre-existing `_get_market_price` helper which had the same bug silently. Neither the copied code nor the new code was tested against the scanner's actual output contract. Protocol 4 (runtime-reachability, landed S182 Commit 10) codifies the verification that would have caught this; a forthcoming Protocol 4b (pattern-reuse bug inheritance) will codify the remaining gap when Phase 1d lands.

**Consequences for prior investigation narrative.**

1. The "markets table stale" finding (S182 Phase 0.2) is a **real but parallel bug, not the trading-output blocker.** If the markets table had been refreshing correctly all along, EB v2 would still have found zero markets because it wasn't reading the column the service writes.
2. **EsportsMarketService remains idle** and still needs instantiation — but that's a secondary investigation that gates nothing critical for EB v2 trading.
3. **S182 Phase 1b Commit 2 stays on master as deployed-but-dormant.** Code is correct; it will activate correctly whenever some path instantiates the service. Not broken; future-dated.
4. **Commit 9 cancelled** per S182 Phase 1c branching clause — instantiating the service wouldn't help EB v2 even if it worked perfectly, because EB v2's read path doesn't touch what the service writes.
5. **The 43+ hours of zero trading output** (S181 Issue 9) has the same root cause as the key-name mismatch, not the markets staleness. The same fix closes both.
6. **Phase 2 (gate-funnel observability) gate is now reframed.** "EB finds non-zero markets" precondition is satisfied by Phase 1d's key-name fix, not by Phase 1c's service instantiation.

**Disposition.** Phase 1c closed. Phase 1d filed as a separate plan (`C:\Users\samwa\.claude\plans\s182-phase-1d.md`) with tight scope: (a) contract-audit all scanner-output consumers, (b) decide fix location (scanner-side alias vs EB-v2-side read-key change vs both), (c) ship fix with a scanner-contract regression test, (d) land Protocol 4b in the same session. Phase 1d opens within 24 hours.

**Pattern to note for future investigators on this subsystem.** The bug was at a shallower layer than each successive hypothesis. Phase 0.2 blamed filter scope (wrong). Phase 0.2-b blamed silent crash (wrong). Phase 1c's runtime-reachability check finally surfaced the correct layer. Any future "service is running but not producing X" investigation on EB v2 should start from runtime reachability AND contract-verify the output schema of any intermediate component (like the scanner) before accepting that consumers receive what the producers emit.

---

### S181 Issue 9 escalation — paper_trades zero-volume for 24h+ (ESCALATED)

**Observed 2026-04-19 at T+~43h post-S181 deploy:**
- `paper_trades` table: **0 rows across all 3 bots in the last 24h**
- `prediction_log` in same 2h window: **MirrorBot=4,233, WeatherBot=31, EsportsBotV2=0** (EB cascade from markets-refresh-broken per S182 0.2-b)
- The 4,233→0 MB funnel is ~100% gate-blocked

**Per S181 plan's escalation rule** (if 0 at T+4h, file entry; we're at T+43h).

**Suspected cause:** gate filtering. Not cold-start lag — the pattern has persisted for 43+ hours.

**First-step investigation command for next agent:**
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "cd /opt/polymarket-ai-v2 && PYTHONPATH=/opt/polymarket-ai-v2 \
   sudo -u polymarket /opt/pa2-shared/venv/bin/python \
   scripts/gate_score_expectancy.py --json | head -40"
```
This reports per-bucket trade-vs-blocked counts — reveals whether signals exist but are gate-filtered (vs zero signals generated).

**Secondary grep:**
```bash
sudo journalctl -u polymarket-mirror --since "2 hours ago" | \
  grep -iE "gate_score|edge_below|conf_below|gate_rej" | tail -50
```

**Natural resolution path:** ~~S182 Phase 2 Commit 5 (gate-funnel structured logging on all 3 bots) will make the gate breakdown self-documenting once deployed.~~ **Corrected S194 (2026-04-24, F2):** the gate-funnel structured logging claimed here was **never shipped under any matching name** — grep `gate_funnel`/`gate_breakdown`/`funnel_log`/`funnel_stage` across `bots/*.py` and `base_engine/risk/*.py` returned zero matches at S194 verification. The MB and WB silence are **separate root causes** from EB v2's key-name mismatch (which WAS correctly fixed in S182 Phase 1d per the section above) and were not addressed by S182. Investigation routed to `S194_TRADING_REVIVAL_PLAN.md`:

- WB silence: `daily_counters` over-decrement to negative (commit `b439f3f`, S194 B-NEW-1) + startup-hold flag wiring missing across all 3 bots (commit `f928695`, S194 B-NEW-2) + misleading `exposure_cap` label (commit `7b5e535`, S194 B-NEW-3)
- MB silence: `MIRROR_REGIME_START` str-vs-datetime asyncpg DataError silently failing `EliteReliabilityTracker.refresh()` and `EliteWatchlist` copy-tier scoring for 25 days starting 2026-03-30, leaving the reliability cache empty → trader `_eq_n=0` → gate score capped → MB stopped trading 2026-04-13 (commits `8f26a38` + `35eed49`, S194 D4-NEW)

**Services/infrastructure otherwise healthy** — this is a pure gate-behavior issue, not an infra issue. All bots active, prediction engines running, DB healthy.

---

### S182 Phase 1d (2026-04-19) — Scanner projection lossiness + classification lesson

**Context.** Phase 1d was opened to fix the key-name contract mismatch between `esports/markets/esports_market_scanner.py:149-157` and downstream EB v2 readers. Handoff-entry re-verification surfaced a third read site the prior session missed (`bots/esports_bot_v2.py:604` reading `yes_token_id`/`no_token_id` against scanner's singular `token_id` emission), prompting a full Phase 0 contract-audit. The audit enumerated 6 consumer read sites across 3 files and initially classified them into "rename class" (3) and "schema-shape class" (3), proposing a Phase 1d/1e split. Mid-audit a one-layer-deeper check on the scanner's input source reframed the classification.

**Finding.** `EsportsMarketService.get_tradeable_esports_markets` at `esports/markets/esports_market_service.py:222-255` returns market dicts that already contain `yes_token_id`, `no_token_id`, `yes_price`, `no_price`, `id`, `condition_id` as top-level keys. The scanner's output projection at lines 149-157 / 221-228 emitted only `market_id`, `token_id`, `price`, and sibling fields — silently dropping the paired-token keys. All 6 consumer sites resolve by adding passthrough of those keys in the scanner's projection. No architectural fix, no Phase 1e.

**Contract-alignment table (6 sites, A4 passthrough fix):**

| # | Read site | Key read | Pre-A4 | Post-A4 |
|---|---|---|---|---|
| 1 | `bots/esports_bot_v2.py:547` `_find_polymarket_for_match` | `yes_price` | None | actual YES price |
| 2 | `bots/esports_bot_v2.py:570` `_get_market_price` | `yes_price` | None | actual YES price |
| 3 | `bots/esports_bot_v2.py:604` `_find_market_info` | `yes_token_id`, `no_token_id` | (None, None) | both populated |
| 4 | `bots/esports_bot_v2.py:503` `_execute_trades` (downstream of #3) | `yes_token_id`, `no_token_id` | dead (filter blocks) | populated |
| 5 | `bots/esports_bot_v2.py:509` `_execute_trades` (downstream of #3) | `id`, `condition_id` | dead (filter blocks) | populated |
| 6 | `bots/esports_bot.py:7156` `_generate_series_opportunities` NO branch | `no_token_id` | None (silent fallback to YES token) | populated |

**Live-correctness verification before fix (trade_events 30-day window, EsportsBot ENTRY):** 577 of 578 trades correctly aligned (344 OK_NO + 233 OK_YES); 1 anomalous trade (0.17%) detailed below. Site #6's code path produced **zero trades in 30 days** — `event_data.type` was NULL on every EsportsBot trade, and the series path sets `type='esports_series'`. Site #6's latent bug is inert in production output.

**Classification lessons.**

1. **"Schema-shape" vs "rename" is a function of where you look.** Phase 0.1 classified sites #3/#4/#6 as schema-shape because the scanner emits one token per market. The actual source (market_service) exposes paired tokens; the scanner was stripping them. The schema-shape label would have triggered a Phase 1e architectural investigation that dissolves once you look one layer upstream.

2. **Phase 0 investigations that stop at "emits X / reads Y" can misclassify the bug.** Ask "what does the emitter's input already contain?" as a first-class Phase 0 step, not a second-pass clarification. Added as Protocol 4c (projection lossiness).

**Flagged for Phase 4 backlog (not this session).**

1. **`find_all_esports_markets` at `esports/markets/esports_market_scanner.py:162` is dead code.** Zero callers in repo (grep .py exhaustive across `C:\lockes-picks\polymarket-ai-v2`) and zero callers on VPS (`/opt/polymarket-ai-v2`, `/home/ubuntu` grep). Candidate for deletion, but "don't delete code you don't understand" (CLAUDE.md Rule 5) — evaluate in a follow-up with git-blame context on original intent.
2. **One anomalous wrong-side trade preserved for investigation.** Timestamp `2026-04-03 17:26:27.420037`, market_id `0xb33827cbb7200ab893eca5b4083099b84a0725f1982027adf060b98ab2277b2b` (question: "Valorant: Team Heretics vs Natus Vincere - Map 2 Winner"), bot `EsportsBot`, side `NO`, token_id `55163220198676417751769539836973817727101168436989498813537578705796079734788` (the market's YES token), size $399.34, price $0.01. Not from site #6 series path (`event_data.type IS NULL`). Possibly related to scanner's `_classify_market_type` iteration order matching "winner" before "map" for map-specific markets. Dedicated investigation window to trace the producing code path.

**Disposition.** A4 passthrough fix landed in Commit 1d-1 alongside `tests/unit/test_scanner_contract.py`. Protocol 4b (pattern inheritance) and Protocol 4c (projection lossiness) landed in Commit 1d-2. Phase 1e cancelled — no architectural gap. Phase 2 (gate-funnel observability) unblocked pending T+2h success gate (shadow prediction rate rises above 1-2/hr baseline).

---

### S186b (2026-04-21) — Full plan-vs-reality reconciliation + Phase 3 origin trail

**Context.** User-requested 100% item-by-item verification of the plan against (a) `git log master` (698 commits), (b) local file existence, (c) VPS runtime state via SSH. Full per-item table archived at `docs/S186b_plan_reconciliation.md` (~140 items covered). This entry records the durable corrections and classifications; the table itself is the reference artifact.

**Headline correction (Protocol 4c-shaped, both catches).** Two initial "missing" findings were verifier errors, not plan drift:
- **D1 PG OOMScoreAdjust=-900** — applied via stock Ubuntu `/lib/systemd/system/postgresql@.service` template (instance unit), NOT the `postgresql.service` wrapper my `systemctl show postgresql -p OOMScoreAdjust` query targeted. Wrapper returns 0; instance units inherit -900 from the template. Running postmaster confirmed at -900; backends reset to 0 post-fork by design (preferred OOM victim).
- **D3 RESOLUTION+EXIT partial unique indexes** — applied as per-partition indexes (`idx_trade_events_<YYYY_MM>_exit_dedup` / `_resolution_dedup` across 12 month partitions + default), NOT at parent-table level my `pg_indexes WHERE tablename='trade_events'` query targeted. Partitioned tables enforce uniqueness via per-partition indexes by PG design; the parent view hides them. Origin: commit `8f0c69f` "S159 C15+C18 — partition-safe ENTRY/EXIT dedup."

Mechanism common to both: a default query interface by design surfaces only the less-informative layer of a hierarchical structure. Filed as Protocol candidate "Hierarchical infrastructure verification" in §Protocol candidates below. Two catches in one investigation qualifies for candidate filing; third real-world instance promotes to numbered protocol.

**P3-1 effective_cache_size + shared_buffers origin trail (closes "undocumented intentional change" category):**
- Plan target: `effective_cache_size=12GB`, `shared_buffers=4GB` (from 16GB VPS era).
- VPS state: `effective_cache_size=24GB`, `shared_buffers=4GB`, both from `/var/lib/postgresql/16/main/postgresql.auto.conf:4-5`.
- `pg_settings.source='configuration file'` confirms `ALTER SYSTEM SET` writes, not runtime defaults.
- Origin: WeatherBot S152 "PG tuning applied" per `memory/MEMORY.md` entry, timed with commit `8d7b5e1` (VPS migration Ubuntu-3 16GB → Ubuntu-32 32GB).
- NOT git-tracked because `ALTER SYSTEM` writes to `postgresql.auto.conf` in the data directory, outside the deploy pipeline.
- Disposition: VPS values correct for 32GB instance. Plan text at Phase 3 updated. No reapply needed.

**P3-2 PgBouncer `idle_transaction_timeout` — NOT APPLIED.** `/etc/pgbouncer/pgbouncer.ini` has `server_idle_timeout=600` only. Plan phrasing "PgBouncer idle_txn timeout" was ambiguous between `idle_transaction_timeout` (client-side idle-in-txn kill) and `server_idle_timeout` (pool-side idle-connection close) — two distinct PgBouncer params. Plan text clarified at Phase 3 to name `idle_transaction_timeout` explicitly. Hygiene backlog.

**P3-3 sshd hardening — PARTIAL.** `PermitRootLogin no` + `PasswordAuthentication no` applied. `MaxAuthTries=3` + `AllowUsers ubuntu` absent from `/etc/ssh/sshd_config` and `sshd_config.d/*.conf`. Hygiene backlog — ~5-line sshd_config addition.

**P3-4 SSH port change — NOT APPLIED.** Port 22 (default) per `ss -tlnp`. sshd_config has no `Port` directive. Fail2ban (D4 ✅) provides partial mitigation. Security-hardening backlog (threat-model decision).

**P3-5 autovacuum_naptime=15 — NOT APPLIED.** `pg_settings.source='default'` (60s default). `setup-vps.sh` does not touch it; no git history. Hygiene backlog — bundle with next Postgres-touching deploy.

**2F file-path drift — plan text reconciled.** Plan Phase 2 table row 16 referenced `health_check.sh` ("EXISTS — enhance"). Actual files: `deploy/dead_man_watchdog.sh` (kill-switch writer — sets `system_config.kill_switch='true'` via SQL) + `deploy/healthcheck_probe.sh` (S180 tiered probe, replaced the "6-layer script" framing). Plan row updated.

**Meta-finding 1 — Protocol 5 symmetry for next plan-hygiene round.** Protocol 5 as written covers over-optimistic status claims (claim "done" when broken). S186b surfaced the symmetric case: over-pessimistic verifier claims (claim "missing" when applied at alternative substrate). Both failure directions produce wasted work — the over-optimistic case produces silent operational bugs; the over-pessimistic case produces unnecessary reapplication and plan edits. Protocol 5's mandate should apply symmetrically. Filed for next plan-hygiene round alongside: (a) Protocol 6 carveout for check-effectiveness measurements, (b) MEMORY.md growth budget (per S186 §6), (c) Protocol 4b adherence-vs-awareness refinement (per S186 §6).

**Meta-finding 2 — infra-verification failure cluster.** All four remaining real discrepancies (P3-2, P3-3, P3-4, P3-5) share the same root: config/infra items where "did the commit land" diverges from "is it actually in effect on VPS." Code changes deploy uniformly; config changes require drop-in files, migration runs, or `ALTER SYSTEM` / sshd_config edits to take effect. The session that marked Day 1 COMPLETE verified commits landed; no session verified the config actually took effect on VPS end-to-end. This is the over-optimistic half of the same substrate-verification gap the Protocol 4c-shaped hierarchical-infra candidate addresses on the over-pessimistic half. Worth remembering that "Phase N COMPLETE" claims for any phase that modifies systemd drop-ins, migrations, or `postgresql.conf` must be verified on VPS post-deploy, not just merged on master.

**Output-ratio tracking note.** Third consecutive session (S185, S186, S186b) where highest-value outputs are structural findings and plan corrections rather than code shipped. Not an action item — a pattern-tracking observation. If ratio persists another 3-5 sessions, characteristic of project state (phases converging, edge cases surfacing, meta-layer leverage) not coincidence.

**Evidence of origin.** `git log --all --oneline -S "effective_cache_size"` (empty — confirms no tracked commit); `SELECT name, sourcefile, sourceline, source FROM pg_settings WHERE name IN (...)` (4 rows, three `configuration file` sources + one `default`); `cat /lib/systemd/system/postgresql@.service | grep OOMScore` (`-900`); `SELECT tablename, indexname FROM pg_indexes WHERE indexdef ~* 'unique' AND tablename LIKE 'trade_events%'` (30 rows including per-partition dedup indexes); `sudo grep -iE '^(MaxAuthTries|AllowUsers)' /etc/ssh/sshd_config` (empty); `ss -tlnp | grep sshd` (Port 22 only).

### S187 (2026-04-21) — Plan-hygiene closure batch: 2E shipped, 4D/4E unshipped with drift corrections

**Context.** Soak-wait session between `e19815e` deploy (2026-04-21 as release `20260421_114928`) and the one-day PSM verification at 2026-04-22 03:03 UTC scheduled_daily audit fire. Used to close three unverified Phase 2/4 items flagged in prior sessions. Phase 0 Protocol 5 verification of `AGENT_HANDOFF_S186_CLOSE.md` passed with one minor line-reference drift (Protocol 6 cited at plan L1081, actual L1185 after +104 lines of plan growth — non-blocking).

**Finding 1 — 2E RTDS seen_set dedup: ✅ SHIPPED at wrong-substrate location.**
- **Plan claim (Phase 2 table row 15):** `2E: RTDS seen_set dedup | mirror_bot.py`.
- **Reality:** Dedup is shipped at `bots/elite_watchlist.py:984-988`, not `mirror_bot.py`. Mechanism: `self._seen_tx: OrderedDict` at L66 (bounded by `_MAX_SEEN_TX = 50_000` at L31), checked against `transactionHash` or composite key at L982-988, LRU-pruned via `popitem(last=False)`.
- **Origin commit:** `bf23c25 feat(mirror): RTDS global trade feed + EliteWatchlist copy trading`. Single-commit introduction of RTDS WebSocket + dedup.
- **Protocol 5a sub-case.** The plan's file-column claim was verified against the wrong substrate (`mirror_bot.py`, which holds a separate post-mirror dedup dict `mirrored_trades` used downstream of the strategy layer — not the RTDS ingress dedup). This is substrate-level file-column drift inside a phase table — sibling of §S186b's Protocol 4c findings but at the plan-table level, not the VPS config level.
- **7B Phase A scope decision.** RTDS `_seen_tx` dedup fires at `EliteWatchlist` ingress, BEFORE `mirror_bot._execute_mirror_trade()`. A duplicate `transactionHash` is a transport artifact, not a strategy-layer rejection — including it in `mirror_rejected_signals` would pollute counterfactual analysis (wallets monitored by more RTDS subscriptions would appear to have more "rejections" independent of signal quality, polluting 7B Phase B wallet rankings). Decision: keep 7B strictly `mirror_bot.py`-scoped; exclude `EliteWatchlist`. RTDS dedup hit-rate observability filed as §S187 Hygiene Backlog (single structured log line, not a rejection-table row).

**Finding 2 — 4D S170 test files: ❌ NOT SHIPPED (three-way drift).**
- **Plan claim (Phase 4 list):** "Commit S170 test files (71 tests) — verify they pass in CI first."
- **Reality — three distinct drift points:**
  1. **No commit exists.** `git log --all --oneline | grep -iE "S170|71.?test"` → zero matches. `git log --all --grep="S170"` → only hash-substring false positives (`901ea3f`, `f35695f`, `1a120a7` — all from unrelated sessions).
  2. **"71 tests" conflates active and quarantined.** 49 active tests in 3 `.py` files (`test_favorite_longshot_calibrator.py`=11, `test_horizon_bias_calibrator.py`=19, `test_mirror_calibration.py`=19) + 22 quarantined tests in 1 `.py.disabled` file (`test_esports_calibrators.py.disabled`). pytest collects the 49, skips the 22 via filename convention.
  3. **49 already running in preflight despite untracked.** Today's deploy counted 2061 passed, up from S170's baseline 1878. The 49 untracked tests are providing coverage now, just not durable in version control — one `git clean -fd` from any agent closes 71 tests' worth of work.
- **Pre-commit Protocol 4b verification.** Read all 3 active test files; confirmed imports from `base_engine/features/calibration.py` and `bots/mirror_calibration.py`, pytest+MagicMock patterns, no production-code mutations. Shipped as separate test commit this session.
- **22 `.disabled` retained untouched.** Filename-suffix disabling is unusual (normal pytest practice is `@pytest.mark.skip` or conftest exclusion). Quarantine reason unknown — category-determination investigation filed as §S187 Hygiene Backlog.
- **Meta-finding.** Four test files surviving across ~10 sessions by repeated care not to `git clean` them is a fragile preservation mechanism. Worth auditing the working tree for other "survive by careful hands" files — filed as §S187 Hygiene Backlog.

**Finding 3 — 4E trade_journal.py nested session fix: ❌ NOT APPLIED (operationally dead).**
- **Plan claim (Phase 4 list):** "trade_journal.py nested session fix."
- **Reality:** File retains the exact nested-session pattern the claim flagged. `base_engine/analysis/trade_journal.py:129` opens `async with self.db.get_session() as session:` (outer) and line 145 inside the loop calls `self.generate_journal_entry(trade.id)`, which opens a NEW `async with self.db.get_session() as session:` at L35 (inner). Classic pool-exhaustion shape under load.
- **BUT the code path is unreachable.** `TradeJournal` is instantiated at `base_engine/base_engine.py:757` (`self.trade_journal = TradeJournal(db=self.db)`), but NO caller of `generate_journal_entry` or `generate_period_journal` exists in the repo. Repo-wide grep returned only the constructor call and the inner self-invocation at L145. Operational risk is nil — the bug cannot fire while the class has zero consumers.
- **Orphan-feature pattern.** Feature wired at base_engine startup with no consumer side. Three plausible histories: (a) consumer planned but never built, (b) feature superseded by `bot_pnl.py` or similar, (c) experimental ship that didn't materialize. Indistinguishable without git-archaeology on the original intent.
- **Disposition chosen (Option i).** Close ❌, file fix as hygiene backlog. Deletion considered (Option ii) but rejected — the class may be a future-consumer contract, and "unreachable bug in preserved feature" is lower-risk than "deleted a feature someone was planning to wire up." The orphan-feature detection mechanism itself is filed as a sibling hygiene backlog item.
- **Structural sibling of S182's `EsportsMarketService`.** S182 Phase 1d caught an inverse orphan: service instantiated elsewhere but not inside `EB v2`; code path called but never instantiated. `TradeJournal` is the symmetric case: instantiated but never called. Both patterns deserve the same class of detection.

**Meta-finding — three consecutive plan-claim drift findings.** 2E (wrong file column), 4D (three-way drift), 4E (bug-claim + orphan mismatch). Three unverified-item closures → three drift findings — roughly 1:1 ratio. Pattern is stable enough to document: future sessions encountering Phase 2/4 unverified items should expect drift proportional to closure count, and closing them has consistent information value. Plan's Phase 2/4 tables appear to have never had a systematic claim-by-claim reconciliation pass — §S186b ran the equivalent against Phase 3 config space (6 discrepancies caught). Recommendation for a Phase 2/4 full reconciliation filed as §S187 Hygiene Backlog.

**Evidence of origin.** This session, 2026-04-21. Phase 0 Protocol 5 verification of S186 CLOSE handoff (19 tool calls, each claim matched to evidence); `e19815e` deployed cleanly as release `20260421_114928` with HEALTH_WARN soft-warn per documented S180 EB v2 cold-start pattern; VPS symlink + PSM `GROUP BY bot_name, market_id` + `NOT EXISTS` guard confirmed live via SSH. Then three unverified-item closures during the 15h soak wait until the 2026-04-22 03:03 UTC audit fire.

### S194 (2026-04-24) — Trading revival: 6 commits unblock MB after 12-day silence + WB infrastructure cleanup

**Context.** S172 §S181 Issue 9 ("paper_trades zero-volume ESCALATED 2026-04-19") was acknowledged but never root-caused. S194 deep-dive identified four behavioral roots, all shipped + deployed in this session. MB resumed trading post-deploy after a multi-day silence (canonical counts via `scripts/bot_pnl.py`).

**Phase A diagnostics (Phase A complete, hypothesis inversions recorded):**

| Hypothesis | Outcome |
|---|---|
| WB `_restore_exposure_from_db()` sums historical (closed) positions, pinning city counters at cap | **FALSIFIED** — restore reads `daily_counters` (correct semantics). Real bug was elsewhere (see B-NEW-1 + B-NEW-2 below). |
| `mirror_state` empty → MB cold-start deadlock | **FALSIFIED** — `_eq_n` source is `EliteReliabilityTracker.total_trade_count()`, not `mirror_state`. `mirror_state` was a red herring. |
| Triple-blind passes (S193 pattern continuing) inverted v2 D4 framing — promoted to second instance of "Triple-blind catches what Pass 1 missed." Promotion candidate strengthened. | |

**Commits (all on master, all deployed):**

- `b439f3f` **B-NEW-1** — `daily_counter.py` GREATEST(0, ...) clamp on both INSERT and ON CONFLICT UPDATE branches. Pre-fix: net-counter callers (WB `_city_exposure`/`_group_exposure`) decrement-on-exit landed counters negative on fresh-zero days. 15+ days of negatives on prod (qualitative — peak count visible in `daily_counters` history, magnitude omitted per Protocol 6). S105b restore-time clamp papered over startup symptoms but didn't fix the write-through bug.
- `f928695` **B-NEW-2** — Wire `mark_positions_seeded()` / `mark_exposure_restored()` / `mark_reconciliation_passed()` across `base_engine.start()` + 3 bot files. Pre-fix: setters defined at `base_engine.py:1275/1280/1285` but ZERO callers in entire codebase. Every restart of every bot hit the 120s startup-hold watchdog and entered degraded mode. Watchdog retained as last-resort fallback.
- `8f26a38` + `35eed49` **D4-NEW** — `MIRROR_REGIME_START` typed as `str` (settings.py:454); asyncpg rejected str-for-timestamptz with `DataError`. Both `EliteReliabilityTracker.refresh()` AND `EliteWatchlist` copy-tier scoring SQL failed silently for 25 days starting 2026-03-30 (cache `updated_at` confirmed). Empty cache → trader `_eq_n=0` → MB gate score capped → MB stopped trading 2026-04-13. Type changed to `Optional[datetime]` via `_parse_iso_dt()` helper that strips tz to naive UTC (PG TIMESTAMP WITHOUT TIME ZONE convention). Follow-up commit added the tz-strip after first deploy revealed `can't subtract offset-naive and offset-aware datetimes`.
- `7b5e535` **B-NEW-3** — Split misleading `exposure_cap` SHADOW_ENTRY label at `weather_bot.py:3508-3521` into 4 disjoint reasons: `sub_min_trade`, `group_cap_exceeded`, `city_cap_exceeded`, `slippage_cap_exceeded`. Pre-fix label conflated all three cap sources; ~75% of WB rejections were actually slippage-cap firings (book-depth too thin), not true cap exhaustion. Pure observability — no behavior change in trading logic.
- `c7bf9e0` **F2** — Correct §S181 line 1005 wrong-claim about gate-funnel observability. Preserves line 970 (EB v2 root cause = key-name mismatch — TRUE, fixed in S182 Phase 1d). Strikes line 1005 ("Phase 2 Commit 5 gate-funnel logging would self-document") — verified never shipped under any matching name (grep across `bots/*.py` + `base_engine/risk/*.py` returned zero matches).

**Deploys.** Five releases in chain: `20260424_122139` (B-NEW-1+B-NEW-2) → `20260424_130539` (D4-NEW first form) → `20260424_131251` (D4-NEW tz-strip follow-up) → `20260424_133833` (B-NEW-3+F2, first attempt) → `20260424_132746` (B-NEW-3+F2, late-completing background race; identical code from same HEAD `c7bf9e0`). Two last deploys raced; current symlink at `132746`. All 6 commits in deployed tree (verified via grep on S194 markers across the 4 affected files).

**Live verification (qualitative, check-internal carveout 6a where applicable; performance via `bot_pnl.py`):**

- Bug 1: `daily_counters` produces zero new negative rows under `counter_date = CURRENT_DATE` post-deploy (audit/check-correctness).
- Bug 2: Each of WB/MB has zero `startup_hold: timeout reached` events post-deploy. All three flags fire via the proper `mark_*` setter chain (`positions seeded` → `reconciliation passed` → `exposure restored` → `engine ready to trade`). Watchdog fallback now never invoked under happy path.
- Bug 3 (D4-NEW): `system_kv['reliability_cache'].updated_at` advanced from 2026-03-30 (25 days stale) to a fresh post-deploy timestamp. Cache size dropped substantially because the regime_start filter is now properly excluding pre-regime data (its original intent).
- Bug 4 (B-NEW-3): new label categories shipped; verification deferred until next WB SHADOW_ENTRY rejection.
- MB resumed producing `paper_trades` rows post-D4-NEW deploy after the multi-day silence (qualitative; canonical count via `scripts/bot_pnl.py MirrorBot 24`).

**Phase 0 audit gate from S193 close (verified S194 close):** `audit_runs.run_id=1282`, `run_type=scheduled_daily`, fired 2026-04-24 03:01:02 UTC. Zero `phantom_position_no_entry_event` emissions. S193 ENTRY auto-heal end-to-end verified.

**EsportsBot v2 residual.** EB v2 still hits the 120s startup-hold timeout once per restart because its `_restore_exposure_from_db()` is invoked from the first scan cycle (~28s typical scan time per S181 finding) and the cold-start XGBoost+Venn-ABERS pipeline fit can push first-scan completion past the 120s window. Out of S194 scope — separate fix would require either watchdog timeout extension for EB or moving EB exposure restore out of the scan loop.

**Plan-hygiene candidates this session (filed, not codified):**
- "Triple-blind verification for inventory/hypothesis claims" — S193 first instance, S194 second instance (v1 → v2 → v3 plan revisions inverted load-bearing claims each pass). Promotion criteria met (≥2 instances). Candidate for next plan-hygiene round.
- "Diagnostic-inverts-remediation-space" pattern — S193 first instance, S194 second instance (D4-NEW: mirror_state hypothesis inverted by triple-blind to find EliteReliabilityTracker as actual store). 2 instances filed.

**Operational consequences.**
- S172 Phase 7 gate (MB elevation) now genuinely evaluable for the first time in weeks. 7B Phase A (wallet selection overhaul) becomes the next-highest-ROI unblocked item.
- §S181 Issue 9 now closed via this entry — both root causes identified and shipped.

**Out of S194 scope (filed for future sessions):**
- Phase C (WB slippage_size_cap): C-NEW-1-A diagnostic still required first to characterize the 4 dominant numeric markets driving `depth_exceeded` rejections. Decision tree (close-no-fix / filter universe / cap raw_size at signal-gen) blocked on diagnostic.
- D-prereq runbook fix: `gate_score_expectancy.py` requires `cd /opt/polymarket-ai-v2` before invocation. Trivial; document in §S187 hygiene backlog.
- D1/D2/D3/D5 secondary MB gate options: hold pending observation of D4-NEW impact in coming days.
- EB v2 startup timing fix (above).

**Evidence of origin.** S194 session 2026-04-24. Phase A diagnostics + triple-blind passes recorded in `S194_TRADING_REVIVAL_PLAN.md` (working tree, gitignored per `.gitignore:147`). Six commits `b439f3f`, `f928695`, `8f26a38`, `35eed49`, `7b5e535`, `c7bf9e0` on master. Five deploys 2026-04-24 12:21 UTC → 13:38 UTC.

---

### S195 (2026-04-25 → 2026-04-26) — RESOLUTION silent-zero + EB matcher overhaul + Day 1/2 follow-ups

**Context.** Two independent root causes found via recursive dive-down. RESOLUTION emission had been silently failing for ~17 days due to a SQL `--` line comment that ate the rest of the INSERT. EsportsBot wasn't trading because (a) the alias table was empty, then (b) once seeded, the matcher accepted any market mentioning either team — so famous teams routed to season-long playoff markets. Multiple intermediate hypotheses got disproven by direct measurement before any fix shipped (recurring pattern this session). Day 1 + Day 2 followed up the same session-day with infrastructure hardening + EB v2 cold-start fix.

**Phase A diagnostics (4 hypothesis inversions, all caught pre-fix):**

| Hypothesis | Outcome |
|---|---|
| The full backfill job isn't being invoked | **FALSIFIED** — background journal grep showed `_do_resolution_queue` calls `run_resolution_backfill` every ~15 min; `log_progress=False` was suppressing all phase logs. |
| `SIGNAL_REQUIRED_BOTS=EsportsBot` puts EB in shadow-only mode | **FALSIFIED** — reading `base_engine/audit/factory.py:55` + `base_engine/audit/checks/signal_execution_check.py` showed it's an audit-check severity dial, not a trading gate. EB is enabled to trade per `BOT_ENABLED_ESPORTS=true`. |
| 35 upcoming matches in 48h window are unpredicted | **FALSIFIED** — own SQL bug (double-prefixed `'ps_' \|\| em.match_id` in JOIN). Correct join showed 0 unpredicted matches; all 35 had predictions but most had NULL `market_price`. |
| Architectural wiring fix in commit 1 (`d67e03e`) is sufficient | **FALSIFIED** — `PostgresSyntaxError: syntax error at end of input` in journal. The wiring change made the silent failure observable but didn't fix it. The actual silent-zero was upstream in `insert_trade_event`. |

Pattern: 4 instances of "diagnostic-inverts-remediation-space" within this session; 4-instance threshold met across S193/S194/S195 — promotion-ready as a Protocol candidate (see plan-hygiene below).

**Commits — initial S195 close (8 on master, 2 successful deploys at code level):**

- `d67e03e` Architectural cleanup — Phase 4b → first-class `backfill_trade_events_resolution()` method + 3 invocation paths + unconditional emission counter. Useful structurally, NOT the root-cause fix. Pre-S195-Day-2 reviewers should note: cleanup commits without root-cause linkage are a class flagged in the plan-hygiene below.
- `9a2f363` EB alias matcher v1 + migration 074 + unmatched-predictions tracker. Matcher returned `any(team in question)` — too loose; necessary but not sufficient.
- `0c32b61` EB seed builder script `seed_esports_team_aliases.py`. Identity pass + fuzzy-link pass; fuzzy pass produced 3 false positives in dry-run; `--no-fuzzy` flag added later.
- `b82ad68` **ROOT CAUSE #1** — `--` SQL comment in `insert_trade_event` RESOLUTION INSERT swallowed the closing `)` + `AND NOT EXISTS` + `RETURNING`. Introduced ~2026-04-08 in S167. 17 days of silent `PostgresSyntaxError` at `database.py:5530`. Fixed via `/* ... */` block comment.
- `989ab66` Migration 074 v2 — drop GENERATED column, use functional `LOWER(alias)` index. First deploy attempt failed because ORM created the table before migration ran (no GENERATED column).
- `05015e7` Migration 074 v3 — inline `COMMENT` statements onto single lines. Second deploy attempt failed because multi-line PostgreSQL adjacent-string concat broke the migration runner's parse.
- `4f63ff5` `bulk_upsert_team_aliases` sets `created_at = NOW()` explicitly + `--no-fuzzy` seed flag. ORM-created tables had no SQL DEFAULT (Python-side `default=` only) so raw INSERT hit `NotNullViolationError`. Hot-patched on prod via `ALTER TABLE`; this commit makes future fresh installs idempotent.
- `f4c5d11` **ROOT CAUSE #2** — Matcher requires BOTH teams + ranks match-specific above season / handicap. Live test: T1 vs BNK FEARX pre-fix returned 25 markets (top = season playoff); post-fix returns 1 market (the correct one). Three new helpers replace `_team_match_score`: `_team_present`, `_both_teams_present`, `_specificity_score`.

**One-off prod ops not in any commit:** manual `sudo pip install rapidfuzz>=3.5.0` + `ALTER TABLE ... SET DEFAULT NOW()` for the two created_at/event_time columns. Lifted into commits in Day 1 (see below).

**Commits — S195 Day 1 (2026-04-26, 4 commits, infrastructure follow-ups):**

- `e3bc0ad` `fix(deps)`: rapidfuzz promoted from `requirements-improvements.txt` to core `requirements.txt`. `deploy/first-run.sh:22` only installs `requirements.txt`, so rapidfuzz was outside the deploy path. Lifts the manual prod hot-patch into version control.
- `c00a148` `fix(migration)`: 075 — lifts the manual `ALTER TABLE ... SET DEFAULT NOW()` hot-patch. Idempotent — no-op on prod (DEFAULT already set), active on fresh installs. `down/075_drop_esports_default_now.sql` documents the intentional non-rollback contract: `bulk_upsert_team_aliases` (commit 7) sets `created_at = NOW()` explicitly, so the DEFAULTs are belt-and-suspenders, NOT load-bearing.
- `1ab85b9` `feat(ci)`: libcst pre-commit guard at `scripts/check_sql_dash_dash.py` against the SQL `--` adjacent-string concat bug class (root cause #1 above). State-aware SQL parser tracks `/* */` block comments + `'...'` string literals + `"..."` quoted identifiers to suppress false positives; only flags `--` in a non-final fragment with no trailing `\n`. 9 contract tests pin the boundary. `.pre-commit-config.yaml` wires `libcst==1.8.6`. Repo-wide sweep clean.
- `4c6a584` `feat(ops)`: `scripts/check_deploy_drift.py` — pip drift (declared-but-missing on VPS; leaf-not-declared notice) + schema drift probe for the two S195-tracked DEFAULT columns. PEP 503 name normalisation. Distinguishes `requirements.txt` (deploy-installed) from `requirements-improvements.txt` (optional). Operator-runnable; auto-wiring into `deploy.sh` deferred to Week 2 alongside the migration-runner replacement (S195 §6.3 in handoff).

**Commits — S195 Day 2 (2026-04-26, 3 commits, EB v2 cold-start fix):**

- `b1dbfad` `perf(esports_v2)`: `XGBoostMetaModel.predict_proba` + `predict_proba_batch` route through `Booster.inplace_predict()` instead of `XGBClassifier.predict_proba`. For binary:logistic, default `predict_type="value"` returns identical probabilities without DMatrix construction overhead. Hot path is `_predict_upcoming_matches` (35+ matches per scan).
- `3708241` `fix(esports_v2)`: persistence migrated from joblib/pickle to `skops>=0.13.0` with explicit `_SKOPS_TRUSTED_TYPES` whitelist (6 entries). `load()` prefers `.skops`, falls back to `.joblib` at the same stem so legacy pre-Day-2 snapshots still load on first restart; next save rewrites in skops format. 5 new contract tests pin save→load round-trip + fallback + stale + reject-untrusted. `requirements.txt` adds `skops>=0.13.0`.
- `c7e2a3e` `fix(esports_v2)`: split `_initialize()` into `_lightweight_init()` (sync, seconds) + `_heavy_warmup()` (5+ min, background asyncio.Task). `start()` kicks off the warmup task and yields to `super().start()` immediately, so the BaseEngine 120s startup-hold runs concurrent with the cold fit instead of being blocked by it. `scan_and_trade()` gates on `_warmup_complete()` which implements the fail-loud contract — re-raises any warmup exception so the scan loop surfaces failures rather than scanning silently against an unfit model. Cancelled tasks (graceful shutdown) return `False` without raising. `_initialize()` retained as a back-compat shim. 6 new gate tests pin the contract.

**Phase 0 verification (Day 1, post-deploy `20260425_213055`):**
- Symlink at expected release; RESOLUTION emission healthy (125 backfill log lines / 6h); MB+WB 120h windows match handoff session-close numbers (canonical via `bot_pnl.py`); 28 trade_events guard tests pass.
- EB re-predicted 13/13 cleared matches at 02:34+ UTC. **Affirmative-path gap (open):** all 13 cleared rows were "no Polymarket equivalent" cases (NRG/M80/RUSTEC/AaB Esport/etc. — no T1 / BNK FEARX / Nongshim / Gen.G in the set). Phase 0 verifies the rejection path of root cause #2 only; affirmative path needs a tier-1 matchable match to enter the 48h horizon (queued in §S195 Hygiene Backlog below).
- Drift detector found uvloop + 10 other declared packages absent on VPS. uvloop is real perf drift (`main.py:629-634` silent fallback to default asyncio). Triage list in §S195 Hygiene Backlog.

**EB v2 cold-start architecture after Day 2.** `start()` → lightweight init → kick `_heavy_warmup()` as background task → `super().start()` immediately. Heavy work (snapshot load + DB rebuild + pipeline fit) runs concurrent with the BaseEngine 120s startup-hold rather than blocking it. `scan_and_trade()` ticks during warmup but skips predicting; on warmup success the gate opens; on warmup failure the gate re-raises. Resolves the §S194 EB v2 startup-timing residual (`HEALTH_WARN` soft mode every restart) at the architectural level. Empirical post-deploy verification pending — current symlink is still the pre-Day-2 `20260425_213055`.

**Plan-hygiene candidates (≥3-instance, promotion-ready per S195 close §5):**
- **Diagnostic-inverts-remediation-space.** S193 §4.1 + S194 D4-NEW + S195 §2.3 four sub-instances. 4-instance threshold cleared. Recommend codifying as Protocol 7 in next plan-hygiene round.
- **Triple-blind verification for hypothesis claims.** S193 + S194 + S195 (4 wrong hypotheses caught before any wrong fix shipped). 3 instances. Promotion-ready as Protocol 8.
- **Architectural cleanup is not a substitute for root cause** (NEW). S195 commit `d67e03e` cleanup added emission counter + invocation paths but did NOT fix the underlying SQL bug (commit `b82ad68` did). Mandate candidate (Protocol 9): cleanup commits without root-cause linkage must carry `Cleanup-Only: yes` trailer + reference the tracked root-cause commit.
- **Silent-loop emission must be observable** (NEW). S195 fix bumped per-row Phase 4b/Phase 4b-alt logs from `debug` to `warning` + added an emission counter. Mandate candidate (Protocol 10): retry/fallback/loop paths that swallow exceptions or skip rows must emit at WARNING + increment a Prometheus counter.

**Operational consequences.**
- §S194 EB v2 startup-timing residual closed at the code-architecture level (Day 2). Empirical confirmation requires deploy + observation.
- bot_pnl.py windowed numbers had been undercounted for 17 calendar days for any window not crossing pre-2026-04-08 data. Now corrected. Future windowed comparisons against pre-fix data will show apparent jumps that are accounting recovery, not actual performance change.
- Migration-runner fragility surfaced on three distinct failure modes in one deploy chain: GENERATED column conflict, multi-line PostgreSQL string-literal concat, internal `;` inside string literal. Replacement scheduled for Week 2 (sqlparse-based splitter + squawk-cli linter per S195 §6.3).
- ORM-vs-migration ordering bug (`Base.metadata.create_all` runs before migrations and silently bypasses SQL DEFAULTs / GENERATED columns / functional indexes) is broader than just the two columns 075 fixes. Probe + structural fix scheduled for Week 2 (kill `create_all()` in service path; wire `alembic check` programmatically at startup).

**Out of S195 scope (filed for future sessions):**
- Affirmative-path matcher verification (rejection-path verified Day 1; affirmative needs a matchable match next).
- EB throughput improvement (~1 prediction per 75 min today, bounded by PandaScore poll cadence not by the matcher) — Week 2.
- 7B Phase B counterfactual retune — blocked on 2-week `mirror_rejected_signals` soak completing ~2026-05-09/10 — Week 3.
- Migration-runner upgrade + ORM-drift probe + auto-wired drift CI gate — Week 2.
- uvloop install on prod venv — operator one-off (see §S195 Hygiene Backlog).
- Token-pair issue at `bots/esports_bot_v2.py:635` (`_find_market_info` requires both `yes_token_id` AND `no_token_id`) — deferred per S195 close §4.1.

**Evidence of origin.** Initial S195 close 2026-04-25 → 2026-04-26 overnight (`AGENT_HANDOFF_S195_CLOSE.md`). Day 1 + Day 2 same session-day 2026-04-26. 15 commits total: `d67e03e`, `9a2f363`, `0c32b61`, `b82ad68`, `989ab66`, `05015e7`, `4f63ff5`, `f4c5d11` (initial close); `e3bc0ad`, `c00a148`, `1ab85b9`, `4c6a584` (Day 1); `b1dbfad`, `3708241`, `c7e2a3e` (Day 2). Pre-session HEAD `c7bf9e0`; post-Day-2 HEAD `c7e2a3e`. Initial S195 close deployed as `20260425_213055`; Day 1 + Day 2 commits not yet deployed.

---

### S195 Hygiene Backlog

**uvloop missing on prod venv (real perf drift) — DONE 2026-04-26.** `requirements.txt` declares `uvloop>=0.19.0; sys_platform != 'win32'` and `main.py:629-634` imports it with `try/except ImportError: pass` graceful fallback. Drift detector confirmed not installed on the prod venv at `/opt/pa2-shared/venv` — bots were running on default asyncio, missing the 2–4× event-loop throughput uplift the comment claims. The fallback is silent: no log line emits "uvloop unavailable, using default loop". **Resolution:** `sudo /opt/pa2-shared/venv/bin/pip install 'uvloop>=0.19.0'` ran on prod 2026-04-26 (uvloop 0.22.1 installed). Bots pick up on next restart; uvloop is loaded once at process start via `uvloop.install()` (`main.py:632`), so currently-running processes remain on default asyncio until natural deploy/restart. **Skops also installed in the same operator window** (S195 Day 2 commit `3708241` added `skops>=0.13.0` to `requirements.txt`; would have been a deploy-blocker on next deploy because `pipeline.save()` does `import skops.io as sio`). The "structural fix" framing in the original entry was overscoped — for a single missing package the minimal fix is a one-line targeted install, not the general venv-refresh redesign. The general redesign (freeze-then-replace venv pattern) remains scheduled for Week 2, but is for the *class* of drift, not a blocker for individual fixes.

**10 other declared-but-missing packages — triage list.** Drift detector also flagged `lleaves`, `pymc`, `nutpie`, `onnxmltools`, `onnxruntime`, `skl2onnx`, `discord-py`, `praw`, `spacy`, `telethon` as declared in `requirements.txt` but absent from the prod venv. Each needs labelling as one of: (a) intentional, gated on phase X (no fix needed, document the gate); (b) deploy bug (install via the same one-off command above). Initial classification: `discord.py`/`praw`/`telethon` → SOCIAL STREAMING phase, gated on Phase 12 sports work — install when phase activates; `pymc`/`nutpie` → Phase 6P/6N Bayesian work, install when phase activates; `lleaves` → LightGBM compiled inference (Linux-only marker, 16× faster), install when WB compiled-pipeline phase activates; `onnxmltools`/`onnxruntime`/`skl2onnx` → ONNX export for esports model compiled inference, defer until perf-regression demand; `spacy` → injury detector NER, gated on sports/news phase. None are blocking trading today — graceful fallbacks are in place — but the drift detector will keep flagging until each is either installed or the gating phase completes and updates the requirements file accordingly.

**Affirmative-path matcher test queue.** Day 1 Phase 0 verified the rejection path of root-cause-#2 (matcher correctly returns 0 markets for tier-3/academy team pairs with no Polymarket equivalent). Affirmative path remains unverified: no tier-1 matchable match (T1/BNK FEARX/Nongshim/Gen.G class) was in the 13 cleared rows. Test contract: when a tier-1 match next enters the 48h `esports_predictions` horizon (typically several per week given LCK/major event cadence), the next-session Phase 0 must (a) confirm at least one such row has `market_price IS NOT NULL` post-prediction, (b) cross-check the matched market via the matcher's `_find_polymarket_for_match` output equals the season-non-playoff market for the specific match. Acceptable to ship the next deploy without this confirmation; the rejection-path evidence is real and the affirmative path is the same code branch — the verification is empirical, not architectural.

**Migration-runner replacement scope (Week 2 anchor).** Three distinct migration-074 failure modes documented in S195 close §2.4 (GENERATED column ordering, multi-line string concat, internal `;` in string literal). The current `scripts/run_migrations.py` already handles dollar-quoted blocks and full-line `--` comments, but doesn't tokenise SQL string literals so a `;` inside `'...'` confuses the splitter. Replacement plan per the audit: replace `text.split(';')` with `sqlparse.split(text)` (one line; `sqlparse` is already a transitive dep) plus an 8-line preprocessor that skips dollar-quoted regions before splitting. Add `squawk-cli` as a CI lint on `migrations/*.sql`. Defer alembic-as-runner until autogenerate is wanted. The drift CI gate (`scripts/check_deploy_drift.py`) auto-wires into `deploy.sh` in the same Week 2 PR.

**ORM-vs-migration ordering — broader fix.** `Base.metadata.create_all` runs at first DB session use and silently bypasses SQL DEFAULTs, GENERATED columns, functional/partial indexes, CHECK constraints, and other PG-specific DDL. Migration 074 + 075 together fix the two specific columns, but the structural bug remains for any future table. Week 2 plan: kill `create_all()` in the live service path (use only against a fresh test DB), wire `alembic check` programmatically at startup with explicit try/except + structured logging, supplement with ~70 LOC of homegrown `pg_catalog` probes for what reflection misses (`attgenerated`, functional/partial indexes via `pg_indexes.indexdef`, CHECK constraints via `information_schema.check_constraints`).

**Audit-check rewrites for SIZE_INVARIANT + FK_MISSING_MARKET — RESCOPED 2026-04-26.** S185's reclassification to `FIX_AUDIT_CHECK` for these two recon types is **incorrect** based on Day 3 prod investigation. The audit checks themselves are structurally sound; the OPEN violations reflect real data-shape issues:

- **SIZE_INVARIANT (10,121 rows)** — sample probe of one MirrorBot violation: ENTRY size=404.727 (NO shares), EXIT size=810.81 (SELL). The SELL-side EXIT is ~2× the ENTRY size. Investigating one further would reveal whether SELL EXITs are dollar-denominated while ENTRYs are share-denominated, or whether MirrorBot's exit logic has a real size-computation bug. Either way the fix is in MirrorBot's emission logic or a units-aware audit check, not a one-line check rewrite. Multi-hour engineering, not bulk-ack class.
- **FK_MISSING_MARKET (7,042 rows)** — the JOIN at `fk_integrity_check.py:50` (`m.id = t.market_id`) is correct: both `markets.id` and `markets.condition_id` are `character varying` and contain the same 0x-hex string for the sampled row. The orphans are real — referenced markets aren't in the `markets` table. Pre-S195 trade_events emit code didn't auto-heal (S193's fix added auto-heal for ENTRY only). Older rows from before that fix permanently orphan the FK. Either backfill stub markets for orphans or live with the recon noise; both are operator-decision territory, not a check rewrite.

**S185's FIX_AUDIT_CHECK label was a misclassification.** Recommended re-classification: SIZE_INVARIANT → FIX_EMISSION (MirrorBot exit-size computation), FK_MISSING_MARKET → ACCEPT_LEGACY_NOISE_OR_BACKFILL_STUBS (operator decision per backlog entry below). Bulk-acking either pre-rewrite would just have them re-raise on the next audit run — same anti-pattern as the original "ack and move on" S185 rejected. Re-classification handle: open a follow-up plan-hygiene PR that updates both checks' module docstrings and reclassifies the recon_type metadata.

**SIZE_INVARIANT diagnostic — bot_pnl.py canonical P&L is contaminated (qualitative).** Day 3 review queued a focused diagnostic: pick a sample of MirrorBot SIZE_INVARIANT violations, inspect their `trade_events` rows, cross-reference the P&L computation path. Result 2026-04-26 — numbers omitted per Rule Zero (no `bot_pnl.py` source for individual trade sizes), described qualitatively:

- Sampled markets show two distinct EXIT-size-vs-ENTRY-size inflation patterns. Some sampled markets had EXIT.size ≤ ENTRY.size and shouldn't have been flagged at all — suggests the audit check has a residual edge case (possibly: violations that self-resolve via subsequent events stay OPEN forever instead of auto-closing). The unambiguous-inflation cases are real anomalies and the focus of this finding.
- `bot_pnl.py:130` reads `trade_events.realized_pnl` directly for both EXIT and RESOLUTION P&L. It does NOT recompute from `size * price`. But `paper_trading.py:683` *originally computes* `realized_pnl = (price - avg_price) * size - fee - _prorated_entry_fee` at EXIT-emit time. So any size inflation propagates into `realized_pnl` at write time and bot_pnl.py reads the resulting value forever after. The contamination is in the `trade_events` column, not the script — bot_pnl.py is doing exactly what it should.
- Likely emission paths: the standard mirror_bot.py exit at line 1352 passes `size=exit_size = pos["size"]`, which would equal entry size for a single-ENTRY position. Two candidates worth tracing first: `position_manager.py:467` auto-close-on-expired-market (`_size = float(pos.size or 0)` reads from `positions` table — if that row was inflated by some other path, the EXIT event inherits it); and `mirror_bot.py:283-310` `_seed_daily_exposure` / restore-state paths that may sum trade_events incorrectly into in-memory state.
- **Hold on every analysis of MirrorBot performance from bot_pnl.py until the inflation source is identified and either fixed forward or quantified for offset.** This includes the Phase 7 MB elevation gate (P(edge>0) ≥ 0.30 on post-Day-2 trades, S195 §forward-audit) — that gate's "ground truth" is bot_pnl.py output, which inherits any contamination written into `trade_events.realized_pnl`. 7B Phase B counterfactual retune (post-soak ~May 9-10) similarly depends on bot_pnl.py being trustworthy. Resolving the inflation source is the prerequisite, not concurrent work.

**Venn-ABERS calibration history annotation 2026-04-26.** The wrapper fix at `0e27917` corrected two API-shape bugs in `esports/models/venn_abers_calibrator.py` (cal_size requirement + predict_proba return shape) that were silent debug-log failures since `venn_abers>=0.4.0` released. EsportsBot v2's per-game calibrator at `bots/esports_bot.py:5636-5663` was unfit during that window — calibrate() fell through to identity passthrough, returning raw model probabilities. Any EB v2 trade decisions during that window used uncalibrated probs for Kelly + edge math. EB v2 has not produced enough trades yet for this to be material to bot_pnl.py numbers (per S195 close §1, EB had not traded), but anyone analysing post-deploy EB v2 performance should account for the post-fix shift from raw → calibrated probs as a regime change, not a continuous metric.

**Phase 3 VPS config — 3/4 DONE 2026-04-26 (SSH port deferred).**

DONE:
- **PgBouncer `idle_transaction_timeout = 300` (5 min)** — added to `/etc/pgbouncer/pgbouncer.ini` after the existing `server_idle_timeout = 600`. Online reload via `systemctl reload pgbouncer` (no connection drop). Closes the connection-leak class observed in S163-S168 where idle-in-transaction sessions held PgBouncer slots indefinitely.
- **PostgreSQL `autovacuum_naptime = 30s` (was 60s default)** — written to `/etc/postgresql/16/main/postgresql.conf`, SIGHUP reload via `systemctl reload postgresql`. More aggressive vacuum cadence reduces dead-tuple buildup on hot tables (`trade_events`, `paper_trades`, `prediction_log`) without measurable load impact.
- **sshd `MaxAuthTries 3` + `AllowUsers ubuntu`** — added to `/etc/ssh/sshd_config`, validated via `sshd -t` before reload, applied via `systemctl reload ssh`. Verified live: a fresh SSH connection still authenticates as `ubuntu` post-reload. The `polymarket` service-runner user keeps working because `AllowUsers` restricts SSH login only — `sudo -u polymarket` from an `ubuntu` SSH session is unaffected.

All three verified live (config files contain the new values, reloads returned ok, runtime checks pass). **NOT in code** — same drift class as rapidfuzz/skops/uvloop. Structural fix is the Week 2 deploy hardening track.

**STILL DEFERRED — SSH port change from 22.** Three coupled prerequisites that none of the prior fixes address:
  1. AWS Lightsail firewall must be reconfigured to open the new port BEFORE sshd starts listening on it (otherwise lockout despite valid sshd config).
  2. `deploy/deploy.sh` + `deploy/rollback.sh` + every operator-side `ssh -i .../key` invocation needs `-p NEW_PORT` plumbed through.
  3. Local `~/.ssh/config` Host entry needs the new port. If the operator's automation uses ad-hoc invocations, each one needs auditing.
  Each step is reversible individually but the chain has multiple chances to lock the operator out. Best done in a session that opens with an explicit recovery rehearsal: confirm Lightsail console access, document the rollback command, and make port 22 the FALLBACK listen port until the new port is verified working.

---

### S187 Hygiene Backlog

**RTDS dedup hit rate observability.** From 2E scope decision: 7B Phase A excludes `EliteWatchlist._seen_tx` dedup from `mirror_rejected_signals`, but the hit rate of `_seen_tx` is itself operationally interesting. "How often does the same trade arrive twice via RTDS?" — 1% = transport noise (expected); 40% = likely upstream dedup bug worth investigating. Cheap fix: one structured log line at `bots/elite_watchlist.py:984-988` emitting a counter (not a rejection-table row, not a 7B coupling). 30-second implementation whenever 7B Phase A work is in the vicinity of this file anyway.

**22 `.disabled` calibrator tests — category-determination investigation.** `tests/unit/test_esports_calibrators.py.disabled` contains 22 tests disabled via filename suffix. Unusual pattern — normal pytest disabling is `@pytest.mark.skip` or conftest-level exclusion. Suggests one of: (a) deliberate quarantine with a known failure reason (tests found real bugs not yet fixed), (b) collateral damage from a refactor where disabling was the quickest unblock, (c) unfinished work where author got sidetracked. Investigation steps: `git log --follow` the file path + the rename, read the 22 test bodies, decide whether to fix + un-disable or delete the file entirely. Un-disabling without resolving category is unsafe — the tests might reveal real failures that were deferred. Related: audit the working tree for other "survive by careful hands" artifacts at risk from any `git clean` invocation.

**TradeJournal nested-session fix (`base_engine/analysis/trade_journal.py`).** From 4E closure: `generate_period_journal` holds an outer session across per-trade calls to `generate_journal_entry`, each of which opens its own inner session. Fix options: (a) fetch the trade IDs first, close the outer session, then iterate `generate_journal_entry` calls; or (b) restructure to pass the session into the inner method rather than reopening. Low priority — unreachable code path today (no consumer). Ship whenever someone touches the file for another reason.

**Orphan-feature CI check (structural).** From 4E closure: `TradeJournal` was wired at `base_engine/base_engine.py:757` but no consumer ever called it — structurally symmetric to S182's `EsportsMarketService` not-instantiated case (called but never constructed; this is constructed but never called). Proposed CI mechanism: a pytest-level or lint-level assertion that fails if any attribute set on `BaseEngine` at startup (`self.X = Class(...)` pattern) has zero external callers of its public methods. Catches the orphan-feature class mechanically rather than by serendipity. Open design questions: (1) how to distinguish "no callers yet (feature in progress)" from "no callers forever (abandoned)" — possibly requires an explicit `@expected_consumer_by_phase_N` decorator or a manifest file in `docs/`, (2) scope — only `BaseEngine` startup or all DI-registered services.

**Full Phase 2/4 reconciliation pass.** Three unverified-item closures this session surfaced three drift findings (2E file column, 4D three-way drift, 4E unreachable-bug + orphan). §S186b ran the same discipline against Phase 3 config space (6 discrepancies caught). Phase 2 and Phase 4 tables have never had a systematic claim-by-claim reconciliation — unverified items in those tables have been closed ad-hoc when a session happens to trigger review, not systematically. Recommendation: a future plan-hygiene session runs the S186b-style pass against all Phase 2 table rows and Phase 4 list items. Expected yield: drift-findings proportional to closure count based on this session's 1:1 ratio. Dedicated session with its own scope.

---

### S198 Hygiene Backlog

**bot_pnl.py CLEAN as gate signal — time-window caveat.** The CLEAN block at `scripts/bot_pnl.py:132-193` (S197 commit `cd9c5cf`) computes all-time realized P&L excluding contaminated markets. The S172 v7 Phase 7 elevation gate at §439-446 asks for `P(edge>0) ≥ 0.30 on 500+ post-fix trades` — a windowed distributional metric, not an all-time total. Any reference to "bot_pnl.py CLEAN total" as a Phase 7 gate signal must specify the time window or be explicitly labeled as directional/qualitative only. The all-time CLEAN total conflates pre- and post-deploy data, and a session that read it as "the gate signal" is over-reading the tool. **Resolution path:** add `--since DEPLOY_TIMESTAMP` flag to the CLEAN block (~20 lines), with a parallel extension to `scripts/edge_verification.py` that adds the same flag plus a CLEAN filter and updates the script's stale gate thresholds to match v7 (≥0.30 / 0.10–0.30 / <0.10, replacing the existing ≥0.9 / 0.7–0.9 / <0.7). Combined ~30 lines. **Forward annotation:** S197 close framed Phase 7 MB elevation gate as "UNBLOCKED via CLEAN total" — that framing was over-scoped. The unblock is the *existence* of a contamination-aware view, not the windowing the formal gate requires. Until the `--since` extensions land, references to CLEAN as a gate signal must carry the directional-only marker.

**Orphan `trade_events` rows + `SHADOW_ENTRY` FK auto-heal bypass.** S193's commit `73bc623` added auto-heal at `insert_trade_event` for ENTRY rows whose `markets` FK target was missing — stub markets are inserted then the FK is re-checked. Audit's FK_INTEGRITY check (`audit_triage.py` "trade_events → markets (no market)" line) reports orphan trade_events with the newest event_time post-S193 deploy. Post-deploy orphans observed are `SHADOW_ENTRY` events. **Structural finding (not just a count):** SHADOW_ENTRY appears to use a write path that bypasses `insert_trade_event` and therefore bypasses S193's FK auto-heal. **Subtasks for next session:** (a) identify the SHADOW_ENTRY writer path — grep for `SHADOW_ENTRY` in writer code (`grep -rn "SHADOW_ENTRY" --include="*.py"` + trace back to the `INSERT INTO trade_events` site that doesn't route through `insert_trade_event`); (b) decide remediation — either route SHADOW_ENTRY through `insert_trade_event` (uniform FK enforcement), apply equivalent FK auto-heal at the SHADOW_ENTRY write site (per-site fix), or accept SHADOW_ENTRY as a separate category exempt from FK enforcement (rule the noise expected). **Parallel to Bug A in shape:** both are writers that touch a trading-state table without going through the canonical write path. Treat with the same investigative discipline as Bug A — identify the writer first, decide remediation second.

**S199 RESOLUTION (forward-update for the SHADOW_ENTRY item above).** The "SHADOW_ENTRY uses a write path that bypasses `insert_trade_event`" hypothesis was **falsified** in S199. Verification: both SHADOW_ENTRY emission sites at `bots/weather_bot.py:3481-3494` and `:3544-3559` DO call `insert_trade_event`. The actual mechanism is at `base_engine/data/database.py:5490` — pre-S199 the FK auto-heal guard tuple was `("ENTRY", "EXIT")` only; SHADOW_ENTRY events fell past it. Schema verification (2026-04-28) confirmed `trade_events` has no DB-level FK to `markets`, so SHADOW_ENTRY emissions on unknown markets succeeded as silent orphans. **Fix shipped:** commit `5d0eefb` extends the guard tuple to `("ENTRY", "EXIT", "SHADOW_ENTRY")` with ENTRY-semantics heal-vs-fail-closed branching. Deployed in release `20260427_215625`. Diagnostic: orphan-events query against prod showed 100% of orphan `trade_events` rows are SHADOW_ENTRY × WeatherBot (collapses 7,410 OPEN FK_MISSING_MARKET reconciliation rows to a single root cause). The S198 hypothesis was wrong on **mechanism** (incomplete type-check tuple, not parallel architecture); both produced the same observable orphan footprint, but the fix shape was 5 lines instead of architectural rework.

---

### S199 Hygiene Backlog

**S198 §2.3 falsified — replace with S199 finding (already done above, this entry tracks the documentation-hygiene principle).** Any future reference to the original "SHADOW_ENTRY bypass write path" framing should be updated or removed. Pattern to track: when a hypothesis built on prior-session reasoning is falsified by fresh diagnostic, the source narrative needs an explicit forward-update — not just a corrective entry in the new session's notes. Otherwise downstream consumers (next session's plan reading, future audit's hypothesis chains) inherit the falsified premise. The forward-update above is the discipline; this is the codified rule.

**CLEAN floor-of-truth caveat — windowed CLEAN totals are provisional until Bug A backfill lands.** The contamination-detecting CTE in `scripts/bot_pnl.py` (S199 commit `ee76994`) is whole-history by design — see comments at `scripts/bot_pnl.py:140`. A market contaminated at any point in its lifetime is excluded from CLEAN totals even in time windows entirely post-contamination-fix. As Bug A keeps generating new contamination per deploy, the CLEAN denominator shrinks; today's MB Phase 7 verdict (INSUFFICIENT SAMPLE on n=265) is unaffected, but a future AMBIGUOUS-boundary verdict (P(edge>0) close to 0.30 or 0.10) would need re-running post-Bug-A-backfill. Operational rule: any Phase 7 verdict landing on a boundary should carry a "provisional pending Bug A backfill" caveat. Decisive verdicts (well above 0.30 or well below 0.10) are not affected — the CLEAN floor is conservative, so PROCEED above floor and INVESTIGATE below floor remain valid even on the conservative denominator.

**28,557 orphan SHADOW_ENTRY backfill.** Pre-S199 SHADOW_ENTRY emissions on unknown markets created orphan `trade_events` rows (no FK target in `markets`). The S199 fix `5d0eefb` prevents new ones, but the historical orphans remain — collapsed to 7,410 OPEN FK_MISSING_MARKET reconciliation rows totaling 28,557 underlying trade_events. **Resolution path:** ~30-line one-shot script — for each distinct orphan `market_id` in `trade_events` with no `markets` FK target, insert a stub `markets` row (id + condition_id when hex), matching the auto-heal stub pattern at `database.py:5500-5509`. Then run audit; auto-close transitions resolved FK_MISSING_MARKET rows to RESOLVED. Verifiable: post-backfill the orphan-events query returns zero rows. **Why not bundle with `5d0eefb`:** writer-side prevention and consumer-side cleanup are separate concerns per Protocol 12 (defense-in-depth). Each is independently verifiable; bundling would conflate "did the fix work?" with "did the backfill clear the residue?"

**Inter-agent reconciliation discipline.** When a session uses two or more parallel Explore agents on overlapping code (S199 ran Bug A and SHADOW_ENTRY agents that both touched `base_engine/data/database.py`), the synthesizing agent must run a 30-second cross-check before declaring findings independent. The check: list the file:line references each agent produced, identify any overlap, verify the findings don't mechanically interact. S199 verified post-hoc (Bug A sites at `trade_coordinator.py:195/250-262` + `database.py:3734`; SHADOW_ENTRY at `database.py:5490`; different code paths, no interaction) — but post-hoc is brittle. Pre-synthesis is the discipline.

**Bug A averaging-up hypothesis falsified — actual mechanism still unknown.** The S198/S199 candidate hypothesis was that `confirm_position` writes new-fill size (not cumulative) to `positions.size` during averaging-up, causing divergence from `trade_events` ENTRY truth. Verification this session: the upstream caller at `base_engine/execution/order_gateway.py:1044` does pass new-fill (`_filled_size = result.get("filled", size)`), but the SQL UPDATE in `confirm_position` at `base_engine/coordination/trade_coordinator.py:257-263` is gated by `WHERE positions.status = 'closed'` — so for averaging-up on an OPEN position, the UPDATE is a DB no-op. The new-fill never reaches `positions.size`. The simple averaging-up-overwrite hypothesis is **wrong**. The actual integrity-violation pattern observed in `bot_pnl.py` integrity-check output (markets with `entry=0.0 res=large`) suggests `trade_events` ENTRY is missing entirely while `positions.size` was set to a non-zero value via some other write path. **Next-session diagnostic:** (a) identify the code path that sets `positions.size > 0` without a corresponding `insert_trade_event(event_type='ENTRY')` call, OR (b) identify why ENTRY trade_events emissions are silently failing for these specific markets (S193 auto-heal failure mode? race condition? deleted ENTRY rows?). Recommended approach: pick one of the 30 in-window violating markets from MB integrity output, trace its full event history (paper_trades + trade_events + positions audit log), and reconstruct what wrote what when.

**EsportsBot post-Day-2 idle (operational, outside Phase 7 scope).** S199 Phase 7 evaluation showed EB has zero closed trades since Day 2 deploy (2026-04-14) — 13.5-day idle period. Bot service is `active running` per systemctl, but no ENTRY/EXIT/RESOLUTION events from `bot_pnl.py` CLEAN block. Possible causes: EB v2 cold-start failure (per S180/S196 known false-positive on health), signal pipeline broken, paper-trading state wedged, or genuine no-opportunity period. Worth a focused diagnostic before the next deploy compounds whatever is wrong. Not Phase-7-blocking (EB has no v7 gate per S172:439), but the bot existing-but-not-trading is a financial-attribution gap — every day EB is idle is a day of foregone opportunity capture without any signal that something is wrong.

**WeatherBot INVESTIGATE verdict (Phase 6 root-cause investigation candidate).** Phase 7 evaluation for WB returned VERDICT INVESTIGATE on 598 closed post-Day-2 trades (P(edge>0) below v7 floor 0.10 per `scripts/edge_verification.py` v7 thresholds). S172's gate framework specifies "ROOT-CAUSE INVESTIGATION replaces elevation" for sub-floor verdicts. WB's elevation track is Phase 6 calibration improvements; the INVESTIGATE verdict is a signal that Phase 6 is the load-bearing intervention, not Phase 6 + parallel calibration tweaks. Recommendation: a focused root-cause session on WB pre-Phase-6 — what's driving the negative edge? Calibration drift? City-mix? Lead-time bias? The 321 in-window data integrity warnings from `bot_pnl.py` (post-Day-2 markets with disposal-but-no-ENTRY) suggest at least some of the negative edge is contamination, not real edge — Bug A's footprint on WB. CLEAN total already excludes 101 contaminated markets, so the negative edge is on whole-history-clean markets, but the residual integrity warnings inside the window indicate Bug A is still firing on WB. Until Bug A is fixed and rows are backfilled, WB's INVESTIGATE verdict is partly contamination-driven; full root-cause-investigation would benefit from running after Bug A backfill lands.

### S203 (2026-04-29) — Protocol 11 retroactive strip on gitignored artifacts (per §S203 hygiene #13 discipline)

**Issue.** During S203 close, the user-facing summary cited specific per-side counts, win-rates, and dollar P&L from ad-hoc SQL on the post-Day-2 CLEAN WB cohort (n=574 NO trades, 64.5% WR, -$1,714 P&L; n=25 YES trades, 48% WR, +$80 P&L). Those numbers were sourced from direct VPS prod SQL during Track 5 hypothesis-test execution, NOT from `scripts/bot_pnl.py`. The Protocol 11 stop-hook fired post-response.

The same numbers had also landed in three artifacts as load-bearing evidence: (a) `S203_WB_PHASE6_HYPOTHESIS_TEST.md` §3.1, §3.2, §3.3, §4 (verdict), §5 (reframe), §6 (next-session candidate), §7 caveat 2, plus the §2 framing-table cohort row that had n=599/P=0.0651 sourced from `scripts/edge_verification.py`; (b) the gitignored `AGENT_HANDOFF_S203_CLOSE.md` §1 headline and §2.7 Phase 7 verdict table; (c) the user-memory `MEMORY.md` S203 entry's Track 5 paragraph.

**Recovery.** Two parallel paths shipped:

1. **Option 3 (immediate strip).** Commit `891d22d` removed all non-bot_pnl.py-cited specific magnitudes from the Track 5 doc, replacing them with qualitative shape claims ("NO-side dominates volume + above-50% WR + drives loss"). The handoff and MEMORY.md were strip-edited in place (gitignored / outside-repo, so no git commit). The §S203 hygiene backlog gained item 12: extend `scripts/bot_pnl.py` block 5 to honor `--since` and `--clean` so future H0' verification produces canonical-citable evidence.

2. **Option 2 (structural fix).** Same-session, after the user-audit observation that the Option 3 deferral pushed a Protocol 11 gap onto S204 Phase 0:
   - Commit `009fbc6` extended `scripts/bot_pnl.py` block 5 with `--since` and `--clean` filters, extracted `_CONTAMINATION_CTE_BODY` as a module-level constant (DRY-up vs block 3b which already used the CTE inline), added `clean: bool = False` parameter to `bot_pnl()` and `--clean` CLI flag, plus 10 new tests pinning the contamination CTE shape and the clean-flag semantics.
   - Commit `d0f8dec` ran the post-`009fbc6` `scripts/bot_pnl.py` against the prod VPS database (release `20260429_134741`) via `python scripts/bot_pnl.py WeatherBot --since 20260414_132211 --clean` and captured the verbatim output as `S203_H0PRIME_BOT_PNL_OUTPUT.txt` (287 lines). The Track 5 doc was then updated to cite that file with line refs, restoring the specific magnitudes as Protocol 11-citable evidence. The canonical bot_pnl.py output also surfaced a new lead-time disaggregation that the original ad-hoc SQL didn't capture (NO × 24-48h is the dominant loss bucket per `S203_H0PRIME_BOT_PNL_OUTPUT.txt:275`), refining the H0' framing.

**Closure.** §S203 hygiene item 12 is CLOSED in-session by commits `009fbc6` and `d0f8dec`. The `S203_H0PRIME_BOT_PNL_OUTPUT.txt` file is the canonical Protocol 11 citation source for any specific magnitude derived from the post-Day-2 CLEAN WB cohort going forward. Operators run the canonical script directly to reproduce: `python scripts/bot_pnl.py WeatherBot --since 20260414_132211 --clean`.

**Kept as durable record** because the immediate-strip recovery (commit `891d22d`) only edited gitignored artifacts (handoff + MEMORY.md). Without this entry, future sessions reading the repo would see the cleaned-up Track 5 doc but no audit trail of the Protocol 11 violation chain or the Option-3-then-Option-2 recovery sequence. Files §S203 hygiene #13 codifies this discipline going forward: Protocol 11 violations occurring in gitignored artifacts must be acknowledged in §Corrections Log so the violation pattern remains visible cross-session.

### S204 (2026-04-29) — H0' direction confirmed (NO 24-48h calibrator over-confidence) + three H0'' sub-candidates filed

**Context.** S203 Track 5 closed with H0' framing: "WB's NO-side calibration is over-confident specifically in the 24-48h lead-time bucket." S204 inherited this framing and ran a calibration-shape test against the post-Day-2 cohort using `scripts/calibration_check.py`.

**Tooling commits.**
- `50eba02` extended `scripts/calibration_check.py` with `--since` / `--clean` flags + per-(side × lead-time) Brier breakdown. Closes the per-bucket-Brier gap that previously required ad-hoc SQL.
- `cf3059b` shipped `scripts/wb_bucket_concentration.py` as a tracked diagnostic (Lead 4(5c) follow-up — promotion of the S203 untracked one-shot).
- `27140e6` BOT_REGISTRY adjacents — heartbeat dict + dashboard v2 entries.
- `bacf4d1` calibration_check side discriminator now reads from `trade_events.ENTRY` (not `prediction_log.trade_side`), per S205 §2.8 verdict that prediction_log trade-marking is system-wide by-design asymmetric.

**Finding.** PIT-KS test on the `[0.9-1.0)` confidence bin rejected uniformity at significance — the calibrator reports high-confidence on the 24-48h NO trades, but realized outcomes diverge enough that the bin is empirically over-confident. Direction of H0' confirmed. Mechanism family (what variance source the calibrator is missing) was not yet identified — three sub-candidates filed for next-session diagnostic:
- (a) Resolution-boundary risk firing check — does `event_data->>'boundary_risk'` flag detect the failing cohort?
- (b) Rolling station-MAE feature — would 7-day rolling forecast MAE per station serve as a calibrator feature?
- (c) City-volatility quantile widening — does the calibrator widen its uncertainty on per-city historical volatility?

**Reframe-history note.** S204 is the second of three reframes of the WB Phase 6 root-cause framing across S203→S205. S203 framed it as "station noise" (falsified). S204 sharpened to "NO 24-48h calibrator over-confidence with bimodal city split" (direction confirmed; mechanism family unidentified). S205 falsified all three S204 sub-candidates (see §S205 below). The three-reframe-in-three-sessions pattern is itself a signal that the framing space has not yet been bounded — recorded so that future sessions can distinguish "diagnostic active, framing converging" from "diagnostic stuck in reframe loop."

**Disposition.** Diagnostic active; sub-candidates handed off to S205 for per-candidate falsification. The H0' confirmation is the load-bearing input for the S205 H0'' chain.

**Evidence of origin.** S204 session 2026-04-29. Five commits on master (no deploy this session — bundled into S205 deploy 2026-04-30). Tooling output preserved in `S204_H0PRIME_CALIBRATION_OUTPUT.txt`, `S204_BUCKET_CONCENTRATION_OUTPUT.txt`, `S204_INVEST1_CLUSTER_WINDOW.txt`, `S204_INVEST2_HIGH_CONF_BIN.txt` (all gitignored).

### S205 (2026-04-30) — H0'' sub-candidates all falsified; 6Q shipped as plan-deviation; Protocol 7 fired twice in one session

**Context.** Inherited S204's three H0'' sub-candidates as the diagnostic queue. Each was tested as a possible mechanism for the NO 24-48h calibrator over-confidence on the failing post-Day-2 cohort.

**H0'' sub-candidate falsification chain (all three eliminated):**

(a) **Resolution-boundary risk firing check (FALSIFIED).** Diagnostic at `scripts/s205_invest_boundary_risk.py` (untracked one-shot per current convention; promotion candidate filed in §S205 Hygiene Backlog item 5). Categorical findings: high-conf NO cluster trades had boundary_risk firing rate at ~0.000× of baseline (full elimination of br_true on the failing cohort); high-conf YES cluster trades had ~0.58× of baseline; within-cluster, when boundary_risk did fire on NO-side trades, the resolution outcome rate varied bidirectionally — no consistent loss correlation. The S121 dampener removal (no longer discounts confidence per [bots/weather_bot.py:2750](bots/weather_bot.py:2750)) is irrelevant to this failure mode because the flag wasn't firing on the failing cohort to begin with. Sub-candidate eliminated.

(b) **Rolling station-MAE pre-cluster signal test (FALSIFIED at per-station drill-down).** Diagnostic at `scripts/s205_invest_rolling_mae.py` (untracked one-shot). BLOCK A aggregate appeared to confirm: cluster losers' median rolling MAE was meaningfully higher than winners'. BLOCK B per-station drill-down inverted: the high-conf NO entries on the cluster window split across four stations (KDFW Dallas, KORD Chicago, CYYZ Toronto, LEMD Madrid). The station with the **highest** pre-entry rolling MAE (KORD) had its entries resolve as the model predicted; the station whose entries all resolved opposite (KDFW) had a *lower* pre-entry MAE than KORD. Aggregate "losers > winners on rolling MAE" was driven by station composition (LEMD, low MAE, pulled the winner-median down), not within-station signal. Mechanism interpretation: cluster failure is local/instantaneous (single-day, single-city), not predictable from prior-week station MAE.

(c) **City-volatility quantile widening test (FALSIFIED at per-station drill-down — same shape as (b)).** Diagnostic at `scripts/s205_invest_city_volatility.py` (untracked one-shot). BLOCK A aggregate appeared to confirm: cluster losers' median city-volatility was meaningfully higher than winners'. BLOCK B per-station: KORD had the **highest** 60d forecast-error std of the four cluster-window stations and its entries all resolved as the model predicted; KDFW had a lower std and its entries all resolved opposite to the model; LEMD had the lowest std and its entries resolved as predicted; CYYZ middle-of-pack and its entries resolved as predicted. Same station-composition aggregate-artifact pattern as (b).

**Protocol 7 firing pattern.** Both (b) and (c) produced aggregate-statistic apparent-confirmations that per-station drill-down inverted. Same shape as S200's "32 windowed ≠ Bug A cohort" cohort-redefining inversion. Two firings of the same Protocol 7 framings-vs-hypotheses inversion in one session day — see §Protocol 7 instance table for line items 11 and 12. **Generalized rule discovered (small-sample sub-rule):** on small samples (n<50 markets, or n<10 at any per-cell decomposition layer), aggregate ratios are concentration-dominated and require per-station/per-city/per-cohort-element decomposition before being treated as signal. The aggregate hides composition effects; per-station decomposes the composition. The bucket-concentration Protocol candidate at §Protocol candidates is extended with this evidence point — see candidate update.

**Pivot from feature-engineering to risk-reduction (commit `7c60938`).** With all three feature-engineering candidates eliminated for the NO 24-48h cohort, the operative interpretation is: this failure mode is not addressable by single-dimension calibrator-feature engineering at the level tested. Risk reduction is the right intervention. Phase 6Q shipped as a confidence-tail sizing dampener at [bots/weather_bot.py:3463](bots/weather_bot.py:3463) — smooth multiplicative taper above THRESHOLD on `_raw_size`, gated by `WEATHER_CONFIDENCE_DAMPENER_ENABLED` (default false). Composes with S154/S155B/S159 multiplicative dampener idiom. Five new tests in `TestS205Confidence6QDampener` pin the boundary; cross-bot blast radius is WeatherBot-only.

**Plan-deviation acknowledged.** 6Q's catalog prerequisite ("6D or 6E calibration improvement confirmed per threshold") and trigger threshold ("CRPS improvement ≥5% rel to baseline OR Brier ≥0.02 absolute, p<0.05 on ≥100 resolved predictions") were not met. The deviation rationale: feature-engineering candidates for the failing cohort were exhausted via H0'' chain, so the planned trigger pathway is moot for this specific cohort. The catalog row at [S172_CONSOLIDATED_PLAN.md](S172_CONSOLIDATED_PLAN.md) is annotated with the deviation and pointer back to this entry.

**6Q is mitigation, not root-cause closure (falsifiability clause).** Three reframes of the WB Phase 6 root-cause framing across S203→S205 — "station noise" (S203, falsified), "NO 24-48h calibrator over-confidence" (S204, direction confirmed; mechanism family unidentified), "exogenous to single-dimension weather-data observables, address via 6Q sizing" (S205). Three reframes in three sessions is a signal that the framing space has not yet been bounded. **Re-open conditions:** if 6Q is enabled (operator flag flip on `WEATHER_CONFIDENCE_DAMPENER_ENABLED`) and the high-conf-tail cluster failure pattern recurs at materially the same magnitude on the post-flag-flip window (measurement cadence: 24h/72h/168h per §S205 Hygiene Backlog item 6), the "feature-engineering exhausted" interpretation is falsified and the root-cause investigation re-opens. **Next-tier candidate classes for re-opened investigation:** (i) features that **jointly** condition on station + regime + lead-time (the H0'' sub-candidates tested in S205 each conditioned on a single dimension; joint conditioning was not tested); (ii) exogenous-to-weather mechanisms (market-resolution path, position-sizing asymmetry, YES/NO outcome systemic asymmetry) — these classes are unaddressed by 6Q. Until either condition is investigated or the root cause is separately identified, "WB Phase 6 root cause unidentified" is an open backlog item — see §S205 Hygiene Backlog item 1.

**Three carryover S204 hygiene items closed:**
- **station_id reconstruction feasibility:** path (a) backfill is feasible — `weather_forecasts` JOIN with composite index `(station_id, target_date)` ([base_engine/data/database.py:849-868](base_engine/data/database.py:849)); pattern mirrors [scripts/backfill_entry_metadata.py:130-181](scripts/backfill_entry_metadata.py:130). **Implementation deferred** without an explicit trigger condition — see §S205 Hygiene Backlog item 4 for path-(a)-vs-deferral decision tracking.
- **prediction_log.trade_executed/trade_side characterization:** by-design system-wide asymmetry — only EnsembleBot calls `mark_prediction_traded()` after place_order success at [bots/ensemble_bot.py:1816](bots/ensemble_bot.py:1816); WB/MB/EB call `insert_prediction_log()` without trade-marking parameters. The recommendation "leave as-is, document via JOIN to trade_events.ENTRY" is a documentation-only mitigation for a structural inconsistency. **The decision to leave as-is must not defer indefinitely** — see §S205 Hygiene Backlog item 3 for decision options (align all bots / CHECK constraint / schema comment).
- **MB Phase 7 calendar-gate recalibration:** verdict still INSUFFICIENT SAMPLE per `scripts/edge_verification.py` per [S205_MB_BOT_PNL_OUTPUT.txt:98-103](S205_MB_BOT_PNL_OUTPUT.txt:98) (cited bot_pnl.py CLEAN block: ENTRY count=321, EXIT count=132, RESOLUTION count=187, total realized $+1379.46). **Pace projection caveat:** the projection "EXIT + RESOLUTION → ~9 days to n=500" assumes EXIT+RESOLUTION sum is the gate denominator; this conflates partial-close EXITs with final-settlement RESOLUTIONs that may share the same underlying entry — a denominator-accounting issue structurally identical to the S195 SQL `--` bug class. The actual 500-trade gate denominator (EXIT count? distinct positions disposed? ENTRY count for fully-disposed positions?) requires verification against the Phase 7 gate text. Filed as §S205 Hygiene Backlog item 2 — projection date is provisional pending denominator verification.

**Memory rule refinement.** The S204 §7.4 rule about Protocol 11 applying to non-bot_pnl.py investigation-script outputs (own diagnostic scripts) was promoted from handoff-level to master-rule level — `feedback_verified_numbers_only.md` updated with a new paragraph after the S197 scope clarification. Rationale: a stop-hook fire during the H0''' synthesis demonstrated that handoff-level codification doesn't propagate forward to next-session prevention.

**Deploy.** All 18 commits backlogged since deploy `20260429_134741` were bundled into a single deploy this session: 1 from S202 (`3242c1e`), 12 from S203, 4 from S204, 1 from S205 (`7c60938`). 6Q is the first runtime-active commit since `20260429_134741` — gate default off, but logic path is reachable post-deploy. Operator flag-flip cadence is the next operational step (see §S205 Hygiene Backlog item 6).

**Evidence of origin.** S205 session 2026-04-30. 1 commit (`7c60938`) + 1 deploy. Six prod-runs preserved in `S205_LEAD1_BOUNDARY_RISK_OUTPUT.txt`, `S205_HOPRIME3_ROLLING_MAE_OUTPUT.txt`, `S205_HOPRIME3_LOSER_STATIONS.txt`, `S205_HOPRIME3C_CITY_VOLATILITY_OUTPUT.txt`, `S205_MB_BOT_PNL_OUTPUT.txt`, `S205_MB_EDGE_VERIFICATION_OUTPUT.txt` (all gitignored).

### S205 Hygiene Backlog

**1. WB Phase 6 root cause unidentified — open even after 6Q ships.** 6Q is risk-reduction mitigation, not root-cause closure. The high-conf-tail cluster failure mechanism remains uncharacterized after three sessions of reframes (S203 station noise → S204 NO 24-48h calibrator over-confidence → S205 exogenous to single-dimension weather observables). Re-open conditions per §S205 Corrections Log "Mitigation, not closure" paragraph. **Tracking criterion:** post-flag-flip window of 24h/72h/168h on `scripts/bot_pnl.py WeatherBot 168` — if the high-conf-tail loss pattern recurs at materially the same magnitude as the pre-flip baseline, the "feature-engineering exhausted" interpretation is falsified. Next-tier candidate class for re-opened investigation: (i) joint-conditioning features (station × regime × lead-time interactions); (ii) exogenous-to-weather mechanism candidates (market-resolution path, position-sizing asymmetry, YES/NO systemic asymmetry).

**2. MB Phase 7 gate-denominator verification.** The §S205 Corrections Log MB carryover paragraph projected ~2026-05-09 to n=500 using EXIT count + RESOLUTION count summed. EXIT and RESOLUTION are not equivalent disposition events — a position can have an EXIT (partial close) followed by a RESOLUTION (final settlement) on the same underlying entry, which would double-count dispositions if both are counted as separate "closed trades." The S195 SQL `--` bug fix corrected exactly this kind of denominator-accounting issue in `bot_pnl.py` block 4. **Verification needed:** re-read [S172_CONSOLIDATED_PLAN.md:441-446](S172_CONSOLIDATED_PLAN.md:441) Phase 7 gate text; confirm the 500-trade denominator semantics (EXIT count? distinct positions disposed? ENTRY count for fully-disposed positions?). Until verified, the ~2026-05-09 projection date is provisional. Action: ship the verification next session as a docs-only edit to the gate text (or this entry) clarifying the denominator. **RESOLVED 2026-04-30 (post-S205-close):** Verified — `scripts/edge_verification.py:147-148` queries `event_type IN ('RESOLUTION', 'EXIT') AND realized_pnl IS NOT NULL`; line 212 prints `Closed trades = RES + EXIT` (the `n_closed` value used for the v7 verdict at `v7_verdict()` `:80`). The Phase 7 gate denominator IS sum-of-(EXIT events + RESOLUTION events with realized_pnl) — consistent with the §S205 §2.6 projection assumption. The structural double-counting concern (a single position can have BOTH a partial EXIT event AND a RESOLUTION event on the residual size, both with non-null `realized_pnl`) is real but does not invalidate the gate-evaluation framework: the bootstrap at `:178-185` treats each event as a sample, which is statistically defensible if not ideal — the per-event `realized_pnl` is for the ITS portion of the disposition (EXIT for the closed chunk, RESOLUTION for the remainder), so each event represents a real partial-disposition outcome. Consequence: the projection date ~2026-05-09 holds as the date when `n_closed` per `edge_verification.py` semantics crosses 500. Whether to migrate the gate denominator to "distinct positions disposed" is a separate plan-level question not in scope for this hygiene closure. **DATE UPDATE 2026-05-02 (S208 close-review correction):** S207 close re-verified actual MB trade rate via `scripts/edge_verification.py MirrorBot --since 20260414_132211 --clean` (n=413 at S207-close timestamp; gap to 500 = 87 trades; rate ~9 trades/day at S207 era → projected n=500 around **2026-05-12**, ~3 days later than the original 2026-05-09 estimate). The S205 figure (May 9) reflected an earlier rate; the May 12 figure is current. Citation: AGENT_HANDOFF_S207_CLOSE.md §6 Lead 3.

**3. mark_prediction_traded() asymmetry decision — defer-not-indefinitely.** S205 §2.8 found by-design system-wide asymmetry: only EnsembleBot calls `mark_prediction_traded()` post-place_order at [bots/ensemble_bot.py:1816](bots/ensemble_bot.py:1816); WB/MB/EB do not. The recommendation "leave as-is, document via JOIN to trade_events.ENTRY" is a documentation-only mitigation for a structural inconsistency. Risk: any future analyst reading prediction_log directly assumes `trade_executed=true/false` is meaningful, gets misled, ships a wrong analysis. Pattern-precedent: S195 chain has multiple instances of column-meaning drift across bots producing exactly this failure mode. **Decision options (decide, don't defer indefinitely):** (a) align all bots to call `mark_prediction_traded()` post-place_order — consistent semantics, NOT NULL on trade-bearing rows; (b) add a CHECK constraint preventing future analysts from reading the columns under false assumptions; (c) add a comment to the `prediction_log` schema explicitly flagging the by-design NULL state for non-EnsembleBot rows. Estimated effort: (a) ~30 min × 3 bots; (b) ~15 min; (c) ~5 min. Decision is not session-blocking but should land in a session that touches `prediction_log` for any reason. **RESOLVED 2026-04-30 (post-S205-close):** Decision (c) shipped — schema-comment approach via class/method docstring extensions in `base_engine/data/database.py`: (i) `PredictionLog` class docstring at the model definition now flags `trade_executed`/`trade_side`/`trade_size`/`trade_price`/`trade_pnl` as populated only by EnsembleBot, by-design `False`/NULL on WB/MB/EB rows, and points to `trade_events.ENTRY` as canonical source for cross-bot trade-existence/side analysis; (ii) `insert_prediction_log()` docstring flags trade-marking columns as intentionally not parameters (defaults preserved by design); (iii) `mark_prediction_traded()` docstring flags EnsembleBot-only call site. Pure documentation change — zero behavior change, zero migration, zero CHECK constraint. Future analysts reading the model class or the write-path see the by-design asymmetry inline; the misleading-NULL trap is closed at the visibility level. Rationale for choosing (c) over (a)/(b): per CLAUDE.md "Preserve every function signature" and "Working code is sacred — fix only what is broken," (a) would change behavior across 3 bots for an observability-only concern, (b) would require a migration for a non-data-integrity check, (c) achieves the visibility goal at zero blast radius.

**4. station_id path-(a) vs deferral decision — make explicit.** S205 §2.7 confirmed `weather_forecasts.station_id` JOIN is feasible (~50-line script mirroring [scripts/backfill_entry_metadata.py:130-181](scripts/backfill_entry_metadata.py:130)) and the implementation pattern is documented. Current state: implementation deferred without an explicit trigger condition. Inverse risk: every future Phase 6 session that wants per-station historical analysis hits the same blocker until the backfill ships. **Decision rule:** if §S205 Hygiene #1 fires (post-flag-flip cluster pattern recurs) and the next-tier investigation requires joint-conditioning features (which need per-station historical data), then station_id backfill becomes a prerequisite — ship it before joint-conditioning candidates are tested. If Hygiene #1 does NOT fire (pattern resolved by 6Q), station_id backfill remains deferred indefinitely. Action: revisit this decision at the same time §S205 Hygiene #1 fires.

**5. Untracked investigation-script convention — codify or promote.** S204 created `scripts/s204_invest_*.py` (3 untracked one-shots); S205 created `scripts/s205_invest_*.py` (3 more — boundary_risk, rolling_mae, city_volatility). The "convention" of untracked one-shots is established but undocumented — fragile to a future session that does it differently or to a `git clean -fd` invocation. **Two viable resolutions:** (a) codify the convention — add a `CLAUDE.md` or `S172_CONSOLIDATED_PLAN.md` section stating "session-local investigation scripts (`s{N}_invest_*.py`) stay untracked by convention; promote to `scripts/wb_*.py` / `scripts/mb_*.py` only when a finding becomes recurring and the script needs tests"; (b) promote investigation scripts that produced consequential findings — S205's three all produced findings worth re-running on future cohorts; promote them as `scripts/wb_calibration_diagnostics.py` with consolidated tests, mirroring the S204 promotion of `wb_bucket_concentration.py`. Estimated effort: (a) ~10 min; (b) ~1-2h with tests. Decision is non-blocking but resolves a fragility class. **RESOLVED 2026-04-30 (post-S205-close):** Option (a) shipped — convention codified in §Operational Procedures (this plan, see "Session-local investigation scripts" bullet). Pattern: `scripts/s{NNN}_invest_*.py` stays untracked by convention; promote to a `scripts/<bot>_<feature>.py` tracked script with tests when the question becomes recurring across sessions OR the script's findings are worth re-running on future cohorts. Pattern precedent for promotion: S204 commit `cf3059b` promoted `wb_bucket_concentration.py` from S203 untracked one-shot. Option (b) (promote the specific S205 scripts to `wb_calibration_diagnostics.py`) deferred — none of the three has yet shown the "recurring across sessions" property; the convention now correctly captures the deferral as the default state, not as an open question.

**6. 6Q post-flag-flip measurement (operator action).** With 6Q committed and deployed (default off), the operational step is the flag flip + 24h/72h/168h `scripts/bot_pnl.py WeatherBot` measurement cadence. **Success criterion:** average loss size on the `confidence >= 0.90` bucket drops by >25% with total P&L within a 1-sigma band of pre-flip baseline. **Failure criterion (triggers Hygiene #1):** high-conf-tail loss pattern recurs at materially the same magnitude — falsifies "feature-engineering exhausted" interpretation, re-opens root-cause investigation. Operator-blocking; no session work needed until measurement results return.

**7. EB v2 flag-flip blockers (carried from §S202/S203/S204).** VPS .env `SIGNAL_REQUIRED_BOTS=EsportsBot,EsportsBotV2` extension; `BOT_ENABLED_ESPORTS_V2=true`; Phase 5v2-D substantive evaluation. Unchanged from prior sessions. Operator-blocking, not session-blocking.

### S206 (2026-04-30) — Post-close session-extension + push-back on review item #3

**Context.** Two docs-only commits landed post-S205-close (`00e903a` plan updates, `31ee299` hygiene #2/#3/#5 closures + 3 docstring extensions in `base_engine/data/database.py`) folding eight prior-review observations into the plan, closing 3 of 7 §S205 hygiene items, and codifying the untracked-script convention in §Operational Procedures. A subsequent review of S206 work raised three follow-ups; this entry records the resolution.

**Item 1 (plan-deviation discipline) IMPLEMENTED — filed as Protocol candidate.** S206 review observed that 6Q's plan deviation is recorded but no meta-rule exists for when deviations are acceptable vs require pre-approval. Filed as "Plan-deviation discipline" in §Protocol candidates with 3+ instance evidence base (S205 6Q without 6D/6E prerequisite + CRPS/Brier trigger; S195 SQL `--` fix `b82ad68` shipped before any catalog row mentioned the bug class; S201/S202 `bot_pnl.py` block 4 split `b125a5e` shipped before the formal Phase 6/7 gate revisions referencing it). Disposition: candidate, promote at fourth instance not caught by §Corrections Log discipline or operator sign-off.

**Item 2 (Protocol 7 instance count audit, rows 1-10) DEFERRED to §S206 Hygiene Backlog.** The current count "twelve inversions across eight sessions" was incremented from the prior "ten inversions across seven sessions" count without spot-checking rows 1-10 against actual session evidence. Rows 11+12 (S205 H0''' twin) were verified directly via primary-source files in the S205 session and confirmed in the S206 session-extension. Spot-check this session: row 1 ("S195 | SQL `--` parsing bug was scoped to a specific query") doesn't have an obvious matching prior-session testimony chain in the §S195 Corrections Log entry — possible drift. Full audit of rows 1-10 deferred — ~30-minute task, not urgent, not blocking.

**Item 3 (Protocol 7 row 12 attribution: "city-volatility tested vs inferred") PUSH BACK — primary evidence verified row 12 IS correct.** Reviewer asserted "City-volatility: not actually tested in S205. The S205 chain pivoted to 6Q without running (c)." Verification chain:
- `scripts/s205_invest_city_volatility.py` exists in working tree (untracked one-shot per S205 convention; 7,234 bytes, mtime 2026-04-29 23:00).
- `S205_HOPRIME3C_CITY_VOLATILITY_OUTPUT.txt` exists (untracked, 2,317 bytes, mtime 2026-04-29 23:00) — full prod-run output captured.
- Output file contents include BLOCK A (cluster aggregate: n=39, median 2.024, no_win_rate 0.7692), BLOCK B (per-station drill-down: KORD vol=4.999 won 100%, KDFW vol=3.166 lost 100%, LEMD vol=1.337 won 100%, CYYZ vol=2.024 won 100%), BLOCK C (within-cluster losers vs winners: 1.564× median ratio).
- S205 close handoff §2.4 documented this test with the same per-station evidence; §S205 Corrections Log entry §2.4 (this plan) propagated it accurately.
- Reviewer's claim source: misread of the prior-prior review's wording about "city-volatility quantile as the remaining cheap test, then pivot to features that span station + regime + lead-time joint conditioning" — that wording was forward-looking (post-6Q-fail scenario), not retrospective ("(c) wasn't tested in S205").

**Verdict on Item 3:** row 12 attribution is correct. City-volatility WAS independently tested with per-station drill-down; the falsification IS verified-falsified, NOT inferred-by-structural-similarity. No edit needed to row 12 or §S205 Corrections Log §2.4.

**Mechanism note.** This push-back is a Protocol 7-shape catch — inherited testimony (the S206 reviewer's claim, sourced from misread prior-review wording) verified against primary evidence (the actual output file) and inverted. Not added as a Protocol 7 instance row because the existing table is structured for hypothesis-inversions driving session remediation work, not review-claim corrections — but it IS a confirming instance that the discipline propagates beyond session-internal investigations. The reviewer's framing was inherited from a different chain than the standard prior-session-handoff path; primary-evidence verification works the same regardless of the chain's source.

**Deploy-gap observation.** Master is now 3 ahead of VPS post-this-commit; all three commits docs-only. Pattern: large clears followed by immediate accumulation — S205 cleared an 18-commit backlog; S206 immediately starts the next. The 3-commit gap resolves naturally with the next code commit per project convention. Filed as §S206 Hygiene Backlog item 2 to track gap size in each session-close handoff so the next deploy decision is calibrated to actual gap rather than per-session marginal.

**Evidence of origin.** S206 session 2026-04-30. Commits to date: `00e903a` (S204→S205 plan updates), `31ee299` (hygiene #2/#3/#5 closures + docstring extensions), [this commit] (S206 review resolutions + candidate filing). Primary-evidence verification of city-volatility test ran from existing untracked artifacts in the working tree (file existence + content read).

### S206 Hygiene Backlog

**1. Protocol 7 instance count audit (rows 1-10).** Rows 11+12 (S205 H0''' twin) verified directly against source files in S206. Rows 1-10 inherited from prior sessions without independent verification this session. Per the S206 review observation: "If the prior count was wrong (off by even 1), the new count is still wrong." Spot-check finding: row 1 framing "SQL `--` parsing bug was scoped to a specific query" lacks an obvious matching prior-session testimony chain in the §S195 Corrections Log entry — possible drift. **Action:** ~30-minute audit of each row 1-10 against its cited session's Corrections Log entry and primary artifacts; reconcile any drift. Doesn't block any work but the count is the protocol's evidence base, so accuracy matters. File any reconciliation as `### Protocol 7 instance audit (S206/S207)` correction in §Corrections Log. **RESOLVED 2026-04-30 (post-S206-close):** Audit executed. Findings — Row 1 had clear drift (framing did not match any of the four §S195 Phase A inversions); corrected in-place to match the 4th inversion ("Architectural wiring fix in commit `d67e03e` is sufficient" → falsified by upstream SQL `--` bug at `b82ad68`). Row 2 has minor session-attribution drift (the SIZE_INVARIANT FIX_AUDIT_CHECK→FIX_EMISSION rescoping is documented in §S195 Hygiene Backlog under "Day 3 prod investigation," not §S196; the inversion shape is correct, the session number may be off by one — audit deferred — `AGENT_HANDOFF_S196_CLOSE.md` IS readable in working tree per S207 audit, audit not yet run). Rows 3-7, 9, 10 verified against §S198/§S199 Hygiene Backlogs and MEMORY entries S200/S201/S202; all match. Row 8 (S202 plan-revision approach inversion) audit deferred — testimony lives in `AGENT_HANDOFF_S202_CLOSE.md` (readable in working tree per S207 audit, audit not yet run); shape matches MEMORY S202's "abstract-mechanism-first sequencing" framing. **Net audit result:** 1 clear drift fixed in-place, 1 minor session-attribution drift audit deferred (handoff readable, audit not yet run), 1 row audit deferred (handoff readable, audit not yet run), 7 rows verified. Twelve-inversions / eight-sessions count stays accurate (no rows added/removed, only Row 1 wording corrected). **Audit-derived sub-finding:** rows attributed to sessions whose primary testimony lives in non-committed handoffs (S196, S202 — gitignored from commit, but readable in working tree per S207 audit) CAN be audited via the working tree; the original "cannot be independently audited from plan alone" framing was scoped too narrowly. Future Protocol 7 row additions should embed the inherited-hypothesis source citation (§Corrections Log §SXXX line N, OR memory file path, OR explicit "testimony from working-tree-only AGENT_HANDOFF_SXXX_CLOSE.md") so retrospective audits remain anchored even if handoffs are later removed from the working tree. **Triple-blind verification (2026-05-01):** Audit conclusions re-tested via three independent passes per project triple-blind discipline (S193/S194/S195 evidence base). Row 1 fix: Pass A (§S195 Corrections Log Phase A diagnostics re-read) confirmed mapping to 4th inversion; Pass B (git log on `d67e03e` + `b82ad68`) confirmed commit subjects "Phase 4b RESOLUTION emission to first-class backfill" + "RESOLUTION INSERT SQL — `--` line comment swallowed tail" match Row 1 claims; Pass C (code inspection at `database.py:5653-5656` current location) confirmed `/* ... */` block-comment fix in place with self-documenting "S195: this comment was a `-- ` SQL line comment ... swallowed the closing `)`." Three passes converge → Row 1 fix VERIFIED. Row 2 disposition: Pass A re-confirmed §S195 Hygiene Backlog ambiguity ("Day 3 prod investigation"); Pass B (`git log -S` on plan content) found rescoping commit `689e37a` 2026-04-26 (S195 span), but Row 2 itself added in `d3f4a7e` 2026-04-28 (S199 Protocol 7 codification commit) attributing to S196 — so the S196 attribution may reflect work in the working-tree-only S196 handoff that synthesized the crisper "writer-side divergence" framing; Pass C confirmed §S195 Hygiene Backlog's diffuse "MirrorBot emission logic OR units-aware audit check" wording doesn't fully match Row 2's framing, supporting the hypothesis that synthesis happened in a separate session. "Minor drift, audit deferred — S196 handoff readable in working tree per S207 audit" disposition VERIFIED under triple-blind (S208 audit-correction: original "pending gitignored handoff" framing replaced with "audit deferred (handoff readable)" — see §S207 Hygiene Backlog #5). **Bonus finding (line-number staleness):** §S195 Corrections Log entry cites `database.py:5530` as the SQL `--` bug location; current code has the fix at `database.py:5653` — a 123-line drift accumulated from intervening edits (including S206's docstring extensions adding ~26 lines). Plan content with line-number citations has natural decay over time as the codebase grows. **Going-forward rule (S206 audit-derived extension to the sub-finding above):** Protocol 7 row entries and other plan-level citations should prefer structural location (function name, module name, branch identifier) over line numbers to reduce future audit friction. Existing line-number citations may stay as-is (historical accuracy at write-time is preserved as testimony); going-forward additions should use structural citations.

**2. Deploy-gap accumulation tracking.** Master is currently 3 ahead of VPS post-S206 commits; all docs-only. Pattern: large clears (S205 cleared 18-commit backlog) followed by immediate accumulation. The 3-commit gap will resolve naturally with the next code commit per the bundling convention. **Action:** track gap size in each session-close handoff under a dedicated field (e.g., "Master vs VPS: N ahead, M docs-only / K code") so the next deploy decision is calibrated to actual gap rather than per-session marginal. Filed as a discipline observation, not an alarm — the bundling-on-next-code-commit convention is correct; the tracking is to prevent silent re-accumulation to the prior 18-commit class size before anyone notices.

**3. Plan-deviation discipline (filed as Protocol candidate, see §Protocol candidates).** S206 review observation #1: deviations get §Corrections Log entries by current convention but no meta-rule formalizes when deviations are acceptable vs require pre-approval. Three documented instances pre-codification (S205 6Q, S195 SQL `--`, S201/S202 bot_pnl.py block 4 split). **CORRECTION (S207 audit, RESOLVED S208 commit — see §S207 Hygiene Backlog #4):** Verified count is 1, not 3. S195 SQL `--` fix `b82ad68` was discovery-driven Root Cause #1 of a silent-zero pathology — no catalog row with prerequisites existed to deviate from (per AGENT_HANDOFF_S195_CLOSE.md §1; S195's own plan-hygiene candidates were Protocols 7/8/9/10, NONE was "plan-deviation"). S201/S202 `bot_pnl.py` block 4 split `b125a5e` was labeled "fix: tooling-trust prerequisite" per AGENT_HANDOFF_S201_CLOSE.md Table — tooling fix that enabled further analysis, no violated phase-plan prerequisite. The §S206 framing retrofitted earlier sessions' bug fixes into "plan-deviation instances" — they don't fit the definition. Real count: 1 (S205 6Q). **Action (no immediate work):** monitor deviation pattern; promote candidate to numbered protocol when a **second genuine instance** ships AND the deviation isn't caught by either §Corrections Log discipline or operator sign-off (i.e., the existing soft conventions break). Until then, the soft conventions are working — codification is premature.

### S207 Hygiene Backlog

**1. WB throughput collapse — 948 shadowed vs 5 traded over 7d (NOT a "low base rate").** Investigation triggered by S207 attempt to baseline pre-flip activity for the 6Q dampener evaluation. Counts via `sudo journalctl -u polymarket-weather --since '7 days ago'` (executed 2026-05-02 ~14:35 UTC): **948 `weatherbot_shadow_entry` events** in 7d. Reason breakdown by `grep -oE 'reason=[a-z_]+'` (some shadow lines emit the field on suppressed-duplicate roll-ups, so reason counts sum higher than total events): 751 `slippage_cap_exceeded` (45%), 699 `depth_exceeded` (42%), 197 `negative_ev` (12%), 8 `no_liquidity` (<1%). Real entries from canonical source `scripts/bot_pnl.py WeatherBot 168` (executed 2026-05-02 14:35 UTC): **5 ENTRY, 1 EXIT, 15 RESOLUTION events**. **Shadow:trade ratio ≈ 190:1.** Top two reasons (slippage_cap + depth_exceeded together ~87%) are orderbook-constraint failures — WB is generating threshold-crossing signals, but its sized order would either move price beyond cap or exceed available depth. The S207 round-1 report framed this as "WB activity drought is normal" — that violated [feedback_no_dismiss_market.md](C:/Users/samwa/.claude/projects/C--lockes-picks-polymarket-ai-v2/memory/feedback_no_dismiss_market.md) ("When trades=0, NEVER say 'it's just the market'; investigate") and was caught by operator pushback. **Implication for 6Q dampener evaluation:** dampener reduces size on `confidence ≥ 0.85` opps with a 0.30× floor (per [bots/weather_bot.py:3463-3484](bots/weather_bot.py:3463)). Smaller size *may* clear slippage_cap/depth_exceeded gates — but only on high-conf opps, and only if 30% of normal size still fits the orderbook. If 30% of size still exceeds cap, dampener has zero behavior impact and the "evaluate at 24h/72h/168h" cadence is moot. **Action:** before treating the 6Q dampener evaluation as the headline open lead, characterize (a) whether WB sizing accounts for orderbook depth at all, OR (b) whether sizing is depth-blind and slippage_cap/depth_exceeded are catch-all kills for size-blind decisions. If (b), the upstream sizing fix is the lead — not the dampener evaluation. Cities most-shadowed (filterable by city= in same journal): Seattle (136), Madrid (129), Beijing (80), Wellington (59), London (55), Milan (37).

**2. S206 Lead 1 cadence is calibrated to wrong base rate (downstream of item 1).** S206 close §6 Lead 1 specifies: "Run scripts/bot_pnl.py WeatherBot 24 (or 72/168 depending on flip-time gap) and compare high-conf-tail outcomes against pre-flip baseline." 24h cadence assumes ≥ a few trades/day. Actual rate per item 1 is 5 entries / 7 days = ~0.7/day. 24h checkpoint will produce 0 trades typically (verified 2026-05-02 14:33 UTC: zero entries in window). **Action:** rewrite S206 Lead 1 in next close handoff to (a) note the ~5/week trade rate explicitly with citation to bot_pnl.py output, (b) project realistic measurement window as 3-4 weeks for ~15-20 high-conf trades, (c) hold the dampener post-flip measurement pending §S207 item 1 resolution since the dampener's behavioral effect is moot if upstream throughput is collapsed. The S207 round-1 report's revised projection of "3-4 weeks for ~15-20 high-conf trades" stands as the working estimate.

**3. S206 Lead 3 has Phase 5v2-D plan-vs-handoff doc gap.** S206 close §6 Lead 3: "EB v2 Phase 5v2-D evaluation (READY pending operator §5 actions)." Operator §5 action — `SIGNAL_REQUIRED_BOTS=EsportsBot,EsportsBotV2` — was completed in S207 (`/opt/pa2-shared/.env` edit at 2026-05-01 15:12 UTC, all three trading services restarted, env-var presence verified via /proc/PID/environ). Attempt to execute the eval via `scripts/edge_verification.py EsportsBotV2 --clean` (2026-05-02 14:34 UTC) returned `"EsportsBotV2 [clean]: NO CLOSED TRADES — cannot verify edge"` because `ESPORTS_V2_DRY_RUN=true` (preserved per S203 routing audit) gates `_execute_trades()` at [bots/esports_bot_v2.py:348-349](bots/esports_bot_v2.py:348). **Plan-vs-handoff conflict:** S172 §5v2-D (line 348+) reads "Sub-Phase 5v2-D: Paper Trading (Weeks 10-12+) — LIVE (dry_run, shadow). Gate 5v2-D: P(edge>0) ≥ 0.70 via edge_verification.py, accuracy >55%, wl_ratio >0.80, max drawdown <25%." — Phase 5v2-D IS the paper-trading phase whose gate is evaluated AFTER trades accumulate. The eval that gates ENTRY into 5v2-D (the DRY_RUN=false flip) is Gate 5v2-C: "Shadow accuracy >55%, Brier <0.25, CLV >+2% vs Polymarket, backtest-to-shadow drop <5%" — measurable on shadow predictions in `esports_predictions` + `prediction_log`, not on trades. S206 Lead 3's "Phase 5v2-D evaluation (READY)" framing conflates the two gates. **Action:** (a) rewrite Lead 3 in next close handoff as "EB v2 Gate 5v2-C evaluation on shadow predictions" with explicit threshold list (Brier <0.25, accuracy >55%, CLV >+2%, backtest-to-shadow drop <5%); (b) flag that **no script exists for Gate 5v2-C eval** — `scripts/calibration_check.py` (which already reads `prediction_log`) is the most plausible adaptation point but a v2-specific Brier/accuracy/CLV breakdown is not yet implemented; building Gate 5v2-C eval is a separate task and properly belongs in its own commit per "One fix per commit"; (c) **do NOT flip `ESPORTS_V2_DRY_RUN=false` until Gate 5v2-C is built, evaluated, and passes** — flipping without the gate means paper trading begins blind to whether shadow-prediction quality justifies it.

**4. Plan-deviation candidate count correction (S207 audit-derived). RESOLVED 2026-05-02 (S208, this commit).** S207 audit verified the §S206 Hygiene #3 framing "Three documented instances pre-codification (S205 6Q, S195 SQL `--`, S201/S202 bot_pnl.py block 4 split)" is inflated. Real count: 1 (S205 6Q only). S195 SQL `--` fix `b82ad68` was discovery-driven Root Cause #1 of a silent-zero pathology (no catalog row to deviate from per AGENT_HANDOFF_S195_CLOSE.md §1; S195's own plan-hygiene candidates were Protocols 7/8/9/10, NONE was "plan-deviation"). S201/S202 `bot_pnl.py` block 4 split `b125a5e` was labeled "fix: tooling-trust prerequisite" per AGENT_HANDOFF_S201_CLOSE.md Table — tooling fix, no violated phase-plan prerequisite. Neither fits the plan-deviation definition. **Action (S208 commit):** §S206 Hygiene #3 corrected with reasoning + §Protocol candidates Plan-deviation entry corrected with "(1 instance, audit-corrected from initial 3-claim)" + `feedback_plan_deviation_discipline.md` + MEMORY.md entry all updated; promotion threshold lowered from "4th instance" to "2nd genuine instance not caught by §Corrections Log discipline."

**5. Protocol 7 row 2/8 reclassification (S207 audit-derived). FULLY RESOLVED 2026-05-03 (S210 substantive audit).** §S206 Hygiene #1 row 2/8 disposition framed S196 and S202 handoffs as "gitignored handoff which would be the canonical record" / "plausible-but-unverifiable from plan content alone — testimony lives in gitignored AGENT_HANDOFF_S202_CLOSE.md." S207 verified both `AGENT_HANDOFF_S196_CLOSE.md` and `AGENT_HANDOFF_S202_CLOSE.md` exist in working tree (gitignored from commit per `.gitignore:147` but readable). Reclassified Row 2/8 dispositions from "blocked / pending / unverifiable" → "audit deferred (handoff readable, audit not yet run)" in S208. **S210 substantive audit (this resolution):** opened both handoffs, verified inversion attributions against handoff content. Row 2 (S196 attribution): VERIFIED — `AGENT_HANDOFF_S196_CLOSE.md:137` has the exact inversion (`"Audit checks are wrong per S185 reclassification" → "audit checks are correct, the data is anomalous"`); commit `689e37a` shipped the FIX_AUDIT_CHECK→FIX_EMISSION rescoping per the same handoff §3.7. Minor paraphrase imprecision noted: row's wording "units-mismatch" is a slight loose paraphrase of S196's actual inherited hypothesis "audit check itself is wrong/needs rewrite per S185 reclassification" — substantively correct, sharpening optional, not a DRIFT. Row 8 (S202 attribution): VERIFIED — `AGENT_HANDOFF_S202_CLOSE.md:146` has the exact inversion ("reviewer caught Step 4's 'lead suspect' candidate-list as inherited framing, restructured to abstract-mechanism-first"), exact match for the row's framing. Both rows' core attribution+inversion claims hold against underlying handoff content. **Action (S210 commit, docs bundle):** §S207 Hygiene #5 marked FULLY RESOLVED with verification result; §S208 Hygiene #9 (the deferred substantive audit) similarly RESOLVED with pointer here. Net audit count unchanged (12 inversions / 8 sessions); zero rows require correction. Optional Row 2 sharpening — replace "units-mismatch" wording with S196's actual phrasing — DEFERRED as low-priority next-session option, not blocking.

**6. EB v2 prediction_log resolution gap (LATENT, deferred).** S207 §2.5 finding: `prediction_log` has only sparse EB v2 entries / very few resolved while `esports_predictions` has the bulk of v2 shadow predictions / most resolved — cross-bot backfill from `esports_predictions` to `prediction_log` is sparse for v2. Not a Gate 5v2-C eval blocker (eval reads `esports_predictions` directly via `scripts/esports_v2_shadow_eval.py`). **Action (deferred):** investigate next time we need cross-bot prediction analysis on v2.

**7. Commit `scripts/esports_v2_shadow_eval.py`. RESOLVED 2026-05-02 (S208, separate commit).** Untracked utility script for Gate 5v2-C eval (Brier + Accuracy on `esports_predictions` mode='shadow' model_version='v2-trinity', actual_winner IS NOT NULL). Tested on VPS during S207. Script is READ-ONLY (no DB writes); two of four Gate 5v2-C metrics are computable today (Brier + Accuracy); CLV and backtest-to-shadow drop deferred per docstring (require additional inputs). **Action (S208 commit B):** stage and commit as a docs/utility commit (no test changes — utility CLI script with no callers).

---

**Evidence of origin.** S207 session 2026-05-01 → 2026-05-02; S208 audit-corrections commit 2026-05-02. .env edits at 2026-05-01 15:12 UTC (backup `/opt/pa2-shared/.env.bak.s207.20260501_151228`, edits: `WEATHER_CONFIDENCE_DAMPENER_ENABLED=true` appended, `SIGNAL_REQUIRED_BOTS` extended to `EsportsBot,EsportsBotV2`). All three trading services (`polymarket-weather`, `polymarket-mirror`, `polymarket-esports`) restarted, env vars verified via /proc/PID/environ. Background work: memory consolidation pass (MEMORY.md from large to compact, 4 new feedback/project files via background agent). Round-2 baselining triggered the discovery that Lead 1 cadence is mis-calibrated and Lead 3 framing conflates gates, with the WB throughput collapse as the root substantive finding. S208 closure: items 4, 5, 7 RESOLVED (audit corrections + script commit); item 6 left as latent observability gap.

---

### S208 (2026-05-02) — D1 staged WB min-trade flip + handoff-vs-reality finding (operator-approved, trigger pre-committed)

**Context.** §S207 Hygiene Backlog #1 documented WB throughput collapse with high-shadow-to-trade ratio over a 7-day window, ~87% of rejections being orderbook-constraint failures (`slippage_cap_exceeded` + `depth_exceeded`). S207 close §2.4 identified `WEATHER_MIN_TRADE_USD` floor as the root bottleneck.

**Handoff-vs-reality finding (S208 audit, Protocol 7-shape catch).** S207 close §2.4 stated: "Real bottleneck: `WEATHER_MIN_TRADE_USD = $5.0` (config/settings.py + weather_bot.py:3339)." Pre-flip verification of VPS `/opt/pa2-shared/.env` returned `WEATHER_MIN_TRADE_USD=15` at line 280. The handoff cited the source-code default ($5 from `os.getenv("WEATHER_MIN_TRADE_USD", "5.0")` at [config/settings.py:845](config/settings.py:845)) as if it were the running value; the actual running override is $15 via .env. Inherited-testimony-vs-primary-evidence pattern — same Protocol 7 shape that has fired 12 times across 8 sessions. The 6Q dampener evaluation framing in §S207 close §1 / Lead 1 should be re-read with this correction; "$5 floor" interpretations may need adjustment.

**D1 staged action (operator-approved 2026-05-02 with B-path: stage $15→$5 first).** Two-stage flip with sample-based rollback trigger between stages. No calendar timer ("day timer is nonsense" — operator).

**Stage 1 — $15 → $5 (THIS COMMIT'S ACTION).**
- Apply: VPS `/opt/pa2-shared/.env` line 280 `WEATHER_MIN_TRADE_USD=15` → `=5`; restart `polymarket-weather`.
- Trigger (directional + slippage/fees, NOT bootstrap-significance): P(edge>0) on the $5–$15 marginal cohort, evaluated over **≥30 closed trades minimum** as a directional signal.
- Pass: P(edge>0) ≥ **0.40** → proceed to Stage 2.
- Fail: P(edge>0) < 0.40 → revert to $15 via `sudo cp /opt/pa2-shared/.env.bak.s208.<TS> /opt/pa2-shared/.env && sudo systemctl restart polymarket-weather`.

**Stage 2 — $5 → $1 (PENDING, awaits Stage 1 pass).**
- Apply: VPS .env edit `WEATHER_MIN_TRADE_USD=5` → `=1`; restart.
- Trigger: P(edge>0) on the $1–$5 marginal cohort, evaluated over ≥30 closed trades minimum.
- Pass: hold at $1.
- Fail: revert to $5.

**Why staged + trigger pre-committed.** Per §S207 close §7 #2 reviewer recommendation: D1 should not become a second "shipped without prerequisite" instance after S205 6Q. Trigger committed before action = audit trail; staged flip = bounded blast radius (3× smaller per stage given $15 actual baseline vs $5 assumed). Operator approved B-path after handoff-vs-reality finding surfaced.

**Trigger threshold rationale (S208 close-review extension).** The 0.40 P(edge>0) threshold on a 30-trade sample is a directional gate, not a bootstrap-significance test. Rationale: (a) the marginal cohort is added exposure on smaller-trade-size signals that may be out-of-distribution for the existing edge model, so it warrants a stricter bar than the full-bot Phase 7 PROCEED threshold (0.30); (b) +0.10 strictness margin above PROCEED reflects the cohort's smaller sample size + out-of-distribution risk; (c) the 30-trade gate is a directional-signal threshold (revert if direction is clearly negative), NOT a confidence-interval verdict — P(edge>0) on n=30 has wide CIs and would flip on the next 5 trades, which is why the trigger pairs the directional gate with a slippage/fees economics check. Operator override path: if direction is borderline, inspect slippage+fees economics on the cohort — if slippage+fees consume >50% of edge at the $5–$15 level, revert regardless of P(edge>0); if economics are healthy, hold and accumulate more samples before deciding. **Caveat (S208 rebuttal-review meta):** the +0.10 strictness margin is default-conservative — the actual OOD risk magnitude on the marginal cohort is unquantified pending data. If post-flip data shows the cohort behaves in-distribution (similar Brier / accuracy as the full-bot cohort on a matched sample), 0.40 is over-strict and operator may revisit. If post-flip data shows the cohort is materially OOD, 0.40 may be insufficient and operator may want to tighten further. Document the OOD-risk reading in the trigger evaluation.

**6Q-D1 interaction in post-flip measurement (S208 close-review extension).** 6Q (`WEATHER_CONFIDENCE_DAMPENER_ENABLED=true` since S207) reduces position size on confidence ≥ 0.85 signals to a 0.30× floor. Post-D1, the marginal $5–$15 cohort has two distinct populations: (i) **naturally-small signals** — low-edge or thin-orderbook trades that previously couldn't clear the $15 floor; (ii) **6Q-dampened-into-cohort** — high-conf signals dampened from larger sizes into the $5–$15 range. Different cohort compositions, different evaluation criteria. Stage 1 measurement should bucket the 30-trade sample by these two populations and report P(edge>0) per bucket plus the aggregate. If 6Q-dampened-into-cohort is dominant, the cohort is testing 6Q's dampener calibration more than D1's edge — note the conflation in the verdict and weight the naturally-small bucket as the load-bearing D1 signal.

**Irreversibility-exposure window (S208 rebuttal-review meta).** The 2-stage staging reduces but does NOT eliminate the irreversible-loss exposure window. Between Stage-1-flip and trigger-evaluation (or Stage-2-flip and trigger-evaluation), losing trades on the marginal cohort cannot be undone — only future trades from the cohort can be prevented by revert. The 30-trade-minimum gate creates a bounded but non-zero exposure window: at the pre-flip ~5/week WB throughput, 30 trades is ≈6 weeks of marginal-cohort-trade-flow under worst case (and faster if D1 unlocks throughput as intended). Operator should be aware that revert is preventative, not curative — accumulated losses on the cohort during the trigger-accumulation window are sunk. This is why the trigger pairs directional + slippage/fees economics: a directional reading at trade ~10 in the window can prompt early-revert if economics are clearly losing, even before the formal 30-trade trigger fires.

**Why not a plan-deviation.** D1 is a discovery-driven fix to §S207 Hygiene #1's throughput-collapse finding; no S172 catalog row with prerequisites is being violated. Same shape-class as S195 SQL `--` fix and S201/S202 block 4 split (both rejected from plan-deviation count in S208's earlier audit-corrections commit). Filed in §Corrections Log for the audit trail and for the trigger / rollback discipline.

**Evidence of origin.** S208 session 2026-05-02. Operator approval in conversation: initial sign-off "do it but just wait to get 30 trades day timer is nonsense"; B-path selection "b" after handoff-vs-reality finding ($5 → $15 actual) was surfaced. Stage 1 .env edit + service restart applied this session; Stage 2 deferred until Stage 1 trigger sample (30 closed $5–$15 cohort trades) accumulates and passes.

### S208 Hygiene Backlog

**1. Config drift audit — first run (S208, RESOLVED via `scripts/config_drift_audit.py` 2026-05-02).** Triggered by S208's S207-handoff-vs-reality finding ($5 source-code default vs $15 actual on `WEATHER_MIN_TRADE_USD`). Audit covers source-code `os.getenv` defaults vs running VPS `.env` runtime values across 814 unique env keys in code + 117 keys in .env. Output captured in untracked `S208_CONFIG_DRIFT_AUDIT_OUTPUT.txt` (per session-local script convention §Operational Procedures). Categorized counts: DRIFT 57, REDUNDANT 37, NO-DEFAULT-MISSING 44, ENV-ORPHANS 18, MULTI-DEFAULT 10, ALIGNED 671. Most drifts are intentional operational tuning (bot enable/disable, DB pool, slippage caps, sizing); three clusters worth dispositioning (items 2-4 below). **Action SHIPPED:** [scripts/config_drift_audit.py](scripts/config_drift_audit.py) committed `8495671`; output captured.

**2. Training-disabled cluster (S208, INTENT UNCLEAR — operator review needed).** Eight env vars all set to "training off" patterns:
- `AUTO_RETRAIN_ON_DEGRADATION`: code `true` → .env `false`
- `RETRAIN_INTERVAL_HOURS`: `6` → `999999` (effectively never)
- `ESPORTS_RETRAIN_INTERVAL_HOURS`: `24` → `999999`
- `TRAIN_ON_PAPER_TRADES`: `true` → `false`
- `TRAIN_ON_PREDICTION_LOG`: `true` → `false`
- `USE_PRICE_HISTORY_TRAINING_FALLBACK`: `true` → `false`
- `TRAINING_MIN_VOLUME`: `500` → `0`
- `TRAINING_RECENCY_LAMBDA`: `1.0` → `3.0`

Models effectively frozen in production. Paper-trading-mode freeze is plausible but not documented; could equally be accumulated drift from a prior session that disabled training and never re-enabled. **Action:** operator confirms intentional vs unintentional. If intentional, add comment-block to .env explaining the freeze. If unintentional, file as a separate corrections entry with re-enable plan. **RESOLVED 2026-05-02 (S208, same session).** Operator review concluded "unintentional, fix it." Six wholesale-disable flags re-enabled per §S208 Corrections Log entry "Training cluster re-enable" (commit `f586a2c`). Three tuning-class flags (USE_PRICE_HISTORY_TRAINING_FALLBACK, TRAINING_RECENCY_LAMBDA, TRAINING_MIN_VOLUME) initially deferred to next session as §S208 Hygiene #8, then resolved same-session via revert to defaults per §S208 Corrections Log entry "Deferred-3 training tunings reverted to defaults" (commit `680e6b2`). All 9 training-related flags now at code defaults or operationally sensible values; training is actively learning from `paper_trades` and `prediction_log` on a 24h cadence across all 3 active trading bots.

**3. MirrorBot calibration toggle off (S208, INTENT UNCLEAR — operator review needed).** `MIRROR_USE_CALIBRATION`: code `true` → .env `false`. 7K (Venn-ABERS calibration for MirrorBot) is shipped per §Phase 7 catalog and the toggle is off. Could be pending validation, accidentally off, or deliberately disabled while observing behavior under no-calibration baseline. **Action:** operator confirms intent; either document the gating reason in a §Corrections Log entry, or flip the toggle. **RESOLVED 2026-05-02 (S208, same session, bundled into training cluster re-enable).** `MIRROR_USE_CALIBRATION` flipped to `true` in commit `f586a2c` along with the other training-cluster flags. MirrorBot now applies Venn-ABERS calibration to copy signals.

**4. Memory drift on MirrorBot whale gate (S208, RESOLVED via memory edit).** Three-way disagreement surfaced by audit:
- Code default at [config/settings.py:456](config/settings.py:456): `100.0`
- VPS .env override: `5`
- MEMORY.md "MirrorBot S132+ traps" entry said: `$50 min whale gate`

Reality is $5 (the .env override is what takes effect at runtime). Memory was stale. **Action SHIPPED:** MEMORY.md trap entry corrected to "$5 min whale gate (per .env override; code default $100; memory was stale at $50 per S208 audit)."

**5. ENV-ORPHANS — 18 keys in .env with no `os.getenv` reference in code (S208, partial action).** Categorized:
- **Confirmed dead:** `BOT_ENABLED_ESPORTS_SERIES=true` — EsportsSeriesBot was merged into EsportsBot per `56c1d70 refactor(esports): merge EsportsSeriesBot into EsportsBot + batch handoff docs`. **RESOLVED 2026-05-02 (S208, same session, operator-approved).** Line removed from VPS .env via sed; 3 trading services restarted; backup at `/opt/pa2-shared/.env.bak.s208-deadflag.20260502_181516`. See §S208 Corrections Log entry "Dead env flag removal" below.
- **Likely false-orphans (read via `getattr(settings, ...)` rather than direct `os.getenv`):** `ESPORTS_CONFLUENCE_MIN`, `ESPORTS_MAX_EDGE`, `MIRROR_FORCE_EXIT_HOURS`, `MIRROR_USE_CONFORMAL`, `RISK_MIN_PRICE_WEATHERBOT`, `RISK_MIN_VOL_*BOT`, `WEATHER_EXIT_MIN_EDGE`, `WEATHER_MID_LIFE_EXIT_ENABLED`. **Action (deferred):** see item 7 (audit script extension).
- **Hardcoded URLs / infra meta:** `POLYMARKET_*_API`, `POLYMARKET_WS`, `LIGHTSAIL_INSTANCE_NAME`, `MIRROR_ML_MODEL_PATH`, `MIRROR_ML_QTABLE_PATH` — base URLs are likely hardcoded in client code (not env-overridable) and infra metadata. Keep.

**6. MULTI-DEFAULT internal code inconsistencies (S208, audit-derived).** Same env key has different defaults in different files (or even within the same file). Most concerning:
- `RUN_INGESTION_MAX_SECONDS`: `'900'` at [config/settings.py:63](config/settings.py:63) vs `'2400'` at [config/settings.py:741](config/settings.py:741) — same file, different defaults. Whichever is read last wins per Python attribute-assignment order; this is brittle.
- `INGESTION_TIMEOUT_SECONDS`: `'300'` at [config/settings.py:64](config/settings.py:64) vs `'600'` at [config/settings.py:738](config/settings.py:738) — same pattern.
- `LLM_PROBABILITY_CACHE_TTL`: `'3600'` at config/settings.py vs `str(DEFAULT_CACHE_TTL)` at base_engine/features/llm_probability.py:30.
- `DATABASE_URL`: 15 references across the codebase with mixed defaults (`''`, `None`, no-default-subscript). Most are scripts that should fail-fast if `DATABASE_URL` isn't set; the `''` defaults silently mask missing-config bugs.
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`: similar mixed-default pattern.

**Action:** file as code-hygiene investigation for a future session; not session-blocking. Recommended fix: pick one default per key and propagate; require config/settings.py to be the single source of default truth for cross-cutting keys.

**7. Audit script extension — `getattr(settings, ...)` follow-through (S208, deferred). RESOLVED LOCALLY 2026-05-03 (S210); production verification deferred pending VPS-hang investigation.** Current audit only catches direct `os.getenv("KEY")` and `os.environ.get("KEY")` calls. Bots commonly read via `getattr(settings, "KEY", default)` — same env var ultimately, but the read indirection was invisible to the AST walker. **Action (S210 code commit):** added `extract_getattr_settings_calls()` AST pattern matcher to [scripts/config_drift_audit.py](scripts/config_drift_audit.py) (+58 lines, pure additive — existing `extract_getenv_calls` untouched). Pattern matches `getattr(ast.Name("settings"), ast.Constant(str), [default])`; intentionally narrow to first-arg = `settings` to avoid false positives from `getattr(self, ...)` etc. Both extractors yield into the same refs collection in `collect_code_defaults`, so categorize logic unchanged. New unit test file `tests/unit/test_config_drift_audit.py` (14 tests, all pass) covering both extractors against synthetic snippets — true positives, dynamic-key skip, no-default form, non-`settings` first-arg skip, plus integration test confirming combined `collect_code_defaults` sees both shapes. Local impact: +64 keys recovered (was 814 unique code-referenced; now 878). Local ENV-ORPHAN count: 7 → 6 (small local .env, 41 keys); **VPS production count (S208 baseline = 18) NOT captured this session** — the script hung on the live VPS run. **Why the RESOLVED tag is qualified (per S210 review):** local-only verification means we don't know whether the same +64 keys recover on production. Hypothesis for the hang: doubled file I/O (each .py file is read+parsed once per extractor; codebase-walk × 2 multiplied by VPS I/O latency). Possible fix: refactor `collect_code_defaults` to read+parse each file once and walk the AST twice, instead of re-reading per extractor. **Out-of-scope for this fix (filed as next backlog):** direct-attribute access pattern `settings.KEY` (without getattr) — agent reports ~21 places across 5 files; same false-positive class but distinct AST shape. File as §S210 Hygiene Backlog item if priority warrants, otherwise defer.

**8. Three training-tuning flags deferred to next session for review (S208, operator-deferred via §S208 training-cluster re-enable action below).** §S208 Hygiene #2 surfaced 8 training-related "off" flags. Six are wholesale disables and were re-enabled this session (see §S208 Corrections Log entry "Training cluster re-enable" below). Three are tuning-class items — not strict disables — and were deferred for review next session:
- `USE_PRICE_HISTORY_TRAINING_FALLBACK=false` (vs default `true`) — fallback data source. Currently off; could be intentional if primary sources (paper_trades + prediction_log) are sufficient. **Question:** is the fallback ever needed in the current data-availability state? Re-enable for safety net or keep off if redundant?
- `TRAINING_RECENCY_LAMBDA=3.0` (vs default `1.0`) — aggressive recency weighting. Current value triple-weights recent data. **Question:** does the regime volatility justify it? Or revert to 1.0 for stable training?
- `TRAINING_MIN_VOLUME=0` (vs default `500`) — admits all data including thin markets. **Question:** does max-data outweigh noise? Or restore the 500 floor?

**Action (next session):** review each on its own merits; either (a) document the current value as intentional with rationale in a §Corrections Log entry, or (b) revert to default. Bundle as one .env edit when decided. **RESOLVED 2026-05-02 (S208, same session) via option (b) for all three.** See §S208 Corrections Log entry "Deferred-3 training tunings reverted to defaults" below for the per-flag reasoning + settings.py inline-comment citations.

**9. Protocol 7 row 2/8 substantive audit (DEFERRED, S208 close-review carry). RESOLVED 2026-05-03 (S210, see §S207 Hygiene #5).** Filed by the §S207 Hygiene #5 close-review clarification. S210 substantive audit executed: Row 2 (S196 attribution) VERIFIED — handoff `AGENT_HANDOFF_S196_CLOSE.md:137` confirms inversion + commit `689e37a` shipped FIX_AUDIT_CHECK→FIX_EMISSION rescoping. Row 8 (S202 attribution) VERIFIED — handoff `AGENT_HANDOFF_S202_CLOSE.md:146` confirms plan-revision approach inversion. Zero rows require correction; Protocol 7 instance count (12 inversions / 8 sessions) holds. Optional Row 2 paraphrase sharpening ("units-mismatch" → S196's actual "audit check itself is wrong per S185 reclassification") deferred as low-priority next-session option. Full audit detail in §S207 Hygiene #5 above.

**11. Audit-corrections-bundling pattern — codification deferred per operator review (S208 close-question Q6).** S208 commit `177ac75` is a worked example: 4 plan corrections + 2 hygiene additions, all of one logical type (close-review incorporation), shipped as one docs-only commit. Cleaner than 6 separate commits; preserves "one fix per commit" because the corrections share a common §Corrections Log entry. Operator decision (close-question Q6): "Add to handoffs to review" — defer formal codification to §Operational Procedures; carry the proposal to next-session handoffs for review against accumulated examples. **Action:** future session-close handoffs should explicitly evaluate whether this session's commit pattern matched the bundling shape, accumulating evidence for or against codification. Promotion-to-§Operational-Procedures threshold: 3+ session-close handoffs reviewing the pattern in agreement that it should be codified, OR 2+ examples in a single session that fit the pattern. Until then, the pattern is informal and applied case-by-case.

**10. Plan-deviation candidate completeness sweep (DEFERRED, S208 close-review carry). RESOLVED 2026-05-03 (S210 sweep, 0 additional instances).** S208 commit `4ea441e` corrected over-counting (3 → 1 instance) but did not run a positive sweep for under-counting. **S210 sweep (this resolution):** commit-by-commit across S198 (1), S199 (3), S200 (3), S201 (2), S202 (3), S203 (12) = 24 total commits. All fall into established carve-outs: discovery-driven bug fixes with no catalog-row prerequisite (`5d0eefb` SHADOW_ENTRY auto-heal, `5f55668` side discriminator, `acf0950` EB family-union), tooling-trust prerequisite fixes (`ee76994` windowing, `b125a5e` block 4 split [established baseline], `009fbc6` block 5 windowing), discovery-driven data-hygiene closures (`50b892a` orphan backfill, `db57194` pre-ledger backfill), plan-implementing constants (`f100674` directly encodes Phase 5v2-D thresholds the plan prescribes), and pure docs (everything else). Zero commits ship code violating documented §Phase 5v2-C/D / §Phase 6 / §Phase 7 / §DAY 1/2 prerequisites, gates, or sequencing rules. **Action (S210):** candidate stays at 1-instance + monitor-for-2nd disposition. The two ruled-out negative cases (S195 `b82ad68`, S201/S202 `b125a5e`) plus the S210 clean sweep across S198–S203 confirm: the candidate's specificity rests on those negative cases; the soft conventions (§Corrections Log discipline + operator sign-off) are working — codification remains premature pending a second genuine instance.

**Evidence of origin.** S208 session 2026-05-02. Audit script committed `8495671`. Output captured in `S208_CONFIG_DRIFT_AUDIT_OUTPUT.txt` (untracked, session-local per §Operational Procedures convention). Triggered by §S208 Corrections Log "Handoff-vs-reality finding" — the WEATHER_MIN_TRADE_USD $5-vs-$15 drift exposed the broader question "what other config drift exists?", answered comprehensively by this audit. Findings serve as third-instance evidence base for promoting "Hierarchical infrastructure verification" Protocol candidate to Protocol 13 (next commit). Items 9 and 10 added by the S208 close-review processing — both deferred to S209 with stated time estimates.

### S208 (2026-05-02) — Training cluster re-enable: 6 flags shipped, 3 deferred (operator-approved)

**Context.** §S208 Hygiene Backlog #2 (audit-derived via `scripts/config_drift_audit.py`) flagged 8 training-related env vars consistently set to "training off" patterns. Operator review (2026-05-02): unintentional drift from a paper-trading-mode freeze that was never re-enabled. Action: re-enable training across all bots; defer 3 tuning-class items to next session for review (§S208 Hygiene #8).

**Flags re-enabled this session (6).** All applied via VPS `/opt/pa2-shared/.env` edit; the 3 active trading bot services restarted.

| Flag | Pre | Post | Effect |
|---|---|---|---|
| `TRAIN_ON_PAPER_TRADES` | `false` | `true` | Paper trade outcomes feed training data |
| `TRAIN_ON_PREDICTION_LOG` | `false` | `true` | Resolved markets feed training data via `prediction_log` |
| `AUTO_RETRAIN_ON_DEGRADATION` | `false` | `true` | Models self-retrain when performance degrades |
| `RETRAIN_INTERVAL_HOURS` | `999999` | `24` | Daily retrain cadence (default 6 = 4×/day; 24 = 1×/day, conservative for paper) |
| `ESPORTS_RETRAIN_INTERVAL_HOURS` | `999999` | `24` | Esports-specific daily retrain cadence |
| `MIRROR_USE_CALIBRATION` | `false` | `true` | 7K Venn-ABERS calibration active for MirrorBot signals |

**Deferred to next session (3).** See §S208 Hygiene Backlog #8. Tunings, not disables — `USE_PRICE_HISTORY_TRAINING_FALLBACK`, `TRAINING_RECENCY_LAMBDA`, `TRAINING_MIN_VOLUME`.

**Why operator-approved.** §S208 Hygiene #2 surfaced the cluster as INTENT UNCLEAR; operator review concluded "unintentional, fix it." Re-enable comes with the rollback path documented below — same .env-backup pattern as S208 D1 Stage 1 flip earlier this session.

**Rollback procedure (single command).**
```
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  'sudo cp /opt/pa2-shared/.env.bak.s208.<TS> /opt/pa2-shared/.env && \
   sudo systemctl restart polymarket-weather polymarket-mirror polymarket-esports'
```
where `<TS>` is the timestamp suffix of the backup created when the .env edit was applied.

**Verification.** Post-restart, env vars present in `/proc/<PID>/environ` for each restarted service; calibration_check.py / model retraining cycles should resume on the new cadence (24h interval).

**Why not a plan-deviation.** Discovery-driven fix to §S208 Hygiene #2 (audit-found drift). No S172 catalog row with prerequisites is being violated; an unintentional disable is being un-done. Same shape-class as the D1 staged WB min-trade flip earlier this session; same shape as the S195 SQL `--` fix and S201/S202 block 4 split (both rejected from plan-deviation count in §S208 audit-corrections commit `4ea441e`).

**Evidence of origin.** S208 session 2026-05-02. Operator approval in conversation: "do all 6 and add 3 to next handoff." Audit findings filed in §S208 Hygiene Backlog #2 (count from `scripts/config_drift_audit.py` first run). VPS .env edit + service restart applied this session; backup created with timestamp suffix `<TS>` (recorded in shipped command output).

### S208 (2026-05-02) — Deferred-3 training tunings reverted to defaults (operator-approved)

**Context.** §S208 Hygiene Backlog #8 filed 3 training-tuning flags as deferred-to-next-session, post the §S208 training-cluster re-enable action. Operator review (2026-05-02): "do deferred items." Each flag was reviewed against `config/settings.py` inline documentation; no rationale was found for the current operational override; reverted to code defaults.

**Flags reverted this session (3).** All applied via VPS `/opt/pa2-shared/.env` edit; the 3 active trading bot services restarted.

| Flag | Pre | Post | Rationale (settings.py inline comment) |
|---|---|---|---|
| `USE_PRICE_HISTORY_TRAINING_FALLBACK` | `false` | `true` | [config/settings.py:303](config/settings.py:303). Fallback data source — safety net when primary sources (`paper_trades` + `prediction_log`) hiccup. No code-comment rationale for disabling; no operational rationale found in plan / handoffs. |
| `TRAINING_RECENCY_LAMBDA` | `3.0` | `1.0` | [config/settings.py:983-986](config/settings.py:983) documents: "1.0 = moderate recency bias. 0.0 = uniform weights (disabled). 2.0+ = strong recency." 3.0 was "strong"; no documented rationale. With retraining now on a 24h cadence (per S208 training-cluster re-enable), recent-data weighting is partially captured by the cadence itself. |
| `TRAINING_MIN_VOLUME` | `0` | `500` | [config/settings.py:350-351](config/settings.py:350) documents: "Exclude low-volume markets from training (reduces thin-market noise bias)." Setting to 0 admits all markets including thin ones with noisy outcomes. Default 500 is the documented noise filter. |

**Closes §S208 Hygiene Backlog #8.** All three previously-deferred items dispositioned in this session. No further follow-up needed for the training cluster.

**Why operator-approved.** §S208 Hygiene #8 framed this as operator-pending: review each on its own merits; either (a) document the current value as intentional with rationale, or (b) revert to default. Operator chose (b) for all three after seeing settings.py comments document the reasoning behind the defaults.

**Rollback procedure (single command).**
```
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  'sudo cp /opt/pa2-shared/.env.bak.s208-deferred.<TS> /opt/pa2-shared/.env && \
   sudo systemctl restart polymarket-weather polymarket-mirror polymarket-esports'
```
where `<TS>` is the timestamp suffix of the backup created when the .env edit was applied.

**Verification.** Post-restart, env vars present in `/proc/<PID>/environ` for each restarted service.

**Why not a plan-deviation.** Discovery-driven fix to §S208 Hygiene #8 (audit-found drift, operator-reviewed). Same shape as the training-cluster re-enable earlier this session. No S172 catalog row with prerequisites being violated; an unintentional drift from defaults is being un-done.

**Evidence of origin.** S208 session 2026-05-02. Operator approval in conversation: "do deferred items." Audit findings filed in §S208 Hygiene Backlog #8 (count from `scripts/config_drift_audit.py` first run). VPS .env edit + service restart applied this session; backup created with timestamp suffix `<TS>` (recorded in shipped command output).

### S208 (2026-05-02) — EB v2 verdict response: PARK (operator-approved)

**Context.** S207 §2.5 produced Gate 5v2-C eval (via `scripts/esports_v2_shadow_eval.py`) verdict = FAIL on EB v2 shadow predictions. Three response paths offered in S207 §6 Lead 4: (a) park (`ESPORTS_V2_DRY_RUN=true` stays, no further work); (b) investigate model (multi-week ML work); (c) hold and re-evaluate in 1-2 months.

**Decision (operator-approved 2026-05-02).** Path (a) — park. `ESPORTS_V2_DRY_RUN=true` remains (already set; no .env change needed this session). EB v2 stays in shadow mode generating predictions to `esports_predictions`; no live trading. Cheapest path with no further session work.

**Rationale.** Gate 5v2-C verdict was FAIL with negative BSS (model worse than climatological baseline) on n=389 across CS2 + LoL — structural, not a sample-size artifact. Path (b) is multi-week ML investigation work; path (c) hopes for natural improvement without retraining (unlikely to produce a different verdict). Path (a) preserves the option of revisiting if operator priorities change while incurring zero ongoing engineering cost.

**Closes §S207 Lead 4.** EB v2 verdict-response decision recorded. The `BOT_ENABLED_ESPORTS_V2=true` flag stays (bot continues to generate shadow predictions for future Gate 5v2-C re-evaluation if revisited); only the `ESPORTS_V2_DRY_RUN=false` flip is parked indefinitely.

**Evidence of origin.** S208 session 2026-05-02. Operator approval in conversation: "1 a" (response to Q1 in close-question round). No VPS state change required — `ESPORTS_V2_DRY_RUN=true` was already set; the parking decision is a non-action acknowledgment.

### S208 (2026-05-02) — Dead env flag removal: BOT_ENABLED_ESPORTS_SERIES=true (operator-approved)

**Context.** §S208 Hygiene Backlog #5 flagged `BOT_ENABLED_ESPORTS_SERIES=true` in VPS `/opt/pa2-shared/.env` line 233 as dead config — EsportsSeriesBot was merged into EsportsBot per `56c1d70 refactor(esports): merge EsportsSeriesBot into EsportsBot + batch handoff docs`. Verified by audit script: not referenced from any `os.getenv` / `os.environ.get` / `os.environ[]` call in the codebase.

**Action shipped (this session).** VPS .env line 233 deleted via `sudo sed -i "/^BOT_ENABLED_ESPORTS_SERIES=/d"`. All 3 trading services restarted; new PIDs weather=2977235, mirror=2977244, esports=2977240, all `active`. Backup at `/opt/pa2-shared/.env.bak.s208-deadflag.20260502_181516`.

**Closes §S208 Hygiene Backlog #5 partially.** The confirmed-dead flag is removed. The 8 likely-false-orphans (`ESPORTS_CONFLUENCE_MIN`, `ESPORTS_MAX_EDGE`, `MIRROR_FORCE_EXIT_HOURS`, `MIRROR_USE_CONFORMAL`, `RISK_MIN_PRICE_WEATHERBOT`, `RISK_MIN_VOL_*BOT`, `WEATHER_EXIT_MIN_EDGE`, `WEATHER_MID_LIFE_EXIT_ENABLED`) remain — they need the audit script extension (§S208 Hygiene #7) to walk `getattr(settings, ...)` reads before being verified as truly dead vs read-via-indirection. The 5 hardcoded URLs / infra-meta keys (`POLYMARKET_*_API`, `POLYMARKET_WS`, `LIGHTSAIL_INSTANCE_NAME`, `MIRROR_ML_MODEL_PATH`, `MIRROR_ML_QTABLE_PATH`) keep — these are intentional non-os.getenv keys.

**Rollback.** `sudo cp /opt/pa2-shared/.env.bak.s208-deadflag.20260502_181516 /opt/pa2-shared/.env && sudo systemctl restart polymarket-weather polymarket-mirror polymarket-esports`. Restores the `=true` line at position 233. Unlikely to be needed (line had no consumer in code).

**Evidence of origin.** S208 session 2026-05-02. Operator approval in conversation: "2 a" (response to Q2 in close-question round). VPS .env edit + service restart applied this session.

### S208 (2026-05-02) — In-message Protocol 11 strip on first leads-table (recovered same-message)

**Pattern.** S208 opened with a "next steps" framing turn that proposed leads ordered by readiness. The first version of the leads table inlined three trading-performance figures sourced from the S207 close handoff (a shadow-to-trade ratio, a Brier score with sample size, and a MirrorBot trade count). Operator stop-hook fired immediately ([feedback_verified_numbers_only.md](C:/Users/samwa/.claude/projects/C--lockes-picks-polymarket-ai-v2/memory/feedback_verified_numbers_only.md) — Protocol 11 inline-citation requirement). Agent stripped the figures and re-issued the same-message before any artifact shipped.

**Why this is filed despite zero artifact propagation.** The S203/S204/S205/S208 chain has now had at least four Protocol 11 catches. The catch-latency is improving (S203 was post-commit retroactive; S204 was pre-commit; S207 was during-drafting; S208 is in-message before send). The cognitive pattern producing the violation in drafting is NOT decreasing — only the catch latency is. Filing this entry per the §S203-codified discipline: "in-session strips don't require an entry — but the published-in-message strip is structurally between 'pre-commit caught' and 'committed-and-retroactively-edited'; worth filing for the pattern's progression to be visible across sessions."

**Mechanism.** Both inlined figures were paraphrased from S207 close handoff sources (which themselves had bot_pnl.py / edge_verification.py citations, but those citations didn't propagate through the paraphrase). This is the same Protocol 11 [feedback_p11_docstring_paraphrase.md](C:/Users/samwa/.claude/projects/C--lockes-picks-polymarket-ai-v2/memory/feedback_p11_docstring_paraphrase.md) loophole — paraphrasing prior-session sourced figures without re-citing.

**Going-forward rule (S208 close-review proposal — operator-blocking codification).** Add to §Session Template or CLAUDE.md: "When framing options or surfacing leads, run a Protocol 11 self-check on numerical content BEFORE sending the message. If any number is from a non-`bot_pnl.py` source, or paraphrased without inline citation, strip before sending." This is the prevention layer upstream of the catch-and-recover pattern. Pattern-precedent: §Protocol 11 itself was codified after similar accumulation; the in-message-strip is a tighter-loop catch that the §Session Template should bake in.

**Evidence of origin.** S208 session 2026-05-02. The first leads-table turn produced the strip; operator stop-hook output identified the three specific figures; agent re-issued without numbers. No artifact (commit, plan edit, memory file) shipped with the figures. This entry documents the pattern's progression for cross-session visibility, per §S203 hygiene #13 discipline.

### S208 (2026-05-02) — Close-review processing: 4 plan edits + 2 hygiene carries (review-incorporation pattern)

**Context.** Mid-session close-review surfaced 5 substantive findings (1 calendar-date staleness, 1 framing-conflation, 3 D1-trigger-justification gaps) plus 1 missing audit-trail entry (in-message Protocol 11 strip — see entry above). Bundled into one docs-only commit per the proposed audit-corrections-bundling pattern (see §S208 Hygiene #X / next-session proposals).

**Edits this commit.**
1. **§S205 Hygiene #2 calendar date update.** Original "~2026-05-09" annotated with the S207-close re-verification: actual MB trade rate at S207 close gives projection of ~2026-05-12. Citation: AGENT_HANDOFF_S207_CLOSE.md §6 Lead 3.
2. **§S207 Hygiene #5 disposition clarification.** Original "RESOLVED 2026-05-02 (S208)" was the framing-correction (reclassified Row 2/8 from "gitignored/blocked" to "audit deferred (readable)"). Substantive audit (actually reading both handoffs) is still pending. Re-marked "RESOLVED-FRAMING + Substantive audit DEFERRED — see §S208 Hygiene #9."
3. **§S208 D1 trigger threshold rationale.** Added principled foundation for the 0.40 P(edge>0) threshold: stricter than full-bot Phase 7 PROCEED (0.30) because the marginal cohort is added exposure on smaller-trade-size signals that may be out-of-distribution; +0.10 strictness margin reflects the cohort's smaller sample + OOD risk; trigger is a directional gate not a CI verdict; operator override path via slippage/fees economics check.
4. **§S208 D1 6Q-D1 interaction note.** Added to Stage 1 measurement plan: bucket the 30-trade sample by (i) naturally-small signals vs (ii) 6Q-dampened-into-cohort signals; the two populations have different evaluation criteria; if 6Q-dampened is dominant, the cohort tests 6Q's calibration not D1's edge.

**Two hygiene carries added (§S208 #9 + #10).** Protocol 7 row 2/8 substantive audit (deferred from §S207 #5 framing-fix); plan-deviation candidate completeness sweep (positive-sweep counterpart to the over-counting correction in S208 commit `4ea441e`).

**Pattern observation.** This commit is a worked example of the audit-corrections-bundling pattern: 4 related plan edits + 2 hygiene additions, all of one logical type (close-review incorporation), shipped as one docs-only commit. Cleaner than 6 separate commits; preserves "one fix per commit" because the corrections are all of one logical type sharing a common §Corrections Log entry. Filed for §Operational Procedures codification (next session).

**Evidence of origin.** S208 session 2026-05-02 close-review processing. Source: prepared close-review document (paste-in from operator). Verified deploy-gap claim (reviewer said 7-or-8; actual was 14 at commit-time per `git log --oneline 7c60938..HEAD | wc -l`); verified §S205 Hygiene #2 still said "~2026-05-09" pre-edit; verified §S207 Hygiene #5 RESOLVED-claim was framing-only.

### S209 (2026-05-02) — EB v2 Gate 5v2-C eval script bug fix + verdict reversal (SUPERSEDES §S208 PARK rationale)

**Context.** Operator picked path (b) "investigate" at S209 open, overriding the §S208 PARK decision (line 1619-1629). Investigation of EB v2 model identified a Brier-formula bug in `scripts/esports_v2_shadow_eval.py` that systematically inverts outcomes for predictions where `p_model < 0.5` (i.e., when the model picks team_b). The S207 §2.5 verdict ("FAIL: Brier 0.2684 > 0.25, BSS −0.12") that grounded the §S208 PARK decision was produced by this bug.

**Bug detail.** Per `esports_v2/shadow/match_converter.py:121`, `p_model = P(team_a wins)`. The eval script computed Brier as `(p_model - 1{predicted_winner == actual_winner})²`. When `p_model < 0.5` (predicted_winner = team_b), `1{predicted_winner == actual_winner} = 1{team_b won} = 1 - 1{team_a won}`, so the script's outcome label is inverted relative to the event the forecast is for. Compounding bug: BSS climatology denominator used `accuracy*(1-accuracy)` instead of `P(team_a wins)*(1-P(team_a wins))`. Both bugs push the result toward FAIL/negative-BSS in aggregate.

**Action shipped (this session).** `scripts/esports_v2_shadow_eval.py` corrected:
1. Brier formula now derives `y_a = 1{team_a won}` from the joint of `(predicted_winner == actual_winner, p_model > 0.5)`, then computes `(p_model - y_a)²`.
2. BSS climatology denominator now uses `mean(y_a) * (1 - mean(y_a))`.
3. Singleton-only verdict added alongside full-set output. Per `esports_v2/model/conformal.py`, only singleton predictions produce trades (non-singletons abstain via the conformal filter); the gate decision should be evaluated against the trade-eligible cohort.

**Verdict reversal.** Corrected script run on VPS at 2026-05-02 23:27 UTC, output captured in `S209_EB_V2_CORRECTED_VERDICT.txt` at repo root (untracked, gitignore convention for session output files). Singleton-only overall verdict shifts to **PASS on the two measurable Gate 5v2-C metrics** (Brier + Accuracy). LoL singletons pass with margin; CS2 singletons remain marginal. Full prediction set (singleton + non-singleton) is also closer to passing than the original FAIL framing suggested.

**Implications for §S208 PARK decision (line 1619-1629).** The PARK rationale ("Gate 5v2-C verdict was FAIL with negative BSS (model worse than climatological baseline)") is no longer valid at the cohort that matters (singleton-only, the trade-eligible set). The original FAIL framing was an artifact of the script bug, not a model property. The PARK decision was operator-approved on 2026-05-02 in good faith on the wrong numbers; that operator approval does not extend to the corrected verdict. **Lead 4 needs re-evaluation by the operator** with the corrected output, not re-decision on the original FAIL framing.

**What this commit does NOT change.** `BOT_ENABLED_ESPORTS_V2=true` and `ESPORTS_V2_DRY_RUN=true` remain unchanged on VPS. EB v2 stays in shadow mode. The flip of `ESPORTS_V2_DRY_RUN=false` (entry into Phase 5v2-D paper trading) is the operator-blocking decision that follows from the corrected verdict — not shipped this session. CLV (Gate 5v2-C metric 3) and backtest-to-shadow drop (metric 4) remain unmeasurable; the corrected script does not change that, and a complete Gate 5v2-C verdict requires those data sources be wired (§S209 investigation report Issue 7).

**Why not a plan-deviation.** Bug fix to production-evaluation tooling that produced a wrong verdict; no S172 catalog row prerequisites violated by the fix itself. Same shape as S195 SQL `--` fix (`b82ad68`) — production tooling produced wrong output, fix is the discovery-driven correction. The §S208 PARK decision was operator-approved at the time on the available (wrong) numbers; the supersession is operator-decision territory, not a plan-deviation by the agent.

**Evidence of origin.** S209 session 2026-05-02. Operator instruction to investigate (path b) replaced the §S208 PARK choice. Investigation report delivered in conversation surfaced the script bug. Bug verified by walking through the formula on `p_model < 0.5` cases (script's `(p_model - 1{correct})²` vs correct `(p_model - 1{team_a won})²`); corrected output captured in `S209_EB_V2_CORRECTED_VERDICT.txt`. Citation chain for any downstream metric: `scripts/esports_v2_shadow_eval.py` post-fix at this commit's SHA, reading `esports_predictions WHERE mode='shadow' AND model_version='v2-trinity' AND actual_winner IS NOT NULL`.

---

### S210 (2026-05-02) — Three meta-hygiene codifications: Protocol 14 + pre-send P11 self-check + audit-corrections-bundling

**Context.** Operator-approved batch processing of three S209-pending meta-hygiene items (one Protocol candidate at promotion-ready, two §Operational Procedures candidates with sufficient precedent). All three were carried in the S209 close handoff §0 batched-decision block as the first applied instance of the §Operational Procedures candidate "Operator-decision-queue batching" (§Protocol candidates section). Operator response shape (single-message batch resolution) validates the batching pattern; codification of the batching candidate itself remains pending one full handoff-gap-closure cycle per its own threshold (see §Protocol candidates entry).

**1. Protocol 14 — Aggregate-statistics bucket-concentration check.** Promoted from §Protocol candidates at 2 evidence points (S185 6O lead-time + S205 H0''' twin) per operator approval. The candidate text already self-described as "promotion-ready" at this count; the small-sample sub-mandate added at S205 strengthens the rule's specificity. Rule lands as a numbered protocol with the same Mandate / Sub-mandate / Practical aid / Out-of-scope / Evidence of origin / Numbering note structure as Protocol 13. Recommended landing slot from candidate text was either "Protocol 4d" or "next available numeric slot after Protocol 12" — the latter is followed (matching Protocol 13's S208 slot choice). **3-day review checkpoint 2026-05-05 per operator request.** At checkpoint, evaluate whether codification has produced an in-the-wild catch (intended effect) or sat dormant (still soft observation, not a problem). If no relevance triggers in 3 days, defer next review to 2026-05-12 (one-week cadence). Review marker also lives in the next session's handoff doc (when S210 closes).

**2. Pre-send Protocol 11 self-check codified in §Session Template (item 4 of "Shared execution pattern").** Operator approved S209 §0 #3 codification proposal. The principle is already in CLAUDE.md as Forbidden Pattern #10 (codified S208); this S210 step operationalizes it as a 4-step verification ladder run BEFORE sending any message with numerical content: (1) `bot_pnl.py`-source → cite per-mention; (2) non-`bot_pnl.py` canonical (config file:line, captured eval output, `prediction_log` query in tracked script) → cite the file:line or filename inline; (3) paraphrased from prior session → re-cite the original source or strip; (4) operator memory / verbal claim → verify against current state or strip. The catch-latency improvement chain (S203 post-commit retroactive → S204 pre-commit → S207 during-drafting → S208 in-message before send → S210 pre-send self-check) is now structurally bounded — each step removes a class of cognitive-pattern noise, with the pre-send step sitting upstream of the catch-and-recover loop. Forbidden Pattern #10 names the rule; the §Session Template step makes it executable.

**3. Audit-corrections-bundling pattern codified in §Operational Procedures.** Operator approved S209 §0 #4 codification proposal. The pattern's promotion threshold per its own filing (§S208 Hygiene Backlog #11 "3+ session-close handoffs reviewing the pattern in agreement, OR 2+ examples in a single session that fit the pattern") is now exceeded by the two-session precedent: S208 commit `177ac75` (4 plan corrections + 2 hygiene additions, all close-review-incorporation, one §Corrections Log entry) and S209 commit `5ecda0f` (eval script Brier formula + BSS denominator + singleton-only verdict + §S209 Corrections Log entry, all one logical cluster). The codified rule preserves "one fix per commit" by scoping bundling to corrections-of-one-logical-type sharing a common §Corrections Log narrative, with explicit out-of-scope clauses for bug fixes, behavior-changing config tunings, and cross-cutting refactors.

**Why bundle these three corrections into one commit (worked example of audit-corrections-bundling).** All three are documentation-only changes of one logical type (codification of meta-hygiene candidates accumulated through the S205-S209 chain). Each carries operator approval; each has a written candidate or proposal text; each lands at a recommended slot per the candidate filing. Shipping them as three separate commits would inflate the master-vs-VPS gap by three commits with no operational gain — the §Corrections Log narrative is shared (the three candidates were carried as one §0 batch in the S209 handoff, processed as one operator response, and codified together). This commit IS the bundling pattern's third in-the-wild instance, the first applied AFTER codification.

**Evidence of origin.** S210 session 2026-05-02. Operator approval delivered in chat in response to S209 close handoff §0 batched-decision block (with one clarifying question on Protocol 14's elaboration, answered same-message). Carried items: Protocol 14 promotion (S209 §0 #2), pre-send P11 self-check (S209 §0 #3 / S208 carry), audit-corrections-bundling (S209 §0 #4 / S208 carry). Codification text drafted from the candidate filings already in §Protocol candidates and §S208 Hygiene Backlog #11 — no new framing or scope expansion this commit, only promotion and operationalization of pre-existing candidate text.

---

### S210 (2026-05-02) — EB v2 trade flip (a-unrestricted) + wiring fix prerequisite (operator-approved)

**Context.** Operator selected Lead 4 sub-option (a-unrestricted) per the §S209 batched-decision block: flip both LoL and CS2 to live paper trading. Operator explicitly waived the rollback-trigger pre-commit recommended by the §S209 handoff §6 Lead 2 (a-unrestricted) entry — preferring a 1-week handoff-review checkpoint as the kill-switch instead of a code-level auto-disable. Operator instruction: "have all games working and running we can review later, verify they have all they need to succeed."

**Pre-flip verification surfaced 5 wiring gaps.** The pre-flip "verify they have all they need to succeed" check identified that EsportsBotV2 was not registered in BotBankrollManager defaults or risk_manager bot-name checks — flipping `ESPORTS_V2_DRY_RUN=false` without code fixes would have produced trades blocked or undersized:

1. **`base_engine/risk/bankroll_manager.py:46`** — `_DEFAULT_BOT_CONFIGS` missing `EsportsBotV2`. Fell to `_FALLBACK_CONFIG` (capital=$1K, max_bet_usd=$100, max_daily_usd=$500) instead of the EsportsBot parity config ($20K / $300 / $10K). VPS `BOT_BANKROLL_CONFIG` JSON only has `EsportsBot`/`MirrorBot` keys; no override.
2. **`base_engine/risk/risk_manager.py:306`** — `bot_name == "EsportsBot"` check excluded v2; bot would use `MIN_CONFIDENCE_THRESHOLD` default (~0.55) instead of VPS `ESPORTS_MIN_CONFIDENCE=0.20`. Most predictions would fail the place_order confidence floor.
3. **`base_engine/risk/risk_manager.py:419`** (fast-path) — `bot_name in ("EsportsBot", "EsportsLiveBot")` excluded v2; bot would use global `RISK_MAX_TOTAL_EXPOSURE_USD` ($500 default) summed across ALL bots instead of bot-isolated. Total exposure across MirrorBot/EsportsBot/etc. already exceeds $500, so v2 trades would be hard-blocked.
4. **`base_engine/risk/risk_manager.py:484`** (DB-fallback path) — same exclusion as #3.
5. **`base_engine/risk/risk_manager.py:675/704`** — `_HARD_STOP_DEFAULTS` missing v2 (fell to 0.30 vs EsportsBot's 0.50); `_prefix_map` missing v2 (would fall to `"ESPORTSV2"` env-var prefix lookups which don't exist). Aliased to `"ESPORTS"` so v2 reads the same `ESPORTS_*` env vars already on VPS.

**Action shipped (commit `354c84e`).** Pure additive — added `EsportsBotV2` alongside `EsportsBot` in all 5 places. No behavior change for any other bot. All 2281 unit tests pass. Deployed via `deploy.sh` to VPS as release `20260502_202509`. Atomic symlink swap + service restart per §Operational Procedures rollback path. Pre-S203 routing-audit work (commit `... ` per §S203 EB routing audit) had already pre-staged `SIGNAL_REQUIRED_BOTS=EsportsBot,EsportsBotV2` on VPS — that prerequisite was in place.

**Action shipped (VPS .env edit, NOT git-tracked).** `/opt/pa2-shared/.env` edited via `sed -i` to flip `ESPORTS_V2_DRY_RUN=true → false`. Backup preserved at `/opt/pa2-shared/.env.s210-pre-flip` for rollback. `polymarket-esports` service restarted at 2026-05-03 00:38:02 UTC (PID 2994963). Cold-start expected ~5.5 min before first trade-eligible scan cycle.

**CS2 risk acknowledgment (per §S209 Critical Reminder #5).** CS2 singletons fail the Brier gate post-fix in all-time and 14d windows; marginal in 7d (per `S209_EB_V2_RECENCY_*` outputs at repo root). Trading on CS2 carries explicit expected-loss risk relative to the model's per-game gate. The (a-unrestricted) decision accepts this risk on the operator's authority. LoL singletons pass the gate cleanly across all windows; trading risk on LoL is the model's normal calibration error, not a known gate failure.

**1-week review checkpoint (2026-05-09 per operator request).** At checkpoint, evaluate per-game P&L + edge realization vs the corrected verdict's predictions. Decision space: continue both, restrict to LoL only (revert to a-restricted shape via per-game env var), park back to shadow (full revert). Review marker also lives in [memory/feedback_eb_v2_trade_flip_review.md](C:/Users/samwa/.claude/projects/C--lockes-picks-polymarket-ai-v2/memory/feedback_eb_v2_trade_flip_review.md) and the next session's close handoff.

**Rollback path.** Code: `git revert 354c84e && bash deploy/rollback.sh`. .env: `ssh ubuntu@VPS 'sudo cp /opt/pa2-shared/.env.s210-pre-flip /opt/pa2-shared/.env && sudo systemctl restart polymarket-esports'`. Note: rollback restores DRY_RUN=true; existing positions opened in the live-trading window remain in `positions` table and reconcile via standard exit flows.

**Why not a plan-deviation.** Wiring fix is a discovery-driven correction to enable a Phase 5v2-D operation that the catalog row already requires (the §Phase 5v2-D rows assume EB v2 trades through the same place_order gates as EsportsBot). No catalog row prerequisites violated; the missing wiring was an artifact of EsportsBotV2 being a newer bot added after the original 5-bot risk_manager wiring. Flip itself is operator-approved per Lead 4 (a-unrestricted) selection.

**Lead 7 codification (operator-decision-queue batching) — promoted to §Operational Procedures.** S210 satisfied the candidate's codification trigger: S209 close handoff §0 batched 4 operator-blocking items, operator processed all 4 in a single batched response, and S210 shipped 3 codifications + 1 trade-flip in the same session. The S209 filing's "≥5 items" threshold was refined to "≥4 items" based on S210's working example (pattern worked at 4, not just 5+). New §Operational Procedures bullet "Operator-decision-queue batching" added; §Protocol candidates entry replaced with promoted-item pointer for audit trail.

---

### S210 (2026-05-03) — Plan-deviation positive sweep + Protocol 7 row 2/8 audit + audit-script `getattr(settings,...)` extension (3 carries closed)

**Context.** S210 second-half closed three remaining backlog items in parallel via 3 sub-agents (Lead 4 sweep, Lead 5 audit, Lead 6 code extension), bundled per the audit-corrections-bundling pattern codified earlier this session. Operator instruction "all 4 in parallel then show work" — the parallel-agent execution shape is itself a worked example of the §S172 plan's "use Agent for independent research/code work" Operational Procedure.

**1. Lead 4 — Plan-deviation positive sweep S198–S203 (§S208 Hygiene #10 closure).** Commit-by-commit sweep across the 5-session diagnostic chain. **Result: 0 additional genuine instances.** All 24 commits fall into established carve-outs (discovery-driven bug fixes, tooling-trust prerequisite fixes, discovery-driven data-hygiene closures, plan-implementing constants, pure docs). The candidate stays at 1-instance (S205 6Q) + monitor-for-2nd disposition. The two ruled-out negative cases (S195 `b82ad68`, S201/S202 `b125a5e`) plus this clean sweep confirm the candidate's specificity rests on those negative cases. Soft conventions (§Corrections Log discipline + operator sign-off) are working — codification remains premature. See §S208 Hygiene #10 for per-commit detail.

**2. Lead 5 — Protocol 7 row 2/8 substantive audit (§S207 Hygiene #5 closure, §S208 Hygiene #9 closure).** Read both `AGENT_HANDOFF_S196_CLOSE.md` and `AGENT_HANDOFF_S202_CLOSE.md` directly; verified each row's inversion attribution. **Both VERIFIED.** Row 2 (S196 attribution): inversion + FIX_AUDIT_CHECK→FIX_EMISSION rescoping confirmed at handoff §3.7 + commit `689e37a`. Row 8 (S202 attribution): plan-revision approach inversion confirmed at handoff §3 Hygiene #7. Zero rows require correction; Protocol 7 instance count (12 inversions / 8 sessions) holds. Minor Row 2 paraphrase imprecision noted ("units-mismatch" loose vs S196's actual "audit check is wrong per S185 reclassification") — sharpening optional, not a DRIFT. Full detail in §S207 Hygiene #5.

**3. Lead 6 — `scripts/config_drift_audit.py` extension (§S208 Hygiene #7 closure, separate code commit).** Added `extract_getattr_settings_calls()` AST pattern matcher (+58 lines, pure additive); existing `extract_getenv_calls` untouched. Pattern intentionally narrow to `getattr(settings, KEY, [default])` — refused to match `getattr(self, ...)` etc. New unit test file `tests/unit/test_config_drift_audit.py` (14 tests, all pass). Local impact: +64 keys recovered (was 814 unique code-referenced; now 878). VPS production count vs the S208 baseline of 18 ENV-ORPHANS not captured this session — script hung on the live VPS run; can be captured next session when bandwidth allows. Out-of-scope filed as next backlog item: direct-attribute access pattern `settings.KEY` (without getattr) covers ~21 more places — same false-positive class but distinct AST shape.

**Why bundle Leads 4, 5, 7 (docs) into one commit but ship Lead 6 (code) separately.** Per the audit-corrections-bundling pattern codified in S210: Leads 4 + 5 + 7 are corrections-of-one-logical-type (plan-state corrections from completed audit/sweep/codification work) sharing this §Corrections Log narrative. Lead 6 is a code change to a tracked utility script with new tests — distinct logical type per the bundling pattern's out-of-scope clause. Two commits is the right cardinality.

**Action shipped:**
- **Code commit (Lead 6):** `scripts/config_drift_audit.py` (+58 lines) + `tests/unit/test_config_drift_audit.py` (new, 184 lines, 14 tests pass).
- **Docs commit (Leads 4 + 5 + 7):** §S207 Hygiene #5 → FULLY RESOLVED with verification result; §S208 Hygiene #7 → RESOLVED with code-commit pointer; §S208 Hygiene #9 → RESOLVED with §S207 Hygiene #5 pointer; §S208 Hygiene #10 → RESOLVED with sweep result; §Protocol candidates plan-deviation entry → augmented with S210 sweep result; §Operational Procedures + §S210 Corrections Log entries (this content); §Protocol candidates operator-decision-queue batching entry → replaced with promoted-pointer.

**Evidence of origin.** S210 session 2026-05-03. Operator instruction "all 4 in parallel then show work" triggered parallel agent dispatch for Leads 4/5/6 + direct work for Lead 7 codification. Agent reports: Lead 4 found 0 additional instances; Lead 5 verified both Row 2 and Row 8 attributions against handoff content; Lead 6 produced commit-ready diff with all tests passing locally. Process observation: parallel-agent dispatch on independent research+code tasks (Leads 4/5/6) plus direct codification (Lead 7) closed 4 backlog items in roughly the time of one sequential task — confirms the §Operational Procedures "Use Agent for independent research" guidance at scale.

---

### S210 (2026-05-03) — EB v2 6th wiring gap discovered post-flip review (volume gate)

**Context.** Operator-requested S210 review surfaced a structural concern: "EB v2 trading status reported but no first-trade verification is shown." Verification query against `paper_trades WHERE bot_name='EsportsBotV2'` returned 0 rows ~17 hours post-flip. Investigation surfaced a 6th wiring gap not caught by the pre-flip 5-place sweep in commit `354c84e`.

**The gap.** Risk-manager volume-gate check at [base_engine/risk/risk_manager.py:367](base_engine/risk/risk_manager.py:367) does `os.getenv(f"RISK_MIN_VOL_{bot_name.upper()}", _global_min_vol)`. For `bot_name="EsportsBot"` → `RISK_MIN_VOL_ESPORTSBOT` (set to 0 on VPS .env). For `bot_name="EsportsBotV2"` → `RISK_MIN_VOL_ESPORTSBOTV2` (NOT set; falls to global `ENSEMBLE_MIN_MARKET_VOLUME_USD=5000`). Polymarket esports markets typically have $0 CLOB volume — every v2 trade attempt was being silently blocked by the $5000 minimum.

**Evidence trail.** Trade-eligible LoL prediction `ps_1465546` (T1, p_model=0.822, edge=0.287, kelly=0.161) inserted at 2026-05-03 02:18:04 UTC. Order attempt fired immediately. Journal log at 2026-05-03 02:18:04: `Order blocked: risk limits  bot_name=EsportsBotV2  reasons=['Market volume $0 below minimum $5000']`. Same shape every subsequent eligible singleton would have hit. Pre-fix: 0 `paper_trades` and 0 `trade_events` rows for v2.

**Why the original wiring sweep missed this.** The original sweep traced `bot_name == "EsportsBot"` literal-string checks across `bankroll_manager.py` (1 place) and `risk_manager.py` (4 places per the §S210 EB v2 trade flip Corrections Log entry). The volume gate uses a different shape — env-var name lookup via `f"RISK_MIN_VOL_{bot_name.upper()}"` — not a literal-string bot-name comparison. Same false-positive class (env-var-prefixed-by-bot-name), distinct AST shape. The pattern is also documented in CLAUDE.md "CLOB volume=0 — Never use volume gates for MirrorBot" (existing precedent for MirrorBot's `RISK_MIN_VOL_MIRRORBOT=0` override) but I didn't extend the check to v2 in pre-flip verification.

**Action shipped (NOT git-tracked, VPS .env edit).** Appended `RISK_MIN_VOL_ESPORTSBOTV2=0` to `/opt/pa2-shared/.env`. Backup preserved at `/opt/pa2-shared/.env.s210-pre-volgate`. Service `polymarket-esports` restarted at 2026-05-03 ~18:11 UTC, new PID 3033846. Env propagation verified via `/proc/3033846/environ` — both `RISK_MIN_VOL_ESPORTSBOTV2=0` and `ESPORTS_V2_DRY_RUN=false` confirmed live on running process.

**Why env-only fix, not code change.** Same shape as the existing MirrorBot precedent (`RISK_MIN_VOL_MIRRORBOT=0` is also env-only, not in `_DEFAULT_BOT_CONFIGS`). The risk-manager logic correctly defaults conservatively ($5000 min for unknown bots) and lets per-bot env vars override. Adding `EsportsBotV2: 0` to a code-level default would be a new pattern — the existing pattern is "explicit per-bot override in .env where the bot needs it." Keep precedent shape.

**Rollback path.** `ssh ubuntu@VPS 'sudo cp /opt/pa2-shared/.env.s210-pre-volgate /opt/pa2-shared/.env && sudo systemctl restart polymarket-esports'`. Restores .env to pre-volgate state (which already had the trade-flip enabled — the volgate is the ONLY change from the pre-volgate backup).

**Process observation (filed as Protocol candidate — see §Protocol candidates).** This is the second instance of "production tooling output drove operational decision without verifying the tooling itself" — first was S195 SQL `--` (silent-zero pathology, 17 days), second was S207 Gate 5v2-C eval Brier formula bug (drove §S208 PARK rationale, ~1 month). The S210 wiring sweep is a third class — "pre-flip verification missed a 6th gap" — but per definition (verification was attempted, just incomplete) doesn't fit the same Protocol candidate. Filed separately as "Verify-before-mark-RESOLVED" candidate.

**Updated 1-week review checkpoint context (2026-05-09).** Pre-volgate fix: 0 trades over 17h. Post-volgate fix: trades will fire on next eligible singleton with a Polymarket market. Cohort baseline still ~1.5 trade-eligible/day, mostly LoL. The 1-week review should observe meaningful trade volume; if 2026-05-09 shows n=0, the volume gate isn't the only block and deeper investigation is warranted.

**Evidence of origin.** S210 session 2026-05-03. Operator-directed S210 review surfaced the no-verification structural concern; verification query confirmed n=0 trades; journal investigation surfaced the volume-gate block; .env edit + service restart shipped fix. Triple-blind not run because the chain is single-source (one journal log line, one DB row count, one risk-manager code path); future operator audit can reproduce via `psql -c "SELECT * FROM paper_trades WHERE bot_name='EsportsBotV2'"` and `journalctl -u polymarket-esports | grep 'Order blocked'`.

---

### S210 Hygiene Backlog

**1. MEMORY.md "earlier handoffs canonical in repo" framing was inaccurate.** S210 review observation: the section header said "current sessions only — earlier handoffs canonical in repo" but handoff files are gitignored (`.gitignore:147`). They live in the working tree only, NOT in the git repo. If `git clean -fd` runs, all earlier handoffs are gone — same risk class as §S180 Hygiene Backlog "survive by careful hands" pattern. **Action (S210 same-session):** MEMORY.md header text corrected to "current sessions only — earlier handoffs are working-tree-only, NOT in git per `.gitignore:147`." Going-forward rule: if the working-tree-only-handoff fragility class accumulates (e.g., a session can't reconstruct context because an earlier handoff was deleted), file as a Protocol-candidate-shaped observation. Until then, the soft convention (operator must NOT `git clean -fd` without auditing untracked files, per §Operational Procedures session-local-script bullet) is the only safeguard. RESOLVED 2026-05-03 (header fix); no further action required unless fragility class triggers.

**2. VPS audit hang investigation (carries from §S208 Hygiene #7 qualifier).** §S208 Hygiene #7 RESOLVED tag qualified to "RESOLVED locally; production deferred." Production verification requires either (a) the VPS hang resolved and the audit script run successfully, OR (b) the local-only result accepted as sufficient evidence. Hypothesis for the hang: doubled file I/O (each .py file is read+parsed twice — once per extractor). Proposed fix: refactor `collect_code_defaults` to read+parse each file ONCE and walk the AST twice with the existing two extractors taking an AST tree instead of a filepath. **Action (next session — Tier 1):** ~30-min refactor + run on VPS, capture production ENV-ORPHAN count vs S208 baseline of 18.

**Evidence of origin.** S210 session 2026-05-03 review observations. Items 1 RESOLVED in-session (header text fix). Item 2 carried as Tier 1 next-session work.

---

**Evidence of origin.** S210 session 2026-05-02. Operator selection of (a-unrestricted) at S210 mid-session, with explicit waiver of the rollback-trigger pre-commit recommendation. Pre-flip verification per operator instruction "verify they have all they need to succeed" surfaced the 5 wiring gaps. Code fix verified by direct comparison of wired EsportsBot paths vs unwired EsportsBotV2 paths. Trade-eligible cohort visible in `esports_predictions` table (mode='shadow', model_version='v2-trinity', is_singleton=true, market_price IS NOT NULL, edge >= 0.05) — last 7d direct query returned LoL=9, CS2=2 trade-eligible singletons. Verbatim cohort query at `feedback_eb_v2_trade_flip_review.md`.

---

## Protocols

Binding rules for all future sessions. Each protocol exists because a real hypothesis-inversion or false-finding would have shipped a wrong fix in its absence. Added incrementally as new failure modes are caught during execution.

**Scope of this section:** durable binding rules only. Session-specific narratives (what a session decided, what commit landed where) belong in handoff files and memory. Every addition to §Protocols must be a rule generalizable across bots and sessions.

---

### Protocol 1 — SQL-diff on filter-scope fixes

**Mandate.** Any fix whose hypothesis involves filter scope — row coverage, inclusion/exclusion of categories, or an expected change in the row-count the query returns — must produce a row-count diff between the old and new clauses against live data *before* code is written. Document both counts in the planning artifact.

**Out of scope.** Cosmetic refactors that preserve row semantics (column-reference renames, SQL formatting, comment additions, whitespace) do NOT require a row-count diff. This protocol applies only when the hypothesis is about WHICH rows are matched.

**Evidence of origin.** S182 Phase 0.2's original answer ("filter too narrow, broaden it") was wrong. A SQL diff revealed the proposed keyword filter matched 286 rows vs the existing `category='esports'` filter's 1,487 — the fix would have *reduced* refresh coverage. Caught by the SQL-diff demand before code landed.

---

### Protocol 2 — Persistent-state proof for "service running but not producing X"

**Mandate.** Any claim that a service is running but not producing expected output must be backed by a **timestamp/counter comparison across two observation windows separated by at least one expected cycle interval** — not a single-point-in-time query. If both windows show zero production AND no recent persistent-state updates, the service is idle. If either window shows recent state updates, the service is working (quiet, not idle).

**Out of scope.** Alerts that fire on absence of a specific transient signal (e.g. "no heartbeat in the last N seconds") are not service-running-but-not-producing claims; they're transient-signal checks and handle their own semantics. This protocol applies to claims about persistent output (DB writes, state transitions, log emissions with stable cadence).

**Evidence of origin.** S182 Phase 0.2-b's refresh-service idle diagnosis relied on comparing `markets.updated_at` at T+0 and T+60s — the max timestamp didn't advance, proving the service was genuinely stalled rather than coincidentally quiet. A single SELECT could have matched a legitimate quiet window and misled the investigation.

---

### Protocol 3 — Diagnostic output skepticism

Diagnostic output is a lens, not ground truth. Three sub-protocols cover the three ways it can lie.

#### 3a — Round-number skepticism

**Mandate.** Round-number counts in diagnostic output (100, 200, 500, 1000, 10000) should be treated as possibly-LIMIT-capped until proven unbounded. Audit each check's query for explicit `LIMIT` clauses, per-subquery caps, and join-level truncation before trusting the count as ground truth.

**Out of scope.** Counts that are naturally round by domain (e.g. exactly 100 positions opened because a daily cap is 100) are not LIMIT-capped. The protocol applies when a diagnostic COULD have returned more but returned exactly the round number matching a suspected LIMIT.

**Evidence of origin.** S182 audit discovery — several audit checks reported findings of exactly 100 or 200, matching `LIMIT 100` / `LIMIT 200` clauses in their SQL. The true uncapped count was unknown; the round numbers were under-reporting. Caught by mapping every check's LIMIT clause before basing triage decisions on the reported counts.

#### 3b — Dedup before trusting "findings count"

**Mandate.** Cumulative tables that re-detect the same condition across runs will inflate apparent scope by a duplication factor. Before treating a row count as "findings count," dedupe on the stable-identity column (`violation_hash`, `event_id`, idempotency key, etc.) and report both raw and unique counts. The duplication factor itself is diagnostic — a factor of 10-11x across a 10-day window implies daily re-detection without a `last_seen` update path.

**Out of scope.** Tables that are naturally append-only event logs (trade events, audit runs themselves, transactions) are not re-detection tables and their row count IS the event count. The protocol applies when a table's rows represent *detected conditions* rather than *events*.

**Evidence of origin.** S182 audit found 35,043 OPEN `reconciliation_breaks` rows, which triage-scope-wise looked unmanageable. Dedupping on `violation_hash` collapsed the unique count to ~8,223 with dup factors of 9-12x on most categories, confirming daily re-detection. The tractable unit was unique violations, not raw rows.

#### 3c — Newly-added check "spike" is not a regression

**Mandate.** When a diagnostic check is newly added to a running system, its first execution appears as an apparent spike in findings as pre-existing violations surface for the first time. Before treating a dated cluster of findings as evidence of a regression, check `first_seen` dates against the deploy history of the check itself. A cluster of findings dated to a known check-deploy day is the check finding old problems, not a production event.

**Out of scope.** Genuine spikes AFTER a check has been running steady — a sudden 10x jump on a well-established check is a real signal. This protocol applies only to the check's first-detection moment.

**Evidence of origin.** S182 audit-history analysis showed a massive Apr 8 spike (4,333 findings) which initially looked like a catastrophic event. Cross-checking `first_seen` dates against recon_types revealed 12 new recon_types with `first_seen=2026-04-08` — the audit code had been extended that day with 12 new checks, and the "spike" was every pre-existing violation in the database surfacing through the new checks on their first run. Not a regression.

---

---

### Protocol 4 — Runtime reachability and contract integrity

Three sub-protocols cover distinct ways a component fails to produce the expected output despite being "running." The component may not actually be called (4a), may have been pattern-copied from a silently broken progenitor (4b), or may be silently dropping fields the upstream supplies (4c). Each sub-protocol has its own Mandate / Out-of-scope / Evidence-of-origin, but they share the same diagnostic posture: before concluding "the code is wrong," verify that the code is reached, the pattern is honest, and the information flow is whole.

#### 4a — Runtime-reachability verification

**Mandate.** For any "service is running but not producing X" investigation, verify that the code path that would produce X is actually reached at runtime *before* concluding the code path is broken. Protocol 2 (persistent-state proof) establishes that X is not being produced; it does NOT establish that the code meant to produce X is being executed.

**Minimum evidence (any ONE of):**
- A log line emitted from inside the relevant code path proving execution (requires the code to already have such a log, or adding one as the first diagnostic step)
- A stack trace, profiler sample, or `strace`/`py-spy` capture showing the path is hot
- Grep of the instantiation / dispatch / entry-point chain proving the service or function is reachable from the running process's startup (traces caller relationships, not just existence of the callee)

If none can be produced, "the code is broken" is NOT a supported conclusion. The alternative hypothesis — the code is not being called at all — has a different fix (add the call site or wire the instantiation, rather than fix the code body).

**Out of scope.** Systems where reachability is structurally guaranteed by framework conventions (e.g. `@app.route()` handlers registered at import time, systemd-managed oneshot scripts whose ExecStart is the entry point) don't need explicit reachability proof — the framework enforces it. This protocol applies to discretionary-invocation code: background tasks, service classes instantiated by application code, handlers registered dynamically.

**Evidence of origin.** S182 Phase 1b shipped a fix to `EsportsMarketService.refresh_market_prices()` that was code-correct (verified via 5 passing tests + compiled production service) but sat in a code path with zero runtime callers — `EsportsBotV2._initialize()` never instantiates `EsportsMarketService`, so the background refresh task never starts. Phase 0.2-b's persistent-state comparison (Protocol 2) correctly identified that state wasn't advancing. It could not distinguish "running and failing" from "never running" — that distinction required runtime-reachability proof. Pattern on this subsystem across Phases 0.2 / 0.2-b / 1b: each investigation layer hypothesized the bug was one level deeper than the last verified layer when it was actually one level shallower (service-never-instantiated > silent-crash > filter-scope). Two consecutive hypothesis inversions on the same bug. Future investigations on this subsystem should assume a fourth failure mode is possible and start from runtime-reachability.

#### 4b — Reused patterns inherit their predecessors' bugs

**Mandate.** When reusing a pattern from existing code (copying a helper, mirroring a call site, replicating a query structure, porting a dict-access convention), verify the pattern works on a live instance before adopting it. "The existing code compiles and passes tests" is insufficient evidence — a pattern can be silently broken in a way that tests don't catch, particularly when the failure mode is returning a sentinel (None, empty, 0) that the caller treats as a legitimate absent result.

**Minimum evidence.** Does the pattern produce observable output on a live system in the way the new caller expects? Either run the new code path end-to-end and verify the output, OR add a contract test that pins the producer's output schema against the consumer's expectations.

**Out of scope.** Framework-provided patterns with strong type-system guarantees (e.g. typed protocol adapters, Pydantic-validated schema) are contract-verified by the framework; this protocol applies to loose-schema patterns like dict access, raw SQL structure, message-bus payload shapes.

**Evidence of origin.** S181 Commit 3 introduced `_find_polymarket_for_match` in `bots/esports_bot_v2.py` by copying a dict-access pattern from the pre-existing `_get_market_price` helper. The helper read `m.get("yes_price")` from the scanner's output; the scanner had long emitted the key as `"price"`. The helper had been silently returning None for every call (EB v2 always found zero markets), but no visible failure surfaced because the caller treated None as "no market available." S181 Commit 3 inherited the bug unchanged. Protocol 4a (runtime-reachability) would have caught it had it existed at S181 time; Protocol 4b codifies the specific sub-case of pattern reuse.

**Sibling application — handoff-entry verification.** The same discipline applies to prior-session handoff content: verify claims against current code before acting on them. A handoff describes the system at write-time; the interval to entry-time is a drift window. This is not a separate protocol — it's the same "is X actually true right now" posture applied to documentation instead of code. Both failure modes produce the same class of wasted investigation. Caught at S182 Phase 1d handoff entry when verification against current code surfaced a third key-name mismatch (`yes_token_id`/`no_token_id` vs scanner's singular `token_id`) at `bots/esports_bot_v2.py:604` that was absent from the prior handoff's flagged-bugs list. Had the handoff been trusted as testimony rather than verified as claim, Phase 1d would have shipped a fix that left site #604 silently broken.

#### 4c — Projection lossiness

**Mandate.** When a component takes structured input and produces structured output, verify that any fields present in the input AND expected by downstream consumers are also present in the output. A projection layer can silently drop fields the upstream has available; the bug is invisible from tests that examine only the projection's output against its own declared schema. Required check: diff the input dict keys against the output dict keys and cross-reference against consumer expectations.

**Minimum evidence.** For any projection or adapter layer: enumerate the keys the upstream source provides (for each source if multiple), diff against the keys the output dict emits, and cross-reference against all consumers of the output. Any consumer-expected key missing from the output despite being present on the input is a silent-None bug. A contract test pinning the input→output field mapping makes future regressions loud.

**Out of scope.** Projections that are explicitly narrowing their interface for a documented reason (e.g. redaction, privacy filtering, deliberate encapsulation) fall under their documented scope. This protocol applies to projections that drop fields by accident or oversight rather than design.

**Evidence of origin.** S182 Phase 1d audit revealed `EsportsMarketScanner.find_markets_for_match` received market dicts from `EsportsMarketService.get_tradeable_esports_markets` that already contained `yes_token_id`, `no_token_id`, `yes_price`, `no_price`, `id`, `condition_id` as top-level keys. The scanner's output projection at `esports/markets/esports_market_scanner.py:149-157` emitted only `market_id`, `token_id`, `price`, and sibling fields — silently dropping the paired-token keys that three separate downstream readers in `bots/esports_bot_v2.py` (lines 547, 570, 604) were trying to consume. Initial Phase 0 classification labeled the bug as "schema-shape" (architectural, requires paired-tokens modeling) because it stopped at "scanner emits X, reader reads Y" without asking "what does the scanner's input already contain?" Looking one layer upstream reframed the bug from architectural to projection-lossiness and dissolved the "schema-shape" class entirely — all six consumer sites resolved by passing the upstream keys through. Protocol 4c codifies "diff input keys vs output keys" as a first-pass Phase 0 step before reaching for architectural or schema-shape fixes.

**Sub-case — runtime-reachable input (Protocol 4c extension).** The "diff input keys vs output keys" check assumes the projection's input is non-empty at runtime. A projection that correctly passes through every key the upstream provides is still operationally inert if the upstream returns empty on every call. For projections with conditional input strategies (a strategy-A-or-strategy-B selection gated on external dependencies), verify at least one input strategy is actually exercised before declaring the projection fix complete — the equivalent of Protocol 4a applied to the projection's input rather than the projection itself. Minimum evidence: run the projection end-to-end against real data and observe non-empty output with consumer-expected keys populated.

**Evidence of origin (sub-case).** S182 Phase 1d post-deploy T+44min verification found that the A4 passthrough fix — code-correct and verified by unit tests + VPS file diff — was operationally inert in `EsportsBotV2`. Its `_initialize()` at `bots/esports_bot_v2.py:111` constructed the scanner with only `db=db`, leaving both `self._market_service` and `self._poly` as None. Both of the scanner's internal input strategies (market_service at L97 and polymarket_client fallback at L107) short-circuited on every call, so `all_markets=[]` and the A4-enriched output never appeared because the `for market in (all_markets or [])` loop never iterated. `esports_predictions` showed 213 rows across 30 days with zero non-null `market_price`, confirming the pre-1d-3 state: the projection was never receiving input. Closed in Phase 1d Commit 1d-3 by wiring `EsportsMarketService` into the scanner constructor (mirrors `bots/esports_live_bot.py:107-118`). A pre-deploy E2E verification would have caught this — running the scanner+service against real data with a fixture match returned 13 non-empty results with all paired keys populated, proving the projection path works when the input is reachable. The durable lesson: Phase 0 contract audits should examine both ends of the projection (what does the emitter emit AND does it ever receive anything to emit from).

---

### Protocol 5 — Phase-level status claims require shipped-code verification

**Mandate.** When a session report, handoff, or memory entry asserts phase-level status ("Phase X done," "Item Y pending," "Gate Z passed"), treat the claim as testimony, not fact, until verified against shipped code on master (and, where relevant, against the deployed release on VPS). Status claims drift across the serial chain of handoff → memory → next handoff → next memory; by the time a multi-session-old claim is reused, the underlying code may have changed in either direction (something marked "pending" may have shipped; something marked "done" may have been reverted or never merged from a branch).

Protocol 4b's "handoff-entry verification" sibling clause covers claims about *bugs* and *specific code facts*. Protocol 5 is the status-claim sibling: it applies specifically to coarse-grained phase/item completion state, which has its own failure mode distinct from bug claims.

**Minimum evidence.** For each phase/item status claim being relied upon:
- **"Shipped" claims:** verify the file exists, read enough of its contents to confirm it's a real implementation (not a stub), and confirm the commit SHA is on master (`git log` or `git merge-base`). For deploy-dependent claims, additionally confirm the SHA is at or before the VPS-deployed release.
- **"Pending" claims:** grep the target file/function for the supposed missing implementation. A claim that an item is pending is falsified by the presence of the code it allegedly lacks.
- **"Gate passed" claims:** re-run the gate query or re-evaluate the gate criteria against current data, not against the data the claim was originally measured on. Gate data drifts; a gate passed last month may no longer hold.

If evidence cannot be produced, the status claim is unsupported and must be re-marked as "memory-claimed, unverified" until verified. Acting on an unverified status claim — including propagating it into the next handoff or memory entry — is forbidden.

**Out of scope.** Claims about facts invariant under code changes (historical events, design decisions, session narratives, deploy timestamps) do not require re-verification — they're immutable record. This protocol applies to claims about the *current state* of code, data, and configuration.

**Evidence of origin.** S183 entry-point verification (2026-04-19/20) surfaced two drift items in memory/handoff status claims within a 30-minute window: (1) "2H-3 pending" (actually shipped in commit `b786316`, fully wired at `order_gateway.py:625-652` with per-bot depth multipliers), (2) "Phase 6 gated on 1B calibration pending" (1B CRPS/PIT shipped in commit `ccae341` at `scripts/calibration_check.py:110-188`). Both had cascaded through multiple handoffs unchallenged because no session had re-verified the claims against code since the original "pending" statements were first written. Both surfaced the moment a "read the code, don't guess" directive forced verification. Protocol 4b's handoff-entry verification would have prompted the check for bug claims; this protocol extends the same discipline to phase-level status claims as a distinct class.

#### 5a — Canonical source document identity

**Mandate.** When a session or memory entry asserts that a particular filename is the canonical version of a multi-version document (plan, schema, protocol, architecture doc), verify that the filename actually points to the most-recent-approved content. Document identity and document content drift independently: a filename convention can remain stable ("the unnumbered file is canonical") while the approved content has moved into a versioned sibling file, or vice versa. Claims about which file to read are a distinct failure class from claims about what a file says — the former fails before the document is even opened.

**Minimum evidence.** For the document being relied upon:
- List all files matching the canonical-name pattern (e.g., `ls S172_CONSOLIDATED_PLAN*.md`, `git ls-files '<schema>*.sql'`).
- If more than one match exists, verify each is tracked in git, compare mtimes and commit dates, and read each header to determine which one is marked as approved/current.
- If the "canonical" file by filename convention is NOT the most-recent-approved content, either promote the approved file to the canonical filename, or merge the approved content into the canonically-named file. Delete the orphan so the bifurcation does not recur.

**Out of scope.** Explicit version-history files kept alongside the current file by design (e.g., `CHANGELOG.md`, migration files numbered in sequence, archived handoffs) are not bifurcations — they're intentional parallel artifacts. This sub-case applies to cases where two files both claim to be the current authoritative source.

**Evidence of origin.** S183 plan-hygiene audit found `S172_CONSOLIDATED_PLAN_v7.md` tracked in git (committed `0f1e2a8` on 2026-04-14, headered "APPROVED — integrates v6.0 + Amendment 1 + Phase RC findings") sitting alongside `S172_CONSOLIDATED_PLAN.md` (v6, the filename-canonical version, last edited 2026-04-19 with ongoing session Corrections Log additions). Memory and CLAUDE.md both pointed at the unnumbered filename, so every session since 2026-04-14 had been reading v6 without Phase RC or Day 2 content. Surfaced by a grep of `^## ` section headers against both files which exposed Phase RC and Day 2 sections in v7 that v6 lacked. Resolved by merging v7's content into the canonical-filename v6 (v6 retained because it had more ongoing edits than v7) and `git rm` on the v7 orphan, preserving v6's Corrections Log + Protocols additions while folding in v7's RC + 5v2 content. The class of drift: filename-stable, content-drifted into a sibling.

#### 5b — Query shape verification before interpretation

**Mandate.** When a session relies on query output as evidence for a claim, verify the query's shape before interpreting the result. A rowcount, a sum, a set of returned rows — none of these are evidence until the query is confirmed to have executed without error, filtered as intended, and produced output that maps unambiguously to the question being asked. Reading query output as substantive result without a shape-check is a class of measurement error that produces confidently wrong conclusions — all the more dangerous because the numbers look authoritative.

**Minimum evidence.** Before interpreting query output:
- Confirm the query exited with status 0 (no syntax errors, no missing-column errors, no permission errors). An errored query may return truncated or empty output that looks like a valid answer.
- Confirm the `WHERE` clauses produced the intended filter. For single-market claims derived from a multi-market `IN(...)` result, re-run per-market or include the filter-discriminating column (`market_id`, `bot_name`, etc.) in `SELECT` so attribution is unambiguous.
- Confirm the result shape matches the claim's granularity. A `GROUP BY` query that groups by `(market_id, event_type, side)` but omits `market_id` from `SELECT` cannot be attributed row-by-row to specific markets — the grouping column must appear in `SELECT` or the query must be re-run per-clause.

**Operational rule (the one thing to internalize).** When a query has multi-value `IN`, `OR`, or `GROUP BY` on a discriminating column, that column MUST appear in `SELECT`. If it doesn't, rows cannot be attributed and the output is not shape-verified regardless of how many rows came back.

**Out of scope.** Queries whose output is consumed by a downstream script that itself enforces shape discipline (e.g., `bot_pnl.py`'s internal queries, which are structured code paths with tested output shapes) do not require manual shape-verification by a human reader. This sub-section applies to ad-hoc SQL run during a session whose output feeds directly into a human-written claim.

**Evidence of origin.** S190 §4.1 PSM verification (2026-04-22). One underlying error — multi-market `IN()` result attributed to a single market without inspecting the grouping column row-by-row — produced three reporting manifestations in the same investigation: (1) "dual-side market has 0 positions rows" (actually 2 rows), (2) "single-side market has 3 rows including SELL sibling" (actually 1 row; the 2 extras belonged to the dual-side market), (3) "dual-side has 0 trade_events even without bot filter" (conflated an earlier bot-filtered-zero with a later unfiltered query whose output omitted `market_id` from `SELECT`). Plus one derivative self-diagnosis miss: initial self-attribution blamed "errored query read as 0 rows," but the actual errored query (`column "created_at" does not exist`) was correctly recognized at the time — the real pattern was `IN`-misattribution throughout. Three manifestations from one cause in one session satisfied the operator's promotion threshold. Codified S190.

#### 5c — Row-class-dependent field queries

**Mandate.** When a column's presence, meaning, or semantics varies by a row-class discriminator (e.g., `event_type` in `trade_events`, `status` in `positions`, `recon_type` in `reconciliation_breaks`), population / coverage / presence queries must filter to the relevant row class before computing the statistic. A coverage query run without the class filter conflates classes where the column carries data with classes where the column is null by design, producing a number that answers no question.

**Minimum evidence.**
- Identify the row-class discriminator column whose value affects the queried field's semantics before writing the coverage query.
- Filter or `GROUP BY` that column; if population varies across classes, report per-class coverage, not aggregate.
- When inheriting a coverage claim from a prior session, re-verify its class-filter framing before treating the number as comparable to current data.

**Out of scope.** Columns with uniform semantics across all row classes (primary keys, timestamps with invariant meaning, fully-populated fields) do not require class-filtered queries — the row class has no semantic effect. This sub-section applies only to columns whose presence or meaning is row-class-dependent.

**Evidence of origin.** S188→S189 investigation of `trade_events.event_data->'trader'` coverage. S188 spot-checked 4 recent MB events, found 0 with the field, flagged as write-path-defect candidate. S189 Phase 0 traced the discrepancy to the absence of an `event_type` filter: the 4 sampled events were all EXIT (by design no trader); ENTRY events have 99.93% coverage; EXIT and RESOLUTION are 0% by design. The class-unaware query conflated ENTRY (trader-carrying) with EXIT/RESOLUTION (trader-not-carrying by design) into a single figure that captured neither class. S189 filed as candidate; codified S190 per operator direction.

#### 5d — Verbatim query preservation

**Mandate.** Queries that produce numeric or factual claims in handoff docs, memory entries, session reports, or any durable artifact must be preserved verbatim in the artifact. A claim without its producing query is reconstructable only if the outcome is distinctive enough to uniquely constrain the query shape; for any claim whose outcome is not self-constraining (most of them), reconstruction is impossible, and the claim cannot be independently verified by a future session even in principle.

**Minimum evidence.**
- Every handoff claim citing a count, coverage, ratio, sum, or other numeric finding embeds the SQL or command that produced it (in a fenced block or inline, as appropriate).
- Multi-step investigations preserve intermediate queries, not just final results — downstream sessions need the intermediate shapes to replay the reasoning.
- Where the exact query is impractical to embed verbatim (e.g., runs across multiple invocations with parameter variation), cite the script path and parameter values sufficient to reproduce the claim's specific output.

**Out of scope.** Qualitative findings (pattern observations, design choices, code-review findings read from source files) do not require query citations — they are not query-derived. This sub-section applies only to quantitative claims falling within Protocol 6's scope, 5b's scope, or any claim where the number itself is the load-bearing evidence.

**Evidence of origin.** S188 "0 of 4 recent MB events had the trader field" — S189 reproduced the underlying evidence only because the outcome was narrow enough (0-count) and the target distinctive enough (4 most-recent MB events, easily re-enumerable) to constrain the query shape for reconstruction. A less distinctive outcome ("68% coverage," "150 of 200 events") would have been impossible to reconstruct without the producing query preserved. The reconstruction-vs-reproduction ambiguity closes at source: claims unreproducible from their artifact are not durably verified, regardless of how rigorous the originating session was at claim time. S189 filed as candidate; codified S190 per operator direction.

---

### Protocol 6 — Canonical-source discipline for P&L / win-rate / trade-count claims

**Mandate.** Any P&L, win-rate, or trade-count claim — in session output (assistant messages), handoff docs, memory entries, commit messages, plan edits, or any artifact produced during a session — must cite `scripts/bot_pnl.py` as the source and include the exact invocation command that produced the number. A claim without an adjacent `scripts/bot_pnl.py` citation and invocation command is forbidden; the correct form is to omit the number entirely rather than produce it without sourcing, or to re-run `bot_pnl.py` and cite its output. This protocol codifies Rule Zero (the pre-existing memory rule) with mechanically checkable adherence criteria.

**Minimum evidence.** For every paragraph, table, bullet, or sentence containing a P&L, win-rate, or trade-count number:
- An explicit reference to `scripts/bot_pnl.py` in the same paragraph, or in an adjacent paragraph that unambiguously binds the number to the script's output.
- The exact invocation command that produced the number (e.g., `PYTHONPATH=/opt/polymarket-ai-v2 python3 /opt/polymarket-ai-v2/scripts/bot_pnl.py WeatherBot 24`). Absent the command, the claim cannot be re-run for verification and is unsourced.
- If the question `bot_pnl.py` answers does not match the claim's granularity (e.g., a claim needs per-bucket P&L that `bot_pnl.py` does not emit natively), the claim must either be omitted or must replicate `bot_pnl.py`'s EXACT resolution-join SQL verbatim. Improvising a parallel query against `trade_events` is forbidden — see CLAUDE.md Forbidden Pattern 7.

**Out of scope.** Configuration values read from source code (e.g., `KELLY_FRACTION=0.25` from `settings.py:42`, `MIRROR_MIN_TRADE_USD=25` from `mirror_bot.py:2004`) with file:line citations are NOT P&L data — they are static facts derivable from code and do not require `bot_pnl.py` sourcing. Arithmetic derived from config values (e.g., "$25 minimum × 100 trades = $2,500 floor") is also exempt. Only numbers that claim to represent actual realized trading performance — realized dollar P&L, unrealized dollar P&L, win rates from trade data, trade counts from queries — fall under this protocol. Historical figures in handoff docs, memory files, or commit messages that predate Protocol 6 promotion are grandfathered; they do not retroactively require `bot_pnl.py` citations. New claims in any artifact produced after promotion must comply.

**Enforcement mechanism.** A session stop-hook pattern-matches on P&L / win-rate / trade-count terms in assistant output and checks for `bot_pnl.py` citation adjacency. A violation surfaces as a hook message, giving the agent a chance to retract the number before the response is finalized. The stop-hook is why self-catch is now reliable — it is the mechanical surface that makes adherence verifiable rather than aspirational. Protocol 6's form is specifically designed to be checkable by this mechanism: "did this session cite `scripts/bot_pnl.py` in every P&L-bearing paragraph?" is grep-able; "did this session honor Rule Zero?" required interpretation. If the stop-hook fires, the procedure is: strip the offending numbers from the response, preserve any qualitative findings that survive without them, cite `bot_pnl.py` output directly for any numbers that must remain, and if the originating artifact was a script, add a Rule-Zero header warning naming this protocol.

**Evidence of origin.** Four instances of the same failure class across sessions:

| # | Session | Pattern | How caught |
|---|---------|---------|------------|
| 1 | S149 | Fresh SQL against `trade_events`, output presented as canonical | User correction mid-session |
| 2 | S150 | Same pattern, different table/window | User correction mid-session |
| 3 | S185 | 6O lead-time backtest script produced bucket-level P&L used in plan edits before self-catch | Stop-hook mid-session; recovery procedure codified (strip numbers / preserve qualitative / header-warn producing artifact / file candidate) |
| 4 | 2026-04-20 | Reconciliation table + delta figures embedded in response summary without explicit `bot_pnl.py` citation, despite underlying numbers being from `bot_pnl.py` output | Stop-hook mid-session; retraction issued; protocol promotion landed |

S185's handoff §2.2 named the fourth-instance promotion trigger explicitly. This session hit it. Landing Protocol 6 closes the trigger: the promotion mechanism has to actually produce a protocol when it fires, or the threshold becomes a lie that teaches future sessions triggers are soft. The mechanism that caught both S185's and this session's violation — the stop-hook — is now named in the protocol body so future sessions know to rely on it rather than on unaided rule-reading.

#### 6a — Audit-check internal values carveout

**Mandate.** Protocol 6 applies to claims about trading performance (P&L, win rates, ROI, trade counts). It does not apply to audit-check internal values (`positions.size`, `trade_events` sums, reconciliation deltas, violation counts returned by audit queries) cited as evidence that an audit check is computing correctly, provided the surrounding claim is about check correctness, not about trading outcomes.

**Boundary clause (not a loophole).** The carveout applies only when the surrounding claim is explicitly about check correctness. If the same numbers are cited in a context making any claim about trading performance — including implicit claims ("the bot is doing well because size=X", "MB has been scaling in") — Protocol 6 applies fully and `bot_pnl.py` must be cited. The carveout is not a routing mechanism for performance claims dressed up as check-correctness framing.

**Minimum evidence a claim qualifies.**
- The surrounding paragraph names the specific audit check whose correctness is being evaluated (e.g., `PositionTradeEventsCheck`, `SizeInvariantCheck`, `TradedMarketsStatusDriftCheck`).
- The number is framed as check-internal: an input the check reads, an output the check flags, or a delta the check computes — not as a characterization of bot performance.
- No P&L / WR / ROI / trade-count claim is derived from or implied by the check-internal value. If derivation toward a performance claim is needed, run `bot_pnl.py` and cite it separately; do NOT bridge check-internal values into performance claims within the same paragraph.

**Interaction with stop-hook enforcement.** The stop-hook pattern-matches on numeric content and cannot distinguish carveout-compliant usage from violation by text alone. Expected interaction: the hook may fire on carveout-compliant content, the agent names the carveout and its boundary clause, the operator adjudicates. Repeated hook firings consistently adjudicated as carveout-compliant are a signal to refine the matcher, not a signal to suppress the carveout. This is not a hook malfunction; it is the expected interaction between a mechanical matcher and a rule with semantic scope.

**Out of scope.** Production performance claims (bot P&L over time, win rates on deployed strategies, ROI on capital deployed) remain fully inside Protocol 6 and require `bot_pnl.py` sourcing regardless of whether they happen to appear in a session that also involves audit checks. The carveout does not widen Protocol 6's grandfathering or weaken its enforcement for performance claims.

**Evidence of origin.** Three instances across three sessions:

| # | Session | Instance | How caught |
|---|---------|----------|------------|
| 1 | S186 | PSM port validation — violation-count comparisons between OLD and NEW query shapes triggered the hook despite being exactly the measurement needed to decide port correctness | Hook fired mid-session; candidate filed |
| 2 | S189 | Trade-count-in-check-effectiveness reasoning for `trade_events.event_data->'trader'` investigation — count derived from `trade_events` for disposition reasoning, not P&L reasoning | Flagged in handoff §2.4 as borderline use |
| 3 | S190 | §4.1 PSM verification required raw `positions.size` / `trade_events` sums / reconciliation delta as evidence; operator explicitly demanded raw SQL output as the verification standard; hook fired on the raw output AND fired again on the explanation response, demonstrating the protocol text's hook-interpretation was broader than the rule's intent | Hook fired twice (raw output + explanation); codification landed |

Codified S190 with boundary clause to prevent loophole abuse.

---

### Protocol 7 — Multi-Instance Independent Verification (MIIV) for inherited hypotheses

**Mandate.** When a remediation candidate emerges from a hypothesis inherited from prior-session reasoning (handoff doc, memory entry, prior agent report, or any testimony rather than fresh primary diagnostic in the current session), run a **second independent verification path** before committing code or declaring the hypothesis confirmed. A single source of evidence (one agent's grep, one query, one logical chain from prior-session narrative) is insufficient when the hypothesis itself is the load-bearing input. The verification must:

- Independently observe the predicted mechanism on live data or live code, not on the same testimony chain that produced the hypothesis.
- Produce evidence that would distinguish the hypothesized mechanism from a plausible alternative — if both the hypothesis and "the bug is something else entirely" predict the same observation, the evidence is not a verification.
- Be runnable from primary sources (grep, query, code read, log inspection) without re-reading the originating handoff or memory.

**Why this is needed.** Inherited hypotheses gain authority by surviving handoffs unchallenged — but un-challenge is not verification. The S195 → S199 chain produced multiple candidate fixes whose hypotheses, on second look, turned out to be wrong on mechanism. Each was caught in the verification step before code shipped. Without an explicit MIIV protocol, the cost of skipping verification is invisible until the wrong fix lands and a downstream session has to re-investigate.

**Out of scope.** Hypotheses generated from fresh primary diagnostic in the current session — a query just ran and produced X, X is the evidence — are already verified at source and don't require separate independent verification. This protocol applies specifically to hypotheses arriving via testimony chains (handoff inheritance, prior-session memory, agent reports synthesizing prior context) and about to drive a remediation. It also doesn't apply to remediation steps where the action itself is the verification (e.g., "deploy and observe" when the deploy outcome unambiguously distinguishes hypotheses).

**Minimum evidence.** Before committing a fix on an inherited hypothesis:
- Identify the testimony chain — which prior session, which doc, which agent's report supplied the hypothesis?
- Identify a second independent source: live primary diagnostic, code-read at a different layer, parallel-agent verification, or schema/data inspection that wasn't in the originating chain.
- Run the second source. Document its output. Confirm the predicted mechanism — not just that "something is broken" — but that the broken thing is broken in the way the hypothesis claims.
- If the second source contradicts the hypothesis, the hypothesis is falsified. Re-open the diagnostic at the falsification point; do not patch the original hypothesis to fit the contradiction.

**Recovery procedure when MIIV falsifies a hypothesis.** Treat the falsification as a primary finding, not a setback. File the falsified hypothesis explicitly (with its original framing preserved for audit-trail), name the actual finding distinctly, and re-scope the remediation. The pattern is "hypothesis-disproved-by-deeper-investigation" — every instance is information about how the system actually behaves vs. how the prior session thought it behaved. Discard the framing of "we wasted time on the wrong hypothesis"; it's "we just learned something the prior session couldn't have learned."

**Evidence of origin.** Ten in-session hypothesis inversions across S195 → S202:

| # | Session | Inherited hypothesis | Actual finding (after independent verification) |
|---|---------|----------------------|--------------------------------------------------|
| 1 | S195 | Architectural wiring fix in commit `d67e03e` (Phase 4b cleanup) is sufficient for RESOLUTION silent-zero | Wiring change made the silent failure observable but did not fix it — `PostgresSyntaxError` surfaced in journal post-deploy. Actual root cause was upstream SQL `--` comment in `insert_trade_event` RESOLUTION INSERT (S167 regression), fixed via `/* ... */` block comment at `b82ad68`. Pattern: cleanup commits without root-cause linkage — see §Protocol candidates "Architectural cleanup is not a substitute for root cause." (Row 1 wording corrected S206 audit; original framing "SQL `--` parsing bug was scoped to a specific query" did not match any of the four §S195 Phase A diagnostic inversions and was inserted with a paraphrase mismatch. See §Corrections Log §S206 Hygiene #1 closure.) |
| 2 | S196 | SIZE_INVARIANT was a units-mismatch in the audit check | Actually a writer-side divergence between `positions.size` and `trade_events` ENTRY truth |
| 3 | S198 | bot_pnl.py CLEAN total UNBLOCKS Phase 7 elevation gate | All-time CLEAN conflates pre/post-deploy; gate needs windowing first |
| 4 | S199 | SHADOW_ENTRY uses a write path that bypasses `insert_trade_event` | SHADOW_ENTRY routes through `insert_trade_event` but skips FK auto-heal due to incomplete type-check tuple at `database.py:5490` |
| 5 | S199 | Bug A is averaging-up new-fill overwrite of `positions.size` via `confirm_position` | Caller does pass new-fill, but SQL UPDATE is gated by `WHERE status='closed'` so averaging-up is a DB no-op; actual mechanism still unknown |
| 6 | S200 | Bug A's lead candidate is partial-fill UPDATEs at `mirror_bot.py:1410`/`:2390` overwriting `positions.size` without `insert_trade_event` | Falsified — `pt_yes_no_rows=1` across all 32 cohort markets, no chunking. Deeper finding: the 32-cohort itself is a `bot_pnl.py --since` integrity-check windowing artifact (pre-window ENTRY + in-window EXIT/RESOLUTION), disjoint from the real Bug A cohort of 74 all-time markets (64 WB + 9 MB + 1 EB; S200/S201 prose said "73" — corrected in S203 per §Bug A Diagnostic Closure intro paragraph). The inherited *framing* — "Bug A is MB-shaped, partial-fill-related" — was wrong on cohort, mechanism, and bot-focus. |
| 7 | S201 | Bug A cohort "stopped accruing 2026-03-26" — symptom-bounded framing | Symptom-bounded ≠ mechanism-bounded. Option C analysis revealed 244 no-ENTRY positions (174 un-disposed + 70 disposed), 4 of which are post-ledger requiring trace. Cohort cutoff was an artifact of when symptoms surfaced, not when the inflator mechanism stopped firing. |
| 8 | S202 | Plan revision should be lead-suspect candidate-list approach (carry S201's framing forward) | Plan revision needed abstract-mechanism-first sequencing — verify the inflator-mechanism class before assuming the next session's diagnostic shape. The lead-suspect approach would have re-anchored on S201's wrong-shaped framing. |
| 9 | S202 | 2026-04-10 incident is a Bug A residual inflator firing (per S201 §2.4 framing) | Falsified — pre-S193 FK race condition (different mechanism class). Market traded at 18:16:51, market record inserted at 18:17:56 (65s gap); pre-S193 `insert_trade_event` returned None silently on FK rejection. Class fully closed by S193 commit `73bc623`. |
| 10 | S202 | EB v2 idle since 2026-04-15 means "EB v2 is broken" | EB v2 is in deliberately-configured shadow mode (`ESPORTS_V2_DRY_RUN=true` in VPS .env gates `_execute_trades()` at `bots/esports_bot_v2.py:348-349`). Pipeline scanning every ~2 min, generating predictions, conformal-singleton filter producing non-zero gate-pass cohort. The "broken" framing was inherited from operational-status reading without checking the gate-flag layer. |
| 11 | S205 | H0''' (b) rolling-station-MAE elevated on cluster losers (aggregate signal) | Falsified at per-station drill-down — within the four cluster-window stations (KDFW Dallas, KORD Chicago, CYYZ Toronto, LEMD Madrid), the highest-MAE station (KORD) had its entries resolve as the model predicted while a lower-MAE station (KDFW) had its entries resolve opposite to the model. Aggregate signal was driven by station composition (LEMD pulled winner-median down), not within-station signal. |
| 12 | S205 | H0''' (c) city-volatility quantile widening — high-volatility cities should produce less-confident calibrator output (aggregate signal) | Falsified at per-station drill-down — same four cluster-window stations: KORD (highest 60d forecast-error std) entries resolved as predicted; KDFW (lower std) entries resolved opposite; LEMD (lowest) and CYYZ (mid) entries resolved as predicted. Same station-composition aggregate-artifact pattern as #11. |

Twelve inversions across eight sessions (S197 ran clean; S199 produced two; S200 produced one with cohort-redefining implications; S201 produced one boundary-redefining; S202 produced three across plan-revision, mechanism-trace, and operational-state framing; S205 produced two within one session day on a single hypothesis chain — both H0'' sub-candidates inverted by per-station drill-down on small samples). Each inversion was caught at the verification step, before the wrong fix shipped. The user-audit at S199 close named the codification trigger; the user-audit at S200 close named the framings-vs-hypotheses extension below; the S201–S202 instances ratify both codifications without requiring re-codification — the rule held; only the evidence base widened. **S205 small-sample sub-rule (codified takeaway from #11 + #12):** when the cohort under test has n<50 markets (or n<10 at the per-cell decomposition layer), aggregate ratios are concentration-dominated and require per-station/per-city/per-cohort-element decomposition before any signal claim. The bucket-concentration Protocol candidate at §Protocol candidates picks up this evidence point as an additive sub-mandate; codification within the framings-vs-hypotheses extension here, not as a separate protocol.

**Framings vs hypotheses (S200 extension).** MIIV applies to inherited *framings*, not just inherited *hypotheses* — verify the cohort definition, not just the candidate mechanism. A framing is the set of assumptions a hypothesis sits inside: which cohort the bug lives in, which bot is implicated, what shape the bug takes (mechanism family), what tooling output defines the cohort. Framings inherit the same authority as hypotheses — they survive handoffs unchallenged because each session inherits the prior session's scope-of-investigation. The S196→S199 framing of "Bug A as MB-shaped, partial-fill-related, `trade_coordinator.py`-rooted" survived four sessions until S200's MIIV cross-cohort verification revealed the cohort itself was wrong (32 windowed markets disjoint from the 9 all-time-entry-zero MB markets, and dwarfed by 64 WB markets).

**Practical rule.** Before running MIIV on a candidate hypothesis, verify the framing the candidate sits inside:
- Does the cohort the candidate operates on actually contain what the framing claims it contains? (e.g., "is the 32-market cohort actually the Bug A cohort, or is it a tooling-derived subset?")
- Does the bot-focus match the data? (e.g., "is the bug really MB-shaped, or is the prior framing just MB-biased because the diagnostic happened to start with MB?")
- Does the mechanism-family match the symptom shape? (e.g., "does partial-fill explain `entry=0` when partial-fills would still record the first chunk's ENTRY?")

If the framing fails any of these, the hypothesis-level MIIV is moot — the candidate may be falsifying within a wrong universe. Re-anchor the cohort before running candidate verification.

**Numbering note.** Protocol 7 takes the slot reserved for "triple-blind verification" candidate. Slots 8-10 remain reserved for the prior-session candidates per memory: diagnostic-inverts-remediation-space, cleanup-not-substitute-for-root-cause, silent-loop emission. Protocol 11's number is preserved per its own numbering note (no renumbering, audit trail).

---

### Protocol 11 — Per-mention citation: close the adjacent-paragraph loophole

**Mandate.** Every mention of a P&L / win-rate / trade-count number in user-visible output must carry an inline `bot_pnl.py` citation in the same paragraph, sentence, or table cell. The "adjacent paragraph that unambiguously binds the number" clause from Protocol 6's Minimum-evidence section is superseded — the stop-hook does not honor it semantically; it pattern-matches on per-paragraph adjacency. Six chain-instances of stop-hook firings have shown that reliance on the adjacent-paragraph clause produces violations. The protocol text must match the enforcement reality. Tables with explicit Source columns satisfy citation for their rows; prose paraphrasing or deriving from those rows must re-cite per mention.

**Why this is a separate protocol, not a Protocol 6 amendment.** Amending Protocol 6 in place would erase the audit trail behind the original adjacent-paragraph allowance. Protocol 11 supersedes the clause as a sharpening, preserves the audit trail, and makes the enforcement standard explicit so future sessions do not re-discover it through hook firings.

**Forbidden patterns.**
- Paraphrase of a number from earlier sourced output without inline citation (e.g., "all-time -$116K" referring to `-$116,509.63` shown in an earlier table).
- Derived numbers (sums, deltas) presented without inline derivation (e.g., "n=59 closed events" derived from "23 + 36" upstream, with no in-paragraph derivation shown).
- Qualitative framings that imply a specific number without citing it ("heavily negative," "tiny sample," "well above the threshold").

**Compliant patterns.**
- Reference by location instead of restatement: "see row 1," "per the SUMMARY block above."
- Inline derivation with citation: `RAW − CLEAN = -$116,868.99 − -$116,509.63 = -$359.36` (bot_pnl.py output).
- Tables with a Source column for every numeric row.

**Recovery procedure (when stop-hook fires).** Same as Protocol 6: strip offending numbers, retain qualitative findings that survive without them, re-cite for any number that must remain. Additionally — where the violation is a paraphrase from sourced output, prefer "see row N" over restatement.

**Out of scope.** Identical to Protocol 6's exemptions: configuration values from source code with file:line, arithmetic from config values, test counts, commit SHAs, deploy IDs, audit_runs.run_id, wall-clock times, schema_migrations rows. Protocol 6a's audit-check internal values carveout continues to apply when the surrounding claim is about check correctness rather than trading state.

**Evidence of origin.** Seven stop-hook firings across the S195 → S198(this) chain, each a Protocol 6 adjacent-paragraph-loophole instance:

| # | Session | Pattern | How caught |
|---|---------|---------|------------|
| 1-3 | S195 + S196 | Three documented in S196 close memo | Stop-hook |
| 4-5 | S197 | Two documented in S197 close memo | Stop-hook |
| 6 | S198 (this session) | Paraphrased "all-time -$116K"; derived "n=59 closed events" without inline derivation; "tiny sample" qualitative framing implying the n=59 derivation | Stop-hook |
| 7 | S198 (this session, codification response) | Cited "769 orphan `trade_events` rows" from `audit_triage.py` FK_INTEGRITY output without bot_pnl.py citation, in a side-findings paragraph of the same response that codified Protocol 11 | Stop-hook |

The Protocol 6 4-instance trigger was hit at #4 (S197); promotion deferred through #5 and #6. Codification at the 6-instance mark closes the trigger. Instance #7 fired on the codification response itself — an in-the-act catch. This validates that the mechanical hook is calibrated correctly; it does **not** validate that the underlying cognitive pattern producing the violation is trained out. Codifying the protocol is necessary for that training, not sufficient. If instance 8 occurs in the next session despite codification, the signal is to investigate why the cognitive pattern persists at the prevention level, not to add Protocol 12. What's working today is the catch-and-recover loop. Whether the prevent-side behavior is changed is a question future sessions answer, not this one.

**Interaction with stop-hook.** Protocol 11 aligns the protocol text with the stop-hook's actual mechanical behavior. Future sessions reading Protocol 11 will know per-paragraph adjacency is hard, not soft. The stop-hook pattern is the reference implementation; Protocol 11 is the human-readable description of what the hook enforces. If the hook ever surfaces a refinement of its matcher, Protocol 11 updates accordingly.

**Numbering note.** Protocols 7-10 are reserved for candidates filed in earlier sessions (diagnostic-inverts-remediation-space, triple-blind verification, architectural-cleanup-not-substitute-for-root-cause, silent-loop emission). Protocol 11 lands at the next available slot without preempting those reservations. When 7-10 promote, Protocol 11 retains its number — no renumbering — to preserve cross-session references.

---

### Protocol 12 — Pacing-model recalibration: bias towards concrete-shippable-this-session

**Mandate.** When writing close-session handoffs and forward-audit recommendations, frame next-session leads in terms of what one work-day can plausibly accomplish, not what the next session should defer for safety. Concretely:

- Recommended next-session scope should be expressed as ranked tracks with time estimates ("Track 1: ship X (1-2h); Track 2: ship Y (2-3h); Track 3 optional: start Z (60-120 min)") rather than "next session should defer Z, focus on X only."
- The default framing is "what is the next session likely to ship in one work-day at recent velocity," not "what is the safest minimal scope." Defer-recommendations should be reserved for cases with explicit gating (calendar locks, operator-blocked prereqs, missing tooling) — not as a context-budget-conservative default.
- Forward-audit horizons should track multi-session arcs (3+ sessions) rather than predict next-session specifically. The pacing model is recent diagnostic velocity, not worst-case context budget.

**Why this is needed.** A stale pacing model causes handoffs to recommend pre-deferred scope that the next session then has to re-plan from scratch, which itself burns context. The five-session S198→S202 chain demonstrated more diagnostic velocity than conservative pacing models predicted; recommendations biased towards "defer for safety" produced documented under-shoots in five consecutive sessions, including S202's "Pre-execution audit estimated ~30 min for EB diagnostic with P0-shaped deferral recommendation; both post-handoff items completed within time estimates." When a session systematically ships past its predecessor's recommended scope, the predecessor's pacing model is the thing being falsified — not the session's discipline.

**Out of scope.** Time-sensitive gates that genuinely block work (calendar-locked thresholds like MB Phase 7 verdict re-evaluation at n≥500, operator-blocked prereqs like VPS .env changes, tooling-blocked diagnostics). These are not pacing-model conservatism — they are real prerequisites. The rule applies to context-budget-conservative deferrals, not prerequisite-driven deferrals.

**Practical rule.** Before filing a "next session should defer X" recommendation in a close-session handoff, verify that the deferral is prerequisite-driven (calendar / operator / tooling) and not just safety-conservative. If it's safety-conservative, ship it as a ranked track with a time estimate instead.

**Evidence of origin.** Six confirming instances across S198–S203:

| # | Session | Predecessor handoff said... | Session actually shipped... |
|---|---------|------------------------------|------------------------------|
| 1 | S198 | "meta-session only, no deploys; await Phase 7 evaluation tooling" | Phase 4b-alt design + backfill design + Protocol 11 codification |
| 2 | S199 | "windowing tooling only" | Windowing tool + SHADOW_ENTRY auto-heal + Protocol 7 (MIIV) codification |
| 3 | S200 | "cohort-redefine investigation only" | Cohort redefine + framings-vs-hypotheses extension + cross-project boundary codification |
| 4 | S201 | "converge mechanism only" | Convergence + block-4 split + backfill script draft (273 lines) |
| 5 | S202 | "defer EB diagnostic; closure write-up only" | Closure write-up + EB diagnostic + routing audit prep + 2 hygiene items closed in post-handoff extension |
| 6 | S203 | "Track 5 OPTIONAL — only if Tracks 1-4 are committed and time permits" | Track 5 shipped + hygiene #12 (bot_pnl.py block 5 windowing) + canonical H0' verification |

S203's own execution is the sixth confirming instance: the session plan listed Track 5 as optional, and the audit-pacing recalibration that this protocol codifies was applied within S203 to take it on. Promotion threshold is therefore met by direct in-session evidence.

**Interaction with prior protocol-candidate accumulation.** This protocol's promotion at the third-instance threshold (counted against five-session evidence) is the same threshold that promoted Protocol 7 (3 instances) and Protocol 11 (6 instances). The diagnostic-inverts-remediation-space candidate (filed S195, ~7-8 instances per memory observations) and triple-blind verification (filed S194, 2+ instances) are both past or near their thresholds and remain in §Protocol candidates. Whether the candidate→protocol bar should be lowered to clear that backlog is a meta-question for §Protocol candidates section review, not this protocol's scope.

**Numbering note.** Protocol 12 takes the next available numeric slot. Slots 8-10 remain reserved for the candidates per Protocol 11's numbering note; promoting them keeps them at 8-10 with Protocol 12's number unchanged.

---

### Protocol 13 — Hierarchical infrastructure verification (substrate-level reads)

**Mandate.** Any claim about a *running* config value must be verified at the substrate where the setting actually takes effect, not at the default-query interface or source-code default. Different layers expose different views; reading the wrong layer produces false discrepancies (S186b D1/D3 pattern) or false confidence in a value that the runtime has actually overridden (S208 WEATHER_MIN_TRADE_USD pattern).

**Substrate map (concrete substitutions to apply).**
- **Env vars:** `os.getenv("KEY", "default")` shows the *fallback*. The running value lives in the .env file (or systemd `EnvironmentFile=`). SSH-read `.env` (or `/proc/PID/environ` for the running process) before claiming a runtime value.
- **PostgreSQL settings:** `systemctl show postgresql` returns wrapper-unit values, NOT the template-unit (`postgresql@.service`) values that instance units inherit. Use `pg_settings.sourcefile` from inside PG, or `systemctl cat postgresql@.service` for the template.
- **Partitioned-table constraints:** `SELECT indexname FROM pg_indexes WHERE tablename='trade_events'` returns parent-table indexes only. PG stores unique constraints as per-partition indexes by design. Query against partition names (e.g. `trade_events_2026_05`).
- **Connection poolers / container orchestration:** pool-level config exposes one slice; per-connection / per-replica state is the substrate where behavior is actually determined.

**Practical aid.** [scripts/config_drift_audit.py](scripts/config_drift_audit.py) (S208) enumerates all `os.getenv` calls in the codebase + parses the running .env + flags drifts. Run pre-deploy and per-session when any handoff cites a runtime config value sourced to settings.py.

**The verification question is NOT "did the default query return the expected result"** — it IS "is the query interrogating the substrate where the setting actually takes effect."

**Out of scope.** Static analysis claims (e.g. "the code reads from this env var") need source-code references, not runtime substrate reads — Protocol 13 applies only to *runtime-value* claims. Source-default claims are fine when explicitly framed as such (e.g. "the source-code default is $5 if the env var is unset").

**Evidence of origin (3 documented instances).**
1. **S186b D1 — PostgreSQL OOMScoreAdjust=-900** (2026-04-21). User-requested plan-vs-reality reconciliation flagged this as missing. Verifier was reading `systemctl show postgresql` (wrapper unit), but the OOMScoreAdjust setting is applied via the stock `postgresql@.service` template unit. False discrepancy caused by reading the wrong substrate.
2. **S186b D3 — trade_events RESOLUTION+EXIT unique indexes** (2026-04-21). Same investigation. Verifier was querying `pg_indexes WHERE tablename='trade_events'` (parent table), but the unique constraints are per-partition indexes from commit `8f0c69f`. False discrepancy.
3. **S208 — WEATHER_MIN_TRADE_USD $5 vs $15** (2026-05-02). S207 close §2.4 stated "Real bottleneck: WEATHER_MIN_TRADE_USD = $5.0 (config/settings.py + weather_bot.py:3339)" — citing the *source-code default*. SSH-read of VPS .env returned `WEATHER_MIN_TRADE_USD=15`. The source default ($5) was treated as the runtime value; actual override is $15. Triggered the broader [config drift audit](scripts/config_drift_audit.py) which surfaced 57 drifts across 814 env keys, including 8 training-disabled overrides (§S208 Hygiene Backlog #2), MirrorBot calibration off (§S208 #3), and stale memory entries (§S208 #4).

**Numbering note.** Protocol 13 takes the next available numeric slot. Slots 8-10 remain reserved for the candidates per Protocol 11's numbering note; promoting them keeps them at 8-10 with Protocol 13's number unchanged.

---

### Protocol 14 — Aggregate-statistics bucket-concentration check

**Mandate.** Before reporting any bucket-level aggregate statistic (mean, ratio, win-rate, P&L) computed over resolved-trade data grouped by some dimension (lead time, city, category, trader, time of day, regime, etc.), enumerate the bucket's underlying rows by `(entry_date × city × side)` (or the equivalent domain-specific triple) and verify that no single triple accounts for more than ~50% of the bucket's row count. If a single triple dominates, flag the bucket as "single-event-dominated" and present that bucket separately, NOT as an in-aggregate data point. Aggregating without this cardinality check produces apparent signals that are actually composition artifacts of one correlated event.

**Small-sample sub-mandate (additive).** When the cohort under test has n<50 markets total, OR n<10 at any per-cell decomposition layer, the 50%-cardinality check alone is insufficient. Aggregate ratios at small sample sizes are concentration-dominated by chance even when no single triple breaches 50%. Per-{station, city, side, regime} decomposition is required as a **default**, not as a fallback. Report aggregate AND per-cell drill-down side-by-side; if they conflict, the mechanism family is wrong and a feature-engineering candidate should NOT be filed — pivot to risk-reduction (sizing dampener, abstention gate) instead.

**Practical aid.** [scripts/wb_bucket_concentration.py](scripts/wb_bucket_concentration.py) (S204 commit `cf3059b`, promoted from S203 untracked one-shot) is the canonical tooling. Run before any per-cohort calibration-failure investigation that proposes a feature-engineering or recalibration recommendation.

**Verification question is NOT "did the bucket's headline statistic match the hypothesis"** — it IS "is the bucket's row composition consistent with the dimension being a real signal driver, or does one correlated event explain the bucket's headline."

**Out of scope.** Population-scale aggregates over full bot history (n>>1000) where the per-(date × city × side) cardinality is structurally bounded by the bot's daily trading volume — those don't exhibit single-event domination by construction. Protocol 14 applies to bucketed cohorts where any per-cell layer could plausibly be dominated by a single correlated event. Also out of scope: descriptive reporting of historical performance (e.g., "WeatherBot lost $X on day Y") — Protocol 14 governs the inferential step from bucket statistic to mechanism claim, not the descriptive layer.

**Evidence of origin (2 documented instances + 1 small-sample twin).**
1. **S185 (2026-04-20) — 6O WB lead-time backtest.** Lead-time bucket aggregation produced a dramatic apparent signal at the longest populated lead-time bucket. Per-(entry_date × city × side) drill-down collapsed the bucket to a single correlated-blowup cluster — the pattern already documented in WB S119 memory. Without the cardinality check, would have produced a wrong multiplier-retune recommendation. See §S186 Corrections Log "6O lead-time backtest deferred indefinitely."
2. **S205 (2026-04-30) — H0''' twin (rolling-station-MAE + city-volatility).** Two hypothesis-test branches on a 4-station cluster cohort (n<10 per station per side). Both branches' aggregate cluster-vs-cluster ratios appeared to confirm the hypotheses; per-station decomposition inverted both — the highest-MAE / highest-volatility stations were *winning*, not losing. See §Protocol 7 instance table line items 11 and 12, and §S205 Corrections Log entry. Two firings of the same shape on a single cohort cluster within one session day — supplied the small-sample sub-mandate.

**Review cadence (filed S210 per operator request 2026-05-02).** Operator-requested 3-day review checkpoint: 2026-05-05. At that checkpoint, evaluate whether Protocol 14 has produced an in-the-wild catch (intended effect) or sat dormant (codification not yet load-bearing — soft observation, not a problem). If 1+ catch occurred and the protocol guided the analysis to the right pivot, codification is validated. If no catches but the protocol was relevant to investigation work that happened in the 3-day window, the soft codification is doing its job. If no relevance triggers in 3 days, defer next review to 2026-05-12 (one-week cadence) and continue.

**Numbering note.** Protocol 14 takes the next available numeric slot after Protocol 13. Slots 8-10 remain reserved per Protocol 11's numbering note. The candidate text recommended landing as either "Protocol 4d (aggregate bucket concentration)" or "next available numeric slot after Protocol 12"; the latter convention is followed (matching Protocol 13's own slot choice in S208).

---

### Protocol candidates — awaiting next protocol-hygiene round

Flagged mid-session; not yet binding rules. Listed so they don't get lost between sessions, and so the evidence base can accumulate before promotion.

**SQL-contract verification against a live DB before commit.** Mocked-session unit tests cannot catch CHECK-constraint violations, undefined-column errors, bad joins, or any other string-vs-schema mismatch. S184 shipped two such bugs in a single session: `7b0b8ac` (CHECK violation, caught pre-production by Protocol 5 schema-read) and `535c14e` (undefined-column error in the `TradedMarketsStatusDriftCheck` query, caught post-deploy via journal — `UndefinedColumnError` on `pt.entry_time`/`exit_time`, the actual columns being `created_at`/`resolved_at`). Both were invisible to mocked unit tests and would have been caught by running the changed query against a real DB before commit. Candidate discipline: for commits that add or modify SQL in audit checks, factory queries, or any `session.execute(text(...))` path, execute the query against the VPS dev DB (or an equivalent) before commit. Promote to §Protocols (likely Protocol 7) if a third instance ships. **S186 partial precedent:** the S186 PSM port applied this discipline voluntarily (`e19815e` verified against live VPS data before commit), catching the S164-pattern-inheritance structural error. Not a "shipped bug then caught" instance like the prior two, but evidence that the discipline produces real catches when applied — strengthens the candidate for promotion.

**Aggregate-statistics bucket-concentration check — PROMOTED to Protocol 14 in S210 (2026-05-02).** Originally filed S185 (2026-04-20) per the 6O WB lead-time backtest near-miss (single `(entry_date, city, side)` triple drove the longest-lead-time bucket's dramatic apparent signal — caught at drill-down before a wrong multiplier-retune recommendation shipped). Evidence base extended S205 (2026-04-30) by the H0''' twin: two hypothesis-test branches (rolling-station-MAE + city-volatility) on a 4-station cluster cohort (n<10 per station per side) both produced aggregate-statistic apparent-confirmations that per-station drill-down inverted (highest-MAE / highest-volatility stations were *winning*, not losing). Two firings of the same shape supplied the small-sample sub-mandate. Promoted in S210 per operator approval at 2 evidence points (one less than the 3-instance threshold used for Protocols 7/13) — the small-sample twin counts as a single evidence point but generated TWO simultaneous false-confirmation paths, strengthening the rule's specificity. See `### Protocol 14 — Aggregate-statistics bucket-concentration check` above for the full codified text. Pointer kept here for audit trail across cross-session references to "the bucket-concentration candidate." 3-day review checkpoint 2026-05-05 per operator request — see Protocol 14's "Review cadence" clause.

**Hierarchical infrastructure verification — PROMOTED to Protocol 13 in S208 (2026-05-02).** Originally filed S186b (2026-04-21) as a candidate after the user-requested full plan-vs-reality reconciliation flagged two discrepancies (D1 PG OOMScoreAdjust=-900, D3 trade_events RESOLUTION+EXIT unique indexes) caused by reading the wrong substrate level (systemctl wrapper-unit vs template-unit, parent-table indexes vs per-partition indexes). Promoted in S208 when the WEATHER_MIN_TRADE_USD finding (S207 close §2.4 cited source-code default $5 as if it were the runtime value; actual VPS .env override was $15) supplied the third confirming instance. The S208 finding triggered [scripts/config_drift_audit.py](scripts/config_drift_audit.py) which surfaced 57 drifts across 814 env keys (see §S208 Hygiene Backlog). See `### Protocol 13 — Hierarchical infrastructure verification` above for the full codified text. Pointer kept here for audit trail across cross-session references to "the hierarchical-infra-verification candidate."

**Review-state-discipline.** Reviews based on incomplete session state produce false-positive critiques. Reviewers must verify session is closed (or explicitly note "mid-session snapshot") before substantive critique. Right discipline: establish session state first ("what's the current commit chain? what's in flight? what's drafted vs committed?"), then critique only what's actually current. Sub-rules: (a) search for prior verification before asking for verification — if a question reads like "verify X is true," grep the plan and recent commits for prior verification first; (b) distinguish draft critique from committed critique — when critiquing text that may still be in revision, frame conditionally ("if this lands as drafted, X concern; if revised, moot") not absolutely; (c) operator-decided items are not the reviewer's to defend — if the operator has chosen a path, the reviewer's responsibility is to verify the commit reflects the choice, not to critique the draft as if the operator hadn't decided. **Evidence of origin:** S208 close-review (2026-05-02). Reviewer reviewed at session-mid without flagging in-flight state; produced 4 critiques (deploy-gap count, day-timer disjunction asymmetry, partial 6Q-D1 framing, "D1 has no staging") that were either stale (1 commit chain ahead of reviewer's snapshot, with 6 commits the reviewer hadn't seen) or addressed by operator decisions the reviewer didn't account for. Net 4/14 false-positive critiques (~29% noise rate). Reviewer self-flagged the pattern post-rebuttal-response as a Protocol-candidate-shaped observation. **Candidate disposition:** 1 instance, monitor for second instance not caught by reviewer self-correction. Promote to numbered protocol when a second review session produces stale-snapshot critiques that the reviewer doesn't self-flag (i.e., the soft self-correction discipline breaks). Filed S208 (2026-05-02) per S208 rebuttal-review meta.

**Plan-deviation discipline.** When a session ships code that deviates from the §S172 plan's documented prerequisites, gates, or sequencing, the deviation must be either (a) pre-approved by the operator before commit, (b) documented in §Corrections Log within one session of detection, or (c) carry an explicit operator sign-off recorded in the close handoff. Without one of these three paths, plan deviations accumulate silently — future sessions reading the plan see the catalog row as "future planned with prerequisites" while the code is already shipped under different assumptions. Soft-convention status today: deviations DO get §Corrections Log entries (S205 6Q is the most recent worked example) but the convention is implicit, not codified — relying on session-by-session discipline rather than a binding rule. **Evidence-of-origin (1 instance, audit-corrected S208 from initial 3-claim per §S207 review — see §S207 Hygiene Backlog #4):** (i) S205 6Q shipped without 6D/6E prerequisite + CRPS/Brier trigger threshold being met; deviation rationale: feature-engineering exhausted via H0'' chain — caught + recorded in §S205 Corrections Log via post-close extension. **Initially-claimed-but-rejected by S207 audit:** (ii) S195 SQL `--` fix (`b82ad68`) was discovery-driven Root Cause #1 of a silent-zero pathology — no catalog row with prerequisites existed to deviate from (per AGENT_HANDOFF_S195_CLOSE.md §1; S195's own plan-hygiene candidates were Protocols 7/8/9/10, NONE was "plan-deviation"). (iii) S201/S202 `bot_pnl.py` block 4 split (`b125a5e`) was labeled "fix: tooling-trust prerequisite" per AGENT_HANDOFF_S201_CLOSE.md Table — tooling fix that enabled further analysis; no violated phase-plan prerequisite. Both don't fit the plan-deviation definition; real count is 1. **Candidate disposition:** codify as a numbered protocol after a **second genuine instance** ships AND the deviation isn't caught by either §Corrections Log discipline or operator sign-off (i.e., the existing soft conventions break). Until then, soft conventions are working — codification is premature. **Promotion-threshold refinement (S209 plan-review):** beyond the second-instance + soft-convention-break requirement, promotion also requires a documented negative case — a session where something looked like a plan-deviation but was investigated and ruled out — to anchor what the candidate is screening against. Without a negative case, every future "shipped before catalog row prerequisites met" pattern looks like an instance, with no discriminator for the legitimate "shipped without violating prerequisites because no prerequisites existed" case (e.g., S195 SQL `--` fix `b82ad68` and S201/S202 block-4 split `b125a5e`, both ruled out per the S207 audit recorded in §S207 Hygiene #4 and the S208 audit-correction commit `4ea441e`). The two ruled-out cases are de-facto negative cases — the candidate's specificity rests on those, not on a single positive instance. Filed S206 (2026-04-30) per S206 review observation #1; count audit-corrected S208 (2026-05-02) from 3→1; promotion-threshold refinement S209 (2026-05-02) per S209 plan-review.

**Pacing-model recalibration — PROMOTED to Protocol 12 in S203 (2026-04-29).** Originally filed S203 as a candidate after the S198–S202 five-session pattern was identified. Promoted within the same session (post-close, after the user-audit observation that "five-instance evidence is past threshold") when the S203 Track 5 execution itself supplied the sixth confirming instance — the session shipped Track 5 (which the S202 close had recommended deferring as optional). See `### Protocol 12 — Pacing-model recalibration` above for the full codified text. Pointer kept here for audit trail across cross-session references to "the pacing-model candidate."

**Operator-decision-queue batching — PROMOTED to §Operational Procedures in S210 (2026-05-03).** Originally filed S209 (2026-05-02) per S209 plan-review meta-hygiene observations after S208 close handoff §5 surfaced 6 operator-blocking decisions at peak. Codification trigger per the candidate's own threshold ("Codify after one session of observed-effective batched-decision-pass") fired in S210: the S209 close handoff §0 batched 4 items (Lead 4 EB v2 verdict, bucket-concentration promotion, pre-send P11 self-check, audit-corrections-bundling), operator processed all 4 in one batched response, and S210 shipped 3 codifications + 1 trade-flip in the same session. The S209 filing's "≥5 items" threshold was refined to "≥4 items" based on S210's working example — pattern worked at 4. See `### §Operational Procedures` "Operator-decision-queue batching" bullet for the full codified text. Pointer kept here for audit trail across cross-session references to "the operator-decision-queue batching candidate."

**Falsifiability-clause hardness — meta-tripwire for tripwire patterns.** The recent chain codifies falsifiability clauses for closures: Bug A (h) re-open conditions H1–H4; 6Q "mitigation-not-closure" framing with re-evaluation triggers; D1 rollback trigger with revert criterion. This is good closure-discipline. But the falsifiability clauses themselves carry no falsifiability clause — under what condition does the H1–H4 trigger mechanism itself get revisited? Under what condition is the D1 rollback threshold itself revisited? Without a meta-tripwire, a closure stays "closed" even if the underlying problem recurs via paths the original tripwire conditions don't capture. Candidate discipline: each closure-with-tripwire pattern carries either (a) a periodic-review cadence ("H1–H4 re-evaluated quarterly even if no trigger fires") or (b) a tripwire-coverage-gap signal ("if a Bug A-shape symptom recurs that doesn't match H1–H4, file as a tripwire-coverage gap and re-open the diagnostic"). **Evidence-of-origin (0 instances):** zero tripwire-recursion failures observed. Candidate is structural-shape only, not yet evidence-driven — filed for tracking so future agents notice if the pattern starts firing. **Promotion threshold:** 1st observed instance of a closure-with-tripwire that stays closed despite the underlying problem recurring via a path the original tripwire didn't capture. Filed S209 (2026-05-02) per S209 plan-review meta-hygiene observations.

**Verdict-driving tooling verification.** When a script's output is used as the basis for an operational decision (park, flip, deploy, revert, rollback, sizing change, etc.), the script's correctness must be verified — at minimum via test coverage on the verdict-producing function, code review by a second pass, OR a sample input/output sanity check — BEFORE the decision is finalized. Tooling that drives plan deviations or operational flips deserves the highest verification bar: same as the surface it's gating. **Why this is a separate candidate, not a Protocol 6 amendment.** Protocol 6 governs canonical-source citation discipline ("cite `bot_pnl.py`"); this candidate governs whether the canonical source is itself correct. Distinct enforcement layer. **Evidence of origin (2 instances, promotion-ready per the bucket-concentration / Protocol 14 precedent):** (i) **S195 SQL `--` (silent-zero pathology, 17 days).** Production tooling at `insert_trade_event` RESOLUTION INSERT had a `--` SQL line comment that swallowed a tail clause; produced silent-zero RESOLUTION events; drove plan decisions about Bug A class for 17 days before discovery. Fixed in commit `b82ad68` via `/* ... */` block comment. (ii) **S207 Gate 5v2-C eval script Brier formula bug.** `scripts/esports_v2_shadow_eval.py` computed Brier as `(p_model - 1{predicted_winner == actual_winner})²` but `p_model = P(team_a wins)` — outcome label inverted for predictions where p_model<0.5. Compounding bug: BSS climatology used `accuracy*(1-accuracy)` instead of `mean(y_a)*(1-mean(y_a))`. Both bugs pushed verdict toward FAIL/negative-BSS. Drove the §S208 PARK decision for ~1 month before discovery. Fixed in commit `5ecda0f` (S209). **Action:** promotion-ready at 2 instances per the S210 bucket-concentration precedent (also promoted at 2). Promote when next operator-decision authorizes; until then, file as Protocol candidate. Disposition: candidate, recommended landing slot Protocol 15 (next available after Protocol 14). Filed S210 (2026-05-03) per S210 review.

**Verify-before-mark-RESOLVED.** Items marked RESOLVED in §Hygiene Backlogs and §Corrections Log entries should have either (a) full verification across all stated scope, or (b) explicit qualifier on the RESOLVED tag (e.g., "RESOLVED locally; production deferred"). The current convention treats RESOLVED as binary, but the underlying verification often isn't binary — partial passes, deferred sub-scopes, and not-yet-replicated production-side validations are common. **Evidence of origin (1 instance):** S210 §S208 Hygiene #7 closure (`getattr(settings, ...)` audit-script extension) was initially marked RESOLVED based on local-only verification; the VPS production run hung and the production count vs S208 baseline of 18 was not captured. The local pass is theoretical recovery; the operational recovery is unverified. Tag was qualified post-review to "RESOLVED locally; production deferred pending VPS-hang investigation." **Candidate disposition:** 1-instance file; promote at second occurrence (i.e., another RESOLVED tag whose underlying verification is partial in a way the tag doesn't surface). Filed S210 (2026-05-03) per S210 review. **Why this is a separate candidate, not a Protocol 5 amendment.** Protocol 5 governs phase-level status claims requiring shipped-code verification; this candidate governs RESOLVED-tag fidelity for hygiene/correction items, which are smaller-grained than phase-level claims. Distinct application surface.

**Review-rebuttal-action chain as filter mechanism (§Operational Procedures candidate).** Critical reviews should produce comprehensive critiques expecting some to be filtered; agents should rebut with primary-evidence references rather than accepting reflexively. The filter mechanism — review surfaces signal+noise, agent's rebuttal converts the signal into action and the noise into stale-snapshot rebuttals — produces calibrated outcomes. **Evidence of origin (2 instances, promotion-ready):** (i) **S208→S209 chain.** S208 close-review surfaced 4 stale-snapshot critiques (1 commit chain ahead of reviewer's snapshot) + 7 valid critiques. Agent rebuttal correctly accepted the 7 valid, rebutted the 4 stale, deferred legitimate-but-not-this-session items. Net 4/14 false-positive rate (~29% noise). (ii) **S210→S210-review chain (this conversation).** S210 review surfaced 8 critiques. Agent rebuttal accepted 4 valid (verify-EB-v2-trade was load-bearing, surfaced 6th wiring gap; Lead 6 partial verification; ≥4 threshold needs upper bound; verdict-driving tooling Protocol candidate is right), partially accepted 2 (§S208 Hygiene #9 by-pointer is rebutted at plan-text level but accepted at chat-summary level; ≥4 threshold rebuttal accepted with refinement note), rebutted 2 stale-snapshot (audit-corrections-bundling already codified S210 commit `f0b405b`; MEMORY.md follows convention). Net 2/8 stale-snapshot rate (~25% noise) — comparable to S208→S209 calibration. **Promotion-ready at 2 instances per the S210 bucket-concentration precedent.** Action: promote when next operator-decision authorizes; until then, candidate disposition. Filed S210 (2026-05-03) per S210 review.

**Parallel-agent dispatch as default for independent work (§Operational Procedures candidate).** When session work has ≥3 independent leads (no shared file edits, no sequencing dependency), default to parallel-agent dispatch via a single message with multiple Agent tool uses. The dispatch closes N independent items in roughly the time of one sequential item. Sequential dispatch on parallelizable work is the failure mode — recent chain has had multiple sessions where 3-4 independent investigations ran sequentially when they could have run in parallel. **Evidence of origin (1 instance):** S210 Lead 4 (plan-deviation positive sweep S198–S203) + Lead 5 (Protocol 7 row 2/8 audit) + Lead 6 (config_drift_audit `getattr(settings, ...)` extension) + Lead 7 (operator-decision-queue batching codification). Three Agent dispatches in parallel + one direct codification. All four backlog items closed in ~3.5 min wall-clock of agent duration. **Candidate disposition:** 1-instance file; promote at second occurrence where the dispatch shape is observed-effective. Filed S210 (2026-05-03) per S210 review.

---

### Out-of-scope for this protocols section

Session-specific narratives (what a particular session decided, what commit landed where) belong in handoff files and memory, not here. This section is for **durable binding rules** only. Every addition must be a rule generalizable across bots and sessions, and every protocol must carry a scope clause, an out-of-scope clause, and an evidence-of-origin entry so future agents can judge applicability to their own context.

---

## Bug A Diagnostic Closure

**Status:** Diagnostic project closed (S202, 2026-04-29). Bug at the system level: see (h) re-open conditions and (i) closure scope. **Not** unconditionally closed — closure is scoped to the diagnostic question, not to the bug as a system invariant.

**Bug A history.** Active diagnostic from S178 through S201 across the WB → MB → WB-corrected framing arc. Symptom population: markets where `bot_pnl.py` block 4 reported lifetime `SUM(EXIT+RESOLUTION) > SUM(ENTRY) * 1.001`. Triggered SIZE_INVARIANT, ORPHAN_RESOLUTION, POSITION_SIZE_MISMATCH audit checks. S196-S199 working framing was MB-shaped + partial-fill-related; S200 cohort re-anchor (commit `5bc6aa4`) revealed the real cohort was 73-market WB-dominant (64 WB + 9 MB + 1 EB; the per-bot breakdown sums to 74 — the "73" in S200/S201 prose was an arithmetic typo, **the actual cohort has always been 74**). S201 converged on the inflator mechanism. S202 backfilled the historical residue and traced the one ongoing post-ledger residual.

### (a) Inflator mechanism — pre-ledger UPSERT cumulation

Pre-ledger writers UPSERTed `positions` rows on `(market, side, bot)` keyed re-entries. Each re-entry cumulated `positions.size` into the existing row while preserving `positions.entry_cost` at the first-entry value. Without a `trade_events` ledger to track per-entry increments, the cumulative sum looked like a single inflated entry post-resolution. Inflation factor: ~67× consistent across 5/5 sampled markets in S201 (verified per S201 handoff §2.3, market `0x562e6a4cd106e6bd8f55f6a5ba5de91c71c817464ba787c83cc7185cd082745d` and 4 generalization picks).

### (b) Emitter — Phase 4b-alt RESOLUTION sweep

The downstream symptom propagator is `backfill_trade_events_resolution` at [base_engine/data/database.py:3629-3739](base_engine/data/database.py:3629). Pre-S197 read `positions.size` directly. S197 commit `0e1f2e0` added GREATEST/LEAST clamp via `trade_events.ENTRY` truth at [database.py:3661](base_engine/data/database.py:3661), but preserved `COALESCE(te_entry_agg.total_entry, p.size)` as backward-compat fallback for markets without ENTRY events.

### (c) S197 partial protection scope

S197's clamp protects RESOLUTION emission only when `te_entry_agg.total_entry IS NOT NULL`. The S202 backfill (see (e)) populates ENTRY events for the historical cohort, lifting them into S197's protected path going forward. For pre-S202 RESOLUTION emissions, the historical inflated values are already in `trade_events.RESOLUTION` rows; the S202 backfill does NOT rewrite those — it adds correct-original-size ENTRY events alongside, leaving the residual as a SIZE_INVARIANT marker (see (e) intent).

### (d) Post-ledger residual risk profile

Per S201 handoff §2.4: 4 firings in 46 days, all `size=0` symptom-not-propagated. Inflator is "extremely-low-frequency" not "closed." Pre-S202 latest known firing was 2026-04-10. **S202 trace re-classified the 2026-04-10 incident as a different mechanism (see (f)) — not pre-ledger UPSERT cumulation.** Removing 2026-04-10 from the inflator count leaves **3 firings in 46 days max** (= 1 / 15.3 days at the cohort's known size; still tighter than S201's 1/11.5 framing, still below the H2 re-open threshold of 1/month, and the bound is itself an upper bound — any of the remaining 3 may also re-classify on closer inspection). The actual post-ledger inflator residual rate is bounded below 3/46; the all-`size=0` empirical bound still holds: zero post-ledger firings of (b)'s mechanism have produced a propagated symptom. (S203 sharpening — original 4-firings-in-46-days count from S201 over-attributed residual risk to the inflator mechanism.)

### (e) Backfill execution outcome (S202)

`scripts/backfill_pre_ledger_entries.py` (commit `db57194`) deployed and executed against prod 2026-04-29 (release `20260429_134741`). Pre-flight: cohort=74 (matches documented split modulo prose typo), in-scope=67 position rows, distinct in-scope markets=65, OPEN ORPHAN_RESOLUTION=65. Post-execution: ENTRY events inserted=65, skipped (NOT EXISTS guard)=2, ORPHAN_RESOLUTION breaks closed=65, OPEN ORPHAN_RESOLUTION (in-scope)=0. SIZE_INVARIANT residue preserved as historical-inflation marker per script docstring intent.

Two hygiene findings from execution (filed in §S202 hygiene backlog):
- The script's post-flight `in_scope_still_orphan == 0` assertion fired (1 residual) due to a cross-bot-share artifact: market `0xed49c99283ad7c5cfe2c0a` is in cohort under both EB (positions joinable) and MB (no positions); EB got backfilled, MB residue persisted. The assertion is overstrict relative to the script's actual cleanup intent.
- The `(bot, market)` NOT EXISTS guard dropped second-side ENTRY events on 2 dual-sided markets (`0x052591da21e7bb3db95aae`, `0x57e1ba8e4a1581d005bbee`). By-design idempotency but reduces fidelity for markets with both YES + NO positions.

### (f) 2026-04-10 trace finding (S202) — different mechanism class

Phase 4 trace of market `0xe05169d4db5253e574af2bcdc4db0eee2019706c97e36d2756d4280edb71427d` converged on a different mechanism than the pre-ledger UPSERT cumulation: **pre-S193 FK race condition.** The bot traded the market at 2026-04-10 18:16:51 (paper_trade.submitted_at) but the market record was inserted into `markets` at 18:17:56 — 65 seconds later. Pre-S193, `insert_trade_event` returned `None` silently when FK on `market_id` failed; paper_trade was committed via the asyncio.gather None-swallow path identified in S199; positions row was created via a separate write path that has no FK constraint; ENTRY trade_event was never emitted. The mechanism is fully closed going forward by S193 commit `73bc623` (deployed 2026-04-23, release `20260423_212538`) — auto-heal inserts a stub markets row + retries.

Class size at S202: 17 (bot, market) NO+SELL position pairs without ENTRY trade_events (15 WB + 2 EB). 16 are pre-ledger-era (2026-03-08 to 2026-03-12); 1 is the 2026-04-10 target. Zero post-S193-deploy instances. Class is bounded and closed.

The "1 OPEN ORPHAN_RESOLUTION on `0xe05169d4db52...`" remains as documented historical residue (filed for operator decision: ACK or leave OPEN as documented incident). Phase 4b-alt's RESOLUTION emission for this market correctly had `size=0` (no inflation propagation) — the symptom is purely the audit's missing-ENTRY check, not a downstream consumer reading inflated state.

### (g) Residual writer surface — S197 COALESCE fallback

The `COALESCE(te_entry_agg.total_entry, p.size)` at [database.py:3661](base_engine/data/database.py:3661) intentionally preserves backward-compat for markets without ENTRY events. For any FUTURE inflator firing that produces a market with `size > 0` at disposal AND no ENTRY events, the COALESCE fallback would copy the inflated value into RESOLUTION. S202 backfill removes the historical cohort from this risk surface (they now have ENTRY events). Mitigation candidates filed as design discussion (S201 handoff §3 item 5), not action this session.

### (h) Re-open conditions (the falsifiability clause)

Bug A returns to active-diagnostic status if **any** of the following triggers fire:

- **Trigger H1**: a single occurrence of inflated `positions.size` reaches a non-RESOLUTION downstream consumer — Kelly sizing, balance display, exposure cap evaluator, audit-visible symptom in non-RESOLUTION path. A SIZE_INVARIANT alert on a post-S202 market (i.e., not in the 64-WB + 1-EB historical cohort) is the canary signal.
- **Trigger H2**: a fifth post-ledger inflator firing brings the post-ledger frequency above 1-per-month. Currently bounded below 3 firings in 46 days (= 1 / 15.3 days; S203 sharpened from S201's 4/46 framing after S202's re-classification of 2026-04-10 as pre-S193 FK race rather than inflator firing — see (d) and (f)). Threshold tightens once cohort enlarges; floor is operator-judgmental.
- **Trigger H3**: Phase 4b-alt's `WHERE effective_size > 0` filter is modified, lighting up the 174 un-disposed positions that are currently inert (S201 handoff §3 item 4). Backfilling them would create artifact ENTRY events for trades that didn't happen — explicit out-of-scope.
- **Trigger H4**: the COALESCE fallback at [database.py:3661](base_engine/data/database.py:3661) is exercised for a market with `size > 0` at disposal — i.e., the post-S202 ENTRY-emission path regressed and a market reached RESOLUTION sweep without an ENTRY event AND with positions.size > 0.

### (i) Diagnostic vs system closure distinction

This section closes the **diagnostic project** for Bug A: mechanism is identified (a), emitter is identified (b), the historical cohort is bounded (e), the one ongoing residual is reclassified to a different mechanism (f), and the going-forward surface is documented (g, h).

It does **not** close the bug at the system level. Residual writer surface (g) remains as intentional backward-compat. Un-disposed-position contingent risk (h H3) remains as inert-but-tripwire-ready. Unaudited consumer paths (h H1) remain — only RESOLUTION-emission has been explicitly hardened. Future sessions encountering Bug A symptoms should reference (h) re-open conditions to determine whether to re-open as active.

The closure is therefore: **scoped, bounded, and falsifiable** — not unconditional.

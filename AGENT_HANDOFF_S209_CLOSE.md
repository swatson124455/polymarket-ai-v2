# S209 CLOSE HANDOFF

**Date:** 2026-05-02
**Predecessor:** S208 close (`a89e0bc`, docs-only, post-S205-deploy `20260430_092402`). See `AGENT_HANDOFF_S208_CLOSE.md` for predecessor context including the §S208 PARK decision for EB v2 (now superseded — see §1 below).
**Master HEAD:** `1d5996d`
**Master vs VPS:** 20 ahead of `7c60938` (post-S205 release `20260430_092402`); 17 docs-only + 2 utility-script edits + 1 utility-script add carried from S208. Code-only commit count = 0; no deploy needed.
**Tests at HEAD:** unchanged from S208. The eval-script fix this session is a utility CLI with no callers in production code, not in test sweep (consistent with S208's framing for `scripts/config_drift_audit.py` and `scripts/esports_v2_shadow_eval.py`).

---

## §0 Operator Decisions Pending — batched for one-pass review

Per the operator-decision-queue batching candidate filed S209 (commit `1d5996d`, see §S172 plan §Protocol candidates). When ≥5 operator-blocking items accumulate across the session chain, a top-of-handoff batch block is preferable to per-§5-item presentation. Current queue at S209 close:

**1. Lead 4 — EB v2 verdict response (NEW priority post-§S209 verdict reversal).** The §S208 PARK decision rationale ("FAIL with negative BSS, structural failure") no longer holds at the trade-eligible cohort post-eval-fix. Four sub-options surfaced post-pushback:
- **(a-restricted)** Game-conditional flip — `ESPORTS_V2_DRY_RUN=false` for LoL only; CS2 stays paper or dampened. Requires per-game env var or game-conditional sizing code change. LoL singletons clear the gate cleanly across all-time / 14d / 7d windows; CS2 singletons fail the Brier gate in all-time and 14d, marginal in 7d (per `S209_EB_V2_CORRECTED_VERDICT.txt` / `S209_EB_V2_RECENCY_14D.txt` / `S209_EB_V2_RECENCY_7D.txt` at repo root).
- **(a-unrestricted)** Flip both games. Singleton overall passes the partial verdict; carries explicit risk of trading on CS2 where the model does not currently pass the Brier gate. Pre-flip rollback trigger should be defined (parallel to D1's pattern): per-game P(edge>0) on early live trades with revert criteria per game.
- **(b)** Per-game validation before any flip — set explicit per-game gate thresholds with sustained-window requirements (e.g., "CS2 singleton Brier <0.25 sustained over 14d before CS2 flip"). LoL meets this today; CS2 doesn't. Effectively (a-restricted) staged with documented criteria.
- **(c)** Hold pending CLV / backtest-drop infrastructure. The corrected verdict doesn't change what's measurable for Gate 5v2-C metrics 3 (CLV) and 4 (backtest-to-shadow drop). Gates on Issues 7 of the §S209 investigation report (resolution-time market price capture + persisted backtest results).

**2. Bucket-concentration Protocol promotion (S206-S208 standing).** Promotion-ready at 2 evidence points (S185 6O lead-time + S205 H0''' twin) + small-sample sub-mandate. Recommended landing slot: Protocol 14 or "Protocol 4d (aggregate bucket concentration)" per the candidate's filing language. See §S172 plan §Protocol candidates.

**3. Pre-send Protocol 11 self-check codification (S208 deferral, Item 4 in S209 plan-review).** The principle is already codified in CLAUDE.md as Forbidden Patterns rule #10 (S208). Open question: whether to additionally elevate to §Session Template with the detailed self-check format proposed in S209 plan-review Item 4 (4-step verification: bot_pnl.py → non-bot_pnl.py canonical → paraphrased-from-prior → operator-memory). Codification once approved is ~5 minutes.

**4. Audit-corrections-bundling pattern codification (S208 hygiene candidate, S209 elevation).** Two-session precedent now (S208 commit `177ac75` + S209 commit `5ecda0f` both used the pattern). Per the §Operational Procedures candidate's "second-instance use" threshold (operator-decision-queue batching candidate), this is now codifiable as a documented practice.

---

## 1. HEADLINE

S209 was a focused two-track session: investigate-and-fix EB v2's eval tooling, and file three meta-hygiene candidates surfaced by the in-session plan review.

**Track 1 — EB v2 eval bug fix + verdict reversal.** Operator selected path (b) "investigate" at S209 open, overriding the §S208 PARK choice. Investigation surfaced two bugs in `scripts/esports_v2_shadow_eval.py`: (i) Brier formula inverted outcomes for predictions where `p_model<0.5` (the model picks team_b); (ii) BSS climatology used `accuracy*(1-accuracy)` instead of the actual event base rate. Both bugs pushed aggregates toward FAIL/negative-BSS. Fix shipped in commit `5ecda0f` along with a singleton-only verdict block (the trade-eligible cohort that would actually trade in Phase 5v2-D) and a §S209 Corrections Log entry that explicitly supersedes the §S208 PARK rationale. Three corrected outputs captured at repo root (all-time, 14d, 7d windows). Verdict reversal: singleton overall passes the partial Gate 5v2-C verdict on the two measurable metrics; LoL singletons pass cleanly; CS2 singletons remain marginal-to-failing across windows. The original "structural failure / negative BSS" framing was a script artifact, but per-game CS2 weakness survives the correction.

**Track 2 — Meta-hygiene candidates.** Three candidates filed in commit `1d5996d`: (a) operator-decision-queue batching (§Operational Procedures candidate, 1-instance evidence base); (b) falsifiability-clause hardness (Protocol candidate, 0-instance structural-shape); (c) plan-deviation negative-case anchoring (refinement to existing Plan-deviation candidate to require a documented negative case before promotion).

**Process observation.** S209 ran a tight review-and-refinement loop. Initial framing of Lead 4 collapsed (a-restricted) and (a-unrestricted) into a single "(a) flip" option, biasing the framing toward action; pushback caught this; re-framing produced four sub-options with the per-game evidence asymmetry (LoL passes, CS2 borderline) explicitly visible. Captured outputs added before re-framing so the operator decision space is now defensibly grounded.

The session's value: substantive (eval-tooling correctness, verdict reversal that reopens an operator-blocked decision) + structural (three meta-hygiene candidates, one elevated, two new) + framing-discipline (pushback caught and re-framed before commit).

---

## 2. WHAT WAS ACCOMPLISHED

### 2.1 Commits + tests + deploy state

| # | Commit | Type | Notes |
|---|--------|------|-------|
| 1 | `5ecda0f` | fix(s209) | Eval script Brier formula + BSS denominator + singleton-only verdict; §S209 Corrections Log entry with §S208 PARK supersession framing |
| 2 | `1d5996d` | docs(s209) | Three meta-hygiene candidates (operator-decision-queue batching; falsifiability-clause hardness; plan-deviation negative-case anchoring refinement) |

Tests: unchanged. The eval script is a utility CLI with no callers in production code; not in test sweep (per S208 framing for similar utility scripts). Syntax verified via `python -c "import ast; ast.parse(...)"`; import verified via `python -c "import scripts.esports_v2_shadow_eval"`.

Deploy state: master 20 ahead of VPS at post-S205 release `20260430_092402` (HEAD `7c60938`). All 20 commits docs-only or utility-CLI. No deploy needed; the gap continues to accumulate per the §S206 hygiene observation.

### 2.2 VPS state changes (NONE)

No VPS .env edits this session. No service restarts. No bot configuration changes. EB v2 remains in shadow-only mode (`ESPORTS_V2_DRY_RUN=true`, `BOT_ENABLED_ESPORTS_V2=true`) — unchanged from S208 close.

The corrected eval script was SCP'd to VPS as `/tmp/esports_v2_shadow_eval_S209.py` and run from `/opt/polymarket-ai-v2/scripts/esports_v2_shadow_eval_S209.py` with `polymarket` user. Both temp files were cleaned up at session end.

### 2.3 Eval script bug fix + verdict reversal (commit `5ecda0f`)

**The bug.** `scripts/esports_v2_shadow_eval.py` computed Brier as `(p_model - 1{predicted_winner == actual_winner})²`. Per [esports_v2/shadow/match_converter.py:121](esports_v2/shadow/match_converter.py:121), `p_model = P(team_a wins)`, not `P(predicted_class wins)`. When `p_model<0.5` the model picks team_b, so `1{predicted == actual} = 1{team_b won} = 1 - 1{team_a won}` — the outcome label is inverted relative to the event the forecast targets. Compounding bug: BSS climatology denominator used `accuracy*(1-accuracy)` instead of `P(team_a wins)*(1-P(team_a wins))`. Both bugs push aggregates toward FAIL/negative-BSS in concert.

**The fix.** Derive `y_a = 1{team_a won}` from the joint of `(predicted_winner == actual_winner, p_model > 0.5)`, then compute Brier as `(p_model - y_a)²`. BSS climatology now uses `mean(y_a) * (1 - mean(y_a))`. Singleton-only verdict added alongside the full-set output — only singleton predictions produce trades (non-singletons abstain via the conformal filter at [esports_v2/model/conformal.py](esports_v2/model/conformal.py)), so the gate decision should be evaluated against the trade-eligible cohort.

**Verdict reversal.** Corrected script run on VPS at 2026-05-02 23:27 UTC. Three captured outputs at repo root (untracked, per session-output convention):
- `S209_EB_V2_CORRECTED_VERDICT.txt` — all-time scope
- `S209_EB_V2_RECENCY_14D.txt` — `--days 14` rolling window
- `S209_EB_V2_RECENCY_7D.txt` — `--days 7` rolling window

Singleton overall passes the partial Gate 5v2-C verdict on the two measurable metrics across all three windows. Per-game asymmetry: LoL singletons pass cleanly with strong margins (BSS strongly positive across windows); CS2 singletons fail the Brier gate in all-time and 14d, marginal in 7d (BSS essentially zero or slightly negative). Recency direction confirmed under correction — 7d window shows uniformly improved metrics vs all-time.

**Implications for §S208 PARK rationale.** The original "structural failure / negative BSS" framing was a script artifact, not a model property. **However**, per-game CS2 weakness survives the correction — CS2 singletons fail the Brier gate post-fix even though the headline aggregate verdict reverses. Lead 4 is now an operator decision among four sub-options (see §0 above), not a binary "park vs flip" choice.

**§S209 Corrections Log entry.** Shipped in same commit as the script fix (S172_CONSOLIDATED_PLAN.md addition). Records the bug, the fix, the verdict reversal, and the explicit supersession of §S208 PARK rationale. Citation chain for any downstream metric: `scripts/esports_v2_shadow_eval.py` post-fix at commit `5ecda0f`, reading `esports_predictions WHERE mode='shadow' AND model_version='v2-trinity' AND actual_winner IS NOT NULL`.

### 2.4 Meta-hygiene candidates (commit `1d5996d`)

Three additions to §S172 plan §Protocol candidates section:

**(a) Operator-decision-queue batching** — §Operational Procedures candidate, NOT Protocol candidate. When session-close operator-blocking queue ≥5 items, surface as a top-of-handoff "Operator Decisions Pending" block with batched-decision invitation. 1-instance evidence base from S208 close handoff §5 (6 items at peak). Codify after one session of observed-effective batched-decision-pass. **The §0 block at the top of this handoff is the first applied instance** — operator's batched response in S210 will be the codification trigger.

**(b) Falsifiability-clause hardness** — Protocol candidate, 0-instance structural-shape only. Recent chain codifies tripwires on closures (Bug A H1-H4, 6Q re-eval triggers, D1 rollback) but the tripwires themselves carry no meta-tripwire. Discipline: each tripwire pattern carries either (a) a periodic-review cadence or (b) a tripwire-coverage-gap signal. Filed for tracking; promotion at 1st observed instance of a closure-with-tripwire that stays closed despite recurrence via uncaptured paths.

**(c) Plan-deviation negative-case anchoring** — refinement to existing Plan-deviation candidate. Beyond second-instance + soft-convention-break requirement, promotion also requires a documented negative case (something investigated as deviation and ruled out) to anchor specificity. The S207-audit ruled-out cases (S195 `b82ad68` and S201/S202 `b125a5e`) serve as de-facto negative cases — refinement makes that explicit so future agents understand the candidate's discriminator.

### 2.5 Framing-pushback resolution (no commit; in-session re-framing)

Initial close-summary of Lead 4 ("flip, validate, or hold pending infra") collapsed (a-restricted) and (a-unrestricted) into a single (a), biasing toward action by hiding the per-game decision granularity. Operator pushback identified three issues:
1. Framing biased — sub-options not surfaced.
2. Singleton per-game numbers under correction not surfaced (despite being in the captured output).
3. Recency improvement not re-verified post-fix.

Resolution: re-ran with `--days 7` and `--days 14`; captured `S209_EB_V2_RECENCY_7D.txt` and `S209_EB_V2_RECENCY_14D.txt`; re-framed Lead 4 with four sub-options (a-restricted / a-unrestricted / b / c) referencing the captured outputs; described directional findings without quoting specific values in chat (Protocol 11 discipline).

No commit shipped from this resolution — the captured outputs are untracked artifacts; the re-framed Lead 4 is documented in §0 above. The §S209 Corrections Log entry already records the supersession at the §S172 plan level.

### 2.6 Protocol 11 catch latency this session

One in-message Protocol 11 catch: the §S209 investigation report initially included specific Brier / accuracy / BSS values from direct SQL queries against `esports_predictions`. Operator stop-hook fired identifying the values as P&L-adjacent performance claims requiring `bot_pnl.py` source citation. Resolution: stripped figures from chat output; cited captured output files (canonical eval-script output) for any downstream reference; described directional findings qualitatively. The catch was post-send (the report had already been displayed) — same latency tier as S208's in-message catch.

The pattern of P11 violation in numerical-content drafting is NOT decreasing; only the catch latency is. Pre-send Protocol 11 self-check codification (Item 4 in §0 above) remains the structural prevention layer.

---

## 3. §S209 HYGIENE BACKLOG (post-close state)

| # | Item | Status |
|---|---|---|
| 1 | Eval script Brier formula + BSS denominator + singleton-only verdict | RESOLVED (`5ecda0f`) |
| 2 | Three meta-hygiene candidates filed | RESOLVED (`1d5996d`) |
| 3 | EB v2 Lead 4 sub-option granularity surfaced post-pushback | RESOLVED (in-session re-framing; §0 above) |
| 4 | Captured corrected verdict outputs (3 files) at repo root | RESOLVED (untracked, per session convention) |
| 5 | EB v2 verdict re-evaluation under corrected formula | OPERATOR-PENDING (§0 #1) |
| 6 | Pre-send Protocol 11 self-check codification (Session Template variant) | OPERATOR-PENDING (§0 #3, carried from S208) |
| 7 | Audit-corrections-bundling pattern codification | OPERATOR-PENDING (§0 #4, S209-elevated to two-instance precedent) |
| 8 | Bucket-concentration Protocol promotion | OPERATOR-PENDING (§0 #2, carried from S206-S208) |
| 9 | CLV measurement infrastructure (resolution-time market price capture) | DEFERRED — Issue 7 of §S209 investigation report; multi-step infrastructure work |
| 10 | Backtest result persistence (`run_backtest.py --save-results`) | DEFERRED — Issue 7 of §S209 investigation report; ~30-60 min CLI addition + workflow change |
| 11 | Plan-deviation positive sweep (S198-S203) | DEFERRED — carried from §S208 Hygiene #10; ~30 min next session |
| 12 | Protocol 7 row 2/8 substantive audit | DEFERRED — carried from §S208 Hygiene #9; ~30 min next session |

Items 9 and 10 are the two unmeasurable Gate 5v2-C metrics (CLV vs Polymarket, backtest-to-shadow drop). Both became visible during the §S209 investigation but neither was in scope for the eval-fix commit (per "one fix per commit" discipline).

---

## 4. Master vs VPS state

**Master HEAD:** `1d5996d`
**VPS release:** post-S205 `20260430_092402` (HEAD = `7c60938`)
**Difference:** 20 ahead

The 20 ahead-commits:
1. `00e903a` — S204→S205 plan updates (S206)
2. `31ee299` — §S205 hygiene closures (S206)
3. `d62cae3` — S206 review push-back + candidate filing (S206)
4. `1f066bb` — §S206 Hygiene #1 closure (Protocol 7 audit) (S206)
5. `ad38188` — Triple-blind verification + line-number-staleness rule (S206)
6. `40fae47` — §S207 Hygiene Backlog (S207)
7. `4ea441e` — Audit corrections — count 3→1 + Row 2/8 framing (S208)
8. `1f2c47b` — Add `scripts/esports_v2_shadow_eval.py` (S208)
9. `cb57a2f` — D1 staged trigger pre-commit (S208)
10. `8495671` — Add `scripts/config_drift_audit.py` (S208)
11. `a289855` — File §S208 Hygiene Backlog (S208)
12. `f296a2c` — Promote Protocol 13 (S208)
13. `f586a2c` — Training cluster re-enable (S208)
14. `680e6b2` — Close deferred-3 (S208)
15. `177ac75` — Close-review incorporation (S208)
16. `619a7ae` — Rebuttal-review meta + Review-state-discipline candidate (S208)
17. `16b80d1` — Close-question round resolutions (S208)
18. `a89e0bc` — Commit prior-session WIP — S177 SHARED MASTER (S208)
19. **`5ecda0f`** — Eval Brier formula + BSS denominator fix + singleton-only verdict (**S209**)
20. **`1d5996d`** — Three meta-hygiene candidates (**S209**)

**Deploy decision:** No deploy this session. All 20 commits docs-only or utility-CLI (no production-code callers). The accumulating-pattern observation continues from S206/S207/S208 — gap is now 20 (was 18 at S208 close).

**VPS state changes this session (NOT git-tracked):** None. EB v2 remains in shadow mode. No service restarts. No .env edits.

---

## 5. Operator actions required

See §0 above for the batched operator-decision queue. The four items there are the carries from previous sessions plus the new Lead 4 priority.

No new operator-blocking proposals from S209 itself — the session's substantive work was an in-session execution of operator-directed (b) "investigate" path from S209 open. The Lead 4 verdict response that follows from the corrected output is the next operator-decision step.

---

## 6. Plan to proceed — S210+ leads (ordered by readiness)

### Lead 1 — D1 Stage 1 trigger evaluation (sample-gated)

**Status:** unchanged from S208 close §6 Lead 1. Sample-gated; awaits ≥30 closed trades in the $5–$15 marginal cohort post-`2026-05-02 16:33 UTC` flip. WB pre-flip rate was extremely low (~5/week per S207 §2.3); post-flip rate may rise if D1 unlocks orderbook-constrained trades. If post-flip rate stays low, sample takes weeks.

When sample reaches 30 closed trades: evaluate per-bucket P(edge>0) per the §S208 D1 trigger spec at `S172_CONSOLIDATED_PLAN.md:1619+` (operator-confirmed 0.40 threshold; 6Q-D1 interaction bucket split; OOD-risk caveat; irreversibility-window note).

### Lead 2 — Lead 4 EB v2 verdict execution (post-operator-decision)

**Status:** READY when operator selects from §0 #1 sub-options. Each sub-option has a different next-step shape:

- **(a-restricted)** requires per-game env var or game-conditional sizing code change in [bots/esports_bot_v2.py](bots/esports_bot_v2.py). Multi-file edit; tests; deploy. Estimate: 1-2 sessions.
- **(a-unrestricted)** requires pre-flip rollback trigger pre-commit (parallel to D1's `cb57a2f`). Then `.env` flip on VPS + service restart. Estimate: 30-60 min once trigger is committed.
- **(b)** requires script extension to add per-game gate threshold + sustained-window evaluation. ~1 hour. Then evaluate weekly until thresholds met or revised.
- **(c)** requires Issues 9 + 10 from §3 above (CLV market price capture + backtest result persistence). Each is multi-step; combined likely 2-3 sessions.

### Lead 3 — MB Phase 7 verdict re-eval (calendar-gated ~2026-05-12)

**Status:** unchanged from S208 close §6 Lead 3. Calendar-gated; n=413 at S207 close per §S205 Hygiene #2 close-review-corrected projection; ~9 trades/day; gap to 500 = 87 trades; ~10 days from S207 close → ~2026-05-12.

### Lead 4 — Plan-deviation positive sweep (READY, ~30 min)

**Status:** unchanged from §S208 Hygiene #10. Positive-sweep across S198–S203 for shipped commits with the shape "code shipped before catalog row prerequisites met OR catalog row exists with explicit prerequisites violated." Symmetric counterpart to the over-counting correction in commit `4ea441e`. **S209-flagged as "most actionable next session"** in the S209 plan-review.

### Lead 5 — Protocol 7 row 2/8 substantive audit (READY, ~30 min)

**Status:** unchanged from §S208 Hygiene #9. Reads `AGENT_HANDOFF_S196_CLOSE.md` (Row 2 SIZE_INVARIANT FIX_AUDIT_CHECK→FIX_EMISSION rescoping) and `AGENT_HANDOFF_S202_CLOSE.md` (Row 8 plan-revision-approach inversion). For each row, verifies the inversion attribution against handoff content. If wrong, file as §Corrections Log row-correction. If both verify, mark §S207 Hygiene #5 fully RESOLVED.

### Lead 6 — Audit script extension to follow `getattr(settings, ...)` (§S208 Hygiene #7, READY)

**Status:** unchanged from S208 close. ~30-60 min. Reduces ENV-ORPHAN false-positive rate in `scripts/config_drift_audit.py` first-run output.

### Lead 7 — Operator-decision-queue batching codification (post-§0 batched-pass)

**Status:** depends on operator response to §0. If operator processes §0 in batched form effectively, the candidate is codifiable per its own threshold. Codification is ~5-10 min in the §Operational Procedures section of the plan.

---

## 7. Critical reminders

1. **§S208 PARK decision is superseded at the §S172 plan level** by the §S209 Corrections Log entry shipped in commit `5ecda0f`. Future sessions reading the plan will see both entries — the S208 PARK rationale documented as the original decision basis (now invalid), and the S209 supersession with the verdict-reversal mechanism + four sub-options. Do NOT default to the PARK framing without re-reading the §S209 entry.

2. **The eval script bug was production-tooling** that drove an operator decision on stale numbers. Same shape as S195 SQL `--` fix (`b82ad68`) — production tooling produced wrong output, fix is discovery-driven. Filed under existing Plan-deviation candidate's negative-case ledger (S195 + S201/S202 + S209 are all "wasn't a deviation despite tooling-correctness work").

3. **The §0 batched-decision block is the first applied instance of the operator-decision-queue batching candidate.** Operator response shape in S210 (batched-pass vs per-item) is the codification trigger. If batched-pass produces clean operator throughput, the candidate is codifiable as §Operational Procedures.

4. **Singleton-only is the trade-eligible cohort.** All four Lead 4 sub-options should reason about singleton metrics, not full prediction set metrics — non-singletons abstain via conformal filter at [esports_v2/model/conformal.py](esports_v2/model/conformal.py); they would never trade in Phase 5v2-D regardless of the gate decision.

5. **Per-game CS2 weakness survives the correction.** The headline verdict reversal applies to overall and LoL; CS2 singletons fail the Brier gate post-fix in all-time and 14d windows. Any "flip both games" decision (a-unrestricted) needs explicit acknowledgment of this — the corrected verdict doesn't make CS2 a clean pass.

6. **Recency direction holds under correction.** The 7d window shows uniformly improved metrics vs all-time across all cohorts; the original investigation's "model improving over recency" finding survives the bug fix. With training now actively learning (S208 §2.5 re-enable), continued improvement is plausible — but unevenly across games (LoL was already strong; CS2 has been improving but from a worse baseline).

7. **The Protocol 11 violation pattern is at 5 sessions now (S203/S204/S205/S208/S209).** Catch latency tier remains "in-message before send" (this session's report had figures stripped post-display). Pre-send self-check codification (§0 #3) is the structural prevention; until codified, expect continued in-message catches.

8. **Two Protocol candidates are now promotion-ready** — bucket-concentration (2 evidence points + small-sample sub-mandate, §0 #2) and audit-corrections-bundling (now two-session precedent, §0 #4). Both need operator approval to land as numbered protocols.

9. **One Protocol candidate is still 1-instance** — Review-state-discipline (S208 commit `619a7ae`). S209 did not produce a second instance.

10. **CLV and backtest-to-shadow drop infrastructure (Issues 9 + 10)** are the limiting factor for a complete Gate 5v2-C verdict. Without those, even a perfect singleton-only Brier + Accuracy pass leaves two of four conditions unevaluated. Operator's risk tolerance on partial-verdict-vs-complete-verdict is an implicit input to Lead 4's sub-option choice.

11. **Today's date locked at 2026-05-02.** Same calendar day as S207 + S208 closes; session boundaries are conversational, not calendar.

---

## End of S209 close handoff

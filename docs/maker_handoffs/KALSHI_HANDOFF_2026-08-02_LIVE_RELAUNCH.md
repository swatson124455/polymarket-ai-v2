# KALSHI MAKER — HANDOFF 2026-08-02. BOT LIVE (relaunched 03:06:42Z). READ §0 FIRST.

## 0. STEP ZERO — verify yourself, trust nothing here (all figures stale by definition)

- Branch `claude/maker-kalshi-live`, worktree from C:\lockes-picks\polymarket-ai-v2 (main
  checkout belongs to another lane — NEVER touch it or master). HEAD at handoff: the
  high-activity-gate commit; deployed quoter md5 **`9bfac08f6c9251b57749e1c80ddc356a`**
  == `git show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum` (LF; the worktree copy
  has CRLF contamination — ALWAYS deploy from `git show`, never the working file).
- Verify: md5 · live.env · STOP absent · service active · a plan row <5 min old.
  "active" is not "trading."
- **live.env deltas this session** (each has a .bak): MAX_MARKET 60→45, MAX_ACTIVATE 60→40,
  `KALSHI_NETEV_GATE=0` (operator: recalibrate from closed-market receipts, then re-review),
  `KALSHI_ALLOC_KEY=1` (D3 verified; CAPRANK_CALIB absent = 1.0 until receipts — operator
  accepted), `KALSHI_SELECT_BUDGET=1`, `KALSHI_SELECT_BUDGET_MARGIN=-0.3` (**negative is
  correct** — the est model under-reads; audit lens 2 #1 found +0.3 inverted),
  `KALSHI_MAX_VOL24H_CT=1000` (high-activity first gate), `KALSHI_PRESENCE_GATE=1`.
  `KALSHI_INCUMBENT_ONLY` ABSENT = gate built but DARK (enable = operator naming only).
- **First live row after relaunch** (2026-08-02T03:06:34Z, ESTABLISHED): equity $251.60,
  footprint 9, quoted 4, select_budget_used $151.65 / limit $176.12, drop_high_activity 21,
  close_unchecked_tail 1,080 (D1 cache warming, →0 over ~30-40 cycles), capped_markets 0,
  budget_backstop_fired absent, 0 journal errors.

## 1. OPERATOR IDEOLOGY (stated 2026-08-02 — the audit standard for ALL work)

"Maximize maker rewards on Kalshi in LOW-TO-MODERATE volume (moderate only if huge payout),
moderate-to-high reward markets; collect maximum rewards while NOT sticking our necks out;
we are not whales — we are scaling and need capital, not risk; minimum payout $1 per market."
First gate = avoid high site activity (>1,000 ct/24h, hot knob, coarse v1, REVIEW LATER).

## 2. WHAT SHIPPED THIS SESSION (all test+mutation+blind-review verified; ~20 commits)

Watcher 4b (watch-only anyloss shadow) · Sel-D10/D2-obs/D4/D8 (telemetry + score-cache
integrity) · Gov-D5/D10/D9-warn/RF3 · RF2 counter split (117 re-rest fails since 07-30,
cause was unmeasurable — now self-diagnosing) · **incumbency gate** (KALSHI_INCUMBENT_ONLY,
dark) · Sel-D9 full-sweep explore (least-recently-attempted; ats stamps) · **Option C**:
D1 full-scan pre-filter + select-to-budget walk (capital-fundable footprint, family budget
= _series_cap(), held exempt, explore at probe cost, cap_desired = alarmed backstop) ·
ban-set type coercion at all load boundaries · **$1 floor on the activate path**
(gate_activate_credit; measured 73/7,080 cycles 07-29..08-01 had activates) · alnum
categorical strikes silent-counted · walk-ref age bar · **high-activity gate** (threshold
basis: random n=160 of 5,448 programs — vol24h p50=0/p90~995/p99~27,568 ct).
Suite: **981 passed / 2 xfailed** (conftest pins cwd — runs from anywhere now).

## 3. WATCH LIST (first day live — judge tape against these)

- `budget_backstop_fired`: persistent nonzero → LOWER the margin further (never raise).
- `drop_high_activity` / `drop_budget_full` / `close_unchecked_tail` (→0) / `gate_*`
  reasons in quotes-*.jsonl / `ats` count rising ≤10/cycle in kalshi_market_scores.json.
- Score cache was ~fully stale at relaunch (score_age_p50 ~5.2d) → first hours lean
  pool-order (big pools) until measurements re-accrue. Expected, not a bug.
- dd/day meters reset 00:00Z; halts $40 dd / $60 day-down write STOP + passive flatten.

## 4. OPEN ITEMS (RULE NINE: nothing here may be dropped or reordered without asking)

- **⚠ 8-3 OPERATOR RE-REVIEW DUE 2026-08-03** (ladder $3/$5, STRIKES_OUT) + operator folded
  Gov-D6 (venue-forced takers pollute strike_hist) into it. Surface it.
- **FREE TEST Aug 3/4/5**: capture credit_history after closes of TRUMPTIME-H2 (Aug 2),
  TRUMPENDORSEMENTS-A5 (Aug 3), TOPMODEL-CLAU5 (Aug 4) — all banned before close. Three
  $0.00s confirms ban-before-close→$0; any payment kills it. Capture = record the web
  app's own authenticated response (direct fetch 401s); see memory
  project_kalshi_credit_history_api.
- Report-only, unnamed: presence-model calibration ($1 floor soft vs over-predicting
  model — needs reward receipts) · walk incumbent-blindness/boundary churn · D4-class
  "fold measures intended not rested" · high-activity gate REVIEW (threshold + TTL) ·
  D2 price band (tabled for gate_entry_band data) · D9 re-review after sweep covers
  universe · Gov-D7 sweep-veto · net-EV recalibration from closed markets · sizing knob
  ($45/mkt stays until receipts price the reward-share curve).
- Aug 2-9 payout calendar stands (close+1 lump; lifetime $167.35 = $152.35 + $15 referral).

## 5. RULES (all 13 hook-injected operator rules; THE NORM)

verify-first · ESTABLISHED/INFERRED/HYPOTHESIS on every number + denominator · tests +
copy-based mutation + blind review before ANY money-path deploy · byte-exact md5 vs git
blob (LF) · one fix per commit · findings→options→execute only what the operator names ·
never demote/remove without asking · own regressions same-session · holds are global ·
drips are fine (never bench presence to stop small losses) · never clear STOP / change a
cap / deploy without operator naming · relist ALL items on every update.
Panic stop: `sudo touch /opt/pa2-maker-kalshi-live/STOP`
VPS: ubuntu@18.201.216.0, key ~/.ssh/LightsailDefaultKey-eu-west-1.pem, dir
/opt/pa2-maker-kalshi-live (root-owned → `sudo -n bash -c`). Journal = UTC.

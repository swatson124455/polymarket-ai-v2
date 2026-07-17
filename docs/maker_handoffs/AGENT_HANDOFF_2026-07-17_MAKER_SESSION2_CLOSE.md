# MAKER LANE — SESSION-2 CLOSE HANDOFF (2026-07-17 ~20:30Z)

**Read order:** `docs/MAKER_MASTER_PLAN.md` on `claude/maker-bot` (**§0 NUMBERS RULE
FIRST**) → memory `project_mm_feasibility_study.md` (07-16/07-17 blocks) → this file.
This file = complete state snapshot + open threads. Nothing here overrides §0.

## 0. HARD RULES (unchanged, operator-set)
- "Maker" never "MB" (=MirrorBot) / "MM"; background processes = RECORDER ARMS, never "sim".
- **NUMBERS RULE (plan §0)**: NEVER quote a Maker number from memory/prior message. Run
  `scripts/maker_research/maker_canon.py` or cite an in-session measurement WITH method.
  MIN_BET = rewardsMinSize DOLLARS (split-pair cost). Snapshot share ≈5× time-avg.
  Contradictions get flagged as corrections, loudly. (Origin: min-bet misquoted 4 ways.)
- Income forecasts from OWN measured capture only; cohort receipts = pool-existence proof.
- Everything is PAPER; pilot = propose-only, operator decides. Trading leg budgeted
  zero-to-slightly-negative, never profit.
- Don't change the plan unilaterally (operator ruling after the farm-led flip got
  reverted): config choices are presented WITH data, decided by operator.
- Sept-1 "rewards expire" claim RETIRED — never resurrect.
- Cross-bot = propose-only handoffs; shared checkout may be held by another session
  (`git branch --show-current` before ANY repo write; linked worktrees).

## 1. RUNNING ON VPS (all read-only/paper, kill: `sudo touch <dir>/STOP` or disable unit)
| unit | what | era boundaries |
|---|---|---|
| polymarket-maker-sim.timer (v1) | naive control, 5-min | since 07-15 00:45Z; fills pre-07-15 20:20Z = lower bounds |
| …-v2.timer | gated poller + touch/wide A/B, 2-min | since 07-15 19:00Z |
| …-v3.service | gated + WS sub-second | **parser fix 07-16 01:22:32Z** — pre-fix = trade-driven-refresh era |
| …-v4.service | in-play lane + classic/split A/B + rebate meter (SIBLING session's arm) | 01:51Z→02:03Z→14:22Z→uncapped ~16:45Z 07-16; ~250 mkts; **memory 246M/384M watch** |
| …-v5.service | **GATE LAB** 6 paired policies (P0 base/P1 volfit/P2 ramp9h/P3 tapevel/P4 all/P5 ungated) | 01:11:43→02:14:5x→**02:36:41Z 07-17 = clean era** |
| polymarket-census.timer | hourly pool census | since 07-16 01:51Z (pre-Jul-19 baseline SECURED) |
| polymarket-maker-backup.timer | nightly 00:20Z data tarball (v5 INCLUDED since 07-17) | + local pull task 09:30 daily |
| /opt/pa2-maker-feeds/ | **WB shard drop LIVE** (wb_forecasts.jsonl + contract README) | WB is BUILDING their writer |

v5 hb quirk: `q=`/`tot=` counts include departed markets (frozen marks) — use `--report`
(splits them out), never hb totals. v3/v5 healthy signature: books=280, stale_books≈0,
nobook=0, http under budget.

## 2. SCHEDULED (operator machine)
- **maker-sim-readout — Fri 07-18 10:00** — THE readout + drafts pilot decision package.
  Its SKILL.md carries all era rules/doctrine flips/refit numbers (updates 17b-e). Present
  farm-led vs sports-led WITH shred data — do NOT pre-decide.
- maker-arms-midwindow-health — Thu 10:00 (covers v1-v5+census+gzip-rotation check).
- maker-data-backup-pull — daily 09:30.
- **Kalshi splinter session RUNNING** (task_6328fadb) — consume its report when done;
  if it died, its prompt is the runbook (Kalshi rewards rules + census, read-only).

## 3. QUOTABLE NUMBERS (current-method set ONLY; sources in memory blocks + `acd40ca`)
- Backtest floor +$1,657/30d gated+fast STANDS (feesEnabled 1,502/1,503 — cascade dissolved).
- Own capture: v2 $1,119/d, v3 $1,055/d (post-fix), v5-P0 $978/d ≈ **$1.0-1.1K/day**
  at 140-mkt min-size footprint (model-estimated; pilot receipts verify).
- Toxicity (side-inferred): 34.1%>1pt / 27.3%>2pt on 4,525 fills; final-16h zone 36-44%>2pt
  vs 10-12% beyond 48h. All-sector table (res-stripped): geo cleanest 10%>2pt, sports 11%,
  politics 14%, finance 23%, weather 35%, esports 42% — BUT freq≠magnitude (weather=
  frequent-small defendable, geo=rare-huge jumps, esports=both). Magnitude pass NOT run yet.
- Chain-fills v1: at-level volume 1.2-2.0× through → fill model ×1-3 (point ≈2). Man-in-
  our-spot receipts: 49% adverse (in-play esports window). v2 spec: level-depth + quiet hours.
- Income (receipts, reproduce EXACTLY): Apr $188.7K / May $181.3K / Jun $422.3K (cup);
  baseline $160-185K/mo PROVISIONAL — **cup-vs-meta TBD per operator until post-cup**.
- Min-bet tiers (canon sweep 07-17): $20×236 mkts ($11.8K/d pools,全coverage $4,720 capital);
  ~80% of 1,356 rewarded mkts ≤$50. Payout BIMODAL: contested ~0-2%/day; empty-band
  90-340%/day transient → **edge = finding/rotating empty bands, not sector choice**.
- Min-size makers VERIFIED PAID (11/12 sampled via chain fills → REWARD records, most paid
  last midnight). Per-market payment itemization = private per-account → pilot's own
  authenticated ledger is the verification instrument.
- v2/v3 rewards carry ≤4.9% gate-exit inflation (bounded). Vol-gate fit halved on clean era.
- In-play rewards = FLOORS (unmodeled in-game multiplier b); v4 rebate meter needs
  feesEnabled filter (flagged to sibling).

## 4. OPEN THREADS / BUILD QUEUE (operator-approved 07-17)
1. **WB forecast tilt**: WB building the shard writer → when lines flow, add TILTED paper
   policy to gate lab (paired vs untilted, ≥3-5d) → readout → pilot option.
2. **negRisk multi-outcome netted quoting — BUILD** (recorder-first; N pools ≈ 1 market's
   capital, self-hedging; split/merge canon in plan §7c).
3. **Sensor-grid feed — BUILD our half** (informed-flow tripwire: bite/stampede/run →
   /opt/pa2-maker-feeds/informed_flow.jsonl; VALIDATE hit-rate ≥1wk before proposing
   consumption to fleet; design in pointer + 07-17 chat).
4. Chain-fills study v2 (level depth → our pro-rata slice; quiet-hour windows).
5. Pool-anticipation calendar; new-listing latency (gamma createdAt join); 16h ramp variant
   for lab round 2; magnitude-of-adverse pass; PM liquidity-partner outreach (operator).
6. Post-cup sequence: census cliff-read across Jul 19 → mm_income_weekly re-run w/ post-
   promo weeks → early-Aug clean month → final baseline → operator go/no-go.

## 5. TRAPS (hard-won this session)
- gamma queries for resolved/closed markets NEED `closed=true` or return empty silently.
- publicnode RPC: shared with redemption-service IP — LIGHT calls only; getLogs via
  1rpc.io/matic at ≤45-block chunks, paced.
- The realized/unreal split is wrong on zero-crossing fills family-wide — use NET only.
- v5 accrual guard = mid-freshness (180s), NOT score-emptiness (that zeroed the legit
  sole-quoter farm case — masking-check catch).
- Blind-review register = plan §7b; fine-print audit = §7c (b multiplier, size-cutoff
  midpoint, feesEnabled, 10,080-sample epoch).
- Branch: ONE branch `claude/maker-bot` (worktree at b3b85ed5 scratchpad /obfix). Research
  scripts + outputs in scripts/maker_research/ (incl. maker_canon.py, all mm_*.py).

## 6. NEXT SESSION PICK-UP PROMPT — copy into chat verbatim
(see chat close 2026-07-17, or reconstruct: read plan §0 → memory blocks → this file →
check readout/health/splinter ran → consume → continue build queue in order)

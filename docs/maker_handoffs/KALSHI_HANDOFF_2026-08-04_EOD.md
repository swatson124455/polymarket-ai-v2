# KALSHI MAKER — HANDOFF 2026-08-04 EOD. READ THIS, THEN THE SCALE PLAN. BOT HALTED.

Supersedes `KALSHI_HANDOFF_2026-08-03_EOD.md` for current state (that doc remains the record of
the 08-03/08-04 fix program). **The plan of record is `KALSHI_SCALE_PLAN_2026-08-04.md` (CANON,
operator-ratified): Phase P prove at ~$350 → Phase B build → Phase S scale $350→$1k→$2.5k+.**
`KALSHI_MASTER_PLAN_2026-08-02.md` stays canon for defects/money history. All 13 rules bind.

## 0. STEP ZERO — verify, trust nothing here

- Worktree `…/5dfe0ebf…/scratchpad/kalshi-wt`, branch `claude/maker-kalshi-live`. Worktree clean.
  Main checkout is ANOTHER LANE.
- **HALTED** since 2026-08-02T10:26:37Z; STOP 230 B, mtime `2026-08-02 10:26:53.414658703 +0000`
  (re-verified 2026-08-04T19:21:18Z). Operator ruled "stay halted". Only an operator restart
  ruling lifts it — that is **Phase P0**, the plan's next physical step.
- **EVERYTHING IS DEPLOYED.** All 10 shipped files md5-match HEAD on the VPS (verified
  19:21:18Z): quoter `afb8cbea…`, scores `cee3bbd0…`, capital_rank `99d3b696…`, recorder
  `2ec0f5b4…`, fill_costs, client, calibrate, rebuild, table `c1b72b45…`. Service + all 3
  timers active. Gate **ARMED** (`NETEV_GATE=1`, in-memory apply proven 08-04T03:18:26Z, zero
  EMPTY-TABLE alarms). The running daemon still executes the pre-08-04 quoter FROM MEMORY —
  the restart loads everything.
- Tests at HEAD: **1132 passed / 2 xfailed, pytest exit 0** (capture the exit code, never grep).
- Live knobs (19:33:16Z): `MAX_TOTAL_CAPITAL=350`, `DAILY_LOSS_HALT_USD=30`,
  `MKT_DAY_LOSS_EXITONLY_USD=3`, `MAX_DAYS_TO_CLOSE=8`, `NETEV_GATE=1`, `SCORE_RANK=1`,
  `ALLOC_KEY=1`, `SWEEP_ENABLED=1`, `PRESENCE_GATE=1`, `MAX_VOL24H_CT=1000`, `CAPTURE_GATE=0`,
  `STANDDOWN=0`, `FUNDING_GATE=1`, `MAX_MARKET_CAPITAL=45`, `SERIES_MAX_USD=100`,
  `INV_SOFT_CT=15`, `INV_HARD_CT=50`, `JOIN_SIZE=0`.
- Capital: **$307.59 cash + $3.72 portfolio** (ledger 08-04). Measured reward baseline:
  **$14.21/day gross average** (credit_history, span 07-21→08-03, best day $42.06) — defect-era
  bot; the fixed bot's rate is UNMEASURED until P2.
- Panic stop: `sudo touch /opt/pa2-maker-kalshi-live/STOP`

## 1. WORK QUEUE (agent-named per operator process ruling 2026-08-04 §3; ORDER = execution
order; only true DECISIONS go to the operator)

| # | Item | Phase | State |
|---|---|---|---|
| W1 | First-restart checkpoint script/checklist (book-read→quote→telemetry live watch) | P1 | next up — buildable pre-restart |
| W2 | `PYTHONUNBUFFERED=1` in the unit file (staged, applies at restart) | B7 | next up |
| W3 | D2 follow-the-profit ranking (spec + proof criteria in scale plan B1) | B1 | not started |
| W4 | D3 size ramp + dollars-at-risk (spec in B2) | B2 | not started |
| W5 | Breadth-capacity study (read-only; bounds deployable capital) | B3 | not started |
| W6 | D1 clause 3 widen measurement path (sweeper vehicle) | B4 | not started |
| W7 | Unknown-market slow probe + 5-min checkpoint | B5 | not started |
| W8 | Recorder scalar fix `revenue/100` (full protocol; then deploy) | B6 | specified |
| W9 | Post-restart net-EV table rebuild on clean data | B8 | blocked on P2 data |
| W10 | **Zero-payer mechanism study** (operator-raised 08-04): why did 20 settled events with real presence pay $0.00 vs a $26.04 forecast (master plan §3)? Test, in order: the **$1 minimum-credit floor** (ESTABLISHED: min credit $1.01 of 54 — sub-$1 accruals may simply truncate to zero), program-window normalization (both-normalized model = 1.07×, n=16), share dilution, and the operator's movement hypothesis ("do books need X movement to pay?" — HYPOTHESIS, untested; program terms pay for RESTING liquidity, so movement should NOT be required, but it must be tested not assumed). Offline vs credit_history + caprank telemetry. | B | not started |
| W11 | **Offline test/backtest harness while halted** (operator-raised 08-04): STOP halts the live quoter ONLY — it does not block offline work. Raw material EXISTS on the VPS: `caprank-YYYYMMDD.jsonl` daily selection/book telemetry (37–104 MB/day, 07-29→08-02 observed) + `ws_daemon_log.jsonl` + the frozen receipt tapes. Build: replay selection+gates over recorded cycles; validate D2 candidates against days where payment is KNOWN from credit_history. HONEST LIMIT: fills/queue-position CANNOT be backtested faithfully (no historical depth-by-price time series), and payout is venue-side — so backtests validate SELECTION, never fill P&L. | B | not started |

## 1b. GATE-MARGIN REVIEW DATA (generated 2026-08-04 from the ARMED table; operator asked for
full data). ⚠ Trading columns are DEFECT-ERA measurements (window 07-24→08-03T17:06Z, fixes
existed but were undeployed) — per RULE SEVEN they are substantially agent-defect, NOT family
economics. Credits are venue receipts and solid.

| family | net% | credits | trading | notional | fills |
|---|---|---|---|---|---|
| KXTOPMODEL | −3.12 | 2.15 | −25.02 | 733.73 | 59 |
| gas | −4.68 | 20.80 | −98.50 | 1659.75 | 282 |
| KXTRUMPENDORSEMENTS | −5.00 | 9.68 | −29.97 | 405.62 | 55 |
| KXDXYDUD | −5.60 | 1.89 | −19.90 | 321.45 | 61 |
| KXTRUMPTIME | −5.89 | 7.90 | −36.26 | 481.29 | 57 |
| temp | −6.09 | 3.68 | −16.89 | 216.76 | 44 |

| margin | keeps | credits kept | defect-era trading kept | credits forgone |
|---|---|---|---|---|
| 0.0% (live) | 0/6 | $0.00 | $0.00 | **$46.10** (≈$4.31/day of the window) |
| −4% | 1/6 | $2.15 | −$25.02 | $43.95 |
| −5% | 2/6 | $22.95 | −$123.53 | $23.15 |
| −6% | 5/6 | $42.42 | −$209.65 | $3.68 |
| −7% | 6/6 | $46.10 | −$226.53 | $0.00 |

The real question the data poses: margin 0.0 protects against trading drag that was mostly OUR
defects, at the price of ~$4.31/day of verified credit income. The defect-free drag is unknown
until P2. Decision #2 below.

## 2. DECISIONS FOR THE OPERATOR (decisions only — everything else is W-queue)

1. **Restart (P0)** — the plan cannot start without it.
2. **`NETEV_MIN_MARGIN_PCT`** — at 0.0 the armed gate benches all 6 receipt families (defect-era
   table). Ladder measured: −4% keeps 1/6, −5% 2/6, −6% 5/6, −7% 6/6. Default if unstated at
   restart: stays 0.0 (current live value).
3. **Per-rung risk envelope dollars** at each Phase-S rung (plan §2 has the % proposals).
4. **`thin`-negative fail-open direction** — documented in the quoter; keep (default) or change.

## 3. THE QUALITY-MARKET FINDER — verified state (operator question 2026-08-04)

**YES for finding, NO for proof-they-pay.** All three axes exist, are deployed, and are
test/live-verified as of 08-04:
- **Lack of movement:** swing penalty in `kalshi_market_scores.score` (a swingy market scores
  0.0873× a calm one fresh — pinned), penalty decays with the measurement (D1 fix), plus the
  `MAX_VOL24H_CT=1000` activity drop in selection. ESTABLISHED.
- **Reward-pool size:** pool (`usd_day`) is the rank prior and multiplies capture
  (score = share×pool product, quoter §SCORE-RANK); pool is never dropped (pinned,
  `test_pool_is_not_discarded`). ESTABLISHED.
- **Risk tolerance:** capital-efficiency denominator (`ALLOC_KEY=1` live — score per $
  committed), per-market $45 / per-series $100 caps, inventory 15/50, loss governors, price
  band, 8-day horizon, and the armed net-EV gate benching receipt-negative families.
  ESTABLISHED, decision-path-verified on the deployed artifact 08-04.
- **The NO half:** the chain has never run live on the fixed code (P1 verifies), and nothing
  yet feeds WHO ACTUALLY PAID back into the rank — the 14 never-paid series (−$127.10) were
  findable-quality by these axes and still never paid. That is exactly W3 (D2). Until W3 + a
  clean P2 window, "finds quality" is ESTABLISHED and "finds quality that pays" is HYPOTHESIS.

## 4. PROCESS (operator-ruled 2026-08-04, permanent)

The agent NAMES work items itself and executes the W-queue without asking for naming.
Only genuine decisions (money thresholds, restart, capital, direction changes) go to the
operator, phrased as decisions. Holds/stops remain global per RULE ELEVEN/THIRTEEN.

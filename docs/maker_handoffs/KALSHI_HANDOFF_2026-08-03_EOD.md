# KALSHI MAKER — HANDOFF 2026-08-03 EOD. BOT STILL HALTED. READ §0 FIRST.

Supersedes `KALSHI_HANDOFF_2026-08-03.md` for current state. That document remains correct for
the 33-commit Phase A–C record; `KALSHI_MASTER_PLAN_2026-08-02.md` remains canon for the halt
post-mortem, the money history and the original 12-defect list.
Every figure here carries its source and denominator. All 13 hook-injected operator rules bind.

## 0. STEP ZERO — verify, trust nothing here

- Worktree `…/5dfe0ebf-2821-475d-946c-72012db34c3b/scratchpad/kalshi-wt`, branch
  `claude/maker-kalshi-live`, **20 commits** on `0f79f04..HEAD`. Worktree clean.
  The main checkout is ANOTHER LANE — never touch it or master.
- **HALTED since 2026-08-02T10:26:37Z; stays halted until the operator names a restart.**
  Verified 2026-08-03T21:26:20Z: STOP present (230 B, uid 0, mtime unchanged at
  `2026-08-02 10:26:53.414658703 +0000`), service `polymarket-maker-kalshi-ws.service` ACTIVE
  (idles under STOP and rests maker EXIT offsets — canon-confirmed, not a violation).
- ✅ **DEPLOYED AND ARMED 2026-08-04 — see §3.** All 7 shipped files md5-verify byte-identical
  to HEAD on the VPS (quoter `a88ddc3acb9b4e7ef8e440a7e2f8ef4e`, recorder
  `2ec0f5b4e33409d0e0d1941ef261b424`). The RUNNING PROCESS still executes the OLD quoter from
  memory — new code takes effect only at an operator-named restart.
- ✅ `kalshi_netev_table.json` IS NOW ON THE VPS (§3), and so is `kalshi_netev_calibrate.py`,
  which was ALSO missing and which the loader imports — without it the table loads as `{}`.
- Test baseline at HEAD: **1132 passed / 2 xfailed**, `python -m pytest kalshi_live/ -q`,
  **pytest exit 0** (capture the exit code, not a grep of the summary line — see §6).
- Live knobs (post-deploy, 2026-08-04T02:21:19Z): `MAX_TOTAL_CAPITAL=350`,
  **`DAILY_LOSS_HALT_USD=30`**, `DAILY_DOWN_HALT_USD` DELETED, **`NETEV_GATE=1` (ARMED)**,
  `MKT_DAY_LOSS_EXITONLY_USD=3`.
- Panic stop: `sudo touch /opt/pa2-maker-kalshi-live/STOP`

## 1. OPERATOR RULINGS THIS SESSION (binding, not re-litigable)

| # | Ruling | State |
|---|---|---|
| §4 net-EV | Option **(c) reconcile, then (a) accept whole-market scope and arm** | reconciliation DONE; **ARMED 2026-08-04T02:21:19Z** |
| Fix order | Fix A/B/C first, then arm on a named window | A/B/C done + 2 more defects found and fixed |
| Deploy | **"deploy and arm but no restart"** (2026-08-04) | DONE — §3. STOP untouched, no restart. |
| Residual | Deposit gap is the **deposit CHARGE**; both numbers correct | CLOSED — see §5 |
| Ladder / D6 | **Keep as is** | no change |
| 1 | Close the `thin` hole via **(i) harden the consumer** | DONE (`1799c2c`) |
| 2 | **Agent names the window** on functional grounds | DONE — `2026-07-24T00:00:00Z` → `2026-08-03T17:06:00Z` (`06ad273`) |
| 3 | `MIN_RECEIPT_FILLS` — agent's recommendation stands | **40** (canon-equivalent is ~24) |
| 4 | Fix credits/trading different clocks | DONE (`844ea16`) |

## 2. WHAT SHIPPED (`0f79f04..HEAD`)

Every money-path change: tests failing-before/passing-after, copy-based mutation in a scratch
copy, adversarial blind review. **Mutation sweep 14/14 caught** across both changed files.

- `a6e9098` **defect A** — `_iso` returned NAIVE datetimes for date-only bounds, so the CLI
  raised `TypeError` on its own documented `--since 2026-07-21` example, always. Every test
  passed a tz-aware tuple straight to `build_table`, so the CLI path had zero coverage.
- `f94fc68` **defect C** — `confidence = "receipt" if cred > 0` graded zero-trading families
  receipt-grade with `net_pct_notional=None`, which the gate cannot mark poor → they opened FULL
  TWO-SIDED while `unproven` families were skipped. No data outranked unknown data.
- `821b133` **defect B** — the caveat shipped inside every document still quoted the
  pre-`9de3d89` figure and told the reader not to arm.
- `e304e70` mutation-found hole in the C fix (`notional <= 0` guard untested).
- `33588af` **own the adversarial review** — see §6.
- `1799c2c` **gate hardened** (ruling 1-i) — only `confidence == "receipt"` with a non-`None`
  `net_pct_notional` reaches the verdict branch; everything else (incl. `thin`, unknown grades)
  → model fallback. **Fails closed.** Exits pinned unblocked on every rejected path.
- `844ea16` **one clock** (ruling 4) — credits scoped by MARKET like trading, not by
  `created_at`.
- `06ad273` the table, on the named window.
- `814256a` three gate tests were secretly reading the committed table file.
- `b4e60a7` frozen tape + reconciliation study preserved.
- `a996a1d` **D1** — staleness sets the LABEL, not the VALUE (score-coverage fix).
- `71c1d36` **own the second adversarial review** — a real D1 value bug (the swing penalty was
  multiplying the pool-prior component FOREVER: capture 0 / ref_move 0.12 / pool 100 scored
  7.6923 at every age 0.5d–6.99d instead of converging to 100), D1's false "exploration is
  byte-unchanged" claim (the stale queue's missing ticker tie-break let a stable sort fall back
  to VALUE order), defect B reintroduced by my own `844ea16` and SHIPPED (the caveat still
  quoted −5.78% / "$2.15 / $23.06 identical" after credits moved onto the trading clock), and
  two D1 pins that were respectively self-fulfilling and vacuous. Plus `credits_out_of_scope`,
  which makes the document reconcile EXACTLY: $109.17 + $15.00 + $74.78 = $198.95.
- `f5cde90` **the last two open bugs** — `shadow_rank` replaced a measured stale row with the
  sweep model (664.5973 → 12.0) and escaped `unknown_haircut` by rewriting `kind`; and
  `1799c2c`'s "FAIL-CLOSED, NOT FAIL-OPEN" was wrong in one direction, now corrected in source.

### Measurements now canon (ESTABLISHED; frozen tape read 2026-08-03T17:06:00.554496Z — 1234 fills / 143 settlements / 58 credits, committed as `kalshi_live/netev_tape_2026-08-03T170600Z.json`, md5 `766985871cf48273cd5a4c12ef4cc022`)

- **THE CANON DISAGREEMENT IS RECONCILED, EXACTLY.** ⚠ Read the clock: the canon window is
  INCLUSIVE **ET** DATES (`kalshi_netev_calibrate._date` slices a `-04:00` `close_timestamp`),
  so like-for-like is `2026-07-21T04:00Z..07-23T03:59:59Z` → gas **−5.78%**, temp **−4.30%**.
  The −2.74% / −7.58% pair in the previous handoff read the same nominal dates on a UTC clock.
- The mechanism is **EXPORT-TIME COMPLETENESS**, not a cash-vs-realized dispute: a CSV `trade`
  row books `close_timestamp` = SETTLEMENT time, so canon is blind to any market unsettled at
  the export instant. **temp: 0 of 25** in-window markets unsettled → both engines agree on
  trading P&L **to the cent, −$36.1178**, leaving the whole temp gap in the notional denominator
  ($142.6720 vs $303.6178). **gas: 9 of 10** unsettled → canon's +$0.2528 is a fragment of a
  complete −$40.2060.
- Credits under the OLD date rule reproduced §M8 screenshots **exactly** ($2.15 gas / $23.06
  temp) — that validated per-event parsing and still stands for that rule. `844ea16` then moved
  credits onto the trading clock, absorbing lag: gas → $10.09, temp → $38.55, and **temp's canon
  window verdict flips −4.30% → +0.80%**.
- §M13 taker exclusion removes **exactly $0.00** on the canon window (0 rows).
- **A CSV `trade` row is a LOT MATCH, not a round trip**: 244 rows vs 290 API fills over the
  export span = **1.1885 fills/row** (gas 1.1692, temp 1.2105). Canon's 20-trade bar ≈ **24
  fills**, so `MIN_RECEIPT_FILLS=40` is ~1.7× stricter — a choice, not an equivalence.
- Launch-defect era, canon window: **four** gas markets carried ≥20 contracts into a **$0.00**
  settlement for combined cash **−$41.4113**, MORE than the entire −$40.2060 gas trading total
  — everything else there was net positive. Raw observation on that window only; **not** a
  re-decomposition of the −$122.57 basis, no percentage claimed.

## 3. DEPLOY — **DONE 2026-08-04, operator-named "deploy and arm but no restart"**

**Shipped 02:20:11–02:20:24Z**, all 7 md5-verified byte-identical to HEAD, backups at
`*.bak-NETEV-20260804_021957`:
`maker_kalshi_quoter.py` · `kalshi_cash_recorder.py` · `kalshi_fill_costs.py` ·
`maker_kalshi_client.py` · **`kalshi_netev_calibrate.py`** · `kalshi_netev_rebuild.py` ·
`kalshi_netev_table.json`.

⚠ **`kalshi_netev_calibrate.py` WAS MISSING FROM THE VPS.** `_load_netev_table` imports it
INSIDE its fail-open `try`, so an armed gate would have loaded `{}` — every family unproven,
markets still skipped on the model, table never consulted. That is the defect-7 trap verbatim,
and it would have made arming a silent no-op. Verified after deploy by loading the table through
the deployed loader on the VPS: **33 families, 6 receipt-grade, 0 unknown grades, 0 receipt rows
with `net_pct_notional=None`** — safe for both old and new gate logic.

**live.env 02:21:19Z:** `KALSHI_NETEV_GATE=0 → 1` (**ARMED**); `KALSHI_DAILY_LOSS_HALT_USD 40 →
30`; `KALSHI_DAILY_DOWN_HALT_USD` line deleted. Mode 600 root:root preserved, 58 lines.

**NO RESTART, and STOP UNTOUCHED** (230 B, uid 0, mtime `2026-08-02 10:26:53.414658703 +0000`).
The daemon still runs the OLD quoter in memory; the new code takes effect only at an
operator-named restart. `KALSHI_NETEV_GATE` IS hot-reloaded (watch list), so it applies to the
running process — but that is **operationally moot while halted**: under STOP the cycle returns
at the sentinel branch before any quoting, so the gate cannot change behaviour until restart.
✅ **IN-MEMORY APPLY CONFIRMED 2026-08-04T14:39Z**, closing the gap first logged as unverified.
The unit sets no `PYTHONUNBUFFERED`, so stdout is block-buffered and the line took ~57 min to
reach the journal — the buffering diagnosis was right, my 14-minute check was simply too early:

    2026-08-04T03:18:26Z  SAFETY KNOB LIVE-APPLIED: DAILY_LOSS_HALT_USD 40.0 -> 30.0
    2026-08-04T03:18:26Z  SAFETY KNOB LIVE-APPLIED: NETEV_GATE 0 -> 1

And **no `EMPTY TABLE` alarm** in the 979 journal lines since the deploy — the armed gate has a
live, non-empty table in the running process, which is the positive confirmation that shipping
`kalshi_netev_calibrate.py` fixed the defect-7 trap rather than merely appearing to.

**THE CONVENTION CHANGE FIRED at 02:22:14Z** on the cash recorder's next timer run (it is
timer-invoked, so it took the new code without a restart):

| field | 02:17:14Z (old) | 02:22:14Z (new) |
|---|---|---|
| `cum_fills_cash` | −232.3697 | −594.9697 |
| `cum_settle_payout` | 26.9012 | 74.4130 |
| `unexplained_todate_cash` | 513.0618 | 828.15 |
| basis markers | absent | `position_aware` / `gross` |

Step **+$315.0882** — **BOOKED AS A CONVENTION CHANGE, NOT INCOME.** The documented tell holds
exactly: at the boundary `cash` 307.5933→307.5933, `n_fills_todate` 1235→1235,
`n_settlements_todate` 147→147 — all three zero. (§3's forecast of +$330.0381 was computed from
the 08-03 snapshot at 1234 fills / 143 settlements; the realized step differs because the tape
moved, not because the mechanism differed.)
⚠ Minor open: the recorder's `cum_settle_payout` 74.4130 vs my independent settlement sum
74.4100 — a $0.0030 difference, unexplained, too small to have blocked the deploy but worth a
look.

### Original checklist, for the record

1. `deploy.sh` ships quoter + recorder + the new modules — **and `kalshi_netev_table.json`**,
   which does not exist on the VPS (§0).
2. live.env: **delete** `KALSHI_DAILY_DOWN_HALT_USD` (knob no longer exists; a leftover line is
   inert), set `KALSHI_DAILY_LOSS_HALT_USD=30` (**hot-reloaded**, no restart).
3. ⚠ **THE LEDGER STEPS ONCE: `unexplained_todate_*` jumps +$330.0381** in one 5-minute
   interval (cum_fills −$365.6000, cum_settle +$35.5619). Book it as a **CONVENTION CHANGE, not
   income**. Tell: at the boundary d(cash), d(n_fills_todate), d(n_settlements_todate) are all 0
   while halted. Row markers `fills_cash_basis` / `settle_payout_basis` appear for the first time.
4. First post-restart governor cycle pulls the whole settlement history once (`min_ts=None`);
   safe only because `mkt_exposure` is empty that cycle. **Do not hand-populate it.**
5. **`KALSHI_NETEV_GATE=0 → 1` is the arming step.** DONE 2026-08-04T02:21:19Z.

**What arming does** — verified end-to-end, real table through the real loader and real gate:
gas −4.68%, temp −6.09%, KXTOPMODEL −3.12%, KXDXYDUD −5.60%, KXTRUMPTIME −5.89%,
KXTRUMPENDORSEMENTS −5.00% → **all six FLAT-SKIP**; 27 further families → model fallback; every
family HOLDING inv=40 → 1×40 reducing quote, **de-risk never blocked**.

⚠ **HEADLINE THE GAUGE'S BLINDNESS, NOT THE NUMBERS.** Every available window is data from a bot
carrying defects 1–14, none deployed. This table measures a DEFECTIVE bot; it cannot separate
agent defects from family economics, and these negatives are **not** a verdict on the strategy.

## 4. OPEN DECISIONS

1. **Restart (E1).** Operator said "no restart" at the 2026-08-04 deploy and has not named one
   since. Bot stays halted until they do.
2. **Deploy `kalshi_market_scores.py` + `kalshi_capital_rank.py`** (all D1 work). Deliberately
   NOT shipped. ⚠ `KALSHI_SCORE_RANK=1`, `KALSHI_ALLOC_KEY=1`, `KALSHI_SWEEP_ENABLED=1` and
   `KALSHI_CAPRANK_TELEMETRY=1` are ALL set in live.env (read 2026-08-04), so shipping these
   activates a live ranking path — that is why every D1 defect below had to be fixed first.
3. **D1 follow-up — haircut only the PRIOR component.** `shadow_rank` still applies
   `unknown_haircut` to `stale` rows, which since D1 are partly measured. The principled fix
   haircuts only the prior half of the blend and needs `score()` to expose the split. Left
   conservative (over-discounting is the safe direction).
4. **The net-EV gate's fail-open direction.** A `thin` family scored net-negative now takes the
   model path and OPENS two-sided at `usd_day` 200 / 5000, where pre-fix it skipped. Measured on
   the T-HARDEN fixture book. Behaviour KEPT (the alternative is trusting a grade no producer
   defines) and now documented in the quoter; operator may want it changed.

**DECIDED — recorded so none of it is re-asked.** All operator rulings, 2026-08-03/04:

| Item | Ruling |
|---|---|
| Deploy + arm | **DONE** 2026-08-04 — "deploy and arm but no restart" |
| Cash-identity residual −$10.7970 | **CLOSED — it is the DEPOSIT CHARGE.** Both figures correct; $640.00 gross, $629.2030 net of charge. Not a leak, not a code question. |
| Ladder rungs $3/$5 | **KEEP AS IS for now** |
| Gov-D6 / `STRIKES_OUT` | **KEEP AS IS** (stays 0) |
| `MIN_RECEIPT_FILLS` 40 vs 24 | **40** ("not a question, go with your rec") |
| rebuild vs calibrate evidence bars | **NOT A QUESTION** — the two engines measure different units; closed |
| `$0.1093` finalized-NO dust | **NOT A QUESTION** — closed. (I never sourced that figure's derivation; noted so it is not silently dropped.) |
| `KALSHI_MAX_TOTAL_CAPITAL` | **Portfolio capital amount, and it changes** — not a fixed knob to revisit |
| NO-family credit gap | Resolved by construction; credits reconcile EXACTLY to $198.95 |

For the record, families that would flip at `MIN_RECEIPT_FILLS=24`: full history —
`KXGENERICBALLOTVOTEHUB` (29 fills), `KXMLABELSHARE` (30); 07-29→now — `KXMLABELSHARE` (30),
`KXTRUMPENDORSEMENTS` (38); 08-01→now — `KXTRUMPENDORSEMENTS` (28), `KXTRUMPTIME` (25, **+0.85%**,
the only allow in the set). Zero flips on the canon window.

## 5. OPEN ITEMS — nothing here may be dropped (RULE NINE)

- **Phase D — D1 two-of-three clauses done; D2 and D3 NOT STARTED** (operator named "do d1-3").
  - **D1 DONE** (`a996a1d`, corrected by `71c1d36` and `f5cde90`): never-measured vs stale
    split, and the swing penalty survives going stale.
  - ⚠ **D1 clause 3, "widen measurement path" — NOT BUILT.** Not guessed at. Scoring today only
    prices books the cycle already read (`maker_kalshi_quoter.py:380-384`), so "widen" means
    measuring markets we do not otherwise read; the sweeper is the obvious vehicle.
  - **D2 — NOT STARTED.** Reward feedback + fill cost + hours-to-close into the rank key; lag
    exclusion keyed on PROGRAM `end_date`, NOT close+1. Evidence base identified: close+1 held
    for only **24 of 33** credited events, **9 of 33** paid BEFORE market close by 30.7–727.0 h
    (master plan §3). Proof criteria: the **14 defensibly-never-paid series** (−$127.10, of 20
    that never earned a cent totalling −$156.12) rank below comparable payers, and the **5
    zero-fill earners** (+$7.51) are NOT deranked.
  - **D3 — NOT STARTED.** Size ramp 5→10→25→50 ct at ≥10 min per rung plus a dollars-at-risk
    term. Proof: a KXTEMPAUSH replay walks 5→50 across cycles; dollar caps bind on some of the
    2,176 50-ct side quotes.
- **Unknown-market slow probe** + 5-min data checkpoint — ruled BUILD 08-02, still NOT BUILT.
- **Restart (E1)** — see §4.
- **8-3 re-review** — ladder rungs and Gov-D6 both RULED "keep as is" (§4). Live strike state
  2026-08-03T12:48:49Z: 24 tickers, 21 at one strike, 3 at two; 9 permanent bans,
  `mkt_out_backup.json` agrees exactly. Retained here because the underlying re-review material
  was delivered and the knobs may still be retuned later.
- **The cash-identity residual — CLOSED as the deposit charge (§4), and the mechanism is now
  understood rather than merely unexplained.** Measured on a single-instant snapshot
  (`kalshi_live/cash_identity_snapshot_2026-08-03T233338Z.json`, re-runnable via
  `kalshi_live/cash_identity_check.py`):

      cash == deposits + credits + settlement_revenue + fill_cashflow
      640.0000 + 198.9500 + 74.4100 − 594.9697 = 318.3903 predicted
      307.5933 actual  →  RESIDUAL −$10.7970   (read 2026-08-03T23:33:38Z)

  **IT DOES NOT DRIFT** — measured at two instants 6h27m apart across 1 new fill and 4 new
  settlements, the model predicted every cash movement **to the cent** (+$0.6700 vs +$0.6700)
  and the residual was identical at both. That REFUTED the prior "drift points at the settlement
  leg" lead, which had been measured with the defect-13 position-blind recorder. A fixed offset
  with perfect dynamics pointed at the initial condition — and the operator confirmed it is the
  deposit charge.
- **$0.0030 — ROOT-CAUSED 2026-08-04, fix NOT applied.** `kalshi_cash_recorder.settlement_payout`
  reconstructs the payout as `net(yes_count_fp − no_count_fp) × value` instead of reading the
  venue's `revenue`. Over the complete history (n=147) that is
  `sum(revenue/100)=74.410000` vs `sum(settlement_payout)=74.413000`, and **exactly ONE row of
  147 differs**: `KXCLUBFBTTS-26JUL26ERKHIL-BTTS`, `market_result="scalar"`, yes 19.00 / no
  18.86 / value 45 → model 0.0630 vs venue-paid 0.0600. A binary net×value reconstruction does
  not describe a SCALAR settlement, and it leans on counts that
  `kalshi_attribution_ledger.settlement_revenue` documents as GROSS TRADED COUNTS — the same
  docstring that records `revenue` as validated to the cent on 51/51 and says "Do NOT substitute
  winning-side-count × $1 here". `kalshi_netev_rebuild` and the ledger BOTH use `revenue`; only
  the recorder does not. **Proposed one-line fix: `return _f(s.get("revenue")) / 100.0`.** NOT
  applied — it is a money-path change to a DEPLOYED, timer-invoked recorder, shifts
  `unexplained_todate_*` by $0.0030, and needs the full protocol plus an operator naming.
- **`KALSHI_MAX_TOTAL_CAPITAL=350`** — ruled a changing portfolio-capital figure (§4), not a
  knob to revisit. Value verified still 350 on 2026-08-04.
- **$0.1093 of finalized-NO dust** — ruled not a question (§4). Retained per RULE NINE.

## 6. HOW THIS SESSION WORKED — and what it got wrong

Process caught, in this session alone: a wrong clock in my own shipped caveat, a measurably
false justification for a constant, an untested guard, an overclaimed pin, and three gate tests
silently coupled to a committed data file.

**The adversarial review was worth more than the tests.** It found the ET-clock error that
changed the headline answer. It also produced one figure that did NOT reproduce (gas −10.95% on
the ET window; I measure −5.7821%, and its temp figure matches mine exactly) — consistent with
it computing from the repo's `AUD_fills.json`, which ends 2026-07-25 and truncates the 26JUL27
gas settlements. **Verify a reviewer's numbers before accepting them; accept the findings that
survive and reject the ones that don't.**

**Two process failures, recorded so they are not repeated:**
1. `06ad273` was committed with a RED SUITE. pytest was piped through `grep`, so the shell
   reported grep's exit status. **Capture pytest's own exit code on every final check.**
2. A mutation sweep reported "8/8 caught" on a set narrow enough to miss two live mutations the
   reviewer then found. A clean mutation score is only as good as the mutation list.

**When a fix breaks a test, check whether the test was asserting the DEFECT before changing
either.** In this session it was, three times (`confidence == "receipt"` on one fill; two
fixtures encoding the old credit scope). Once the test was right and my mutation list was wrong.

**Commit BEFORE mutating**, and never mutate the worktree — scratch copies only.

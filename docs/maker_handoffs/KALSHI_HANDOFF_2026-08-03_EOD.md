# KALSHI MAKER — HANDOFF 2026-08-03 EOD. BOT STILL HALTED. READ §0 FIRST.

Supersedes `KALSHI_HANDOFF_2026-08-03.md` for current state. That document remains correct for
the 33-commit Phase A–C record; `KALSHI_MASTER_PLAN_2026-08-02.md` remains canon for the halt
post-mortem, the money history and the original 12-defect list.
Every figure here carries its source and denominator. All 13 hook-injected operator rules bind.

## 0. STEP ZERO — verify, trust nothing here

- Worktree `…/5dfe0ebf-2821-475d-946c-72012db34c3b/scratchpad/kalshi-wt`, branch
  `claude/maker-kalshi-live`, **14 commits** on `0f79f04..HEAD`. Worktree clean.
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
- Test baseline at HEAD: **1128 passed / 2 xfailed**, `python -m pytest kalshi_live/ -q`,
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
⚠ I could NOT confirm the in-memory apply from the journal: the unit sets no `PYTHONUNBUFFERED`,
so stdout is block-buffered and the newest journal line lagged ~14 min. The
`SAFETY KNOB LIVE-APPLIED:` line should appear on flush. **UNVERIFIED, not failed.**

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

- **Deploy + arm** (§3) — operator-gated.
- **VERIFY THE DEPOSIT TOTAL.** The cash identity closes to $0.0000 at deposits of
  **$629.2030**; the operator-stated figure is **$640.00** ($565.00 venue-verified + $75 added
  2026-08-03). The −$10.7970 residual is a fixed offset, not a leak (§5), so this single number
  is the whole remaining gap. Needs a bank/venue deposit-record check — no code change can
  settle it.
- **The two engines now carry different evidence bars** — rebuild `MIN_RECEIPT_FILLS=40`,
  calibrate `MIN_RECEIPT_TRADES=20`. Not a defect; an inconsistency nobody has ruled on.

**DECIDED, recorded so it is not re-asked:** `MIN_RECEIPT_FILLS` stays **40** (operator, "not a
question, go with your rec"). For the record, the families that would flip at 24: full history —
`KXGENERICBALLOTVOTEHUB` (29 fills), `KXMLABELSHARE` (30); 07-29→now — `KXMLABELSHARE` (30),
`KXTRUMPENDORSEMENTS` (38); 08-01→now — `KXTRUMPENDORSEMENTS` (28), `KXTRUMPTIME` (25, **+0.85%**,
the only allow in the set). Zero flips on the canon window.

## 5. OPEN ITEMS — nothing here may be dropped (RULE NINE)

- **Phase D, unstarted:** D1 score-coverage + sweeper pts into ranking; D2 reward feedback +
  fill cost + hours-to-close into the rank key; D3 size ramp (5→10→25→50 @10 min) + a
  dollars-at-risk sizing term. Specced in master plan §6. **Never named by the operator.**
- **Unknown-market slow probe** + 5-min data checkpoint — ruled BUILD 08-02, still NOT BUILT.
- **Restart (E1)** — operator-named, gated on all defects fixed.
- **8-3 re-review** — ladder rungs ($3/$5, "keep, retune later" was the agent's reading, never
  corrected) and Gov-D6 / `STRIKES_OUT` (ruled LEAVE at 0, revisit only if armed) remain the
  operator's to rule. Live strike state 2026-08-03T12:48:49Z: 24 tickers, 21 at one strike, 3 at
  two; 9 permanent bans, `mkt_out_backup.json` agrees exactly.
- **The cash-identity residual — MEASURED 2026-08-03, and the standing lead is REFUTED.**
  Against a single-instant snapshot (balance + fills + settlements + credits read together;
  `kalshi_live/cash_identity_snapshot_2026-08-03T233338Z.json`, re-runnable via
  `kalshi_live/cash_identity_check.py`):

      cash == deposits + credits + settlement_revenue + fill_cashflow
      640.0000 + 198.9500 + 74.4100 − 594.9697 = 318.3903 predicted
      307.5933 actual  →  RESIDUAL −$10.7970   (read 2026-08-03T23:33:38Z)

  **IT DOES NOT DRIFT.** Measured at two instants 6h27m apart across 1 new fill and 4 new
  settlements, the model predicted every cash movement **to the cent** (+$0.6700 predicted vs
  +$0.6700 actual) and the residual was **identical at both: −$10.7970**. So the prior lead
  ("the $1.50 drift points at the settlement leg") is **refuted** — that drift was measured with
  the DEPLOYED recorder, i.e. the defect-13 position-blind fill model, the very instrument now
  known to be wrong.

  A constant offset with perfect dynamics points at the **initial condition, not a flow**.
  Deposits implied by a zero residual: **$629.2030** vs operator-stated **$640.00** — a
  difference of exactly the residual. Credits are clean (58/58 `status=applied`, 0 clawbacks, no
  single credit or settlement row near $10.7970). **Remaining candidates: the deposit total
  itself, or a one-time account adjustment no API feed exposes. This is an operator
  record-check, not a code question** — see §4.
- **`KALSHI_MAX_TOTAL_CAPITAL=350`** predates the deposit — verified still 350.
- **$0.1093 of finalized-NO dust.**
- `KXRAIN-26AUG03-PHIL` self-closed 08-02T18:57:33Z (−$0.62 ticker realized) — **closed**.
- **NO-family credit gap — RESOLVED by construction.** The rebuild engine creates a family per
  series root, so in the named window $84.69 of credits across 12 families outside gas/temp are
  attributed (vs $24.48 gas+temp; $15.00 unattributed = the referral credit, which names no
  event and keeps the date rule). ⚠ Most of those earners sit BELOW the 40-fill bar, so the
  table now SEES that income but the gate does not act on receipts for it.

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

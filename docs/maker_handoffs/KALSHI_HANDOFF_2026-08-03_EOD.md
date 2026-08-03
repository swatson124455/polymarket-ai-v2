# KALSHI MAKER — HANDOFF 2026-08-03 EOD. BOT STILL HALTED. READ §0 FIRST.

Supersedes `KALSHI_HANDOFF_2026-08-03.md` for current state. That document remains correct for
the 33-commit Phase A–C record; `KALSHI_MASTER_PLAN_2026-08-02.md` remains canon for the halt
post-mortem, the money history and the original 12-defect list.
Every figure here carries its source and denominator. All 13 hook-injected operator rules bind.

## 0. STEP ZERO — verify, trust nothing here

- Worktree `…/5dfe0ebf-2821-475d-946c-72012db34c3b/scratchpad/kalshi-wt`, branch
  `claude/maker-kalshi-live`, HEAD **`b4e60a7`**, 10 commits on `0f79f04..HEAD`. Worktree clean.
  The main checkout is ANOTHER LANE — never touch it or master.
- **HALTED since 2026-08-02T10:26:37Z; stays halted until the operator names a restart.**
  Verified 2026-08-03T21:26:20Z: STOP present (230 B, uid 0, mtime unchanged at
  `2026-08-02 10:26:53.414658703 +0000`), service `polymarket-maker-kalshi-ws.service` ACTIVE
  (idles under STOP and rests maker EXIT offsets — canon-confirmed, not a violation).
- **NOTHING FROM THIS SESSION OR THE PREVIOUS ONE IS DEPLOYED.** Deployed quoter
  `9bfac08f6c9251b57749e1c80ddc356a` vs HEAD blob `a88ddc3acb9b4e7ef8e440a7e2f8ef4e`; deployed
  recorder `9d842c41c12afc8de804cab4013bd2c2` vs HEAD `2ec0f5b4e33409d0e0d1941ef261b424`.
  `kalshi_netev_rebuild.py` HEAD blob `f158eec51045bfdd34fab7d2feb029e8` — not on the VPS at all.
- ⚠ **THERE IS NO `kalshi_netev_table.json` ON THE VPS.** An armed gate today would load `{}`,
  fire the empty-table alarm, and put every family on the model fallback. The table must ship
  WITH the code.
- Test baseline at HEAD: **1128 passed / 2 xfailed**, `python -m pytest kalshi_live/ -q`,
  **pytest exit 0** (capture the exit code, not a grep of the summary line — see §6).
- Live knobs (read 2026-08-03T21:26:20Z): `MAX_TOTAL_CAPITAL=350`, `DAILY_LOSS_HALT_USD=40`,
  `DAILY_DOWN_HALT_USD=60`, **`NETEV_GATE=0`**, `MKT_DAY_LOSS_EXITONLY_USD=3`.
- Panic stop: `sudo touch /opt/pa2-maker-kalshi-live/STOP`

## 1. OPERATOR RULINGS THIS SESSION (binding, not re-litigable)

| # | Ruling | State |
|---|---|---|
| §4 net-EV | Option **(c) reconcile, then (a) accept whole-market scope and arm** | reconciliation DONE; arming NOT done |
| Fix order | Fix A/B/C first, then arm on a named window | A/B/C done + 2 more defects found and fixed |
| 1 | Close the `thin` hole via **(i) harden the consumer** | DONE (`1799c2c`) |
| 2 | **Agent names the window** on functional grounds | DONE — `2026-07-24T00:00:00Z` → `2026-08-03T17:06:00Z` (`06ad273`) |
| 3 | `MIN_RECEIPT_FILLS` — agent's recommendation stands | **40** (canon-equivalent is ~24; see §4) |
| 4 | Fix credits/trading different clocks | DONE (`844ea16`) |

## 2. WHAT SHIPPED (10 commits, `0f79f04..b4e60a7`)

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

## 3. DEPLOY CHECKLIST (operator-gated; nothing below has been done)

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
5. **`KALSHI_NETEV_GATE=0 → 1` is the arming step.** Still 0. Operator must name it.

**What arming does** — verified end-to-end, real table through the real loader and real gate:
gas −4.68%, temp −6.09%, KXTOPMODEL −3.12%, KXDXYDUD −5.60%, KXTRUMPTIME −5.89%,
KXTRUMPENDORSEMENTS −5.00% → **all six FLAT-SKIP**; 27 further families → model fallback; every
family HOLDING inv=40 → 1×40 reducing quote, **de-risk never blocked**.

⚠ **HEADLINE THE GAUGE'S BLINDNESS, NOT THE NUMBERS.** Every available window is data from a bot
carrying defects 1–14, none deployed. This table measures a DEFECTIVE bot; it cannot separate
agent defects from family economics, and these negatives are **not** a verdict on the strategy.

## 4. OPEN DECISIONS

- **Deploy + arm** (§3) — operator-gated.
- **`MIN_RECEIPT_FILLS` 40 vs 24.** 40 shipped. Families that flip between them:
  full history — `KXGENERICBALLOTVOTEHUB` (29 fills), `KXMLABELSHARE` (30); 07-29→now —
  `KXMLABELSHARE` (30), `KXTRUMPENDORSEMENTS` (38); 08-01→now — `KXTRUMPENDORSEMENTS` (28),
  `KXTRUMPTIME` (25, **+0.85%**, the only allow in the set). Zero flips on the canon window.
- **The two engines now carry different bars** — rebuild 40 fills, calibrate
  `MIN_RECEIPT_TRADES=20`. Not a defect; an inconsistency nobody has ruled on.

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
- **The $5.77–$9.30 cash-identity residual** — still unexplained, still not started. It DRIFTED
  $1.50 while halted (no trading, 4 settlements), pointing at the settlement leg. Deposits
  operator-stated **$640.00** ($565.00 venue-verified + $75 added 2026-08-03); model implies
  $629.20 cash-form / $631.81 funded-form. ⚠ Cannot be honestly re-derived from the live ledger
  until the defect-13 fix is deployed — the running recorder still uses the position-blind fill
  model. It CAN now be attacked offline against the committed frozen tape.
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

# KALSHI MAKER — HANDOFF 2026-08-02. BOT LIVE. READ §0 AND §6 FIRST.

## 0. STEP ZERO — verify yourself, trust nothing here

- Branch `claude/maker-kalshi-live`. Deployed quoter md5 **`420dc43feb1ed46353e902ecee58b5ae`**
  (== `git show b728902:kalshi_live/maker_kalshi_quoter.py`), 5,064 lines, mtime 2026-07-31T19:54:30Z.
- Verify: md5 vs git blob (LF) · `live.env` · **STOP file** · a plan row fresher than 5 min.
  "active" is not "trading."
- **Last verified state 2026-08-01T21:36:56Z (plan row, 24 s old):** equity **$252.13**, day peak
  $289.77, **drawdown $37.64 of $40**, **day-down $54.44 of $60**, 6 markets quoted, **no STOP**,
  service active, 0 journal errors, caps **MARKET=45 / ACTIVATE=40** (auto-revert fired
  10:00:01Z, verified), `MAX_TOTAL_CAPITAL=350`.
- **⚠ The bot was within $2.36 of the drawdown halt when this was written.** Check first whether
  it self-halted overnight. If a STOP exists, the flatten cost ~$5 of dust (see §1) — the real
  cost is cancelled resting orders.

## 1. POSITION STATE — effectively flat

- **20 tickers carry only fractional dust.** `position_fp` values: −0.52, 0.11, 0.55, 0.61, −1.00,
  −1.08, 1.27, 0.02, −0.44, 0.28, −0.28, −0.37, −0.73, −0.48, −0.33, 0.24, 0.43, 0.72, −0.19, 0.11.
  **Largest absolute position 1.27 contracts. Total `market_exposure_dollars` = $5.16**, largest
  single market $0.91.
- **6 resting orders across 4 tickers** (21:0xZ): TRUMPENDORSEMENTS-26AUG01-A3 ×2 (closes
  **2026-08-02 14:00Z**) · BABELMANDEBWEEKLY-26AUG02-T150 ×2 (closes 2026-08-04 13:00Z) ·
  APRPOTUS-26AUG07-39.3 ×1 and -39.9 ×1 (close 2026-08-07 15:00Z).
- ⚠ **The field is `position_fp`.** `position` / `market_position` do not exist and silently
  return 0 — I made exactly that mistake (§6.9).

## 2. OPERATOR DIRECTIVES OUTSTANDING

- **"just stop doing new markets and keep all else equal"** — **NOT DONE, and deliberately so.**
  Findings: none of the 22 hot-reloadable knobs gate new-market entry; every selection knob
  (`FOOTPRINT_TOP`, `MAX_ACTIVATE_CAPITAL`, `MAX_TOTAL_CAPITAL`, `MAX_ENTRY_CUTOFF_MIN`) is
  import-time only and needs a **service restart**. Worse, **the selector has no concept of
  "new" vs "existing" market** — it rebuilds the footprint from scratch every cycle
  (`:1379-1389`); incumbency is a scoring bonus, not a gate. So the instruction is **not
  expressible in current config.** Options put to the operator, none named yet:
  `MAX_ACTIVATE_CAPITAL=0` + restart (blocks seeding void books only) · `FOOTPRINT_TOP=4` +
  restart (still re-picks *which* 4) · build a real gate (code change, full protocol).
- **Earlier wind-down instruction superseded** by the above ("ignore prior items").
- **⚠ 8-3 OPERATOR RE-REVIEW DUE 2026-08-03** — ladder thresholds $3/$5, `STRIKES_OUT`.
  Every session must surface it.

## 3. WHAT SHIPPED 2026-08-01

- **Cap auto-revert repaired and verified.** The 10:00Z script had unexpanded `$D` → every path
  resolved to `/live.env` (filesystem root). It would have restarted the bot and silently left
  caps at 60/60 all day. Proven in a stubbed sandbox, repaired, installed
  (md5 `13af0184d2a1e68f98730e1fd64eb069`); **fired correctly at 10:00:01Z**, caps 60/60 → 45/40,
  log verified, no stray `/caps_autorevert.log`.
- **⛔ CANON: a Kalshi API reward feed EXISTS** — `GET
  https://api.elections.kalshi.com/v1/users/{user_id}/credit_history?limit=1000`. Returns
  `{credits:[…], cursor:null}`; `cursor:null` = complete. Fields `credit_id, status, type,
  amount_cents, reason, created_at`. **This refutes the standing "receipts+UI only" canon.**
  Capture method: record the web app's OWN authenticated response (patch `window.fetch`/XHR in
  the logged-in tab, click the **Credit** filter on `kalshi.com/account/activity`). A direct
  `fetch` with `credentials:'include'` returns **401** — auth is header-based.
  **LIMIT:** `reason` names the EVENT only, never the strike.
- **Lifetime rewards, exact:** **$167.35 = $152.35 incentive + $15.00 referral** (n=46, all
  applied). Supersedes the rounded "$132". Zero-reward credit days: 07-26, 07-30, 07-31.
- **Family concentration** (of $152.35): temperature $55.03 (36.1%) · gas $33.04 (21.7%) ·
  **index INXHUD/NDQHUD/DXYDUD $24.34 (16.0%, on the DENY list)** · MLABELSHARE $16.15 ·
  club football $14.24 · TRUMPENDORSEMENTS $6.08 · GENERICBALLOT $2.33 · CHIPBURRITO $1.14.
- **Payout model = close+1** (lump at close, aggregating multi-day presence). Proven by the
  presence↔credit join: presence 07-29 → credits 07-30 = **$0.00**; presence 07-30 → credits
  07-31 = **$0.00**, both at heavy presence. **The Aug 2–9 payout calendar stands.**
- **Account identity, all inputs venue-verified:** deposits **$565.00** (7 rows) · withdrawals
  **$0** (venue-verified, "No activity with these filters") · rewards **$167.35** · equity
  $288.23 ⇒ **lifetime trading P&L −$444.12**. Canon previously carried −$341.56 off stale
  $465 deposits and $132 rewards.
- **Commits:** `8dd761a` (LOSS_LEDGER REWARD column + §5 reward ledger) · `fd12db3` (retraction
  of my close+1 error). Two review docs added alongside this handoff.
- **NOT deployed, backed up only:** the watch-only any-loss cooldown shadow (option A, operator-
  named). Function `_anyloss_shadow` added to the worktree quoter, **never called** — uncommitted.
  Backup: `scratchpad/quoter_WIP_anyloss_shadow_backup.py`.

## 4. THE TWO REVIEWS

- **`KALSHI_SELECTION_SCORING_REVIEW_2026-08-01.md`** — 11 agents, triple-blind. Root defect:
  40 slots allocated, ~12 quoted, because the close pre-filter appends ~3,300 unchecked rows
  (`:1316-1318`) that a later belt kills — `footprint + drop_far_market_close == 40` in
  **2,277/2,277 cycles**. Plus: 50.7% of quote rows emit no price and no counter; the venue's
  largest pool ($1,000/day) held a slot every cycle and was never priced; net-EV gate running
  uncalibrated; one market per series ever; score cache 69% our own gate decisions.
- **`POLYMARKET_PRO_TRADER_STUDY_2026-08-01.md`** — 13 agents, read-only. Cheapest actionable
  item: **O1, 2 GETs**, to test whether `timePeriod=MONTH` really returns DAY data — our
  MirrorBot watchlist consumes that parameter (`bots/elite_watchlist.py:139`). **MB lane, not
  this one.**

## 5. HOLDS AND REPORT-ONLY ITEMS (nothing demoted)

Holds: D weekly ratchet day-by-day · B join-size cut receipts-gated · **8-3 re-review due
2026-08-03** · G removed · shelved stays shelved · index families DENIED.
Report-only awaiting operator: D5, D6, D7, D9, D10, RF2, RF3, RF4 · the `cum_settle_payout`
decreasing-cumulative anomaly (**four observations 08-01: 52.0765 → 48.3352 → 44.7479 → 44.6383
while settlement count rose 97 → 105** — do not quote that field) · the ban-before-close → $0
reward hypothesis (n=2 vs 1; **free test: TRUMPTIME-H2 closes Aug 2, TRUMPENDORSEMENTS-A5 Aug 3,
TOPMODEL-CLAU5 Aug 4 — all banned before close; three $0.00s confirms, any payment kills it**) ·
the 10 open questions in the selection review §6.
Dark and unnamed: `ALLOC_KEY`, `ALLOC_INCUMBENT_FIRST`, `PIVOT_SELECT`, `PAIR_UNWIND`.
F4 = PLAN ONLY. Queue: cooldown any-loss feed (built, watch-only, undeployed), fill-shock pause,
sign-flip counter, intra-cycle cap re-check, category caps, scoring share-term bait, velocity
breaker retune, F12, F13, mark-based governor arm, state hardening.

---

## 6. ⛔ MY FAILURES THIS SESSION — READ BEFORE YOU WORK

Operator-requested. Thirteen, with root cause and the rule that prevents each.

**6.1 — Published a canon change off ONE anomalous row.** I declared the close+1 payout model
"refuted" because `KXCHIPBURRITO-26AUG02` was credited before its event date, and wrote that into
`LOSS_LEDGER.md` **and** memory canon. The presence↔credit join — ten minutes of work — proved
close+1 correct. Retracted same session (`fd12db3`).
**Root cause:** asserted a model change at conversation tempo without the cheap verifying read.
**Rule:** RULE THIRTEEN §1. A model change NEVER goes to canon in the same message that
introduces it. Run the join first.

**6.2 — Produced −$3,317.83 of "trading P&L" on a $565 account.** I improvised a settlement
payout formula instead of using `settlement_payout()` in our own `kalshi_cash_recorder.py`,
whose docstring says in plain text that the gross/paired model is **REFUTED** and produced a
−$1,977 residual. I reproduced a bug that was already documented in our own code. Caught it on
the account identity before presenting.
**Root cause:** reinventing a canonical function instead of grepping for one.
**Rule:** before computing any money figure, `grep` for an existing implementation. If our code
has a function for it, use that function.

**6.3 — Presented gross reward by family as "where the money comes from."** Revenue with no cost
denominator, framed as a profit map. The operator caught it; I did not.
**Rule:** never present a numerator as a finding. State the denominator in the same breath or
do not state the number.

**6.4 — Claimed the bot was capital-bound at ~6–7 markets** and used it to argue selection fixes
wouldn't matter. **REFUTED:** `capped_markets = 0` in **1,125/1,125** and **1,363/1,363** cycles;
capital utilisation p50 0.384. I derived it from arithmetic ($280 ÷ $45) and never checked the
counter that measures it.
**Rule:** if a counter exists for the claim, read the counter. Arithmetic is not measurement.

**6.5 — Claimed six days of zero temperature presence was our defect.** **REFUTED for 5 of 6:**
Kalshi ran **zero KXTEMP programs venue-wide for 9 days 7:02:14** (07-23 → 08-01). I never
checked whether the programs existed before blaming our selection.
**Rule:** before attributing an absence to our code, prove the thing was present to be missed.

**6.6 — Ticker-substring searches that silently missed the target, twice.** Searched
'YOUNGBOY'/'NEVERBROKE'/'KENTRELL' against tickers and concluded no such program existed. The
real ticker is `KXYTVIEWS-YOU26AUG` — abbreviated to "YOU". Also missed it in the scored universe
for the same reason.
**Rule:** substring search on abbreviated identifiers is not evidence of absence. Search titles
or enumerate, then confirm.

**6.7 — Paginated on `cursor` instead of `next_cursor`**, silently received 1,000 of 4,801
programs, and reported "MISSING: 0" off that truncated set. Caught only because the number
contradicted the bot's own `programs_seen`.
**Rule:** any paginated pull must reconcile its total against an independent count before use.

**6.8 — Iterated a JSON file's wrapper instead of its payload** (`{schema, markets}` → 2 top-level
keys), producing "2 distinct series" and "0 YoungBoy hits". Caught before speaking.
**Rule:** print the shape (`type`, `len`, sample key) before aggregating anything.

**6.9 — Read the wrong position field during a live wind-down decision.** Used
`position`/`market_position`; the real field is **`position_fp`**. I reported "0 held tickers"
while equity−cash implied ~$7.93 of exposure. Caught only because that contradiction was visible.
**Rule:** never advise on a money decision off a parse whose fields you have not dumped once.

**6.10 — Proposed the "identity total" as a clean reward detector** without checking its variance.
`resting_reservation` swings $25–70 routinely, so it is noise, not a discriminator. I recommended
it as the better filter one message before disproving it.
**Rule:** measure a proposed metric's noise floor before recommending it.

**6.11 — Implemented a $15 cash-alarm threshold knowing it could not work.** The two observed
non-events were $22.45 and $22.14 — both above $15. I set it as named and said so, but should
have led with the fact that the number was uninformative before implementing.
**Rule:** if a named parameter provably cannot achieve its purpose, say that first.

**6.12 — Reported "0.79% of venue pool" against a denominator that includes markets we can never
trade.** The review corrected it: only $45,391.67/day of $293,111.67/day is inside
`MAX_DAYS_TO_CLOSE=8`, so the honest figure is **~5.2% of reachable pool**.
**Rule:** state which denominator a coverage figure uses, and prefer the reachable one.

**6.13 — Buried decisions under hedges.** The operator had to ask repeatedly for facts and
explicitly say "no opinion." Verified caveats are required; leading with them instead of the
decision-relevant number is not.
**Rule:** lead with the number that changes the decision. Caveats go after it, once.

**The pattern across all thirteen:** every single one was caught by a cheap check I could have
run *before* speaking — a grep for an existing function, a counter, a shape dump, a denominator,
an identity. The work-product layer (tests, mutation, md5) held all day; **every defect landed in
the conversation layer, which has no forced checkpoint.** That is exactly what RULE THIRTEEN
exists for, and it is the thing to fix in the next session.

**What went right, for calibration:** the broken cap-revert was caught before it fired; the
−$3,318 and the "0 held tickers" errors were both caught by identity/contradiction checks before
they reached a decision; the close+1 error was self-caught and retracted within the session; and
every one of the failures above is in this document rather than discovered later.

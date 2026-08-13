# KALSHI MONEY LADDER — MASTER ROADMAP + HANDOFF (2026-08-13, operator-ratified "acceptable")
STEP ZERO for any new session: read THIS DOC top to bottom, then the docs it points to, then
verify live state yourself (§7). Trust posture unchanged: verify never inherit; md5-vs-git-blob;
test EXIT CODES never grep; timestamped venue reads; pre-register before measuring.

## 1. THE MANDATE (operator, 2026-08-13 — all verbatim-intent, binding)
- Goal: MAKE MONEY on Kalshi liquidity rewards. Maker/LIP ONLY — NO directional bets, NO
  cross-venue arb (operator: "q4 a no b no").
- Capital: $3k available contingent on a clean defect record ("if fixed i will"); UNLIMITED
  if measured rewards > costs. Current venue cash $252.5294 (flatten read 08-12T19:50:24Z);
  operator can fund more when a rung justifies it.
- Halt budget: $10/day realized. Entry only if market closes ≤ 8 days out; hard exit any
  position held > 10 days. Slip-a-day-on-review-majors CONFIRMED (quality over date).
- Decision date: pre-registered 2026-08-27 scale-or-kill (compressed target ~08-25/26).
- Item naming is DELEGATED to the agent permanently; bring operator DECISIONS ONLY.
- **RATIFIED 08-13 ("acceptable"): the RUNG LADDER (§3).** Convention preserved: the FIRST
  live order after the wind-down is a relight-class event — get a one-word GO in-session
  before rung-1's first order. Everything read-only needs no ask.

## 2. WHY (the measured diagnosis — sources frozen, never re-derive from memory)
- D1 (md5 db64af18, commit ef517d8): lifetime net −$391.67 full-tape = realized −$580.73 +
  paid $189.06; defect-era concentrated (fixed build 08-10+ = −$6.22); credit-less events
  −$301.81 of net; winners are low-fill low-taker; 5 series earned credits with ZERO fills.
- D2 (md5 4c4a6561): venue pool $250,003.33/day over 3,591 programs (08-13T01:01:30Z read;
  3,735 at 13:50:39Z — moves daily), FLAT distribution (top-100 = 11.92%).
- D3 (md5 4b56d880): maker fills −$0.0478/ct settlement basis; >half lost within +5m; toxic
  classes = political-announcement, ladders near strike, weather near resolution; survivable
  = slow mechanical (−2..−3.5c/ct). LIP covered ~35% of maker drag lifetime.
- CONCLUSION ON RECORD: fills lose, presence pays. Strategy = presence with minimal fills in
  paying markets. HOW MUCH presence pays at what size is rung-ladder territory (§3) because:

## 3. THE RUNG LADDER (operator-ratified; each rung's result gates the next; never skip)
**R0a — PAPER BACKTEST ($0):** apply ALL v2.0 filters (close≤8d, mechanism-toxicity,
2×-with-paid-ratio eviction, min/target sizing) retroactively to our own fill+credit tape.
Output: what the filtered subset actually earned. If clearly credit-negative → report; the
tape may already answer the mandate.
**R0b — FILTERED VENUE CENSUS ($0):** all ~3,700 programs × [close≤8d; toxicity by MECHANISM
(ladder |mid−strike|, announcement exposure, resolution proximity) not series name; expected
$/day = pool × share_formula(our_ct, book_ct, DF) at min size AND at target size]. Output:
true addressable $/day + ranked candidates. If < ~$500/day addressable at target size →
arithmetic verdict, present to operator.
**R1 — FLOOR PROBE (~$5–20 collateral, 48h; needs in-session GO):** standalone reviewed
script (NOT the quoter) rests min-size paired quotes in 1–2 empty-book pool markets from
R0b. Read estimates feed at +24h/+48h. Answers: (a) does SUB-TARGET presence accrue anything
(the zero-payer-floor question, econ-T1 — W10 canon says a below-target book pays NOBODY;
if accrual > 0 the floor reading is refuted and BREADTH-at-min-size is reinstated);
(b) who fills a lone quote in an empty book (econ-T6).
**R2 — ONE MARKET AT FULL TARGET (~$300 ≈ current cash, 2–4 days):** THE concept test:
full-target presence in ONE R0b-selected quiet market. PASS = accrual ≈ formula-predicted
pool share AND fills ≈ 0. This rung proves or kills the concept for ≤ halt-budget × days.
**R3 — THREE MARKETS (~$1k, operator funds on R2 pass):** replication test.
**R4 — SCALE ($3k+, then per mandate):** multiplication of a measured-positive unit only.
Every rung: burn capped by $10/day halt + per-market bounds; estimates feed gives yield
readout in ~24–48h; mass-cancel to 0 resting takes seconds (proved 08-12: 11/11 in 11s).

## 4. GUARDRAILS (all binding; sources: v1.1 T1–T11 + triple-blind 24 threats, PRESENCE_FIRST_PLAN_2026-08-13.md)
- **Money map = lifeblood:** daily full-universe job + 5-min appearance scan; quoter may act
  ONLY on EARN-bucket markets; never-observed = UNKNOWN = untradeable, no override path;
  new market needs ≥24h tape; map stale → freeze new entries AND strip quotes on any market
  not re-confirmed EARN; bucket file written atomically (tmp+rename) with generated_at +
  per-market program_end self-expiry.
- **Concurrency ≤ 12 markets** (cancel-budget physics: 80 writes × 0.6s = 48s; cap live
  book to what mass-cancels in <15s).
- **Est-feed three-state gate:** FRESH+low accrual → exit; STALE/absent → FREEZE-AND-HOLD +
  alarm (never fire-sale on a dead feed); accrual windows ≥ venue recompute period; eviction
  numerator = accrual × series historical paid/accrued ratio (never-paid series = ratio 0
  until first payment); assumed maker cost prior −3c/ct (maker fees are $0 — fee-based cost
  is a lie).
- **Snap defense = SIZE + SELECTION only.** Cancel-on-signal is hygiene, never load-bearing.
  Worst-case math must close on size alone. Ladders: require |mid − strike| distance; no
  catalyst inside horizon; empty-book entries need a hard price anchor (never self-referential
  pricing).
- **State migration at relight is operator-visible:** run new load_state against prod
  quoter_state.json in test first; DD_CARRY vs new $10 halt decided by name; the residual
  wind-down positions (settling ≤08-17) pinned EXIT-ONLY; their settlements EXCLUDED from any
  new window's drag (legacy bucket).
- **Scoring (process-lens fixes, all pre-registered before any rung that scores):** per-EVENT
  same-set scoring (cost+credit travel together; post-window conclusions → CARRY bucket);
  verdict read at window-end +72h (credit envelope 38–48h is empirical, not contractual);
  NO size change inside a scored window; expected trajectory pre-registered (credits ≈ $0
  through day ~3.5 = ON-PLAN; early wind-down = VOID-UNPOWERED, never FAIL); power rule
  N ≥ 15 concluded-AND-paid programs else UNPOWERED; daily report leads with cumulative drag
  INCLUDING evicted markets (survivors-only views labeled as such).
- **Scoreboard re-registration checklist before any new window:** new T0/T7/OBS_DEADLINE/
  T0_CASH constants; flat-at-T0 verified with an ALARM not an assumption; legacy settlements
  excluded. (kalshi_window_scoreboard.py hardcodes the OLD window — it scores the dead
  window until edited.)
- **Ship discipline (unchanged, non-negotiable):** every new/changed artifact → tests +
  suite exit-code + 8-angle adversarial review + md5-vs-blob deploy + real-run + negative
  test. The review found 10 real defects in a 199-line script this session; nothing skips it.
- **fp-scale reconciliation** (ledger fractional counts vs venue integer positions) asserted
  before any gate consumes fill costs; STOP-context taker-min floor → 1ct (dust rides
  outside governors at 5ct).
- **Rate budget:** map job in declared window with lockfile the quoter respects; cancels get
  minimal retry-with-backoff; 429s logged+alarmed.

## 5. STANDING INFRA (all verified live this session — re-verify, don't inherit)
- Bot DOWN + disabled since 08-12T19:50:13Z (operator wind-down §0-A of
  KALSHI_HANDOFF_2026-08-12_LIVE_WINDOW.md); STOP file present; 0 resting (verified
  08-12T23:54:16Z); residual positions settling (gas+rain settled 08-13T13:05:44Z, rev $0.94
  on 4.055, $0 others; remaining: KXTRUMPTIME-26AUG15-H3 +0.30, KXTRUMPEND-26AUG14-A20
  −0.15, KXTOPMODEL-26AUG17-CLAUM −0.43 settle ≤08-17).
- Timers (all exit-code alarmed): reward-pnl 07:30Z; window-scoreboard 07:40Z (scores the
  OLD window until re-registered — §4); w16/w17 14:00Z; tape-compress 02:30Z (gzip-exit
  fixed `+`); gate-eval Tue 08-18 08:00Z; d4-rotate 02:40Z.
- D4 recorder LIVE (kalshi-d4-recorder.service, 60s books + trades + meta, 23 tickers,
  commit 3e13697 md5 80125768). Legacy sampler (/opt/pa2-maker-kalshi) records ONE ticker/day
  — its top-100 footprint claim is FALSE (measured 08-13); do not use it as evidence.
- Old-window bookkeeping: drag through stop −$21.9397 + first settlement +$0.94; credits
  counted $0 (correct post-F1 semantics: only credits PAID in [T0, 08-21] count — all-zero
  buckets ≠ broken feed); 08-19 §5 scoring = formality, on the record.
- Deployed box md5s: quoter 57adab17 (untouched); scoreboard 660fc479; reward_pnl 55545585;
  d4 recorder 80125768. Branch claude/maker-kalshi-live; worktree kalshi-wt (git worktree
  list → Temp scratchpad path); ALWAYS `git branch --show-current` before repo writes.
- Suite at HEAD: 1335 passed / 2 xfailed exit 0. (2 test_weather_bot.py date failures exist
  in the FULL repo suite — other lane, report-only.)

## 6. CONCERNS / OPEN ITEMS (nothing demoted; carry all)
- Parked on named triggers (unchanged): EST_FEED+M-9/M-6/M-10 guards; 9a/9b; halt-meter
  mark basis; Proposal B; cfg_stamp (rides next quoter restart — NOTE: any relight deploys
  it, intentional); scale-plan rungs (superseded-in-practice by §3 ladder but NOT deleted).
- Monitor-on-review: plan-row schema drift; parse_iso 10-copy consolidation (own review
  needed); credit_history client pagination upgrade (shared-module).
- Gate-eval 08-18 = floor-constant validation input for eviction thresholds.
- Defects 13/14 NAMED (FILLCASH-POSITION-BLIND, SETTLED-POSITIONS-BLIND) and closed.
- The triple-blind threat texts live in this session's transcript; load-bearing ones are
  encoded in §3/§4 — if ambiguity arises, the guardrail text here WINS over memory of the
  threats.

## 7. NEW-SESSION VERIFICATION RITUAL (before any work)
1. `git worktree list` → kalshi-wt path; `git branch --show-current` = claude/maker-kalshi-live;
   HEAD ≥ 9f75fd1. 2. SSH: service polymarket-maker-kalshi-ws inactive+disabled; STOP present;
   0 resting (paged read, never single-page). 3. md5sum the four deployed scripts vs git
   blobs. 4. `pytest kalshi_live -q` exit code via PIPESTATUS (expect 1335/2). 5. Read
   newest reward_pnl + scoreboard rows (exit codes of the units, not just content). 6. D4
   recorder active + rows growing. 7. Then start at §3 R0a.

## 8. CALENDAR
08-13/14: R0a + R0b (read-only, start immediately) + probe script build/review → GO ask →
R1 live 48h. 08-15/16: R1 read + R2 build/review → R2 live 2–4 days. 08-18: gate-eval.
08-19: old-window scoring (formality). ~08-20/21: R2 verdict → R3 funding decision.
~08-25/26: R3 verdict → 08-27 mandate decision. Slips only via the confirmed
quality-over-date clause; every slip named to operator same-day.

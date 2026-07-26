# KALSHI MAKER — HANDOFF (session close 2026-07-25/26)

Branch `claude/maker-kalshi-live`. Bot **PARKED** (`KALSHI_MAX_TOTAL_CAPITAL=1`). **NOTHING IN THIS
SESSION IS DEPLOYED.** Deployed build is still md5 `727ca7c59840a42b51c19e24c65a0982`.

---

## §00 — THE THESIS IS TABLED. DO NOT TURN ON THE RANKER.

**The capture model has never been checked against money actually received, and it cannot be on
existing data.** Attempted 2026-07-26: correlate modelled capture vs the 31 reward receipts.

- 16 distinct rewarded events, $88.07 total (`rewards_ui.tsv`).
- **15 of 16 fall before 2026-07-23T20:05Z**, where Kalshi has purged zero-fill order history.
- Usable pairs (receipt + complete order history): **n=1**. No correlation is computable.
- ⚠ **Do NOT "fix" this by including the pruned events.** Only orders that FILLED survive from that
  era, and fills track activity which tracks reward — it would manufacture a correlation out of
  survivorship bias and look like confirmation.

**Consequences, binding:**
- `KALSHI_SCORE_RANK` **stays OFF.** It ranks on modelled capture. Unvalidated.
- `KALSHI_PRESENCE_GATE` is **also model-dependent** — its $1.20 floor is documented by Kalshi, but
  the estimate it compares against comes from the same model. Same caveat.
- **Survives independently of the model:** the R1 division fix (a defect regardless), the per-market
  telemetry (pure observation), the far-close cap (rests on MEASURED presence, not the model).

**Validation requires spending money.** Prediction is available while parked; payment is not. There
is no read-only path. So: deploy telemetry parked → un-park at minimum size → collect ≥20 matched
pairs → then judge the ranker.

**TABLED BY OPERATOR (2026-07-26): P&L of the inventory that presence creates.** Revisit once live.
Fees are measured at $0.0000; the adverse-selection cost is not isolable without matched round
trips, and those need live fills.

## §01 — WHAT AN HOUR ON THE BOOK COSTS (measured, replaces a guessed threshold)

Unpruned slice only (orders created ≥ 2026-07-23T20:05Z): **443 orders, 5,043 contracts placed,
5,476.3 contract-hours resting.**

| | |
|---|--:|
| fees paid | **$0.0000** — maker resting is free |
| contracts filled | 1,423 = 28.2% of contracts placed |
| fill rate | **0.26 contracts per contract-hour rested** |
| inventory taken on by resting 20ct for 1h | **~5.2 contracts** |
| median life, FILLED order | 238s |
| median life, UNFILLED order | 245s |

The last two being near-identical matters: **fill risk does not accelerate with time on book.**
Presence is roughly linear in risk, not compounding. Since fees are zero, the cost of presence is
inventory — and inventory is already capped (`INV_HARD_CT=60`). At 5.2 ct/hour that is ~11.5 hours
of continuous one-sided resting before the cap binds.

⇒ **There is no measured reason to hold presence below the inventory cap.** An earlier "20%
presence" threshold in my audit tree was invented and has been struck.

## §02 — AUDIT TREE (v2)

**0 · Deploy telemetry, still parked** — rows/cycle > 0 and = footprint size → pass; any cycle
exception → rollback.
**1 · Un-park small** — $25 cap · 20ct · **12–16 markets** (capital-bound: $248.68 ÷ $15/market) ·
≤3d. Nothing rests after 3 cycles → stop, bot is broken.
**2 · Plumbing** — `create_skipped` ÷ intended > 30% → capital too tight. `amend_fail` > 0 → turn
amend off. Venue resting ≠ bot's intended for 3 cycles → stop (the bot has lost track of its own
book; 3 cycles so one dropped request doesn't trip it).
**3 · Presence** — bounded by the inventory cap, not a percentage. Fill rate ≫ 0.26 ct/ct-hr → book
turned toxic. Gaps > 1h → find the cause (79% of lost time was 5 long gaps).
**4 · Model** — matched pairs < 20 → keep collecting, ranker OFF. rho < 0.3 → model dead, rank by
pool, delete the ranker. rho ≥ 0.5 → ranker ON. 0.3–0.5 → collect more.
**5 · Profitable?** — rewards − realised fill P&L < 0 over 7d → stop.
**6 · Scale** — only after 4 and 5 pass. Cap 2× → re-run 3, 4, 5. Any regression → revert.

**Kill switches:** equity −$40/day auto-halt (live); resting ≠ intended 3 cycles; $0 rewards for 48h
while resting > 0.

## §03 — COUNT DISCIPLINE (operator correction)

"2,291 reward markets" is misleading. Same universe, three levels — **151 series / 297 events /
2,291 market strikes** (median 7 strikes per event, max 43). There are ~297 reward markets in any
human sense. 2,291 only matters because the bot reads one orderbook per STRIKE.

**Cheap fix available, untested at scale:** `/markets?limit=1000` returns **1,000 markets in 1.7s**
carrying best bid/ask, size at the touch, and 24h volume (verified against a known-deep market:
bid 0.95 / size 304 / ask 0.96 / vol24 5,359). That is a pre-filter for the whole universe in ~3
calls, instead of 2,291 orderbook reads. It gives the TOUCH only, not depth to Target Size, so it
narrows the field for the 200-read budget rather than replacing it.

## §0 — REVIEW FIRST (operator directive 2026-07-25)

**The horizon cap is temporary and deliberately conservative. Review it next session.**
`KALSHI_MAX_DAYS_TO_CLOSE=3.0` refuses any market resolving more than 3 days out. Operator's words:
*"dont do any markets more than 2-3 days away for now and we will ramp up later we need results and
data."* So the review question is not "is 3 correct" but **"do we now have enough results to ramp
it up, and to what?"** The measurement that should drive that decision is presence by market life
— re-run `kalshi_presence_calibrate.py` and compare against the numbers in §2.

Also carried forward for review:
- **Sizing / leaning in — NOT BUILT.** Order size is a flat 20 contracts capped at $15 per market,
  and does not vary with opportunity (`_capped_join`). Against measured rival books of 850 to
  100,000 DF-weighted, 20 contracts is a rounding error. The operator asked for "really lean in and
  make it count" where we do commit; nothing in this session addresses it. See §4.
- **Deploy decision still open.** Deploying the telemetry necessarily also deploys the R1 selection
  fix (same file). Inert while parked; live the moment the bot is un-parked.

---

## §1 — WHAT CHANGED THIS SESSION (all on branch, none deployed)

| commit | what |
|---|---|
| `e01e188` | per-market-per-cycle telemetry (`quotes-YYYYMMDD.jsonl`) — the R4 denominator nothing recorded |
| `86e65a1` | temp-live config plan |
| `168998a` | **retraction** of the era-confounded temp claim + venue-wide scan |
| `48b5be9` | presence measured from order history |
| `8a260ca` | presence plugin + gate |
| `0f32d32` | $1.20 floor + amend-on-decrease |
| *this* | far-close cap (3 days) |

**322 tests + 2 xfailed green** (was 281+2 at session start).

---

## §2 — THE NUMBER THAT REFRAMED EVERYTHING

**Kalshi pays on size × TIME PRESENT.** Verbatim from the LIP help page:
> "Your score for each snapshot = Order Size × Distance Multiplier"
> "Your final score = Sum of all your snapshot scores during the time period"
> "Kalshi takes random snapshots every second during trading hours"

Queue position appears nowhere in that formula. Our reward share is `f×s / (B + f×s)` — at our
scale, near-linear in BOTH presence and size, so they are worth about the same. The difference is
cost: size costs capital and fill exposure, time in markets we already chose costs neither.

**Measured presence (Kalshi order history, 980 orders / 91 markets, 2026-07-20..07-25):**
| scope | median presence |
|---|--:|
| all 91 markets | **5.7%** |
| unpruned slice only (n=20, first order ≥ 07-23T20:05Z) | **7.0%** |
| — markets under 24h life (n=10) | **16.6%** |
| — 4–14 days (n=5) | **10.0%** |
| — **over 14 days (n=5)** | **0.02%** (max 1.15%) |

⚠ Kalshi PURGES zero-fill cancel history older than ~2026-07-23T20:05Z (only filled orders survive
before that), so any presence study must use the unpruned slice or it under-counts. Boundary was
static across two reads 35 min apart — retention rule not established.

**Where the missing time goes** (14 markets, 15.63h of interior gaps):
- cancel/recreate churn (<30s): **0.30%** — 81 gaps, 2.4 minutes total
- genuine absence (≥30s): **99.70%**, and 5 gaps over an hour are 79% of it

⇒ **Order-management churn is NOT the presence problem.** Long absences are. Scope: interior gaps
only — time before our first order and after our last is not decomposed here and is likely larger.

---

## §3 — DEFECTS FOUND AND FIXED

1. **R1 pool divided by window length** (`select_footprint`) — `period_reward/10000` is ALREADY the
   daily pool. This is a **SELECTION** bug, not cosmetic: `usd_day` orders the footprint, the series
   rotation, `cap_desired` and `bound_creates`. Inflated ~1h windows ~24x and deflated long ones by
   their length in days. The 07-25 audit fixed the analysis scanner and never the bot; the deployed
   build still carries it. Fixed + regression-pinned (`test_r1_pool.py`).
2. **Size in the diff key** (`diff_orders`) — trimming a resting order by one contract cancelled and
   rebuilt it at the back of the queue. Kalshi preserves queue position for size DECREASES only, so
   only that case is routed to amend (`KALSHI_AMEND_DECREASE`, default 0).
   ⚠ **The amend endpoint is UNVERIFIED against the live venue** — unit-tested only.
3. **Instantaneous reward estimate** — `_prospective_capture` assumed 100% presence. Now corrected
   by window-fraction-remaining (structural) × presence factor (empirical).

**Resolved as NOT defects** (forensic agent, read-only): the 3,420-vs-899 create gap is (a) `creates`
logging INTENT not acceptance — actually sent was 1,604 — and (b) Kalshi's history purge. No orders
were lost. Post-only rejections are correctly raised and counted (50 of 3,608 = 1.4%).

---

## §4 — OPEN, NOT ADDRESSED

- **Sizing/allocation.** See §0. The `$1.20` floor is what makes concentration correct: spread thin
  and every market lands under the floor and pays zero. Needs a real allocator, not a cap.
- **Capital.** 1,794 of 5,214 intended orders (34.4%) were never placed pre-wind-down because the
  capital cap bit. An order never placed earns nothing.
- **Telemetry gaps:** no counter for creates ACCEPTED (`:1254` logs intent); only the FIRST create
  error per cycle is kept (`:1229-1230`), so how many of the 50 failures were post-only crossings is
  unknown.
- **Position limits.** Rule 5.19(a) counts RESTING orders toward the limit, not just filled ones,
  and the limit is loss-denominated. Our allowlist has never been audited against per-contract
  limits. **UNVERIFIED — do before un-parking.**
- **Unexplained outage** 2026-07-22T02:34Z, ~55 min. The other 55-min gap that day is explained (the
  daily-loss halt firing at 17:31:13Z).
- **Queue position** is exposed by the API (`Get Order Queue Position`) and we never read it.

---

## §5 — POSITIONS AT SESSION CLOSE (measured 2026-07-25 ~23:4xZ)

**ZERO resting orders ⇒ ZERO reward accruing.** LIP pays for resting orders, not for positions.

| ticker | pos | closes in | exit route | value |
|---|--:|--:|---|--:|
| `KXAAAGASW-26JUL27-4.160` | −34 | 1.16d | sell NO @0.99, depth 36,989 | **$33.66** |
| `KXAAAGASW-26JUL27-4.120` | +9 | 1.16d | sell YES @0.16, depth 619 | $1.44 |
| `KXTRUMPENDORSEMENTS-26JUL25-A20` | +20 | 0.58d | sell YES @0.02, depth 1,560 | $0.40 |
| `KXAAAGASW-26JUL27-4.080` | −20 | 1.16d | **no bid** | $0.00 |
| `KXAAAGASW-26JUL27-4.140` | +62 | 1.16d | **no bid** | $0.00 |
| `KXTRUMPENDORSEMENTS-26JUL25-A3` | −17 | 0.58d | **no bid** | $0.00 |

Total mark-to-bid **$35.50**, of which one position is $33.66.

**Recommended resolution: let them settle.** Three have no bid at any price — settlement is the only
route and they are already near-worthless, so waiting costs nothing. The one position holding
essentially all the value sits at 99c: settlement pays the last cent AND avoids the taker fee that
crossing a bid would incur. Everything closes inside ~1.2 days. The four `KXAAAGASW` strikes are the
same event and resolve together on one gas print.

---

## §6 — TEMP

Watcher ran ~2h continuous at 1-min cadence this session (poll #120, 2026-07-25T23:02Z): **temp
still absent**, 2,271 active liquidity programs. Latest temp `end_date` anywhere remains
2026-07-22T17:00:00Z. All five `KXTEMP*` cities are in the live allowlist, so nothing must be added
for them to be picked up on return. **Absent ≠ gone** — they are ~58-minute hourly programs.
Restart the watcher next session: `scratchpad/temp_watch.py` (does not survive a session change).

⚠ The temp-vs-gas comparison is **ERA-CONFOUNDED** — temp ran 07-20..07-22 through the launch-day
defect window, gas earned 91.9% of its total on 07-23 onward. Totals hold; causal family claims do
NOT. Do not re-derive "temp is worse" from that data.

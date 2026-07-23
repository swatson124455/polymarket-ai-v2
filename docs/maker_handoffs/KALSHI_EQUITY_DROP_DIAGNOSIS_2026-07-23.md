# KALSHI MAKER — EQUITY DROP DIAGNOSIS (2026-07-23)

**Operator question (live):** "Portfolio was ~$245, now ~$230 — why the bleed?"
**Window:** loss-meter re-baseline 17:12Z ($247.54, right after a +$150 deposit; **no deposits since**) → reads through ~19:41Z.
**Method:** measured, not assumed. Units validated (`balance`/`portfolio_value` = cents/100; `position_fp` = contracts; settled-contract tape phantoms excluded via venue `market_positions`). Every mark read is instantaneous on a fast, thin gas book — figures given as ranges where the book moved under the read.

---

## 1. ONE-LINE ANSWER + BRIDGE TO THE CENT

**The account is down ~$8 (currently ~$237–239) from the 17:12Z $247.54 baseline. It is NOT the reassuring "benign maker mark-to-mid, rewards just lagged" story — the loss is dominated by REALIZED adverse-selection churn on the ATM gas ladder, which is permanent and reward-uncompensated. It has stabilized (not still falling).**

Decomposition, post-17:12Z baseline (the settlement is PRE-baseline — see below):

| component | $ | durable? |
|---|---|---|
| **realized churn** (post-baseline, tape==venue exact) | **−6.26** | **YES — permanent, locked** |
| unrealized markdown (remaining on book) | −1.7 to −7 (timing-noisy) | no — mark-dependent |
| fees | −0.00 | — |
| post-baseline settlements | 0.00 | — |
| deposits | 0.00 | — |

**The bridge closes to the cent, two independent ways:**

- **19:05Z snapshot** (equity $233.47, the trough): Δequity from baseline = 233.47 − 247.54 = **−14.07** = realized −4.195 + Δunrealized −9.875 + fees 0 + deposits 0. **Residual $0.0000.**
- **19:26Z snapshot** (equity $239.54, after recovery): Δequity = 239.54 − 247.54 = **−8.00** = realized −6.255 + Δunrealized −1.74. **Residual < $0.01.**

The move BETWEEN those two snapshots is the whole story in miniature: the bot **sold marked-down gas inventory**, converting unrealized (−9.875 → −1.74) into realized (−4.195 → −6.255), while equity mark bounced +$6 on the thin book. i.e. the "recovery" is a thin-book mark bounce on remaining inventory; the loss that got *locked in* grew.

**Cross-checks that pin the units (no unit bug found — R3):** tape S.replay reproduces venue `position_fp` 10/10; tape carried cost basis == venue `market_exposure_dollars` = $89.33 to $0.0000 (10/10); tape realized == venue `realized_pnl_dollars` = −$4.66 to −$4.93 (10/10); venue `portfolio_value` $82.21 == independent orderbook mid-mark $82.29 ($0.08 apart).

**Settlement is NOT part of this drop.** Today's *only* settlement was **−$7.47 at 12:25:36Z** (KXAAAGASD-26JUL23, 4 strikes, total revenue 29¢). That is **PRE-baseline** (12:25Z < 17:12Z) and is already absorbed into the $247.54 re-baseline. **Nothing settled after 12:25Z.** So the settlement does not contribute to the −$8 drop-from-baseline — but note it means **today's total realized loss including settlement is ~−$12.4** ($7.47 settlement + ~$4.9 open-book churn).

**Confirmed clean:** 0 taker fills today (TAKER_FLATTEN=0), $0.00 fees, 131 fills today all maker.

---

## 2. IS IT STILL FALLING? — NO. Stabilized, marginally recovered.

**Not falling.** Governs urgency: this is not an active free-fall requiring an emergency stop.

Direct `/portfolio/balance` mark-equity reads (cash + portfolio_value):

| time (UTC) | equity | note |
|---|---|---|
| 18:48 | 232.63 | trough |
| 19:05 | 233.47 | |
| 19:23 | 236.87 | |
| 19:26 | 239.54 | |
| 19:26–19:29 (5 samples) | 239.54 / 239.54 / 239.56 / 239.56 / 239.30 | flat ±$0.26, cash pinned |
| 19:32 | 239.28 | |
| 19:37–19:41 | ~237.4 | |

Net from the 18:48Z trough: **up ~$5–7**, oscillating in a ±$1.5 band that is **thin-book mark jitter, not trades** (cash held flat across the 19:26–19:29 window while mark moved a few cents). One 19:07 sample is the mechanism in one line: cash +$3.06 / mark −$3.06, equity unchanged — a maker ask filled, inventory→cash, **benign cash↔mark rotation, not loss.**

**De-risking, not deploying:** cost basis fell $102.12 → $89.33 over the same window; cash rose. The bot is currently reducing the footprint, not expanding into it. (Forward-risk caveat in §5 — it also *reloaded* the toxic strike.)

---

## 3. BENIGN vs REAL — **the unrealized is genuine adverse drift, NOT benign mark-to-mid.** (all three verifier passes escalated this)

**Lead finding (R2/R3/R4, all MEDIUM, all "refuted" the benign story):**

The reassuring hypothesis was "buy at bid, mark to mid → small benign markdown, reward-compensated." **That is refuted on the sign and the magnitude:**

- **Half-spread is a CREDIT, not the loss.** For a maker filled at its own resting bid, marking to mid is a small *gain*. Measured across all 10 positions: half-spread cushion = Σ½·spread·q = **+$2.27 to +$2.64** (a cushion). It is an order of magnitude too small — and the wrong sign — to be the loss.
- **The loss is REALIZED adverse-selection churn.** Venue `realized_pnl_dollars` on the open book = **−$4.93** (read 19:28–19:32Z), of which **GASD-26JUL24-4.110 alone = −$7.35**. Post-baseline realized change = **−$6.26** (tape==venue, mark-independent). This is permanent; no future reward reverses it.
- **The single toxic strike, GASD-26JUL24-4.110:** the bot's resting YES bids were filled at 0.87/0.85/0.84/0.78/0.74 (16:17–18:32Z) by one-directional sell flow; the ATM strike then collapsed to ~0.52–0.55; the bot took forced NO-side exits and flipped to net short, eating **−$7.11 realized**. A −$7 single-strike *realized* loss cannot come from mid-marking — it is a maker knife-catch (adverse selection).
- **Residual unrealized is directional, not spread.** The remaining below-cost markdown is dominated by **GASW-26JUL27-4.140** (long-YES 40 @ 0.710 vs mid ~0.605 = **−$4.00 to −$4.20**, spread only 1c) — a fair-value move, not a half-spread.
- **Coherent gas repricing confirms adverse (not noise):** hourly ladder candles show *every* GASD-26JUL24 strike fell 16→19Z, biggest at the ATM (4.110: 0.885→0.525, −36c; 4.115 −15c; 4.120 −10c; 4.130 −4.5c). A monotone, ATM-centered down-shift = the implied gas distribution moved down, adverse to a book that is net long YES at the ATM. GASW-26JUL27 same signature.

**Benign share of the loss: ~0%.** Split at current marks: ~**+$2.3 benign half-spread cushion** offset by ~**−$8.9 genuine adverse drift** (liquidation basis), of which ~−$6.3 has already crystallized into realized.

**One reassuring claim survives (R1, could NOT be refuted):** equity is not still falling (§2), and the book is de-risking (cost basis $102→$89). So it is a **bounded, stabilized ~$8-net loss off baseline, not an ongoing free-fall.** But R1's own caveat: "rewards will fix it" *undersells the realized share* — ~$6.26 is locked and reward-uncompensated.

---

## 4. THE LAGGED-REWARD CONTEXT — a real fact, but insufficient as comfort (explicit GUESS / upper bound)

The lag is genuine and matches the **§M13 mid-period pattern**: mid-period `portfolio_value` shows the **cost side** of maker inventory with **none of the not-yet-posted LIP reward side** — reward credits post at reward-period *close*, so the account marks the bought inventory down now and books the compensating credit later.

**Reward-period close times (from `/markets`, so this is exact, not guessed):**
- GASD-26JUL24 reward period closes **2026-07-24T03:59:00Z** (~8h out) — **$0 posts today.**
- GASW-26JUL27 reward period closes **2026-07-27T03:59:00Z** (~4 days out) — **$0 posts today.**

**Reward magnitude — GUESS / receipt-anchored UPPER BOUND (NOT `rewards_residual`, which is banned as demonstrably wrong):**
- Receipt anchor (`reward_reality_refute.json` m7d): actual **paid** gas-daily credits = [$3.75, $1.75, $2.57, $2.02] = **$10.09 per 4 GASD markets per ~13.15h period**. The pricing *model* over-predicts these ~5×, so **only the receipts are usable.**
- The bot had heavy GASD-26JUL24 presence for only ~2.3–2.6h of a ~13h period (deployed 16:55–18:11Z) → today's GASD accrual **~$2–4 gross (GUESS)**. Today's GASW slice is a tiny fraction of a 7-day pool.
- **Grand defensible upper bound ~$4–8 gross accrued today, $0 posted.**

**Why the lag does not make the loss benign:**
1. It gives **zero relief to today's equity** — nothing posts before 2026-07-24T03:59Z.
2. It **cannot cover the booked churn** — today's realized (−$6.26 post-baseline, or −$12.4 incl. settlement) exceeds the ~$4–8 gross reward upper bound, and reward credits **do not reverse realized losses** regardless.
3. Even at full maturity, **canon §M8: mature gas net = +1.1% of notional** (~+$0.9 on ~$84 gas notional) — *rewards included*. So the fully-posted reward leaves this book razor-thin positive, **not a cushion capable of absorbing a −$5 to −$12 realized day.**

Net: the lag explains why the cost shows before the credit, but "rewards will cover it" is **cope for the realized leg** (R2). The unrealized leg *might* be EV-neutral-to-positive once rewards post — **unbooked, unproven, must not be netted against the loss now.**

---

## 5. WHAT (IF ANYTHING) TO DO — options, by reversibility. **No code deploy. No cutting temp.**

The account is stabilized and de-risking; there is no emergency action. The decision-relevant risk is **forward, not current**: at 19:34Z the bot **RE-LOADED +23 YES of the same toxic GASD-4.110 strike**, is still quoting the adversely-selected gas ladder, and has room from ~$89 cost basis to the **$250 cap** — so the adverse-selection mechanism is **live and can scale with the footprint** (R4).

**Option A — Do nothing (let it ride).**
Rationale: equity flat ~$237, not falling; GASD-26JUL24 settles tomorrow ~14:00Z and its LIP posts 03:59Z; GASW LIP posts after 07-27. Reversible by definition (no change).
Cost: leaves the live adverse-selection mechanism unbounded to the $250 cap; if gas ladders keep repricing against the long-YES ATM inventory, more knife-catches book.

**Option B — Reduce `MAX_TOTAL_CAPITAL` (Tier-2 config, reversible, operator-gated). [recommended if the operator wants to bound downside tonight]**
Rationale: caps further deployment into the same thin gas books that keep marking down, without touching quoting logic or code. Directly addresses the R4 forward risk (footprint scaling the mechanism). Bounds the adverse-selection exposure to roughly current inventory while rewards accrue.
Reversibility: fully reversible env change — `export MAX_TOTAL_CAPITAL=<old>` + operator restart to revert. This is a Tier-2 trade-universe change (narrows what the bot can deploy), **operator-gated — I am not executing it.**
Not-covered: does not de-risk existing inventory; only stops *adding*.

**Option C — Watch specific positions (no change).**
- **GASD-26JUL24-4.110** — reloaded at 19:34Z, actively churning at realized losses; the live toxic strike.
- **GASW-26JUL27-4.140** — long-YES 40 @ 0.710, ~−$4 unrealized drift, LIP not until after 07-27.
- **GASD-26JUL24 settlement tomorrow ~14:00Z** — worthless-expiry risk on the far long-YES strikes (4.120/4.125/4.130); this is the §M8 signature. Watch the settlement, not tonight's marks.

**Explicitly NOT recommended:**
- **No code deploy.** This is a diagnosis; the quoter is untouched (md5 unchanged by this session).
- **Do NOT cut temp.** Temp markets are gated to 2026-07-27 and are **not even in the current book** (the book is gas + one AMSAVO). Cutting temp addresses nothing here.

---

## 6. HONEST UNCERTAINTY — what could not be verified + read-only checks

**Could not verify / GUESSes flagged:**
- **No stored mark-equity time series exists.** `quoter_state.json held_hist` is cost-basis, not equity. Equity anchors are 17:12Z ($247.54 from the loss meter) + direct reads 18:48Z onward. **The equity path 17:12→18:48Z is inferred, not sampled.**
- **`unrealized_base ≈ 0` at 17:12Z is reconstructed, not a direct venue mark.** Rebuilt to $247.539 vs the $247.54 baseline (residual $0.001) by position-aware tape; the loss meter was re-based at cost right after the deposit, so ~$0 unrealized is inferred. Not a stored snapshot.
- **Thin-book timing noise.** `portfolio_value` refreshes in discrete ~40–50s steps and swings ±$5 on this book. Any single-instant unrealized or "to-the-cent" bridge is a **snapshot**, not a steady state — this is why unrealized reads range −$1.7 to −$8.9 depending on the second. The **mark-independent** number (realized churn −$6.26, tape==venue) is the durable floor; trust it over any single mark.
- **"Gas moved" is inferred from the coherent Kalshi ladder repricing, not an external AAA gas feed.** No independent underlying cross-check taken.
- **Reward magnitude (~$4–8 gross today) is a GUESS** — receipt-anchored upper bound, not a booked figure. `rewards_residual` was NOT used (banned as demonstrably wrong).
- **Net-EV of the adverse inventory** (does accrued+future LIP beat the −$8.9 markdown?) is **not answerable from marks alone.**

**Read-only commands for the operator to check independently** (from `.../kalshi-wt/kalshi_live`, module `L`):
```
# current total mark equity = (balance + portfolio_value)/100
python3 -c "import kalshi_attribution_ledger as L; b=L.get('/portfolio/balance'); print((b['balance']+b['portfolio_value'])/100, b['balance']/100, b['portfolio_value']/100)"

# per-position venue realized + exposure (the durable loss floor)
python3 -c "import kalshi_attribution_ledger as L; p=L.get('/portfolio/positions'); [print(m['ticker'], m['position_fp'], m.get('realized_pnl_dollars'), m.get('market_exposure_dollars'), m.get('fees_paid_dollars')) for m in p['market_positions'] if m['position_fp']]"

# confirm zero takers / zero fees today, single settlement pre-baseline
python3 -c "import kalshi_attribution_ledger as L; f=L.get_paginated(L.P+'/portfolio/fills','fills'); tak=[x for x in f if x.get('is_taker')]; print('fills',len(f),'takers',len(tak),'fee_sum',sum(x['fee_cost'] for x in f))"
python3 -c "import kalshi_attribution_ledger as L; s=L.get_paginated(L.P+'/portfolio/settlements','settlements'); print([(x['ticker'], x.get('settled_time')) for x in s][:5])"

# reward-period close times (proves $0 posts today)
python3 -c "import kalshi_attribution_ledger as L; import json; print([ (t, L.get('/markets/'+t).get('market',{}).get('close_time')) for t in ['KXAAAGASD-26JUL24-B4.110','KXAAAGASW-26JUL27-T4.140'] ])"
```

---

## VERIFIER (adversarial) VERDICTS — reported in full, including those that contradict the benign story

| lens | refuted the benign story? | severity | one-line |
|---|---|---|---|
| **still-bleeding** | **NO** (could not refute "stabilized") | LOW | Equity NOT falling — 232.63(18:48)→239.5(19:26)→flat. Overreach flagged: ~$6.26 has crystallized to realized, so "rewards fix it" undersells the locked share. |
| **reward-lag-is-cope** | **YES** | MEDIUM | Loss dominated by BOOKED realized churn (−$4.93 open book, −$7.35 on 4.110); reward accrual ~$4–8 gross, $0 posts today, §M8 net +1.1% too thin to cover. Lag is a fact; comfort is cope. |
| **measurement/unit-error** | **YES** (units OK, but "closes to $0 / reverted to −$1.98" is a stale-mark artifact) | MEDIUM | No unit bug (tape==venue 10/10). Durable loss = realized −$6.256, mark-independent. GASW-4.140 −$4.00 below cost = adverse drift, not spread. |
| **adverse-selection-not-benign** | **YES** | MEDIUM | Genuine adverse selection on ATM gas: 4.110 −$7.11 realized knife-catch; half-spread cushion only +$2.27 (too small). LIVE forward risk: bot reloaded 4.110 at 19:34Z, footprint room to $250 cap. |

**Consensus:** not still falling (reassuring, holds) — BUT the loss is **real crystallized adverse selection, not benign mark-to-mid**, and **rewards cannot and do not cover the realized leg**. The mechanism is **live and scalable to the cap** (the only forward-looking concern → Option B).

---

**Bottom line:** ~−$8 off the 17:12Z $247.54 baseline (now ~$237–239), decomposing to **−$6.26 realized adverse-selection churn** (permanent, mostly the GASD-26JUL24-4.110 knife-catch) **+ ~−$2 to −$7 unrealized markdown** (thin-book, timing-noisy), **$0 fees, $0 post-baseline settlements**. The unrealized is **genuine adverse drift on long-YES ATM gas, ~0% benign half-spread** (the half-spread is a +$2.3 *cushion*). Rewards are lagged and receipt-thin — a real fact, not a fix. **Equity is stabilized, not falling** — no emergency. The one live risk is the bot re-loading the same toxic gas ladder with room to the $250 cap; if the operator wants to bound that tonight, **reduce `MAX_TOTAL_CAPITAL` (reversible Tier-2, operator-gated)** — otherwise do nothing and watch the GASD-26JUL24 settlement tomorrow ~14:00Z. **This is a diagnosis, not a change; no deploy.**

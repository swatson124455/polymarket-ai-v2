# KALSHI MAKER — INVENTORY DOCTRINE (FINAL)
**2026-07-23 · SANDBOX PROPOSAL under the 2026-07-23T02:19Z freeze. Nothing deployed.**
Read-only session: no live contact, no `live.env` writes, no orders, no systemctl, no edits to
any existing module. New files only (`git status` shows `??` for every file this session created).

---

## §0. THE HEADLINE, BEFORE ANY DOCTRINE

**Operator feedback on v1 was "this is still too crude — a threshold ladder with hand-picked cent
values, which also broke on short-lived markets." That feedback is upheld, and so is something
worse: v1 was refuted at BLOCKER severity by all four independent review lanes, and my own
re-measurement confirms them.** This document is not a patch on v1. Its central claim is
different, and most of it is a subtraction.

**The one-line answer to "what should the inventory doctrine be":**

> **Inventory doctrine is the wrong lever. The money is lost at the fill, not at the exit, and the
> single largest measured improvement available to us is a one-line config change we already have
> on the table. What survives as doctrine is four deletions and one selection rule — and the
> selection rule keys on the reward program, not on inventory, not on price, not on P&L.**

The decisive measurement, re-derived by me this session from the receipts
(`kalshi_live/kalshi_transactions_2026-07-23.csv`, 244 trades, complete for 07-20→07-22):

| cell | lots | contracts | basis $ | P&L $ | **a (c/ct)** |
|---|---|---|---|---|---|
| GAS traded out | 126 | 651.1 | 268.06 | −5.47 | **0.84** |
| GAS settled | 4 | 0.8 | 0.22 | +0.19 | −23.22 *(n=0.8 ct, ignore)* |
| TEMP **settled** | 28 | 245.3 | 57.02 | −23.78 | **9.69** |
| TEMP **traded out** | 86 | 572.5 | 176.82 | −50.93 | **8.90** |

**Temp's traded-out fills lose 92% as much per contract as its settled fills (8.90 vs 9.69 c/ct).**
Exiting is not what went wrong. If every settlement death had been exited perfectly at the
prevailing price, we would have swapped a 9.69 c/ct loss for an 8.90 c/ct loss. An exit doctrine
cannot recover a loss that was already priced into the fill.

⚠ **I got this wrong on the first pass this session and caught it.** My initial classifier called
a lot "settled" iff `exit_price == 0.00`, which keeps every losing settle and discards every
winning one — a selection bias that inflated `a_settle` to 19.26 c/ct and would have made the exit
look like the lever. A Kalshi settlement exits at exactly 0.00 **or** 1.00. Corrected classifier in
`kalshi_live/kalshi_doctrine_final_verify.py` (V3); the corrected figures above reproduce the
design lane's numbers exactly.

---

## §1. WHAT PROFESSIONALS ACTUALLY DO — and what does not transfer

### 1a. The literature, with what was actually read

| source | read? |
|---|---|
| Avellaneda & Stoikov (2008), *Quantitative Finance* 8(3):217–224 | ✅ full PDF, equations (3)–(30) |
| Guéant, Lehalle & Fernandez-Tapia (2013), *Math. Fin. Econ.* 7:477–507 (arXiv 1105.3115) | ✅ Props 1–3, Thms 1–2 |
| Guéant (2017), "Optimal market making", *Applied Math. Finance* 24(2) (arXiv 1605.01862) | ✅ §2–§5 |
| Glosten & Milgrom (1985), *JFE* 14:71–100 | ✅ full PDF |
| Feil & Nendel (2026), "Optimal Market Making in Prediction Markets", arXiv 2607.17991 | ✅ model, HJB, numerics |
| **Kyle (1985)**, *Econometrica* 53(6):1315–1335 | ❌ **scan, no text layer — restated from secondary source** |
| **Ho & Stoll (1981)**, *JFE* 9:47–73 | ❌ **paywalled, not obtained — cited only as A-S themselves describe it** |

**The forms.** A-S maximise CARA utility over `dS = σ dW` with exponential fill intensities
`λ(δ) = A e^{−kδ}`. The reservation price (their Eq. 8, recovered as Eq. 29) is

```
r(s,q,t) = s − q γ σ² (T − t)
```

and the optimal total spread (Eq. 30) is `γσ²(T−t) + (2/γ)ln(1+γ/k)` — **independent of q**.
Inventory is managed entirely by *shifting both quotes together*, never by widening.

**The single most transferable fact in the entire canon**, and it is a negative result:

> **In A-S, GLFT, Guéant (2017) and Feil–Nendel, the state is `(t,q)` or `(t,p,q)`. There is no
> entry price, no cost basis, no P&L-to-date in any of them. No model's optimal quote depends on
> what you paid.**

### 1b. What practitioners do

- **Bürgi, Deng & Whelan (Jan 2026), "Makers and Takers: The Economics of the Kalshi Prediction
  Market"** (UCD WP; GWU CER WP 2026-001; MPRA 126350) — 313,972 Kalshi contracts, 2021→Apr 2025.
  Verbatim (p.27): *"The average return on contracts for Makers was −9.64% while for Takers it was
  −31.46%."* And: *"On average, Makers who buy contracts costing 50c and over earn a 2.6% rate of
  return."* Returns worsen monotonically as contract price falls.
  ⚠ Measured **to settlement**, i.e. exactly our F2 population, and **before** LIP existed.
  **Reading: the aggregate Kalshi maker book, held to resolution, loses ~10%, concentrated in cheap
  contracts. Rewards are not a bonus on a profitable book — they are the whole business.**
- **Hendershott & Menkveld (2014), "Price pressures", *JFE* 114(3)** — 11 years of NYSE specialist
  inventory; price pressures average 0.49% with a **0.92-day half-life**. Intermediaries mean-revert
  inventory **by moving quotes and waiting**, not by firing a stop.
- **Menkveld (2013), "High frequency trading and the new market makers", *J. Fin. Markets* 16(4)** —
  *"four out of five of its trades are passive"*; the firm *"incurs a loss on its inventory but
  earns a profit on the bid-ask spread."* Inventory is a cost centre, paid down deliberately.
- **Susquehanna (SIG)** became Kalshi's first designated institutional MM in Apr 2024. Kalshi's
  *Liquidity Provider Program* is **not** LIP: it requires an executed Market Maker Agreement and
  runs per-series auctions. **LIP — our programme — contains no risk or inventory guidance at all.**
  ⚠ And per canon §M9, the two are **mutually exclusive**: S1 excludes from LIP eligibility
  *"members who have executed a Market Maker Agreement with Kalshi"*.
- **Polymarket's maker-rewards spec** scores `S(v,s)=((v−s)/v)²·b`, and — precedent worth noting —
  makes one-sided orders score at a **÷3 haircut** when mid ∈ [0.10,0.90], requiring double-sided
  liquidity **only at the tails**.

### 1c. What transfers to us, bluntly

**TRANSFERS:**
1. **Cost basis has no place in a quote.** Unanimous across the canon. This is the single change
   with the strongest theoretical and empirical backing available to us.
2. **Inventory is worked off with price and patience, not with a stop.**
3. **Cheap contracts are where makers lose** (Bürgi et al., n=313,972) — corroborated in our own
   receipts, where the refused temp band 0.00–0.20 carries 466 of 818 temp contracts.

**DOES NOT TRANSFER — say it plainly:**
1. **Continuous requoting.** A-S/GLFT assume you requote continuously. We run a **2-minute cycle
   with `WRITE_BUDGET=60`**. Measured time-to-first-opposite-taker in temp is **p25 0.97 min, p50
   1.58 min** (n=109 lots) — *shorter than our sample period*. A control loop whose sample interval
   exceeds the process time constant does not track. Any doctrine requiring sub-cycle reaction is
   useless here.
2. **Menkveld's HFT design** is a two-venue hedge at 8.1%/64.4% participation. We have one venue,
   no hedge instrument, and $85.
3. **σ and γ.** A-S's whole spread is `γσ²(T−t)`. We have no calibrated γ, and on a ~1-hour binary
   with a hard settlement, `σ` is not a diffusion coefficient in any stable sense.
4. **Being the marginal maker.** A-S assumes your quote moves your fill probability. Against a
   **1000-contract Target Size with 20 contracts**, canon §M2 measured our size as marginal to
   two-sidedness in **0 of 304** snapshots, both sides. We are ~2% of the qualifying set. We do not
   move the book; we sit in it.

---

## §2. THE ONE INSIGHT THAT REFRAMES THE PROBLEM

The prompt's candidate was: *professionals never place a capped exit order; inventory is worked off
through skewed two-sided quoting, which dissolves the notion of "an unwind order that cannot fill".
Does our DF cliff permit that, or forbid it?*

**Answer: it forbids exactly one half of it and makes the other half FREE. The asymmetry is the
insight, and it is specific to a rebate farmer.**

A-S shifts **both** quotes by `qγσ²(T−t)`. Decompose that into its two halves under LIP:

**The accumulating half is FORBIDDEN.** Stepping the accumulating quote away from reference costs
`DF^N = 0.50^N` — one tick is −50%, two is −75%. Worse, it is **bimodal, not a slope**: the skew
study (n=657 FROZEN + 469 FRESH contract-sides) found the credit either survives at a median
**0.53** multiplier or goes to **exactly zero**, and the zero occurs **if and only if depth at the
reference price alone already meets Target Size** — mechanism explains **117/117 and 189/189**
zeroes, none unexplained. The cliff rate is not a constant: it moved **0.6% → 61%** on the *same
series* 14 hours apart. **No fixed parameter can price a skew step.**

**The reducing half is FREE — and is actually reward-POSITIVE.** Under **R4**, the Reference Yes
Price is the highest qualifying bid, and score is `DF^N × size`. If we raise our bid **above** the
prior best on the reducing side, *we become the reference at N=0* (full credit), and every
competing bid that was at N=0 moves to N=1, **halving its score** and raising our normalised share.
Confirmed against the canon's own conformance table (§M3, `qualifying_walk`).

So:

> **Under LIP, front-of-book is simultaneously maximum reward and maximum fill risk. For the
> REDUCING side, both are what you want — aggression is free and doubly rewarded. For the
> ACCUMULATING side they are in direct conflict, and DF=0.50 prices stepping back at −50%/tick,
> which is far too expensive to use as a risk control.**
>
> **Therefore: implement exactly half of Avellaneda–Stoikov. Skew the reducing side through
> reference, always. Never skew the accumulating side at all — its only control is size, and
> (per §3) that control is on/off, not continuous.**

This genuinely does dissolve "an unwind order that cannot fill" **as a pricing problem**. It does
not dissolve it as an *existence* problem — see F1 in §4, which is where the doctrine's honest
limit lies.

⚠ **And it does not make money.** Per §0, the exit is second-order. This insight is real, it is
correct, and it is worth roughly the difference between 8.90 and 9.69 c/ct on temp.

---

## §3. THE DOCTRINE

### 3.0 Objective

Per dollar of quote value resting for one hour, on one side of one contract:

```
EV  =  ρ_c · Λ(size)  −  h · a
```

where `Λ` is the **payout functional, not a multiplier** — because of **R2**:

```
Λ(size) = period_payout(size)   if period_payout(size) >= $1.00   else   0
```

**This discontinuity is the entire reason v1 failed, and it is a rulebook fact, not a tuning
choice.**

### 3.1 THE MEASUREMENT THAT KILLS CONTINUOUS CONTROL

R2 says a whole-Time-Period payout below $1.00 pays **zero**. Our complete observed credit
population is 10 rows, CSV-verified, totalling $25.21:

```
1.01  1.14  1.33  1.73  1.88  2.23  2.29  2.47  3.74  7.39
```

Apply a uniform size haircut λ (optimistically assuming payout is *linear* in size before the
floor — that is the skew study's own `cost/control = 1.000` finding; if it is superlinear the cliff
is worse):

| λ | paid $ | % of full | contracts zeroed | **reward elasticity** |
|---|---|---|---|---|
| 1.00 | 25.21 | 100.0% | 0/10 | — |
| 0.92 | 22.26 | 88.3% | 1/10 | **1.49** |
| 0.79 | 18.22 | 72.3% | 2/10 | **1.38** |
| 0.75 | 16.30 | 64.6% | 3/10 | **1.52** |
| 0.53 | 9.60 | 38.1% | 5/10 | **1.52** |
| 0.50 | 9.06 | 35.9% | 5/10 | **1.48** |
| 0.35 | 3.90 | 15.5% | 8/10 | **1.78** |
| 0.10 | 0.00 | **0.0%** | 10/10 | — |

**Elasticity is 1.29–1.80 everywhere in our operating range. Fill hazard scales exactly 1.0.
Therefore every continuous size throttle destroys reward faster than it destroys risk.** The
per-credit critical fraction `g_crit = 1.00/credit` is `0.14 0.27 0.40 0.44 0.45 0.53 0.58 0.75
0.88 0.99` — **median ≈ 0.45–0.53**: half our credits die at a ~50% size cut.

⚠ **Sample size and concentration (Protocol 14):** n=10 credits, one 2-day window (07-21→07-22).
The largest credit ($7.39) is **29.3%** of the total. Per canon §M8's screenshot attribution,
**8 of the 10 are TEMP** ($23.06 of $25.21 = 91.5%) — so this elasticity is 91% a temp
measurement. Gas is represented by exactly two rows, $1.01 and $1.14, **both within 14c of the
floor** — which if anything makes the cliff *more* binding for gas, not less. Not covered: any
period longer than gas-daily's 13.15h window; gas-weekly's 156.08h period had not closed.

**Consequences, both of which contradict currently-deployed behaviour:**
1. **Size is BANG-BANG: full `JOIN_SIZE` or zero.** There is no defensible intermediate.
2. **`MIN_QUOTE_CT` as a floor is a provably dominated state.** `MIN_QUOTE_CT=2` against
   `JOIN_SIZE=20` is λ=0.10 → **$0.00 across all ten observed credits**, while resting fillable
   size and consuming collateral. The `minjoin` path (`maker_kalshi_quoter.py:1190`) is exactly
   this state.
   ⚠ **Its own code comment states the refuted premise:** *"else the snapshot is excluded and even
   our resting exit quote earns $0"*. Per **R3** exclusion is **market-level, not participant-level**,
   and per §M2 our 20 ct was never marginal (0/304). The comment's premise is false; the §M12 A/B
   that "CONFIRMED" the mechanism measured **coverage**, never reward.

### 3.2 THE SELECTOR — ρ_c, and why it needs no fitted parameter

Every selector proposed so far keyed on **realized P&L by price band**. All four refutation lanes
independently destroyed that, and I confirm the two decisive objections:

- **It is arithmetically forced one-sided.** A binary's sides are `p` and `1−p`, so admitting
  [0.20,0.40) and [0.80,1.00) means their complements (0.60,0.80] and [0,0.20] are *refused by
  construction*. No price admits both sides; [0.40,0.60) admits neither.
- **It doesn't remove the contract, it flips us onto the expensive token.** LIP scores
  **contracts**; capital buys contracts at price `p`; so reward-contracts per dollar ∝ `1/p`.
  Measured at the deployed `_capped_join` shape (`maker_kalshi_quoter.py:366-368`, $15/market ⇒
  $7.50/side):

  | price | join ct | $ used | reward-ct per $ |
  |---|---|---|---|
  | 0.14 | 20 | 2.80 | **7.14** |
  | 0.86 | 8 | 6.88 | **1.16** |

  **6.2× worse reward per dollar** — on an $85 account.

**The replacement: select on `ρ_c`, the per-contract reward rate.** `$/day = period_reward /
(end_date − start_date)`, straight from `/incentive_programs?status=active` — an endpoint the
quoter already consumes. **No fill data, no P&L history, no in-sample fitting, no free parameter.**

**Live public read, 2026-07-23T17:22Z** (`kalshi_live/kalshi_rho_c_probe.py`, new file, read-only;
2,309 active programs; 87 open contracts sampled at 20/series):

```
KXAAAGASD   $188.235/contract/day     (canon R1: $182.51 — agrees; window 12.75h vs 13.15h)
KXAAAGASW   $ 15.376/contract/day     (canon R1: $15.38  — EXACT)
spread within the allowlist = 12x
7 of 22 programmed contracts carry 80% of the allowlist's total $/day

NO ACTIVE PROGRAM (rho_c = 0):  65 of 87 sampled quotable contracts = 74.7%
```

⚠ **Two unit/shape traps caught by smell-test in this very probe** — recorded so nobody repeats
them. A naive parse returned **$18,823/day** (impossible against canon's $182.51) because
`period_reward` is **fixed-point ×10,000**, not cents: `1000000 / 10000 = $100.00`, exactly canon
R1's measured pool. And a naive `orderbook→yes/no` read returned **100% both-sides-empty** (also
impossible) because the live payload is `orderbook_fp → yes_dollars/no_dollars` with 4-dp decimal
**strings**. Both are the R1/§M7f unit-trap family.

**The admission rule, and it is the only one in this document that needs no unpinned constant:**

```
EV = ρ_c · Λ − h · a        at ρ_c = 0:  EV = −h·a  <  0   for any a > 0
```

> **REFUSE any contract with `ρ_c = 0`. This is not a threshold with a tunable value — it is the
> observation that a contract with no reward program is pure inventory risk against zero revenue,
> and its sign is determined without knowing ρ, h, or a.**

This is not hypothetical. Canon §M4 records a **live held position**,
`KXAAAGASW-26JUL27-4.080`, with **no active program at all**. And the F1 deadlock's entire naked
$20.17 sat in **GASW — 12× the lower reward rate, venue rank 43** (canon §M4), while GASD at rank 3
was gated out alongside it.

⚠ **CRITICAL CAVEAT — do NOT read the temp column as "temp is dead."** The probe shows
`rho_c = 0` on 100% of sampled temp contracts, but canon §M7c already caught and corrected exactly
this error: **temp programs are ~1-hour and hourly-cycling, so any instantaneous census between
windows reads zero.** This is an argument *for* ρ_c as a control, not against temp: a static
`KALSHI_SERIES_ALLOW` cannot track an hourly-cycling program, whereas a per-cycle ρ_c read tracks
it automatically.

Beyond the ρ_c=0 refusal, **rank by ρ_c and take the top K**, with K≈6–7 from canon §M1's measured
optimum (oracle best K=6 at $99.43/day; as-is best K=7 at $84.83/day — both **upper bounds** per
§M7d, which measured the model over-predicting receipts 2–6×).

### 3.3 THE CONTROL LAW

```
──────────────────────────────────────────────────────────────────────────────
EVERY CYCLE, PER CONTRACT c:

  # ---- SELECT (sets the sign; no inventory input, no P&L input) -------------
  rho_c = period_reward(c) / period_days(c)          # live, /incentive_programs
  if rho_c == 0:            skip c entirely           # derived, not tuned
  if rank(rho_c) > K:       skip c entirely           # K ~ 6-7, canon SS-M1

  # ---- REDUCE  (computed FIRST, independently, never gated) -----------------
  if |naked_c| >= INV_TOLERANCE:
      ref = reference price on the REDUCING side
      if ref exists:
          price = ref + TICK * through_ticks(tau)     # THROUGH reference; see S2
          size  = min(|naked_c|, buying_power_room)   # bounded - see F1 note
          emit REDUCE(price, size)                    # no cost-basis input, ever
      else:
          # 48.3% of contracts, measured. NO DEFINED ANSWER YET -- see S5/E2.
          emit nothing, and RAISE a telemetry counter reduce_unpriceable{c}

  # ---- ACCUMULATE (sets the scale; BANG-BANG, never continuous) -------------
  if naked_event(E(c)) >= B_event:      accumulate_size = 0      # local, per event
  elif sum_naked_account >= B_account:  accumulate_size = 0      # global backstop
  else:                                 accumulate_size = JOIN_SIZE   # FULL, or nothing
  accumulate_price = reference                       # ALWAYS. never skewed.
  if accumulate_size > 0: emit ACCUMULATE(reference, JOIN_SIZE)
──────────────────────────────────────────────────────────────────────────────
```

**Note what is absent: there is no cent value anywhere in this law.** Answering the operator's
objection directly, here is the complete free-parameter census:

| parameter | status |
|---|---|
| ρ_c refusal threshold | **0. Derived** — sign of EV at ρ_c=0 is negative regardless of ρ, h, a |
| accumulating price offset | **0 ticks. Derived** from DF=0.50 bimodality (117/117, 189/189) |
| accumulating size | **`JOIN_SIZE` or 0. Derived** from R2's $1.00 floor + measured elasticity 1.29–1.80 |
| K (contracts held) | 6–7, **measured** (canon §M1), flat plateau — not sensitive |
| `B_event` | ⚠ **NOT DERIVED — must be measured (E3).** Carried over as the one open quantity |
| `B_account` | ⚠ **GUESS.** Must satisfy `Σ B_event ≤ B_account`; v1 deleted the global brake with no replacement and that was a defect |
| `through_ticks(τ)` ramp | ⚠ **GUESS.** See §5 — this is the weakest clause in the document |
| empty-reducing-side fallback | ⚠ **UNDEFINED. Honest gap.** See F1 |

**And there is no time-based ladder at all.** v1's `g_time` taper is deleted outright — which is
the specific thing the operator reported "broke on short-lived markets." Two independent reasons:
(i) R2 makes any taper reward-superlinear against us; (ii) the taper's premise is false —
v1 illustrated `g_time` on a "1h life", but the receipts measure temp lifetimes at **p50 122 min,
p90 145 min, max 147 min** (n=41 tickers, first-fill→last-close; ⚠ that is a *proxy* for market
life from our own fill record, not the listed life).

---

## §4. WHAT IT FIXES — F1 to F5

| | fix | quantified |
|---|---|---|
| **F1** deadlock | **PARTIAL — see below** | ~50% of the mechanism |
| **F2** settlement death | **NOT by this doctrine** — by the selector, and mostly by the config change | 99.97% via one env line |
| **F3** cost-basis cap | **FIXED, cleanly** | the strongest clause here |
| **F4** global brake on local problem | **FIXED** | 16 of 17 markets un-gated |
| **F5** −low/+high shape | **NOT FIXED** | 24 instances measured, unaddressed |

**F3 — FIXED.** `_unwind_price` (`maker_kalshi_quoter.py:391-399`) caps the reducing quote at
`1 − cost + MAX_UNWIND_LOSS`; when that cap falls outside the priceable band the function returns
**no quote at all** (`:458`, `:519`) — the `at_ref_pct = 0.0` fingerprint in the freeze telemetry.
Deleting the cost-basis input is backed by the entire academic canon (§1a) and by all four
refutation lanes, which listed it under "what survives" unanimously. Expected benefit from the
tape: on the 20 doomed lots, **18/20 had a fillable price** at one tick through, median wait
**4.2 min**, median crystallised loss **4.0c — i.e. twice the $0.02 cap**. The cap made a
reachable exit unreachable in 18 of 20 cases.

**F4 — FIXED.** `HELD_MAX_USD` (`:214`, tripped at `:936`, applied `:1163-1195`) is a book-wide
level trigger. All the naked risk was in one weekly-gas event; 16 daily-gas markets with zero
inventory were gated out. A per-event budget confines the brake to the event that earned it.
⚠ **With the correction v1 missed:** v1 said *"there is no global reduce-only mode"* and left
nothing bounding the account. `B_account` is restored above as a backstop.

**F1 — ONLY PARTIALLY FIXED, and this is the doctrine's honest limit.** The deadlock had three
links. Two are severed: the global brake (F4) and the cost-basis cap (F3). **The third is not.**

`reduce_price = reference_reducing_side + TICK·through_ticks(τ)` presumes a reference **exists** on
the reducing side. It frequently does not:

| probe | n | at least one side completely empty |
|---|---|---|
| refutation lane, 17:11Z | 88 | 51.1% |
| refutation lane, 17:14Z | 88 | 48.9% |
| refutation lane, independent | 67 | 50.7% |
| **mine, 17:22Z** (`kalshi_rho_c_probe.py`) | **87** | **48.3%** |

**Four independent reads, all ~50%.** With an empty reducing book there is no reference, the
formula is undefined, and `_priceable` (`:446-450`) already returns `[]` — reproducing the exact
`at_ref_pct = 0.0` fingerprint that F3 was blamed for. **F1 returns by a second route that this
doctrine does not close.** I do not have a validated fallback, and I decline to invent one: any
answer here is a guess, and the honest move is to instrument it (E2) rather than ship a number.

Also unfixed within F1: the termination proof. v1 claimed naked inventory is *"a supermartingale
absorbed at 0"* with `P(fill) → 1`. Its own cited data contradicts it — **5/109 temp lots never saw
a first opposite taker, and 9/114 (8%) never fully exited at any price**. 8% non-absorbing mass is
not absorption. And on books with the modal 1-tick spread (25.6–35.6% of two-sided contracts),
"one tick through" **crosses** — making us the taker, which §4 of the tape study measured as
loss-making at every horizon (N=5 −$0.26 … N=10 −$14.76), and canon §M13 shows every taker trade in
the whole export lost money (69 taker-touched lots, −$39.11 of −$79.99).

**F2 — fixed, but NOT by this doctrine, and the credit belongs elsewhere.** My verification (V2):
the 07-22 massacre is **20 rows, 210.05 contracts, −$40.62** — of which **TEMP = −$40.61 and GAS =
−$0.01**.

> **Dropping `KXTEMP*` from `KALSHI_SERIES_ALLOW` — a Tier-2 env change with a one-line rollback,
> already standing as canon §M8's proposal — avoids $40.61 of $40.62 = 99.97% of the massacre,
> with zero new code.** The elaborate price-band selector avoided $39.78 (97.9%) — **$0.83 worse**.

Any doctrine must be scored against that null. This one does not clearly beat it.

**F5 — NOT FIXED, and I want this on the record because v1 listed it as a required fix and never
returned to it.** Nothing in ADMIT, the budget, or REDUCE reads cross-strike shape.
`ladder_pairing` (`:1631`) deliberately never *matches* long-high-strike + short-low-strike, so the
budget sees the size but no rule refuses the **shape**. Measured on the fill record: **24 unfloored
−low/+high combinations across 8 events** (⚠ proxy — net signed position per ticker over the
export, which may overstate simultaneity). There is also a live specification ambiguity that spans
"handled" and "unguarded": `naked_event_usd` is undefined as between the quoter's **signed**
`event_deltas` and the **absolute** `naked_held_cost` (`:1540`). Under the signed reading, short
4.090 + long 4.105 nets to ~0 → budget sees **zero risk** → full accumulation into precisely the
shape that killed us on 07-23. **This must be specified before anything ships.**

---

## §5. WHAT IT DOES NOT FIX — and why this is the section that matters

**1. It does not fix the losses, because the exit is second-order.** §0's table is the whole
argument: temp traded-out fills lose **8.90 c/ct** against settled fills' **9.69 c/ct** — 92%. The
loss is priced in at the fill. Independently corroborated by the tape lane's counterfactuals on the
same 20 lots: exiting at the first ≤2c-loss moment gives −$14.11 vs actual −$40.62, but **8 of the
20 lots were born unexitable** — the best price ever available after our fill was already more than
2c below basis, sometimes within 1 minute (CHIH-T70.99 filled 0.65, best subsequent bid 0.55 at
T+1m).

**2. The break-even parameter `a*` is not pinned, by 23×.** Every admission rule of the form
`admit iff a < a* = ρ/h` is undetermined on existing data (V6):

| ρ route | ρ ($/committed $-h) | **a\*** |
|---|---|---|
| matched window (credits 07-21..22 / ~704 committed $-h) | 0.0358 | **3.41 c/ct** |
| screenshot ~$42 / 689.8 committed $-h | 0.0609 | **5.80 c/ct** |
| screenshot ~$42 / 51.2 **at-reference** $-h | 0.8203 | **78.20 c/ct** |

The numerator is a **self-declared partial-scroll LOWER BOUND**; the denominators differ by 13.5×
depending on whether ρ is read per *committed* or per *at-reference* dollar-hour — and v1's own
table labelled it one way and computed it the other. `kalshi_doctrine_params.py`'s own docstring
says ρ *"is NOT receipt-measurable from local data"*. **At a\* = 78 c/ct every cell is admitted,
including the two carrying 84% of temp's loss. The doctrine deliberately contains no rule that
consumes a\*.**

**3. ρ is not a scalar and the refuters are right that it should be per-cell.** Receipts put TEMP
credits at $23.06 vs GAS at $2.15 on *more* gas capital-time. My live probe measures a **12× spread
in ρ_c within the allowlist alone** and 74.7% of sampled contracts at ρ_c = 0. Any single
book-wide ρ is a fiction.

**4. Reward staleness, not the throttle, is where the reward actually went.** Measured at-ref/committed
from plans telemetry: 07-22 **9.9% in NORMAL cycles**; 07-23 **22.4%**. Even with nothing throttling,
**78–90% of resting dollars are already OFF reference.** `THROTTLE_STEP_TICKS 1→0` is worth one
tick past `INV_SOFT_CT`; the remaining ~80pp is quote staleness against reference drift on a
2-minute cycle with `WRITE_BUDGET=60`. **Under DF=0.50 each tick of staleness halves credit.** This
doctrine does not address it, and $85 with a 2-minute timer cannot buy out of it.

**5. F5 and the empty-reducing-side case** — see §4.

**6. Everything here is in-sample.** n=244 lots over 3 days spanning a deposit, a cap change
(65→85) and a naked-risk fix. There is no holdout. Event-level concentration is severe: GAS is
**−$5.28 over 4 events**, and dropping the single worst event (`KXAAAGASD-26JUL21`) moves it to
**−$0.01** (V5). "GAS is profitable" rests on 4 risk draws.

**Refuter verdicts, reported in full even where they contradict the design lane:**

| lane | verdict | core defect |
|---|---|---|
| reward-economics | **REFUTED / BLOCKER** | selector forces temp permanently one-sided (both sides admitted in **0.2%** of 2,080 quoted-minutes, n=41 tickers); credits $25.21 → ~$10.38 after R4 halving + R2 floor |
| deadlock-and-edge-cases | **REFUTED / BLOCKER** | invariance theorem false at the R2 boundary; reduce path fails ~49% of the time on empty books |
| small-account-realism | **REFUTED / BLOCKER** | regulator is EV-destroying (elasticity >1); selector dominated by a one-line Tier-2 change |
| does-it-fix-the-actual-losses | **REFUTED / BLOCKER** | the saving is the fitting residual; both temp ADMIT cells flip to REFUSE on drop-**best** leave-one-out |

**I accept all four.** The bang-bang sizing, the ρ_c selector, the restored `B_account`, the
retirement of `g_time`, and the F1/F5 admissions in §4 are direct consequences of those refutations,
not defences against them. **No lane returned nothing** — all four returned substantive BLOCKER
findings, and the academic lane additionally flagged that Kyle (1985) and Ho & Stoll (1981) could
not be read in original form (§1a).

---

## §6. MIGRATION — knob by knob

**Nothing below is authorised. The 02:19Z freeze holds. Sequenced cheapest-and-most-reversible
first; each row is independently rollback-able.**

| # | knob | today | proposed | tier | rollback |
|---|---|---|---|---|---|
| **1** | `KALSHI_SERIES_ALLOW` | incl. `KXTEMP*` | **drop `KXTEMP*`** | 2 | restore string, restart |
| **2** | `KALSHI_MAX_UNWIND_LOSS` | **0.02** (code default 0.10, `:200`) | **remove the cap** (F3) | 3 | revert commit |
| **3** | `KALSHI_HELD_MAX_USD` | **50** (code default 20, `:214`) | per-event `B_event` + `B_account` | 3 | revert commit |
| **4** | `KALSHI_THROTTLE_STEP_TICKS` | **1** | **0** | 2 | set back to 1 |
| **5** | `KALSHI_MIN_QUOTE_CT` / `minjoin` | 2, floored (`:1190`) | **0** — bang-bang | 3 | revert commit |
| **6** | `KALSHI_INV_SOFT_CT` / `INV_HARD_CT` | **15 / 60** | retire SOFT; HARD → `B_event` | 3 | revert commit |
| **7** | `KALSHI_LATE_LIFE_FRAC` | **0.6** (`:192`) | **remove for gas** | 2 | set back to 0.6 |
| **8** | ρ_c selector | none | new gate | 3 | feature-flag off |

**Sequencing rationale.** #1 alone captures the largest measured benefit (99.97% of F2) at Tier 2.
#2 and #4 are the two changes with unanimous refuter support. **#5 is the one row that contradicts
a live, A/B-"confirmed" mechanism** — it must not ship before E1 (§7). #7: canon-measured gas
adverse selection *by life fraction* is **3.92 / 0.72 / −0.57 / −0.29 c/ct** (first→last quarter) —
**gas is worst EARLY and profitable LATE**, so a late-life entry gate blocks the good part. ⚠ n and
event-concentration for that split are not established; treat as directional.

⚠ **Rows 2, 3, 5, 6 touch `maker_kalshi_quoter.py`, which another workflow may be editing.** Per
this session's constraints I created no edits; these are specifications, not patches. Re-check
`md5 727ca7c59840a42b51c19e24c65a0982` (= branch HEAD blob at freeze) before any of them.

---

## §7. THE EXPERIMENT — before any deploy

**E1 — THE R2 CLIFF. Decisive; run first; nothing else matters if this is wrong.**
Everything in §3.1 rests on payout being a **hard zero** below $1.00 rather than a linear taper.
Sandbox version, no money, no code change: replay the frozen paired-snapshot corpus
(`kalshi_live/concentration_samples.jsonl`, md5 `e920bf99850279099897a79e8ad78dec`) through the real
CFTC scoring core in `scripts/maker_kalshi_recorder.py` (`qualifying_walk` / `side_share`), and
compute the **whole-Time-Period** payout — mean share over snapshots × pool, **then** the $1.00
floor — for arms λ ∈ {1.0, 0.79, 0.50, 0.10}. Report per-contract payout, count zeroed, and
elasticity `d(log payout)/d(log λ)`, **split by family** (gas 13.15h and temp ~1h sit at very
different distances from the floor).
⚠ The sampler must **not** pre-filter empty-sided books — that is canon §M6b's known selection bias.
**PREDICTION: elasticity > 1, non-trivial zeroed count at λ ≤ 0.79.** If elasticity ≤ 1 and nothing
zeroes, §3.1 is wrong and continuous regulation is back on the table.
**Live confirmation, near-free:** halve `JOIN` on *two* named contracts (one gas paying ~$1.10–1.40,
one temp paying ~$2.00–2.50) for ~6 Time Periods and read the credits out of the export. Linear
model predicts ~$0.55–0.70 and ~$1.00–1.25; R2-floor model predicts **$0.00** and ~$1.00–1.25. The
outcome is a zero/non-zero indicator, so n≈6 per arm suffices.

**E2 — THE EMPTY REDUCING SIDE (closes the F1 gap this doctrine leaves open).**
Run `kalshi_live/kalshi_reduce_feasibility_probe.py` on a 5-minute cadence for 24h and report, per
hour-of-day: (a) fraction of allowlist contracts with a completely empty reducing side, (b) the
spread-tick distribution, (c) fraction where `spread ≤ through_ticks(τ)` — i.e. where the reduce
quote **crosses** and becomes the taker that §M13 shows always lost money. Four point-reads already
agree at ~48–51%; a 24h series says whether any hour supports the termination claim.
**The conditional rate is the one that matters and is unmeasured:** we hold what flow is dumping,
so `P(empty | we are long)` is expected to be *worse* than the 48.3% unconditional.

**E3 — `B_event`, and the `naked_event_usd` specification.** Not a study first, a **specification**:
state whether `naked_event_usd` is the signed `event_deltas` or the absolute `naked_held_cost`
(`:1540`), then replay both against the 07-23 GASD book that settled **between** our 4.090 and 4.105
strikes. If the signed reading returns "zero risk" on that state, the budget is blind to F5 and
`B_event` cannot be sized until it is fixed.

**E4 — OUT-OF-SAMPLE, and it is unavoidable.** Re-export the Kalshi transaction CSV **after
2026-07-27T04:00Z** — canon §M13's own stated settling condition, the first moment every Time Period
covering 07-21→07-23 quoting has closed and been credited, gas-weekly's 156.08h period included.
This simultaneously (a) pins ρ over a window where numerator and denominator cover the same
quoting, resolving the 23× ambiguity in §5, and (b) provides the first genuine holdout. Report per
cell with **n, distinct-event count, top-event share, and BOTH leave-one-out directions**
(drop-worst *and* drop-best — v1 reported only the flattering direction).

**E5 — THE DOMINANCE TEST, which retires or justifies this whole document.**
Run migration row #1 (gas-only) for a clean 7-day window and measure net = trading P&L + credits
from the export alone. **That is the null hypothesis every clause above must beat.** One env line,
one restart, no new code. If gas-only is net positive, only F3 (row #2) and F4 (row #3) are worth
building. If gas-only is net **negative**, then per §0 no inventory doctrine can fix it, and the
lane's question changes to *"is there any series at this account size where ρ_c exceeds h·a"* —
which E4 answers directly.

---

## §8. FILES CREATED THIS SESSION (all new; zero edits to existing modules)

| file | what |
|---|---|
| `kalshi_live/kalshi_doctrine_final_verify.py` | independent re-verification of every receipt-grade number here (V0–V6) |
| `kalshi_live/kalshi_rho_c_probe.py` | live public read: ρ_c per contract + book shape + the two unit traps |
| `kalshi_live/rho_c_probe.json` | its output, 2026-07-23T17:22Z |
| `docs/maker_handoffs/KALSHI_INVENTORY_DOCTRINE_2026-07-23.md` | this file |

**Reproduce:** `python kalshi_live/kalshi_doctrine_final_verify.py` (offline, receipts only) and
`python kalshi_live/kalshi_rho_c_probe.py` (public API, ~90s, no keys).

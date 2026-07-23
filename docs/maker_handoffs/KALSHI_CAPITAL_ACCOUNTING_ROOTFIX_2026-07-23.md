# KALSHI CAPITAL-ACCOUNTING ROOT-FIX — committed-capital guard

**Date:** 2026-07-23 · **Venue:** Kalshi live production maker account (`external-api.kalshi.com`), real money · **Author lane:** Kalshi maker, capital-accounting dive
**Target:** `maker_kalshi_quoter.py` guard at `:1251–1287` (deployed md5 `727ca7c5…` git-blob / `39386d7d…` CRLF worktree — byte-identical, line-ending only). **This file is a DESIGN SPEC. No module was edited; the quoter is imported read-only.**
**Method note:** All numbers below are from GET-only reads (public + authed read-only). No orders, no writes, no `systemctl`, no `live.env` writes. Every number carries its sample size. This is a **CODE change** and requires **full ship discipline (pytest + adversarial review + a live place→fill measurement) BEFORE deploy** — see §6/§8.

---

## ⚠ VERDICT UP FRONT (read this before the spec)

The **diagnosis is sound and the fix DIRECTION is correct**, but the fix **as previously drafted is NOT ship-ready**. Four independent adversarial refuters converged on the same load-bearing defect, and I **confirmed it in code and against a live balance read**:

- **BLOCKER — the "reconciles to the cent ($89.19)" validation is CIRCULAR and must not be trusted.** `account_value` is **never read from Kalshi**. The raw `/portfolio/balance` payload exposes only `{balance, balance_breakdown, balance_dollars, portfolio_value, updated_ts}` — **no total-equity field, no reserved/available split** (dumped live this session, §2). Every reconcile script *defines* `account_value = free_cash + true_reserve + pv` (`kalshi_capital_gap_atomic.py:61`, `kalshi_true_reservation.py:68`) and then "verifies" `account_value − free_cash == true_reserve + pv` — which is `x == x`. It cannot fail, so it validates nothing. The §6 "verify before trusting" step as written is a tautology.
- **Three under-reservation defects survive** (all fixable, all fixed in this spec): a coverage-double-count in the drafted `naked_buy_draw`, the danger of demoting `MAX_TOTAL_CAPITAL` to a loose $10k fuse given `ladder_pairing`'s documented silent-dark failure, and a fill-time collateral-release assumption measured only at **n=1**.

**BUT the refuters split on whether the fix is actually WRONG, and here the evidence favors the designer:** two refuters argued "balance is NET of reserve → the gate double-counts → treadmill returns," citing the comment at `kalshi_capital_gap_atomic.py:11`. That comment's proof is invalid, and the repo's own **measured, append-only correction** says the opposite — `KALSHI_RUNNING_TAB.md` (07-20): *"Resting orders do NOT visibly deduct from balance_dollars."* Under that (GROSS) model — which is a measurement, not an assertion — the gate does **not** double-count and **does** end the treadmill. The designer picked the right model; the designer cited the wrong (circular) proof for it.

**Net:** ship the fix's *direction*, but (1) replace the circular check with a real one, (2) fix the coverage double-count, (3) keep a real gross backstop, (4) run a controlled live place→fill before trusting. Details below.

---

## 1. THE BUG IN ONE SENTENCE + THE GAP IN ONE NUMBER

**One sentence:** `MAX_TOTAL_CAPITAL` gates new accumulating buys on `committed = Σ(standing price×count) + gross_held_cost` (`:1254–1259`) — a **static ceiling** that adds together **already-spent held cost** (money that already left `balance` at fill and can never draw free cash again) and **gross resting notional** (which the venue reserves **$0** for until fill), so held inventory grows into any ceiling, the operator raises it, and inventory re-eats it. **Treadmill.**

**The gap in one number (atomic snapshot, ~18:5xZ, n=1 instant, 9 positions / 3 events / ~23 resting orders):**

| quantity | value |
|---|---|
| guard `committed` (`:1254`+`:1259`) | **$250.07** — pinned at the $250 cap |
| free cash (`balance_dollars`) sitting idle | **$139.67** |
| worst-case cash a new-order burst could actually draw (`funding_draw`) | **$89.19** |
| **fundable quoting capacity stranded right now** = free_cash − funding_draw | **≈ $50.48** |

At that instant the guard placed **$0** of new quotes (pinned $250.07 ≥ $250) while the account could safely fund **~$50 more** of reward-earning quotes. Earlier in the day the same pin stranded even more (18:07Z: cash **$156**, committed $149.32/$150). Four cap raises today (**85 → 100 → 150 → 250**) each bought headroom that fresh inventory re-consumed within cycles. **The gross count strands real cash; that is the defect.**

*(A second live read this session, ~read-time `updated_ts` 1784834554: `balance_dollars` **$156.97**, `portfolio_value` mark **$80.06** — the book churns each cycle, so treat the snapshot numbers as ±few dollars, not static.)*

---

## 2. WHAT KALSHI ACTUALLY RESERVES (empirically-derived rule + sample sizes)

Kalshi is fully collateralised. Four rules, each with its evidence and its sample size. **Where a number could not be independently determined, it is flagged.**

**RULE 1 — A resting order reserves NOTHING observable in `balance`; cash moves ONLY at fill.**
Every isolated place/cancel left `balance_dollars` unchanged; the only mover was a fill, by exact contract cost. Evidence: `KALSHI_RUNNING_TAB.md` 07-20 append-only correction ("resting orders do NOT visibly deduct from balance_dollars"); `kalshi_attribution_ledger.py` place/cancel intervals with balance Δ0; VENUE-phase place/cancels p3/p4/p28 (3 clean Δ0). A fill debits exactly contract cost (p13: buy NO ×20 @$0.16 → balance −$3.20 = 20×$0.16).
**Sample:** ≈4 place/cancel events (3 clean Δ0) + several fills. **Placement half is solid; balance is GROSS w.r.t. resting.**

**RULE 2 — Netting is at the MARKET (ticker) level, with collateral RELEASE at fill.**
Opposing YES/NO in one market net to one signed `position_fp`; a completed offsetting pair returns $1/pair **at the offsetting fill**, not at settlement. Evidence: VENUE-phase Capture-1 p36 (covered buy 2 YES @$0.38 while net short → cost $0.76 but balance **rose +$1.24** = $2.00 pair-release − $0.76); settlements pay `max(net,0)×$1`, never gross.
**Sample:** **fill-time release = n=1 passive observation** (not a controlled experiment) + settlement rows. **This is the thinnest link and the one the reducing-leg exemption leans on — flag it (§4, §6).**

**RULE 3 — NO netting at the EVENT level. The venue locks the arithmetic SUM of per-market max-loss.**
The venue's own `event_exposure_dollars` equals the plain sum of that event's per-market `market_exposure_dollars`, with zero cross-strike credit: KXAAAGASD $45.36 = Σ 5 markets; KXAAAGASW $55.67 = Σ 3 markets; KXAMSAVO $5.00 = $5.00.
**Sample:** n=3 events. **Solid.** The old "~4.3× ladder netting" folklore stays **RETRACTED** — the ~3–4× ratio was cumulative maker churn (`total_cost_shares_fp` 347 vs current net ~83 ct), not margin. **A floored +low-YES / −high-NO ladder still pays FULL gross per-market collateral.**

**RULE 4 — `balance` = free cash; held cost already removed at fill; NO hidden reserve field.**
Raw `/portfolio/balance` dumped live this session:
```json
{ "balance": 15697, "balance_breakdown": [ {"balance":"156.9738","exchange_index":0} ],
  "balance_dollars": "156.9738", "portfolio_value": 8006, "updated_ts": 1784834554 }
```
`balance_breakdown` is a **per-exchange cash bucket equal to the full balance** — **not** a reserved/available split. `portfolio_value` ($80.06) < `balance` ($156.97) → `portfolio_value` is the **held mark, excluding cash** (this also partially resolves the open question flagged in the code at `:854–860`). **There is no total-equity field and no reserved field.**
**Sample:** n=1 raw dump. **Consequence: `account_value` must be reconstructed, so any "reconcile account_value − free_cash" check is circular (the BLOCKER).**

### What is UNKNOWN / not covered
- **Gross-vs-net of `balance` cannot be read from the payload** (no reserved field). It is inferred GROSS from Rule 1's *placement* measurement. The **fill-time** direction (does a covered/reducing fill release collateral, Rule 2) is **n=1**. A controlled place→fill experiment (§6) is required before the reducing-leg exemption is *trusted*, not just *believed*.
- Single-instant snapshot; no intraday/time-of-day variance, no larger books, no settlement-day dynamics.
- `portfolio_value` cash-inclusion is inferred from one read where pv < balance; on a book where pv could exceed balance this inference should be re-checked.

---

## 3. THE FIX

Two constraints the old cap conflated must become **two separate limits**:

- **FUNDING gate (replaces the binding cap):** worst-case cash that can leave `balance` if every resting + new accumulating buy fills ≤ **free cash**. Self-adjusting — no knob to raise. This kills the treadmill.
- **RISK gates (unchanged):** `naked_held_cost ≤ HELD_MAX_USD` breaker + per-market `MAX_MARKET_CAPITAL`, plus an **optional NEW per-EVENT gross cap** for concentration (§4/§5).

### 3.1 The corrected `funding_draw` — with the coverage-double-count FIXED

The previous draft computed `red = min(order.count, |held|)` **per order**, reusing the *full* `|held|` for every order on a ticker. Two same-reducing-side orders summing above `|held|` would both be exempted to $0 while the venue covers `|held|` only **once** — a real under-reservation. The venue model the reconcile script itself derived (`kalshi_capital_gap_atomic.py:49–56`) consumes coverage **greedily**. The fix must match it:

```python
def _reduces(side, held):
    # a BUY 'no' reduces a long (held>0); a BUY 'yes' reduces a short (held<0)
    # SAME predicate the breaker uses (_keep_reducing) — battle-tested polarity.
    return (held > 0 and side == "no") or (held < 0 and side == "yes")

def naked_buy_draw(standing, held_by, cancelled_ok):
    """Worst-case cash that leaves `balance` if EVERY not-cancelled resting BUY fills and
    NO reducing leg fills. A reducing leg offsets held inventory (up to |held|) and RETURNS
    cash on fill -> $0 draw. Coverage is a FINITE per-ticker pool (|held|), consumed greedily
    across orders and NEVER re-granted to each order (that re-grant was the under-reservation
    bug the refuters found). Held cost is EXCLUDED entirely (already out of `balance`)."""
    cover = {t: abs(n) for t, n in (held_by or {}).items()}   # offsetting coverage pool / ticker
    draw = 0.0
    for t, ol in standing.items():
        n = held_by.get(t, 0.0)
        for o in sorted(ol, key=lambda x: x["order_id"]):     # deterministic; sum is order-invariant
            if o["order_id"] in cancelled_ok:
                continue
            cnt = o["count"]
            if _reduces(o["side"], n) and cover.get(t, 0.0) > 0:
                cov = min(cnt, cover[t])
                cover[t] -= cov                                # CONSUME shared pool (fixes double-count)
                cnt -= cov
            draw += o["price_dollars"] * cnt
    return draw
```

**Free cash is the ceiling** (Rule 1; reuse the balance read already at `:863`, fail CLOSED to 0 if unreadable):

```python
free_cash = float(client.get_balance().get("balance_dollars") or 0)   # already fetched at :863
```

**Gate (replaces `:1259`, `:1274`, `:1287`):**

```python
# was: committed = Σ standing price*count ; committed += held_cost   (:1254-1259)
funding_draw = naked_buy_draw(standing, held_by, cancelled_ok)        # held_cost NOT added
...
for i, c in enumerate(creates):
    cost = c["price_dollars"] * c["count"]
    reducing = c.get("reason") == "unwind"
    ...                                            # failed-cancel handling unchanged (:1267-1273)
    if not reducing and funding_draw + cost > free_cash:   # was: committed + cost > MAX_TOTAL_CAPITAL
        create_skipped += 1
        continue
    ...create + record...
    if not reducing:
        funding_draw += cost                       # reducing creates return cash -> excluded (safe)
```

**Why this is worst-case-correct AND safe:** the maximum cash that can leave `balance` = all accumulating buys fill + no reducing leg fills = `funding_draw`. Reducing legs filling can only *add* cash (Rule 2). So `funding_draw ≤ free_cash` is the exact fundability bound, tight on the safe side. **Held cost is excluded because it already left `balance`; that exclusion is what kills the treadmill.**

> **Under the confirmed GROSS model (Rule 1):** `free_cash = balance_dollars` still holds the money that resting fills will consume, so `funding_draw + cost ≤ free_cash` does **not** double-count. **If** the venue were secretly NET-of-reserve (the refuters' worry, contradicted by Rule 1's measurement), the same gate would over-reserve by ≈`funding_draw` and re-pin — the SAFE direction (idle cash), never an overdraw. Belt-and-suspenders below makes even that non-catastrophic.

### 3.2 Kill the $1/contract fallback phantom (`_held_cost:1581`)

`total += me if me else abs(n)` books held at **max-loss** ($1/ct) whenever `market_exposure_dollars` is momentarily absent. On an established position a present→absent→present blip is a phantom **+$X then −$X**, and the −$X trips the daily-loss `down` meter (`:901`) → **false halt**. Split the fix by consumer:

- **RISK path — `naked_held_cost:1557` keeps `|naked|×$1`.** Over-stating risk trips reduce-only (the safe direction). **No change.**
- **EQUITY/loss-meter path — `_held_cost` total (feeds `:863`) carries forward last-known per-ticker exposure:**
```python
me = float(p.get("market_exposure_dollars") or 0)
if not me:  me = last_known_exposure.get(ticker)         # cures present->absent->present
if me:      total += me; costs[ticker] = me/abs(n); last_known_exposure[ticker] = me
else:       total += abs(n); unknown_cost_tickers.append(ticker)   # never-seen -> conservative
```
  If `unknown_cost_tickers` is non-empty (a *never-seen* position), **disarm the daily-loss halt this cycle** (same pattern as the balance-read disarm at `:868`) rather than fire it on a fabricated equity number.
- **FUNDING path uses no `held_cost`** (dropped in §3.1), so the phantom **cannot reach the funding gate** — that immunity is part of the fix.

**Note:** on the live book this fallback contributed **$0** — every position reported `market_exposure_dollars` (verified: `_held_cost` total = Σ venue `event_exposure_dollars` exactly). This is a **latent-rearm** fix, not an active over-count today.

### 3.3 Per-EVENT rather than global? — YES, as the durable concentration cap

Global gross notional is the wrong axis (it's what inventory eats into). The durable limit is **per-event RISK**. Because the venue charges **gross per-market with no event netting (Rule 3)**, a figure meant to mirror venue-locked collateral must use per-market **`market_exposure`** (= `held_cost`), **never `naked_held_cost`**. Reuse the existing `event_deltas` / `_event_key` / `_is_ladder_event` plumbing:

```python
KALSHI_MAX_EVENT_RISK_USD = _envf("KALSHI_MAX_EVENT_RISK_USD", 20.0)   # GUESS start (see §Flags)
# per event: sum this event's per-market GROSS market_exposure_dollars; block NEW accumulating
# creates whose event would exceed the cap. Uses GROSS (Rule 3), NOT naked -> a silent ladder
# pairing miss cannot make it under-reserve.
```

---

## 4. DUAL-DIRECTION SAFETY — lead with the under-reservation risk

**The under-reservation risks the refuters found, and how this spec guards each:**

1. **Circular validation (BLOCKER).** The reconciliation cannot detect a wrong model. **Guard:** §6 replaces it with a real place→observe/fill measurement; until that passes, the funding gate is **belt-and-suspendered by the venue itself** (Kalshi is fully collateralised → it *rejects* any order it can't fund; the bot can be wrong and still not overdraw, only get `create_fail`).
2. **Coverage double-count in `naked_buy_draw` (HIGH).** Two same-reducing-side orders summing > `|held|` were both exempted to $0. **Guard:** greedy coverage consumption (§3.1) — matches the venue model; pin test #3.
3. **Backstop removal (HIGH).** Demoting `MAX_TOTAL_CAPITAL` to $10k leaves unhedged accumulation bounded only by `naked_held_cost ≤ $20` + free-cash exhaustion, and `naked_held_cost` depends on `ladder_pairing`/`_strike_of`, which **silently goes dark** on a ticker-shape it can't parse (documented at `_strike_of:1599–1603`: `KXAAAGASM-…-US-4.00` read 100% of inventory as unpairable, no error). **Guard: DO NOT demote to $10k.** Keep `MAX_TOTAL_CAPITAL` as a **real** gross backstop (≈1.3–1.5× account) *and* add the per-event gross cap (§3.3). Surface `strike_parse_failed` in the plan row (already emitted) so a dark pass is visible.
4. **Fill-time collateral release is n=1 (MEDIUM).** The reducing-leg $0-draw exemption assumes Rule 2 releases collateral at fill. **Guard:** the exemption only ever *under*-counts if release does NOT happen at fill — and Kalshi's own collateral check backstops that (rejection, not overdraw). Still: measure it (§6) before trusting; conservative fallback = count reducing creates gross too (over-reserve, safe).

**What it UNBLOCKS (the idle cash — the point):** at the live snapshot, `funding_draw` $89.19 < free_cash $139.67 → ~$50 of new accumulating quotes the old gross+held cap ($250.07 ≥ $250) refused are now placeable. Over-reservation gone.

**What it must STILL BLOCK (the under-commit hole the old cap opened):** once raised high, the old static cap never looked at free cash — it would admit accumulating buys even with free cash nearly exhausted, risking a fill-burst the account can't fund. The new gate refuses any accumulating buy whose worst-case fill pushes `funding_draw` past free cash. **So the fix closes an under-reservation hole that opened every time the operator raised the cap, while fixing the over-reservation. Both directions improve; neither is traded for the other.**

---

## 5. WOULD IT HAVE ENDED THE TREADMILL?

**On the funding axis: YES.** The four raises (85 → 100 → 150 → 250) were all chasing a ceiling that **held cost re-ate**, because held was counted against a static number. The fix removes held from the ceiling and makes the binding gate **free cash**, which the operator neither can nor needs to raise — it tracks the account automatically. There is no knob to raise, so the treadmill has nothing to run on.

**But the refuters are right that *net exposure* can still grow — just not into a raisable knob.** Held inventory can still accumulate until **free cash** is exhausted (at which point the bot correctly stops accumulating). And **per-event concentration is bounded by neither the old cap nor free cash.** So:

> **The durable answer to "inventory grows into the limit" is the per-EVENT gross cap (§3.3), not a global number.** The free-cash funding gate ends the *treadmill* (the raise-and-re-pin cycle); the per-event cap ends the *concentration* growth the global cap never really controlled. Ship both.

---

## 6. MIGRATION + ROLLBACK + THE RECONCILIATION THAT MUST PASS (a REAL one, not the circular one)

**Migration (env-only, `live.env`):**

| Var | From | To |
|---|---|---|
| `KALSHI_FUNDING_GATE` *(NEW flag, default `1`)* | — | `1` (set `0` to instantly revert to the old gross gate, no deploy) |
| `KALSHI_MAX_TOTAL_CAPITAL` | `250` (binding, treadmill) | **real backstop ≈ 1.3–1.5× account** (e.g. `220`) — **NOT** the loose $10k the draft proposed |
| `KALSHI_MAX_EVENT_RISK_USD` *(NEW, optional)* | — | `20` **(GUESS)** if the per-event cap is adopted |
| `KALSHI_HELD_MAX_USD` / `KALSHI_MAX_MARKET_CAPITAL` / `KALSHI_DAILY_LOSS_HALT_USD` | unchanged | unchanged |

**Rollback:** `KALSHI_FUNDING_GATE=0` reverts to the exact old `committed`/`MAX_TOTAL_CAPITAL` gate next cycle (no deploy); plus `git revert <sha>` for the code.

**The reconciliation that MUST pass before the fix is trusted — and the circular one it REPLACES:**

- ❌ **DO NOT trust:** `funding_draw == account_value − free_cash − pv`. `account_value` is reconstructed (`= free_cash + reserve + pv`), so this is `x == x` and always passes. It validated nothing. Delete it as a gate.
- ✅ **DO run (READ-ONLY, no fill needed):** `delta = (account_value_reconstructed) − balance_dollars − pv`. This still isn't independent, so treat it as **descriptive only**.
- ✅ **DO run (the real test — one controlled place→observe, READ-ONLY on balance):** read `balance_dollars`; place ONE resting accumulating BUY of known cost; re-read `balance_dollars`. **Δ0 ⇒ GROSS (Rule 1 holds, gate correct). Δ = −cost ⇒ NET-of-reserve (gate would double-count; switch to `cost ≤ free_cash`).** The placement half already reads GROSS across ≈4 events — re-confirm once live under the fix.
- ✅ **DO run (the load-bearing gap — requires a FILL, operator-run write experiment):** hold a small long-YES position; place a covered NO-side reducing order; let it FILL; record the `balance_dollars` delta **at fill**. **Up by ≈(1−p)×count ⇒ Rule 2 release confirmed, reducing-leg $0-draw exemption is safe. Down by p×count ⇒ exemption unsafe; fall back to counting reducing legs gross.** *(This is a WRITE and outside this read-only lane — the operator must run it; today it is n=1 passive.)*
- ✅ **DO run (phantom-halt regression):** synthetically drop `market_exposure_dollars` for one ticker across a cycle; assert the daily-loss `down` meter shows no jump (carry-forward holds it flat).

---

## 7. INTERIM — least-bad config until the code ships

Today's answer was **"raise the cap."** After the last raise, **`MAX_TOTAL_CAPITAL=250` sits ABOVE account value** (live: cash $156.97 + held mark $80.06 ≈ $237 equity; earlier ~$247). **A cap above account value is INERT** — `committed` can rarely reach it — so right now the funding-cap and the per-market cap ($250, also ≈ inert) effectively **do not bind**, leaving **only `DAILY_LOSS_HALT_USD=$40`** as a live brake (the naked breaker at $20 measures *risk*, a different axis, not fundability).

**Least-bad interim (config-only, no code):** **stop raising; set `KALSHI_MAX_TOTAL_CAPITAL` back DOWN to ≈ free cash (~$155), NOT above it.** Rationale: at ≈ free cash it is a **real, binding** backstop aligned with venue fundability; its only downside is the treadmill's **idle-cash** inefficiency — which is the **SAFE** failure direction (strands cash, never overdraws; Kalshi rejects any unfundable order regardless). An inert $250 cap is strictly worse: it removes the backstop and leans the whole account on the single $40 daily-loss halt.

**Risks this interim carries (state both):**
- **Over-reserve / idle cash** — the very treadmill we're fixing; accepted as the price of not shipping code yet. **Safe direction.**
- **Concentration is still unguarded** — neither the global cap nor free cash bounds per-event exposure; until §3.3 ships, watch per-event `market_exposure` manually and keep `DAILY_LOSS_HALT_USD=$40` armed.
- **If the cap is left inert ($250 > account):** only the $40 daily-loss halt is a real brake — **not recommended**; prefer the ≈free-cash setting above.

---

## 8. SHIP DISCIPLINE (mandatory before deploy)

This is a **Tier-3 code change** to a live real-money capital guard. Required before any deploy:
1. **`pytest`** green on the Kalshi suite (currently 83) **plus** the new pin tests below.
2. **Adversarial review** of the diff (verdict-code lens) — the previous draft earned 4 refutations; re-review is mandatory.
3. **The real reconciliation (§6)** run live read-only, plus the operator-run place→fill write experiment for Rule 2, **before** flipping `KALSHI_FUNDING_GATE=1`.
4. md5-gated install + kill/rollback one-liner (`KALSHI_FUNDING_GATE=0`) ready.

### Pin tests (new)
1. `test_naked_buy_draw_excludes_held_cost` — held cost never appears in the draw.
2. `test_naked_buy_draw_reducing_leg_zero` — one reducing order ≤ |held| → draw 0.
3. `test_naked_buy_draw_coverage_not_double_counted` — **TWO** reducing orders on one ticker summing > |held| → only |held| covered once; the excess draws cash. *(Kills the refuter-found bug; the old draft passes this only with a single reducing order per ticker.)*
4. `test_funding_gate_admits_when_free_cash_available` — funding_draw $89 < free_cash $139 → a $40 accumulating create is admitted (the unblocked idle cash).
5. `test_funding_gate_blocks_on_free_cash_exhaustion` — funding_draw near free_cash → new accumulating create blocked (the under-commit hole closed).
6. `test_funding_gate_fail_closed_on_balance_read_error` — balance read raises → free_cash=0 → all accumulating creates deferred; reducing still allowed.
7. `test_reducing_creates_never_blocked` — a reducing/unwind create is admitted even when funding_draw > free_cash.
8. `test_held_cost_carryforward_no_phantom_halt` — drop `market_exposure_dollars` for one ticker → `_held_cost` total flat via `last_known` → daily-`down` meter does not jump.
9. `test_per_event_gross_cap_uses_market_exposure_not_naked` — the event cap sums per-market GROSS exposure (Rule 3), never `naked_held_cost`.

---

## APPENDIX — GUESSES and sample sizes (every number's provenance)

**GUESSES (flagged):**
- `KALSHI_MAX_EVENT_RISK_USD = $20` start — a GUESS (risk dollars, set once; not gross notional inventory eats into). Tune from live per-event exposure.
- Interim `MAX_TOTAL_CAPITAL ≈ $155` (≈ free cash) and backstop `≈1.3–1.5× account` — judgment from live reads, not a canon value.

**Sample sizes:**
- Atomic capital-gap snapshot (the $250.07 / $139.67 / $89.19 / $50.48 numbers): **n=1 instant**, 9 positions / 3 events / ~23 orders.
- Live raw `/portfolio/balance` dump (field-set + $156.97 / $80.06): **n=1**, this session.
- Rule 1 (resting → balance Δ0, GROSS): **≈4 place/cancel events**, running tab + attribution ledger + VENUE phase.
- Rule 2 (fill-time collateral release): **n=1 passive** (Capture-1 p36) + settlement rows. **Thinnest; needs the §6 write experiment.**
- Rule 3 (no event netting, gross sum): **n=3 events** (gas daily/weekly, avocado).
- Rule 4 (no equity/reserved field): **confirmed** by the raw dump above.

**REFUTER VERDICTS (reported in full, including where they contradict the designer):**
- **`under-reservation-blowup` (HIGH, refuted):** central proof circular ✔ CONFIRMED (code + live dump); canon contradicts net-of-reserve premise ✔ CONFIRMED (running tab); coverage double-count ✔ CONFIRMED, **fixed §3.1**; backstop removal dangerous ✔ CONFIRMED (`_strike_of:1599`), **fixed §4 (keep real backstop)**.
- **`venue-model-wrong` (MEDIUM, refuted):** circular proof ✔; contradictory in-repo models ✔ (their NET reading is the weaker, assertion-based one — the measured model is GROSS, which *supports* the fix); fill-time release unmeasured ✔ (n=1) → **§6 write experiment required**.
- **`fallback-and-edge-cases` (HIGH, refuted):** one read-only balance-delta settles gross-vs-net → **adopted as the §6 real check**; their double-count worry is the NET model (contradicted by Rule 1) but the check is worth running live before trusting.
- **`does-it-end-the-treadmill` (HIGH, refuted):** "double-counts → treadmill returns at ~50%" — **valid ONLY under the NET model, which Rule 1 measures against.** Under the confirmed GROSS model the treadmill ends. The circular-proof half is CONFIRMED and fixed by §6. Their alternative gate (`cost ≤ free_cash`, seed 0) is the correct fallback **if** the live place→observe reads NET — encoded in §6's decision rule.

**Bottom line for the operator:** the direction is right and the idle-cash bug is real and quantified (~$50 stranded now, more at earlier pins). Do **not** ship on the circular reconciliation. Ship after: (a) coverage-double-count fixed (§3.1), (b) a real gross backstop kept + per-event cap added (§3.3/§4), (c) the live place→observe confirms GROSS and the operator's place→fill confirms Rule 2 (§6), (d) pytest + adversarial review (§8).

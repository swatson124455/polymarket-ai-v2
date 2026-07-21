# KALSHI QUOTER build ea28fa38 — INDEPENDENT ADVERSARIAL REVIEW (2026-07-21)

Method: 6 blind finder lenses (venue semantics / delta-neutrality / capital+units / failure modes /
lifecycle / test adequacy) -> dedup -> 2 independent adversarial refuters PER finding, prompted to
refute against the actual code (many ran live repros of the pure functions and probed the public
venue API). 85 agents total. 6 findings from the session's own line-by-line audit entered the
verify stage on the same blind footing. 56 raw agent findings + 6 -> 39 deduped -> 36 CONFIRMED
(both refuters upheld), 1 SPLIT, 2 REFUTED, 0 unverified.

Severity shown = original/verifier-consensus. Line numbers refer to build ea28fa38
(kalshi_live/maker_kalshi_quoter.py) and its client (e0b4c9c0 LF).

## CONFIRMED (36)

### C1. [HIGH/MED+MED] create_order_v2 raises on 'canceled' — the legitimate terminal status of a partially-filled IOC
`kalshi_live/maker_kalshi_client.py:171`

[source: venue+tests] The 200-with-rejected-order guard raises RuntimeError when order status is in ('rejected','canceled','cancelled'). But an IOC order that PARTIALLY fills terminates with status 'canceled' (remainder auto-canceled) while carrying a real fill_count. flatten_to_zero calls create_order_v2 inside try/except Exception: break (maker_kalshi_quoter.py:828-846), so the confirmed fill is discarded, remaining is never decremented, crossed is never incremented, and the retry ladder aborts on the first partial fill. Only an exactly-full fill ('executed') survives the guard. MockClient returns no 'status' and always full fill_count, so all 35 tests are blind to this (no test exercises partial fill, fill-then-cancel status, zero-fill break, or the still-open-IOC-cancel branch).

**Scenario:** Near-settlement de-risk on a thin hourly temp book: position +40 yes, depth at the touch = 15. Pass 1 IOC ask 40@best_bid fills 15, venue cancels the remainder, response order.status='canceled' -> RuntimeError -> except: break with remaining=40, crossed=0. No further passes; ~25 contracts of directional delta ride into resolution — the exact carry-into-settlement outcome the taker backstop exists to prevent, and it fails precisely when books are thin (partial fills most likely). The same path degrades _flatten_all's STOP escalation.

### C2. [HIGH/MED+HIGH] Blackout guard never fires when only the positions read fails (streak reset defeats the guard)
`kalshi_live/maker_kalshi_quoter.py:576`

[source: failure+tests+task1-audit] run_once resets read_fail_streak to 0 on a good standing read (line 576) BEFORE the positions read (line 582); when positions then fails, _blackout_guard increments 0->1 and returns. Under a sustained positions-only blackout the streak is pinned at 1 forever (reset-then-increment every cycle), so the BLACKOUT_CANCEL_AFTER=2 cancel of last-known orders NEVER triggers — repro-confirmed: 6 consecutive positions-blind cycles, streak=1 each cycle, zero cancels, quotes still live. The suite cannot catch this: the positions-failure test runs one cycle and only asserts no creates; the blackout-cancel test exercises only the standing-read failure path with a hand-seeded streak.

**Scenario:** GET /portfolio/positions starts persistently 500ing (endpoint drift, permission change, position_fp schema break) while /portfolio/orders still works. Every 2-min cycle logs 'skipping cycle (delta unknown)' and does NOTHING: no wind-down, no repricing, no cancels, no unwind, no taker backstop. The ~$90 resting GTC book sits on hourly temp markets into settlement, where stale wrong-side quotes are adversely lifted with near-certainty once the outcome is known. Loss bounded only by the full resting book — effectively the whole pilot bankroll — with the designed blind-fill guard structurally unreachable.

### C3. [HIGH/MED+MED] Orders created this cycle are never covered by last_oids (venue order_id discarded) — blackout guard cancels only stale ids
`kalshi_live/maker_kalshi_quoter.py:720`

[source: venue+failure+tests+task1-audit] last_oids is snapshotted from standing-at-cycle-start (line 577); the create loop discards the venue order_id in the create response and stores a fabricated 'sim-{cyc}-{i}' even in live mode (line 720), never appending real ids to st['last_oids']. Repro-confirmed: first cycle after deploy (standing empty) creates the full book with last_oids=[]; a full read blackout from the next cycle onward fires the guard which prints 'no last-known order ids — nothing to cancel' while every order rests live. In steady state the guard cancels the pre-churn ids — most of which the last good cycle itself already canceled/replaced — while that cycle's fresh GTC creates remain resting and fillable. Also, ids whose cancel raised are dropped from last_oids anyway (line 506) and never retried.

**Scenario:** Deploy (or any cycle on hourly-churn temp markets, where most of the book is re-created each cycle — 52% void snapshots make churn the norm), then reads black out for 2+ cycles. The blackout guard — the sole defense against blind fills — covers only the pre-cycle snapshot: after a fresh deploy it covers nothing at all, and in steady state it misses every quote placed in the last good cycle. It logs 'blind fills stopped' while stopping nothing; blind fills accumulate on exactly the newest (majority) quotes up to the $90 book and ride unmanaged into hourly settlements.

### C4. [HIGH/HIGH+HIGH] get_orders/get_positions are unpaginated, unfiltered single GETs against paginated endpoints — bot goes delta-blind as rows accumulate
`kalshi_live/maker_kalshi_client.py:226`

[source: venue+failure] KalshiOrderClient.get_positions() passes no cursor, limit, or count_filter (line 226); Kalshi docs: default limit=100, cursor-paginated, and the response includes settled/zero positions unless count_filter is passed. _held_cost/held_by, event_deltas, strand-unwind, the taker backstop, and the committed capital cap all read only page 1. get_orders (line 220) has the same no-cursor gap (~80 resting orders vs a 100-row page in the live config). The reconcile guard (raw>0/parsed==0) cannot detect truncation because truncated rows never arrive.

**Scenario:** Five hourly temp series produce ~120 new market_positions rows/day (settled rows retained in the response). Within roughly a day current positions silently fall off page 1: held_by omits real inventory, so no skew, no unwind quote, no settlement taker for those tickers — signed delta rides into resolution unmanaged; held_cost undercounts so MAX_TOTAL_CAPITAL over-admits new orders. On the orders side, restoring FOOTPRINT_TOP=60 or strand/carryover orders pushing resting >100 causes diff_orders to re-create duplicate quotes on invisible tickers (stacked collateral, doubled fill exposure). Truncation is indistinguishable from a complete read, so nothing warns.

### C5. [MED/MED+MED] mk-stopflat-{i}-{side} client_order_id reused verbatim across repeated STOP cycles — STOP degrades to metronomic taker fire-sale
`kalshi_live/maker_kalshi_quoter.py:904`

[source: venue+failure+task1-audit] With the STOP sentinel present, the 2-min timer re-invokes run_once -> _flatten_all EVERY cycle in live mode (run_once:515-520), and each run builds offset client_order_ids from a fixed prefix plus enumerate index. Kalshi dedups client_order_id, so run >= 2 either gets a duplicate-id rejection or an idempotent echo of the prior (since-canceled) order — either way no new offset rests. i comes from enumerate(held.items()) whose order can shift between runs, so the same coid can map to a DIFFERENT ticker across runs. Each pass also cancels the previous pass's passive offsets (resetting queue priority so they realistically never fill), then sleeps STOP_ESCALATE_S=90s inside a 2-min cadence and taker-crosses any residual >= 5ct — on EVERY pass.

**Scenario:** STOP run 1 rests a working passive NO offset (front of queue, partially filling). Run 2 fires 2 minutes later: the cancel-all pass pulls that working offset, then the replacement create with the identical coid mk-stopflat-0-no is rejected as a duplicate -> position sits with NO passive offset through the 90s wait -> escalation taker-crosses the full residual. The maker-first STOP doctrine silently converts to exactly the taker fire-sale pattern that previously cost real money, on every STOP cycle after the first, until flat.

### C6. [MED/MED+MED] STOP maker offsets capped by per-side $ room -> undersized offset guarantees taker escalation
`kalshi_live/maker_kalshi_quoter.py:901`

[source: venue] _flatten_all sizes its passive offset via _unwind_size, whose `room` bound is (MAX_MARKET_CAPITAL/2)/price (line 218) — even though the codebase elsewhere treats reducing orders as capital-exempt because a reducing fill frees the covered collateral (run_once:706-715). With the live env MAX_MARKET_CAPITAL=15, room ~= 15 contracts at p=0.50, so any position above ~15ct can never be fully offset passively in the STOP path's single shot.

**Scenario:** STOP with a 50ct long-yes position (INV_HARD_CT=60 permits this): offset rests only 15ct. After the 90s wait, even a COMPLETE passive fill leaves 35ct >= STOP_TAKER_MIN_CT=5 -> flatten_to_zero taker-crosses 35 contracts and pays the spread on a book that would happily have filled a full-size resting offset. The bounded-escalation design is structurally forced into its taker branch for any position larger than ~$7.50/price, on every STOP.

### C7. [MED/MED+MED] Unwind creates bypass failed-cancel deferral — stacked reduce orders flip position sign through flat
`kalshi_live/maker_kalshi_quoter.py:711`

[source: delta+capital] run_once defers a ticker's creates when its paired cancel failed, but explicitly exempts reducing ('unwind') creates (`if c['ticker'] in failed_cancel_tickers and not reducing`). If the standing order that failed to cancel is itself a stale unwind, the new unwind is placed on top of it, so total resting reducing size = 2x|inv| (verified by mock run: +15 held, stale 15ct NO-unwind uncancellable via 429, new 15ct NO-unwind placed anyway -> 30 ct reducing resting vs 15 held). _unwind_size's overshoot invariant is enforced per-ORDER, never per-book; with price drift plus repeated cancel failures the stack grows by |inv| per cycle. The same pattern exists in flatten_to_zero (standing-oid cancels are try/except-pass) and in _flatten_all offsets. The suite covers failed-cancel deferral only for the non-unwind case.

**Scenario:** Live, inv=+15 on a gas ticker, cycle N rested NO-unwind 15@0.50. Cycle N+1 the book ticks to 0.49; DELETE on the old order 429s (rate-limit burst); the new NO-unwind 15@0.49 is created anyway. A buyer sweeps both resting yes-asks within the 2-min window: 30 ct sold against +15 held -> position flips to -15. The bot then spends further cycles unwinding the short it created, paying the round-trip; at HARD-scale inventory the flip is +/-60 ct — exactly the sign-flip the overshoot guard was built to prevent, on multiple tickers simultaneously in a 429 storm.

### C8. [MED/MED+MED] SETTLE_UNWIND_MIN(30) > WIND_DOWN_MIN(20): taker preempts the entire passive wind-down window — taker becomes the routine hourly exit
`kalshi_live/maker_kalshi_quoter.py:606`

[source: delta+lifecycle+tests+task1-audit] The de-risk pass taker-flattens any |pos| >= INV_TOLERANCE(3) when close_time < now + SETTLE_UNWIND_MIN(30 min), and it runs BEFORE desired_quotes. Quote wind-down only starts at WIND_DOWN_MIN(20 min), so the taker window opens 10 minutes BEFORE passive wind-down even begins, and two-sided quoting stays alive INSIDE the taker window: the `flattened` skip lasts one cycle only, so the market re-enters desired next cycle with fresh 2-ct joins that can refill and get taker-crossed again. Code defaults (WIND=45 > SETTLE=30) give a passive-only window; live.env inverts it. The wind_down keep-resting-the-reducing-side branch (lines 247-257, 'fix F') is effectively dead code in the deployed config. CONFIRMED by repro: at 25 min to close with env values, desired_quotes still emits yes 2ct + no 2ct. No test or startup assert pins WIND_DOWN_MIN >= SETTLE_UNWIND_MIN; every settle/wind-down test runs at code defaults.

**Scenario:** Hourly temp market, t-29: fill of 3+ ct -> next cycle taker-flattens it (pays spread + taker fee, realizes adverse selection); t-27 the bot re-quotes both sides, refills, t-25 taker-flattens again — a 2-min-cadence quote/fill/taker churn loop across up to TAKER_MAX_MKTS=8 markets, recurring EVERY hour on every temp series. Repeated across 5 temp cities x ~15 quotable hours/day, taker crossings become a routine daily cost stream rather than a rare backstop — the exact ~$45-lesson pattern the maker-first design exists to avoid. Fix direction is config-level (SETTLE_UNWIND_MIN < WIND_DOWN_MIN, or gate the settle-taker on passive having had its window).

### C9. [MED/MED+MED] Conflicting inv/event signs: gate-exempt per-ticker unwind grows |event_delta| — hedged ladder converted into naked directional exposure
`kalshi_live/maker_kalshi_quoter.py:328`

[source: delta] Throttle direction is chosen by per-ticker inv whenever |inv| >= INV_TOLERANCE, with event_delta only supplying magnitude. Verified: inv=+5, ev=-55 -> the side whose fill would REDUCE the event short is throttled to 2 ct while a NO unwind rests whose fill pushes the event to -60. Unwind orders are exempt from cap_desired, the committed cap, the failed-cancel deferral, and are write-budget-prioritized — the event HARD pull cannot stop them, because on a counter-sign ticker the 'accumulating' side is the event-correcting one. Strikes with opposite-sign inventory (a genuine spread/hedge across a ladder) are unwound leg-by-leg with no ordering by event impact.

**Scenario:** Gas ladder accumulates +50 on the T3.10 strike and -60 across T3.20/T3.30 (event = -10, an economically hedged spread). Gas prints soft: NO-unwinds on the +50 leg fill first while the -60 legs' YES-unwinds don't. Event delta walks from -10 to -60: the bot sold its hedge leg and now carries a naked ~HARD-sized short built entirely by orders tagged 'unwind' and exempt from every gate. Transient worst case ~120 ct before HARD pulls bite; dollar loss bounded by held cost within MAX_TOTAL_CAPITAL=90 but the directional risk state directly contradicts the delta-neutral mandate.

### C10. [MED/LOW+MED] _unwind_size room cap ((MAX_MARKET_CAPITAL/2)/price) limits the reducing order to a fraction of the HARD envelope
`kalshi_live/maker_kalshi_quoter.py:218`

[source: delta+capital] With live MAX_MARKET_CAPITAL=15, _unwind_size caps the reducing quote at int(7.5/price): 15 ct at 0.49, 8 ct at 0.90, 7 ct at 0.96 (verified), while INV_HARD_CT=60 allows ~61 ct to accumulate (verified one-way sim). The reducing order can never rest more than ~25% (mid prices) or ~13% (high prices) of a HARD-sized position, so full flattening needs 4-8 consecutive full fills at 2-min cycles (>=16-30 min best case). The $-room bound is unnecessary for a reducing order: selling held contracts frees collateral — run_once itself asserts this and exempts unwinds from the committed cap, yet _unwind_size still applies the accumulating-side capital formula.

**Scenario:** One-way drift builds inv to 61 ct at ~0.50 in ~20 minutes (verified sim). Recovery flow appears willing to absorb 60 ct — but the bot only ever rests 15 ct of unwind per 2-min cycle, staying directional 15-30+ extra minutes. On short-lived markets the slower drain raises the probability the position survives to T-30 and exits via the settle-taker, paying spread on the residual instead of the free maker unwind — the fire-sale path the ~$45 of past taker losses came from.

### C11. [MED/MED+MED] Event-level single-cycle leak: N correlated strikes each below throttle can fill simultaneously — event delta jumps to ~2-3x HARD in one window
`kalshi_live/maker_kalshi_quoter.py:335`

[source: delta] The throttle acts only at cycle boundaries and only via resting sizes. With live values (capped_join = 15 ct at 0.50), a temp/gas event with 7-10 listed strikes all flat and event under SOFT(15) rests up to 15-20 ct per strike per side with zero shaping. A one-way sweep within a single 2-min window can fill every strike's same side: event delta jumps ~120-165 ct before any throttle or HARD pull acts — 2-3x the documented INV_HARD_CT=60 envelope (the code comment's 'HARD + one fill' claim is per ticker, not per event, and the event is the declared true risk unit). Drain is then limited to ~15 ct/strike/cycle by the _unwind_size room cap. Mitigants keeping this MED: dollar-bounded by MAX_MARKET_CAPITAL/MAX_TOTAL_CAPITAL (~$90), and the temp hourly RAMP shrinks temp joins to 2-4 ct, so the full-size jump is mainly gas-series exposure.

**Scenario:** A gas-print leak hits between cycles: within 90 seconds, sellers sweep the YES bids on all 7 strikes of a gas event, 15 ct each -> +105 ct correlated long acquired at pre-news prices, ~$50 notional, throttle never engaged. Next cycle everything is at/above HARD (that side's rewards stop event-wide), and passive drain at <=15 ct/strike/cycle against a one-way book mostly fails, so the position rides to the settle-taker. Adverse-selection loss of several dollars to tens of dollars per episode, repeatable every news event.

### C12. [MED/MED+MED] Per-market $15 cap never applied to HELD inventory — contract envelope allows ~$57 on one ticker
`kalshi_live/maker_kalshi_quoter.py:207`

[source: capital] MAX_MARKET_CAPITAL ($15, 'cap per market (both sides)') is only used to size RESTING quotes (_capped_join/_unwind_size); held inventory per ticker is bounded solely by INV_HARD_CT=60 CONTRACTS. Contracts and dollars are different units: at prices near MAX_PRICE=0.96, 60 ct = $57.6 held cost on one ticker — 3.8x the per-market dollar intent and ~77% of the $75 account concentrated in a single market. The aggregate committed cap only stops NEW accumulating creates after the concentration already exists.

**Scenario:** A high-prob temp strike trades ~0.90-0.96. _capped_join rests 7-8 yes/cycle; a one-way drift fills through the SOFT throttle until inv reaches HARD=60. Held cost ~= 60x0.93 ~= $56 on ONE ticker. Market resolves against: loss up to ~$56 of a $75 account from a mechanism labeled '$15 per market'. Event-delta throttling shares the same contract-denominated envelope, so a whole ladder caps at 60 ct, not $15.

### C13. [MED/MED+MED] RAMP_MIN=180 swallows entire ~58-min life of hourly temp markets — 2-4 ct quotes forever on the flagship 5 temp series
`kalshi_live/maker_kalshi_quoter.py:324`

[source: capital+lifecycle] live.env does not set RAMP_MIN, so the code default 180 min applies. Temp markets live ~58 min and enter the footprint only with 20-60 min left, so mins_left < RAMP_MIN ALWAYS: every join on every KXTEMP* market is ramped to max(MIN_QUOTE_CT, int(capped_join*scale)) = 2-4 contracts for its entire life (CONFIRMED by execution: 3 ct at 55 min to end; scale peaks at 0.24). Consequences: (a) resting size vs target_size_fp=1000 is ~0.2-0.4%, so collectible pro-rata reward on the flagship temp slice is pennies while the bot still bears fills, taker events, and churn; (b) the SOFT throttle is inert on temp — max(MIN_QUOTE_CT, int(2*(1-over)))==2 — so between SOFT and HARD the accumulating side is NOT actually shrunk; (c) int() steps in the decaying scale cancel/re-create quotes on static books.

**Scenario:** Bot goes live on the 7-series allowlist. The 5 temp series (the pilot's core) quote 2-3 ct/side all day — near-zero reward accrual while a one-way drift can still build |pos| to 10-15 that gets taker-crossed at close-30 — and gas daily/weekly carry full 15-ct size, silently concentrating all reward-seeking capital in the 2 gas series (the documented one-sided-drift trap). The deployed temp lane is structurally cost>revenue and telemetry shows nothing wrong (quotes ARE live).

### C14. [MED/MED+MED] Cancels run unbounded before the write budget, which then drops ALL creates including unwinds — deterministic on hourly rollover
`kalshi_live/maker_kalshi_quoter.py:466`

[source: capital+failure+lifecycle+tests] The cancel loop executes every diff cancel with no budget check (lines 687-691); bound_creates then computes budget = max(0, WRITE_BUDGET - len(cancels)) (line 466), which is 0 whenever cancels >= 60 (live WRITE_BUDGET=60), dropping every create group — the unwind-first sort inside bound_creates is moot at budget 0, defeating the fix-A doctrine that a risk-reducing order is never dropped for budget. Repro-confirmed: 80 cancels + one unwind create -> kept=[], dropped=1. All 5 city hourly temp markets share one close time, so at close-20 the ENTIRE temp footprint (up to 80 resting orders) exits selection in ONE cycle. The 80 unmetered cancels also press the rate limit, feeding the cancel-429 unwind-stacking finding. test_bound_creates_prioritizes_unwind uses cancels=[] so it pins only intra-budget ordering.

**Scenario:** Live config: FOOTPRINT_TOP=40 x 2 sides = 80 resting orders. A full-reprice or top-of-hour rollover cycle diffs ~80 cancels: all execute, budget=0 -> zero creates. The entire book — including replacement UNWIND quotes for held inventory and strand-unwinds for the closing hour — is pulled and nothing re-rested: inventory sits without its reducing order (near settlement this forces the taker backstop, converting a $0 maker unwind into a paid taker cross), rewards go to zero, and under sustained churn the bot ping-pongs cancel-all/create-some on alternating cycles. Deterministic every hour the quoted count exceeds ~29.

### C15. [MED/MED+MED] Blackout guard wipes last_oids even when every cancel failed
`kalshi_live/maker_kalshi_quoter.py:506`

[source: failure] _blackout_guard clears st['last_oids'] unconditionally after the cancel loop (line 506) with all cancel exceptions swallowed (line 504) — repro-confirmed: 0/2 cancels succeeded (network down), ids wiped, and after the write path healed the next guard invocation had nothing to cancel.

**Scenario:** A VPS-side network partition causes the read blackout — the same partition makes the DELETE cancels fail (same host, same client). The one-shot guard burns its only ammunition on the dead connection, then permanently disarms: when connectivity partially heals (writes ok, reads still failing) the quotes remain live and unfillable-by-us. The guard's primary trigger scenario (network outage) is precisely the one where it self-destructs. plan.blackout_cancelled=0 is logged but nothing acts on it.

### C16. [MED/MED+MED] Partial standing parse creates invisible ghost orders: uncancellable, uncounted, and stacked on
`kalshi_live/maker_kalshi_quoter.py:567`

[source: failure+task1-audit] The reconcile guard only halts when raw_rows>0 AND parsed==0 (line 567); any partial parse proceeds. Repro-confirmed with 1-of-3 rows parsing: the 2 ghost orders were never cancelled (absent from standing so diff can't see them, absent from last_oids so the blackout guard can't either), excluded from the committed-capital sum (cap over-admits), and diff placed a NEW quote on the same ticker/side directly on top of a ghost (doubled size at the level).

**Scenario:** Kalshi ships a field-name variant on a subset of orders (e.g. fractional orders lose {outcome}_price_dollars) — exactly the API-drift class the reconcile guard was built for, but affecting 30 of 40 rows instead of 40. The cycle runs 'healthily' while 30 GTC orders rest permanently: never repriced, never wind-downed, riding into every hourly settlement, with real exposure invisible to the $90 cap and duplicate quotes stacking collateral on top. Only mitigations: STOP's _flatten_all cancels raw rows without parsing; dropped_book_rows doesn't cover this path — so nothing surfaces it.

### C17. [MED/MED+MED] No run lock: manual run overlapping the timer doubles the book past the capital cap
`kalshi_live/maker_kalshi_quoter.py:511`

[source: failure] run_once has no lockfile/flock/pid guard (line 511); systemd only serializes the SAME unit. Two concurrent processes each read the same standing snapshot, each compute the same missing quotes, and each pass the committed-capital check independently — client_order_id nonces are int(timestamp) per process so venue dedup does not collide (though same-second starts CAN collide ids, causing duplicate-id create_fail noise instead).

**Scenario:** During an incident the operator runs the quoter by hand to 'see a cycle' while the 2-min timer unit is also mid-cycle (cycles can run minutes: up to 200 reads x 0.55s spacing plus write spacing). Both processes place the full desired book: ~2x quotes at the same levels, committed ~== 2 x MAX_TOTAL_CAPITAL ($180 on a $75-floor account), and both sides' duplicates then get cancel-churned apart over subsequent cycles. Manual-run-during-incident is the most likely operator behavior precisely when state is already degraded.

### C18. [MED/MED+LOW] Settle-taker close_time fetch is fail-open: exception silently disables the settlement backstop
`kalshi_live/maker_kalshi_quoter.py:607`

[source: lifecycle] In the de-risk pass (lines 604-608), a failed GET /markets/{t} (transient 5xx, timeout, or the read-budget RuntimeError from public_get) is swallowed by `except Exception: pass`, leaving near_settle=False — the ONLY taker trigger — so the settlement backstop silently skips that ticker, contradicting the fail-closed pattern used for standing/positions reads.

**Scenario:** Bot holds +12 YES on KXTEMPCHIH at close-28 min. Kalshi's market endpoint returns 502 for that cycle and the next few (or a sustained API degradation hits the final 30 min). near_settle stays False every cycle; at close-20 the market leaves the footprint and the strand path rests a maker unwind that never fills on the trending side; the full delta (up to ~$7.50/market, $90 book-wide) is carried into resolution unmanaged — the exact loss mode SETTLE_UNWIND_MIN exists to prevent. A one-cycle blip self-heals in 2 min, but the guard degrades exactly when the venue is flaky.

### C19. [MED/MED+MED] usd_day pot-ranking + 50 concurrent temp programs > FOOTPRINT_TOP=40: gas markets permanently crowded out of the footprint
`kalshi_live/maker_kalshi_quoter.py:172`

[source: lifecycle] select_footprint ranks by program pot per day (lines 172-174): hourly temp programs score ~$1,920/day (an $80 pot over a 58-min program) vs gas daily ~$192 and gas weekly ~$15. Probe: 50 temp + 29 gas allowlisted programs are active concurrently; PER_SERIES_CAP=10 admits all 50 temp rows, so the 40 footprint slots fill with temp before any gas row is reached — the longer-lived, tighter gas markets (the slice where a 2-min-cadence maker with 180-min ramp actually functions as designed) are never quoted.

**Scenario:** Deployed as-is: footprint = 40/40 hourly temp strikes every cycle during temp trading hours; combined with the ramp finding, the bot spends its entire footprint on markets where it collects ~0.3% of pot and pays routine settle-takers, while KXAAAGASD/W get zero quotes and zero rewards. The ranking optimizes the pot, not our collectible share.

### C20. [LOW/LOW+LOW] Fetch-fail-retained quotes lose their 'unwind' reason and can be cap-dropped — resting unwind cancelled by a read hiccup
`kalshi_live/maker_kalshi_quoter.py:634`

[source: venue+capital+failure] On a transient orderbook fetch failure the standing orders are re-injected into `desired` as {side, price_dollars, count} WITHOUT the reason field (lines 633-635), so cap_desired (line 444/450) and the committed gate treat a standing UNWIND order as accumulating; if the desired book exceeds MAX_TOTAL_CAPITAL and the ticker's usd_day is low, the retained market is dropped from kept, after which diff_orders CANCELS its standing orders — defeating the stated purpose of retention, including a resting unwind on a ticker with live inventory.

**Scenario:** Committed near MAX_TOTAL_CAPITAL=$90 plus one transient orderbook fetch failure on a low-usd_day ticker carrying a working reducing offset: cap_desired sorts the reason-less market into the accumulating tail and drops it -> the live unwind quote (queued at the front of the book) is pulled for a cycle and re-created next cycle at the back of the queue. Repeated transient failures keep churning the one order that flattens risk; if failures persist near settlement, the passive exit window is squandered and the settlement taker inherits the full size.

### C21. [LOW/LOW+LOW] Cross-event correlation invisible to event_deltas: gas daily/weekly and successive hourly temp events each get an independent HARD budget
`kalshi_live/maker_kalshi_quoter.py:961`

[source: delta] event_deltas keys on SERIES-EVENT (first two dash fields). KXAAAGASD-26JUL20 and KXAAAGASW-w30 are distinct keys but both are 'AAA national gas price above X' — directionally additive in reality. Likewise consecutive hourly temp events for the same city (and nearby cities in a regional heat event) are correlated but aggregated separately. True correlated directional exposure can reach ~N_correlated_events x (HARD + leak) while every individual throttle reads healthy. Bounded in dollars by MAX_TOTAL_CAPITAL=90 and long-only max-loss=cost.

**Scenario:** Gas prices drift down for a week: the bot accumulates +55 ct on the daily event and +55 ct on the weekly event (each under HARD=60, neither throttled to zero). Real directional gas exposure is ~110 ct; a continued move loses on both books at once, ~2x what the operator believes the HARD envelope permits, until the $90 committed cap finally stops new accumulation.

### C22. [LOW/LOW+LOW] Price-sanity and crossed-book gates also cancel the resting unwind when the reducing-side reference exceeds MAX_PRICE(0.96)
`kalshi_live/maker_kalshi_quoter.py:260`

[source: delta] In the JOIN path, spread_sanity/crossed-book returns [] before the inventory branch is reached, and the wind_down branch requires _priceable — so when the reducing side's reference price leaves (MIN_PRICE, MAX_PRICE] the bot cancels any resting unwind and rests nothing, even while holding inventory. The strand-unwind loop has the same bound. For a long-yes position with yes collapsing (best_n rises through 0.96) — exactly the adverse-selected loser — the passive exit shuts off for the remainder of the collapse; only flatten_to_zero (own bounds 0.01-0.99) at T-30 can exit.

**Scenario:** Bot is +40 ct yes at ~0.50 on a gas ticker; adverse news drives yes toward 0.03 (best_n 0.97 > 0.96). desired_quotes returns [] every cycle: the standing NO-unwind is cancelled and never re-rested, so late buyer flow that would have paid 0.03-0.04 for our yes is never captured. The position rides unmanaged until the T-30 taker crosses it at the worst book of its life.

### C23. [LOW/LOW+LOW] MIN_QUOTE_CT=2 < INV_TOLERANCE=3: floor-size fills are invisible to every de-risk mechanism and ride into settlement
`kalshi_live/maker_kalshi_quoter.py:601`

[source: lifecycle] During the ramp (a temp market's whole life) both joins rest at the 2-ct floor, but the unwind quote (line 358), the settle-taker (line 601), and the strand path (line 658) all gate at |pos| >= INV_TOLERANCE=3 — so a single 2-ct fill produces a position no mechanism will ever reduce; it is carried into resolution by construction.

**Scenario:** At close-25 the 2-ct YES join at 0.92 fills as the temp answer flips; |pos|=2 < 3, so no unwind is rested, the T-30 taker skips it, wind-down cancels quotes at close-20, and the 2 ct expire worthless: -$1.84. Per-event bound ~$1.92; expected cost is adverse-selection-only, but it recurs across every strike/hour and is structurally unfixable by the current guards (floor < tolerance guarantees the blind spot).

### C24. [LOW/LOW+LOW] ACTIVATE mostly dead at deployed env ($15 gate needs bid-sum <= 0.75); when it fires it posts un-ramped full-size contracts inside the taker window
`kalshi_live/maker_kalshi_quoter.py:304`

[source: capital+lifecycle+tests] Minimum activate pair is 20/20 contracts (add = max(JOIN_SIZE, target-ext)), so the $15 MAX_ACTIVATE_CAPITAL gate mathematically requires best_y+best_n <= 0.75 — CONFIRMED by execution (0.40/0.35: gated out; 0.30/0.30: activates 20/20). With ~52% of temp snapshots void, the ACTIVATE lane is mostly dead at these dials — the pilot's dominant void-market opportunity silently yields zero quotes with no error, and the suite only reaches the branch by monkeypatching MAX_ACTIVATE_CAPITAL=100000. Where it does fire, activate sizes have NO ramp (ramp is JOIN-only) and no JOIN_SIZE-vs-INV_HARD clamp: at 0.04 add can reach 150 ct/side (2.5x the 60-ct envelope), and it can post as late as T-21 — inside the settle-taker window that opened at T-30.

**Scenario:** At close-24 a strike's book thins through ext_y=990 while wide (0.40/0.30): ACTIVATE posts 20 YES + 20 NO (~$14) full-size where a ramped join would rest 2 ct. Informed flow lifts the wrong side; next cycle the taker crosses ~20 ct over a >=25-tick spread: ~$5 realized, up to $15 at risk; `flattened` expires after one cycle so the still-void book can re-activate until close-20. Meanwhile plan telemetry shows activate_markets ~0 in normal operation and neither the 35 tests nor the dryrun smoke (which never enters ACTIVATE, wind-down, ramp, or live-mode paths) would flag either behavior.

### C25. [LOW/LOW+LOW] Cancel-succeeds/create-fails leaves repriced sides (including unwinds) naked for a cycle
`kalshi_live/maker_kalshi_quoter.py:717`

[source: failure] All cancels execute before any create (line 687 vs 717); a replacement create that 429s or is rejected post_only (book moved between plan and send) is counted in create_fail and NOT retried or rolled back, leaving that side unquoted — for an 'unwind' create this means held inventory loses its reducing quote until the next cycle.

**Scenario:** A rate-limit burst or fast book move during a reprice cycle: old unwind cancelled, new unwind rejected. For >=2 minutes the position has no passive exit; recurring bursts (correlated with the churny cycles that generate the most ops) extend the window. Bounded per-cycle, but it compounds with the write-budget starvation finding into whole-book-pulled cycles.

### C26. [LOW/LOW+LOW] quoter_state.json corruption silently resets the blackout guard; STOP cycles write no telemetry
`kalshi_live/maker_kalshi_quoter.py:397`

[source: failure] load_state swallows every exception and returns {} (line 397), silently zeroing read_fail_streak and last_oids; save_state is atomic (os.replace) but unfsynced, so a host crash can leave a truncated file that loads as {}. Separately, the STOP branch returns before load_state/append_plan/save_state (lines 515-520), so STOP invocations leave no plan row and no state update.

**Scenario:** Host crash mid-save corrupts the state file at the same time as an API blackout — a correlated pair, both stemming from host trouble. The next cycles rebuild streak from 0 and last_oids from the next good read; if reads never recover, the guard fires with nothing to cancel. Meanwhile a STOP+flatten sequence leaves zero audit trail in plans-*.jsonl for reconstructing what was cancelled/crossed during the most consequential cycles the bot runs.

### C27. [LOW/LOW+LOW] Throttled side silently pulled to zero BELOW HARD when the 1-tick step-inside lands on MIN_PRICE
`kalshi_live/maker_kalshi_quoter.py:365`

[source: tests] The throttle steps the accumulating side to best-TICK (lines 346/352), then the emit gate requires MIN_PRICE_DOLLARS < price STRICTLY (line 365). With live.env MIN_PRICE=0.04, a best of 0.05 throttles to 0.04, fails the strict '>' and the side is dropped entirely while mag is still in (SOFT,HARD) — violating the pinned invariant that below HARD a side is shrunk to MIN_QUOTE_CT but never pulled. CONFIRMED by repro: best_y=0.05, inv=30 -> only the NO unwind is quoted; YES vanishes. Every shaping test uses 0.48-0.50 books.

**Scenario:** Late-hour temp markets routinely trade 0.05/0.94. Long-yes inventory over SOFT there -> YES quote silently disappears each cycle -> that side's liquidity-reward stream stops with no telemetry distinguishing it from a deliberate HARD pull. Money impact is forgone rewards, not risk (the dropped side is the accumulating one).

### C28. [INFO/INFO+INFO] Same-second cycle nonce can collide client_order_ids across overlapping runs
`kalshi_live/maker_kalshi_quoter.py:522`

[source: venue] cyc = int(now.timestamp()) is the only cross-cycle uniqueness component of order_id_for; two run_once invocations in the same wall-clock second (manual run racing the systemd timer, or a supervisor retry) generate identical mk-{cyc}-{i}-{side} ids.

**Scenario:** Operator runs the quoter by hand while the timer fires: the second process's creates are rejected as duplicate client_order_ids -> a full cycle of create_fail noise and a one-cycle quoting gap. Bounded and self-healing next cycle; worth knowing when reading a create_fail spike in plans-*.jsonl.

### C29. [INFO/INFO+INFO] Core venue mappings verified correct under this lens
`kalshi_live/maker_kalshi_client.py:175`

[source: venue] Reviewed and found CORRECT: (a) create_quote's NO@p -> ask@(1-p) yes-scale transform, including the throttle's stepped price; (b) committed-$ for no-side orders uses p_no x count, equal to venue collateral of an ask; (c) flatten direction/scale for long-yes and long-no — both marketable, correct sign; (d) position_fp signed decode and raw signed additivity of event_deltas over 'greater'-strike ladders; (e) _live_standing outcome_side + {outcome}_price_dollars round-trips exactly into diff_orders keys; (f) crossed-book gate best_y+best_n>=1.0 is conservative and venue-correct.

**Scenario:** No failure — recorded to scope what was checked so the caller does not re-derive these surfaces.

### C30. [INFO/INFO+INFO] Self-referential reference price: bot re-steps 1 tick inside its own best quote every cycle — churn tax on thin books
`kalshi_live/maker_kalshi_quoter.py:346`

[source: delta] best_y/best_n come from the public book, which in live mode includes our own resting orders. When throttled, the accumulating quote is placed at best - TICK; if our order was the sole best, next cycle 'best' is our new quote and the price steps down another tick, repeating until it undercuts external best - 1. Each step is a cancel+create pair (2 ops, 12 write-tokens) against WRITE_BUDGET=60. Delta-wise favorable (progressively less likely to fill), no direct money loss — but a persistent churn tax that can crowd lower-priority creates out of the write budget. Related: any partial fill changes remaining_count, so the exact-match diff cancels and re-creates the order, resetting queue priority.

**Scenario:** Thin gas book where the bot is best yes bid at 0.50 and gets throttled: cycles quote 0.49, 0.48... down to external best 0.44 over 5 cycles = 10 order ops producing nothing, while a 40-market cycle is already near the 60-op budget; bound_creates then drops the lowest-usd_day tickers' quotes that cycle.

### C31. [INFO/INFO+INFO] Positive verifications under the delta lens (per-ticker envelope, overshoot cap, HARD pull, ACTIVATE inventory handling)
`kalshi_live/maker_kalshi_quoter.py:341`

[source: delta] Verified by simulation with live values: (1) per-ticker one-way leak well bounded — terminal 61 ct, accumulating side pulled at >=60, MIN_QUOTE_CT floor leaks ~2 ct/cycle in [SOFT,HARD), HARD pull correctly overrides the floor. (2) _unwind_size never exceeds |inv|. (3) flatten_to_zero's cumulative-cross cap at |pos0| via venue-confirmed fill_count is sound against lagging position reads. (4) ACTIVATE branch handles inventory correctly (rests only the reducing side when carrying inventory; flat ticker in a directional event refuses to add; no live-mode oscillation). (5) Throttle direction from event_delta when the ticker is flat is correct in the aligned-sign case. No path found that grows PER-TICKER exposure unbounded.

**Scenario:** No failure — records what was checked and holds, so the findings above read as exceptions rather than a broken core.

### C32. [INFO/INFO+INFO] committed double-counts covered unwinds and trusts unverified market_exposure_dollars semantics (primary path untested)
`kalshi_live/maker_kalshi_quoter.py:698`

[source: capital] committed = surviving standing (incl. resting unwind orders at full price*count) + held_cost + new creates (unwind creates also increment it, line 720). For a covered unwind the venue reserves ~nothing, so the same exposure is counted twice -> the $90 gate binds early and blocks accumulating joins — conservative, cost is reward only. Separately, market_exposure_dollars is assumed to equal reserved cost basis; if the field is mark-to-market instead, committed drifts from real reserved cash (fallback abs(n)*$1 is conservative).

**Scenario:** Held $40 across tickers with $40 of resting unwinds: committed starts at ~$80 of the $90 cap before any join is considered -> nearly all reward-earning joins skipped while true reserved cash is ~$40 — the bot idles at half its intended deployment. No loss; measurable reward shortfall.

### C33. [INFO/INFO+INFO] _held_cost's primary path (market_exposure_dollars) is completely untested — mock omits the field so the suite pins only the abs(n) fallback
`kalshi_live/maker_kalshi_quoter.py:950`

[source: tests] Committed-capital accounting prefers float(p.get('market_exposure_dollars')) (line 950), but MockClient positions fixtures never include that field, so test_held_cost_reads_prod_position_fp (asserting total=24.83) actually pins the $1/contract FALLBACK, not the field the code runs in prod. If the prod field is fp/cent-scaled, negative for shorts, or inclusive of resting-order exposure, the MAX_TOTAL_CAPITAL gate mis-scales silently and no test would fail.

**Scenario:** Prod returns market_exposure_dollars in a different unit or sign convention than assumed -> committed_usd wrong by a constant factor -> either the cap deadlocks quoting (the exact S-history '8x over-conservative' failure this line was written to fix) or admits real over-commitment. Needs one live-read assertion or a prod-shaped fixture with the field present.

### C34. [INFO/INFO+INFO] Wind-down unwind branch (fix F) is unreachable from the live loop; its protection is actually delivered by the strand path
`kalshi_live/maker_kalshi_quoter.py:247`

[source: lifecycle] run_once computes `now` once (line 513) and passes the same value to select_footprint (drops end < now+20) and desired_quotes (line 641), so no footprint market can ever satisfy `end < now + WIND_DOWN_MIN` at line 247 — the hold-inventory-in-wind-down unwind branch (lines 247-257, including its _priceable requirement) is dead code in production flow. Wind-down actually happens by footprint-drop -> diff cancels-all -> strand-unwind path (lines 656-677), which has slightly different gates (per-side price bounds instead of _priceable's both-sides requirement).

**Scenario:** No direct money loss — the strand path covers the same inventory. But test_settlement_ramp/wind-down tests exercise the dead branch directly, so the suite certifies behavior the shipped integration path never executes; a future edit to the strand path (the REAL wind-down unwind) would not be caught by the fix-F tests.

### C35. [INFO/INFO+INFO] close_time vs program end_date: empirically identical today (10/10 sampled), so the dual-clock design is currently coherent
`kalshi_live/maker_kalshi_quoter.py:605`

[source: lifecycle] Selection, ramp, and wind-down key off program end_date while the settle-taker keys off market close_time (line 605). Live probe of 10 allowlisted programs (temp hourly + gas daily/weekly): end_date == close_time to the second in every case. If Kalshi ever sets a program end later than the market close (or a market closes early), ramp/wind-down would silently misalign — full-size quoting to the close with only the taker backstop — but there is no evidence of divergence in current data.

**Scenario:** No current failure. Documented so a future series addition outside KXTEMP*/KXAAAGAS* (or a Kalshi program-schema change) gets this assumption re-verified rather than inherited.

### C36. [INFO/LOW+INFO] Capital gate can strip one side of a planned pair near the ceiling
`kalshi_live/maker_kalshi_quoter.py:714`

[source: task1-audit] The per-create committed gate evaluates each side independently; near the $90 ceiling one side of a join/activate pair can be created while the other is skipped, resting a one-sided quote.

**Scenario:** committed=$80, yes side $7 placed (87<=90), no side $8 skipped (95>90): one-sided quote rests, fills are directional by construction until the next cycle rebalances.

## SPLIT (one refuter upheld, one refuted) (1)

### S1. [MED/MED] Cap-exempt unwind orders can still be rejected for real balance — no fallback, balance never read
`kalshi_live/maker_kalshi_quoter.py:704`

[source: capital] The unwind exemption rests on 'a risk-reducing order can never over-commit the account', but the code's own adjacent comment concedes collateral is reserved WHILE RESTING. A long-NO unwind maps to a yes-scale BID (client line 181-185) which plausibly requires price*count cash up front. When held cost has consumed most of the $75 balance, Kalshi rejects the unwind for insufficient funds; create_quote raises, it's counted as an anonymous create_fail, and there is no downsize-and-retry, no balance read (get_balance() exists but run_once never calls it), no distinct telemetry.

**Scenario:** Held long-NO across 6 tickers, ~15 ct each at no-price ~0.5 -> ~$45 held cost, ~$30 free. Six unwind yes-bids need ~$45 reserved -> several rejected EVERY cycle, the passive de-risk path silently dead. The settlement taker for long-NO is also a cash-consuming IOC buy — if free balance is short it rejects too and the loop breaks, carrying delta into resolution despite the mandate. The 'flatten maker-first' machinery is inoperative exactly when the book is fullest.

## REFUTED (2)

- **flatten decrements by o['fill_count'] with no fill_count_fp fallback** — The finding is conditional on the V2 create-order response carrying fill_count_fp instead of fill_count. The official Kalshi docs for POST /trade-api/v2/portfolio/events/orders (Create Order V2) define the response schema as exactly: order_id, client_order_id, fill_count
- **MAX_TOTAL_CAPITAL=$90 exceeds the $75 account — software ceiling can never bind first** — The finding's factual premises partially hold (MAX_TOTAL_CAPITAL=$90 > $75 account; quoter never calls get_balance — verified by grep, get_balance exists only in the client/tests/standalone scripts), but the failure scenario cannot occur as described. The naive-sum 'comm

(0 findings unverified; full verifier notes live in the session workflow output, not committed.)

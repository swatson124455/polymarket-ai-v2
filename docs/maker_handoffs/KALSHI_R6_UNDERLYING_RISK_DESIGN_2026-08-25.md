# R6 DESIGN — PER-UNDERLYING EXPOSURE ACCOUNTING (2026-08-25, for signoff)

Overhaul item R6 ("18 gas strikes = ONE gas bet; caps count markets"). D3-B (dollar-
weighted event delta, live since 16:56Z) was the first cut — it covers strikes of ONE
event. This design covers the remaining layer: correlated exposure ACROSS events and
series that share a physical underlying. Design only — NO code until signoff.

## 1. The gap, stated with this window's own portfolio
- F9 selection was 25 markets over exactly THREE underlyings: 18x KXAAAGASW tails +
  4x KXDIESELW + 3x KXTOPMODEL (F9_RECOUNT_2026-08-19.json). Every cap in force
  (per-market $60, series $100, total $200, event delta) treats the 18 gas tails
  across daily/weekly/monthly series as independent — but one AAA gas print moves
  all of them together. Today's tape rhymes: cheap-side fills clustered by underlying
  (three gas-family fills 12:01-15:17Z on three different tickers).
- Nothing enforces "how much total money is exposed to gas".

## 2. Design
1. **Mapping (env, explicit, fail-safe)**: `KALSHI_UNDERLYING_MAP` =
   `KXAAAGASD:aaa_gas,KXAAAGASW:aaa_gas,KXAAAGASM:aaa_gas,KXDIESELW:diesel,`
   `KXTOPMODEL:topmodel,...` — series -> underlying label. An UNMAPPED series is its
   own underlying (independent by default; mapping only ever MERGES risk, so a
   missing entry under-groups but never over-permits a mapped group).
2. **Two meters per underlying, every cycle** (same bounded-loss unit as D3-B):
   - `held_usd[u]`   = SUM over held tickers of |inv| x basis $/ct (max further loss);
   - `committed_usd[u]` = held_usd + SUM over ACCUMULATING resting quotes of
     price x count (worst-case new basis if everything fills).
3. **Enforcement (both default OFF = 0)**:
   - `KALSHI_UNDERLYING_MAX_COMMITTED_USD` — ENTRY gate: no new accumulating quote
     on any ticker of underlying `u` while committed_usd[u] >= cap. Skip-with-stat,
     self-reversing as exposure unwinds.
   - `KALSHI_UNDERLYING_MAX_HELD_USD` — HARD envelope: at/above it the whole
     underlying goes reduce-only (accumulating quotes stripped, exits untouched) —
     the underlying-level clone of INV_HARD.
   - Exits NEVER gated (house doctrine, unchanged everywhere).
4. **Proposed initial values (operator sets)**: COMMITTED $80/underlying, HELD
   $20/underlying — sitting between today's per-market $60 and total $200 so the
   3-underlying portfolio keeps full headroom under normal operation and the cap
   only bites on correlated pile-ups. INFERRED sizing, not measured — which is why:
5. **Rollout: METERS FIRST.** Ship the meters + a per-cycle telemetry row
   (`underlying_exposure` in the plan) with enforcement knobs at 0. Read a few days
   of real distribution, then set caps from the observed p95 rather than my guess.
6. **Interactions**: event-delta throttle stays (finer, directional, intra-event);
   series-$ cap stays (venue-structural); this is the coarse correlated envelope
   above both. No existing knob changes semantics.

## 3. Test plan (ships with the code)
Mapping parse (malformed -> refuse loudly, the MID_BAND_OUT pattern); meter math pins
(held vs committed, basis fallback); entry gate skip + self-reversal; hard envelope
reduce-only with exits preserved; unmapped-series independence; knobs-off byte-identical;
chaos cycle with all three risk layers armed.

## 4. Signoff asks (decisions only)
1. Approve the design shape (map + two meters + two caps)?
2. Meters-first rollout (recommended), or arm enforcement immediately with the
   proposed $80/$20?
3. Confirm the initial underlying map above (gas family merged; diesel separate from
   gas, or merged with it? — they are distinct AAA prints but economically cousins;
   your call which way to group).

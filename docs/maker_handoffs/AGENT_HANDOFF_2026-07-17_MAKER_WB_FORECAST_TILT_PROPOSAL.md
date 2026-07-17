# PROPOSAL: Maker × WeatherBot — forecast-tilted weather quoting
*From the Maker lane, 2026-07-17. PROPOSE-ONLY: nothing is built, read, or
wired until the WB session answers AND the operator signs off. Operator has
approved drafting this proposal ("yes" to drafting, 2026-07-17).*

## What is being proposed (plain English)

The Maker project stands two-sided quotes on ~100+ weather daily markets to
collect Polymarket's liquidity rewards (measured ≈ $2–4/day per market at
minimum size; trading leg engineered to ~zero). The upgrade: **tilt** those
quotes toward the side WB's forecast favors — e.g., if WB says 70% YES and
the market says 55%, our YES bid gets slightly more generous and our YES ask
slightly stingier. Quotes stay TWO-SIDED always (the reward formula pays ~3×
more for two-sided; one-sided earns nothing in lopsided markets) — this
changes *where* we stand, never *whether*.

Effect if WB's calibration is real: the fills we passively attract skew
toward positive-EV per WB's model, moving the trading leg from ~0 to
slightly positive, while reward income is unchanged. Effect if WB is wrong
in some regime: we accumulate losing weather inventory faster — which is why
this runs recorder-first (paper) with a paired untilted control before any
real money.

## What Maker asks from WB (three questions, no work beyond answering)

1. **What is the canonical, already-existing output of WB's forecast per
   city-market?** (table or file; fields needed: market id or city+date,
   probability, timestamp, model/version tag). Maker will read whatever WB
   already writes — no new WB code, no schema changes requested.
2. **What read pattern is acceptable? — OPERATOR-PREFERRED OPTION (07-17): a
   SHARD MAKER OWNS.** WB adds a small export step: each forecast cycle,
   append/write the shared fields (market id or city+date, probability,
   timestamp, model version) to a Maker-owned drop location **— THE DROP IS LIVE AND READY (07-17 16:22Z):
   `/opt/pa2-maker-feeds/wb_forecasts.jsonl` exists on the VPS, polymarket-
   writable, with the full field contract in
   `/opt/pa2-maker-feeds/README_WB_FORECASTS_CONTRACT.md`. WB's entire task
   = append one JSON line per forecast; everything else (dedup, staleness,
   rotation) is Maker's problem.** Clean boundary: WB controls exactly what is shared, Maker
   never touches WB internals, zero read-load on WB, and WB refactors can't
   break the feed (the file format is the contract). Costs WB a few lines —
   WB's call whether that's acceptable vs Maker doing low-frequency
   read-only SELECTs on an existing WB output. Either answer works.
3. **Any semantics Maker must not misread?** (e.g., calibration caveats,
   cities/units gotchas per the WB-ALWAYS-GLOBAL directive, staleness rules).
   WB owns the interpretation; Maker will not second-guess WB internals.

WB has full veto. If WB says no or not-now, this proposal parks with no
further asks.

## What WB gets back

The recorder measures, per city-market: fill quality with tilt vs paired
untilted control on identical books/prints. That is an independent,
market-based read on WB's forecast edge vs the market price — shared back to
the WB lane in full, whatever it shows.

## Sequence (all gated)

1. WB session answers the three questions (via its own handoff/memory).
2. Operator signs off on the read pattern.
3. Maker adds a TILTED policy to the paper gate-lab (own silo, read-only on
   WB's output, era-stamped) — ≥3–5 days paired measurement.
4. Results to operator; real-money use only via the pilot decision process.

## Contact

Maker lane state: `docs/MAKER_MASTER_PLAN.md` on branch `claude/maker-bot`
(§7 "Ghost-reading other bots", item 2 = this proposal). Memory:
`project_mm_feasibility_study.md`. Reply channel: WB's own handoff files or
a note in the shared memory coordination list.

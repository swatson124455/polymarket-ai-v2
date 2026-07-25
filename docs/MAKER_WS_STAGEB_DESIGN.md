# MAKER WS HOT PATH — STAGE B DESIGN + FEASIBILITY (S7, 2026-07-25)

Operator said "start" Stage B (the ~200 ms reprice-only hot path). Per the
no-blind-copy rule this was designed against Poly's ACTUAL architecture, not
transcribed from Kalshi. The design pass produced a real decision — recorded here.

## Capital-safety invariant (binds every variant)
A hot reprice must be **capital-monotonic**: committed capital may not increase
between cold (full-guard) cycles. A price move changes a leg's cost (px×sz), so a
reprice that raises cost is NOT safe to run outside the full guard stack. Rule:
a hot reprice may cancel+replace a STANDING two-sided pair only when the new
per-leg cost ≤ the standing per-leg cost; anything that would increase committed
capital defers to the next cold cycle (which runs `guards.check_place`). Never
open a market with no standing quote on the hot path.

## Three candidate architectures (this is the fork)

**(A) In-loop targeted reprice (dirty-set).** ws appliers add the ticked asset to
a thread-safe `_DIRTY` set (alongside `_WS_TICK`). The fast loop, on a hot
sub-pass, processes only markets whose asset is dirty (skipping the other ~138),
so each pass is O(dirty) not O(universe) — this lets `ws_min_s` go lower for
faster effective repricing WITHOUT a separate thread. Full passes still run on
cadence (freshness/gate/stale-cancel for ALL markets) so nothing is starved.
- SAFE: dirty markets still go through the full guard stack (capital-safe by
  construction); clean markets would have no-op'd on hysteresis anyway.
- Benefit: real only at LARGE universe (skipping 138 clean markets matters at 140,
  nothing at 3). No pilot benefit.
- Cost: moderate; a dirty-set + a "which markets to scan this pass" gate. No
  concurrency hazard. Testable.

**(B) Concurrent reprice thread (Kalshi's shape).** A separate thread reprices on
ticks between cold cycles. FASTEST, but two code paths now place/cancel orders for
the same market → double-placement / cancel-race hazard on the LIVE-CAPITAL path,
requiring per-market locking and careful ownership. This is the highest-risk
option and the one the arc's binding lessons warn hardest about.
- REJECTED for now: capital-path concurrency is not worth it pre-receipt.

**(C) Do nothing beyond Stage A.** Stage A already reprices the full cycle on a
tick down to `ws_min_s` (250 ms, tunable lower). For the pilot (3 markets) the
full cycle is trivially cheap, so Stage A already delivers sub-250 ms repricing.

## Assessment (honest, context over momentum)
- **Stage B has ZERO pilot benefit.** Its only payoff is scale-efficiency at a
  large universe; the pilot is 3 markets. Stage A already covers the pilot.
- **(B) adds live-capital concurrency risk** — exactly the class the 2 DO-NOT-SHIPs
  were paid for. Not justified before a single receipt proves the strategy earns.
- **(A) is the right Poly-native Stage B IF/WHEN we scale** — safe, no concurrency,
  reuses the full guard stack, and it's the real reason to want Stage B (skip the
  clean 138). But building it now is speculative scale-infra with no near-term use.

## RECOMMENDATION
Defer Stage B until a receipt + a scaling decision make (A) worth its build/review/
smoke cost. Ship Stage A (done, gated OFF) in the cutover bundle; that is the
latency win the pilot can actually use. If the operator wants scale-infra built
ahead of need, build **(A)** only (never (B)), default-OFF, full discipline.

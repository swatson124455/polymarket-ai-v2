# MAKER WS HOT-PATH BUILD PLAN (Poly-native port of the Kalshi WS-daemon idea)

Operator directive (S7, 07-24): build the WS event-driven reprice path "if we can,
if not touching kalshi". Feasibility: YES. Kalshi-touch: NONE — this is a Poly-native
refactor using Poly's OWN `ws_worker`/`BOOKS`; zero Kalshi files read-into or copied.
The Kalshi commit `614eb5a` is the *design reference only*; its feed
(`kalshi_ws_feed.py`) is venue-specific and is NOT ported (Poly already has a live ws
feed). Ships in the CUTOVER BUNDLE (decision 3), default OFF, review-gated.

## What already exists (why this is lower-risk than it looks)
- Poly is ALREADY ws-driven: `ws_worker` (`:655`) holds live books in `BOOKS`; the
  `run()` loop (`:1657`, `while True` at `:1856`) reprices every ~1s (`time.sleep(1)` `:2384`).
- `run()` already follows an **"extracted from run() so it's testable"** pattern:
  `commit_placements` (`:281`), `plan_quote_commit` (`:245`), `onesided_derisk_leg`
  (`:361`), `discovery_suspect` (`:572`), `collect_owned_assets` (`:1033`) are already
  module-level + tested. The heavy placement/capital/de-risk logic is ALREADY out.

## The extraction contract (Stage 0 — identity refactor, ships nothing behavioral)
Wrap the per-scan orchestration (the `while True` body, `:1857`→just before `:2384`)
into `run_once(ctx, now)` where `ctx` is a small holder of the currently-closed-over
mutable state:

    ctx = { state, universe, uni_by_ev, guards, execc, cfg, pol, meta, base,
            state_path, GEN/gen, rot_skip, timers(last_disk,last_discovery),
            st_of, cancel_market_quotes }   # exact set finalized during extraction

Loop becomes:
    while True:
        rc = run_once(ctx, time.time())     # returns a small status (halt/stop/continue)
        ctx.wait_next(rc)                    # Stage 0: == time.sleep(1), unchanged

INVARIANT (Stage 0): behavior byte-identical. Proven by: full suite green +
a differential test (same seeded state → identical placements/cancels/ledger rows
pre- vs post-extraction). Per the binding lesson, the extraction must be
mutation-tested — a broken `run_once` must fail a test, or the test pins nothing.

## Stage A — event wakeup (behind MAKER_WS_HOT, default OFF)
`ws_worker` sets a `threading.Event` on any book apply (`_apply_book_snapshot`/
`_apply_price_change`). `wait_next` becomes `event.wait(timeout=1.0)` then clear:
- flag OFF  -> falls back to the 1s timer exactly (no wakeup wired) — identical to Stage 0.
- flag ON   -> full guarded `run_once` fires on a book tick, capped by a min-interval
              floor (`MAKER_WS_MIN_MS`, default e.g. 250ms) so a tick storm can't spin.
Cold path is `run_once` VERBATIM — all guards intact (freshness, gates, caps, halt,
zombie, backoff). No new placement logic; only WHEN the same cycle runs.

## Stage B — reprice-only hot path (behind MAKER_WS_HOT, review-gated, LAST)
Optional ~200ms sub-path that ONLY adjusts price of a resting two-sided quote:
- REPRICE-ONLY: it may cancel+replace an EXISTING pair at a new price; it may NOT
  open capital in a market with no standing quote (that stays cold-path only).
- CAPITAL-SAFETY INVARIANT (ported from Kalshi's reasoning, re-proven on Poly):
  committed capital is monotonically non-increasing between cold cycles on the hot
  path — a reprice cancels the standing leg BEFORE (or atomically with) the new one,
  so gross/event/sector caps cannot be breached by hot-path activity. Differential
  test: hot-path repricing over a seeded book never increases `spent`/gross beyond
  the last cold cycle's committed level.

## Ship gates (each stage, in order)
1. tests (unit + differential + mutation) green; full suite green.
2. INDEPENDENT adversarial review (guard-with-its-caller; the reviewer must be able
   to kill a broken mutant). Stage B review is a SEPARATE round from Stage A.
3. first-output cross-check on the paper arm (isolated scratch instance, prod arm untouched).
4. bundle with the cutover deploy (decision 3) — never a standalone live push.

## Non-goals / guardrails
- No taker path — repricing is post-only maker BUYs only (unchanged).
- No behavior change with the flag OFF, ever — that is the rollback.
- No Kalshi file touched; no `base_engine` import; Maker-owned tree only.

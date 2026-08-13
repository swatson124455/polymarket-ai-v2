# R1 FLOOR PROBE — build + 8-angle adversarial review record (2026-08-13)

Artifact: `kalshi_live/r1_floor_probe.py` + `kalshi_live/test_r1_floor_probe.py`.
Review: 4 independent adversarial reviewers, 2 angles each (order-safety/economics ·
state-machine/crash-recovery · API-canon/environment · authorization-bypass/test-gaps),
all prompted to refute. Run BEFORE first commit of the script — every finding below was
fixed pre-commit; the committed version is the post-fix one. Suite after fixes:
**1370 passed / 2 xfailed, exit 0** (35 probe tests). Real CLI negative runs: `place`
w/ GO on wrong host → exit 2 (quoter gate fails closed); `halt` w/o state → exit 2,
zero cancels; `status` w/o state → exit 0.

## Findings and dispositions (deduplicated across reviewers)

BLOCKERS (all FIXED):
- **B1 `halt` was dead code**: resting read used a raw client call with the wrong path
  (missing `/trade-api/v2`) AND iterated the returned dict's keys — the script's only
  mutating safety valve crashed unconditionally. Fix: `resting_orders()` uses the
  client's proven `get_orders("resting")` wrapper and unwraps `.get("orders")`.
  Test: `test_resting_orders_uses_client_wrapper_shape`.
- **B2 `place` trusted the plan file** (stored `within_cap` flag; prices/counts sent
  verbatim) — a hand-edited or stale-tool plan could redefine what the operator's GO
  meant (e.g. $1,980 at risk vs the $20 cap). Fix: `validate_orders()` re-checks every
  row against the CONSTANTS at the mutation boundary (price bounds, y+n<1,
  count==PROBE_CT, recomputed total ≤ cap); file flags ignored.
  Tests: `test_place_refuses_adversarial_plan`, `test_validate_*` (7 cases).
- **B3 orphan-order hole**: an ambiguous write failure on a planned ticker's FIRST
  order left a live venue order invisible to `status`/`halt` (both filtered by
  *placed* tickers). Fix: state written atomically BEFORE the first order; probe
  tickers = placed ∪ planned; `client_order_id="r1probe-…"` for attribution.
  Test: `test_state_written_before_first_order`, `test_probe_tickers_unions…`.
- **B4 `halt` scope escape**: with absent/corrupt state it defaulted to cancelling
  EVERY resting order on the account, then reported success (`left` computed against
  the empty set). Fix: refuses (exit 2) with pointer to `flatten_kalshi.py` as the
  named account-wide kill switch; `left` computed against targeted tickers.
  Tests: `test_halt_refuses_without_state`, `…on_corrupt_state`, `…only_probe_tickers`.

MAJORS (all FIXED):
- **M1 halt gauge was position-blind fill cash** (Defect-13 class): the probe's own
  designed worst case (~$18 both-pairs-fill) would have fired the −$10 "loss" halt on
  zero actual loss. Fix: gauge = day EQUITY (windowed position-aware cash + inventory
  marked at rival book mid); "mark UNKNOWN" is headlined per Rule 12 and combines with
  cash for a freeze-class halt, never silently.
- **M2 `replay_fills` contract violation**: tape was windowed BEFORE replay (must
  replay from flat). Fix: full per-ticker tape replayed, events windowed after; and
  `plan` refuses any candidate with an existing position, resting order, or ANY
  historical fill (clean science + contract).
- **M3 accrual read was cumulative-not-delta and silently dropped unmapped programs**
  — either error fakes the probe's headline verdict (false REFUTED / false CONFIRMED).
  Fix: program ids resolved DIRECTLY from the venue at place time and stored in state;
  estimates baseline snapshotted at place; `status` reports delta-from-baseline with
  `accrual_basis` ∈ {DELTA_OK, PARTIAL, VOID} and alarms on anything but DELTA_OK
  ("a zero here is plumbing, NOT a floor confirmation").
- **M4 stale-anchor guard declared but unenforced** (`ANCHOR_STALE_S` unused): a
  weeks-old last_price in a quiet book is the probe's biggest avoidable adverse fill.
  Fix: ts-less last_price anchors cap BOTH quote prices at $0.20; two-sided-mid
  anchors preferred. (No last-trade timestamp exists on the market payload; the cap
  bounds the wound instead of labeling it.)
- **M5 partial placement raised a bare traceback** past a resting naked side. Fix:
  caught per-order; explicit "PARTIAL PLACEMENT — run halt" + distinct exit 4.
- **M6 state writes were non-atomic** and corrupt state made `status` exit 0 (blind)
  — fix: tmp+fsync+replace (same pattern as recorder `save_map` / ledger
  `save_state`); corrupt state now exits 2 loudly from both `status` and `halt`.
- **M7 quoter mutual-exclusion gate failed OPEN** on unknown-unit/wrong-host/degraded
  systemd. Fix: only explicit `inactive`/`failed` passes; anything else refuses with
  the raw output; missing systemctl refuses with "wrong host?". Verified by the real
  negative run above. Test: `test_quoter_gate_passes_only_inactive_or_failed`.
- **M8 status/venue-read failure exited like "no breach"**. Fix: catch → exit 2 with
  "COULD NOT VERIFY — treat as FREEZE-AND-HOLD".

MINORS (all FIXED): t0 stamped before the loop (pre-t0 snipe fills were excluded);
mixed ISO 'Z'/'+00:00' string comparisons → `parse_iso` datetimes; estimates newest-
file-empty rollover falls back one file; `halt` arming error → remediation message
exit 2 (not a traceback); `place` preamble prints the armed client module path + STOP
path; STOP re-checked between markets mid-place.

Reviewer-verified NON-defects kept on record: no liquidity-taking path exists
(post_only hard-wired at call site and client; a post_only GTC that fails to rest
raises); self-cross impossible (our NO bid rests at yes-scale ≥0.55 vs YES bid ≤0.45);
price-scale mapping single-conversion correct; `worst_case=(y+n)·ct` overstates the
true one-sided worst (conservative, kept).

## Residual risks (named, accepted for a $20 48h probe)
- TOCTOU on STOP/quoter between gate check and each order: window is seconds, ≤4
  orders, STOP re-checked between markets. Accepted.
- Post-cancel re-read in `halt` runs immediately; venue lag can print a spurious
  nonzero `left` → exit 1 (fail-noisy). Accepted.
- `KALSHI_DATA_DIR` relocates STOP/state/import-path together; mitigated by the
  preamble print of the armed module + STOP path. Accepted.
- Halt needs `KALSHI_LIVE_ARMED` to cancel: documented — arming must persist until
  the probe is flat; refusal message names the remediation.

## GO checklist (when the operator says GO, in this order)
1. scp `r1_floor_probe.py` to the box; `md5sum` vs `git show HEAD:kalshi_live/r1_floor_probe.py | md5sum` (CRLF: compare CR-stripped).
2. Copy `R0B_VENUE_CENSUS_2026-08-13.json` to the box (or re-run the census).
3. `plan` on the box (read-only) → review the printed orders + worst case (≤$20).
4. STOP file: `place` refuses while it exists — clearing it is an operator-named act.
5. `place --operator-go GO` within 30 min of the plan.
6. `status` at +1h, then +24h/+48h (the probe's read points); halt on exit 3.

## Post-review addendum (same session, after two real box runs of `plan`)
Two functional changes landed AFTER the 4-reviewer pass, each forced by a real
read-only run, each with tests (suite re-run green after each):
1. Selection: anchor-bearing candidates first, scan depth 12→40 (quietest-12 gave 0
   placeable — a dead series + anchor-less one-siders, all refused fail-closed).
2. Score discipline: JOIN AT the rival reference (N=0) or refuse when the ref exceeds
   the price cap; margin-pricing only where WE set the reference. (A clamped price 49
   ticks under ref scores DF^49≈0 — the probe would have read "floor CONFIRMED" out
   of tick discounting, not the floor.)
3. Constants: census-measured fact — 0 of 232 survivors have both refs ≤ $0.45 (quiet
   sub-target books are skewed); PRICE_MAX 0.45→0.60 with PROBE_CT 10→8 keeps the
   at-caps worst case $19.20 ≤ the $20 collateral cap. Self-cross safety rests on the
   y+n<1 invariant (unchanged, enforced at the mutation boundary).
Final real plan run (box, 15:2xZ): 2 placeable candidates, both KXTOPUSAGEAI-26AUG10
sibling strikes (sub-target BOTH sides = true zero-payer pools) — see the GO ask.

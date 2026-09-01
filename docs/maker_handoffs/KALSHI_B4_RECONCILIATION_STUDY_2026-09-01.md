# B4 RECONCILIATION STUDY — sub-Target accrual vs the CFTC two-sided exclusion (2026-09-01 ~18:0xZ)

Question (standing canon conflict since 08-25, gating the SUPPLY build): R1 probe programs
accrued on books characterized "far below Target both sides" — the filing says those
snapshots pay $0. Study run by a read-only background agent over the R1 probe archive
(133 log rows 08-13T23:45→08-19T01:50Z), estimates-202608.jsonl (7,420 rows), program map,
R0B full-walk census (08-13T14:59:56Z), D2 programs_raw, and a live incentive_programs read
(09-01).

## VERDICT
- Hypothesis (b) UNITS MISPARSE: **REFUTED.** target_size_fp is raw contracts — probe-era
  programs all "1000.00"; live read: 944×"1000.00" + 56×"300.00" of 1000 rows, min 300 max
  1000, all inside the filing bounds (100, 20,000); every rescale exits the bounds; the one
  rescale fitting the probe pattern (÷100) is killed independently by the gas-zero
  measurement.
- Hypothesis (c) RULE VARIANT: **REFUTED.** Killer row: KXYTVIEWSW-YOU26AUG16-9.0M — NO
  side alone 2,100ct ≥ Target with our probe legs resting from 08-14T00:20:07Z, and
  **0cc in every one of 1,269 feed rows** — no-exclusion and per-side variants both dead
  (gas-zero kills them from the deep-YES direction too). Positive control: UE — the only
  book both-sides ≥ Target at census — accrued in 68 consecutive hourly batches and froze
  in exactly the batch after our orders were canceled (filing-shaped on/off).
- Hypothesis (a) BOOKS WERE QUALIFIED AT ACCRUAL TIMES (breathing / stale census instant):
  **SUPPORTED by elimination + on/off signatures.** JUS+YOU estimate rows created at the
  same instant (08-14T21:19:10Z) then frozen = a transient qualification window; accrual
  rank follows book tightness, not our (identical 8ct) size. Caveat: zero contemporaneous
  depth reads exist — literal snapshot-instant breathing vs unrepresentative census instant
  is undecidable with this data; both resolve the conflict the same way.

## RECORD CORRECTION (verified by consolidator spot-check, box read ~17:5xZ)
The standing "5 of 7 probe programs accrued nonzero" is WRONG → **6 of 7**: DEEP 3,969cc /
TENC 2,642cc (agent-read) / OPEN 2,942 / NETFLIX 1,254 / UE 1,135 / JUS 12 (all four
consolidator-verified exact) / YOU 0 (consolidator-verified: max 0cc across the tape).

## CONSEQUENCES
1. **The filing's two-sided-Target exclusion rule SURVIVES as operative.** The 08-25 canon
   conflict is RESOLVED: the R1-era "sub-Target both sides" characterization came from
   census instants 9-23h stale; it was false at accrual times.
2. **QUALIFIABLE_GATE's premise stands.** The quoter comment at :3290-3296 ("R1 REFUTED
   this gate's premise live") is itself now refuted — comment correction queued as a code
   PR item (not edited in this docs commit).
3. **SUPPLY (D2) rationale is INTACT in principle** — qualification is real and binding
   (YOU's $0 on a 2,100ct single side proves both-sides matters). The measurement deadlock
   (B3) remains.
4. **Decisive next measurement (needs operator authorization — it rests orders):** one
   instrumented day on 2-3 active thin-book programs: min-size resting quotes + a full
   R4-walk depth log (cum-to-Target both sides) every 1-5 min; join qualification intervals
   to estimate increments. An increment in a non-qualifying hour refutes the rule as
   operative; increments confined to qualifying hours closes (a) conclusively. The walk
   already exists in docs/maker_handoffs/workflow_scripts/r0b_venue_census.py — it needs a
   loop + timestamped log. This doubles as the SUPPLY-precursor instrument.

Full agent report preserved in session task transcript; sources and row-level evidence
cited inline there. Bot OFF throughout; study was read-only.

# MAKER LIVE-PILOT CUTOVER SHEET — ONE PAGE, SIGN THE DECISIONS (drafted S9, 2026-09-06)

Operator signs each `[ ]` line. A broad "go" does not approve anything —
per lane rule §C.0(a), approval is of the SPECIFIC VALUES on this sheet.
Anything unsigned does not ride. Format per the Kalshi money-plan practice.

## 0. PRECONDITIONS (all must be TRUE before the GO question is even asked)
| # | check | status 2026-09-06 20:4xZ |
|---|-------|--------------------------|
| P1 | ~$98 USDC on wallet `0x9B24D25A514eE02b44DFF20F5e835585d35CE7b4`, verified on-chain | **NOT MET** — chain read 20:39Z: 5.0 POL, $0 USDC.e, $0 native USDC |
| P2 | Collateral form resolved EMPIRICALLY via balance-allowance (pUSD-vs-USDC.e; S244 canon, never guessed) | blocked on P1 |
| P3 | `--stage scoring` run from /opt/pa2-maker-live: cancel-shape probe + $3/$20 `is_order_scoring` (OPEN-C4) — output SHOWN to operator before any order rides | blocked on P1 |
| P4 | Fresh SAME-DAY softness sample at pin time (samples are perishable; the 09-06 20:39Z sample dies with the day) | re-run on GO day |
| P5 | Deployed engine md5(LF) == branch HEAD | MET — `7e97417e` verified 20:38Z |
| P6 | WS flags OFF (`MAKER_WS_HOT` stays 0 until the finding-#2 order-rate ack) | MET — unset |
| P7 | GO not within ±60 min of 00:00Z (§C.0(c)) | check at GO |
| P8 | Prune-aware recon in repo (F1 fix `1f57e67`) runs from repo at first recon | MET |

## A. KNOBS (all CONFIG, `deploy/maker-pilot-env.staged`; env applied only at GO)
- [ ] mode: `MAKER_SUBMIT_MODE=live` + ACK line
- [ ] universe: allowlist `sports,entertainment,politics` · `MAX_MARKETS=3` · `MAX_PER_SECTOR=2` (fail-closes the "unknown" gate hole)
- [ ] caps: market gross **$30** · event **$30** · sector gross **$60** → max committed **$90** vs ~$98 wallet
- [ ] loss floors: mark **$10** + realized **$10** (operator's 2026-09-01 "lose max $10 per 24h" ruling, both arms)
- [ ] behavior: `ONESIDED_DERISK=1` · `GATE_POLICY=P0_base` · `QUOTE_STYLE=wide` · clock veto **24h min / 30d max** to end
- [ ] `MAKER_SIG_TYPE`: whatever `--stage sanity` measured (empirical, never guessed)
- [ ] market picks: pinned on GO day from the fresh softness sample + engine clock veto; names + modeled **M** ($/day, MODEL tier) written into the GO message. Modeled day total MUST clear the R8 $1/day payout floor with margin (target M ≥ $3/day) or every day pays $0 by rule.

## B. PRE-REGISTERED DECISIONS (anti-tinker mandate — signed BEFORE money moves)
- [ ] **B1. First-receipt decision table** (receipt R vs pinned model M, read at `--stage receipts`):
      · R ≥ 0.25×M → CONTINUE UNCHANGED next window; record calibration row.
      · $0 < R < 0.25×M → STOP QUOTING; diagnose (calibration row + `is_order_scoring` re-probe + book re-read); re-select; resume only between windows.
      · R = $0 → STOP; defect-vs-rules decomposition BEFORE any change — first checks: was modeled day ≥ $1 (R8 floor)? sub-msz scoring (OPEN-C4)? payout timing/address (R6)?
      Post-receipt behavior is decided HERE, never improvised. (0.25 is my proposal; the operator's number rules.)
- [ ] **B2. Change freeze**: measurement window = GO instant → the `--stage receipts` read after the next 00:00Z payout. INSIDE a window nothing changes (engine, env, selection) except the operator's kill switch. Changes land only BETWEEN windows, ONE variable at a time, each with a pre-registered success/kill criterion and the operator's yes on the specific values.
- [ ] **B3. Receipts are the only optimization signal**: selection/tuning inputs = the receipt-vs-model calibration table ONLY (MASTER_PLAN 09-01 amendment, binding). `accday` is MODEL; paper G2 over-credit is known; neither tunes anything.
- [ ] **B4. Losing day → decompose first**: defect / rules-misread / structural cost, each with sources, THEN at most ONE proposed change with blast radius. Never a fix and a strategy change in the same window.
- [ ] **B5. Stop condition**: **5 consecutive $0-credit days** (with modeled ≥ $1/day each) → stop-or-change sheet to the operator; no quoting resumes until they rule. (Kalshi precedent was 7×$0; 5 proposed — operator picks N.)

## C. WORST CASE, STATED
Committed capital ceiling $90 (caps, §A). Daily loss ceiling $10 (mark arm sees
every loss incl. marks; realized arm backstops the frozen-inventory blindness,
RULE TWELVE class). Catastrophic ceiling if both arms fail = the $90 committed.
No number on this sheet is income; first income number = first receipt.

## D. SEPARATE OPEN DECISION (not a cutover item)
- [ ] Paper-arm mark floor re-size: −300 tripped 09-02 on pure mark noise
      (settle_realized was +$6.15). Rec: mark **750**, realized stays **75**.
      Today's paper marks run −$88..−$113 intraday (hb 20:38Z) — 300 remains
      one bad cluster away from another measurement blackout.

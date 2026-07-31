# KALSHI MAKER — LOSS LEDGER (per market / per family / reasons)

Bookkeeping only — touches no code. Created 2026-07-31 ~23:55Z (operator-named).
Every figure carries its source. Labels: **DEFECT** = ruled agent bug (on record),
**MECHANISM** = measured mechanism class, not yet ruled bug-vs-structural,
**ERA-AGG** = only attributed at era level, per-market split not available without the
queued ledger-method study (naive fill-sign math FAILED the identity check 2026-07-30 —
per-market numbers for old settled markets must NOT be reconstructed that way).

**Maintenance rule:** append a REWARD column entry per market as each receipt posts
(payout = day after that market closes, operator-stated 2026-07-31); re-rule MECHANISM
rows to DEFECT/STRUCTURAL only with evidence; never edit a sourced number in place —
correct by a new dated line.

## Sources
- [A] venue realized-by-market read 2026-07-31T23:46:54Z (fee-incl; covers UNSETTLED
  markets only — the feed drops settled ones)
- [B] governor/ban state quoter_state.json (values captured from the live realized feed
  pre-settlement; read 2026-07-31)
- [C] double-blind bleed review 2026-07-31 13:31:04Z (scaled-era −$182.06, 25/25
  venue-validated) + triple audit same day (mechanism buckets)
- [D] settlements API read 2026-07-31T23:48:02Z (97 events; fee_cost per market)
- [E] launch-era canon decomposition (−$122.57 basis, reconciles to $0.0000)
- [F] middle-era ledger study 2026-07-31 overnight (+$16.10; era-level)
- [G] M6 defect measurement 18:06–18:51Z 2026-07-31 (journal + fill records)

## 1. Per-market ledger — scaled era, markets closed 2026-07-31 (payout due 2026-08-01)

| Market | Realized $ | Source | Reason / ruling |
|---|---|---|---|
| KXMLABELSHARE-SME | −25.76 | [B] venue-attributed | **DEFECT** — burn-and-run governor blind spot (flat-in-one-cycle invisible to old feed; root-fixed `11fd9da`) |
| KXMLABELSHARE-UMG + -WMG | −10.88 (45-min window) | [G] | **DEFECT** — M6 incumbent exemption (mine; reversed `99556b1`). WMG portion ≈ −4.96 of the window per fill snapshot 18:28:49Z |
| KXMUSKNW-T700 | −24.82 | [B] | **MECHANISM** — churn on quiet 1c-spread book, own-fill fingerprint [C]; one-shot ≥$5 burn → permanent ban. Bug-vs-structural not ruled. Fees this market lifetime $8.20 [D] |
| KXEURUSDAW-26JUL31 (9 strikes) | −12.00 total (worst: 1.1410 −5.57, 1.1470 −1.97, 1.1430 −1.51) | [A] | **MECHANISM** — churn class [C]; 1.1410 tripped the $5 permanent rung |
| KXAAAGASD-26JUL31-4.105 | not separately attributed | [D] settled | **ERA-AGG** — sub-$5-drip bucket [C] |
| KXAPRPOTUS-26JUL31 (40.3/40.6) | not separately attributed | [D] settled | **ERA-AGG** — sub-$5-drip bucket [C] |
| KXGENERICBALLOTVOTEHUB-T5.7 | not separately attributed | [D] settled | **ERA-AGG** |

## 2. Per-market ledger — still open (lifetime realized to date, [A] 23:46:54Z)

| Market | Realized $ | Reason / ruling |
|---|---|---|
| KXTRUMPTIME-H2 | −27.27 | **MECHANISM** — churn + adverse-move classes [C]; permanent ban (trip-snap −9.20 on the day it tripped) |
| KXTOPMODEL-CLAU5 | −19.42 | **MECHANISM** — one-shot burn → permanent ban [B] |
| KXTRUMPENDORSEMENTS-A5 | −16.21 | **MECHANISM** — one-shot burn → permanent ban [B] |
| KXTRUMPENDORSEMENTS-A10 | −4.92 | MECHANISM (churn class) |
| KXAAAGASD-26AUG01 (4 strikes) | −3.99 | sub-$5 drip |
| KXTRUMPTIME-H4 | −3.29 | MECHANISM; day-latched 07-31 |
| KXAAAGASD-26AUG01-4.105 note | (−2.67 of the −3.99) | " |
| KXMCMORROWENDORSE (3) | −3.96 | sub-$5 drip |
| KXMAMDANIEO-T0 | −2.25 | sub-$5 drip |
| KXTRUMPENDORSEMENTS-A3 | −1.74 | sub-$5 drip |
| KXNHPRIMARY28-28 | −1.40 | far-market pattern (closes 2028-01-22); flat |
| KXCHINAAI-ALIB | −1.00 | sub-$5 drip |
| KXAPRPOTUS-39.2 | −0.54 | dust |
| KXCLARITYVOTE | −0.30 | dust |
| KXTRUMPUAP | −0.16 | dust |
| KXTRUMPTIME-H3 | +0.03 | — |
| KXSENATEADJOURN | 0.00 | — |
| KXACTBLUETOP, KXBABELMANDEBWEEKLY | 0.00 traded (resting only) | no fills yet |

## 3. Family rollups (sum of the sourced rows above; scaled-era only)

| Family | $ | Dominant reason |
|---|---|---|
| KXMLABELSHARE | ≈ −36.6 | **BOTH RULED DEFECTS** (burn-and-run blind spot + M6) |
| KXTRUMPTIME | −30.53 | mechanism: churn/adverse-move |
| KXMUSKNW | −24.82 | mechanism: quiet-book churn |
| KXTRUMPENDORSEMENTS-26AUG01 | −22.87 | mechanism: burns + churn |
| KXTOPMODEL | −19.42 | mechanism: one-shot burn |
| KXEURUSDAW-26JUL31 | −12.00 | mechanism: churn |
| KXAAAGASD (open strikes) | −3.99 | drip |
| all others open | ≈ −9.6 combined | drip/dust |

## 4. Era aggregates (the history that predates per-market capture)

| Era | $ | Attribution (inline per RULE SEVEN) |
|---|---|---|
| Launch (<07-25) | −122.57 | canon decomposition [E]: taker/aggressive ≈ −43.86 DEFECT + naked settlement tail ≈ −50.03 DEFECT + structural −28.68; defect share honestly 61–77% of this basis only |
| Middle 07-25..28 | **+16.10** | ledger study [F], era-level |
| Scaled (≥07-29) | −182.06 realized, fee-incl, 25/25 venue-validated [C] | mechanism buckets: churn −97.4 / adverse-move flatten −57.8 / sub-$5 drip −26.9 / halt cost −10.2 (this era's own decomposition — NOT the launch basis) |

Rewards side for context (never argue strategy from bare losses): rewards $132 lifetime
(operator UI read 07-30); structural cost is covered by rewards on the launch-basis
canon [E]. Receipts landing from 2026-08-01 add the per-market reward column here.

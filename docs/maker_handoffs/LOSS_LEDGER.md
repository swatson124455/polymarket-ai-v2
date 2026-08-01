# KALSHI MAKER — LOSS LEDGER (per market / per family / reasons)

Bookkeeping only — touches no code. Created 2026-07-31 ~23:55Z (operator-named).
Every figure carries its source. Labels: **DEFECT** = ruled agent bug (on record),
**MECHANISM** = measured mechanism class, not yet ruled bug-vs-structural,
**ERA-AGG** = only attributed at era level, per-market split not available without the
queued ledger-method study (naive fill-sign math FAILED the identity check 2026-07-30 —
per-market numbers for old settled markets must NOT be reconstructed that way).

**Maintenance rule:** append a REWARD column entry per market as each receipt posts;
re-rule MECHANISM rows to DEFECT/STRUCTURAL only with evidence; never edit a sourced
number in place — correct by a new dated line.

**DATED CORRECTION 2026-08-01 (per the no-edit-in-place rule):** the original maintenance
line above read "payout = day after that market closes, operator-stated 2026-07-31". The
first full receipt pull [H] does NOT support that model. Credits post in a daily batch at
**05:15–06:58 UTC** paying for the PRIOR DAY'S PRESENCE, including on markets that are
still OPEN. Decisive counter-example: `KXCHIPBURRITO-26AUG02` credited 2026-08-01T06:27Z —
the day BEFORE its event date, so it cannot be a post-close payout. The close+1 model and
the Aug 2–9 payout calendar derived from it are therefore SUSPECT. Reported to operator
2026-08-01; superseding model not yet operator-ruled — do not treat either as settled.

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
- [H] **credit_history API pull 2026-08-01 ~13:25Z** — `GET https://api.elections.kalshi.com/
  v1/users/{user_id}/credit_history?limit=1000`, captured by recording the Kalshi web app's
  own authenticated response (read-only). Returns `{credits:[…], cursor:null}`; cursor null
  = complete history. n=46, all `status=applied`. Fields: `amount_cents`, `reason` (event
  ticker), `created_at`, `type`. **This REFUTES the standing canon "no API reward feed
  exists (receipts+UI only, verified 07-30)"** — a machine-readable per-EVENT reward feed
  does exist; it was missed because it is a `v1` user-scoped endpoint, not on the trading
  API surface. Raw rows: `scratchpad/kalshi_credit_history_20260801.md`.
  LIMIT: `reason` names the EVENT only, never the strike — per-strike attribution is NOT
  available from this feed (nor from the UI, which shows identical event labels).

## 1. Per-market ledger — scaled era, markets closed 2026-07-31 (payout due 2026-08-01)

REWARD column added 2026-08-01 from [H]. Rewards are credited PER EVENT, so a per-strike
row shares its family's single event figure — never split it across strikes.

| Market | Realized $ | **REWARD $ [H]** | Source | Reason / ruling |
|---|---|---|---|---|
| KXMLABELSHARE-SME | −25.76 | *(event-level: see below)* | [B] venue-attributed | **DEFECT** — burn-and-run governor blind spot (flat-in-one-cycle invisible to old feed; root-fixed `11fd9da`) |
| KXMLABELSHARE-UMG + -WMG | −10.88 (45-min window) | *(event-level: see below)* | [G] | **DEFECT** — M6 incumbent exemption (mine; reversed `99556b1`). WMG portion ≈ −4.96 of the window per fill snapshot 18:28:49Z |
| **KXMLABELSHARE-W3026JUL30 (EVENT total, all 3 strikes)** | ≈ −36.6 (family, §3) | **+16.15** (3 credits 08-01T06:24Z: 12.11 / 2.93 / 1.11 — strike mapping UNAVAILABLE) | [H] | — |
| KXMUSKNW-T700 | −24.82 | **0.00** | [B], [H] | **MECHANISM** — churn on quiet 1c-spread book, own-fill fingerprint [C]; one-shot ≥$5 burn → permanent ban. Bug-vs-structural not ruled. Fees this market lifetime $8.20 [D]. **Zero reward despite being the single largest modeled earner ($48.15 frozen model) — see §5** |
| KXEURUSDAW-26JUL31 (9 strikes) | −12.00 total (worst: 1.1410 −5.57, 1.1470 −1.97, 1.1430 −1.51) | **0.00** | [A], [H] | **MECHANISM** — churn class [C]; 1.1410 tripped the $5 permanent rung. Zero reward vs $13.83 modeled |
| KXAAAGASD-26JUL31-4.105 | not separately attributed | **0.00** | [D] settled, [H] | **ERA-AGG** — sub-$5-drip bucket [C] |
| KXAPRPOTUS-26JUL31 (40.3/40.6) | not separately attributed | **0.00** | [D] settled, [H] | **ERA-AGG** — sub-$5-drip bucket [C]. Model predicted $0.96 total, i.e. under the $1 payout floor — **model called this correctly** |
| KXGENERICBALLOTVOTEHUB-T5.7 | not separately attributed | **+2.33** | [D] settled, [H] | **ERA-AGG**. Model predicted $23.45 → 10.1× over |
| KXCHIPBURRITO-26AUG02 | no loss row (not traded at loss) | **+1.14** | [H] | Credited 08-01T06:27Z, i.e. BEFORE its event date — the close+1 counter-example |

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

**DATED CORRECTION 2026-08-01 (no edit in place):** the "$132 lifetime (operator UI read
07-30)" figure above is superseded by the full API pull [H]: **$167.35 lifetime credits =
$152.35 liquidity incentive + $15.00 referral** (n=46, all applied, cursor null =
complete). The $132 was a rounded UI read taken before the 08-01 batch.

## 5. REWARD LEDGER — full lifetime, per event [H]

| Credit day | Incentive $ | Note |
|---|---|---|
| 2026-07-21 | 18.63 | |
| 2026-07-22 | 6.58 | |
| 2026-07-23 | 42.06 | best day on record |
| 2026-07-24 | 8.81 | (+ $15.00 referral, non-incentive) |
| 2026-07-25 | 11.99 | |
| 2026-07-26 | **0.00** | |
| 2026-07-27 | 20.32 | |
| 2026-07-28 | 22.45 | |
| 2026-07-29 | 1.89 | |
| 2026-07-30 | **0.00** | |
| 2026-07-31 | **0.00** | |
| 2026-08-01 | 19.62 | first receipt of the scaled era |

### Per family (of the $152.35 incentive total — denominator stated per RULE SIX)
| Family group | $ | share of $152.35 |
|---|---|---|
| Temperature (AUSH 21.12 / NYCH 17.99 / CHIH 8.21 / DCH 5.86 / LAXH 1.85) | 55.03 | 36.1% |
| KXAAAGASD (gas) | 33.04 | 21.7% |
| **Index (INXHUD 13.12 / NDQHUD 9.33 / DXYDUD 1.89)** | **24.34** | **16.0%** |
| KXMLABELSHARE | 16.15 | 10.6% |
| Club football (BTTS 8.36 / SPREAD 4.70 / TOTAL 1.18) | 14.24 | 9.3% |
| KXTRUMPENDORSEMENTS | 6.08 | 4.0% |
| KXGENERICBALLOTVOTEHUB | 2.33 | 1.5% |
| KXCHIPBURRITO | 1.14 | 0.7% |

Temp + gas + index = **$112.41 = 73.8%** of lifetime incentive.
⚠ The index families (KXINX/KXNDQ/KXDXY) are on the standing DENY list, recorded as
"TABLED NOT DEAD — operator: don't rule out the money". Measured: they produced **16.0% of
lifetime incentive across just 2 credit days (07-28, 07-29)** before exclusion. REPORTED
2026-08-01; no change proposed, DENY list untouched — operator decides.

### Receipt vs frozen model — first out-of-sample test
Model = `RECEIPT_MODEL_FROZEN_2026-07-30.json`, frozen 2026-07-30T18:46:38Z (before any
credit), so this is a genuine out-of-sample test.

| Scope | Modeled | Actual [H] | Ratio |
|---|---|---|---|
| Jul-31-closing families, aggregate (14 modeled markets) | $133.90 | $18.48 | **7.24× over** |
| KXMLABELSHARE | $47.51 (2 of 3 strikes modeled) | $16.15 (event, 3 strikes) | ~2.9× over |
| KXGENERICBALLOTVOTEHUB | $23.45 | $2.33 | 10.1× over |
| KXMUSKNW-T700 | $48.15 | **0.00** | total miss |
| KXEURUSDAW ladder | $13.83 | **0.00** | total miss |
| KXAPRPOTUS (3 strikes) | $0.96 | 0.00 | correct — under the $1 floor |

**The error is CONCENTRATED, not a uniform multiplier.** $61.98 of the $133.90 modeled
total (46.3%) came from two markets that paid exactly nothing. Excluding those two, the
remainder is ≈3.8× hot. HYPOTHESIS (not established): both zero-payers are on the permanent
`mkt_out` ban list — but KXMLABELSHARE-SME is also banned and DID pay, so a simple
"ban kills the reward" story does not hold. Unresolved.

**Consequence for sizing:** modeled reward is not usable as a per-market sizing input — it
can be ~3× hot or infinitely hot with no way to tell in advance. ALLOC_KEY and CAPRANK_CALIB
were both gated on this calibration. REPORTED; no change made.

### Reward rate across the scale-up (INFERRED — confounded, do not treat as clean)
- 07-21 → 07-28 (pre-scale, 8 days): $130.84 incentive ≈ **$16.36/day**
- 07-29 → 08-01 (scaled, 4 days): $21.51 ≈ **$5.38/day**

Confounders, all material: the market mix changed completely; the index families (16.0% of
lifetime incentive) were denied mid-window; and Aug-1 presence is not yet credited (it pays
in the 08-02 batch), so the scaled window is partly uncredited. Directional only.

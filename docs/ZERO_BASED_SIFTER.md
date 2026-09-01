# ZERO-BASED SIFTER - charter (2026-08-25)

**Operator directive: "redo sifter with assumption all prior info is
wrong."** This pipeline inherits NOTHING strategic. Voided for this
pipeline: the copy-skilled-whales premise, the winners-only orientation,
the first-buy/hold-to-resolution estimand as sole estimand, the 0.65-0.85
band, every scout-filter number ($25k / 5 markets / 10-250 trades), the
1,000 fills/day bot line, all qualification bars, and the one-afternoon
(2026-07-30, 6h) candidate universe. Retained: ONLY chain/venue MECHANICS
proven against ground truth (subscribe shape, keepalive, fee formula,
label pipeline, the canon verifier, the e-process math).

## Stage 0 - ORE (build: scripts/firehose_recorder.py)
Record EVERY venue trade, all wallets, both sides, multi-day, daily-rotated
gzip, disk-guarded. No filters. Minimum before any sieve is derived:
**7 full days** (captures weekday/weekend + all hours; the old universe
was one afternoon).

## Stage 1 - POPULATION STUDY (no thresholds allowed in, only out)
Over the full capture, per wallet: trades/day, active days, median/total
size, market breadth, side mix, inter-trade spacing, burstiness, category
mix. Deliverable = DISTRIBUTIONS (percentile tables + cluster structure),
not verdicts. Every downstream cut names its percentile and shows a
sensitivity band (cut at p90 vs p95 vs p99 -> how the pool changes).

## Stage 2 - FOLLOWABILITY (mechanical, measured, not judged)
A wallet is followable iff our MEASURED detection+quote latency
(quote_lag_s now recorded per signal) is small relative to that wallet's
observed inter-trade spacing and its markets' price half-life. Pure
physics: can we react before the information is gone. No skill judgment.

## Stage 3 - TWO-SIDED SLUICE (the sifter)
Every followable wallet gets TWO anytime-valid e-processes:
  COPY-score:  H0 copy-edge <= 0 on the wallet's followed entries
  FADE-score:  H0 fade-edge <= 0 (the mirror bet)
under MULTIPLE pre-registered estimands in parallel (first-buy-hold;
all-buys; with-exits once SELL recording ships), canon fees, canon labels.
A reliably WRONG wallet is as valuable as a reliably right one.
e-thresholds and futility for this pipeline are set AFTER stage 1 from the
observed e-distributions (with the FPR analysis shown), not inherited.

## Stage 4 - ASSAY (expensive, last, few)
Fraud/integrity deep-dive ONLY for wallets whose sluice score is alive,
trigger percentile derived from stage 3's observed distribution. Nothing
reaches live capital without assay + operator composition go.

## Non-negotiables carried over (measurement law, not strategy)
Pre-registration before looking; immutable locks; forward data only;
canon estimand/fee/label functions imported never re-implemented; every
cut discloses its percentile + sensitivity; the daily canon verifier must
read ALARMS=0 for any of this to be quotable.

## Relationship to the running instruments
The 36 live trials + band test CONTINUE untouched (clean forward data,
zero cost; killing them would burn accrual). They are the OLD pipeline's
tests; this charter governs the NEW pipeline. If both point the same way,
confidence compounds; if they diverge, that is itself a finding.

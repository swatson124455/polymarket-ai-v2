# WB S222 Post-Fix Verification Prompt

**Purpose:** copy-paste prompt for a VPS-access session, to A/B the deployed S222
fixes against the pre-fix baseline captured 2026-07-02 and decide gate retirement.

**S229 re-point (2026-07-13):** this prompt RAN on the 07-11→07-13 window (77 resolved
markets) and returned **FAIL on every criterion — nothing retired**. Root cause found the
same day: that window traded on a poisoned global EMOS corrector (mixed °C/°F climatology
pooling — see `WEATHER_STATUS.md` OPEN DECISION 2b), fixed + deployed in release
`20260713_160143` @ `24b2847`, effective restart **2026-07-13 16:02:29Z**. For the NEXT
run, substitute cutoffs throughout: window start `2026-07-13 16:02:29` (SQL) /
`--since 20260713_160229` (scripts), deployed release `20260713_160143` or later, and add
a precondition: `grep -c 'S229' <deployed>/bots/weather_bot.py` must be ≥9 and the journal
must show `weather_global_emos_by_station_loaded` (stations≈40) after each ~6h reload —
if the pooled `avg_clim_mean` field EVER reappears in `weatherbot_global_samos_fitted`,
the defect is back; STOP. Everything else below applies unchanged (read the old 00:47
cutoffs as 16:02:29).

**S227 re-point (2026-07-11):** the verification window now starts at the **S227 fix
deploy** (release `20260711_002634`, effective service restart **2026-07-11 00:47:00Z**).
Why the restart moved twice:
- The original post-S224 window (07-08 →) is **DISCARDED for verification**: S227 found
  that a str-vs-datetime bind crashed every confidence-calibrator fit AND every
  EMOS/bias/tail calibration reload since the 07-08 deploy (journal-proven: 0
  `weatherbot_calibration_reloaded`, 1109 `cal_fit_failed`). That whole window traded
  without calibration while the 07-02 baseline had it working — not comparable.
- Rows written 2026-07-11 00:26→00:47 are ALSO old-code output (the release stamp is
  00:26 but the first cut crash-looped on a missing `data/` skeleton and the box ran
  the rolled-back old release until the repaired flip at ~00:46; startup at 00:47:11).
  Tarball stamp ≠ restart time — third occurrence, encoded on day one this time.
- Post-fix proof-of-life (2026-07-11 00:48): `weatherbot_calibration_reloaded`
  (41 stations / 571 rows, 2 EMOS-ready), `cal_fit` path executing
  (`insufficient_data n=0 need=200`), both failure counters 0.

**Preconditions before firing:** (1) deployed release is `20260711_002634` or later
with S227 markers present, (2) ≥50 DISTINCT resolved markets in the clean window
(Precondition 0.4a). The prompt self-aborts if either fails. ETA to the gate:
~2.5–3 days from 2026-07-11 at the observed ~19 distinct resolutions/day.

**Pre-fix baseline reference (2026-07-02, release 20260701_144329, PAPER mode):**
PIT KS stat 0.155 / p<1e-4 / mean 0.563 / U-shaped · traded-subset Brier 0.2907,
BSS −0.163 · high-conf reliability gaps (traded) ≈ −0.12/−0.34/−0.30 in
[0.7-0.8)/[0.8-0.9)/[0.9-1.0) · side×price Brier: NO 80-100¢ 0.11 (best),
NO 0-20¢ 0.43 / YES 0-20¢ 0.44 (worst) · per-confidence-bin realized WR far
below stated in every bin (0.90+ → 56%; 0.55-0.59 → 17%, canonical bot_pnl.py)
· `weather_tail_calibration` 0 rows · calibrator fitted (n_no=153, n_yes=72,
oos 0.281 vs raw 0.308).

**Baseline-comparability note:** the baseline system had a FITTED calibrator; the
post-S227 system starts with an identity calibrator that re-learns as clean samples
accumulate (and its fit window carries 53 leak-era entries until ~2026-08-07). The
A1/A3 raw-probability verdicts (PIT, reliability) are about the RAW pipeline and
remain comparable; treat calibrator-dependent observations as context, not verdicts.

---

## PROMPT (copy from here down)

```
WeatherBot POST-FIX verification — A/B vs the pre-fix baseline (read-only)

The 5 S222 root-cause fixes (commits 3a3fd20, 3b71e54, b1892b1, 2783708, 9e3a288)
+ 2 repaired measurement scripts + the S223/S224/S225/S226 batches + the S227
calibrator-crash fix are deployed (release 20260711_002634 or later). This re-runs
the same measurements captured as the PRE-FIX baseline (2026-07-02), to decide
whether the YES/NO price caps + confidence dampeners can start being retired.

Counting unit: DISTINCT RESOLVED MARKETS, not per-log rows (S225: one long-open
market logged 42x manufactured a fake "confidence inversion"; always pass
--dedup-markets to calibration_check).

Protocol 11 / operator rule #11: return NO dollar/stake/$-per-trade figures.
Brier / BSS / reliability / PIT / KS / accuracy = calibration metrics (return with
source). Win-rate + counts = source from canonical bot_pnl.py, not the trade_events
brier scripts. Everything read-only; clean up temp files; modify nothing.

── PRECONDITION 0 — prove this is actually POST-FIX (do NOT skip) ──────────
  1. Deployed WB release:  readlink -f /opt/polymarket-ai-v2-weather
     Report the release stamp; confirm it is 20260711_002634 or later.
  2. Code fingerprint (the fixes must be physically present in the deployed release):
     grep -n "S222 A1" <deployed>/bots/weather/engine/base_engine/weather/probability_engine.py
     grep -n "S222 A3" <deployed>/bots/weather/engine/base_engine/weather/probability_engine.py
     grep -c "S227" <deployed>/bots/weather_bot.py    # must be >=4 (calibrator fix present)
     If any marker is ABSENT → the fixes are NOT deployed; STOP and report that.
  3. Effective config from the running bot:  read /proc/<wb_pid>/environ
     Report effective WEATHER_VARIANCE_INFLATION_FACTOR (default 1.4) — the A1 knob.
  4. WINDOW INTEGRITY + gate counts (all SQL via: sudo -u postgres psql polymarket -f -):
     4a. CLEAN-window gate (THE gate — post-S227-restart code only):
         SELECT count(DISTINCT market_id) FILTER (WHERE resolution IS NOT NULL)
         FROM prediction_log
         WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-11 00:47:00';
         If <50 → measurements are premature; report the count and the ETA to 50,
         then STOP.
     4b. Calibration alive check (the S227 regression guard):
         journalctl -u polymarket-weather --since "24 hours ago" | grep -c weatherbot_calibration_reloaded   (expect >=1 per ~6h)
         journalctl -u polymarket-weather --since "24 hours ago" | grep -c weatherbot_calibration_reload_failed  (expect 0)
         journalctl -u polymarket-weather --since "24 hours ago" | grep -c weatherbot_confidence_cal_fit_failed  (expect 0)
         If reload_failed or fit_failed is nonzero → calibration is crashing again;
         STOP and report the error lines.
     4c. Leak-regression check (must be ZERO — the tripwire is also live):
         SELECT count(*) FROM prediction_log
         WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-11 00:47:00'
           AND predicted_prob >= 0.9995;
         If nonzero → REGRESSION of the closed manufactured-certainty leak; STOP,
         report rows + journalctl grep 'weatherbot_impossible_certainty'.
     4d. PSW frame-ambiguity count (should be structurally 0 — all post-window rows
         are post-migration-080 and prob_frame-labelled; nonzero = labelling broke):
         SELECT count(*) FROM prediction_log
         WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-11 00:47:00'
           AND model_name IN ('weather_precipitation','weather_snowfall','weather_wind')
           AND prob_frame IS NULL AND resolution IS NOT NULL;

── MEASUREMENTS (only if Precondition 0 passes) ───────────────────────────
Cutoff at the S227 restart so no dead-calibration data leaks in.
  a. PYTHONPATH=. python scripts/calibration_check.py WeatherBot --since 20260711_004700 --clean --dedup-markets
  b. PYTHONPATH=. python scripts/weather_brier.py <days>
     # <days> = full days since 2026-07-11 00:47Z, ROUNDED DOWN (so the window
     # cannot reach back past the restart). State the days value used.
  c. python scripts/weather_brier_by_side.py <hours>
     # <hours> = hours since 2026-07-11 00:47Z, ROUNDED DOWN. State it.
  d. PYTHONPATH=. python scripts/bot_pnl.py WeatherBot <hours>   # canonical WR/counts/conf-bins; same <hours>
(The repaired brier scripts are IN the deployed release — no /tmp copies needed.)

── RETURN: A/B table, post-fix value vs the pre-fix baseline ──────────────
  - PIT: KS stat + p-value, PIT mean, histogram shape.       (baseline: 0.155, p<1e-4, mean 0.563, U-shaped)
  - Overall BSS + Brier on the TRADED subset (step b).       (baseline: Brier 0.291, BSS −0.163)
  - High-confidence reliability gaps [0.7-0.8)/[0.8-0.9)/[0.9-1.0). (baseline ≈ −0.12/−0.34/−0.30)
  - Per (side × lead-time) Brier + BSS.
  - Per (side × price-bucket) Brier (step c).                (baseline: NO 80-100¢ 0.11 best; 0-20¢ cells ~0.43-0.44 worst)
  - Per-confidence-bin realized WR vs stated (step d).       (baseline: 0.90+ → 56%; 0.55-0.59 → 17%)
  - Calibrator status: REPORT (fitted, n_no/n_yes, oos vs raw_oos Brier) but do
    NOT judge or gate on it — it re-learns from zero starting 2026-07-11 and its
    fit window carries 53 leak-era entries until ~2026-08-07 (self-clears).
    Do NOT touch it regardless of the verdict.

── GATE-RETIREMENT PASS/FAIL (state each verdict explicitly) ──────────────
N = distinct resolved markets in the CLEAN window (Precondition 0.4a).
Retire NOTHING unless its criterion is met on N>=50:
  * A1/A3 effectiveness: PASS if PIT KS p-value RISES AND |high-conf reliability
      gap| shrinks vs baseline. FAIL/PARTIAL if PIT still U-shaped with p<0.05 →
      VIF 1.4 insufficient; recommend raising WEATHER_VARIANCE_INFLATION_FACTOR
      (Tier-1 env tune) and re-measuring BEFORE any gate comes off.
  * YES/NO price dampeners may retire only if: traded-subset BSS > 0 AND
      per-confidence-bin realized WR within ~10pp of stated in the 0.70+ bins.
  * YES/NO max-entry-price caps may retire only if the favorite-buying Brier skew
      is gone (NO 80-100¢ no longer dramatically better than the 0-20¢ cells).
  * Flat-size → Kelly re-enable (C0): ONLY if the [0.9,1.0) bin is no longer
      anti-calibrated (realized WR >= ~0.85) AND PIT KS no longer rejects. Last —
      and even then it stays DEFERRED until the calibrator re-learn verdict
      (WEATHER_STATUS.md OPEN DECISION #1) is in.

Report each as PASS / PARTIAL / FAIL with the numbers behind it. If Precondition 0
fails, the entire answer is just "fixes not deployed / window too narrow" + counts.
```

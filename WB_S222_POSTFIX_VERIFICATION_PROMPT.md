# WB S222 Post-Fix Verification Prompt

**Purpose:** copy-paste prompt for a VPS-access session, to A/B the deployed S222
fixes against the pre-fix baseline captured 2026-07-02 and decide gate retirement.

**S227 true-up (2026-07-10):** the substrate changed again at the S224 deploy
(2026-07-08), so the verification window starts THERE, not at the original S222
release. Operator confirmed the ≥50 gate: **50 distinct resolved markets** at
cutoff `2026-07-08 15:13:30` (counted 2026-07-10). Three window hazards are now
encoded below — do not skip them:
- **Stamp ≠ restart (S226 gotcha #3):** the S224 tarball stamp is 15:13:30Z but the
  service restarts were **18:08:41 / 19:18:38** (journal-confirmed; deploy record
  19:18:44Z). Rows between 15:13:30 and 19:18:38 are PRE-fix code output and include
  known `predicted_prob = 1.0` leak rows (last at 17:59:38). **The verdict window
  therefore starts at `2026-07-08 19:18:38`.**
- **PSW frame ambiguity:** `weather_precipitation/snowfall/wind` rows written before
  migration 080 went live (release `20260710_204822`, ~2026-07-10 20:48Z) are
  frame-ambiguous (`prob_frame IS NULL`). calibration_check reads predicted_prob +
  resolution directly, so any such resolved rows in the window pollute PSW cells.
- **Known log outage:** prediction logging was silently DOWN 2026-07-10
  20:12→20:48Z (S226 hotfix `535ec86`). A row gap there is a known outage, NOT a
  data regression.

**Preconditions before firing:** (1) deployed release is `20260710_204822` or later,
(2) ≥50 DISTINCT resolved markets in the CLEAN window (Precondition 0.4b). The
prompt self-aborts if either fails.

**Pre-fix baseline reference (2026-07-02, release 20260701_144329, PAPER mode):**
PIT KS stat 0.155 / p<1e-4 / mean 0.563 / U-shaped · traded-subset Brier 0.2907,
BSS −0.163 · high-conf reliability gaps (traded) ≈ −0.12/−0.34/−0.30 in
[0.7-0.8)/[0.8-0.9)/[0.9-1.0) · side×price Brier: NO 80-100¢ 0.11 (best),
NO 0-20¢ 0.43 / YES 0-20¢ 0.44 (worst) · per-confidence-bin realized WR far
below stated in every bin (0.90+ → 56%; 0.55-0.59 → 17%, canonical bot_pnl.py)
· `weather_tail_calibration` 0 rows · calibrator fitted (n_no=153, n_yes=72,
oos 0.281 vs raw 0.308).

---

## PROMPT (copy from here down)

```
WeatherBot POST-FIX verification — A/B vs the pre-fix baseline (read-only)

The 5 S222 root-cause fixes (commits 3a3fd20, 3b71e54, b1892b1, 2783708, 9e3a288)
+ 2 repaired measurement scripts are deployed (along with the S223/S224/S225/S226
batches — the deployed release should be 20260710_204822 or later). This re-runs
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
Do not trust any number below as "post-fix" until the DEPLOYED code contains the
fixes AND enough post-restart resolutions exist.
  1. Deployed WB release:  readlink -f /opt/polymarket-ai-v2-weather
     Report the release stamp; confirm it is 20260710_204822 or later.
  2. Code fingerprint (the fixes must be physically present in the deployed release):
     grep -n "S222 A1" <deployed>/bots/weather/engine/base_engine/weather/probability_engine.py
     grep -n "S222 A3" <deployed>/bots/weather/engine/base_engine/weather/probability_engine.py
     grep -n "S222" <deployed>/bots/weather_bot.py | head
     grep -c "S226" <deployed>/bots/weather_bot.py    # must be >=18 (latest batch present)
     If any marker is ABSENT → the fixes are NOT deployed; STOP and report that.
  3. Effective config from the running bot:  read /proc/<wb_pid>/environ
     Report effective WEATHER_VARIANCE_INFLATION_FACTOR (default 1.4) — the A1 knob.
  4. WINDOW INTEGRITY + gate counts (all SQL via: sudo -u postgres psql polymarket -f -):
     4a. Stamp-window gate (context only — this is the count the operator tracked):
         SELECT count(DISTINCT market_id) FILTER (WHERE resolution IS NOT NULL)
         FROM prediction_log
         WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-08 15:13:30';
         (Operator got 50 on 2026-07-10.)
     4b. CLEAN-window gate (THE gate — post-restart code only):
         SELECT count(DISTINCT market_id) FILTER (WHERE resolution IS NOT NULL)
         FROM prediction_log
         WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-08 19:18:38';
         If <50 → measurements are premature; report BOTH counts (4a vs 4b) and the
         ETA to 50, then STOP.
     4c. Leak-regression check (must be ZERO — the tripwire is also live):
         SELECT count(*) FROM prediction_log
         WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-08 19:18:38'
           AND predicted_prob >= 0.9995;
         If nonzero → REGRESSION of the closed manufactured-certainty leak; STOP,
         report rows + journalctl grep 'weatherbot_impossible_certainty'.
     4d. PSW frame-ambiguity count (caveat, not a blocker):
         SELECT count(*) FROM prediction_log
         WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-08 19:18:38'
           AND model_name IN ('weather_precipitation','weather_snowfall','weather_wind')
           AND prob_frame IS NULL AND resolution IS NOT NULL;
         Report the count. If >~5% of resolved rows in the window, flag every
         PSW-containing cell in the results as PARTIAL (frame-ambiguous rows inside).

── MEASUREMENTS (only if Precondition 0 passes) ───────────────────────────
Cutoff at the REAL restart so no pre-fix data leaks in.
  a. PYTHONPATH=. python scripts/calibration_check.py WeatherBot --since 20260708_191838 --clean --dedup-markets
  b. PYTHONPATH=. python scripts/weather_brier.py <days>
     # <days> = full days since 2026-07-08 19:19Z, ROUNDED DOWN (so the window
     # cannot reach back past the restart). State the days value used.
  c. python scripts/weather_brier_by_side.py <hours>
     # <hours> = hours since 2026-07-08 19:19Z, ROUNDED DOWN. State it.
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
    NOT judge or gate on it — it is mid-re-learn toward identity (S224 reset) and
    mildly contaminated by 53 leak-era entries until ~2026-08-07 (self-clears).
    Do NOT touch it regardless of the verdict.

── GATE-RETIREMENT PASS/FAIL (state each verdict explicitly) ──────────────
N = distinct resolved markets in the CLEAN window (Precondition 0.4b).
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
      (WEATHER_STATUS.md OPEN DECISION #1, ~2026-08-07) is in.

Report each as PASS / PARTIAL / FAIL with the numbers behind it. If Precondition 0
fails, the entire answer is just "fixes not deployed / window too narrow" + counts.
```

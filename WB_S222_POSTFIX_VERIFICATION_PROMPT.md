# WB S222 Post-Fix Verification Prompt

**Purpose:** copy-paste prompt for a VPS-access session, to A/B the deployed S222
fixes against the pre-fix baseline captured 2026-07-02 and decide gate retirement.

**Preconditions before firing:** (1) the S222 fixes are cut into a deployed
weather splinter release (merge `claude/new-whiteboard-session-9b23tq` →
local `wb/main` worktree → deploy.sh), (2) ≥50 post-deploy resolved predictions
(~1 week). The prompt self-aborts if either fails.

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
+ 2 repaired measurement scripts should now be in a deployed weather release. This
re-runs the same measurements captured as the PRE-FIX baseline (2026-07-02), to
decide whether the YES/NO price caps + confidence dampeners can start being retired.

Protocol 11 / operator rule #11: return NO dollar/stake/$-per-trade figures.
Brier / BSS / reliability / PIT / KS / accuracy = calibration metrics (return with
source). Win-rate + counts = source from canonical bot_pnl.py, not the trade_events
brier scripts. Everything read-only; clean up temp files; modify nothing.

── PRECONDITION 0 — prove this is actually POST-FIX (do NOT skip) ──────────
Do not trust any number below as "post-fix" until the DEPLOYED code contains the
fixes AND enough post-deploy resolutions exist.
  1. Deployed WB release:  readlink -f /opt/polymarket-ai-v2-weather
     Report the release stamp; confirm it postdates the pre-fix release 20260701_144329.
  2. Code fingerprint (the fixes must be physically present in the deployed release):
     grep -n "S222 A1" <deployed>/bots/weather/engine/base_engine/weather/probability_engine.py
     grep -n "S222 A3" <deployed>/bots/weather/engine/base_engine/weather/probability_engine.py
     grep -n "S222" <deployed>/bots/weather_bot.py | head
     If these markers are ABSENT → the fixes are NOT deployed; STOP and report that.
  3. Effective config from the running bot:  read /proc/<wb_pid>/environ
     Report effective WEATHER_VARIANCE_INFLATION_FACTOR (default 1.4) — the A1 knob.
  4. Post-deploy resolution count (need >=50 for a verdict):
     SELECT count(*) FILTER (WHERE resolution IS NOT NULL)
     FROM prediction_log
     WHERE bot_name='WeatherBot' AND prediction_time >= '<deploy_stamp>';
     If <50 → measurements are premature; report the count and ETA to 50, stop.

── MEASUREMENTS (only if Precondition 0 passes) ───────────────────────────
Cutoff at the deploy stamp so no pre-fix data leaks in.
  a. PYTHONPATH=. python scripts/calibration_check.py WeatherBot --since <deploy_stamp> --clean
  b. PYTHONPATH=. python scripts/weather_brier.py <days-since-deploy>
  c. python scripts/weather_brier_by_side.py <hours-since-deploy>
  d. PYTHONPATH=. python scripts/bot_pnl.py WeatherBot <hours>   # canonical WR/counts/conf-bins
(The repaired brier scripts are IN the deployed release now — no /tmp copies needed.)

── RETURN: A/B table, post-fix value vs the pre-fix baseline ──────────────
  - PIT: KS stat + p-value, PIT mean, histogram shape.       (baseline: 0.155, p<1e-4, mean 0.563, U-shaped)
  - Overall BSS + Brier on the TRADED subset (step b).       (baseline: Brier 0.291, BSS −0.163)
  - High-confidence reliability gaps [0.7-0.8)/[0.8-0.9)/[0.9-1.0). (baseline ≈ −0.12/−0.34/−0.30)
  - Per (side × lead-time) Brier + BSS.
  - Per (side × price-bucket) Brier (step c).                (baseline: NO 80-100¢ 0.11 best; 0-20¢ cells ~0.43-0.44 worst)
  - Per-confidence-bin realized WR vs stated (step d).       (baseline: 0.90+ → 56%; 0.55-0.59 → 17%)
  - Calibrator status (fitted, n_no/n_yes, oos vs raw_oos Brier).

── GATE-RETIREMENT PASS/FAIL (state each verdict explicitly) ──────────────
Retire NOTHING unless its criterion is met on N>=50 post-fix resolutions:
  * A1/A3 effectiveness: PASS if PIT KS p-value RISES AND |high-conf reliability
      gap| shrinks vs baseline. FAIL/PARTIAL if PIT still U-shaped with p<0.05 →
      VIF 1.4 insufficient; recommend raising WEATHER_VARIANCE_INFLATION_FACTOR
      (Tier-1 env tune) and re-measuring BEFORE any gate comes off.
  * YES/NO price dampeners may retire only if: traded-subset BSS > 0 AND
      per-confidence-bin realized WR within ~10pp of stated in the 0.70+ bins.
  * YES/NO max-entry-price caps may retire only if the favorite-buying Brier skew
      is gone (NO 80-100¢ no longer dramatically better than the 0-20¢ cells).
  * Flat-size → Kelly re-enable (C0): ONLY if the [0.9,1.0) bin is no longer
      anti-calibrated (realized WR >= ~0.85) AND PIT KS no longer rejects. Last.

Report each as PASS / PARTIAL / FAIL with the numbers behind it. If Precondition 0
fails, the entire answer is just "fixes not deployed / window too narrow" + counts.
```

# HANDOFF — S222 WeatherBot Root-Cause Fixes + Deploy

**Session:** WeatherBot (WB) silo · **Branch:** `claude/new-whiteboard-session-9b23tq`
**HEAD at handoff:** `012649f` · **Status:** ✅ deployed to production paper trading, healthy
**Date:** 2026-07-05 · **Mode:** PAPER (`SIMULATION_MODE=true`) — treated as live per CLAUDE.md

> **⚠ S223 ADDENDUM (2026-07-06): the verification clock restarts at release `20260706_110300`.**
> The S222 post-fix window gathered **zero data**: WeatherBot had logged no predictions
> since 2026-07-03 08:24 UTC. Three root causes, all fixed + deployed in release
> `20260706_110300` (started 15:08:00 UTC, NRestarts=0):
> ① **Dead Gamma tag** — discovery queried retired `temperature` (1 stale event) instead
> of `daily-temperature`; markets=1/scan, thin-filter dropped it → nothing to predict
> (`e0d476e`). Post-fix: events=156 / markets=1716 / kept=296.
> ② **DB semaphore slot leak** — `_SemaphoreSession.__aenter__` leaked its slot when
> session creation failed; a 07-03 03:00 storm drained all 15 slots → 54h wedge, scans
> never completed, heartbeat frozen (`c61a712`, BOTH engine trees).
> ③ **Watchdog crash-loop race** — E1 force-exit read the ancient heartbeat at boot and
> os._exit(1)'d before the first scan; 6 crash-loops on 07-05 (`4170a8c`, 10-min startup
> grace, alert still fires).
> **Run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` with cutoff `2026-07-06 15:08:00` once
> ≥50 post-deploy resolutions exist (~1 week from 07-06).** Also note: release stamps are
> operator-machine LOCAL time; the VPS journal is UTC (the "10:21 vs 14:23" gap on 07-05
> was timezone, not a missed restart). Cherry-pick proposals for MB (shared modules,
> splinter-deployed only): `c61a712` (database.py) + `4170a8c` (main.py watchdog grace).

---

## 0. TL;DR for the next session

WeatherBot was **accurate but leaking edge** (high hit-rate, negative realized edge). Root cause: the raw probabilities are **overconfident/over-dispersed** (worst on the YES/tail side), and the payoff structure throws the edge away. This session **fixed 5 root causes**, repaired the 2 broken measurement scripts, left all safety gates ON as containment, captured a pre-fix baseline, and **deployed to the live weather splinter**. 

**The only open work is time-gated:** wait ~1 week for ≥50 resolved predictions on the new code, then run the verification prompt and retire gates on the result. **Do not start new WeatherBot changes until that verdict is in** — you'd be tuning blind.

---

## 1. What is LIVE right now

- **Deployed:** yes. New release cut 2026-07-05 to `/opt/pa2-weather-releases/<stamp>`, symlink `/opt/polymarket-ai-v2-weather` flipped, `polymarket-weather.service` restarted.
- **Health confirmed:** `systemctl is-active` = `active`, fresh PID, 2500 markets loaded, WebSocket subscribed to 1000 price streams, no tracebacks. Code fingerprint `grep -c S222 bots/weather_bot.py` = **15** on the deployed release.
- **Previous release (rollback target):** `/opt/pa2-weather-releases/20260701_144329`.
  Rollback one-liner (from a machine with the deploy key):
  `ssh ubuntu@18.201.216.0 "sudo ln -sfn /opt/pa2-weather-releases/20260701_144329 /opt/polymarket-ai-v2-weather && sudo systemctl restart polymarket-weather"`

---

## 2. The diagnosis (why these fixes exist)

Confirmed by adversarial re-verification (8 findings CONFIRMED, 2 ADJUSTED-stronger, 0 REFUTED) **and** a live pre-fix baseline from the VPS (2026-07-02):

- **Over-dispersion / overconfidence** is real and severe: PIT U-shaped, KS rejects uniformity (p<1e-4), PIT mean 0.563 (too extreme), BSS negative on every meaningful-N segment (worse than climatology). Reliability curve inverted (high-confidence bins wildly over-predict).
- **YES side worse than NO:** YES lives in the distribution tails, exactly where overconfidence is worst, and the layers meant to correct it were dead/absent.
- **Favorite-buying funnel:** on multi-bucket temperature markets most buckets are cheap, so the bot buys **NO at high prices** — high win-rate is mechanically guaranteed (break-even ≈ price), not alpha. Brier is best where it buys heavy favorites, worst on longshots.
- **Payoff-blind sizing:** flat $100 sizing bypasses Kelly (the only payoff-aware stage), so wins are small and losses are large.

**IMPORTANT (operator directive, hardcoded CLAUDE.md Forbidden Pattern #11):** never quote P&L — dollar P&L, stake, realized gains, raw "edge %", or win-rate-framed-as-profit. Communicate quality via **calibration** (Brier/reliability/PIT) and hit-rate on the underlying prediction only. bot_pnl.py may be run for internal diagnosis but its dollar/edge outputs must NOT be surfaced.

---

## 3. The 5 root-cause fixes (each has a defect-reproducing test; 247 tests pass)

All fixes went to the **vendored copy the deployed bot imports**: `bots/weather/engine/base_engine/weather/**` and `bots/weather_bot.py`. The top-level `base_engine/weather/**` duplicate is now DIVERGED and is shared/MB-priority — sync it only with operator+MB authorization.

| Commit | Fix | What it does |
|--------|-----|--------------|
| `3a3fd20` | **METAR at_or_below 0.97 push removed** | It fired pre-dawn (lead_time anchors to 18:00 UTC, so <12h opens before daytime heating), asserting "max stays below ceiling" and manufacturing near-certain YES probs + fake sibling NO edges via renormalization. Only branch not gated by the <2h aggressive flag. |
| `3b71e54` | **METAR at_or_higher 0.001 rule-out removed** | Same non-monotone class, NO side. |
| `b1892b1` | **Calibrator rejection stale-state (B2a)** | On Brier/OOS rejection the calibrator set `_fitted=False` but kept `_model_yes`; `calibrate()` gates on `_fitted` (inert) while the S159 0.85× YES dampener gated on `_model_yes is None` (suppressed) → YES got NEITHER. Fix: `_clear_models()` on rejection + dampener hardened to `_fitted AND _model_yes`. |
| `2783708` | **A1: variance inflation on empirical path** | The dominant ≥50-member empirical CDF route applied ZERO underdispersion correction (parametric path applies VIF 1.4). Now inflates member deviations around the mean (`m→mean+VIF*(m-mean)`), shape-preserving. Root of YES tail overconfidence. |
| `9e3a288` | **A3: EMOS train/serve mismatch** | `_fit_emos` trains on `forecast_temp` = deterministic high (NBM/local/GFS), but serve applied (a,b) to the ensemble mean → systematic location bias. Now passes `deterministic_high` to all 4 call sites; empirical path uses a uniform shift (removed silent b<1 spread-narrowing). |

**Script repairs** (both were measuring columns the bot never writes → always returned zero/garbage):
- `f1faad5` `weather_brier.py` — filtered on `predicted_probability` (never populated); now scores `confidence` column, side-attributed, dual-side markets excluded.
- `aaaa8de` `weather_brier_by_side.py` — read `event_data->>'confidence'` (never written); now reads the `confidence` COLUMN; dedup + dual-side exclusion.

**Docs/directives:** `db10ce3` `3718458` `6fca08b` (P&L directive #11 + conflict resolution + factual corrections), `61cb348` (stale-comment cleanup incl. flagging `WEATHER_COMBINED_BOOST_CAP` as dead config), `fd6f456` (persisted the verification prompt).

---

## 4. PENDING WORK — exact next steps

### Step A — (after ~1 week / ≥50 resolved predictions) run the POST-FIX verification
The prompt is committed at **`WB_S222_POSTFIX_VERIFICATION_PROMPT.md`** (repo root). Run it in a VPS-access session. It self-aborts if the fixes aren't actually deployed (code fingerprint) or if <50 resolutions. It returns **PASS / PARTIAL / FAIL per gate** against the pre-fix baseline.

**Pre-fix baseline for the A/B (2026-07-02, calibration metrics only, no P&L):** PIT KS stat 0.155 / p<1e-4 / mean 0.563 / U-shaped · traded-subset Brier 0.291, BSS −0.163 · high-conf reliability gaps ≈ −0.12/−0.34/−0.30 in [0.7-0.8)/[0.8-0.9)/[0.9-1.0) · side×price Brier: NO 80-100¢ best, 0-20¢ cells worst · per-confidence-bin realized WR far below stated in every bin · `weather_tail_calibration` 0 rows.

### Step B — act on the verdict (gate retirement, in this order ONLY)
Gates were left ON as containment; retire on evidence:
1. **First check A1/A3 worked at all:** PIT KS p-value must rise + high-conf reliability gap must shrink. If PIT is still U-shaped (p<0.05) → **VIF 1.4 was insufficient**; raise `WEATHER_VARIANCE_INFLATION_FACTOR` above 1.4 in `.env.weather` (Tier-1 env tune, no code) and re-measure BEFORE touching any gate.
2. **YES/NO price dampeners** (`WEATHER_*_PRICE_DAMPENER_SLOPE`) — retire only if traded-subset BSS > 0 AND per-confidence-bin realized WR within ~10pp of stated in the 0.70+ bins.
3. **YES/NO max-entry-price caps** (`WEATHER_*_MAX_ENTRY_PRICE`, both 0.85) — retire only if the favorite-buying Brier skew is gone.
4. **Flat-size → Kelly re-enable (C0)** — LAST. Only if the [0.9,1.0) bin is no longer anti-calibrated AND PIT KS no longer rejects. Set `WEATHER_FLAT_SIZE_USD=0`. Flat sizing was a DELIBERATE mitigation (S173) for anti-calibrated confidence — flip it early and you reintroduce the documented catastrophic-tail oversizing.

### Step C — (optional, deferred) sync the top-level engine duplicate
`base_engine/weather/probability_engine.py` now diverges from the deployed vendored copy. Sync requires operator + MB authorization (shared module). Until then it's harmless — nothing imports it for WeatherBot (verified: callers are `bots/weather_bot.py` + tests only).

---

## 5. Config gotchas discovered (flag to operator, don't silently change)

- **`WEATHER_COMBINED_BOOST_CAP=2.5` on the VPS is DEAD CONFIG** — read by no code; the floor is hardcoded `0.25` at `weather_bot.py:3321`. The env value changes nothing. (Documented in `settings.py`.)
- **`WEATHER_NO_MAX_ENTRY_PRICE` is double-defined** — shared `.env`=0.75 vs `.env.weather`=0.85; `.env.weather` loads last so **0.85 wins**. Reconcile by deleting it from the shared `.env` (WB-owned value belongs in `.env.weather`).
- The `weather_tail_calibration` table has **0 rows and no writer** anywhere — the tail-isotonic calibration is loaded-but-dead. Reviving it (plan item "B1") is a FEATURE BUILD (needs a writer + a read path + shadow validation), not a quick fix. Do not treat it as low-hanging fruit.

---

## 6. Deploy mechanics (how it got live, how to do it again)

**The deploy is a tarball splinter release** (NOT git-on-VPS): WeatherBot runs from `/opt/polymarket-ai-v2-weather` → `/opt/pa2-weather-releases/<stamp>`, its own venv, `polymarket-weather.service` with a systemd drop-in (`deploy/polymarket-weather.service.d/00-splinter.conf`). Weather splinter restart is **WB's call, not MB's** (per `deploy/README.md`) — only master merges wait on MB.

**What actually worked (use this for future deploys — from the operator's machine, key `~/.ssh/wb_deploy2`):**
```powershell
cd C:\lockes-picks\polymarket-ai-v2
git checkout claude/new-whiteboard-session-9b23tq; git pull
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"; $key = "$env:USERPROFILE\.ssh\wb_deploy2"; $tar = "$env:TEMP\wb-$stamp.tar.gz"
tar czf $tar "--exclude=.git" "--exclude=__pycache__" "--exclude=*.pyc" "--exclude=venv" "--exclude=.venv" "--exclude=node_modules" "--exclude=.env" .
scp -i $key -o IdentitiesOnly=yes $tar ubuntu@18.201.216.0:/tmp/wb-$stamp.tar.gz
scp -i $key -o IdentitiesOnly=yes deploy\wb-release-cut.sh ubuntu@18.201.216.0:/tmp/wb-release-cut.sh
ssh -i $key -o IdentitiesOnly=yes ubuntu@18.201.216.0 "tr -d '\r' < /tmp/wb-release-cut.sh > /tmp/rc.sh; bash /tmp/rc.sh $stamp"
```
Success = `service: active | S222 markers in weather_bot.py: <N>` + a `ROLLBACK:` line. The release-cut script (`deploy/wb-release-cut.sh`, committed) handles extract → reuse venv → chown → symlink flip → restart → verify → print rollback. It reuses the current venv (valid while `requirements.txt` is unchanged; rebuild the venv if deps change).

**GitHub Actions deploy (built but NOT relied upon):** `.github/workflows/deploy-weather.yml` triggers on `wb-deploy-*` tag push; guarded by a `WB_DEPLOY_SSH_KEY` secret. **Known issue:** the secret kept getting mangled through the Windows clipboard→browser path ("error in libcrypto" = CRLF in the key); the workflow now scrubs CRLF and validates before dialing, but the direct-SSH path above is simpler and is what shipped this release. If you want the Actions path working, set the secret with `gh secret set WB_DEPLOY_SSH_KEY --body ([Convert]::ToBase64String([IO.File]::ReadAllBytes("$env:USERPROFILE\.ssh\wb_deploy2")))` (file-direct, no clipboard). The workflow is not registered for `workflow_dispatch` until it reaches the default branch (master merge = MB right-of-way).

---

## 7. Scope & constraints (bind the next session)

- **This is a WB silo session.** Touch only WeatherBot-owned files (`bots/weather_bot.py`, `bots/weather/engine/**`, `.env.weather`, weather scripts/tests). Shared modules (`base_engine/**`, `risk_manager`, `bankroll_manager`, `database.py`, `/opt/pa2-shared/.env`, `deploy.sh`) are **MB-priority** — defer.
- **Neg-risk markets are IN scope** — do NOT add a neg_risk block (see CLAUDE.md).
- **Never quote P&L** (Forbidden Pattern #11). Calibration metrics only.
- **One fix per commit; test before/after; snapshot first.** Full CLAUDE.md checklist applies.
- Verification/measurement needs the VPS DB (`localhost:5432` on the box) — cannot be run from a cloud sandbox session; hand the verification prompt to a VPS-access session or run on the box.

---

## 8. Key file map

- `bots/weather_bot.py` — bot engine, sizing, METAR override (~2880), calibrator class (~60-680), analyze_group (~2480)
- `bots/weather/engine/base_engine/weather/probability_engine.py` — fit_distribution / empirical_bucket_probabilities / EMOS (the A1/A3 fixes)
- `bots/weather/engine/config/settings.py` — weather config defaults (gates/limiters ~820-970)
- `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` — the pending verification (run in ~1 week)
- `scripts/weather_brier.py`, `scripts/weather_brier_by_side.py`, `scripts/calibration_check.py` — measurement (first two repaired this session; calibration_check was already sound)
- `deploy/wb-release-cut.sh` — the deploy script · `deploy/README.md` — splinter deploy model
- `EDGE_VERIFICATION_1I_RESULTS.md` — the original S172 edge-verification that started this
- `docs/SESSION_HANDOFF_PROTOCOL.md` — how this handoff was written / how to write the next one

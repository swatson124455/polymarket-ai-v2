# WB S228 Kickoff Prompt

Copy-paste the block below to start the next WeatherBot session. Written at S227 close
(2026-07-11, HEAD `b97d041`). Companion docs: `docs/WEATHER_STATUS.md` (canonical) +
`docs/WEATHER_S227_STATUS.md` (S227 handoff).

---

```
WeatherBot session (S228 — extension of S227). Land on the right branch FIRST — before
anything else:
    git fetch origin
    git checkout claude/new-whiteboard-session-9b23tq
    git pull
If that branch isn't on origin after the fetch, STOP and tell me — the environment
can't reach it. HEAD should be at b97d041 (or later).
Then orient:
1. bash scripts/wb_resume_check.sh — must be ALL PASS. Expected WARN at b97d041: HEAD is
   4 commits ahead of the deploy record (deployed sha is 6770883; the 4 are record + docs
   commits — that's the normal handoff-ahead-of-record case). Any FAIL: stop and report.
2. Read docs/WEATHER_STATUS.md (OPEN DECISIONS + WHAT IS LIVE), then
   docs/WEATHER_S227_STATUS.md (full S227 handoff — especially §5 Gotchas and §6 the NEW
   deploy recipe).
Critical current state — do not get this wrong:
- Release 20260711_002634 @ 6770883 is LIVE and VERIFIED; the EFFECTIVE code start is
  2026-07-11 00:47:00Z (release stamp 00:26 is pre-crash-loop — stamp≠restart, always).
  Rollback target 20260710_204822. Deploys are now cut via git archive + a mandatory
  data/-skeleton mirror (ProtectSystem=strict makes the release tree read-only at runtime
  — a release without data/ crash-loops). Read S227 handoff §6 BEFORE any deploy.
- THE CALIBRATOR WAS DEAD 07-08→07-11 (S227 discovery): the S224 cutoff was bound as a
  str into a timestamptz param → asyncpg DataError → every calibrator fit (1,109 failures)
  and every EMOS/bias/tail reload (silent, debug-level) crashed. Fixed in 92740f3, deployed,
  proof-of-life verified. The calibrator is NOW genuinely re-learning from zero (started
  07-11). Do NOT touch it; do NOT judge it harshly before ~2026-08-07 (53 leak-era entries
  age out then). Health greps: calibration_reloaded should recur ~6h;
  calibration_reload_failed and cal_fit_failed MUST stay 0 (both warning-level now).
- S222 verification clock RESTARTED at 2026-07-11 00:47:00Z. Every pre-07-11 window is
  DISCARDED (traded without calibration — not comparable to the 07-02 baseline).
Closed — don't re-litigate: manufactured-certainty leak (S226, journal-proven, tripwire
stays); "confidence inversion" (counting artifact, --dedup-markets); V23 frame bug (fixed
at root, prob_frame labelled); S227 calibrator crash (fixed + deployed + verified); the
07-11 crash-loop (data-skeleton recipe codified).
Pending queue (from WEATHER_STATUS.md OPEN DECISIONS):
1. (Operator actions, nag me if undone) Rotate the Pinnacle API key (URGENT — echoed into
   journal + chat transcripts) then delete /opt/pa2-shared/.env junk lines ~360/367; check
   the local wallet.txt original (172B — if a real key: consider wallet rotation; move the
   file out of the repo dir); Remove-Item $env:TEMP\wb-*.tar.gz; prune old releases only
   after the S222 verdict (keep _204822 + current).
2. MAIN CANDIDATE: S222 post-fix verification — gated on ≥50 DISTINCT resolved markets
   after 2026-07-11 00:47:00Z (ETA was ~07-13/07-14 at ~19/day; 56 distinct markets were
   already logged in the first 90 min). Check the count FIRST:
     SELECT count(DISTINCT market_id) FILTER (WHERE resolution IS NOT NULL)
     FROM prediction_log
     WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-11 00:47:00';
   If ≥50: run WB_S222_POSTFIX_VERIFICATION_PROMPT.md (fully re-pointed to this window;
   self-aborts on any precondition failure) from a VPS-access path. On PASS: retire
   containment gates per WEATHER_S222_STATUS.md §4-B in order (A1/A3 → dampeners → caps;
   C0 Kelly LAST and still calibrator-gated). If <50: report the count and stop.
3. Calibration health spot-check (see greps above) — silence on the failure events is
   meaningful now; any nonzero reload_failed/fit_failed is a regression of 92740f3.
4. Deferred switches (V28 gate ON, deeper V26, bootstrap_gfs purge, WU-only filter) —
   gated on #2 + #3.
5. Optional one-commit improvement: fold the data/-skeleton mirror into
   deploy/wb-release-cut.sh (exact snippet in S227 handoff §6).
Housekeeping note: a one-shot reminder Routine (trig_012fmdChMLAMu2PfhjzVtx9X) fires into
the OLD S227 cloud session at 2026-07-13 12:00Z with the same nag list. If this new session
has taken over, delete it (delete_trigger) or let it fire harmlessly in the old thread.
Scope / rules:
- WeatherBot files only (bots/weather_bot.py, bots/weather/engine/**, .env.weather,
  weather scripts/tests, schema/migrations/*weather*|*prediction_log*). DB-layer changes
  are the sanctioned exception and must hit BOTH database.py copies (top-level AND vendored
  — the WB service binds the TOP-LEVEL one at runtime) + cross-bot verify.
- Never quote P&L (CLAUDE.md Forbidden Pattern #11) — calibration metrics only
  (Brier/PIT/reliability/hit-rate).
- One fix per commit, defect-test-first, keep docs/WB_HANDOFF_MANIFEST.json in sync
  (the resume check FAILs on fingerprint drift; tag new work S228+ so S223–S227 counts
  don't move). Watch for asyncpg str-vs-timestamptz binds in any new SQL (grep
  "AS timestamptz") — mock/sqlite tests do NOT catch that class.
- Deploy + DB verification need the VPS deploy key (~/.ssh/wb_deploy2) + box DB — a cloud
  sandbox can't reach them. Route those steps to me (the operator) as paste-ready
  PowerShell. The proven patterns:
    multi-line bash:  @'…'@ | ssh -i ~/.ssh/wb_deploy2 ubuntu@18.201.216.0 "tr -d '\r' | sudo bash -s"
    SQL:              @'…SQL…'@ | ssh -i ~/.ssh/wb_deploy2 ubuntu@18.201.216.0 "sudo -u postgres psql polymarket -f -"
  (never raw multi-line text at a bare PS prompt; PS eats inner quotes on plain ssh args).
- Test-suite note for cloud sandboxes: pip install pytest pytest-asyncio pytest-cov
  structlog python-dotenv numpy sqlalchemy pydantic pydantic-settings aiohttp httpx
  web3 redis pandas scipy scikit-learn xgboost openskill psutil; feedparser needs the
  manual sgmllib shim (download sgmllib3k sdist, copy sgmllib.py to site-packages,
  pip install --no-deps feedparser). Run suites with:
  python -m pytest <files> -q -p no:cacheprovider -o addopts=""
  If a run dirties base_engine/data/ingestion_error_capture.txt, restore it
  (git checkout --), never commit it.
Confirm the resume check passed and summarize the OPEN DECISIONS back to me before doing
anything. The main candidate for real work is #2 (S222 verification) if the 50-market gate
has opened — check the distinct-resolved count first; if still <50, report the count and
stop. Don't start new calibration changes — the calibrator only STARTED its real re-learn
on 07-11 and S222 hasn't passed; you'd be tuning blind.
```

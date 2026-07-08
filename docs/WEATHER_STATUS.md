# WeatherBot — CANONICAL STATUS (always current)

> **This file is the single always-current WeatherBot status pointer. Its filename never
> changes.** Session-stamped docs (e.g. `WEATHER_S222_STATUS.md`) are archival deep-dives —
> read them for detail, but THIS file is the source of truth for "what is live and what's open."
> Update the three sections below at the end of every WB session (same commit as the work).

**Last updated:** 2026-07-08 (S223 handoff-hardening session)
**Pinned branch:** `claude/new-whiteboard-session-9b23tq` (see `.claude/session-branch`)
**Resume check:** `bash scripts/wb_resume_check.sh` (self-deriving; replaces the hand-typed checklist)

---

## OPEN DECISIONS  ← always at the top, always the first thing a resume reads

1. **Renorm-fix decision (live-corrupting, top priority).** Fallacy-audit finding #1
   (CRITICAL): sum-to-1 renormalization over a *non-exhaustive* bucket set manufactures a
   literal `model_prob=1.0` (matches the observed live signal: prob 1.0, price 0.43,
   fabricated edge 0.57 at ~0.8h lead). Same fallacy at the METAR override renorm site.
   **Decision:** whether/how to fix — candidate is a `len(group.buckets) >= 2` (or
   skip-normalization-for-singleton) guard across **both** engine paths
   (`probability_engine.py` parametric + empirical), the METAR override renorm, and
   `analyze_opportunity`. Corrupts admission, direction, AND the S222 verification readout.
   Detail: `docs/WB_FALLACY_AUDIT_S223.md` finding #1.

2. **S222 post-fix verification (time-gated).** Wait for ≥50 resolved predictions on the
   post-fix code (~1 week from 2026-07-06), then run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md`
   from a VPS-access session (needs the box DB; self-aborts on <50 resolutions or a
   code-fingerprint mismatch). Returns PASS/PARTIAL/FAIL per gate vs. the 2026-07-02 baseline;
   only then retire containment gates in the order in `WEATHER_S222_STATUS.md` §4-B.
   **Do not start new calibration changes until this verdict is in — you'd be tuning blind.**

---

## WHAT IS LIVE NOW

- **Deployed:** WeatherBot on its splinter (`/opt/polymarket-ai-v2-weather` →
  `/opt/pa2-weather-releases/<stamp>`, `polymarket-weather.service`). Paper mode, treated
  as production. Carries the six S223 root-cause fixes (dead tag, DB-semaphore leak,
  watchdog startup-grace, YES-bias exec-edge, mid-life-exit config, exits-must-SELL).
- **Health (per last verification):** service `active`, `NRestarts=0`, funnel restored by
  the tag fix; all S222 safety gates left **ON as containment**. Quality is
  "accurate-but-leaking-edge" — communicate via **calibration** (Brier/PIT/reliability),
  **never P&L** (CLAUDE.md Forbidden Pattern #11).
- **Deploy parity from a keyless (cloud) session:** compare the branch you're on to
  `deploy/LAST_DEPLOY.json` (`bash scripts/wb_resume_check.sh` does this). Live-VPS health
  still needs the deploy key — see the ssh one-liner in `scripts/wb_resume_check.sh`.

---

## POINTERS (archival detail — do not treat as "current" over this file)

- `WEATHER_S222_STATUS.md` — the S222/S223 session's full handoff (fixes, diagnosis,
  pending-work order, deploy mechanics, config gotchas, file map).
- `docs/WB_FALLACY_AUDIT_S223.md` — the 18-of-62 verified fallacy findings (INCOMPLETE;
  resume the verify phase). `docs/WB_FALLACY_AUDIT_S223_raw.json` — raw findings.
- `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` — the time-gated verification to run in ~1 week.
- `docs/SESSION_HANDOFF_PROTOCOL.md` — how to write the next handoff.
- `docs/WB_HANDOFF_MANIFEST.json` — machine-readable state consumed by the resume check.

---

## CHANGELOG (newest first — one line per session-end update)

- **2026-07-08 (handoff hardening):** committed the resume-integrity harness
  (`scripts/wb_resume_check.sh` + `docs/WB_HANDOFF_MANIFEST.json`), the SessionStart
  branch-pin hook (`.claude/`), this canonical status file, and the deploy-record mechanism
  (`deploy/wb-record-deploy.sh` + `deploy/LAST_DEPLOY.json`); documented all four in
  `docs/SESSION_HANDOFF_PROTOCOL.md`. No bot behavior changed.
- **2026-07-06 (S223):** six root-cause fixes deployed; fallacy audit started (18/62
  verified, credit-limited mid-verify). See `WEATHER_S222_STATUS.md` S223 addendum.

# esports_silo — START PROMPT (pick-up for a fresh session)

You are continuing the esports_silo build. Branch `claude/blissful-davinci-twt397-n94qo6`
(current tip — `git log --oneline`; do not pin a stale SHA).

## BEFORE ANYTHING — read in full (they override defaults)
`esports_silo/COMMANDMENTS.md`, `esports_silo/HANDOFF.md`, `esports_silo/PLAN.md`,
`esports_silo/GO_LIVE_CHECKLIST.md`. Then `python -m esports_silo.run.selftest` (expect 14/14).

## Operating discipline (non-negotiable)
Verify every claim on data; tag facts VERIFIED / DOC-SOURCED / UNVERIFIED. Commandments:
(1) P&L is not evidence, (2) de-vig does not exist, (3) surgical cut / label DEVIATIONs,
(4) quarantine by default. One fix per commit, clear messages. **Never claim you ran anything
that needs the box** — you have no VPS/DB/API/network (curl → 403). The operator runs box
commands and pastes output; you interpret and fix at the root. Don't open a PR unless asked.

## STATE (2026-07-07) — the silo is LIVE on the box and self-collecting
The odds→price→pair→resolve chain is BUILT, wired, and running. Do NOT rebuild collectors,
runner, matcher, calibrator, or gate.
- **Odds = pinnodds (Pinnacle), self-serve.** base `https://pinnodds.com/kit/v1`, header
  `x-portal-apikey`, **esports = sport_id 11**, one `/markets` call = all esports events, raw
  money_line (num_0 = match winner). `collectors/pinnodds_collector.py`. ~20 req/window limit.
- **Auto-pull ON.** systemd timer every 15 min (collect-only, `SILO_ENTRY_HALT=true`), writing
  `odds_raw` + `polymarket_snapshots` + `matches.winner`. Installed by `deploy/enable_autopull.sh`.
- **Polymarket pairing = tag-based, verified.** `collectors/polymarket_collector.py` resolves
  esports tag IDs at runtime from `/tags` by exact slug (drift-warned). `scripts/link_report.py`
  proved linking (LoL 6/6, Dota2 1/1 of real overlaps). Results attach to pinnodds rows
  orientation-aware (`collectors/results_collector.py`).
- **No history / no backfill** — pinnodds has no archive (VERIFIED). Calibrator fits FORWARD
  only (~2–4 wks of the timer). `backfill_historical_odds.py` is DEAD (legacy OddsPapi).

## The box
Repo clone: `/home/ubuntu/esports_silo_src` (git; the /opt/* dirs are release copies, NOT git).
Silo DB `esports_silo` (gate PASSED, 32,369 matches). Env: `/opt/esports_silo/.env`. Venv
python with deps: `/opt/pa2-esports-shared/venv/bin/python`. Operator runs box commands as:
`ssh -t -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "cd ~/esports_silo_src && git pull && <cmd>"`.

## TWO blockers, no engineering left in the happy path
1. **Valid PandaScore key.** Current one is INVALID → matches never settle → the whole proving
   chain starves. This is THE blocker. Operator rotates at pandascore.co, puts it in the .env.
2. **Time.** Forward data must accrue before the calibrator can fit (forward-only; no backfill).

## Pairing — DONE for all 4 games (no open items)
CS2 was the last gap and it's CLOSED (VERIFIED 2026-07-08): CS2 matches live under
`counter-strike-2` (id 100780) + `cs2` (100677); adding those slugs took CS2 from 0 → 26 match
markets. Live PM match coverage now cs2=26/lol=21/dota2=16/valorant=29, all pairing to pinnodds.
Nothing left to build or verify on pairing.

## After CS2 — the sequence (all still HALTED; GO_LIVE_CHECKLIST steps 9–12 rewritten forward-only)
Rotate PandaScore → timer accrues (odds, price, result) → `scripts/fit_calibrator.py` once enough
settle (refusal-until-enough is expected) → runner `--predict` writes the forward ledger (all
`no_bet`) → `scripts/skill_report.py` weekly (≥200 resolved = the ONLY verdict). A PASS does NOT
flip the halt — that's a separate, deliberate human action.

## Hard lines
`SILO_ENTRY_HALT=true` until the forward skill gate passes AND the operator deliberately flips it.
Real keys never go into git/PR/chat (pinnodds + old keys were shared in chat — rotate them).
Artifacts in `esports_silo/artifacts/` are box-local + gitignored.

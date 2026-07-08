# esports_silo — VERIFY the handoff, then PROCEED

Two prompts. Run **Part A first** (read-only) to confirm the live box matches `HANDOFF.md`
before trusting it. Then **Part B** is the go-forward — all of it stays HALTED (no bets) until
the forward skill gate passes and a human deliberately flips `SILO_ENTRY_HALT`.

Operator runs box commands as:
`ssh -t -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "<cmd>"`

---

## PART A — VERIFY (read-only; proves the handoff on data)

One command runs the whole battery:
```
ssh -t -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "cd ~/esports_silo_src && git pull && bash esports_silo/scripts/verify_state.sh"
```

What each section must show (if any fails, that's the gap to close — don't proceed past it):
1. **code + tests** → `14/14 test modules green`.
2. **collection timer** → an `esports-silo-collect` timer listed, with a recent last-run. Missing ⇒ re-run `deploy/enable_autopull.sh` (needs the pinnodds key).
3. **DB freshness** → `odds_raw(pinnodds)` and `polymarket_snapshots` row counts climbing, `newest` within ~15 min. `matches winner_set` should rise **once PandaScore is valid**.
4. **keys** → **PandaScore VALID** is the one that matters. (OddsPapi INVALID is expected — abandoned.)
5. **pairing** → all four games (cs2/lol/dota2/valorant) show PAIRED rows.
6. **HALT** → `SILO_ENTRY_HALT=true`.

Interpretation: sections 1–2–3(odds/PM)–5–6 green = the collection+pairing half is LIVE and
matches the handoff. Section 4 (PandaScore) is the known open blocker.

---

## PART B — PROCEED (the go-forward; stays halted throughout)

### Step 0 — (fresh DB only) load the CS2 alias seed
On a brand-new silo DB, load the curated aliases once so CS2 pairs fully (Keyd/PVISION/BB Team):
```
ssh ... 'cd ~/esports_silo_src && set -a && . /opt/esports_silo/.env && set +a && psql "$DATABASE_URL" -f esports_silo/db/alias_seed.sql'
```
✅ `UPDATE 3` (or `INSERT 0 3`). Skip if already applied — it's idempotent. (Diacritic folding
in `match_matcher.fold()` is code, needs no DB step.)

### Step 1 — rotate the keys (the actual blocker)
The old PandaScore/Riot/OddsPapi keys are INVALID and the pinnodds key was shared in chat.
- PandaScore → pandascore.co → regenerate. **This is the one that unblocks resolution.**
- Riot → developer.riotgames.com (personal keys expire every 24h; only needed for LoL patch context).
- pinnodds → rotate (was exposed) — key goes in `/opt/esports_silo/.env` as `PINNODDS_API_KEY`.
Put them in `/opt/esports_silo/.env` (never chat/git), then confirm:
```
ssh ... "cd ~/esports_silo_src && set -a && . /opt/esports_silo/.env && set +a && /opt/pa2-esports-shared/venv/bin/python -m esports_silo.scripts.validate_keys"
```
✅ PandaScore `VALID`.

### Step 2 — confirm results start settling
With a valid PandaScore key the 15-min timer's results pass fills `matches.winner` (and
`results_collector` attaches winners to the pinnodds rows). Re-run Part A after a few timer
cycles: `forward (source=pinnodds) winner_set` should climb above 0. That's the proof the
proving chain is fed.

### Step 3 — let forward data accrue (~2–4 weeks)
No backfill exists (pinnodds has no archive). The calibrator needs a few hundred settled
(odds, outcome) pairs. Nothing to do but let the timer run; spot-check with Part A.

### Step 4 — fit the calibrator (backfit; does NOT prove skill)
```
ssh ... "cd ~/esports_silo_src && set -a && . /opt/esports_silo/.env && set +a && /opt/pa2-esports-shared/venv/bin/python -m esports_silo.scripts.fit_calibrator --dry-run"
```
It **refuses** if the fit is too small / holdout calibration is bad — that refusal is EXPECTED
until enough matches settle. When it passes, drop `--dry-run` to write the artifact. Reads
`odds_raw` where `aggregator='pinnodds'`. (GO_LIVE_CHECKLIST step 10.)

### Step 5 — turn on the forward prediction ledger (still no_bet)
Add `--predict` to the timer (GO_LIVE_CHECKLIST step 11). Each pass then writes a `predictions`
row per linkable market (calibrated p_model + live PM price, pre-match). All decisions `no_bet`.

### Step 6 — the skill gate (the ONLY verdict)
```
ssh ... "cd ~/esports_silo_src && set -a && . /opt/esports_silo/.env && set +a && /opt/pa2-esports-shared/venv/bin/python -m esports_silo.scripts.skill_report"
```
Needs ≥200 resolved forward predictions. Exit 0 = PASS (model beats the Polymarket price on
Brier + calibration). Run weekly.

### Step 7 — the halt is a human decision
A gate PASS does **not** auto-flip anything. Flipping `SILO_ENTRY_HALT` to paper-trade is a
separate, deliberate step taken together. A FAIL = we stop, cheaply, with the receipts.

---

**Hard lines:** verify before trusting; never claim a box result you didn't run; raw keys never
touch git/PR/chat; `SILO_ENTRY_HALT=true` until the gate passes AND a human flips it. Do NOT
rebuild the collectors/runner/matcher/calibrator/gate — they are built, tested, and running.

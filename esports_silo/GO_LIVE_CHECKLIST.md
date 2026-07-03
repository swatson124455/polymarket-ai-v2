# esports_silo — Go-Live Checklist (dead simple)

Two lanes. **YOU** = the human (3 quick things). **OPERATOR** = the box session (the rest).
The two halves can start at the same time. **Nothing trades** through any of this — the bot is
halted (`SILO_ENTRY_HALT=true`) the whole way. Verified end-to-end in a rehearsal DB.

If any step's output doesn't look like the "✅ you should see" line, **STOP and send Claude that
output** — don't improvise.

---

## LANE 1 — YOU (≈15 min, needs a browser). Blocks only the collector steps.

Two old keys leaked into git history, so they must be replaced. One key never existed.

1. **Rotate PandaScore key** → pandascore.co → log in → *Dashboard → API* → regenerate → copy.
2. **Rotate Riot key** → developer.riotgames.com → log in → regenerate the production key → copy.
3. **Register OddsPapi (new)** → oddspapi.io → sign up (free tier is fine to start) → copy the key.

➡️ Hand all 3 values to the **operator** privately. Do **not** paste them into git, a PR, or chat.
That's it for you.

---

## LANE 2 — OPERATOR (on the box). First half needs NO keys — start now.

### Step 0 — get the latest code
```
git pull
```
✅ You should see: the branch fast-forwards (includes the timestamp fixes + results collector).

### Step 1 — make the silo's own database (one-time)
```
PW=$(openssl rand -hex 24)                 # stays only in the .env; never echo it to chat
sudo -u postgres psql -c "CREATE ROLE esports_silo LOGIN PASSWORD '$PW' NOSUPERUSER NOCREATEDB NOCREATEROLE;"
sudo -u postgres createdb -O esports_silo esports_silo
export SILO_DB="postgresql://esports_silo:$PW@localhost:5432/esports_silo"
psql "$SILO_DB" -f esports_silo/db/schema.sql
```
✅ You should see: 5 `CREATE TABLE`. (Same-instance separate DB + locked-down role = the silo's
"own DB". Grant this role nothing on the old 15-bot DB.)

### Step 2 — load the history (needs read access to the OLD bot DB, no API keys)
```
SOURCE_DATABASE_URL="postgresql://…old-polymarket-db"   # existing read creds; copy VALUE only
DATABASE_URL="$SILO_DB" SOURCE_DATABASE_URL="$SOURCE_DATABASE_URL" \
  python -m esports_silo.scripts.import_from_prior_bot --matches-from-db --aliases-from-db --dry-run
```
✅ You should see counts (matches ~32k, aliases ~1,777). Then run it **again without `--dry-run`**
to actually write. Paste the imported / skipped / unresolved-winner / alias-collision numbers.

### Step 3 — remove the one known bad row
```
psql "$SILO_DB" -c "DELETE FROM matches WHERE match_id='grid_1015039';"
```
✅ You should see: `DELETE 1`. (It's a match with a fake 0–0 score — excluded, not guessed.)

### Step 4 — RUN THE GATE (the milestone)
```
DATABASE_URL="$SILO_DB" python -m esports_silo.scripts.verify_data_quality ; echo "EXIT=$?"
```
✅ You should see: `GATE: PASS` and `EXIT=0` → the data has left quarantine.
❌ If `GATE: QUARANTINE` for any reason other than a row you can explain → STOP, paste it.

--- everything below needs the 3 keys from Lane 1 ---

### Step 5 — put the keys in the silo's .env
Copy `esports_silo/.env.example` → `/opt/esports_silo/.env`, fill in `DATABASE_URL` (=`$SILO_DB`),
`ODDSPAPI_API_KEY`, `PANDASCORE_API_KEY`, `RIOT_API_KEY`. Leave `SILO_ENTRY_HALT=true`.

### Step 6 — check the keys work
```
python -m esports_silo.scripts.validate_keys
```
✅ You should see: all three `VALID`. (Riot INVALID = it expired; use the production key.)

### Step 7 — test the feeds (no writes) and confirm the book names
```
python -m esports_silo.collectors.odds_collector --once --dry-run --poll-all
python -m esports_silo.collectors.polymarket_collector --once --dry-run
```
✅ You should see: a per-`(game, book)` coverage table + the first raw payloads logged. From the
odds payload, confirm the EXACT bookmaker strings for Pinnacle / Singbet / Thunderpick; if they
differ from `pinnacle,singbet,thunderpick`, fix `SHARP_BOOKS` in the `.env`. Any book at **0**
coverage → say so; don't proceed as if covered.

### Step 8 — turn on collection (still trades nothing)
Install ONE of the schedulers in `esports_silo/deploy/` (systemd timer or cron). It collects
odds + Polymarket prices + results every 15 min. ⚠️ Requires a **paid** OddsPapi plan first
(the free 250/mo is only enough for Step 7) — see the quota math in the `.timer` file.
✅ You should see: rows accumulating in `odds_raw`, `polymarket_snapshots`, and `matches.winner`
filling in as matches finish.

---

## AFTER THAT — the waiting game (nobody touches anything)
~2–4 weeks of collection → Claude fits the calibrator on the forward data and measures skill vs
the market → **only if the skill gate passes** do we even discuss flipping the halt to paper
trading. If it fails, we stop — cheaply. No real money at any point in this checklist.

## Optional side-quests (any time, zero dependencies)
- `python -m esports_silo.scripts.probe_market_microstructure --once --label pre-match --out probe.jsonl`
  (and `--label in-play` during a live match) → measures today's real order-book spreads to test
  the old "in-play is untradeable" claim on fresh data.
- ✅ History depth CONFIRMED (2026-07-03, live probe): pinnacle's archive reaches ≥6 months back
  (January BLAST fixture returned full line history); soft books age out in ~2 weeks. The
  backfill is BUILT: after the DB gate passes (Step 4+) run
  `python -m esports_silo.scripts.backfill_historical_odds --from 2026-01-01 --to 2026-07-01 --games cs2 --max-requests 40`
  It attaches archived pinnacle closing lines to your already-labelled matches so the calibrator
  can fit on day one (skill still proves on FORWARD data). Every fixture costs 1 API request —
  on the free 250/mo key keep `--max-requests` small; a full sweep needs the paid plan. Re-running
  the same command resumes where quota stopped (already-backfilled matches are skipped).
  ⚠️ Old fixtures may carry only ONE pre-start tick hours before start — rows store the honest
  `line_time` and `is_closing` stays false outside the 30-min window; nothing is dressed up.

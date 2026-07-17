# Sports-Bot (SB) — Owls Insight All-Sports Back-Data Harvest

**Branch:** `claude/sports-bot-owls-backdata` (session 1, 2026-07-17)
**Operator directive:** new sports bot; reuse the API EB probed (Owls Insight),
store back data on ALL sports remotely for future use.
**Status:** harvest RUNNING unattended on the VPS (Phase 1 → auto-chains → Phase 2).

## §0 — PICK UP HERE (next session)

1. Check harvest state on the VPS (nothing local to run):
   ```bash
   KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
   ssh -i "$KEY" ubuntu@18.201.216.0 'cd /home/ubuntu/sports-odds && \
     tail -3 sb_history_index_[abc].log sb_chain.log sb_history_odds.log 2>/dev/null; \
     wc -l owls_history_index_*.jsonl 2>/dev/null; ls -la *.gz 2>/dev/null; \
     wc -l sb_history_odds.done 2>/dev/null'
   ```
2. If Phase 1 finished: `sb_chain.log` says "phase2 odds harvester launched" and
   `sb_history_odds.log` is growing. Phase 2 runs for days/weeks (that's fine —
   resumable, quota-guarded); check `~N ev/h` progress lines.
3. If a lane is stuck past the restart cap (20): `sb_chain.log` says so —
   investigate the lane log, then rerun the lane manually (commands in §3).
4. **CAUTION — pkill self-match:** any ssh command that both kills and
   references `sb_owls_history_index.py` will kill its own shell (burned twice
   this session, exit 255). Kill in its OWN ssh call using a bracketed pattern:
   `pkill -f "python3 sb_owls_[h]istory"`.

## §1 — Vendor / API facts (probed live 2026-07-17, this session)

- **Owls Insight** (`api.owlsinsight.com`), MVP tier $49.99/mo, ~300k req/mo,
  400/min. Auth `Authorization: Bearer`, key at VPS `/home/ubuntu/.eb_owls_key`
  (0600 — NEVER in git/chat; **shared with EB**, whose live recorder needs
  ~6k req/mo; every SB harvester carries a quota floor so EB can't be starved).
- **HTTP/1.1 REQUIRED** — the API hangs on HTTP/2 (EB's probe; `urllib` is fine,
  `curl` needs `--http1.1`).
- **Traditional-sports history coverage** (`/api/v1/history/games?sport=X`,
  totals as of 2026-07-17 ~16:15Z): soccer 157,281 · tennis 77,667 ·
  mlb 21,902 · nba 13,820 · nhl 2,263 · nfl 37. (cs2 20,730 = EB's, untouched.)
  Invalid sport values 400 cleanly (football/basketball/baseball/hockey/mma/…
  all 400 — the API wants league-style names; soccer+tennis are the exceptions).
- **Index rows carry final + period scores** and `oddsSnapshots`/`propsSnapshots`
  counts → the index is a results archive AND the Phase-2 shopping list.
- **NFL is near-empty ON PURPOSE — not a harvest bug (verified 2026-07-17).**
  API's own `pagination.total` for `sport=nfl` is 37; my walk pulled all 37.
  Reasons: (a) the live-archive only began ~Feb 2026, so the entire 2025-26 NFL
  regular season (Sep 2025–Jan 2026) predates it — earliest row is the Feb-08
  Super Bowl; (b) NFL has been out of season since, so the only gridiron games
  in the feed are **CFL** (Hamilton Tiger-Cats, Saskatchewan Roughriders, … —
  the vendor files CFL under the same `nfl` slug). Only 14 of 37 have odds.
  NFL back-data stays empty until the 2026 season (~Sep 2026); a forward live
  recorder is the only way to capture it (see §2 — now BUILT).
- **Per-event odds** (`/api/v1/history/odds?eventId=…`): multi-book US lines —
  draftkings, caesars, betmgm, stations, … — markets h2h/totals/spreads,
  `recordedAt`-timestamped, American prices. MUCH richer than the esports side
  (1xbet-only). `limit` clamps at 5000/page (~16s, ~580KB); `market=`/`opening=`
  filters take 95-100s — never use them. eventIds contain spaces/`@`/parens —
  always urlencode. Example id: `mlb:New York Mets@Philadelphia Phillies-20260716`.
- **Broken/slow server paths (do not build on):** `/history/closing-odds`
  524/500s even when narrowed; date-filtered `/history/games` queries TIME OUT
  whenever the window is empty (this killed the v1 month-window harvester);
  `limit=500` on history/games times out, 1000 clamps to 100.
- **Live endpoints are PINNACLE-sourced (major find, verified 2026-07-17):**
  `/api/v1/{soccer,nba,nhl,nfl,tennis,mlb}/odds` all 200; body is
  `data.{book}[…]` keyed by bookmaker, and **`pinnacle` is present** (soccer 103
  events, tennis 34, nfl 16, mlb 15 on one tick — plus fanduel/draftkings/betmgm/
  novig/caesars/circa/…), h2h+spreads+totals with `maxRiskStake` limits. This is
  the SHARP reference book — the thing EB never got for esports (Owls esports =
  1xbet only). History per-event odds are US retail; only the LIVE feed carries
  Pinnacle → forward recording is the way to capture sharp closing lines.
  (nba/nhl showed no pinnacle mid-July = off-season, few upcoming events.)

## §2 — What is running on the VPS (all under `/home/ubuntu/sports-odds/`)

| Piece | What | State |
|---|---|---|
| `sb_owls_history_index.py` ×3 lanes | Phase 1: full games index, offset walk, newest-first | RUNNING since ~16:50Z 07-17 (lane a: nfl done + nhl/nba/mlb; b: tennis; c: soccer) |
| `sb_chain.sh` | Babysitter: restarts dead incomplete lanes (cap 20), launches Phase 2 when all done | RUNNING |
| `sb_owls_history_odds.py` | Phase 2: per-event odds for every event with `oddsSnapshots>0`, newest first, gzip JSONL | auto-starts after Phase 1 |
| `sb_owls_live_recorder.py` | **Forward** RAW recorder: live `/odds` (Pinnacle+retail) for all 6 sports, hourly | **LIVE via cron `45 * * * *`** since 07-17 18:30Z; first manual tick 6/6 OK |
| `sb_probe_*.py` | one-shot probes (coverage/shape/params), keep for reference | idle |

**Shared crontab (07-17):** appended ONE line — `45 * * * * cd /home/ubuntu/
sports-odds && /usr/bin/python3 sb_owls_live_recorder.py >> sb_live_recorder.log
2>&1`. All 7 pre-existing EB/WB lines preserved untouched; backup at
`sports-odds/crontab.bak_sb_<ts>`. Recorder output: `sb_live_snapshots.jsonl`
(one raw-body line per sport per tick; dedupe/parse offline). Kill = remove that
one crontab line (restore the .bak).

Outputs: `owls_history_index_{sport}.jsonl` (append-only; **dupes possible —
dedupe on eventId**), `owls_history_odds_{sport}.jsonl.gz` (one line per page:
eventId/sport/gameDate/offset/n/fetched_at/snapshots[]; dedupe eventId+offset),
`sb_history_odds.done` (completed eventIds), `sb_state_{a,b,c}.json` (lane
resume state). Disk: 268G free at start; worst-case Phase-2 estimate ≈ tens of
GB gzipped — rechecked via `ls -la *.gz` in §0.

Quota floors: Phase 1 aborts below 290k remaining-month; Phase 2 below 25k
(env `SB_QUOTA_FLOOR`). Phase 2 optional `SB_MAX_EVENTS` caps a run.

## §3 — Manual lane restart (only if chainer gave up)

```bash
cd /home/ubuntu/sports-odds
SB_SPORTS=<csv> SB_STATE=/home/ubuntu/sports-odds/sb_state_<x>.json \
  nohup python3 sb_owls_history_index.py >> sb_history_index_<x>.log 2>&1 &
```
Kill-all (own ssh call, see §0 caution): `pkill -f "python3 sb_owls_[h]istory"`
and `pkill -f "sb_[c]hain"`.

## §4 — Open threads / NOT built (operator decisions or next sessions)

1. ~~Live sports recorder~~ **BUILT + LIVE 07-17** (operator "proceed with
   build") — `sb_owls_live_recorder.py`, hourly cron, Pinnacle+retail raw
   capture (see §2). NOT-YET-DONE next steps on it: (a) nothing consumes
   `sb_live_snapshots.jsonl` yet — offline parser to extract Pinnacle closing
   lines is future work; (b) verify the :45 cron actually fired (check
   `sb_live_recorder.log` has hourly `ok=6/6` lines on the next session).
2. **Daily incremental index top-up** (same crontab caveat): re-walk head pages
   per sport to keep the index current; without it the back data freezes at
   harvest date (Phase-2 head re-walks are the interim).
3. **Props history** (`propsSnapshots` is huge — 142,976 on one MLB game;
   endpoint not probed): massive volume, only worth it with a concrete use.
4. **Archive depth**: rows observed are 2026-era (live-archive began ~Feb-2026
   for cs2; sports likely similar). Exact per-sport earliest gameDate — read it
   off the finished index files, don't burn API calls.
5. **PM-overlap study** (which harvested games have Polymarket markets) — the
   natural next analysis once indexes land; mirrors EB's proposed CS2 overlap
   count.
6. **What SB even bets on** — this session built the data layer only; no bot
   code, no signals, no trading integration.

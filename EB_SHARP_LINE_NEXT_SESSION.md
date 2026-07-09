# EsportsBot Sharp-Line — Next-Session Handoff

**Branch:** `claude/esports-sharp-line-rebuild-36c8u9` (all work pushed to GitHub)
**Updated:** 2026-07-09
**Read order:** this file → `EB_SHARP_LINE_STATE.md` → `EB_SHARP_LINE_PLUMBING.md`
(esp. "Step-3 PREFLIGHT" + "LIVE MEASUREMENT") → `EB_MARKET_SHAPE_RESULTS.md` → `CLAUDE.md`.

---

## 1. One-paragraph state

The dead ratings model is being replaced by an **external sharp-line signal**:
strip the vig off Pinnacle to a fair prob, align it to the Polymarket YES outcome,
bet where Polymarket underprices vs the sharp line (`edge = sharp_prob − price − fee`).
The offline signal core is built + unit-tested. This session **wired a live sharp-odds
source (PinnOdds)** and **started forward-collecting it on a VPS cron** — because no
cheap *historical* Pinnacle esports source exists. EB stays **HALTED / paper**; nothing
deployed to the live bot. The backtest is gated on odds *history*, which is now
accumulating forward.

## 2. DONE (this session — all committed + pushed)

| Area | Result |
|---|---|
| **Market shape** | Probed 2100 live esports markets. Match-winner path = **shape-2 (team-name outcomes)**; ~1057 are props to ignore. `EB_MARKET_SHAPE_RESULTS.md`. |
| **Orientation parser** | Proven correct on the FULL live corpus: **315/315** shape-1 correct (0 sign-flips), 438/438 pollution bailed, 68/68 shape-2 authoritative. No code change needed (fix-only-what's-broken); locked with real-corpus regression tests. |
| **Live sign-flip check** | On prod DB+CLOB: **0/36** flips; `yes_token_id` is a reliable key → step-3 is a robustness upgrade, not an active bug. |
| **Odds source** | **PinnOdds** wired: `esports_v2/data/pinnodds_loader.py` → `match_key→(odds_a,odds_b)`. Live-verified **36 match-winner lines**. Fixed 2 live bugs (empty key in env; WAF 403s python UA → browser UA). |
| **Forward-collector** | `esports_v2/scripts/collect_pinnodds.py` + `fetch_rows()`. **Running on VPS cron every 15 min**, appending snapshots to `/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl` (standalone bootstrap; canonical code in repo). |

Tests: PinnOdds loader/collector 10 green; full esports+odds suite 92 green.

## 3. RUNNING NOW (don't re-do)

- **VPS cron** (`ubuntu` crontab): `*/15 * * * * /usr/bin/python3
  /home/ubuntu/eb-odds/collect_pinnodds_standalone.py >> /home/ubuntu/eb-odds/collect.log 2>&1`
- **Snapshots:** `/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl` (append-only JSONL;
  one line per match per run: `captured_at, match_key, home, away, starts, league_name,
  odds_a, odds_b, event_type`).
- **Check progress:** `ssh … 'wc -l /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl;
  tail -1 /home/ubuntu/eb-odds/collect.log'`

## 4. Key facts / env

- **PinnOdds:** base `https://pinnodds.com/kit/v1`, header `x-portal-apikey`, esports
  `sport_id=11`, match winner = `periods.num_0.money_line.{home,away}` (decimal). **WAF
  403s default python UA — a browser User-Agent is required.**
- **Key:** `PINNACLE_ODDS_API_KEY` in `/opt/pa2-shared/.env` (VPS). *(Was exposed in a
  chat during setup — consider rotating in the PinnOdds panel.)*
- **VPS:** `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0`.
  EsportsBot deploy: `/opt/polymarket-ai-v2-esports` (NOT a git repo; has `venv/`,
  `base_engine/`, `.env`→`/opt/pa2-shared/.env`).
- **This is a cloud session:** cannot reach the VPS/DB/PinnOdds directly (egress-scoped +
  no SSH key); the operator runs VPS commands and pastes output.

## 5. NEXT ACTIONS (circle back once history has built up)

1. **Reduce snapshots → closing line** per match: last snapshot with
   `captured_at <= starts`, per `match_key`.
2. **Join to free RESULTS** we already have (`data/esports_matches_bulk.jsonl`,
   `data/cs2/pandascore_cs2.json`) by team/date via the alias matcher → who won.
3. **Finish Step 3 orientation plumbing** (working-code edit; follow the PREFLIGHT):
   store the YES **team NAME** from the CLOB outcome label (flip-proof), not a bool.
   Verify live before/after. EB stays halted.
4. **Backtest**: `esports_v2/model/sharp_reference.py:enrich_with_sharp_prob →
   evaluate_edge`; report edge / CLV / hit-rate.
5. **De-vig method** (simple no-vig vs Shin) is an **OPEN operator decision** — ASK.

## 6. Guardrails / landmines

- **EB scope only.** MB has priority on ALL shared resources — do not touch shared
  modules, MB state, or other bots' env values.
- **Do NOT deploy** — EB is halted, code isn't wired into the live bot.
- **Correct-or-absent everywhere:** any doubt → None/skip, never a wrong bool (a flipped
  orientation inverts the edge — the S152/B2 loss).
- **PinnOdds ≠ PandaScore odds:** PandaScore makes its *own* model odds (not sharp);
  used only for free RESULTS. OddsPapi has **no** esports. OddsPortal = ToS/scrape (unsafe).
- **`*_HANDOFF.md` is gitignored** — that's why this is `_NEXT_SESSION.md`.

## 7. File map

- `esports_v2/data/pinnodds_loader.py` — PinnOdds client (`fetch_odds`, `fetch_rows`, `from_env`)
- `esports_v2/data/odds_loader.py` — OddsPapi loader (sibling, kept) + `make_match_key`
- `esports_v2/scripts/collect_pinnodds.py` — canonical forward-collector
- `esports_v2/model/orientation.py` — `resolve_yes_is_team_a` (correct-or-absent)
- `esports_v2/model/sharp_reference.py` — no-vig core + `enrich_with_sharp_prob`
- `scripts/esports_market_shape_probe_public.py` / `esports_orientation_live_check.py` — read-only probes
- tests: `test_pinnodds_loader.py`, `test_collect_pinnodds.py`, `test_esports_orientation*.py`, `test_esports_sharp_reference.py`

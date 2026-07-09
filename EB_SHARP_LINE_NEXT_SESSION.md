# EsportsBot Sharp-Line — Next-Session Handoff

**Branch:** `claude/esports-sharp-line-rebuild-36c8u9` (all work pushed to GitHub)
**Updated:** 2026-07-09 (session 2 — backtest pipeline built)
**Read order:** this file → `EB_SHARP_LINE_STATE.md` → `EB_SHARP_LINE_PLUMBING.md`
(esp. "Step-3 PREFLIGHT" + "LIVE MEASUREMENT") → `EB_MARKET_SHAPE_RESULTS.md` → `CLAUDE.md`.

---

## 0. SESSION-2 UPDATE (2026-07-09) — the whole backtest pipeline is now built

All four circle-back steps (§5) are IMPLEMENTED, unit-tested, and (Step 3) live-
verified. What is NOT done is producing real numbers — that is blocked on data,
not code. **Two data gaps** and **one operator decision** below.

**Built this session (4 commits, all pushed; +65 unit tests, full suite 138 green):**

| Step | Module | What | Runs now? |
|---|---|---|---|
| 1 | `esports_v2/model/closing_line.py` | snapshots → closing line per match (last snap with `captured_at <= starts`; no look-ahead). Projects to the `(odds_a,odds_b)` lookup. | ✅ pure |
| 2 | `esports_v2/model/results_join.py` | join closing lines to free RESULTS by team (alias matcher, injectable) + day → `home_won`. Bijective, drops ambiguous multi-winner. | ✅ pure |
| 3 | `esports_v2/data/clob_labels.py` | flip-proof orientation: stored `yes_token_id` → authoritative CLOB outcome (YES team NAME) → `resolve_yes_is_team_a`. | ✅ **live-verified 5/5** vs real CLOB |
| 4 | `esports_v2/model/sharp_eval.py` + `esports_v2/scripts/eval_sharp_line.py` | metrics (favorite hit-rate, Brier, reliability, closing-vs-open CLV) + `edge_backtest` wiring + CLI driver reduce→join→eval. | ✅ verified 40/40 on synthetic-over-real-CS2 |

**Run the whole chain** (once data aligns — see gaps):
```
python -m esports_v2.scripts.eval_sharp_line \
  --snapshots data/odds/pinnodds_snapshots.jsonl \
  --bulk data/esports_matches_bulk.jsonl --cs2 data/cs2/pandascore_cs2.json \
  --de-vig simple            # or shin — OPEN operator decision (§DECISION)
```

### TWO DATA GAPS blocking real numbers (both are data, not code)

- **GAP A — date non-overlap (measured).** Free results on disk end **2026-04-14**;
  forward odds-collection began **2026-07-09**. **Zero overlap** → the join yields 0
  today no matter how many odds snapshots accumulate. FIX: pull fresh free results
  covering the collection window AFTER those matches resolve (re-run the Oracle/
  PandaScore results fetch for 2026-07+; PandaScore CS2 is match-level and the
  cleanest join target — Oracle LoL rows are per-GAME and get dropped as ambiguous
  by design). The join code is done and correct; it just needs same-window results.
- **GAP B — no historical Polymarket prices (design gap).** The actual EB signal is
  `edge = sharp_prob − PM_price − fee`. The forward-collector captures **PinnOdds
  only** — no PM price at bet time — so `edge_backtest` reports the gap instead of
  fabricating a price. The sharp-line **hit-rate/Brier/CLV** metrics (which validate
  the signal SOURCE) need only odds+results and WILL run once Gap A closes. To
  backtest the real edge, extend the forward-collector to also snapshot the matched
  Polymarket YES price (+ `condition_id`/`yes_token_id` for the CLOB orientation
  backfill) alongside each PinnOdds line. That is the highest-value next data change.

### DECISION RESOLVED (operator, 2026-07-09) — de-vig = SIMPLE no-vig
Operator chose **`--de-vig simple`** (proportional no-vig; fleet standard,
`sharp_reference.no_vig_two_way`) — which is already the code default, so no change.
Shin (`--de-vig shin` / `clv.odds_to_implied`) stays wired as a one-flag alternative
if revisited. Report all sharp-line numbers with simple no-vig.

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

## 5. NEXT ACTIONS — steps 1-4 are BUILT (§0); remaining work is DATA, not code

1. ✅ **[DONE, session 2]** Reduce snapshots → closing line (`closing_line.py`).
2. ✅ **[DONE, session 2]** Join to free RESULTS (`results_join.py`).
3. ✅ **[DONE, session 2 — live-verified 5/5]** Flip-proof orientation via the
   authoritative CLOB label (`clob_labels.py`). This is the OFFLINE backfill path
   (option a from the PREFLIGHT) — read-only, no live-bot change, EB stays halted.
   The live-scan wiring (option b) is deferred until there is odds data to test the
   whole chain against, exactly as the PREFLIGHT recommended.
4. ✅ **[DONE, session 2 — wiring]** `sharp_eval.py` + `eval_sharp_line.py` tie
   reduce→join→metrics and wire `enrich_with_sharp_prob`. **Numbers pending data.**

**What's actually left (do in this order):**
- **(a) Close GAP A** (§0): get free results covering the 2026-07+ collection window
  (re-pull Oracle/PandaScore after those matches resolve). Then run the driver — the
  sharp-line hit-rate / Brier / CLV numbers come out immediately.
- **(b) Close GAP B** (§0): extend `collect_pinnodds.py` to also snapshot the matched
  Polymarket YES price + `condition_id`/`yes_token_id`. That unlocks the real
  `edge = sharp − PM_price` backtest via `edge_backtest`.
- **(c) ✅ De-vig decided** (§DECISION): operator chose `--de-vig simple`. No action.
- **(d)** Only after (a)+(c) give a real sharp-line hit-rate, and (b) gives a real
  edge, consider the live-scan orientation wiring (PREFLIGHT option b) + un-halting.

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

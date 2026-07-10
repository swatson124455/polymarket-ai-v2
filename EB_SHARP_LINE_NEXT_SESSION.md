# EsportsBot Sharp-Line — Next-Session Handoff

**Branch:** `claude/esports-sharp-line-rebuild-36c8u9-7m96gg` (all work pushed to GitHub)
**Updated:** 2026-07-09 (session 3 — GAP B code built: PM price capture + edge wiring)
**Read order:** this file → `EB_SHARP_LINE_STATE.md` → `EB_SHARP_LINE_PLUMBING.md`
(esp. "Step-3 PREFLIGHT" + "LIVE MEASUREMENT") → `EB_MARKET_SHAPE_RESULTS.md` → `CLAUDE.md`.

---

## PICK UP HERE (copy-paste prompt for the next session)

> **EsportsBot sharp-line rebuild — continue. Branch:
> `git checkout claude/esports-sharp-line-rebuild-36c8u9-7m96gg && git pull`.**
>
> Read first, in order: this file (start at §0 + §3 "COLLECTOR STATUS"), then
> `EB_SHARP_LINE_STATE.md`, `EB_SHARP_LINE_PLUMBING.md`, `EB_MARKET_SHAPE_RESULTS.md`,
> then `CLAUDE.md`.
>
> **Context:** cloud session — you cannot reach the VPS/DB/PinnOdds directly
> (egress-scoped, no SSH key). CLOB + gamma-api ARE reachable. The operator runs VPS
> commands (`ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0`,
> often md5-gated base64 one-shots) and pastes output back. Operator is on Windows
> PowerShell (no `\` line-continuation; one command per paste).
>
> **DONE last session (do not redo):** full offline backtest pipeline BUILT + tested
> (138 green) + pushed — Step 1 `esports_v2/model/closing_line.py`, Step 2
> `esports_v2/model/results_join.py`, Step 3 `esports_v2/data/clob_labels.py`
> (flip-proof orientation, LIVE-verified 5/5), Step 4 `esports_v2/model/sharp_eval.py`
> + `esports_v2/scripts/eval_sharp_line.py`. De-vig decided: simple no-vig. VPS
> collector was DEAD (no cron + 429); FIXED — cron installed, hardened prematch-only
> script deployed (`deploy/vps/collect_pinnodds_standalone.py`, md5
> `3f6e794f21e3bd40ef97b01c7fad3116`), 18:45 UTC tick verified firing.
>
> **FIRST ACTION — have the operator run + paste back:**
> ```
> ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "date -u; wc -l /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl; grep -aE 'appended|429' /home/ubuntu/eb-odds/collect.log | tail -5"
> ```
> As of last session the collector was 429 rate-limited (PinnOdds demo-tier quota
> drained by test runs), frozen at 33 lines. Interpret: **>33 + `appended=<nonzero>`**
> → 429 cleared, collector alive, let odds accumulate. **still 33 + continuous `429`**
> → free tier can't sustain 1 req/15min → operator decision (paid tier / widen cadence
> `*/30`|hourly one-line crontab edit / different source) → ASK.
>
> **THEN the two DATA gaps (code is done; see §0):** (A) fresh results covering the
> forward window (free results end 2026-04-14, odds start now → join yields 0 until a
> forward Oracle/PandaScore pull); (B) capture the matched Polymarket price alongside
> each odds pull to enable the real `edge = sharp − PM_price` backtest.
>
> **GUARDRAILS:** EB scope only; MB priority on shared resources; EB stays HALTED — do
> NOT deploy the trading bot (the odds cron is not a bot deploy); correct-or-absent
> everywhere (doubt → None, never a wrong bool); preserve other crontab entries on any
> cron edit. Commit + push each step.

---

## 0b. SESSION-3 UPDATE (2026-07-09) — GAP B is CODE-DONE (capture + edge wiring)

**What changed:** GAP B ("no historical Polymarket prices") is now closed on the
CODE side — the forward-collector captures the matched PM price, and the whole
reduce→join→edge path consumes it. Two commits, pushed, +25 unit tests (full
sharp-line suite **163 green**):

| Commit | Module(s) | What |
|---|---|---|
| `4f56d2c` | `esports_v2/data/pm_market_index.py` (new) + `collect_pinnodds.py` + `deploy/vps/collect_pinnodds_standalone.py` | Build a `match_key → PMMarketRef` index of live Gamma (`tag_id=64`) shape-2 **match-winner** markets (props/Yes-No rejected; ambiguous key collisions dropped). Collector writes `condition_id`/`yes_token_id`/`yes_outcome`/`market_price` on each snapshot (None when unmatched; Gamma failure → null fields, odds never blocked). **Live-verified:** `build_pm_index` → 45 match winners; standalone == canonical byte-for-byte. |
| `7a0086b` | `closing_line.py` + `results_join.py` + `sharp_eval.py` + `eval_sharp_line.py` | Thread the PM fields ClosingLine→JoinedRecord; add `edge_backtest_from_joined()` (pure, injectable orientation resolver = live CLOB by default, flip-proof via `clob_labels`); driver runs the edge backtest after the sharp-line report, **guarded** so zero CLOB calls until a joined record actually carries a PM price. |

**⚠️ OPERATOR ACTION REQUIRED to start capturing PM prices** — the VPS bootstrap
changed (adds the Gamma PM index + the bijective team matcher). Redeploy
`deploy/vps/collect_pinnodds_standalone.py` to
`/home/ubuntu/eb-odds/collect_pinnodds_standalone.py`. **Current md5:
`5fcb2c4f0143c35351c12704f3a2edcf`** (prior `87bebc3c…` = exact-name only;
`3f6e794f…` = odds-only). The md5-`87bebc3c` PM-capture drop was live-deployed +
verified 2026-07-10 00:2x UTC; this `5fcb2c4f` drop ADDS the alias/token-subset
matching below and must replace it.
Until redeployed the cron keeps writing odds-only rows (no PM fields). The VPS
must have egress to `gamma-api.polymarket.com` (the live bot already does).
After redeploy, each tick logs `pm_matched=<n>`; new snapshot rows gain the four
PM fields. **This does NOT change the trading bot — EB stays halted; only the
odds/price cron.**

**What's STILL open after GAP B code:**
- **GAP A (unchanged) — date overlap.** Free results end 2026-04-14; forward
  odds+PM start now. The join yields 0 until fresh results cover the collection
  window. Pull Oracle/PandaScore results for 2026-07+ AFTER matches resolve, then
  run the driver — sharp-line hit-rate/Brier/CLV **and now** the PM-edge backtest
  come out together.
- **PM↔PinnOdds matching (session 3, EXPANDED beyond exact name).** Now reuses the
  results-join matcher (`esports_v2/model/team_match.py`): bijective both-teams
  equality via exact-normalized / injected-alias / shared-non-generic token-subset
  (`match_pm_ref`), within a ±1-day window, dropping rows that match two distinct
  PM markets as ambiguous. Catches "Team Vitality"↔"Vitality", "G2 Esports"↔"G2"
  (live: 21/21 suffix-perturbed rows matched; canonical==standalone on 40/40).
  Still correct-or-absent — any doubt → null PM fields, never a wrong attach.
  The `alias_expand` hook is wired but fed `None` in the standalone (no DB on the
  cron); inject the real `esports_team_aliases` map (1,777 rows) to link
  hard cases like "NAVI"↔"Natus Vincere" if `pm_matched` runs low once live.

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

## 3. COLLECTOR STATUS (session 2, 2026-07-09 — fixed but rate-limited)

**Was DEAD, now FIXED — but blocked on PinnOdds rate-limit.** On check-in the file
was frozen at 33 lines since first run. Root causes: **(1) no cron was ever
installed**, and **(2) the bootstrap script 429'd** (bare urllib, no Retry-After
handling, fired live+prematch back-to-back). Both fixed:
- Cron NOW installed (`ubuntu` crontab, verified `grep -c` = 1):
  `*/15 * * * * /usr/bin/python3 /home/ubuntu/eb-odds/collect_pinnodds_standalone.py
  >> /home/ubuntu/eb-odds/collect.log 2>&1`
- Bootstrap replaced with the hardened **prematch-only** version (429 Retry-After
  backoff; live feed dropped — it's post-start look-ahead the reducer discards
  anyway). Deployed bytes tracked in repo: `deploy/vps/collect_pinnodds_standalone.py`
  (md5 `3f6e794f21e3bd40ef97b01c7fad3116`).

**Verified the cron fires** (18:45:01 UTC tick logged cleanly). **BUT PinnOdds now
returns HTTP 429 (`Retry-After: 60`) persistently** — the demo-tier quota was drained
by this session's manual test runs. Each tick logs `appended=0 total_lines=33`.
- **Action: let it sit.** Stop manual runs (they consume quota). The cron keeps
  trying every 15 min and will resume appending once the quota window resets
  (likely a daily reset). **Check next day.**
- If STILL 429-locked after a full day: the free tier can't sustain 1 req/15min →
  operator decision — paid PinnOdds tier, slower cadence (`*/30` or hourly), or a
  different sharp source. (Widening cadence is a one-line crontab edit.)
- **Snapshot schema** (`/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl`, append-only):
  `captured_at, match_key, home, away, starts, league_name, odds_a, odds_b, event_type`
  — **plus (session 3, after the md5-`87bebc3c` redeploy)** `condition_id,
  yes_token_id, yes_outcome, market_price` (the matched PM match-winner; null when
  no PM market matches the odds row).
- **Check progress:** `ssh … "date -u; wc -l /home/ubuntu/eb-odds/pinnodds_snapshots.jsonl;
  grep -aE 'appended|429' /home/ubuntu/eb-odds/collect.log | tail -5"`

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

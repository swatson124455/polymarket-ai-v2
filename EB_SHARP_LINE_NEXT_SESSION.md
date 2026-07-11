# EsportsBot Sharp-Line — Next-Session Handoff

**Branch:** `claude/esports-sharp-line-rebuild-gqy1na` (session 4; supersedes
`…-36c8u9-7m96gg` — same history + the PM-index coverage fix below)
**Updated:** 2026-07-10 (session 4 — CRITICAL collector coverage fix, redeploy pending)
**Read order:** this file → `EB_SHARP_LINE_STATE.md` → `EB_SHARP_LINE_PLUMBING.md`
(esp. "Step-3 PREFLIGHT" + "LIVE MEASUREMENT") → `EB_MARKET_SHAPE_RESULTS.md` → `CLAUDE.md`.

---

## 0-S4d. SESSION-4 STRESS HARNESS (`633f5a9`) — PIPELINE PASSES ON SYNTHETIC GROUND TRUTH

`esports_v2/scripts/stress_sharp_pipeline.py` (+CI wrapper, quick config): a
seeded synthetic universe with KNOWN answers run through the REAL chain. Full
run (4000 matches / 2419 bets / 88K-line scale stage) PASSES all scenarios:
0 mislabels, 0 look-ahead leaks, calibration recovered (Brier gap 0.0003),
planted +0.10 edge recovered at +0.097, absent-orientation drops never corrupt,
sibling fixtures never cross-join, garbage survived, 88K lines in 42s.
Re-run anytime: `python -m esports_v2.scripts.stress_sharp_pipeline` (all its
numbers are SYNTHETIC — validates software, not market edge).

**PERMANENT FINDING (S5b/S5c): backtest P&L is structurally BLIND to
orientation flips.** A flipped resolver settles with the same wrong bool it
decided with → self-consistent phantom PROFIT (+0.416 "ROI"); the same
decisions settled against reality destroy the edge (+0.097 → +0.025). So a
healthy-looking backtest P&L can NEVER be cited as evidence that orientation
is correct — only the independent CLOB label check (clob_labels) can. Keep
this in mind when interpreting the first real edge readout.

---

## 0-S4c. SESSION-4 FINAL (2026-07-10) — SIBLING-ROSTER VETO (matcher root fix)

**Bug (operator-directed "fix the naming issue for good", `672f5a7`):** the
matcher's token-subset rule linked an org's MAIN roster to its sibling roster —
"T1" ↔ "T1 Esports Academy", "Shopify Rebellion" ↔ "Shopify Rebellion Black" —
because academy/youth/etc. were classed as ignorable GENERIC decoration. These
are DIFFERENT teams that play the same day (LCK vs LCK CL) → wrong-attach risk
(price + orientation, the S152/B2 class). The seeded `esports_team_aliases`
table is contaminated with the same cross-roster groups (2026-07-10 dump: T1/T1
Academy, Weibo/Youth, W7M/Fe, KC Blue/Blue Stars — the dump's ONLY 16 groups
are all already-token-linked pairs, i.e. the alias file adds no new matching
power; that's fine/expected).

**Fix:** `SIBLING_QUALIFIERS` (academy, youth, junior(s), rookies,
challenger(s), female, fe, women(s), ladies, gc, blue, white, black, gold,
stars) — a DIFFERENCE in these tokens hard-vetoes `same_team` BEFORE the alias
and subset paths (so contaminated alias groups are inert too). Same qualifier
both sides still matches ("T1 Academy" == "T1 Esports Academy"); decorations
untouched ("G2 Esports" == "G2"). Veto can only REMOVE a match (safe
direction). Applied to `team_match.py` + standalone mirror; results_join
inherits via re-export. Suite 219 green; live sweep: 5 sibling fixtures in
TODAY's index (Spirit/NRG/MIBR/Vitality Academy, SR Black) now unreachable
from main-roster odds rows, and all 4 spot-checked main-slate pairs still
self-match. Two old tests asserting "DRX Academy"=="DRX" encoded the bug —
inverted with dated notes.

**⚠️ OPERATOR: redeploy the collector (supersedes §0-S4b one-liner B; run
§0-S4b one-liner A first if not done). md5 `5c67c2ba3b03af1eeddee9739a26510b`:**
`ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "curl -fsSL https://raw.githubusercontent.com/swatson124455/polymarket-ai-v2/claude/esports-sharp-line-rebuild-gqy1na/deploy/vps/collect_pinnodds_standalone.py -o /home/ubuntu/eb-odds/collect_pinnodds_standalone.py && echo '5c67c2ba3b03af1eeddee9739a26510b  /home/ubuntu/eb-odds/collect_pinnodds_standalone.py' | md5sum -c -"`

`eb_label_audit.sh` md5 UNCHANGED (`e56e8ed6…`) — it re-clones HEAD, so the
veto applies to the join automatically at the next audit run.

---

## 0-S4b. SESSION-4 LATE (2026-07-10) — ALIAS INJECTION + DECISION-GRADE READOUT; 429 TABLED

**TABLED (operator, 2026-07-10):** the PinnOdds 429 stall. The `4d46e275`
coverage-fix collector WAS redeployed + md5-verified on the VPS, but its first
successful tick is pending — PinnOdds quota was drained (20:00+21:00 UTC ticks
429'd; file frozen at 994 lines / 39 PM-attached rows). Next session: check
`tail -3 collect.log` + the 0x row count first. If still all-429 a full day past
2026-07-10 21:00 UTC → operator decision: paid tier vs slower cadence. Do NOT
run the collector manually (burns quota).

**Built while waiting (both pushed, suite 210 green):**
- **Alias injection (`ba220bd`).** The 1,777-row `esports_team_aliases` table
  now feeds the matchers via a JSON file: NEW `esports_v2/data/alias_file.py`
  (correct-or-absent loader: missing/malformed → None → matching EXACTLY as
  strict as before), `--aliases` on `eval_sharp_line`/`dump_joined`,
  `EB_ALIASES_PATH` on both collectors, stdlib mirror in the standalone,
  audit script passes the path. Catches "NAVI"↔"Natus Vincere"-class misses
  the token matcher can't. **OPERATOR: two one-liners below** (dump once, then
  redeploy collector md5 `0673cb50ab842e7182afff2f9bd59b1a`; both zero
  PinnOdds cost).
- **Decision-grade readout (`0981c32`).** Wilson 95% CIs on fav hit-rate and
  edge hit-rate, automatic `UNSTABLE (n<50)` labels, per-edge-size bucket
  table (n/wins/pnl/roi per bucket) in the edge backtest — so the first
  multi-record readout is interpretable, not just a point estimate.

**Operator one-liner A (once): dump aliases → `/home/ubuntu/eb-odds/aliases.json`**
`ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "curl -fsSL https://raw.githubusercontent.com/swatson124455/polymarket-ai-v2/claude/esports-sharp-line-rebuild-gqy1na/deploy/vps/eb_dump_aliases.sh -o /tmp/eba.sh && echo '5307d3f96b0eb78e3d3b530c9179f62c  /tmp/eba.sh' | md5sum -c - && bash /tmp/eba.sh"`

**Operator one-liner B (after A): redeploy collector with alias support** (do
NOT append a manual run this time — quota). **md5 SUPERSEDED by §0-S4c** —
current drop is `5c67c2ba3b03af1eeddee9739a26510b` (adds the sibling-roster
veto); use the §0-S4c one-liner.

`eb_label_audit.sh` md5 is now `e56e8ed671f0c103e9ee68ac0a05a8a9` (adds
`--aliases`; supersedes `1c866f6d…` — the §0-FINAL/PRIMARY command's md5 token
must be this value).

---

## 0-S4. SESSION-4 (2026-07-10) — PM-INDEX COVERAGE BUG FOUND+FIXED; COLLECTOR REDEPLOY DONE (md5 SUPERSEDED by §0-S4b)

**Bug (live-measured, then fixed):** gamma-api hard-caps offset pagination
(HTTP 422 `offset too large` past ~2100) while the esports tag holds ~3600
active markets. The PM index paged in DEFAULT order (id ascending = oldest
first), so the newest ~1500 markets were unreachable — **93 of 130 live
match-winner markets were invisible** to the collector (PARIVISION vs FaZe,
T1 vs ZETA, Team Liquid vs Eternal Fire, NAVI, 3DMAX vs Heroic, the whole
PinnOdds-overlapping slate). This — not calendar non-overlap — is the main
reason `pm_matched` stayed ~0; the JD Gaming vs TYLOO hit was luck (old-enough
market id). Fix (`f4bd962`): page `order=id&ascending=false` (newest first) in
BOTH the canonical `pm_market_index._default_fetch_page` and the standalone's
`gamma_page`; the offset-cap truncation now falls on stale Jan–Jun markets the
±1-day matcher can never use. **Live-verified from this session:** index 37 →
**118** refs, all days 07-10..07-19 covered, standalone==canonical parity
118==118 zero diffs, suite 191 green (+1 URL-shape regression test).

**⚠️ OPERATOR ACTION 1 — redeploy the collector** (until then the cron keeps
running the truncated index). New md5 `4d46e275dc4085f5ec50b2846adf8e6c`
(replaces `5fcb2c4f…`). One line:
`ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "curl -fsSL https://raw.githubusercontent.com/swatson124455/polymarket-ai-v2/claude/esports-sharp-line-rebuild-gqy1na/deploy/vps/collect_pinnodds_standalone.py -o /home/ubuntu/eb-odds/collect_pinnodds_standalone.py && echo '4d46e275dc4085f5ec50b2846adf8e6c  /home/ubuntu/eb-odds/collect_pinnodds_standalone.py' | md5sum -c - && /usr/bin/python3 /home/ubuntu/eb-odds/collect_pinnodds_standalone.py"`
(the trailing manual run doubles as verification — expect `pm_matched` in the
double digits vs the current ~0-2; it consumes one PinnOdds call, acceptable).

**Audit one-liner rebased to this branch** (the old one clones the superseded
branch and would MISS this fix): `eb_label_audit.sh` md5 is now
`1c866f6d38c32101e38405cf003b20f9` — the §0-FINAL command below is updated
in place. Everything else from §0-FINAL stands (do not redo).

---

## PICK UP HERE (copy-paste prompt for the next session)

> **EsportsBot sharp-line rebuild — continue; you are a new session picking up
> seamlessly. Branch:
> `git checkout claude/esports-sharp-line-rebuild-gqy1na && git pull`.**
>
> Read first, in order: `EB_SHARP_LINE_NEXT_SESSION.md` (start at §0-S4c, then
> §0-S4b, §0-S4, §0-FINAL, §0a/§0b), `EB_SHARP_LINE_STATE.md`,
> `EB_SHARP_LINE_PLUMBING.md`, `EB_MARKET_SHAPE_RESULTS.md`, then `CLAUDE.md`.
>
> **Context:** cloud session — no direct VPS/DB/PinnOdds/PandaScore access.
> CLOB + gamma-api + raw.githubusercontent ARE reachable. The operator runs VPS
> commands (`ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0`)
> on Windows PowerShell and pastes output back (one command per paste; no `\`
> line-continuation). **Ops-script delivery mechanism (established — do NOT
> regress to base64 chat pastes, they corrupt):** commit the script to
> `deploy/vps/eb_*.sh`, push, then hand the operator ONE line:
> `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "curl -fsSL
> https://raw.githubusercontent.com/swatson124455/polymarket-ai-v2/<branch>/deploy/vps/<script>
> -o /tmp/x.sh && echo '<md5>  /tmp/x.sh' | md5sum -c - && bash /tmp/x.sh"`.
> Known-bad: grep patterns with escaped quotes inside PowerShell one-liners
> (backslashes get mangled → silent 0). Count things server-side from a script.
>
> **STATE — everything below is DONE and verified; do not redo (details §0-FINAL):**
> pipeline is FULLY LIVE end-to-end (odds+PM capture → results → join → metrics →
> PM-edge backtest). VPS steady state: collector HOURLY cron with PM capture
> (current drop = md5 `5c67c2ba3b03af1eeddee9739a26510b`, §0-S4c sibling-roster
> veto; older drops: `0673cb50…` alias-support, `4d46e275…` coverage-fix,
> `5fcb2c4f…` pre-fix. VPS showing anything older than `5c67c2ba…` → run
> §0-S4b one-liner A once, then the §0-S4c redeploy one-liner),
> PM-first-hit watcher hourly at :07 (marker exists — first capture was
> JD Gaming vs TYLOO, 39 snaps, orientation sanity-checked). First labeled run:
> 19/36 closing lines joined; labels AUDITED correct (LYON 3-0 G2 at MSI verified
> real); fav hit-rate 0.421 on n=19 — **UNSTABLE, do not act on**. 1 PM-priced
> record so far (edge < min_edge → correctly no bet). The old daily check-in
> trigger was deleted (purpose fulfilled).
>
> **PRIMARY NEXT ACTION:** the 2026-07-15..19 slate (T1, Gen.G, DRX, G2,
> Sentinels, Paper Rex … — these markets HAVE PM prices attached) resolves →
> have the operator run the rerunnable audit+eval one-shot (§0-FINAL; it
> re-clones HEAD so it auto-picks-up any new commits):
> `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "curl -fsSL
> https://raw.githubusercontent.com/swatson124455/polymarket-ai-v2/claude/esports-sharp-line-rebuild-gqy1na/deploy/vps/eb_label_audit.sh
> -o /tmp/ebl.sh && echo 'e56e8ed671f0c103e9ee68ac0a05a8a9  /tmp/ebl.sh' |
> md5sum -c - && bash /tmp/ebl.sh"` (single line) → interpret the first
> multi-record sharp-vs-PM edge backtest. Meanwhile you can check capture health:
> `tail -1 /home/ubuntu/eb-odds/collect.log` (expect `pm_matched>0` as the slate
> nears). **Secondary:** rotate `PANDASCORE_API_KEY` + `PINNACLE_ODDS_API_KEY`
> after the PoC (both were exposed in chat; env backup `/opt/pa2-shared/.env.bak_eb`).
> **Only after** a real multi-record edge readout: consider live-scan orientation
> wiring (PLUMBING §PREFLIGHT option b) + the un-halt discussion — operator
> decisions, not yours.
>
> **GUARDRAILS:** EB scope only; MB has priority on ALL shared resources; EB
> stays HALTED — do NOT deploy the trading bot (odds/results crons are not bot
> deploys); correct-or-absent everywhere (doubt → None, never a wrong bool — a
> flipped orientation inverts the edge); preserve other crontab lines on any cron
> edit; numbers only from cited sources and label n=19-era metrics UNSTABLE;
> commit + push each step.

---

## 0-FINAL. SESSION-3 CLOSE (2026-07-10) — GAP A CLOSED; PIPELINE FULLY LIVE E2E

**Both gaps are now closed. The entire chain ran end-to-end on real data.**
All numbers below are from the operator-pasted VPS eval output (2026-07-10
20:36 UTC run, HEAD `ebfc63d`).

- **GAP A closed:** `esports_v2/scripts/fetch_results_window.py` (stdlib-only,
  PandaScore, window-scoped, correct-or-absent, id-mapped winner fallback)
  pulls finished Valorant/CS2/LoL/Dota2/R6 matches; 226 rows for the 4-day
  window. The old PandaScore key was DEAD (`Invalid credentials`) — operator
  installed a fresh one in `/opt/pa2-shared/.env` (backup `.env.bak_eb`;
  **key appeared in chat → rotate after PoC**, same as the PinnOdds note).
- **First real labeled run: 19 of 36 closing lines joined** (0 ambiguous, 0
  winner-not-a-team; 17 unjoined = mostly not-yet-played 07-11..07-13 + a few
  lower-league PandaScore gaps). Sharp-line: favorite hit-rate 0.421 (n=19,
  book mean fav prob 0.635), Brier 0.270. **n=19 — UNSTABLE, do not act on.**
- **Label audit (operator-requested "bug review"): NO label flip.** Per-match
  dump (`dump_joined.py` / `deploy/vps/eb_label_audit.sh`) + independent web
  verification: LYON really 3-0'd G2 at MSI 2026 (07-10), and B8 as 1.4 fav
  over Virtus.pro matches world ranks (15 vs 52). The low fav hit-rate is an
  upset-heavy day on a tiny sample, not corruption. During review a REAL
  latent bug was fixed anyway: the fetcher's score-fallback winner was mapped
  positionally; now by `team_id` (never guess).
- **Coverage fixes from the audit's unjoined list:** +R6 (`/r6siege`, verified
  live) and diacritic folding in `normalize_team` ('Çilekler'=='Cilekler';
  guard test keeps 'KRU Spark' ≠ 'KRÜ Esports'). Joins 15→19 after fix.
- **Ops (steady state):** collector HOURLY with PM capture (`5fcb2c4f…`);
  PM-first-hit watcher installed + fired; the daily 15:06 UTC check-in trigger
  was DELETED at session close (purpose fulfilled — first hit confirmed); VPS
  scripts ship via `curl <repo raw> | md5sum -c | bash`.
- **What next:** let the 07-15..07-19 slate play (T1/Gen.G/DRX/G2 markets have
  PM prices attached) → rerun `deploy/vps/eb_label_audit.sh` (same command,
  re-clones HEAD) for the first multi-record PM-edge backtest. Only 1 PM-priced
  record so far (JD Gaming vs TYLOO; edge below min_edge, no bet — correct).

---

## 0a. SESSION-3 CONFIRMATION (2026-07-10) — GAP B CAPTURE VERIFIED ON REAL DATA

**PM price capture is CONFIRMED live.** First captured match (from the VPS
snapshot file, operator-pasted diagnostic output 2026-07-10):
`JD Gaming vs TYLOO` (Valorant VCT China, starts 2026-07-10T11:00Z) — **39
snapshots** 00:30→10:00 UTC, each with the PinnOdds line AND the Polymarket
price (0.405→0.415), `condition_id 0x8c395b57…`, `yes_outcome "JD Gaming"`.
**Orientation sanity check passed:** PinnOdds (2.27, 1.581) de-vigs to ~0.410
fair for JD Gaming vs PM price 0.405 — agreement within ~0.005 (a flip would
read ~0.41 vs ~0.595). Matching + orientation are correct on real data.

**Mismatch "bug" review (operator-requested, full-file audit via the DEPLOYED
matcher, `deploy/vps/eb_hourly_and_diag.sh`): NO BUG.** A) 0 missed matches
vs the live index; B) 0 date/window misalignments; C) all near-misses were
genuinely different matches (e.g. `M80 vs 100 Thieves` ≠ `M80 vs SaD Esports`).
Verdict: remaining `pm_matched=0` ticks are genuine calendar/genre non-overlap
(PinnOdds prematch horizon vs PM's 07-15+ slate), which section D enumerates.

**Known-bad check to never reuse:** `grep -c 'condition_id\": \"0x'` pasted
through PowerShell mangles the backslashes and returns 0 even when rows exist.
Count PM rows with the python diagnostic (or grep WITHOUT escaped quotes:
`grep -c '"condition_id": "0x'` run server-side from a script file).

**Ops state (2026-07-10):** collector cron HOURLY (`0 * * * *`, decided for the
PoC after 429 stalls at `*/15`); PM-first-hit watcher installed (`7 * * * *`,
`pm_hit_watch.sh`, marker `/home/ubuntu/eb-odds/PM_FIRST_HIT.txt` — already
written). VPS ops scripts now ship via `curl` from the repo raw URL +
`md5sum -c` guard (`deploy/vps/eb_*.sh`) — no more base64 chat pastes.

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
- **DECIDED (operator, 2026-07-10): HOURLY for the proof of concept.** `*/15`
  kept draining the demo quota (multi-hour 429 stalls; file frozen at 994 lines
  16:45→18:15 UTC). Cron edited to `0 * * * *` (24 calls/day). Closing line (last
  pre-start snapshot) is still captured; only intraday density is lost. Rollback:
  restore the `*/15` crontab line. Revisit paid tier if PoC shows edge.
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

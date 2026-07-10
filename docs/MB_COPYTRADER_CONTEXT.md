# MirrorBot Copy-Trader Investigation — FULL CONTEXT BRIEF

**Written 2026-07-10 for session handoff. This is the complete reasoning chain,
not just conclusions. Read `CLAUDE.md` first (binding), `docs/MB_STATE.md` §1a
for the compressed state, THIS FILE for everything behind it.**
**Branch: `claude/mirrorbot-persistence-check-oc02tk` (PR #1). All code pushed.**

---

## 0. The mission in one sentence

Find Polymarket traders whose profit is a REPEATABLE PROCESS — provably, on
their complete bet histories, with no self-deception — so MirrorBot v3 can copy
them; or prove no such traders are findable and close the thesis honestly.

## 1. How we got here (decision log — each step was an operator decision)

1. **Old MirrorBot is dead.** Whale-copy strategy audited at no measurable
   edge; paused to paper 2026-07-05. Clean silo `mirror_v3/` built, strategy
   slot empty behind an acceptance gate.
2. **The prior "Stage-1 FAIL" of the ranking engine is hearsay** — its commit
   (`172d72a`) was never pushed and is unrecoverable. Worse, a 61-agent
   adversarial review (2026-07-09) proved the in-repo validation harness
   (`bots/mirror_scoring/validation.py`) is CIRCULAR — a false-PASS machine.
   **Never clear anything with it** (MB_STATE §7 landmine).
3. **Two interim instruments** were built and run on the rejected-signals
   corpus (tail backtest at realistic lag; cross-period persistence check).
   Results: pooled dead, sports a suggestive-but-unconfirmed lead. Both are
   now SECONDARY.
4. **The operator redirect (the pivot):** *"Why the fuck are we running on
   leftovers?"* — the rejected-signals corpus is what the broken bot DECLINED,
   at the broken bot's latency, missing every trader's wins. The public APIs
   (and the chain) hold every bet every trader ever made. Test on THAT.
   Also: *"We are not just copying a trader, we are finding traders that are
   copyable."* Test per-trader, not pooled.
5. **Full-history search built** (`scripts/find_copyable_traders.py`),
   adversarially reviewed BEFORE running (2 agents; 4 critical flaws fixed
   pre-run: leaderboard collider, newest-first truncation, cache poisoning,
   seq-scan timeout). Run 1 completed clean — see §3.
6. **Grading redesigned with the operator into the WALK-FORWARD rule** after
   two objections he raised killed simpler designs — see §2. This is now the
   LEAD instrument (`scripts/walkforward_copy_traders.py`).
7. **Pre-run tinker** (operator: *"tinker before we spend 7 hours"*): bot
   exclusion + gamma label backfill + cache deepening — see §4.
8. **A 3-stage detached pipeline is running on the VPS.** Next action = read
   its output (§6).

## 2. The grading rules and WHY each exists (operator-locked, do not re-derive)

**Edge (the unit of everything):** every share pays $1 if right, $0 if wrong;
price = the market's implied odds. Edge per bet = (won ? 1 : 0) − price, in
probability points per contract. Averaged per market (first BUY per market —
one whale spraying 50 orders into a game counts once). Size-proof: a $500k bet
on a 99¢ favorite has huge P&L and ~1¢ edge — nothing copyable. **Why not
lifetime winnings:** winnings count the bets that BUILT the record; a copier
only ever gets the bets AFTER the record exists. Lucky traders ace "who won,"
flunk "did knowing they won make you money afterward."

**Walk-forward (the deployable rule, tested as it would run):** step through
time monthly; at each review, using only information that existed then:

- **HIRE on the lifetime résumé:** ≥25 distinct resolved markets, prices
  2–98¢, record spanning ≥60 days, positive mean edge, bootstrap
  P(edge>0) ≥ 0.90. *Why lifetime:* the operator rejected trailing-form
  qualification — "people have bad months, even the goats" — monthly form is
  noise; 300 markets of history barely move on one cold month. *Why the span
  floor:* 30 wins in one hot tournament week is one lucky regime. *Why
  improvers still get caught:* a bad-start/good-finish trader qualifies the
  moment the good run outweighs the bad start in the cumulative record, and is
  graded only from then — which is exactly how you'd copy him live.
- **FIRE only on convincing RECENT decay:** trailing 90 days, ≥10 markets,
  negative mean AND P(edge<0) ≥ 0.90. *Why:* the operator caught the
  lifetime-only hole — "win 1 milly then bleed 5k/month for years" stays
  qualified forever on lifetime alone. The fire test never sees old glory.
  A GOAT's cold month can't clear a 90% bar; sustained real decay can.
  A fired trader re-enters only when the decay signal clears.
- **GRADE = only post-hire bets**, pooled market-clustered edge + per-interval
  consistency. Nothing before a hire date ever counts.
- **Honesty rails:** review grid locked to anchor 2025-01-01 (never
  data-chosen) with ±15-day shifted robustness grids that can VETO a PASS;
  outcome knowledge gated by `markets.resolved_at` (a bet on a market that
  resolved at day 200 is not roster evidence at day 100 — no peeking at
  long-dated results; missing resolved_at falls back to entry time, counted
  and printed); **primary verdict = VOL-sourced, non-truncated universe
  only** (profit-board membership is earned partly by recent wins — a collider
  that inflates measured future edge, same circularity class as the broken
  validation harness; the volume board selects on activity, not outcomes;
  PNL+VOL double-listers count as VOL). PASS needs P≥0.95 on ≥30 markets from
  ≥5 rostered traders + robustness-grid agreement. FAIL-TERMINAL only if the
  bootstrap upper bound is below the +0.02 econ floor; else INCONCLUSIVE.

Self-test proves all four personas mechanically: goat survives a cold month,
$1M-then-bleeder passes the lifetime bar but gets fired on recent decay,
improver hired only after turnaround with zero pre-hire bets graded,
hot-streaker blocked by the span rule. (`walkforward_copy_traders.py --self-test`)

## 3. Run 1 results (find_copyable_traders, 2026-07-09) — what's real, what isn't

Clean run: 496 leaderboard addresses (timePeriod=ALL, PNL+VOL lists), 0
partial failures, resolutions chain-verified 20/20. Regression-to-mean visible
(top P1 stars at +0.15 collapsed in P2 — the lucky filter demonstrably works).

- **Real and promising (descriptive):** P1-qualified traders held **+2.3pts P2
  edge, P=0.962, 1,065 markets, 22 traders**. First broadly positive result of
  the entire investigation. Politics strongest (+5.0pts, P=0.957).
- **Why it is NOT a verdict:** includes PNL-collider-tainted traders, and
  politics-is-best is a post-hoc cell pick across 6 cells. Leads, not results.
- **Primary UNDERPOWERED, not failed:** 329/496 histories truncated at the
  20k-bet cap; the VOL whales are by definition the deepest → clean cell
  starved to 1 trader. Design collision, since fixed (§4).
- **Named strong-both-halves candidates** (pending clean re-test + chain
  audit): `0x6bab41a0dc` (264 P1 mkts +0.067 P=1.00 → 109 P2 mkts +0.089
  P=0.99), `0xd1acd3925d` (242 → 25 mkts +0.150 P=0.96), `0xa6a856a8c8`
  (sports, 593 P2 mkts +0.025 P=0.89), `0x4dfd481c16` (politics, 17 mkts
  +0.410 P=1.00).
- **Label coverage was only 24%** — everyone judged on a quarter of their
  record (power loss + recency/popularity skew, but cannot fake a positive:
  missing labels drop bets, they don't flip outcomes).

## 4. The tinker fixes (why the current pipeline is ~2h, not 7h)

1. **Bot/market-maker exclusion** (`--hft-max-rate 200` bets/day, both at
   download time via first-page rate and at analysis time): accounts trading
   hundreds of times daily are bots — mechanically uncopyable (spread capture,
   not knowledge) and they were ~all of the truncation and most of the
   download budget.
2. **Gamma resolution backfill** (`scripts/backfill_resolutions_gamma.py`):
   pulls outcomes for markets our DB never ingested into
   `<cache>/gamma_resolutions.json`; graders merge it UNDER the DB map (DB
   wins, holes filled). Wording-independent labels (resolution=YES means
   token[0] won, stored with clobTokenIds) so team-name binaries label
   correctly; only closed markets with a definitive ≥0.99/≤0.01 payout are
   labeled — split/open markets skipped and counted, never guessed. Expected
   coverage ~80–95%; the residue is mostly markets that haven't resolved
   (unlabelable in principle) + voided/split (shouldn't label) + a small
   slug-key bucket (fixable with a slug-lookup pass if the report shows it's
   big).
3. **Cache deepening** (`should_refetch` + `--deepen vol`): truncated cache
   entries re-pull when --max-bets rises (they used to be reused forever);
   only VOL-sourced traders deepen (the primary set), everyone else rides the
   cache.

## 5. Hard-won API/infra facts (do not rediscover these the expensive way)

- **data-api /activity rejects offset ≥ 3500** (400). Deep histories are
  walked by TIME-WINDOWED pagination: offset-page to 3000, re-anchor `end` at
  the oldest timestamp seen, repeat; cross-window dedupe (tx hashes are shared
  by multi-fill transactions — dedupe key includes token/ts/price/size).
- **Leaderboard must pin `timePeriod=ALL`** (unpinned default is a recent
  window → irreproducible, outcome-selected universe). Offset caps at 1000.
- **gamma `/markets?condition_ids=` is silently ignored** (200 + `[]` — two
  full backfill runs labeled ZERO before this was caught, 2026-07-10). Label
  lookups by condition id go to CLOB `/markets/{cid}` per key (production-
  proven; `resolution_backfill.py:17`). Numeric ids: gamma `/markets/{id}`.
- **`pkill -f` from an SSH one-liner must use a bracket pattern**
  (`backfill_resol[u]tions`) or it matches the one-liner's own shell and
  kills the session mid-command.
- **Never cache a partial pull** (timeout/circuit-breaker mid-pagination) —
  it silently becomes a "complete" record and corrupts every later run.
- **markets lookups must be two index-backed queries** (condition_id ANY /
  integer id ANY) — the OR+CAST form seq-scans and times out on the live DB.
  Same lesson on mirror_rejected_signals: only `(rejection_stage, event_time)`
  is index-backed; full-table resolution scans time out at 300s.
- **The VPS runs commands via one-line SSH from the operator's Windows
  PowerShell** — always hand him a single `ssh -t -i ~/.ssh/
  LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "..."` one-liner
  (multi-line paste breaks; he cannot paste after entering the VPS). Long jobs:
  wrap in `nohup ... &` so they survive the SSH window.
- Deployed code lives at `/opt/polymarket-ai-v2` (venv there); session code is
  cloned to `/tmp/mbpc` and run with `PYTHONPATH=/tmp/mbpc` — never deploy to
  the live tree for analysis.
- History cache: `/tmp/copyable_cache` (per-address JSON + gamma_resolutions
  .json). Results JSONs: `/tmp/copyable_traders.json`, `/tmp/walkforward.json`.

## 6. WHAT IS RUNNING RIGHT NOW + the decision tree

A detached 3-stage pipeline on the VPS (started ~2026-07-10 01:00 UTC):
gamma backfill → `/tmp/gamma_backfill.log`; deepen (humans only, 120k cap,
--deepen vol) → `/tmp/copyable3.log`; walk-forward → `/tmp/walkforward.log`.

Check: `ssh ... "tail -3 /tmp/gamma_backfill.log /tmp/copyable3.log /tmp/walkforward.log"`

When `/tmp/walkforward.log` prints its table, read the PRIMARY verdict:

- **PASS** → the rostered addresses are the deliverable. Sequence: per-fill
  OrderFilled chain audit (`blockchain_client.query_exchange_order_filled_
  events`) on those addresses (MANDATORY before any money decision), then the
  fill-quality gate (`scripts/backtest_copyable_fills.py --from-json ...` —
  audited pessimistic coarse model at the real ask; SURVIVES / FILL-KILLED /
  NO-BOOK per trader), then operator decision on a v3 forward PAPER deploy.
  Never a live deploy from a backtest.
- **FAIL-TERMINAL** → the copy thesis is closed retrospectively on the best
  data that will ever exist. Say so plainly; only a forward shadow test could
  revive it.
- **UNDERPOWERED / INCONCLUSIVE / NO-DATA** → widen DATA, never loosen
  thresholds: check achieved label coverage (gamma report), slug-key residue
  (add a slug-lookup pass if big), primary-universe size after bot exclusion
  (raise --universe), then rerun. Every stage is resumable; caches make
  completed work free.

## 7. Binding discipline (violating these voids any result)

1. **No rework-then-retest until an instrument passes** — that's p-hacking.
2. **Verdicts only from pre-registered primary cells.** Descriptive cells
   (including politics' +5pts) are leads for FUTURE pre-registration, never
   promotable after peeking.
3. **Widen data when underpowered; never loosen thresholds after seeing data.**
4. **Every number is cited with its coverage/sample qualifiers** (CLAUDE.md
   Forbidden Patterns 8/9/10). A trader with 12 P2 bets is a candidate, not a
   conclusion.
5. **Chain wins over API/DB on any mismatch.** Per-fill chain audit before any
   money decision names a specific trader.
6. **`bots/mirror_scoring/validation.py` PASSes mean nothing** (circular).
   Don't run it for decisions.
7. All scripts here are read-only vs the DB and GET-only vs APIs. Keep it so.

## 8. Tooling map (all on this branch, all with --self-test + pytest suites)

| Tool | Role |
|---|---|
| `scripts/find_copyable_traders.py` | full-history P1/P2 search; builds/maintains the history cache; run 1 done |
| `scripts/walkforward_copy_traders.py` | **LEAD**: hire/fire/grade walk-forward — the deployable rule |
| `scripts/backfill_resolutions_gamma.py` | label coverage 24% → 80%+; resumable |
| `scripts/backtest_copyable_fills.py` | fill-quality gate for a NAMED roster (post-PASS) |
| `scripts/backtest_tail_leaderboard.py` | secondary: lag screen on rejected corpus (superseded) |
| `scripts/check_trader_persistence.py` | secondary: cross-period autocorrelation (superseded) |
| `tests/unit/test_{find_copyable_traders,walkforward_script,copyable_fills_script,tail_backtest_script,persistence_check_script}.py` | 60+ tests; run with `python3 -m pytest ... --override-ini "addopts="` |

Sandbox notes: no DB, no Polymarket network access (proxy 403) — verification
in-sandbox = self-tests + pytest + static reads. The operator runs everything
real on the VPS and pastes logs back.

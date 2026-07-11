# SWOT / EV Audit — polymarket-ai-v2 pilot system

> **TRIAGE ADDENDUM (operator review, 2026-07-11) — read before citing anything below.**
> Operator frame supersedes this audit's original scope: **only MB/WB/EB exist**; total P&L is
> known-corrupted and banned as an input; devig is decided IN; this session is now MB-only.
> Retired as BAD INTEL / noise: the 2026-04-13 fleet edge verdicts (stale, pre-fix, P&L-derived),
> the EnsembleBot/LLMForecaster orphan finding, all non-3-bot fleet items (W2, W5, O9, and the
> live-reconciliation thread O7/W4 — total-P&L class). Survives as current, code-verified, or
> canonical-script-verified: S1–S3, S5, W3, W7, O1–O6, O8 (EB lane), T1–T7. WB figures (S4, W6,
> O4) are structure-valid but magnitude-stale — re-derive via `bot_pnl.py` before acting.

**Date:** 2026-07-11 · **Session branch:** `claude/polymarket-ai-v2-setup-7tq1sh` · **Tree audited:** `c88d50d`
**Method:** read-only repo + docs audit (6 parallel sweeps: fleet, money path, edge evidence, data assets, EB/WB lanes, MB lane context). No code modified. No DB access from this session.

> **NUMBERS DISCLAIMER (binding, per CLAUDE.md Protocol 11):** Every figure in this report is a
> **documented claim quoted from a repo doc**, cited as *(doc, date)*. None is a fresh measurement.
> The only edge verdicts on record are from `EDGE_VERIFICATION_1I_RESULTS.md` (2026-04-13) and were
> **never re-run** — they are ~3 months stale. All P&L figures are **paper** unless explicitly
> marked live. Counterfactual EV figures are quarantined upper bounds (hold-to-resolution,
> entry-price-only, early exits excluded).

> **Scope note:** MirrorBot is owned by a dedicated session (hard fence). MB items here are
> **awareness + recommendations routed to the operator/MB session** — nothing in this report
> authorizes touching `mirror_v3/**`, the MB branch, or the `polymarket-mirror3` shadow watcher.
> (That watcher exists on the VPS per operator fence; it is *ahead of* repo docs, which still list
> v3 signal collection as an open to-do — MB_STATE.md §5.)

---

## 0. Bottom line

**Is there meat on the bone? Yes — but it is specific meat, and none of it is a proven edge yet.**

The system's honest current state: a **strong measurement-and-execution chassis with no validated
positive edge attached to it**. All three lanes ever measured showed negative paper edge as of
2026-04-13 (WB −14.67%, MB −7.20%, EB −14.74% raw edge; *EDGE_VERIFICATION_1I_RESULTS.md*), the
old whale-copy and esports-ratings strategies have both been formally killed since, and 11 of 14
registered bots have **no edge documentation at all**. What survived the kills is valuable: a
fail-closed acceptance-gate harness, ~17.5M rejected-signal labels still accruing for free, a
documented +EV trader subset inside the mirror feed, one genuinely profitable WeatherBot pocket,
and an ~80%-built esports sharp-line pipeline. EV is god — and the highest-EV moves right now are
mostly **measurement unlocks costing hours or small vendor fees**, not new strategy code.

---

## 1. STRENGTHS

**S1. The money path is EV-proportional and realistic — rare for a pilot.**
Per-bot fractional Kelly (`f* = (p·b − q)/b`, quarter-Kelly default) with calibration/Brier
down-weighting, drawdown compression, and layered caps (`bankroll_manager.py:312-458`). Paper
fills walk **real L2 ladders** (VWAP over asks/bids), model partial fills, intra-scan book
depletion, and whale-liquidity consumption (`paper_trading.py:28-158, 484-511`). Shadow-fill
telemetry records edge-at-signal vs edge-at-VWAP for retroactive EV analysis (`paper_trading.py:875-929`).

**S2. The system kills its own bad ideas — institutionalized skepticism.**
MomentumBot deleted; esports v1 killed on 4/4 kill criteria, P(edge>0)=0.002 (*S175 handoff,
2026-04-14*); whale-copy scoring declared dead — "signal indistinguishable from zero, no +EV
wallet subset" (*MB_REBUILD_PLAN.md, 2026-07-02*). The MB rebuild gate admits **nothing** without
market-clustered bootstrap P(edge>0) ≥ 0.95 on the PRECISE fill model, ≥30 markets, with FAIL as
the stated prior (`bots/mirror_backtest/gate.py:26-97`). "Algo proposes, backtest disposes" is a
real, tested mechanism, not a slogan.

**S3. A defensible data moat, still growing at zero cost.**
`mirror_rejected_signals` **17.59M rows** → ~5.06M deduped labeled whale signals (crypto 73% /
sports 17% / esports 5%), 286k gate-labeled; `shadow_fills` **12,713 real L2 book snapshots** (the
only true depth data); `whale_trades` ~270K rows/day; 32,369 labeled esports matches + the
1,777-row team-alias resolver ("hardest rebuild piece, already built"); 91 weather stations on
free keyless APIs. (*m0_db_results_2026-07-02.md; EB_REBUILD_CARRYFORWARD.md 2026-06-23.*) The
paused-to-paper old MB **keeps writing the signal stream** — data accrues while nothing is at risk.

**S4. One documented profitable pocket exists.**
WeatherBot NO-side long lead-time: "72–120h NO: +$773.71 at 88.8% WR — PROFITABLE, do not touch"
and 48–72h NO +$418.36 (*bot_pnl.py output quoted in WB S162 handoff, 2026-04-08; paper*). The
remaining WB loss is **localized to one bucket** (NO×24–48h: 116 trades, −$814.57 ≈ half of total
NO loss; *S203_WB_PHASE6, 2026-04-29*) — a surgical target, not a diffuse failure.

**S5. Clean-silo rebuild pattern with fail-closed safety.**
`mirror_v3/` boots with env allowlist guard, 4-scope guards, ordering-safe state restore, 22
tests; ends the documented env-drift-to-live footgun for that lane. 3,449+ tests fleet-wide
(*WORK_PROGRAM.md claim*). Canonical measurement scripts (`bot_pnl.py`, `verify_salvage_data.py`)
plus codified forbidden patterns give the system unusually strong epistemic hygiene on paper.

---

## 2. WEAKNESSES

**W1. No lane has a validated positive edge; the verdicts on file are negative and stale.**
The only edge verification (2026-04-13) found negative raw edge in all three measured lanes and
was never re-run after subsequent fixes. Everything since is diagnostic or counterfactual. The
pilot has not yet produced one gate-passing strategy.

**W2. The documented flagship is dead-wired.** EnsembleBot — "primary directional trading bot,"
largest documented capital ($8,000), 11-model ensemble, `BOT_ENABLED_ENSEMBLE=true` — is **not in
BOT_REGISTRY and never imported in main.py** (0 references). This also strands LLMForecasterBot
(a data producer whose only consumer is the orphaned Ensemble). Highest-capital strategy on paper,
zero live EV contribution.

**W3. Paper P&L is an optimistic estimator of live EV — four inflators on by default.**
(a) taker fee defaults to 0 bps (`paper_trading.py:596-598`); (b) trades without a book snapshot
fill at signal price with **zero slippage** (`:456-457`); (c) fills exceeding 10–20% adverse move
are *rejected* in paper where live would fill worse — survivorship bias (`:565-574`);
(d) latency drift off by default (`:530`). "Paper trading is production" (CLAUDE.md) is
undermined exactly at the fill-realism margin that determines whether paper EV predicts live EV.

**W4. Live-dollar EV is currently unmeasurable.** 44 of 57 live position rows lack cost basis;
0 live RESOLUTION events ever recorded; "a complete historical realized-P&L dollar figure is
unrecoverable from internal data" (*LIVE_ONCHAIN_RECONCILIATION_2026-06-03.md*). The system can
measure paper EV precisely and live EV not at all.

**W5. Fleet-scale dead weight and doc/code drift.** 10 of 14 registry bots are off-by-default,
mostly "never traded," blocked on absent keys (SportsDataIO, PandaScore, Kalshi RSA); every
`BOT_*.md` is ~4 months stale with ≥3 enable-status contradictions (CrossPlatformArb doc=NO vs
code=true; LogicalArb doc=YES vs code=false; Esports doc=RUNNING vs code=false);
`PROJECT_STATUS.md` is frozen at 2026-02-21 with a roster that no longer exists. State legibility
is itself an EV cost: sessions burn time re-deriving truth.

**W6. WB YES side is structurally broken.** −$24,424.90 of the −$31,788.02 all-time paper
drawdown is YES-side at 37.8% WR (*WB S162 handoff, 2026-04-08*); the YES calibrator is identity
passthrough (n=62 < 100). The "never disable sides" invariant means this must be fixed, not
switched off.

**W7. Shared-infra hazards.** Two parallel Kelly implementations (deprecated
`risk_manager.calculate_position_size` still present — divergence risk); phase cap parsed in two
places with different fallbacks; fail-open state restores (a failed seed silently under-counts
exposure → over-trading after restart); global-not-per-bot daily loss limit ($50 default) that
kill-switches the whole fleet.

---

## 3. OPPORTUNITIES — the meat, ranked by EV per unit cost

**O1. Run the mirror_scoring validate. Cost: one operator command. (Highest information/$ in the system.)**
The kill-criterion validation over 286k gate-labeled signals has **never actually run** — it
crashed on a one-char bug (`db.initialize()` vs `init()`), the fix landed (`8ea683d`), and the
re-run (`deploy/mb_vps_oneshot.sh`) is still pending. Until it runs, the entire trader-scoring
lane is unvalidated. *(Owner: operator + MB session — fenced from this session.)*

**O2. The mirror feed's +EV subset — the single most concrete edge signal on file.**
Of well-sampled tracked traders (≥200 resolved signals), **~43% are +EV after fees but produce
only ~13% of volume** (*MIRRORBOT_FILTER_AUDIT_S244 addendum v3, 2026-06-13, quarantined
counterfactual*). The aggregate feed is ~−EV after fees (+0.0086/$1 pre-fee), so the play is
**selection, not tuning**: prune the watchlist to the validated subset, gated through the
acceptance harness. Adjacent: the opposing-side filter family blocks signals with counterfactual
+0.163/$1 on 50,509 — flagged in docs as needing flip-cost analysis + operator sign-off before
any change. *(Owner: MB session.)*

**O3. Sharp-line pipeline unlock — small vendor spend converts built-idle harnesses into live edge tests.**
MB lane: `sharp_reference.py` core is built + tested; blocked only on the **OddsPapi paid tier**
(key presence + response-shape verification) and a sports name→condition_id matcher (EB's alias
matcher is reusable). EB lane: forward-collect Pinnacle closing odds into the empty
`esports_odds` table (~2–4 weeks) — **after resolving the "devig was killed fleet-wide" decision**,
which currently blocks the whole EB pivot thesis (*EB_REBUILD_CARRYFORWARD.md Part 4 #1,
2026-06-23*). Sports+esports = 22% of signal volume and is the *knowledge* (tailable) share.

**O4. WeatherBot surgical calibration — the only lane with a live profitable pocket.**
Three defined, cheap moves: (a) confirm NO×24–48h Brier exceeds train_brier=0.237 via the single
SQL already specified (*S203_WB_PHASE6 §6*); (b) side/lead-time-targeted sizing (dampen NO 24–48h
and YES, protect NO 48–120h — S162 already moved this direction); (c) cold-start calibration
research (regional EMOS pooling / hierarchical prior) targeting 20 days → ~3 days, which matters
because Polymarket rotates cities. *(Owner: WB session.)*

**O5. Crypto kill-test — unblocked, formally drops 73% of the signal firehose.**
Run crypto signals through the fill-replay harness at realistic 60s latency to confirm the
latency-trap hypothesis (*MB_STATE §5*). Expected outcome is FAIL — which is the point: it
converts an assumption into a measurement and concentrates all future effort on the tailable 22%.

**O6. Make paper EV honest — flip the realism knobs.** Set nonzero taker fee where markets charge
one, enable latency drift, and log (not reject) adverse-slippage fills. Hours of config/code, and
every downstream paper number becomes a defensible EV estimate instead of an upper bound.

**O7. Make live EV measurable.** Purchase the Polygonscan key and run the on-chain
reconciliation the 2026-06-03 doc specifies; backfill cost bases. Until then "going live" cannot
even be scored.

**O8. EB LoL-only flip — the one cohort that passes.** "LoL singletons pass cleanly with strong
margins (BSS strongly positive across windows)" (*S209 close, 2026-05-02*), while CS2 fails.
A staged LoL-only `ESPORTS_V2_DRY_RUN=false` flip is a defined, evaluable EV path — **hard-gated**
on the two S203 BLOCKING accounting fixes (bot_pnl/edge_verification bot_name cohort split).
GRID Open Access (free CS2 data) remains an unclaimed unlock. *(Owner: EB session.)*

**O9. Registry hygiene as EV.** Decide EnsembleBot: wire it into BOT_REGISTRY or delete it and
LLMForecasterBot's stranded pipeline. Either answer frees capital-of-attention; the current state
(flagship documented, unreachable) is pure drag. Same for the 0-byte `ml_score_*` stubs and the
non-reproducible manifest claims (~1,421 positions vs 191 measured).

---

## 4. THREATS

**T1. The skeptical priors may all be right.** The MB gate's own stated expectation is FAIL; the
EB pivot's core transform (Shin devig) was "killed fleet-wide" pending a decision; the mirror feed
aggregate is −EV after fees. It is a live possibility that the pilot's honest terminal state is
"no tailable edge at our latency/fee structure" — in which case the chassis's value is capped at
what the WB pocket and trader-subset selection can carry.

**T2. Env-drift-to-live.** The VPS was live-config by default (`SIMULATION_MODE=false`,
`CANARY_STAGE=4`) before the 2026-07-05 pause, and `CANARY_AUTO_ADVANCE` **defaults to true in
code** (`scheduler.py:579`) — a fresh or misconfigured host auto-promotes real capital behind
strategies whose only measured edges are negative. `mirror_v3`'s env guard fixes this for one
lane; the pattern is not fleet-wide.

**T3. The neg-risk gateway landmine.** The `order_gateway` neg-risk BUY block no-ops today by
key-mismatch accident; "repairing" it silently recreates Bug 14 (election/tournament blackout).
Codified in CLAUDE.md; any gateway indexing work must neutralize the block in the same commit.

**T4. Counterfactual optimism.** Every current +EV signal (trader subset, opposing-side family)
is hold-to-resolution, entry-price-only, fee-approximated, early-exits excluded — the docs
themselves warn these numbers "would get worse under real early-exit modeling." Shipping on them
without the fill-replay gate would repeat the exact failure mode that killed whale-copy.

**T5. Vendor and key dependencies.** OddsPapi paid tier unpurchased with UNVERIFIED response
shape (live fetch raises NotImplementedError); Pinnacle access for individuals unresolved
(pinnapi.com probe never run); Valorant coverage gap ~1,589 markets; sports/esports v1 bots
inert without SportsDataIO/PandaScore/Kalshi keys. Multiple EV paths dead-end at procurement.

**T6. Verdict staleness + process risk.** The negative-edge verdicts are ~3 months old and
pre-date many fixes — decisions in either direction (kill or scale) on stale numbers are both
errors. The EB lane's own 186-instance overclaim audit (*EB_SESSION_ERROR_AUDIT.md*) shows how
easily this codebase's narrative drifts from its data; the strongest defense is the
already-codified rule that only canonical scripts count.

**T7. PRECISE-model coverage is thin.** 12,713 L2 ladder rows total against a gate requiring ≥30
distinct precise markets per rule — honest backtesting is possible but coverage-starved, and the
v3 rejection-logging/RTDS plumbing that would grow it is still a to-do (repo docs; the VPS shadow
watcher now collecting is ahead of the documented state).

---

## 5. Recommended action queue (EV-ranked, with owners)

| # | Action | Cost | Owner | Unblocks |
|---|--------|------|-------|----------|
| 1 | Re-run `mb_vps_oneshot.sh` (scoring validate go/no-go) | 1 command | Operator | Entire trader-scoring lane |
| 2 | Decide devig question (EB #1 open decision) | 1 decision | Operator | Entire EB pivot thesis |
| 3 | OddsPapi paid tier + key presence check | small $ | Operator | MB sports sharp-line gate test |
| 4 | Crypto kill-test at 60s latency | build, unblocked | MB session | Drops 73% of noise, focuses effort |
| 5 | Start Pinnacle forward-collection into `esports_odds` | ~0 + 2–4 wk clock | EB session | EB CLV go/no-go |
| 6 | Paper-realism knobs (fees, latency, log-not-reject slippage) | hours | Any session (shared-module protocol!) | Honest paper EV fleet-wide |
| 7 | WB NO×24–48h Brier confirmation SQL + targeted sizing | hours | WB session | Stops the one localized bleed |
| 8 | Polygonscan key + on-chain reconciliation | small $ | Operator | Live EV measurability |
| 9 | EnsembleBot wire-or-delete decision | 1 decision | Operator | Registry truth, attention |
| 10 | Re-run edge verification post-fixes (refresh 04-13 verdicts) | script run | Operator | Un-stales every verdict |

Items 1–3 are pure operator unlocks — the highest-EV moves in the system cost approximately one
command, one decision, and one small subscription. **Note item 6 touches `base_engine/paper_trading`
(shared module): MB has standing priority; operator authorization required before any session edits it.**

---

## 6. SWOT one-screen summary

| | Helpful | Harmful |
|---|---|---|
| **Internal** | **S:** EV-proportional Kelly sizing; L2 VWAP paper fills; acceptance-gate + kill-criteria culture; 17.5M-signal data moat growing free; WB profitable NO long-lead pocket; clean-silo pattern | **W:** zero validated positive edges (stale negative verdicts); flagship bot dead-wired; 4 paper-EV inflators on by default; live P&L unrecoverable; 10 never-traded bots + doc drift; WB YES side broken; dual Kelly paths, fail-open restores |
| **External** | **O:** +EV trader subset (~43% of well-sampled, quarantined counterfactual); sharp-line harnesses built-idle behind small vendor spends; LoL-only flip; WB calibration surgery; crypto kill-test; cheap measurement unlocks (validate run, Polygonscan, edge re-run) | **T:** skeptical priors may hold (no tailable edge); env-drift-to-live + auto-advance defaults; neg-risk landmine; counterfactual optimism; vendor/key dead-ends; thin L2 coverage; verdict staleness |

---

*Prepared read-only by the setup session. MB items route to the MB session per the standing fence;
shared-module items require operator authorization per CLAUDE.md priority rules. No experiment was
run in this audit, so no verdict criteria were pre-registered; every number above carries its
source doc + date and the paper/counterfactual qualifier.*

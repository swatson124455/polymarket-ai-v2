# WB REBUILD — TEARDOWN / BUILD-UP KICKOFF (written 2026-07-23, operator-directed)

> **Operator directive:** "scrap the plan but keep all data, we are breaking down
> and building up" + "review all options and leave nothing unturned, deep research."
>
> This supersedes `WB_S235_KICKOFF_PROMPT.md`. That file and every prior plan doc
> are KEPT as record (scrapped ≠ deleted) but are **no longer the roadmap**.

Paste this whole file into the new session. Standing rules still bind: NEVER
quote P&L (calibration only); WB-ALWAYS-GLOBAL (no US-only filters, ever); no
cross-bot vendor/secret bleed; one fix per commit; RULE ONE-A (WB never touches
MB). The calibrator hands-off runs to ~08-07 — **that is a constraint on
CHANGING it, not on measuring or designing.**

---

## 0. YOUR MANDATE

Do **not** resume the old program. Start from the evidence and answer one
question exhaustively:

> **Is there any configuration in which WeatherBot has positive expected edge —
> and if so, which one, on what evidence?**

Every option in §4 must be examined and either advanced with evidence or killed
with a stated reason. "Leave nothing unturned" is the operator's phrasing: an
option dismissed without a written reason is not dismissed. Deliver a ranked
recommendation, not a survey.

**You are permitted to conclude that the answer is "none."** That is a valid,
valuable finding. Do not manufacture an edge to have something to build.

---

## 1. DATA — ALL PRESERVED, VERIFIED 2026-07-23 02:1xZ

Nothing was deleted and nothing should be. Verified counts:

| asset | state |
|---|---|
| `prediction_log` (WeatherBot) | **229,600 rows, 2026-03-11 → 2026-07-23** — the irreplaceable asset |
| `trade_events` | 6,999 total / **4,093 WB**, 13 partitions |
| `weather_calibration` | 27,375 · `weather_climatology` 39,162 · `weather_forecasts` 24,133 |
| `positions` 8,995 · `paper_trades` 3,273 · `traded_markets` 17,398 · `equity_snapshots` 312 |
| DB backups | daily dumps, current **07-22 05:28, 7.9 GB** (`/opt/pa2-backups/`) |
| research | **105 MB** `~/wb_research` incl. **21 study outputs** (`.out`/`.json`) |
| feeds | 23 MB `/opt/pa2-weather-feeds` (pws_mesh + nat_mesh + mesh_debias), mesh files from 07-16 |
| Maker feed | 7.3 MB `/opt/pa2-maker-feeds/wb_forecasts.jsonl` |

⚠ **`pg_stat_user_tables` reports `trade_events` n_live_tup = 0.** That is a
partitioned-parent artifact, NOT data loss. Always `COUNT(*)`.

---

## 2. WHAT IS SCRAPPED

Marked dead as a *plan*; the documents stay for their evidence.

- The Phase-2 nowcast program **as a roadmap** (peakpass, signal-2, the queue).
- **VIF tuning as "the answer."** VIF 1.4→1.8 did not fix overconfidence
  (see §3.1). VIF→2.0 may still be worth doing, but it is a patch, not a plan.
- `WB_S235_KICKOFF_PROMPT.md`'s QUEUE and WATCH ordering.
- Any assumption that the current model architecture is the thing to improve.

**NOT yours to scrap:** the S172 system-wide consolidated plan (cross-bot), and
anything owned by MB/EB/Maker.

**Keep running, do not switch off:** the mesh/nat_mesh/mesh_debias crons and the
Maker feed. They are cheap, they are accruing the data any rebuild needs, and
one of them (mesh lead) is the only proven asset. Live trading state is the
operator's call — the bot is paper, so accrual costs nothing but produces labels.

---

## 3. HARD FINDINGS THAT SURVIVE — DO NOT RE-DERIVE

These are expensive and verified. Re-deriving them is waste; contradicting them
requires better evidence, not a fresh opinion. All from
`docs/WB_NOWCAST_CAPTURE_SPEC.md` (S234 sections at the tail).

### 3.1 The model is worse than doing nothing
Deduped to **230 distinct markets**, since 20260713_160229:
- Model Brier **0.3686**, **BSS vs climatology −0.4748** (47% worse than the base rate).
- PIT mean **0.593**, KS p=0.0000 → **overconfident, predictions too extreme**.
- Uniformly negative across every lead-hour block, stable in a split-half test
  (12/12 blocks negative in both halves) — a real deficiency, not noise.
- The second half is mostly POST-VIF-1.8 and still uniformly negative.

### 3.2 We beat the market in ZERO slices
- Model 0.3686 vs **market 0.2468** → skill vs market **−0.4935**.
- Every sub-slice negative. Market Brier is remarkably stable at 0.235–0.275
  everywhere while the model swings 0.28–0.51.

### 3.3 The market IS soft — that is not the problem
- Market beats climatology by only **+1.3%** (0.2468 vs clim 0.2499) on entered
  markets. The bar is **beat the base rate**, not beat a sharp market.
- ⚠ Selection caveat: those 230 markets are chosen by us disagreeing with price,
  biasing toward contested ~50/50 markets. **Whether the UNTRADED weather
  universe is equally soft is UNTESTED** — needs a price source for markets we
  never entered. **This is one of the highest-value open questions (§4.A).**

### 3.4 The losing mechanism is self-inflicted adverse selection
- Extremity `mean |p−0.5|`: **model 0.324 vs market 0.059**. Market sits on the
  fence; model sits at the poles.
- Entries fire on *disagreement*, so we size hardest where our error is largest.
- Proof: across the 203 markets with meaningful disagreement, model Brier climbs
  **0.30 → 0.37 → 0.47** while market Brier stays flat 0.24–0.26.
- Not an informed counterparty. We do it to ourselves.

### 3.5 The mesh lead is REAL — the one proven asset
- 4 consecutive graded days PASS all gates: 60%/74.8min → 62%/61.0 → 72%/49.0 →
  77%/63.0 (led% / pooled median lead). The mesh sees the peak ~49–75 min before
  the public print.
- **But**: post-peak the outcome locks (false-lock **0.00%** L1 / **0.17%** L2)
  and **supply vanishes** — fills ≤0.97 exist on only ~1% of locked days.
  Verdict: *no free money at the close — supply, not settlement risk.*
- Live confirmation: **every** nowcast shadow line is `reason=repriced`,
  **zero** entry crossings. The market has already moved when we see it.
- **The central rebuild question: we have a real ~1h information lead and no way
  to monetize it as a taker. Is there another way? (§4.D/E)**

### 3.6 Dead ends — do not revisit without new information
- **9–12h day-of cell**: DEAD at scale (+0.002, n=692), bot-independent.
- **Peakpass**: viability fail (supply), not signal quality.
- **~9% false-lock: RETRACTED** — miswired-station artifact. Never re-cite.
- **c13 Maker feed purge**: verified NO-OP, nothing to purge.
- **Day-5 (0720) mesh-lead**: IEM 1-min backfill finished at ~8%. Permanently
  ungradeable. Next trend point is day-6 (`--lead 20260721`).

---

## 4. THE OPTION SPACE — EXAMINE EVERY BRANCH, KILL WITH REASONS

Ranked by my read of expected value, but **do not inherit that ranking** —
re-derive it. For each: state the thesis, the test that would falsify it, the
data needed, and a go/kill verdict.

### A. Measure the untraded universe first (cheapest, highest information)
Everything in §3 is conditional on markets we entered. Before redesigning
anything, establish the base truth: **across ALL weather markets (not just ours),
how well does the market price predict resolution?**
- If the market is soft universe-wide → a modest honest model has room.
- If it is only soft where we self-select → the opportunity may not exist at all.
- Needs a market-price source for untraded markets (Gamma/CLOB history, or
  `weather_forecasts`/`traded_markets`). **Find out whether we already store it.**
This single answer reorders every other option. Do it first.

### B. Fix the forecast (the conventional path)
- Recalibrate: VIF→2.0, isotonic/Platt on WB's own 229k rows, per-city EMOS.
- Attack extremity directly (0.324 → toward 0.059) — §3.4 says that alone
  changes both the estimate AND the entry set.
- Better inputs: KMA (Seoul minutely), WU key, Synoptic, MADIS, NWWS-OI, ECMWF.
- **Kill criterion: does it beat climatology out-of-sample?** Today it is −47%.
  Anything that does not clear 0 is not a candidate.

### C. Fix the entry rule independent of the model
- Gate on *model-beats-climatology-in-this-city/horizon*, not on disagreement.
- Require agreement-in-direction with a second independent source.
- Cap extremity at entry; refuse trades where |model−market| is largest (the
  exact band where §3.4 shows we are worst — an inversion of current behaviour).
- Horizon: 24–48h was less bad than <24h (−0.371 vs −0.536). Worth a cut.

### D. Monetize the mesh lead differently (the proven asset)
We genuinely see the peak first. Taker capture fails on supply. Alternatives:
resting maker orders placed BEFORE the crossing; quoting rather than taking;
selling the information (Maker feed already does a version of this); different
market structures where a 1h lead is monetizable.

### E. Change role: taker → maker
The Maker lane exists and is a separate session — **coordinate, do not absorb.**
But WB's forecast could inform quoting rather than directional bets. Evaluate
honestly whether that is WB's job or Maker's.

### F. Change venue / instrument
Kalshi weather (separate live lane — coordinate, do not raid), other weather
instruments, precipitation/wind/snow rather than daily-max temperature
(different market microstructure, possibly different softness).

### G. Do nothing / wind down
If A shows no exploitable softness and B cannot clear climatology, the honest
answer may be to stop trading weather directionally and keep only the research
collectors. **This must be on the table with equal seriousness.** Paper mode
means there is no bleeding, so there is no urgency forcing a bad build.

---

## 5. RESEARCH DISCIPLINE — TRAPS THAT COST TIME IN S234

Every one of these produced a wrong or near-wrong conclusion in a single session.

1. **DEDUP MARKETS.** `prediction_log` holds many rows per market — 6,419 rows
   were only **230 markets (~28×)**. Any n in the thousands from the per-lead
   view is inflated.
2. **`calibration_check` does NOT dedup its per-side × lead-time section** even
   with `--dedup-markets` (`_build_per_side_lead_time_sql` takes only `clean`).
   Its H0′ verdict runs on inflated counts. Fix or label it.
3. **Concentration check BEFORE presenting (P14).** The one cell that "beat the
   market" was a single Paris market counted 41×. Leave-one-out left nothing.
4. **Base-rate traps.** "Hour 5: 92.7% accurate" was climatology (base 0.076).
   High accuracy at a degenerate base rate is not skill.
5. **BSS explodes when base rate → 0/1.** Treat |BSS| > 2 as an artifact until
   proven otherwise.
6. **Frame discipline.** `predicted_prob` is P(YES) on every row (`95c732c`);
   `trade_events.price` is the token price of the SIDE BOUGHT. Sanity-check the
   mapping empirically — it PASSED for NO (99.5%) and did NOT for YES (14.3%).
7. **P&L is banned** (CLAUDE.md #11). Communicate via calibration/Brier only.
8. Shell: backticks in double-quoted commit messages EXECUTE; f-strings with
   escaped quotes break in heredocs; Git-Bash `/tmp` ≠ Windows Python `/tmp`
   (use the scratchpad); `grep "trades="` also matches `ms_trades=`.

---

## 6. ENVIRONMENT

- Worktree **only**: `.claude/worktrees/wb-whiteboard`, branch
  `claude/new-whiteboard-session-9b23tq`. Main checkout is on SB's branch —
  `git -C <worktree>` for every git op, absolute paths for everything else.
- VPS `ubuntu@18.201.216.0`, key `~/.ssh/wb_deploy2`. WB release
  **`20260721_230638`**; master **`20260721_232241`**. WB runs from the splinter
  tree via the `00-splinter.conf` drop-in (survives master deploys).
- DB: `PW=$(grep -oP "postgresql[^ ]*://polymarket:\K[^@]+" /opt/pa2-shared/.env | head -1)`
  then `PGPASSWORD="$PW" psql -h 127.0.0.1 -U polymarket -d polymarket`.
- Deploys are operator-gated. `deploy/rollback.sh` reverts master.
- ⚠ `polymarket-ingestion` logs `Bulk inserted`, **not** `scan_done`.

## 7. OPEN ITEMS INHERITED (not the plan, but do not lose them)

- **Cross-bot prompts written and possibly undelivered** —
  `docs/WB_S234_CROSSBOT_PROMPTS.md` (MB, Maker, EB, SB). MB took 41 commits of
  master; Maker must NOT purge its feed on the stale c13 relay.
- **KBKF**: root-caused (hourly with 2–4h gaps vs a uniform 180-min staleness
  gate that halts all Denver trading). Fix designed, NOT applied — needs a call.
- **Debias drop-rule churn**: ~8 cities within 0.1 °F of the hard 1.5 cut, so
  publish/drop flips daily. "No city regressed" is not a usable acceptance test.
- **EDDB** should appear in the 07-23 09:15Z debias run (tested: sd 0.34 with a
  full Berlin day). If absent, that IS a finding.
- `wb-vif-tune-remeasure` fires 07-24 — consume it as *evidence*, not as a plan.

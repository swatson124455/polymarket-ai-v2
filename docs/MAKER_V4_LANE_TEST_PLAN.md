# MAKER V4 — Live-Sports Lane Test Arm + Pool Census: Full Plan (Phase 1 of plan→build→scan)

Date: 2026-07-15 · Silo: linked worktree, branch `claude/maker-sim-v4-lane` (off `claude/maker-paper-sim` @ 55b089c)
Status: PLAN — build follows in Phase 2, adversarial error-scan in Phase 3. Nothing deploys without operator go.

## 0. What this tests and why now

Two hypotheses from the 3rd-party review (handoff §4c Bucket 1), both testable only with new collection:

- **H1 — Live-sports lane**: quoting live SPORTS games (unlike esports) with hard inventory caps and
  ~1s re-centering yields non-negative NET (reward accrual + realized − inventory bleed). Motivated by:
  (a) canon sweep 07-15: pools concentrate in live game windows (WC-semi family ≈ $190K/day-rate);
  (b) sim v1: sports small-positive even at 5-min staleness and 3× caps, while esports bled.
- **H2 — Split-inventory (ask-ask) quoting**: posting ASKS on both tokens from pre-split pairs bounds
  inventory risk vs classic bid/ask-around-mid, at comparable reward score (both are two-sided).
- **H3 — Pool concentration & promo decay**: total/sector pools vary strongly intraday and will step
  down after the World Cup incentive ends Jul 19 (changelog-verified). Needs hourly census STARTING
  BEFORE Jul 19 — this is the time-critical piece.

Explicitly OUT of scope for this build (planned as later avenues, kept out to protect review quality):
resolution-lag watcher, negRisk over-round scanner, catalyst calendar gate, any live-order mechanics.

## 1. Components (both 100% separate from v1/v2/v3 — own dirs, own state, own units)

### 1a. `scripts/maker_paper_sim_v4.py` — the new arm (daemon, WS-driven; clone of v3 @ 55b089c)

**Inherits verbatim from v3** (do not re-derive): WS book maintenance + generation-based thread
lifecycle + book eviction on refresh (55b089c), oldest-first multi-page tape fetch (96df6d2),
time-matched quote-history fill attribution, frozen-msz caps (2e44be4), vol-pull window semantics,
STOP sentinel / disk cap / HTTP budget / heartbeat with stale-book counter, atomic state persistence
with qh/mid_hist stripping.

**Deltas vs v3 (the entire experiment):**

| # | Delta | Spec |
|---|-------|------|
| D1 | Universe = rewarded SPORTS game markets only | sector == "sports" AND `gameStartTime` present. Top-70 by pool (140 assets = 2 WS chunks). Discovery every 15 min (game markets churn daily; 30 min too slow near start times). |
| D2 | INVERTED in-play gate | Quote ONLY when `game_start <= now`. Pre-game = gated (v2/v3 own that window ⇒ zero overlap, clean attribution). NO last_hours gate (night games ARE last hours; v3's 19:00Z gate would kill the lane). vol_pull KEPT (2pt/window → 10-min pull; protection during goals, logged for attribution). extreme-mid: skip quoting when S(v, s_mine, msz) ≤ 0 (outside incentive band), as v3. |
| D3 | Hard caps ±1× frozen msz | INV_CAP_MULT = 1 (v1–v3 use 3). The "hard inventory caps" of H1. |
| D4 | A/B by market-id parity | even id → `classic` (bid+ask at touch, exactly v3-touch); odd id → `split` (ask-ask from pre-split pairs, spec §2). Same assignment mechanic as v2/v3 width A/B (precedented, deterministic, balanced-in-expectation; heartbeat logs per-arm market counts to verify balance). |
| D5 | Complement-side fills COUNTED | v1–v3 ignore NO-token prints (documented conservative undercount). In-play risk is the question here, so v4 maps NO prints to YES space (p_yes = 1 − p_no, buy↔sell) and matches BOTH tapes against quotes. Symmetric across arms. Cross-arm comparisons to v1–v3 must note the convention difference. |
| D6 | One-sided score haircut | If the split arm has inventory only on one side, its own q_mine gets the docs' one-sided treatment: ×(1/3) inside mid∈[0.10,0.90], ×0 outside. v1–v3 never need this (always two-sided); v4-split does. |
| D7 | "settled" pull gate | mid ≥ 0.92 or ≤ 0.08 → pull quotes (gate counter logged). Rationale: the inverted in-play gate would otherwise quote FINISHED-but-unresolved games to resolution — exactly the v1-esports bleed channel. Also pulls during in-play blowouts, when gap risk is worst; the forgone-rewards cost is measured via the gate counter. (Added pre-build after spec review — v3 never faced this because it gates all of in-play.) |

**Split-arm accounting spec (D4/H2), per market:**
- Virtual capital: at first quote, "split" `PAIRS = 1 × msz` pairs → `yes_inv = no_inv = msz`,
  `capital = msz × $1.00` (split is $1-for-pair, fee-free; canon-verified mechanics).
- Quotes: ask YES at `mid + s_mine`; ask NO at `(1 − mid) + s_mine` (≡ YES-space bid at `mid − s_mine`
  — same book positions as classic, different inventory semantics).
- Fill on YES ask (print through it, either tape after D5 mapping): sell `msz` YES → `cash += msz × ask`,
  `yes_inv −= msz`. Mirror for NO. A side with zero inventory is unquoted (D6 haircut applies).
- Re-split: when BOTH sides are at zero, split again (capital += msz), max 3 re-splits/market/day
  (bounds capital like classic's cap; logged).
- P&L: `real+unreal = cash + yes_inv×mid + no_inv×(1−mid) − capital`. Max loss is structurally bounded:
  the pair pool is fully collateralized (worst case: unsold side goes to 0 while sold side was sold
  above 0 → loss < capital; contrast classic, where adverse mid moves are unbounded down to 0/1).
- Reward accrual: identical formula to classic (q_mine from own quote distances, both sides when both
  quoted), with D6 haircut.

**Heartbeat (5 min):** quoting/gated counts, in_play count, per-arm (classic|split) acc/real/fills,
requote-latency median, books/stale_books, resplits.

### 1b. `scripts/pool_census.py` — hourly pool census (timer, stdlib-only)

- Sweep: union of 3 gamma orderings (volume24hr, liquidity, startDate; desc, limit=100, page to
  422/empty, ≤25 pages each — the 07-15 canon method; ~$266.7K/day measured, known LOWER BOUND).
- Output `census-YYYYMMDD.jsonl`, one summary line/run: ts, markets_seen, n_rewarded, total_pool,
  per-sector pool totals, plus compact per-market rows (id, pool, sector, event-title[:40],
  gamma bestBid/bestAsk for spread trend — NO CLOB calls), gzip-rotated daily.
- Budget: ≤80 HTTP GETs/run, hourly timer ⇒ ~2K/day (v1 alone runs ~100K/day; negligible).
- Disk cap 300MB (≈2 months). STOP sentinel. Same systemd sandbox pattern.
- Readout products: (i) pool-by-hour curves; (ii) Jul-19 promo-end step (H3); (iii) live-window
  attribution via gameStartTime; (iv) spread-compression kill-trigger trend.

### 1c. Tests — `tests/test_maker_sim_v4.py` (pytest, runs locally, no network)

Unit coverage for every NEW mechanism (inherited v3 code is live-verified, not re-tested):
split-arm invariants (inventory never negative; cash+inventory value − capital == realized spread on
a full round-trip; re-split cap enforced), D5 complement mapping (NO print at 0.62 fills YES bid 0.39),
D6 haircut (one-sided share ×1/3 in band, 0 outside), D2 gate truth-table (pre-game gated, in-play
quoted, no last_hours, vol_pull still pulls), D4 parity assignment, ±1× cap enforcement on classic arm.

### 1d. Deploy artifacts (written, NOT executed)

`deploy/polymarket-maker-sim-v4.service` (daemon, own venv w/ websockets, /opt/pa2-maker-sim-v4,
strict sandbox, MemoryMax=384M CPUQuota=30% — matches v3 precedent) and
`deploy/polymarket-census.{service,timer}` (hourly, stdlib, /opt/pa2-maker-census, TimeoutStartSec=900
for degraded-network worst case). Exact install commands in §4; execution requires operator go.

## 2. What each comparison isolates (the readout contract)

| Comparison | Isolates |
|-----------|----------|
| v4-classic vs v1's sports-in-play slice | speed (1s vs 5min) + caps (1× vs 3×) in live sports |
| v4-classic vs v4-split (same markets universe, parity A/B) | inventory-model value (H2), same speed/gates |
| v4 (any) vs v3 sports pre-game | live-window vs pre-game economics of the SAME sector |
| census hourly totals across Jul 19 | promo vs steady-state pool base (H3) — decides if H1 matters |
| v4 gate counters (vol_pull etc.) | how often protection fired; rewards forgone attribution |

Kill criteria (pre-registered): H1 dead if v4-classic NET < 0 after ≥5 days AND ≥100 fills with
vol-pull active; H2 dead if split underperforms classic on NET in ≥70% of shared game-days; H3's
"lane is real" needs post-Jul-19 live-sports pools ≥ 3× pre-game pools on ordinary days.

## 3. Risks / known limitations (stated up front)

1. `rewardsDailyRate` prorating on intraday game markets UNKNOWN — same convention as v1–v3 for
   comparability; absolute reward $ carries this caveat until a live micro-pilot ground-truths payouts.
2. Paper-share optimism: near-empty in-band books ⇒ high hypothetical share; mitigated by reporting
   share distribution, not just totals.
3. D5 mapping assumes YES/NO books mirror the same liquidity (canon: single book, two views) — scan
   phase must verify on live tape that complement prints aren't double-counted when both views report
   the same trade (dedup key includes asset ⇒ same tx appearing on both tokens would double-count →
   scan item S-1).
4. Sports universe churn: 15-min discovery may still miss just-listed in-game markets; census
   quantifies what the arm missed.
5. Parity assignment could imbalance small samples (heartbeat logs counts; readout stratifies by game).
6. No pre-game quoting means v4 accrues nothing on quiet days with no live games — expected, not a bug.

## 4. Deploy (operator-gated; commands only)

```bash
# on VPS, after operator go:
python3 --version   # MUST be >= 3.9; parse_iso normalizes gamma's "+00" offsets either way
sudo mkdir -p /opt/pa2-maker-sim-v4 /opt/pa2-maker-census
sudo cp scripts/maker_paper_sim_v4.py /opt/pa2-maker-sim-v4/
sudo cp scripts/pool_census.py /opt/pa2-maker-census/
sudo python3 -m venv /opt/pa2-maker-sim-v4/venv && sudo /opt/pa2-maker-sim-v4/venv/bin/pip install websockets
sudo chown -R polymarket:polymarket /opt/pa2-maker-sim-v4 /opt/pa2-maker-census
# ^ REQUIRED (scan HIGH): units run User=polymarket under ProtectSystem=strict;
#   root-owned dirs -> first write fails -> census collects ZERO data while looking installed
sudo cp deploy/polymarket-maker-sim-v4.service deploy/polymarket-census.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-census.timer polymarket-maker-sim-v4.service
sudo systemctl start polymarket-census.service   # first census immediately, don't wait for :07
# verify within 2 min: journalctl -u polymarket-maker-sim-v4 -n 5   (expect "universe: N sports game markets")
#                      ls -la /opt/pa2-maker-census/                (expect census-YYYYMMDD.jsonl)
# kill switches: touch /opt/pa2-maker-sim-v4/STOP ; systemctl disable --now polymarket-maker-sim-v4 polymarket-census.timer
```

## 5. Phase gates

- Phase 1 (this doc) → commit.
- Phase 2 BUILD → separate commits per component; local verification: pytest green + `pool_census.py --once`
  live run + v4 `--run` 2-minute local smoke (venv w/ websockets) showing hb line with books streaming.
- Phase 3 SCAN → independent fresh-context reviewers over the diff (correctness vs this spec;
  adversarial hidden-bug hunt on fills/threading/state; live-data verification of S-1). Findings fixed,
  re-verified, THEN the go/no-go handoff to operator with deploy commands.

## 6. Phase-3 scan errata (2026-07-15/16 — 3 independent reviewers; all fixes applied + re-verified)

**S-1 RESOLVED with live data:** data-api default tape is TAKER-ONLY — each trade appears once, on
the taker's token (0/1,100 cross-token duplicates sampled). NO-taker prints are 6–50% of live tapes
with exactly complementary prices ⇒ D5 mapping is correct and NECESSARY (v1–v3 silently miss them).
The takerOnly=false view double-reports every trade — never switch the fetch without cross-leg dedup.

**Fixed (CRITICAL/HIGH):**
1. Resolution backfill (`finalize_dropped`): residual inventory was frozen at last mid — overstated
   NET both arms (~residual×(1−frozen_mid) per game) and biased H2 toward split. Now marked to gamma
   `outcomePrices` once closed; quotes+qh cleared on universe drop (also kills stale-quote re-entry
   fills). `residual`/`final_mid` logged per market.
2. Same-second print drops: per-print `ts<=last_ts` watermark discarded ~24% of live prints (integer-
   second timestamps) and made the old dedup dead code. Rewritten: batch-end watermark + persisted
   edge-second identity set (fetch_tape 5-tuple) = exactly-once without drops, restart-safe.
3. `parse_iso` normalizes gamma's short "+00" offset (Python ≤3.10 returned None → empty universe);
   empty-universe rediscovery backed off to 60s (was every second → HTTP-budget burn).
4. Deploy §4: added `chown -R polymarket:polymarket` (root-owned dirs + User=polymarket ⇒ zero data).
**Fixed (MED/LOW):** restart phantom accrual at share=1.0 (ephemeral bid/ask/last_mid/last_acc_t no
longer persisted + book-freshness guard); `is_sports` category-authoritative + esports KW expanded
(residual risk: empty-category team-name-only slugs — bounded by settled/vol_pull/±1× caps); band-exit
now PULLS quotes (was: stale quotes left standing); disk-cap check on its own hourly clock (was dead
in steady state); `clobTokenIds` type guard (crash-loop); HTTP budget 12K→16K/hr (fill starvation on
hot nights); census: `degraded` flag distinguishes truncated sweeps from real pool declines, gzip
daily rotation, negative-rate guard, non-dict element guard, TimeoutStartSec=900, Wants=network-online.
**Accepted/documented (no code change):** frozen-msz semantics (declared D3 refinement — freeze-once,
frozen size for caps+fills+accrual); dust prints fill at full msz (v1–v3 convention, arm-symmetric);
zombie-book resurrection after GEN swap (metric-only); census dust rows display pool 0.0 (rounding);
report includes finalized markets at resolution marks (intended).

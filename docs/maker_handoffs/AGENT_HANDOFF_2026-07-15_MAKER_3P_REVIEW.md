# MAKER — 3rd-Party Skeptic Items Review (handoff for main session)

**Date:** 2026-07-15 (evening)
**Written by:** siloed side session (EB-branch checkout, zero repo commits — see §Silo below)
**Lane:** Maker / MakerBot (market-making initiative). NOT "MB" — MB = MirrorBot exclusively.
**Reads with:** memory `project_mm_feasibility_study.md` · branch `claude/maker-paper-sim` (local + origin) · VPS `/opt/pa2-maker-sim{,-v2,-v3}`

---

## 0. Why this doc exists

Operator relayed an outside (3rd-party) skeptic take on the maker initiative. The four claims were
adjudicated on 2026-07-15; this doc consolidates the verdicts, adds a fresh primary-source re-check
of the one UNVERIFIED claim, and records the silo protocol so the main session can pick this lane up
without re-deriving anything.

## 1. The four 3rd-party items — verdicts

| # | Claim | Verdict | Basis |
|---|-------|---------|-------|
| 1 | Adverse selection kills makers | **Mechanism real, magnitude ~0 here at mean; the TAIL is the real cost** | Short-horizon (5–30min, ex-resolution) mean signed drift: only geopolitical significantly positive (+0.48pt, CI [+0.10,+0.86]); every other sector's CI straddles 0. Pick-off tail real (sports 45.7% of fills move ≥2pt informed-direction/30min). Maker fee moat (makers pay ZERO, takers pay p(1−p) fees) partially inverts the edge. The sim's realized adverse-selection channel turned out to be **esports in-play inventory run-over**, now gated in v2/v3 arms. Source: feasibility study 2026-07-14/15 (memory `project_mm_feasibility_study.md`). |
| 2 | It's a speed game (ms-level HFT) | **No — the bar is MINUTES** | Reward scoring is per-minute **random sampling** (verified in official docs) → snapshot-sniping dead by design; sub-second speed buys nothing for rewards. For fill quality, V3 WS arm (median requote 716ms) vs V2 (120s poll) isolates the $ value of speed empirically. Friday readout settles it. |
| 3 | Rewards farming is the real money, not spread capture | **VERIFIED characterization** | Total pools ~$75.8K/day (gamma clobRewards sum, 07-14 sweep). Durable (≥2-day) corner is small (~$434/day gross on ~$4.7K capital, 33 markets); the bulk is a daily treadmill of $200–1,000/day pools resetting each morning. Favors this system's uptime/infra advantage over discretionary humans. |
| 4 | Program "expires Sept 1" | **STILL UNVERIFIED — no primary source exists (re-checked 2026-07-15 this session)** | Web search: zero hits for a Sept 1 end date. Official docs page (`docs.polymarket.com/market-makers/liquidity-rewards`, fetched 07-15): **no end date, no expiration language** for the general program; the ONLY dated program is the **World Cup 2026 liquidity incentive, June 11 → July 19, 2026** — plausibly the garbled source of the claim. Prior session found rebates-page "sole discretion… may change" language. **True kernel of the claim = discretionary-subsidy risk, not a dated sunset. Action: ask the 3rd party for their source.** 07-16 EXHAUSTIVE SWEEP: 3 web searches + intl docs + maker-rebates docs + changelog + help center + **Polymarket US incentives docs** (program started Jun 1 2026, explicitly "Ongoing", no end date) — ZERO Sept-1 mentions anywhere; intl program actively updated Jul 2 + Jul 10. Verdict upgraded: not merely unsourced — **contradicted by every official source**. |

Items 1–3 get their empirical settlement from the Friday four-way sim readout (task `maker-sim-readout`):
v1 naive control vs v2 gated (2-min poll) vs v3 gated (WebSocket, quote-history fill matching), plus
per-rule cut-candidate $ ledger.

## 2. Silo verification (how this review runs without touching anything)

Verified this session, from a checkout sitting on the **EB branch** (`claude/esports-sharp-line-rebuild-gqy1na`, clean tree):

1. **This doc is gitignored** — `git check-ignore` confirms `AGENT_HANDOFF_*.md` matches `.gitignore:158`. Writing it makes zero commits, no branch switch, no EB contamination. It cannot be accidentally staged.
2. **The review itself is read-only**: primary web sources + memory + existing branch artifacts. No shared modules, no `/opt/pa2-shared/.env`, no deploys, no MirrorBot resources, no DB writes.
3. **If commits are ever needed** (code, committed docs): use a **linked worktree** on `claude/maker-paper-sim` — the branch exists locally and on origin; precedent from the 07-14/15 maker session (its worktree lived in that session's scratchpad; a stale registration may remain — `git worktree prune` from the main session if needed). Never commit maker work on the EB/WB checkout branch.
4. **VPS sim arms are already sandboxed**: `/opt/pa2-maker-sim{,-v2,-v3}` are own-dir, deploy.sh-independent, trading-impossible (no keys, GET-only, no DATABASE_URL), kernel-sandboxed. Reading their state or running `--report` touches no other service. Kill switch per arm: `touch /opt/pa2-maker-sim*/STOP` or `systemctl disable --now polymarket-maker-sim*.{timer,service}`.

**Conclusion: yes — 3rd-party-item review work silos cleanly.** The only shared thing it touches is operator attention.

## 3. Open items for the main session

- [ ] Ask the 3rd party for a source on "Sept 1" (expect: none, or the World Cup promo dates).
- [ ] Friday readout (`maker-sim-readout`): four-way comparison settles items 1–3 in $; includes speed-value (v2 vs v3) and cut-candidate ledger.
- [ ] If new 3rd-party items arrive: adjudicate against primary sources only (docs.polymarket.com, on-chain, sim data) — the first draft of the feasibility study was demolished by red-team for median-vs-mean and horizon-contamination errors; keep the corrected methodology (short-horizon mean drift, ex-resolution, CIs, n≥40).
- [ ] Discretionary-subsidy risk is the real strategic exposure — worth a standing note in any go/no-go: the program can change or end at Polymarket's discretion regardless of any rumored date.

## 4a. SECOND 3rd-party drop (2026-07-15 eve): full "practitioner playbook" — reviewed

Operator relayed a long structured report (rewards farming / negRisk arb / oracle plays / game
theory / API). Graded against our measured internals + a fresh docs fetch + read-only
`git show claude/maker-paper-sim:scripts/maker_paper_sim.py`. Reliability: its MECHANICS check out
against our verified canon (fee formula, scoring spec, V2/pUSD cutover) — good sign; its $ CLAIMS
are blog-vintage or dated-window and should not be trusted without re-measurement.

**CONFIRMED GAPS IN OUR SIM (verified against script on branch, lines ~194-211):**
1. **No $1/day minimum payout** — docs (fetched 07-15): "minimum reward payout is $1; amounts below
   this will not be paid." Sim accrues `share×pool×dt/86400` continuously with no floor → live pays
   ZERO on any market-day accruing <$1. With 95 weather pools at $3–45/day this plausibly inflates
   sim rewards materially, weather-heaviest. **FIX AT READOUT, not in the running arms**: recompute
   per-market-per-day accruals from tick logs and apply the $1 floor as a sensitivity line in the
   Friday report. This is the #1 adoption from the review.
2. **No in-game multiplier `b` / no Pre-vs-Live pool split.** Docs scoring is S=((v−s)/v)²·b; sim
   omits b. Report cites per-game splits (live ≈2.5× pre; e.g. EPL $2.8K pre/$7.2K live, A/B-tier
   CS2/LoL $1.55K/$3.95K). Our v2/v3 `in_play` gate forfeits the LARGER bucket by design — readout
   must state "rewards forgone by gate vs bleed avoided" explicitly, and check whether gamma
   `rewardsDailyRate` even includes live buckets.
3. **Maker rebates uncounted** (favorable — sim understates maker income). Bonus: report resolves
   our sports-rebate docs conflict — sports cut 25%→15% July 2026 (one docs page stale). Verify via
   changelog, then update canon in memory.
4. Minor: sim uses raw touch mid, docs use size-cutoff-ADJUSTED midpoint (dust levels shift it). LOW.

**POOL-TOTAL DISCREPANCY (verify before reallocating):** we measured ~$75.8K/day total pools
(gamma clobRewards sum, single 23:50Z snapshot, 07-14); report claims ">$5M/month" (~$165K+/day)
and near-$8M sports months (blog-sourced). Likely reconciliation: per-game sports/esports pools
created intraday + live buckets invisible to a single end-of-day snapshot (our known caveat). If
real, the bulk of the subsidy sits exactly where our gates are. Action: intraday re-sweep of
rewarded-market discovery + enumerate per-game pools across one full day.

**ITEM-BY-ITEM VERDICTS (vs our data):**
- Rewards program = biggest exploitable mechanic, subsidy-not-free-money → **AGREES with our program**; their adverse-selection framing is anecdotal, ours is measured (mean ~0 ex-geo; tail + in-play run-over = the real cost).
- Quadratic scoring / Q_min / band / per-minute sampling → **matches docs we fetched; sim implements core correctly** (gaps above). Report says "sampled every minute" — docs say random sampling within the minute (kills snapshot-sniping; we verified this earlier).
- Akey et al (makers capture profits, takers lose) → **strongest external validation of the maker thesis**; nothing to change, supports go-case.
- NegRisk/complement arb ($39.6M IMDEA) → mechanics correct, BUT window = Apr 2024–Apr 2025 (largely fee-free, election-cycle, pre-V2). Today: ~3s windows, co-located competition. **Keep deprioritized; optional post-readout: measurement-only over-round scanner** (we already have EU VPS + negRisk routing knowledge).
- Cross-platform (Kalshi) arb → resolution-authority mismatch risk is real; windows 2.7s. **Deprioritize — agreed.**
- Split/merge mechanics → **genuine live-phase design adoption**: `mergePositions()` to unwind paired inventory without paying the spread twice; split-then-quote-both-asks as inventory-neutral quoting. Note for go-live design, not sim.
- Favorite-longshot bias → literature contested, 1–4pp, fee-eaten. **Skip — not our lane.** Report itself flags the disagreement.
- Resolution-lag yield (buy winners at 0.97–0.999 pre-redemption) → small, real, objective-markets-only; pairs with our existing redeem timer. **Candidate overlay, later; not now.**
- UMA dispute risk → known; design rule for live: cap size in subjective-resolution markets; our universe (sports/esports/weather) is mostly objective.
- Latency arb / esports stream-lag sniping → **agreed deprioritized** (our verdict stands: rewards speed bar is minutes; v3 measures speed's $ value at 716ms vs 120s). Useful fact: CLOB served from Amsterdam; our VPS (eu-west-1) is ~10-15ms away — fine for minute-scale, irrelevant for ms-scale.
- Whale-copy advice ("deprioritize, honeypots") → generically valid cautions; conflicts with a separate measured internal program (different lane, evidence-based admissions). **No action in maker lane.**
- API/technical (V2 cutover, pUSD, batch-15 orders, GTD quirks, tick_size_change WS, rate limits, ~1s Python signing) → consistent with our on-chain-verified canon; **live-phase design inputs** (batch quoting, post-only GTC, dynamic fee/tick fetch). Sim's 520-req/240s budget is far under the 9K/10s CLOB limit.
- US geoblock Stage-0 gate → system already trades the venue live; settled at system level, nothing new.
- Notably ABSENT from their report: per-sector toxicity CIs (our original data), in-play inventory run-over as the dominant realized loss channel (our sim's finding), and any "Sept 1 expiry" (their sunset framing = "pools may shrink as liquidity self-sustains" = same discretionary-subsidy kernel we identified).

**CANON FACT-CHECK (2026-07-15 ~20:15Z, operator asked "can we fact-check with actual canon data" — YES, five checks run):**

1. **Pool total — REPORT VINDICATED, our baseline was a 3.5× undercount.** Fresh intraday sweep
   (gamma, union of 3 orderings paged to the offset wall at ~2,100/ordering → 6,121 unique markets,
   1,081 rewarded): **TOTAL = $266,665/day** at 20:13 UTC vs our $75.8K at the 07-14 23:50Z snapshot.
   Driver is exactly the report's thesis: live-game pools — the England–Argentina WC semifinal market
   family alone ≈ $190K/day-rate across ~20 derivative markets (O/U $28K, winner legs $23K each,
   spread $19.6K, BTTS $13K, corners $10.9K…). TWO CAVEATS before reallocating: (a) the changelog
   confirms a TIME-LIMITED World Cup incentive program **ending Jul 19** — part of today's total is
   promo, not steady-state; (b) OPEN QUESTION whether `rewardsDailyRate` on an intraday game market
   pays the full stated rate or prorates by market lifetime — must resolve before sizing. Also note
   $266.7K is a LOWER bound (offset walls truncate each ordering).
2. **$1/day floor — MATERIALITY DOWNGRADED after measuring against real sim state (read-only VPS).**
   With per-arm uptime-prorated thresholds: v1 keeps 99% of accrued rewards, v2 98%, v3 100%. The
   sim's hypothetical share of near-empty in-band books is large enough that per-market accrual paces
   clear $1/day. Keep as a one-line readout sensitivity (full-day granularity, gated arms), but it
   does NOT bias the Friday comparison materially. (First pass wrongly showed 61%/43% for v2/v3 —
   artifact of applying v1's 19.5h uptime to arms that started 19:00Z/19:37Z.)
3. **Sports fee + rebate changes — VERIFIED verbatim in the official changelog, Jul 10 2026:**
   "sports taker fee rate increases from 0.03 to 0.05" and "sports maker rebate decreases from 25%
   to 15%". **Canon item 3 (docs 15-vs-25 disagreement) RESOLVED** — the 25% page is stale. Batch
   limit 5→15 also confirmed (Aug 21 2025).
4. **IMDEA arb paper — abstract confirms "realized estimate of 40 million USD of profit extracted"**
   (title: "Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets", Saguillo/Ghafouri/
   Kiffer/Suarez-Tangil). Finer splits ($29M negRisk etc.) would need the PDF; headline stands.
5. **VPS→CLOB latency measured: 0.17–0.23s per fresh HTTPS request** (eu-west-1 → clob.polymarket.com)
   — comfortably fine for minute-scale reward sampling; confirms ms-scale games stay out of reach
   without co-location, as already decided.

**SWEEP-METHOD NOTE for the main session:** gamma 422s at offset ~2,100 per ordering. The sim's
discovery (order=volume24hr, top ~2,100) can therefore miss rewarded markets — top-25/sector by pool
is probably safe, but any TOTAL-pool number must union multiple orderings and is still a lower bound.

**PRIORITIZED ADOPTIONS:** (1) $1-floor sensitivity in Friday readout [HIGH, readout-level only];
(2) intraday pool re-sweep + per-game live-bucket enumeration [HIGH — could reshape sector
allocation]; (3) rewards-forgone-vs-bleed-avoided line for the in_play gate [MED, readout];
(4) verify sports-rebate 15% via changelog → canon update [LOW]; (5) live-phase design notes:
merge-unwind, batch-15, tick handling, subjective-market size cap [for go/no-go doc].

## 4b. STATUS LEDGER (operator ask: notate VERIFIED vs THEORY — supersedes §4a verdict bullets as the canonical list)

Tags: **VERIFIED** = primary source fetched or measured against canon/live data this or prior session.
**THEORY** = plausible but unverified (blogs, anecdotes, unread PDFs, un-fetched doc pages). **MIXED** noted inline.

**A. Rewards mechanics**
1. Quadratic scoring S=((v−s)/v)²·b, Q_min c=3.0, [0.10,0.90] band, per-minute RANDOM sampling, 10,080-sample weekly epoch — **VERIFIED** (docs fetched 07-15 twice; sim implements core correctly).
2. $1/day minimum payout, no rollover — **VERIFIED** (docs verbatim); materiality to our arms **MEASURED IMMATERIAL** (real state.json, per-arm uptime: v1 99% / v2 98% / v3 100% kept).
3. Pool total ">$5M/month" — **VERIFIED in direction by our own sweep** ($266.7K/day lower bound intraday ≈ $8M/mo pace during WC semi); steady-state post-Jul-19 = **THEORY** until re-swept.
4. Live ≈2.5× pre-game pools + per-game cap table (EPL $10K, CS2/LoL $5.5K…) — direction **VERIFIED** (live-game families dominate: WC-semi family ≈$190K observed live); exact ratios/caps **THEORY** (sports-program page not independently fetched).
5. In-game multiplier b exists in the formula — **VERIFIED** (docs); its per-market values **THEORY**.
6. Makers pay zero fees; rebate = pro-rata share of taker fees, daily — **VERIFIED** (canon + docs).
7. Jul 10 2026: sports taker fee 0.03→0.05 AND sports rebate 25%→15% — **VERIFIED verbatim** (changelog). Closes canon item 3.
8. Taker fee = C×rate×p(1−p); category base rates — **VERIFIED** (prior canon + changelog).
9. ">98¢ trades don't qualify" / wash-trade disqualification — **THEORY** (absent from the docs page fetched).
10. "~52K wallets ever LP'd; $100 = top 6%; $650 = top 1,000" — **THEORY** (blog-sourced).
11. "$200–300/day on $10K" early-LP anecdotes; "$12M LP rewards in 2025" — **THEORY**.

**B. Our sim gaps (all code-checked via read-only `git show`)**
12. No $1 floor — **VERIFIED gap, measured immaterial** (item 2).
13. No b / no Pre-vs-Live split — **VERIFIED gap**; $ impact on the in_play gate unquantified → Friday readout line.
14. Rebates uncounted — **VERIFIED gap** (favorable — sim understates income).
15. Raw touch-mid instead of size-cutoff-adjusted mid — **VERIFIED gap in code**; materiality **THEORY** (low).
16. Gamma offset wall ~2,100/ordering blinds discovery + any total-pool sum — **VERIFIED** (422s observed); "top-25/sector unaffected" = **THEORY**.

**C. Arbitrage**
17. IMDEA ~$40M extracted — **VERIFIED** (arXiv abstract). Splits (73% negRisk, $2.0M top wallet, $8.18M top-10) — **THEORY** (PDF unread). Window = Apr-2024–Apr-2025 largely fee-free era — **VERIFIED caveat**.
18. NegRisk conversion = capital efficiency NOT arb; arb only when ΣYES<$1 (buy all YES) or ΣYES>$1 (complement set) — **VERIFIED mechanics** (consistent with our negRisk contract canon).
19. Cross-platform windows 12.3s→2.7s; 3.6s median in-game persistence — **THEORY** (cited studies unread).
20. split/merge/redeem fee-free via CTF — **VERIFIED** (on-chain canon 06-11). "Merge beats selling both legs" — logic sound, operationally **THEORY**.

**D. Oracle / resolution**
21. UMA flow: $750 bond, 2h challenge, MOOv2 37 whitelisted proposers, DVM escalation, ~98.5% first-layer — **THEORY** (plausible; primary docs not re-fetched).
22. Zelenskyy/MicroStrategy resolved-against-evidence; 1,150 disputes in 5mo 2026; UMA-cap-vs-market-size vote risk — **THEORY** (news-sourced).
23. Resolution-lag 0.97–0.999 sweep — the 2h+ lag is **VERIFIED-mechanics**; opportunity size/yield **THEORY**.

**E. Speed / information**
24. CLOB served from Amsterdam / 1.2ms co-located — **THEORY**; our own eu-west-1 → CLOB latency **VERIFIED 0.17–0.23s/HTTPS** (fine for minute-scale rewards; ms games out of reach as already decided).
25. Esports stream-lag sniping ($200K+ ops; RN1 $2.6M) — **THEORY** (vendor/news anecdotes).
26. Whale-copy honeypot/lag risks — **THEORY** generically; superseded internally by a measured program (other lane).

**F. Game theory**
27. Akey/Grégoire/Harvie/Martineau paper exists; finding = winners are limit-order MAKERS, losers are market-order takers, extreme profit concentration — **VERIFIED** (SSRN/CEPR listing + press). Exact concentration figure varies by draft vintage (84% earlier vs 76.5% in the version the report cites) — **THEORY** per the report's own caveat.
28. Thin-market manipulation / periphery-attack (UMA) reflexivity — **THEORY** (structurally sound, undemonstrated).

**G. API / technical**
29. CLOB V2 cutover + pUSD collateral — **VERIFIED** (our on-chain canon 2026-06-11).
30. Batch limit 5→15 — **VERIFIED** (changelog, Aug 21 2025).
31. Rate limits 15K/10s general, 9K/10s CLOB — **THEORY** (plausible; all our tooling far under).
32. GTD quirks, post-only GTC-only, tick-size rules (0.001 beyond 0.96/0.04, 0.0025 some sports, tick_size_change WS) — **THEORY** (verify at live phase; sim quotes at existing book prices so unaffected).
33. ~1s Python signing latency — **THEORY** (irrelevant at minute cadence; measure only if going live).
34. Geoblock / Polymarket US (QCX) details — **THEORY** details; operationally settled (venue already traded live by this system).

**TALLY: 14 VERIFIED, 5 verified-gaps-in-our-sim (2 measured for materiality), ~15 THEORY.** Every
adoption in the PRIORITIZED ADOPTIONS list above rests only on VERIFIED items.

## 4c. ACTIONABILITY TRIAGE (operator ask: "dumbass statements or actual logic/tactics we can add")

**BUCKET 1 — REAL ADDITIONS (things we did not have):**
1. **Live-sports reward lane hypothesis** [from items 3/4 + our sweep]: pools concentrate in live game
   windows (WC-semi family ≈$190K observed); sports in-play measured small-POSITIVE in sim so far
   (unlike esports). Testable v4 policy: quote live SPORTS with hard inventory caps + v3-speed
   re-centering. Biggest tactical takeaway of the whole report. Sequence AFTER Friday readout +
   post-Jul-19 steady-state re-sweep.
2. **Split-inventory quoting + merge-unwind** [item 20]: hold $X, split to YES+NO, post ASKS on both
   tokens → fills REDUCE inventory instead of building it (bounded-risk two-sided quoting);
   mergePositions() to exit paired inventory without paying the spread twice. Core professional maker
   mechanic we had not written down. Live-phase design; candidate future sim arm.
3. **Pool census over time-of-day + regime-watch triggers**: intraday sweep methodology (union
   orderings) now exists; add to readout: pool-total trend + target-market spread compression
   ("all 1¢ → edge gone, rotate to new listings") + changelog check cadence.
4. **Yield-per-dollar allocation**: rank markets by measured reward-share per $ quoted (sim already
   measures share) instead of top-25-by-pool. Allocation-policy v2 candidate.
5. **Catalyst-aware gating** beyond game-start: scheduled-event calendar (Fed/CPI prints for finance
   markets) as a widen/pull gate. Small, cheap, fits existing gate framework.
6. **Live-phase mechanics kit** [items 30/32 + rebates/floor]: post-only orders (guarantee maker-side,
   never accidentally cross), batch-15 quote refresh, tick_size_change handling, GTD 60s-early expiry,
   $1/day floor, rebate accounting. None affect the sim; ALL required before real orders.
7. **Subjective-market size cap + depth floor** [items 21/28]: dispute-lock risk + thin-book
   mid-manipulation (our marks AND vol_pull gate read the mid — a manipulable mid is an attack surface
   on the policy itself). Encode as market-selection rules.
8. **Resolution-lag sweep** [item 23]: buy objective-market winners at 0.97–0.999 pre-redemption;
   pairs with the existing redeem timer. Small capacity, later.
9. **NegRisk over-round monitor on NEW listings only** [items 17/18]: skip the 3s co-located game;
   the minutes-scale sliver is fresh listings/repricings. Measurement-only first, post-readout.

**BUCKET 2 — ALREADY OURS (report validates, adds nothing):** scoring formula + share-via-depth
estimation (sim implements); quote-recentering on drift (v3, 716ms); in-play risk (we MEASURED the
loss channel they only theorize); adverse-selection framing (ours has CIs); maker-only stance + fee
moat (canon); "avoid crowded 1¢ books" (share formula prices this); Akey et al (strategic validation
of the thesis, not a tactic); fee/rebate schedule (canon, now updated); measure-before-deploy
(their Stage-1 plan IS our sim, minus our guard rails).

**BUCKET 3 — NOISE (color/anecdote, no action):** 52K-wallets factoid; "$200–300/day" anecdote;
"$12M in 2025"; RN1/$2.6M and $200K esports-sniper stories (only confirm a deprioritization we
already made from our own latency position); 12.3s→2.7s window stats; Amsterdam 1.2ms; Zelenskyy/
MicroStrategy war stories (the RULE they motivate is Bucket-1 #7; the stories themselves are color);
geoblock lecture (operationally settled); "avoid presidential/Fed markets" (asserted, not shown —
our politics quotes are small-positive so far; let the readout answer, don't adopt platitudes).

## 5. V4 TEST ARM BUILT (operator-directed plan→build→scan, 2026-07-15/16 — own silo)

Operator commissioned Bucket-1 testing as "a completely new arm in its own silo". Executed the full
cycle on branch **`claude/maker-sim-v4-lane`** (linked worktree in session scratchpad; EB checkout and
v1/v2/v3 arms untouched):
- **Plan** (`fb1ca6c`): `docs/MAKER_V4_LANE_TEST_PLAN.md` — H1 live-sports lane (sports-only, in-play-
  only, ±1× caps, WS speed), H2 split-inventory vs classic A/B by id parity, H3 hourly pool census
  across the Jul-19 promo end. Kill criteria pre-registered.
- **Build** (`401ff98`): `scripts/maker_paper_sim_v4.py` (WS daemon, clone of v3 tip 55b089c + deltas
  D1–D7), `scripts/pool_census.py` (3-ordering union sweep hourly), systemd units (same guard-rail
  sandbox as v1–v3, trading impossible), unit tests.
- **Scan** (`726f20e`): 3 independent adversarial reviewers → 1 CRITICAL (residual inventory frozen at
  last mid instead of resolution — inflated NET both arms, biased H2), 3 HIGH (same-second tape drops
  ≈24% of live prints; py≤3.10 date parse would silently empty the universe + burn HTTP budget;
  missing chown in deploy = census writes zero data), 9 MED/LOW. ALL fixed + re-verified (29 tests,
  live census runs, live daemon smokes). **S-1 settled with live data: tape is taker-only; complement-
  print mapping (up to 50% of live prints) is necessary and correct.** Full errata in plan §6.
- Census live runs already captured the H3 signal: $266.7K/day during the WC semi → $95.5K after the
  game → $84.9K later — live-game pool concentration is dramatic and measurable hourly.
- **DEPLOYED 2026-07-16 01:51Z on operator "proceed"** — md5-verified transfer, chown applied,
  `polymarket-maker-sim-v4.service` live (universe 11 sports mkts / 9 in-play, WS streaming, 0 stale)
  and `polymarket-census.timer` hourly at :07 (first census-20260716.jsonl written). v1/v2/v3 verified
  untouched. Deployed code includes `b9f86d2` (batched price_change parser, ported from v3's 0c9708f).
  Kill switches: `touch /opt/pa2-maker-sim-v4/STOP` · `sudo systemctl disable --now
  polymarket-maker-sim-v4 polymarket-census.timer`. Branch pushed to origin.
- **WIDENED 02:03Z (operator: "this is for testing, don't bottleneck")**: `4d59181` — lane now covers
  sports AND esports game markets (≤40/sector, per-sector×arm attribution). Rationale: v1's esports
  in-play bleed was measured on the UNPROTECTED policy; v4 tests whether the armor (±1× caps, WS
  speed, settled gate, split arm) fixes it — sports and esports answer separately in the readout.
  Post-restart universe: 30 game markets (19 esports + 11 sports, 10 in play — EWC runs overnight
  while US sports sleep, so the widening also fills v4's dead hours).
- **FULL COVERAGE 07-16 14:22Z (operator: "v4 should cover all items it can")**: the only scope filter
  is now `gameStartTime` presence — and gamma stamps it on DAILY markets too (SPY/WTI dailies, weather
  cities, geo dates; live-verified), so v4 quotes 100 markets / 79 in-play: weather 40 (sector cap),
  esports 23, finance 16, other 10, geo 5, sports 6. Full-KW sector labels for clean sector×arm
  attribution; ≤40/sector, top-100 by pool, HTTP budget 24K/hr, 3 WS conns. Readout caveat: for
  non-game sectors "in-play" = the measurement day, so v4-vs-v2/v3 there isolates settled-gate+speed
  vs gated-poller rather than in-play-vs-pre-game.

## 4. Pointers

- Memory: `project_mm_feasibility_study.md` (full study state incl. sim arms, guard rails, directives)
- Branch: `claude/maker-paper-sim` (`8d83f2a` tip incl. v3) — local + origin
- Handoff (prior): `AGENT_HANDOFF_2026-07-14_MB_ORDERBOOK_WORSTOFBOOK.md` (working tree)
- Report: `python3 /opt/pa2-maker-sim/maker_paper_sim.py --report` (per-sector rewards$ vs realPnL$; note it omits open-inventory marks — include them for the NET answer)

# KALSHI MAKER — HANDOFF 2026-07-23 EOD (~18:25 UTC)

**Scope: MAKER-KALSHI ONLY.** Do NOT touch `claude/maker-bot`, MB/WB/EB/SB, shared modules.
Branch `claude/maker-kalshi-live`, HEAD `9c3c6b4`. Work in a linked worktree; bash cwd drifts.

**Step zero for the next session:** paste `KALSHI_KICKOFF_NEXT_SESSION.md` as your opening message
(step-zero + verify block + the 3 gotchas with exact expected hashes). Then read this file, then
`KALSHI_LIP_RULE_CANON.md` (the rules + §M1–M13 measurements), then verify §0 state yourself before
believing it.

⚠ **BRANCH HEAD ≠ DEPLOYED — the single easiest way to lose money here.** Deployed quoter
`727ca7c5…` (ran all day, unchanged). Branch HEAD quoter `9a24f605…` (this session committed code
fixes that are NOT deployed and NOT all deploy-ready). `git show HEAD:file | md5sum` will not match
the box — that is correct. NEVER `deploy HEAD`; every deploy is per-file, md5-gated, reviewed. See §4.

---

## §0 LIVE STATE — VERIFIED AT WRITE TIME

| thing | state |
|---|---|
| Bot | **LIVE + TRADING**, 2-min timer, no STOP. Cycle runs <1s, waits 2min. |
| Deployed code | `maker_kalshi_quoter.py` md5 **`727ca7c59840a42b51c19e24c65a0982`** = the build that ran all day. **NO CODE DEPLOYED THIS SESSION.** All 5 `.py` md5s unchanged. |
| `live.env` sha256 | `4092ac26a5a61dfca7edf8657e7eb6e812e94ce4ce082330f4b803e2a6386303` |
| Account | balance **$162.04** + positions (mark) ~$53 = **~$215** equity; 8 positions, ~14 resting. **Operator deposited +$150 at ~17:11Z.** |
| Loss meter | `equity_day_start` **$247.54** (re-baselined after the deposit) → halt trips at **$207.54** |

### ⚠⚠ THE RISK STATE THAT MATTERS MOST
**The two self-imposed capital brakes are BOTH inert** (set above account value today):
- `HELD_MAX_USD=100` — naked-risk breaker, unreachable
- `MAX_TOTAL_CAPITAL=250` — gross-notional cap, above ~$215 equity

**`DAILY_LOSS_HALT_USD=40` (trips at $207.54 account value) is the ONLY live risk brake**, plus
Kalshi's own over-balance rejection. This is a deliberate bridge while the capital-accounting
root-fix is designed (see §3). It is NOT a good long-term state. If the operator moves money again,
the loss meter MUST be re-baselined (clear `equity_day`/`equity_day_start` from `quoter_state.json`)
or the deposit reads as profit and loosens the brake.

---

## §1 EVERY LIVE CHANGE THIS SESSION (all config, all operator-authorized, ALL reversible)

Chronological. Every change is a single `live.env` line; **no code was deployed.** Backups exist
per change as `live.env.bak-<UTC>`.

| time (Z) | change | why | reversible |
|---|---|---|---|
| 15:11 | `REDUCE_ONLY_KEEP_BOTH` 1←auto-flipped→0, then **0→1** | A/B's OFF arm auto-fired; turned plug-in back ON. §M12 A/B: 66.1% two-sided ON vs 0.0% OFF. | `=0` |
| 15:19 | `TAKER_FLATTEN` **0→1** | to stop positions expiring worthless | *(reverted, see below)* |
| 16:16 | `HELD_MAX_USD` **20→50** | breaker deadlocked at $20.17 (17c over), 60min idle | `=20` |
| 16:22 | `SERIES_ALLOW` +`KXB200MON,KXAMSAVO` (7→9) | expansion — gas-like, ratio~1, fee-free | remove |
| 16:58 | `MAX_TOTAL_CAPITAL` **85→100** | committed cap pinned | `=85` |
| 17:01 | `PER_SERIES_CAP` **10→30** | footprint stuck at 17 (per-series binding) | `=10` |
| 17:09 | `MAX_TOTAL_CAPITAL` **100→150** | pinned again | — |
| 17:12 | **loss meter re-baselined** $63.34→$247.54 | operator deposited +$150; deposit corrupts the equity meter | (state file) |
| 17:17 | `PER_SERIES_CAP` **30→100** | let B200 (40 strikes) compete for footprint | `=30` |
| 17:23 | `SERIES_ALLOW` +5 (9→14): `KXH100MON,KXMUSKNW,KXCHIPBURRITO,KXTRUMPENDORSEMENTS,KXGENERICBALLOTVOTEHUB` | census found ≥80% two-sided gas-like series | remove |
| 18:09 | `TAKER_FLATTEN` **1→0** (REVERTED) | verified it would de-hedge live pairs (see §2) | — |
| 18:11 | `MAX_TOTAL_CAPITAL` **150→250** | gross-committed pinned at $150 while $156 cash idle | `=85` to fully revert |

**Current allowlist (14 series):** 5 temp + `KXAAAGASD,KXAAAGASW` + `KXB200MON,KXAMSAVO,KXH100MON,
KXMUSKNW,KXCHIPBURRITO,KXTRUMPENDORSEMENTS,KXGENERICBALLOTVOTEHUB`.

⚠ **The cap-raising (85→100→150→250) is a TREADMILL, not a fix.** The committed guard counts GROSS
held cost; paired/binary exposure barely uses net capital, so held inventory grows into any cap
within cycles. The root-fix dive (§3) is designed to end this. Until it ships, the cap sits above
account value and the loss halt is the only brake.

---

## §2 THE ONE LIVE-RISK ITEM I CAUGHT AND FIXED — `TAKER_FLATTEN`

I flipped `TAKER_FLATTEN=1` at 15:19Z to fix expiry losses. Two later findings reversed that call,
and I reverted it at 18:09Z:
1. **It would de-hedge live pairs.** Verified on today's book: the trigger is naked-only but the
   flatten crosses the FULL position. `GASW-4.140` (naked +6, held +40) → would cross 40, orphan 34
   on the paired 4.160 leg. `GASD-4.110` → orphan 15. It armed ~03:39Z tonight before I reverted.
2. **The exit it protects is worth ~8%** (inventory-doctrine dive: 9.69→8.90 c/ct). Bad trade.
3. Second failure mode: on a one-sided book (`~50%` of contracts), `flatten_to_zero` cancels our
   resting exit FIRST, the IOC fails, the fallback fails on the same book → exit cancelled, nothing
   replaces it.

**`TAKER_FLATTEN=0` now (venue doctrine: residuals left resting, checked manually).** The root
bug — crossing the full position when only naked triggered — is **task #10 / #12** (code fix).

---

## §3 RESEARCH DIVES — 4 launched, 3 DONE + committed, 1 RUNNING

### DONE, committed `9c3c6b4`, all three refuted their own proposals and CONVERGE:
**The money is decided by MARKET SELECTION and SIZING — not exit logic, not drift detection.**

1. **`KALSHI_INVENTORY_DOCTRINE_2026-07-23.md`** — a perfect exit is worth ~8%. Exit is the wrong
   lever. The unused selector is **reward density ρ_c**: 74.7% of quotable contracts pay $0. Our
   own `KXAAAGASW` is 12× worse per contract than `KXAAAGASD` yet holds our biggest position. The
   bot ranks by `usd_day` (volume); it should rank by reward/contract. ⚠ Also: "gas profitability"
   is −$5.28 over 4 events, −$0.01 dropping one — indistinguishable from zero.
2. **`KALSHI_DRIFT_AWARENESS_2026-07-23.md`** — being run over is **NOT detectable** at 2-min
   cadence. Weather GAPS (one temp print re-rates the book 0.485→0.085 in a minute), it doesn't
   drift. No detector survived; all fire on profitable gas too. Faster cadence would make it WORSE
   (filled on the toxic side sooner). **The cadence check the operator asked for: measured live,
   gas/compute/avocado move ≤1 tick / not at all over 2 min — 2-min is fine, no reward left on the
   table. Weather is unfixable by speed.**
3. **`KALSHI_EXPANSION_PROPOSAL_2026-07-23.md`** — three starting premises measured FALSE: "dark
   most of the day" (actually 99.2% coverage, zero dark hours); "5 temp series diversify coverage"
   (ONE calendar, identical to the minute — they diversify RISK across 5 cities, not coverage);
   "capital binds at K≈7". Cheapest real lever = **`MIN_DEPTH_SYM` 0.25→0.20** (depth, not breadth).
   Breadth slate did NOT survive refutation. ⚠ Flags TWO live items: the TAKER_FLATTEN de-hedge
   (§2, done) and the raw-capital basket re-solve.

### ⚠ ALL THREE DIVES INDEPENDENTLY FLAG THE SAME BRAKE:
**Do NOT cut `KXTEMP*` on the −$13.06 figure — that was the withdrawn partial ledger (§M13).**
Temp is **91% of reward income**; credits lag one Time Period; one $12.94 NYCH credit closes 99% of
the claimed gap. **Decision gated on re-export of the transaction CSV AFTER 2026-07-27T04:00Z**,
when every period (incl. gas-weekly) has credited. Pre-registered rule: de-admit temp ONLY if
net-of-full-rewards stays materially negative; else hold or reversibly size down.

### RUNNING (still not returned as of 18:25Z): `wef4ikfcf` — CAPITAL-ACCOUNTING ROOT-FIX
Launched ~18:12Z, **still running at handoff close.** Designing the fix for the gross-vs-net
committed bug (§1 treadmill). Will write `KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md`.
**NEXT SESSION: check `/workflows` or `TaskOutput wef4ikfcf` FIRST — if that file exists, read it;
if the dive was killed by the session ending, resume it (`Workflow scriptPath` +
`resumeFromRunId: wf_60f6f8d2-5e0`, script at
`…/workflows/scripts/kalshi-capital-accounting-rootfix-wf_60f6f8d2-5e0.js`).**
Key question it answers: what does Kalshi ACTUALLY reserve (measured from balance deltas, not the
retracted "4.3× netting" folklore), and should the real limit be net-capital + a PER-EVENT cap
rather than the global gross knob. The machinery (`ladder_pairing`, `naked_held_cost`) already
exists; the cap just doesn't use it. **The deadliest refuter angle: a pair that half-fills leaves a
hedge that only existed on paper → under-reservation blowup. The fix must keep a free-cash hard
ceiling.** This is the fix that ends both the cap treadmill AND the inert-brake state (§0) — the
highest-value open thread in the whole handoff.

## §3a LIVE SNAPSHOT AT HANDOFF CLOSE (2026-07-23 18:24:47Z) — for drift comparison next session
`fp=40 quoted=15 two-sided=12 one-sided=3 committed=$234.53/250 held=$85.54 naked=$27.95
at_ref=75.7%`. Cap raise to 250 took effect (committed climbed from the $150-pin to $234). Bot is
actively quoting 15 markets, 12 two-sided. `at_ref` 75.7% (dipped from ~94% as it deploys fresh
capital into newer books — watch it recover). Balance $162.04, ~8 positions. **If the next session
sees committed pinned at $250 with cash idle again, the treadmill has returned — the root-fix
(above) is the answer, not another raise.**

---

## §4 EARLIER-SESSION WORK — committed, NOT deployed (from the instrument-fix workflow)

Commits `80315af`,`5cb3fd9`,`72c01f3`,`49f60bc`,`13385e3`. Suite **159 passed, 2 xfailed**; smoke clean.

| fix | ship verdict |
|---|---|
| **attribution ledger root fix** (`72c01f3`) — fill cash sign-inverted on 156/317 fills; residual now $0.00 in 24/27 intervals | **DO NOT DEPLOY AS-IS** — 4 preconditions (task #11), hourly timer so a brick is silent |
| **settlement P&L attributor** (`49f60bc`) — kills the −$442 reading; GASD-26JUL23 = −$8.20 | safe (zero importers) |
| **study fixes** (`80315af`) — D1 selection bias, D2 census ceiling, D3 degenerate ranking, 22 pins | safe (not on VPS) |
| **quoter 3 defects** (`5cb3fd9`) — categorical event-netting guard, strike-parse, inflatable loss meter | **changes trading; the loss-meter half needs an equity-jump clamp (task #13) before deploy.** ⚠ verifier found: the categorical guard fails OPEN not safe, and the strike-parse defect is latent-not-live (0/456 tickers). |

The 2 xfails pin the two design defects left unfixed: **paired-inventory downside invisible to the
breakers**, and **no exit path for a matched pair on a program-expired ticker**.

---

## §5 OPEN TASKS (nothing lost — full list)

| # | item | state |
|---|---|---|
| 3 | Mirror-symmetry claim overstated (pins 0/3 polarity bugs); find real polarity coverage | open |
| 4 | Finish ladder self-hedge review debt (paired_ct now live, was 0) | open |
| 6 | Redeploy `flatten_kalshi.py` from git blob + md5-gate (CRLF, kill switch) | open, freeze-gated |
| 7 | Re-derive the two-sided plug-in EV (exclusion is MARKET-level) | partly answered by §M12 A/B; EV still open |
| 8 | Commit `kalshi_live/series_fee_types.json` — 2 fee pins skip silently without it | open |
| 9 | Frozen-dataset md5 guard is CRLF-dependent | open |
| 10 | **My freeze-check was sha256-equality — round-trip-blind. Two config writes read green.** Replace with mtime+backup inventory. | open |
| 11 | BLOCKER before ledger deploy: version the row, mixed cash models | open |
| 12 | Quoter: read strikes off the market object, not the ticker string (fixes `KXTRUMPENDORSEMENTS` A-prefix darkness) | open |
| 13 | Clamp single-cycle equity jumps before the new loss meter can fire | open, gates quoter deploy |

### ⚠ KNOWN LIVE HAZARD from today's expansion (not yet a task):
**`KXTRUMPENDORSEMENTS` has A-prefixed strikes (`A3,A5,A10…`).** The deployed `_strike_of` does
`float(tail.lstrip("T"))` — `float("A10")` throws → returns None → **ladder pairing silently
disabled for that series**, inventory always counted fully naked (conservative, but no hedge
benefit, no error logged). It is ALSO the highest-toxicity shape added (Truth Social endorsement
counts = discrete news-driven flow). Watch its naked exposure; consider removing if it accumulates.

---

## §6 THINGS I GOT WRONG TODAY (so the next session doesn't inherit them as fact)

- **"The bot is net negative / not killing it"** — WITHDRAWN (§M13). Pooled a one-off emergency
  flatten and a lagging credit ledger. Yesterday's actual market-making made **+5.81% of notional**.
- **"Temp is the loss, cut it"** — the −$13.06 was the withdrawn partial ledger. See §3.
- **"Maker fees are not universally zero, our series are exempt"** — BACKWARDS (§M10). Zero by
  default; only 130/12,151 series charge.
- **"Dark most of the day"** — FALSE, 99.2% coverage (§3).
- **`TAKER_FLATTEN=1` was a good idea** — it wasn't; reverted (§2).
- **Six measurement bugs self-caught** (capital→size $156/period, degenerate ranking, missing R3,
  selection bias, two sample sizes in one doc, fee rule backwards). All recorded in canon.

The through-line: I moved faster than the evidence for much of the afternoon. The dives caught up
and corrected several live calls. The careful posture now: **revert done (TAKER_FLATTEN), stop
tuning, wait for the capital root-fix and the 07-27 re-export.**

---

## §7 WHAT THE NEXT SESSION SHOULD DO, IN ORDER

1. **Read `wef4ikfcf` output** (capital root-fix) when it lands — it's the fix for the treadmill and
   the inert-brakes state. Likely proposes net accounting + a per-event cap. CODE change, full ship
   discipline (pytest + adversarial review) before deploy.
2. **Re-baseline the loss meter after ANY money move.** Right now it's the only live brake.
3. **Rank the footprint by reward density, not `usd_day`** — the one lever all dives point at. 75%
   of quotable contracts pay $0; we just widened the funnel to 14 series.
4. **2026-07-27T04:00Z: re-export the transaction CSV**, recompute per-family net-of-full-rewards,
   decide temp. Pre-registered rule in §3.
5. **Watch `KXTRUMPENDORSEMENTS`** (unpaired + toxic) and the new series' first settlements.
6. Two support emails drafted, NOT SENT (`KALSHI_SUPPORT_EMAIL_DRAFT.md`, `..._FOLLOWUP_DRAFT.md`).
   The follow-up's item 5 (Sep-1 sunset — BOTH incentive programs expire 2026-09-01) is the highest
   business-value ask; the whole reward basis has a ~6-week known horizon.

## §8 COMMANDS

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"; VPS="ubuntu@18.201.216.0"
python kalshi_live/kalshi_status_readonly.py ; python kalshi_live/kalshi_delta_check.py
ssh -i "$KEY" $VPS 'sudo tail -3 /opt/pa2-maker-kalshi-live/plans-$(date -u +%Y%m%d).jsonl'
# config: ssh … 'sudo grep -vE "KEY|PRIVATE" /opt/pa2-maker-kalshi-live/live.env'
# KILL: ssh … 'sudo systemctl disable --now polymarket-maker-kalshi-live.timer && sudo -u polymarket touch /opt/pa2-maker-kalshi-live/STOP'
```

**Untracked research artifacts (~60 files in `kalshi_live/`)**: probe scripts + JSON from the 4
dives (`gasm_*`, `kalshi_drift_*`, `kalshi_horizon_*`, `kalshi_doctrine_*`, etc.). Evidence, not
code. Left untracked deliberately; triage before committing or cleaning. `series_fee_types.json`
among them = task #8.

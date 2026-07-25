# KALSHI MAKER — HANDOFF 2026-07-25 (new session)

Branch `claude/maker-kalshi-live` @ `905778f`. Bot **PARKED**. Read §0 and §1 before anything.

---

## §0 — READ FIRST: RETRACTED CLAIMS. DO NOT INHERIT THESE.

This session made several confident claims that were then **disproved by blind audit**. They are wrong. If you find them in an older doc or a commit message, they are void:

| Retracted claim | Truth |
|---|---|
| "R1: $/day = period_reward/10000/**window_days**" — the lane's canon formula | **WRONG. `period_reward/10000` IS ALREADY the daily per-market pool.** Understated the venue **6.2×**. See `project_kalshi_r1_formula_wrong.md` |
| "Venue pool $27,755/day; KXAAAGASD is #1, biggest on the venue" | **$174,403/day. KXAAAGASD is rank 29.** |
| "62% of the time the opposite side prints within 60s in gas" | **One strike, one moment. 0–5% on other gas strikes.** The script's column that supposedly produced it was structurally dead (`created_time_ts` doesn't exist) |
| "Sub-minute maker round trips are profitable (+1.46¢/ct)" | **Tautology.** That cell is 10 trips / 64ct / **+$0.93 total**, 74% from one strike. Spread capture is FLAT across all holding buckets; only drift varies, mechanically with time. Forcing a 60s flatten converts to taker exits at **−23.3¢/ct** |
| "+2.5¢ of spread per round trip (entry+exit)" | Composition error — EXIT ct (1,896) > ENTRY ct (1,375) proves the labels are broken. Correctly paired: **+2.89¢** on 339 matched trips |
| "Rewards were under-counted 3.5×" | **Window artifact.** The $25.21 CSV figure was EXACT for its window (cutoff 07-23T03:38Z) |
| "The ledger identity closes ✓" | **Algebraically vacuous** — rewards is defined as the residual, so it closes by construction |
| "The $1.00 per-period floor is confirmed" | **Not established.** Zero items below $1.00, but at n=31 that's p≈0.14–0.37 |
| "KXAAAGASW earned $0 — the reward model failed its one positive test" | **VOID. It's a WEEKLY that closes 07-27.** Nothing to credit yet |
| "$10.19 derived-vs-UI reward gap is mark uncertainty" | Marks explain **$0.49**. Gap is **$9.72** and points at **deposits** |

**META-LESSON, now enforced by a hook:** every number needs its source AND its denominator, and a *pending* period is not a *zero*. `.claude/hooks/inject_verification_rules.py` (wired in `.claude/settings.local.json`) injects RULE ZERO / SIX / SEVEN before every turn. **The pending-vs-zero rule must live in the SCRIPTS, not in your head** — I made that error twice in one session, the second time in my own generated table.

---

## §1 — LIVE STATE (verified 2026-07-25 20:41Z)

- **Deployed build:** `727ca7c5` (md5 `727ca7c59840a42b51c19e24c65a0982`), timer `polymarket-maker-kalshi-live.timer` **active** (2-min), **no STOP sentinel**.
- **WOUND DOWN, and the wind-down is enforced by `KALSHI_MAX_TOTAL_CAPITAL=1`** — NOT by reduce-only. Any "just flip a flag" plan rests **nothing**: a 20ct order costs >> $1 and every accumulating create is skipped. **Raising that cap IS un-parking the bot and needs operator sign-off.**
- Other live env: `TAKER_FLATTEN=0`, `REDUCE_ONLY_KEEP_BOTH=1`, `HELD_MAX_USD=100`, `DAILY_LOSS_HALT_USD=40`, 14-series allowlist.
- **Equity $286.17** (cash $248.68 + positions $37.49). **0 resting orders.**
- ⚠ The `ubuntu` SSH login **cannot read** `/opt/pa2-maker-kalshi-live/` — use `sudo -n bash -c "..."` so globs expand as root.

## §2 — MONEY, verified

| | | source |
|---|--:|---|
| Deposits | $365.00 | operator (**no receipt — prime suspect for the $9.72 gap**) |
| Rewards | **$88.07** | operator UI, 31 items, parse sums exactly |
| Realized trading | −$137.79 | `kalshi_ledger.py`; matches canonical `kalshi_settlement_pnl.py` to **$0.0000** on the same 70 settled contracts |
| Unrealized | −$37.67 | mark |
| **Net** | **−$77** (−21.1%) | |

- Reward split: **temp-hourly $55.03 (18 items) / gas-daily $33.04 (13 items)**. No other family has ever paid.
- Row-level pairing **receipt-verified for $25.21 / 10 rows (28.6%)** via the CSV's credit-batch timestamps; shift-0 is the unique zero-violation alignment. **The other $62.86 (71.4%) is unattested.**
- Credit lag: **+1 day from real venue close, 31/31**. Credits CAN land same-day (`KXAAAGASD-26JUL25` paid within 24h of close).
- **Quote it as "$88.07, of which $25.21 is receipt-verified."**
- ⚠ **One row needs re-reading off the UI:** `KXTEMPNYCH-26JUL2206 $12.94` = **14.7% of the total**, 2.6× the next item, reward÷notional **0.719 vs 0.079 median** (9× outlier), on a single $18 fill. Nothing can corroborate it.

## §3 — WHAT ACTUALLY PAYS (26 closed-window events: 16 paid, 12 zero)

| | PAID (16) | ZERO (12) |
|---|--:|--:|
| **pool $/day** | **975** | **942** ← does NOT separate |
| ct per strike | 58 | 26 |
| entry point in window | **0.16** | **0.36** |
| activity span | 0.39 | 0.19 |

Weak signal: earlier entry + more size per strike. **But the controlled pairs refute a behavioral story.** Same hour (window closing 07-22 10:00Z), same pool:
- `KXTEMPNYCH` 20ct/1 strike → **$12.94**
- `KXTEMPDCH` 20ct/1 strike → $1.51 (behaviorally identical, 8.6× less)
- `KXTEMPCHIH` **52ct/2 strikes, earlier, longer span** → **$0.00**

**The largest credit in the whole ledger came from our smallest footprint.** The dominant variable is market competition in the R4 score denominator, which we do not measure.

**THE STRUCTURAL BLOCKER:** plan logs are **per-CYCLE, not per-market**. Those three events shared the same cycles, so they share one `at_ref_pct`. **The telemetry cannot discriminate the events it needs to.** Day-level analysis is confounded beyond use (temp died mid-window; n=3 usable days).
**⇒ The single highest-value change is to log, PER MARKET PER CYCLE: our resting size, our price vs the reference, and the competing qualifying depth.** Until then no "what pays" model is testable.

## §4 — PENDING, NOT ZERO (do not score these as failures)

| event | closes | status |
|---|---|---|
| `KXAAAGASW-26JUL27` | 07-27 03:59Z | 51 fills / 449ct across 6 strikes — **credits ~07-28** |
| `KXMUSKNW-26JUL31` | 07-31 22:59Z | pending |
| `KXTRUMPENDORSEMENTS-26JUL25` | 07-26 14:00Z | pending |
| `KXAMSAVO-26JUL24` | 07-24 22:59Z | lag not elapsed |

## §5 — TEMP (62.5% of all reward income) IS ABSENT, NOT PROVEN GONE

- **Latest temp `end_date` anywhere = 2026-07-22T17:00:00Z** (across 10,000 program records, all statuses).
- **88 polls at 1-min cadence, 19:07→20:38Z 07-25: zero temp appearances** (`scratchpad/temp_poll.py`, appends `temp_poll.jsonl`).
- Mechanism: temp are **~58-minute hourly** programs that exist only while that hour's market is live, so they only ever appear in `status=closed`. **Do NOT claim they are permanently gone** — keep polling.

## §6 — VENUE UNIVERSE (corrected formula)

**$174,403/day · 2,190 active programs · 147 series. We quote 8 = $10,500/day = 6.0%.**
Per-market daily pool: min $25 / median $100 / **max exactly $1,000** → **147/147 inside Kalshi's documented $10–$1,000/day/market band** (this is the check that caught the old formula).

Top: KXFUNDRAISING $10,470 · KXRT $5,600 · **KXAAAGASM $5,400 (rank 3 — a gas sibling we do NOT quote)** · KXFEDMENTION $4,300 · KXFEDERALCHARGE $4,000. Ours: **KXMUSKNW rank 13**.
**Niche:** `KXEOWEEK` — Target **300** (vs 1000, 3.3× easier gate) + $2,333/day + **498h window**. 8 series total carry Target 300.

**API footguns (verified):**
- `/incentive_programs` status filter is **CASE-SENSITIVE**; only `active`/`closed`/`upcoming` recognized. `status=ACTIVE`, `pending`, `scheduled`, or any typo **silently returns the whole 118,844-record history unfiltered**. Lowercase `active` IS exact.
- **Zero future-window programs exist** (`start_date > now` = 0 across 96,526). An active scan misses nothing that exists — but it IS a single instant: **147 live vs 2,782 series the LIP has ever touched.**
- Fields are `*_fp` / `*_dollars`. Plain `volume`, `open_interest`, `yes_bid` return **None** — reading them fabricates zeros (this is what made me call live compute markets "dead"). Orderbook = `orderbook_fp` → `yes_dollars`/`no_dollars`. Candles = `close_dollars`. `/markets/trades` has `created_time` (ISO), **no** `created_time_ts`.
- **Fill direction is ACTION-ONLY** (buy=+1, sell=−1, yes-signed) — venue-verified on **100% of the tape** (12/12 unsettled + 70/70 settled). Deriving sign from `side` inverts ~half the fills and already caused one false conclusion.

## §7 — CODE ON THE BRANCH, NOT DEPLOYED

- **WS daemon** (`maker_kalshi_ws_daemon.py`, `kalshi_ws_feed.py`): Stage A = WS-triggered full `run_once()` cycles; Stage B = ~200ms reprice behind **`KALSHI_WS_HOT` (default 0)**. Survived 2 adversarial review rounds; round 2 fixed a partial-fill refill BLOCKER by taking the replacement count from the **venue's cancel response**. **11/11 mutants killed.** Owed before arming: round-3 review + one observed live fill (the `fill` channel payload has never been seen).
  - ⚠ **The markout/flatten justification for this build was retracted (§0).** It's uptime/robustness infrastructure, not a proven money-maker.
- **HTTP pooling** (`KALSHI_HTTP_POOL`, default 0): measured **173ms → 63ms p50 (2.7×)**. Tests pin the two dangerous parts — a 4xx must still raise, and retries disabled so a POST can't double-submit.
- **`KALSHI_REQ_SPACING_S` / `KALSHI_READ_BUDGET`** now env-overridable, defaults unchanged (0.55 / 200). 0.55s/read is the largest single contributor to cycle time; the read token cost is undocumented, so lowering it is an operator call.
- **`kalshi_ledger.py`** — four-way reconciliation. ⚠ Two fixes owed: drop `unrealized` from the derivation (use `rewards = cash + cost_basis − deposits − realized` so the mark cancels — removes $0.49 contamination and a latent **$32.40** hole where a missing mid silently drops a whole position), and stop printing "IDENTITY CHECK closes".
- 281 tests + 2 xfailed green.

## §8 — OPERATOR DECISIONS OPEN

1. **Exact deposit total** — is it $365? A ~$9.70 difference closes the entire reward gap.
2. **Re-read the `$12.94` row** (§2).
3. **Un-park or not** — requires raising `MAX_TOTAL_CAPITAL` from 1. Nothing is proven positive; rewards are real ($88.07) but so is the fill cost.
4. `KXAAAGASM` (rank 3, $5,400/day, gas sibling, 152h window) — never quoted. Worth a look.

## §9 — SCRATCH ARTIFACTS
`.../8289ccb6-.../scratchpad/`: `rewards_ui.tsv` (31 canonical reward rows) · `universe.py` + `universe.json` (corrected) · `universe_WRONG_r1.json` (old, do not use) · `what_pays.py` + `.json` · `markout_v2.py` (gates on position reconcile; **`markout_test.py` is guarded-dead, inverted sign**) · `holding_time.py` · `flatten_speed.py` (**part b not trustworthy**) · `temp_poll.py`/`.jsonl` · `plans_all.jsonl` (2,680 rows).

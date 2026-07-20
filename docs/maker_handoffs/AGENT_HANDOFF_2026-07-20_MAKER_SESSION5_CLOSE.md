# MAKER LANE — SESSION-5 CLOSE HANDOFF (2026-07-20)

**One-line state:** on-chain reconciliation BUILT (the #1 pre-live gap, closed),
the kill primitive re-scoped so it can never cancel the co-tenant bot's orders,
and the maker venv's CLOB SDK installed. Three commits on `claude/maker-bot`,
pushed. Nothing deployed; nothing has ever traded real money.

---

## 0. HARD RULES (operator-set, unchanged — obey before anything else)

- **"Maker"** — NEVER "MB" (= MirrorBot) or "MM". Background processes are
  **RECORDER ARMS**, never "sim".
- **NUMBERS RULE:** never quote a Maker $ from memory or a prior message. ROI/EV
  ONLY from a fresh `scripts/maker_research/mm_roi_canon.py`. Rewards $ = MODEL
  accrual until real receipts. Flag any contradiction as a correction, out loud.
- Everything is PAPER. A real-capital pilot is **propose-only** — the operator
  decides capital, mix, kill numbers.
- **`git branch --show-current` before ANY repo write.** The main checkout is
  held by another bot; work in a worktree on `claude/maker-bot`.
- **Priority: PEER (changed 2026-07-20).** MB no longer has right of way — all
  bots coordinate on shared resources, no default winner. ⚠ `CLAUDE.md` still
  says "MIRRORBOT HAS ALL PRIORITIES … non-negotiable" — that file is STALE vs
  this rule (see §4 TODO).

## 1. WHAT SHIPPED THIS SESSION (branch `claude/maker-bot`)

| Commit | What |
|---|---|
| `a68173b` | `scripts/maker_onchain_recon.py` + tests — read-only on-chain ledger reconciliation |
| `0063c61` | kill primitive scoped to Maker's own tokens (shared-account safety) |
| `971e68e` | verification-round fixes: asset-key chain twin + cancel-response proof |
| (VPS, no commit) | `py_clob_client_v2==1.0.1` + `httpx` installed into `/opt/pa2-maker-live/venv` |

- **Reconciler:** event-sources the ledgers (deltas + paper snapshots +
  settlements) into a NET view that must equal engine state, and a GROSS view
  (fill legs only) that must equal the chain. Reporting contract: PASS/DRIFT/
  SKIP/WARN, exit `0` pass · `2` drift · `3` surplus-only · `4` cannot-certify ·
  `1` error. **rc=0 is the ONLY go-signal.** Shared-wallet aware (foreign
  baseline; unattributable is reported, never guessed). Live-validated against
  66 real on-chain positions; data-api agreed on all 66.
- **Kill fix:** `client.cancel_all()` is ACCOUNT-wide and the wallet is shared
  with MB, so it would silently cancel MB's resting orders. `cancel_all()` now
  reads `get_open_orders()` (paginates to END_CURSOR — verified, no truncation),
  keeps orders on Maker's own tokens, cancels only those in BATCH_MAX chunks.
  Rule: **UNKNOWN IS NOT FLAT** — an order it cannot attribute forces `False`
  unless the durable token history (`state.json`) loaded (`_scope_complete`).
- **Tests:** 115 engine (was 73 at session open), 130 reconciler, 330
  maker-family — all pass.
- **Review trail:** 3 adversarial rounds on the reconciler + a 5-lens/3-skeptic
  verification workflow on the kill fix. Rounds found, in order: a band-aid
  settled-skip; six ways a loss exited 0; four bugs introduced by those fixes;
  an rc=0-unreachable regression; a call-site wiring bug all unit tests missed;
  then the asset-chain twin of the id-chain bug + an unproven cancel response.
  Every finding fixed and regression-tested.

## 2. VPS STATE (2026-07-20)

Host `ubuntu@18.201.216.0`, key `C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem`.

- Unit `polymarket-maker-live` **active**, `MAKER_SUBMIT_MODE=paper`, engine md5
  `b27d1a92…` = **the OLD engine (4264175)**. The 3 new commits are NOT deployed.
- **The paper arm is HALTED on the day-loss floor** (dayPnL below −$75, q=0/140,
  every integrity counter zero). This is correct behaviour, not a fault. The
  operator resumes by removing `/opt/pa2-maker-live/HALT`.
- venv now imports the CLOB SDK (was missing — live mode would have crashed at
  startup). Handoff-4 claimed v2 **1.1.0**; the version actually proven on this
  host is **1.0.1** — corrected.

## 3. HARD CONSTRAINTS ON DATA AND TRADING (operator ask — the full inventory)

*All cited to source. "Hard" = enforced in code or an operator directive, not a
tuning preference. Env knobs that GATE the trade universe are Tier-2 per
CLAUDE.md and need a rollback line when changed.*

### 3a. Operator directives (non-negotiable, not in code)
- **Naming:** "Maker", never "MB"/"MM"; arms are "recorders", never "sim".
- **NUMBERS RULE** (`MAKER_MASTER_PLAN.md` §0): no Maker $ from memory; canon or
  in-session measurement only; contradictions flagged as corrections.
- **Paper is production** (CLAUDE.md): every check that matters live matters
  identically in paper. The paper/live flag affects ONLY the final submit step.
- **Income basis = OUR measured capture**, floored by the backtest, verified by
  pilot receipts to our own wallet. Cohort receipts are a pool-existence anchor,
  **never** an income forecast (`MAKER_MASTER_PLAN.md` §2, §5).
- **Priority = peer** (2026-07-20), superseding MB-primacy.

### 3b. Live-mode interlocks (`load_config`, refuses to start otherwise)
- `MAKER_SUBMIT_MODE ∈ {paper, live}`; live REQUIRES all three of: `MAKER_PK`,
  `MAKER_LIVE_ACK=I-UNDERSTAND-REAL-MONEY`, and a wallet. Partial config → hard
  `ValueError` at boot (deliberate; do not "fix").
- Nonsense knobs fail LOUD at start (negative jitter, freshness ≤ 0,
  rotation_frac ∉ [0,1), any cap ≤ 0) — never corrupt the money path at runtime.

### 3c. Trading mechanics (hard, in code)
- **Post-only, never-cross, ALWAYS.** Guard-stack `would_cross` deny +
  `post_only=True` at submit (belt AND suspenders). The engine only ever BUYs
  (YES-bid or NO-bid leg); a non-BUY fill is ledgered loud and never applied.
- **Guard stack order (single choke-point):** kill-switch → day-loss floor →
  freshness (BOTH legs' books) → market net cap → market gross cap → sector
  gross cap → per-event one-winner floor → liquidity (never-cross) → execute →
  confirm. Any deny blocks the order; reasons are heartbeat-counted.
- **Kill sequence is ALWAYS cancel-ALL-first-THEN-halt** (latency = loss rate).
  HALT persists across restarts (HALT file + state flag); only the operator
  lifts it by removing `<base>/HALT`.
- **Cancels are scoped to Maker's own tokens** (this session) — the shared
  account means an account-wide cancel is forbidden. "Could not prove flat"
  must never read as "flat".
- **Batch-15 max per post/cancel** (`BATCH_MAX`, official docs canon).
- **Tick size is per-market and MUST be fetched dynamically** (`0.0025` on WC
  ML/spread/totals, `0.0001` exists; default `0.001`). A stale tick = order
  rejections + retry churn.
- **Neg-risk is routed, not blocked** — `neg_risk` fetched per market and passed
  to the order builder. (Distinct from MB's neg-risk rule; here it's just
  correct contract routing.)
- **HTTP budget 36,000/hr**; discovery every 1800s; disk cap 500MB → clean stop.

### 3d. Trade-universe gates (which markets Maker will quote)
- **Excluded sectors: `esports,finance`** by default (`MAKER_EXCLUDED_SECTORS`).
- **In-play block:** sports/esports markets are gated OFF once `game_start`
  passes (`gate()` → `in_play`) — adverse selection is worst live.
- **Weather extreme block:** weather markets with mid outside **[0.10, 0.90]**
  are gated (`extreme_wx`). This is the band rule applied to WB's niche.
- **Wind-down / last-hours:** per gate policy — either a ramp (`ramp_h` before
  `end_ts`) or `last_hours` (after 19:00 UTC on the expiry day). Adverse>2pt is
  36–44% of fills in the final 16h vs 10–12% beyond 48h.
- **Vol-pull, tape-velocity, sensor-hot:** transient per-market pulls after a
  sharp move / fast tape / informed-flow event (sensor consumption OFF by
  default until ≥07-25, validation-first).
- **Gate policy** via `MAKER_GATE_POLICY ∈ {P0_base, P1_volfit, P2_ramp,
  P3_tapevel, P4_all}`; default **P0_base is PROVISIONAL** pending the v5
  lab lock (see §4).
- Discovery filters: ≥2 tokens, value>0, `rewardsMinSize`>0; top by pool,
  `max_per_sector` (25) then `max_markets` (140).

### 3e. Sizing / exposure caps (hard $ limits, per BotBankrollManager-style)
- **Market net cap** = `inv_cap_mult (3.0) × rewardsMinSize` per market.
- **Market gross cap** = `$150` (`MAKER_MARKET_GROSS_CAP_USD`).
- **Event cap** = `$200` — the per-event one-winner floor (v6 semantics): a
  netted multi-outcome position's worst-case winner minus total cost.
- **Sector gross cap** = `$600` (per-sector override via `MAKER_SECTOR_CAPS_USD`);
  summed over LIVE markets only (departed markets quarantined out).
- **Day-loss floor** = `−$75` (`MAKER_DAY_LOSS_FLOOR_USD`). Tight by design
  vs the paper footprint (~7.5% trigger); **size it to real capital before a
  pilot — that is an operator kill-number decision.**
- Min-size footprint: ~140 markets at each market's `rewardsMinSize`.

### 3f. Data-source traps (hard, learned the hard way)
- **Gamma resolution reads MUST use the path form `/markets/<id>`**, never
  `?id=` (the query param EXCLUDES closed markets — silently broke settlements).
- **data-api `/trades` is TAKER-ONLY** by default.
- **CLOB `asset_id` == on-chain ERC-1155 `positionId`** (verified this session,
  4/4 exact) — no `getPositionId` derivation needed for reconciliation.
- **Tokens sit at the proxy/funder wallet, not the signer EOA** — reconcile
  against `MAKER_FUNDER`.
- **CTF shares are 6-decimal** (both V2 collaterals). An 18-dec collateral read
  as 6 inflates 1e12 — the reconciler has a tripwire for it.
- **Import ONLY `py_clob_client_v2`** — the archived `py_clob_client` is rejected
  by the CLOB since the Apr-28 V2 migration.
- **Trading must run from the VPS** — residential IPs are geo-403 for order
  submission (reads are fine).
- Reward $ are share-model approximations (scoring `b` in-game multiplier
  unpublished; size-cutoff-adjusted midpoint; rebates only on
  `feesEnabled:true`) — always a FLOOR, verified only by pilot receipts.

## 4. TODO / NO-WORK-DONE (open items, none touched this session)

*Nothing below was started — this is the parking lot.*

1. **Deploy decision (operator).** 3 commits are ahead of the VPS engine. The
   arm is halted on the day floor right now — the safest possible moment to
   deploy. Not done on a bare "proceed"; needs explicit go. Deploy = backup +
   swap + verify boot; roll back with the prior release symlink.
2. **`CLAUDE.md` priority contradiction (operator/central).** It still says
   "MIRRORBOT HAS ALL PRIORITIES … non-negotiable". Memory is updated to peer;
   the repo file every session reads is not. Fix centrally — it lives in the
   main checkout held by another bot, so this session did not touch it.
3. **Wire the reconciler into the engine (own review cycle).** Today it is a
   standalone read-only tool. Boot-time self-heal (chain truth for y/n) and/or a
   runtime drift → halt trigger are a live-state change to a hardened engine and
   need their own tests + adversarial review. NOT a "while I'm here" add.
4. **Gate-policy lock (calendar-gated).** When the data clears ≥3 clean
   POST-CLIFF days: re-run `mm_roi_canon.py` + the v5 lab cross-check; if the
   ranking holds, set `MAKER_GATE_POLICY` in `/opt/pa2-maker-live/env` and
   restart (config only, no code). ⚠ TRAP: the v5 report's headline "active
   markets" table ranks the WORST policy first — rank only on total NET or
   canon EV/day.
5. **Funded preflight (blocked on operator go + a real fill).** Once deployed and
   the operator green-lights: `scripts/maker_preflight.py --stage sanity →
   scoring → fill → receipts`. The `receipts` stage is the first real check that
   rewards income is real, not modelled. `_cancel_shortfall` demands an
   affirmative `canceled` list — an unanticipated-but-benign response shape would
   surface HERE (fails loud), not in a live kill; confirm the real shape at the
   `fill` stage.
6. **Day-loss floor sizing (operator).** −$75 is a paper number; a real pilot
   needs a kill-number sized to deployed capital.

## 5. STANDING DISCIPLINE (kept — earned every round this week)

Ship nothing to live state without: tests + an INDEPENDENT adversarial review +
a first-output cross-check against a separate source + a live smoke test where
possible. **Assume one more bug exists** — every round this session found one,
including bugs introduced by the previous round's fixes, twice via the
adjacent-shape miss (fix one instance, its mirror stays broken → grep for the
twin). When a fix touches money math, PROVE equivalence with a differential test.

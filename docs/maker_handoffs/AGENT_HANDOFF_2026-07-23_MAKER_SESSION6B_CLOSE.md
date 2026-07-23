# MAKER LANE — SESSION 6B CLOSE HANDOFF (2026-07-23)

**One-line state:** the capital deadlock is FIXED, tested (157), 3× adversarially
reviewed, DEPLOYED to the paper arm, and A/B-proven on real infra; the pilot is
re-scoped to a ~$60 / $20-tier footprint with a softness measure built; the ONLY
blocker is still the operator-provisioned wallet.

Branch `claude/maker-bot` @ **`281c0c5`** (pushed, clean). VPS `polymarket-maker-live`
runs this engine in **paper**, now **RESUMED** (no HALT). Nothing has ever traded
real money.

---

## 0. HARD RULES (operator-set; read before doing anything)

- **"Maker"**, never "MB" (=MirrorBot) or "MM". Background processes are RECORDER
  ARMS, never "sim".
- **NUMBERS: rewards basis ONLY.** rewROI/day labelled "model, unverified"; NO
  net/EV headline ever; no derived EV until a real receipt. Read
  `docs/MAKER_NUMBERS_LEDGER.md` before quoting anything. Trading = NOISE, band only.
- **NO TAKER ANYWHERE.** A maker never crosses the spread. There is no taker path
  in the engine and there must never be one.
- **RULE FIVE (NEW 2026-07-23): KALSHI IS KING.** Kalshi is live + trading; this
  Polymarket Maker session/bot makes **NO change to any shared item without
  explicit operator permission** — the VPS box beyond `/opt/pa2-maker-live`,
  systemd, shared `/opt/pa2-shared/.env`, `base_engine/**`, `deploy.sh`, master.
  Maker-OWNED resources stay free (our branch, `/opt/pa2-maker-live` + its own
  env, `/opt/pa2-maker-sim*`, census, `scripts/maker_*`). Kalshi = a SEPARATE
  session/venue; never operate/modify its bot, units (`polymarket-maker-kalshi*`),
  or branch `claude/maker-kalshi-live`. (memory `feedback_kalshi_king_maker_shared_permission`)
- `git branch --show-current` before any repo write. Main checkout is held by
  another bot — worktree `claude/maker-bot`. Bash cwd drifts to the SB checkout;
  use `git -C <worktree>` + absolute paths.
- Windows CRLF: compare engine md5 via `tr -d '\r' | md5sum`.

## 1. STEP ZERO (read in order)

1. `docs/POLYMARKET_MAKER_RUNNING_TAB.md` — the append-only ledger of record.
   Its top has a **⚠ DO-NOT-CONFLATE table** (two separate cap problems) — read it.
2. This handoff.
3. `docs/MAKER_NUMBERS_LEDGER.md` + `MAKER_MASTER_PLAN.md` §0/§0a/§0b.
4. `deploy/maker-pilot-env.staged` — the staged (NOT applied) pilot config.

## 2. THE BLOCKER — the wallet (unchanged, only thing gating live)

Operator provisions a DEDICATED wallet (NOT MirrorBot's): fresh wallet → ~$5 POL
→ connect once on polymarket.com (creates the deposit wallet + approvals) →
deposit USDC → hand over `MAKER_PK` + `MAKER_FUNDER` for `/opt/pa2-maker-live/env`.
`MAKER_SIG_TYPE` is determined EMPIRICALLY at `--stage sanity`, never guessed.

**Pilot budget re-scoped (operator, 07-23): total well under $150.** See §4.

## 3. WHAT SHIPPED THIS SESSION (all on branch, `3531d83`→`281c0c5`)

**The capital deadlock — found, fixed, deployed, A/B-proven:**
- Root cause: the gross cap was **MERGE-blind**. `merge_pairs` nets spend down
  AFTER a fill, but the cap is checked BEFORE the order, so it denied the very
  order whose fill would relieve it; both legs cost money ⇒ ALL quoting stopped.
- Fix 1 `a660aa1` — merge-aware `eff_cost = cost − min(sz, held_opposite)`.
- Fix 2 `87b7c30` + `44998b1` — one-sided de-risk placement (`MAKER_ONESIDED_DERISK`,
  default ON, operator-DECIDED, on the revisit list). Necessary because the caller
  is two-sided-or-nothing, so the accumulating leg denies first and the hedge was
  discarded. A lone hedge scores ZERO rewards — risk reduction only.
- Also fixed `match_fills_paper` blindness (a None-bid row was discarded wholesale,
  so an ask-only hedge could never fill in paper).
- **3 adversarial review rounds, 2 DO-NOT-SHIPs before it was right.** 157 tests,
  13 mutants. Deployed `bb4ebe7`-era to the paper arm; A/B stress test: old engine
  quoted NOTHING on the seeded deadlock state, new engine placed the hedge alone.
  **Now firing LIVE on the arm's legacy one-sided inventory** (`derisk1` climbing,
  accrual up).

**Pilot de-risk + re-scope:**
- `MAKER_SECTOR_ALLOWLIST` — pins sectors BEFORE ranking (a shrunk max-markets
  can't grab wrong markets). `discovery_suspect()` fix so a small allowlisted
  universe doesn't freeze.
- Preflight `--stage scoring` cancel-shape probe (runs the engine's own
  `_cancel_shortfall` against the real venue response).
- **Classifier bug class fixed TWICE** — substring matches: `heat-`/"miami-heat",
  then `ar-IRAN-g`/`se-NATO-r`/`premier-E`/`d-EPL-oyment`. Word boundaries added.
  ⚠ family arms keep the OLD pattern mid-era (measurement attribution only).
- Kalshi doctrine port `docs/MAKER_KALSHI_DOCTRINE_PORT.md` — their taker
  machinery is N/A BY PLATFORM (both our legs are BUYs).
- Softness probe `scripts/maker_research/mm_softness_probe.py` — measures reward
  SHARE from public books. FINDING: share-rank ≠ pool-rank; biggest pools have
  ~0% share (whales camp the band). Standout: FIFA-viewership `2954097`, ~4.8%
  share on a $300 pool. ⚠ snapshot-noisy — average several samples before it
  drives selection.

**Owed-to-WB, delivered:** the P6 tilt-vs-control readout (`mm_tilt_readout.py`) —
tilt is rewards-NEGATIVE −14.0%/−13.5%, null control exact ($0.00 at w=0).

## 4. THE PILOT — staged, wallet-gated (`deploy/maker-pilot-env.staged`)

**Key correction:** `rewardsMinSize` (msz) is the REWARD-QUALIFYING threshold,
NOT the exchange's min order size. $2–3 orders are accepted; they score ZERO.
Since trading alone ≈ breakeven and the income IS the subsidy, don't go BELOW the
threshold — go to the CHEAPEST market that meets it ($20), not weather ($100).
- Weather is uniformly $100 → incompatible with a sub-$100 wallet.
- Staged config: `MAKER_SECTOR_ALLOWLIST=sports,entertainment,politics`,
  `MAX_MARKETS=3`, `MAX_PER_SECTOR=2`. Excludes `unknown` (the fail-open gate hole).
- ⚠ **Caps RE-SIZED** — the default day-floor $75 EXCEEDS a ~$60 wallet, so it
  would never fire. Now day floor $15, market/event $30, sector $60.

**Live path once the wallet lands:** `--stage sanity` (read-only) → SHOW OPERATOR
→ `--stage scoring` (incl. cancel-shape probe; also measure sub-msz scoring:
place a $3 and a $20 order, call `is_order_scoring` on both — MEASURE, don't
reason) → tiny live footprint (`MAKER_SUBMIT_MODE=live`, apply the staged env)
through one 00:00Z window → `--stage receipts` next day = FIRST VERIFIED NUMBER →
`maker_onchain_recon.py --wallet <MAKER_FUNDER>`. Scaling decision = operator's,
on the receipt.

## 5. VPS STATE (verified at close)

- `polymarket-maker-live` ACTIVE, paper, **RESUMED** (HALT cleared 07-23; the
  halt was mark drift on 7 legacy one-sided positions, settle_realized only $2.26
  — not live bleed. Copy kept `HALT.cleared-20260723`).
- Deployed engine md5 (on-box) = branch HEAD (installed `bb4ebe7`-era; §3 doc
  commits after are docs-only, engine byte-identical). Rollback backup:
  `maker_live_engine.py.bak-20260723_161838` (= the pre-session `1961f4b9…`).
- Family arms: v5 + v6 ACTIVE; sim/census inactive (as before).
- **Isolation verified 07-23** (the RULE FIVE review): our service loads only its
  own env; no Kalshi unit writes any Maker path; no Kalshi commit touched a shared
  file; we import zero `base_engine`. Forward guard, no existing bleed.

## 6. OPEN ITEMS / PARKED (all propose-only; several need OPERATOR input)

- **GAP-4 cap SIZING** (OPEN, operator capital call) — flat $150/market leaves
  30/140 unquotable; live arm shows heavy `market_gross_cap` denials at full size.
  MOOT for a sub-$150 pilot. **Do NOT conflate with merge-blindness (FIXED)** —
  see the tab's top table.
- **"soft" needs multi-sample de-noising** before it drives selection.
- Revisit `MAKER_ONESIDED_DERISK=ON` on first receipts / scaling / frequent `derisk1`.
- Parked: active delta shaping (GAP-1); wind-down keeps reducing side (GAP-2);
  qh row consumption so paper stops over-crediting a partly-filled one-sided row (G2);
  "unknown"-sector gate hole (de-fanged for pilot by the allowlist, unfixed full-universe);
  gate-policy lock (calendar-gated ≥3 clean days; clock restarted 07-23 16:38Z);
  reconciler→engine wiring.

## 7. STANDING DISCIPLINE (this arc earned every word of it)

Ship nothing to live state without: tests + an INDEPENDENT adversarial review +
a first-output cross-check vs a separate source + a live smoke test where possible.
**Binding lessons, paid for with 2 DO-NOT-SHIPs this session:**
1. A guard cannot be reviewed apart from its CALLER (killed 2 attempts).
2. Extracting only the arithmetic RELOCATES the untested surface — extract the
   whole caller.
3. A safety test that doesn't kill a deliberately-broken implementation pins
   nothing — MUTATION-TEST every safety test (4 of 6 once passed a broken mutant).
Money math gets differential tests. Assume one more bug exists. First-output
cross-checks against LIVE data caught both classifier bugs — keep doing them.

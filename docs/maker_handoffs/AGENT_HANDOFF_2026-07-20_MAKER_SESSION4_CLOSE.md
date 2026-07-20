# MAKER LANE — SESSION-4 CLOSE HANDOFF (2026-07-20 ~00:10Z)

**One-line state:** the live maker engine is BUILT, hardened through 7 independent
review rounds, and running in PAPER mode on the VPS. Real-money mode is code-complete
but locked behind three interlocks and blocked on the operator's wallet. Nothing has
ever traded real capital.

---

## 0. HARD RULES (operator-set, unchanged — obey before anything else)

- **"Maker"** — NEVER "MB" (= MirrorBot) or "MM". Background processes are **RECORDER
  ARMS**, never "sim".
- **NUMBERS RULE:** never quote a Maker $ from memory or a prior message. ROI/EV ONLY
  from a fresh run of `scripts/maker_research/mm_roi_canon.py`. Rewards $ = MODEL
  accrual until real receipts. Flag any contradiction as a correction, out loud.
- Everything is PAPER. A real-capital pilot is **propose-only** — the operator decides
  capital, mix, and kill numbers.
- **`git branch --show-current` before ANY repo write.** The main checkout
  (`C:/lockes-picks/polymarket-ai-v2`) is held by ANOTHER bot on a different branch —
  do not write there. Work in a worktree on `claude/maker-bot` (see §1).
- ONE branch: **`claude/maker-bot`**.

## 1. WHERE EVERYTHING IS

| Thing | Location |
|---|---|
| Branch | `claude/maker-bot`, HEAD `909aeed`, pushed + synced with origin, clean tree |
| Engine | `scripts/maker_live_engine.py` |
| Tests | `tests/test_maker_live_engine.py` (73) — full maker family = **153 pass** |
| Preflight | `scripts/maker_preflight.py` (sanity / scoring / fill / receipts stages) |
| Unit + env template | `deploy/polymarket-maker-live.service`, `deploy/maker-live-env.example` |
| Decision anchor | repo-root `AGENT_HANDOFF_2026-07-18_MAKER_PILOT_DECISION_PACKAGE_DRAFT.md` (sibling session) |
| Engine annex | `docs/MAKER_PILOT_GO_NOGO_DRAFT.md` (§6b/§6c/§6d = residuals, verification, crash-runbook) |
| Master plan | `docs/MAKER_MASTER_PLAN.md` (§0 NUMBERS RULE; §3 lists the live-engine arm) |

**Getting a worktree** (the prior session's was a session-scoped scratchpad and is gone):
```bash
git -C C:/lockes-picks/polymarket-ai-v2 worktree list          # look for claude/maker-bot
# if absent, create one (do NOT touch the main checkout's branch):
git -C C:/lockes-picks/polymarket-ai-v2 worktree add <your-scratchpad>/maker claude/maker-bot
```

## 2. VPS STATE (verified 2026-07-20 00:08Z)

Host `ubuntu@18.201.216.0`, key `C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem`.

- **Live engine:** `/opt/pa2-maker-live`, unit `polymarket-maker-live`, **active**,
  `MAKER_SUBMIT_MODE=paper`, engine md5 **`b27d1a92…`** (matches branch HEAD).
- Also running: recorder arms v3/v4/v5/v6 + sensor. Inactive: v1/v2 timers, backup,
  the two Kalshi units (separate venue, sibling session — do not touch).
- **Latest heartbeat (journalctl):** `q=73/140 halted=False zombies=0 lmiss=0
  feedfail=0 anom=0/0 respend=9` — every integrity counter zero; denials are the
  benign `market_gross_cap`/`market_net_cap` (oversized-min-size markets).
- **Kill switches:** `sudo touch /opt/pa2-maker-live/STOP` (cancels quotes, clean exit)
  or `sudo systemctl disable --now polymarket-maker-live`. A self-imposed halt writes
  `/opt/pa2-maker-live/HALT`; **operator resumes by deleting that file.**

## 3. WHAT IS BUILT AND VERIFIED

Engine = execution core (post-only GTC place / batch-15 / cancel / cancel_all on
`py_clob_client_v2`) + guard stack (single choke-point: kill → freshness → caps
[per-market net/gross, per-sector, per-event one-winner floor] → day-loss floor →
post-only never-cross) + resolution backfill + paper/live split at the final submit
step only. Gate policy is **config, not code** (`MAKER_GATE_POLICY`).

**Seven review rounds, every confirmed finding fixed** (do not redo these):
build-review 22 → 2nd-pass 9 → settlement-review 7 → pre-pilot 4-agent → triple-blind
whole-file (1 confirmed + 1 uncertain) → fix-verification triple-blind (1 regression +
1 residual) → **root-vs-bandaid audit: 20 ROOT / 5 PARTIAL / 0 BAND-AID**, all 5
partials since upgraded to true root.

**Ship-gate caught a real blocker** (worth knowing the standard): the fill-path
atomicity rewrite diverged from the old cost-basis math by one tick on ~2% of
0.001-tick markets (IEEE-754 double-rounding). Fixed by merging per-fill on the local
copy; a **400-trial differential fuzz vs the old semantics now proves zero divergence**
and lives in the test suite permanently.

**Live-verified on the VPS, not just unit-tested:** restart normalization (no phantom
downtime fills), day-floor kill + cancel-all + halt persistence across restart,
`.bak` state recovery from a deleted `state.json`, paper preserve-on-unrecoverable,
settlement reconciliation (a settled market reconciled exactly against the fills ledger).

## 4. WHAT IS **NOT** DONE

**Blocking real capital:**
1. **On-chain ledger reconciliation DOES NOT EXIST** — nothing reconciles the engine's
   in-memory `y`/`n`/`spent` against actual on-chain positions. Multiple review rounds
   named this the single biggest pre-live design gap; it is the backstop that would make
   an empty-state boot self-heal. **Highest-value engineering work remaining.**
2. **Operator wallet** (deposit-flow-provisioned, pUSD + POL + V2 approvals) — build
   spec step 3 (funded preflight) cannot start without it. Ask; do not engineer around it.
3. **Rewards income is MODEL accrual, never reconciled to an on-chain receipt.** The
   preflight `receipts` stage is the first real check. The whole thesis rests on this.
4. **Paper→live fill fidelity unmeasured** (queue position, adverse selection at our
   quoted levels). Only the pilot prices it.

**Calendar-gated (cannot be worked, only waited for):**
5. **Gate policy is NOT locked.** Two independent sources (canon + the v5 lab paired
   ledger) agree on the ranking, but the window has not cleared the ≥3-clean-day bar and
   the Jul-19 promo cliff means the clock should restart on **post-cliff** data. When it
   qualifies: set `MAKER_GATE_POLICY` in `/opt/pa2-maker-live/env` and restart — no code
   change. ⚠ **Trap:** the v5 report's headline "active markets" table ranks the WORST
   policy first (its damage sits in the excluded departed/frozen-mark bucket). Rank only
   on total NET or canon EV/day.
6. **Post-cliff pool re-measure** — the hourly census is capturing it unattended.

**Deferred / watch (documented, non-blocking):**
- FIX-2 structural `settled` clear at the fast loop (deferred — risky, no current defect).
- `meta["res_pending"]` is write-only dead state (cosmetic).
- After a crash, a settlement can be settled-in-state yet missing from the settlements
  ledger — reconcile, don't naive-sum (annex §6d).

## 5. LANDMINES

- **SDK:** the VPS exec venv contains BOTH `py_clob_client` (0.34.6, ARCHIVED — rejected
  by the CLOB since the Apr-28 V2 migration) and `py_clob_client_v2` (1.1.0, works).
  **Import only `py_clob_client_v2`.** An `import py_clob_client` is always a bug.
- **Live mode refuses to start on partial config** — `MAKER_SUBMIT_MODE=live` +
  `MAKER_PK` + `MAKER_LIVE_ACK=I-UNDERSTAND-REAL-MONEY` are all required together. That
  is deliberate; do not "fix" it.
- **Trading must run from the VPS.** Residential IPs are geo-403 for order submission
  (reads are fine).
- **Gamma:** the `?id=` query param EXCLUDES closed markets — use the path form
  `/markets/<id>` for anything resolution-related. This silently broke settlements once.
- **The day-loss floor is tight by design** ($75 vs the paper footprint ≈ 7.5% trigger).
  It tripped three times on cup-final day; each was correct behavior, not a bug. Size it
  to real capital before a pilot — that is an operator kill-number decision.
- Do NOT touch the Kalshi units (separate venue, sibling session) or other bots' arms.

## 6. NEXT ACTIONS, IN ORDER

1. Read `docs/MAKER_MASTER_PLAN.md` §0 (NUMBERS RULE) → this file → the decision-package
   anchor → annex §6b/§6c/§6d.
2. Health-check the paper arm (heartbeat counters all zero? halted? respend draining?).
3. **Build on-chain reconciliation** (§4 item 1) — the highest-value work that needs no
   operator input and no waiting.
4. When the window clears ≥3 post-cliff clean days: re-run canon + the v5 cross-check;
   if the ranking holds, set `MAKER_GATE_POLICY` and restart.
5. Ask the operator for wallet status; when provisioned, run
   `scripts/maker_preflight.py --stage sanity` → `scoring` → `fill` → `receipts`.
6. Only then: assemble the real-capital go/no-go for the operator's decision.

## 7. STANDING DISCIPLINE (earned the hard way — keep it)

Ship nothing to live state without: tests + an INDEPENDENT adversarial review +
first-output cross-check against a separate source + a live smoke test where possible.
**Assume one more bug exists** — every round of this session found one, including bugs
introduced by the previous round's fixes. When a fix touches money math, prove
equivalence with a differential test rather than reasoning about it.

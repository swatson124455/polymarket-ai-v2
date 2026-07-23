# KALSHI FUNDING-GATE BUILD — SHIP-READINESS + HANDOFF (2026-07-23)

**Verdict: GREEN / READY TO DEPLOY (flag OFF).** No CRITICAL/HIGH survived. All four adversarial
lenses returned `refuted: false, severity: NONE`. The change is a provable no-op with the flag off;
turning the flag ON is a separate, operator-gated step that still fails safe.

- **Commit:** `6bc7d0e0818458eec2c4cf04c6882acad52b920a` — branch `claude/maker-kalshi-live`
- **Status:** NOT DEPLOYED, NOT PUSHED. Worktree edit only; the running VPS bot is unaffected
  (deploy is a separate per-file md5-gated step nobody takes here).
- **Files with CODE changes:** `kalshi_live/maker_kalshi_quoter.py` (gate) and new
  `kalshi_live/test_funding_gate.py` (pins). ⚠ The task's `git add -A` also swept ~133 pre-existing
  untracked probe artifacts into the same commit; they are inert data/scripts, not part of this fix.
  To reduce to a clean two-file commit if desired:
  `git reset --soft HEAD~1 && git restore --staged . && git add kalshi_live/maker_kalshi_quoter.py kalshi_live/test_funding_gate.py && git commit -m "<same msg>"`.

---

## 1. WHAT CHANGED

### The one behavioral change (in plain English)

The accumulating-buy capital gate used to count **money the bot had already spent**. When inventory
was bought, its cost basis (`held_cost`) was cash that had already left `balance`. The old gate added
that already-spent `held_cost` back into `committed` and tested it against `MAX_TOTAL_CAPITAL`:

```
committed = sum(surviving resting notional) + held_cost      # <- held_cost double-counts spent cash
if not reducing and committed + cost > MAX_TOTAL_CAPITAL: skip
```

So as soon as the bot held a normal amount of inventory, `committed` pinned at the cap and **new buys
froze while real free cash sat idle**. That is the treadmill the operator kept escaping by raising the
cap (85 → 100 → 150 → 250 in one day).

Behind a new flag `KALSHI_FUNDING_GATE` (`_envi`, **default 0 = OFF**), flag ON:

- **stops counting `held_cost`** in the accumulating gate (that cash is already gone), and
- instead caps the **resting BUY book** at `min(free_cash, MAX_TOTAL_CAPITAL)`, where
  `free_cash = balance_dollars`, **reused from the existing equity read — no second fetch**:

```
funding_committed = sum(surviving resting BUY notional)       # NO held_cost term
funding_gate_on   = bool(FUNDING_GATE) and free_cash is not None
if not reducing:
    if funding_gate_on:
        if funding_committed + cost > min(free_cash, MAX_TOTAL_CAPITAL): skip
    elif committed + cost > MAX_TOTAL_CAPITAL:                 # legacy branch, unchanged
        skip
```

**Why it is safe regardless of gross/net balance (the hard ceiling).** Every resting order the bot
places is a `post_only` bid (a BUY). Gating the resting BUY book against **free cash** means the bot
can never rest more buy notional than free cash could fund if it all filled → no overdraw. If the
venue's `balance` turns out to be NET-of-reservations rather than GROSS, `funding_committed` re-counts
the survivor notional on top of an already-net `free_cash`, so the gate becomes *strictly stricter* —
worst case a harmless re-freeze (revert the flag), never a blowup. `MAX_TOTAL_CAPITAL` stays a real
backstop via `min()`; whichever is smaller binds.

- **Reducing / unwind creates stay EXEMPT** exactly as today (`if not reducing:` wraps the whole gate;
  `funding_committed` is only incremented on successful *accumulating* creates).
- **No naked-coverage netting term** was introduced (per the directive — the prior design's
  `naked_buy_draw` had a coverage double-count bug). The gate is gross surviving BUY notional vs
  free_cash only: simpler and strictly safe.
- The loss meter / equity (`balance + held_cost`) is untouched — that is a different, correct quantity.

### Proof that flag-OFF is a no-op

With `KALSHI_FUNDING_GATE` unset or `0`, `FUNDING_GATE = _envi(..., 0) = 0`, so
`funding_gate_on = bool(0) and (...) = False` on every path. Consequently:

1. **Gate:** control takes the `elif committed + cost > MAX_TOTAL_CAPITAL` branch, and
   `committed += held_cost` still runs unconditionally — **byte-for-byte the legacy gross+held gate**.
2. **Balance read:** the one refactored line splits
   `_equity = float(get_balance().get("balance_dollars") or 0) + held_cost` into
   `free_cash = float(...); _equity = free_cash + held_cost`. Numerically identical, **one**
   `get_balance()` call, identical raise semantics (a raise leaves `free_cash = None`, `_equity = None`).
3. **Dead locals:** `free_cash` and `funding_committed = committed` are captured but never read when off.
4. **Plan telemetry:** `funding_gate` / `funding_committed_usd` / `free_cash_usd` are emitted **only
   under `funding_gate_on`**, so a flag-off plan row is byte-identical to legacy output.

Empirically: the 159 pre-existing tests all run with the flag unset and pass identically; the new
flag-off no-op pin (`test_flag_off_is_noop_legacy_gate_still_blocks`) also passes on the **old** code
(git show HEAD~1), confirming flag-off == legacy.

### cap_desired — left unchanged (deliberate, as asked)

`cap_desired` (`maker_kalshi_quoter.py:~683`) sums only `_mkt_capital(qs)` (freshly-desired book
notional) against `MAX_TOTAL_CAPITAL` and **never adds `held_cost`** — it has no spent-cash
double-count to fix. It is a coarse whole-market pre-trim that keeps unwind markets unconditionally;
the real binding per-create funding check is the committed gate this fix corrects. Touching it would
be a second behavioral change against the "one change behind one flag" directive with no treadmill
benefit. Under flag-on the desired book stays coarsely bounded by `MAX_TOTAL_CAPITAL` (consistent
with the `min(free_cash, MAX_TOTAL_CAPITAL)` backstop), then the funding gate does the real work.

---

## 2. VERIFIER RESULTS

**No CRITICAL/HIGH. Nothing survived. Verdict: READY.**

Four independent adversarial lenses, each run against commit `6bc7d0e` and — for discrimination —
against an isolated copy of `HEAD~1` with only an inert `FUNDING_GATE=0` attribute shimmed in so the
tests resolve without changing the old gate logic:

| Lens | Refuted? | Severity | Result |
|------|----------|----------|--------|
| flag-off-is-a-noop | no | NONE | Flag-off byte-identical to legacy on gate, balance read, and plan dict; default resolves to 0; free_cash never even read when off. |
| flag-on-never-overdraws | no | NONE | Resting accumulating-BUY book bounded by `min(free_cash, MAX_TOTAL_CAPITAL) <= free_cash`; all resting orders are bids so `committed == surviving BUY notional`; increment only on create success. |
| fails-safe-if-net | no | NONE | Under NET balance the gate is *strictly more conservative* (re-counts survivors) → worst case re-freeze, never over-commit. Malformed/zero balance freezes (fail-safe). |
| tests-are-real-not-asserting-the-bug | no | NONE | Pins #2/#3/reducing-exempt FAIL on old logic and PASS on the fix (genuine behavior-change pins); pins #1/#4 pass on both by design (flag-off / fail-closed equivalence pins). |

**Discrimination proof (measured, not asserted).** With the old gate logic + `FUNDING_GATE=0` shim:
- `test_flag_on_admits_when_held_inflated_but_free_cash_ample` — **FAILS on old** (old blocks the
  create because held_cost pushes past the cap), **PASSES on fix** (admits it). Pins the treadmill fix.
- `test_hard_ceiling_refuses_beyond_free_cash_even_with_huge_cap` — **FAILS on old** (old admits a
  buy exceeding free cash under a huge cap), **PASSES on fix** (refuses). Pins the hard ceiling.
- `test_reducing_create_exempt_from_funding_gate` — **FAILS on old**, **PASSES on fix**.
- `test_flag_off_is_noop_legacy_gate_still_blocks` and
  `test_balance_read_failure_fails_closed_to_legacy_gate` — **PASS on both** by design (they assert
  flag-off / fail-closed == legacy equivalence; not discriminators, but they pin the guarantee).

**One PRE-EXISTING, directive-sanctioned residual (NOT a refutation, known gap).** Reducing/unwind
creates are exempt from the gate and do not increment `funding_committed`, yet an unwind is itself a
buy of the opposite side that draws cash at fill under GROSS. Total draw = accumulating book
(`<= free_cash`) + reducing book (uncounted) could transiently exceed free_cash before the offsetting
pair settles. This is **identical to the legacy gate** (old code also exempted reducing), is
explicitly scoped out by the directive ("KEEP reducing/unwind creates EXEMPT; do NOT add a
naked-coverage netting term"), and is separately bounded by `HELD_MAX_USD` / the naked-risk breaker /
the overshoot cap. It does not make the fix less safe than what it replaced.

---

## 3. SHIP CHECKLIST (green)

**pytest (full Kalshi suite, this build):**
```
cd kalshi_live && python -m pytest test_live_hardening.py test_funding_gate.py test_attribution.py test_settlement_pnl.py test_studies.py -q
=> 164 passed, 2 xfailed in ~3.1s   (python 3.13.3 / pytest)
```
The 2 xfails are pre-existing and unrelated. The 5 new funding-gate pins all pass.

**md5 (per-file, for the md5-gated deploy):**
```
git show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum
=> 22c1f97b26c2eddceb3b3f882f111967

git show HEAD:kalshi_live/test_funding_gate.py | md5sum
=> e352d2fcc6b7ebf311cf00456da048a3

# for reference, parent (pre-fix) quoter:
git show HEAD~1:kalshi_live/maker_kalshi_quoter.py | md5sum
=> 9a24f6052cee32a7908fd2f993523efd
```

**Deploy step (per-file md5-gated — operator only, NOT taken in this lane):**
The Kalshi deploy verifies the destination file's md5 matches the intended source md5 before the swap.
Ship `maker_kalshi_quoter.py` at md5 `22c1f97b26c2eddceb3b3f882f111967`. Do NOT ship the ~133 swept
probe artifacts — deploy only the quoter (the test file does not run on the VPS). **`.env` change: none
required to deploy** — the flag defaults to 0 (legacy behavior). Deploy = code only.

**Rollback (two independent paths):**
1. **Flag revert (no redeploy):** `KALSHI_FUNDING_GATE` unset (or `=0`) is a provable no-op even if
   the code is already deployed. Restart the service to pick up the env change. This is the fast path.
2. **Commit revert:** `git revert 6bc7d0e` (or redeploy the parent quoter at md5
   `9a24f6052cee32a7908fd2f993523efd`).

---

## 4. THE ONE THING STILL UNCONFIRMED

**Flipping `KALSHI_FUNDING_GATE=1` assumes `balance` is GROSS** (Kalshi reserves cash at FILL, not at
placement). Best committed evidence says it is — n~4 place/cancel showed a balance delta of 0
(`KALSHI_RUNNING_TAB.md` 07-20; `kalshi_attribution_ledger.py:436`). But per the rootfix doc
(`KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md` §1/§6), the prior design's reconciliation "proof"
was **circular** (`account_value` is never read from the venue; `/portfolio/balance` exposes only
`{balance, portfolio_value}`), so **do not rely on a reconciliation identity as validation.**

The clean confirmation is a **place → observe balance → cancel** test order (a live write,
operator-gated). Until that runs:

- **The code can DEPLOY safely right now** — flag OFF, provable no-op, zero live behavior change.
- **Turning the flag ON is the step that wants that confirmation.** And even without it, flag-on is
  built to fail safe: the free-cash hard ceiling means the worst case if `balance` is actually NET is
  a harmless **re-freeze** (revert the flag), never an overdraw. The idle-cash direction, not the
  overdraw direction.
- The fill-time collateral-release behavior for offsetting legs is UNMEASURED (n=1) — no logic in
  this build depends on it.

---

## 5. TIER-3 LIVE-MONEY CHANGE — PROCESS GATE

This is a **Tier-3 code change to a live-money trading path.** Before ANY deploy:

1. **Operator sign-off** — explicit, naming this commit.
2. **Adversarial re-review** — the verdict-bearing gate code re-reviewed at deploy time (a guard
   cannot be reviewed apart from its caller).
3. **Per-file md5-gated deploy** — ship `maker_kalshi_quoter.py` at md5
   `22c1f97b26c2eddceb3b3f882f111967`; the deploy verifies the md5 before the atomic swap.
4. Deploy with the flag **OFF** (default) first; observe a clean cycle; only then consider the live
   place→observe→cancel confirmation and the operator-gated flip to `KALSHI_FUNDING_GATE=1`.

Kalshi venue only. No MB/WB/EB/SB or shared-module code was touched.

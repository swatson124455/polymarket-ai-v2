# KALSHI STAND-DOWN GUARD — BUILD HANDOFF (2026-07-24)

**Lane:** Kalshi Maker. **Branch:** `claude/maker-kalshi-live`. **Commit:** `62f39e924078c6945be821a1980c287da83a6db7`.
**Status:** BUILT, COMMITTED, GREEN. **NOT DEPLOYED.** Tier-3 change — operator sign-off + md5-gated deploy required before live.
**Files touched:** `kalshi_live/maker_kalshi_quoter.py` (+82 / -0), `kalshi_live/test_standdown.py` (+196, new). 278 insertions, 0 deletions.

---

## 1. WHAT CHANGED (plain English) + FLAG-OFF NO-OP PROOF

### The problem it fixes
The bot had no "should I even be playing right now?" brain. It farmed LIP rewards mechanically. On a day when temperature markets (~91% of reward income) go DARK, it was left churning thin gas — and each gas fill is a small adverse-selection loss (fingerprint ~ **-$0.011/contract** on gas, worse on temp). With no reward to cover those losses and no logic to fold, it played a losing hand and bled (measured **~-$9 on 2026-07-23**, mostly one ATM gas strike getting run over by one-way flow). Neither the VELOCITY breaker (held-$ growth) nor the $40 DAILY-LOSS halt catches a slow adverse grind while rewards are absent — that is the gap this build closes.

### The mechanism chosen (design decision)
**Per-market reward-density stand-down** (a principled blend of candidates A + B from the design brief). Behind a new flag `KALSHI_STANDDOWN`:

- A pure helper `_standdown_market(m, void)` reads the market's already-in-cycle R1-normalized LIP reward density `usd_day` (no new API read), applies an R3 one-sidedness discount (`STANDDOWN_VOID_MULT = 0.5`) on void/one-sided books, and returns `(stood_down, eff_usd_day)` where `stood_down = eff < STANDDOWN_MIN_USD_DAY` (default floor **$20/day**). Missing `usd_day` → 0 → stands down (conservative).
- When a market is stood down, the bot **opens LESS** there:
  - **JOIN branch:** the accumulating YES/NO counts are floored to `MIN_QUOTE_CT` (`y_cnt=min(y_cnt,MIN_QUOTE_CT)`, `n_cnt=min(...)`), price left at reference — smaller fills = proportionally smaller adverse loss.
  - **ACTIVATE branch:** a stood-down thin void book returns `[]` (skips activate) — but only when FLAT, because held-inventory unwind is handled *above* this point.
- **Exits are never touched:** the JOIN floor caps only the *accumulating* base size; the unwind block re-sizes the reducing side from `|inv|` *afterward* (a `=` assignment, not a `min()`), so de-risk always rests at full size. The void-branch skip is downstream of the inventory-unwind early return, so it fires only when flat.
- **Telemetry** (emitted only when the flag is on): `standdown`, `standdown_floor_usd_day`, `standdown_markets`, `standdown_min_rho_usd_day` — the reward-vs-floor numbers driving the decision, observable in `plans-*.jsonl`.

### Insertion sites (commit 62f39e9)
| Site | Location (approx) | What it does |
|---|---|---|
| Config | quoter config block | `STANDDOWN=_envi("KALSHI_STANDDOWN",0)`, `STANDDOWN_MIN_USD_DAY=20.0`, `STANDDOWN_VOID_MULT=0.5` |
| Helper | `_standdown_market(m, void)` | pure, no API read; `eff = usd_day*(0.5 if void else 1.0)`; stands down iff `eff < floor` |
| ACTIVATE branch | after flat/event guards | stood-down thin void book → `return []` (flat only; held inventory unwinds above) |
| JOIN branch | after base `_capped_join`, before ramp/throttle/unwind | stood down → floor `y_cnt`/`n_cnt` to `MIN_QUOTE_CT`; unwind block later overwrites the reducing side from `|inv|` |
| Telemetry | plan-emit, inside `if STANDDOWN:` | `standdown`, `standdown_floor_usd_day`, `standdown_markets`, `standdown_min_rho_usd_day` |

### FLAG-OFF NO-OP PROOF
- `git diff --numstat HEAD~1 HEAD` = **`82  0`** on the quoter — **82 insertions, 0 deletions. No existing line altered.**
- Every new statement lives under `if STANDDOWN:`. `STANDDOWN = _envi("KALSHI_STANDDOWN", 0)` is falsy when unset or `"0"`.
- `_standdown_market` is referenced only inside those guards → never called with the flag off → `usd_day` is never read.
- New module constants are new names with no import-time side effects.
- Telemetry keys emit only inside `if STANDDOWN:` → plan rows are byte-for-byte legacy with the flag off.
- **Pinned by test T1** at both the `desired_quotes` level and the full-cycle level: with the flag off, a thin regime (`usd_day=5`) produces quotes/plan identical to a no-`usd_day` legacy row, and the cycle emits no `standdown_*` key.

**Deploys with zero live change until the flag is flipped.**

---

## 2. VERIFIER VERDICTS

**Four independent adversarial verifiers. Zero refutations. No CRITICAL / HIGH / MEDIUM findings. No never-blocks-exits failure.** All four ran against commit `62f39e9` and the full 177-test suite.

| # | Primary lens | Refuted? | Severity |
|---|---|---|---|
| 1 | flag-off-is-a-noop | **false** | NONE |
| 2 | never-blocks-exits | **false** | NONE |
| 3 | does-not-forfeit-plus-ev-gas | **false** | NONE |
| 4 | actually-cuts-the-dead-day-bleed | **false** | NONE |

### Verifier 1 — flag-off-is-a-noop (NONE)
Clean guard-gated no-op with the flag off; none of the four failure modes reproduce. `git diff --numstat` = 82/0, no existing line altered. Every new statement under `if STANDDOWN:`; `STANDDOWN` falsy when unset. Full suite 177 passed / 2 xfailed. Void-branch `return []` is downstream of the inventory-unwind early return (fires only when flat); JOIN-branch `min()` on the reducing side is overwritten by the unwind block that re-sizes from `|inv|`. Live gas `usd_day~150 >> floor 20` → full quotes. Residual is **calibration, not correctness**: cutting the exact 07-23 gas-strike bleed depends on that strike's `usd_day` being below the floor — tunable before the flag flips, inert until then.

### Verifier 2 — never-blocks-exits (NONE) — the deadliest lens
CONFIRMED SAFE. Traced every reducing/unwind path. `wind_down` has no stand-down code. Void-held rests the reducing side and returns BEFORE the stand-down block, so the `return []` is reachable only when FLAT (strands nothing). JOIN-held: the stand-down floor `min(.,MIN_QUOTE_CT)` is **fully overwritten** by the unwind block which reassigns the reducing side via `_unwind_size(|inv|)` (a `=`, not a `min()`, floored at `max(1,·)`); no early return exists between the floor and the unwind re-size. Deadliest scrutinized case: `inv=+40` long-yes on a thin market, flag ON — both sides floored to 2, then the reducing NO side reassigned to 40, overwriting the floor. Only the *accumulating* side stays floored (risk being ADDED, not an exit). Reducing quote emits at full `|inv|` (T3: `no.count==40`). **Inventory cannot be trapped.**

### Verifier 3 — does-not-forfeit-plus-ev-gas (NONE)
`_standdown_market` stands down iff `eff < $20/day`. Live gas `usd_day≈$150/day` (operator ground truth, same unit as `select_footprint` `(period_reward/10000)/days`) → join `eff=$150`, void `eff=$75`, both `>= $20` → NOT stood down → gas keeps full size (7.5x join / 3.75x void margin). When `_sd` is False the STANDDOWN block is a strict no-op. T4 pins `usd_day=150` → 100/100 under flag ON, identical to OFF. **Minor observation (not a refutation):** a marginally-live one-sided gas book at `usd_day~$20-40` gives void `eff~$10-20` and would be activate-skipped — but that's only the riskier one-sided book (two-sided gas protected down to `usd_day>=$20`), an activate-skip never an exit-block, tunable via `KALSHI_STANDDOWN_VOID_MULT`, and inert until the flag flips.

### Verifier 4 — actually-cuts-the-dead-day-bleed (NONE)
Empirical dual-module comparison vs pre-commit HEAD~1: **420 flag-off fixtures = 0 divergences** (no-op); **192 flag-ON held-inventory fixtures = 0 exits dropped, 0 down-sized** (reducing unwind always present at ≥ legacy size because the unwind block re-sizes after the cap, and void held-inv returns before the gate). Flag-ON `usd_day=150`/`20` → full 100 ct (gas kept); `usd_day=5` → 2 ct + thin activate skipped (bleed cut: ~98% fill-exposure cut, adverse `$1.10→$0.022` per fill at fingerprint -0.011/ct). **Non-blocking operator note (flag ships OFF):** the $20/day floor is a documented regime-separator tunable, not fingerprint-derived — a strike with reward above the floor yet still adverse would not trip; observable via `standdown_min_rho_usd_day`, calibrate against the ledger before flipping.

**Common thread across all four:** the only residual is **calibration of the $20/day floor**, not correctness. It is telemetry-observable and meant to be tuned in a shadow/observe window before the flag is trusted live.

---

## 3. GREEN — TEST COUNTS, MD5, DEPLOY, ROLLBACK

### pytest
```
test_standdown.py ........            8 passed in 0.47s
Full suite (test_*.py):              177 passed, 2 xfailed in 3.17s
```
The 2 xfailed are **pre-existing and unrelated** — strict xfails at `test_live_hardening.py:1521` / `:1530` (`FINDING 1` / `FINDING 2`). `test_standdown.py` has zero xfails.

`test_standdown.py` — 8 tests over the 4 required pins:
- **T1 flag-off no-op** — thin `usd_day=5` == no-`usd_day` legacy row; full cycle emits no `standdown_*` key (both `desired_quotes` and full-cycle levels).
- **T2 fix pin** — thin regime; OFF opens 100/100, ON opens 2/2 at reference (fails on legacy, passes on fix). Cycle variant shows `est_capital_usd` slashed.
- **T3 never-blocks-exits** — long +40 → NO unwind still rests at 40, identical to flag-off; plus a void-activate held-exit variant.
- **T4 does-not-forfeit-gas** — `usd_day=150` → full 100/100 both flag states.
- plus an R3/floor helper unit test.

### MD5 (md5-gated deploy)
```
git -C <worktree> show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum
  → ed5af253cd0691de642928ac3d48a26d

git -C <worktree> show HEAD:kalshi_live/test_standdown.py | md5sum
  → a5989c01e8c7c9ef722374a6e5e10226
```

### Per-file md5-gated deploy step (Tier-3 — run ONLY after operator sign-off)
```bash
# 1. Confirm the exact reviewed bytes before shipping (MUST match ed5af25… or STOP):
git -C /opt/pa2-maker-kalshi show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum
# expect: ed5af253cd0691de642928ac3d48a26d
# 2. Fast-forward the kalshi live checkout to 62f39e9 (venue-scoped; NOT deploy.sh, NOT master).
# 3. Flag stays OFF at deploy: KALSHI_STANDDOWN unset/0 → byte-for-byte legacy (proven §1).
# 4. Restart only the Kalshi maker process per its own runbook. No shared-env change, no other service.
```
Deploy ships the code with the flag OFF (no live change). The flag is flipped separately in a shadow/observe window (§4).

### Rollback
- **Instant, zero-risk:** leave / set `KALSHI_STANDDOWN` unset or `0` → provable no-op (§1). No restart-race, no code change needed.
- **Full code revert:** `git revert 62f39e924078c6945be821a1980c287da83a6db7` (pure-additive commit; clean revert).

---

## 4. EXPECTED LIVE EFFECT WHEN FLIPPED ON (`KALSHI_STANDDOWN=1`)

- **Temp-dark / adverse day (the 07-23 scenario):** markets whose R1-normalized `usd_day` falls below the $20/day floor open at `MIN_QUOTE_CT` (thin JOIN books) or skip activate when flat (thin void books). Per-fill adverse exposure shrinks ~50x. **A dead reward day costs ~$0 instead of ~-$9.**
- **De-risk always proceeds:** any held inventory unwinds at full `|inv|` size regardless of stand-down — exits are never blocked or down-sized (verifier 2, T3).
- **Normal (reward-present) day:** live gas at `usd_day≈$150/day >> $20` is NOT stood down → keeps quoting full size. The only +EV lane is preserved (verifier 3, T4).
- **Composes with, does not duplicate, existing guards:** unqualifiable gate, void/selection gates, velocity + HELD_MAX breakers, and the $40 DAILY_LOSS_HALT are unchanged. Pivot-select (if merged) fills the footprint with earners; stand-down then caps/sizes the total when even the earners are too thin.
- **Observable from `plans-*.jsonl`:** `standdown` (state), `standdown_floor_usd_day`, `standdown_markets`, `standdown_min_rho_usd_day` (the reward-vs-floor numbers).

**Not verified / operator action:** the default floor **$20/day is a regime-separator starting point, not a ledger-fitted EV constant.** Before trusting it live, flip `KALSHI_STANDDOWN=1` in a shadow/observe window and confirm from the plan telemetry that live gas on a NORMAL day is not being stood down (`standdown_markets` should exclude live gas; `standdown_min_rho_usd_day` should sit well above what live gas reports). Tune `KALSHI_STANDDOWN_MIN_USD_DAY` / `KALSHI_STANDDOWN_VOID_MULT` against the ledger before relying on it.

---

## 5. TIER-3 CHANGE — SIGN-OFF GATE

This is a `.py` edit to a live-venue file → **Tier-3**. Required before any live effect:
1. **Operator sign-off** on this build (code reviewed, verdicts read).
2. **md5-gated deploy:** confirm `maker_kalshi_quoter.py` md5 == `ed5af253cd0691de642928ac3d48a26d` on the target checkout BEFORE swap; abort on mismatch.
3. **Ships with flag OFF** — byte-for-byte legacy at deploy (§1), zero live change.
4. **Flip `KALSHI_STANDDOWN=1` only in a shadow/observe window**, verify plan telemetry (§4), calibrate the floor, THEN trust it live.

**Constraints honored:** edited only `maker_kalshi_quoter.py` + new `test_standdown.py`; no deploy, no `live.env`, no ssh-write, no systemctl; every signature preserved; exits/reducing + funding gate + loss meter unchanged; one behavioral change behind one flag; Kalshi venue only; no MB/WB/EB/SB or shared-module touch.

# KALSHI PIVOT-SELECT BUILD — 2026-07-24

**Branch:** `claude/maker-kalshi-live`
**Commit:** `4c731f63555040d1ea946dfa2661b1268ade8b1b`
**Status:** COMMITTED, **NOT DEPLOYED**. Ships default-OFF (`KALSHI_PIVOT_SELECT=0`), a provable byte-for-byte no-op.
**Scope:** `kalshi_live/maker_kalshi_quoter.py` (selection/fill only) + new `kalshi_live/test_pivot_select.py`. Kalshi venue only. No gate/breaker/funding-gate/loss-meter/signature changes. No deploy, no live.env, no ssh/systemctl.
**Tier:** **Tier-3 (code change).** Operator sign-off + md5-gated deploy required before any live flip.

---

## 1. What changed (plain English) + flag-off no-op proof

### The problem (measured live 2026-07-24 ~12:10Z)
The bot was quoting only ~9 of a `FOOTPRINT_TOP=40` book and was in **0 of 17** eligible gas-daily strikes — our best market. Root cause was **SELECTION, not the gates**:

1. `select_footprint` sorted eligible programs by `usd_day` desc then **round-robined 1 market/series/round** across all active series. With 8 active series that handed EACH series exactly 5 slots — gas-daily (`usd_day` 150.2) got the same 5 slots as KXH100MON (`usd_day` 2.6). Egalitarian dilution of the best market.
2. Within a series, rows tie-broke by **ticker ascending**, so gas-daily's 5 slots went to the 5 LOWEST strikes (4.065–4.085) — deep-ITM, `best_yes ~0.98` — which then **failed the price-bound gate** at quote-gen. The near-money strikes (4.100–4.135, symmetric, in-bounds) that WOULD earn were never selected (6th–16th by ticker, beyond the 5-slot round-robin).
3. Result: gas contributed ~1 quotable strike; ~31 footprint markets gated out at quote-gen; the bot simply quoted FEWER instead of backfilling.

Verified live gate outcomes on the 17 gas strikes: 4.065–4.080 price-bound-DROP; **4.085–4.135 WOULD QUOTE (11 strikes, symmetric + in-bounds)**; 4.140–4.145 price-bound-DROP. 11/17 quotable, but only ~5 low ones ever selected and 4 of those gated.

### The fix — operator directive: "We NEVER gate any markets. If the rewards aren't there we PIVOT to another trade. Hardcode this."
Behind new flag **`KALSHI_PIVOT_SELECT`** (default `0` = OFF):
- **Over-select:** build an ordered eligible candidate pool larger than `FOOTPRINT_TOP`, bounded by `pool_cap = min(PIVOT_POOL_MULT*FOOTPRINT_TOP, len(rows), READ_BUDGET_PER_CYCLE - PIVOT_READ_RESERVE)`.
- **Density weighting:** the top series (gas) is no longer capped to an egalitarian 5. A `PIVOT_COVERAGE` floor gives each active series minimal coverage, then the remainder fills by pure density (gas-first); `PER_SERIES_CAP` still binds.
- **Within-series near-money ordering:** strikes ordered by proximity to the series median strike (via existing `_strike_of`), so the balanced/tradeable near-money strikes are picked ahead of extreme-ticker deep-ITM/OTM strikes. Price-free — no extra book read to order.
- **Fill by qualification (PIVOT):** the quote loop runs the SAME `desired_quotes` per candidate; if it returns `[]` (gated) the market is SKIPPED and the next candidate pulled; the loop stops once `FOOTPRINT_TOP` markets are actually QUOTED or the pool is exhausted.
- **Gates untouched.** A non-earning book (price-bound / crossed / unqualifiable / lopsided) is still rejected by `desired_quotes` and never quoted — pivot quotes a DIFFERENT earner, never the bad one.

New flags (`_envi`, `:92`): `PIVOT_SELECT` (default `0`), `PIVOT_POOL_MULT` (`2`), `PIVOT_COVERAGE` (`1`), `PIVOT_READ_RESERVE` (`30`).

### Flag-off no-op proof
- `select_footprint` fill (`:353–369`): the legacy round-robin is moved **byte-for-byte** inside `if not PIVOT_SELECT:` and **returns before any pivot code**. The pivot branch's in-place `by_series` / `rs.sort` mutations live in the never-executed `else` — off, `by_series` is never mutated.
- Quote loop (`:1099`, `:1110`, `:1176`): the three additions are `if PIVOT_SELECT`-guarded and short-circuit `False` when off. `consumed=[]` is the only unconditional add and is never assigned back to `footprint`, so `footprint` keeps object identity. No collision (`consumed` appears only in the pivot block).
- Telemetry (`:1454`/`:1459`): `pivot_*` plan keys emit only under `if PIVOT_SELECT:`.
- `_envi` parses unset / `"0"` / malformed → `0` = OFF.

**Empirical corroboration:** HEAD~1 (legacy) and HEAD imported side-by-side; with `PIVOT_SELECT=0`, `select_footprint` output IDENTICAL over 60 randomized program sets (series/rewards/strikes/lives varied, `FOOTPRINT_TOP` 1–40, `PER_SERIES_CAP` 1–12) = 60/60; `run_once` plan dict + created/cancelled/crosses byte-identical on a gated-footprint run AND a held-inventory run (strand-unwind + ladder + breaker). Flag-ON diverged 59/60 (flag is live, not dead). All pre-existing legacy select tests pass unchanged on the default branch.

---

## 2. Verifier verdicts

**No CRITICAL / HIGH / MEDIUM findings. All four lenses: `refuted=false`, `severity=NONE`.**

| Lens | Refuted | Severity | Verdict |
|------|---------|----------|---------|
| flag-off-is-a-noop | false | NONE | Byte-for-byte no-op verified: 60/60 identical selection off, plan/created/cancelled/crosses identical on gated + held-inventory `run_once`; flag-ON diverges 59/60. |
| never-quotes-a-non-earner | false | NONE | Gate body (`desired_quotes`, price-bound/crossed/unqualifiable/lopsided) NOT in the diff; creates flow only from `desired` via `if q:`; a gated market pivots past, never quoted. T3 pins bad books absent from `created`. |
| no-thrash-or-unbounded-reads | false | NONE | Reads hard-bounded (`pool_cap` + `READ_BUDGET_PER_CYCLE=200` RuntimeError ceiling); selection order is price-free so stable cycle-to-cycle; no while-True. T4 pins bounded pool + terminates. |
| gas-actually-fills-now | false | NONE | Drove committed `select_footprint` on the 17-strike gas fixture: legacy picks 5 lowest (1 quotable), pivot picks 10 near-money 4.085–4.130 (10 quotable, 5 earners before the `FOOTPRINT_TOP` break). |

**Accepted non-defect notes (operator-accepted, bounded):**
- **Boundary churn (flag-ON only):** a market oscillating across a gate price-boundary can swap its footprint slot cycle-to-cycle → bounded cancel/create churn legacy wouldn't emit. Bounded by `cap_desired`/`bound_creates`; the operator explicitly accepted this ("if the rewards aren't there we PIVOT"). Never a non-earner quote.
- **Strand-read starvation (flag-ON, edge):** under simultaneous heavy gating AND many held positions the pivot loop can consume up to `pool_cap` reads, leaving fewer than `PIVOT_READ_RESERVE=30` for strand/ladder reads, which then hit the RuntimeError ceiling and `break` gracefully — the same failure mode legacy already has under budget pressure. Does not occur in the current live regime (eligible universe ~9–17 ≪ `FOOTPRINT_TOP`).
- **T4 read-COUNT assertion is vacuous (LOW test-quality note, not a code defect):** T4 stubs `public_get` so `_reads` never increments; `reads < 200` / `reads <= pool_cap+6` trivially pass. The read bound is instead enforced structurally by `pool_cap` + the RuntimeError ceiling, and T4's non-vacuous pool assertions (`pool_cap==10`, `len(picked)<=pool_cap`, `gated_out==pivot_pool`) do pin the pool bound.

---

## 3. Green build — counts, md5s, deploy, rollback

### Pytest
```
cd .../kalshi-wt/kalshi_live && python -m pytest test_*.py -q
=> 169 passed, 2 xfailed in 3.19s
```
164 pre-existing + 5 new pivot-select tests (T1a legacy-select byte equality, T1b no `pivot_*` telemetry off, T2 gas-book fix pin on>off, T3 non-earner never quoted, T4 bounded pool/reads). The 2 xfailed are pre-existing. New-file-only: `5 passed in 0.52s`.

### md5 (git blob at HEAD `4c731f6`)
```
git -C .../kalshi-wt show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum
=> 1c68e130b0c80adefd6698314d257d15

git -C .../kalshi-wt show HEAD:kalshi_live/test_pivot_select.py | md5sum
=> 7542871bfbc38639d7aa04cf2104cd49
```

### Per-file md5-gated deploy step (operator, after sign-off)
Deploy is Tier-3 and operator-gated. Ship the exact reviewed blob only — verify the md5 on the VPS-side file BEFORE restart, abort on mismatch:
```bash
# on the VPS, after placing the release:
EXPECT=1c68e130b0c80adefd6698314d257d15
GOT=$(md5sum /opt/pa2-maker-live/kalshi_live/maker_kalshi_quoter.py | awk '{print $1}')
[ "$GOT" = "$EXPECT" ] || { echo "MD5 MISMATCH ($GOT != $EXPECT) — ABORT, do not restart"; exit 1; }
echo "md5 OK — safe to restart maker"
```
The flag stays OFF at deploy (default `0`). Deploying the code is a pure no-op; the live behavior change happens ONLY when the operator later sets `KALSHI_PIVOT_SELECT=1` in the maker env and restarts.

### Rollback
- **Fastest (no redeploy):** the flag default is a byte-for-byte no-op. Unset `KALSHI_PIVOT_SELECT` (or set `=0`) and restart maker → exact legacy behavior. No code revert needed.
- **Full code revert:** `git revert 4c731f63555040d1ea946dfa2661b1268ade8b1b` and redeploy.

---

## 4. Expected LIVE effect when the flag is flipped ON

On the current book (FOOTPRINT_TOP=40, gas cap/mkt 150/day, 8 active series):
- **Gas fills to the near-money strikes.** Median of the 17 gas strikes = 4.105; proximity ordering pulls `4.105, 4.100, 4.110, 4.095, 4.115, 4.090, 4.120, 4.085, 4.125, 4.130…` first — the first ~9 all sit inside the qualifying 4.085–4.135 band. Gas goes from **~1 quotable strike → up to ~9–10 near-money earners** (`PER_SERIES_CAP` binding), vs legacy's ~1.
- **Footprint backfills to a full slate of EARNERS.** Instead of quoting ~9 markets and dropping ~31 gated slots, the loop pivots past gated candidates and keeps pulling from the density-ordered pool until `FOOTPRINT_TOP` markets are actually QUOTED (or the eligible pool exhausts). Expected: footprint fills toward **~40 quoted earning markets** where the eligible universe supports it (today's eligible universe is smaller, so it fills to the earner count available and stops — no garbage quoted to reach a number).
- **Non-earners still never quoted.** Price-bound / crossed / unqualifiable / lopsided books remain gated; pivot substitutes a different earner.
- **Reads bounded:** `pool_cap = min(2*FOOTPRINT_TOP, len(rows), 200-30)`; loop stops at `FOOTPRINT_TOP` quoted; `READ_BUDGET_PER_CYCLE=200` RuntimeError is the hard ceiling.
- **Bounded cancel/create churn** may appear for markets straddling the qualification boundary — accepted tradeoff.

---

## 5. Tier-3 gate

This is a **Tier-3 code change** to a live-trading selection path. Before any live effect:
1. **Operator sign-off** on this build (`4c731f6`) and on flipping `KALSHI_PIVOT_SELECT=1`.
2. **md5-gated deploy** (§3) — verify `1c68e130b0c80adefd6698314d257d15` on the VPS file before restart; abort on mismatch.
3. Deploy ships **flag OFF** (no-op). The behavior change is a separate, explicit operator env flip + restart.
4. Rollback is a flag unset (no redeploy) or `git revert 4c731f6`.

Kalshi is KING on shared resources (RULE FIVE); the Maker/Kalshi session STOPS and asks before touching anything shared. This build touches only `kalshi_live/**` in the worktree and does not affect the running VPS bot.

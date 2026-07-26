# KALSHI NET-EV FLAGGING — BUILD HANDOFF (2026-07-24)

**Commit:** `f17c0c260b3faa2e925f84e76a67943a0f7dda02` on `claude/maker-kalshi-live` — **NOT DEPLOYED**
**Status:** green, flag-off inert, reproduces M8. Tier-3 change: needs operator sign-off + md5-gated deploy before live.
**Venue:** Kalshi only. No deploy / no live.env / no ssh-write / no systemctl performed.

---

## 1. WHAT CHANGED (plain English)

The old flagging asked the WRONG question. The `KALSHI_CAPTURE_GATE` (reward-only) and the
`KALSHI_STANDDOWN` (pool-only) both gate on *"can we capture some reward here"*. That is not enough:
**a market can capture reward and STILL lose money net** if the fill losses (adverse selection) exceed the
reward credits. TEMP is the live proof — it earned +$23.06 of reward credits over 07-21..22 and STILL lost
money, because the fills bled −$36.12. Reward-possible said "quote it"; the receipts said "this is bleeding".

The new gate asks the RIGHT question: **will this market make US money, NET?**

> net = reward credits − fill/adverse-selection P&L − fees

and it answers **from actual receipts, not from the over-predicting R4 model**. Per FAMILY (gas, temp, …)
it uses the calibrated realized net (this automates the M8/M13 hand-analysis). A family that is calibrated
**net-negative** is POOR FOR US → **skip when FLAT, reduce-only when HOLDING**. A **net-positive** family
(gas) is kept full-size. A **NEW / UNPROVEN** family with no receipt history falls back to the R4 model with
the §M7 3× haircut minus a fill fingerprint — open only if that conservative number is positive, else
unproven-skip. This gate **supersedes** the reward-only capture gate and the pool-only stand-down (those two
become redundant — keep them off).

### Flag-off no-op proof
- Knob: `KALSHI_NETEV_GATE`, default **0 = OFF**. Diff is **purely additive: 646 insertions, 0 deletions**
  (`git show --numstat`: 279/0 + 101/0 + 266/0). No legacy line was edited or removed.
- Module-level `NETEV_TABLE = _load_netev_table() if NETEV_GATE else {}` — when OFF this short-circuits to
  `{}` with **zero file IO** and never even imports `kalshi_netev_calibrate` (the import lives inside the
  flag-guarded helper). Verified: `import maker_kalshi_quoter` with the env unset → `NETEV_GATE=0`,
  `NETEV_TABLE={}`.
- The gate body is `if NETEV_GATE and not void:` (quoter.py:754); the telemetry is `if NETEV_GATE:`
  (quoter.py:1761). Every executable reference is behind an `if NETEV_GATE` guard. OFF → the plan row is
  byte-identical to legacy and emits no `netev_*` key.
- `test_flag_off_is_byte_for_byte` + the cycle-telemetry test pin this empirically (temp quoted full 100/100
  == legacy when OFF).

---

## 2. THE CALIBRATION ENGINE + THE JSON IT PRODUCES

**`kalshi_live/kalshi_netev_calibrate.py`** (new, 279 lines, read-only). Contract:
- `family_of(ticker)` — rules `KXAAAGAS→gas`, `KXTEMP→temp`, else series-root. Byte-for-byte matched by the
  quoter's `_netev_family` (pinned by `test_family_of_matches_quoter_replica`).
- `calibrate(csv, credits=M8 map, exclude_taker=True, credit_lag_families=("gas",))` — per family:
  `trading_pnl` from the **venue tape** realized-with-fees P&L (receipt, NOT model), `credits` from the M8
  screenshot attribution **cross-checked against the exact CSV credit total**, `net = trading_pnl + credits`,
  `net_pct_notional = net / notional`, windowed to the credit-posting date span, taker/fee-bearing rows
  excluded as one-off forced exits (§M13 exclusion 1). The R4 model appears **nowhere** in the engine — it is
  the quoter's else-branch fallback for unproven families only.
- Re-runnable each transaction-CSV export; writes `kalshi_netev_table.json` the gate loads at startup.

### JSON produced on `kalshi_transactions_2026-07-23.csv` (independently re-run 2026-07-24)

```json
{
  "credit_attribution_ok": true,
  "credit_total_csv": 25.21,
  "credit_total_attributed": 25.21,
  "exclude_taker": true,
  "window": {"start": "2026-07-21", "end": "2026-07-22", "days": 2},
  "families": {
    "gas":  {"confidence": "receipt", "credit_lag": true,  "credits": 2.15,
             "trading_pnl": 0.2528,   "fees": 0.0, "notional": 214.8476,
             "net": 2.4028,   "net_pct_notional":  0.011184, "net_per_day":  1.2014,
             "n_trades": 99, "out_of_window_trades": 31, "excluded_taker_trades": 0},
    "temp": {"confidence": "receipt", "credit_lag": false, "credits": 23.06,
             "trading_pnl": -36.1178, "fees": 0.0, "notional": 142.672,
             "net": -13.0578, "net_pct_notional": -0.091523, "net_per_day": -6.5289,
             "n_trades": 60, "out_of_window_trades": 54, "excluded_taker_trades": 0}
  },
  "caveats": [
    "Credits LAG a Time Period (§M13): trading scored only over the credit-settled window 07-21..07-22.",
    "Per-FAMILY credit attribution is operator-UI screenshot-derived (credit rows carry an EMPTY market_ticker, §M11); only the CSV credit TOTAL is exact. Per-SERIES credits are NOT available.",
    "Taker (fee-bearing) rows excluded as one-off forced exits (§M13 exclusion 1).",
    "credit_lag families (period may be open at export -> credits under-counted, net biased pessimistic): gas."
  ]
}
```

**Does it reproduce M8?** YES, exactly:
- **gas = +1.1%** of notional (net_pct `+0.011184`; +$0.25 trading + $2.15 credits over $214.85 notional).
- **temp = −9.2%** of notional (net_pct `−0.091523`; −$36.12 trading + $23.06 credits over $142.67 notional).
- Credit cross-check **`25.21 == 25.21`** (`credit_attribution_ok: true`).

### The credit-ticker-gap caveat (honest constraint — designed around, not hand-waved)
CREDIT rows in the CSV carry an **EMPTY `market_ticker`** (Kalshi fix pending per §M11). Therefore:
- **Fill P&L is per-series exact** (from the API tape).
- **Credits are attributable at FAMILY level only** (gas / temp), from the CSV credit **total** + operator-UI
  screenshots; only the **total** is CSV-verified. The engine emits per-FAMILY calibrated net reliably and
  declares per-series credits unavailable rather than fabricating them. `credit_lag` flags gas as biased
  **pessimistic** (its weekly period may be open at export → credits under-counted).

---

## 3. VERIFIER VERDICTS (four adversarial lenses — lead with the deadliest)

**All four lenses: NOT REFUTED. Severity NONE. No CRITICAL / HIGH finding.**

### (A) NEVER-BLOCKS-EXITS — the deadliest lens — NOT REFUTED
The net-EV gate **cannot block or downsize any reducing/exit quote**, so no inventory trap exists.
- HOLDING + net-negative → the reduce-only branch (quoter.py:774–784) is a **byte-identical clone** of the
  proven `wind_down`/capture/void unwind path: same reducing-side selection (inv>0→NO, inv<0→YES), same
  `_unwind_price(best, cost)`, same `_unwind_size` (count = max(1, min(|inv|, room)), capped at |inv|, never
  overshoots flat). The gate only **removes the accumulating ADD side** — dropping the growing side is *more*
  de-risk, not an exit block.
- The two `return []` inside that branch fire **only** on a loss-capped price out of `[MIN,MAX]` — the SAME
  predicate legacy honors at line 942, riding to the settlement-taker backstop. No new suppression.
- `wind_down` (702–718) sits **above** the gate → end-of-life exits reached first; the gate is scoped
  `and not void` → void-book unwind untouched.
- Pinned by `test_exit_size_matches_legacy_unwind` (count+price == legacy) and `test_never_blocks_exits_full_size`
  (long +40 → NO 40 @0.49 unwind; short −40 → YES 40). Empirically confirmed against the REAL on-disk table.

### (B) FALSE-SKIP-GAS (keeps-net-positive) — NOT REFUTED
Real receipt table gas `net_pct = +0.011184` (`confidence:"receipt"`). With `NETEV_MIN_MARGIN_PCT` default
`0.0`: `poor = (0.011184 < 0.0) = False` → gas falls through to the **full two-sided JOIN**, 100/100 flat,
and keeps quoting when held. Gas tickers `KXAAAGASD/W` both start with `KXAAAGAS` → `_netev_family → "gas"`
matches the table. `credit_lag=True` biases gas net **pessimistic** yet it still clears positive. Not starved.
Pinned by T3 (flag-ON gas == flag-OFF) + cycle test (`netev_skipped=0`, `created=2`).

### (C) CALIBRATION-IS-ACTUALLY-MODEL — NOT REFUTED (it is receipt-based)
Independent recompute of the CSV tape matched the engine EXACTLY (gas +0.2528/214.85; temp −36.1178/142.67)
from the venue tape realized-with-fees column — **receipts, not the R4 model**. `_prospective_capture` /
`qualifying_share` appear ONLY in the unproven `/3`-haircut fallback branch, which gas/temp never enter
(both `confidence:"receipt"`). Credit total CSV-verified `25.21==25.21`; empty-ticker constraint respected
(no fabricated per-series credits); credit-lag caveat surfaced.

### (D) FLAG-OFF NO-OP / SKIPS-NET-NEGATIVE — NOT REFUTED
Flag OFF → desired_quotes output identical to parent `HEAD~1` (flat/long/short/gas), zero file IO, no plan
key. Flag ON → temp (`−0.091523 < 0`) **skips flat / reduce-only holding** (T2 cycle: `created=0`,
`netev_skipped=1`, family `temp`).

### Two NON-refuting notes (config foot-guns, not code defects — record for operator)
1. **`NETEV_MIN_MARGIN_PCT` is a FRACTION despite the `_PCT` name.** It is compared against `net_pct_notional`
   which is a fraction (`0.011`, not `1.1`). Safe at the default `0.0` (only the sign matters). **But an
   operator entering `1` intending "1%" would demand 100% margin and skip EVERYTHING, including gas.** When
   tuning: `0.005` == 0.5%, `0.01` == 1%. Do not enter whole percents.
2. **Receipt gate bites only if `kalshi_netev_table.json` exists at `DATA_DIR` when flipped.** Absent → gate
   fail-opens to `{}` → conservative model fallback. The table is present on disk with correct signs but is
   **untracked/uncommitted** (generated data; acceptable — gate fail-opens and ships default-off). The deploy
   step below md5-gates the table onto the VPS.

---

## 4. GREEN — COUNTS, MD5s, DEPLOY, ROLLBACK

### Pytest (full suite, independently re-run)
```
203 passed, 2 xfailed in 3.61s
```
The 12 new net-EV pins pass. The 2 xfails are pre-existing in `test_live_hardening.py` (unrelated). Coverage
"no data" warnings are the coverage-plugin artifact, not test failures.

### New/changed-file MD5s (worktree, 2026-07-24)
```
0d890798b6f02b04d6c879f0a25a1dcc  kalshi_live/kalshi_netev_calibrate.py
46406f79fcaca0f8f88ef0c50a748bd4  kalshi_live/maker_kalshi_quoter.py
439bab953dc57217b3c07756ebcf8981  kalshi_live/test_netev_gate.py
```

### Per-file md5-gated deploy step (Tier-3 — DO NOT run without §7 sign-off)
The generated calibration table is NOT committed; it must be placed on the VPS separately. Deploy is
operator-gated. After sign-off, on the deploy host:
```bash
# 1) verify the three source files match this handoff BEFORE anything ships
cd <maker-kalshi checkout>/kalshi_live
md5sum kalshi_netev_calibrate.py maker_kalshi_quoter.py test_netev_gate.py
#   MUST equal the three md5s above. Any mismatch -> STOP.

# 2) regenerate the calibration table from the CURRENT export and md5-check it
python kalshi_netev_calibrate.py kalshi_transactions_<latest>.csv --out kalshi_netev_table.json
python - <<'PY'  # sanity: gas net_pct > 0, temp net_pct < 0, credit_attribution_ok
import json; t=json.load(open("kalshi_netev_table.json"))
assert t["credit_attribution_ok"], "credit total mismatch"
f=t["families"]; assert f["gas"]["net_pct_notional"]>0 and f["temp"]["net_pct_notional"]<0
print("table OK", {k:round(v["net_pct_notional"],4) for k,v in f.items()})
PY

# 3) ship the table to DATA_DIR on the VPS, md5-verify the copy landed intact
#    (operator-run; scoped to /opt/pa2-maker-live per RULE FIVE — ask before touching anything shared)

# 4) flip the flag ONLY after the table is verified in place
export KALSHI_NETEV_GATE=1        # default margin 0.0 = skip net-negative only
# (do NOT set NETEV_MIN_MARGIN_PCT unless intending a fractional floor; see foot-gun #1)
```

### Rollback (two independent levers)
- **Instant, no redeploy:** `unset KALSHI_NETEV_GATE` (or set `=0`) → provable byte-for-byte no-op, table not
  even loaded. This is the primary kill.
- **Code:** `git revert f17c0c2` — purely additive commit, clean revert, zero legacy touched.

---

## 5. EXPECTED LIVE EFFECT (once `KALSHI_NETEV_GATE=1`, table in place)

- **TEMP (net −9.2%, receipt):** auto-**skipped when flat**; **reduce-only when holding** (rests the reducing
  side at the unwind price/size, drops the accumulating side). This stops the −$36/family-window fill bleed
  that reward-only quoting was funding. This is the headline win.
- **GAS (net +1.1%, receipt):** **kept, full two-sided size.** The net-positive family is not starved.
- **UNPROVEN families (new series, no receipt credits):** conservative R4 model fallback (prospective capture
  / 3 − fill fingerprint); open only if positive, else unproven-skip / MIN size.
- **EXITS / reducing / unwind:** ALWAYS proceed, full |inv| size, never blocked or downsized — in every
  family, gated or not.
- **Redundant gates:** `KALSHI_CAPTURE_GATE` (reward-only) and `KALSHI_STANDDOWN` (pool-only) are superseded;
  keep them off. Funding gate, pivot-select, loss meter unchanged and composed.
- **Telemetry:** each cycle the plan carries `netev_gate`, `netev_min_margin_pct`, `netev_skipped_markets`,
  and (when >0) `netev_min_signal` + `netev_skipped_families` for observability.

---

## 6. CALIBRATION-REFRESH RUNBOOK (keeps the gate receipt-calibrated)

The gate is only as good as the last export. Credits LAG a Time Period, so the table must be refreshed on
every transaction-CSV export. **Next export due after `2026-07-27T04:00Z`** (Task #3).

**On each new export:**
1. Pull the fresh `kalshi_transactions_<date>.csv` into `kalshi_live/`.
2. Update the operator-UI screenshot credit attribution (§M8/§M11) — the family credit split for the new
   window. The CSV credit **total** is exact; the per-family split comes from the UI until Kalshi fills the
   empty `market_ticker` on credit rows.
3. Re-run:
   ```bash
   cd kalshi_live
   python kalshi_netev_calibrate.py kalshi_transactions_<date>.csv \
       --credits '{"gas": <g>, "temp": <t>, ...}' --out kalshi_netev_table.json
   ```
4. **Validate before trusting:** `credit_attribution_ok` must be `true` (attributed sum == CSV total). Sanity
   the signs against the prior window; a family that flips sign is a decision, not a silent update — surface
   it. Note any `credit_lag` families (biased pessimistic — a marginal-negative there may be an artifact of an
   open period, re-check next export before hard-skipping a formerly-positive family).
5. Md5 the table, ship to `DATA_DIR` on the VPS (§4 step 3, operator-scoped), and the running bot picks it up
   at next restart (table is loaded once at import; a restart or redeploy re-reads it — no per-cycle API read,
   no per-cycle file IO).
6. Re-run `python -m pytest test_*.py -q` after any engine edit — all must stay green.

**New series with no receipt history:** it stays UNPROVEN (model fallback) until it accumulates credits over
a settled window; then it graduates to `confidence:"receipt"` on the next refresh. Do not hand-promote.

---

## 7. TIER-3 GATE — SIGN-OFF REQUIRED BEFORE LIVE

This is a `.py` behavioral change to the live-trading quoter (Tier-3 per Config Tuning Protocol) and touches
shared/Kalshi-live resources (RULE FIVE: Kalshi is KING; Maker/shared changes STOP-and-ASK). Before flipping
`KALSHI_NETEV_GATE=1` on the VPS:
1. **Operator sign-off** on this handoff.
2. **Md5-gate** all three source files against §4 before any ship (mismatch → STOP).
3. **Md5-gate** the generated `kalshi_netev_table.json` in place on the VPS, with `credit_attribution_ok:true`
   and the gas>0 / temp<0 sanity passing.
4. Confirm `KALSHI_CAPTURE_GATE` and `KALSHI_STANDDOWN` are OFF (superseded — avoid double-gating).
5. Flip the flag; watch telemetry (`netev_skipped_markets`, `netev_skipped_families`) for the first cycles to
   confirm temp skips and gas keeps quoting; loss meter unchanged.
6. Kill path rehearsed: `unset KALSHI_NETEV_GATE` = instant no-op.

Until all six are done, this ships **default-off** and changes nothing live.

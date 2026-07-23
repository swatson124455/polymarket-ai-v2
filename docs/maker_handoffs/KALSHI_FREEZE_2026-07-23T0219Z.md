# KALSHI MAKER — STATE FREEZE 2026-07-23T02:19:44Z

**Operator directive:** "freeze current status and sandbox all new plans and logic/mechanisms."

This file is the **reference baseline**. Any later session compares against it to detect drift.
Every value here was read from the live box or computed from the git blob at freeze time —
nothing is remembered or carried over from a handoff.

**Freeze means:** no config flips, no deploys, no artifact writes, no order-path changes.
All new plans, logic and mechanisms are built and measured in the **sandbox** (no keys, no
money, paired scoring on identical book snapshots) and are **not** deployed under this freeze.

---

## 1. FROZEN ARTIFACTS — md5, deployed vs branch HEAD

Branch `claude/maker-kalshi-live`, HEAD `27d8318`.
Deployed md5s read with `sudo md5sum /opt/pa2-maker-kalshi-live/*.py`;
HEAD md5s computed from the **git blob** (`git show HEAD:kalshi_live/<f> | md5sum`),
never the Windows working file.

| artifact | deployed md5 | HEAD blob md5 | match |
|---|---|---|---|
| `maker_kalshi_quoter.py` | `727ca7c59840a42b51c19e24c65a0982` | `727ca7c59840a42b51c19e24c65a0982` | ✅ exact |
| `maker_kalshi_client.py` | `3599d513be15bd5bad5b00f2f6dab425` | `3599d513be15bd5bad5b00f2f6dab425` | ✅ exact |
| `kalshi_attribution_ledger.py` | `67363bdd4b02a6edd99fe168923dab30` | `67363bdd4b02a6edd99fe168923dab30` | ✅ exact |
| `kalshi_ab_plugin_report.py` | `f5de2b82205640322db2211672dbefd5` | `f5de2b82205640322db2211672dbefd5` | ✅ exact |
| `flatten_kalshi.py` | `b422eda541160a7da28d6cb910a55e3c` | `0926ad408197f7b9b99ddcdbb7acd290` | ⚠ **MISMATCH — line endings only** |

### ⚠ FREEZE FINDING — the kill switch was deployed from the Windows working file

`flatten_kalshi.py` is **functionally identical**: 87 lines both sides, and
`diff <(tr -d '\r' head) <(tr -d '\r' deployed)` is **empty**. The whole delta is line endings,
measured directionally:

```
CR count:  HEAD blob = 0   (LF)      deployed = 87   (CRLF, every line)
HEAD blob as-stored == HEAD blob LF-normalised == 0926ad40…   (git really does store LF)
```

So the direction is the **opposite** of the usual trap: HEAD is clean LF and the **deployed copy
is the CRLF one**. That artifact was installed from a Windows working file rather than from the
git blob — the exact practice the handoff warns against ("hash the git blob, never the working
file"). Python executes CRLF fine, so **this is not a functional risk and is NOT being fixed
under the freeze.** It is recorded because it proves md5-gating was not applied uniformly across
artifacts: only the quoter was ever gated. `flatten_kalshi.py` is the **kill switch** — the one
file you least want deployed by an unverified path.

**Do not "fix" this during the freeze.** When the freeze lifts, redeploy it from the git blob
and md5-gate it like the quoter.

## 2. FROZEN CONFIG

`/opt/pa2-maker-kalshi-live/live.env` sha256 `8ebc0b76be7697abd7718e46fcd5c0591b2aebcc5684e4dd154d8463e6186179`

```
KALSHI_TRADING_MODE=live            KALSHI_LIVE_ARMED=operator-approved-live-pilot
KALSHI_MAX_TOTAL_CAPITAL=85         KALSHI_MAX_MARKET_CAPITAL=15   KALSHI_MAX_ACTIVATE_CAPITAL=15
KALSHI_HELD_MAX_USD=20              KALSHI_DAILY_LOSS_HALT_USD=40
KALSHI_TAKER_FLATTEN=0              KALSHI_TAKER_MAX_MKTS=8
KALSHI_MAX_UNWIND_LOSS=0.02         KALSHI_INV_TOLERANCE=1
KALSHI_INV_SOFT_CT=15               KALSHI_INV_HARD_CT=60
KALSHI_THROTTLE_STEP_TICKS=1        KALSHI_REDUCE_ONLY_KEEP_BOTH=1
KALSHI_WIND_DOWN_MIN=20             KALSHI_SETTLE_UNWIND_MIN=20
KALSHI_FOOTPRINT_TOP=40             KALSHI_PER_SERIES_CAP=10       KALSHI_JOIN_SIZE=20
KALSHI_MAX_SPREAD_TICKS=8           KALSHI_MIN_DEPTH_SYM=0.25
KALSHI_MAX_PRICE_DOLLARS=0.96       KALSHI_MIN_PRICE_DOLLARS=0.04  KALSHI_WRITE_BUDGET=60
KALSHI_SERIES_ALLOW=KXTEMPDCH,KXTEMPAUSH,KXTEMPLAXH,KXTEMPNYCH,KXTEMPCHIH,KXAAAGASD,KXAAAGASW
```

`KALSHI_THROTTLE_SMART` unset (= off). **`TAKER_FLATTEN=0` is frozen AS-IS** — the pending
flip to `1` (task #2) is deferred by this freeze, not merely by the A/B window.

## 3. FROZEN RUNTIME

| thing | frozen value |
|---|---|
| STOP sentinel | **absent** (bot is live and trading) |
| `polymarket-maker-kalshi-live.timer` | enabled, active, 2-min cadence |
| `polymarket-maker-kalshi-ledger.timer` | enabled, active, hourly |
| `kalshi-plugin-off.timer` | **transient**, armed, fires `2026-07-23T04:38:22Z` |
| `equity_day` / `equity_day_start` | `20260723` / **$63.341146** (loss meter armed, $40 quota) |
| `read_fail_streak` / `balance_fail_streak` | 0 / 0 |
| account | balance **$40.85** + held **$24.13** = **equity $64.98**, 8 positions, 5 resting |
| per-event delta | `KXAAAGASD-26JUL23` −0.18 ct (~flat) · `KXAAAGASW-26JUL27` −27.00 ct (>TOL) |
| local suite | **83 passed**, `dryrun_smoke.py` clean (forbidden-call tripwires all 0) |

### ⚠ One thing the freeze deliberately does NOT stop

`kalshi-plugin-off.timer` **will fire at 04:38:22Z and flip `REDUCE_ONLY_KEEP_BOTH` off by
itself.** That is pre-existing scheduled state, i.e. part of the status being frozen. Cancelling
it would itself be a live mutation, and letting it run returns config to the pre-A/B baseline —
arguably *more* frozen, not less. **Left armed.** If the operator wants the ON arm held
indefinitely instead, the timer must be cancelled explicitly — that is a live change and needs
its own authorization.

## 4. LIVE TELEMETRY AT FREEZE (drift baseline)

Plan rows `plans-20260723.jsonl`, three cycles spanning the freeze:

| ts (Z) | naked $ | paired_ct | two-sided | one-sided | at_ref_pct | committed $ | held $ | breaker |
|---|---|---|---|---|---|---|---|---|
| 02:00:17 | 26.60 | 2.0 | 1 | 4 | 0.0 | 69.69 | 28.09 | reduce_only |
| 02:02:17 | 22.64 | 2.0 | 1 | 3 | 0.0 | 57.99 | 24.13 | reduce_only |
| 02:18:20 | 14.27 | **12.0** | 1 | 3 | **31.0** | 59.58 | 23.18 | **cleared** |

Constant across all three: `taker_flattens=0`, `taker_failed=0`, `ladder_cross=1`,
`fetch_failed=0`, `cancel_fail=0`, `create_fail=0`, `quote_fail=0`, `ladder_violation` **absent
(=0)**.

**Two live state changes worth carrying forward:**
- **The naked-risk breaker cleared** between 02:02 and 02:18 as naked fell $22.64 → $14.27,
  back under `HELD_MAX_USD=20`. The bot is no longer reduce-only, which means the A/B's ON arm
  stopped accumulating samples in the mode it is meant to measure.
- **`paired_ct` 2.0 → 12.0.** At handoff it was **0** — the ladder pairing path was unvalidated
  in production. It is now firmly exercised, so the review debt (task #4) finally has real
  behaviour to review against rather than argument.

⚠ `ladder_cross` (`maker_kalshi_quoter.py:1089`) is the hedge-**action** counter, **not** a
violation. The violation counter is `ladder_violation` (`:909`, `:916`). Do not confuse them.

## 5. SANDBOX MANDATE (in force until the operator lifts the freeze)

New plans/logic/mechanisms are **built and measured in the sandbox only**:

- read-only, **no API keys, no money**; paired arms scored on **identical book snapshots**
- reward scoring imports the real CFTC LIP core from `scripts/maker_kalshi_recorder.py`
- pattern to follow: `kalshi_live/kalshi_ab_throttle_study.py`
- local sample corpus: `kalshi_live/ab_throttle_samples.jsonl` (612 market-snapshots, untracked)

**Standing honesty requirement** (operator directive, running tab §A0): every sandbox result
reports study name, sample size, the numbers, and explicitly **what it does not cover**. The
known structural gap: the sandbox scores the **reward** side; it **cannot** simulate fill rate
or the cost side without queue position. A result that ignores the risk side is half an answer
and must be labelled as such.

**Nothing measured in the sandbox gets deployed under this freeze.** Sandbox findings become
*proposals* with measured numbers attached, for the operator to act on when the freeze lifts.

## 6. HOW TO VERIFY THIS FREEZE LATER

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"; VPS="ubuntu@18.201.216.0"
ssh -i "$KEY" $VPS 'sudo sh -c "md5sum /opt/pa2-maker-kalshi-live/*.py | sort -k2"'
ssh -i "$KEY" $VPS 'sudo sha256sum /opt/pa2-maker-kalshi-live/live.env'
ssh -i "$KEY" $VPS 'sudo ls -la /opt/pa2-maker-kalshi-live/STOP; systemctl list-timers --all | grep -i kalshi'
python kalshi_live/kalshi_status_readonly.py ; python kalshi_live/kalshi_delta_check.py
```

Any md5 change, any `live.env` sha256 change, or a STOP sentinel appearing = **the freeze was
broken**. Find out by whom and why before proceeding.

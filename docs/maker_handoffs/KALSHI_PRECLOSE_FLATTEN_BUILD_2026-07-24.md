# KALSHI PRE-CLOSE SETTLEMENT FLATTEN — BUILD 2026-07-24

**Commit:** `d9dfbee97d046b3bda1d7629a225ba14d047db0e` (`d9dfbee`)
**Branch:** `claude/maker-kalshi-live` — **NOT DEPLOYED** (worktree edit only; running VPS bot unaffected)
**Flag:** `KALSHI_PRECLOSE_FLATTEN` — default `0` = OFF = byte-for-byte legacy
**Venue:** Kalshi only. No MB/WB/EB/SB, no shared modules, no `deploy.sh`, no live `.env`, no systemctl.
**Files touched:** `kalshi_live/maker_kalshi_quoter.py` (+170 / −0), `kalshi_live/test_preclose_flatten.py` (+225, new). Additions-only — zero legacy lines deleted (`git diff --numstat HEAD~1 HEAD`).

---

## 1. WHAT CHANGED (plain English)

**The bleed being fixed.** Measured 2026-07-24: gas-daily `26JUL24` settled 12:55Z for **REALIZED −$34.98** across 7 strikes. We were holding a **NAKED (unpaired, net-directional)** band-bet at the ATM — NO on 4.100 (resolved YES, −$10.99), YES on 4.105/4.110 (resolved NO, −$11.69 / −$9.51). Kalshi markets **CLOSE (trading ends) at 03:59Z but SETTLE at 12:55Z** — after close we cannot trade, so whatever naked inventory we hold at close rides to settlement and resolves against us. A properly PAIRED ladder (YES-low / NO-high, adjacent) self-hedges to ~$1/pair and is SAFE to carry; only the NAKED residual is the settlement gamble.

**The new mechanism.** A new `_preclose_naked_flatten` (`maker_kalshi_quoter.py:2062`), called from `run_once` at `:1720` **after** the order-apply block (so the reducing maker quote is already resting), computes each event's NAKED residual via the existing `ladder_pairing(held_by)` and, only within `PRECLOSE_FLATTEN_MIN` (default 15) of the MARKET `close_time` (trading-end, NOT the reward-period end), exits **only that unpaired residual** so it never rides into settlement. The PAIRED inventory is left to self-hedge into settlement as before.

- **MAKER-FIRST:** a cross-cycle `grace_state` (`{ticker: iso_first_seen}`, persisted in `quoter_state`) gives the passive offset `STOP_ESCALATE_S` (90s) to fill before any taker.
- **TAKER-BACKSTOP, capped:** `_taker_cross_capped` (`:1998`) crosses **at most `|naked|`** contracts (cap = `int(round(abs(ladder_pairing(held_by)[t])))`, NOT `|held|`), decrementing by the venue-CONFIRMED `fill_count` (never a lagging position re-read), and only fires when `|naked| >= STOP_TAKER_MIN_CT` (5).
- **ADDITIVE:** the taker cancels nothing pre-existing — a failed IOC on a one-sided book leaves the resting maker exit in place.

**Flag-off no-op proof.** `PRECLOSE_FLATTEN = _envi("KALSHI_PRECLOSE_FLATTEN", 0)` (`:391`) defaults to 0. The entire call site is gated `if PRECLOSE_FLATTEN:` (`:1717`). With the flag unset: the block never runs, no `preclose_grace` state key is created, no `preclose_*` plan key is written, no reads and no crosses occur — the cycle is byte-for-byte identical to legacy. Proven by (a) the `run_once`-driven `test_live_hardening` suite passing unchanged with the flag defaulted off, and (b) `test_flag_off_is_noop` asserting empty crosses / untouched grace / no plan keys. The general `TAKER_FLATTEN` path, `flatten_to_zero`, `_flatten_all`, funding gate, pivot-select, net-EV gate and loss meter are untouched — this composes by ordering only.

---

## 2. HOW IT FIXES ALL 3 `TAKER_FLATTEN` DISABLE-REASONS

The general `TAKER_FLATTEN` was reverted 2026-07-23 18:09Z (`KALSHI_HANDOFF_2026-07-23_EOD.md §2`) for three reasons. This mechanism is a **separate, scoped, pre-close-only** path that fixes each; `TAKER_FLATTEN=0` stays off and untouched.

### Reason 1 — IT DE-HEDGED LIVE PAIRS (crossed the FULL position, orphaning paired legs)
**Old bug:** GASW-4.140 was naked +6 but held +40; the old flatten crossed all 40, orphaning the 34 paired on the 4.160 leg.
**Fix:** the cross cap is sourced from `ladder_pairing(held_by)` — the **unmatched** ladder residual per ticker — NOT `|held|`. `_taker_cross_capped` hard-caps cumulative crossing at `cap_ct` and only ever submits orders for the one naked ticker `t`, so it structurally cannot place an order on a sibling's book. Paired legs surface as `npos≈0` and are filtered by `abs(npos) < INV_TOLERANCE` before the taker.
**Evidence:** verifier live-traced the exact fixture — held `{4.140:+40, 4.160:−34}` → `ladder_pairing` → `{4.140:+6, 4.160:0}` → cap 6 on 4.140, 4.160 skipped. Test #1 pins `low_crossed == 6`, `high_crossed == 0`.

### Reason 2 — THE EXIT IT PROTECTS IS WORTH ~8% (spread cost of flattening the whole position)
**Old cost:** taker-flattening the WHOLE position cost ~8% in spread (inventory-doctrine 9.69→8.90 c/ct).
**Fix:** this crosses **only the naked residual**, and **only near close** where the settlement loss (−$35 today) dwarfs the taker cost. Kalshi taker fee ≈ `ceil(0.07 · P · (1−P) · qty · 100)/100`, cap $0.035/ct → ~$1 on a 40-ct residual vs a $35 settlement loss. Below `STOP_TAKER_MIN_CT` (5) the residue rides on the maker exit — the taker is a bounded backstop, never the always-on whole-position cross.
**Evidence:** window gate at `:2103` (`if mins > PRECLOSE_FLATTEN_MIN: continue`) with a `STOP_ESCALATE_S` grace and `STOP_TAKER_MIN_CT` floor; tests #4 (120 min out → no fire) and #5 (maker-first grace).

### Reason 3 — ONE-SIDED-BOOK STRANDING (cancelled the resting exit first, then the IOC failed)
**Old bug:** `flatten_to_zero` cancelled our resting exit FIRST, the IOC failed on a one-sided book, the fallback failed too → exit cancelled, nothing replaced it.
**Fix:** `_preclose_naked_flatten` cancels nothing; `_taker_cross_capped` has **no** standing-order pre-cancel loop (contrast `flatten_to_zero:1852`, deliberately NOT reused). Its only `cancel_order` reaps the taker's OWN lingering IOC if the venue left it resting — never the maker exit's order_id. On a one-sided book `_touch` returns `price=None` → `break` before any order is created, and the resting reducing maker exit remains.
**Evidence:** test #3 — one-sided book, taker cannot fill → `cancelled == []`, the reducing exit still present in `_resting`.

---

## 3. VERIFIER VERDICTS

Four independent adversarial lenses ran against `HEAD d9dfbee`. **ALL FOUR: NOT REFUTED. No CRITICAL/HIGH. Highest severity = LOW, config-gated and pre-existing, not reachable under the intended `PRECLOSE_FLATTEN=1 / TAKER_FLATTEN=0` deploy.**

### LEAD — `crosses-naked-only-never-dehedges` (deadliest: the exact old GASW-4.140 bug) — NOT REFUTED (LOW)
The pre-close flatten provably crosses at most `|naked|` and never touches a paired leg. Trace: `naked_by = ladder_pairing(held_by)` (`:2088`), cap `= int(round(abs(npos)))` — the UNMATCHED ladder residual, NOT `|held|` — passed to `_taker_cross_capped` (`:2126`). Live-traced GASW-4.140 (held +40 / −34 → naked `{4.140:+6, 4.160:0}`): cap 6, paired leg reads 0 and is skipped. `_taker_cross_capped` hard-caps cumulative crossing at `cap_ct`, decrements by venue-CONFIRMED `fill_count`, breaks on `fill<=0` (`:2016-2044`); the taker only ever submits for the naked ticker so the paired sibling is structurally untouchable. Test #1 pins `low_crossed==6, high_crossed==0`.

**LOW residual caveats (neither reachable under the intended single-flag config):** (a) running BOTH `TAKER_FLATTEN=1` AND `PRECLOSE_FLATTEN=1` with overlapping windows could let the general path flatten first, then the pre-close block act on a cycle-start-stale `held_by` — but the deploy mandates `TAKER_FLATTEN=0`, and the pre-close path reads fresh `held_by` each cycle as the sole taker; (b) a `_strike_of` parse failure could mark a genuine pair as naked, but that is pre-existing behavior shared by the general/STOP/unwind paths, telemetry-surfaced via `strike_parse_failed`, and does not occur for real gas-daily tickers (traced `4.140 → 4.14`).

### `never-strands-the-exit` — NOT REFUTED (NONE)
The only `cancel_order` in the entire new codepath (`_taker_cross_capped:2031`) targets the taker's OWN just-created IOC if the venue left it resting; it never references the maker exit. `_preclose_naked_flatten` cancels nothing. On a one-sided book `_touch` returns `None` → break before any create_order. `WIND_DOWN` (`:715-730`) keeps the reducing maker exit resting through the window, so the taker genuinely composes on top of a live exit. Test #3: `cancelled==[]`, exit still resting. Minor non-defect notes: `PRECLOSE_FLATTEN_MIN` default 15 sits inside the general WIND_DOWN window (maker-first premise satisfied either way); `_default_preclose_close_time` issues one `public_get` per naked ticker per cycle (latency note, not correctness).

### `flag-off-is-a-noop` — NOT REFUTED (NONE)
Purely additive (170 ins / 0 del), no legacy line touched. `KALSHI_PRECLOSE_FLATTEN` defaults 0; entire call-site block `1717-1724` under `if PRECLOSE_FLATTEN:` → no `st` key, no plan key, no call when off; general `TAKER_FLATTEN` backstop, `flatten_to_zero`, `_flatten_all` all unchanged. Only residual (non-refuting): `grace_state` persists across a process restart, so a mid-window restart could skip the `STOP_ESCALATE_S` wait on the first post-restart cycle — but the reducing maker quote is still rested that same cycle before the pre-close block runs, taker stays capped at `|naked|` and cancels nothing (arguably intended: "the clock cannot live in memory").

### `fires-only-near-close` — NOT REFUTED (NONE)
The only taker is called solely from `_preclose_naked_flatten:2126`, itself only under `if PRECLOSE_FLATTEN:` at `:1717`. The window gate at `:2103` (`if mins > PRECLOSE_FLATTEN_MIN: grace_state.pop(t); continue`) is a hard continue strictly above the taker with no bypass. Clock is `/markets/{ticker}.close_time` (trading-end), NOT the reward-period end. Probes: 15.5 min → no fire; 600 min with a stale grace pre-armed 999s ago → no fire AND stale grace discarded; 15.0 min → fires (boundary inclusive); fully-paired ladder → zero crosses; unknown clock → no fire + `preclose_check_failed`. Benign non-defect edge: the window has no lower bound so `mins<0` (already past close) also attempts a taker — but that is post-close where the live venue rejects orders, so the IOC no-ops and the un-cancelled resting maker exit remains (the mock fills it only because it lacks closed-market rejection).

---

## 4. GREEN — TEST COUNTS, MD5s, DEPLOY STEP, ROLLBACK

**Pytest (`cd kalshi_live`):**
- New pins alone — `python -m pytest test_preclose_flatten.py -q` → **7 passed in 0.29s**
- Full suite — `python -m pytest test_*.py -q` → **210 passed, 2 xfailed in 3.13s** (the 2 xfails are pre-existing `xfail`-marked cases in `test_live_hardening.py`, unrelated; the no-op proof passes them unchanged).

**Per-file md5 (working tree at `d9dfbee`):**

| File | md5 |
|------|-----|
| `kalshi_live/maker_kalshi_quoter.py` | `c4c37f9348215ea2b7729863489f6c85` |
| `kalshi_live/test_preclose_flatten.py` | `a5e596570fe5d087bfc1bc3716f0d8c0` |

**Md5-gated deploy step (operator, Tier-3, AFTER sign-off — NOT done here):**
```bash
# On the release host, after checking out d9dfbee for the Kalshi bot:
md5sum kalshi_live/maker_kalshi_quoter.py kalshi_live/test_preclose_flatten.py
# EXPECT EXACTLY:
#   c4c37f9348215ea2b7729863489f6c85  kalshi_live/maker_kalshi_quoter.py
#   a5e596570fe5d087bfc1bc3716f0d8c0  kalshi_live/test_preclose_flatten.py
# If either differs, DO NOT ship — the tree is not d9dfbee.
cd kalshi_live && python -m pytest test_*.py -q   # expect 210 passed, 2 xfailed
```
Then arm (operator's call): `export KALSHI_PRECLOSE_FLATTEN=1` (optionally `KALSHI_PRECLOSE_FLATTEN_MIN=15`) and restart the quoter.

**Rollback (two independent layers):**
1. **Flag unset — instant, no redeploy:** `unset KALSHI_PRECLOSE_FLATTEN` (or `=0`) + restart. Reverts to byte-for-byte legacy behavior (proven no-op).
2. **Code revert:** `git revert d9dfbee` — additions-only, so revert is clean and touches only the two files.

---

## 5. EXPECTED LIVE EFFECT (when armed)

Near close (within `PRECLOSE_FLATTEN_MIN` of MARKET `close_time`), the NAKED residual is exited **maker-first** (passive reducing quote gets `STOP_ESCALATE_S` = 90s to fill) with a **taker backstop** capped at `|naked|` if it is still `>= STOP_TAKER_MIN_CT` (5) contracts at the deadline. The PAIRED ladder is left to self-hedge into settlement (~$1/pair). A directional residual **no longer rides into settlement**. In the measured shape, a **−$35 settlement day becomes ~−$1 of taker cost** — the residual is closed at the spread instead of resolving fully against us.

---

## 6. PRE-CLOSE WINDOW + TAKER-COST NOTE

- **Window:** `KALSHI_PRECLOSE_FLATTEN_MIN` default **15.0** min, measured against the market **`close_time`** (trading-end), NOT the reward-period end (they can differ). Sits inside the general WIND_DOWN window (live 20 min), so a reducing maker exit is already resting when the block fires — maker-first is genuine, not asserted.
- **Taker cost vs loss avoided:** Kalshi taker fee ≈ `ceil(0.07 · P · (1−P) · qty · 100)/100`, cap $0.035/ct. On a **40-ct residual ≈ $1**; on the measured 7-strike naked band the avoided settlement loss was **−$34.98**. Paying spread to dodge the settlement gamble is +EV **only** in the final pre-close stretch on naked qty — which is exactly (and only) when this fires. Below `STOP_TAKER_MIN_CT` the residue is left on the maker exit rather than paying taker.

---

## 7. TIER-3 — SIGN-OFF GATE

This is a **Tier-3 code change** (`.py` edit) behind one flag. Before any live arming:
1. **Operator sign-off** required — this is a behavioral change to the live-trading Kalshi bot, gated but real when flipped.
2. **Md5-gated deploy** (§4) — verify both file md5s match `d9dfbee` on the release host and rerun the full suite (210 passed / 2 xfailed) BEFORE flipping the flag.
3. Arm with `KALSHI_PRECLOSE_FLATTEN=1` only after 1+2. `TAKER_FLATTEN` stays `0` (untouched by this change).
4. First-live watch: telemetry keys `preclose_flatten`, `preclose_naked_ct`, `preclose_taker_ct`, `preclose_taker_failed`, `preclose_check_failed` are written ONLY when the mechanism engages — confirm the first pre-close window shows naked-only crosses and the paired legs untouched.

---

*Build not deployed. Worktree `claude/maker-kalshi-live` @ `d9dfbee`. Kalshi venue only.*

# KALSHI MAKER — LIVE PILOT RUNBOOK (operator GO recorded 2026-07-19)

**Status: operator gave GO on the pilot after the first readout** (`GO_NO_GO_2026-07-19.md`).
Recommendation was CONDITIONAL GO on a SMALL weather/temp farm slice. This runbook is the
turnkey path from "GO" to real orders. **Going live is OPERATOR-ONLY** — a session cannot and
must not fund the account, create prod keys, or set the arm phrase. The three-lock safety
(`maker_kalshi_client.py`) enforces this; do not weaken it.

Every $/config number here is a recommended DEFAULT — the operator sets final capital-at-risk.

---

## Phase 1 — OPERATOR-ONLY preconditions (a session does NONE of these)
1. Fund a real Kalshi account + complete KYC (US person — operator eligible). SSN on file for
   reward tax reporting above IRS thresholds.
2. Accept Kalshi's Developer Agreement (click-through, once).
3. Create **production** API keys at kalshi.com/account/profile → API Keys. Private key is shown
   ONCE — save the PEM to a gitignored path (mirror the demo key at `C:\Users\samwa\.kalshi\`).
   **Keys never go in the repo and never onto the VPS in plaintext-in-git.**
4. Decide final pilot capital (recommended: a few hundred $ total, not $5K cold).

## Phase 2 — Pre-live verification (session, on DEMO/prod micro — NO scale capital)
1. **`post_only` cross-block probe — BUILT 2026-07-19, RUN STILL PENDING.**
   `scripts/verify_kalshi_postonly.py` (+ 12 offline tests in
   `tests/test_maker_kalshi_postonly.py`). Two-arm experiment:
   **ARM A control** = a deeply non-marketable post_only order must REST (proves the plumbing
   is testable; without it a rejection in ARM B proves nothing); **ARM B test** = a deliberately
   crossing post_only order must be REJECTED and must NOT fill. Only "A rests AND B rejected,
   no fill" is a PASS; a fill is FAIL-CRITICAL. Prefers crossing EXTERNAL liquidity (isolates
   post_only); falls back to a self-cross, which tests post_only+STP jointly and is reported as
   the weaker result.
   ```
   KALSHI_TRADING_MODE=demo KALSHI_API_KEY_ID=<id> KALSHI_RSA_PRIVATE_KEY_PATH=<pem> \
     python scripts/verify_kalshi_postonly.py
   ```
   ⚠ **First run 2026-07-19 came back INCONCLUSIVE — the demo exchange was CLOSED**
   (`GET /exchange/status` → `exchange_active:false, trading_active:false`; every write 503
   `service_unavailable`). Confirmed external, not a probe defect: the previously-passing
   `verify_kalshi_demo.py` failed at the identical point with the same 503. Reads were fine.
   **RERUN when the demo exchange is open** (check `/exchange/status` FIRST — the probe is only
   meaningful while `trading_active:true`). The residual stays OPEN until it returns PASS.
2. Re-run `scripts/verify_kalshi_demo.py` against demo → expect 6 PASS / 0 FAIL (auth, discovery,
   two-sided lifecycle, cancel, maker_fees=0).
3. Confirm current per-market tick sizes dynamically (`market['price_ranges']`) for the pilot
   series — sub-cent steps exist.

## Phase 3 — Recommended SMALL weather-slice config (env; operator confirms $)
The quoter is fully env-configurable — no code change needed. `select_footprint` sorts by
`usd_day`, and the five KXTEMP* city-high series dominate a small footprint by ~100× (readout
Section C), so a tiny top-N naturally selects the temp farm.

```
KALSHI_TRADING_MODE=live
KALSHI_LIVE_ARMED=operator-approved-live-pilot   # operator sets — session cannot
KALSHI_API_KEY_ID=<prod key id>                  # operator
KALSHI_RSA_PRIVATE_KEY_PATH=<prod PEM path>      # operator

# footprint — start with ~2 cities:
KALSHI_FOOTPRINT_TOP=4        # top-4 programs by usd_day → ~2 temp cities × 2 mkts
KALSHI_PER_SERIES_CAP=2       # ≤2 markets per series
KALSHI_JOIN_SIZE=100          # contracts/side on non-void markets (capital caps bind this)

# capital caps — OPERATOR SETS FINAL $ (these are conservative starts):
KALSHI_MAX_MARKET_CAPITAL=40  # $ per market, both sides
KALSHI_MAX_ACTIVATE_CAPITAL=30 # $ per void market you both-side to unlock the snapshot
KALSHI_MAX_TOTAL_CAPITAL=300  # $ hard cap on the whole resting book

# safety rails (defaults are fine):
KALSHI_WIND_DOWN_MIN=45       # pull quotes 45 min before market end
KALSHI_MAX_PRICE_DOLLARS=0.97
KALSHI_MIN_PRICE_DOLLARS=0.01
```
To widen after receipts confirm: raise FOOTPRINT_TOP (10 → all 5 temp cities), then
MAX_TOTAL_CAPITAL. Do NOT widen before real reward receipts land in the wallet.

## Phase 4 — Deploy the live quoter (session, after Phase 1–2)
The VPS quoter unit currently has `ProtectHome=true` and NO keys → it physically cannot trade.
Going live requires a deployment change (operator + session):
- Make the prod PEM readable by the `polymarket` user (or inject via systemd credential/
  environment file with 0400 perms) and add the Phase-3 env (systemd `EnvironmentFile=`, NOT
  committed). Adjust `ReadWritePaths`/`ProtectHome` only enough to read the key.
- md5-verify the .py deploy as always; keep the STOP sentinel.
- **Slow the cadence for the pilot** (e.g. 10-min oneshot is fine; Basic tier = 100 write tok/s
  no burst, 0.16s request spacing → ~63 tok/s peak, ~37% margin at cold start).

## Phase 5 — Day-1 live monitoring (session)
- **Watch real reward receipts** — the pilot's own reward/rebate payment records are the ONLY
  ground truth (readout $ are model estimates, not payments). Verify a receipt hits the wallet
  before ANY scale-up.
- Settlement success rate (own orders) — page on first revert.
- `cycle ok mode=live … fails=0 badrows=0 capped=…` — a `WARNING` line = systematic failure,
  investigate immediately.
- Capital caps binding as expected; no runaway resting book.
- Kill: `sudo touch /opt/pa2-maker-kalshi-quoter/STOP` or
  `sudo systemctl disable --now polymarket-maker-kalshi-quoter.timer`.

## Living tripwires (already running)
- Recorder census = the LIP-lapse tripwire (Sep-1 sunset; operator ruling = assume renews).
  The Jul-19 WC cliff already fired cleanly in the readout; ex-WC base held.
- Re-run `maker_kalshi_readout.py` on fresh recorder data to re-check share erosion vs
  competition as the pilot runs.

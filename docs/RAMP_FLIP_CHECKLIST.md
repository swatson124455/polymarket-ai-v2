# Ramp Flip Checklist — $1 Cap → $25 Cap Event

**Document:** P0.22  
**Purpose:** Deliberate cap-flip event checklist. NOT a 5-minute env edit.  
This is an irreversible capital-exposure increase. Work through every step.

---

## Prerequisites — All 7 must pass before proceeding

Verify against [`docs/SHADOW_LIVE_EXIT_CRITERIA.md`](SHADOW_LIVE_EXIT_CRITERIA.md).

```bash
# Quick check — run this first
journalctl -u polymarket-mirror --since "7 days ago" | grep "critical" \
  | grep -v "matic_balance_low\|mirror_halt_breaker_unready"
# Must return zero lines

python scripts/counterfactual_pnl.py --bot MirrorBot --days 7
# Must exit 0

python scripts/bot_pnl.py MirrorBot 168
# Review open positions section — operator decision required on any open paper position
```

If any criterion fails, STOP. Fix the criterion before proceeding.

---

## Step 1 — Daily cap recompute

At $1 cap, the shadow-live config is: `max_bet_usd=1, max_daily_usd=10`
(preserves ~10 trades/day at $1 each).

At $25 cap, to preserve ~10 trades/day:

| Parameter | $1 shadow-live | $25 cap-flip |
|-----------|----------------|--------------|
| `max_bet_usd` | 1 | **25** |
| `max_daily_usd` | 10 | **250** |
| `capital` | 20000 | 20000 (unchanged) |
| `kelly_fraction` | 0.25 | 0.25 (unchanged) |

New `BOT_BANKROLL_CONFIG` entry for MirrorBot at $25:
```json
{"MirrorBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 25, "max_daily_usd": 250}}
```

---

## Step 2 — MATIC budget recompute

At $1 cap, gas cost per order typically exceeds order value. At $25, the ratio normalizes.

```bash
# Check current MATIC balance
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "journalctl -u polymarket-mirror --since '24 hours ago' | grep matic_balance"

# Expected: matic_balance_ok with balance > MATIC_MIN_BALANCE_WARN (1.0 MATIC default)
# At $25 cap and ~10 trades/day: gas per trade ~$0.01-0.05 → ~$0.10-0.50/day → budget 7-day: ~$3.50
# If weekly MATIC burn exceeded $10 during shadow-live, top up before flip.
```

---

## Step 3 — Apply .env changes

SSH to VPS and edit `/opt/pa2-shared/.env`:

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0
# Edit .env — change only the MirrorBot section of BOT_BANKROLL_CONFIG:
nano /opt/pa2-shared/.env
```

Changes to make:
```
# BEFORE (shadow-live $1 cap):
BOT_BANKROLL_CONFIG='{"MirrorBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 1, "max_daily_usd": 10}}'

# AFTER (cap-flip $25):
BOT_BANKROLL_CONFIG='{"MirrorBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 25, "max_daily_usd": 250}}'
```

---

## Step 4 — M10 trap: verify env vars loaded by service

**M10 trap:** `systemctl` reads env at service start, not at `.env` edit time.
Editing `.env` does NOT automatically update running services. Must restart + verify.

```bash
# 1. Restart polymarket-mirror service
sudo systemctl restart polymarket-mirror

# 2. Wait 30 seconds for startup sequence
sleep 30

# 3. Verify the service loaded the new env var (M10 verification)
sudo systemctl show -p Environment polymarket-mirror | grep BOT_BANKROLL_CONFIG
# Confirm max_bet_usd=25 is shown in output

# 4. Check health: confirm service is active and MB is scanning
journalctl -u polymarket-mirror -n 30
# Expect: "mirror_scan_start" or similar within 120s of restart
```

---

## Step 5 — Rollback dry-run re-test

Even though rollback was tested at initial shadow-live flip, re-test now at cap-flip.
Env changes for the rollback are different from the initial flip.

```bash
# Rollback procedure (memorize before flipping):
# sudo nano /opt/pa2-shared/.env
#   → restore max_bet_usd=1, max_daily_usd=10
# sudo systemctl restart polymarket-mirror
# sudo systemctl show -p Environment polymarket-mirror | grep BOT_BANKROLL_CONFIG
# python scripts/bot_pnl.py MirrorBot 1  # confirm trade size drops back to ~$1
```

---

## Step 6 — Post-flip verification (first 2 hours)

```bash
# Monitor first trades at new cap:
journalctl -u polymarket-mirror -f | grep -E "paper_trade_placed|order_risk_cap|mirror_scan"

# After first trade lands:
python scripts/bot_pnl.py MirrorBot 1
# Confirm: cost column shows ~$25 orders, not $1

# Confirm shadow_fills rows have expected size:
# SELECT order_size_usd, created_at FROM shadow_fills
# WHERE bot_name='MirrorBot' ORDER BY created_at DESC LIMIT 5;
```

---

## Step 7 — Ramp exit criteria ($25 → full target)

Criteria for ramping from $25 to full-target cap:

1. **P&L trend non-catastrophic** — 7-day P&L from `bot_pnl.py MirrorBot 168` shows no
   accelerating loss. Not requiring positive P&L at $25 (sample too small at ~10 trades/day
   × 7 days = ~70 trades); requiring no structural failure signals.

2. **counterfactual_pnl.py alignment** — intended_size fields populated (P0.2/P0.3 shipped),
   `fill_frac_at_intended` > 0.80 median, `vwap_at_intended` within 0.5¢ of `vwap_fill_price`.
   Low gap = book walk at $25 reasonably predicts actual fill.

3. **Zero P0.20 regression** — all 7 exit criteria from shadow-live still pass at $25 cap.

4. **Operator sign-off** — explicit decision documented in session handoff before cap increase.

Full-target cap, daily cap, and bankroll config for the final ramp are deferred — they depend
on observed fill rates and P&L trajectory at $25.

---

## Rollback

```bash
# Instant rollback — revert to $1 shadow-live cap:
sudo nano /opt/pa2-shared/.env
# → restore: max_bet_usd=1, max_daily_usd=10
sudo systemctl restart polymarket-mirror

# Verify:
sudo systemctl show -p Environment polymarket-mirror | grep BOT_BANKROLL_CONFIG
python scripts/bot_pnl.py MirrorBot 1  # confirm $1 orders

# Git rollback (code only, not .env):
# git revert HEAD  # if any code was changed for this event
```

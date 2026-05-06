# Shadow-Live Flip Checklist — Paper → Live ($1 Cap)

**Document:** P0.7  
**Purpose:** Step-by-step operator checklist for flipping MirrorBot from paper trading
(SIMULATION_MODE=true) to shadow-live (SIMULATION_MODE=false, max_bet_usd=$1, live CLOB orders).  
**This is a capital-exposure event.** Work through every step. Do not skip ahead.

---

## Pre-flip verification gate

Run these checks and confirm each passes before touching any env var.

### 1. P0 items shipped and deployed

```bash
# Confirm all Batch 1–5 + P0.1 commits are in the active release:
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "ls -la /opt/polymarket-ai-v2 && cat /opt/polymarket-ai-v2/deploy/VERSION 2>/dev/null || echo 'no VERSION file'"
# Expect: symlink points to a release >= 20260505_220545

git log --oneline | head -15
# Confirm these commits present (in order newest→oldest):
#   721844e feat(p0.1): loop guard
#   c64af53 feat(p0.7): SHADOW_LIVE_FLIP_CHECKLIST
#   f09c5c1 feat(p0.6): counterfactual_pnl.py
#   4b1dfc8 feat(p0.3b): intended_size_usd/shares wire
#   1b00fd6 feat(p0.5): shadow_fills completeness
#   3a1e01e feat(p0.3): twin book-walk
#   ac30768 feat(p0.2): get_bet_size tuple return
```

### 2. Schema migration applied

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "psql -U polymarket -d polymarket -c \
   \"SELECT column_name FROM information_schema.columns \
     WHERE table_name='shadow_fills' \
     ORDER BY ordinal_position;\" | grep intended"
# Must show: intended_size_shares, intended_size_usd, vwap_at_intended,
#            slippage_at_intended, fill_frac_at_intended, intended_walk_error
# (Migration 076 — shipped in Batch 3)
```

### 3. Paper trading is currently active

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "sudo systemctl show -p Environment polymarket-mirror | grep SIMULATION_MODE"
# Must show: SIMULATION_MODE=true
# If false: bot is already live — do not re-flip without investigation.
```

### 4. counterfactual_pnl.py runs to completion

```bash
python scripts/counterfactual_pnl.py --bot MirrorBot --days 7
# Must exit 0.
# NULL warnings for intended_* fields are expected (pre-P0.2/P0.3 rows).
# BIAS WARNING is expected ($1 cap < $50 threshold).
```

### 5. Bot is healthy and scanning

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "journalctl -u polymarket-mirror --since '1 hour ago' | grep -E 'mirror_scan|scan_start|ERROR|critical'"
# Expect: regular mirror_scan_start log lines (every ~60s)
# No critical errors allowed.
```

### 6. MATIC balance adequate

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "journalctl -u polymarket-mirror --since '1 hour ago' | grep matic_balance"
# Expect: matic_balance_ok with balance > 1.0 MATIC
# At $1 cap ~10 trades/day: gas ~$0.01-0.05/trade → ~$0.35/week
# Minimum recommended balance: 2.0 MATIC before flip.
```

---

## .env values at flip time

**Record these values in the session handoff before changing anything.**

Current paper-trading config (verify against running service):
```
SIMULATION_MODE=true
BOT_BANKROLL_CONFIG='{"MirrorBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 1, "max_daily_usd": 10}}'
```

> Note: `max_bet_usd=1` and `max_daily_usd=10` are already set for shadow-live sizing.
> The ONLY change at this flip is `SIMULATION_MODE: true → false`.
> BOT_BANKROLL_CONFIG does NOT change here — it changes at the cap-flip event (P0.22).

Shadow-live target config:
```
SIMULATION_MODE=false
BOT_BANKROLL_CONFIG='{"MirrorBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 1, "max_daily_usd": 10}}'
```

---

## Step 1 — Git snapshot

Before any env change:
```bash
# On local machine — record current VPS release:
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
  "readlink /opt/polymarket-ai-v2"
# Save this path as your rollback target.
```

---

## Step 2 — Apply .env change

SSH to VPS and edit the shared env file:

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0

# Edit SIMULATION_MODE only — do not touch BOT_BANKROLL_CONFIG here:
nano /opt/pa2-shared/.env
```

Change:
```
SIMULATION_MODE=true
```
To:
```
SIMULATION_MODE=false
```

---

## Step 3 — Restart service (M10 trap)

`systemctl` reads env at service start, not at edit time. Must restart.

```bash
sudo systemctl restart polymarket-mirror

# Wait 30 seconds:
sleep 30

# Verify SIMULATION_MODE is now false in the running service:
sudo systemctl show -p Environment polymarket-mirror | grep SIMULATION_MODE
# Must show: SIMULATION_MODE=false

# Verify BOT_BANKROLL_CONFIG is still max_bet_usd=1:
sudo systemctl show -p Environment polymarket-mirror | grep BOT_BANKROLL_CONFIG
# Must show max_bet_usd=1
```

---

## Step 4 — Post-flip monitoring (first 30 minutes)

```bash
# Stream logs — watch for first live order attempt:
journalctl -u polymarket-mirror -f | grep -E \
  "paper_trade_placed|order_placed|order_risk_cap|mirror_scan|shadow_fill_insert_failed|critical"

# Expected sequence on first trade:
#   mirror_scan_start
#   → paper trade path bypassed (SIMULATION_MODE=false)
#   → order_placed with size ~$1
#   → shadow_fill row written (trade_executed=true)

# NOT expected: paper_trade_placed (this indicates SIMULATION_MODE is still true)
```

After the first live order:
```bash
python scripts/bot_pnl.py MirrorBot 1
# Confirm: cost column shows ~$1 orders (not paper trades)
```

---

## Step 5 — Verify shadow_fills populated correctly

```bash
# On VPS or local (with tunnel):
# SELECT order_size_usd, trade_executed, created_at
# FROM shadow_fills
# WHERE bot_name='MirrorBot' ORDER BY created_at DESC LIMIT 5;
#
# Expect: trade_executed=true, order_size_usd ~1.0
```

---

## Rollback procedure

If any post-flip check fails or critical logs appear:

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0
nano /opt/pa2-shared/.env
# Change: SIMULATION_MODE=false → SIMULATION_MODE=true
sudo systemctl restart polymarket-mirror
sudo systemctl show -p Environment polymarket-mirror | grep SIMULATION_MODE
# Confirm: SIMULATION_MODE=true
python scripts/bot_pnl.py MirrorBot 1
# Confirm: back to paper trades (cost column shows $0.00 fills)
```

---

## After flip — next gate

Once shadow-live is confirmed working:
1. Run for 1 calendar week minimum
2. Check all 7 criteria in [`docs/SHADOW_LIVE_EXIT_CRITERIA.md`](SHADOW_LIVE_EXIT_CRITERIA.md)
3. When all 7 pass, proceed to [`docs/RAMP_FLIP_CHECKLIST.md`](RAMP_FLIP_CHECKLIST.md) (cap-flip to $25)

---

## Session handoff fields (fill before flipping)

Record these in the session handoff document:

```
Flip date/time (UTC): _______________
VPS release at flip  : _______________
SIMULATION_MODE before: true
SIMULATION_MODE after : false
BOT_BANKROLL_CONFIG   : {"MirrorBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 1, "max_daily_usd": 10}}
MATIC balance at flip : _______________ MATIC
First live order      : market_id=_____________, size=_____________, time=_______________
Operator              : _______________
Open positions at flip: _______________  (grandfather/close/accept — record decision)
```

# Shadow-Live Exit Criteria — $1 Cap → Cap-Flip Event

**Document:** P0.20  
**Purpose:** Concrete pass/fail thresholds for graduating from shadow-live ($1 cap)
to the cap-flip event (P0.22, $1 → $25). All 7 criteria must pass.  
**Measurement window:** 1 calendar week minimum from first live MB order.  
**Hard calendar ceiling:** 2 weeks — escalate to operator review if criteria still failing.

---

## The 7 Criteria

### 1. Zero orphan orders

Every `order_id` that appears in MirrorBot's `_pending_orders` dict resolves — within
60 seconds — to either:
- a fill recorded in `positions`, OR
- a cancel/reject recorded in `shadow_fills` (via rejection writer) or `trade_events`.

**Verification:**
```sql
-- Should return zero rows
SELECT order_id, submitted_at
FROM shadow_fills
WHERE bot_name = 'MirrorBot'
  AND trade_executed = false
  AND rejection_type IS NULL
  AND created_at > NOW() - INTERVAL '7 days';
```
Query above finds shadow_fill rows written without a rejection_type — indicates
an order was attempted but neither filled nor explicitly rejected within the write path.

---

### 2. ≥95% submitted-order coverage in shadow_fills ∪ rejection writer

At least 95% of trade signals that reach `OrderGateway.place_order` must have a
corresponding row in `shadow_fills` (either `trade_executed=true` for fills, or
`trade_executed=false` with a `rejection_type` for rejections).

5% slop allows for race conditions. If coverage drops below 95%, escalate to
investigation before proceeding.

**Verification:**
```bash
# Count shadow_fills rows for MB in the past 7 days
python scripts/bot_pnl.py MirrorBot 168
# Cross-check trade count vs shadow_fills row count:
# SELECT COUNT(*) FROM shadow_fills WHERE bot_name='MirrorBot' AND created_at > NOW()-INTERVAL '7 days';
```

---

### 3. MATIC burn ≤ $25 for the week

MATIC consumed by gas fees on live MB orders must not exceed $25 for the
measurement week.

*Note: Initial budget. Verify pre-flip with paper-mode gas measurement to refine.
At $1 cap, gas cost typically exceeds order value — this is expected and acceptable
during shadow-live. The cap-flip to $25 normalizes the ratio.*

**Verification:**
```bash
# Check MATIC balance delta in journalctl logs:
journalctl -u polymarket-mirror --since "7 days ago" | grep "matic_balance_ok\|matic_balance_low"
```

---

### 4. Zero unintended live activations on other 13 bots

All 13 non-MirrorBot bots must show zero live activity during the shadow-live week.
Verified daily.

**Verification:**
```bash
# Run for each non-MB bot — should show empty TRADE EVENTS section
for bot in ArbitrageBot CrossPlatformArbBot OracleBot SportsBot LLMForecasterBot \
           WeatherBot SportsInjuryBot SportsLiveBot SportsArbBot \
           EsportsBot EsportsBotV2 EsportsLiveBot LogicalArbBot; do
    python scripts/bot_pnl.py $bot 24 | grep -E "Entries:|0 trade"
done
```

---

### 5. P0.6 counterfactual_pnl.py runs to completion

`scripts/counterfactual_pnl.py` runs against the full week's shadow_fills data
without raising an exception and without NULL-column warnings (beyond the expected
staging window for P0.2/P0.3 fields not yet populated).

**Verification:**
```bash
python scripts/counterfactual_pnl.py --bot MirrorBot --days 7
# Exit code must be 0. NULL warnings for intended_size_* fields are expected
# until P0.2/P0.3 deploy; all other columns must be non-NULL.
```

---

### 6. Zero `log_critical` events for the week (excluding alert hooks)

No `logger.critical(...)` lines appear in `journalctl -u polymarket-mirror` for the
measurement week, EXCEPT:
- `matic_balance_low` — acceptable if MATIC is refilled before next check
- `mirror_halt_breaker_unready` — acceptable if cleared via MIRROR_BREAKER_BYPASS

Any other `critical` event is a blocker. Investigate before proceeding.

**Verification:**
```bash
journalctl -u polymarket-mirror --since "7 days ago" \
  | grep "critical" \
  | grep -v "matic_balance_low\|mirror_halt_breaker_unready"
# Must return zero lines
```

---

### 7. Zero nonce conflicts

No CLOB responses indicating nonce reuse or ordering conflicts during the week.
Budget is 0 — any nonce conflict is a blocker. B7 (nonce locking) is deferred to P1;
if nonce conflicts appear during shadow-live, ship B7 before cap-flip.

**Verification:**
```bash
journalctl -u polymarket-mirror --since "7 days ago" \
  | grep -i "nonce\|sequence\|duplicate order"
# Must return zero lines
```

---

## Measurement window

| Phase | Duration | Action |
|-------|----------|--------|
| Shadow-live starts | Day 0 | First live MB order placed after SIMULATION_MODE=false |
| Earliest exit | Day 7 | All 7 criteria pass → proceed to P0.22 cap-flip |
| Hard ceiling | Day 14 | Escalate to operator review if any criterion still failing |

---

## Cap-flip event

See [`docs/RAMP_FLIP_CHECKLIST.md`](RAMP_FLIP_CHECKLIST.md) for the $1 → $25 cap-flip
procedure. Do NOT flip the cap without passing all 7 criteria above.

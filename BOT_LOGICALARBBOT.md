# LogicalArbBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | YES (BOT_ENABLED_LOGICAL_ARB=true, enabled since Session 49) — off in isolation mode |
| Capital | $500 (BotBankrollManager) |
| Max bet | $200 per position (LOGICAL_ARB_MAX_POSITION_USD) |
| Kelly fraction | 0.20 |
| VPS State | DISABLED in current isolation mode |
| Last trade | Session 49 era — enabled and scanning |
| Blocker | None when isolation lifted |

## Purpose & Strategy
Exploits cross-market logical constraint violations that create risk-free or near-risk-free arb.

**Three violation types detected:**
1. **Mutual exclusivity**: `sum(YES prices) > 1.0` when markets are mutually exclusive
   → Sell YES on the most overpriced market (edge = sum - 1.0, distributed by price weight)
2. **Subset violation**: `P(A) > P(B)` when A logically implies B (A is subset of B)
   → Sell YES on subset market (A), buy YES on superset market (B)
3. **Complement violation**: `P(A) + P(B) ≠ 1.0` when A and B are complements (A = NOT B)
   → Buy/sell both sides based on complement sum deviation

**Background:** $40M documented arb profits from Polymarket logical mispricings (IMDEA Networks
research study, April 2024 - April 2025).

**Scan flow:**
1. Fetch 500 active markets from Polymarket
2. LogicalArbitrageDetector clusters markets by question similarity (sentence_transformers embeddings)
3. Within each cluster: check all 3 violation types
4. Execute top 3 opportunities per scan (MAX_OPPS_PER_SCAN=3) to avoid market impact
5. Multi-leg trades: both legs verified before execution

**Confidence formula:** `min(0.95, 0.5 + spread * 5)` — higher spread = higher confidence.

**Lazy initialization:** LogicalArbitrageDetector (heavy, has async init) loaded on first scan,
not at startup. May add 2-3s latency to first scan.

**Orphan risk:** In subset_violation, leg 2 can fail after leg 1 succeeds. Logged as WARNING
(not fatal). Position on leg 1 remains open until manual intervention.

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/logical_arb_bot.py (~316 lines) |
| Logical arb detector | base_engine/analysis/logical_arbitrage.py |
| Base bot | bots/base_bot.py |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Main scan | scan_and_trade() | ~73 |
| Detector init (lazy) | _get_detector() | ~61 |
| Route by violation type | _execute_logical_arb() | ~125 |
| Mutual exclusivity | _execute_mutual_exclusivity() | ~138 |
| Subset violation (2-leg) | _execute_subset_violation() | ~192 |
| Complement violation (2-leg) | _execute_complement_violation() | ~254 |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| Polymarket API | YES | 500 active markets per scan |
| sentence_transformers | YES | Installed on VPS in Session 49 (pip install) |
| LogicalArbitrageDetector | YES | Async init, lazy-loaded on first scan |
| PostgreSQL | YES | Positions, market data |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_LOGICAL_ARB | false | false (isolation) | Enable gate (was true before isolation) |
| LOGICAL_ARB_ENABLED | false | false | Secondary enable flag |
| LOGICAL_ARB_MIN_SPREAD | 0.025 | 0.025 | Minimum profitable spread (2.5pp) |
| LOGICAL_ARB_MAX_POSITION_USD | 200 | 200 | Max position size per leg |
| SCAN_INTERVAL_LOGICAL_ARB | 60 | 60 | Scan interval (s) |

## Known Issues & Debug History
- **[Session 49 — ENABLED]** Bot enabled with `BOT_ENABLED_LOGICAL_ARB=true + LOGICAL_ARB_ENABLED=true`.
  `sentence_transformers` installed via pip on VPS for question embeddings.
- **[Session 41]** Bot created (16th bot). Platt scaling wired. VPS full deploy.
- **[OPEN]** Orphan risk on subset_violation: If leg 2 fails after leg 1, position on leg 1
  is unhedged. Monitor WARNING logs and check positions table for unbalanced legs.
- **[OPEN]** Lazy init latency: First scan may be 2-3s slower while sentence_transformers loads.
- **[OPEN]** Market cap per scan: 3 opportunities per scan is conservative. May increase if
  market impact proves negligible.
- **[OPEN]** Embedding quality: sentence_transformers clustering works best for clearly worded
  questions. Ambiguous market phrasing may miss logical relationships.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Live logs
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep LogicalArbBot"

# Check for orphaned leg-1 positions (WARNING sign)
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since 'today' | grep -i 'orphan\|leg.*fail\|subset.*error'"

# Recent logical arb trades
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT created_at, market_id, side, size, price, realized_pnl
  FROM paper_trades WHERE bot_name='LogicalArbBot'
  ORDER BY created_at DESC LIMIT 20;\""

# Open positions (check for unbalanced legs)
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT market_id, side, size, avg_price, status
  FROM positions WHERE bot_id='LogicalArbBot' AND status='open';\""

# Run LogicalArbBot tests
pytest tests/ -k "logical_arb or logical_arb_bot" -v
```

## Next Steps / Blockers
- [ ] Re-enable when isolation mode lifted (BOT_ENABLED_LOGICAL_ARB=true + LOGICAL_ARB_ENABLED=true)
- [ ] Monitor for orphaned leg-1 positions in subset_violation path (WARNING logs)
- [ ] Check if sentence_transformers embeddings are finding logical clusters in current markets
- [ ] Consider raising MAX_OPPS_PER_SCAN from 3 if market impact proves negligible

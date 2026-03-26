# MirrorBot Session 126 — 2026-03-24

## What Was Done

### 1. ML Selector Models Retrained on VPS
- **Problem:** ML shadow race from S124 was dead. `mirror_ml_selector_loaded ql=False xgb=False` on every restart because `models/` directory didn't exist on VPS.
- **Fix:** Created `/opt/polymarket-ai-v2/models/`, ran `train_mirror_ml_selector.py` on VPS (1167 samples, AUC=0.590), restarted service. Now loads `xgb=True ql=True`.
- **Files:** No code changes. VPS-only: `models/mirror_ml_selector.pkl`, `models/mirror_ml_qtable.pkl`, `models/mirror_ml_selector_meta.json`.
- **TODO for future deploys:** `deploy.sh` must preserve `models/` directory or retrain after deploy. Models are NOT in git.

### 2. Phase 4b-alt: Position-Based Resolution Path
- **Problem:** 313 closed MirrorBot positions have ZERO RESOLUTION events. Phase 4b joins `paper_trades → trade_events` by `market_id`, but `paper_trades.market_id` and `positions.market_id` are different identifiers for the same market (condition_id vs Gamma ID). Result: 2,642 resolution events emitted but matching zero positions.
- **Fix:** Added Phase 4b-alt after Phase 4b in `resolution_backfill.py` (~line 486). Queries `positions` directly for `status='closed'` + `m.resolution IN ('YES','NO')` + no existing RESOLUTION event. Subtracts already-captured EXIT P&L to prevent double-counting on partial exits.
- **File:** `base_engine/data/resolution_backfill.py` (SHARED INFRA — all 15 bots)
- **Tests:** 1717 passed

### 3. Bogus end_date_iso Cleanup
- **Problem:** ~28 markets imported via CLOB have `end_date_iso = 2020-11-04` (garbage default). These consume priority-0 backfill slots every cycle even though CLOB confirms they're still open.
- **Fix:** In Phase 2 of `resolution_backfill.py`, when CLOB says `closed=False` and DB has `end_date_iso < NOW() - 30 days`, null it out. Best-effort, non-fatal.
- **File:** `base_engine/data/resolution_backfill.py` (~line 316)

## What Was NOT Done / Blocked

### 4b-alt Has NOT Fired Yet
The VPS service keeps getting externally restarted every ~5-7 minutes (systemd `Stopping polymarket-ai.service`), killing the ingestion cycle before it reaches the resolution backfill phase. This is a **pre-existing issue** — no crontab found, cause unknown. When a full cycle completes, 4b-alt will start draining the backlog.

### ML Shadow Race Still Blocked
MirrorBot at 597/600 position cap. Only 1 ML-scored entry in 24h. The shadow race needs new entries to collect data. Position cap needs addressing.

## Bugs Found (Not Fixed — Scope)

| # | Bug | Impact | Owner |
|---|-----|--------|-------|
| 1 | `_check_price` NameError in RTDS instant copy | Warning-level, non-fatal | Mirror session (deploy mismatch — function removed/renamed but VPS has old code) |
| 2 | 10 ghost positions (closed, 0 trade_events) | Minor P&L gap | System session |
| 3 | 117 partial exits (EXIT < ENTRY on same market) | 4b-alt handles via EXIT P&L subtraction | Handled |
| 4 | Service external restarts every ~5-7min | Blocks ingestion + resolution backfill | System session (investigate systemd/watchdog) |
| 5 | 313 closed positions on still-live markets | Will resolve naturally when markets close | Monitor |

## Blast Radius

**`resolution_backfill.py` is shared infrastructure (all 15 bots).** Two additions:
1. Phase 4b-alt: Additive — new code block after existing Phase 4b. Does NOT modify Phase 4b logic. Queries `positions` table (all bots) for closed positions missing RESOLUTION events.
2. Bogus end_date fix: Runs inside Phase 2's condition_id branch. Only fires when `end_date_iso < NOW() - 30 days` AND CLOB confirms `closed=False`. Best-effort UPDATE, non-fatal on error.

Both changes are wrapped in try/except with warning-level logging.

## Verification Commands

```bash
# Check ML selector loaded
journalctl -u polymarket-ai -n 200 | grep ml_selector_loaded

# Check 4b-alt output (after a full ingestion cycle completes)
journalctl -u polymarket-ai --since '1 hour ago' | grep '4b-alt'

# Check position backlog
PYTHONPATH=/opt/polymarket-ai-v2 python3 -c "
import asyncio
from sqlalchemy import text
async def main():
    from base_engine.data.database import Database
    db = Database()
    await db.init()
    async with db.get_session() as s:
        for bot in ['MirrorBot', 'WeatherBot', 'EsportsBot']:
            r = await s.execute(text(
                'SELECT COUNT(*) FROM positions p '
                'WHERE p.source_bot = :bot AND p.status = \'closed\' '
                'AND NOT EXISTS ('
                '  SELECT 1 FROM trade_events te '
                '  WHERE te.market_id = p.market_id AND te.bot_name = :bot '
                '  AND te.event_type = \'RESOLUTION\''
                ')'
            ), {'bot': bot})
            print(f'{bot}: {r.fetchone()[0]} missing RESOLUTION')
asyncio.run(main())
"

# Check ML-scored entries accumulating
PYTHONPATH=/opt/polymarket-ai-v2 python3 -c "
import asyncio
from sqlalchemy import text
async def main():
    from base_engine.data.database import Database
    db = Database()
    await db.init()
    async with db.get_session() as s:
        r = await s.execute(text(
            'SELECT COUNT(*), MIN(event_time), MAX(event_time) '
            'FROM trade_events '
            'WHERE bot_name = \'MirrorBot\' AND event_type = \'ENTRY\' '
            'AND event_data->>\'ml_score_xgb\' IS NOT NULL'
        ))
        row = r.fetchone()
        print(f'ML-scored entries: {row[0]}, first: {row[1]}, last: {row[2]}')
asyncio.run(main())
"
```

## TODOs for Next Session

1. **P0: Investigate external service restarts** — blocking ingestion cycle completion. Check systemd timers, deploy scripts, OOM killer, or other processes sending SIGTERM.
2. **P1: Position cap** — 597/600 open. Either raise cap, accelerate exits, or both. ML shadow race needs new entries.
3. **P1: Deploy mismatch** — `_check_price` NameError. VPS code is out of sync with git. Run full `deploy.sh` to sync.
4. **P2: Verify 4b-alt fires** — once an ingestion cycle completes without interruption, check `journalctl | grep 4b-alt`.
5. **P3: Shadow race analysis** — once 48h of ML-scored data exists, run analysis to pick winner (XGBoost vs Q-learning vs combo).
6. **P5: Model persistence in deploy.sh** — add `models/` dir to deploy atomic swap so models survive deploys.

## Files Modified (local, not committed)
- `base_engine/data/resolution_backfill.py` — Phase 4b-alt + bogus end_date fix

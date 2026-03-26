# Polymarket AI Trading Dashboard — UI Design Brief

> **Purpose**: Self-contained spec for outsourced UI design/development.
> **Date**: 2026-03-15
> **Constraint**: The UI is **strictly read-only**. It queries existing PostgreSQL tables. It does NOT write, modify, or interact with the trading bots in any way. Zero footprint on the live system.

---

## 1. What This System Is

An automated prediction-market trading system running on [Polymarket](https://polymarket.com). Four trading bots independently scan markets, detect mispricings, and place paper trades (simulated execution against real market prices). Each bot has its own strategy, capital allocation, and risk limits.

The system runs 24/7 on an Ubuntu VPS. All trade data, predictions, positions, and performance metrics are stored in PostgreSQL. **The UI's only job is to read and visualize that data.**

### Current state
- **Paper trading** (simulated execution, real market prices)
- **$20,000 notional capital** across all bots
- **~510 open positions** across all bots
- **+$15,940 total realized P&L** (since inception)

---

## 2. The Four Bots

### 2.1 MirrorBot — "Copy the smart money"
Tracks the top 1,000 traders on Polymarket by historical accuracy. When multiple elite traders independently bet the same side of a market, MirrorBot copies the trade. Automatically exits when source traders exit.

| Metric | Value |
|--------|-------|
| Strategy | Consensus-based copy trading |
| Realized P&L | +$15,051 |
| Open positions | ~103 |
| Capital allocation | $3,000 |
| Key config | min_confidence=0.55, min_reliability=0.52, max_positions=200 |

**Unique data**: Tracks which elite trader addresses triggered each trade. Has trader reliability scores.

### 2.2 WeatherBot — "Beat the weather markets"
Trades temperature and precipitation bucket markets (e.g., "Will NYC high be 50-60°F on March 20?"). Pulls 82-member ensemble forecasts from NOAA (ECMWF + GEFS), fits probability distributions, and compares model probabilities against market prices to find mispriced buckets.

| Metric | Value |
|--------|-------|
| Strategy | Ensemble weather forecast vs. market odds |
| Realized P&L | +$910 |
| Open positions | ~400 |
| Capital allocation | $25,000 |
| Key config | min_edge=0.08, kelly=0.25, max_positions=500 |

**Unique data**: Per-station calibration (MSE), forecast ensemble spreads, temperature bucket probabilities, city/date exposure groups.

### 2.3 EsportsBot — "Predict competitive gaming outcomes"
Pre-game esports predictions across 8 titles (LoL, CS2, Dota 2, Valorant, CoD, R6, StarCraft II, Rocket League). Uses game-specific ML models + Glicko-2 team ratings. Also handles series trading (best-of-N match predictions).

| Metric | Value |
|--------|-------|
| Strategy | ML ensemble + Glicko-2 ratings |
| Realized P&L | -$21 |
| Open positions | ~7 |
| Capital allocation | $5,000 |
| Key config | min_confidence=0.52, min_edge=0.08, kelly=0.25 |

**Unique data**: Per-game predictions, Glicko-2 team ratings, patch version tracking, game-specific Kelly multipliers, per-game/tournament/team exposure.

### 2.4 EsportsLiveBot — "Trade during live matches"
Real-time in-game event detection and betting. Monitors PandaScore for live game state updates (first blood, economy swings, gold leads) and places trades on significant events. **Currently disabled** — ready to activate.

| Metric | Value |
|--------|-------|
| Strategy | Live event detection → immediate trade |
| Realized P&L | $0 (disabled) |
| Open positions | 0 |
| Capital allocation | $5,000 |
| Key config | poll_interval=10s, event_max_age=60s |

**Unique data**: Live game state events, event confidence scores, per-event cooldowns.

---

## 3. Data Catalog — What the UI Can Query

**Everything below is read-only. The UI connects to PostgreSQL and runs SELECT queries. No INSERT, UPDATE, or DELETE — ever.**

### 3.1 `trade_events` — The P&L Authority (Immutable Append-Only)

Every trade the system has ever made. This is the **single source of truth** for all P&L calculations. Partitioned monthly.

| Column | Type | What it means |
|--------|------|---------------|
| `sequence_num` | BIGSERIAL | Auto-incrementing event ID |
| `event_type` | TEXT | `ENTRY` (opened position), `EXIT` (closed by bot), `RESOLUTION` (market settled) |
| `execution_mode` | TEXT | `paper` or `live` |
| `event_time` | TIMESTAMP | When the trade happened (UTC, no timezone) |
| `bot_name` | TEXT | Which bot (`MirrorBot`, `WeatherBot`, `EsportsBot`, `EsportsLiveBot`) |
| `market_id` | TEXT | Polymarket market identifier |
| `token_id` | TEXT | YES or NO token |
| `side` | TEXT | `YES` or `NO` |
| `size` | NUMERIC(18,8) | Number of shares |
| `price` | NUMERIC(18,8) | Execution price (0.00–1.00) |
| `fees` | NUMERIC(18,8) | Transaction fees |
| `realized_pnl` | NUMERIC(18,4) | Profit/loss (only on EXIT and RESOLUTION events) |
| `confidence` | NUMERIC(6,4) | Model confidence at trade time (0–1) |
| `predicted_probability` | NUMERIC(6,4) | Model's predicted probability |
| `model_name` | TEXT | Which model made the prediction |
| `idempotency_key` | TEXT | Deduplication key |
| `event_data` | JSONB | Extra metadata (varies by bot) |
| `correlation_id` | TEXT | Groups related events (e.g., entry + exit of same position) |

**Key queries:**
- Realized P&L by bot: `SELECT bot_name, SUM(realized_pnl) FROM trade_events WHERE event_type IN ('EXIT','RESOLUTION') GROUP BY bot_name`
- Trade count by day: `SELECT DATE(event_time), COUNT(*) FROM trade_events WHERE event_type='ENTRY' GROUP BY 1`
- Win/loss breakdown: `SELECT bot_name, COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins, COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses FROM trade_events WHERE realized_pnl IS NOT NULL GROUP BY bot_name`

### 3.2 `positions` — Open Positions (Mark-to-Market)

Currently held positions. `current_price` and `unrealized_pnl` auto-update every 10 seconds by the position manager.

| Column | Type | What it means |
|--------|------|---------------|
| `id` | BIGSERIAL | Position ID |
| `bot_id` | TEXT | Which bot holds this position |
| `market_id` | TEXT | Market identifier |
| `token_id` | TEXT | YES/NO token |
| `side` | TEXT | `YES` or `NO` |
| `size` | FLOAT | Shares held |
| `entry_price` | FLOAT | Price paid per share |
| `current_price` | FLOAT | Latest market price (updated every 10s) |
| `unrealized_pnl` | FLOAT | `(current_price - entry_price) * size` |
| `entry_cost` | FLOAT | Total cost including slippage |
| `breakeven_price` | FLOAT | Price needed to break even after fees |
| `opened_at` | TIMESTAMP | When position was opened (UTC) |
| `status` | TEXT | `open`, `reserving`, or `closed` |
| `is_paper` | BOOLEAN | Always `true` in paper mode |
| `trader_addresses` | TEXT[] | (MirrorBot only) Elite traders being mirrored |

**Key queries:**
- Open positions by bot: `SELECT bot_id, COUNT(*), SUM(size * entry_price) AS deployed, SUM(unrealized_pnl) AS upnl FROM positions WHERE status='open' GROUP BY bot_id`
- Largest positions: `SELECT * FROM positions WHERE status='open' ORDER BY ABS(unrealized_pnl) DESC LIMIT 20`

### 3.3 `prediction_log` — Model Predictions + Calibration

Every prediction every model has ever made, whether or not a trade was executed. Essential for calibration analysis (Brier scores, reliability diagrams).

| Column | Type | What it means |
|--------|------|---------------|
| `id` | BIGSERIAL | Prediction ID |
| `market_id` | TEXT | Market identifier |
| `model_name` | TEXT | Model that made the prediction |
| `predicted_prob` | FLOAT | Model's YES probability (0–1) |
| `market_price` | FLOAT | Market YES price at prediction time |
| `edge` | FLOAT | `predicted_prob - market_price` |
| `confidence` | FLOAT | Model confidence level |
| `trade_executed` | BOOLEAN | Did this prediction result in a trade? |
| `trade_side` | TEXT | If traded, which side |
| `trade_size` | FLOAT | If traded, how much |
| `trade_pnl` | FLOAT | If traded, what was the P&L |
| `resolution` | TEXT | Market outcome (`YES`, `NO`, or `NULL` if pending) |
| `was_correct` | BOOLEAN | Was prediction correct? |
| `realized_edge` | FLOAT | Actual outcome minus market price |
| `bot_name` | TEXT | Which bot |
| `prediction_time` | TIMESTAMPTZ | When prediction was made |
| `resolved_at` | TIMESTAMPTZ | When market resolved |
| `ensemble_pred` | FLOAT | Blended ensemble prediction |
| `feature_snapshot` | JSON | ML features at prediction time |

**Key queries:**
- Brier score by model: `SELECT model_name, AVG(POWER(predicted_prob - CASE WHEN resolution='YES' THEN 1.0 ELSE 0.0 END, 2)) AS brier FROM prediction_log WHERE resolution IS NOT NULL GROUP BY model_name`
- Calibration buckets: `SELECT ROUND(predicted_prob, 1) AS bucket, AVG(CASE WHEN resolution='YES' THEN 1.0 ELSE 0.0 END) AS actual_rate, COUNT(*) FROM prediction_log WHERE resolution IS NOT NULL GROUP BY 1 ORDER BY 1`

### 3.4 `equity_snapshots` — Daily Portfolio Metrics

One row per bot per day. Tracks equity curve, drawdown, Sharpe ratio, win/loss counts.

| Column | Type | What it means |
|--------|------|---------------|
| `snapshot_date` | DATE | Day of snapshot |
| `bot_name` | TEXT | Which bot |
| `total_capital` | NUMERIC(18,4) | Starting capital |
| `deployed_capital` | NUMERIC(18,4) | Capital in open positions |
| `realized_pnl` | NUMERIC(18,4) | Cumulative realized P&L |
| `unrealized_pnl` | NUMERIC(18,4) | Mark-to-market uPnL |
| `total_equity` | NUMERIC(18,4) | `capital + realized + unrealized` |
| `open_positions` | INTEGER | Number of open positions |
| `daily_trades` | INTEGER | Trades executed that day |
| `win_count` | INTEGER | Winning trades that day |
| `loss_count` | INTEGER | Losing trades that day |
| `peak_equity` | NUMERIC(18,4) | All-time peak |
| `drawdown_pct` | NUMERIC(8,6) | Current drawdown from peak |
| `rolling_sharpe` | NUMERIC(8,4) | Rolling Sharpe ratio |

**Key queries:**
- Equity curve: `SELECT snapshot_date, bot_name, total_equity FROM equity_snapshots ORDER BY snapshot_date`
- Daily P&L: `SELECT snapshot_date, SUM(realized_pnl) FROM equity_snapshots GROUP BY 1 ORDER BY 1`

### 3.5 `markets` — Market Metadata

Every Polymarket market the system has ever seen.

| Column | Type | What it means |
|--------|------|---------------|
| `id` | TEXT | Market identifier (PK) |
| `question` | TEXT | Market question ("Will BTC hit $100k by March?") |
| `category` | TEXT | Market category (crypto, sports, politics, etc.) |
| `end_date_iso` | TIMESTAMP | When market expires |
| `yes_price` | FLOAT | Current YES price |
| `no_price` | FLOAT | Current NO price |
| `volume` | FLOAT | Market volume |
| `liquidity` | FLOAT | Market liquidity |
| `resolved` | BOOLEAN | Has market settled? |
| `resolution` | TEXT | Outcome (YES, NO, etc.) |
| `resolved_at` | TIMESTAMP | When market resolved |
| `active` | BOOLEAN | Is market active? |

### 3.6 `daily_counters` — Daily Exposure Tracking

Write-through counters that persist daily exposure state across bot restarts.

| Column | Type | What it means |
|--------|------|---------------|
| `bot_id` | TEXT | Which bot |
| `counter_date` | DATE | Resets at UTC midnight |
| `counter_name` | TEXT | What's being counted (e.g., `daily_exposure_usd`, `game_LoL`) |
| `counter_value` | NUMERIC | Current value |
| `updated_at` | TIMESTAMPTZ | Last update |

**Key queries:**
- Today's exposure by bot: `SELECT bot_id, counter_name, counter_value FROM daily_counters WHERE counter_date = CURRENT_DATE`

### 3.7 `users` — Trader Profiles (MirrorBot data)

Elite trader metadata. Useful for showing who MirrorBot is following.

| Column | Type | What it means |
|--------|------|---------------|
| `address` | TEXT | Wallet address (PK) |
| `total_profit` | FLOAT | Trader's all-time profit |
| `total_volume` | FLOAT | Trader's volume |
| `win_rate` | FLOAT | Win percentage |
| `total_trades` | INTEGER | Number of trades |
| `is_elite` | BOOLEAN | Passes elite filter? |
| `roi` | FLOAT | Return on investment |

### 3.8 `traded_markets` — Markets We've Actually Bet On

Subset of markets where at least one bot has placed a trade. Used for resolution backfill tracking.

| Column | Type | What it means |
|--------|------|---------------|
| `market_id` | TEXT | Market identifier (PK) |
| `bot_names` | TEXT | CSV list of bots that traded this market |
| `first_trade_at` | TIMESTAMP | When first trade was placed |
| `resolved` | BOOLEAN | Is market resolved? |
| `resolution` | TEXT | Outcome |
| `resolved_at` | TIMESTAMP | When resolved |

### 3.9 `market_prices` — Historical Price Data

Price history snapshots, partitioned monthly.

| Column | Type | What it means |
|--------|------|---------------|
| `market_id` | TEXT | Market |
| `token_id` | TEXT | YES/NO token |
| `price` | FLOAT | Price at timestamp |
| `side` | TEXT | BUY or SELL |
| `timestamp` | TIMESTAMP | When (UTC) |

---

## 4. Proposed Dashboard Views

### 4.1 Command Center (Home)

**Purpose**: At-a-glance system health. Answer "is everything working?" in 2 seconds.

**Layout concept:**
```
┌─────────────────────────────────────────────────────────┐
│  TOTAL P&L: +$15,940    POSITIONS: 510    DEPLOYED: $X  │
│  ═══════════════════════════════════════════════════════ │
│                                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Mirror   │ │ Weather  │ │ Esports  │ │ EspLive  │   │
│  │ +$15,051 │ │ +$910    │ │ -$21     │ │ DISABLED │   │
│  │ 103 pos  │ │ ~400 pos │ │ 7 pos    │ │ 0 pos    │   │
│  │ ● ACTIVE │ │ ● ACTIVE │ │ ● ACTIVE │ │ ○ OFF    │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│                                                         │
│  [────── Equity Curve (all bots stacked) ──────────]    │
│                                                         │
│  [────── Last 10 Trades (live feed) ───────────────]    │
└─────────────────────────────────────────────────────────┘
```

**Data sources:**
- Bot cards: `SELECT bot_name, SUM(realized_pnl) FROM trade_events WHERE event_type IN ('EXIT','RESOLUTION') GROUP BY bot_name` + `SELECT bot_id, COUNT(*) FROM positions WHERE status='open' GROUP BY bot_id`
- Equity curve: `equity_snapshots` table
- Live feed: `SELECT * FROM trade_events ORDER BY event_time DESC LIMIT 10`

### 4.2 Bot Detail Page (one per bot)

**Purpose**: Deep dive into a single bot. Positions, trade history, P&L curve, key metrics.

**Layout concept:**
```
┌─────────────────────────────────────────────────────────┐
│  [MirrorBot]  [WeatherBot]  [EsportsBot]  [EspLive]    │
│  ═══════════════════════════════════════════════════════ │
│                                                         │
│  P&L: +$15,051 realized  |  +$631 unrealized           │
│  Win rate: 62%  |  Positions: 103/200  |  Sharpe: 1.4   │
│                                                         │
│  [────── Bot Equity Curve ─────────────────────────]    │
│                                                         │
│  OPEN POSITIONS                              [filters]  │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Market Question  │ Side │ Size │ Entry │ uPnL   │    │
│  │ Will BTC hit...  │ YES  │ 50   │ 0.65  │ +$12   │    │
│  │ NYC temp 50-60   │ NO   │ 30   │ 0.40  │ -$3    │    │
│  │ T1 vs GenG       │ YES  │ 20   │ 0.55  │ +$1    │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  TRADE HISTORY                               [filters]  │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Time     │ Type  │ Market  │ Side │ PnL         │    │
│  │ 14:32    │ ENTRY │ BTC...  │ YES  │ —           │    │
│  │ 13:10    │ EXIT  │ ETH...  │ NO   │ +$45        │    │
│  │ 12:05    │ RESOL │ Rain... │ YES  │ -$12        │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

**Data sources:**
- Positions: `positions WHERE bot_id='MirrorBot' AND status='open'`
- Trades: `trade_events WHERE bot_name='MirrorBot' ORDER BY event_time DESC`
- Market questions: JOIN `markets` on `market_id`
- Equity: `equity_snapshots WHERE bot_name='MirrorBot'`

### 4.3 Trade Feed

**Purpose**: Chronological stream of all trade events across all bots. Filterable by bot, event type, side, date range.

**Layout concept:**
```
┌─────────────────────────────────────────────────────────┐
│  Filters: [All Bots ▼] [All Types ▼] [Last 24h ▼]      │
│  ═══════════════════════════════════════════════════════ │
│                                                         │
│  14:32:05  ENTRY   MirrorBot   "Will BTC..."  YES  $50  │
│  14:31:42  EXIT    WeatherBot  "NYC temp..."   NO   +$8  │
│  14:30:18  RESOL   EsportsBot  "T1 vs GenG"   YES  -$3  │
│  14:28:55  ENTRY   WeatherBot  "London..."     YES  $25  │
│  14:25:11  ENTRY   MirrorBot   "ETH merge..."  NO   $40  │
│  ...                                                     │
│                                           [Load More]    │
└─────────────────────────────────────────────────────────┘
```

**Data sources:**
- `trade_events` joined with `markets` for question text
- Paginated: `ORDER BY event_time DESC LIMIT 50 OFFSET ?`

### 4.4 Risk Dashboard

**Purpose**: Exposure breakdown, concentration warnings, daily limit utilization. Answer "how much risk do we have and where?"

**Layout concept:**
```
┌─────────────────────────────────────────────────────────┐
│  SYSTEM EXPOSURE: $8,200 / $20,000 cap                  │
│  ████████████████████░░░░░░░░░░░░░  41%                 │
│  ═══════════════════════════════════════════════════════ │
│                                                         │
│  BY BOT               │  BY CATEGORY                    │
│  Mirror:  $4,100 ███  │  Crypto:     $2,800 ██          │
│  Weather: $3,500 ██   │  Weather:    $3,500 ███         │
│  Esports: $600   █    │  Esports:    $600   █           │
│  EspLive: $0         │  Politics:   $1,300 █           │
│                       │                                  │
│  DAILY LIMITS                                           │
│  Mirror daily:  $1,200 / $10,000  ████░░░░░░  12%      │
│  Weather daily: $800 / $10,000    ███░░░░░░░  8%       │
│  Esports daily: $50 / $500        ██░░░░░░░░  10%      │
│                                                         │
│  TOP CONCENTRATED POSITIONS                              │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Market              │ Bots │ Total $  │ % total │    │
│  │ Will BTC hit $100k  │ 2    │ $800     │ 9.8%    │    │
│  │ NYC high 50-60°F    │ 1    │ $400     │ 4.9%    │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

**Data sources:**
- Exposure by bot: `SELECT bot_id, SUM(size * entry_price) FROM positions WHERE status='open' GROUP BY bot_id`
- Exposure by category: JOIN `positions` with `markets` on market_id, GROUP BY category
- Daily limits: `daily_counters WHERE counter_date = CURRENT_DATE`
- Concentration: `SELECT market_id, COUNT(DISTINCT bot_id), SUM(size * entry_price) FROM positions WHERE status='open' GROUP BY market_id ORDER BY 3 DESC`

### 4.5 Analytics

**Purpose**: Model performance, calibration quality, edge decay. Answer "are the bots getting smarter or dumber?"

**Layout concept:**
```
┌─────────────────────────────────────────────────────────┐
│  Period: [Last 7 days ▼]   Bot: [All ▼]                │
│  ═══════════════════════════════════════════════════════ │
│                                                         │
│  WIN RATES (resolved trades only)                       │
│  Mirror: 62% (376 resolved)                             │
│  Weather: 58% (156 resolved)                            │
│  Esports: 48% (62 resolved)                             │
│                                                         │
│  [────── Calibration Plot ─────────────────────────]    │
│  (X: predicted probability, Y: actual outcome rate)     │
│  (diagonal = perfect calibration)                       │
│                                                         │
│  [────── Edge Over Time ──────────────────────────]     │
│  (X: date, Y: average edge at entry)                    │
│  (declining edge = markets getting efficient)            │
│                                                         │
│  [────── P&L by Market Category ─────────────────]     │
│  (bar chart: crypto, weather, esports, politics)         │
│                                                         │
│  BRIER SCORES (lower = better calibrated)               │
│  ensemble: 0.21  |  weather: 0.19  |  esports: 0.24    │
└─────────────────────────────────────────────────────────┘
```

**Data sources:**
- Win rates: `trade_events WHERE realized_pnl IS NOT NULL`
- Calibration: `prediction_log WHERE resolution IS NOT NULL`
- Edge over time: `SELECT DATE(prediction_time), AVG(edge) FROM prediction_log WHERE trade_executed=true GROUP BY 1`
- Brier scores: `SELECT model_name, AVG(POWER(predicted_prob - CASE WHEN resolution='YES' THEN 1.0 ELSE 0.0 END, 2)) FROM prediction_log WHERE resolution IS NOT NULL GROUP BY 1`
- P&L by category: JOIN `trade_events` with `markets`, GROUP BY category

### 4.6 Period Snapshot & Compare

**Purpose**: Pick any two dates (or a date range) and see exactly how the portfolio changed. Answer "how did I do last week?" or "compare this month vs last month."

**Layout concept:**
```
┌─────────────────────────────────────────────────────────┐
│  FROM: [2026-03-01]  TO: [2026-03-15]   [Compare]      │
│  Presets: [Today] [Last 7d] [Last 30d] [MTD] [All Time] │
│  ═══════════════════════════════════════════════════════ │
│                                                         │
│  PERIOD SUMMARY                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │              │ Start    │ End      │ Delta       │    │
│  │ Total Equity │ $34,200  │ $35,940  │ +$1,740     │    │
│  │ Realized PnL │ $14,200  │ $15,940  │ +$1,740     │    │
│  │ Unrealized   │ $1,050   │ $1,200   │ +$150       │    │
│  │ Positions    │ 480      │ 510      │ +30         │    │
│  │ Deployed     │ $7,800   │ $8,200   │ +$400       │    │
│  │ Drawdown     │ 1.8%     │ 2.3%     │ +0.5%       │    │
│  │ Sharpe       │ 1.51     │ 1.42     │ -0.09       │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  PER-BOT BREAKDOWN (same period)                        │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Bot       │ Trades │ Wins │ Losses │ Net PnL    │    │
│  │ Mirror    │ 45     │ 28   │ 17     │ +$1,200    │    │
│  │ Weather   │ 32     │ 18   │ 14     │ +$480      │    │
│  │ Esports   │ 8      │ 3    │ 5      │ +$60       │    │
│  │ EspLive   │ 0      │ 0    │ 0      │ $0         │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  [────── Equity Curve (zoomed to period) ──────────]    │
│                                                         │
│  [────── Daily P&L Bars (green/red per day) ───────]    │
└─────────────────────────────────────────────────────────┘
```

**Data sources:**
- Period boundaries: `SELECT * FROM equity_snapshots WHERE snapshot_date IN (start_date, end_date)`
- Delta: simple subtraction of end - start values
- Per-bot trades in period: `SELECT bot_name, COUNT(*) AS trades, COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins, COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses, SUM(realized_pnl) AS net FROM trade_events WHERE event_type IN ('EXIT','RESOLUTION') AND event_time BETWEEN start AND end GROUP BY bot_name`
- Equity curve zoom: `SELECT snapshot_date, bot_name, total_equity FROM equity_snapshots WHERE snapshot_date BETWEEN start AND end`
- Daily P&L bars: `SELECT DATE(event_time) AS day, SUM(realized_pnl) AS daily_pnl FROM trade_events WHERE event_type IN ('EXIT','RESOLUTION') AND event_time BETWEEN start AND end GROUP BY 1 ORDER BY 1`

**Presets logic:**
- "Today": start = today 00:00 UTC, end = now
- "Last 7d": start = today - 7, end = today
- "Last 30d": start = today - 30, end = today
- "MTD": start = first of current month, end = today
- "All Time": start = earliest equity_snapshot date, end = today

---

## 5. Technical Requirements

### Non-negotiable
- **Read-only** — The UI connects to PostgreSQL with a read-only DB user. No writes, no mutations, no side effects on the trading system. Zero footprint.
- **Lightweight** — Must run alongside the trading bots on the same VPS (Ubuntu, 16GB RAM, 4 vCPU). The bots are the priority workload. The UI must not compete for resources.
- **Single-user** — Owner-only access. Simple auth (API token, basic auth, or similar). No user registration, no multi-tenancy.

### Backend
- **Language**: Python 3.13 (same as trading system)
- **API framework**: FastAPI (preferred) — lightweight REST endpoints, optional WebSocket for live trade feed
- **Database**: PostgreSQL via asyncpg (read-only connection)
- **Cache**: Redis available for caching query results (optional)

### Frontend
- **Framework**: Designer's choice — React, Vue, Svelte, or even server-rendered templates (HTMX/Jinja) are all acceptable
- **Charts**: Designer's choice — Recharts, D3, Plotly, Lightweight Charts, Apache ECharts all fine
- **Styling**: Dark mode strongly preferred (trading dashboard aesthetic). Clean, minimal, data-dense.

### Deployment
- Runs on the same VPS as the trading system
- Should be deployable via `docker-compose up` or a simple systemd service
- HTTPS via existing Caddy/nginx reverse proxy (already configured on VPS)

---

## 6. P&L Calculation Rules (CRITICAL — Must Be Exact)

The UI **must** use these formulas. Getting P&L wrong is unacceptable.

### Source of truth
- **Realized P&L**: `SELECT SUM(realized_pnl) FROM trade_events WHERE event_type IN ('EXIT', 'RESOLUTION') AND bot_name = ?`
- **Unrealized P&L**: `SELECT SUM(unrealized_pnl) FROM positions WHERE status = 'open' AND bot_id = ?`
- **Total equity**: `capital + realized_pnl + unrealized_pnl`

### P&L formula (uniform for all sides)
```
cost_basis    = entry_price * size
unrealized    = (current_price - entry_price) * size
realized_exit = (exit_price - entry_price) * size - fees
realized_res  = (resolution_value - entry_price) * size - fees
```

Where `resolution_value` = 1.0 if market resolved YES and position is YES side, 0.0 otherwise (and vice versa for NO side — but prices are already token-specific, so **never invert the formula for NO positions**).

### What NOT to do
- Do NOT read `paper_trades` for P&L — it's legacy and incomplete
- Do NOT invert formulas for NO-side positions — prices are token-specific
- Do NOT treat ENTRY events as having realized P&L (they don't)

---

## 7. Open Questions for Designer/Developer

Please come back with your recommendations on these:

1. **Mobile responsive?** Dashboard will primarily be used on desktop, but occasional mobile check-in would be nice. How much effort to make it responsive?

2. **Real-time updates?** Options:
   - Polling every 30s (simplest)
   - WebSocket push for trade feed only (medium)
   - Full WebSocket for all data (complex)
   - Recommendation?

3. **Chart library?** What do you prefer working with for financial/trading dashboards?

4. **Auth approach?** Simple bearer token? Basic auth? Session-based? The system is single-user, so complexity here should be minimal.

5. **Scope estimate?** Given the 5 views described, what's your estimate for:
   - API layer (FastAPI endpoints for each view)
   - Frontend (5 pages + navigation)
   - Total hours / timeline

6. **Design system?** Any preference on component library (shadcn/ui, Tailwind, Material, etc.)?

7. **Hosting preference?** Same VPS via Docker, or separate lightweight host?

---

## 8. Sample Data Shapes

What the API responses would look like:

### Bot summary (Command Center)
```json
{
  "bots": [
    {
      "name": "MirrorBot",
      "status": "active",
      "realized_pnl": 15051.23,
      "unrealized_pnl": 631.45,
      "open_positions": 103,
      "deployed_capital": 4100.00,
      "win_rate": 0.62,
      "total_trades": 573,
      "last_trade_at": "2026-03-15T14:32:05Z"
    }
  ],
  "system": {
    "total_realized_pnl": 15940.23,
    "total_unrealized_pnl": 1200.00,
    "total_positions": 510,
    "total_deployed": 8200.00,
    "exposure_cap": 20000.00
  }
}
```

### Position (Bot Detail)
```json
{
  "id": 1542,
  "bot_id": "MirrorBot",
  "market_id": "0x1234abcd...",
  "market_question": "Will Bitcoin reach $100,000 by March 31?",
  "market_category": "crypto",
  "side": "YES",
  "size": 50.0,
  "entry_price": 0.65,
  "current_price": 0.71,
  "unrealized_pnl": 3.00,
  "opened_at": "2026-03-12T08:15:00Z",
  "trader_addresses": ["0xabc...", "0xdef..."]
}
```

### Trade event (Trade Feed)
```json
{
  "sequence_num": 28451,
  "event_type": "EXIT",
  "bot_name": "WeatherBot",
  "market_id": "0x5678efgh...",
  "market_question": "Will NYC high be 50-60°F on March 18?",
  "side": "NO",
  "size": 30.0,
  "price": 0.45,
  "realized_pnl": 8.50,
  "confidence": 0.72,
  "event_time": "2026-03-15T14:31:42Z"
}
```

### Daily equity snapshot (Analytics)
```json
{
  "snapshot_date": "2026-03-14",
  "bot_name": "MirrorBot",
  "total_equity": 18051.23,
  "realized_pnl": 15051.23,
  "unrealized_pnl": 631.45,
  "deployed_capital": 4100.00,
  "open_positions": 103,
  "daily_trades": 12,
  "win_count": 8,
  "loss_count": 4,
  "drawdown_pct": 0.023,
  "rolling_sharpe": 1.42
}
```

---

## 9. What's Off-Limits

To reiterate — the UI is a **passive observer**:

- No starting/stopping bots
- No changing config values
- No placing or canceling trades
- No modifying database records
- No calling external APIs (Polymarket, PandaScore, NOAA, etc.)
- No writing to any file on the server
- No WebSocket connections to the trading system's internal bus

The UI reads PostgreSQL. That's it. If a future version needs controls, that's a separate scope.

---

## 10. File & Schema Reference

For the developer building the API layer, here are the key files in the codebase:

| What | Where |
|------|-------|
| Database models + queries | `base_engine/data/database.py` |
| Migration files (schema DDL) | `schema/migrations/` |
| Canonical P&L script | `scripts/bot_pnl.py` |
| Config / settings | `config/settings.py` |
| Bot bankroll config | `BOT_BANKROLL_CONFIG` env var (JSON) |

All timestamps in the database are **UTC, timezone-naive** (no `+00:00` suffix). The UI should display times in the user's local timezone.

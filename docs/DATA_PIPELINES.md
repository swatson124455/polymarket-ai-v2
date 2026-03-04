# Data Pipelines

This document describes the two data pipelines that feed the bot training system.

## Pipeline 1: Poly Data (Gamma + Goldsky)

**Purpose:** Markets and trades from Gamma API and Goldsky subgraph.

**Flow:**
1. `poly_data/update_all.py` – fetches markets (Gamma), order-filled events (Goldsky), missing markets, processes trades
2. `scripts/import_poly_data_to_db.py` – imports into PostgreSQL (markets, trades), derives OHLC from trades → market_prices

**Output tables:** `markets`, `trades`, `market_prices` (OHLC derived from trades)

**When to use:** Primary pipeline when CLOB API is blocked or unavailable. No VPN required.

## Pipeline 2: Legacy CLOB

**Purpose:** Historical price data from Polymarket CLOB API.

**Flow:**
1. `DataIngestionService.ingest_historical_prices()` – fetches from CLOB `/prices-history`
2. `bulk_insert_prices_raw()` → `market_prices` table

**Output tables:** `market_prices`

**When to use:** When CLOB API is available. Provides official price history (can be denser than OHLC from trades).

## Backtest Data Sources

Backtest uses (in order):
1. **Trades** – from `trades` table (poly_data or legacy ingestion)
2. **Prices** – from `market_prices` table (legacy CLOB or poly_data OHLC)

If `market_prices` is empty, backtest will raise a clear error. Run `import_poly_data_to_db.py` (without `--skip-ohlc`) to derive OHLC from trades as fallback.

## Coordination: Lock File

While Poly Data pull is running, backtest is blocked to prevent reading partial data.

- Lock file: `poly_data/.pull_in_progress`
- Created at start of Poly Data pull, removed when complete
- Stale locks (>2h) are auto-cleaned

## Bot Training Dependencies

| Consumer | Data source |
|----------|-------------|
| `learn_from_price_history` | `market_prices` |
| `_fallback_training_from_prices` | `market_prices` |
| Backtest | `market_prices` or `trades` |
| FeatureStore | `market_prices` |

**Critical:** If `market_prices` is empty, bot training and backtest are blocked. Use poly_data OHLC derivation when legacy CLOB is down.

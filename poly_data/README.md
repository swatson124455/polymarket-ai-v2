# Poly Data (Extended)

Polymarket data pipeline extended with **resolution, liquidity, category** for bot learning.

Based on [warproxxx/poly_data](https://github.com/warproxxx/poly_data) with these additions:

- **Markets**: `resolved`, `resolution`, `liquidity`, `category` from Gamma API
- **Windows-compatible**: Uses pandas instead of tail/head/subprocess
- **Import to DB**: `scripts/import_poly_data_to_db.py` loads into polymarket-ai-v2 PostgreSQL

## Quick Start

```powershell
# 1. Fetch data (run from polymarket-ai-v2 root)
cd polymarket-ai-v2\poly_data
python update_all.py

# 2. Import to PostgreSQL
cd ..
python scripts/import_poly_data_to_db.py

# 3. Update elite status and retrain (via dashboard or CLI)
```

## Pipeline Stages

1. **update_markets** – Gamma API, extended with resolution/liquidity/category
2. **update_goldsky** – Goldsky subgraph orderFilledEvents
3. **process_live** – Raw orders → structured trades (market_id, price, side)

## Output Files

| File | Description |
|------|-------------|
| `markets.csv` | Markets with token IDs, resolution, liquidity, category |
| `missing_markets.csv` | Markets discovered from trades |
| `goldsky/orderFilled.csv` | Raw order-filled events |
| `processed/trades.csv` | Structured trades for import |

## First-Time Data

Download [archive snapshot](https://polydata-archive.s3.us-east-1.amazonaws.com/archive.tar.xz) to skip ~2 days of initial scraping.

```bash
python scripts/extract_archive.py path/to/archive.tar.xz
```

See `docs/ARCHIVE_STRATEGY.md` for archive age limits.

## Coordination

While Poly Data pull runs, backtest is blocked (lock: `poly_data/.pull_in_progress`). Stale locks (>2h) auto-clean.

## Limits

- **POLY_DATA_MAX_MARKETS** – Stop after fetching this many new markets (default: 0 = no limit). Set to e.g. `5000` for incremental runs from the dashboard.
- **POLY_DATA_MAX_MISSING_TOKENS** – Cap for fetching missing markets per run (default: 300).

# What You Need to Run Live Today

Checklist of passwords, .env, logins, and programs required to run the system in live mode.

---

## 1. Programs / Services

| Program | Required? | Notes |
|--------|-----------|--------|
| **Python 3.11+** | Yes | `python --version` |
| **PostgreSQL** | Yes (for live/multi-bot) | With **TimescaleDB** extension. DB must be up so KillSwitch and TradeCoordinator work. |
| **Redis** | No | Optional cache. Set `REDIS_ENABLED=false` in .env to run without it. |
| **Streamlit** | No (for UI only) | For dashboard: `streamlit run ui/dashboard.py` |

Install deps:
```bash
cd polymarket-ai-v2
pip install -r requirements.txt
```

---

## 2. .env File

Copy the template and fill in real values:

```bash
copy .env.example .env
# then edit .env
```

### Required for live trading and coordination

| Variable | Description | Example |
|----------|-------------|---------|
| **DATABASE_URL** | Full Postgres connection string (async). | `postgresql+asyncpg://USER:PASSWORD@HOST:5432/DATABASE` |
| **SUPABASE_DB_URL** | Alternative; used if DATABASE_URL is not set. | Same format as above. |
| **PRIVATE_KEY** | Wallet private key (64 hex chars, with or without `0x`). | `0x1234...` |
| **WALLET_ADDRESS** | Same wallet’s address (for display/logging). | `0x...` |

- **Database password**: goes inside `DATABASE_URL` (or `SUPABASE_DB_URL`), e.g. `postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/polymarket`.
- **No separate “Polymarket login”**: live trading uses the wallet only (signing with `PRIVATE_KEY`). No Polymarket API key.

### Optional but recommended for live

| Variable | Description | Default |
|----------|-------------|---------|
| **BOT_ID** | Process identity for KillSwitch/TradeCoordinator. | `default` |
| **REDIS_ENABLED** | Use Redis for cache. | `true` |
| **REDIS_HOST** | Redis host. | `localhost` |
| **REDIS_PORT** | Redis port. | `6379` |

### Optional: API / proxy / VPN

- **POLYMARKET_*** URLs: default to official Polymarket endpoints; only change if you use a proxy or custom backend.
- **POLYMARKET_PROXY** (or **HTTP_PROXY** / **HTTPS_PROXY**): set if you need a proxy/VPN to reach Polymarket (e.g. `http://127.0.0.1:7890`).
- **SKIP_VPN_FOR_INGESTION**: set to `true` only if you want to allow ingestion without proxy/VPN (trading still uses proxy if set).

### Optional: external signals / ML

- **OPENAI_API_KEY**, **NEWSAPI_KEY**, **TWITTER_BEARER_TOKEN**, **REDDIT_CLIENT_ID**, **REDDIT_CLIENT_SECRET**: only if you enable those signal sources.
- **POLYGON_RPC**, **QUICKNODE_HTTP**, **ALCHEMY_HTTP**, **BLASTAPI_HTTP**: only if you use blockchain price or chain-dependent features.

---

## 3. Passwords and Secrets (summary)

| What | Where | Notes |
|------|--------|--------|
| **Postgres password** | Inside `DATABASE_URL` (or `SUPABASE_DB_URL`) | Strong password; DB must be reachable. |
| **Wallet private key** | `PRIVATE_KEY` in .env | 64 hex chars. Never commit .env. |
| **Wallet address** | `WALLET_ADDRESS` in .env | Same wallet as the key; for display. |

No Polymarket username/password: everything is wallet-based.

---

## 4. Logins / Accounts

- **Polymarket**: no account login in the app. You need a funded wallet; the app signs orders with `PRIVATE_KEY`.
- **Supabase** (if used): you get a connection string from the Supabase project (Database → Connection string). Put it in `DATABASE_URL` or `SUPABASE_DB_URL` (use the “URI” with password).
- **Redis**: no auth in default config; if your Redis has a password, you’d need to add it to the connection (code may need a small change to support a Redis URL with password).

---

## 5. Running “live” today

1. **Postgres**: running, DB created, TimescaleDB extension installed; password set and reflected in `DATABASE_URL` (or `SUPABASE_DB_URL`).
2. **.env**: at least `DATABASE_URL`, `PRIVATE_KEY`, `WALLET_ADDRESS`; optionally Redis and proxy.
3. **Optional**: start Redis if `REDIS_ENABLED=true`.
4. **Optional**: VPN or proxy if Polymarket is blocked in your region; set `POLYMARKET_PROXY` (or HTTP_PROXY/HTTPS_PROXY).
5. Validate then run:
   ```bash
   python validate.py
   python main.py
   ```
   Or UI: `streamlit run ui/dashboard.py` then initialize and start from the dashboard.

---

## 6. Without live trading (view-only / paper)

- Omit **PRIVATE_KEY** (and optionally **WALLET_ADDRESS**). Execution engine runs in read-only mode (no real orders).
- **DATABASE_URL** still required if you want coordination (KillSwitch/TradeCoordinator), learning, and persistence. If DB is down or not set, coordination is skipped and orders could still be placed if a key is added later; for production, DB must be up and set.

---

## 7. Quick reference: minimal .env for live

```env
# Database (required for live/multi-bot)
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_DB_PASSWORD@localhost:5432/polymarket

# Wallet (required for real orders)
PRIVATE_KEY=0xYOUR_64_HEX_PRIVATE_KEY
WALLET_ADDRESS=0xYOUR_WALLET_ADDRESS

# Optional: Redis (set REDIS_ENABLED=false to disable)
REDIS_ENABLED=true
REDIS_HOST=localhost
REDIS_PORT=6379

# Optional: proxy if needed for Polymarket
# POLYMARKET_PROXY=http://127.0.0.1:7890
```

Replace `YOUR_DB_PASSWORD`, `YOUR_64_HEX_PRIVATE_KEY`, and `YOUR_WALLET_ADDRESS` with real values; adjust host/port/db name if not local.

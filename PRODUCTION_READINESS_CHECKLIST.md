# PRODUCTION READINESS CHECKLIST
## Polymarket AI V2 — Full Power 15-Bot Capacity
### Audit Date: 2026-03-03 | VPS: Ubuntu-2 (54.154.227.247) | 8GB RAM, 2 vCPU

---

## [1] COMPUTE & HARDWARE

### 1.1 — CPU Capacity
**Status: NEEDS UPGRADE**
What I need from you: Upgrade VPS to 4 vCPU (minimum) or 8 vCPU (recommended).
Why it blocks full power: Load average is 4.8–6.0 on a 2-vCPU machine. CPU is saturated at 240%+ utilization. 15 bots + WS + ingestion + ML retraining cannot run simultaneously on 2 cores. Current service consumes 79% of total CPU in steady state.

### 1.2 — RAM
**Status: READY**
7.6 GB total, 6.2 GB available. Service uses 435 MB (peak 476 MB). PostgreSQL, Redis, and OS use ~1.4 GB. Adequate headroom for 15 bots.

### 1.3 — Disk
**Status: READY**
154 GB total, 43 GB used (28%). DB is 11 GB. Growth rate ~200 MB/day at current ingestion. ~6 months runway before 80%.

### 1.4 — File Descriptors
**Status: READY**
LimitNOFILE=65536 in systemd unit. Adequate for 15 bots + WS connections + DB pool.

### 1.5 — TCP Keepalive
**Status: READY**
tcp_keepalive_time=30s, intvl=10s, probes=3. Applied in /etc/sysctl.conf.

### 1.6 — Network Latency
**Status: READY**
Gamma API: 332ms, CLOB API: 400ms. 0.0% packet loss (MTR 100 probes). Dublin→Cloudflare direct.

### 1.7 — Systemd Unit
**Status: READY**
Security hardened: ProtectSystem=strict, NoNewPrivileges=true, PrivateTmp=true. MemoryMax=6000M, CPUQuota=180%. Restart=always, RestartSec=10.

---

## [2] DATABASE (PostgreSQL)

### 2.1 — shared_buffers
**Status: NEEDS UPGRADE**
What I need from you: Approve tuning shared_buffers from 128 MB → 2 GB.
Why it blocks full power: 128 MB is 1.7% of RAM. PostgreSQL recommends 25% (2 GB). All 11.8M market_prices rows hit disk instead of cache. Every bot scan triggers I/O.

### 2.2 — effective_cache_size
**Status: NEEDS UPGRADE**
What I need from you: Approve tuning effective_cache_size from 512 MB → 5 GB.
Why it blocks full power: Tells query planner how much OS page cache is available. 512 MB causes the planner to avoid index scans on large tables, choosing seq scans instead.

### 2.3 — max_connections
**Status: NEEDS VERIFICATION**
Current: max_connections=40. App pool: DB_POOL_SIZE=25 + DB_MAX_OVERFLOW=5 = 30. Leaves 10 for admin/monitoring.
What I need from you: If upgrading to 4+ vCPU, increase to max_connections=100 and DB_POOL_SIZE=40.

### 2.4 — Connection Health
**Status: NEEDS VERIFICATION**
Current: 34 connections (8 active on LWLock, 1 idle-in-transaction, 13 idle). LWLock contention suggests buffer pressure from undersized shared_buffers.
What I need from you: Monitor after shared_buffers upgrade. LWLock contention should resolve.

### 2.5 — decision_events Table Bloat
**Status: NEEDS UPGRADE**
What I need from you: Approve VACUUM FULL on decision_events (1028 MB for 2.77M rows of JSONB).
Why it blocks full power: 1 GB table with heavy JSONB causes lock contention during concurrent writes. VACUUM FULL will reclaim space and eliminate bloat.

### 2.6 — Autovacuum Tuning
**Status: READY**
scale_factor=0.02 applied to hot tables (trades, market_prices, prediction_log). Dead tuple counts healthy.

### 2.7 — Indexes
**Status: READY**
Migration 025 applied: 6 unused indexes dropped, composite index on prediction_log eliminates 37M seq reads (now 0.254ms index-only scan).

### 2.8 — Duplicate .env Keys
**Status: NEEDS VERIFICATION**
What I need from you: VPS .env has duplicate ENSEMBLE_SIDE_BIAS_THRESHOLD (0.75 and 0.65). python-dotenv uses first-wins. Decide which value to keep and remove the duplicate.

### 2.9 — .env File Permissions
**Status: NEEDS UPGRADE**
What I need from you: Approve `chmod 600 /opt/polymarket-ai-v2/.env` (currently 0644 — world-readable).
Why it blocks full power: When CLOB keys and PRIVATE_KEY are added, world-readable .env exposes credentials to any user on the system.

---

## [3] REDIS

### 3.1 — Redis Health
**Status: READY**
Version 7.0.15. Memory: 148 MB / 2 GB limit (7.4%). 20 clients. allkeys-lru eviction. Healthy.

---

## [4] EXECUTION LAYER

### 4.1 — Order Gateway
**Status: READY**
Unified 7-layer guard stack: kill switch → risk limits → liquidity → cascade → coordinator → adverse selection → orderbook. Paper and live paths share identical guards and latency instrumentation.

### 4.2 — Paper = Live Instrumentation
**Status: READY**
Latency logging unified (Session 43): "Order latency" + "Order latency breakdown" (risk_ms, coord_ms, exec_ms, total_ms) identical for both paths. Prometheus histograms feed both. Alert threshold applied to both.

### 4.3 — CLOB API Keys
**Status: NEEDS INPUT**
What I need from you: Generate and provide CLOB_API_KEY, CLOB_SECRET, CLOB_PASSPHRASE from your Polymarket account.
Why it blocks full power: Without these, ExecutionEngine is in read-only mode. No live orders can be placed.

### 4.4 — Wallet / Private Key
**Status: NEEDS INPUT**
What I need from you: Generate or provide an Ethereum PRIVATE_KEY for order signing, and the corresponding WALLET_ADDRESS.
Why it blocks full power: py-clob-client needs the private key to sign orders. Without it, the live execution path returns "No wallet configured."

### 4.5 — USDC Approval
**Status: NEEDS VERIFICATION**
What I need from you: After wallet is funded, run with PREAPPROVE_ON_STARTUP=true to approve MAX_UINT256 USDC spending on Polymarket contract. Verify approval succeeded in logs.

### 4.6 — Kill Switch
**Status: READY**
system_config table exists. kill_switch_events table exists. KillSwitch reads from system_config with 30s TTL cache. Multi-kill-switch supports bot-level + portfolio-level + system-level halts.

### 4.7 — Circuit Breakers
**Status: READY**
Two implementations: PolymarketClient (API) + ExecutionEngine (CLOB). Both: 5-failure threshold, 60s timeout, HALF_OPEN probe. 4xx errors don't trip breaker.

### 4.8 — Drawdown Controller
**Status: READY**
Integrated at OrderGateway. Daily halt at 5%, weekly at 15%. Graduated reduction: 1.0→0.5→0.25→0.0. SELL orders always allowed (emergency exits).

---

## [5] RISK MANAGEMENT

### 5.1 — Risk Manager Bot Flags
**Status: READY**
_bot_enabled_flags covers all 15 bots (Session 43 fix). Kelly fraction calculation uses correct bot count.

### 5.2 — Phase Bet Caps
**Status: READY**
paper=$15, learning=$20, graduated=$200, production=$1000. Enforced in OrderGateway.

### 5.3 — TRADING_PHASE in .env
**Status: NEEDS INPUT**
What I need from you: Add TRADING_PHASE=paper to VPS .env (currently missing — uses code default).
Why it blocks full power: Explicit phase declaration ensures bet caps are applied correctly. Without it, defaults to "paper" from code but should be explicit.

### 5.4 — Canary Deployment
**Status: READY**
4-stage graduated capital scaling (5%→25%→50%→100%) controlled by CANARY_STAGE env var (default 0=off).

### 5.5 — NegRisk Defense
**Status: READY**
Blocks BUY on multi-outcome NegRisk markets (tokens unsellable). Active in OrderGateway.

---

## [6] ML & PREDICTION

### 6.1 — Model Cache
**Status: READY**
data/model_cache.pkl exists (4.2 MB, updated Mar 2). Auto-rebuilds on missing (slower startup ~30-120s).

### 6.2 — RL Q-Table
**Status: READY**
data/rl_qtable.pkl exists (16 KB). RL trade timing agent uses it (disabled by default).

### 6.3 — Platt Scaling
**Status: READY (gated)**
Requires 200+ resolved predictions. Current: 242 resolved. PLATT_SCALING_ENABLED=false by default. Can enable when ready.

### 6.4 — Phase Graduation
**Status: READY**
PhaseTracker evaluates every 24h. Paper→Learning requires: win_rate≥52%, predictions≥100, brier≤0.22. Current predictions: 242 (sufficient). Promotion is LOG ONLY — manual .env change required.

### 6.5 — RLVR Model
**Status: READY (optional)**
RLVR_ENABLED defaults to false. If enabled, requires HuggingFace model at RLVR_MODEL_PATH. Graceful fallback to API LLMs if missing.

---

## [7] MONITORING & ALERTING

### 7.1 — Prometheus Histograms
**Status: READY**
WS_SIGNAL_LATENCY + ORDER_PIPELINE_LATENCY histograms created (Session 43). MetricsCollector.record_trade() feeds both.

### 7.2 — Latency Alerts
**Status: READY**
WS signal latency alerts at >50ms (WS_SIGNAL_LATENCY_ALERT_MS). Order latency alerts at >5000ms (ORDER_LATENCY_ALERT_MS). Both active for paper and live.

### 7.3 — Alert Channels
**Status: NEEDS INPUT**
What I need from you: Configure at least ONE alert channel in VPS .env. Options:
  - SLACK_WEBHOOK=https://hooks.slack.com/...
  - DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
  - SMTP_HOST + SMTP_USER + SMTP_PASSWORD + ALERT_EMAIL_TO (email)
  - TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + TWILIO_FROM_NUMBER + ALERT_SMS_TO (SMS, critical only)
Why it blocks full power: All alert infrastructure is built (Slack/Discord/Email/SMS channels, rate limiting, severity routing). But no webhooks are configured. Critical events (kill switch, drawdown halt, connection loss) only appear in journald logs — no push notifications to operator.

### 7.4 — Log Rotation
**Status: READY**
journald handles log rotation via systemd. StandardOutput=journal, StandardError=journal.

### 7.5 — Unclosed Transport Warnings
**Status: NEEDS VERIFICATION**
What I need from you: Monitor after VPN code removal deployment. ResourceWarning: unclosed transport on fd=46,49,61 suggests WebSocket/HTTP connection leak. May resolve with proxy code removal (fewer connection objects).

---

## [8] WEBSOCKET & DATA

### 8.1 — WS Signal Latency
**Status: READY**
time.monotonic() captured at ws.recv(), threaded through dispatch chain to bot handler. Active on VPS (showing 74-1037ms range).

### 8.2 — WS Reconnection
**Status: READY**
Auto-reconnect with exponential backoff. Logs "WebSocket reconnected" on recovery. Observed working on VPS.

### 8.3 — Data Ingestion
**Status: READY**
19,176 markets ingested. 11.8M price records. 1,173K trades. Scheduler runs daily with configurable intervals.

### 8.4 — Unknown Categories
**Status: NEEDS VERIFICATION**
What I need from you: 8,311 markets have unknown category. These markets are tradeable but category-specific strategies (sports, weather, etc.) may miss them. Consider running category reclassification.

---

## [9] SECURITY

### 9.1 — SSH Hardening
**Status: READY**
PasswordAuthentication=no, PermitRootLogin=no. Key-only access.

### 9.2 — Firewall
**Status: READY**
UFW enabled: default deny incoming, SSH allowed, rate-limited.

### 9.3 — .env Permissions
**Status: NEEDS UPGRADE** (same as 2.9)
chmod 600 required before adding credentials.

### 9.4 — VPN/Proxy Code
**Status: READY**
All VPN/proxy code removed from codebase (Session 43). Direct connection via VPS Dublin IP. python-socks removed from requirements. 14 files cleaned. 1037/1037 tests pass.

### 9.5 — fail2ban
**Status: NEEDS VERIFICATION**
What I need from you: fail2ban v1.0.2 is incompatible with Python 3.13 (exits immediately). UFW rate limiting is active as alternative. Consider upgrading fail2ban when a compatible version is available, or accept UFW-only.

---

## [10] EXTERNAL API KEYS (for full bot coverage)

### 10.1 — SportsDataIO
**Status: NEEDS INPUT**
What I need from you: SPORTSDATAIO_API_KEY for sports bots (SportsBot, SportsLiveBot, SportsArbBot).
Why it blocks full power: 3 sports bots cannot operate without live sports data feeds.

### 10.2 — PandaScore
**Status: NEEDS INPUT**
What I need from you: PANDASCORE_API_KEY for esports bots (EsportsBot, EsportsLiveBot, EsportsSeriesBot).
Why it blocks full power: 3 esports bots cannot operate without live esports data feeds.

### 10.3 — Riot API
**Status: NEEDS INPUT (optional)**
What I need from you: RIOT_API_KEY for League of Legends live game state (EsportsLiveBot LoL markets).
Why it blocks full power: LoL live bot falls back to PandaScore if Riot API unavailable. Optional but improves signal quality.

### 10.4 — Political Data APIs
**Status: NEEDS INPUT (optional)**
What I need from you: VOTEHUB_API_KEY, CONGRESS_GOV_API_KEY, PROPUBLICA_API_KEY, COURTLISTENER_API_TOKEN for political prediction models.
Why it blocks full power: Political bots fall back to Polymarket-only data. External APIs improve prediction accuracy but are not required.

---

## [11] DEPLOYMENT

### 11.1 — VPN Code Removal Deployment
**Status: NEEDS DEPLOYMENT**
What I need from you: Approve deployment of VPN-removed code to VPS.
Why it blocks full power: VPS is running old code with VPN/proxy logic. New code removes all proxy overhead and is cleaner.

### 11.2 — PostgreSQL Tuning Deployment
**Status: NEEDS DEPLOYMENT**
What I need from you: Approve PG config changes (shared_buffers, effective_cache_size) and PG restart.
Why it blocks full power: Requires PostgreSQL restart which means brief downtime (~5s).

---

## SUMMARY MATRIX

| # | Item | Status | Blocks Live? |
|---|------|--------|-------------|
| 1.1 | CPU upgrade (2→4+ vCPU) | NEEDS UPGRADE | YES |
| 2.1 | shared_buffers (128MB→2GB) | NEEDS UPGRADE | YES |
| 2.2 | effective_cache_size (512MB→5GB) | NEEDS UPGRADE | YES |
| 2.5 | VACUUM FULL decision_events | NEEDS UPGRADE | NO (perf) |
| 2.8 | Duplicate ENSEMBLE_SIDE_BIAS_THRESHOLD | NEEDS VERIFICATION | NO |
| 2.9 | .env chmod 600 | NEEDS UPGRADE | YES (security) |
| 4.3 | CLOB API keys | NEEDS INPUT | YES |
| 4.4 | Wallet / Private Key | NEEDS INPUT | YES |
| 4.5 | USDC approval | NEEDS VERIFICATION | YES |
| 5.3 | TRADING_PHASE in .env | NEEDS INPUT | NO (default ok) |
| 7.3 | Alert channel webhooks | NEEDS INPUT | YES |
| 7.5 | Unclosed transport warnings | NEEDS VERIFICATION | NO |
| 8.4 | Unknown category markets | NEEDS VERIFICATION | NO |
| 9.5 | fail2ban | NEEDS VERIFICATION | NO |
| 10.1 | SportsDataIO API key | NEEDS INPUT | YES (3 bots) |
| 10.2 | PandaScore API key | NEEDS INPUT | YES (3 bots) |
| 10.3 | Riot API key | NEEDS INPUT | NO (optional) |
| 10.4 | Political API keys | NEEDS INPUT | NO (optional) |
| 11.1 | Deploy VPN-removed code | NEEDS DEPLOYMENT | YES |
| 11.2 | Deploy PG tuning | NEEDS DEPLOYMENT | YES |

### HARD BLOCKERS FOR FULL PRODUCTION (must resolve):
1. CPU upgrade to 4+ vCPU
2. PostgreSQL tuning (shared_buffers + effective_cache_size)
3. CLOB API keys + Wallet + USDC approval
4. Alert channel configuration
5. .env permissions hardening
6. Deploy updated code + PG config

### ITEMS I CAN DO RIGHT NOW (with your approval):
1. Deploy VPN-removed code to VPS
2. Apply PG tuning (shared_buffers=2GB, effective_cache_size=5GB, requires PG restart)
3. VACUUM FULL decision_events
4. Fix .env permissions (chmod 600)
5. Add TRADING_PHASE=paper to VPS .env
6. Remove duplicate ENSEMBLE_SIDE_BIAS_THRESHOLD

# KALSHI WS DAEMON — DEPLOY NOTES (NOT DEPLOYED; operator-gated)

Built 2026-07-25 per operator "do 200ms build now". Additive-only:
`maker_kalshi_quoter.py` untouched (deployed md5 `727ca7c5` unaffected).

## What it is
`kalshi_live/maker_kalshi_ws_daemon.py` + `kalshi_live/kalshi_ws_feed.py` +
`kalshi_live/test_ws_daemon.py` (32 tests; full suite 240+2xf green).

- **Stage A (on by default when the daemon runs):** WS book-move / own-fill
  triggers an immediate FULL `run_once()` cycle (every guard verbatim) +
  60s heartbeat. Reaction ≈ 2–4s (REST reads), vs 0–120s timer wait.
- **Stage B (`KALSHI_WS_HOT=1`, DEFAULT 0):** surgical ~200ms reprice from the
  WS mirror. REPRICE-ONLY invariant (a create without a same-side cancel is
  impossible → committed capital can only fall or stay between cold cycles).
  Preconditions: fresh ctx <90s · clean mirror · no STOP · no foreign writer ·
  budget · not dry_run. **⚠ DO NOT ARM Stage B without its own adversarial
  review (ship-discipline: new order-path code = review before live).**

## Live-verified facts baked in (07-25 smoke, read-only)
- WS endpoint: `wss://api.elections.kalshi.com/trade-api/ws/v2` (REST-host
  candidate refused; candidate list handles it).
- Dialect: snapshots `yes_dollars_fp`/`no_dollars_fp`; deltas
  `price_dollars`/`delta_fp`/`side`/`ts_ms`; **seq is GLOBAL per
  subscription** → gap = reconnect + fresh snapshots (all mirrors dirtied).
- Measured delivery jitter ≈ **1ms** (p50 vs p90 within 1ms; the constant
  ~1.45s offset is local clock skew, not latency).
- Mirror parity: mirror best bid/ask == REST book on live compute markets.

## Deploy (when operator authorizes — reverses nothing by itself)
```
# 1) copy the three files into /opt/pa2-maker-kalshi-live/ (md5-gate each)
# 2) venv needs websockets:  sudo -u <svcuser> /opt/pa2-maker-kalshi-live/venv/bin/pip install "websockets>=12"
# 3) REPLACE the timer with the service (never run both; flock only guards cycles, not cadence):
sudo systemctl disable --now polymarket-maker-kalshi-live.timer
# 4) unit: /etc/systemd/system/polymarket-maker-kalshi-ws.service
#    [Service]
#    WorkingDirectory=/opt/pa2-maker-kalshi-live
#    EnvironmentFile=/opt/pa2-maker-kalshi-live/live.env
#    ExecStart=/opt/pa2-maker-kalshi-live/venv/bin/python /opt/pa2-maker-kalshi-live/maker_kalshi_ws_daemon.py
#    Restart=always
#    RestartSec=5
sudo systemctl daemon-reload && sudo systemctl enable --now polymarket-maker-kalshi-ws.service
```
Rollback: stop the service, re-enable the timer. STOP file semantics unchanged
(daemon's cold cycle honors it; hot path hard-blocks on it).

## Watch after enabling (first hour)
`kalshi_live/ws_daemon_log.jsonl`: `cold_cycle` cadence + `hot_ctx:true`;
`hot_reprice` rows with `reaction_ms` (Stage B only); any `*_error` rows;
`foreign_writer` (means the timer is still alive — stop it).

## Open items before Stage B arming
1. Adversarial review of the hot path (reprice-only diff, standing-view drift,
   breaker recompute parity with run_once).
2. Fill-channel payload shape unverified (no fill occurred during smokes) —
   verify the first live fill row before trusting `on_fill`.
3. Kalshi WS docs for the `fill` channel name/params were not confirmed —
   subscribe may need `fills` on prod; check the subscribe ack when armed.

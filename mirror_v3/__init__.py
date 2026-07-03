"""MirrorBot v3 — clean-silo rebuild (MB_REBUILD_PLAN.md §5, operator 'go' 2026-07-02).

Five isolation layers from the poisoned v2 home:
  code     : this package NEVER imports bots/mirror_bot.py or bots/elite_watchlist.py.
             Shared base_engine imports only (verified green) + salvaged patterns
             copied in with their named bugs fixed.
  env      : boots ONLY from an explicit-allowlist env file (deploy/env.mirror3.example
             -> /opt/pa2-shared/.env.mirror3). env_guard refuses to start unless the
             safety trio is EXPLICITLY set — unset is a boot error, never a default.
  process  : own systemd unit (deploy/polymarket-mirror3.service); not a slot in the
             shared main.py fleet.
  identity : BOT_NAME = 'MirrorBotV3' — clean rows in positions/trade_events/bot_pnl;
             the old MirrorBot history stays queryable but never mixes in.
  capital  : paper-only. The strategy slot is EMPTY behind the acceptance gate
             (bots/mirror_backtest: precise-model pass required); flipping live is a
             separate operator action that must satisfy env_guard's live ack.
"""

BOT_NAME = "MirrorBotV3"

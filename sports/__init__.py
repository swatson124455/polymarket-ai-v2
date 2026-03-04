"""
Sports Betting Infrastructure Package.

Provides injury detection, market scanning, live game monitoring,
and adaptive Kelly sizing for sports prediction markets on
Polymarket, Kalshi, and blockchain venues.

Sub-packages:
  sports.data      — ORM tables, player registry, injury store
  sports.markets   — Kalshi client, market scanner, cross-platform arb
  sports.news      — Twitter, RSS, Reddit, Discord/Telegram monitors + NLP
  sports.live      — WebSocket game state and event detection
  sports.kelly     — Adaptive Kelly fraction and bankroll management
  sports.projections — (Phase 3) Baseline + ML projection adjustment
"""

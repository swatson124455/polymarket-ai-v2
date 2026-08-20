# SHADOW-BID SIMULATOR — band-only maker-execution measurement (pre-registered)

**Registered:** 2026-08-19 (operator "proceed"). Purpose: measure the TRUE
maker fill rate the 2026-08-19 snapshot study could not (its proxy floor was
47.6%; break-even for maker-vs-taker in the band needs ~74%).

## What it simulates
On every roster first-buy BUY signal whose **whale_price is in [0.65, 0.85)**,
post a shadow BID at exactly the whale's price. The bid rests until:
- **FILL:** any real trade prints at price <= bid on that token (from the RTDS
  firehose, all traders), or
- **EXPIRE:** 24h with no such print.

One bid per (trader, token) — mirrors the first-buy estimand. Both detection
paths (chain + RTDS) may register; the registry dedupes on the key.

## Known biases (fixed at registration, bracketing the truth)
- The fill rule "any print <= bid" is QUEUE-OPTIMISTIC: a real resting order
  might sit behind queue at that level and miss part of the print. This
  OVERSTATES fills — the exact opposite bias of the snapshot proxy
  (which UNDERSTATED at 47.6%). The two instruments bracket the true rate.
- No size modeling: a fill is all-or-nothing at the print price.
- 24h expiry is a design choice; the taker alternative fills instantly, so
  time-to-fill is recorded for a latency-adjusted comparison later.

## The registered decision hypothesis (from the 2026-08-19 study)
**True band fill rate >= 74% => maker beats taker in [0.65, 0.85).**
(Taker edge +0.0756 vs maker conditional +0.1022, n=131; rebates uncounted,
pro-maker.) The simulator's fill-rate estimate, bracketed with the snapshot
floor, decides chase-vs-post for the eventual live bot.

## Mechanics
- Sink: `/opt/pa2-shared/mirror3_bidsim.jsonl` — events `post` / `fill` /
  `expire`, each carrying token, trader, bid, whale price, timestamps, source.
- Enabled via `MIRROR3_BIDSIM=1` (explicit; absent = off, watcher unchanged).
- Restart-safe: open bids rehydrate from the sink (posts within 24h without a
  terminal event).
- Read-only vs the venue: no orders, paper $0, same shadow discipline.

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

---

## AMENDMENT 1 (2026-08-21, operator-directed: "resolve sim now then get proper data")

**Defect found on live forward data, not on theory.** At 35 posts / 30 fills /
2 expires the headline fill rate read 30/32 = 93.8%. The wait-time
distribution showed why that number was not measuring maker execution:

- **21 of 30 fills landed within 5 seconds; median wait 0.6s.**
- Fill print price was *exactly* the bid in 23 of 30.
- All 5 chain-sourced posts filled in <=0.6s.
- Only 6 of 30 waited >60s (13min / 13.9min / 48min / 3.3h / 10.2h / 13.9h).

**Root cause (code-verified).** `on_print` filled any open bid from any print
at/below the bid, with no knowledge of which trade the print belonged to. A
whale order matched against several makers arrives as several tape rows; row 1
registered the bid and rows 2..N of *the same transaction* filled it. The
existing single-row guard (`on_print` runs before `register` for a given row)
prevented only the literal trigger row, not the rest of its transaction.

This is **not** the queue-optimism bias registered at launch. Queue optimism is
"our resting order might sit behind others at the same level." This was an
attribution error: counting the very order we reacted to as our counterparty.

**Correction (surgical, one rule).** A bid records the `trigger_tx` of the
whale fill that prompted it; `on_print` will not fill a bid from that same
transaction. Nothing else about the estimand moves — queue optimism is
deliberately RETAINED, so this instrument still brackets the 2026-07-19
snapshot proxy (47.6% floor) from above.

**What was NOT changed, and why.** A resting BID is economically fillable only
by SELL-side aggression, and the current rule ignores side. Measured on the
2026-07-30 raw firehose capture (400,000 rows): BUY 346,225 / SELL 53,775, and
only 30,839 of 241,869 (token,ts,price) groups carry both sides. A sell-only
rule would therefore trade a known bias for an unquantified feed-asymmetry
bias. Rather than guess, every fill event now records `fill_tx`, `fill_trader`
and `fill_side`, so the side question is answerable from forward data. **This
remains an OPEN question and an operator decision — it is not settled.**

**Epoch reset — the old sample is not poolable.** The fill rule changed, so
pre-amendment events measure a different quantity. Records before the
amendment epoch are PARKED (kept, never pooled, never quoted as fill rate):

- **Amendment epoch: 2026-08-21T14:05:00Z** (deploy/restart time, recorded at
  cutover).
- Parked sample: `mirror3_bidsim.jsonl.pre-amend1-20260821` (35 posts /
  30 fills / 2 expires).
- The ~100-resolved-bid tripwire **restarts from zero** on the new sink.

**Registered decision hypothesis is UNCHANGED:** true band fill rate >= 74%
=> maker beats taker in [0.65, 0.85). The forward comparison is still forward
taker edge vs forward fill-rate x maker edge, per FORWARD DATA ONLY.

## AMENDMENT 2 (2026-08-25) - FILL-SIDE RULING (operator: "go with rec 3")
The chase-vs-post decision reads TWO fill rates, both from chain truth:
- STRICT (headline): taker-SELL aggression only, classified per fill_tx
  receipt (scripts/bidsim_classify_fills.py) - the fills a resting bid
  could genuinely receive.
- BRACKET (upper): the charter any-print rate.
DECISION RULE: maker (post) wins ONLY if BOTH rates clear the 74%
break-even bar; otherwise the default is chase (taker). The ~100-resolved
proposal reports both plus the label-coverage caveat (two-stage: fill-rate
firm first, cond-edge as markets resolve).

#!/usr/bin/env python3
"""ROLLING MARKET SCORE CACHE — rank markets on what they actually pay, not on pot size.

THE PROBLEM THIS SOLVES
Selection ranks by `usd_day`, the reward pool, and pool ALONE is the wrong key.

To be precise, because it is easy to state this wrongly: **the pool matters directly.** It is a
LINEAR MULTIPLIER in the reward — `reward = share x pool` — so doubling the pool doubles the money,
all else equal. It is not noise and must never be dropped from the estimate.

What makes it a poor RANK KEY is that it is only one of the two terms, and across real markets it
is the one that varies LESS. Measured over 30 series / 165 book-side depth readings
(venue_scan.json, 2026-07-25): pool spans 6x ($1,750 -> $10,470/day); rival qualifying depth, which
sets `share`, spans 71,330x (1 -> 71,330). Sorting on the 6x term while ignoring the 71,330x term
gets the order wrong. KXFUNDRAISING, the biggest pool on the venue at $10,470/day, models to
$5.65/day because it is crowded; KXVOGUECOVER at $1,800/day models to $42.03/day because it is
nearly empty.

So the rank key here is the PRODUCT — `capture = share x pool` — which uses the pool at full
weight. Not depth alone, and not pool alone.

THE CHICKEN-AND-EGG
Capture needs the orderbook; ranking happens before the books are read. And the read budget only
allows ~200 of 2,271 active programs per cycle (0.55s spacing x 200 reads = 110s of a 120s cycle),
so the venue cannot be swept in one pass.

THE FIX
Score markets from the books we ALREADY read each cycle and remember the scores. Ranking then uses
measured capture from previous cycles, and an EXPLORATION QUOTA guarantees unscored markets keep
getting sampled so the sweep completes over many cycles instead of never starting.

DECAY, not deletion: a score ages out toward "unknown" rather than being trusted forever or thrown
away. Stale scores must not pin the bot to yesterday's winners.

SWING PENALTY: the operator's requirement is to verify money is earned and not given back on price
swings. A market whose reference price moves a lot between cycles fills us adversely, so ref_move
(an EWMA of |change in reference| per cycle) discounts the score. It costs nothing to collect — the
reference price is already in every telemetry row.
"""
import json
import os
import time

SCHEMA = 1
HALF_LIFE_S = 3600.0        # a score decays to half its weight after an hour
EWMA_ALPHA = 0.3            # ref_move smoothing


def _now():
    return time.time()


def load(path):
    """Fail-OPEN to {} -> every market unscored -> ranking falls back to the legacy pool order."""
    try:
        with open(path) as fh:
            d = json.load(fh)
        if d.get("schema") != SCHEMA:
            return {}
        m = d.get("markets")
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def save(path, markets):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"schema": SCHEMA, "markets": markets}, fh, separators=(",", ":"))
    os.replace(tmp, path)


def update(markets, ticker, capture_usd_day, ref_yes, now=None):
    """Fold one observation into the cache. Pure dict mutation, no I/O — the caller owns the file
    so a scoring fault can never interrupt a trading cycle."""
    now = now if now is not None else _now()
    row = markets.get(ticker) or {}
    prev_ref = row.get("ref")
    move = 0.0
    if prev_ref is not None and ref_yes is not None:
        move = abs(float(ref_yes) - float(prev_ref))
    prev_move = row.get("ref_move")
    row["ref_move"] = (move if prev_move is None
                       else (EWMA_ALPHA * move + (1 - EWMA_ALPHA) * float(prev_move)))
    if ref_yes is not None:
        row["ref"] = float(ref_yes)
    row["capture"] = float(capture_usd_day or 0.0)
    row["ts"] = now
    row["n"] = int(row.get("n", 0)) + 1
    markets[ticker] = row
    return row


def score(markets, ticker, pool_usd_day, now=None, swing_penalty=1.0, unknown_bonus=1.0):
    """Rank key for one market. Higher is better.

    UNKNOWN markets score `pool * unknown_bonus` so they are neither buried nor blindly trusted —
    they still need to be sampled, and the exploration quota below guarantees it regardless.
    KNOWN markets score their measured capture, decayed by age and discounted by how much their
    reference price moves (swings are how a maker gives the rewards back)."""
    now = now if now is not None else _now()
    row = markets.get(ticker)
    if not row or row.get("ts") is None:
        return float(pool_usd_day or 0.0) * unknown_bonus, "unknown"
    age = max(0.0, now - float(row["ts"]))
    decay = 0.5 ** (age / HALF_LIFE_S)
    cap = float(row.get("capture") or 0.0)
    # blend toward the pool prior as the score ages out, so a stale winner cannot pin the bot
    blended = cap * decay + float(pool_usd_day or 0.0) * unknown_bonus * (1.0 - decay)
    penalty = 1.0 / (1.0 + swing_penalty * float(row.get("ref_move") or 0.0) * 100.0)
    return blended * penalty, "scored"


def rank(markets, rows, pool_key="usd_day", ticker_key="ticker", now=None,
         swing_penalty=1.0, unknown_bonus=1.0, explore=0):
    """Order `rows` best-first by score. `explore` reserves that many slots at the FRONT for the
    least-recently-seen unscored markets, so the venue keeps getting swept even while known-good
    markets dominate the ranking. Without it the bot converges on whatever it happened to read
    first and never discovers anything better."""
    now = now if now is not None else _now()
    scored = []
    for r in rows:
        s, kind = score(markets, r.get(ticker_key), r.get(pool_key), now,
                        swing_penalty, unknown_bonus)
        scored.append((s, kind, r))
    scored.sort(key=lambda x: (-x[0], str(x[2].get(ticker_key))))
    if explore <= 0:
        return [r for _, _, r in scored]
    unseen = [t for t in scored if t[1] == "unknown"]
    if not unseen:
        return [r for _, _, r in scored]
    picked, seen_ids = [], set()
    for t in unseen[:explore]:
        picked.append(t[2])
        seen_ids.add(id(t[2]))
    rest = [r for _, _, r in scored if id(r) not in seen_ids]
    return picked + rest

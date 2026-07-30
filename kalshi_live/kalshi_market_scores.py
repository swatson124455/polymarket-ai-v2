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
# A score older than this is STALE and becomes eligible for an exploration slot again. Without it,
# a market scored once is never re-sampled: `score()` would keep calling it "scored" forever, so it
# could only crawl back through decay toward its pool prior. A cheap market that was crowded an hour
# ago and is empty now is exactly the opportunity we want, and it would have been invisible.
# Books move; a score is a snapshot, not a verdict.
STALE_S = 1800.0


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


# audit batch 3 (J4, operator-approved 2026-07-29): the cache accumulates one row per market
# ever scored and never forgets — thousands of rotating weekly programs = unbounded file and
# memory growth. A row past EVICT_AGE_S is long past STALE_S (it already scores as pool-prior),
# so dropping it changes no ranking outcome; the count bound is a backstop for pathological
# churn. Mutates in place, returns evicted count; caller persists via save() as usual.
EVICT_AGE_S = 7 * 86400.0
EVICT_MAX_ROWS = 8192


def _obs_ts(r):
    """A row's LAST OBSERVATION time — actual (ts) or prospective (pts), whichever is newer.
    Sweeper-only rows have pts but no ts; evicting them on the missing ts would delete every
    sweep measurement each cycle (sweeper root-fix 2026-07-30). None when never observed."""
    ts, pts = r.get("ts"), r.get("pts")
    if ts is None and pts is None:
        return None
    return max(float(ts or 0.0), float(pts or 0.0))


def evict(markets, now=None, max_age_s=EVICT_AGE_S, max_rows=EVICT_MAX_ROWS):
    now = now if now is not None else _now()
    dead = [t for t, r in markets.items()
            if _obs_ts(r) is None or (now - _obs_ts(r)) > max_age_s]
    for t in dead:
        del markets[t]
    if len(markets) > max_rows:
        oldest = sorted(markets, key=lambda t: _obs_ts(markets[t]) or 0.0)
        for t in oldest[:len(markets) - max_rows]:
            del markets[t]
            dead.append(t)
    return len(dead)


# ref_move is PER-CYCLE volatility; an observation after a long gap measures DRIFT, not
# volatility (operator slate item G, 2026-07-29: a market read every 3h got one huge "move"
# and was penalized for having been IGNORED, not for swinging). Gaps beyond this skip the
# fold — the reference still updates, the EWMA just doesn't eat the drift.
SWING_MAX_GAP_S = 600.0


def update(markets, ticker, capture_usd_day, ref_yes, now=None):
    """Fold one observation into the cache. Pure dict mutation, no I/O — the caller owns the file
    so a scoring fault can never interrupt a trading cycle."""
    now = now if now is not None else _now()
    row = markets.get(ticker) or {}
    prev_ref = row.get("ref")
    prev_ts = row.get("ts")
    gap_ok = prev_ts is None or (now - float(prev_ts)) <= SWING_MAX_GAP_S
    move = 0.0
    if prev_ref is not None and ref_yes is not None:
        move = abs(float(ref_yes) - float(prev_ref))
    prev_move = row.get("ref_move")
    if gap_ok:
        row["ref_move"] = (move if prev_move is None
                           else (EWMA_ALPHA * move + (1 - EWMA_ALPHA) * float(prev_move)))
    if ref_yes is not None:
        row["ref"] = float(ref_yes)
    row["capture"] = float(capture_usd_day or 0.0)
    row["ts"] = now
    row["n"] = int(row.get("n", 0)) + 1
    markets[ticker] = row
    return row


def update_prospective(markets, ticker, pcap_usd_day, ref_yes, now=None):
    """Fold one SWEEPER observation into the cache (freshness root-fix 2026-07-30, Phase 1).

    Prospective capture is a MODEL (the M7 join-at-reference walk; over-predicts 2-6x vs
    actual) — so it lives in its OWN keys (pcap/pref/pts/pn) and this function NEVER touches
    the actual-measurement keys (capture/ts/ref/ref_move/n). Nothing in rank()/score()
    consumes pcap yet: wiring it into live ranking is Phase 3 (haircut + age cutoff in one
    change, operator-gated). Until then these rows are data collection only."""
    now = now if now is not None else _now()
    row = markets.get(ticker) or {}
    if ref_yes is not None:
        row["pref"] = float(ref_yes)
    row["pcap"] = float(pcap_usd_day or 0.0)
    row["pts"] = now
    row["pn"] = int(row.get("pn", 0)) + 1
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
    if age >= STALE_S:
        # Old enough that the book has probably moved. Report it as STALE so the exploration quota
        # picks it up again — a market that was crowded an hour ago may be empty now.
        return float(pool_usd_day or 0.0) * unknown_bonus, "stale"
    decay = 0.5 ** (age / HALF_LIFE_S)
    cap = float(row.get("capture") or 0.0)
    # blend toward the pool prior as the score ages out, so a stale winner cannot pin the bot
    blended = cap * decay + float(pool_usd_day or 0.0) * unknown_bonus * (1.0 - decay)
    penalty = 1.0 / (1.0 + swing_penalty * float(row.get("ref_move") or 0.0) * 100.0)
    return blended * penalty, "scored"


def rank(markets, rows, pool_key="usd_day", ticker_key="ticker", now=None,
         swing_penalty=1.0, unknown_bonus=1.0, explore=0,
         incumbents=None, incumbency_bonus=0.0):
    """Order `rows` best-first by score. `explore` reserves that many slots at the FRONT for the
    least-recently-seen unscored markets, so the venue keeps getting swept even while known-good
    markets dominate the ranking. Without it the bot converges on whatever it happened to read
    first and never discovers anything better."""
    now = now if now is not None else _now()
    scored = []
    for r in rows:
        s, kind = score(markets, r.get(ticker_key), r.get(pool_key), now,
                        swing_penalty, unknown_bonus)
        # INCUMBENCY (operator slate item A, 2026-07-29): a market we are currently resting in
        # holds an ASSET — queue position built by sitting, destroyed on exit — so a challenger
        # must beat it by the bonus margin, not merely tie. Sunk LOSSES buy no loyalty (the
        # loss governor handles those); only presence does. 0.0 = provable no-op.
        if incumbents and incumbency_bonus > 0 and r.get(ticker_key) in incumbents:
            s *= (1.0 + incumbency_bonus)
        scored.append((s, kind, r))
    scored.sort(key=lambda x: (-x[0], str(x[2].get(ticker_key))))
    if explore <= 0:
        return [r for _, _, r in scored]
    # Exploration covers BOTH never-seen and STALE markets. Never-seen first (we know nothing at
    # all about them), then the oldest stale ones — a market whose book has had time to change is
    # a genuine re-look, not a re-run. Restricting this to never-seen would mean a market is judged
    # once and then effectively retired.
    unseen = [t for t in scored if t[1] == "unknown"]
    stale = sorted((t for t in scored if t[1] == "stale"),
                   key=lambda t: float((markets.get(t[2].get(ticker_key)) or {}).get("ts") or 0.0))
    pool_of_candidates = unseen + stale
    if not pool_of_candidates:
        return [r for _, _, r in scored]
    picked, seen_ids = [], set()
    for t in pool_of_candidates[:explore]:
        t[2]["explore"] = True      # mark the sampling picks: probe-sizing (item E) keys on this
        picked.append(t[2])
        seen_ids.add(id(t[2]))
    rest = [r for _, _, r in scored if id(r) not in seen_ids]
    return picked + rest

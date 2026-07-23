#!/usr/bin/env python3
"""Kalshi Maker QUOTER — the quoting engine, PLAN-ONLY by default (dry-run client).

Build-ahead doctrine (operator 2026-07-18): the full machine exists now; data
tunes the dials later. Nothing here can trade until the operator provides
credentials AND sets the live-arming phrase (see maker_kalshi_client.py's
three-lock safety model). In dry_run the engine runs the COMPLETE cycle —
footprint, quote plan, gates, order diff — and logs every order it WOULD have
placed to plans-YYYYMMDD.jsonl. That log is the dress rehearsal: it measures
quote churn (order-ops/cycle vs rate-tier budgets) and wind-down behavior on
real market data before a single contract is at risk.

Cycle (timer-driven, default 10-min):
  1. Fetch active liquidity programs (public) -> select footprint.
  2. Per market: fetch book (public), compute desired two-sided quotes
     (join at reference; sizes from config) with GATES:
       - wind_down: pull quotes when program (or market close) ends within
         WIND_DOWN_MIN minutes  [calibrate from readout data later]
       - void_activate: on void markets, size up to Target (capped by
         MAX_ACTIVATE_CAPITAL per market)
       - spread_sanity: never quote a side whose reference implies paying
         more than MAX_PRICE_DOLLARS
       - inventory: per-market exposure cap enforced against the ledger
  3. Diff desired quotes vs standing orders -> cancel/create ops.
  4. Execute via KalshiOrderClient (dry_run: intents logged; demo/live: sent,
     batch-chunked, write-budget-metered).

Stop:   sudo touch <dir>/STOP
Kill:   sudo systemctl disable --now polymarket-maker-kalshi-quoter.timer
"""
import json
import math
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_client import KalshiOrderClient, API_ROOT, PROD_BASE  # noqa: E402

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
STOP_FILE = os.path.join(DATA_DIR, "STOP")
STATE_FILE = os.path.join(DATA_DIR, "quoter_state.json")
LOCK_FILE = os.path.join(DATA_DIR, "quoter.lock")


def _acquire_lock():
    """Single-instance guard (review C17): stops a manual run from overlapping the timer cycle and
    double-placing the book past the capital cap (two processes each pass the process-local cap
    check on the same standing snapshot). Linux flock; returns None (no-op) where fcntl is absent
    (Windows/test host) — systemd already serializes the timer unit, so the lock only needs to
    catch an operator's concurrent manual run on the VPS. Returns the held fd, None, or False."""
    try:
        import fcntl
    except ImportError:
        return None
    fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return False
    return fd


def _release_lock(fd):
    if fd:
        try:
            fd.close()                              # closing the fd releases the flock
        except Exception:
            pass

# ---- config (calibration dials — readouts tune these; env-overridable) ----
def _envi(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


FOOTPRINT_TOP = _envi("KALSHI_FOOTPRINT_TOP", 60)   # markets quoted per cycle
PER_SERIES_CAP = _envi("KALSHI_PER_SERIES_CAP", 10)
JOIN_SIZE = _envi("KALSHI_JOIN_SIZE", 100)          # contracts/side on non-void markets
# REWARDS ARE PAID FOR QUOTES ON THE BOOK, NOT INVENTORY HELD. So BOTH sides must stay live
# every cycle — the throttle SHRINKS the accumulating side but never pulls it to zero (that
# would kill the reward on that side). This is the floor it shrinks toward.
MIN_QUOTE_CT = _envi("KALSHI_MIN_QUOTE_CT", 2)      # never quote a live side below this
MAX_ACTIVATE_CAPITAL = _envf("KALSHI_MAX_ACTIVATE_CAPITAL", 150.0)  # $/void market
MAX_MARKET_CAPITAL = _envf("KALSHI_MAX_MARKET_CAPITAL", 250.0)  # $ cap per market (both sides)
MAX_TOTAL_CAPITAL = _envf("KALSHI_MAX_TOTAL_CAPITAL", 10000.0)  # $ cap on the whole resting book
MAX_PRICE_DOLLARS = _envf("KALSHI_MAX_PRICE_DOLLARS", 0.97)  # never rest a bid above this
MIN_PRICE_DOLLARS = _envf("KALSHI_MIN_PRICE_DOLLARS", 0.01)  # never rest a bid at/below this
WIND_DOWN_MIN = _envi("KALSHI_WIND_DOWN_MIN", 45)   # pull quotes N min before end
WRITE_BUDGET_PER_CYCLE = _envi("KALSHI_WRITE_BUDGET", 400)  # order-ops ceiling/cycle
JOIN_ALWAYS = os.environ.get("KALSHI_JOIN_ALWAYS") == "1"   # drill switch (default off)
# series allowlist: if set, ONLY quote markets whose series (ticker before the first
# '-') is listed. The pilot scopes to the weather/temp slice; empty = no filter (legacy).
SERIES_ALLOW = [s for s in os.environ.get("KALSHI_SERIES_ALLOW", "").split(",") if s.strip()]
# --- DELTA-NEUTRALITY (inventory control) — the core maker mandate ---
# TWO signals, distinct jobs:
#   inv         = OUR signed net on ONE ticker (+long yes / -long no) — what to UNWIND on that
#                 ticker (grow the reducing side at reference; its fill passively flattens at
#                 $0 maker fee). Tagged 'unwind' -> exempt from every capital/budget gate.
#   event_delta = aggregate signed net across ALL strikes of one nested-threshold event. Kalshi
#                 'above X' ladders are DIRECTIONALLY correlated, so the true directional risk is
#                 the event aggregate, not any single ticker. It drives the THROTTLE direction:
#                 above SOFT skew 1 tick inside + shrink the ACCUMULATING side; above HARD pull
#                 the accumulating side entirely (JOIN) / don't ADD via ACTIVATE (void-safe).
# (Kalshi's ladder margin-offset remains a separate CAPITAL concern handled by the $ caps.)
# --- THROTTLE PRICE STEP (reward economics, CFTC-verified 2026-07-22) ---
# The LIP score is DiscountFactor^(ticks from the Reference Price) x size, and our programs run
# discount_factor_bps=5000 => 0.50. So stepping the accumulating side 1 tick inside HALVES that
# side's credit — and can zero it outright, because the qualifying walk stops once the book
# reaches Target Size: a quote one tick back can fall out of the scored set entirely.
# Set 0 to keep the accumulating side AT reference (full credit) and throttle by SIZE alone.
# Default 1 = existing behaviour; this is a knob to A/B against the ledger, NOT a silent change.
THROTTLE_STEP_TICKS = _envi("KALSHI_THROTTLE_STEP_TICKS", 1)
# SMART-STEP (default OFF): skip the price step when the top level alone already meets Target
# Size, because the sandbox A/B measured the step ZEROING our credit in 12% of such snapshots.
# DEFAULT OFF ON PURPOSE: it puts the accumulating side back AT reference, which is exactly the
# placement the live A/B measured as ~tripling naked-inventory build. The reward gain is
# measured; the risk cost of THIS narrower version is NOT. Enable only to run that test.
THROTTLE_SMART = os.environ.get("KALSHI_THROTTLE_SMART") == "1"
# --- REDUCE-ONLY KEEPS BOTH SIDES (plug-in; instant off via env, no deploy needed) ---
# The CFTC Feb-2026 amendment EXCLUDES any snapshot without two-sided qualifying liquidity. The
# breaker's reduce-only mode drops the accumulating side, which makes us ONE-SIDED — so while
# the guard is engaged we earn exactly $0, including on the exit quote that is still resting.
# Observed live 07-23: all 3 markets one-sided during reduce-only, and the bot flips in and out
# of that state every few minutes, so a large share of the day earns nothing.
# With this ON the accumulating side is kept but SHRUNK TO THE FLOOR (MIN_QUOTE_CT) instead of
# pulled: the market stays qualifying (both quotes earn) while added risk is ~10x smaller than a
# normal join. NOT zero added risk — a floor quote can still fill; that is the trade.
# TAP OUT: set KALSHI_REDUCE_ONLY_KEEP_BOTH=0 in live.env and the next cycle reverts exactly.
REDUCE_ONLY_KEEP_BOTH = os.environ.get("KALSHI_REDUCE_ONLY_KEEP_BOTH", "1") == "1"
INV_SOFT_CT = _envf("KALSHI_INV_SOFT_CT", 30.0)
INV_HARD_CT = _envf("KALSHI_INV_HARD_CT", 80.0)
# INVARIANT (fix H): a single JOIN fill must not by itself breach the hard cap, or one fill
# overshoots the shapeable [SOFT,HARD] band before the next cycle can throttle. Clamp the
# resting join size to the hard cap so accumulation stays inside the gradient we control.
if INV_HARD_CT > 0 and JOIN_SIZE > int(INV_HARD_CT):
    JOIN_SIZE = int(INV_HARD_CT)
TICK = 0.01
# --- taker de-risk BACKSTOP (the ONLY place the bot pays a taker fee) ---
# Passive maker-unwind (above) is PRIMARY. This last-resort crosses the spread ONLY to
# GUARANTEE flat when passive can't: near settlement (carry no delta into resolution) or a
# hard inventory breach (passive not keeping up in a one-way drift). Tunable to OFF.
INV_TOLERANCE = _envf("KALSHI_INV_TOLERANCE", 3.0)          # < this many ct == "flat"
SETTLE_UNWIND_MIN = _envi("KALSHI_SETTLE_UNWIND_MIN", 30)   # taker-flatten if settlement within N min


def _clamp_settle_window(settle, wind):
    """COHERENCE (review C8): the settlement taker is the backstop for AFTER passive wind-down has
    pulled the two-sided quotes. If SETTLE_UNWIND_MIN > WIND_DOWN_MIN the taker window opens BEFORE
    wind-down begins, so a held position is taker-crossed while the market is still fully
    two-sided-quoting -> quote->fill->taker churn every cycle (the fire-sale pattern maker-first
    exists to avoid). Clamp so the taker can never fire before wind-down starts."""
    return min(settle, wind)


if SETTLE_UNWIND_MIN > WIND_DOWN_MIN:
    print(f"WARNING SETTLE_UNWIND_MIN({SETTLE_UNWIND_MIN}) > WIND_DOWN_MIN({WIND_DOWN_MIN}); "
          f"clamping settle-taker to {WIND_DOWN_MIN}min to preserve maker-first ordering")
    SETTLE_UNWIND_MIN = _clamp_settle_window(SETTLE_UNWIND_MIN, WIND_DOWN_MIN)
TAKER_FLATTEN = os.environ.get("KALSHI_TAKER_FLATTEN", "1") == "1"   # last-resort enabled (set 0 = never)
TAKER_MAX_MKTS = _envi("KALSHI_TAKER_MAX_MKTS", 8)         # cap taker-flattens per cycle (rate/cost guard)
# --- SETTLEMENT RAMP (audit HIGH-2): the settlement taker fires into the WORST liquidity, so
# the design goal is to BE SMALL at settlement, making that taker a rare backstop. Within
# RAMP_MIN of market end the ACCUMULATING quote sizes scale down linearly toward MIN_QUOTE_CT
# (reducing/unwind quotes are NOT ramped — de-risking gets easier, adding gets harder).
RAMP_MIN = _envi("KALSHI_RAMP_MIN", 180)                    # start shrinking N min before end
# The ABSOLUTE RAMP_MIN over-covers SHORT markets: a ~58-min hourly temp market is younger than
# 180 min for its whole life, so it would rest at the ramp floor (2-4 ct) from birth — near-zero
# reward on the flagship temp lane (review C13). Cap the effective ramp per-market at a FRACTION
# of THAT market's own program lifetime (computed in select_footprint) so the ramp only bites in
# the final stretch of short markets while long gas markets still get the full 180-min taper.
RAMP_LIFE_FRAC = _envf("KALSHI_RAMP_LIFE_FRAC", 0.5)
# --- LATE-LIFE ENTRY GATE (2026-07-22 live loss): a short-lived market near its end is a
# one-way informed market — the outcome is nearly known, resting bids get adversely lifted
# (hourly temp at 10:30pm: the day's max temp already happened). NEVER *enter* (footprint) a
# market past LATE_LIFE_FRAC of its OWN life; a held position on such a market unwinds via the
# strand path (reduce-only). For long-lived markets the fraction over-blocks, so the cutoff is
# capped at MAX_ENTRY_CUTOFF_MIN absolute (e.g. gas daily: no entry in the final 2h).
LATE_LIFE_FRAC = _envf("KALSHI_LATE_LIFE_FRAC", 0.6)
MAX_ENTRY_CUTOFF_MIN = _envf("KALSHI_MAX_ENTRY_CUTOFF_MIN", 120.0)
# --- UNWIND LOSS CAP (2026-07-22 live loss): the maker unwind re-priced at reference every
# cycle CHASES a trending market — buying the reducing side at the top realized the full
# adverse move (~50c/pair on DC vs the doctrine's 1-2c bleed). The reducing quote never rests
# at a price that would lock in more than MAX_UNWIND_LOSS per pair vs our cost basis; past
# that it rests AT the cap (deeper in the book) and the residual waits for the backstop.
# Bounded realized loss per pair, accepted trade: delta may ride longer.
MAX_UNWIND_LOSS = _envf("KALSHI_MAX_UNWIND_LOSS", 0.10)
# --- VELOCITY CIRCUIT BREAKER (2026-07-22 live loss): held-$ grew $0->$28 in 3 cycles of
# 'cycle ok' — adverse accumulation is invisible to plumbing telemetry. If held cost grows
# more than BREAKER_HELD_GROWTH_USD within BREAKER_WINDOW_S, the WHOLE book goes REDUCE-ONLY
# (only 'unwind' quotes survive; accumulating quotes are cancelled by the diff) until the
# growth condition clears. Generic backstop for every toxicity mode not yet imagined.
BREAKER_HELD_GROWTH_USD = _envf("KALSHI_BREAKER_HELD_GROWTH_USD", 20.0)
BREAKER_WINDOW_S = _envi("KALSHI_BREAKER_WINDOW_S", 600)
# --- HELD-$ CEILING (operator invariant 2026-07-22: "never lose more than the reward"): total
# unpaired held cost is the ONLY uncapped loss channel left (pairs capped at MAX_UNWIND_LOSS,
# taker off). Above HELD_MAX_USD the whole book goes REDUCE-ONLY until it drains — sized so the
# worst-case settlement loss on any day is about one day's measured rewards (~$20 receipt rate).
# LEVEL trigger, complementing the velocity trigger above; can overshoot by at most one cycle's
# small quote sizes before it bites.
HELD_MAX_USD = _envf("KALSHI_HELD_MAX_USD", 20.0)
# --- DAILY LOSS KILL (review 07-22: the "treadmill" gap): held-$ velocity/level breakers can't
# see CUMULATIVE realized losses — acquire ~$18/hour, settle at a loss, repeat, forever under
# both triggers with 'cycle ok'. Track equity (balance + held cost) vs the UTC-day start; a drop
# beyond DAILY_LOSS_HALT_USD writes the STOP sentinel (maker-first flatten + halt until the
# operator removes it). Completes the never-lose-more-than-the-rewards invariant's daily arm.
DAILY_LOSS_HALT_USD = _envf("KALSHI_DAILY_LOSS_HALT_USD", 20.0)
# --- config-coherence clamps (review 07-22): foot-gun envs must not silently disable trading
# or the gate ordering. LATE_LIFE_FRAC >= 1 would exclude every short-lived market entirely;
# MAX_ENTRY_CUTOFF below WIND_DOWN would violate the always-at-least-wind-down guarantee.
if not (0.0 <= LATE_LIFE_FRAC <= 0.9):
    print(f"WARNING LATE_LIFE_FRAC({LATE_LIFE_FRAC}) out of [0,0.9]; clamping")
    LATE_LIFE_FRAC = min(max(LATE_LIFE_FRAC, 0.0), 0.9)
if MAX_ENTRY_CUTOFF_MIN < WIND_DOWN_MIN:
    print(f"WARNING MAX_ENTRY_CUTOFF_MIN({MAX_ENTRY_CUTOFF_MIN}) < WIND_DOWN_MIN({WIND_DOWN_MIN}); clamping up")
    MAX_ENTRY_CUTOFF_MIN = float(WIND_DOWN_MIN)
# --- STOP ESCALATION (audit HIGH-1): pure-maker STOP can leave you hanging (offsets may never
# fill); pure-taker STOP is a fire-sale. STOP = maker-first with BOUNDED escalation: rest the
# offsets, wait, re-check, and taker-cross ONLY what is still material after the wait.
STOP_ESCALATE_S = _envi("KALSHI_STOP_ESCALATE_S", 90)       # seconds passive offsets get to fill
STOP_TAKER_MIN_CT = _envf("KALSHI_STOP_TAKER_MIN_CT", 5.0)  # escalate only if |pos| still >= this
# --- selection: prefer BALANCED books (maker-unwind fills) over one-sided drift traps ---
MAX_SPREAD_TICKS = _envi("KALSHI_MAX_SPREAD_TICKS", 8)      # skip wide/illiquid books
MIN_DEPTH_SYM = _envf("KALSHI_MIN_DEPTH_SYM", 0.25)         # min(depth)/max(depth) both sides
REQ_SPACING_S = 0.55
READ_BUDGET_PER_CYCLE = 200

_last_req = [0.0]
_reads = [0]


def utcnow():
    return datetime.now(timezone.utc)


def public_get(path):
    if _reads[0] >= READ_BUDGET_PER_CYCLE:
        raise RuntimeError("read budget exhausted")
    wait = REQ_SPACING_S - (time.time() - _last_req[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PROD_BASE + path,
                                 headers={"User-Agent": "maker-kalshi-quoter/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        _last_req[0] = time.time()
        _reads[0] += 1
        return json.loads(r.read())


# ---------------- pure planning (unit-tested offline) ----------------

def parse_iso(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# DROP REASONS from the last select_footprint call. A footprint that silently empties (clock
# skew, API date-format change, a series renamed out of the allowlist) otherwise prints
# 'cycle ok footprint=0' forever while the bot is functionally dead (masking audit 07-22).
# Module-level rather than a parameter so the 2-arg signature every caller/test uses is intact.
FP_DROPS = {}


def select_footprint(progs, now):
    FP_DROPS.clear()
    drops = FP_DROPS
    rows = []
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            drops["drop_not_liquidity"] = drops.get("drop_not_liquidity", 0) + 1
            continue
        if p.get("target_size_fp") is None or p.get("discount_factor_bps") is None:
            drops["drop_null_fields"] = drops.get("drop_null_fields", 0) + 1
            continue
        t = p.get("market_ticker")
        if not t:
            continue
        if SERIES_ALLOW and t.split("-")[0] not in SERIES_ALLOW:
            drops["drop_allowlist"] = drops.get("drop_allowlist", 0) + 1
            continue                       # series allowlist (pilot = weather/temp only)
        try:
            end = parse_iso(p["end_date"])
            start = parse_iso(p["start_date"])
        except Exception:
            drops["drop_date_parse"] = drops.get("drop_date_parse", 0) + 1
            continue
        life_min = max((end - start).total_seconds() / 60.0, 1.0)
        # LATE-LIFE ENTRY GATE (2026-07-22): no entry past LATE_LIFE_FRAC of THIS market's own
        # life (abs-capped for long markets); held inventory on excluded markets goes reduce-only
        # via the strand path. Always at least the wind-down cutoff.
        cutoff_min = min(MAX_ENTRY_CUTOFF_MIN, max(WIND_DOWN_MIN, LATE_LIFE_FRAC * life_min))
        if end < now + timedelta(minutes=cutoff_min):
            drops["drop_late_life"] = drops.get("drop_late_life", 0) + 1
            continue
        days = max((end - start).total_seconds() / 86400, 1 / 24)
        # period_reward may be present-but-null (pending programs) -> `or 0`, not .get default
        rows.append({"ticker": t, "usd_day": ((p.get("period_reward") or 0) / 10000) / days,
                     "target": float(p["target_size_fp"]), "end": end.isoformat(),
                     # per-market ramp window = min(global RAMP_MIN, a fraction of THIS market's own
                     # program lifetime) so short markets only ramp in their final stretch (C13).
                     "ramp_min": min(RAMP_MIN, RAMP_LIFE_FRAC * days * 1440.0)})
    rows.sort(key=lambda r: (-r["usd_day"], r["ticker"]))
    # ROUND-ROBIN across series (review C18): a single high-pot series (50 concurrent hourly temp
    # strikes ~ $1,920/day each) would otherwise fill the whole FOOTPRINT_TOP by usd_day and starve
    # every other allowlisted series — the fee-free gas lane got ZERO slots. Take one market per
    # series per round, in series-best-usd_day order, until the footprint is full; PER_SERIES_CAP
    # still binds. (A single-series universe collapses to the old highest-usd_day-first order.)
    by_series = defaultdict(list)
    for r in rows:
        by_series[r["ticker"].split("-")[0]].append(r)
    series_order = sorted(by_series, key=lambda s: (-by_series[s][0]["usd_day"], s))
    picked, per_series = [], defaultdict(int)
    progressed = True
    while len(picked) < FOOTPRINT_TOP and progressed:
        progressed = False
        for s in series_order:
            i = per_series[s]
            if i >= PER_SERIES_CAP or i >= len(by_series[s]):
                continue
            picked.append(by_series[s][i])
            per_series[s] += 1
            progressed = True
            if len(picked) >= FOOTPRINT_TOP:
                break
    return picked


def _levels(raw):
    """Parse [[price_str,size_str]...] to [(price,size)] floats. Returns
    (levels, n_malformed). Rows that fail to PARSE are counted (n_malformed) —
    a systematic parse failure (e.g. API shape change) must not be invisible;
    rows with size<=0 are legit-empty and NOT counted as malformed."""
    out, malformed = [], 0
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            malformed += 1
            continue
        if s > 0:
            out.append((p, s))
    return out, malformed


def _capped_join(best, other_price):
    """Contracts to rest at `best` so this side's $ stays within half the
    per-market cap; >=1 (caller gates unpriceable elsewhere)."""
    per_side = MAX_MARKET_CAPITAL / 2.0
    n = min(JOIN_SIZE, int(per_side / best)) if best > 0 else 0
    return max(1, n)


def _unwind_size(base, price, inv):
    """Contracts to rest on the reducing side to unwind toward flat. Capped at |inv| — NEVER
    more — because resting > |inv| would, on a full fill, cross THROUGH flat and open the
    OPPOSITE position (a de-risk that flips the sign is not a de-risk). Also bounded by a per-side
    $ budget (room). Floored at 1 (a valid order). `base` is retained for call-site compatibility
    but is deliberately NOT a floor: a floor above |inv| IS the overshoot bug.

    room = the FULL MAX_MARKET_CAPITAL (not half): a reducing order is the ONLY order resting on
    its side (no paired accumulating side to share the per-market budget with), and its fill FREES
    collateral. Halving it (review C6/C10) throttled the de-risk drain to ~1/4 of the HARD
    envelope, so a HARD-sized position could not passively flatten before the settle-taker fired.

    int() (truncate), NEVER round(): Kalshi positions are fractional (position_fp e.g. 1.6), and
    round(1.6)=2 rests MORE than held — a full fill would cross THROUGH flat by 0.4 ct (opposite
    dust). Truncating rests 1, leaving 0.6 ct of sub-minimum dust that NO order can act on anyway
    (venue min order = 1 ct) — provably-never-overshoot."""
    room = int(MAX_MARKET_CAPITAL / price) if price > 0 else int(abs(inv))
    return max(1, min(int(abs(inv)), room))


def _unwind_price(best, cost):
    """Price for a reducing (unwind) quote: the book reference, CAPPED so a fill can never lock
    in more than MAX_UNWIND_LOSS per pair vs our cost basis (pair realized loss = held-side cost
    + exit-side price - 1). cost<=0 means basis unknown -> no cap (reference, legacy behavior).
    Floored to the cent so float noise can never round the cap UP past the intended bound."""
    if cost <= 0:
        return best
    cap = math.floor((1.0 - cost + MAX_UNWIND_LOSS) * 100.0) / 100.0
    return min(best, cap)


def _throttled_quote(best, cnt, over, levels, target):
    """(price, count) for a THROTTLED accumulating side.

    The step-inside is a risk brake, but the sandbox A/B (n=612 real snapshots, CFTC formula)
    measured it ZEROING our reward credit in 12% of cases — not halving it. That happens when
    the depth AT the best price already meets Target Size: the qualifying walk stops there, so
    anything a tick behind is outside the scored set entirely and earns nothing.

    So: only pay the price step when it still buys us something. If the top level alone already
    satisfies Target Size, keep the quote AT reference (full 1.0x credit) and take the risk
    reduction from SIZE instead — the brake is preserved, the reward is not thrown away.
    Free-win case identified by the A/B; everywhere else behaviour is unchanged."""
    shrunk = max(MIN_QUOTE_CT, int(cnt * (1 - over)))
    depth_at_best = sum(s for p, s in levels if abs(p - best) < TICK / 2)
    if THROTTLE_SMART and THROTTLE_STEP_TICKS > 0 and depth_at_best >= target > 0:
        # stepping back would fall outside the qualifying set -> stay at ref, shrink harder
        return best, max(MIN_QUOTE_CT, int(shrunk * (1 - over)))
    if THROTTLE_STEP_TICKS <= 0:
        return best, shrunk
    return round(best - TICK * THROTTLE_STEP_TICKS, 4), shrunk


def desired_quotes(m, yes_levels, no_levels, now, own=None, inv=0.0, event_delta=0.0, stats=None,
                   cost=0.0):
    """Desired resting orders for one market. Returns list of
    {side, price_dollars, count, reason} — reason 'unwind' marks a RISK-REDUCING order
    (exempt from the capital cap). Delta-neutral shaping is driven by TWO signals:
      inv         = OUR signed net on THIS ticker (+long yes / -long no). Sets the UNWIND
                    (grow the reducing side toward |inv|) AND the THROTTLE DIRECTION (throttle
                    the side whose fill grows this ticker's |inv|, driving it toward flat).
      event_delta = aggregate signed net across the whole nested-threshold event. Correlated
                    'above X' strikes are directionally additive, so the event aggregate is the
                    true directional risk. It LOWERS the throttle trigger (max(|inv|,|event|))
                    so strikes each individually under SOFT still throttle, and — when we're
                    flat on this ticker — supplies the throttle direction."""
    own = own or {"yes": 0.0, "no": 0.0}
    inv = float(inv or 0.0)
    ev = float(event_delta or 0.0)
    (yl, bad_y), (nl, bad_n) = _levels(yes_levels), _levels(no_levels)
    if stats is not None:
        stats["dropped_book_rows"] = stats.get("dropped_book_rows", 0) + bad_y + bad_n
    best_y = max((p for p, _ in yl), default=None)
    best_n = max((p for p, _ in nl), default=None)
    end = parse_iso(m["end"])
    _priceable = (best_y is not None and best_n is not None
                  and MIN_PRICE_DOLLARS < best_y <= MAX_PRICE_DOLLARS
                  and MIN_PRICE_DOLLARS < best_n <= MAX_PRICE_DOLLARS
                  and best_y + best_n < 1.0)
    if end < now + timedelta(minutes=WIND_DOWN_MIN):
        # wind_down: pull the two-sided quotes. But if we still HOLD inventory here, keep
        # resting the REDUCING side (passive $0 maker unwind) until the settlement taker
        # backstop takes over — never abandon an open position into resolution (fix F).
        if abs(inv) >= INV_TOLERANCE and _priceable:
            if inv > 0:
                up = _unwind_price(best_n, cost)
                if not (MIN_PRICE_DOLLARS < up <= MAX_PRICE_DOLLARS):
                    return []                       # loss-cap out of range -> ride to backstop
                return [{"side": "no", "price_dollars": up,
                         "count": _unwind_size(_capped_join(up, best_y), up, inv), "reason": "unwind"}]
            up = _unwind_price(best_y, cost)
            if not (MIN_PRICE_DOLLARS < up <= MAX_PRICE_DOLLARS):
                return []
            return [{"side": "yes", "price_dollars": up,
                     "count": _unwind_size(_capped_join(up, best_n), up, inv), "reason": "unwind"}]
        return []                                   # wind_down (flat -> pull entirely)
    if best_y is None or best_n is None:
        return []                                   # unpriceable side
    if not (MIN_PRICE_DOLLARS < best_y <= MAX_PRICE_DOLLARS) or \
       not (MIN_PRICE_DOLLARS < best_n <= MAX_PRICE_DOLLARS):
        return []                                   # spread_sanity (both bounds)
    if best_y + best_n >= 1.0:
        return []                                   # crossed/degenerate book — a yes
        # bid @best_y and no bid @best_n would cross (yes_bid >= yes_ask); skip so a
        # stale-book quote can never take even if post_only were silently ignored.
    # external depth = public depth minus our own resting order on that side
    ext_y = max(0.0, sum(s for _, s in yl) - float(own.get("yes", 0)))
    ext_n = max(0.0, sum(s for _, s in nl) - float(own.get("no", 0)))
    target = m["target"]
    void = ext_y < target or ext_n < target
    # REWARD-QUALIFICATION GATE (CFTC Feb-2026 LIP amendment, verified 07-22): "Snapshots will
    # be excluded if there is not two-sided liquidity (i.e. resting orders sufficient to meet
    # the Target Size on each side)". If a side's book cannot reach Target Size, NOBODY scores
    # that snapshot — quoting there earns exactly $0 while still taking fill risk. Live probe:
    # 5 of 8 of our own allowlisted programs had one side at ZERO depth against target=1000.
    # We can only bridge a gap we can actually fund, so qualification is judged against the
    # most size our per-market activate budget could add. Inventory still unwinds (de-risk is
    # never gated on reward) — this only stops us OPENING in markets that cannot pay.
    _addable = (MAX_ACTIVATE_CAPITAL / max(best_y, best_n, 0.01))
    _qualifiable = (ext_y + _addable >= target) and (ext_n + _addable >= target)
    if not _qualifiable and abs(inv) < INV_TOLERANCE:
        if stats is not None:
            stats["unqualifiable"] = stats.get("unqualifiable", 0) + 1
        return []                                   # cannot reach two-sided Target Size -> $0 reward
    # SELECTION GATE (only when ~flat — if we hold inventory we must keep quoting to unwind):
    # skip WIDE or ONE-SIDED books. A balanced two-sided book is where the maker-unwind
    # reliably fills; a one-directional/wide book is the gas-ladder trap that adverse-selects
    # us and then won't let the passive exit fill. This is the primary defense of "flatten as
    # a maker". ACTIVATE (void) markets are intentionally thin -> exempt (handled elsewhere).
    if not void and abs(inv) < INV_TOLERANCE:       # ONLY when truly FLAT (not just below SOFT):
        spread_ticks = (1.0 - best_n - best_y) / TICK   # any inventory in [TOL,SOFT) must keep
        sym = min(ext_y, ext_n) / max(ext_y, ext_n, 1e-9)   # quoting the reducing side to unwind
        if spread_ticks > MAX_SPREAD_TICKS or sym < MIN_DEPTH_SYM:
            return []                               # one-sided / wide -> unwind-unreliable, skip
    if JOIN_ALWAYS:
        # drill/testing switch: tiny join on both sides of any priceable market,
        # ignoring void/activate economics — exercises place/diff/cancel machinery.
        return [{"side": "yes", "price_dollars": best_y, "count": _capped_join(best_y, best_n), "reason": "join"},
                {"side": "no", "price_dollars": best_n, "count": _capped_join(best_n, best_y), "reason": "join"}]
    quotes = []
    if void:
        # ACTIVATE (thin book, we supply Target depth). If we CARRY inventory here, do NOT
        # blanket-pull (that removes the $0 maker unwind AND leaves the taker unreachable while
        # inv is frozen) — rest ONLY the reducing side to unwind passively.
        if abs(inv) >= INV_TOLERANCE:
            if inv > 0:      # long yes -> rest a reducing NO bid
                up = _unwind_price(best_n, cost)
                if not (MIN_PRICE_DOLLARS < up <= MAX_PRICE_DOLLARS):
                    return []                       # loss-cap out of range -> ride to backstop
                return [{"side": "no", "price_dollars": up,
                         "count": _unwind_size(_capped_join(up, best_y), up, inv), "reason": "unwind"}]
            else:            # long no -> rest a reducing YES bid
                up = _unwind_price(best_y, cost)
                if not (MIN_PRICE_DOLLARS < up <= MAX_PRICE_DOLLARS):
                    return []
                return [{"side": "yes", "price_dollars": up,
                         "count": _unwind_size(_capped_join(up, best_n), up, inv), "reason": "unwind"}]
        if abs(ev) > INV_SOFT_CT:
            return []                               # event already directional -> don't ADD via activate
        add_y = max(JOIN_SIZE, target - ext_y)
        add_n = max(JOIN_SIZE, target - ext_n)
        cap = best_y * add_y + best_n * add_n
        if cap > MAX_ACTIVATE_CAPITAL:
            return []                               # too expensive to activate
        quotes.append({"side": "yes", "price_dollars": best_y, "count": int(add_y), "reason": "activate"})
        quotes.append({"side": "no", "price_dollars": best_n, "count": int(add_n), "reason": "activate"})
    else:
        # JOIN: external depth meets Target both sides, so shaping OUR size never voids it.
        # BOTH sides ALWAYS rest here (never pulled to zero) — the resting quotes are what earns
        # the rewards; inventory earns nothing. Position control is done by SKEW, not by removing
        # a quote: shrink+step-in the accumulating side, grow the reducing side. Both stay live.
        y_price, y_cnt, y_reason = best_y, _capped_join(best_y, best_n), "join"
        n_price, n_cnt, n_reason = best_n, _capped_join(best_n, best_y), "join"
        # THROTTLE DIRECTION follows THIS ticker's own inventory (accumulating side = the one whose
        # fill grows our |inv|). When flat on the ticker, follow the EVENT aggregate (a flat ticker
        # in a directional event must not ADD to the drift). MAGNITUDE uses max(|inv|,|event|) so
        # correlated strikes each under SOFT still throttle via the event aggregate.
        # SETTLEMENT RAMP (audit HIGH-2): be SMALL at settlement so the settle-taker is a rare
        # backstop, not the primary exit into the worst tick. Inside RAMP_MIN both join sizes
        # scale down linearly with time-to-end (floor MIN_QUOTE_CT); unwind quotes are never
        # ramped (set below) — de-risking gets easier as the end nears, adding gets harder.
        mins_left = (end - now).total_seconds() / 60.0
        ramp_min = m.get("ramp_min") or RAMP_MIN     # per-market (C13); fallback to global default
        if mins_left < ramp_min:
            scale = max(0.0, (mins_left - WIND_DOWN_MIN) / max(1.0, ramp_min - WIND_DOWN_MIN))
            y_cnt = max(MIN_QUOTE_CT, int(y_cnt * scale))
            n_cnt = max(MIN_QUOTE_CT, int(n_cnt * scale))
        if abs(inv) >= INV_TOLERANCE:
            acc = 1 if inv > 0 else -1
        elif abs(ev) > INV_SOFT_CT:
            acc = 1 if ev > 0 else -1
        else:
            acc = 0
        mag = max(abs(inv), abs(ev))
        # per-market HELD-$ envelope (review C12): INV_HARD_CT bounds CONTRACTS, but at high prices
        # HARD contracts are many multiples of the MAX_MARKET_CAPITAL dollar intent (60 ct @0.96 =
        # ~$57 on one ticker). Pull the accumulating side once held $ on THIS ticker reaches the
        # per-market $ cap, not only at the contract HARD — whichever binds first. held-$ uses this
        # ticker's own signed inventory (0 when flat/event-driven -> the contract HARD governs).
        held_usd = abs(inv) * (best_y if inv > 0 else best_n)
        hard = mag >= INV_HARD_CT or held_usd >= MAX_MARKET_CAPITAL
        if acc != 0 and (mag > INV_SOFT_CT or hard):
            # shrink the accumulating side toward MIN_QUOTE_CT and step it 1 tick inside so it
            # fills last. AT/ABOVE HARD (contract OR $) the accumulating side IS pulled to zero
            # (audit MED-3): the MIN_QUOTE floor would keep leaking fills on a one-way market, so
            # HARD is the hard position envelope. Above it, bounded risk beats that side's reward.
            over = min(1.0, (mag - INV_SOFT_CT) / max(1.0, INV_HARD_CT - INV_SOFT_CT))
            if acc > 0:                             # accumulating YES -> throttle YES
                if hard:
                    y_cnt = 0                       # HARD STOP: cap the envelope, stop the leak
                else:
                    y_price, y_cnt = _throttled_quote(best_y, y_cnt, over, yl, target)
            else:                                   # accumulating NO -> throttle NO
                if hard:
                    n_cnt = 0
                else:
                    n_price, n_cnt = _throttled_quote(best_n, n_cnt, over, nl, target)
        # OFFSET the position: grow the REDUCING side toward |inv| at reference so its fills drain
        # the overhang back to ~zero net delta (maker offset, tagged 'unwind' = exempt from the
        # capital cap; capped at |inv| so it can't overshoot past flat). This does NOT bloat into a
        # held pair — it only sizes enough to cancel what we already hold.
        if abs(inv) >= INV_TOLERANCE:
            if inv > 0:                             # long yes -> grow NO (reduces), at ref BUT
                n_price, n_reason = _unwind_price(best_n, cost), "unwind"   # loss-capped
                n_cnt = _unwind_size(_capped_join(n_price, best_y), n_price, inv)
            else:                                   # long no -> grow YES (reduces)
                y_price, y_reason = _unwind_price(best_y, cost), "unwind"
                y_cnt = _unwind_size(_capped_join(y_price, best_n), y_price, inv)
        if y_cnt > 0 and MIN_PRICE_DOLLARS < y_price <= MAX_PRICE_DOLLARS:
            quotes.append({"side": "yes", "price_dollars": y_price, "count": y_cnt, "reason": y_reason})
        if n_cnt > 0 and MIN_PRICE_DOLLARS < n_price <= MAX_PRICE_DOLLARS:
            quotes.append({"side": "no", "price_dollars": n_price, "count": n_cnt, "reason": n_reason})
    return quotes


def diff_orders(standing, desired):
    """standing: {ticker: [{side, price_dollars, count, order_id}]};
    desired:  {ticker: [{side, price_dollars, count, reason}]}.
    Returns (cancels [order_id], creates [order dict w/ ticker]).
    An order survives only if side+price+count match exactly."""
    cancels, creates = [], []
    for t in set(standing) | set(desired):
        have = {(o["side"], round(o["price_dollars"], 4), o["count"]): o
                for o in standing.get(t, [])}
        want = {(q["side"], round(q["price_dollars"], 4), q["count"]): q
                for q in desired.get(t, [])}
        for k, o in have.items():
            if k not in want:
                cancels.append(o["order_id"])
        for k, q in want.items():
            if k not in have:
                creates.append(dict(q, ticker=t))
    return cancels, creates


# ---------------- cycle ----------------

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_FILE)


def append_plan(row):
    path = os.path.join(DATA_DIR, f"plans-{utcnow().strftime('%Y%m%d')}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def order_id_for(cyc, i, side):
    """Unique client_order_id per create: cycle-nonce + per-create index + side.
    Unique within a cycle (index) and across cycles (nonce) — no ticker prefix,
    so no truncation collision and no cross-cycle reuse of Kalshi's dedup key."""
    return f"mk-{cyc}-{i}-{side}"


def own_resting(standing):
    """{ticker: {'yes':contracts,'no':contracts}} from our standing orders."""
    out = defaultdict(lambda: {"yes": 0.0, "no": 0.0})
    for t, orders in standing.items():
        for o in orders:
            if o.get("side") in ("yes", "no"):
                out[t][o["side"]] += float(o.get("count") or 0)
    return out


def _mkt_capital(quotes):
    return sum(q["price_dollars"] * q["count"] for q in quotes)


def cap_desired(desired, usd_day):
    """Keep whole markets in strict usd_day priority (highest first), stopping at
    the first ACCUMULATING market that would breach MAX_TOTAL_CAPITAL — keep the
    most valuable, cut the tail. REDUCING (any 'unwind' quote) markets are kept
    UNCONDITIONALLY: a risk-reducing order can never over-commit the account, so
    the cap must not drop it (polarity-aware, fix A). Returns (kept, dropped_count)."""
    kept, total = {}, 0.0
    for t, qs in desired.items():
        if any(q.get("reason") == "unwind" for q in qs):
            kept[t] = qs
            total += _mkt_capital(qs)
    order = [t for t in sorted(desired, key=lambda t: -usd_day.get(t, 0)) if t not in kept]
    for i, t in enumerate(order):
        c = _mkt_capital(desired[t])
        if total + c > MAX_TOTAL_CAPITAL:
            return kept, len(order) - i        # everything from here down is dropped
        kept[t] = desired[t]
        total += c
    return kept, 0


def bound_creates(creates, cancels, usd_day):
    """Keep whole-ticker create groups (highest usd_day first) so
    len(cancels)+kept <= WRITE_BUDGET. Never splits a market's two sides.
    Returns (kept_creates, dropped_ticker_count)."""
    if len(cancels) + len(creates) <= WRITE_BUDGET_PER_CYCLE:
        return creates, 0
    by_t = defaultdict(list)
    for c in creates:
        by_t[c["ticker"]].append(c)
    budget = max(0, WRITE_BUDGET_PER_CYCLE - len(cancels))
    kept, used, dropped = [], 0, 0
    # REDUCING (unwind) groups first, then by usd_day — a risk-reducing create must never be
    # dropped for write budget while an accumulating create survives (polarity-aware, fix A).
    def _unwind_group(t):
        return 0 if any(c.get("reason") == "unwind" for c in by_t[t]) else 1
    for t in sorted(by_t, key=lambda t: (_unwind_group(t), -usd_day.get(t, 0))):
        grp = by_t[t]
        if used + len(grp) <= budget:
            kept.extend(grp)
            used += len(grp)
        else:
            dropped += 1
    return kept, dropped


BLACKOUT_CANCEL_AFTER = _envi("KALSHI_BLACKOUT_CANCEL_AFTER", 2)  # consecutive blind cycles


def _blackout_guard(client, st, plan):
    """READ-BLACKOUT GUARD (audit MED-4): fail-closed stops NEW actions, but the quotes already
    resting on the exchange stay live and can fill while we're blind. After
    BLACKOUT_CANCEL_AFTER consecutive failed read cycles, best-effort cancel the LAST-KNOWN
    order ids (persisted from the most recent good read) so blind fills can't keep accumulating.
    Cancel-by-known-id needs no read, so it works during the blackout itself."""
    st["read_fail_streak"] = int(st.get("read_fail_streak", 0)) + 1
    plan["read_fail_streak"] = st["read_fail_streak"]
    if st["read_fail_streak"] < BLACKOUT_CANCEL_AFTER:
        return
    oids = st.get("last_oids") or []
    if not oids:
        print("WARNING blackout persists but no last-known order ids — nothing to cancel")
        return
    ok, remaining = 0, []
    for oid in oids:
        try:
            client.cancel_order(oid); ok += 1
        except Exception:
            remaining.append(oid)                   # cancel FAILED (network/429) -> KEEP for retry.
            # do NOT drop it (review C15): wiping ids we never cancelled disarms the guard exactly
            # in a network partition (the scenario it exists for). A benign 404 (already gone) also
            # lands here and is harmlessly retried next blackout cycle.
    plan["blackout_cancelled"] = ok
    st["last_oids"] = remaining                     # keep only the ones we could NOT cancel
    print(f"WARNING read blackout x{st['read_fail_streak']} — best-effort cancelled "
          f"{ok}/{len(oids)} last-known quotes ({len(remaining)} left to retry)")


def run_once():
    os.chdir(DATA_DIR)
    _lock = _acquire_lock()
    if _lock is False:
        print("WARNING another quoter instance holds the run lock; skipping this run (no order ops)")
        return 0
    now = utcnow()
    client = KalshiOrderClient()          # dry_run unless operator-configured
    if os.path.exists(STOP_FILE):
        # emergency stop: cancel quotes + rest MAKER offsets to flatten passively (never taker).
        print("STOP sentinel present; maker-flattening (cancel quotes + rest offsets) + exiting")
        if client.mode != "dry_run":
            _flatten_all(client)
        _release_lock(_lock)
        return 0
    _reads[0] = 0
    cyc = int(now.timestamp())            # per-cycle nonce for unique order ids
    st = load_state()
    plan = {"ts": now.isoformat(), "mode": client.mode}
    created_ok = []
    cancels, creates = [], []
    fetch_failed = 0
    quote_fail = 0                        # desired_quotes raised (our-logic error, surfaced)
    first_quote_err = None
    first_create_err = None
    qstats = {"dropped_book_rows": 0}     # malformed book rows skipped by _levels
    try:
        progs = []
        cursor = ""
        for _ in range(5):
            d = public_get("/trade-api/v2/incentive_programs?status=active&limit=10000"
                           + (f"&cursor={cursor}" if cursor else ""))
            progs.extend(d.get("incentive_programs", []))
            cursor = d.get("next_cursor") or ""
            if not cursor:
                break
        footprint = select_footprint(progs, now)
        plan.update(FP_DROPS)                 # drop reasons (empty when a test patches selection)
        plan["programs_seen"] = len(progs)
        usd_day = {m["ticker"]: m["usd_day"] for m in footprint}

        # standing FIRST so activate can size against external (non-own) depth.
        # In demo/live the PUBLIC orderbook already includes our resting orders, so
        # subtract own to get external depth. In dry_run the public book never
        # contained our (never-placed) simulated orders, so own must be 0 there —
        # subtracting it would double-count and make activate oscillate every cycle.
        held_by = {}                          # signed net position per ticker (delta)
        held_cost = 0.0
        cost_by = {}                          # per-ticker avg cost basis (unwind loss cap)
        breaker = False
        if client.mode == "dry_run":
            standing = st.get("simulated_standing", {})
            own = {}
        else:
            try:
                standing, raw_rows = _live_standing(client)
            except Exception as e:
                # cannot read our resting orders -> act on NOTHING this cycle (safe):
                # never cancel/create blind. BUT (audit MED-4) our quotes are still LIVE on the
                # exchange and can fill while we're blind — on a SUSTAINED blackout, best-effort
                # cancel the last-known order ids so blind fills can't accumulate.
                plan["standing_read_failed"] = repr(e)[:120]
                _blackout_guard(client, st, plan)
                print(f"WARNING could not read standing ({e!r}); skipping cycle (no order ops)")
                return 0                     # finally: appends plan + saves state
            parsed = sum(len(v) for v in standing.values())
            if raw_rows > 0 and parsed == 0:
                # reconcile guard: the exchange HAS resting orders we failed to parse.
                # Do NOT create the book on top of them (that stacks collateral). Halt.
                plan["reconcile_fail"] = raw_rows
                # A total parse failure means we hold resting orders we cannot interpret — we are
                # functionally BLIND to our own book even though the read succeeded, and those
                # quotes keep filling while we halt. So it must drive the blackout streak too:
                # sustained, it escalates to cancel-by-last-known-id (which needs no parse).
                _blackout_guard(client, st, plan)
                print(f"WARNING reconcile FAIL: {raw_rows} resting rows parsed to 0 — halting (no order ops)")
                return 0
            own = own_resting(standing)
            # last_oids: seed from the standing read NOW so the blackout guard always has real
            # resting ids to cancel even if the positions read below fails. Do NOT reset the fail
            # streak yet (review C2): a good standing read alone is NOT a complete cycle, so a
            # sustained positions-ONLY blackout must still accumulate the streak and eventually
            # trigger the cancel — resetting here pinned the streak at 1 forever and the guard
            # never fired.
            st["last_oids"] = [o["order_id"] for ol in standing.values() for o in ol]
            # DELTA: read signed inventory ONCE, AHEAD of the quote loop, so shaping acts on
            # THIS cycle's position (not one cycle stale). Fail CLOSED (defer) if unreadable —
            # never shape/create blind to our own delta.
            try:
                held_cost, held_by, cost_by = _held_cost(client)
            except Exception as e:
                plan["positions_read_failed"] = repr(e)[:120]
                _blackout_guard(client, st, plan)
                print(f"WARNING could not read positions ({e!r}); skipping cycle (delta unknown)")
                return 0
            # BOTH reads succeeded -> a complete cycle; only NOW clear the blackout streak.
            st["read_fail_streak"] = 0
            # VELOCITY CIRCUIT BREAKER: compare held-$ now vs the LOWEST held-$ inside the
            # window. Rapid growth = adverse accumulation -> the whole book goes REDUCE-ONLY
            # below (only unwind quotes survive; accumulating quotes cancelled by the diff).
            # Self-releasing: reduce-only stops the growth, the window slides, the gate clears.
            # DAILY LOSS KILL (treadmill guard): equity = cash + held cost basis, so SETTLED
            # losses show up here even though held-$ returns to zero. Drop beyond the daily
            # budget -> write STOP (operator must clear it) + maker-first flatten NOW. Balance
            # read failure only skips this check (primary reads above remain fail-closed).
            _equity = None
            try:
                _equity = float(client.get_balance().get("balance_dollars") or 0) + held_cost
            except Exception as e:
                plan["balance_read_failed"] = repr(e)[:120]
                st["balance_fail_streak"] = int(st.get("balance_fail_streak", 0)) + 1
                plan["balance_fail_streak"] = st["balance_fail_streak"]
                # the daily-loss kill is DISARMED while this persists — every other guard read
                # fails closed with a WARNING; this one used to fail open silently.
                print(f"WARNING balance read failed x{st['balance_fail_streak']} ({e!r}) — "
                      f"DAILY LOSS KILL DISARMED this cycle")
            else:
                st["balance_fail_streak"] = 0
            if _equity is not None:
                _day = now.strftime("%Y%m%d")
                if st.get("equity_day") != _day:
                    st["equity_day"] = _day
                    st["equity_day_start"] = _equity
                elif float(st.get("equity_day_start", _equity)) - _equity > DAILY_LOSS_HALT_USD:
                    drop = float(st["equity_day_start"]) - _equity
                    plan["daily_loss_halt"] = round(drop, 2)
                    with open(STOP_FILE, "w") as fh:
                        fh.write(f"auto daily-loss halt {now.isoformat()} drop=${drop:.2f} "
                                 f"(equity ${_equity:.2f} vs day-start ${st['equity_day_start']:.2f})\n")
                    print(f"WARNING DAILY LOSS HALT: equity down ${drop:.2f} today (> "
                          f"${DAILY_LOSS_HALT_USD:.0f}) — STOP written, maker-flattening")
                    _flatten_all(client)
                    return 0
            # RISK measure for the breakers = NAKED (unhedged) cost only. Gross held includes
            # floored ladder pairs whose real downside is the strike gap, and gating on those
            # pinned the bot in reduce-only over risk it does not carry (live 07-22).
            # NOTE: the CAPITAL cap (committed vs MAX_TOTAL_CAPITAL) still uses GROSS held_cost —
            # paired inventory really does consume cash, so capital accounting must not shrink.
            risk_cost = naked_held_cost(held_by, cost_by)
            plan["naked_held_usd"] = round(risk_cost, 2)
            hist = [h for h in st.get("held_hist", [])
                    if now.timestamp() - h[0] < BREAKER_WINDOW_S]
            hist.append([now.timestamp(), risk_cost])
            st["held_hist"] = hist[-max(30, BREAKER_WINDOW_S // 60):]
            # trips on VELOCITY (rapid growth = adverse accumulation) OR LEVEL (total unpaired
            # held-$ above the day's-rewards-scale ceiling) — either way: reduce-only below.
            breaker = (risk_cost - min(h[1] for h in hist) > BREAKER_HELD_GROWTH_USD
                       or risk_cost > HELD_MAX_USD)

        # --- DE-RISK PASS (TAKER = GENUINE LAST RESORT ONLY). Normal position control is the
        # maker SKEW in desired_quotes (grow the reducing side, keep BOTH quotes live). The taker
        # crosses the spread, so it realizes the loss AND stops earning — it fires ONLY where
        # passive can no longer work: a MATERIAL position on a market about to SETTLE (you can't
        # maker-unwind what's about to close; don't carry directional delta into resolution).
        # A hard inventory breach alone does NOT taker — the skew + capital cap bound it while it
        # keeps quoting (that reflex 'get it out now' was the fire-sale that realized losses).
        # ladder pairing: floored cross-strike pairs are ~riskless (see ladder_pairing) — every
        # de-risk mechanism below targets the NAKED remainder, never the paired quantity.
        naked_by = ladder_pairing(held_by)
        # LIVE STRESS TEST of the ladder self-hedge (operator 07-22: stress it live rather than
        # re-review it). Every cycle, assert the invariants the pairing MUST satisfy against real
        # positions; any violation is loud + counted, so a wrong pairing surfaces immediately
        # instead of silently stripping guards from inventory that is not actually hedged.
        for _t, _n in naked_by.items():
            _h = held_by.get(_t, 0.0)
            if (_n and _h and (_n > 0) != (_h > 0)) or abs(_n) > abs(_h) + 1e-9:
                plan["ladder_violation"] = plan.get("ladder_violation", 0) + 1
                print(f"WARNING LADDER INVARIANT VIOLATED {_t}: naked {_n:+.2f} vs held {_h:+.2f} "
                      f"(sign flip or naked>held) — pairing is UNSAFE")
        for _ev in {_event_key(t) for t in held_by}:
            _sh = sum(v for k, v in held_by.items() if _event_key(k) == _ev)
            _sn = sum(v for k, v in naked_by.items() if _event_key(k) == _ev)
            if abs(_sh - _sn) > 1e-6:               # pairing must CONSERVE the event's signed sum
                plan["ladder_violation"] = plan.get("ladder_violation", 0) + 1
                print(f"WARNING LADDER SUM NOT CONSERVED {_ev}: held {_sh:+.2f} vs naked {_sn:+.2f}")
        plan["paired_ct"] = round(sum(abs(held_by.get(t, 0.0)) - abs(n) for t, n in naked_by.items()), 2)
        flattened = set()
        taker_flattens = 0
        taker_failed = 0
        if client.mode != "dry_run" and TAKER_FLATTEN and held_by:
            oids_by_t = {t: [o["order_id"] for o in ol] for t, ol in standing.items()}
            for t, pos in list(naked_by.items()):
                if abs(pos) < INV_TOLERANCE or taker_flattens >= TAKER_MAX_MKTS:
                    continue
                near_settle = False
                try:
                    close = public_get(f"/trade-api/v2/markets/{t}").get("market", {}).get("close_time")
                    near_settle = bool(close) and parse_iso(close) < now + timedelta(minutes=SETTLE_UNWIND_MIN)
                except Exception:
                    # the settle-taker's ONLY arming signal failed to read (was silently swallowed,
                    # review C16). Leave near_settle False (don't taker on an unknown clock), but
                    # COUNT it so a persistent blind spot in the settlement backstop is visible.
                    plan["settle_check_failed"] = plan.get("settle_check_failed", 0) + 1
                if near_settle:                         # ONLY genuine last resort: settling soon
                    # NOTE: flatten_to_zero crosses the ticker's FULL venue position (its own
                    # fresh read), which can include a paired leg — bounded pennies, rare, and
                    # never a flip (capped at |pos0|). The TRIGGER is naked-only by design.
                    ok, nc = flatten_to_zero(client, t, oids_by_t.get(t))
                    # HONEST OUTCOME (masking audit 07-22): flatten_to_zero cancels the ticker's
                    # resting orders FIRST, so a FAILED flatten (book unreadable / every IOC
                    # rejected / zero liquidity) leaves the position naked with NO reducing quote.
                    # Treating that as success (popping the ticker + counting a flatten) skipped
                    # the passive unwind AND suppressed the sysfail alarm — telemetry claimed the
                    # backstop ran while the position rode into settlement. Only a CONFIRMED flat
                    # retires the ticker; a failure falls through to normal maker unwind handling.
                    standing.pop(t, None)               # its resting orders WERE cancelled either way
                    if ok:
                        taker_flattens += 1
                        flattened.add(t)
                        held_by.pop(t, None)
                        naked_by.pop(t, None)
                    else:
                        taker_failed += 1
                        print(f"WARNING taker flatten FAILED on {t} (pos={pos:+.2f}, {nc} crosses) "
                              f"— position kept for maker unwind this cycle")
        plan["taker_flattens"] = taker_flattens
        plan["taker_failed"] = taker_failed

        # per-EVENT aggregate signed delta (post de-risk) — drives the throttle direction so
        # correlated nested strikes can't accumulate unbounded directional exposure.
        ev_delta = event_deltas(held_by)

        desired = {}
        book_refs = {}                                  # ticker -> (best_yes, best_no) this cycle
        for m in footprint:
            t = m["ticker"]
            if t in flattened:
                continue                                # just de-risked; leave it alone this cycle
            try:
                ob = public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
                if not (ob.get("yes_dollars") or ob.get("no_dollars")):
                    # ENVELOPE-level emptiness (API field rename / genuinely empty book) is
                    # invisible to the row-level malformed counter — count it (masking audit).
                    qstats["empty_books"] = qstats.get("empty_books", 0) + 1
            except RuntimeError:
                break                     # budget exhausted — stop fetching
            except Exception:
                # transient fetch fail: RETAIN this market's standing (do not
                # let diff cancel everything on it); skip re-pricing this cycle
                if standing.get(t):
                    # TAG the reducing side as 'unwind' when we hold a position here: a
                    # reason-less retained copy is invisible to every polarity-aware gate
                    # (cap_desired's unconditional keep, bound_creates priority, the breaker
                    # filter), so under a tight capital cap the ticker gets dropped and the diff
                    # CANCELS our live reducing order — stranding the position on a transient
                    # fetch error (review 07-22 skeptic). Tagging is diff-neutral: the retained
                    # copy still matches standing exactly, so no cancel/create is emitted.
                    _pos = naked_by.get(t, 0.0)
                    desired[t] = [
                        dict({"side": o["side"], "price_dollars": o["price_dollars"],
                              "count": o["count"]},
                             **({"reason": "unwind"} if (abs(_pos) >= INV_TOLERANCE and
                                 ((_pos > 0 and o["side"] == "no") or
                                  (_pos < 0 and o["side"] == "yes"))) else {}))
                        for o in standing[t]]
                fetch_failed += 1
                continue
            # book refs for the ladder escape hatch below (cheap re-parse of small lists)
            _byl, _ = _levels(ob.get("yes_dollars") or [])
            _bnl, _ = _levels(ob.get("no_dollars") or [])
            _by_best = max((p for p, _ in _byl), default=None)
            _bn_best = max((p for p, _ in _bnl), default=None)
            if _by_best is not None and _bn_best is not None:
                book_refs[t] = (_by_best, _bn_best)
            try:
                q = desired_quotes(m, ob.get("yes_dollars") or [], ob.get("no_dollars") or [],
                                   now, own=own.get(t), inv=naked_by.get(t, 0.0),
                                   event_delta=ev_delta.get(_event_key(t), 0.0), stats=qstats,
                                   cost=cost_by.get(t, 0.0))
            except Exception as e:
                # isolate one degenerate market, but SURFACE it as quote_fail (a
                # systematic desired_quotes failure must not hide inside gated_out)
                q = []
                quote_fail += 1
                if first_quote_err is None:
                    first_quote_err = f"{t}: {e!r}"
            if q:
                desired[t] = q

        # STRAND UNWIND (fix E): inventory on a held ticker NOT in this cycle's footprint
        # (dropped from selection — its program near-ended / usd_day fell off) gets no maker
        # unwind above. Rest the REDUCING side at reference so it still flattens passively;
        # the taker backstop only fires near settlement / on a hard breach.
        fp_tickers = {m["ticker"] for m in footprint}
        for t, pos in list(naked_by.items()):
            if t in fp_tickers or t in flattened or abs(pos) < INV_TOLERANCE:
                continue
            try:
                ob = public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
            except RuntimeError:
                break                                   # read budget exhausted
            except Exception:
                continue                                # transient — retry next cycle
            syl, _ = _levels(ob.get("yes_dollars") or [])
            snl, _ = _levels(ob.get("no_dollars") or [])
            sby = max((p for p, _ in syl), default=None)
            sbn = max((p for p, _ in snl), default=None)
            if sby is None or sbn is None or sby + sbn >= 1.0:
                continue                                # unpriceable/crossed — taker handles it
            up_n = _unwind_price(sbn, cost_by.get(t, 0.0))
            up_y = _unwind_price(sby, cost_by.get(t, 0.0))
            if pos > 0 and MIN_PRICE_DOLLARS < up_n <= MAX_PRICE_DOLLARS:
                desired[t] = [{"side": "no", "price_dollars": up_n,
                               "count": _unwind_size(_capped_join(up_n, sby), up_n, pos), "reason": "unwind"}]
            elif pos < 0 and MIN_PRICE_DOLLARS < up_y <= MAX_PRICE_DOLLARS:
                desired[t] = [{"side": "yes", "price_dollars": up_y,
                               "count": _unwind_size(_capped_join(up_y, sbn), up_y, pos), "reason": "unwind"}]

        # LADDER ESCAPE HATCH (operator 2026-07-22): a PARKED same-strike unwind (loss cap holds
        # it below the touch) may never fill in a trended market — the exact regime where the
        # position needs an exit most. Split the reducing size: half stays parked at the capped
        # price, half rests on the ADJACENT strike (higher for long-yes, lower for long-no) at
        # THAT book's reference. The cross fill doesn't realize the move — it converts the naked
        # position into a FLOORED pair (risk ~ strike gap, see ladder_pairing). Both lanes are
        # 'unwind'-tagged; combined size <= |naked| so no fill combination can flip the sign.
        strikes_avail = defaultdict(list)
        for t2 in book_refs:
            s2 = _strike_of(t2)
            if s2 is not None:
                strikes_avail[_event_key(t2)].append((s2, t2))
        for t, qn in list(naked_by.items()):
            if abs(qn) < INV_TOLERANCE or t not in desired:
                continue
            uws = [qq for qq in desired[t] if qq.get("reason") == "unwind"]
            ref = book_refs.get(t)
            s0 = _strike_of(t)
            if len(uws) != 1 or not ref or s0 is None:
                continue
            touch = ref[1] if qn > 0 else ref[0]        # the reducing side's own reference
            if uws[0]["price_dollars"] >= touch - TICK / 2:
                continue                                # at the touch -> not parked, no hatch
            cands = [(s2, t2) for s2, t2 in strikes_avail.get(_event_key(t), [])
                     if t2 != t and (s2 > s0 if qn > 0 else s2 < s0)]
            if not cands:
                continue
            _s2, t2 = min(cands) if qn > 0 else max(cands)   # NEAREST adjacent strike
            r2 = book_refs[t2]
            price2 = r2[1] if qn > 0 else r2[0]         # buy NO on higher / YES on lower, at ref
            if not (MIN_PRICE_DOLLARS < price2 <= MAX_PRICE_DOLLARS):
                continue
            c0 = uws[0]["count"]
            keep = max(1, c0 // 2)
            cross = min(c0 - keep, int(MAX_MARKET_CAPITAL / price2))
            if cross < 1:
                continue
            uws[0]["count"] = keep                      # keep+cross <= c0 <= |naked|: flip-safe
            plan["ladder_cross"] = plan.get("ladder_cross", 0) + 1
            # reason 'ladder' (NOT 'unwind'): this order BUYS a new position on t2 — it commits
            # fresh collateral, so the unwind exemptions ('a reducing fill frees collateral')
            # do NOT apply to it (review 07-22). Tagging it 'unwind' would exempt it from the
            # committed-capital gate and let several simultaneous hatches breach MAX_TOTAL.
            # As 'ladder' it is capital-gated like any accumulating create and is dropped on
            # breaker cycles — correct, since a breaker must not commit new cash.
            desired.setdefault(t2, []).append(
                {"side": "no" if qn > 0 else "yes", "price_dollars": price2,
                 "count": cross, "reason": "ladder"})

        # VELOCITY BREAKER application: reduce-only book — only 'unwind' quotes survive; every
        # accumulating quote (join/activate) is dropped from desired, so the diff CANCELS its
        # standing counterpart. Position control keeps working; acquisition stops.
        if breaker:
            plan["breaker_reduce_only"] = 1

            def _keep_reducing(t, q2):
                if q2.get("reason") == "unwind":
                    return True
                # fetch-fail-RETAINED standing copies carry no reason; cancelling a held
                # ticker's live unwind over a transient fetch error would strand the position
                # (review 07-22). Keep exactly the REDUCING side of a held ticker; everything
                # else (accumulating, flat tickers) is dropped -> cancelled by the diff.
                if q2.get("reason") is None:
                    pos = held_by.get(t, 0.0)
                    return ((pos > 0 and q2.get("side") == "no")
                            or (pos < 0 and q2.get("side") == "yes"))
                return False
            def _shape(t, qs):
                out = []
                for q2 in qs:
                    if _keep_reducing(t, q2):
                        out.append(q2)
                    elif (REDUCE_ONLY_KEEP_BOTH and abs(naked_by.get(t, 0.0)) >= INV_TOLERANCE
                          and q2.get("count", 0) > MIN_QUOTE_CT):
                        # ONLY where we HOLD inventory: keep that market two-sided (else the
                        # snapshot is excluded and even our resting exit quote earns $0) at the
                        # floor size, so added risk is ~10x smaller than a normal join. FLAT
                        # markets stay pulled — reduce-only must still mean reduce-only.
                        out.append(dict(q2, count=MIN_QUOTE_CT, reason="minjoin"))
                    elif (REDUCE_ONLY_KEEP_BOTH and abs(naked_by.get(t, 0.0)) >= INV_TOLERANCE
                          and q2.get("reason") is not None):
                        out.append(q2)          # already at/below the floor — keep as-is
                return out
            desired = {t: _shape(t, qs) for t, qs in desired.items()}
            desired = {t: qs for t, qs in desired.items() if qs}
            print(f"WARNING breaker: naked ${plan.get('naked_held_usd', 0):.2f} of "
                  f"${held_cost:.2f} held (growth>{BREAKER_HELD_GROWTH_USD:.0f}"
                  f"/{BREAKER_WINDOW_S}s or level>{HELD_MAX_USD:.0f}) — REDUCE-ONLY cycle")

        # REWARD-CREDIT TELEMETRY: LIP pays DiscountFactor^ticks-from-reference x size, so money
        # resting AT the touch earns full credit while money parked/stepped back earns 0.5^n (or
        # nothing at all if the book already meets Target Size above it). Measure the split so the
        # ledger can attribute rewards to quote placement instead of us theorising about it.
        at_ref = off_ref = 0.0
        for _t, _qs in desired.items():
            _r = book_refs.get(_t)
            if not _r:
                continue
            for _q in _qs:
                _best = _r[0] if _q["side"] == "yes" else _r[1]
                _v = _q["price_dollars"] * _q["count"]
                if abs(_q["price_dollars"] - _best) < TICK / 2:
                    at_ref += _v
                else:
                    off_ref += _v
        _two = sum(1 for _qs in desired.values()
                   if {q2["side"] for q2 in _qs} >= {"yes", "no"})
        plan["two_sided_markets"] = _two
        plan["one_sided_markets"] = len(desired) - _two
        plan["at_ref_usd"] = round(at_ref, 2)
        plan["off_ref_usd"] = round(off_ref, 2)
        plan["at_ref_pct"] = round(100 * at_ref / max(at_ref + off_ref, 1e-9), 1)

        desired, capped_markets = cap_desired(desired, usd_day)     # aggregate $ cap
        cancels, creates = diff_orders(standing, desired)
        creates, budget_dropped = bound_creates(creates, cancels, usd_day)  # whole-ticker

        # execute — each order isolated; one failure never aborts the rest
        cancel_fail = create_fail = create_skipped = 0
        oid_ticker = {o["order_id"]: t for t, ol in standing.items() for o in ol}
        cancelled_ok = set()
        for oid in cancels:
            try:
                client.cancel_order(oid); cancelled_ok.add(oid)
            except Exception:
                cancel_fail += 1
        # tickers whose cancel FAILED -> defer their creates a cycle (never stack
        # stale+new on the same ticker); a failed-cancel oid maps to its ticker.
        failed_cancel_tickers = {oid_ticker.get(oid) for oid in cancels if oid not in cancelled_ok}
        # ...and the SIDES of the still-resting failed-cancel orders (review C7): a new 'unwind'
        # create is normally exempt from the failed-cancel deferral, but if the SAME-SIDE stale
        # reducing order still rests, a second unwind stacks to ~2x|inv| and a full fill crosses
        # THROUGH flat into the opposite position — the exact sign-flip the overshoot cap prevents
        # per-order but not per-book. So a same-side unwind is deferred too.
        failed_cancel_sides = defaultdict(set)
        for _t, _ol in standing.items():
            for _o in _ol:
                if _o["order_id"] in cancels and _o["order_id"] not in cancelled_ok:
                    failed_cancel_sides[_t].add(_o["side"])
        # REAL committed capital = surviving standing (not-cancelled) + held inventory.
        # This is the guard the $ cap actually needs (cap_desired only bounds the
        # freshly-computed desired book, blind to survivors + fills).
        committed = sum(o["price_dollars"] * o["count"]
                        for ol in standing.values() for o in ol
                        if o["order_id"] not in cancelled_ok)
        # held_cost was read ahead of the quote loop (fail-closed there); reuse it — no
        # second positions fetch, and the cycle already halted if it was unreadable.
        committed += held_cost
        # process REDUCING (unwind) creates FIRST and NEVER block them on the capital cap —
        # a risk-reducing order can never over-commit the account (Kalshi frees the covered
        # collateral on fill). Only ACCUMULATING creates are gated.
        creates = sorted(creates, key=lambda c: 0 if c.get("reason") == "unwind" else 1)
        for i, c in enumerate(creates):
            cost = c["price_dollars"] * c["count"]
            reducing = c.get("reason") == "unwind"
            if c["ticker"] in failed_cancel_tickers:
                # accumulating creates always deferred on a failed-cancel ticker; a reducing
                # (unwind) create is exempt UNLESS a same-side stale reducing order still rests
                # (would stack -> overshoot through flat, review C7).
                if not reducing or c["side"] in failed_cancel_sides.get(c["ticker"], ()):
                    create_skipped += 1
                    continue
            if not reducing and committed + cost > MAX_TOTAL_CAPITAL:
                create_skipped += 1                 # cap gates ACCUMULATING orders only
                continue
            try:
                resp = client.create_quote(c["ticker"], c["side"], c["price_dollars"], c["count"],
                                    post_only=True, client_order_id=order_id_for(cyc, i, c["side"]))
                # capture the VENUE order id (live) so the blackout guard can cancel THIS cycle's
                # own creates (review C3); dry_run carries none -> keep the deterministic sim id
                # the simulated_standing rebuild below relies on.
                oid = f"sim-{cyc}-{i}"
                if isinstance(resp, dict) and not resp.get("dry_run"):
                    ro = resp.get("order") if isinstance(resp.get("order"), dict) else resp
                    oid = (ro or {}).get("order_id") or oid
                created_ok.append((c, oid)); committed += cost
            except Exception as e:
                create_fail += 1
                if first_create_err is None:        # anonymous create_fail hid WHAT was rejected
                    first_create_err = f"{c['ticker']}/{c['side']}/{c.get('reason')}: {e!r}"[:160]

        # next dry-run standing = prior standing - cancels + created (reflects truncation)
        if client.mode == "dry_run":
            cx = set(cancels)
            ns = {t: [o for o in olist if o.get("order_id") not in cx]
                  for t, olist in standing.items()}
            for c, oid in created_ok:
                ns.setdefault(c["ticker"], []).append(
                    {"side": c["side"], "price_dollars": c["price_dollars"],
                     "count": c["count"], "order_id": oid})
            st["simulated_standing"] = {t: v for t, v in ns.items() if v}
        else:
            # refresh last_oids to the ACTUAL post-cycle resting book: survivors (standing not
            # cancelled) + this cycle's freshly created VENUE ids. A subsequent read blackout can
            # then cancel EVERYTHING currently live, including quotes placed this cycle — the guard
            # previously saw only the pre-cycle snapshot and missed all fresh creates (review C3).
            survivors = [o["order_id"] for ol in standing.values() for o in ol
                         if o["order_id"] not in cancelled_ok]
            new_ids = [oid for (_c, oid) in created_ok if not str(oid).startswith("sim-")]
            st["last_oids"] = survivors + new_ids

        plan.update({
            "footprint": len(footprint), "quoted_markets": len(desired),
            "cancels": len(cancels), "creates": len(creates),
            "order_ops": len(cancels) + len(creates),
            "write_tokens": len(creates) * 10 + len(cancels) * 2,
            # retained fetch-fail markets are already IN desired -> do NOT subtract
            # them again (that double-counted and could go negative)
            "reads": _reads[0], "gated_out": len(footprint) - len(desired),
            "fetch_failed": fetch_failed, "capped_markets": capped_markets,
            "budget_dropped_markets": budget_dropped,
            "cancel_fail": cancel_fail, "create_fail": create_fail,
            "create_skipped": create_skipped,
            "quote_fail": quote_fail, "first_quote_err": first_quote_err,
            "first_create_err": first_create_err,
            "empty_books": qstats.get("empty_books", 0),
            "dropped_book_rows": qstats["dropped_book_rows"],
            "unqualifiable": qstats.get("unqualifiable", 0),
            "activate_markets": sum(1 for qs in desired.values()
                                    if qs and qs[0].get("reason") == "activate"),
            "est_capital_usd": round(sum(_mkt_capital(qs) for qs in desired.values()), 2),
            # REAL committed $ (surviving standing + held inventory + new creates) —
            # the number that must respect MAX_TOTAL_CAPITAL, not the desired est above.
            "committed_usd": round(committed, 2),
            "held_cost_usd": round(held_cost, 2),
        })
    finally:
        # bookkeeping ALWAYS runs, even if the cycle body raised
        append_plan(plan)
        save_state(st)
        _release_lock(_lock)
    # escalate to WARNING on a SYSTEMATIC failure (not per-item noise): most quotes
    # failing to compute, most creates rejected, or the whole footprint gated out.
    fp = plan.get("footprint", 0) or 0
    cr = plan.get("creates", 0) or 0
    ca = plan.get("cancels", 0) or 0
    sysfail = (plan.get("quote_fail", 0) > max(3, 0.5 * fp) or
               (cr and plan.get("create_fail", 0) >= cr) or
               (ca and plan.get("cancel_fail", 0) >= ca) or      # total cancel-path failure
               (fp and plan.get("fetch_failed", 0) >= fp) or     # total book-fetch failure
               plan.get("taker_failed", 0) > 0 or                # a backstop that did NOT work
               (plan.get("programs_seen", 0) > 0 and fp == 0) or  # dead selection (was invisible)
               (fp and plan.get("quoted_markets", 0) == 0 and not plan.get("fetch_failed")
                and not plan.get("taker_flattens")       # de-risk-only cycle != failure
                and not plan.get("breaker_reduce_only")))  # nor a reduce-only cycle with a flat
    # footprint: quoting NOTHING is the CORRECT breaker outcome when we hold no position in any
    # footprint market. Flagging it "systematic failure" cries wolf on a working guard, and
    # alarm fatigue on WARNING lines is what let the 07-22 loss run behind healthy telemetry.
    status = "cycle ok" if not sysfail else "WARNING systematic failure"
    print(f"{status} mode={plan['mode']} footprint={plan.get('footprint','?')} "
          f"quoted={plan.get('quoted_markets','?')} ops={plan.get('order_ops','?')} "
          f"(cancel {plan.get('cancels',0)}/create {plan.get('creates',0)}) "
          f"fails={plan.get('cancel_fail',0)}c/{plan.get('create_fail',0)}cr/"
          f"{plan.get('quote_fail',0)}q skipped={plan.get('create_skipped',0)} "
          f"badrows={plan.get('dropped_book_rows',0)} "
          f"capped={plan.get('capped_markets',0)} write_tokens={plan.get('write_tokens',0)} "
          f"reads={_reads[0]} committed=${plan.get('committed_usd', plan.get('est_capital_usd',0)):,.2f}"
          f"/{MAX_TOTAL_CAPITAL:,.0f} held=${plan.get('held_cost_usd',0):,.2f}"
          + (f" first_err={plan.get('first_quote_err')}" if plan.get("first_quote_err") else ""))
    return 0


def _touch(ob):
    """(best_yes_bid, best_yes_ask) from an orderbook_fp payload; None if absent.
    yes_ask == 1 - best_no_bid."""
    yl, _ = _levels(ob.get("yes_dollars") or [])
    nl, _ = _levels(ob.get("no_dollars") or [])
    yb = max((p for p, _ in yl), default=None)
    nb = max((p for p, _ in nl), default=None)
    return yb, (round(1.0 - nb, 4) if nb is not None else None)


def flatten_to_zero(client, ticker, standing_oids=None, tries=4):
    """LAST-RESORT taker de-risk of ONE ticker to flat — the sole taker path. Cancels our
    resting orders on the ticker first (avoid a self-trade cross), then crosses the residual
    with marketable IOC orders.

    OVERSHOOT-SAFE: reads the starting signed position ONCE and HARD-CAPS cumulative crossing
    at |pos0|, decrementing by the venue's CONFIRMED fill_count each pass (never by a possibly-
    lagging positions re-read — an eventually-consistent read could otherwise re-cross full
    size and flip a long into a short). The get_positions re-poll is a SECONDARY check only.
    Returns (flat_bool, n_crossed)."""
    for oid in (standing_oids or []):
        try:
            client.cancel_order(oid)
        except Exception:
            pass
    try:
        pos0 = _held_cost(client)[1].get(ticker, 0.0)      # STARTING signed position, read ONCE
    except Exception:
        return False, 0                                    # blind -> stop (fail closed)
    if abs(pos0) < INV_TOLERANCE:
        return True, 0
    long_yes = pos0 > 0
    remaining = int(round(abs(pos0)))                      # hard cap on cumulative crossing
    crossed = 0
    for _ in range(tries):
        if remaining < max(1, int(INV_TOLERANCE)):
            break
        try:
            ob = public_get(f"/trade-api/v2/markets/{ticker}/orderbook").get("orderbook_fp") or {}
        except Exception:
            break
        yb, ya = _touch(ob)
        price, side = (yb, "ask") if long_yes else (ya, "bid")   # long yes->sell yes; long no->buy yes
        if price is None or not (0.01 <= price <= 0.99):
            break
        try:
            resp = client.create_order_v2(ticker, side, remaining, price,
                                          time_in_force="immediate_or_cancel", post_only=False)
            o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
            o = o or {}
            fill = float(o.get("fill_count") or 0)           # CONFIRMED fill (venue-authoritative)
            # IOC must never rest. If the venue returned a still-open order (didn't honor IOC),
            # cancel it so a naked, non-post_only taker order can't linger past this pass (fix G).
            if str(o.get("status") or "").lower() in ("resting", "open", "active"):
                try:
                    client.cancel_order(o.get("order_id"))
                except Exception:
                    pass
            remaining -= int(round(fill))
            crossed += 1
            if fill <= 0:
                break                                        # nothing at the touch; don't spin
        except Exception:
            break
    # SECONDARY consistency check (never the driver); fall back to our own confirmed count.
    try:
        return abs(_held_cost(client)[1].get(ticker, 0.0)) < INV_TOLERANCE, crossed
    except Exception:
        return remaining < max(1, int(INV_TOLERANCE)), crossed


def _flatten_all(client):
    """EMERGENCY-STOP de-risk: MAKER-FIRST with BOUNDED ESCALATION (audit HIGH-1).
    Pure-taker STOP is a fire-sale (realizes the loss + pays the spread); pure-maker STOP can
    leave the book hanging on offsets that never fill — as wrong in the other direction. So:
      1. cancel every resting quote (stop making),
      2. rest a PASSIVE maker offset on the reducing side of each held position ($0 fee),
      3. WAIT STOP_ESCALATE_S for them to fill,
      4. re-read; whatever is STILL >= STOP_TAKER_MIN_CT gets taker-crossed — bounded, sized
         to the residual only, never the whole book. Below-threshold residue is left/reported."""
    try:
        orders = client.get_orders("resting").get("orders") or []
    except Exception as e:
        print(f"flatten: could NOT read resting orders ({e!r}) — run flatten_kalshi.py manually")
        orders = []
    n = 0
    for o in orders:
        try:
            client.cancel_order(o["order_id"]); n += 1
        except Exception:
            pass
    print(f"flatten: cancelled {n}/{len(orders)} resting quotes (stopped making)")
    try:
        _tot, _by, _costs = _held_cost(client)
        _naked = ladder_pairing(_by)
        _paired = sum(abs(_by[t]) - abs(_naked.get(t, 0)) for t in _by)
        if _paired > 0:
            print(f"flatten: {_paired:.0f} ct held in FLOORED ladder pairs (risk ~ strike gap) "
                  f"— left to settle, offsetting only the naked remainder")
        held = {t: p for t, p in _naked.items() if abs(p) >= INV_TOLERANCE}
    except Exception as e:
        print(f"flatten: could NOT read positions ({e!r}) — inventory MAY remain, check manually")
        return
    if not held:
        print("flatten: no material inventory — book is flat")
        return
    # --- pass 1: MAKER offsets on the reducing side ---
    # per-invocation nonce so a REPEATED STOP run (timer still firing while STOP sentinel present)
    # never reuses a client_order_id — Kalshi dedups on it, so a reused id would reject the fresh
    # offset and force the taker escalation, turning maker-first STOP into a metronomic taker
    # fire-sale on every cycle after the first (review C5).
    _nonce = int(time.time())
    offset_oids = {}                                   # ticker -> our offset order id
    for i, (t, pos) in enumerate(held.items()):
        try:
            ob = public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
        except Exception:
            print(f"flatten: {t} pos={pos:+.2f} — book unreadable, will re-check at escalation")
            continue
        by = max((p for p, _ in _levels(ob.get("yes_dollars") or [])[0]), default=None)
        bn = max((p for p, _ in _levels(ob.get("no_dollars") or [])[0]), default=None)
        # reducing side (maker): long yes -> rest a NO bid; long no -> rest a YES bid.
        if pos > 0 and bn is not None and MIN_PRICE_DOLLARS < bn <= MAX_PRICE_DOLLARS:
            side, price, other = "no", bn, (by or bn)
        elif pos < 0 and by is not None and MIN_PRICE_DOLLARS < by <= MAX_PRICE_DOLLARS:
            side, price, other = "yes", by, (bn or by)
        else:
            print(f"flatten: {t} pos={pos:+.2f} — reducing side unpriceable, will re-check at escalation")
            continue
        price = _unwind_price(price, _costs.get(t, 0.0))             # loss-capped offset
        if price <= MIN_PRICE_DOLLARS:
            print(f"flatten: {t} pos={pos:+.2f} — loss-cap leaves no priceable offset, "
                  f"will re-check at escalation")
            continue
        cnt = _unwind_size(_capped_join(price, other), price, pos)   # <= |pos|, never overshoot
        try:
            r = client.create_quote(t, side, price, cnt, post_only=True,
                                    client_order_id=f"mk-stopflat-{_nonce}-{i}-{side}")
            o = r.get("order") if isinstance(r, dict) and isinstance(r.get("order"), dict) else {}
            if o.get("order_id"):
                offset_oids[t] = o["order_id"]
            print(f"flatten: {t} pos={pos:+.2f} -> rested MAKER offset {side} {cnt}@{price} (passive)")
        except Exception as e:
            print(f"flatten: {t} pos={pos:+.2f} — offset REJECTED ({e!r}), will re-check at escalation")
    # --- pass 2: bounded escalation — give passive a real chance, then taker the RESIDUAL ---
    if STOP_ESCALATE_S > 0:
        print(f"flatten: waiting {STOP_ESCALATE_S}s for passive offsets to fill...")
        time.sleep(STOP_ESCALATE_S)
    try:
        residual = {t: p for t, p in _held_cost(client)[1].items() if abs(p) >= STOP_TAKER_MIN_CT}
    except Exception as e:
        print(f"flatten: could NOT re-read positions ({e!r}) — offsets remain resting; check manually")
        return
    if not residual:
        print("flatten: passive offsets cleared the book (or residue below taker threshold) — done")
        return
    if not TAKER_FLATTEN:
        print(f"flatten: {len(residual)} residual position(s) but TAKER_FLATTEN=0 — left resting, check manually")
        return
    for t, pos in residual.items():
        oids = [offset_oids[t]] if t in offset_oids else None   # pull our offset first (self-trade guard)
        ok, c = flatten_to_zero(client, t, oids)
        print(f"flatten: ESCALATED {t} pos={pos:+.2f} -> taker residual "
              f"{'FLAT' if ok else 'RESIDUAL (check manually)'} ({c} crosses)")


def naked_held_cost(held_by, cost_by):
    """Cost basis of the NAKED (unhedged) portion of inventory only.

    The risk gates (HELD_MAX level breaker, velocity breaker) must measure RISK, not gross
    position. A ladder pair (+yes on a lower strike vs +no on a higher one) is floored at >= $1
    per pair — its downside is the strike gap, pennies — yet counting its full cost against the
    ceiling pinned the bot in REDUCE-ONLY over risk it does not carry, blocking all reward
    earning (observed live 2026-07-22: $27.76 held of which $11.84 was fully paired, so the bot
    sat out on ~$12 of near-riskless inventory). Cost basis is per-contract, so scale each
    ticker's cost by the fraction of it that is still naked. Falls back to |naked| x $1 when the
    venue omitted the cost field — conservative, never understates."""
    naked = ladder_pairing(held_by)
    total = 0.0
    for t, n in (naked or {}).items():
        if not n:
            continue
        per_ct = cost_by.get(t)
        total += abs(n) * per_ct if per_ct else abs(n)
    return total


def _held_cost(client):
    """(total_cost, {ticker: signed_contracts}, {ticker: avg_cost_per_contract}) of held
    inventory (fills). Cost is CONSERVATIVE — each held contract can be worth up to $1, so
    |pos|*1 reserves the max. Real committed capital must include this, not just the resting
    book. The per-ticker avg cost feeds the unwind loss cap (only when the venue reports
    market_exposure_dollars; absent -> no entry -> cap disabled for that ticker, never faked).
    RAISES if positions cannot be read — the caller must fail CLOSED (defer creates),
    never treat unknown inventory as $0 (matches the standing-read/reconcile guards)."""
    pos = client.get_positions()          # may raise -> caller defers all creates
    by, costs, total = {}, {}, 0.0
    for p in (pos.get("market_positions") or []):
        # PROD-VERIFIED 2026-07-20: field is position_fp (string, fractional, signed);
        # 'position' does not exist -> reading it silently blinded the committed cap.
        n = float(p.get("position_fp") or p.get("position") or 0)
        if not n:
            continue
        by[p.get("ticker")] = n
        # REAL reserved cost, not |pos|*$1 (8x over-conservative -> tripped the cap at half
        # real capital and deadlocked the unwind). market_exposure_dollars = actual cost.
        me = float(p.get("market_exposure_dollars") or 0)
        total += me if me else abs(n)
        if me:
            costs[p.get("ticker")] = me / abs(n)
    return total, by, costs


def _strike_of(ticker):
    """Numeric strike from a ladder ticker: everything AFTER the SERIES-EVENT prefix, so a
    NEGATIVE strike keeps its sign ('KXCPI-26SEP-T-0.4' -> -0.4, verified live on the public
    API). Taking only the last dash-field silently returned +0.4 there — and a sign flip inverts
    strike ordering, which would let ladder_pairing mark a genuinely UNFLOORED combo as paired
    and strip every guard from it (review 07-22; sub-zero-F winter temp strikes are the live
    exposure). None when unparseable -> that ticker never participates in pairing."""
    try:
        parts = ticker.split("-")
        if len(parts) < 3:
            return None
        return float("-".join(parts[2:]).lstrip("T"))
    except (ValueError, AttributeError, TypeError):
        return None


def ladder_pairing(held_by):
    """LADDER SELF-HEDGE (operator directive 2026-07-22): within one 'above X' event, long-YES
    on a LOWER strike + long-NO on a HIGHER strike is a FLOORED pair — outcome <= low strike
    pays the NO, outcome > high strike pays the YES, in between BOTH pay: settlement returns
    >= $1 per matched pair, so the risk is ~the strike gap (pennies on adjacent strikes), not
    the position. Match greedily (lowest longs vs highest shorts, strictly long_strike <
    short_strike) and return naked_by = the UNMATCHED signed remainder per ticker. Paired
    quantity is EXCLUDED from unwind targeting, throttle direction, the settle-taker and STOP
    offsets — unwinding both legs of a floored pair pays two spreads to shed penny risk.
    (The opposite-direction combo — yes on the HIGHER strike, no on the LOWER — has NO floor
    and is deliberately never matched.) Pairing conserves the event's signed sum, so
    event_deltas is unchanged either way."""
    naked = dict(held_by)
    by_event = defaultdict(list)
    for t, n in (held_by or {}).items():
        s = _strike_of(t)
        if s is not None and n:
            by_event[_event_key(t)].append((s, t))
    for rows in by_event.values():
        pos = sorted((s, t) for s, t in rows if naked.get(t, 0) > 0)            # longs, low first
        neg = sorted(((s, t) for s, t in rows if naked.get(t, 0) < 0), reverse=True)  # shorts, high first
        i = j = 0
        while i < len(pos) and j < len(neg):
            ls, lt = pos[i]
            ss, st_ = neg[j]
            if ls >= ss:
                break                              # long strike not strictly below short strike
            m = min(naked[lt], -naked[st_])
            naked[lt] -= m
            naked[st_] += m
            if naked[lt] == 0:
                i += 1
            if naked[st_] == 0:
                j += 1
    return naked


def event_deltas(held_by):
    """Aggregate SIGNED net position across the strikes of each event. Kalshi ticker =
    SERIES-EVENT-STRIKE; strikes of one nested-threshold ladder ('above X') are
    DIRECTIONALLY correlated, so the event aggregate — not the per-ticker position — is the
    true directional exposure. Returns {event_key: signed_delta}."""
    ev = defaultdict(float)
    for t, n in (held_by or {}).items():
        ev["-".join(t.split("-")[:2])] += n
    return dict(ev)


def _event_key(ticker):
    return "-".join(ticker.split("-")[:2])


def _live_standing(client):
    """Returns (standing_dict, raw_row_count). Reads resting V2 orders back into our
    (outcome, outcome-price) form. Per-order parse is ISOLATED so one malformed
    record cannot crash the cycle before cancels/wind-down run. The raw_row_count
    lets the caller reconcile (rows>0 but parsed==0 => parse failure => halt)."""
    out = defaultdict(list)
    orders = client.get_orders("resting").get("orders") or []
    for o in orders:
        try:
            outcome = o.get("outcome_side")   # 'yes' | 'no'
            price_str = o.get(f"{outcome}_price_dollars") if outcome else None
            if outcome is None or price_str is None:
                continue
            cnt = o.get("remaining_count_fp") or o.get("remaining_count") or \
                o.get("initial_count_fp") or o.get("count") or 0
            out[o["ticker"]].append({"side": outcome, "price_dollars": float(price_str),
                                     "count": int(float(cnt)), "order_id": o["order_id"]})
        except Exception:
            continue
    return dict(out), len(orders)


def report():
    import glob
    rows = []
    for p in sorted(glob.glob(os.path.join(DATA_DIR, "plans-*.jsonl"))):
        for line in open(p):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        print("no plan data yet")
        return 0
    n = len(rows)
    print(f"cycles={n} window={rows[0]['ts'][:16]} .. {rows[-1]['ts'][:16]}")
    for k in ("footprint", "quoted_markets", "order_ops", "activate_markets",
              "est_capital_usd", "gated_out"):
        vals = sorted(r.get(k, 0) for r in rows)
        print(f"{k:18} mean={sum(vals)/n:9.1f}  p50={vals[n//2]:9.1f}  max={vals[-1]:9.1f}")
    span_days = max((parse_iso(rows[-1]["ts"]) - parse_iso(rows[0]["ts"])).total_seconds() / 86400, 1e-6)
    total_tokens = sum(r.get("write_tokens", r.get("order_ops", 0) * 10) for r in rows)
    print(f"\nwrite-token/day pace: {total_tokens / span_days:,.0f}  "
          f"(create=10/cancel=2 tok; Basic write = 100 tok/s = 8.64M tok/day — "
          f"peak per-cycle burst must stay <100 tok/s, our 0.16s spacing caps it ~63)")
    return 0


if __name__ == "__main__":
    sys.exit(report() if "--report" in sys.argv else run_once())

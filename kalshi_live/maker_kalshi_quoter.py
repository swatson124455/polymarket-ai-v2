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

# --- SILENT-FAILURE COUNTERS ---------------------------------------------------------------------
# An audit of all 62 exception handlers found 14 that swallow the error with no counter and no log.
# Most are harmless (telemetry, best-effort cleanup) but four sit where silence hides a REAL fault:
#   ioc_cancel_fail   the venue left an IOC order RESTING and our cancel failed -> a naked,
#                     non-post_only taker order lingers. The code exists precisely to stop that.
#   standing_row_skip a malformed resting-order row is skipped -> we see FEWER standing orders than
#                     exist -> the diff re-creates them -> DUPLICATE orders. This is the shape the
#                     *_fp field-rename footgun produces.
#   rank_fail         score ranking threw -> we silently fall back to pool order while believing we
#                     are ranking on capture.
#   flatten_cancel_fail  a pre-flatten cancel failed -> a maker order can still fill mid-flatten.
# Counting only. NO control flow is changed by this — swallowing is often the right behaviour;
# doing it INVISIBLY is not.
_SILENT = defaultdict(int)          # defaultdict, not Counter: only `defaultdict` is imported here


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
# EVERY KALSHI_* knob this module reads, registered AS IT IS READ. A knob absent from
# live.env silently takes its code default and nothing logged it — that is how
# KALSHI_THROTTLE_SMART sat OFF in production unnoticed, and how KALSHI_PRECLOSE_FLATTEN
# (the purpose-built defence against riding naked inventory into settlement) was built,
# tested and never switched on. Audited 2026-07-26: the module read 67 knobs, live.env set
# 34 — 37 (55%) were on defaults nobody had chosen. Registering inside the accessors means
# the list cannot drift from the code the way a hand-maintained one would.
_ENV_DECLARED = {}                      # name -> default as passed at the call site

# Absence of these specific knobs means a PROTECTION IS OFF, so they are named in the log
# rather than only counted. Everything else lands in the plan row's `env_absent`.
_SAFETY_KNOBS = ("KALSHI_PRECLOSE_FLATTEN", "KALSHI_TAKER_FLATTEN", "KALSHI_THROTTLE_SMART",
                 "KALSHI_CAPTURE_GATE", "KALSHI_STANDDOWN", "KALSHI_NETEV_GATE",
                 "KALSHI_MAX_UNWIND_LOSS", "KALSHI_HELD_MAX_USD",
                 "KALSHI_DAILY_LOSS_HALT_USD", "KALSHI_DAILY_DOWN_HALT_USD")


def _envi(name, default):
    _ENV_DECLARED[name] = default
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _envf(name, default):
    _ENV_DECLARED[name] = default
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _envb(name, default_on=False):
    """Register a boolean flag and evaluate it with the module's EXISTING semantics:
    present-and-'1' is ON. `default_on=True` mirrors the call sites that passed a default
    of "1" to os.environ.get; `default_on=False` mirrors the bare
    `os.environ.get(name) == "1"` (absent -> None -> False). Byte-identical behaviour to
    the inline expressions this replaces — the ONLY addition is registration."""
    _ENV_DECLARED[name] = "1" if default_on else "0"
    return os.environ.get(name, "1" if default_on else None) == "1"


def env_absent():
    """KALSHI_* knobs the code reads that the environment does NOT set (-> code default)."""
    return sorted(k for k in _ENV_DECLARED if k not in os.environ)


FOOTPRINT_TOP = _envi("KALSHI_FOOTPRINT_TOP", 60)   # markets quoted per cycle
PER_SERIES_CAP = _envi("KALSHI_PER_SERIES_CAP", 10)
# PIVOT-SELECT (KALSHI_PIVOT_SELECT, default 0 = OFF, provable no-op). OFF => the legacy
# egalitarian round-robin select_footprint AND the legacy quote loop run byte-for-byte. ON =>
# select_footprint over-selects a density-weighted, near-money-ordered candidate pool (larger
# than FOOTPRINT_TOP) and the quote loop PIVOTS past markets that gate out (return []) —
# pulling the NEXT eligible market into the slot — until FOOTPRINT_TOP markets are actually
# QUOTED or the pool is exhausted. The GATES are untouched: a gated (non-earning) market is
# still skipped, never quoted; pivot means quoting a DIFFERENT earner, not relaxing a gate.
PIVOT_SELECT = _envi("KALSHI_PIVOT_SELECT", 0)      # 0 = legacy select+quote (provable no-op)
PIVOT_POOL_MULT = _envi("KALSHI_PIVOT_POOL_MULT", 2)   # candidate pool = MULT * FOOTPRINT_TOP
PIVOT_COVERAGE = _envi("KALSHI_PIVOT_COVERAGE", 1)     # min slots/series before density fill
PIVOT_READ_RESERVE = _envi("KALSHI_PIVOT_READ_RESERVE", 30)  # reads held back (strand/ladder/settle)
JOIN_SIZE = _envi("KALSHI_JOIN_SIZE", 100)          # contracts/side on non-void markets
# REWARDS ARE PAID FOR QUOTES ON THE BOOK, NOT INVENTORY HELD. So BOTH sides must stay live
# every cycle — the throttle SHRINKS the accumulating side but never pulls it to zero (that
# would kill the reward on that side). This is the floor it shrinks toward.
MIN_QUOTE_CT = _envi("KALSHI_MIN_QUOTE_CT", 2)      # never quote a live side below this
# --- STAND-DOWN (KALSHI_STANDDOWN, default 0 = OFF, provable no-op) -----------------------------
# The bot has no "should I even be playing right now?" brain: it farms LIP mechanically, so on a
# day when the flagship temp reward (~91% of reward income) is DARK it churns thin gas and every
# gas fill is a small adverse-selection loss with no reward to cover it (measured ~-$9 on 07-23,
# mostly ONE ATM gas strike run over by one-way flow). Our realized trading edge BEFORE rewards is
# NEGATIVE (fingerprint ~-$0.011/ct on GAS, worse on temp); LIP rewards are the ONLY thing that
# makes the book +EV. So when a market's reward is too thin to justify the expected fill loss, OPEN
# LESS there instead of full-size — a dead day then costs ~$0 instead of bleeding.
#
# MECHANISM (per-market reward-density gate — the robust core of the operator's option A, without
# the fragile "our-share" proxy): each footprint row already carries usd_day = period_reward/10000
# /days = the R1-normalized LIP $/day pool for THAT market (select_footprint). No extra API read is
# needed — the reward number and the void (one-sided) flag are already in-cycle. R3 (two-sidedness)
# adjustment: a one-sided/void book scores at risk, so its effective density is discounted by
# STANDDOWN_VOID_MULT. When the effective density is below STANDDOWN_MIN_USD_DAY the market is
# "stood down":
#   JOIN (two-sided) books  -> the ACCUMULATING side(s) are sized to MIN_QUOTE_CT (both sides stay
#                              LIVE at reference so the snapshot still qualifies and still earns the
#                              thin reward, but each fill is ~JOIN_SIZE/MIN_QUOTE_CT smaller => the
#                              adverse loss shrinks in proportion). The REDUCING/unwind side is NOT
#                              touched here — it is (re)sized by the unwind block below, so de-risk
#                              is never blocked or down-sized.
#   ACTIVATE (void) books    -> skipped entirely when FLAT (committing MAX_ACTIVATE_CAPITAL of Target
#                              depth into a thin-reward book is the opposite of "open less"). Held
#                              inventory on a void market still rests its reducing side (handled
#                              above, before this gate) — de-risk always proceeds.
# CALIBRATION: the floor is a REGIME SEPARATOR, not a fitted EV constant. Live reference densities
# (code comments / ledger): flagship temp ~$1,920/day per strike, live gas ~$150/day, a dead day
# leaves only sub-$10/day dregs. The default $20/day sits with a wide margin BELOW live gas (so a
# normal reward-present day keeps quoting gas at full size — requirement: never forfeit the +EV gas
# lane) and ABOVE the dead-day dregs (so a genuinely dark regime sizes down). Tune against the
# ledger; because the flag ships OFF the default only takes effect once the operator flips it.
STANDDOWN = _envi("KALSHI_STANDDOWN", 0)            # 0 = today exact behavior, byte-for-byte
STANDDOWN_MIN_USD_DAY = _envf("KALSHI_STANDDOWN_MIN_USD_DAY", 20.0)   # reward-density floor ($/day)
STANDDOWN_VOID_MULT = _envf("KALSHI_STANDDOWN_VOID_MULT", 0.5)        # R3 discount for one-sided books
MAX_ACTIVATE_CAPITAL = _envf("KALSHI_MAX_ACTIVATE_CAPITAL", 150.0)  # $/void market
MAX_MARKET_CAPITAL = _envf("KALSHI_MAX_MARKET_CAPITAL", 250.0)  # $ cap per market (both sides)
MAX_TOTAL_CAPITAL = _envf("KALSHI_MAX_TOTAL_CAPITAL", 10000.0)  # $ cap on the whole resting book
# --- CAPTURE GATE (KALSHI_CAPTURE_GATE, default 0 = OFF, provable no-op) -------------------------
# The market-quality BRAIN the unqualifiable/selection gates lack. Those two ask "can ANYONE earn
# here?" (is the BOOK two-sided at Target Size). This asks "can WE earn here?" — our PROSPECTIVE R4
# capture. LIP reward is pro-rata by DF^N-weighted qualifying score, so against a deep rival book
# our own resting size is a rounding error -> our reward ~= $0 while we still carry adverse-fill
# risk. Measured gap: KXTRUMPENDORSEMENTS-...-A15 holds capital with a two-sided book but our R4
# qualifying share is ~0; saturated big pools (KXFUNDRAISING) fill Target with rival depth so our
# 20-ct share ~= 0. The unqualifiable/selection gates PASS both; this gate SKIPS both.
#
# MECHANISM (per-market, JOIN/two-sided books only, no extra API read): compute our PROSPECTIVE
# snapshot share if we rested our intended join size AT reference — our_size scored at ref /
# (book qualifying score + our_size), R3 two-sided (both sides must qualify) — and multiply by the
# R1 pool (m['usd_day']) already on the footprint row. Below CAPTURE_MIN_USD_DAY the market is POOR
# FOR US: skip when FLAT, go reduce-only (rest only the reducing side, full |inv|) when HOLDING —
# de-risk is NEVER blocked or down-sized. This is a MODEL (M7 over-predicts 2-6x) used as a RELATIVE
# gate signal against a calibrated floor, NOT a ledgered EV.
#
# FLOOR (KALSHI_CAPTURE_MIN_USD_DAY=5.0 model $/day): a REGIME separator, not a fitted EV. On a
# normal near-money GAS book we are a real fraction of a shallow qualifying set -> model capture
# ~$10-40/day, several x above $5 (never starve the +EV gas lane). Where our size is a rounding
# error vs a deep rival book -> model capture ~$1/day, below $5. M7 haircut baked in: a $5 model
# floor ~= $0.8-2.5 real $/day. Calibrate against actual period-close credits via the
# capture_min_pc_usd_day telemetry. Ships OFF -> the default only bites once the operator flips it.
# vs STAND-DOWN (KALSHI_STANDDOWN, built, NOT deployed): that uses pool DENSITY only; THIS gate uses
# our actual SHARE x pool and is the more complete signal — the PRIMARY market-quality gate.
# Stand-down can stay OFF; the two compose harmlessly if both are on (stand-down shrinks size,
# this skips/reduces).
CAPTURE_GATE = _envi("KALSHI_CAPTURE_GATE", 0)                 # 0 = today's exact behavior, byte-for-byte
CAPTURE_MIN_USD_DAY = _envf("KALSHI_CAPTURE_MIN_USD_DAY", 5.0)  # model $/day floor (see above)
CAPTURE_DF_DEFAULT = _envf("KALSHI_CAPTURE_DF", 0.5)          # discount_factor_bps=5000 => 0.50 (live)

# --- PER-MARKET REWARD TELEMETRY (KALSHI_MKT_TELEMETRY, default 1 = ON) --------------------------
# WHY: "what actually pays" is currently unanswerable, and the blocker is telemetry, not analysis.
# Plan rows are per-CYCLE, so the three events in the hour closing 2026-07-22T10:00Z that paid
# $12.94 / $1.51 / $0.00 shared the same cycles and therefore share ONE at_ref_pct — the log cannot
# discriminate the events it needs to. Pool size does not separate them either (975 vs 942 $/day).
# R4 pays pro-rata by DF^N-weighted qualifying score, so the variable that separates paid from zero
# is the COMPETING QUALIFYING DEPTH (the score denominator) — which nothing records today. This
# emits ONE ROW PER MARKET PER CYCLE to quotes-YYYYMMDD.jsonl: our intended size, our price vs the
# reference, and the rival qualifying book. Those are the three columns a reward model needs.
#
# COSTS NOTHING, RISKS NOTHING: pure observation over the orderbook the cycle ALREADY fetched — zero
# extra API reads, zero write tokens — and it never feeds a gate. Every call site is wrapped so a
# telemetry fault can never break a trading cycle.
#
# IT ALSO RUNS WHILE PARKED. The book is fetched and desired_quotes is evaluated for every footprint
# market BEFORE the capital cap gates the create, so at KALSHI_MAX_TOTAL_CAPITAL=1 every column is
# still measured, with our resting size honestly recorded as 0. That makes the competition
# denominator observable on markets we have NEVER quoted — including KXTEMP* the moment its hourly
# programs return — without resting a single contract.
MKT_TELEMETRY = _envi("KALSHI_MKT_TELEMETRY", 1)

# --- PRESENCE GATE (KALSHI_PRESENCE_GATE, default 0 = OFF, provable no-op) -----------------------
# THE DEFECT IT CORRECTS: LIP snapshots the book every second and SUMS the scores, so reward is an
# INTEGRAL over the window — proportional to size x SECONDS RESTING. `_prospective_capture` returns
# an INSTANTANEOUS $/day, i.e. it assumes we rest the whole window at that share. Measured from the
# venue's own order history (980 orders / 91 markets, 2026-07-20..07-25): MEDIAN presence 5.7%,
# mean 11.9%. So the instantaneous number can overstate the real credit by better than an order of
# magnitude, and it does so WORST exactly where it is most tempting — long-window markets, where
# the daily pool looks generous and our coverage is 1-2%.
#
# TWO MULTIPLIERS, DELIBERATELY SEPARATE (they are not the same kind of number):
#   window fraction left  STRUCTURAL, exact, known from the program's own clock. Entering a window
#                         80% through caps the score near 20% however well we execute.
#   presence factor       EMPIRICAL, from the calibration table, and partly a record of OUR OWN
#                         defects (42% of historical orders died inside one 120s cycle). DEFAULTS
#                         TO 1.0 — an ABSENT table must never make the bot pessimistic; only a
#                         MEASURED number may lower the estimate.
# Because the empirical half can encode our own bugs, a skip driven by it is a potential death
# spiral (never re-enter -> never improve). So the gate ALSO records what it would have decided at
# presence=1.0 (`presence_skipped_execution_only`): the difference is the part that is US, not the
# market, and it is the number to watch after any fill/uptime fix.
#
# THE FLOOR IS ECONOMIC, NOT A PREFERENCE: Kalshi documents "Minimum payout: $1.00 (rounded down to
# nearest cent)". Below a dollar the credit is ZERO while the fill risk is unchanged — so quoting
# a market that cannot clear it for the remaining window is paying for a lottery ticket with no
# prize. Scope caveat: the help text does not state $1.00 per WHAT; our 31 reward rows are
# consistent with per market per period and the gate assumes that reading.
#
# WHY THE GATE SITS AT $1.20, NOT $1.00 (operator decision 2026-07-25): the estimate feeding this
# gate is a MODEL — it is known to over-predict, the presence factor is measured off a small sample,
# and the venue floor is a CLIFF (a cent short pays zero, not 99 cents). Setting the gate at the
# cliff edge means every modelling error lands on the wrong side of it. A 20% margin buys room to
# be imprecise, and what it gives up is worth pennies against the fill risk of holding a position
# in a market that was never going to pay.
# --- AMEND-ON-DECREASE (KALSHI_AMEND_DECREASE, default 0 = OFF, provable no-op) -----------------
# diff_orders survives an order only on an exact (side, price, count) match, so trimming a resting
# order by ONE contract cancels it and rebuilds it at the BACK of the queue. Kalshi preserves queue
# position for a size DECREASE and nothing else, so routing just that case through amend is free
# time-on-book with no behavioural trade-off. Increases and reprices forfeit queue either way and
# deliberately keep the existing path.
# ⚠ The amend endpoint is UNVERIFIED against the live venue (exercising it would mutate real
# resting orders on a parked account), which is why this ships OFF and its first live cycle needs
# watching.
AMEND_DECREASE = _envi("KALSHI_AMEND_DECREASE", 0)

# --- DROP HYSTERESIS (KALSHI_DROP_GRACE, default 0 = OFF, provable no-op) ------------------------
# ROOT of the "identical churn" the order forensics found: 17 of 478 zero-fill cancels on the clean
# slice were followed by a re-create at the SAME price AND size. diff_orders cannot do that — an
# exact (side, price, count) match survives. It happens when a ticker falls out of `desired`
# ENTIRELY for one cycle and returns the next: the diff cancels its whole book, then rebuilds it.
# The common benign cause is FOOTPRINT ROTATION — the pool-ordered top-N shuffles and a market drops
# out for a cycle with nothing about it having changed. We pay a full teardown and lose queue
# position for a market we still want.
#
# NARROW BY DESIGN — grace applies ONLY when the ticker is absent from this cycle's FOOTPRINT (we
# never looked at it). It does NOT apply when the market WAS looked at and something rejected it:
# a gate, the capital cap, the breaker, wind-down. Those are decisions, and retaining through a
# decision would defeat the cap that made it. Grace is for "we didn't check", never for "we said no".
DROP_GRACE = _envi("KALSHI_DROP_GRACE", 0)          # cycles a rotated-out ticker keeps its book

# --- SCORE-BASED RANKING (KALSHI_SCORE_RANK, default 0 = OFF, provable no-op) --------------------
# Selection ranks by usd_day, the reward POOL — and pool ALONE is the wrong key. Stated precisely,
# because it is easy to get backwards: the pool MATTERS DIRECTLY. It is a LINEAR MULTIPLIER in the
# reward (reward = share x pool), so doubling it doubles the money. It is never dropped.
# What makes it a poor RANK key is that it is one of TWO terms and the one that varies LESS.
# Measured over 30 series / 165 book-side depth readings (venue_scan.json 2026-07-25): pool spans
# 6x ($1,750-$10,470/day); rival qualifying depth, which sets `share`, spans 71,330x (1-71,330).
# Sorting on the 6x term while ignoring the 71,330x term gets the order wrong — KXFUNDRAISING, the
# venue's biggest pool at $10,470/day, models to $5.65/day because it is crowded, while
# KXVOGUECOVER at $1,800/day models to $42.03/day because it is nearly empty.
# So the key used here is the PRODUCT (capture = share x pool), at full pool weight.
#
# Capture needs the orderbook and ranking happens BEFORE books are read, so the score is carried
# ACROSS CYCLES: every cycle scores the books it already read (free — same numbers the per-market
# telemetry emits) and the next cycle ranks on them. EXPLORE reserves slots for never-seen markets
# so the venue keeps being swept; without it the bot converges on whatever it read first and never
# discovers anything better. Scores DECAY toward the pool prior so a stale winner cannot pin it.
#
# SWING PENALTY: a market whose reference price moves between cycles fills us adversely — that is
# how a maker hands the rewards back. ref_move discounts the score and costs nothing to collect.
SCORE_RANK = _envi("KALSHI_SCORE_RANK", 0)
SCORE_EXPLORE = _envi("KALSHI_SCORE_EXPLORE", 10)     # slots/cycle reserved for unscored markets
SCORE_SWING_PENALTY = _envf("KALSHI_SCORE_SWING_PENALTY", 1.0)
SCORE_UNKNOWN_BONUS = _envf("KALSHI_SCORE_UNKNOWN_BONUS", 1.0)
SCORE_PATH = os.environ.get("KALSHI_SCORE_PATH", os.path.join(DATA_DIR, "kalshi_market_scores.json"))


def _load_scores():
    """Fail-OPEN to {} -> every market unscored -> ranking is byte-for-byte the legacy pool order."""
    try:
        import kalshi_market_scores
        return kalshi_market_scores.load(SCORE_PATH)
    except Exception:
        return {}


# loaded ONCE at import and ONLY when the flag is on (flag-off does zero file IO -> provable no-op)
SCORES = _load_scores() if SCORE_RANK else {}

PRESENCE_GATE = _envi("KALSHI_PRESENCE_GATE", 0)              # 0 = today's exact behavior
VENUE_PAYOUT_FLOOR_USD = 1.00                                 # Kalshi's documented minimum payout
MIN_CREDIT_USD = _envf("KALSHI_MIN_CREDIT_USD", 1.20)         # venue floor + 20% modelling margin
PRESENCE_DEFAULT = _envf("KALSHI_PRESENCE_DEFAULT", 1.0)      # no table -> assume perfect execution
PRESENCE_TABLE_PATH = os.environ.get(
    "KALSHI_PRESENCE_TABLE", os.path.join(DATA_DIR, "kalshi_presence_table.json"))
# Bucketing kept LOCAL (byte-equal to kalshi_presence_calibrate) so the calibration module stays out
# of the live quoter's import graph — same discipline as the _qualifying_score and net-EV replicas.
PRESENCE_LIFE_BUCKETS = ((0, 6, "0-6h"), (6, 24, "6-24h"), (24, 96, "1-4d"),
                         (96, 336, "4-14d"), (336, 1e9, "14d+"))


def _life_bucket(life_hours):
    for lo, hi, name in PRESENCE_LIFE_BUCKETS:
        if lo <= life_hours < hi:
            return name
    return "14d+"


def _load_presence_table():
    """Fail-OPEN to {} -> every market uncalibrated -> presence defaults to 1.0 and the gate can
    only ever be blocked by the STRUCTURAL half. A bad file never blocks trading."""
    try:
        import kalshi_presence_calibrate
        return kalshi_presence_calibrate.load_table(PRESENCE_TABLE_PATH)
    except Exception:
        return {}


# loaded ONCE at import and ONLY when the flag is on (flag-off does zero file IO -> provable no-op)
PRESENCE_TABLE = _load_presence_table() if PRESENCE_GATE else {}


def _presence_factor(ticker, life_min):
    """Measured share of the window we actually stay resting. 1.0 when uncalibrated."""
    if not PRESENCE_TABLE or not life_min:
        return PRESENCE_DEFAULT
    fam = _netev_family(ticker)                      # same family buckets as the net-EV gate
    row = (PRESENCE_TABLE.get(f"{fam}|{_life_bucket(life_min / 60.0)}")
           or PRESENCE_TABLE.get(f"*|{_life_bucket(life_min / 60.0)}"))
    if not row:
        return PRESENCE_DEFAULT
    v = row.get("presence_median")
    return float(v) if isinstance(v, (int, float)) and v > 0 else PRESENCE_DEFAULT


def _window_frac_left(m, now):
    """Fraction of THIS program's window still ahead of us, in [0,1]. Structural and exact."""
    life_min = float(m.get("life_min") or 0.0)
    if life_min <= 0 or not m.get("end"):
        return 1.0                                   # unknown -> do not penalise
    try:
        left_min = (parse_iso(m["end"]) - now).total_seconds() / 60.0
    except Exception:
        return 1.0
    return max(0.0, min(1.0, left_min / life_min))


def _expected_credit_usd(m, yl, nl, best_y, best_n, target, now):
    """Credit we can still earn HERE in ONE PAYOUT PERIOD, in dollars.

    ⚠ WHY ONE PAYOUT PERIOD AND NOT THE WHOLE REMAINING WINDOW — the conservative reading.
    Kalshi's LIP page states the floor ("Minimum payout: $1.00, rounded down to nearest cent") but
    NEVER states what unit it applies to. Two readings are live and they disagree for multi-day
    programs:
      per PERIOD : score is "the Sum of all your snapshot scores during the time period" and
                   "Time periods: Up to 31 days each" -> one payout per window.
      per DAY    : Kalshi help elsewhere says "your daily payout equals your score divided by the
                   total scores, multiplied by THAT DAY'S reward pool for that specific market".
    Under the per-DAY reading, scaling by the FULL remaining window is wrong in the DANGEROUS
    direction: a 7-day market earning $0.30/day shows $2.10, clears the floor, and then pays ZERO
    every single day. That is exactly the long, thin market an 8-day horizon makes visible.

    So the payout unit is taken as min(ONE DAY, remaining window). That is the smaller of the two
    readings, so a market clearing it clears under EITHER — and it collapses to the whole-window
    amount for sub-day programs (temp's ~58-minute windows), which is correct there.

    Every reward receipt we hold came from a period of a day or less (temp hourly, gas daily), so
    our own data cannot discriminate. `KXAAAGASW-26JUL27` is a ~6.5-day program crediting ~07-28;
    that one payout settles it, and this conservatism can be relaxed then.

    Returns (expected_usd, expected_usd_at_perfect_execution, frac_left) — both dollar figures are
    now PER PAYOUT PERIOD."""
    pc = _prospective_capture(m, yl, nl, best_y, best_n, target)     # $/day, instantaneous
    frac = _window_frac_left(m, now)
    days_left = (float(m.get("life_min") or 0.0) / 1440.0) * frac
    payout_days = min(1.0, days_left)               # the conservative unit — see above
    ideal = pc * payout_days
    return ideal * _presence_factor(m.get("ticker"), m.get("life_min")), ideal, frac
# --- NET-EV GATE (KALSHI_NETEV_GATE, default 0 = OFF, provable no-op) ----------------------------
# The RECEIPT-CALIBRATED market-quality brain. Every gate above (unqualifiable, selection, capture,
# stand-down) asks a REWARD question — "can the book pay / can WE capture a slice". None asks the
# NET question: a family can capture reward and STILL lose money if its adverse-fill bleed exceeds
# the reward. This gate answers it from ACTUAL RECEIPTS: kalshi_netev_calibrate turns the transaction
# CSV (credits) + per-trade fill P&L (fees included) into a per-FAMILY realized NET % of notional
# (canon §M8: GAS +1.1% ✅, TEMP −9.2% ❌), refreshed offline each export. Loaded from disk at
# startup -> NO extra per-cycle API read.
#   FLAT + family net < NETEV_MIN_MARGIN_PCT  -> SKIP (a net-negative family is money-losing to open).
#   HOLDING                                   -> REDUCE-ONLY (rest ONLY the reducing side at full |inv|,
#                                                a clone of the capture/wind-down reduce-only path) —
#                                                de-risk is NEVER blocked or down-sized.
#   UNPROVEN family (no receipt credits)      -> conservative R4 model fallback (§M7 haircut): open
#                                                only if prospective_capture/HAIRCUT − fill-fingerprint
#                                                > 0, else unproven-skip. Labelled model-not-receipt.
# Void/activate books are scoped OUT (as in the capture gate). This SUPERSEDES the pool-only
# KALSHI_STANDDOWN (density only) and the reward-only KALSHI_CAPTURE_GATE (model capture only): both
# answer a strictly weaker question. They compose harmlessly if co-enabled (each only skips/shrinks
# further), but net-EV is the complete signal. Ships OFF -> the default only bites once flipped.
NETEV_GATE = _envi("KALSHI_NETEV_GATE", 0)                     # 0 = today's exact behavior, byte-for-byte
NETEV_MIN_MARGIN_PCT = _envf("KALSHI_NETEV_MIN_MARGIN_PCT", 0.0)  # family net% floor (0 => skip net-negative)
NETEV_MODEL_HAIRCUT = _envf("KALSHI_NETEV_MODEL_HAIRCUT", 3.0)    # §M7 model over-prediction haircut (unproven)
NETEV_FINGERPRINT_USD_DAY = _envf("KALSHI_NETEV_FINGERPRINT_USD_DAY", 5.0)  # conservative fill cost, unproven series
NETEV_TABLE_PATH = os.environ.get("KALSHI_NETEV_TABLE",
                                  os.path.join(DATA_DIR, "kalshi_netev_table.json"))
# family rules kept LOCAL (byte-equal to kalshi_netev_calibrate.family_of, pinned in test_netev_gate)
# so the calibration module stays out of the live quoter's import graph (same discipline as the
# _qualifying_score replica).
NETEV_FAMILY_RULES = (("KXAAAGAS", "gas"), ("KXTEMP", "temp"))


def _netev_family(ticker):
    """Family bucket for the net-EV gate. Byte-equivalent to kalshi_netev_calibrate.family_of."""
    t = ticker or ""
    for prefix, fam in NETEV_FAMILY_RULES:
        if t.startswith(prefix):
            return fam
    return t.split("-")[0]


def _load_netev_table():
    """Load the calibration table from disk (fail-OPEN to {} -> every family unproven, never blocks)."""
    try:
        import kalshi_netev_calibrate
        return kalshi_netev_calibrate.load_table(NETEV_TABLE_PATH)
    except Exception:
        return {}


# loaded ONCE at import, and ONLY when the flag is on (flag-off does zero file IO -> provable no-op)
NETEV_TABLE = _load_netev_table() if NETEV_GATE else {}
# FUNDING GATE (KALSHI_FUNDING_GATE, default 0 = OFF, provable no-op). When OFF the accumulating
# capital gate is the legacy `committed (= surviving resting notional + held_cost) vs
# MAX_TOTAL_CAPITAL`, byte-for-byte. When ON it STOPS counting already-spent held_cost (that cash
# left `balance` at fill; re-counting it is the treadmill the operator kept escaping by RAISING the
# cap) and instead caps the resting BUY book at min(free_cash, MAX_TOTAL_CAPITAL) — free cash is a
# HARD ceiling that is safe whether the venue's `balance` is GROSS or NET of resting reservations:
# a resting fill can never draw more cash than free cash funds, so no overdraw either way; if
# `balance` turned out NET the worst case is a re-freeze (revert the flag), never a blowup.
# VENUE ASSUMPTION (state it, do not silently rely on it): Kalshi reserves cash at FILL, not at
# placement (GROSS) — measured n~4 place/cancel with balance delta 0 (KALSHI_RUNNING_TAB.md 07-20,
# kalshi_attribution_ledger.py:436). Rootfix design:
# docs/maker_handoffs/KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md.
FUNDING_GATE = _envi("KALSHI_FUNDING_GATE", 0)  # 0 = legacy gross+held gate; 1 = free-cash funding gate
MAX_PRICE_DOLLARS = _envf("KALSHI_MAX_PRICE_DOLLARS", 0.97)  # never OPEN a bid above this
MIN_PRICE_DOLLARS = _envf("KALSHI_MIN_PRICE_DOLLARS", 0.01)  # never OPEN a bid at/below this
# EXIT bounds are the VENUE's, not the strategy's (see _ok_exit_price). A reducing order must
# not be refused for being expensive — that is MAX_UNWIND_LOSS's job — only for being
# unacceptable to Kalshi (valid range 0.01-0.99 inclusive).
EXIT_MAX_PRICE_DOLLARS = _envf("KALSHI_EXIT_MAX_PRICE_DOLLARS", 0.99)
EXIT_MIN_PRICE_DOLLARS = _envf("KALSHI_EXIT_MIN_PRICE_DOLLARS", 0.01)
WIND_DOWN_MIN = _envi("KALSHI_WIND_DOWN_MIN", 45)   # pull quotes N min before end
WRITE_BUDGET_PER_CYCLE = _envi("KALSHI_WRITE_BUDGET", 400)  # order-ops ceiling/cycle
JOIN_ALWAYS = _envb("KALSHI_JOIN_ALWAYS")   # drill switch (default off)
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
THROTTLE_SMART = _envb("KALSHI_THROTTLE_SMART")
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
REDUCE_ONLY_KEEP_BOTH = _envb("KALSHI_REDUCE_ONLY_KEEP_BOTH", True)
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
TAKER_FLATTEN = _envb("KALSHI_TAKER_FLATTEN", True)   # last-resort enabled (set 0 = never)
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
# --- FAR-CLOSE CAP (KALSHI_MAX_DAYS_TO_CLOSE, operator directive 2026-07-25) --------------------
# The late-life gate above refuses markets ending too SOON. This refuses markets ending too FAR
# OUT, and it exists because reward is size x TIME PRESENT and we are barely present in long
# markets. Measured presence by market life (venue order history, unpruned slice, n=20 markets):
#   under 24h  median 16.6%   |   4-14d  median 10.0%   |   14d+ median 0.02% (max 1.15%, n=5)
# A market we are absent from 99.98% of the time cannot pay, but it still ties up capital and
# carries fill risk the whole way. Worse, the presence gate CANNOT catch these on its own without
# a calibration table: with no table the presence factor defaults to 1.0 and a 30-day window looks
# BETTER than a short one, because the estimate multiplies the daily pool by 30 days.
# So this is a hard structural cap, independent of any calibration file being present.
# Deliberately conservative for now — the directive is to gather results and data on short markets
# first and ramp the horizon up later. Held inventory in an excluded market is NOT stranded: it
# falls through to the STRAND UNWIND path, which rests the reducing side at reference so the
# position still flattens passively. 0 disables the cap.
MAX_DAYS_TO_CLOSE = _envf("KALSHI_MAX_DAYS_TO_CLOSE", 3.0)
# --- UNWIND LOSS CAP (2026-07-22 live loss): the maker unwind re-priced at reference every
# cycle CHASES a trending market — buying the reducing side at the top realized the full
# adverse move (~50c/pair on DC vs the doctrine's 1-2c bleed). The reducing quote never rests
# at a price that would lock in more than MAX_UNWIND_LOSS per pair vs our cost basis; past
# that it rests AT the cap (deeper in the book) and the residual waits for the backstop.
# Bounded realized loss per pair, accepted trade: delta may ride longer.
MAX_UNWIND_LOSS = _envf("KALSHI_MAX_UNWIND_LOSS", 0.10)
# HOLD BOTH SIDES (2026-07-26). The block's own design comment is "shrink the accumulating
# side, grow the reducing side, both stay live" — but the reducing side was sized min(|inv|,
# room) off the NOMINAL join, so at inventory below INV_SOFT_CT (where the throttle never
# fires) it rested ADD=100 vs RED=8 and a double fill left us +100. Measured across regimes.
# 0 restores that legacy sizing exactly.
PAIR_BOTH_SIDES = _envb("KALSHI_PAIR_BOTH_SIDES", True)
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
# both triggers with 'cycle ok'. Equity (balance + held cost) is metered against TWO uninflatable
# quotas — drawdown from the intraday high-water mark, and the cumulative sum of per-cycle equity
# DECREASES — and the worse of them breaching DAILY_LOSS_HALT_USD writes the STOP sentinel
# (maker-first flatten + halt until the operator removes it). Completes the daily arm of the
# never-lose-more-than-the-rewards invariant.
# WAS a drop from a FROZEN day-start, which INCOME inflated: reward credits raised equity while
# the baseline stood still, so the room to lose grew all day (measured 07-23: $76.42 of effective
# room against a $40 nominal quota, on an $85 account). See the meter in run_once.
DAILY_LOSS_HALT_USD = _envf("KALSHI_DAILY_LOSS_HALT_USD", 20.0)
# Separate limit for the RATCHETING cumulative-down measure. Defaults to DAILY_LOSS_HALT_USD so
# behaviour is unchanged when unset; set it higher to let true-drawdown be the tight limit without
# ordinary mark noise tripping the halt. See the halt block for why these are not the same number.
DAILY_DOWN_HALT_USD = _envf("KALSHI_DAILY_DOWN_HALT_USD", 0.0) or DAILY_LOSS_HALT_USD
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
# --- PRE-CLOSE SETTLEMENT FLATTEN (2026-07-24 measured loss): a market CLOSES (trading ends) at
# its close_time but SETTLES hours later; after close we CANNOT trade, so whatever NAKED (unpaired,
# net-directional) ladder inventory we hold AT CLOSE rides to settlement and resolves against us
# (gas-daily 26JUL24: -$34.98 across 7 strikes — a directional band-bet at the ATM). A properly
# PAIRED ladder (yes-low / no-high) self-hedges to ~$1/pair and is SAFE to carry; only the NAKED
# residual is the settlement gamble. This mechanism, within PRECLOSE_FLATTEN_MIN of MARKET CLOSE
# (trading end, NOT the reward-period end), MAKER-FIRST rests the reducing quote (existing unwind
# path) and, if the naked residual still exceeds STOP_TAKER_MIN_CT after a STOP_ESCALATE_S grace,
# TAKER-crosses AT MOST |naked| contracts — NEVER a paired leg, NEVER cancelling the resting exit
# (the taker is additive; a failed taker leaves the maker exit resting). SEPARATE from the general
# TAKER_FLATTEN backstop (that stays as-is). DEFAULT 0 = OFF = byte-for-byte today's behavior.
PRECLOSE_FLATTEN = _envi("KALSHI_PRECLOSE_FLATTEN", 0)          # 0 = OFF, provable no-op until flipped
PRECLOSE_FLATTEN_MIN = _envf("KALSHI_PRECLOSE_FLATTEN_MIN", 15.0)  # act within N min of MARKET CLOSE
# --- selection: prefer BALANCED books (maker-unwind fills) over one-sided drift traps ---
MAX_SPREAD_TICKS = _envi("KALSHI_MAX_SPREAD_TICKS", 8)      # skip wide/illiquid books
MIN_DEPTH_SYM = _envf("KALSHI_MIN_DEPTH_SYM", 0.25)         # min(depth)/max(depth) both sides
# READ spacing. Env-overridable (default UNCHANGED at 0.55 => provable no-op).
# The knob exists because 0.55s is the single largest contributor to cycle time:
# a 40-market cycle spends ~22s sleeping here vs ~5-10s on actual network, i.e.
# our own throttle is ~2x the entire round trip and is applied 40-200x/cycle.
# Token math (Basic tier, ~100 tok/s): reads bill far less than the create=10
# tok writes, and 0.55s is only ~1.8 reads/s, so there is large headroom — but
# the exact read token cost is NOT documented anywhere we have verified, so
# LOWERING THIS IS AN OPERATOR DECISION, not a default. Measure 429s if changed.
REQ_SPACING_S = _envf("KALSHI_REQ_SPACING_S", 0.55)
READ_BUDGET_PER_CYCLE = _envi("KALSHI_READ_BUDGET", 200)

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


# ---------------- OPTION B: pluggable book source ------------------------------------------------
# The cold cycle's book reads dominate its wall clock: ~40 serialized REST round trips, measured
# 2026-07-27 from the VPS at 320ms p50 / 327ms p90 (n=78) = ~12.5s of pure network. The WS daemon
# already holds a live mirror of exactly those books, so it can answer the same question from
# memory (feed latency 47ms p50, n=342, same measurement).
#
# BOOK_SOURCE is a callable(ticker) -> orderbook_fp-shaped dict, or None.
#   None (the DEFAULT)  -> every book read is a REST fetch, byte-identical to the legacy path.
#                          A timer run or a manual quoter run installs nothing and is unchanged.
#   installed           -> the provider answers, and returning None means "I cannot vouch for this
#                          ticker — fetch it over REST". The provider owns the whole staleness
#                          predicate; see Daemon.mirror_book in maker_kalshi_ws_daemon.py.
#
# The contract is deliberately one-way: a provider can only ever DECLINE. It can never make the
# cycle skip a book, and it can never suppress a REST fetch it did not answer.
BOOK_SOURCE = None
_book_src = {"mirror": 0, "rest": 0, "src_err": 0}      # per-cycle attribution (reset in run_once)


def _get_book(ticker):
    """orderbook_fp dict for `ticker` — from BOOK_SOURCE when it vouches, REST otherwise.

    Raises exactly what public_get raises (RuntimeError on read-budget exhaustion, urllib errors
    on transport), so every caller's existing except-branch keeps its meaning unchanged.

    A provider that RAISES is treated as a provider that declined: a bug in the mirror path must
    degrade this cycle to REST, never break a live trading cycle. It is counted, not swallowed."""
    src = BOOK_SOURCE
    if src is not None:
        try:
            ob = src(ticker)
        except Exception:
            ob = None
            _book_src["src_err"] += 1
        # `is not None`, NOT truthiness: an empty book ({} / empty level lists) is a legitimate
        # answer the caller already handles (the empty_books counter). Treating it as "declined"
        # would issue a REST read to re-learn the same emptiness on every quiet market.
        if ob is not None:
            _book_src["mirror"] += 1
            return ob
    _book_src["rest"] += 1
    return public_get(f"/trade-api/v2/markets/{ticker}/orderbook").get("orderbook_fp") or {}


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
        # FAR-CLOSE CAP — refuse markets resolving beyond the horizon we can actually stay present
        # in. Measured presence in 14d+ markets was a 0.02% median (n=5). Structural, so it holds
        # even with no presence calibration loaded.
        if MAX_DAYS_TO_CLOSE > 0 and end > now + timedelta(days=MAX_DAYS_TO_CLOSE):
            drops["drop_far_close"] = drops.get("drop_far_close", 0) + 1
            continue
        # window length in days — still needed for the per-market ramp below (NOT for usd_day).
        days = max((end - start).total_seconds() / 86400, 1 / 24)
        # R1 POOL — DO NOT DIVIDE BY WINDOW LENGTH. `period_reward / 10000` IS ALREADY the DAILY,
        # PER-MARKET pool. The old `/ days` form was refuted by a blind audit: across the active set
        # the raw value lands 147/147 series inside Kalshi's documented "$10-$1,000 per day per
        # market" band, while the divided form put 56% BELOW the $10/day floor and never reached the
        # ceiling. The bias is SYSTEMATIC and it is a SELECTION bias, not just a display one: usd_day
        # orders the footprint (:506), the series rotation (:517, :556, :562), cap_desired (:1129)
        # and bound_creates (:1154), so dividing by window length flattered short-window (hourly)
        # programs ~24x and buried long-window ones by their length in days.
        # period_reward may be present-but-null (pending programs) -> `or 0`, not .get default
        rows.append({"ticker": t, "usd_day": (p.get("period_reward") or 0) / 10000,
                     "target": float(p["target_size_fp"]), "end": end.isoformat(),
                     # discount factor for the CAPTURE GATE's R4 walk; discount_factor_bps is
                     # guaranteed non-null by the guard above. Additive key read by nothing except
                     # the capture gate (off by default) -> inert; sort keys/consumers all named.
                     "df": (p["discount_factor_bps"] / 10000) or 0.5,
                     # per-market ramp window = min(global RAMP_MIN, a fraction of THIS market's own
                     # program lifetime) so short markets only ramp in their final stretch (C13).
                     "ramp_min": min(RAMP_MIN, RAMP_LIFE_FRAC * days * 1440.0),
                     # THIS program's own window length. Additive key, read only by the presence
                     # gate (off by default) -> inert. Needed because reward is an INTEGRAL over the
                     # window: entering 80% through caps the score at ~20% however well we execute.
                     "life_min": life_min})
    rows.sort(key=lambda r: (-r["usd_day"], r["ticker"]))
    # SCORE-BASED RANKING: replace the pool ordering with measured capture carried across cycles.
    # Falls back to exactly the pool order above for any market with no score yet, so a cold cache
    # (or a flag-off run) is byte-for-byte legacy. Wrapped — a ranking fault must never stop a cycle.
    if SCORE_RANK and rows:
        try:
            import kalshi_market_scores
            rows = kalshi_market_scores.rank(
                SCORES, rows, now=now.timestamp(),
                swing_penalty=SCORE_SWING_PENALTY, unknown_bonus=SCORE_UNKNOWN_BONUS,
                explore=SCORE_EXPLORE)
        except Exception:
            _SILENT["rank_fail"] += 1        # silently fell back to POOL order
    # ROUND-ROBIN across series (review C18): a single high-pot series (50 concurrent hourly temp
    # strikes ~ $1,920/day each) would otherwise fill the whole FOOTPRINT_TOP by usd_day and starve
    # every other allowlisted series — the fee-free gas lane got ZERO slots. Take one market per
    # series per round, in series-best-usd_day order, until the footprint is full; PER_SERIES_CAP
    # still binds. (A single-series universe collapses to the old highest-usd_day-first order.)
    by_series = defaultdict(list)
    for r in rows:
        by_series[r["ticker"].split("-")[0]].append(r)
    if not PIVOT_SELECT:
        # ---- LEGACY egalitarian round-robin (bytes unchanged; provable flag-off no-op) ----
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
    # ---- PIVOT: density-weighted, over-selected, near-money-ordered candidate pool ----
    # The pool is deliberately LARGER than FOOTPRINT_TOP so the quote loop can read PAST markets
    # that gate out and still fill FOOTPRINT_TOP earners. Two ordering rules replace the
    # egalitarian round-robin's failure modes:
    #   (1) DENSITY: the remainder (after a small per-series coverage floor) is filled purely by
    #       usd_day desc — a 150-usd/day gas strike outranks every 2.6-usd/day H100 strike, so the
    #       best series takes the bulk of the pool (no egalitarian 5-cap). PER_SERIES_CAP still binds.
    #   (2) NEAR-MONEY within a series: a price-free proxy (distance from the series' MEDIAN numeric
    #       strike via the existing _strike_of) sorts deep-ITM/OTM extreme strikes LAST, so the
    #       loop reaches the balanced near-money strikes that actually qualify before it runs dry.
    # READ COST is bounded: pool_cap caps candidates and the loop stops at FOOTPRINT_TOP quoted;
    # the pre-existing READ_BUDGET_PER_CYCLE RuntimeError is the hard ceiling regardless.
    pool_cap = min(PIVOT_POOL_MULT * FOOTPRINT_TOP, len(rows),
                   READ_BUDGET_PER_CYCLE - PIVOT_READ_RESERVE)   # bounded read cost
    _med = {}                           # per-series median numeric strike (price-free proxy)
    for s, rs in by_series.items():
        ks = sorted(k for k in (_strike_of(r["ticker"]) for r in rs) if k is not None)
        _med[s] = ks[len(ks) // 2] if ks else 0.0

    def _prox(r):                       # near-money proxy: |strike - series median|
        s = _strike_of(r["ticker"])
        return abs(s - _med[r["ticker"].split("-")[0]]) if s is not None else 1e9
    for s, rs in by_series.items():
        rs.sort(key=lambda r: (_prox(r), r["ticker"]))          # near-money first, then ticker
    series_order = sorted(by_series, key=lambda s: (-by_series[s][0]["usd_day"], s))
    picked, per_series = [], defaultdict(int)
    for s in series_order:              # 1) COVERAGE floor: >=PIVOT_COVERAGE per active series
        for r in by_series[s][:PIVOT_COVERAGE]:
            picked.append(r)
            per_series[s] += 1
    dens = sorted(rows, key=lambda r: (-r["usd_day"], _prox(r), r["ticker"]))  # 2) REMAINDER by density
    seen = {id(r) for r in picked}
    for r in dens:
        if id(r) in seen:
            continue
        s = r["ticker"].split("-")[0]
        if per_series[s] >= PER_SERIES_CAP:
            continue
        picked.append(r)
        per_series[s] += 1
        if len(picked) >= pool_cap:
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


def _ok_entry_price(p):
    """Strategy band — governs OPENING risk. Unchanged semantics (exclusive lower bound)."""
    return p is not None and MIN_PRICE_DOLLARS < p <= MAX_PRICE_DOLLARS


def _ok_exit_price(p):
    """VENUE bounds — governs REDUCING risk.

    The strategy band (MIN/MAX_PRICE_DOLLARS, live 0.04/0.96) exists so we never OPEN at
    extreme prices. Applying it to an EXIT refuses to close a position precisely when it has
    moved deep against us, because its exit price is then near 1.00 — the reducing side of a
    position whose book sits at 0.99 is a 0.99 bid, and 0.99 > 0.96 was rejected as
    "unpriceable" (live KXAAAGASW-26JUL27-4.080, 2026-07-26T15:31:46Z). That is an entry
    guard applied to leaving, the same family as the standing rule that the YES/NO mandate
    governs ENTRIES only.

    MAX_UNWIND_LOSS remains the SOLE economic governor of whether an exit is worth taking;
    this bound is only "can the venue accept the order at all"."""
    return p is not None and EXIT_MIN_PRICE_DOLLARS <= p <= EXIT_MAX_PRICE_DOLLARS


def _reducing_quotes(best_y, best_n, inv, cost):
    """THE reducing-side quote builder — long yes -> a NO bid, long no -> a YES bid,
    loss-capped by _unwind_price and never larger than |inv| (_unwind_size).

    Extracted 2026-07-26. This block existed in FIVE byte-identical copies inside
    desired_quotes (wind-down, presence gate, net-EV gate, capture gate, void/activate), and
    every copy repeated the same entry-band bug. Five copies meant five places for the defect
    to live and five places a future fix could miss; the root fix is that there is now one.

    Returns [] when the exit is unpriceable at VENUE bounds, or when the book side we would
    have to rest ON is absent — a one-sided book must not raise (the callers previously
    passed None straight into _unwind_price).

    FLAT GUARD: _unwind_size floors at 1, so calling this with inv==0 would rest a
    1-contract order — OPENING risk from a reducing helper. Every caller guards on
    INV_TOLERANCE today; this makes the helper safe on its own terms so a future caller
    cannot reintroduce it. A reducing quote with nothing to reduce is meaningless."""
    if abs(inv) < INV_TOLERANCE:
        return []
    if inv > 0:
        if best_n is None:
            return []
        up = _unwind_price(best_n, cost)
        if not _ok_exit_price(up):
            return []
        return [{"side": "no", "price_dollars": up,
                 "count": _unwind_size(_capped_join(up, best_y), up, inv), "reason": "unwind"}]
    if best_y is None:
        return []
    up = _unwind_price(best_y, cost)
    if not _ok_exit_price(up):
        return []
    return [{"side": "yes", "price_dollars": up,
             "count": _unwind_size(_capped_join(up, best_n), up, inv), "reason": "unwind"}]


def _offset_size(add_cnt, price, inv):
    """Size the REDUCING HALF OF A TWO-SIDED QUOTE so a double fill lands exactly PAIRED.

    NOT a pure unwind. `_unwind_size` caps at |inv| so a pure exit can never cross flat —
    right for the strand path, the wind-down exit and `_flatten_all`, which have stopped
    quoting and are only leaving. At the two-sided call site it was wrong twice over: it
    capped at |inv| AND measured against the NOMINAL join rather than the post-throttle
    adding size, so the reducing side ignored whatever shaping had already happened.

    `add_cnt` is the accumulating side's count AFTER stand-down, ramp and throttle. Setting
    RED = add_cnt + |inv| makes net delta after a double fill exactly zero in EVERY regime
    (measured: below-SOFT, above-SOFT, stand-down, hard-stop, flat, short). The hard-stop
    case is unchanged, because add_cnt is 0 there and the result is |inv| as before.

    `room` (MAX_MARKET_CAPITAL/price) still bounds it, so the dollar envelope is untouched.
    The caller clamps the ADDING side to RED - |inv| for the case where room binds, which it
    does in production (MAX_MARKET_CAPITAL=15 -> room ~23), otherwise the imbalance could
    still grow. KALSHI_PAIR_BOTH_SIDES=0 restores the legacy min(|inv|, room)."""
    room = int(MAX_MARKET_CAPITAL / price) if price > 0 else int(abs(inv))
    want = (int(add_cnt) + int(abs(inv))) if PAIR_BOTH_SIDES else int(abs(inv))
    return max(1, min(want, room))


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


def _standdown_market(m, void):
    """(standdown_bool, eff_rho) for the STAND-DOWN gate. eff_rho = this market's R1-normalized
    LIP reward density (m['usd_day'] = period_reward/10000, ALREADY daily), R3-discounted by
    STANDDOWN_VOID_MULT on a one-sided/void book (a snapshot that scores at risk counts for less).
    standdown is True when eff_rho is below the fingerprint-calibrated floor STANDDOWN_MIN_USD_DAY:
    the reward is too thin to justify the expected adverse fill loss, so OPEN LESS here.

    PURE + OBSERVABLE + BOUNDED: uses only the reward number already on the footprint row and the
    in-cycle void flag — no extra API read, no state. Callers invoke it ONLY under `if STANDDOWN`,
    so with the flag off it never runs and behavior is byte-for-byte legacy. A missing usd_day
    defaults to 0.0 -> stands down (the conservative direction: open less when reward is unknown)."""
    rho = float(m.get("usd_day", 0.0) or 0.0)
    eff = rho * (STANDDOWN_VOID_MULT if void else 1.0)
    return eff < STANDDOWN_MIN_USD_DAY, eff


def _qualifying_score(bids, our_price, our_size, target, df):
    """R4 walk (a byte-equivalent replica of kalshi_market_scorecard.qualifying_share): reference =
    highest bid (<1.0); walk bids desc accumulating size to Target; score = DF^N*size (N = ticks
    below reference). Returns (our_score/book_total, side_qualifies) — book_total EXCLUDES our
    not-yet-placed order (the reward denominator once we rest is book_total + our_score).

    A LOCAL copy (not an import of the scorecard) keeps the ledger's credential-path/env-resolution
    machinery out of the live quoter's import graph; test_capture_gate pins equivalence to the
    scorecard on shared fixtures."""
    bids = sorted(((p, s) for p, s in bids if s > 0), key=lambda x: -x[0])
    if not bids or bids[0][0] >= 1.0:
        return 0.0, False
    ref = bids[0][0]
    cum = total = 0.0
    lowest_q = ref
    for price, size in bids:
        n = round((ref - price) / TICK)
        total += (df ** n) * size
        cum += size
        lowest_q = price
        if cum >= target:
            break
    if cum < target:
        return 0.0, False                       # book can't reach Target on this side -> $0 for everyone
    our = 0.0
    if our_price is not None and our_price >= lowest_q - 1e-9:  # our order is inside the qualifying set
        our = (df ** round((ref - our_price) / TICK)) * our_size
    return (our / total if total > 0 else 0.0), True


def _prospective_capture(m, yl, nl, best_y, best_n, target):
    """Our PROSPECTIVE R4 capture $/day if we rested our intended JOIN size at reference on both
    sides. Per side raw = our_score/book_total (book excludes our not-yet-placed order); the
    reward denominator once we rest is book_total + our_score, so the prospective per-side share is
    raw/(1+raw). R3 (both sides must qualify) two-sided snapshot = (share_yes + share_no)/2, times
    the R1 pool (m['usd_day']). We join AT reference (N=0, DF^0=1) so our_score = our_size. Intended
    size = _capped_join — the exact size the JOIN branch would rest — so the gate models the order
    it is deciding whether to place. MODEL (M7 over-predicts 2-6x): a RELATIVE signal only."""
    df = m.get("df") or CAPTURE_DF_DEFAULT     # present-but-None / 0 -> default (stress: "no df")
    ry, qy = _qualifying_score(yl, best_y, _capped_join(best_y, best_n), target, df)
    rn, qn = _qualifying_score(nl, best_n, _capped_join(best_n, best_y), target, df)
    if not (qy and qn):
        return 0.0
    snap = (ry / (1.0 + ry) + rn / (1.0 + rn)) / 2.0
    return snap * float(m.get("usd_day", 0.0) or 0.0)


def _qualifying_breakdown(bids, target, df):
    """OBSERVATION-ONLY companion to _qualifying_score: the SAME R4 walk, but it returns the BOOK
    side of it (the reward DENOMINATOR) instead of our share. Returns
    (df_total, cum_ct, ref, lowest_q, qualifies), where df_total = sum(DF^N * size) over the
    qualifying set = the competing score our own order is diluted by.

    Deliberately a SEPARATE function, not a new return value on _qualifying_score: that signature is
    consumed by the capture gate and the net-EV gate and is pinned byte-equivalent to
    kalshi_market_scorecard.qualifying_share, so it is not touched. test_market_telemetry pins this
    walk against _qualifying_score so the two can never drift."""
    bids = sorted(((p, s) for p, s in bids if s > 0), key=lambda x: -x[0])
    if not bids or bids[0][0] >= 1.0:
        return 0.0, 0.0, None, None, False
    ref = bids[0][0]
    cum = total = 0.0
    lowest_q = ref
    for price, size in bids:
        n = round((ref - price) / TICK)
        total += (df ** n) * size
        cum += size
        lowest_q = price
        if cum >= target:
            break
    return total, cum, ref, lowest_q, (cum >= target)


def _market_telemetry_row(cyc, now, m, yl, nl, quotes, own_side, inv, gates):
    """Build ONE per-market-per-cycle telemetry row. Pure function (no I/O) so it is testable."""
    t = m["ticker"]
    target = float(m.get("target") or 0.0)
    df = float(m.get("df") or CAPTURE_DF_DEFAULT)   # see above
    own_side = own_side or {}
    row = {"ts": now.isoformat(), "cyc": cyc, "ticker": t, "series": t.split("-")[0],
           "usd_day": round(float(m.get("usd_day") or 0.0), 4),
           "target": target, "df": df, "inv": round(float(inv), 2)}
    if gates:
        row["gates"] = gates
    shares = []
    for tag, levels, side in (("y", yl, "yes"), ("n", nl, "no")):
        df_total, cum, ref, low_q, qual = _qualifying_breakdown(levels, target, df)
        oq = next((x for x in quotes if x.get("side") == side), None)
        our_px = oq["price_dollars"] if oq else None
        our_ct = float(oq["count"]) if oq else 0.0
        score = 0.0
        if qual and our_px is not None and low_q is not None and our_px >= low_q - 1e-9:
            score = (df ** round((ref - our_px) / TICK)) * our_ct
        share = score / (df_total + score) if (df_total + score) > 0 else 0.0
        row[tag + "_ref"] = ref
        row[tag + "_book_df"] = round(df_total, 2)      # INCLUDES our own resting order (public
        row[tag + "_cum_ct"] = round(cum, 2)            # depth); *_rest_ct below makes the
        row[tag + "_qual"] = bool(qual)                 # rival-only denominator recoverable.
        row[tag + "_lowq"] = low_q
        row[tag + "_px"] = our_px                       # our INTENDED price (None while gated)
        row[tag + "_ct"] = our_ct                       # our INTENDED size (0 while gated/parked)
        row[tag + "_rest_ct"] = float(own_side.get(side, 0.0) or 0.0)   # what is ACTUALLY resting
        row[tag + "_score"] = round(score, 3)
        row[tag + "_share"] = round(share, 6)
        shares.append(share if qual else 0.0)
    # R3: a snapshot pays only if BOTH sides qualify -> otherwise the market pays $0 to everyone.
    two_sided = row["y_qual"] and row["n_qual"]
    row["capture_usd_day"] = (round((sum(shares) / 2.0) * float(m.get("usd_day") or 0.0), 4)
                              if two_sided else 0.0)
    return row


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
    try:
        end = parse_iso(m["end"])
    except Exception:
        return []            # unusable clock -> quote nothing here (stress: "garbage end").
                             # select_footprint already drops unparseable dates, so this is
                             # defence in depth, not the primary guard.
    # (`_priceable` lived here and gated the wind-down EXIT on the ENTRY band. Its only caller
    # now uses _reducing_quotes, which checks the one side it rests on at venue bounds, so the
    # variable is dead and is removed rather than left to be re-used by mistake.)
    if end < now + timedelta(minutes=WIND_DOWN_MIN):
        # wind_down: pull the two-sided quotes. But if we still HOLD inventory here, keep
        # resting the REDUCING side (passive $0 maker unwind) until the settlement taker
        # backstop takes over — never abandon an open position into resolution (fix F).
        # `_priceable` was the ENTRY band (both sides inside 0.04-0.96 and sum < 1.0). Gating
        # the EXIT on it meant a position could not be unwound into wind-down on exactly the
        # books where it most needed to be. _reducing_quotes checks the ONE side it rests on,
        # at venue bounds.
        if abs(inv) >= INV_TOLERANCE:
            return _reducing_quotes(best_y, best_n, inv, cost)
        return []                                   # wind_down (flat -> pull entirely)
    # CROSSED BOOK IS CHECKED FIRST, and refuses BOTH entry and exit. A crossed book
    # (yes_bid + no_bid >= 1.0) is a stale/degenerate quote, not a price — a yes bid @best_y
    # and a no bid @best_n would cross, so it must never be rested even if post_only were
    # silently ignored. It is ordered ahead of the entry gates below because those now fall
    # through to a reducing quote when we hold inventory: leaving it last would have started
    # resting exits onto crossed books, which the strand path explicitly refuses
    # ("crossed/stale — do not chase"). Both paths now agree.
    if best_y is not None and best_n is not None and best_y + best_n >= 1.0:
        return []
    # THE NEXT TWO GATES REJECT AN *ENTRY*. Each used to `return []`, which also discarded
    # the reducing quote built further down — so a held position on a one-sided or extreme
    # book got NO exit order at all, which is precisely the book a losing position ends on.
    # Flat -> still nothing. Holding -> rest the reducing side and stop.
    if best_y is None or best_n is None:            # one-sided: cannot JOIN, can still EXIT
        return _reducing_quotes(best_y, best_n, inv, cost) if abs(inv) >= INV_TOLERANCE else []
    if not (_ok_entry_price(best_y) and _ok_entry_price(best_n)):
        return _reducing_quotes(best_y, best_n, inv, cost) if abs(inv) >= INV_TOLERANCE else []
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
    # PRESENCE / $1-FLOOR GATE (KALSHI_PRESENCE_GATE, default 0 = provable no-op). The most basic
    # economic test there is, and the one nothing else asks: CAN THIS MARKET STILL PAY US AT ALL for
    # the time that is actually left? Reward is an integral over the window, and Kalshi pays $0 below
    # a $1.00 credit — so a market whose remaining-window credit cannot clear a dollar pays exactly
    # nothing while the fill risk is unchanged. Held inventory is NEVER blocked (reduce-only, full
    # size), identical to the capture-gate clone below.
    if PRESENCE_GATE and not void:
        _exp, _ideal, _frac = _expected_credit_usd(m, yl, nl, best_y, best_n, target, now)
        if stats is not None:
            stats["presence_min_credit"] = min(stats.get("presence_min_credit", 1e18), _exp)
        # ENTRY GATE, NOT AN EXIT GATE. The floor asks "could this market earn MIN_CREDIT_USD from
        # scratch?" — the right question when DECIDING TO OPEN, and the wrong one once we are
        # already resting here. Reward accrues continuously over the window, so for a market we are
        # in, the remaining accrual is ADDITIVE on top of what we have already banked: it does not
        # have to clear the floor again, it only has to beat the marginal fill risk. Measured
        # profile of the bug this fixes (pool $100/day, join 20ct, floor $1.20): flat-entry
        # coverage of a 1-DAY market was 51% — the gate pulled our resting quotes at the halfway
        # point and walked away from the afternoon's accrual. Holding INVENTORY already bypassed
        # this (de-risk is never gated); resting ORDERS with no fills did not, and that is the hole.
        # Continuation is still bounded by the ordinary late-life / wind-down gates, so this cannot
        # pin us in a market to settlement.
        _resting_here = (float((own or {}).get("yes") or 0.0)
                         + float((own or {}).get("no") or 0.0)) > 0.0
        if _resting_here and _exp < MIN_CREDIT_USD and stats is not None:
            stats["presence_continued_under_floor"] = \
                stats.get("presence_continued_under_floor", 0) + 1
        if _exp < MIN_CREDIT_USD and not _resting_here:
            if stats is not None:
                stats["presence_skipped"] = stats.get("presence_skipped", 0) + 1
                # THE DEATH-SPIRAL COUNTER: would this market have cleared the floor if we executed
                # perfectly? If yes, the skip is OUR fault, not the market's. Watch this after any
                # uptime fix — it is the part that should shrink.
                if _ideal >= MIN_CREDIT_USD:
                    stats["presence_skipped_execution_only"] = \
                        stats.get("presence_skipped_execution_only", 0) + 1
                if _frac < 0.5:
                    stats["presence_skipped_late_entry"] = \
                        stats.get("presence_skipped_late_entry", 0) + 1
            if abs(inv) < INV_TOLERANCE:
                return []                           # FLAT + cannot clear $1 -> never open
            return _reducing_quotes(best_y, best_n, inv, cost)
    # NET-EV GATE (KALSHI_NETEV_GATE, default 0 = provable no-op) — the RECEIPT-CALIBRATED brain, and
    # the COMPLETE signal that supersedes the reward-only capture gate + pool-only stand-down. Look up
    # this market's FAMILY net-EV (credits − fill P&L − fees, per kalshi_netev_calibrate). A family
    # calibrated NET-NEGATIVE (net% below NETEV_MIN_MARGIN_PCT) is POOR FOR US -> FLAT skip / HOLDING
    # reduce-only (clone of the capture reduce-only block; de-risk never blocked). An UNPROVEN family
    # (no receipt credits) falls back to the conservative R4 model (prospective capture / haircut minus
    # the fill fingerprint) -> open only if positive, else unproven-skip. Void books scoped OUT.
    if NETEV_GATE and not void:
        fam = _netev_family(m["ticker"])
        ent = NETEV_TABLE.get(fam)
        if ent is not None and ent.get("confidence") not in (None, "unproven"):
            net_pct = ent.get("net_pct_notional")           # RECEIPT signal (net % of notional)
            poor = net_pct is not None and net_pct < NETEV_MIN_MARGIN_PCT
            signal = net_pct if net_pct is not None else 0.0
        else:                                               # UNPROVEN: conservative model fallback
            pc = _prospective_capture(m, yl, nl, best_y, best_n, target)
            signal = pc / NETEV_MODEL_HAIRCUT - NETEV_FINGERPRINT_USD_DAY
            poor = signal <= 0.0                            # model-not-receipt: open only if +ve
        if stats is not None:
            stats["netev_min_signal"] = min(stats.get("netev_min_signal", 1e18), signal)
        if poor:
            if stats is not None:
                stats["netev_skipped"] = stats.get("netev_skipped", 0) + 1
                _nf = stats.setdefault("netev_families", {})
                _nf[fam] = _nf.get(fam, 0) + 1
            if abs(inv) < INV_TOLERANCE:
                return []                                   # FLAT + net-negative-for-us -> skip
            return _reducing_quotes(best_y, best_n, inv, cost)
    # CAPTURE GATE (KALSHI_CAPTURE_GATE, default 0 = provable no-op) — the market-quality brain the
    # unqualifiable/selection gates lack: they check the BOOK can pay; this checks WE get paid. On a
    # two-sided JOIN book, compute our PROSPECTIVE R4 capture $/day (our_size scored at ref / (book
    # qualifying score + our_size), R3 two-sided, x R1 pool). Below the floor the market is POOR FOR
    # US:
    #   FLAT     -> skip (never open — a $0-for-us book is pure adverse-fill risk).
    #   HOLDING  -> REDUCE-ONLY: rest ONLY the reducing side at full |inv| (a line-for-line clone of
    #              the wind_down reduce-only block) so de-risk is NEVER blocked or down-sized.
    # Void/activate books are scoped OUT (we supply Target depth there -> high share; the existing
    # activate economics govern). Uses only the in-cycle book + intended size — no extra API read.
    if CAPTURE_GATE and not void:
        pc = _prospective_capture(m, yl, nl, best_y, best_n, target)
        if stats is not None:
            stats["capture_min_pc"] = min(stats.get("capture_min_pc", 1e18), pc)
        if pc < CAPTURE_MIN_USD_DAY:
            if stats is not None:
                stats["capture_skipped"] = stats.get("capture_skipped", 0) + 1
            if abs(inv) < INV_TOLERANCE:
                return []                           # FLAT + poor-for-us -> skip
            return _reducing_quotes(best_y, best_n, inv, cost)
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
            return _reducing_quotes(best_y, best_n, inv, cost)
        if abs(ev) > INV_SOFT_CT:
            return []                               # event already directional -> don't ADD via activate
        if STANDDOWN:                               # STAND-DOWN: don't commit activate depth into a
            _sd, _eff = _standdown_market(m, True)  # thin-reward void book (flat here -> strands
            if _sd:                                 # nothing). Held inventory unwinds above, untouched.
                if stats is not None:
                    stats["standdown"] = stats.get("standdown", 0) + 1
                    stats["standdown_min_rho"] = min(stats.get("standdown_min_rho", 1e18), _eff)
                return []                           # reward too thin to justify Target-size activate
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
        # STAND-DOWN: on a thin-reward regime, size BOTH accumulating sides down to MIN_QUOTE_CT
        # (price left AT reference so the snapshot still qualifies + earns the thin reward, only the
        # SIZE shrinks -> each fill's adverse loss shrinks in proportion). Applied to the base join
        # size BEFORE ramp/throttle/unwind: ramp/throttle only shrink further (fine), and the unwind
        # block below RE-sizes the reducing side from |inv| (so de-risk is never capped by this).
        # min() never INCREASES a side; when flat both sides rest at the floor -> a dead day ~$0.
        if STANDDOWN:
            _sd, _eff = _standdown_market(m, False)
            if _sd:
                y_cnt = min(y_cnt, MIN_QUOTE_CT)
                n_cnt = min(n_cnt, MIN_QUOTE_CT)
                if stats is not None:
                    stats["standdown"] = stats.get("standdown", 0) + 1
                    stats["standdown_min_rho"] = min(stats.get("standdown_min_rho", 1e18), _eff)
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
            # RED = ADD + |inv| (ADD = the count AFTER stand-down/ramp/throttle), then clamp
            # ADD to RED - |inv| for when `room` caps RED. Together these hold the invariant
            # "a double fill lands paired" in every regime, instead of only at hard-stop.
            _iv = int(abs(inv))
            if inv > 0:                             # long yes -> grow NO (reduces), at ref BUT
                n_price, n_reason = _unwind_price(best_n, cost), "unwind"   # loss-capped
                n_cnt = _offset_size(y_cnt, n_price, inv)
                if PAIR_BOTH_SIDES:                 # clamp is part of the fix, not of legacy
                    y_cnt = min(y_cnt, max(0, n_cnt - _iv))
            else:                                   # long no -> grow YES (reduces)
                y_price, y_reason = _unwind_price(best_y, cost), "unwind"
                y_cnt = _offset_size(n_cnt, y_price, inv)
                if PAIR_BOTH_SIDES:
                    n_cnt = min(n_cnt, max(0, y_cnt - _iv))
        # FINAL EMIT — the band depends on WHAT the quote is, not merely on its price. A leg
        # re-priced to "unwind" above is REDUCING risk and takes venue bounds; anything else is
        # OPENING risk and takes the strategy band. Applying the entry band here undid the
        # reducing-side fixes further up, because this is the last gate every quote passes.
        _ok_y = _ok_exit_price(y_price) if y_reason == "unwind" else _ok_entry_price(y_price)
        _ok_n = _ok_exit_price(n_price) if n_reason == "unwind" else _ok_entry_price(n_price)
        # THE OFFSET ASSUMED ITS PARTNER WOULD REST. RED was sized ADD+|inv| so that a DOUBLE
        # fill lands paired — but the ADDING side can still be dropped right here (price outside
        # its band, or shaped to 0). If that happens, RED stands alone and a full fill carries us
        # THROUGH flat by the whole ADD component. Caught by stress_inventory.py on a real book:
        # inv=-16 with the adding side dropped left RED=113 -> net +97, growing the imbalance 6x.
        # When the partner will not rest, fall back to a pure unwind capped at |inv|.
        if abs(inv) >= INV_TOLERANCE and PAIR_BOTH_SIDES:
            _iv2 = int(abs(inv))
            if inv > 0 and not (y_cnt > 0 and _ok_y):
                n_cnt = min(n_cnt, _iv2)
            elif inv < 0 and not (n_cnt > 0 and _ok_n):
                y_cnt = min(y_cnt, _iv2)
        if y_cnt > 0 and _ok_y:
            quotes.append({"side": "yes", "price_dollars": y_price, "count": y_cnt, "reason": y_reason})
        if n_cnt > 0 and _ok_n:
            quotes.append({"side": "no", "price_dollars": n_price, "count": n_cnt, "reason": n_reason})
    return quotes


def apply_drop_grace(standing, desired, footprint_tickers, prev_grace, grace_cycles):
    """Retain a ticker's existing book for a few cycles when it merely ROTATED OUT of the footprint.

    Returns (desired, new_grace) where new_grace maps ticker -> cycles used so far.

    A ticker qualifies ONLY if all of:
      - it has standing orders (something to protect),
      - it is absent from `desired` (the diff would otherwise cancel it), AND
      - it is absent from THIS CYCLE'S FOOTPRINT — i.e. we never looked at it.
    A ticker that WAS looked at and rejected (gate, capital cap, breaker, wind-down) is in the
    footprint but not in desired, so it is NOT granted grace. That distinction is the whole safety
    argument: grace covers "we didn't check", never "we said no". Retaining through a decision would
    defeat the cap or gate that made it.

    Retained orders are copied VERBATIM from standing, so the diff sees an exact match and emits
    neither a cancel nor a create — the book simply stays, keeping its queue position and its
    time-on-book. Pure function, no I/O."""
    new_grace = {}
    if grace_cycles <= 0:
        return desired, new_grace
    out = dict(desired)
    for t, orders in standing.items():
        if not orders or t in out or t in footprint_tickers:
            continue                       # nothing to keep / still wanted / actively rejected
        used = int(prev_grace.get(t, 0))
        if used >= grace_cycles:
            continue                       # grace exhausted -> let the diff cancel it
        new_grace[t] = used + 1
        out[t] = [{"side": o["side"], "price_dollars": o["price_dollars"], "count": o["count"]}
                  for o in orders]
    return out, new_grace


def split_amends(standing, desired):
    """Pull out the (ticker, side, price) pairs where the ONLY change is a SMALLER count, and
    return (amends, standing_left, desired_left) with those pairs removed from both sides.

    WHY: diff_orders survives an order only on an exact (side, price, count) match, so changing a
    resting order's size by a single contract cancels it and rebuilds it at the BACK of the queue.
    Measured over the clean slice of our own order history (478 zero-fill cancels, 2026-07-23T20:05Z
    onward): 100 were same-price-different-size, of which 44 were DECREASES — queue position thrown
    away for nothing. Kalshi's amend preserves queue position for exactly that case and no other:
      "Amending a resting order preserves queue position only when the amendment decreases size.
       All other amendments — like increasing size or changing price forfeit queue position and
       place the order at the back of the queue."
    So increases and reprices are deliberately NOT routed here — amend would buy nothing and the
    existing cancel+create path is already correct for them.

    Pure function, no I/O. Caller runs diff_orders on what is left, so with the flag off the
    behaviour is byte-for-byte unchanged."""
    amends = []
    s_left = {t: list(v) for t, v in standing.items()}
    d_left = {t: list(v) for t, v in desired.items()}
    for t in set(standing) & set(desired):
        # index the desired book by (side, price); a duplicate side+price is ambiguous -> skip the
        # whole ticker rather than guess which resting order a given target refers to.
        want = {}
        ambiguous = set()
        for w in desired.get(t, []):
            k = (w["side"], round(w["price_dollars"], 4))
            if k in want:
                ambiguous.add(k)
            want[k] = w
        for o in list(s_left.get(t, [])):
            k = (o["side"], round(o["price_dollars"], 4))
            w = want.get(k)
            if w is None or k in ambiguous:
                continue
            if not (0 < w["count"] < o["count"]):
                continue                      # only a strict DECREASE preserves queue position
            amends.append({"order_id": o["order_id"], "ticker": t, "side": o["side"],
                           "price_dollars": o["price_dollars"], "count": w["count"],
                           "from_count": o["count"], "reason": w.get("reason")})
            s_left[t] = [x for x in s_left[t] if x is not o]
            d_left[t] = [x for x in d_left[t] if x is not w]
    s_left = {t: v for t, v in s_left.items() if v}
    d_left = {t: v for t, v in d_left.items() if v}
    return amends, s_left, d_left


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
    _book_src.update(mirror=0, rest=0, src_err=0)   # per-cycle book-source attribution
    cyc = int(now.timestamp())            # per-cycle nonce for unique order ids
    st = load_state()
    plan = {"ts": now.isoformat(), "mode": client.mode}
    # CONFIG VISIBILITY (2026-07-26): a knob absent from live.env takes its code default
    # silently. That class of defect hid KALSHI_THROTTLE_SMART (OFF in production) and
    # KALSHI_PRECLOSE_FLATTEN (built + tested, never switched on) while every cycle
    # printed "cycle ok". Full list into the plan row; the protection-bearing ones are
    # NAMED in the log, because a bare count is exactly as ignorable as silence was.
    _absent = env_absent()
    plan["env_absent_n"] = len(_absent)
    plan["env_absent"] = _absent
    _unset_safety = [k for k in _SAFETY_KNOBS if k in _absent]
    if _unset_safety:
        plan["env_absent_safety"] = _unset_safety
        print(f"WARNING {len(_unset_safety)} PROTECTION knob(s) unset in live.env -> code "
              f"default: {', '.join(_unset_safety)} "
              f"(set each explicitly — even to its default — to confirm it is a CHOICE)")
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
        free_cash = None                      # real free cash (balance_dollars); None = unread this
                                              # cycle -> funding gate FAILS CLOSED to the legacy gate
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
            # DAILY LOSS KILL (treadmill guard): equity = cash + held COST BASIS, so SETTLED
            # losses show up here even though held-$ returns to zero. Drop beyond the daily
            # budget -> write STOP (operator must clear it) + maker-first flatten NOW. Balance
            # read failure only skips this check (primary reads above remain fail-closed).
            # COST BASIS, NOT MARK, DELIBERATELY (re-affirmed 2026-07-23): /portfolio/balance
            # exposes portfolio_value (integer cents) as a venue mark, but whether it INCLUDES
            # cash is unverified — read wrong it double-counts the balance and halves or doubles
            # the meter, and the state freeze forbids probing the endpoint. Cost basis also keeps
            # the meter noise-free: an entry moves cash down and cost up by the same amount, so
            # only realized/settled moves tick it. KNOWN GAP: open (unrealized) losses stay
            # invisible until settlement. That is a separate change needing a live probe of
            # portfolio_value's semantics first — NOT fixed here.
            _equity = None
            try:
                # ONE get_balance() call; free_cash reused by the funding gate below (no second
                # fetch). A raise leaves free_cash None (init above) -> funding gate fails closed.
                free_cash = float(client.get_balance().get("balance_dollars") or 0)
                _equity = free_cash + held_cost
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
                # DRAWDOWN, NOT DROP-FROM-DAY-START (defect fixed 2026-07-23). Measuring against
                # a FROZEN day-start let INCOME inflate the quota: every reward credit raised the
                # numerator while the baseline stood still, so the room to lose grew monotonically
                # all day. Measured live 07-23: equity $99.76 vs day_start $63.34 => the halt only
                # tripped at $23.34 — $76.42 of effective room against a nominal $40 quota (1.91x),
                # i.e. 76% of an $85 account could evaporate first. Two meters now, both immune to
                # income and to deposits, and the halt fires on the WORSE of them:
                #   dd   = drawdown from the intraday HIGH-WATER MARK. A credit/deposit lifts the
                #          peak by the same amount it lifts equity, so it buys ZERO extra room.
                #   down = CUMULATIVE sum of per-cycle equity DECREASES. Up-moves are ignored
                #          entirely, so no inflow can pay back a loss already taken (a deposit
                #          resets 'dd' but can never reset 'down'). This is the treadmill arm.
                # Peak seeds from equity_day_start so a PRE-fix state file migrates with the old
                # drop-from-day-start behaviour intact as a FLOOR: the meter is >= the old one on
                # every input, never weaker.
                _day = now.strftime("%Y%m%d")
                if st.get("equity_day") != _day:
                    st["equity_day"] = _day
                    st["equity_day_start"] = _equity
                    st["equity_day_peak"] = _equity
                    st["equity_day_down"] = 0.0
                    st["equity_prev"] = _equity
                else:
                    _start = float(st.get("equity_day_start", _equity))
                    _peak = max(float(st.get("equity_day_peak", _start)), _equity)
                    _prev = float(st.get("equity_prev", _equity))
                    _down = float(st.get("equity_day_down", 0.0)) + max(0.0, _prev - _equity)
                    st["equity_day_peak"] = _peak
                    st["equity_day_down"] = _down
                    st["equity_prev"] = _equity
                    _dd = _peak - _equity
                    plan["daily_dd"] = round(_dd, 2)
                    plan["daily_down"] = round(_down, 2)
                    # TWO DIFFERENT MEASURES, TWO DIFFERENT LIMITS (2026-07-26). These were both
                    # compared against DAILY_LOSS_HALT_USD via max(), which makes the tighter of
                    # the two the effective limit for BOTH — and they are not the same quantity:
                    #   _dd    TRUE DRAWDOWN from the day's peak. Falls back when equity recovers.
                    #          This is what an operator means by "stop if I'm down $X".
                    #   _down  CUMULATIVE sum of every per-cycle DECREASE. It RATCHETS and never
                    #          resets on recovery, so ordinary mark noise walks it upward all day
                    #          regardless of whether we are actually losing.
                    # Setting the halt to $10 tripped INSTANTLY on a _down of $21.72 that had been
                    # accumulating for hours while true drawdown was $4.76 — a mechanically correct
                    # halt that meant nothing economically.
                    drop = max(_dd, _down)
                    # NAME THE LIMB THAT ACTUALLY BREACHED (2026-07-26). The message used to
                    # render `cumulative-down $X > $DAILY_LOSS_HALT_USD` unconditionally — the
                    # wrong quantity against the wrong limit. Live at 14:54:08Z it printed
                    # "cumulative-down $8.98 > $10", which is not even true; the real trigger was
                    # drawdown $13.74 > $10. Misreading WHICH measure halted the bot is the single
                    # most expensive diagnostic error this lane has made, so the halt must say so
                    # itself rather than leave it to be re-derived from two similar numbers.
                    _breaches = []
                    if _dd > DAILY_LOSS_HALT_USD:
                        _breaches.append(f"DRAWDOWN ${_dd:.2f} > ${DAILY_LOSS_HALT_USD:.2f} "
                                         f"(from day-peak ${_peak:.2f})")
                    if _down > DAILY_DOWN_HALT_USD:
                        _breaches.append(f"CUMULATIVE-DOWN ${_down:.2f} > ${DAILY_DOWN_HALT_USD:.2f} "
                                         f"(ratcheting sum, never resets)")
                    if _breaches:
                        _why = " AND ".join(_breaches)
                        plan["daily_loss_halt"] = round(drop, 2)
                        plan["daily_halt_reason"] = _why
                        with open(STOP_FILE, "w") as fh:
                            fh.write(f"auto daily-loss halt {now.isoformat()} drop=${drop:.2f} "
                                     f"TRIGGER: {_why} "
                                     f"(equity ${_equity:.2f} vs day-peak ${_peak:.2f}; "
                                     f"dd ${_dd:.2f} / cumulative-down ${_down:.2f}; "
                                     f"day-start ${_start:.2f})\n")
                        print(f"WARNING DAILY LOSS HALT: {_why} — STOP written, "
                              f"maker-flattening")
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
        naked_by = ladder_pairing(held_by, plan)     # plan collects 'strike_parse_failed'
        if plan.get("strike_parse_failed"):
            # LOUD: held inventory whose strike would not parse can never be paired, so 100% of
            # it stays naked with no error. Silent darkness here is indistinguishable from
            # 'nothing to pair' — say it out loud, every cycle it is true.
            print(f"WARNING strike parse FAILED on {plan['strike_parse_failed']} held ticker(s) — "
                  f"that inventory cannot be ladder-paired (unpairable, counted naked)")
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
        # ...but ONLY across events proved to be additive threshold ladders. A categorical event
        # is reported per-ticker instead (see event_deltas); count them so an un-nettable event
        # in the book is visible rather than inferred.
        _ev_groups = defaultdict(list)
        for _t in held_by:
            _ev_groups[_event_key(_t)].append(_t)
        _nonladder = [k for k, ts in _ev_groups.items() if not _is_ladder_event(ts)]
        if _nonladder:
            plan["nonladder_events"] = len(_nonladder)

        desired = {}
        book_refs = {}                                  # ticker -> (best_yes, best_no) this cycle
        consumed = []                                   # PIVOT: markets actually TRIED this cycle
        for m in footprint:
            # PIVOT/BACKFILL: stop once FOOTPRINT_TOP markets are actually QUOTED (earning). The
            # pool is over-selected, so a gated market (desired_quotes -> []) simply falls through
            # to the next candidate — the GATES are unchanged, we just keep reading down the pool
            # until we have FOOTPRINT_TOP earners or the pool is exhausted. Flag-off: len(desired)
            # can never reach FOOTPRINT_TOP faster than footprint ends, and the guard is skipped
            # anyway (short-circuits False), so behavior is byte-identical.
            if PIVOT_SELECT and len(desired) >= FOOTPRINT_TOP:
                break                                   # enough EARNERS quoted -> stop reading
            if PIVOT_SELECT:
                consumed.append(m)                      # count every market we TRY (gated or not)
            t = m["ticker"]
            if t in flattened:
                continue                                # just de-risked; leave it alone this cycle
            try:
                ob = _get_book(t)              # WS mirror when it vouches, else REST (identical)
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
            # Snapshot the gate counters so THIS market's skip reason is attributable by diffing
            # them after the call — desired_quotes returns a bare [] and its signature is a contract
            # (Rule 2), so the reason is recovered without touching it.
            _pre_stats = ({k: v for k, v in qstats.items() if type(v) is int}
                          if MKT_TELEMETRY else {})
            try:
                q = desired_quotes(m, ob.get("yes_dollars") or [], ob.get("no_dollars") or [],
                                   now, own=own.get(t), inv=naked_by.get(t, 0.0),
                                   event_delta=event_delta_for(ev_delta, t), stats=qstats,
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
            # PER-MARKET REWARD TELEMETRY — observation only, and deliberately the LAST thing in the
            # loop body: it reads state, writes one line, and can never alter `desired`. Wrapped so a
            # telemetry fault (bad row, full disk) can never break a live trading cycle.
            if MKT_TELEMETRY:
                try:
                    _gates = {k: v - _pre_stats.get(k, 0) for k, v in qstats.items()
                              if type(v) is int and v - _pre_stats.get(k, 0) > 0}
                    _row = _market_telemetry_row(cyc, now, m, _byl, _bnl, q,
                                                 own.get(t), naked_by.get(t, 0.0), _gates)
                    with open(os.path.join(DATA_DIR,
                                           f"quotes-{now.strftime('%Y%m%d')}.jsonl"), "a") as _fh:
                        _fh.write(json.dumps(_row, separators=(",", ":")) + "\n")
                    # SCORE CACHE: fold this book into the rolling rank. Free — capture_usd_day and
                    # the reference price are already computed above. This is what lets the NEXT
                    # cycle rank on measured capture instead of pool size.
                    if SCORE_RANK:
                        import kalshi_market_scores as _kms
                        _kms.update(SCORES, t, _row.get("capture_usd_day"),
                                    _row.get("y_ref"), now=now.timestamp())
                except Exception:
                    pass

        # PIVOT: collapse `footprint` to what we ACTUALLY tried (the over-selected pool tail we
        # never reached is NOT part of this cycle's footprint). Every downstream consumer
        # (fp_tickers below, gated_out at the plan block, the print line) then keeps its exact
        # legacy meaning — footprint = markets tried, gated_out = tried-but-skipped = the pivots.
        # A held ticker still sitting in the UN-consumed pool tail is picked up by strand-unwind
        # exactly as legacy (it is absent from fp_tickers). Flag-off: consumed is [] and this line
        # is skipped, so `footprint` object identity is unchanged.
        if PIVOT_SELECT:
            footprint = consumed
        # STRAND UNWIND (fix E): inventory on a held ticker NOT in this cycle's footprint
        # (dropped from selection — its program near-ended / usd_day fell off) gets no maker
        # unwind above. Rest the REDUCING side at reference so it still flattens passively;
        # the taker backstop only fires near settlement / on a hard breach.
        fp_tickers = {m["ticker"] for m in footprint}
        for t, pos in list(naked_by.items()):
            if t in fp_tickers or t in flattened or abs(pos) < INV_TOLERANCE:
                continue
            try:
                ob = _get_book(t)              # WS mirror when it vouches, else REST (identical)
            except RuntimeError:
                break                                   # read budget exhausted
            except Exception:
                continue                                # transient — retry next cycle
            syl, _ = _levels(ob.get("yes_dollars") or [])
            snl, _ = _levels(ob.get("no_dollars") or [])
            sby = max((p for p, _ in syl), default=None)
            sbn = max((p for p, _ in snl), default=None)
            # ONE-SIDED BOOK (2026-07-26): a reducing quote rests on exactly ONE side — a long-YES
            # exit is a NO bid (needs sbn only) and a long-NO exit is a YES bid (needs sby only).
            # Demanding BOTH sides blocked a PLACEABLE exit whenever the book went one-sided, which
            # is precisely the state a losing position ends in. Measured live 2026-07-26T14:27:46Z:
            # 3 of 6 held positions had the side they needed and were skipped anyway. The old guard
            # also deferred to "taker handles it" — TAKER_FLATTEN=0, so nothing did, and the
            # position rode to settlement with no exit order resting at all.
            # (_capped_join ignores its second arg, so a None `other` there is inert.)
            need = sbn if pos > 0 else sby
            if need is None:
                plan["strand_no_exit_side"] = plan.get("strand_no_exit_side", 0) + 1
                continue                                # the side we must rest ON has no book
            if sby is not None and sbn is not None and sby + sbn >= 1.0:
                plan["strand_crossed_book"] = plan.get("strand_crossed_book", 0) + 1
                continue                                # crossed/stale book — do not chase it
            up_n = _unwind_price(sbn, cost_by.get(t, 0.0)) if sbn is not None else None
            up_y = _unwind_price(sby, cost_by.get(t, 0.0)) if sby is not None else None
            if pos > 0 and _ok_exit_price(up_n):
                desired[t] = [{"side": "no", "price_dollars": up_n,
                               "count": _unwind_size(_capped_join(up_n, sby), up_n, pos), "reason": "unwind"}]
            elif pos < 0 and _ok_exit_price(up_y):
                desired[t] = [{"side": "yes", "price_dollars": up_y,
                               "count": _unwind_size(_capped_join(up_y, sbn), up_y, pos), "reason": "unwind"}]
            else:
                plan["strand_unpriceable"] = plan.get("strand_unpriceable", 0) + 1

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
            # NO _unwind_price CAP HERE, DELIBERATELY (re-reviewed 2026-07-26). It is tempting:
            # this leg OPENS a position, the resulting pair always pays >= $1, so
            # (held_cost + price2 - 1) looks like a locked loss the cap should bound. Applying
            # it broke test_ladder_escape_hatch_splits_parked_unwind, and the TEST WAS RIGHT.
            #
            # The cross leg is bought at the ADJACENT book's own reference, which is priced
            # consistently with the held leg's. Worked example from that test: +20 YES on 4.050
            # at basis 0.62 while the book marks yes-bid 0.13, hedged with NO on 4.060 at 0.92.
            # Spending $9.20 converts an asset worth ~$1.30 into one worth >= $10.00 — roughly
            # neutral at market prices. It removes VARIANCE, not expected value.
            #
            # _unwind_price bounds loss against COST BASIS, so applying it here charges an
            # ALREADY-SUNK mark-to-market loss against a fresh hedge and refuses the hedge
            # precisely when the position has moved most — the same sunk-cost error this
            # session diagnosed in the unwind cap itself. The venue bound is the right guard;
            # size is bounded by MAX_MARKET_CAPITAL and by keep+cross <= |naked| below.
            if not _ok_exit_price(price2):
                continue
            # CAP AT |naked| BEFORE SPLITTING. The hatch's whole safety argument is
            # keep+cross <= |naked| so no fill combination can flip the sign. The two-sided
            # offset now sizes the reducing quote at ADD+|inv| (larger than |naked|), so the
            # split must be taken from |naked|, not from the quote's own count.
            c0 = min(int(uws[0]["count"]), int(abs(qn)))
            if c0 < 2:
                continue                                # nothing meaningful to split
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

        # DROP HYSTERESIS — before the diff, give a ROTATED-OUT ticker (absent from this cycle's
        # footprint, i.e. never looked at) a few cycles to come back instead of tearing its book
        # down and rebuilding it identically. Runs BEFORE cap_desired so a retained book is still
        # subject to the capital cap like anything else.
        grace_used = {}
        if DROP_GRACE > 0:
            try:
                _fp_now = {m["ticker"] for m in footprint}
                desired, grace_used = apply_drop_grace(
                    standing, desired, _fp_now,
                    (load_state().get("drop_grace") or {}), DROP_GRACE)
            except Exception:
                grace_used = {}
        desired, capped_markets = cap_desired(desired, usd_day)     # aggregate $ cap
        # AMEND-ON-DECREASE: pull out same-price size REDUCTIONS so they keep their queue position
        # instead of being cancelled and rebuilt at the back. `standing` itself is deliberately NOT
        # rebound — everything downstream (committed capital, failed-cancel deferral, the blackout
        # guard) keeps counting the amended order at its ORIGINAL, LARGER size. That over-counts
        # committed capital by the trimmed amount for one cycle, which is the safe direction.
        amends = []
        _std, _des = standing, desired
        if AMEND_DECREASE:
            amends, _std, _des = split_amends(standing, desired)
        cancels, creates = diff_orders(_std, _des)
        creates, budget_dropped = bound_creates(creates, cancels, usd_day)  # whole-ticker

        # execute — each order isolated; one failure never aborts the rest
        cancel_fail = create_fail = create_skipped = 0
        # AMENDS FIRST: a decrease only ever frees exposure, so it can never over-commit and must
        # not be starved by the write budget behind a queue of accumulating creates. A failure here
        # is benign — the order simply keeps resting at its old, LARGER size, which is the size
        # every capital check above already assumed.
        amend_fail = 0
        for _a in amends:
            try:
                client.amend_quote(_a["order_id"], _a["ticker"], _a["side"],
                                   _a["price_dollars"], _a["count"])
            except Exception as e:
                amend_fail += 1
                if first_create_err is None:
                    first_create_err = f"amend {_a['ticker']}: {e!r}"
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
        # FUNDING GATE (KALSHI_FUNDING_GATE=1): the resting BUY book that would draw cash IF it
        # fills = the SAME surviving-standing gross notional as `committed` right now, but WITHOUT
        # the already-spent held_cost term (that cash already left `balance` at fill; re-counting it
        # is the treadmill). Gated below against min(free_cash, MAX_TOTAL_CAPITAL). Fails CLOSED to
        # the legacy gross+held gate when the balance read failed this cycle (free_cash is None).
        funding_committed = committed
        funding_gate_on = bool(FUNDING_GATE) and free_cash is not None
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
            if not reducing:
                # ACCUMULATING gate — reducing/unwind creates are ALWAYS exempt (a risk-reducing
                # order can never over-commit; Kalshi frees the covered collateral on fill).
                #   Flag ON  : never rest more BUY notional than free cash can fund (HARD ceiling);
                #              MAX_TOTAL_CAPITAL stays a backstop -> whichever is smaller binds.
                #   Flag OFF : byte-for-byte the legacy `committed vs MAX_TOTAL_CAPITAL` gate.
                if funding_gate_on:
                    if funding_committed + cost > min(free_cash, MAX_TOTAL_CAPITAL):
                        create_skipped += 1
                        continue
                elif committed + cost > MAX_TOTAL_CAPITAL:
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
                if funding_gate_on and not reducing:
                    funding_committed += cost           # a filled accumulating buy would draw cash
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

        # PRE-CLOSE SETTLEMENT FLATTEN (KALSHI_PRECLOSE_FLATTEN, default 0 = OFF). Runs AFTER the
        # order-apply block so the reducing MAKER quote is already resting this cycle (maker-first);
        # then, within PRECLOSE_FLATTEN_MIN of MARKET CLOSE, it taker-crosses ONLY the naked residual
        # (>= STOP_TAKER_MIN_CT after a STOP_ESCALATE_S grace) so it never rides into settlement —
        # cancelling nothing, so a failed taker leaves the maker exit resting. Flag-OFF: this block
        # is skipped entirely (no st key, no plan key) -> the cycle is byte-for-byte legacy.
        if PRECLOSE_FLATTEN:
            grace_state = st.get("preclose_grace", {})
            try:
                _preclose_naked_flatten(client, held_by, now, plan, grace_state)
            except Exception as e:                          # a backstop bug must never abort the cycle
                plan["preclose_error"] = f"{e!r}"[:160]
                print(f"WARNING preclose flatten pass RAISED: {e!r} — cycle continues")
            st["preclose_grace"] = grace_state

        plan.update({
            "footprint": len(footprint), "quoted_markets": len(desired),
            "cancels": len(cancels), "creates": len(creates),
            "order_ops": len(cancels) + len(creates),
            "write_tokens": len(creates) * 10 + len(cancels) * 2,
            # retained fetch-fail markets are already IN desired -> do NOT subtract
            # them again (that double-counted and could go negative)
            "reads": _reads[0], "gated_out": len(footprint) - len(desired),
            # OPTION B attribution: how many books this cycle came from the WS mirror vs REST,
            # and how many times the provider FAILED (src_err > 0 means the mirror path is
            # broken and we are silently back on REST — that must be visible, not inferred).
            "book_mirror": _book_src["mirror"], "book_rest": _book_src["rest"],
            "book_src_err": _book_src["src_err"],
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
        # funding-gate observability — emitted ONLY when the gate is active so a flag-OFF plan row
        # is byte-identical to the legacy output (provable no-op).
        if funding_gate_on:
            plan["funding_gate"] = 1
            plan["funding_committed_usd"] = round(funding_committed, 2)
            plan["free_cash_usd"] = round(free_cash, 2)
        # pivot-select observability — emitted ONLY when the flag is on so a flag-OFF plan row is
        # byte-identical to the legacy output (same discipline as the funding-gate block above).
        if PIVOT_SELECT:
            plan["pivot_select"] = 1
            plan["pivot_pool"] = len(footprint)         # = len(consumed) after the reassign
            plan["pivot_quoted"] = len(desired)
        # stand-down observability — emitted ONLY when the flag is on so a flag-OFF plan row is
        # byte-identical to the legacy output (same discipline as the funding-gate/pivot blocks).
        # standdown_markets = books sized-down (join) or skipped (activate) this cycle; min_rho =
        # the thinnest effective reward density that tripped it, against the floor that drove it.
        if STANDDOWN:
            plan["standdown"] = 1
            plan["standdown_floor_usd_day"] = STANDDOWN_MIN_USD_DAY
            plan["standdown_markets"] = qstats.get("standdown", 0)
            if qstats.get("standdown"):
                plan["standdown_min_rho_usd_day"] = round(qstats.get("standdown_min_rho", 0.0), 2)
        # capture-gate observability — emitted ONLY when the flag is on so a flag-OFF plan row is
        # byte-identical to the legacy output (same discipline as the funding/pivot/standdown blocks).
        # capture_skipped_markets = markets skipped/reduced this cycle because our prospective R4
        # capture was below the floor; capture_min_pc_usd_day = the thinnest prospective capture that
        # tripped it — the calibration signal to compare against actual period-close credits.
        # PRESENCE GATE telemetry. presence_skipped_execution_only is the one to watch: markets that
        # WOULD have cleared the $1 floor at perfect execution and were skipped only because our
        # measured presence dragged them under. That is our defect, not the market's economics.
        # amends_ct = contracts of queue position PRESERVED this cycle that the legacy path would
        # have cancelled and rebuilt at the back of the book.
        # SCORE CACHE persisted ONCE per cycle (not per market) — scored = markets with a measured
        # capture carried forward; the rest still rank on the pool prior.
        if SCORE_RANK:
            try:
                import kalshi_market_scores as _kms
                _kms.save(SCORE_PATH, SCORES)
                plan["scored_markets"] = len(SCORES)
                plan["score_explore"] = SCORE_EXPLORE
            except Exception:
                pass
        # DROP GRACE: carry the per-ticker counter into the next cycle. Tickers absent from
        # grace_used are simply not written back, which RESETS them — correct, because they either
        # came back into the footprint or their grace ran out and the diff cancelled them.
        if DROP_GRACE > 0:
            st["drop_grace"] = grace_used
            plan["grace_retained"] = len(grace_used)
        if _SILENT:
            plan["silent_failures"] = dict(_SILENT)   # audited swallowers that actually fired
        if AMEND_DECREASE:
            plan["amends"] = len(amends)
            plan["amend_fail"] = amend_fail
            plan["amends_ct"] = sum(a["from_count"] - a["count"] for a in amends)
        if PRESENCE_GATE:
            plan["presence_gate"] = 1
            plan["presence_floor_usd"] = MIN_CREDIT_USD
            plan["presence_skipped_markets"] = qstats.get("presence_skipped", 0)
            plan["presence_skipped_execution_only"] = qstats.get(
                "presence_skipped_execution_only", 0)
            plan["presence_skipped_late_entry"] = qstats.get("presence_skipped_late_entry", 0)
            # markets we KEPT quoting though they no longer clear the floor — the accrual we would
            # have walked away from before the entry/continuation split
            plan["presence_continued_under_floor"] = qstats.get(
                "presence_continued_under_floor", 0)
            if qstats.get("presence_min_credit") is not None and qstats.get("presence_skipped"):
                plan["presence_min_credit_usd"] = round(qstats.get("presence_min_credit", 0.0), 4)
        if CAPTURE_GATE:
            plan["capture_gate"] = 1
            plan["capture_floor_usd_day"] = CAPTURE_MIN_USD_DAY
            plan["capture_skipped_markets"] = qstats.get("capture_skipped", 0)
            if qstats.get("capture_skipped"):
                plan["capture_min_pc_usd_day"] = round(qstats.get("capture_min_pc", 0.0), 3)
        # net-EV observability — emitted ONLY when the flag is on so a flag-OFF plan row is
        # byte-identical to the legacy output (same discipline as the funding/pivot/standdown/capture
        # blocks). netev_skipped_markets = markets skipped/reduced this cycle because their FAMILY is
        # calibrated net-negative (or the unproven model was <=0); netev_skipped_families = the
        # per-family breakdown; netev_min_signal = the worst net% (receipt) or model $/day that tripped.
        if NETEV_GATE:
            plan["netev_gate"] = 1
            plan["netev_min_margin_pct"] = NETEV_MIN_MARGIN_PCT
            plan["netev_skipped_markets"] = qstats.get("netev_skipped", 0)
            if qstats.get("netev_skipped"):
                plan["netev_min_signal"] = round(qstats.get("netev_min_signal", 0.0), 4)
                plan["netev_skipped_families"] = qstats.get("netev_families", {})
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
          f"reads={_reads[0]} books={_book_src['mirror']}ws/{_book_src['rest']}rest"
          + (f"/{_book_src['src_err']}ERR" if _book_src["src_err"] else "")
          + f" committed=${plan.get('committed_usd', plan.get('est_capital_usd',0)):,.2f}"
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
            _SILENT["flatten_cancel_fail"] += 1   # a maker order may fill DURING the flatten
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
                    _SILENT["ioc_cancel_fail"] += 1   # a NAKED taker order may now be resting
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
        if pos > 0 and _ok_exit_price(bn):
            side, price, other = "no", bn, (by or bn)
        elif pos < 0 and _ok_exit_price(by):
            side, price, other = "yes", by, (bn or by)
        else:
            print(f"flatten: {t} pos={pos:+.2f} — reducing side unpriceable, will re-check at escalation")
            continue
        price = _unwind_price(price, _costs.get(t, 0.0))             # loss-capped offset
        if not _ok_exit_price(price):
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


def _taker_cross_capped(client, ticker, cap_ct, long_yes, tries=4):
    """Cross AT MOST cap_ct contracts to the REDUCING side with marketable IOC orders, WITHOUT
    cancelling ANY resting order — the taker is ADDITIVE (pre-close settlement flatten, reason 3).

    Contrast with flatten_to_zero, which (a) reads the ticker's FULL venue position and crosses
    ALL of it — including a paired leg — and (b) cancels our resting orders FIRST. Here the caller
    passes cap_ct = |naked| (the UNPAIRED residual only, from ladder_pairing), and NOTHING is
    cancelled. Crossing is HARD-CAPPED at cap_ct and decremented by the venue's CONFIRMED
    fill_count each pass (never a lagging positions re-read), so cumulative crossing can PROVABLY
    never exceed |naked| — a paired leg is never touched (reason 1).

    NO SELF-TRADE despite the un-cancelled resting exit: our reducing maker quote sits on the
    COMPLEMENTARY book side (long-yes -> a resting NO bid == a YES ask at 1-bn, ABOVE the YES bid
    we hit), so a same-side IOC never matches it; and if Kalshi's self-match prevention ever did
    fire, the taker simply fails to fill and the resting exit REMAINS — which is exactly the
    desired never-strand outcome. Returns (flat_bool, n_contracts_crossed)."""
    remaining = int(round(abs(cap_ct)))
    if remaining < max(1, int(INV_TOLERANCE)):
        return True, 0
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
            fill = float(o.get("fill_count") or 0)             # CONFIRMED fill (venue-authoritative)
            # IOC must never rest; if the venue left it open (didn't honor IOC), cancel THAT order
            # (never our resting maker exit) so a naked non-post_only taker can't linger (fix G).
            if str(o.get("status") or "").lower() in ("resting", "open", "active"):
                try:
                    client.cancel_order(o.get("order_id"))
                except Exception:
                    _SILENT["ioc_cancel_fail"] += 1   # a NAKED taker order may now be resting
            remaining -= int(round(fill))
            crossed += int(round(fill))
            if fill <= 0:
                break                                          # nothing at the touch; don't spin
        except Exception:
            break
    return remaining < max(1, int(INV_TOLERANCE)), crossed


def _default_preclose_close_time(ticker):
    """MARKET close_time (trading END) for the pre-close flatten window — NOT the reward-period
    end_date carried in the footprint program (they can differ: gas-daily trades until 03:59Z but
    the reward period / settlement is separate). Read straight off the market, like the settle
    backstop. Returns the ISO string or None (unknown clock -> caller must NOT taker)."""
    try:
        return public_get(f"/trade-api/v2/markets/{ticker}").get("market", {}).get("close_time")
    except Exception:
        return None


def _preclose_naked_flatten(client, held_by, now, plan, grace_state,
                            close_time_of=_default_preclose_close_time):
    """NAKED-ONLY PRE-CLOSE SETTLEMENT FLATTEN (2026-07-24). For each event, exit ONLY the naked
    (unpaired, net-directional) ladder residual before the MARKET CLOSES, so it never rides into
    settlement; leave the FLOORED pairs (risk ~ strike gap) to self-hedge. This is the missing
    ACTIVE flatten — WIND_DOWN only STOPS quoting and the late-life gate only blocks ENTRY; nothing
    today crosses the naked residual before close, it just rides.

    Composition & the three reasons TAKER_FLATTEN was disabled (KALSHI_HANDOFF_2026-07-23 §2):
      1. CROSSES-NAKED-ONLY: the naked residual is ladder_pairing(held_by); the taker is capped at
         |naked| via _taker_cross_capped, so a paired leg is provably never crossed (the GASW-4.140
         bug: naked +6 of a +40 hold must cross <= 6, never 40).
      2. NAKED + NEAR-CLOSE ONLY: fires only WITHIN PRECLOSE_FLATTEN_MIN of MARKET CLOSE and only on
         a real naked residual (>= STOP_TAKER_MIN_CT after grace) — not the always-on whole-position
         de-hedge that paid ~8% spread on live pairs.
      3. NEVER-STRANDS-THE-EXIT: MAKER-FIRST (the reducing quote is rested by the unwind path this
         same cycle) + a STOP_ESCALATE_S grace; the taker is ADDITIVE and cancels NOTHING, so a
         taker that cannot fill on a one-sided book leaves the resting maker exit in place.

    grace_state: {ticker: iso_first_seen_naked_in_window}, persisted in quoter_state across cycles
    (a per-cycle process; the clock cannot live in memory). Cleared when a ticker leaves the window
    or goes flat. Mutated in place. Guarded by PRECLOSE_FLATTEN + non-dry_run at the call site;
    telemetry (plan keys) is written ONLY when the mechanism actually engages, so a flag-OFF plan
    row is byte-identical to legacy."""
    if not held_by:
        return
    naked_by = ladder_pairing(held_by)
    for t, npos in naked_by.items():
        if abs(npos) < INV_TOLERANCE:
            grace_state.pop(t, None)                       # flat/paired -> forget any grace clock
            continue
        try:
            close = close_time_of(t)
            mins = (parse_iso(close) - now).total_seconds() / 60.0 if close else None
        except Exception:
            mins = None
        if mins is None:
            # the ONLY arming signal (the market clock) is unknown -> do NOT taker on a blind
            # clock (mirror the settle backstop); count it so a persistent blind spot is visible.
            plan["preclose_check_failed"] = plan.get("preclose_check_failed", 0) + 1
            continue
        if mins > PRECLOSE_FLATTEN_MIN:
            grace_state.pop(t, None)                       # not in the window yet -> reset the clock
            continue
        # --- in the pre-close window WITH a naked residual ---
        plan["preclose_flatten"] = 1
        plan["preclose_naked_ct"] = round(plan.get("preclose_naked_ct", 0.0) + abs(npos), 2)
        # MAKER-FIRST grace: the reducing maker quote is (re)rested every cycle by the unwind path;
        # record when we FIRST saw this ticker naked-in-window and give the passive offset
        # STOP_ESCALATE_S to fill before crossing. Taker only AFTER the grace AND still material.
        first = grace_state.setdefault(t, now.isoformat())
        try:
            grace_elapsed = (now - parse_iso(first)).total_seconds()
        except Exception:
            grace_state[t] = now.isoformat()               # unparseable stamp -> restart the clock
            grace_elapsed = 0.0
        if grace_elapsed < STOP_ESCALATE_S:
            continue                                       # maker grace still running -> no taker
        if abs(npos) < STOP_TAKER_MIN_CT:
            continue                                       # residue below taker threshold -> leave resting
        # TAKER: cross AT MOST |naked|, additive, cancelling nothing (reasons 1 & 3).
        if client.mode == "dry_run":
            continue                                       # never taker in plan-only mode
        try:
            flat, nc = _taker_cross_capped(client, t, int(round(abs(npos))), npos > 0)
        except Exception as e:
            plan["preclose_taker_failed"] = plan.get("preclose_taker_failed", 0) + 1
            print(f"WARNING preclose flatten RAISED on {t} (naked {npos:+.2f}): {e!r}")
            continue
        plan["preclose_taker_ct"] = round(plan.get("preclose_taker_ct", 0.0) + nc, 2)
        print(f"preclose flatten {t}: naked {npos:+.2f}, {mins:.1f}min to close -> taker crossed "
              f"{nc} ct ({'FLAT' if flat else 'RESIDUAL — maker exit remains resting'})")
        if flat:
            grace_state.pop(t, None)                       # naked cleared -> forget the clock
        else:
            plan["preclose_taker_failed"] = plan.get("preclose_taker_failed", 0) + 1


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


def _strike_of(ticker, stats=None):
    """Numeric strike from a ladder ticker: everything AFTER the SERIES-EVENT prefix, so a
    NEGATIVE strike keeps its sign ('KXCPI-26SEP-T-0.4' -> -0.4, verified live on the public
    API). Taking only the last dash-field silently returned +0.4 there — and a sign flip inverts
    strike ordering, which would let ladder_pairing mark a genuinely UNFLOORED combo as paired
    and strip every guard from it (review 07-22; sub-zero-F winter temp strikes are the live
    exposure). None when unparseable -> that ticker never participates in pairing.

    REAL TICKER SHAPES (parsing fix 2026-07-23):
      KXAAAGASD-26JUL22-4.055       3 fields, bare strike
      KXTEMPNYCH-26JUL2117-T81.99   'T'-prefixed strike
      KXCPI-26SEP-T-0.4             'T'-prefixed NEGATIVE strike (the sign MUST survive)
      KXAAAGASM-25MAR31-US-4.00     LEGACY 4-part, region-qualified. float('US-4.00') raised,
                                    so this returned None and ladder_pairing went DARK
                                    SILENTLY — 100% of that inventory read as unpairable with
                                    no error anywhere. Latent today (gas dropped the '-US'
                                    suffix); it re-arms the moment Kalshi restores it.
    Leading PURELY-ALPHABETIC qualifier fields are dropped — never the LAST field (that is a
    categorical outcome, not a qualifier) and never a bare 'T' (that is the strike prefix, and
    eating it would flip the sign of 'T-0.4').

    CATEGORICAL strikes ('-HELLO', '-LAL') still return None BY DESIGN: they are not thresholds,
    must never be ordered, and must never be paired. That is not an error — but it IS invisible,
    so every failure bumps stats['strike_parse_failed'] when a stats dict is supplied. Without
    the counter a fully dark pairing pass is indistinguishable from 'no pairs were available'."""
    def _fail():
        if stats is not None:
            stats["strike_parse_failed"] = stats.get("strike_parse_failed", 0) + 1
        return None
    try:
        fields = ticker.split("-")
    except (AttributeError, TypeError):
        return _fail()
    if len(fields) < 3:
        return _fail()
    fields = fields[2:]
    while len(fields) > 1 and fields[0].isalpha() and fields[0] != "T":
        fields.pop(0)                      # 'US' in KXAAAGASM-25MAR31-US-4.00
    try:
        return float("-".join(fields).lstrip("T"))
    except (ValueError, TypeError):
        return _fail()


def ladder_pairing(held_by, stats=None):
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
    event_deltas is unchanged either way.

    stats (optional dict): receives 'strike_parse_failed' — the count of HELD tickers whose
    strike would not parse, i.e. inventory this pass could not even consider for pairing. A
    non-zero count on a book that expects to pair means the pairing is DARK; run_once surfaces
    it in the plan row. Tickers with zero position are not counted (nothing went dark)."""
    naked = dict(held_by)
    by_event = defaultdict(list)
    for t, n in (held_by or {}).items():
        if not n:
            continue
        s = _strike_of(t, stats)
        if s is not None:
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


def _is_ladder_event(tickers):
    """TRUE only when the tickers of ONE event form a provable ADDITIVE THRESHOLD LADDER:
    every one of them parses to a numeric strike, and those strikes are DISTINCT (a threshold
    ladder has exactly one contract per threshold — a duplicate proves the strike field is not
    the discriminator, so the event is something else). Fail SAFE: unprovable => not a ladder.

    Canon §T: reward accrues per CONTRACT, risk accrues per EVENT, and the event aggregate is
    the true directional risk ONLY for an 'above X' ladder, where the strikes are directionally
    correlated and adjacent ones partly offset. A CATEGORICAL series (independent word-binaries,
    mutually_exclusive:false, strike_type:custom) or a bucket/range series fails this test:
    its strikes are independent or ANTI-correlated, and netting them is not conservative, it is
    wrong in the dangerous direction. This is the structural reason categorical series are
    unsafe to admit — it no longer depends on allowlist hygiene."""
    strikes = []
    for t in tickers:
        s = _strike_of(t)
        if s is None:
            return False
        strikes.append(s)
    return len(set(strikes)) == len(strikes)


def event_deltas(held_by):
    """Aggregate SIGNED net position across the strikes of each event — but ONLY where the
    event is a PROVABLE additive threshold ladder (_is_ladder_event). Kalshi ticker =
    SERIES-EVENT-STRIKE; strikes of one nested-threshold ladder ('above X') are DIRECTIONALLY
    correlated, so the event aggregate — not the per-ticker position — is the true directional
    exposure.

    DEFECT FIXED 2026-07-23: the old version bucketed EVERY ticker by '-'.join(split('-')[:2])
    with no additivity test. On a categorical series that collapses N INDEPENDENT risks into one
    key, so long 20 of one word-binary + short 20 of another netted to ZERO and the event read
    FLAT while two live naked exposures were carried. ladder_pairing already abstains safely on
    non-numeric strikes (_strike_of -> None); this is the equivalent guard for the aggregate.

    Returns {risk_key: signed_delta}. risk_key is the EVENT key for a proven ladder and the
    TICKER ITSELF otherwise (independent risks are reported independently, never netted). Look a
    ticker up with event_delta_for() — a bare ev[_event_key(t)] index silently reads 0.0 on a
    categorical event, which is exactly the failure this fix removes."""
    groups = defaultdict(list)
    for t in (held_by or {}):
        groups[_event_key(t)].append(t)
    ev = defaultdict(float)
    for k, tickers in groups.items():
        ladder = _is_ladder_event(tickers)
        for t in tickers:
            ev[k if ladder else t] += held_by[t]
    return dict(ev)


def event_delta_for(ev_delta, ticker):
    """Resolve one ticker's directional delta out of an event_deltas() result. TICKER key FIRST
    (an unprovable/categorical event is keyed per ticker, and an independent risk must never
    inherit its siblings' net), then the EVENT key (proven ladder — this is how a FLAT strike
    inside a directional ladder still gets throttled), then 0.0."""
    if ticker in ev_delta:
        return ev_delta[ticker]
    return ev_delta.get(_event_key(ticker), 0.0)


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
            _SILENT["standing_row_skip"] += 1     # unreadable resting order -> risk of a DUPLICATE
            continue                              # create; see the _SILENT block for why it matters
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

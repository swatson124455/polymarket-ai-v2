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
import hashlib
import json
import math
import os
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_client import KalshiOrderClient, API_ROOT, PROD_BASE  # noqa: E402
import kalshi_exit_calc  # noqa: E402  (pure exit-cost arithmetic; receipt-verified fee model)

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
_SILENT_PREV = {}                   # audit batch 3 (J2): last cycle's snapshot -> per-cycle delta
# audit batch 3 (J6, operator-approved 2026-07-29): a market whose creates the venue rejects
# every cycle (bad params, closed, restricted) was retried forever, burning write tokens and
# spamming create_fail. After CREATE_FAIL_RATCHET_N consecutive failures the ticker's
# ACCUMULATING creates cool off exponentially; reducing/unwind creates are NEVER ratcheted
# (risk-reducing orders must always be attempted). In-memory by design: a restart retries
# once and re-ratchets — cheap, and it avoids another quoter_state amnesty landmine.
# ticker -> [consecutive_fails, next_try_mono]
_CREATE_FAIL_RATCHET = {}
CREATE_FAIL_RATCHET_N = 3
CREATE_FAIL_RATCHET_BASE_S = 60.0
CREATE_FAIL_RATCHET_MAX_S = 3600.0


def _create_ratchet_blocked(ticker, reducing):
    """J6: True when this ticker's ACCUMULATING creates are cooling off."""
    r = _CREATE_FAIL_RATCHET.get(ticker)
    return bool(r and not reducing and r[0] >= CREATE_FAIL_RATCHET_N
                and time.monotonic() < r[1])


def _create_ratchet_fail(ticker):
    """J6: record one consecutive create failure; arm/extend the cool-off past N."""
    r = _CREATE_FAIL_RATCHET.setdefault(ticker, [0, 0.0])
    r[0] += 1
    if r[0] >= CREATE_FAIL_RATCHET_N:
        r[1] = time.monotonic() + min(
            CREATE_FAIL_RATCHET_BASE_S * (2 ** (r[0] - CREATE_FAIL_RATCHET_N)),
            CREATE_FAIL_RATCHET_MAX_S)


def _silent_report(plan):
    """J2: silent_failures = what fired THIS cycle; *_total keeps the lifetime dict."""
    if not _SILENT:
        return
    delta = {k: v - _SILENT_PREV.get(k, 0) for k, v in _SILENT.items()
             if v - _SILENT_PREV.get(k, 0) > 0}
    if delta:
        plan["silent_failures"] = delta
    plan["silent_failures_total"] = dict(_SILENT)
    _SILENT_PREV.clear()
    _SILENT_PREV.update(_SILENT)
_REALIZED_BY = {}                   # ticker -> venue realized_pnl_dollars; refreshed by _held_cost


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
                 "KALSHI_HELD_MAX_USD",
                 "KALSHI_DAILY_LOSS_HALT_USD")


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

# TOTAL CAP TRACKS THE PORTFOLIO (operator directive 2026-07-31: "base total capital on total
# portfolio until further notice"). The whole-book capital cap is live MARK EQUITY (the same
# cash+marked-positions read the day-meters use), refreshed every healthy balance cycle, with
# the env MAX_TOTAL_CAPITAL kept as a static ceiling on top — min(equity, env) binds. Before
# any equity has ever been observed (first cycle of a fresh install) the env cap alone binds.
# A balance-fail cycle keeps the LAST-GOOD equity (persisted equity_prev) — never a guess.
_TOTAL_CAP_EFF = [None]                      # [0] = last-known mark equity (list: 3.13 scoping)


def _total_cap():
    eq = _TOTAL_CAP_EFF[0]
    try:
        return min(MAX_TOTAL_CAPITAL, float(eq)) if eq is not None else MAX_TOTAL_CAPITAL
    except (TypeError, ValueError):
        return MAX_TOTAL_CAPITAL
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
# INCUMBENCY BONUS (operator slate item A, 2026-07-29, weighted heavily by operator): a market
# we rested in LAST cycle keeps its seat unless a challenger beats it by this margin — the queue
# position built by sitting is an asset, destroyed on exit. Value is PROVISIONAL (HYPOTHESIS)
# until the Aug 1-2 receipts price a seat; sunk losses buy no loyalty (loss governor's job).
INCUMBENCY_BONUS = _envf("KALSHI_INCUMBENCY_BONUS", 0.0)     # 0 = OFF (provable no-op)
_INCUMBENT_TICKERS = set()          # prev cycle's standing tickers; refreshed each run_once
# EXPLORE PROBE SIZING (operator slate item E: "$2 bopping around"): exploration slots get
# probe-sized accumulating orders instead of full _capped_join size, so sampling a market
# costs a few dollars of collateral, not an earner's full allocation. 0 = OFF (full size).
EXPLORE_PROBE_CT = _envi("KALSHI_EXPLORE_PROBE_CT", 0)


def _load_scores():
    """Fail-OPEN to {} -> every market unscored -> ranking is byte-for-byte the legacy pool order."""
    try:
        import kalshi_market_scores
        return kalshi_market_scores.load(SCORE_PATH)
    except Exception:
        return {}


# loaded ONCE at import and ONLY when the flag is on (flag-off does zero file IO -> provable no-op)
SCORES = _load_scores() if SCORE_RANK else {}

# SCORES is now touched from TWO threads: the trading cycle (run_once on the daemon's worker
# thread) and the background venue sweeper (freshness root-fix 2026-07-30, Phase 1). Every
# SCORES touchpoint — rank, shadow_rank, update, evict+save, and the sweeper's writes — takes
# this lock. Hold times are dict-op tiny; evict() iterates the dict and would RuntimeError on
# a concurrent resize, which is exactly what the lock prevents.
SCORES_LOCK = threading.Lock()
_SWEEPER = None      # set by _ensure_sweeper(); stays None when KALSHI_SWEEP_ENABLED=0

# --- CAPITAL-AWARE RANKING TELEMETRY (KALSHI_CAPRANK_TELEMETRY, default 0 = OFF, provable no-op) -
# Operator-ordered 2026-07-29 (task #1), TELEMETRY-FIRST: the current rank is blind to dollars
# committed per market and to per-market realized fill cost. This block only LOGS the would-be
# capital-aware ordering alongside the actual one (caprank-YYYYMMDD.jsonl, one row per cycle) so
# the operator can review the divergence on real cycles. Selection is UNTOUCHED — flipping the
# live rank to cap_score is a separate, operator-named change. See kalshi_capital_rank.py.
CAPRANK_TELEMETRY = _envi("KALSHI_CAPRANK_TELEMETRY", 0)
# receipt-vs-model calibration multiplier on the capture term. STAYS 1.0 until the first real
# reward credit lands (Thu 2026-07-31 ballot window) — receipts > models.
CAPRANK_CALIB = _envf("KALSHI_CAPRANK_CALIB", 1.0)
# RISK-AVERSION knobs (operator ask 2026-07-29) — shadow-only, defaults 1.0 = prior behavior:
# lambda multiplies the measured fill-cost penalty; the haircuts discount evidence quality
# (prospective = book measured offline but our join hypothetical; unknown = pure pool guess).
CAPRANK_RISK_LAMBDA = _envf("KALSHI_CAPRANK_RISK_LAMBDA", 1.0)
CAPRANK_PROSPECTIVE_HAIRCUT = _envf("KALSHI_CAPRANK_PROSPECTIVE_HAIRCUT", 1.0)
CAPRANK_UNKNOWN_HAIRCUT = _envf("KALSHI_CAPRANK_UNKNOWN_HAIRCUT", 1.0)
FILL_COST_PATH = os.environ.get("KALSHI_FILL_COST_PATH",
                                os.path.join(DATA_DIR, "kalshi_fill_costs.json"))
PROSPECTIVE_PATH = os.environ.get("KALSHI_PROSPECTIVE_PATH",
                                  os.path.join(DATA_DIR, "kalshi_prospective_capture.json"))


def _load_fill_costs():
    """Fail-OPEN to {} -> every market costed $0/day; the shadow rank simply has no penalty term.
    Re-read on every telemetry call (one small json read per ~2-min cycle) so re-running the
    feed tools takes effect immediately — an import-once cache here would silently pin the
    shadow to stale data until the next service restart."""
    try:
        import kalshi_capital_rank
        return kalshi_capital_rank.load_fill_costs(FILL_COST_PATH)
    except Exception:
        return {}


def _load_prospective():
    """Fail-OPEN to {} -> no offline sweep yet; unmeasured markets keep their pool prior."""
    try:
        import kalshi_capital_rank
        return kalshi_capital_rank.load_prospective(PROSPECTIVE_PATH)
    except Exception:
        return {}


# W3/D2 FOLLOW-THE-PROFIT (scale plan B1; ships OFF = provable no-op). The feed is built
# OFFLINE by kalshi_credit_feedback.py from receipts (credit_history + orders + settlements);
# the rank multiplies a "paid" series' base by D2_BONUS and a filled-never-paid-and-due
# series' base by D2_NEVERPAID_MULT. Sweep evidence (w3_policy_sweep over 6 recorded days,
# 2026-08-04): the never-paid penalty is the working lever — never-paid median rank 16-25 ->
# 31-36 while payer ranks improve; the bonus alone moves almost nothing. Enabling is a
# separate operator-named deploy after the P2 clean days (ruling 2026-08-04, option a).
D2_FEEDBACK = _envi("KALSHI_D2_FEEDBACK", 0)
D2_BONUS = _envf("KALSHI_D2_BONUS", 1.5)
D2_NEVERPAID_MULT = _envf("KALSHI_D2_NEVERPAID_MULT", 0.5)
CREDIT_FEEDBACK_PATH = os.environ.get("KALSHI_CREDIT_FEEDBACK_PATH",
                                      os.path.join(DATA_DIR, "kalshi_credit_feedback.json"))


def _load_credit_feedback():
    """Fail-OPEN to {} (multiplier 1.0 everywhere). Flag OFF -> None without touching disk."""
    if not D2_FEEDBACK:
        return None
    try:
        import kalshi_capital_rank
        return kalshi_capital_rank.load_credit_feedback(CREDIT_FEEDBACK_PATH)
    except Exception:
        return {}


# W4/D3 SIZE RAMP (operator-ruled 2026-08-02 "5 then 10 then 25 then 50, >=10 min per rung";
# built 2026-08-05 under the proceed-all-build ruling after the first-touch loss: 78% of the
# -$7.97 restart-window loss was ONE 30-ct fill on KXNETFLIXTOPVIEWSTV, a series this account
# had never traded — fills-per-lot separates losers at AUC 0.982 (master plan §4) while every
# dollar cap sat inert). Ships OFF (KALSHI_D3_RAMP=0 = provable no-op).
#   Rung = time since WE first intended accumulating quotes on the ticker (persisted in quoter
#   state under "d3_first_seen", so a restart cannot amnesty a young market to full size; a
#   ticker that leaves the desired set re-enters at rung 0 — conservative by construction).
#   W7 NEW-SERIES CLAMP rides the same helper: a series with NO settled evidence of ours
#   (no credits, no due filled events in the credit-feedback feed) is held at
#   D3_NEWSERIES_MAX_RUNG regardless of age. Time-based rungs alone would NOT have capped
#   the NETFLIX loss (the market was ~109 min old at the fill, top rung); the history clamp
#   is the piece that would have (rung 1 = 10 ct -> ~1/3 the damage). A MISSING feedback
#   table therefore clamps DOWN, not open — for a risk limiter the conservative direction is
#   smaller size, the exact opposite of the estimator-fail-open doctrine, and deliberate.
#   Unwind quotes are NEVER ramped (de-risk is never gated — house doctrine).
W12_PRICE_SHAPE = _envi("KALSHI_W12_PRICE_SHAPE", 0)   # 0 = OFF (provable no-op)
W12_SHAPE_EXP = _envf("KALSHI_W12_SHAPE_EXP", 1.0)     # P2-receipt calibration knob


def _w12_shape(p):
    """The ONE price-shape implementation -- _prospective_capture AND the telemetry row's
    parallel capture math both call it (review finding A: shaping only one of the two split
    the sweeper/ALLOC feed into mixed shaped/unshaped bases, up to 8.6x apart per ticker)."""
    p = min(0.99, max(0.01, float(p)))
    return (4.0 * p * (1.0 - p)) ** W12_SHAPE_EXP
ALLOW_PROBE_EXCEPTION = _envi("KALSHI_ALLOW_PROBE_EXCEPTION", 0)   # 0 = allowlist absolute
PROBE_MAX_SLOTS = _envi("KALSHI_PROBE_MAX_SLOTS", 5)   # concurrent probe markets, "as small
#   as you can to get what is needed" (operator 2026-08-05): 5 probes x EXPLORE_PROBE_CT=5ct
#   bounds discovery exposure to ~$12 notional worst-case while each probe stays big enough
#   for its accrual to clear the venue's $1 credit floor (a smaller probe on a 100-ct-target
#   book earns a share too small to ever pay, which reads as a false "never pays").
D3_RAMP = _envi("KALSHI_D3_RAMP", 0)
D3_RUNG_S = _envf("KALSHI_D3_RUNG_S", 600.0)
D3_NEWSERIES_MAX_RUNG = _envi("KALSHI_D3_NEWSERIES_MAX_RUNG", 1)   # -1 disables the clamp
try:
    D3_RUNGS = [int(x) for x in os.environ.get("KALSHI_D3_RUNGS", "5,10,25,50").split(",")
                if int(x) > 0]
except (TypeError, ValueError):
    D3_RUNGS = [5, 10, 25, 50]
if not D3_RUNGS:
    D3_RUNGS = [5, 10, 25, 50]


def _d3_ramp_ct(ticker, now_ts, first_seen, feedback):
    """Contract cap for this ticker's ACCUMULATING quotes at this moment. Registers the
    ticker's first-seen timestamp as a side effect (rung 0 on first sight)."""
    fs = first_seen.get(ticker)
    if fs is None:
        first_seen[ticker] = float(now_ts)
        fs = now_ts
    rung = min(int(max(0.0, float(now_ts) - float(fs)) / max(D3_RUNG_S, 1.0)),
               len(D3_RUNGS) - 1)
    if D3_NEWSERIES_MAX_RUNG >= 0:
        row = (feedback or {}).get((ticker or "").split("-")[0])
        # Proven = the series has actually PAID (credits_n > 0). Review F2 (2026-08-05):
        # counting due_filled_events as proof let a CONVICTED never-paying series ramp to
        # full size while the merely-unknown were held at 10 ct — backwards for a
        # follow-the-profit sizer. Size-trust now requires a receipt; never_paid_due and
        # unknown alike hold at the clamp rung (presence continues, size stays probe-scale
        # — sizing is not benching).
        proven = isinstance(row, dict) and (row.get("credits_n") or 0) > 0
        if not proven:
            rung = min(rung, D3_NEWSERIES_MAX_RUNG)
    return D3_RUNGS[rung]


def _d3_first_seen_ensure(st):
    """Restore the ramp first-seen map BEFORE the select-budget walk needs it (wave-1
    review C-1: the lazy quote-loop restore ran AFTER the walk, so the first cycle after
    every restart est'd every ticker at rung 0 — over-admitting and firing the backstop
    alarm whose documented interpretation is 'tighten the margin', i.e. restart noise
    masquerading as a config signal). Idempotent; the quote-loop loader stays as belt."""
    global _D3_FIRST_SEEN, _D3_LAST_DESIRED
    if _D3_FIRST_SEEN is None:
        try:
            _D3_FIRST_SEEN = {str(k): float(v) for k, v in
                              (st.get("d3_first_seen") or {}).items()}
        except Exception:
            _D3_FIRST_SEEN = {}
        try:                                       # F14: the grace map survives restarts too
            _D3_LAST_DESIRED = {str(k): float(v) for k, v in
                                (st.get("d3_last_desired") or {}).items()}
        except Exception:
            _D3_LAST_DESIRED = {}
    return _D3_FIRST_SEEN


D3_KEEP_S = _envf("KALSHI_D3_KEEP_S", 1800.0)   # F14: ramp memory survives absences up to this
_D3_LAST_DESIRED = {}                           # ticker -> last ts it was in the intended book


def _d3_prune_first_seen(st, desired, now_ts):
    """F14 (reward audit 2026-08-06): pruning first-seen to `t in desired` reset a proven
    earner's ramp to rung 0 on ANY one-cycle absence (gate blip, budget refusal, fetch fail)
    — a 5ct sawtooth on payers whose cost is credit, not risk. A ticker now keeps its ramp
    clock through absences up to D3_KEEP_S; only a SUSTAINED absence re-enters at rung 0.
    Writes the pruned map to st['d3_first_seen'] (and the last-seen map beside it) and
    returns the tracked count for the plan row."""
    global _D3_LAST_DESIRED
    for t in desired:
        _D3_LAST_DESIRED[t] = float(now_ts)
    for t in _D3_FIRST_SEEN:
        # first observation of an absence (or first cycle after deploy/restart with no
        # stamp): the grace clock starts NOW — never backdates to 0/epoch, which would
        # prune instantly and reproduce the sawtooth this fix removes.
        _D3_LAST_DESIRED.setdefault(t, float(now_ts))
    keep = {t for t in _D3_FIRST_SEEN
            if t in desired
            or (float(now_ts) - float(_D3_LAST_DESIRED.get(t, 0.0))) <= D3_KEEP_S}
    for t in list(_D3_FIRST_SEEN):
        if t not in keep:
            _D3_FIRST_SEEN.pop(t, None)
            _D3_LAST_DESIRED.pop(t, None)
    for t in list(_D3_LAST_DESIRED):
        # review #6: entries stamped for tickers that never entered first-seen (unwind-only
        # rows, D3-off intervals) would otherwise accumulate for the daemon's lifetime
        if t not in _D3_FIRST_SEEN and (float(now_ts) - _D3_LAST_DESIRED[t]) > D3_KEEP_S:
            _D3_LAST_DESIRED.pop(t, None)
    st["d3_first_seen"] = dict(_D3_FIRST_SEEN)
    st["d3_last_desired"] = {t: v for t, v in _D3_LAST_DESIRED.items() if t in _D3_FIRST_SEEN}
    return len(keep)


def _d3_est_ct(ticker, now_ts):
    """SIDE-EFFECT-FREE ramp ct for BUDGET ESTIMATION (operator-ruled 2026-08-06,
    pulled forward from the W6 gate): the select-budget walk charged FULL est
    (~$45-50) for markets the ramp then sizes at 5-25ct — measured binding 00:52Z:
    used 208.4/210.25 'deployed' vs $16.85 actually committed (~12x over-read),
    blocking CHIPBURRITO ($990/day) at drop_budget_full and family-evicting
    established books. Mirrors _d3_ramp_ct WITHOUT registering first-seen (the walk
    must never start a ticker's ramp clock). Unknown ticker -> rung 0."""
    fs = (_D3_FIRST_SEEN or {}).get(ticker)
    if fs is None:
        rung = 0
    else:
        rung = min(int(max(0.0, now_ts - float(fs)) / max(D3_RUNG_S, 1e-9)),
                   len(D3_RUNGS) - 1)
    if D3_NEWSERIES_MAX_RUNG >= 0:
        row = _d3_feedback_cached(now_ts).get((ticker or "").split("-")[0])
        proven = isinstance(row, dict) and (row.get("credits_n") or 0) > 0
        if not proven:
            rung = min(rung, D3_NEWSERIES_MAX_RUNG)
    return D3_RUNGS[rung]


def _d3_apply_ramp(q, ramp_ct, qstats=None):
    """Clamp accumulating quote counts to ramp_ct in place; unwind quotes untouched.
    macro_probe quotes are ALSO untouched (blind review 2026-08-06 #1): the ladder's whole
    purpose is meeting Target Size — rung-clamping it to 5-10ct re-creates the exact
    receipts deadlock D-C exists to break, while its risk is already bounded in DOLLARS
    (deep-discount levels; reserved $ = max loss), which contract rungs cannot express."""
    for o in q:
        if o.get("reason") not in ("unwind", "macro_probe") and o.get("count", 0) > ramp_ct:
            if qstats is not None:
                qstats["d3_ramp_capped"] = qstats.get("d3_ramp_capped", 0) + 1
            o["count"] = max(1, int(ramp_ct))
    return q


_D3_FIRST_SEEN = None                 # {ticker: epoch} lazily restored from quoter state
_D3_FB_CACHE = {"ts": 0.0, "table": {}}


# --- D-A: VENUE PER-USER REWARD-ESTIMATE FEED (operator-ruled 2026-08-06, ships OFF) --------
# The web chip's own data source (GET /v1/incentives/users/{uid}/estimates, discovered
# 2026-08-06) is snapshotted every 5 min by kalshi_estimates_recorder.py. With the flag ON,
# the $1-floor gate sees max(model, venue_est): the venue's own number can only REDUCE false
# refusals (the reward audit's spread-thin-earn-zero class) — it never admits less than the
# model would, and it never substitutes for the model (semantics caveat: the venue calls it
# a live estimate that "can move up or down"; observed 08-06 it climbed like an accrual;
# est→credit truth checkpoint = KXAPRPOTUS program end 2026-08-07T15:00Z).
# Disk-only: the trading cycle NEVER calls the estimates API itself — recorder dies -> feed
# goes stale -> fail-open to the model (staleness bound below).
# --- D-C: MACRO-PROBE (operator-ruled 2026-08-06; ships OFF via empty ticker list) ----------
# The 5ct probe is formula-invisible where Target Size is 1000ct and the book is empty — the
# side never qualifies, so receipts can never arrive from exactly the LOW-competition pools
# the thesis targets (reward audit §3.5). The macro-probe rests a TWO-SIDED deep-discount
# ladder meeting Target on operator-DESIGNATED tickers only. Deep-discount levels mean the
# reserved dollars ARE the max loss and any fill is far below fair (a bargain, not a pickoff);
# per the D-F ruling the ladder spans 3 real levels, never a 1c-only wall. Flat-only — held
# inventory routes to the standard reducing/exit machinery untouched.
MACRO_PROBE_TICKERS = frozenset(
    t.strip() for t in os.environ.get("KALSHI_MACRO_PROBE_TICKERS", "").split(",") if t.strip())
MACRO_PROBE_USD = _envf("KALSHI_MACRO_PROBE_USD", 60.0)   # reserved-$ cap per market (both sides)
MACRO_PROBE_TOP = _envf("KALSHI_MACRO_PROBE_TOP", 0.03)   # top ladder level, dollars


def _macro_probe_quotes(m, yl, nl, stats=None, own=None):
    """Two-sided Target-meeting ladder for a designated macro-probe market. Per side:
    need = Target − RIVAL external depth (the public book includes our own resting ladder —
    blind review 2026-08-06 #2: without subtracting it, cycle 2 reads 'target met', pulls
    everything, cycle 3 re-rests: a permanent cancel/create oscillation halving snapshot
    capture); 3-level ladder MACRO_PROBE_TOP..−2 ticks (floored at 1c, and NEVER a single
    level — review #13: top=0.01 would produce the 1c-only wall the D-F ruling forbids).
    Per-side cost cap = min(MACRO_PROBE_USD/2, 0.9×HELD_MAX_USD) — review #5: a full-side
    fill above HELD_MAX_USD trips the whole-book reduce-only breaker, so the ladder must
    never be able to create a held position the breaker treats as catastrophic; raising the
    cap is an OPERATOR decision on both knobs together. Unaffordable Target -> rest NOTHING
    on the whole market (partial depth cannot qualify = pure fill risk; counted)."""
    target = float(m.get("target") or 0.0)
    if target <= 0:
        return []
    own = own or {}
    side_cap = min(MACRO_PROBE_USD / 2.0, 0.9 * HELD_MAX_USD)
    quotes = []
    for side, levels in (("yes", yl), ("no", nl)):
        ext = sum(s for _, s in levels if s > 0)
        ext_rivals = max(0.0, ext - float(own.get(side) or 0.0))
        need = max(0.0, target - ext_rivals)
        if need <= 0:
            continue                      # side already qualifies on RIVAL depth alone
        prices = [round(MACRO_PROBE_TOP - i * TICK, 2) for i in range(3)]
        prices = [p for p in prices if p >= 0.01 - 1e-9]
        if len(prices) < 2:
            if stats is not None:
                stats["macro_probe_wall_refused"] = stats.get("macro_probe_wall_refused", 0) + 1
            return []                     # single-level wall — configuration refused, never rested
        weights = [0.4, 0.3, 0.3][:len(prices)]
        wsum = sum(weights)
        counts = [int(need * w / wsum) + 1 for w in weights]      # ceil-ish: covers need
        cost = sum(p * c for p, c in zip(prices, counts))
        if cost > side_cap:
            if stats is not None:
                stats["macro_probe_unaffordable"] = stats.get("macro_probe_unaffordable", 0) + 1
            return []
        for p, c in zip(prices, counts):
            quotes.append({"side": side, "price_dollars": p, "count": c,
                           "reason": "macro_probe"})
    if quotes and stats is not None:
        stats["macro_probe_markets"] = stats.get("macro_probe_markets", 0) + 1
    return quotes


EST_FEED = _envi("KALSHI_EST_FEED", 0)
EST_FEED_MAX_AGE_S = _envf("KALSHI_EST_FEED_MAX_AGE_S", 1800.0)   # ignore snapshots older
EST_FEED_MIN_FRAC = _envf("KALSHI_EST_FEED_MIN_FRAC", 0.25)  # review #3: the estimate is
# (at least partly) accrued-to-date; late in a window those are BANKED dollars that pay
# regardless of quoting now, so flooring a flat re-ENTRY on them buys pure fill risk.
# The floor applies only while >= this fraction of the program window remains; semantics
# refine at the 2026-08-07T15:00Z APRPOTUS est->credit checkpoint.
_EST_FEED_CACHE = {"ts": 0.0, "table": {}}


def _est_feed_cached(now_ts, max_age_s=120.0):
    """{market_ticker: venue_est_usd} from the newest recorder snapshot, re-read at most
    every max_age_s. Fail-open {} on any read/parse problem and on snapshots older than
    EST_FEED_MAX_AGE_S (a dead recorder must degrade to the model, never freeze a value)."""
    if now_ts - _EST_FEED_CACHE["ts"] > max_age_s:
        try:
            table = {}
            import glob as _glob
            files = sorted(_glob.glob(os.path.join(DATA_DIR, "estimates-*.jsonl")))
            last = None
            if files:
                with open(files[-1]) as fh:
                    for _ln in fh:
                        if _ln.strip():
                            last = _ln
            if last:
                snap = json.loads(last)
                snap_ts = parse_iso(snap["ts"]).timestamp()
                if now_ts - snap_ts <= EST_FEED_MAX_AGE_S:
                    pmap = json.load(open(os.path.join(DATA_DIR, "kalshi_program_map.json")))
                    for e in snap.get("estimates") or []:
                        pr = pmap.get(str(e.get("program_id"))) or {}
                        mt = pr.get("market_ticker")
                        # review #4: an ENDED program's estimate is banked dollars pending
                        # payout, not earnable-future credit — it must never floor an
                        # ENTRY gate. Rows whose program end has passed are skipped.
                        try:
                            _pend = pr.get("end_date")
                            if _pend and parse_iso(_pend).timestamp() <= now_ts:
                                continue
                        except Exception:
                            continue          # unparseable program end -> not evidence
                        if mt:
                            table[mt] = table.get(mt, 0.0) + \
                                float(e.get("reward_centicents") or 0) / 10000.0
            _EST_FEED_CACHE["table"] = table
            _EST_FEED_CACHE["snap_ts"] = snap_ts if last else 0.0
        except Exception:
            # review #9: a torn last line (recorder mid-append) is transient — keep the
            # previous GOOD table while ITS snapshot is still inside the staleness bound,
            # so est-floor-admitted markets don't flap on one bad read.
            if now_ts - _EST_FEED_CACHE.get("snap_ts", 0.0) > EST_FEED_MAX_AGE_S:
                _EST_FEED_CACHE["table"] = {}
        _EST_FEED_CACHE["ts"] = now_ts
    return _EST_FEED_CACHE["table"]


def _d3_feedback_cached(now_ts, max_age_s=60.0):
    """The W7 clamp's evidence table, re-read at most once a minute. Independent of the D2
    flag (the clamp needs it even when the rank multiplier is off). Fail-open {} — which for
    THIS consumer means the new-series clamp binds (see the D3 block comment: a risk limiter
    fails toward smaller size, deliberately)."""
    if now_ts - _D3_FB_CACHE["ts"] > max_age_s:
        try:
            import kalshi_capital_rank
            _D3_FB_CACHE["table"] = kalshi_capital_rank.load_credit_feedback(
                CREDIT_FEEDBACK_PATH)
        except Exception:
            _D3_FB_CACHE["table"] = {}
        _D3_FB_CACHE["ts"] = now_ts
    return _D3_FB_CACHE["table"]


def _caprank_variants():
    """The knob-sets the shadow is scored under EVERY cycle — so risk-aversion settings are
    chosen from logged evidence, not blind (operator 2026-07-29: 'can we shadow on multiple
    settings, thats a massive change blind'). The 'env' variant is whatever the three
    KALSHI_CAPRANK_* knobs say (defaults 1.0 = the un-leaned score); 'lean'/'averse' are fixed
    comparison points. KALSHI_CAPRANK_VARIANTS (a JSON list of {name, risk_lambda,
    prospective_haircut, unknown_haircut}) REPLACES the two fixed ones; a malformed value
    fails open to the defaults. The env variant is always first and always present."""
    variants = [{"name": "env", "risk_lambda": CAPRANK_RISK_LAMBDA,
                 "prospective_haircut": CAPRANK_PROSPECTIVE_HAIRCUT,
                 "unknown_haircut": CAPRANK_UNKNOWN_HAIRCUT}]
    extra = [{"name": "lean", "risk_lambda": 2.0,
              "prospective_haircut": 0.8, "unknown_haircut": 0.5},
             {"name": "averse", "risk_lambda": 3.0,
              "prospective_haircut": 0.6, "unknown_haircut": 0.25}]
    raw = os.environ.get("KALSHI_CAPRANK_VARIANTS")
    if raw:
        try:
            parsed = json.loads(raw)
            cleaned = []
            for v in parsed if isinstance(parsed, list) else []:
                cleaned.append({"name": str(v["name"]),
                                "risk_lambda": float(v["risk_lambda"]),
                                "prospective_haircut": float(v["prospective_haircut"]),
                                "unknown_haircut": float(v["unknown_haircut"])})
            if cleaned:
                extra = cleaned
        except Exception:
            pass                               # malformed env -> the fixed defaults
    return variants + extra


def _caprank_telemetry(rows, picked, now):
    """SHADOW ranking log — observation only, and structurally unable to alter selection: it
    receives the already-final `picked`, returns nothing, and is wrapped so a telemetry fault
    (bad row, full disk) can never break a live trading cycle (same contract as MKT_TELEMETRY)."""
    if not CAPRANK_TELEMETRY or not rows:
        return
    try:
        import kalshi_capital_rank as _kcr
        costs = _load_fill_costs()           # read ONCE per cycle, shared by every variant
        prospective = _load_prospective()
        fb = _load_credit_feedback()         # same once-per-cycle contract (review F6)
        actual = [r["ticker"] for r in picked[:FOOTPRINT_TOP]]
        row = {"ts": now.isoformat(), "actual": actual, "variants": []}
        for v in _caprank_variants():
            with SCORES_LOCK:
                shadow = _kcr.shadow_rank(rows, SCORES, costs, MAX_MARKET_CAPITAL,
                                          INV_HARD_CT, now.timestamp(), calib=CAPRANK_CALIB,
                                          swing_penalty=SCORE_SWING_PENALTY,
                                          unknown_bonus=SCORE_UNKNOWN_BONUS,
                                          prospective=prospective,
                                          risk_lambda=v["risk_lambda"],
                                          prospective_haircut=v["prospective_haircut"],
                                          unknown_haircut=v["unknown_haircut"],
                                          credit_feedback=fb,
                                          d2_bonus=D2_BONUS,
                                          d2_neverpaid_mult=D2_NEVERPAID_MULT)
            top = shadow[:FOOTPRINT_TOP]
            shadow_t = [d["ticker"] for d in top]
            row["variants"].append(
                {"name": v["name"],
                 "params": {k: v[k] for k in
                            ("risk_lambda", "prospective_haircut", "unknown_haircut")},
                 "shadow": shadow_t,
                 "overlap": len(set(actual) & set(shadow_t)),
                 "would_enter": [t for t in shadow_t if t not in actual],
                 "would_exit": [t for t in actual if t not in shadow_t],
                 "components": top})
        with open(os.path.join(DATA_DIR, f"caprank-{now.strftime('%Y%m%d')}.jsonl"), "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        _SILENT["caprank_fail"] += 1     # surfaced via plan["silent_failures"], never raises

# --- PER-MARKET LOSS GOVERNORS (operator directive 2026-07-29: "dont ban markets as fixes,
# fix what caused the issue") — both LOGIC fixes, market-agnostic, both default OFF (no-op).
# The 07-29 loss shape they close (KXMUSKNW, -$11.02 of the day per session_econ 18:26Z; same
# shape as the pre-exclusion index bleed): join a trending book at the touch -> get filled on
# the wrong side -> strand-cross out at a loss -> RE-JOIN the same book minutes later -> repeat.
# Nothing bounded the per-market bleed and nothing stopped the re-entry. A series ban treats one
# symptom by name; these govern the BEHAVIOR for every market.
#   MKT_DAY_LOSS_EXITONLY_USD  a market whose REALIZED loss today (venue receipt:
#       realized_pnl_dollars delta vs the day-start snapshot) reaches this goes EXIT-ONLY for
#       the rest of the UTC day: unwind quotes survive, accumulating quotes are stripped and
#       the diff cancels any resting ones. Receipt-based (churn/mark immune, same doctrine as
#       the cost ratchet). Trip latches for the day even if the row vanishes on full flat.
#   REENTRY_COOLDOWN_S  after a strand taker-cross on a ticker (we PAID to leave), the ticker
#       is exit-only for this many seconds — a book that just ran us over must not be rejoined
#       one cycle later. Persisted in quoter_state so a restart cannot amnesty it.
MKT_DAY_LOSS_EXITONLY_USD = _envf("KALSHI_MKT_DAY_LOSS_EXITONLY_USD", 0.0)   # 0 = OFF
# TIERED LOSS LADDER (operator-named 2026-07-31 "do 3$ then 5$ then out", superseding the
# same-day one-strike-out): a market whose realized day-loss reaches the EXITONLY threshold
# (live $3) goes exit-only for the day and records a strike; any day its realized day-loss
# reaches MKT_OUT_LOSS_USD (live $5) it is OUT -- permanent, prune-exempt, persisted in
# quoter_state['mkt_out']; only an operator clearing the entry (or market close) ends it.
# Markets banned under the earlier one-strike rule were grandfathered into mkt_out at the
# first cycle this shipped. Count-based strike bans are now OFF by default (STRIKES_OUT=0);
# the knob remains for the operator's 2026-08-03 re-review.
MKT_OUT_LOSS_USD = _envf("KALSHI_MKT_OUT_LOSS_USD", 5.0)
# UNWIND ALLOWANCE (defect 4 root fix, operator-approved 2026-08-02). Rung 2 used to judge the
# FROZEN loss at the moment of the $3 trip, so a market that kept bleeding after tripping could
# never reach the $5 rung that day — live 2026-08-02 KXRAIN-26AUG02-CHI froze at -$1.28 and
# finished at -$7.29, never banned. Rung 2 now judges the LIVE day-delta, which alone would
# punish correct behaviour: once tripped the market is exit-only, so the spread we pay to LEAVE
# would itself walk every tripped market to the $5 rung. The allowance refunds that liquidation
# COST and nothing else: a market that genuinely loses $5 today goes out even if the last
# dollars arrived on the way out; it only gets credit for the spread it paid to exit.
# $0.04/contract is the measured half-spread ($3.02 over 123 ct = $0.0246 on the 08-02 loss
# sample, INFERRED from the session doc, not a canonical script) plus ~60% margin. It is capped
# at the DERIVED rung gap (MKT_OUT_LOSS_USD - MKT_DAY_LOSS_EXITONLY_USD = $2.00 live) so the
# allowance can never span more than one rung, and at INV_HARD_CT=50 the cap binds exactly.
# Set to 0 for a pure live-delta rung 2 (which would repeal the M3 directive for markets
# tripping between -$4 and -$5, where one unwind's spread can carry them over).
MKT_UNWIND_ALLOW_PER_CT = _envf("KALSHI_MKT_UNWIND_ALLOW_PER_CT", 0.04)
# TAKER-CROSS BEHAVIORAL GOVERNOR (operator-named 2026-07-31 "A do it and go live with it"):
# >= TAKER_GOV_CROSSES paid taker exits on a ticker in one day AND realized day-loss <=
# -TAKER_GOV_LOSS_USD -> exit-only for the day + strike. Encodes the measured era fingerprint
# (repeatedly PAYING to leave = the toxicity receipt: -$176.01 of -$182.06 era realized on
# taker legs; this compound trip would have saved $98-122 of that era, COMPUTED). Counter is
# incremented at the single point every paid exit passes (_taker_cross_capped), mirrored in
# quoter_state['mkt_taker_xn'], reset at the UTC day roll. TAKER_GOV_CROSSES=0 disables.
TAKER_GOV_CROSSES = _envi("KALSHI_TAKER_GOV_CROSSES", 3)
TAKER_GOV_LOSS_USD = _envf("KALSHI_TAKER_GOV_LOSS_USD", 1.0)
_TAKER_XN = {}                               # ticker -> paid-exit count today (st mirror)
_REALIZED_LAST_GOOD = {}                     # last successful all-traded realized snapshot
def _ban_set(vals):
    """Persisted ban/trip collections must be str tickers, but quoter_state.json and
    mkt_out_backup.json are operator-editable JSON and nothing enforced the type at the
    load boundary — a single non-string entry (hand-edit, bare JSON int) made the
    governor's sorted() raise a mixed-type TypeError, failing the WHOLE book closed via
    gov_fail_streak (root-caused 2026-08-02, operator-named root fix). Coerce LOUDLY and
    never drop: a mangled entry stays banned under its string form — protective state is
    never silently discarded."""
    out = set()
    for v in (vals or ()):
        if not isinstance(v, str):
            _SILENT["ban_entry_coerced"] += 1
            v = str(v)
        out.add(v)
    return out


def _mkt_out_backup_union(current):
    """Amnesty guard (re-review, bleed-F9 class): permanent bans ALSO live in a tiny separate
    file, unioned on every governor pass — losing/corrupting quoter_state.json can no longer
    silently re-admit permanently-OUT markets. Writes only on change; read+union is cheap.
    Failure never blocks trading (returns what it knows)."""
    path = os.path.join(DATA_DIR, "mkt_out_backup.json")   # call-time: DATA_DIR is
    known = set()                                           # test-patched per harness
    try:
        if os.path.exists(path):
            known = _ban_set(json.load(open(path)) or [])
    except Exception:
        _SILENT["mkt_out_backup_read_fail"] += 1
    merged = known | _ban_set(current)
    if merged != known:
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(sorted(merged), fh)
            os.replace(tmp, path)
        except Exception:
            _SILENT["mkt_out_backup_write_fail"] += 1
    return merged
# STRIKE LADDER (operator-named 2026-07-31, tightened same day: "one strike your out for
# anything costing over 5 dollars until 8-3 rereview"). A market that trips the $/day governor
# STRIKES_OUT times is OUT: banned with NO expiry, EXEMPT from memory pruning — only an
# operator clearing quoter_state's mkt_strike_hist entry (or the market's own close) ends it.
# STRIKES_OUT=0 IS THE LIVE SETTING — count-based bans are OFF. CORRECTED 2026-08-03 on
# operator ruling ("resolve to match reality and keep it that way"). This comment previously
# read "STRIKES_OUT=1 (the live setting) means one trip = permanent ban", which contradicted
# the code two lines below (default 0) and the box (KALSHI_STRIKES_OUT is unset in live.env,
# verified 2026-08-03), i.e. it described a ban mechanism that has not been armed. Every one of
# the 9 permanent bans on record was minted by the $5 rung or by the one-time grandfather, NOT
# by a strike count. Strikes still ACCRUE (mkt_strike_hist: 24 tickers, 21 at one strike and 3
# at two, read 2026-08-03T12:48:49Z) — they are a record, not a trigger, unless this knob is
# raised. The knob exists so the operator's 2026-08-03 policy re-review can retune without a
# code deploy. Strike history persists in quoter_state (per-market trip dates; entries BELOW
# the OUT threshold are pruned at TWO_STRIKES_MEMORY_D so the file stays bounded). Rides the
# same governor: inert unless MKT_DAY_LOSS_EXITONLY_USD > 0. Born 2026-07-31 00:00-02:32Z: the
# midnight reset re-admitted all five of yesterday's tripped markets and they burned another
# ~$10.6 in 2.5h (fills API); tightened to 1 the same day after MLABELSHARE (-25.76 venue
# realized) showed a single day can cost 5x the threshold.
TWO_STRIKES = _envi("KALSHI_TWO_STRIKES", 1)
TWO_STRIKES_MEMORY_D = _envi("KALSHI_TWO_STRIKES_MEMORY_D", 14)
STRIKES_OUT = _envi("KALSHI_STRIKES_OUT", 0)   # 0 = count-based bans OFF (E ladder rules)

# INCUMBENT-ONLY GATE (operator directive 2026-08-01: "just stop doing new markets and keep
# all else equal"; option c — a real gate — operator-named same day). ON: the set of markets
# with a resting order or held position is captured ONCE, persisted to state
# (incumbent_only_set), and every OTHER market keeps only its unwind quotes — accumulating
# quotes are stripped at the same choke point the loss governor uses, so selection, scoring,
# sizing, and exits are all byte-identical ("keep all else equal"). Held inventory always
# unwinds; new markets simply never open. Flipping OFF clears the captured set, so the next
# enable re-captures fresh. HOT-RELOADABLE (in _refresh_safety_knobs' watch list) — the
# directive's original blocker was that no selection knob could hot-apply.
INCUMBENT_ONLY = _envi("KALSHI_INCUMBENT_ONLY", 0)

# OPTION C — SELECT-TO-BUDGET (operator-named 2026-08-02, study
# D1_D6_CAPITAL_COUPLING_STUDY_2026-08-02.md). ON: the footprint is walked in allocation-
# priority order accumulating each market's estimated two-sided commit
# (kalshi_capital_rank.est_commit_usd — byte-aligned with _capped_join sizing) and capped
# near _total_cap(), with a per-family budget = _series_cap() (the D6 multiplicity bound).
# Held markets are never gated (de-risk first, and their capital is already spent);
# explore probes are charged probe cost, not join cost. cap_desired stays as an unchanged
# backstop that should now almost never fire — when it does, budget_backstop_fired alarms.
# MARGIN covers the commit model's measured ~30% under-read (study §3) — tune from the
# alarm counter. Default 0 = byte-identical (test-pinned). Both knobs hot-reloadable.
# DEPENDENCY: est refs come from the SCORE_RANK cache — with SCORE_RANK=0 every ref is
# unknown and est pins at the 0.50/0.50 maximum (conservative: footprint ~limit/max_est
# markets). Run this with SCORE_RANK=1 (live config) for honest sizing.
SELECT_BUDGET = _envi("KALSHI_SELECT_BUDGET", 0)
SELECT_BUDGET_MARGIN = _envf("KALSHI_SELECT_BUDGET_MARGIN", 0.3)

# ANY-LOSS COOLDOWN SHADOW — WATCH-ONLY (operator-named 2026-08-01, option A: "A review next
# handoff though. we can handle drips and we make money on drips").
# GAP MEASURED: the re-entry cooldown is fed ONLY by taker-cross exits (":549", the three
# cross sites); the day-loss ladder starts at MKT_DAY_LOSS_EXITONLY_USD (live $3). A market
# that bleeds UNDER the rung without a cross exit gets no brake and can be re-joined the next
# cycle. Measured 2026-08-01T00:33:59Z (scope: the 31 markets in the venue positions feed):
# sub-$3 band = 22 markets / $22.70; the 4 markets over $5 carry $68.47 and already trip.
# WHY WATCH-ONLY: benching a market removes the book presence that earns the reward, and the
# operator's standing framing is that drips are acceptable and reward-positive. This exists to
# PRICE that trade-off (how many markets would be benched, for how long, and how much they
# actually bled) — never to act on it. Enabling requires operator naming plus receipts showing
# the benched markets were reward-negative.
# INVARIANT: writes ONLY st["anyloss_shadow"] and plan["anyloss_sh_*"] telemetry. It must never
# touch reentry_cool, _exit_only_mkts, mkt_out, or any trip set. Faults are swallowed by the
# caller's nested guard so a shadow bug cannot fault the real governor.
ANYLOSS_SHADOW_FLOORS = (0.25, 0.5, 1.0, 2.0)
ANYLOSS_SHADOW_MAX_TICKERS = 200          # per floor per day — keeps quoter_state bounded
# Cap on the departed-ticker realized carry (defect 5a). Same bounding doctrine as the shadow
# above: quoter_state must stay small. Only TRADED tickers ever enter the governor feed, so
# real usage is tens of floats; on overflow the MOST NEGATIVE are kept, because those are the
# only ones that can still trip a rung, and the truncation is counted rather than silent.
REALIZED_CARRY_MAX = 500
# Last emitted env_absent signature, so the (near-constant, ~44%-of-row) list is written only
# when it CHANGES. Module-level: it resets on restart, which is exactly right — a fresh process
# must state the full list once before any later row can mean "unchanged".
_ENV_ABSENT_SIG = [None]

# --- ALWAYS-EMIT COUNTERS (defect 9, Phase A3). Every name below is written ONLY as an
# accumulation — `plan[k] = plan.get(k, 0) + 1` — and never plainly assigned, so a cycle in
# which the thing never happened emitted NO KEY AT ALL. Absence then reads identically to
# "never evaluated", which is the ambiguity that let a protection look fine while it was
# structurally unable to fire. Seeding them to 0 at the top of every cycle makes absence mean
# "not evaluated" and 0 mean "evaluated, did not fire" — two different facts that were one.
# DERIVATION, stated with its limits (corrected 2026-08-03 after blind review). The list was
# built by a TEXTUAL search for `plan["k"] = plan.get("k"...` with no plain assignment. That
# rule has two blind spots, and the first pass missed SIX keys because of them:
#   * a `round(plan.get(k, 0.0) + x, n)` wrapper — the accumulation is not at the head of the
#     RHS (exit_cross_cost_usd, preclose_naked_ct, preclose_taker_ct, strand_crossed_ct);
#   * a statement split across lines, so a per-line scan sees only `= round(`
#     (settle_topup_usd, exit_ladder_would_pay_usd).
# All six are now included. Anyone re-deriving this list must match accumulation ANYWHERE in
# the RHS and join continuation lines first — a per-line prefix match silently under-reports.
# The magnitudes (…_usd, …_ct) are seeded on the same reasoning as the counts: a missing
# magnitude is as ambiguous as a missing count.
# SAFETY: seeding cannot change behaviour. Every in-module consumer reads these with
# `.get(k, 0)` or truthiness, so a falsy 0 is byte-identical to absence at every read site;
# the same holds for the hourly roll-up (kalshi_plans_hourly.py), which coerces with
# `.get(f, 0) or 0`.
# WHAT THE 0 DOES AND DOES NOT MEAN (corrected 2026-08-03): seeding happens at plan
# construction, BEFORE the pipeline runs, and run_once has early `return 0` paths (standing-read
# blackout, reconcile failure, positions-read failure) whose rows the `finally` still writes —
# with all of these at 0. So a 0 does NOT prove the stage was evaluated. What the change buys is
# a LOSSLESS RELABEL: pre-seeding, absence already conflated "did not fire" with "never ran",
# and every write site is `+ 1` or a guarded assignment, so no consumer can distinguish less
# than before. The discriminator lives in the same row and is deliberately NOT seeded —
# standing_read_failed / reconcile_fail / positions_read_failed are plainly assigned and are
# absent on a healthy row.
_ALWAYS_EMIT_COUNTERS = (
    "create_ratchet_skipped", "drop_far_market_close", "exit_cross_unpriced",
    "exit_ladder_stepped", "exit_sweep_veto", "exit_trend_cross", "farclose_check_failed",
    "ladder_cross", "ladder_cross_gated", "ladder_violation", "mkt_out_rung2",
    "preclose_check_failed", "preclose_taker_failed", "series_probe", "settle_check_failed",
    "settle_topups", "strand_cross_failed", "strand_crossed_book", "strand_no_exit_side",
    "strand_read_failed", "strand_unpriceable", "taker_gov_tripped", "trip_inv_missing",
    # the six the first derivation missed (round()-wrapped or line-split accumulations)
    "settle_topup_usd", "exit_ladder_would_pay_usd", "exit_cross_cost_usd",
    "preclose_naked_ct", "preclose_taker_ct", "strand_crossed_ct",
    # C1 live inventory meter: these are plainly assigned inside the mark block, so they are
    # absent whenever the balance read fails or marking raises. Seeding them keeps the A3
    # contract — a missing gauge means "did not run", never "measured flat".
    "mkt_unreal_n", "mkt_unreal_usd", "mkt_unreal_neg_usd", "mkt_unreal_worst_usd",
    "mkt_unreal_measured",
    # C2 pairedness split + C3 net-EV table alarm: plainly assigned inside guarded blocks, so
    # absent whenever that block did not run. Seeded for the same A3 reason.
    "inv_gross_ct", "inv_naked_ct", "inv_paired_ct", "inv_gross_usd", "inv_naked_usd",
    "inv_paired_usd", "inv_paired_frac", "inv_pairedness_measured",
    # NOTE: the netev_* alarm keys are deliberately NOT seeded — flag-off must emit zero
    # net-EV telemetry (a pinned provable-no-op property of that subsystem), and seeding would
    # write them on every flag-off cycle. See _netev_table_alarm.
)
# Footprint DROP REASONS. FP_DROPS is merged into the row at the end of selection, so a reason
# that never fired was likewise absent — indistinguishable from a selection stage that never
# ran (the exact failure mode the FP_DROPS block itself was built to end).
_ALWAYS_EMIT_DROPS = (
    "close_unchecked_tail", "drop_allowlist", "drop_date_parse", "drop_far_close",
    "drop_far_market_close_sel", "drop_high_activity", "drop_late_life", "drop_not_liquidity",
    "drop_not_selected", "drop_null_fields", "drop_series_capped", "drop_series_deny",
)


def _anyloss_shadow(st, realized, base, tripped, out, cooling, now, day, plan):
    """Pure shadow bookkeeping for the any-loss cooldown. Returns nothing; mutates only
    st["anyloss_shadow"] and plan telemetry keys. `cooling` is the REAL reentry_cool key set.

    Per floor, a ticker whose realized day-delta is <= -floor and that is not already inside
    a shadow cooldown "trips": it is counted NEW if no existing brake (day-latch, permanent
    OUT, or real cross-fed cooldown) already covers it, REDUNDANT otherwise. Only NEW trips
    represent behaviour this feature would add.
    """
    sh = st.get("anyloss_shadow") or {}
    if sh.get("day") != day:
        sh = {"day": day, "floors": {}}       # shadow counters are per-UTC-day, like the rungs
    floors = sh.get("floors") or {}
    cool_s = REENTRY_COOLDOWN_S if REENTRY_COOLDOWN_S > 0 else 3600.0
    for _f in ANYLOSS_SHADOW_FLOORS:
        fkey = "%.2f" % _f
        rec = floors.get(fkey) or {"active": {}, "trips": 0, "new": 0, "redundant": 0,
                                   "bled_usd": 0.0, "tickers": []}
        _still = {}
        for _t, _exp in (rec.get("active") or {}).items():
            try:
                if parse_iso(_exp) > now:
                    _still[_t] = _exp          # unparseable stamps simply expire: shadow only
            except Exception:
                pass
        for _t, _v in realized.items():
            _d = _v - base.get(_t, _v)         # same day-delta basis as the real governor
            if _d > -_f or _t in _still:
                continue
            rec["trips"] = int(rec.get("trips", 0)) + 1
            if _t in tripped or _t in out or _t in cooling:
                rec["redundant"] = int(rec.get("redundant", 0)) + 1
            else:
                rec["new"] = int(rec.get("new", 0)) + 1
                # blind review 2026-08-01 (lens A #3): _d is CUMULATIVE-since-baseline, so a
                # re-trip after cooldown expiry must add only the increment since the last
                # counted trip — adding _d again double-counted bleed (overstated, the exact
                # bias that would flatter enabling the feature). counted{} is per-day like
                # the rest of rec and shares the ticker cap.
                _cnt = rec.setdefault("counted", {})
                _prev = float(_cnt.get(_t, 0.0))
                if _t in _cnt or len(_cnt) < ANYLOSS_SHADOW_MAX_TICKERS:
                    _cnt[_t] = _d
                rec["bled_usd"] = round(float(rec.get("bled_usd", 0.0)) + (_d - _prev), 4)
                if (_t not in rec["tickers"]
                        and len(rec["tickers"]) < ANYLOSS_SHADOW_MAX_TICKERS):
                    rec["tickers"].append(_t)
            _still[_t] = (now + timedelta(seconds=cool_s)).isoformat()
        rec["active"] = _still
        floors[fkey] = rec
        # active = markets this floor would have benched RIGHT NOW (the presence cost);
        # new = cumulative trips today that no existing brake already covered.
        plan["anyloss_sh_%s_active" % fkey] = len(_still)
        plan["anyloss_sh_%s_new" % fkey] = int(rec.get("new", 0))
        plan["anyloss_sh_%s_redundant" % fkey] = int(rec.get("redundant", 0))
        plan["anyloss_sh_%s_bled" % fkey] = round(float(rec.get("bled_usd", 0.0)), 2)
    sh["floors"] = floors
    st["anyloss_shadow"] = sh


def _two_strikes(hist, tripped_today, day, now):
    """Pure strike bookkeeping. hist: {ticker: [iso dates]}; returns (hist, banned_set).
    Strike ladder (operator-named 2026-07-31, tightened same day to "one strike your out
    ... until 8-3 rereview" — STRIKES_OUT is the knob for that re-review):
      >= STRIKES_OUT strikes -> OUT: banned with NO expiry and EXEMPT from memory pruning —
                   only an operator clearing quoter_state's mkt_strike_hist entry (or the
                   market's own close) ends it.
      below the threshold    -> no ban here; entries prune at TWO_STRIKES_MEMORY_D. The
                   same-day trip is still exit-only via the caller's day-latch regardless.
    LIVE STATE, corrected 2026-08-03 on operator ruling: STRIKES_OUT is 0 (unset in live.env,
    code default 0), so the `>= STRIKES_OUT` branch is GATED OFF at the call site and this
    function bans nothing today — it is pure bookkeeping. The prior wording ("at STRIKES_OUT=1
    this branch only ever holds markets from a former multi-strike era") described an armed
    mechanism and was wrong."""
    for t in tripped_today:
        dl = hist.setdefault(t, [])
        if day not in dl:
            dl.append(day)
    cut = (now - timedelta(days=TWO_STRIKES_MEMORY_D)).strftime("%Y-%m-%d")
    banned = set()
    for t in list(hist):
        if STRIKES_OUT > 0 and len(hist[t]) >= STRIKES_OUT:
            banned.add(t)                   # OUT — no prune, no expiry
            continue
        hist[t] = [d for d in hist[t] if d >= cut]
        if not hist[t]:
            hist.pop(t)
    return hist, banned
REENTRY_COOLDOWN_S = _envf("KALSHI_REENTRY_COOLDOWN_S", 0.0)                 # 0 = OFF
# STOP-flatten pacing (self-audit A2-F4, 2026-07-29): while the STOP sentinel is present, every
# heartbeat re-ran the FULL flatten — cancel, re-offset, sleep, and a fresh tries=4 taker burst
# with a fresh slippage anchor — indefinitely, on any residual that would not fill (the C5 review
# named this "a metronomic taker fire-sale" and fixed only the order-id collision). The first
# invocation still flattens IMMEDIATELY; repeats are spaced at least this far apart (the maker
# offsets it rested stay working the whole time). 0 = legacy every-heartbeat behavior.
STOPFLAT_REPEAT_S = _envf("KALSHI_STOPFLAT_REPEAT_S", 1800.0)
# HALT CONFIRMATION (operator-named 2026-07-29 after the 18:18:29Z halt): the final $6.6 of
# that trigger arrived in the 90s after a 60-ct fill on a thin book — a mark blip supplied the
# push over the arm, and the halt then crystallized it by selling into the same thin book. The
# breach must now HOLD for N consecutive cycles (~15-30s at daemon cadence) before the STOP is
# written: a one-tick paper mark cannot shut the book, a real crash still halts in <30s. The
# streak resets the moment equity recovers inside the arm. 1 = legacy fire-on-first-breach.
HALT_CONFIRM_N = _envi("KALSHI_HALT_CONFIRM_N", 3)
# stamp path is computed AT USE (not import): freezing it at import is the exact F17
# import-once class this same audit flagged — and it broke the test harness, which
# redirects DATA_DIR per test. Sidecar file because the STOP branch runs pre-state.

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
_PRESENCE_MTIME = [0.0]


def _presence_table_refresh():
    """Reload the presence table when its file changes (audit item: import-once staleness with
    the gate ON live — the gate was judging today's markets with the table frozen at process
    start). mtime-gated: zero I/O beyond a stat when unchanged; a vanished/broken file KEEPS
    the last good table (fail-open to stale beats fail-closed to empty, which would zero the
    gate's floor)."""
    global PRESENCE_TABLE
    if not PRESENCE_GATE:
        return
    try:
        m = os.path.getmtime(PRESENCE_TABLE_PATH)
    except OSError:
        return
    if m != _PRESENCE_MTIME[0]:      # != not >: a backup-restored file has an OLDER mtime
        t = _load_presence_table()
        if t:
            PRESENCE_TABLE = t
        else:
            _SILENT["presence_table_stale_kept"] += 1   # blind-review: stale-keep must count
        _PRESENCE_MTIME[0] = m


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


def _expected_credit_usd(m, yl, nl, best_y, best_n, target, now, own_orders=None):
    """Credit we can still earn HERE over the REMAINING PROGRAM PERIOD, in dollars.

    F1 (reward audit 2026-08-06): the payout unit was min(ONE DAY, remaining window) — a
    deliberate conservative reading taken while the venue never stated the floor's unit. That
    ambiguity is now RESOLVED, in both directions of evidence:
      * help article 13823851 (updated 2026-08-05): score and reward are computed over the TIME
        PERIOD ("Time periods: Up to 31 days each"), and the where-to-find article states "a
        final reward below $1 for an individual program is not paid" — per PROGRAM PERIOD.
      * receipt: KXSENATEADJOURN-27 was credited as ONE $6.44 lump on 2026-08-02 for its
        multi-day program period (market still open to 08-15) — one payout per period, exactly
        the discriminating observation the old docstring said would settle this.
    So a multi-day program accruing $0.50/day for 5 remaining days is a $2.50 period credit and
    MUST clear a $1 floor; the per-day unit refused exactly those payers (the audit's
    spread-thin-earn-zero mechanism).

    F3 (same audit): pc models the FULL _capped_join size, but the D3 ramp/new-series clamp then
    rests 5-10ct — the gate admitted markets whose CLAMPED size cannot clear the floor. When the
    ramp is armed, scale the expectation by (ramp ct / modeled join ct) — share is ~linear in our
    size while our share is small, which is precisely the regime where the clamp binds.
    Uses _d3_est_ct (side-effect-free; never starts a ramp clock).

    Returns (expected_usd, expected_usd_at_perfect_execution, frac_left) — both dollar figures
    are PER REMAINING PROGRAM PERIOD."""
    pc = _prospective_capture(m, yl, nl, best_y, best_n, target,
                              own_orders=own_orders)               # $/day, instantaneous
    frac = _window_frac_left(m, now)
    days_left = (float(m.get("life_min") or 0.0) / 1440.0) * frac
    payout_days = days_left                          # F1: the period IS the unit (see above)
    if D3_RAMP:
        join_ct = max(_capped_join(best_y, best_n), _capped_join(best_n, best_y), 0.0)
        eff_ct = float(_d3_est_ct(m.get("ticker") or "", now.timestamp()))
        if (EXPLORE_PROBE_CT > 0 and m.get("explore") and SERIES_ALLOW
                and (m.get("ticker") or "").split("-")[0] not in SERIES_ALLOW):
            # probe clamp (allowlist exempt, A1). EXPLORE_PROBE_CT=0 means NO clamp — same
            # guard as the actual sizing clamp and the budget-walk mirror (blind review #2:
            # without it, ct=0 default zeroed every explore market's expected credit).
            eff_ct = min(eff_ct, float(EXPLORE_PROBE_CT))
        if join_ct > 0 and eff_ct < join_ct:
            pc *= eff_ct / join_ct                   # F3: gate at the size that will actually rest
    ideal = pc * payout_days
    expected = ideal * _presence_factor(m.get("ticker"), m.get("life_min"))
    if EST_FEED and frac >= EST_FEED_MIN_FRAC:
        # D-A: the venue's own per-program estimate floors the expectation (never lowers it);
        # late-window entries fall back to the pure model (see EST_FEED_MIN_FRAC).
        _est = _est_feed_cached(now.timestamp()).get(m.get("ticker"))
        if _est is not None and _est > 0.0:
            ideal = max(ideal, _est)
            expected = max(expected, _est)
    return expected, ideal, frac
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
    """Load the calibration table from disk (fail-OPEN to {} -> every family unproven, never blocks).

    ⚠ THE FAIL-OPEN IS THE DEFECT-7 TRAP. Returning {} does not disable the gate — it makes
    every family "unproven", which routes them to the MODEL fallback and the conservative
    fingerprint cost. The gate then still SKIPS markets, on a table that does not exist, and
    says nothing: live 2026-07-31/08-01 it skipped 640 of 2,195 evaluations including 71 for a
    family its own (undeployed) table rates net-POSITIVE. Silence is the whole bug — the
    caller must alarm when the gate is armed and the table is empty. See _netev_table_alarm."""
    try:
        import kalshi_netev_calibrate
        return kalshi_netev_calibrate.load_table(NETEV_TABLE_PATH)
    except Exception:
        return {}


def _netev_table_alarm(plan):
    """ALARM on every plan row when the net-EV gate is ARMED but its table is EMPTY (defect 7).

    SCOPE, deliberately narrower than the A3 always-emit doctrine: when the gate is OFF this
    writes NOTHING. Flag-off being a PROVABLE NO-OP — no file IO, no behaviour change, and no
    telemetry — is an explicit design property of this subsystem, pinned by
    test_netev_gate.test_flag_off_cycle_emits_no_netev_telemetry. Seeding these keys would emit
    them on every flag-off cycle and break that proof, so the invariant wins over the doctrine
    here; absence of netev_gate already means "gate off" unambiguously, and that is exactly
    what the pin asserts.
    When the gate IS armed, all three keys are written every cycle, so
    netev_table_families == 0 means "checked, empty" rather than "nobody looked"."""
    if not NETEV_GATE:
        return
    plan["netev_gate"] = 1
    # NETEV_TABLE IS the {family: row} map, NOT the whole document: _load_netev_table returns
    # kalshi_netev_calibrate.load_table, which already unwraps data["families"] (:229-238), and
    # the gate consults it as NETEV_TABLE.get(fam). A first version of this alarm counted
    # NETEV_TABLE["families"] and would therefore have reported 0 families and raised the EMPTY
    # alarm on EVERY armed cycle — a false alarm on a populated table. Caught by reading the
    # loader contract; the pin below now uses the real shape.
    plan["netev_table_families"] = len(NETEV_TABLE or {})
    plan["netev_table_empty"] = 0
    if not plan["netev_table_families"]:
        plan["netev_table_empty"] = 1
        print(f"WARNING NET-EV GATE ARMED WITH AN EMPTY TABLE ({NETEV_TABLE_PATH}) — every "
              f"family reads as UNPROVEN, so the gate is skipping markets on the MODEL "
              f"fallback with no receipts behind it. This is silent by construction; it is "
              f"being said out loud instead.")


# loaded ONCE at import, and ONLY when the flag is on (flag-off does zero file IO -> provable no-op)
NETEV_TABLE = _load_netev_table() if NETEV_GATE else {}
_NETEV_MTIME = [0.0]


def _netev_table_refresh():
    """Reload the net-EV table when its file changes (audit item: import-once staleness, same
    fix as the presence table 2026-07-30). Gate OFF => never runs; empty/broken load keeps the
    last good table."""
    global NETEV_TABLE
    if not NETEV_GATE:
        return
    try:
        m = os.path.getmtime(NETEV_TABLE_PATH)
    except OSError:
        return
    if m != _NETEV_MTIME[0]:         # != not >: a backup-restored file has an OLDER mtime
        t = _load_netev_table()
        if t:
            NETEV_TABLE = t
        else:
            _SILENT["netev_table_stale_kept"] += 1
        _NETEV_MTIME[0] = m
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
# F17 / D-G (reward audit 2026-08-06, operator-ruled): the ABSOLUTE 45-min wind-down forfeited
# ~78% of a sub-hour program window (58-min hourly temp: enterable ~13 min, quoted-down 45) —
# C13 fixed exactly this over-coverage for the ramp (RAMP_LIFE_FRAC) but not here. The wind-down
# is now proportional for short windows: min(WIND_DOWN_MIN, WIND_DOWN_FRAC x window), floored at
# WIND_DOWN_MIN_FLOOR so we always stop entering before the very end. Long programs unchanged.
WIND_DOWN_FRAC = _envf("KALSHI_WIND_DOWN_FRAC", 0.2)
WIND_DOWN_MIN_FLOOR = _envf("KALSHI_WIND_DOWN_MIN_FLOOR", 3.0)


def _effective_wind_down_min(life_min):
    """Effective wind-down minutes for a program whose window is `life_min` minutes.

    Floored at SETTLE_UNWIND_MIN (blind review #3): resting ACCUMULATING quotes inside the
    armed settle-taker window breaks the C8 maker-first/taker-second ordering — a fill at
    T-29min would be taker-crossed immediately, re-rested two-sided, and refilled (the exact
    quote->fill->taker churn C8 prevents). So the wind-down can shrink proportionally for
    short windows, but never below the taker window; markets shorter than ~SETTLE_UNWIND_MIN
    stay effectively unenterable, as before this fix. Scaling the taker window itself for
    short programs is future work, noted, NOT built."""
    try:
        life = float(life_min or 0.0)
    except (TypeError, ValueError):
        life = 0.0
    if life <= 0:
        return float(WIND_DOWN_MIN)                 # unknown window -> legacy absolute
    floor = max(WIND_DOWN_MIN_FLOOR, float(SETTLE_UNWIND_MIN))
    return max(floor, min(float(WIND_DOWN_MIN), WIND_DOWN_FRAC * life))
WRITE_BUDGET_PER_CYCLE = _envi("KALSHI_WRITE_BUDGET", 400)  # order-ops ceiling/cycle
JOIN_ALWAYS = _envb("KALSHI_JOIN_ALWAYS")   # drill switch (default off)
# series allowlist: if set, ONLY quote markets whose series (ticker before the first
# '-') is listed. The pilot scopes to the weather/temp slice; empty = no filter (legacy).
SERIES_ALLOW = [s for s in os.environ.get("KALSHI_SERIES_ALLOW", "").split(",") if s.strip()]
# SERIES DENY-LIST (operator decision 2026-07-29, live evidence): comma-separated ticker
# PREFIXES excluded from selection. The fast index books were the repeat bleeder across both
# live sessions — 07-29: KXDXYDUD = -$8.44 of the session's -$9.44 realized (venue tape,
# 01:44:30Z); 07-27: the NDQ/INX hourlies drove the whole loss mechanism. Fast one-way bursts
# are structurally the worst microstructure for a resting maker, and the reward thesis does not
# depend on them. PREFIX match (startswith) so one entry covers a family's hourly/daily strike
# variants. Held inventory in a denied series still unwinds via the strand path (same guarantee
# as the far-close cap). Empty = no-op.
# ⚠ A6 (operator-ruled 2026-08-05: allowlist wins): deny is NO LONGER absolute — an EXACT
# SERIES_ALLOW member overrides any deny prefix (KXDXY/KXNDQ/KXINX were shadowing the
# receipt-proven KXDXYDUD/KXNDQHUD/KXINXHUD). To bench an ALLOWLISTED series, remove it
# from SERIES_ALLOW (or rely on per-ticker mkt_out); a deny entry alone will not do it.
SERIES_DENY = [s.strip() for s in os.environ.get("KALSHI_SERIES_DENY", "").split(",") if s.strip()]
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
# REMOVED (operator Q1 decision, 2026-07-28): KALSHI_REDUCE_ONLY_KEEP_BOTH kept the accumulating
# side alive (floor-sized) on HELD markets under the breaker. That is a direct contradiction of
# the holding => exit-only risk rule, and the live 07-27 tape showed the shape it enables
# (KXDXYDUD flipped -20 -> +17 THROUGH flat under reduce-only). The old pairing is in git
# history (`0a86b2b`, removed with the flags in this commit); revert is the revert mechanism.
INV_SOFT_CT = _envf("KALSHI_INV_SOFT_CT", 30.0)
INV_HARD_CT = _envf("KALSHI_INV_HARD_CT", 80.0)
# INVARIANT (fix H): a single JOIN fill must not by itself breach the hard cap, or one fill
# overshoots the shapeable [SOFT,HARD] band before the next cycle can throttle. Clamp the
# resting join size to the hard cap so accumulation stays inside the gradient we control.
# JOIN_SIZE == 0 (operator decision 2026-07-29: "set at any contract amount") means the join
# has NO contract cap of its own — quote size is governed by DOLLARS (MAX_MARKET_CAPITAL/2 per
# side) alone, bounded only by the fix-H hard envelope below so one fill still cannot blow
# through the position ceiling. Positive values keep the legacy per-quote contract cap.
if JOIN_SIZE > 0 and INV_HARD_CT > 0 and JOIN_SIZE > int(INV_HARD_CT):
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
# --- THE INVENTORY RISK RULE IS UNCONDITIONAL (operator Q1 decision, 2026-07-28) --------------
# Operator's rule, on record 2026-07-27 19:48:56Z: "we can sell at a loss"; "we shouldnt be one
# sided unles we are exiting". Flat => both sides or nothing. Holding => the reducing side and
# NOTHING else, at any size. One-sided is legal ONLY as an exit.
# Two flags briefly made this switchable (KALSHI_EXIT_AT_TOUCH, KALSHI_HOLDING_EXIT_ONLY,
# 21596e3) and a loss cap (KALSHI_MAX_UNWIND_LOSS) once priced exits BEHIND the touch. The
# operator ordered all three OUT: the losing behaviour must not be one env var away (the same
# shape as the dormant neg-risk landmine in CLAUDE.md). What they guarded against:
#   - the cap (2026-07-22, ~50c/pair chase) is superseded by the STRAND CROSS (STRAND_CROSS_S):
#     losses are bounded in TIME by paying the spread, never by refusing to price the exit —
#     "a loss cap that blocks the exit is not a loss cap" (live 2026-07-27: exit pinned 0.73 vs
#     market 0.82, 42 ct rode to settlement).
#   - re-offering the losing side ("both sides always rest, control by SKEW") re-armed every
#     adverse fill: KXNDQHUD hit 3x in 47s at 0.60 -> 0.66 -> 0.70; KXDXYDUD ran -20 -> +17
#     THROUGH flat under the breaker.
# git revert is the revert mechanism (this commit; behaviour flags deliberately NOT recreated).
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
# --- DAILY LOSS KILL: equity (balance + marked inventory) is metered as TRUE DRAWDOWN from the
# intraday high-water mark; a breach sustained across HALT_CONFIRM_N of the last 5 cycles writes
# the STOP sentinel (maker-first flatten + halt until the operator removes it). The peak-based
# measure is immune to income and deposits: a credit lifts the peak by the amount it lifts
# equity, so it buys ZERO extra room (WAS a drop from a FROZEN day-start, which income inflated —
# measured 07-23: $76.42 of effective room against a $40 nominal quota, on an $85 account).
# The second arm — the RATCHETING cumulative-sum-of-decreases (DAILY_DOWN_HALT_USD, the 07-22
# "treadmill" guard) — was REMOVED BY OPERATOR ORDER 2026-08-02 after the 08-02 halt post-mortem:
# 34.51% of its $68.68 reading was a torn-read accounting artifact, and it never netted
# recoveries. KNOWN ACCEPTED GAP: a realize-loss/recover-via-credits cycle no longer accumulates
# toward a halt; only the drawdown arm stands. The equity snapshot itself is torn-read-proof —
# see the consistency re-read in the meter (run_once).
DAILY_LOSS_HALT_USD = _envf("KALSHI_DAILY_LOSS_HALT_USD", 20.0)
# DD CARRY (2026-08-09) — ships ON, deliberately, unlike most new knobs. The behaviour it
# replaces is a HOLE, not a feature: the day/marker re-baseline re-seeded the peak at current
# equity, so any drawdown still open at the boundary was forgiven and the bot got a full fresh
# envelope while already down (measured live 2026-08-09: dd $3.00 at 23:58:18Z -> $0.00 at
# 00:00:09Z, $7.55 of slide erased). Shipping this OFF would leave that hole open every single
# midnight, which is the opposite of a safe default. Set KALSHI_DD_CARRY=0 to restore the old
# forgive-at-baseline behaviour. The carry DECAYS as equity recovers — a debt, not a penalty —
# so it can only make the halt fire EARLIER on a bleed that is still open, never later.
DD_CARRY = _envb("KALSHI_DD_CARRY", True)
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
# --- FLATTEN SLIPPAGE BOUND (operator Q7 decision 2026-07-28): flatten_to_zero's IOC loop hit
# whatever the touch was after each pass, 4 tries deep — live 2026-07-27 the STOP escalation
# walked KXDXYDUD 0.52 -> 0.50 -> 0.46 -> 0.25 in ~2s, selling 23 ct at 0.25 that settled at
# 1.00 the next day. A pass whose touch has moved more than this many DOLLARS against us from
# the FIRST pass's touch is refused; the residual keeps/regains its maker exit and later passes
# (next cycle / strand clock) retry from the fresh book. 0 disables the bound.
FLATTEN_MAX_SLIP = _envf("KALSHI_FLATTEN_MAX_SLIP", 0.10)
# market close_time cache for the far-close market-clock cap (static per market; the daemon is
# a long-lived process so steady-state cost is ~zero reads).
# audit batch 3 (J1, operator-approved 2026-07-29): entries are (close_time_or_"", stamp).
# A payload with NO close_time used to cache "" FOREVER — bool("") is never "far", so one bad
# read permanently re-admitted a far market past the market-clock cap. Negative entries now
# expire (re-fetched after the TTL) and the cache is bounded (the daemon sees thousands of
# rotating programs; unbounded growth is the same class as the SCORES/telemetry leaks).
_CLOSE_TIME_CACHE = {}
CLOSE_CACHE_NEG_TTL_S = 3600.0
# B-3 (identity review, operator "go" 2026-08-06): POSITIVE entries used to live forever,
# but the venue can amend close_time (early determination / extension) — re-verify every
# 6h. With state persistence (B-2) the TTL also bounds how stale a restored clock can be.
CLOSE_CACHE_POS_TTL_S = _envf("KALSHI_CLOSE_CACHE_POS_TTL_S", 21600.0)
CLOSE_CACHE_MAX = 8192


def _close_cache_snapshot():
    """B-2: positives only ({ticker: close_time}) for state persistence — negatives are
    cheap to re-learn and must not survive a restart with a stale stamp."""
    return {t: v[0] for t, v in _CLOSE_TIME_CACHE.items() if v[0]}


def _close_cache_restore(saved):
    """B-2: repopulate at startup with a FRESH stamp — close_times are near-static and
    the POS TTL forces a re-verify within CLOSE_CACHE_POS_TTL_S anyway. Kills the
    ~0.5-2h per-restart warmup fail-open window (measured: 3/5 probe slots on
    beyond-horizon markets, 2026-08-06T00:52Z, one closing 2028)."""
    base = time.monotonic()
    for t, ct in (saved or {}).items():
        if ct and len(_CLOSE_TIME_CACHE) < CLOSE_CACHE_MAX:
            # review C-2: a uniform stamp made all ~8k restored entries expire in the
            # same instant every POS_TTL — per-ticker jitter (capped TTL/2 so nothing
            # restores pre-expired) spreads the re-reads over a 3h window.
            jitter = abs(hash(str(t))) % max(int(CLOSE_CACHE_POS_TTL_S // 2), 1)
            _CLOSE_TIME_CACHE.setdefault(str(t), (str(ct), base - jitter))


# HIGH-ACTIVITY FIRST GATE (operator-named 2026-08-02, coarse v1 — review later): skip
# markets whose venue-wide 24h traded volume exceeds MAX_VOL24H_CT contracts. Crowded/hot
# markets are where adverse fills live and where our share of the reward pool is smallest;
# the ideology targets low-to-moderate activity. Volume piggybacks on the close-time read
# (zero extra reads on a miss; TTL'd so activity spikes are seen within VOL24_TTL_S).
MAX_VOL24H_CT = _envf("KALSHI_MAX_VOL24H_CT", 0.0)      # 0 = gate OFF (provable no-op)
VOL24_TTL_S = _envf("KALSHI_VOL24_TTL_S", 21600.0)      # re-read activity every 6h
_VOL24_CACHE = {}                                        # {ticker: (contracts_24h, mono)}


def _vol24_cache_get(ticker):
    v = _VOL24_CACHE.get(ticker)
    if v is None:
        return None
    vol, stamp = v
    if (time.monotonic() - stamp) > VOL24_TTL_S:
        _VOL24_CACHE.pop(ticker, None)
        return None
    return vol


def _close_cache_get(ticker):
    """close_time str, "" (recent negative), or None (absent/expired -> caller re-fetches)."""
    v = _CLOSE_TIME_CACHE.get(ticker)
    if v is None:
        return None
    ct, stamp = v
    age = time.monotonic() - stamp
    if not ct and age > CLOSE_CACHE_NEG_TTL_S:
        _CLOSE_TIME_CACHE.pop(ticker, None)
        return None
    if ct and age > CLOSE_CACHE_POS_TTL_S:       # B-3: amended clocks get re-read
        _CLOSE_TIME_CACHE.pop(ticker, None)
        return None
    return ct


def _close_cache_put(ticker, close_time):
    if len(_CLOSE_TIME_CACHE) >= CLOSE_CACHE_MAX:
        # evict the oldest 1/8 by stamp; O(n log n) only when the bound is hit
        for k, _ in sorted(_CLOSE_TIME_CACHE.items(),
                           key=lambda kv: kv[1][1])[:CLOSE_CACHE_MAX // 8]:
            _CLOSE_TIME_CACHE.pop(k, None)
    _CLOSE_TIME_CACHE[ticker] = (close_time or "", time.monotonic())
# --- PRE-CLOSE SETTLEMENT FLATTEN (2026-07-24 measured loss): a market CLOSES (trading ends) at
# its close_time but SETTLES hours later; after close we CANNOT trade, so whatever NAKED (unpaired,
# net-directional) ladder inventory we hold AT CLOSE rides to settlement and resolves against us
# (gas-daily 26JUL24: -$34.98 across 7 strikes — a directional band-bet at the ATM). A properly
# PAIRED ladder (yes-low / no-high) self-hedges to ~$1/pair and is SAFE to carry; only the NAKED
# residual is the settlement gamble. This mechanism, within PRECLOSE_FLATTEN_MIN of MARKET CLOSE
# (trading end, NOT the reward-period end), MAKER-FIRST rests the reducing quote (existing unwind
# path) and, if the naked residual still exceeds STOP_TAKER_MIN_CT after a STOP_ESCALATE_S grace,
# TAKER-crosses AT MOST |naked| contracts — NEVER a paired leg. SEPARATE from the general
# TAKER_FLATTEN backstop (that stays as-is). DEFAULT 0 = OFF = byte-for-byte today's behavior.
# ORDERING (FIX 4, operator-approved 2026-07-27): every taker cross is CANCEL -> CROSS -> RE-REST.
# The original design left the resting maker exit alone ("the taker is additive") on a no-self-trade
# argument — but self-trade was never the risk. Live 2026-07-27 19:40:03Z: a taker crossed KXNDQHUD
# flat, and the un-cancelled 41ct@0.73 exit filled 7 SECONDS LATER (+40.55 ct) — the moment the
# position died, the "exit" became a naked ENTRY. Never-strand is now provided by the RE-REST leg
# (a failed/partial cross re-rests the maker exit), not by never cancelling.
PRECLOSE_FLATTEN = _envi("KALSHI_PRECLOSE_FLATTEN", 0)          # 0 = OFF, provable no-op until flipped
PRECLOSE_FLATTEN_MIN = _envf("KALSHI_PRECLOSE_FLATTEN_MIN", 15.0)  # act within N min of MARKET CLOSE
# --- FIX 3 (operator-approved 2026-07-27): CROSS THE EXIT IF IT DOES NOT FILL --------------------
# The 07-27 loss mechanism: the maker exit rested (at the loss-cap price, 9c behind the touch) and
# simply never filled while the market trended — nothing crossed it away from the settlement window,
# so 42 ct rode from a -$0.59 touch-exit to a -$15.29 settlement. Fixes 1/2 make the exit fillable
# and stop re-arming the losing side; THIS knob bounds how long an unfilled exit may rest before we
# pay the spread and get out. Per-ticker clock (persisted in quoter_state, cycles are fresh
# processes): once a NAKED residual >= STOP_TAKER_MIN_CT has existed for STRAND_CROSS_S seconds,
# ONE capped IOC pass per cycle fires at the touch — cancel-confirmed first, capped at the FRESH
# |naked| (re-read inside the pass, never the cycle snapshot), maker exit re-rested for any
# residual, clock re-armed after every attempt so book-walking is paced (the 07-27 STOP escalation
# walked DXY 0.52 -> 0.25 in 4 back-to-back IOCs; one pass per clock period cannot).
# The taker leg needs TAKER_FLATTEN=1 and live mode; otherwise the clock and telemetry still run.
# Default 15s — OPERATOR-CONFIRMED 2026-07-28 ("ok proceed with 15s and we can adjust"), revised
# down from the 30s proposal on the operator's read that a spike that matters persists: the one
# live chain (07-27 KXNDQHUD) ran 0.60 -> 0.66 in 32s, 0.70 at 47s, and never came back, so a
# shorter wait exits cheaper in exactly the trends that hurt. Effective exit latency is this
# clock + up to one cycle (~5-8s live). 0 disables the mechanism entirely.
STRAND_CROSS_S = _envf("KALSHI_STRAND_CROSS_S", 15.0)
# EXIT LOSS-MIN CALCULATOR (operator-named 2026-07-31 "make one for exits that is beyond
# reproach"; module kalshi_exit_calc, arithmetic receipt-verified). Before the strand clock
# pays a taker, the EXACT cross cost (half-spread + the venue's exact 0.07*C*P*(1-P) fee) is
# computed from the live touches. Cheap or unavoidable crosses (1-tick spread, cost <=
# EXIT_CHEAP_CROSS_USD, or ladder exhausted) pay immediately, exactly as before; otherwise the
# resting maker exit is IMPROVED one tick per strand period (EXIT_LADDER_STEPS periods max) —
# giving up a tick beats paying half the spread + fee on mean-reverting books, and the era
# receipts put -$176.01 of -$182.06 realized on taker legs. Bounded time is preserved: worst
# case adds EXIT_LADDER_STEPS * STRAND_CROSS_S seconds before the taker backstop fires.
# EXIT_LADDER_STEPS=0 restores the legacy cross-at-first-strand behavior byte-for-byte.
EXIT_LADDER_STEPS = _envi("KALSHI_EXIT_LADDER_STEPS", 2)
EXIT_CHEAP_CROSS_USD = _envf("KALSHI_EXIT_CHEAP_CROSS_USD", 0.25)
# J (operator-named 2026-07-31): touch-motion discipline on the paid exit. Between strand
# evaluations (>= STRAND_CROSS_S apart) the exec-side touch is tracked per ticker:
#   one FAST move against us (>= SWEEP_VETO_TICKS)      -> SPIKE: defer this pass entirely
#       (a whale sweep's worst prints revert; measured ~$6.7 lost buying 35-ct slices of
#       700-1200 ct sweeps). Deferral is bounded: it cannot repeat consecutively.
#   two CONSECUTIVE fast moves                          -> TREND: cross IMMEDIATELY, skip the
#       maker ladder (patience in a real trend is how -$5-in-44s happens).
# SWEEP_VETO_TICKS=0 disables both arms (byte-identical strand behavior).
SWEEP_VETO_TICKS = _envi("KALSHI_SWEEP_VETO_TICKS", 3)
_STRAND_STEP = {}                            # ticker -> ladder step; mirror of st['strand_step']
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
# SELECTION SHAPE (Phase C2), kept in its OWN namespace. FP_DROPS means "why a candidate was
# dropped" and test_selection_observability asserts that meaning by exact set — mixing
# measurement into it would have forced that pin to be loosened, so the pin stays and the
# measurement lives here. Merged into the plan row alongside FP_DROPS, cleared with it.
FP_SHAPE = {}


def _record_pool_shape(rows, picked, shape):
    """POOL HISTOGRAM + BELOW-CUT SHAPE (Phase C2). Writes into FP_SHAPE, a namespace of its
    own — FP_DROPS means "why a candidate was dropped" and a test asserts that meaning by
    exact set, so measurement is kept separate rather than loosening the pin.

    The funnel counted how many candidates were dropped but never WHAT they looked like, so
    "we passed over 200 markets" could not be turned into "we passed over 200 markets whose
    pools were all under $20/day" — the two have opposite implications for whether the cut is
    costing anything. Recording the histogram of the whole candidate pool and of the part below
    the cut makes the question answerable from the tape.

    Buckets are usd_day (the venue's daily reward pool), chosen around the measured live
    distribution: p50 $140 / max $1,000 across 475 quoted tickers on 2026-08-02.
    Pure measurement — returns nothing and changes no selection."""
    try:
        _edges = (0.0, 1.0, 10.0, 50.0, 140.0, 500.0)
        def _bucket(v):
            for i, e in enumerate(_edges):
                if v < e:
                    return i
            return len(_edges)
        _picked_ids = {id(r) for r in picked}
        _below = [r for r in rows if id(r) not in _picked_ids]
        for name, group in (("pool", rows), ("below", _below)):
            _h = [0] * (len(_edges) + 1)
            for r in group:
                try:
                    _h[_bucket(float(r.get("usd_day") or 0.0))] += 1
                except (TypeError, ValueError):
                    pass
            shape[f"{name}_hist"] = _h
        shape["below_cut_n"] = len(_below)
        # Row-safe: ONE malformed usd_day used to raise into the outer handler and take the
        # ENTIRE shape record with it — a single bad row losing all the telemetry, which is the
        # opposite of what a measurement layer should do under bad input. Caught by its own pin
        # 2026-08-03. Bad rows are skipped and counted; everything else still reports.
        _sum, _bad = 0.0, 0
        for r in _below:
            try:
                _sum += float(r.get("usd_day") or 0.0)
            except (TypeError, ValueError):
                _bad += 1
        shape["below_cut_usd_day_sum"] = round(_sum, 2)
        shape["shape_bad_rows"] = _bad
        # vol24h coverage over the candidate pool: how much of the selection input we actually
        # have, so a later cut on it knows its own denominator.
        _v = [r["vol24h_ct"] for r in rows if r.get("vol24h_ct") is not None]
        shape["pool_vol24h_known"] = len(_v)
        shape["pool_n"] = len(rows)
        if _v:
            _vs = sorted(_v)
            shape["pool_vol24h_p50"] = round(_vs[len(_vs) // 2], 2)
            shape["pool_vol24h_max"] = round(_vs[-1], 2)
    except Exception:
        _SILENT["pool_shape_fail"] += 1


def select_footprint(progs, now):
    FP_DROPS.clear()
    FP_SHAPE.clear()
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
        probe_only = False
        if t in MACRO_PROBE_TICKERS:
            # D-C (operator-ruled 2026-08-06): an operator-DESIGNATED macro-probe ticker is
            # force-admitted at full designation — never probe_only, never activity-gated
            # (exemptions below key off this flag). Deny and the clock drops still apply.
            probe_only = False
            drops["macro_probe_admitted"] = drops.get("macro_probe_admitted", 0) + 1
        elif SERIES_ALLOW and t.split("-")[0] not in SERIES_ALLOW:
            # CLOSED-WORLD PILOT EXCEPTION (operator-ruled 2026-08-05, question 4): with the
            # flag on, a non-allowlist series is not dropped -- it passes PROBE-ONLY, tagged
            # explore so the existing probe clamp (EXPLORE_PROBE_CT) sizes it and the D3 ramp
            # / W7 receipt clamp bound it further. Real presence stays allowlist-only; the
            # discovery surface costs at most probe scale. Flag off = the absolute allowlist.
            if not ALLOW_PROBE_EXCEPTION:
                drops["drop_allowlist"] = drops.get("drop_allowlist", 0) + 1
                continue                   # series allowlist (pilot = proven payers only)
            probe_only = True
            drops["allow_probe_passed"] = drops.get("allow_probe_passed", 0) + 1
        if (SERIES_DENY and any(t.startswith(p) for p in SERIES_DENY)
                and not (SERIES_ALLOW and t.split("-")[0] in SERIES_ALLOW)):
            # A6 (operator-ruled 2026-08-05: allowlist wins): an EXACT SERIES_ALLOW member
            # outranks a deny PREFIX — the KXDXY/KXNDQ/KXINX prefixes were shadowing
            # allowlisted KXDXYDUD/KXNDQHUD/KXINXHUD whenever their programs return.
            # Deny keeps full force for every non-allowlisted series, probe rows included.
            drops["drop_series_deny"] = drops.get("drop_series_deny", 0) + 1
            continue                       # operator-excluded family (see SERIES_DENY comment)
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
        cutoff_min = min(MAX_ENTRY_CUTOFF_MIN,
                         max(_effective_wind_down_min(life_min), LATE_LIFE_FRAC * life_min))
        _is_macro = t in MACRO_PROBE_TICKERS   # D-C review #6: designation overrides the
        if end < now + timedelta(minutes=cutoff_min) and not _is_macro:  # horizon prefs (the
            drops["drop_late_life"] = drops.get("drop_late_life", 0) + 1  # operator designated
            continue                                                      # a specific ticker
        # FAR-CLOSE CAP — refuse markets resolving beyond the horizon we can actually stay present
        # in. Measured presence in 14d+ markets was a 0.02% median (n=5). Structural, so it holds
        # even with no presence calibration loaded. Designated macro tickers exempt (review #6);
        # date-parse and close-past safety drops still apply to them.
        if (MAX_DAYS_TO_CLOSE > 0 and end > now + timedelta(days=MAX_DAYS_TO_CLOSE)
                and not _is_macro):
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
        rows.append({"ticker": t, **({"explore": True} if probe_only else {}),
                     "usd_day": (p.get("period_reward") or 0) / 10000,
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
    # MARKET-CLOCK PRE-FILTER (funnel audit 2026-07-29). The far-market-close veto originally ran
    # in run_once AFTER selection — so long-dated markets carrying short reward windows (the
    # KXNHPRIMARY28 pattern: market resolves 2028, program ends this week) consumed footprint
    # slots and were then vetoed, collapsing a 40-slot footprint to ~5 survivors (measured live
    # 15:11Z: 3,338 programs -> 40 selected -> 35 vetoed post-hoc). The veto must spend slots on
    # markets that can actually be quoted: filter the TOP candidate rows by the market's OWN
    # close_time BEFORE slot allocation. Reads are lazy + cached (_CLOSE_TIME_CACHE, static per
    # market; steady-state ~zero fetches) and bounded to the head of the pool-ranked list; an
    # unreadable clock KEEPS the row (fail-open, same as the run_once belt, which remains as a
    # cheap second check on whatever this pass could not price).
    if MAX_DAYS_TO_CLOSE > 0 and rows:
        _kept, _checked = [], 0
        # ≤ half the cycle read budget (live 15:15Z: 4x FOOTPRINT starved ctx.build -> hot path
        # down for the cycle). Post-D1: full-universe warm-up is ~ceil(rows/_budget) cycles
        # (~33 at 3,300 rows / 100 budget) after each process start; bounded per cycle,
        # visible via close_unchecked_tail shrinking to zero.
        _budget = min(FOOTPRINT_TOP * 4, max(40, READ_BUDGET_PER_CYCLE // 2))
        for _ri, r in enumerate(rows):
            # D1 ROOT FIX (selection review 2026-08-01; operator-named Option C
            # 2026-08-02): the old `len(_kept) >= FOOTPRINT_TOP*2` early stop made the
            # read budget inert once the cache held ~80 head rows — every cycle appended
            # ~3,300 permanently-unchecked rows, the round-robin drew most of its 40
            # picks from them, and the belt killed ~28 (footprint p50 12/40 in
            # 2,277/2,277 cycles). Now the scan continues through the WHOLE list: cached
            # clocks filter exactly at zero cost, paid reads stay budget-bounded, and
            # only the genuinely-unpriceable remainder is kept fail-open (belt = second
            # check), COUNTED so telemetry shows it shrinking to zero as the persistent
            # cache covers the universe.
            if _checked >= _budget:
                drops["close_unchecked_tail"] = len(rows) - _ri
                _kept.extend(rows[_ri:])               # cache still warming -> fail-open
                break
            _t3 = r["ticker"]
            _ct3 = _close_cache_get(_t3)
            _v3 = _vol24_cache_get(_t3)
            if _ct3 is None or (_v3 is None and MAX_VOL24H_CT > 0):
                _checked += 1
                try:
                    _mkt3 = public_get(f"/trade-api/v2/markets/{_t3}").get("market", {})
                    _ct3 = _mkt3.get("close_time")
                    _close_cache_put(_t3, _ct3)
                    try:
                        _v3 = float(_mkt3.get("volume_24h_fp") or 0.0)
                    except (TypeError, ValueError):
                        _v3 = 0.0
                    _VOL24_CACHE[_t3] = (_v3, time.monotonic())
                except Exception:
                    _kept.append(r)                    # unreadable clock -> keep (fail-open)
                    continue
            # HIGH-ACTIVITY FIRST GATE (operator-named 2026-08-02: "avoid any market on
            # high activity on the site for now as the 1st gate. review later"). Activity
            # = venue volume_24h_fp contracts, same payload the close read already pays,
            # cached VOL24_TTL_S. Measured basis for the live threshold (random n=160 of
            # 5,448 active liquidity programs, API 2026-08-02): p50=0, p75~107, p90~995,
            # p99~27,568 ct/24h -> 1,000 ct ~= the top decile. Fail-open when unknown;
            # held inventory on a gated market unwinds via the strand path as with every
            # other selection drop. 0 = gate OFF (test-pinned no-op).
            # PERSIST THE SELECTION INPUT, not just the verdict (Phase C2). The gate's own
            # threshold was measured once against a sample; carrying vol24h onto the row means
            # a later study can re-cut it from OUR OWN candidates instead of re-sampling the
            # venue — and can ask the question the 08-02 measurement could not: what vol24h did
            # the markets we actually LOST money on carry? (The gate caught 0 of the 6 losers.)
            # Recorded whether or not the gate is armed, so the data exists before a decision
            # needs it.
            if _v3 is not None:
                r["vol24h_ct"] = float(_v3)
            _allowlisted3 = ((bool(SERIES_ALLOW) and r["ticker"].split("-")[0] in SERIES_ALLOW)
                             or r["ticker"] in MACRO_PROBE_TICKERS)   # D-C designation exempts
            if (MAX_VOL24H_CT > 0 and _v3 is not None and _v3 > MAX_VOL24H_CT
                    and not _allowlisted3):
                # ALLOWLIST EXEMPTION (operator option A, 2026-08-05): a receipts-proven series
                # is screened by its OWN payment history, and the diagnostic measured this gate
                # eating 24/40 allowlist rows -- including 17/17 gas strikes (vol 1,001-21,636ct,
                # the series that paid us 13 credits AT that activity) -- while selecting FOR
                # decided low-volume strikes that then fail the quote gates (quoted=0 wedge).
                # The gate stays fully live for probes/unknowns: the 32k-ct probe books it was
                # built for (operator-named 2026-08-02) are still blocked.
                drops["drop_high_activity"] = drops.get("drop_high_activity", 0) + 1
                continue
            # B-1 (identity review, operator "go" 2026-08-06): selection previously only
            # asked "too far?" never "already past?" — a settled market with a lingering
            # active program (KXEOWEEK-26JUL25 class) burned footprint/probe slots and
            # reads every cycle. One predicate kills the class.
            try:
                if bool(_ct3) and parse_iso(_ct3) <= now:
                    drops["close_past_selected"] = drops.get("close_past_selected", 0) + 1
                    continue
            except Exception:
                pass                                   # unparseable clock -> legacy path
            try:
                _far3 = bool(_ct3) and parse_iso(_ct3) > now + timedelta(days=MAX_DAYS_TO_CLOSE)
            except Exception:
                _far3 = False
            if _far3 and _farclose_paying_keep(r["ticker"].split("-")[0], r.get("end"), now):
                # FIX H: receipt-proven series with a paying program inside the horizon
                # rides past the market clock; the program's own expiry evicts it later.
                drops["farclose_paying_kept"] = drops.get("farclose_paying_kept", 0) + 1
                _far3 = False
            if _far3:
                drops["drop_far_market_close_sel"] = drops.get("drop_far_market_close_sel", 0) + 1
            else:
                _kept.append(r)
        rows = _kept
    # PROBE SLOT CAP (moved BELOW the market-clock/activity pre-filter, review 2026-08-05:
    # capping first burned every slot on rows the pre-filter then killed). FIX P
    # (operator-ruled 2026-08-06): slots are now streak-rotated, series-diverse, and
    # series-total-pool ranked -- see _cap_probe_slots. Allowlist rows never touched.
    if ALLOW_PROBE_EXCEPTION and PROBE_MAX_SLOTS >= 0:
        rows = _cap_probe_slots(rows, drops)
    # SCORE-BASED RANKING: replace the pool ordering with measured capture carried across cycles.
    # Falls back to exactly the pool order above for any market with no score yet, so a cold cache
    # (or a flag-off run) is byte-for-byte legacy. Wrapped — a ranking fault must never stop a cycle.
    if SCORE_RANK and rows:
        try:
            import kalshi_market_scores
            with SCORES_LOCK:
                rows = kalshi_market_scores.rank(
                    SCORES, rows, now=now.timestamp(),
                    swing_penalty=SCORE_SWING_PENALTY, unknown_bonus=SCORE_UNKNOWN_BONUS,
                    explore=SCORE_EXPLORE,
                    # A1 (logic audit 2026-08-05): allowlist payers must never absorb a
                    # sampling slot — the probe clamp was sizing proven earners at 5ct.
                    explore_exempt=(frozenset(SERIES_ALLOW) if SERIES_ALLOW else None),
                    incumbents=_INCUMBENT_TICKERS, incumbency_bonus=INCUMBENCY_BONUS)
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
    # Phase-3 (KALSHI_ALLOC_KEY=1): rotate series in RANK order — `rows` is already ordered by
    # the measured-capture rank above, so the series whose best member ranks highest spins
    # first. Audit issue #3 (series rotation was pool-keyed). Computed ONCE here because BOTH
    # selection branches need it (blind-review 2026-07-31: the PIVOT branch was uncovered —
    # the exact split-key inconsistency the audit condemned, reintroduced). Flag OFF -> pool
    # order, byte-identical, in both branches.
    _first = {}
    if ALLOC_KEY:
        for _i, _r in enumerate(rows):
            _first.setdefault(_r["ticker"].split("-")[0], _i)
    if not PIVOT_SELECT:
        # ---- LEGACY egalitarian round-robin (bytes unchanged; provable flag-off no-op) ----
        if ALLOC_KEY:
            series_order = sorted(by_series, key=lambda s: (_first.get(s, 1 << 30), s))
        else:
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
        if len(rows) > len(picked):
            # D10 (selection review 2026-08-01): the funnel's largest drop stage had no
            # counter — rows past the last slot vanished with zero reason codes, leaving a
            # viable market indistinguishable in telemetry from one never discovered.
            # Blind review lens A #6: label the cause — slots genuinely full vs the
            # round-robin exhausting under PER_SERIES_CAP with slots still empty.
            if len(picked) >= FOOTPRINT_TOP:
                drops["drop_not_selected"] = len(rows) - len(picked)
            else:
                drops["drop_series_capped"] = len(rows) - len(picked)
        _record_pool_shape(rows, picked, FP_SHAPE)
        _caprank_telemetry(rows, picked, now)   # observation only; no-op unless flag ON
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
    if ALLOC_KEY:
        # PIVOT branch on the SAME key (blind-review fix): series rotation and the density
        # remainder follow the rank order of `rows`; near-money still sorts within series.
        series_order = sorted(by_series, key=lambda s: (_first.get(s, 1 << 30), s))
    else:
        series_order = sorted(by_series, key=lambda s: (-by_series[s][0]["usd_day"], s))
    picked, per_series = [], defaultdict(int)
    for s in series_order:              # 1) COVERAGE floor: >=PIVOT_COVERAGE per active series
        for r in by_series[s][:PIVOT_COVERAGE]:
            picked.append(r)
            per_series[s] += 1
    dens = (list(rows) if ALLOC_KEY else                    # rank order IS the density order
            sorted(rows, key=lambda r: (-r["usd_day"], _prox(r), r["ticker"])))  # 2) REMAINDER
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
    if len(rows) > len(picked):
        drops["drop_not_selected"] = len(rows) - len(picked)   # D10, same as legacy branch
    _record_pool_shape(rows, picked, FP_SHAPE)  # PIVOT branch too (split-branch parity)
    _caprank_telemetry(rows, picked, now)       # observation only; no-op unless flag ON
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
    per-market cap; >=1 (caller gates unpriceable elsewhere).

    JOIN_SIZE 0 = no per-quote contract cap (operator 2026-07-29): dollars govern, with the
    fix-H hard inventory envelope (INV_HARD_CT) as the only contract-level ceiling so a single
    fill can never breach the position ceiling the throttle enforces."""
    if best <= 0:
        return max(1, 0)
    per_side = MAX_MARKET_CAPITAL / 2.0
    dollar_ct = int(per_side / best)
    if JOIN_SIZE > 0:
        n = min(JOIN_SIZE, dollar_ct)
    else:
        n = min(int(INV_HARD_CT), dollar_ct) if INV_HARD_CT > 0 else dollar_ct
        # QUANTIZE (audit F10, 2026-07-29): a dollar-governed count follows the reference price,
        # so without this a 1-tick move changes the count by 1 and the exact-match diff cancels +
        # re-places the order — forfeiting queue position (the thing rewards pay for) and
        # no-oping the Stage-B same-count reprice. Round DOWN to a multiple of 5 (>=5), so the
        # count only changes when the price has moved ~5+ ticks; the hard clamp (multiple of 5
        # by construction at the 80 default) and small counts (<5) are unaffected.
        if n >= 5:
            n = (n // 5) * 5
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
    (venue min order = 1 ct) — provably-never-overshoot.

    audit F12 (2026-07-29): the `room` dollar bound is GONE. It was harmless in the 20-ct era
    (room never bound) but at 80-ct fills it clipped the exit to ~53 ct, leaving the tail with
    no resting exit until the strand cross paid taker fees for it. A reducing order is capped at
    |inv| (can never overshoot), FREES collateral on fill, and the house doctrine already exempts
    reducing orders from every capital gate — so dollars must not clip the exit either.
    `price` is retained for call-site compatibility; it no longer sizes anything."""
    return max(1, int(abs(inv)))


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

    There is deliberately NO economic governor on an exit price (operator: "we can sell at a
    loss"); this bound is only "can the venue accept the order at all"."""
    return p is not None and EXIT_MIN_PRICE_DOLLARS <= p <= EXIT_MAX_PRICE_DOLLARS


def _improved_exit(touch, opposite, improve):
    """Ladder-improved exit price: `touch` + `improve` ticks toward the opposite side, never
    at/through it (post-only survives), never below the touch. `opposite` is the OTHER side's
    best bid (so this side's ask = 1 - opposite); with no opposite bid the bound is unknowable
    and the touch is returned unchanged (never guess a bound)."""
    if improve <= 0 or touch is None:
        return touch
    if opposite is None:
        return touch
    bound = round(1.0 - opposite - kalshi_exit_calc.TICK, 2)
    if bound <= touch:
        return touch
    return round(min(touch + kalshi_exit_calc.TICK * improve, bound), 2)


def _reducing_quotes(best_y, best_n, inv, cost, improve=0):
    """THE reducing-side quote builder — long yes -> a NO bid, long no -> a YES bid,
    priced AT the reference by _unwind_price and never larger than |inv| (_unwind_size).
    `improve` (exit loss-min ladder, operator-named 2026-07-31) prices the exit that many
    ticks INSIDE the spread instead of at the touch — bounded post-only by _improved_exit;
    0 (the default) is byte-for-byte the legacy touch pricing.

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
        up = _improved_exit(_unwind_price(best_n, cost), best_y, improve)
        if not _ok_exit_price(up):
            return []
        return [{"side": "no", "price_dollars": up,
                 "count": _unwind_size(_capped_join(up, best_y), up, inv), "reason": "unwind"}]
    if best_y is None:
        return []
    up = _improved_exit(_unwind_price(best_y, cost), best_n, improve)
    if not _ok_exit_price(up):
        return []
    return [{"side": "yes", "price_dollars": up,
             "count": _unwind_size(_capped_join(up, best_n), up, inv), "reason": "unwind"}]


def _offset_size(add_cnt, price, inv):
    """ORPHANED since the Q1 decision (2026-07-28): its only call site — the two-sided offset
    block in desired_quotes — became unreachable behind holding => exit-only and was removed.
    Retained (with KALSHI_PAIR_BOTH_SIDES) pending an explicit operator removal decision; it is
    not called from any production path.

    Size the REDUCING HALF OF A TWO-SIDED QUOTE so a double fill lands exactly PAIRED.

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
    """Price for a reducing (unwind) quote: the book reference, ALWAYS (operator Q1 decision
    2026-07-28 — "we can sell at a loss").

    A price cap here (the removed KALSHI_MAX_UNWIND_LOSS) meant that once the market moved
    further than the cap, our exit rested BEHIND the touch — unfillable at exactly the moment we
    most needed out. Measured live 2026-07-27: 42 ct KXNDQHUD NO at cost 0.364, cap-priced exit
    0.73 vs market 0.82, never filled, rode to settlement. A loss cap that blocks the exit is
    not a loss cap. Loss is bounded in TIME instead: the strand cross (STRAND_CROSS_S) pays the
    spread after a bounded wait, and the mark-aware daily halt bounds the day.

    `cost` is retained for call-site compatibility (and the audit trail); it no longer prices
    anything."""
    return best


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


def _qualifying_score(bids, our_price, our_size, target, df, own_orders=None):
    """R4 walk (a byte-equivalent replica of kalshi_market_scorecard.qualifying_share): reference =
    highest bid (<1.0); walk bids desc accumulating size to Target; score = DF^N*size (N = ticks
    below reference). Returns (our_score/book_total, side_qualifies) — book_total EXCLUDES our
    not-yet-placed order (the reward denominator once we rest is book_total + our_score).

    F5 (reward audit 2026-08-06, BLOCKER): in live mode the fetched book INCLUDES our RESTING
    orders, so book_total silently violated this function's own contract and callers composing
    raw/(1+raw) double-counted us — an empty book we occupied alone measured share ~S/(B+2S)=0.5
    instead of ~1.0, steering the rank AWAY from exactly the books the thesis targets.
    `own_orders` = [(price, count), ...] of OUR resting orders on THIS side; their df-weighted
    contribution (only where inside the qualifying set) is subtracted from the total, restoring
    the documented rivals-only denominator. Default None = byte-identical legacy behavior.
    Qualification (cum >= target) is deliberately judged on the RAW book: the venue counts our
    resting depth toward Target, so removing it would model $0 for a side we ourselves qualify.

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
    if own_orders:
        # p <= ref guard (blind review #4): a standing order ABOVE the fetched book's
        # reference (stale book / WS-mirror skew) would get a NEGATIVE tick exponent and an
        # amplified subtraction — over-admitting for a cycle. Outside [lowest_q, ref] it
        # cannot be inside the qualifying set of THIS book snapshot; skip it.
        rest = sum((df ** round((ref - p) / TICK)) * c for p, c in own_orders
                   if p is not None and lowest_q - 1e-9 <= p <= ref + 1e-9)
        total = max(total - rest, 0.0)
    our = 0.0
    if our_price is not None and our_price >= lowest_q - 1e-9:  # our order is inside the qualifying set
        our = (df ** round((ref - our_price) / TICK)) * our_size
    return (our / max(total, 1e-9) if our > 0 else 0.0), True


def _prospective_capture(m, yl, nl, best_y, best_n, target, own_orders=None):
    """Our PROSPECTIVE R4 capture $/day if we rested our intended JOIN size at reference on both
    sides. Per side raw = our_score/book_total (book excludes our not-yet-placed order); the
    reward denominator once we rest is book_total + our_score, so the prospective per-side share is
    raw/(1+raw). R3 (both sides must qualify) two-sided snapshot = (share_yes + share_no)/2, times
    the R1 pool (m['usd_day']). We join AT reference (N=0, DF^0=1) so our_score = our_size. Intended
    size = _capped_join — the exact size the JOIN branch would rest — so the gate models the order
    it is deciding whether to place. MODEL (M7 over-predicts 2-6x): a RELATIVE signal only.
    F5: `own_orders` = {"yes": [(price, count)...], "no": [...]} of OUR resting orders — the live
    book includes them, and without the subtraction the raw/(1+raw) composition double-counts us
    (~0.5 measured share on a book we hold alone). None = legacy byte-identical."""
    df = m.get("df") or CAPTURE_DF_DEFAULT     # present-but-None / 0 -> default (stress: "no df")
    _own = own_orders or {}
    ry, qy = _qualifying_score(yl, best_y, _capped_join(best_y, best_n), target, df,
                               own_orders=_own.get("yes"))
    rn, qn = _qualifying_score(nl, best_n, _capped_join(best_n, best_y), target, df,
                               own_orders=_own.get("no"))
    if not (qy and qn):
        return 0.0
    snap = (ry / (1.0 + ry) + rn / (1.0 + rn)) / 2.0
    if W12_PRICE_SHAPE and best_y is not None and best_n is not None:
        # W12 (built 2026-08-05; ships OFF): weight the forecast by the price-level shape
        # w(p) = 4*p*(1-p) -- 1.0 at 50c, 0.116 at 97c -- via _w12_shape(). p is the book
        # MID (best_y + 1 - best_n)/2, reflection-INVARIANT (blind review 2026-08-05:
        # best_y alone weighted mirror books 8.6x apart; a stale far bid on a decided
        # market dodged the discount entirely). W10 measured the signature this models:
        # KXEURUSDAW-26JUL31 rested 12.84h with 65.8% of presence at min(p,1-p) < $0.05 and
        # was credited $0.00, inside a ~20-event population the un-weighted model forecast
        # ~$26.04 for (aggregate only -- not row-reproducible, W10 study doc §6). This
        # estimator feeds the $1.20 floor gate; over-predicting at extremes is what admits
        # sub-$1 drive-bys (W10 §5). INFERRED shape: the venue documents P(1-P) for FEES,
        # not LIP scoring -- the exponent knob lets P2 receipts calibrate or refute it.
        # ENABLE INTERACTION (review): NETEV_MODEL_HAIRCUT=3.0 was fitted on the UNSHAPED
        # model's 2-6x over-prediction; part of that gap IS the extreme-price signature, so
        # arming W12 without re-fitting the haircut double-discounts extremes. Re-fit at B8.
        snap *= _w12_shape((float(best_y) + 1.0 - float(best_n)) / 2.0)
    return snap * float(m.get("usd_day", 0.0) or 0.0)


# ---------------- BACKGROUND VENUE SWEEPER glue (freshness root-fix 2026-07-30, Phase 1) ---------
# The sweeper (kalshi_market_sweeper.py) is pure scheduling/pacing; the three callables below are
# the ONLY seams between it and the quoter. It stores PROSPECTIVE capture in its own cache keys
# (pcap/pref/pts) — live ranking does NOT consume them yet (Phase 3, operator-gated), so with the
# flag on this is data collection only, and with KALSHI_SWEEP_ENABLED=0 (the default) none of it
# runs at all.

def _sweep_ages():
    """{ticker: last-MEASURED epoch} snapshot for the sweeper's oldest-first queue.
    Measurement times only (ts/pts) — attempt stamps (ats, D9) must NOT push a market the
    quoter merely TRIED to measure to the back of the sweep queue: the sweeper exists to
    measure exactly what the quoter couldn't (D9 review fix #2, 2026-08-02)."""
    with SCORES_LOCK:
        out = {}
        for t, r in SCORES.items():
            ts, pts = r.get("ts"), r.get("pts")
            if ts is None and pts is None:
                continue
            out[t] = max(float(ts or 0.0), float(pts or 0.0))
        return out


def _sweep_store(ticker, pcap, ref_yes):
    import kalshi_market_scores as _kms
    with SCORES_LOCK:
        _kms.update_prospective(SCORES, ticker, pcap, ref_yes)


def _sweep_measure(m, ob):
    """orderbook_fp -> (prospective capture $/day, ref_yes). The same M7 join-at-reference model
    the join gate uses, on the same _levels parse. A book missing either side measures $0 (R3:
    a one-sided snapshot pays nobody) — that IS the observation, not a decline."""
    yl, _ = _levels(ob.get("yes_dollars") or [])
    nl, _ = _levels(ob.get("no_dollars") or [])
    by = max((p for p, _ in yl), default=None)
    bn = max((p for p, _ in nl), default=None)
    if by is None or bn is None:
        return 0.0, by
    return _prospective_capture(m, yl, nl, by, bn, float(m.get("target") or 0.0)), by


FILLCOST_REFRESH_S = _envf("KALSHI_FILLCOST_REFRESH_S", 3600.0)   # 0 = off


def _refresh_fill_costs(client):
    """I (operator-named 2026-07-31): keep the per-market fill-cost feed FRESH in-process —
    the standalone kalshi_fill_costs.py script was manual-run only, so the feed sat stale at
    07-29 values carrying costs ~10x under reality (TOPMODEL -3.19 vs -19.42 final) — the
    landmine under any future ALLOC_KEY enable. Rewrites at most once per FILLCOST_REFRESH_S
    (mtime-gated, ~2 paginated reads/hour), atomic replace, fail-soft: a failed refresh keeps
    the stale file and counts _SILENT (telemetry feed — must never block trading)."""
    if FILLCOST_REFRESH_S <= 0:
        return
    try:
        if (os.path.exists(FILL_COST_PATH)
                and (time.time() - os.path.getmtime(FILL_COST_PATH)) < FILLCOST_REFRESH_S):
            return
    except OSError:
        pass
    try:
        import kalshi_fill_costs as _kfc
        # DEFECT 14 ROOT FIX (operator-named 2026-08-03: "fix then build"). This positions read
        # cannot see a SETTLED market — measured 0 of 129 settled tickers present under
        # unfiltered, count_filter=total_traded AND count_filter=position (2026-08-03T12:39:55Z)
        # — so the cost feed was blind to every completed round trip it exists to price, and it
        # is the feed the net-EV calibration is built on. Settlements are now supplied so a
        # settled market is priced from its receipt plus the position-aware cash of its own
        # fills. Fail-soft is unchanged: the whole helper is wrapped and counts _SILENT.
        positions = client._get_paginated(f"{API_ROOT}/portfolio/positions",
                                          "market_positions")["market_positions"]
        fills = client._get_paginated(f"{API_ROOT}/portfolio/fills", "fills")["fills"]
        try:
            settlements = client.get_settlements().get("settlements") or []
        except Exception:
            settlements = []                  # cost feed must never block on a third read
            _SILENT["fillcost_settlements_fail"] += 1
        markets = _kfc.build(positions, fills, settlements=settlements)
        tmp = FILL_COST_PATH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"schema": _kfc.SCHEMA, "markets": markets}, fh, separators=(",", ":"))
        os.replace(tmp, FILL_COST_PATH)
    except Exception:
        _SILENT["fillcost_refresh_fail"] += 1


def _ensure_sweeper():
    """Start the sweeper thread once per process. No-op unless KALSHI_SWEEP_ENABLED=1."""
    global _SWEEPER
    if _SWEEPER is None:
        import kalshi_market_sweeper as _kmsw
        _SWEEPER = _kmsw.start(_sweep_ages, _sweep_measure, _sweep_store,
                               score_rank_on=bool(SCORE_RANK))
    return _SWEEPER


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


def _market_telemetry_row(cyc, now, m, yl, nl, quotes, own_side, inv, gates, own_orders=None):
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
    _own_ord = own_orders or {}
    for tag, levels, side in (("y", yl, "yes"), ("n", nl, "no")):
        df_total, cum, ref, low_q, qual = _qualifying_breakdown(levels, target, df)
        oq = next((x for x in quotes if x.get("side") == side), None)
        our_px = oq["price_dollars"] if oq else None
        our_ct = float(oq["count"]) if oq else 0.0
        score = 0.0
        if qual and our_px is not None and low_q is not None and our_px >= low_q - 1e-9:
            score = (df ** round((ref - our_px) / TICK)) * our_ct
        # F5 (reward audit 2026-08-06): df_total is the PUBLIC book, which includes our own
        # RESTING orders — adding `score` on top double-counted us (a solo book read ~0.5
        # share). Subtract our resting orders' df-contribution (only where inside the
        # qualifying set) so the denominator is rivals + our intended order, matching the
        # venue's per-side normalization. own_orders None = legacy byte-identical.
        rest_df = 0.0
        if qual and ref is not None and low_q is not None:
            rest_df = sum((df ** round((ref - p) / TICK)) * c
                          for p, c in (_own_ord.get(side) or [])
                          if p is not None and low_q - 1e-9 <= p <= ref + 1e-9)
            rest_df = min(rest_df, df_total)
        denom = max(df_total - rest_df, 0.0) + score
        share = score / denom if denom > 0 else 0.0
        row[tag + "_ref"] = ref
        row[tag + "_rest_df"] = round(rest_df, 2)       # F5 audit trail (0.0 when not resting)
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
    cap = (sum(shares) / 2.0) * float(m.get("usd_day") or 0.0) if two_sided else 0.0
    if W12_PRICE_SHAPE and two_sided and row.get("y_ref") is not None             and row.get("n_ref") is not None:
        # same shape as _prospective_capture, same flag -- the offline sweep and the live
        # gates must never disagree on basis (review finding A).
        cap *= _w12_shape((float(row["y_ref"]) + 1.0 - float(row["n_ref"])) / 2.0)
    row["capture_usd_day"] = round(cap, 4)
    return row


def desired_quotes(m, yes_levels, no_levels, now, own=None, inv=0.0, event_delta=0.0, stats=None,
                   cost=0.0, own_orders=None):
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
    # EXIT LOSS-MIN LADDER (operator-named 2026-07-31): a ticker the strand clock has stepped
    # gets its resting exit priced that many ticks inside the spread (see _improved_exit).
    _improve = int(_STRAND_STEP.get(m.get("ticker"), 0) or 0) if EXIT_LADDER_STEPS > 0 else 0
    (yl, bad_y), (nl, bad_n) = _levels(yes_levels), _levels(no_levels)
    if stats is not None:
        stats["dropped_book_rows"] = stats.get("dropped_book_rows", 0) + bad_y + bad_n
    best_y = max((p for p, _ in yl), default=None)
    best_n = max((p for p, _ in nl), default=None)
    try:
        end = parse_iso(m["end"])
    except Exception:
        # D2 (selection review 2026-08-01): 50.7% of quote rows emitted no price and no
        # counter — every priceless exit below now says why (gate_* family; the caller's
        # qstats diff turns each into that row's `gates` entry).
        if stats is not None:
            stats["gate_bad_clock"] = stats.get("gate_bad_clock", 0) + 1
        return []            # unusable clock -> quote nothing here (stress: "garbage end").
                             # select_footprint already drops unparseable dates, so this is
                             # defence in depth, not the primary guard.
    # (`_priceable` lived here and gated the wind-down EXIT on the ENTRY band. Its only caller
    # now uses _reducing_quotes, which checks the one side it rests on at venue bounds, so the
    # variable is dead and is removed rather than left to be re-used by mistake.)
    if end < now + timedelta(minutes=_effective_wind_down_min(m.get("life_min"))):
        # wind_down: pull the two-sided quotes. But if we still HOLD inventory here, keep
        # resting the REDUCING side (passive $0 maker unwind) until the settlement taker
        # backstop takes over — never abandon an open position into resolution (fix F).
        # `_priceable` was the ENTRY band (both sides inside 0.04-0.96 and sum < 1.0). Gating
        # the EXIT on it meant a position could not be unwound into wind-down on exactly the
        # books where it most needed to be. _reducing_quotes checks the ONE side it rests on,
        # at venue bounds.
        if abs(inv) >= INV_TOLERANCE:
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
        if stats is not None:
            stats["gate_wind_down_flat"] = stats.get("gate_wind_down_flat", 0) + 1
        return []                                   # wind_down (flat -> pull entirely)
    # CROSSED BOOK IS CHECKED FIRST, and refuses BOTH entry and exit. A crossed book
    # (yes_bid + no_bid >= 1.0) is a stale/degenerate quote, not a price — a yes bid @best_y
    # and a no bid @best_n would cross, so it must never be rested even if post_only were
    # silently ignored. It is ordered ahead of the entry gates below because those now fall
    # through to a reducing quote when we hold inventory: leaving it last would have started
    # resting exits onto crossed books, which the strand path explicitly refuses
    # ("crossed/stale — do not chase"). Both paths now agree.
    if best_y is not None and best_n is not None and best_y + best_n >= 1.0:
        if stats is not None:
            stats["gate_crossed_book"] = stats.get("gate_crossed_book", 0) + 1
        return []
    # D-C MACRO-PROBE: an operator-designated ticker takes the Target-ladder path and
    # BYPASSES the quality gates below (designation IS the override) — but never the safety
    # gates above (bad clock, wind-down, crossed book) and never inventory handling: held
    # inventory unwinds through the standard reducing path exactly like every other market.
    if MACRO_PROBE_TICKERS and m.get("ticker") in MACRO_PROBE_TICKERS:
        if abs(inv) >= INV_TOLERANCE:
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
        return _macro_probe_quotes(m, yl, nl, stats, own=own)
    # THE NEXT TWO GATES REJECT AN *ENTRY*. Each used to `return []`, which also discarded
    # the reducing quote built further down — so a held position on a one-sided or extreme
    # book got NO exit order at all, which is precisely the book a losing position ends on.
    # Flat -> still nothing. Holding -> rest the reducing side and stop.
    if best_y is None or best_n is None:            # one-sided: cannot JOIN, can still EXIT
        if abs(inv) >= INV_TOLERANCE:
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
        # counter fires ONLY on the priceless [] path (blind review 2026-08-01 lens A #8:
        # counting the reducing-quote branch overstated "priceless exits")
        if stats is not None:
            stats["gate_one_sided_book"] = stats.get("gate_one_sided_book", 0) + 1
        return []
    if not (_ok_entry_price(best_y) and _ok_entry_price(best_n)):
        # the KXMAMDANIEO mechanism: best_y + best_n < 1 means a YES ref below MIN_PRICE
        # forces NO above MAX_PRICE — both sides fail together, excluding the whole
        # longshot category. Now it excludes it OUT LOUD (priceless [] path only).
        if abs(inv) >= INV_TOLERANCE:
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
        if stats is not None:
            stats["gate_entry_band"] = stats.get("gate_entry_band", 0) + 1
        return []
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
        _exp, _ideal, _frac = _expected_credit_usd(m, yl, nl, best_y, best_n, target, now,
                                                   own_orders=own_orders)
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
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
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
        # ⚠ THE VERDICT BRANCH DEMANDS A REAL RECEIPT AND A REAL NUMBER. It used to read
        # `confidence not in (None, "unproven")` with `poor = net_pct is not None and ...`, and
        # that pair INVERTED the gate on two reachable inputs (both executed 2026-08-03, not
        # reasoned about):
        #   * confidence="thin" — kalshi_netev_calibrate emits a THIRD value (:175-180) on any
        #     window with fewer than MIN_RECEIPT_TRADES in-window trades. "thin" is not
        #     "unproven", so it took this branch and was believed as receipt-grade.
        #   * net_pct_notional=None — emitted whenever in-window notional is 0. `poor` can never
        #     fire on None, so the family fell through the gate ENTIRELY and opened full
        #     two-sided, while a genuinely UNPROVEN family in the same book was skipped to the
        #     conservative model. No data outranked unknown data.
        #   Measured: thin+None -> 2 quotes {yes:100,no:100}; thin+(-0.50) -> 0 quotes;
        #   receipt+None -> 2 quotes. The table shipped on disk is calibrate output, so this was
        #   live-reachable, not theoretical.
        # The producer-side bar (kalshi_netev_rebuild.MIN_RECEIPT_FILLS) closes it for ONE
        # producer. This closes it for ALL of them — calibrate, rebuild, and any hand-written or
        # legacy table — because the safety property belongs in the gate, not in a writer.
        # Anything that is not an explicit "receipt" carrying a real number now routes to the
        # model fallback, which is what "we have no verdict for this family" is supposed to mean.
        # ⚠ THIS IS NOT UNIFORMLY FAIL-CLOSED, and 1799c2c's commit message claiming
        # "FAIL-CLOSED, NOT FAIL-OPEN" was wrong in one direction. Against an unearned ALLOW it
        # tightens. Against a NEGATIVE non-receipt verdict it LOOSENS: a `thin` family the CSV
        # engine scored net-negative used to be hard-skipped when flat, and now takes the model
        # path, which opens whenever prospective_capture / HAIRCUT - fingerprint > 0. Measured
        # against this function on the T-HARDEN fixture book, varying only usd_day:
        #     thin/-0.50  usd_day=  50 -> pre: skip | post: 0 quotes
        #     thin/-0.50  usd_day= 200 -> pre: skip | post: 2 quotes  <-- LOOSENED
        #     thin/-0.50  usd_day=5000 -> pre: skip | post: 2 quotes  <-- LOOSENED
        # The five T-HARDEN pins all run at the _mkt default usd_day=50.0, where the model
        # returns [] for every input — so the loosened case is exactly the one they cannot see.
        # KEPT, AND IT MATCHES THE BOT'S THESIS RATHER THAN MERELY BEING TOLERABLE (operator
        # instruction 2026-08-04: "think of our goals and thesis of the bot and adjust to
        # match"). The thesis on record is REWARD-POSITIVE, DEFECT-NEGATIVE: presence is what
        # EARNS, rewards more than cover the structural maker cost, "drips are fine — we make
        # money on drips", and benching presence to stop small losses is exactly what canon
        # forbids. A `thin` verdict is BY DEFINITION too little evidence to justify removing
        # reward-earning presence, so routing it to the model — which still opens only on
        # positive prospective capture — is the thesis-correct outcome, not a leak to be
        # apologised for. Benching belongs where the evidence is STRONG: a receipt-grade family
        # whose net% is negative WITH CREDITS ALREADY COUNTED is genuinely not paying for
        # itself, and that is what NETEV_MIN_MARGIN_PCT=0.0 acts on.
        # ⚠ WHERE THE THESIS AND THE ARMED TABLE STILL DISAGREE — flagged, not silently
        # resolved: every window available was produced by a bot carrying defects 1-14, so the
        # table cannot separate agent defects from family economics, and all six receipt-grade
        # families on it read negative (KXTOPMODEL -3.12%, gas -4.68%, KXTRUMPENDORSEMENTS
        # -5.00%, KXDXYDUD -5.60%, KXTRUMPTIME -5.89%, temp -6.09%) — so at margin 0.0 the gate
        # benches ALL SIX. Under RULE SEVEN those losses are substantially agent defects, and
        # benching on them launders defect losses into "this family loses money". Measured
        # margin ladder if the operator prefers to tolerate drips instead: -4% keeps 1 of 6,
        # -5% keeps 2, -6% keeps 5, -7% keeps all 6. NOT CHANGED HERE — moving a live money
        # threshold is the operator's call, and the honest fix is a table rebuilt on post-fix
        # trading once the bot runs again.
        if (ent is not None and ent.get("confidence") == "receipt"
                and ent.get("net_pct_notional") is not None):
            net_pct = ent["net_pct_notional"]              # RECEIPT signal (net % of notional)
            poor = net_pct < NETEV_MIN_MARGIN_PCT
            signal = net_pct
        else:                                               # UNPROVEN: conservative model fallback
            pc = _prospective_capture(m, yl, nl, best_y, best_n, target,
                                      own_orders=own_orders)
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
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
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
        pc = _prospective_capture(m, yl, nl, best_y, best_n, target,
                                  own_orders=own_orders)
        if stats is not None:
            stats["capture_min_pc"] = min(stats.get("capture_min_pc", 1e18), pc)
        if pc < CAPTURE_MIN_USD_DAY:
            if stats is not None:
                stats["capture_skipped"] = stats.get("capture_skipped", 0) + 1
            if abs(inv) < INV_TOLERANCE:
                return []                           # FLAT + poor-for-us -> skip
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
    # SELECTION GATE (only when ~flat — if we hold inventory we must keep quoting to unwind):
    # skip WIDE or ONE-SIDED books. A balanced two-sided book is where the maker-unwind
    # reliably fills; a one-directional/wide book is the gas-ladder trap that adverse-selects
    # us and then won't let the passive exit fill. This is the primary defense of "flatten as
    # a maker". ACTIVATE (void) markets are intentionally thin -> exempt (handled elsewhere).
    if not void and abs(inv) < INV_TOLERANCE:       # ONLY when truly FLAT (not just below SOFT):
        spread_ticks = (1.0 - best_n - best_y) / TICK   # any inventory in [TOL,SOFT) must keep
        sym = min(ext_y, ext_n) / max(ext_y, ext_n, 1e-9)   # quoting the reducing side to unwind
        if spread_ticks > MAX_SPREAD_TICKS or sym < MIN_DEPTH_SYM:
            if stats is not None:
                stats["gate_wide_or_asym"] = stats.get("gate_wide_or_asym", 0) + 1
            return []                               # one-sided / wide -> unwind-unreliable, skip
    if JOIN_ALWAYS:
        # drill/testing switch: tiny join on both sides of any priceable market,
        # ignoring void/activate economics — exercises place/diff/cancel machinery.
        # HOLDING => EXIT ONLY applies HERE TOO (leak found in the 2026-07-28 adversarial review of
        # fix 2): without this guard the drill switch re-arms the accumulating side while holding —
        # the exact defect the risk rule exists to kill. Risk rule wins over drills.
        if abs(inv) >= INV_TOLERANCE:
            if stats is not None:
                stats["holding_exit_only"] = stats.get("holding_exit_only", 0) + 1
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
        return [{"side": "yes", "price_dollars": best_y, "count": _capped_join(best_y, best_n), "reason": "join"},
                {"side": "no", "price_dollars": best_n, "count": _capped_join(best_n, best_y), "reason": "join"}]
    quotes = []
    if void:
        # ACTIVATE (thin book, we supply Target depth). If we CARRY inventory here, do NOT
        # blanket-pull (that removes the $0 maker unwind AND leaves the taker unreachable while
        # inv is frozen) — rest ONLY the reducing side to unwind passively.
        if abs(inv) >= INV_TOLERANCE:
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
        if abs(ev) > INV_SOFT_CT:
            if stats is not None:
                stats["gate_event_directional"] = stats.get("gate_event_directional", 0) + 1
            return []                               # event already directional -> don't ADD via activate
        if STANDDOWN:                               # STAND-DOWN: don't commit activate depth into a
            _sd, _eff = _standdown_market(m, True)  # thin-reward void book (flat here -> strands
            if _sd:                                 # nothing). Held inventory unwinds above, untouched.
                if stats is not None:
                    stats["standdown"] = stats.get("standdown", 0) + 1
                    stats["standdown_min_rho"] = min(stats.get("standdown_min_rho", 1e18), _eff)
                return []                           # reward too thin to justify Target-size activate
        if PRESENCE_GATE:
            # $1-MINIMUM ON THE ACTIVATE PATH (double-blind audit 2026-08-02, lens 1 #3;
            # operator-named same day). Every credit gate was scoped `not void`, so seeding
            # an empty book committed up to MAX_ACTIVATE_CAPITAL with ZERO expected-credit
            # check — the one OPEN path outside the $1-minimum ideology. Same floor, same
            # model, flat entry only (held inventory unwound above, untouched). Measured
            # exposure: 73 of 7,080 cycles (07-29..08-01 plan rows) had >=1 activate.
            _expA, _idealA, _fracA = _expected_credit_usd(m, yl, nl, best_y, best_n,
                                                          target, now, own_orders=own_orders)
            if _expA < MIN_CREDIT_USD:
                if stats is not None:
                    stats["gate_activate_credit"] = stats.get("gate_activate_credit", 0) + 1
                return []
        # audit F2 (2026-07-29): with JOIN_SIZE=0 the old max(JOIN_SIZE, target-ext) floor could
        # go to ZERO on a book short on only one side — and this branch appended the count-0
        # quote unconditionally (venue 400s it every cycle). Floor each side at 0 and only
        # append sides with a whole contract to add; JOIN_SIZE>0 keeps the legacy floor.
        add_y = max(JOIN_SIZE, target - ext_y) if JOIN_SIZE > 0 else max(0.0, target - ext_y)
        add_n = max(JOIN_SIZE, target - ext_n) if JOIN_SIZE > 0 else max(0.0, target - ext_n)
        cap = best_y * add_y + best_n * add_n
        if cap > MAX_ACTIVATE_CAPITAL:
            if stats is not None:
                stats["gate_activate_cost"] = stats.get("gate_activate_cost", 0) + 1
            return []                               # too expensive to activate
        # audit F1 clamp (2026-07-29): activate counts get the SAME fix-H contract ceiling as
        # joins — without it a $40 activate at low prices could rest hundreds of contracts into
        # a book that is thin by definition of void, and one fill would dwarf INV_HARD_CT.
        _acl = int(INV_HARD_CT) if INV_HARD_CT > 0 else 10 ** 9
        if int(add_y) >= 1:
            quotes.append({"side": "yes", "price_dollars": best_y,
                           "count": min(int(add_y), _acl), "reason": "activate"})
        if int(add_n) >= 1:
            quotes.append({"side": "no", "price_dollars": best_n,
                           "count": min(int(add_n), _acl), "reason": "activate"})
    else:
        # HOLDING => EXIT ONLY (operator directive 2026-07-27; made UNCONDITIONAL by the Q1
        # decision 2026-07-28). This is THE line that turned a single adverse fill into a
        # 42-contract position. The legacy design kept the accumulating side live (shrunk, but
        # live) whenever we held inventory, so every fill was followed by a fresh offer of the
        # SAME losing side. Live tape 2026-07-27, KXNDQHUD:
        #   19:06:44  sell 20 @ NO 0.40   (maker)
        #   19:07:16  sell 17 @ NO 0.34   (maker)  <- only possible because we re-posted
        #   19:07:31  sell  5 @ NO 0.30   (maker)  <- and again
        # 22 of the 42 contracts exist solely because of the re-post. Same shape on KXINXHUD
        # (22 of 29.1 ct). Once we hold, the only order we want working is the one that gets us
        # out. Flat is still both-sides-or-nothing (below); one-sided is legal ONLY as an exit.
        if abs(inv) >= INV_TOLERANCE:
            if stats is not None:
                stats["holding_exit_only"] = stats.get("holding_exit_only", 0) + 1
            return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve)
        # JOIN: external depth meets Target both sides, so shaping OUR size never voids it.
        # BOTH sides ALWAYS rest here (never pulled to zero) — the resting quotes are what earns
        # the rewards; inventory earns nothing. Reachable only when FLAT (sub-tolerance dust):
        # the skew/offset machinery that shaped an accumulating side WHILE HOLDING was removed
        # with the Q1 decision (unreachable behind the return above); the event-delta throttle
        # below still shapes a flat ticker inside a directional event.
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
        # THROTTLE DIRECTION follows the EVENT aggregate (a flat ticker in a directional event
        # must not ADD to the drift). Per-ticker inventory can no longer drive this block: the
        # holding => exit-only return above guarantees |inv| < INV_TOLERANCE here, so the
        # inv-driven throttle arm, the held-$ envelope and the offset/pairing machinery that
        # shaped an accumulating side WHILE HOLDING were removed as unreachable (Q1, 2026-07-28;
        # `git show 228bedd^:kalshi_live/maker_kalshi_quoter.py` has the last live copy).
        # SETTLEMENT RAMP (audit HIGH-2): be SMALL at settlement so the settle-taker is a rare
        # backstop, not the primary exit into the worst tick. Inside RAMP_MIN both join sizes
        # scale down linearly with time-to-end (floor MIN_QUOTE_CT); unwind quotes are never
        # ramped — de-risking gets easier as the end nears, adding gets harder.
        mins_left = (end - now).total_seconds() / 60.0
        ramp_min = m.get("ramp_min") or RAMP_MIN     # per-market (C13); fallback to global default
        if mins_left < ramp_min:
            scale = max(0.0, (mins_left - WIND_DOWN_MIN) / max(1.0, ramp_min - WIND_DOWN_MIN))
            y_cnt = max(MIN_QUOTE_CT, int(y_cnt * scale))
            n_cnt = max(MIN_QUOTE_CT, int(n_cnt * scale))
        if abs(ev) > INV_SOFT_CT:
            acc = 1 if ev > 0 else -1
            mag = abs(ev)
            hard = mag >= INV_HARD_CT
            # shrink the accumulating side toward MIN_QUOTE_CT and step it 1 tick inside so it
            # fills last. AT/ABOVE HARD the accumulating side IS pulled to zero (audit MED-3):
            # the MIN_QUOTE floor would keep leaking fills on a one-way market, so HARD is the
            # hard envelope. Above it, bounded risk beats that side's reward.
            over = min(1.0, (mag - INV_SOFT_CT) / max(1.0, INV_HARD_CT - INV_SOFT_CT))
            if acc > 0:                             # event drifts YES-ward -> throttle YES
                if hard:
                    y_cnt = 0                       # HARD STOP: cap the envelope, stop the leak
                else:
                    y_price, y_cnt = _throttled_quote(best_y, y_cnt, over, yl, target)
            else:                                   # event drifts NO-ward -> throttle NO
                if hard:
                    n_cnt = 0
                else:
                    n_price, n_cnt = _throttled_quote(best_n, n_cnt, over, nl, target)
        # FINAL EMIT — the band depends on WHAT the quote is, not merely on its price. A leg
        # tagged "unwind" is REDUCING risk and takes venue bounds; anything else is OPENING risk
        # and takes the strategy band. (Post-Q1 nothing in THIS branch emits an unwind — every
        # reducing quote comes from _reducing_quotes via the returns above — but the reason-aware
        # gate stays: it is the last gate every quote passes and must never re-gate an exit.)
        _ok_y = _ok_exit_price(y_price) if y_reason == "unwind" else _ok_entry_price(y_price)
        _ok_n = _ok_exit_price(n_price) if n_reason == "unwind" else _ok_entry_price(n_price)
        if y_cnt > 0 and _ok_y:
            quotes.append({"side": "yes", "price_dollars": y_price, "count": y_cnt, "reason": y_reason})
        if n_cnt > 0 and _ok_n:
            quotes.append({"side": "no", "price_dollars": n_price, "count": n_cnt, "reason": n_reason})
    return quotes


# --- FIX H (operator-ruled 2026-08-06): FAR-CLOSE PAYING EXCEPTION (default 0 = hard rule).
# Measured 2026-08-06T00:05Z: KXCHIPBURRITO (receipt-proven payer) carried a $990/day
# program (6 x $165, 08-03->08-09) on markets closing 09-02 -- the 8-day market-clock cap
# dropped every row all day, excluding the largest live allowlist pool entirely. The cap's
# lockup fear is answered by existing machinery: entry cutoffs key on the PROGRAM window,
# and a program's expiry evicts its rows from the harvest so strand-unwind flattens holds.
# With the flag on, a row is kept past the market clock ONLY when its series has venue
# credit RECEIPTS (credits_n>0 -- the same proof the W7 ramp trusts) AND its program window
# ends inside MAX_DAYS_TO_CLOSE. UNPROVEN series keep the hard rule (receipts are the
# criterion, allowlist membership is not -- a once-paid probe series qualifies, review F3).
FARCLOSE_PAYING_EXCEPTION = _envi("KALSHI_FARCLOSE_PAYING_EXCEPTION", 0)


def _farclose_paying_keep(series, prog_end_iso, now):
    """True when the FAR-CLOSE PAYING EXCEPTION keeps a market-clock-far row: flag on,
    series receipt-proven, and the PROGRAM (not market) ends inside the horizon.
    Any parse/read failure -> False (the hard rule stands; fail-toward-drop)."""
    if not FARCLOSE_PAYING_EXCEPTION or not prog_end_iso:
        return False
    try:
        if parse_iso(prog_end_iso) > now + timedelta(days=MAX_DAYS_TO_CLOSE):
            return False
        row = _d3_feedback_cached(now.timestamp()).get(series)
        return isinstance(row, dict) and (row.get("credits_n") or 0) > 0
    except Exception:
        return False


# --- FIX P (operator-ruled 2026-08-06): PROBE SLOT QUALITY. Measured 23:59:54Z: 4/5 slots
# burned on gate-refused books (3 same-series KXAPRPOTUS strikes + a July-dated KXEOWEEK)
# while $1,000/day W16 successor candidates went unsampled. Selection was per-market-pool
# with no diversity and no feedback from the quote gates.
_PROBE_GATE_REFUSED = {}      # ticker -> consecutive cycles a probe slot rested nothing


def _cap_probe_slots(rows, drops):
    """Keep at most PROBE_MAX_SLOTS probe-only rows, chosen by:
      1. lowest gate-refusal streak (a probe that keeps failing the book gates YIELDS its
         slot -- deprioritization, never a ban: with no competition it still samples),
      2. round-robin ONE PER SERIES, series ordered by TOTAL pool (a $1,000/day series
         split over 10 strikes must outrank one $333 market),
      3. row pool within a series.
    Non-probe rows pass through untouched; surviving rows keep their original relative
    order (downstream round-robin/rank depend on it). Emits FP_SHAPE['probe_slots']."""
    probe_idx, _unknown_close = [], []
    for i, r in enumerate(rows):
        if (r.get("explore") and SERIES_ALLOW
                and r["ticker"].split("-")[0] not in SERIES_ALLOW):
            # B-2 (identity review, operator "go" 2026-08-06): a probe is pure discovery
            # with zero urgency — it must never ride the close-cache warmup fail-open
            # into a slot (measured: 2028-close market probed at 00:52Z). Close-unknown
            # candidates wait for the cache; the ALLOWLIST fail-open path is untouched.
            if not _close_cache_get(r["ticker"]):
                _unknown_close.append(i)
            else:
                probe_idx.append(i)
    if _unknown_close:
        drops["probe_close_unknown"] = (drops.get("probe_close_unknown", 0)
                                        + len(_unknown_close))
    if not probe_idx and not _unknown_close:
        return rows
    if not probe_idx:
        _dropset0 = set(_unknown_close)
        return [r for i, r in enumerate(rows) if i not in _dropset0]
    spool = {}
    for i in probe_idx:
        s = rows[i]["ticker"].split("-")[0]
        spool[s] = spool.get(s, 0.0) + float(rows[i].get("usd_day") or 0.0)
    # per-series candidate queues, best row first
    per_series = {}
    for i in probe_idx:
        per_series.setdefault(rows[i]["ticker"].split("-")[0], []).append(i)
    for s in per_series:
        per_series[s].sort(key=lambda i: (
            int(_PROBE_GATE_REFUSED.get(rows[i]["ticker"], 0)),
            -float(rows[i].get("usd_day") or 0.0), rows[i]["ticker"]))
    # series precedence: least-refused best candidate first (rotation beats pool), then
    # series TOTAL pool -- so an untried series outranks a gate-refused veteran even when
    # the veteran's pool is larger, and pools decide among equally-fresh candidates.
    def _series_key(s):
        best = per_series[s][0]
        return (int(_PROBE_GATE_REFUSED.get(rows[best]["ticker"], 0)), -spool[s], s)

    series_order = sorted(per_series, key=_series_key)
    picked, ring = set(), 0
    while len(picked) < max(PROBE_MAX_SLOTS, 0) and any(per_series.values()):
        s = series_order[ring % len(series_order)]
        ring += 1
        if per_series[s]:
            picked.add(per_series[s].pop(0))
    dropped = [i for i in probe_idx if i not in picked]
    if dropped:
        drops["probe_slots_dropped"] = drops.get("probe_slots_dropped", 0) + len(dropped)
    FP_SHAPE["probe_slots"] = [rows[i]["ticker"] for i in sorted(picked)]
    _dropset = set(dropped) | set(_unknown_close)
    return [r for i, r in enumerate(rows) if i not in _dropset]


DAY_BASELINE_RESET_MARKER = os.path.join(DATA_DIR, "day_baseline_reset")


def _consume_day_baseline_marker():
    """A7 (operator-ruled 2026-08-05): True exactly once when the operator-named-restart
    marker exists; the marker is deleted on consumption. Anything failing (permission,
    race) reads as absent — the governor then keeps the ordinary UTC-day baseline, which
    is the strictly-safer meter."""
    try:
        if os.path.exists(DAY_BASELINE_RESET_MARKER):
            os.remove(DAY_BASELINE_RESET_MARKER)
            return True
    except Exception:
        pass
    return False


def _l3_default_close_of(ticker):
    """Market close_time for the L3 expiry check — cached (static per market), one public
    read on a miss, negative results cached too (same idiom as the far-close cap)."""
    ct = _close_cache_get(ticker)
    if ct is None:
        try:
            ct = public_get(f"/trade-api/v2/markets/{ticker}"
                            ).get("market", {}).get("close_time")
            _close_cache_put(ticker, ct)
        except Exception:
            return None                    # unknown -> caller keeps the taint
    return ct


def _l3_out_series(mkt_out, now, close_of=None):
    """Series prefixes whose L3 series-probe taint is LIVE (A1b, operator-ruled
    2026-08-05: a conviction EXPIRES when its market closes). A settled strike cannot
    fill again, so it cannot bleed again — measured 08-05: 9/10 mkt_out members were
    settled markets, permanently probe-sizing 6/23 pilot payer series (TOPMODEL's
    quotable strike held at 5ct on a $200/day pool). Unknown/unparseable close KEEPS
    the taint — the risk limiter fails toward smaller size. Reads at most len(mkt_out)
    cached market clocks (n=10 live). NEVER mutates mkt_out: the per-ticker exit-only
    ban is a different consumer and keeps its own semantics."""
    close_of = close_of or _l3_default_close_of
    live = set()
    for t in (mkt_out or []):
        ct = close_of(t)
        try:
            expired = bool(ct) and parse_iso(ct) <= now
        except Exception:
            expired = False                # unparseable clock -> keep the taint
        if not expired:
            live.add(str(t).split("-")[0])
    return live


def apply_drop_grace(standing, desired, footprint_tickers, prev_grace, grace_cycles,
                     held=None, exit_only=None, inv_tolerance=1.0):
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
    time-on-book. Pure function, no I/O.

    A9-F4 (logic audit, operator-authorized 2026-08-05): `held` ({ticker: signed naked ct})
    re-tags the reducing side of a retained held ticker as reason='unwind' — a reason-less
    copy is invisible to every polarity-aware gate downstream (cap_desired's unconditional
    keep at its :3181-check, bound_creates priority, the breaker filter), so a tight capital
    cap could DROP the retained ticker and the diff would cancel a live resting exit on a
    held position. Mirrors the in-loop fetch-fail retention (review 07-22 skeptic), which
    already did exactly this. Tagging is diff-neutral (diff keys on side+price+count only).
    `exit_only` (set of governed tickers) applies the same F6a parity as the fetch-fail
    block: accumulating copies of a governed market are stripped; if nothing reducing rests,
    the ticker is not retained at all. Defaults (None/None) are byte-identical pre-F4."""
    new_grace = {}
    if grace_cycles <= 0:
        return desired, new_grace
    held = held or {}
    exit_only = exit_only or frozenset()
    out = dict(desired)
    for t, orders in standing.items():
        if not orders or t in out or t in footprint_tickers:
            continue                       # nothing to keep / still wanted / actively rejected
        used = int(prev_grace.get(t, 0))
        if used >= grace_cycles:
            continue                       # grace exhausted -> let the diff cancel it
        copies = [{"side": o["side"], "price_dollars": o["price_dollars"], "count": o["count"]}
                  for o in orders]
        _pos = float(held.get(t, 0.0) or 0.0)
        if abs(_pos) >= inv_tolerance:
            copies = [dict(o, **({"reason": "unwind"} if
                                 ((_pos > 0 and o["side"] == "no") or
                                  (_pos < 0 and o["side"] == "yes")) else {}))
                      for o in copies]
        if t in exit_only:
            copies = [o for o in copies if o.get("reason") == "unwind"]
            if not copies:
                continue                   # governed + nothing reducing -> let the diff cancel
        new_grace[t] = used + 1
        out[t] = copies
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
    # ABSENT file = legitimate cold start (silent). PRESENT-BUT-UNREADABLE = every latching
    # guard (halt baselines, day-peak, per-market loss trips, cooldowns) would
    # silently reseed from scratch — a full amnesty (self-audit A2-F20). Preserve the corrupt
    # file for forensics, be LOUD, and count it where the plan row surfaces it.
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception as e:
        _SILENT["state_corrupt"] += 1
        try:
            os.replace(STATE_FILE, STATE_FILE + ".corrupt-" + utcnow().strftime("%Y%m%d_%H%M%S"))
        except Exception:
            pass
        print(f"WARNING quoter_state.json UNREADABLE ({e!r}) — preserved aside; latching "
              f"guards are re-seeding from scratch (halt baselines, loss trips, cooldowns)")
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


def _own_order_levels(rows):
    """F5: standing rows for ONE ticker -> {"yes": [(price, count)...], "no": [...]} for the
    own-size subtraction in the share model. None/empty -> {} (legacy behavior everywhere)."""
    out = {"yes": [], "no": []}
    for o in (rows or []):
        s = o.get("side")
        if s in out:
            try:
                out[s].append((float(o.get("price_dollars")), float(o.get("count") or 0)))
            except (TypeError, ValueError):
                continue
    return out


def _mkt_capital(quotes):
    return sum(q["price_dollars"] * q["count"] for q in quotes)


# INCUMBENT-FIRST CAPITAL (operator-named 2026-07-30: "stay in the markets we are in and let
# them naturally extinguish then slowly build new markets with new rules" — BUILT, NOT ENABLED).
# When ON, cap_desired funds markets we are ALREADY standing in before any new entrant, so a
# rank/allocation-rule change can phase in without ripping up queue positions: incumbents keep
# their dollars until their reward windows close; freed capital then enters under whatever key
# orders the non-incumbent group. Ships OFF (default 0) => cap_desired ordering byte-identical.
ALLOC_INCUMBENT_FIRST = _envi("KALSHI_ALLOC_INCUMBENT_FIRST", 0)
# PER-FAMILY DOLLAR CAP (operator-named 2026-07-30, from the sibling-overload finding: resting
# concentration measured 19:30:47Z was 32% of $346.78 in ONE family, and no dollar-level family
# cap existed — PER_SERIES_CAP bounds slots only and is live-set to 100). Max accumulating
# dollars per series (ticker family); a sibling that would push its family past the cap is
# SKIPPED (capital flows on to the next family — unlike the total cap's tail-cut). Reducing
# (unwind) orders are NEVER blocked, but their dollars DO count toward the family total, so a
# heavy family stops accepting new siblings first — the conservative direction. 0 = OFF
# (default) => cap_desired behavior byte-identical.
SERIES_MAX_USD = _envf("KALSHI_SERIES_MAX_USD", 0.0)
# FAMILY CAP = 25% OF CAPITAL (operator-named 2026-07-31 "25% capital per family at a time"):
# the per-family budget is SERIES_PCT of live capital (the portfolio-tracking equity the total
# cap uses), with env SERIES_MAX_USD kept as a static ceiling when set. VERIFIED in the entry
# formula: cap_desired skips a sibling BEFORE any create when the family budget is full, and
# families are seeded with HELD dollars, so fills never reopen headroom. SERIES_PCT=0 falls
# back to the static SERIES_MAX_USD alone.
SERIES_PCT = _envf("KALSHI_SERIES_PCT", 0.25)
# L2 PAIR-CARRY MAKER UNWIND (operator-named 2026-07-31 "DO if guarded + blast-radius-
# verified"; BUILT DARK, default 0 = OFF => byte-identical). A floored ladder pair (long
# yes-low + long no-high) stays exempt from every TAKER de-risk path — crossing out pays two
# spreads to shed penny risk. This adds the PASSIVE lane: MAKER reduce-orders on both legs
# (buy no on the yes-leg, buy yes on the no-leg — Kalshi self-nets each leg to $1), whole
# contracts only, ONLY when both legs' books were read fresh this cycle, and ONLY when the
# combined proceeds beat the $1 settlement floor: (1-pn_A)+(1-py_B) >= 1 + MIN_EDGE. Each
# leg rests at its own inside (1 - opposite best bid = that leg's touch), so a lone fill
# leaves the partner NAKED at a touch-priced sale — the normal naked machinery (skew,
# strand clock) takes over, bounded. Orders carry reason='unwind': every polarity-aware
# gate (capital-cap keep, probe clamp, exit-only strips, breaker shape) already treats
# them as risk-reducing.
PAIR_UNWIND = _envi("KALSHI_PAIR_UNWIND", 0)
PAIR_UNWIND_MIN_EDGE = _envf("KALSHI_PAIR_UNWIND_MIN_EDGE", 0.02)


def _series_cap():
    try:
        eq = _TOTAL_CAP_EFF[0]
        dyn = SERIES_PCT * float(eq) if (eq is not None and SERIES_PCT > 0) else None
    except (TypeError, ValueError):
        dyn = None
    if dyn is not None and SERIES_MAX_USD > 0:
        return min(SERIES_MAX_USD, dyn)
    if dyn is not None:
        return dyn
    return SERIES_MAX_USD
_SERIES_CAP_DROPS = [0]     # markets skipped by the family cap in the LAST cap_desired call

# UNIFIED ALLOCATION KEY — Phase 3 of the allocation-key audit (operator-named 2026-07-30,
# BUILT NOT ENABLED). When ON, the capital cut, the create budget, and the series rotation
# all order by the SAME capital-aware score the caprank shadow has logged every cycle since
# 07-29 (kalshi_capital_rank.shadow_rank):
#   cap_score = (base x CAPRank calib  -  risk_lambda x realized fill cost) / committed $
# where base = measured capture (fresh, decayed) > sweeper prospective x haircut > pool
# prior x haircut. The "lost money cannot compound" concept (operator 2026-07-30) lives in
# the cost term: a market that burned real dollars ranks lower for NEW dollars, and
# ALLOC_RISK_LAMBDA > 1 punishes proven burners harder than their average loss — variance-
# aversion priced into allocation, not just observed. Fail-OPEN: any fault in scoring falls
# back to the pool dict (legacy ordering) and counts _SILENT["alloc_key_fail"].
# Ships OFF (default 0) => every consumer receives the pool dict, byte-identical behavior.
# Enabling is a separate operator naming AFTER receipts set KALSHI_CAPRANK_CALIB.
ALLOC_KEY = _envi("KALSHI_ALLOC_KEY", 0)
ALLOC_RISK_LAMBDA = _envf("KALSHI_ALLOC_RISK_LAMBDA", 1.0)
ALLOC_PROSPECTIVE_HAIRCUT = _envf("KALSHI_ALLOC_PROSPECTIVE_HAIRCUT", 1.0)
ALLOC_UNKNOWN_HAIRCUT = _envf("KALSHI_ALLOC_UNKNOWN_HAIRCUT", 1.0)
# Sweeper pcap older than this is NOT fed into the key (freshness plan: rank correlation is
# decision-grade to the 6-12h band, measured 2026-07-30; receipts may re-fit this).
ALLOC_PCAP_MAX_AGE_S = _envf("KALSHI_ALLOC_PCAP_MAX_AGE_S", 21600.0)


def _alloc_priority(footprint_rows, now, usd_day):
    """{ticker: priority} for capital allocation. Flag OFF -> usd_day verbatim (legacy).
    Flag ON -> cap_score from the shadowed capital-aware key, with the NEW sweeper's pcap
    (kalshi_market_scores rows, age-cutoff) merged into the prospective feed."""
    if not ALLOC_KEY:
        return usd_day
    try:
        import kalshi_capital_rank as _kcr
        costs = _load_fill_costs()
        now_ts = now.timestamp()
        # EVIDENCE FRESHNESS, one bar for BOTH sources (blind-review fix 2026-07-31): the
        # offline sweep file gets the SAME age cutoff as the live sweeper, and where both
        # have a row the NEWER observation wins — a days-old file must never shadow a
        # minutes-old sweeper measurement.
        prospective = {}
        for _t, _r in (_load_prospective() or {}).items():
            _fts = _r.get("ts")
            if _fts is not None and (now_ts - float(_fts)) > ALLOC_PCAP_MAX_AGE_S:
                continue
            prospective[_t] = _r
        with SCORES_LOCK:
            for _t, _r in SCORES.items():
                if (_r.get("pcap") is not None and _r.get("pts") is not None
                        and (now_ts - float(_r["pts"])) <= ALLOC_PCAP_MAX_AGE_S):
                    _cur = prospective.get(_t)
                    if _cur is None or float(_cur.get("ts") or 0.0) < float(_r["pts"]):
                        prospective[_t] = {"capture": _r["pcap"], "ref": _r.get("pref")}
            comp = _kcr.shadow_rank(footprint_rows, SCORES, costs, MAX_MARKET_CAPITAL,
                                    INV_HARD_CT, now.timestamp(), calib=CAPRANK_CALIB,
                                    swing_penalty=SCORE_SWING_PENALTY,
                                    unknown_bonus=SCORE_UNKNOWN_BONUS,
                                    prospective=prospective,
                                    risk_lambda=ALLOC_RISK_LAMBDA,
                                    prospective_haircut=ALLOC_PROSPECTIVE_HAIRCUT,
                                    unknown_haircut=ALLOC_UNKNOWN_HAIRCUT,
                                    credit_feedback=_load_credit_feedback(),
                                    d2_bonus=D2_BONUS,
                                    d2_neverpaid_mult=D2_NEVERPAID_MULT)
        return {d["ticker"]: d["cap_score"] for d in comp}
    except Exception:
        _SILENT["alloc_key_fail"] += 1
        return usd_day


def _fam_concentration(desired, denom=None):
    """(top_family, top_family_usd, top_family_pct) of an intended book. Pure —
    the concentration gauge (operator-named 2026-07-30) rides every plan row so sibling
    overload is visible the cycle it forms, not at the next manual audit.
    denom: when supplied (>0), pct is computed against IT — the live call passes the
    capital basis _series_cap() caps against, so fam_top_pct is directly comparable to
    SERIES_PCT (runtime-review F1 2026-07-31: pct-of-intended-book read 33.8% while the
    true capital share was 12.1% — the gauge looked like a cap breach that wasn't).
    Without denom (legacy/tests): pct of the intended-book total, as before."""
    fam = defaultdict(float)
    for t, qs in desired.items():
        fam[t.split("-")[0]] += _mkt_capital(qs)
    tot = sum(fam.values())
    if tot <= 0:
        return None, 0.0, 0.0
    s = max(fam, key=lambda k: fam[k])
    base = denom if (denom is not None and denom > 0) else tot
    return s, fam[s], 100.0 * fam[s] / base


_SERIES_CAP_SOLO = [0]      # blind-review fix 2026-07-31: markets whose OWN notional exceeds
                            # SERIES_MAX_USD even with an empty family — the config-footgun
                            # signature (cap set below per-market size blocks ALL new entries)


def cap_desired(desired, usd_day, incumbents=None, fam_held=None):
    """Keep whole markets in strict usd_day priority (highest first), stopping at
    the first ACCUMULATING market that would breach MAX_TOTAL_CAPITAL — keep the
    most valuable, cut the tail. REDUCING (any 'unwind' quote) markets are kept
    UNCONDITIONALLY: a risk-reducing order can never over-commit the account, so
    the cap must not drop it (polarity-aware, fix A). Returns (kept, dropped_count).

    `incumbents` (optional, default None => legacy ordering byte-identical): a set of
    tickers we are currently standing in. When provided, incumbents outrank every
    non-incumbent (pool order WITHIN each group is unchanged) — the phased-migration
    ordering above. The allocation-key audit (2026-07-30) lists this site as issue #1;
    the pool key for the NON-incumbent group is replaced in Phase 3, receipts-calibrated."""
    kept, total = {}, 0.0
    _SERIES_CAP_DROPS[0] = 0
    _SERIES_CAP_SOLO[0] = 0
    # Family EXPOSURE, not just quote-notional (blind-review fix 2026-07-31): seed each family
    # with its HELD-inventory dollars (caller passes them, $1/contract conservative — the same
    # reserve convention committed capital uses), so fills don't open headroom the cap was
    # supposed to close. fam_held=None (tests/legacy callers) => quote-notional-only, as before.
    fam = defaultdict(float)
    for k, v in (fam_held or {}).items():
        fam[k] += v
    for t, qs in desired.items():
        if any(q.get("reason") == "unwind" for q in qs):
            kept[t] = qs
            c = _mkt_capital(qs)
            total += c
            fam[t.split("-")[0]] += c
    inc = incumbents or set()
    order = [t for t in sorted(desired,
                               key=lambda t: (0 if t in inc else 1, -usd_day.get(t, 0)))
             if t not in kept]
    tail_cut = 0
    for i, t in enumerate(order):
        c = _mkt_capital(desired[t])
        _fcap = _series_cap()
        if _fcap > 0 and fam[t.split("-")[0]] + c > _fcap:
            _SERIES_CAP_DROPS[0] += 1          # family full: skip THIS sibling, keep going
            if c > _fcap:                      # would never fit even alone: the footgun signature
                _SERIES_CAP_SOLO[0] += 1
            continue                           # the dollars stay available to other families
        if total + c > _total_cap():   # portfolio-tracking cap (operator 2026-07-31)
            tail_cut = len(order) - i
            break
        kept[t] = desired[t]
        total += c
        fam[t.split("-")[0]] += c
    # SEMANTIC UN-DRIFT (blind-review 2026-07-31): the returned count is the TOTAL-CAPITAL
    # tail-cut ONLY — its meaning before the family cap existed. Family skips are reported
    # exclusively via series_cap_dropped; summing the two plan keys no longer double-counts.
    return kept, tail_cut


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
# audit batch 3 (J5, operator-approved 2026-07-29): during a sustained blackout the guard
# re-attempted EVERY failed cancel EVERY cycle — under the event-driven daemon (cycles can be
# seconds apart) a network partition became a cancel storm against a venue that is already
# refusing us. Attempts now back off exponentially (in-memory: a restart retries immediately,
# which is correct — a fresh process should probe once). [attempt_count, next_try_mono]
_BLACKOUT_BACKOFF = [0, 0.0]
BLACKOUT_RETRY_BASE_S = _envf("KALSHI_BLACKOUT_RETRY_BASE_S", 30.0)
BLACKOUT_RETRY_MAX_S = _envf("KALSHI_BLACKOUT_RETRY_MAX_S", 600.0)


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
    if time.monotonic() < _BLACKOUT_BACKOFF[1]:        # J5: paced retry, not a cancel storm
        plan["blackout_retry_paced"] = 1
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
    if remaining:                                   # J5: only failed attempts escalate the pace
        _BLACKOUT_BACKOFF[0] += 1
        _BLACKOUT_BACKOFF[1] = time.monotonic() + min(
            BLACKOUT_RETRY_BASE_S * (2 ** (_BLACKOUT_BACKOFF[0] - 1)), BLACKOUT_RETRY_MAX_S)
    else:
        _BLACKOUT_BACKOFF[:] = [0, 0.0]
    print(f"WARNING read blackout x{st['read_fail_streak']} — best-effort cancelled "
          f"{ok}/{len(oids)} last-known quotes ({len(remaining)} left to retry)")


def _refresh_safety_knobs():
    """Re-read the SAFETY knobs from the live env FILE each cycle (self-audit A2-F17): under
    the long-lived daemon every knob froze at import, so an operator tightening a loss limit
    mid-incident got SILENCE — the edit did nothing until a restart, with no error. systemd
    injects live.env only at service start, so os.environ is equally frozen; the FILE is the
    operator's actual control surface. Scope is deliberately the nine _SAFETY_KNOBS plus the
    per-market governors — the operator's emergency levers — not the full 60+ knob surface
    (selection/pacing knobs keep restart semantics; changing those mid-flight is a deploy).
    KALSHI_ENV_FILE unset (the default) -> provable no-op. Every applied change is PRINTED."""
    path = os.environ.get("KALSHI_ENV_FILE")
    if not path:
        return
    watch = {"KALSHI_DAILY_LOSS_HALT_USD": ("DAILY_LOSS_HALT_USD", float),
             "KALSHI_HELD_MAX_USD": ("HELD_MAX_USD", float),
             "KALSHI_TAKER_FLATTEN": ("TAKER_FLATTEN", int),
             "KALSHI_PRECLOSE_FLATTEN": ("PRECLOSE_FLATTEN", int),
             "KALSHI_THROTTLE_SMART": ("THROTTLE_SMART", int),
             "KALSHI_CAPTURE_GATE": ("CAPTURE_GATE", int),
             "KALSHI_STANDDOWN": ("STANDDOWN", int),
             "KALSHI_NETEV_GATE": ("NETEV_GATE", int),
             "KALSHI_MKT_DAY_LOSS_EXITONLY_USD": ("MKT_DAY_LOSS_EXITONLY_USD", float),
             "KALSHI_REENTRY_COOLDOWN_S": ("REENTRY_COOLDOWN_S", float),
             "KALSHI_HALT_CONFIRM_N": ("HALT_CONFIRM_N", int),
             "KALSHI_EXIT_LADDER_STEPS": ("EXIT_LADDER_STEPS", int),
             "KALSHI_EXIT_CHEAP_CROSS_USD": ("EXIT_CHEAP_CROSS_USD", float),
             "KALSHI_MKT_OUT_LOSS_USD": ("MKT_OUT_LOSS_USD", float),
             "KALSHI_MKT_UNWIND_ALLOW_PER_CT": ("MKT_UNWIND_ALLOW_PER_CT", float),
             "KALSHI_TAKER_GOV_CROSSES": ("TAKER_GOV_CROSSES", int),
             "KALSHI_TAKER_GOV_LOSS_USD": ("TAKER_GOV_LOSS_USD", float),
             "KALSHI_SERIES_PCT": ("SERIES_PCT", float),
             "KALSHI_STRIKES_OUT": ("STRIKES_OUT", int),
             "KALSHI_PAIR_UNWIND": ("PAIR_UNWIND", int),
             "KALSHI_PAIR_UNWIND_MIN_EDGE": ("PAIR_UNWIND_MIN_EDGE", float),
             "KALSHI_INCUMBENT_ONLY": ("INCUMBENT_ONLY", int),
             "KALSHI_SELECT_BUDGET": ("SELECT_BUDGET", int),
             "KALSHI_SELECT_BUDGET_MARGIN": ("SELECT_BUDGET_MARGIN", float),
             "KALSHI_MAX_VOL24H_CT": ("MAX_VOL24H_CT", float)}
    # Gov-D9 (1.1 review 2026-07-31): these four LOOK hot-reloadable (they sit in the same
    # env file) but are import-time only — an operator editing them mid-flight got a silent
    # no-op, the exact defect class KALSHI_THROTTLE_SMART hid. Changed-but-not-applied is
    # now loud every cycle it remains true. Applying them live stays a deploy decision.
    restart_only = {"KALSHI_SWEEP_VETO_TICKS": ("SWEEP_VETO_TICKS", int),
                    "KALSHI_EXPLORE_PROBE_CT": ("EXPLORE_PROBE_CT", int),
                    "KALSHI_SERIES_MAX_USD": ("SERIES_MAX_USD", float),
                    "KALSHI_FILLCOST_REFRESH_S": ("FILLCOST_REFRESH_S", float),
                    # D-A/D-C knobs (review #10): an operator killing a runaway macro probe
                    # or estimate floor mid-incident must get the needs-restart warning,
                    # never a silent no-op
                    "KALSHI_EST_FEED": ("EST_FEED", int),
                    "KALSHI_EST_FEED_MIN_FRAC": ("EST_FEED_MIN_FRAC", float),
                    "KALSHI_MACRO_PROBE_USD": ("MACRO_PROBE_USD", float),
                    "KALSHI_MACRO_PROBE_TOP": ("MACRO_PROBE_TOP", float),
                    "KALSHI_MACRO_PROBE_TICKERS": (
                        "MACRO_PROBE_TICKERS",
                        lambda s: frozenset(x.strip() for x in s.split(",") if x.strip()))}
    try:
        g = globals()
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if "=" not in line or line.startswith("#"):
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k in restart_only:
                    gname, cast = restart_only[k]
                    try:
                        nv = cast(v.strip())
                    except (TypeError, ValueError):
                        # audit lens 1 #10b: a MALFORMED value was the one silent sub-case
                        # left in the class Gov-D9 fixed — say it, don't swallow it
                        print(f"WARNING RESTART-ONLY knob {gname}: malformed value "
                              f"{v.strip()!r} in {path} — ignored")
                        continue
                    if g.get(gname) != nv:
                        print(f"WARNING RESTART-ONLY knob {gname} changed in {path} "
                              f"({g.get(gname)} -> {nv}) but NOT applied — this knob needs "
                              f"a service restart")
                    continue
                if k not in watch:
                    continue
                gname, cast = watch[k]
                try:
                    nv = cast(v.strip())
                except (TypeError, ValueError):
                    continue                     # malformed value -> keep the running one
                if g.get(gname) != nv:
                    print(f"SAFETY KNOB LIVE-APPLIED: {gname} {g.get(gname)} -> {nv} "
                          f"(from {path})")
                    g[gname] = nv
    except Exception:
        _SILENT["knob_refresh_fail"] += 1        # unreadable file -> keep running values


def run_once():
    os.chdir(DATA_DIR)
    _refresh_safety_knobs()
    try:
        _ensure_sweeper()     # background venue sweeper — no-op unless KALSHI_SWEEP_ENABLED=1
    except Exception:
        _SILENT["sweeper_start_fail"] += 1    # blind-review: swallowed faults must count
    try:
        _presence_table_refresh()   # audit fix: presence table no longer frozen at import
    except Exception:
        _SILENT["presence_refresh_fail"] += 1
    try:
        _netev_table_refresh()      # audit fix: same staleness class (gate OFF live = no-op)
    except Exception:
        _SILENT["netev_refresh_fail"] += 1
    if SERIES_MAX_USD > 0 and SERIES_MAX_USD < MAX_MARKET_CAPITAL:
        # config footgun (blind-review): a family cap below the per-market cap can block EVERY
        # new entry venue-wide; loud each run so a mid-incident env edit cannot hide it.
        print(f"WARNING KALSHI_SERIES_MAX_USD ({SERIES_MAX_USD}) < KALSHI_MAX_MARKET_CAPITAL "
              f"({MAX_MARKET_CAPITAL}) — single markets may never fit their family cap "
              f"(watch series_cap_solo)")
    _lock = _acquire_lock()
    if _lock is False:
        print("WARNING another quoter instance holds the run lock; skipping this run (no order ops)")
        return 0
    now = utcnow()
    client = KalshiOrderClient()          # dry_run unless operator-configured
    try:
        _refresh_fill_costs(client)       # I: hourly fill-cost feed refresh (mtime-gated)
    except Exception:
        _SILENT["fillcost_refresh_fail"] += 1   # helper is fail-soft; this is belt+suspenders
    if os.path.exists(STOP_FILE):
        # emergency stop: cancel quotes + rest MAKER offsets to flatten passively (never taker).
        _since = None
        try:
            with open(os.path.join(DATA_DIR, "stopflat.last")) as _fh:
                _since = (now - parse_iso(_fh.read().strip())).total_seconds()
        except Exception:
            pass                                     # no/corrupt stamp -> treat as first run
        if _since is not None and STOPFLAT_REPEAT_S > 0 and _since < STOPFLAT_REPEAT_S:
            print(f"STOP sentinel present; flatten ran {_since:.0f}s ago "
                  f"(< {STOPFLAT_REPEAT_S:.0f}s pacing) — offsets resting, standing by")
            _release_lock(_lock)
            return 0
        print("STOP sentinel present; maker-flattening (cancel quotes + rest offsets) + exiting")
        # READ-BUDGET RESET (root fix 2026-08-02). The normal reset lives at the bottom of this
        # block (search `_reads[0] = 0`), but the STOP branch RETURNS before reaching it, so in
        # the long-lived WS daemon `_reads[0]` is MONOTONE across STOP cycles: every flatten
        # spends from a budget that never refills. On exhaustion public_get raises
        # "read budget exhausted", every book read in _flatten_all fails, and the bail paths
        # cancel resting exits they can no longer replace — i.e. a halted bot ends up holding
        # inventory with NO working exit, which is the opposite of what STOP is for.
        # Each flatten must start with a full budget; that is what this line guarantees.
        # Placed before the dry_run guard so the pacing/flatten path is covered in every mode.
        # MEASURED 2026-08-02 (journal, window 10:26:00Z->23:2xZ): 27 flatten runs, 1,389 paced
        # skips, 0 exhaustions — real but not yet triggered in this halt window. The paced-skip
        # branch above returns without spending reads, so only flatten runs accumulate.
        _reads[0] = 0
        if client.mode != "dry_run":
            try:
                _sfp = os.path.join(DATA_DIR, "stopflat.last")
                with open(_sfp + ".tmp", "w") as _fh:
                    _fh.write(now.isoformat())
                os.replace(_sfp + ".tmp", _sfp)
            except Exception:
                pass                                 # unpaceable is survivable; flatten anyway
            _flatten_all(client)
        _release_lock(_lock)
        return 0
    _reads[0] = 0
    _book_src.update(mirror=0, rest=0, src_err=0)   # per-cycle book-source attribution
    cyc = int(now.timestamp())            # per-cycle nonce for unique order ids
    st = load_state()
    # side-channel mirrors for helpers that don't receive st (same pattern as _REALIZED_BY):
    # the exit ladder steps feed desired_quotes' unwind pricing; last-good equity feeds the
    # portfolio-tracking total cap until this cycle's balance read refreshes it.
    _STRAND_STEP.clear()
    _STRAND_STEP.update(st.get("strand_step") or {})
    _TAKER_XN.clear()
    _TAKER_XN.update(st.get("mkt_taker_xn") or {})
    if st.get("equity_prev") is not None:
        _TOTAL_CAP_EFF[0] = st.get("equity_prev")
    plan = {"ts": now.isoformat(), "mode": client.mode}
    # defect 9: absence must mean "not evaluated", never "did not fire". See the
    # _ALWAYS_EMIT_COUNTERS block for why this cannot change behaviour.
    for _k9 in _ALWAYS_EMIT_COUNTERS:
        plan[_k9] = 0
    for _k9 in _ALWAYS_EMIT_DROPS:
        plan[_k9] = 0
    # Defect 7. Placed HERE, immediately after `plan` exists — the first attempt called this
    # up beside _netev_table_refresh(), which runs ~170 lines earlier, so it raised NameError
    # on every cycle and the surrounding broad `except` swallowed it. The alarm against silent
    # degradation was itself silently dead; caught by its own pins.
    _netev_table_alarm(plan)
    # CONFIG VISIBILITY (2026-07-26): a knob absent from live.env takes its code default
    # silently. That class of defect hid KALSHI_THROTTLE_SMART (OFF in production) and
    # KALSHI_PRECLOSE_FLATTEN (built + tested, never switched on) while every cycle
    # printed "cycle ok". Full list into the plan row; the protection-bearing ones are
    # NAMED in the log, because a bare count is exactly as ignorable as silence was.
    _absent = env_absent()
    plan["env_absent_n"] = len(_absent)
    # DEDUP (operator-authorized 2026-08-03). This list is near-constant for a process, and it
    # dominated the row: MEASURED on the live box over plans-20260802.jsonl (707 rows), the
    # field was 1,921 B of a 4,330 B row (44.4%, 72 entries) and took exactly ONE distinct
    # value across all 707 rows. So it was ~44% of every row restating the same fact.
    # The COUNT and a stable SIGNATURE are still emitted every cycle, so a CHANGE is always
    # detectable from any row; the full list is emitted only when the signature moves (and on
    # the first row of each process, since the module global resets on restart). Absence of
    # `env_absent` therefore means "identical to the last row that carried it" — never
    # "unknown". The protection-bearing knobs are unaffected: unset _SAFETY_KNOBS are still
    # NAMED in the log every cycle, below.
    _sig9 = hashlib.sha1(",".join(_absent).encode()).hexdigest()[:12]
    plan["env_absent_sig"] = _sig9
    if _ENV_ABSENT_SIG[0] != _sig9:
        _ENV_ABSENT_SIG[0] = _sig9
        plan["env_absent"] = _absent
        plan["env_absent_changed"] = 1
    else:
        plan["env_absent_changed"] = 0
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
        _INCUMBENT_TICKERS.clear()
        _INCUMBENT_TICKERS.update(st.get("prev_standing_tickers") or [])
        # FIX P: gate-refusal streaks feed probe slot rotation (restored so a restart
        # cannot amnesty a book that refuses to quote — same doctrine as d3_first_seen).
        _PROBE_GATE_REFUSED.clear()
        _PROBE_GATE_REFUSED.update({str(k): int(v) for k, v in
                                    (st.get("probe_gate_refused") or {}).items()})
        # B-2: close clocks survive restarts — kills the warmup fail-open window.
        if not _CLOSE_TIME_CACHE and st.get("close_cache"):
            _close_cache_restore(st.get("close_cache"))
        footprint = select_footprint(progs, now)
        # L3 (operator-named 2026-07-31, series probe insurance): a NEW ticker whose series
        # already has a permanently-OUT member ($5 rung) enters PROBE-SIZED (the explore
        # clamp, EXPLORE_PROBE_CT) instead of at full join — the strike ladder is per-ticker
        # and the venue mints fresh siblings daily; this bounds the fresh sibling's first
        # burn to probe scale. Incumbent/held tickers are untouched (their unwind sizing
        # must never shrink; the explore clamp already exempts reason=='unwind').
        if EXPLORE_PROBE_CT > 0:
            # A1b (operator-ruled 2026-08-05): only convictions whose market is still
            # OPEN taint their series — settled strikes expire (see _l3_out_series).
            _out_series = _l3_out_series(st.get("mkt_out") or [], now)
            if _out_series:
                for _r5 in footprint:
                    if (not _r5.get("explore")
                            and _r5.get("ticker", "").split("-")[0] in _out_series):
                        # LIVE-EVIDENCE REVERSAL of the M6 incumbent exemption (2026-07-31
                        # 18:06-18:51Z: MLABELSHARE siblings UMG+WMG -- standing incumbents
                        # of a banned series -- churned 21 fills/19 taker at full size,
                        # -$10.88 equity in 45min). EVERY sibling of a series with a
                        # permanently-OUT member quotes probe-sized; held positions keep
                        # their full unwind (the probe clamp exempts reason=='unwind').
                        _r5["explore"] = True
                        plan["series_probe"] = plan.get("series_probe", 0) + 1
        plan.update(FP_DROPS)                 # drop reasons (empty when a test patches selection)
        plan.update(FP_SHAPE)                 # C2 selection shape, same lifecycle
        plan["programs_seen"] = len(progs)
        # FAR-CLOSE CAP ON THE MARKET CLOCK (operator Q6 decision 2026-07-28). select_footprint's
        # cap gates on the reward-PROGRAM window end — but a market can carry a short weekly
        # program while the MARKET itself resolves years out. Live 2026-07-27: KXNHPRIMARY28-28
        # (resolves 2028) passed an 8-day cap via its weekly program, was quoted, and filled
        # 20 ct @ 0.73. The horizon rule is min(program end, market close): both must be inside
        # MAX_DAYS_TO_CLOSE. close_time is static per market -> cached in-process (the daemon is
        # long-lived, so steady-state adds ~zero reads). An unreadable clock KEEPS the market
        # (counted): a transient read failure must not evacuate the whole footprint. Held
        # inventory in a dropped market still unwinds via the strand path, as with the
        # program-end cap.
        if MAX_DAYS_TO_CLOSE > 0:
            _fc_keep = []
            for _m in footprint:
                _t2 = _m["ticker"]
                _ct = _close_cache_get(_t2)
                if _ct is None:
                    try:
                        _ct = public_get(f"/trade-api/v2/markets/{_t2}"
                                         ).get("market", {}).get("close_time")
                        _close_cache_put(_t2, _ct)
                    except Exception:
                        plan["farclose_check_failed"] = plan.get("farclose_check_failed", 0) + 1
                        _fc_keep.append(_m)
                        continue
                try:
                    if bool(_ct) and parse_iso(_ct) <= now:
                        # review C-3: the belt mirrors B-1 — a past-close row that rode
                        # a selection fail-open dies here instead of quoting one cycle.
                        plan["close_past_belt"] = plan.get("close_past_belt", 0) + 1
                        continue
                except Exception:
                    pass
                try:
                    _far = bool(_ct) and parse_iso(_ct) > now + timedelta(days=MAX_DAYS_TO_CLOSE)
                except Exception:
                    _far = False
                if _far and _farclose_paying_keep(_m["ticker"].split("-")[0],
                                                  _m.get("end"), now):
                    _far = False                      # FIX H: belt honors the same exception
                if _far:
                    plan["drop_far_market_close"] = plan.get("drop_far_market_close", 0) + 1
                else:
                    _fc_keep.append(_m)
            footprint = _fc_keep
        usd_day = {m["ticker"]: m["usd_day"] for m in footprint}
        # Phase-3 unified allocation key: identical to usd_day while KALSHI_ALLOC_KEY=0.
        alloc_prio = _alloc_priority(footprint, now, usd_day)

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
        # governed-market set: initialized HERE, before the mode branch, because the quote loop
        # reads it unconditionally — initializing it inside the live branch broke every dry-run
        # cycle with UnboundLocalError (self-audit F18, 2026-07-29, same-day regression of the
        # loss-governor commit). Dry-run keeps it empty: no fills, nothing to govern.
        _exit_only_mkts = set()
        _exit_only_all = False               # F2/F3: whole-book reduce-only for this cycle
        if client.mode == "dry_run":
            standing = st.get("simulated_standing", {})
            own = {}
            # F5 blind review #1 (BLOCKER): simulated orders were NEVER PLACED, so the public
            # book never contained them — subtracting them removes RIVAL depth and inflates
            # every dark-mode share/capture number in the anti-conservative direction (the
            # same invariant the `own = {}` line above already enforces for counts).
            _f5_standing = {}
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
            _f5_standing = standing        # live: the book really contains these orders
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
            _BLACKOUT_BACKOFF[:] = [0, 0.0]           # J5: healthy reads re-arm an instant retry
            # PER-MARKET LOSS GOVERNORS (see config block): build the exit-only set for this
            # cycle (initialized empty above the mode branch). Governor faults fail OPEN (a
            # parse fault must not block trading — the global halts still stand behind them).
            _day_mkt = now.strftime("%Y-%m-%d")
            # Blind-review D2 (2026-07-31): ALREADY-KNOWN bans are enforced HERE, before and
            # outside the governor try — a fault in the day-governor body used to skip the
            # _exit_only_mkts union entirely, so permanently-OUT markets quoted full
            # accumulating size for up to 2 cycles until the fail-closed streak caught it.
            # The governor block below only ADDS this cycle's new trips. Day-latches are
            # enforced only for TODAY's state (a rolled day reopens them, same as the body).
            try:
                _exit_only_mkts |= _ban_set(st.get("mkt_out")) | _mkt_out_backup_union(set())
                if st.get("mkt_realized_day") == _day_mkt:
                    _exit_only_mkts |= _ban_set(st.get("mkt_loss_tripped"))
            except Exception:
                _SILENT["mkt_out_enforce_fail"] += 1
            if MKT_DAY_LOSS_EXITONLY_USD > 0:
                try:
                    # BURN-AND-RUN ROOT FIX (operator-named 2026-07-31): the governor's feed is
                    # a dedicated ALL-TRADED read (count_filter=total_traded), NOT the open-
                    # positions side channel. count_filter=position drops a market the cycle it
                    # goes fully flat, so a market that burned and flattened within one cycle
                    # realized 5x the threshold with zero trips/strikes on record
                    # (KXMLABELSHARE-W3026JUL30-SME, -$25.76 venue-attributed). Flat rows keep
                    # realized_pnl_dollars in the total_traded read (probe 13:15:36Z 07-31).
                    # Read failure falls back to the side channel — never worse than the old
                    # feed, and the governor's fail-OPEN doctrine is unchanged.
                    # Everything the governor knew BEFORE this cycle. Captured here because the
                    # successful-read branch below overwrites st["realized_last_good"], and a
                    # ticker that departs is by definition absent from the fresh read — so the
                    # carry can only be computed against prior knowledge, never against the
                    # post-departure view.
                    _prev_known = dict(_REALIZED_LAST_GOOD) or dict(
                        st.get("realized_last_good") or {})
                    _feed_ok = True
                    try:
                        _realized = client.get_realized_by_market()
                        _REALIZED_LAST_GOOD.clear()
                        _REALIZED_LAST_GOOD.update(_realized)
                        # Blind-review D3 (2026-07-31): the last-good snapshot must survive a
                        # daemon restart, or a deploy during a feed outage re-opens the
                        # burn-and-run blind spot (fallback would be the flat-dropping side
                        # channel). One float per traded ticker — tiny.
                        st["realized_last_good"] = dict(_realized)
                    except Exception:
                        _feed_ok = False
                        # F3 (2026-07-31): prefer the LAST-GOOD all-traded snapshot -- the
                        # open-positions side channel drops flat markets (the burn-and-run
                        # blind spot) and is only the bootstrap fallback. Re-review M4: this
                        # degradation was SILENT -- now counted, surfaced, and feeding the
                        # same fail-closed streak as any other governor fault.
                        _realized = (dict(_REALIZED_LAST_GOOD)
                                     or dict(st.get("realized_last_good") or {})
                                     or dict(_REALIZED_BY))
                        _SILENT["realized_feed_fail"] += 1
                        plan["realized_feed_fallback"] = 1
                        st["gov_fail_streak"] = int(st.get("gov_fail_streak", 0)) + 1
                        if st["gov_fail_streak"] >= 3:
                            _exit_only_all = True
                            plan["governor_fail_reduce_only"] = 1
                    # FEED RECONCILIATION (defect 5a root fix, 2026-08-02). The feed is a
                    # /portfolio/positions read, and that endpoint NEVER emits a SETTLED market
                    # under any parameter — count_filter=total_traded keeps FLAT-but-alive rows
                    # (the 07-31 burn-and-run fix, correct and unchanged) but has no effect on
                    # settled ones. So a ticker's realized loss VANISHED from the governor the
                    # moment it settled, and the overwrite above erased it from the in-memory
                    # snapshot, the persisted snapshot and the fallback source in one statement.
                    # Live 2026-08-02: the largest single loss of the day (KXTEMPAUSH, -$9.4939
                    # all-in) was absent from the governor's realized read entirely.
                    # A departed ticker's last known value is CARRIED for the rest of the day.
                    # FRESH ALWAYS WINS, so a live ticker is never shadowed by a stale carry.
                    # This restores measurement; it cannot by itself produce a trip the governor
                    # did not already have the data to produce, because a carried value is
                    # FROZEN at its last observed level (the settlement top-up that can move it
                    # is the separate B4b commit).
                    _realized_raw = dict(_realized) if _feed_ok else {}
                    # EXPOSURE SNAPSHOT (defect 5b): what a still-alive market would cost us if
                    # it settled worthless. Built from the reads this cycle ALREADY did — no
                    # extra I/O. A ticker that later departs keeps its last value, which is the
                    # frozen pre-settlement cost, and that is the only correct basis for the
                    # settlement delta. (The settlement row's own yes/no_total_cost_dollars are
                    # GROSS LIFETIME costs on both legs and are NOT a P&L basis — see
                    # maker_kalshi_client.get_settlements.)
                    try:
                        _expo = dict(st.get("mkt_exposure") or {})
                        _expo_ct = dict(st.get("mkt_exposure_ct") or {})
                        for _te in _realized_raw:
                            _ct_te = abs(float(held_by.get(_te, 0.0) or 0.0))
                            if _te in cost_by:
                                _expo[_te] = round(float(cost_by[_te]) * _ct_te, 6)
                            elif _te in held_by:
                                _expo[_te] = None      # venue omitted the cost field -> unknown
                            else:
                                _expo[_te] = 0.0       # flat but alive: nothing left to lose
                            # Contract count alongside the dollars: the settlement re-base
                            # below needs to know how many contracts that exposure was for.
                            _expo_ct[_te] = _ct_te
                        st["mkt_exposure"] = _expo
                        st["mkt_exposure_ct"] = _expo_ct
                    except Exception:
                        _SILENT["settle_expo_fail"] += 1
                        _expo = dict(st.get("mkt_exposure") or {})
                        _expo_ct = dict(st.get("mkt_exposure_ct") or {})
                    # SETTLEMENT TOP-UP. Own nested try: a settlements outage must never raise
                    # into the governor try, and per operator decision 2026-08-02 it does NOT
                    # feed gov_fail_streak — this feed is ADDITIVE protection, and a new
                    # outage->halt path on it would be a worse trade than the blind spot.
                    # Self-healing: on failure the watermark does not advance, so the next good
                    # read catches up with zero data loss.
                    _settle_pnl = dict(st.get("mkt_settle_pnl") or {})
                    try:
                        _wm = int(st.get("settle_watermark") or 0)
                        _srows = (client.get_settlements(min_ts=_wm or None)
                                  .get("settlements") or [])
                        _wm_new = _wm
                        for _sr in _srows:
                            try:
                                _sts = int(parse_iso(str(_sr.get("settled_time"))).timestamp())
                            except Exception:
                                _SILENT["settle_ts_unparsed"] += 1
                                continue
                            if _sts <= _wm:
                                continue
                            _wm_new = max(_wm_new, _sts)
                            _st4 = _sr.get("ticker")
                            _e4 = _expo.get(_st4, "__missing__")
                            if _e4 is None or _e4 == "__missing__":
                                # Never fabricate a permanent ban on an unknown basis.
                                _SILENT["settle_exposure_unknown"] += 1
                                continue
                            # RE-BASE ONTO WHAT ACTUALLY SETTLED (blind-review fix 2026-08-03).
                            # mkt_exposure is refreshed ONLY inside this governor block, and the
                            # STOP branch returns at :2998 before load_state() at :3002 — so
                            # during a halt _flatten_all sells inventory while the exposure
                            # basis stays FROZEN at its last pre-halt value. Settling on that
                            # stale basis understates P&L by exactly the sale proceeds, always
                            # biased toward a phantom LOSS: a fully flattened ticker would
                            # compute delta = -(full pre-halt exposure) and mint a PERMANENT
                            # ban on a market whose true day P&L was ~$0. Permanent bans write
                            # two files and drag every series sibling to probe size, so this
                            # must never fire on a stale basis.
                            # The settlement row carries the counts that actually settled (both
                            # fields present 127/127, read 2026-08-02T02:30:57Z), so re-base:
                            # NOTE (corrected 2026-08-03): "no count basis -> skip" describes a
                            # MISSING SNAPSHOT (_ct04), not missing venue fields. A settlement
                            # row lacking yes_count_fp/no_count_fp coerces to 0.0 - 0.0 and
                            # takes the `_net4 <= 0` arm, recording a ZERO delta rather than
                            # skipping. That is the safe direction — a row we cannot size
                            # cannot mint a ban — and both fields are present 127/127 on the
                            # live tape, but the two cases are NOT the same and were described
                            # as one.
                            _net4 = abs(float(_sr.get("yes_count_fp") or 0.0)
                                        - float(_sr.get("no_count_fp") or 0.0))
                            _ct04 = float((_expo_ct or {}).get(_st4, 0.0) or 0.0)
                            if _net4 <= 0.0:
                                _e4 = 0.0              # nothing survived to settlement
                            elif _ct04 <= 0.0:
                                # A position settled that we never saw held: no basis to
                                # prorate against, so do not ban on it.
                                _SILENT["settle_exposure_unknown"] += 1
                                continue
                            elif _net4 < _ct04:
                                _e4 = float(_e4) * (_net4 / _ct04)   # prorate to the residual
                            # _net4 >= _ct04: the position rode into expiry intact — the
                            # operator-named case where a settlement loss MAY ban. Basis stands.
                            # ASSIGN, never accumulate -> re-processing a row is idempotent.
                            _settle_pnl[_st4] = round(
                                float(_sr.get("revenue") or 0.0) / 100.0 - float(_e4), 6)
                            plan["settle_topups"] = plan.get("settle_topups", 0) + 1
                            plan["settle_topup_usd"] = round(
                                plan.get("settle_topup_usd", 0.0) + min(0.0, _settle_pnl[_st4]), 4)
                        st["settle_watermark"] = _wm_new
                    except Exception:
                        _SILENT["settle_feed_fail"] += 1
                        plan["settle_feed_fail"] = 1
                    st["mkt_settle_pnl"] = _settle_pnl
                    _departed = {}
                    try:
                        # Everything known before this cycle, prior carry included, minus
                        # whatever the fresh read still shows = the tickers that left.
                        _known = dict(_prev_known)
                        _known.update(st.get("mkt_realized_carry") or {})
                        _departed = {t: v for t, v in _known.items()
                                     if t not in _realized_raw}
                        if _departed:
                            # The top-up applies to the EVALUATION copy only. _departed itself
                            # is persisted as the carry, and it must stay at the pre-settlement
                            # realized value — adding the delta into it would re-add the same
                            # delta on every subsequent cycle and compound a one-off settlement
                            # into an unbounded loss.
                            _eval_dep = {t: v + _settle_pnl.get(t, 0.0)
                                         for t, v in _departed.items()}
                            _merged = dict(_eval_dep)
                            _merged.update(_realized)     # fresh always wins
                            _realized = _merged
                    except Exception:
                        _SILENT["realized_carry_fail"] += 1
                        _departed = {}
                    _base = st.get("mkt_realized_base") or {}
                    _tripped = _ban_set(st.get("mkt_loss_tripped"))
                    if "mkt_out" in st:
                        _out4 = _ban_set(st.get("mkt_out"))
                    else:
                        # E-migration (operator "3$ then 5$ then out"): markets banned under
                        # the earlier one-strike-out rule stay banned -- grandfather every
                        # struck ticker into the permanent OUT set exactly once.
                        _out4 = _ban_set(st.get("mkt_strike_hist") or {})
                    _snap = st.get("mkt_trip_snap") or {}
                    # Inventory held AT the moment of the trip — the basis for the unwind
                    # allowance (defect 4). Per-day, cleared with the other ladder state on the
                    # roll. The latch is the EXISTING UTC calendar day (mkt_realized_day),
                    # operator-named 2026-08-02; no rolling-24h state is introduced.
                    _inv4 = st.get("mkt_trip_inv") or {}
                    if st.get("mkt_realized_day") != _day_mkt:
                        st["mkt_realized_day"] = _day_mkt
                        # Yesterday's departed tickers do not exist today: drop them BEFORE
                        # re-baselining, or they persist in mkt_realized_base forever and the
                        # carry write below would immediately undo this clear. Only when the
                        # feed actually read this cycle — otherwise _realized is the fallback
                        # snapshot and must be left alone.
                        if _feed_ok:
                            _realized = dict(_realized_raw)
                        _departed = {}                   # nothing carries across the roll
                        # Settlement deltas are per-day for the same reason the carry is: the
                        # ladder latches on the UTC calendar day (operator-named 2026-08-02),
                        # so yesterday's settlement must not count toward today's rungs.
                        # The watermark is deliberately NOT reset — it is a feed cursor, not
                        # ladder state, and rewinding it would re-process settled rows forever.
                        _settle_pnl = {}
                        st["mkt_settle_pnl"] = {}
                        st["mkt_exposure"] = {t: v for t, v in (st.get("mkt_exposure") or {}).items()
                                              if t in _realized_raw}
                        st["mkt_exposure_ct"] = {t: v for t, v
                                                 in (st.get("mkt_exposure_ct") or {}).items()
                                                 if t in _realized_raw}
                        _base = dict(_realized)          # fresh day -> fresh baseline
                        _tripped = set()                 # trips are per-day
                        _snap = {}                       # M3: trip snapshots are per-day too
                        _inv4 = {}                       # trip inventory is per-day too
                        st["mkt_realized_carry"] = {}    # departed-ticker carry is per-day too
                        _TAKER_XN.clear()                # paid-exit counts are per-day too
                    for _t4, _v4 in _realized.items():
                        # first seen mid-day -> baseline NOW: lifetime realized from prior days
                        # must not trip today's governor (fail-open by construction)
                        _base.setdefault(_t4, _v4)
                        _d4 = _v4 - _base[_t4]
                        # M3 (operator-clarified 2026-07-31: "3 timeout then open up and if 5
                        # then out for good"): losses realized WHILE serving the $3 timeout
                        # (our own forced exits) must not escalate to the $5 permanent rung.
                        # ROOT FIX 2026-08-02 (defect 4): this used to judge rung 2 on the loss
                        # FROZEN at the trip, which made the $5 rung UNREACHABLE for any market
                        # that kept bleeding after tripping — live KXRAIN-26AUG02-CHI froze at
                        # -$1.28 and finished the day at -$7.29 without ever being banned.
                        # Rung 2 now judges the LIVE day-delta plus a bounded ALLOWANCE that
                        # refunds only the liquidation cost, so M3's intent survives without its
                        # blind spot: a market that genuinely loses $5 today goes out even if
                        # the last dollars arrived on the way out; it only gets credit for the
                        # spread it paid to leave. A one-shot burn arriving already at/past $5
                        # still goes OUT immediately (untripped -> zero allowance). Per-day
                        # state resets at the day roll -- after reopening, fresh counting.
                        _was_tripped4 = _t4 in _tripped
                        _cap4 = max(0.0, MKT_OUT_LOSS_USD - MKT_DAY_LOSS_EXITONLY_USD)
                        _allow4 = 0.0
                        if _was_tripped4:
                            if _t4 in _inv4:
                                _allow4 = min(_cap4, MKT_UNWIND_ALLOW_PER_CT * _inv4[_t4])
                            else:
                                # Tripped on an earlier build (or state predating this fix), so
                                # no inventory basis exists. Fail toward the OLD behaviour — the
                                # full rung gap — rather than banning on an allowance we cannot
                                # justify. Counted so the gap is visible, never silent.
                                _allow4 = _cap4
                                plan["trip_inv_missing"] = plan.get("trip_inv_missing", 0) + 1
                        _eff4 = _d4 + _allow4
                        if _d4 <= -MKT_DAY_LOSS_EXITONLY_USD:
                            _tripped.add(_t4)            # LATCH: stays for the day even if flat
                            _snap.setdefault(_t4, _d4)   # freeze the pre-timeout loss
                            # Basis for this market's allowance: inventory held AT the trip.
                            # held_by is this cycle's signed position, bound above and not
                            # mutated until the quote loop.
                            _inv4.setdefault(_t4, abs(float(held_by.get(_t4, 0.0) or 0.0)))
                        if MKT_OUT_LOSS_USD > 0 and _eff4 <= -MKT_OUT_LOSS_USD:
                            if _t4 not in _out4:
                                plan["mkt_out_rung2"] = plan.get("mkt_out_rung2", 0) + 1
                                print(f"WARNING mkt_out RUNG 2: {_t4} day-delta ${_d4:.2f} "
                                      f"(trip snapshot ${_snap.get(_t4, _d4):.2f}, unwind "
                                      f"allowance ${_allow4:.2f} on {_inv4.get(_t4, 0):.0f} ct) "
                                      f"<= -${MKT_OUT_LOSS_USD:.2f} — permanently OUT")
                            _out4.add(_t4)               # $5 rung: OUT -- permanent, no expiry
                        if (TAKER_GOV_CROSSES > 0
                                and _TAKER_XN.get(_t4, 0) >= TAKER_GOV_CROSSES
                                and _d4 <= -TAKER_GOV_LOSS_USD):
                            if _t4 not in _tripped:
                                _tripped.add(_t4)        # behavioral trip: paying to leave x3
                                _snap.setdefault(_t4, _d4)
                                _inv4.setdefault(_t4, abs(float(held_by.get(_t4, 0.0) or 0.0)))
                                plan["taker_gov_tripped"] = plan.get("taker_gov_tripped", 0) + 1
                    # Persist the carry — ONLY when the feed read succeeded, or a single outage
                    # would freeze every live ticker's value into the carry and shadow the real
                    # feed once it recovers. Bounded: only TRADED tickers ever enter the feed
                    # (27 were ever filled on 08-02, master plan §4), so this is tens of floats.
                    if _feed_ok:
                        try:
                            _new_carry = dict(_departed)
                            if len(_new_carry) > REALIZED_CARRY_MAX:
                                # keep the MOST NEGATIVE — the ones that can still trip a rung
                                _kept = sorted(_new_carry.items(),
                                               key=lambda kv: kv[1])[:REALIZED_CARRY_MAX]
                                _new_carry = dict(_kept)
                                _SILENT["realized_carry_capped"] += 1
                            st["mkt_realized_carry"] = _new_carry
                            plan["realized_carried"] = len(_new_carry)
                            plan["realized_carried_usd"] = round(
                                sum(min(0.0, v) for v in _new_carry.values()), 4)
                        except Exception:
                            _SILENT["realized_carry_fail"] += 1
                    st["mkt_realized_base"] = _base
                    st["mkt_loss_tripped"] = sorted(_tripped)
                    _bk4 = _mkt_out_backup_union(_out4)     # amnesty guard (separate file)
                    _re4 = _bk4 - _out4
                    if _re4:
                        # Blind-review D4 (2026-07-31): an operator clearing quoter_state's
                        # mkt_out WITHOUT editing mkt_out_backup.json gets the ban silently
                        # re-applied — say it out loud so the un-ban procedure failure is
                        # visible, not mistaken for a standing ban.
                        print(f"WARNING mkt_out amnesty guard RE-APPLIED {sorted(_re4)} from "
                              f"mkt_out_backup.json — clearing a ban requires editing BOTH "
                              f"quoter_state.json AND mkt_out_backup.json")
                    _out4 |= _bk4
                    st["mkt_out"] = sorted(_out4)
                    st["mkt_trip_snap"] = _snap
                    st["mkt_trip_inv"] = _inv4
                    st["mkt_taker_xn"] = dict(_TAKER_XN)
                    _exit_only_mkts |= _tripped | _out4
                    # Gov-D5 (1.1 review 2026-07-31): the L3 sibling probe clamp ran at
                    # SELECTION time against LAST cycle's mkt_out — a market banned at the
                    # $5 rung THIS cycle left its series siblings quoting full-size for one
                    # extra cycle (the exact MLABELSHARE burn window L3 exists to bound).
                    # Re-apply the clamp here, after this cycle's bans are written and
                    # before the quote loop reads footprint. Same rules as L3: unwind
                    # sizing is exempt via the probe clamp's reason=='unwind' carve-out.
                    # Own guard (blind review 2026-08-01, lens A #5 / lens B #4): this is a
                    # telemetry-grade clamp inside the governor try — without isolation a
                    # corrupt mkt_out entry (non-string, hand-edited state) would bump
                    # gov_fail_streak and flip the whole book reduce-only after 3 cycles.
                    if EXPLORE_PROBE_CT > 0:
                        try:
                            # A1b applies HERE too (deploy-verify 2026-08-05: fixing only
                            # the selection-time site left series_probe=12 — THIS re-clamp
                            # rebuilt the taint raw every cycle): settled convictions do
                            # not probe-size their siblings.
                            _out_series5 = _l3_out_series(sorted(_out4), now)
                            for _r7 in footprint:
                                if (not _r7.get("explore")
                                        and _r7.get("ticker", "").split("-")[0] in _out_series5):
                                    _r7["explore"] = True
                                    plan["series_probe"] = plan.get("series_probe", 0) + 1
                        except Exception:
                            _SILENT["series_probe_reclamp_fail"] += 1
                    plan["loss_exitonly"] = len(_tripped)
                    if _out4:
                        plan["mkt_out"] = len(_out4)
                    if TWO_STRIKES:
                        _hist, _strike2 = _two_strikes(st.get("mkt_strike_hist") or {},
                                                       _tripped, _day_mkt, now)
                        st["mkt_strike_hist"] = _hist
                        _exit_only_mkts |= _strike2
                        if _strike2:
                            plan["strike2_exitonly"] = len(_strike2)
                except Exception:
                    _SILENT["loss_governor_fail"] += 1
                    st["gov_fail_streak"] = int(st.get("gov_fail_streak", 0)) + 1
                    if st["gov_fail_streak"] >= 3:
                        # F3 (operator-named 2026-07-31): three consecutive faulted governor
                        # cycles -> reduce-only until a clean pass (fail-open became a
                        # standing blind spot; unwinds/exits untouched)
                        _exit_only_all = True
                        plan["governor_fail_reduce_only"] = 1
                else:
                    if not plan.get("realized_feed_fallback"):
                        st["gov_fail_streak"] = 0    # M4: a feed-fallback cycle is NOT clean
            if MKT_DAY_LOSS_EXITONLY_USD <= 0:
                # coverage fix: PERMANENT bans must not die when the day-governor knob is
                # zeroed -- enforce mkt_out (incl. the amnesty backup) unconditionally.
                try:
                    _out4b = _ban_set(st.get("mkt_out")) | _mkt_out_backup_union(set())
                    if _out4b:
                        st["mkt_out"] = sorted(_out4b)
                        _exit_only_mkts |= _out4b
                        plan["mkt_out"] = len(_out4b)
                except Exception:
                    _SILENT["mkt_out_enforce_fail"] += 1
            if REENTRY_COOLDOWN_S > 0:
                try:
                    _cool = st.get("reentry_cool") or {}
                    _active = {}
                    for _t4, _exp in _cool.items():
                        try:
                            if parse_iso(_exp) > now:
                                _active[_t4] = _exp      # still cooling; expired ones drop
                        except Exception:
                            # FAIL CLOSED (self-audit F19, 2026-07-29): forgetting an
                            # unparseable stamp re-opened the exact re-entry this governor
                            # exists to block. Re-stamp a fresh full cooldown instead — the
                            # market stays exit-only and the clock self-heals.
                            _active[_t4] = (now + timedelta(
                                seconds=REENTRY_COOLDOWN_S)).isoformat()
                    st["reentry_cool"] = _active
                    _exit_only_mkts |= set(_active)
                    plan["reentry_cooldown"] = len(_active)
                except Exception:
                    _SILENT["loss_governor_fail"] += 1
            # ANY-LOSS COOLDOWN SHADOW (operator-named 4b, 2026-08-01): watch-only pricing of
            # the sub-rung drip gap. Reads the governor's own st keys (post-write, so trips/
            # bans/cooldowns are this cycle's truth) and writes ONLY st["anyloss_shadow"] +
            # plan telemetry. A shadow fault must never fault the real governor.
            if MKT_DAY_LOSS_EXITONLY_USD > 0:
                try:
                    _anyloss_shadow(st,
                                    _realized,
                                    st.get("mkt_realized_base") or {},
                                    set(st.get("mkt_loss_tripped") or []),
                                    set(st.get("mkt_out") or []),
                                    # cooldown OFF -> nothing is "cooling": the raw state key
                                    # is unpruned in that config and would misclassify trips
                                    # as redundant (blind review 2026-08-01 lens B #3)
                                    (set(st.get("reentry_cool") or {})
                                     if REENTRY_COOLDOWN_S > 0 else set()),
                                    now, _day_mkt, plan)
                except Exception:
                    _SILENT["anyloss_shadow_fail"] += 1
            # VELOCITY CIRCUIT BREAKER: compare held-$ now vs the LOWEST held-$ inside the
            # window. Rapid growth = adverse accumulation -> the whole book goes REDUCE-ONLY
            # below (only unwind quotes survive; accumulating quotes cancelled by the diff).
            # Self-releasing: reduce-only stops the growth, the window slides, the gate clears.
            # DAILY LOSS KILL (treadmill guard): drop beyond the daily budget -> write STOP
            # (operator must clear it) + maker-first flatten NOW. Balance read failure only
            # skips this check (primary reads above remain fail-closed).
            # MARK-TO-MARKET (operator decision 2026-07-28, replacing cost basis). The cost-basis
            # meter's own comment admitted "KNOWN GAP: open (unrealized) losses stay invisible
            # until settlement" — live 2026-07-27 it read -$1.00 while the marked number was
            # ~-$22, and the operator was told "flat" three times. Held inventory is now marked
            # at LIQUIDATION value from the same books the cycle already reads (mirror-served
            # under the daemon, so ~zero extra REST): long yes -> |pos| x best YES bid, long no
            # -> |pos| x best NO bid. Per-ticker fallback to COST on an unreadable/one-sided
            # book, and a whole-marking fallback to the old cost-basis meter on any error —
            # a marking bug must never DISARM the halt (counted, not swallowed).
            # We still do NOT read the venue's portfolio_value: whether it includes cash is
            # unverified (re-affirmed 2026-07-23); our own books are ground truth we control.
            _equity = None
            _eq_consistent = False
            try:
                # ONE get_balance() call on the happy path; free_cash reused by the funding gate
                # below (no second fetch — the torn-read retry may add one, mismatch-only). A
                # raise leaves free_cash None (init above) -> funding gate fails closed.
                free_cash = float(client.get_balance().get("balance_dollars") or 0)
                # TORN-READ ROOT FIX (defect 1 of the 08-02 post-mortem, operator-named
                # 2026-08-02). The cycle's positions were read ~230 lines (and several REST
                # calls) earlier; a fill or settlement landing between that read and this
                # balance read is seen by ONE half only, so the summed equity LEVEL is fiction
                # for exactly one cycle (08-02: $23.70 of the $68.68 halt reading — 34.51% —
                # was this artifact, round-tripping to the cent on the next row). The peak
                # ratchet makes the UPWARD tear permanent: a sell landing between the reads
                # double-counts (cash up AND the sold position still marked), inflating
                # equity_day_peak forever and manufacturing false drawdown from then on.
                # Fix: CONFIRM the snapshot. Re-read positions after the balance read —
                # identical position digests bracketing the balance read prove no fill landed
                # between them. On mismatch, one bounded retry (fresh balance + third positions
                # read). Still unstable -> the cycle's equity level is TORN: telemetry still
                # emits, but day-seed/peak/drawdown arithmetic and the halt decision are
                # skipped (counted + WARNED, never silent), and 2 consecutive torn cycles stop
                # acquisition (mirror of the balance-fail F2 rule). Credits and deposits move
                # balance without touching positions, so genuine income still lands as a
                # consistent equity rise by construction.
                def _pos_digest(_hb, _cb):
                    return tuple(sorted((_t, round(float(_p), 4),
                                         round(float(_cb.get(_t, 0.0)), 6))
                                        for _t, _p in _hb.items()))
                _eq_held_cost, _eq_held_by, _eq_cost_by = held_cost, held_by, cost_by
                try:
                    _hc2, _hb2, _cb2 = _held_cost(client)
                    if _pos_digest(_hb2, _cb2) == _pos_digest(held_by, cost_by):
                        _eq_consistent = True
                        _eq_held_cost, _eq_held_by, _eq_cost_by = _hc2, _hb2, _cb2
                    else:
                        plan["equity_snapshot_retried"] = 1
                        free_cash = float(client.get_balance().get("balance_dollars") or 0)
                        _hc3, _hb3, _cb3 = _held_cost(client)
                        _eq_held_cost, _eq_held_by, _eq_cost_by = _hc3, _hb3, _cb3
                        if _pos_digest(_hb3, _cb3) == _pos_digest(_hb2, _cb2):
                            _eq_consistent = True
                except Exception as _te:
                    # Re-read failed -> cannot PROVE consistency -> torn path (fail safe).
                    plan["equity_reread_failed"] = repr(_te)[:120]
                _marked, _mark_fb = 0.0, 0
                # LIVE PER-MARKET INVENTORY METER (defect 2, Phase C1). The loss ladder is fed
                # by the venue's realized_pnl_dollars, which is 0.0 for as long as a position is
                # OPEN — so a market can bleed all day and the governor sees nothing until it
                # closes. Live 2026-08-02 KXTEMPAUSH was first seen at -$9.00 having never
                # tripped the $3 rung: two lifetime fills, 108.81s apart, the whole loss
                # realized in one round trip.
                # This is MEASUREMENT ONLY and gates nothing. Per the plan's own framing, a
                # live meter buys honest measurement and earlier cross-market visibility, NOT
                # loss limitation on a one-tick adverse fill — the reduce path had already
                # flipped in the same cycle, so a rung firing on an unrealized mark would have
                # banned a market that was already exiting. Whether unrealized should FEED the
                # rungs is an operator policy call, deliberately not taken here.
                _unreal = {}
                try:
                    for _t, _p in _eq_held_by.items():
                        if not _p:
                            continue
                        _mv = None
                        try:
                            _ob = _get_book(_t) or {}
                            if _p > 0:
                                _bb = max((pr for pr, _ in _levels(_ob.get("yes_dollars") or [])[0]),
                                          default=None)
                            else:
                                _bb = max((pr for pr, _ in _levels(_ob.get("no_dollars") or [])[0]),
                                          default=None)
                            if _bb is not None:
                                _mv = abs(_p) * _bb
                        except Exception:
                            _mv = None
                        if _mv is None:
                            _mv = abs(_p) * float(_eq_cost_by.get(_t, 0.0))   # fallback: cost
                            _mark_fb += 1
                        else:
                            # Only a REAL mark carries signal. The cost fallback would report
                            # exactly 0.00 unrealized by construction, which is not a
                            # measurement of anything — recording it would dilute the gauge
                            # with markets we could not price.
                            _unreal[_t] = _mv - abs(_p) * float(_eq_cost_by.get(_t, 0.0))
                        _marked += _mv
                    _equity = free_cash + _marked
                    plan["equity_mark_usd"] = round(_equity, 2)
                    # The C1 gauge. ALWAYS emitted (A3 doctrine): 0 must mean "measured, flat",
                    # never "we did not look". A market priced only by the cost fallback is
                    # excluded from _unreal and shows up in mark_fallback_tickers instead.
                    _worst_t, _worst_v = "", 0.0
                    for _ut, _uv in _unreal.items():
                        if _uv < _worst_v:
                            _worst_t, _worst_v = _ut, _uv
                    # DID THE METER ACTUALLY RUN? A3 seeds the gauges to 0, which is the right
                    # default everywhere else but hides exactly the distinction this gauge
                    # exists to make: a 0 from "measured, nothing underwater" and a 0 from
                    # "the mark block never ran" are different facts. Found by mutation
                    # 2026-08-03 — a mutant that emitted the gauges only when non-empty passed
                    # the suite, because the seed supplied the 0 either way.
                    # This flag is seeded 0 and set 1 ONLY here, so measured==1 means the
                    # numbers beside it were computed this cycle.
                    plan["mkt_unreal_measured"] = 1
                    plan["mkt_unreal_n"] = len(_unreal)
                    plan["mkt_unreal_usd"] = round(sum(_unreal.values()), 4)
                    plan["mkt_unreal_neg_usd"] = round(
                        sum(v for v in _unreal.values() if v < 0), 4)
                    plan["mkt_unreal_worst_usd"] = round(_worst_v, 4)
                    plan["mkt_unreal_worst"] = _worst_t
                    # LOUD at the ladder's own thresholds — visibility, not enforcement. This is
                    # the line that would have named KXTEMPAUSH while it was still open.
                    if MKT_DAY_LOSS_EXITONLY_USD > 0 and _worst_v <= -MKT_DAY_LOSS_EXITONLY_USD:
                        print(f"WARNING UNREALIZED {_worst_t} {_worst_v:+.2f} at/below the "
                              f"${MKT_DAY_LOSS_EXITONLY_USD:.2f} rung — the ladder cannot see "
                              f"this yet (it reads REALIZED, which is 0 while the position is "
                              f"open); measurement only, nothing gated")
                    if _eq_consistent:
                        # A TORN level must not poison the portfolio-tracking cap either —
                        # keep the last-good value on torn cycles (Gov-D10 semantics).
                        _TOTAL_CAP_EFF[0] = _equity      # portfolio-tracking total cap refresh
                        plan["total_cap_eff"] = round(_total_cap(), 2)
                    if _mark_fb:
                        plan["mark_fallback_tickers"] = _mark_fb
                except Exception as _me:
                    plan["mark_failed"] = repr(_me)[:120]
                    _equity = free_cash + _eq_held_cost   # old meter, never disarmed
                    if _TOTAL_CAP_EFF[0] is None:
                        # Gov-D10 (1.1 review 2026-07-31): fresh process + lost state
                        # (equity_prev gone) + a failed mark read left the portfolio-
                        # tracking equity None for the cycle — the SERIES_PCT family cap
                        # degraded to static-only (or fully OFF in a pure-PCT config,
                        # since cap_desired treats cap<=0 as no gate). Cost basis is a
                        # worse mark but a far better denominator than nothing. A
                        # last-good (non-None) value is never overwritten here.
                        _TOTAL_CAP_EFF[0] = _equity
                        plan["total_cap_seeded_cost_basis"] = 1
                # ONE-TIME BASIS MIGRATION: day baselines written by the cost-basis meter would
                # register the bid-ask spread as an instant "loss" under marks. Re-baseline ONCE
                # on the definition change (this is not a deposit re-baseline; the deposit-proof
                # property of dd/down is unchanged thereafter).
                if st.get("equity_basis") != "mark":
                    st["equity_basis"] = "mark"
                    st["equity_day"] = None               # forces the new-day seed below
            except Exception as e:
                plan["balance_read_failed"] = repr(e)[:120]
                st["balance_fail_streak"] = int(st.get("balance_fail_streak", 0)) + 1
                plan["balance_fail_streak"] = st["balance_fail_streak"]
                # the daily-loss kill is DISARMED while this persists — every other guard read
                # fails closed with a WARNING; this one used to fail open silently.
                print(f"WARNING balance read failed x{st['balance_fail_streak']} ({e!r}) — "
                      f"DAILY LOSS KILL DISARMED this cycle")
                if st["balance_fail_streak"] >= 2:
                    # F2 (operator-named 2026-07-31): the last-resort loss guard is blind --
                    # stop ACQUIRING until it can see again. Unwinds/exits are untouched.
                    _exit_only_all = True
                    plan["balance_fail_reduce_only"] = 1
            else:
                st["balance_fail_streak"] = 0
            if _equity is not None:
                # DRAWDOWN, NOT DROP-FROM-DAY-START (defect fixed 2026-07-23). Measuring against
                # a FROZEN day-start let INCOME inflate the quota: every reward credit raised the
                # numerator while the baseline stood still, so the room to lose grew monotonically
                # all day. Measured live 07-23: equity $99.76 vs day_start $63.34 => the halt only
                # tripped at $23.34 — $76.42 of effective room against a nominal $40 quota (1.91x),
                # i.e. 76% of an $85 account could evaporate first. The drawdown meter is immune
                # to income and to deposits:
                #   dd   = drawdown from the intraday HIGH-WATER MARK. A credit/deposit lifts the
                #          peak by the same amount it lifts equity, so it buys ZERO extra room.
                # (The second arm — 'down', the RATCHETING cumulative sum of per-cycle equity
                # decreases, the 07-22 treadmill guard — was REMOVED by operator order 2026-08-02
                # after the 08-02 halt post-mortem; see the config block at DAILY_LOSS_HALT_USD.
                # Its state keys equity_day_down / equity_prev_cost / down_basis are no longer
                # read or written; stale copies in an old state file are ignored harmlessly.)
                # Peak seeds from equity_day_start so a PRE-fix state file migrates with the old
                # drop-from-day-start behaviour intact as a FLOOR: the meter is >= the old one on
                # every input, never weaker.
                _day = now.strftime("%Y%m%d")
                if not _eq_consistent:
                    # TORN CYCLE (defect 1 root fix): the equity LEVEL is untrusted — no
                    # day-seed, no peak move, no halt verdict off it. A false peak would poison
                    # every later cycle of the day (the ratchet never un-inflates); a false dip
                    # would halt on fiction. Loud + counted, never silent; 2 consecutive torn
                    # cycles stop ACQUIRING (mirror of the balance-fail F2 rule — unwinds and
                    # exits untouched). The breach-confirmation window is FROZEN on torn cycles,
                    # not fed a 0, so a real sustained breach interleaved with tears is not
                    # diluted out of confirmation.
                    st["equity_torn_streak"] = int(st.get("equity_torn_streak", 0)) + 1
                    plan["equity_torn"] = 1
                    plan["equity_torn_streak"] = st["equity_torn_streak"]
                    print(f"WARNING equity snapshot TORN x{st['equity_torn_streak']} — a fill "
                          f"landed between the positions/balance reads and the retry did not "
                          f"stabilise; halt arithmetic skipped this cycle")
                    if st["equity_torn_streak"] >= 2:
                        _exit_only_all = True
                        plan["equity_torn_reduce_only"] = 1
                elif (st.get("equity_day") != _day) | _consume_day_baseline_marker():
                    # BOTH operands are evaluated on purpose (bitwise | on two bools, never
                    # short-circuiting `or`). DEFECT FOUND LIVE 2026-08-09: with `or`, a day
                    # change short-circuits and leaves the marker ON DISK, so the NEXT cycle
                    # takes this branch a SECOND time and re-baselines again. Measured that
                    # night: 23:52:05Z day-change re-baseline (08-07 -> 08-08, marker
                    # untouched), then 23:54:33Z marker consumed = second reset 2.5 min later,
                    # then 00:00:09Z the UTC roll = a third. Three baselines in 8 minutes.
                    # Evaluating both consumes the marker AT the re-baseline it belongs to.
                    # A7 (operator-ruled 2026-08-05): an operator-NAMED restart may drop the
                    # day_baseline_reset marker (restart_bundle.sh) — the daily-loss governor
                    # then re-baselines at current equity so its day agrees with the fresh P2
                    # verdict window instead of inheriting pre-restart tainted carry (the
                    # 08-05 "18.25 today-only" workaround this replaces). Marker is consumed
                    # ONCE, only on an untorn cycle, and only widens nothing: the halt still
                    # measures true drawdown from the new baseline. Auto/crash restarts do
                    # not create the marker — no amnesty for those.
                    plan["equity_torn"] = 0
                    st["equity_torn_streak"] = 0
                    if st.get("equity_day") == _day:
                        plan["day_baseline_reset"] = 1
                    # DRAWDOWN CARRY ACROSS THE BASELINE (KALSHI_DD_CARRY, default ON).
                    # DEFECT FOUND LIVE 2026-08-09, cost real money: this branch re-seeded the
                    # peak at CURRENT equity unconditionally, so a drawdown still OPEN at the
                    # boundary was simply forgiven. Measured that night: dd had correctly
                    # climbed to $3.00 at 23:58:18Z (equity 272.16 vs peak 275.16) and was
                    # tracking toward the $10 halt; at 00:00:09Z the UTC roll re-seeded the peak
                    # at 267.61 — the bottom — and dd went to 0.00 with $7.55 of the slide
                    # erased. A bot bleeding at 23:59 got a clean full envelope at 00:00 with
                    # no operator action and no log line. The daily budget is meant to cap NEW
                    # losses, not to launder an in-flight one.
                    # Carry = the unresolved drawdown at the instant of re-baseline. It DECAYS
                    # as equity climbs back (see the else-branch), so recovering out of the hole
                    # returns the room — it is a debt, not a permanent penalty.
                    _prev_peak = float(st.get("equity_day_peak", _equity) or _equity)
                    _carry = max(0.0, _prev_peak - _equity) if DD_CARRY else 0.0
                    if _carry > 0.0:
                        print(f"DD CARRY: re-baselining with ${_carry:.2f} of drawdown still "
                              f"OPEN (prev peak ${_prev_peak:.2f} -> equity ${_equity:.2f}); "
                              f"it stays against the ${DAILY_LOSS_HALT_USD:.2f} limit until "
                              f"equity climbs back out")
                    st["equity_day_carry"] = round(_carry, 6)
                    plan["daily_dd_carry"] = round(_carry, 2)
                    st["equity_day"] = _day
                    st["equity_day_start"] = _equity
                    st["equity_day_peak"] = _equity
                    st["equity_prev"] = _equity
                else:
                    plan["equity_torn"] = 0
                    st["equity_torn_streak"] = 0
                    _start = float(st.get("equity_day_start", _equity))
                    _peak = max(float(st.get("equity_day_peak", _start)), _equity)
                    st["equity_day_peak"] = _peak
                    # PEAK AUDIT TRAIL (defect 2026-07-30: recorded day-peak $299.96 < the
                    # 12:00Z mark $321.77 — a regression that delayed the halt ~$22; the
                    # in-process max() cannot regress, so the writer was external. Mechanism
                    # UNCONFIRMED; this key puts the peak on every plan row so the next
                    # regression is caught red-handed, cycle-stamped.)
                    plan["equity_day_peak"] = round(_peak, 2)
                    st["equity_prev"] = _equity        # mark-equity telemetry continuity
                    _dd_raw = _peak - _equity
                    # CARRIED DEBT, DECAYING (see the re-baseline branch). Every dollar of
                    # equity recovered above the day's start pays the carry down first; once it
                    # is repaid the meter is a pure same-day drawdown again. Without this, a
                    # bleed that straddles the baseline is invisible to the halt.
                    _carry = float(st.get("equity_day_carry", 0.0) or 0.0) if DD_CARRY else 0.0
                    # REPAYMENT IS LATCHED — measured against the day's PEAK, never against the
                    # instantaneous equity. Using current equity (the first cut of this fix, and
                    # a defect caught in adversarial review before deploy) let a repaid carry
                    # RESURRECT: recover to a new high, carry_eff -> 0, then dip back while still
                    # above day-start and the debt reappears, so the halt could fire on a bot
                    # sitting near its own peak. _peak is monotone within the day, so once the
                    # hole is climbed out of it stays climbed out.
                    # Algebraically this makes dd == prev_peak - equity, i.e. exactly "do not
                    # reset the peak while a drawdown is still open" — which is the intent.
                    _carry_eff = max(0.0, _carry - max(0.0, _peak - _start))
                    _dd = _dd_raw + _carry_eff
                    plan["daily_dd_raw"] = round(_dd_raw, 2)
                    plan["daily_dd_carry"] = round(_carry_eff, 2)
                    plan["daily_dd"] = round(_dd, 2)
                    # SINGLE MEASURE, SINGLE LIMIT (the cumulative-down arm was removed by
                    # operator order 2026-08-02):
                    #   _dd    TRUE DRAWDOWN from the day's peak, MARK basis. Falls back when
                    #          equity recovers. What an operator means by "stop if I'm down $X".
                    # NAME THE LIMB THAT ACTUALLY BREACHED (2026-07-26): one limb remains, and
                    # the halt still names it and the limit it was tested against — misreading
                    # WHICH measure halted the bot is the single most expensive diagnostic
                    # error this lane has made, so the halt says so itself.
                    _breaches = []
                    if _dd > DAILY_LOSS_HALT_USD:
                        _breaches.append(
                            f"DRAWDOWN ${_dd:.2f} > ${DAILY_LOSS_HALT_USD:.2f} "
                            f"(from day-peak ${_peak:.2f}"
                            + (f"; ${_carry_eff:.2f} CARRIED across the baseline"
                               if _carry_eff > 0 else "") + ")")
                    # F1 (operator-named 2026-07-31): N-of-5 WINDOW, not a hard-reset streak
                    # -- an oscillating mark used to reset the streak to 0 on any single
                    # non-breach cycle, deferring confirmation forever under real losses.
                    _bhw = max(5, int(HALT_CONFIRM_N))   # M5: window >= knob or the halt
                    _bh = [int(b) for b in (st.get("halt_breach_hist") or [])][-(_bhw - 1):]
                    _bh.append(1 if _breaches else 0)
                    st["halt_breach_hist"] = _bh
                    st["halt_breach_streak"] = sum(_bh)          # key name kept (state compat)
                    plan["halt_breach_streak"] = st["halt_breach_streak"]
                    if _breaches and sum(_bh) >= max(1, HALT_CONFIRM_N):
                        _why = " AND ".join(_breaches)
                        plan["daily_loss_halt"] = round(_dd, 2)
                        plan["daily_halt_reason"] = _why
                        with open(STOP_FILE, "w") as fh:
                            fh.write(f"auto daily-loss halt {now.isoformat()} drop=${_dd:.2f} "
                                     f"TRIGGER: {_why} "
                                     f"(equity ${_equity:.2f} vs day-peak ${_peak:.2f}; "
                                     f"dd ${_dd:.2f}; day-start ${_start:.2f})\n")
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
            # PAIREDNESS SPLIT (Phase C2). Canon's own lever for a reward-earning maker is
            # PAIREDNESS — the whale study's "every fill is a cost" holds for NAKED inventory,
            # while a floored ladder pair settles at >= $1 and carries only the strike gap. The
            # bot has always ACTED on that distinction (naked_held_usd gates the breakers) but
            # never RECORDED it, so no study could ask what share of a day's inventory was
            # paired, or what the naked share cost. That gap is why the 08-02 loss could not be
            # split paired-vs-naked after the fact. Contracts AND cost, both sides, always
            # emitted — derived from reads the cycle already made.
            try:
                _naked_by = ladder_pairing(held_by)
                _gross_ct = sum(abs(float(v or 0.0)) for v in held_by.values())
                _naked_ct = sum(abs(float(v or 0.0)) for v in _naked_by.values())
                plan["inv_gross_ct"] = round(_gross_ct, 2)
                plan["inv_naked_ct"] = round(_naked_ct, 2)
                plan["inv_paired_ct"] = round(max(0.0, _gross_ct - _naked_ct), 2)
                plan["inv_gross_usd"] = round(held_cost, 4)
                plan["inv_naked_usd"] = round(risk_cost, 4)
                plan["inv_paired_usd"] = round(max(0.0, held_cost - risk_cost), 4)
                plan["inv_paired_frac"] = round(
                    (max(0.0, _gross_ct - _naked_ct) / _gross_ct) if _gross_ct else 0.0, 4)
                plan["inv_pairedness_measured"] = 1
            except Exception:
                _SILENT["pairedness_split_fail"] += 1
            hist = [h for h in st.get("held_hist", [])
                    if now.timestamp() - h[0] < BREAKER_WINDOW_S]
            hist.append([now.timestamp(), risk_cost])
            # audit F8 (2026-07-29): retention must cover the WINDOW at the daemon's 5-8s
            # event-driven cadence, not the retired 60s-timer cadence — 30 samples spanned only
            # ~2.5-4 min of a 600s window, silently shortening the velocity lookback.
            st["held_hist"] = hist[-max(150, BREAKER_WINDOW_S // 4):]
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
        # INCUMBENT-ONLY GATE capture (see the knob comment): the incumbent set is standing
        # orders + held positions + last cycle's resting tickers (blackout-edge widening,
        # conservative toward "keep existing"), captured ONCE and persisted. The strip lives
        # in the quote loop below, beside the loss-governor strip.
        _incumbent_only = None
        if INCUMBENT_ONLY:
            _cap9 = st.get("incumbent_only_set")
            if _cap9 is None:
                _cap9 = sorted(set(standing)
                               | {_t9 for _t9, _p9 in held_by.items()
                                  if abs(_p9) >= INV_TOLERANCE}
                               | _ban_set(st.get("prev_standing_tickers")))  # editable state:
                               # coerce (audit lens 2 #5 — same sorted() crash class)
                st["incumbent_only_set"] = _cap9
                print(f"INCUMBENT-ONLY gate ARMED: {len(_cap9)} incumbent market(s) captured "
                      f"— no new markets will be opened while the gate is on")
            _incumbent_only = set(_cap9)
            plan["incumbent_only_n"] = len(_incumbent_only)
        elif "incumbent_only_set" in st:
            st.pop("incumbent_only_set", None)       # OFF -> next enable re-captures fresh
        # OPTION C — SELECT-TO-BUDGET walk (see the knob comment). Runs after the governor
        # and the incumbent capture, before the quote loop, so it sees this cycle's truth.
        # Fail-OPEN: any fault leaves the footprint unfiltered (the cap_desired backstop
        # still bounds capital) and counts select_budget_fail.
        _budget_said_no = set()          # budget-REFUSED tickers: excluded from drop-grace
        if SELECT_BUDGET and footprint:
            try:
                import kalshi_capital_rank as _kcr
                if D3_RAMP:
                    _d3_first_seen_ensure(st)      # review C-1: est needs real rungs
                _limit9 = _total_cap() * (1.0 + SELECT_BUDGET_MARGIN)
                _famcap9 = _series_cap()
                _refs9 = {}
                _now9 = now.timestamp()
                with SCORES_LOCK:
                    for _m9 in footprint:
                        _r9 = SCORES.get(_m9["ticker"]) or {}
                        _rts9 = _r9.get("ts")
                        # audit lens 2 #3: refs age out at the same bar the alloc
                        # prospective feed uses — a stale extreme ref halves est and
                        # over-admits; stale -> unknown -> max est (conservative)
                        if (_rts9 is not None
                                and (_now9 - float(_rts9)) <= ALLOC_PCAP_MAX_AGE_S):
                            _refs9[_m9["ticker"]] = _r9.get("ref")
                _order9 = sorted(footprint,
                                 key=lambda _m9: (-float(alloc_prio.get(_m9["ticker"], 0.0)
                                                        or 0.0), _m9["ticker"]))
                _heldset9 = {_t9 for _t9, _p9 in held_by.items()
                             if abs(_p9) >= INV_TOLERANCE}
                _tot9, _fam9 = 0.0, defaultdict(float)
                _keepset9, _saidno9 = set(), set()
                _drop_budget9 = _drop_family9 = 0
                _fam_dropped9 = []
                for _m9 in _order9:
                    _t9 = _m9["ticker"]
                    _f9 = _t9.split("-")[0]
                    if _t9 in _heldset9:
                        _keepset9.add(_t9)      # de-risk never gated; capital already held
                        # blind review C#3: seed the family total with held dollars
                        # ($1/contract, cap_desired's own convention) so the walk cannot
                        # admit siblings the family backstop then skips every cycle
                        _fam9[_f9] += abs(held_by.get(_t9, 0.0))
                        continue
                    # blind review C#2: markets whose ACCUMULATING quotes the incumbent
                    # gate or the loss governor will strip anyway commit ~nothing —
                    # charging full est let phantom demand budget-out a standing
                    # incumbent. Keep them at zero cost; the strips do the real gating.
                    if ((_incumbent_only is not None and _t9 not in _incumbent_only)
                            or _t9 in _exit_only_mkts):
                        _keepset9.add(_t9)
                        continue
                    _est9 = _kcr.est_commit_usd(_refs9.get(_t9), MAX_MARKET_CAPITAL,
                                                INV_HARD_CT)
                    if _m9.get("explore") and EXPLORE_PROBE_CT > 0:
                        # probes rest EXPLORE_PROBE_CT contracts a side; at any yes/no
                        # split the two sides' dollars sum to ~probe_ct x $1
                        _est9 = min(_est9, float(EXPLORE_PROBE_CT))
                    if D3_RAMP:
                        # ramp-aware est (operator-ruled 2026-08-06): charge what the
                        # ramp will actually let rest (~$1/ct), not the full join.
                        _est9 = min(_est9, float(_d3_est_ct(_t9, now.timestamp())))
                    if _famcap9 > 0 and _fam9[_f9] + _est9 > _famcap9:
                        _drop_family9 += 1      # family budget full: skip THIS sibling
                        _saidno9.add(_t9)
                        _fam_dropped9.append(_t9)   # A3 telemetry: name the evicted
                        continue
                    if _tot9 + _est9 > _limit9:
                        _drop_budget9 += 1      # keep walking: a cheaper market may fit
                        _saidno9.add(_t9)
                        continue
                    _keepset9.add(_t9)
                    _tot9 += _est9
                    _fam9[_f9] += _est9
                # blind review C#1: filter in ORIGINAL footprint order — rebuilding in
                # walk order silently destroyed the pivot branch's near-money ordering,
                # which the quote loop's FOOTPRINT_TOP early-stop depends on.
                footprint = [_m9 for _m9 in footprint if _m9["ticker"] in _keepset9]
                _budget_said_no = _saidno9
                plan["select_budget_used"] = round(_tot9, 2)
                plan["select_budget_limit"] = round(_limit9, 2)
                if _drop_budget9:
                    plan["drop_budget_full"] = _drop_budget9
                if _drop_family9:
                    plan["drop_family_budget"] = _drop_family9
                    # A3 (operator-ruled 2026-08-05, telemetry-now): WHICH siblings the
                    # family cap evicted — the count alone couldn't answer whether the
                    # eviction lands on the right (lowest-priority) rows, given the
                    # alphabetical same-pool tie-break. Capped: worst case one family
                    # ladder ~50 strikes; 40 keeps the row bounded.
                    plan["family_dropped_tickers"] = _fam_dropped9[:40]
            except Exception:
                _SILENT["select_budget_fail"] += 1
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
                    # audit F3 (2026-07-29): was flatten_to_zero on the FULL venue position —
                    # "paired leg = bounded pennies" was a 20-ct-era claim; at 31-80 ct legs a
                    # full-position cross de-pairs the ladder and cascades a second cross on the
                    # orphaned sibling ($5-8/occurrence). Now capped at |naked| via
                    # _taker_cross_capped (cancel-confirmed -> cross -> re-rest): the paired
                    # remainder self-hedges to settlement exactly as ladder_pairing intends.
                    ok, nc = _taker_cross_capped(client, t, int(round(abs(pos))), pos > 0,
                                                 cost=cost_by.get(t, 0.0))
                    # RE-ENTRY COOLDOWN feed (self-audit F5, 2026-07-29): the settle-taker is
                    # one of the taker exits — "a book that just ran us over must not be
                    # rejoined" applies here exactly as at the strand cross.
                    if REENTRY_COOLDOWN_S > 0 and nc:
                        _cool6 = st.get("reentry_cool") or {}
                        _cool6[t] = (now + timedelta(seconds=REENTRY_COOLDOWN_S)).isoformat()
                        st["reentry_cool"] = _cool6
                    # HONEST OUTCOME (masking audit 07-22): flatten_to_zero cancels the ticker's
                    # resting orders FIRST, so a FAILED flatten (book unreadable / every IOC
                    # rejected / zero liquidity) leaves the position naked with NO reducing quote.
                    # Treating that as success (popping the ticker + counting a flatten) skipped
                    # the passive unwind AND suppressed the sysfail alarm — telemetry claimed the
                    # backstop ran while the position rode into settlement. Only a CONFIRMED flat
                    # retires the ticker; a failure falls through to normal maker unwind handling.
                    # FIX 4 note: "its resting orders WERE cancelled either way" was an ASSUMPTION,
                    # and the 07-27 tape disproved it (a silent cancel failure left an exit resting
                    # through a cross). flatten_to_zero now CONFIRMS the cancel or refuses to cross
                    # — on a False return orders may legitimately still rest, so the ticker stays
                    # in `standing` and the ordinary reconcile handles them; popping on ok only
                    # costs a few redundant-cancel fails in the rare crossed-but-residual case.
                    if ok:
                        standing.pop(t, None)           # cancel-confirmed -> book truly clear
                        taker_flattens += 1
                        naked_by[t] = 0.0               # NAKED cleared; the PAIR may remain
                        _pair_left = abs(held_by.get(t, 0.0)) - abs(pos)
                        if _pair_left < INV_TOLERANCE:
                            # nothing (or dust) remains -> fully retire the ticker as before
                            flattened.add(t)
                            held_by.pop(t, None)
                            naked_by.pop(t, None)
                        else:
                            # a self-hedging pair rides to settlement BY DESIGN (F3): keep it in
                            # held_by so ev_delta/unwind bookkeeping still sees it, and do NOT
                            # mark it flattened (the strand-unwind loop may keep a maker reduce).
                            held_by[t] = held_by.get(t, 0.0) - pos
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
                    # LOSS GOVERNOR (self-audit F6a, 2026-07-29): the retained copy routed
                    # around the exit-only strip below, so a governed market's accumulating
                    # resting orders survived on any transient fetch error. Strip them here
                    # too — for a governed market, cancelling the accumulating side is the
                    # governor doing its job, not a stranding risk (unwind copies survive).
                    if _exit_only_all or t in _exit_only_mkts:
                        desired[t] = [o for o in desired[t] if o.get("reason") == "unwind"]
                        if not desired[t]:
                            desired.pop(t, None)
                if SCORE_RANK and m.get("explore"):
                    # D9 review fix #1 (2026-08-02): a fetch TRY is an attempt. Without this
                    # stamp a dead ticker (404 mid-window, the sweeper's documented livelock
                    # case) stays never-attempted forever, pins the front of the sweep queue,
                    # and re-wedges the explore quota through the fetch-fail path.
                    try:
                        import kalshi_market_scores as _kms
                        with SCORES_LOCK:
                            _kms.touch_attempt(SCORES, t, now=now.timestamp())
                    except Exception:
                        _SILENT["explore_stamp_fail"] += 1
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
            # always computed (was MKT_TELEMETRY-only): FIX P's streak needs the same
            # gate diff to know a probe was refused by BOOK quality specifically —
            # ~30 int keys per market, negligible; telemetry WRITES stay flag-guarded.
            _pre_stats = {k: v for k, v in qstats.items() if type(v) is int}
            try:
                q = desired_quotes(m, ob.get("yes_dollars") or [], ob.get("no_dollars") or [],
                                   now, own=own.get(t), inv=naked_by.get(t, 0.0),
                                   event_delta=event_delta_for(ev_delta, t), stats=qstats,
                                   cost=cost_by.get(t, 0.0),
                                   own_orders=_own_order_levels(_f5_standing.get(t)))
            except Exception as e:
                # isolate one degenerate market, but SURFACE it as quote_fail (a
                # systematic desired_quotes failure must not hide inside gated_out)
                q = []
                quote_fail += 1
                if first_quote_err is None:
                    first_quote_err = f"{t}: {e!r}"
            # FIX P (review F1): mark a PROBE row refused by the BOOK gates this pass —
            # the ONLY signal that rotates its slot. Strips/breaker/budget later in the
            # chain deliberately never set this; a fetch-fail never reaches here.
            if (not q and m.get("explore") and SERIES_ALLOW
                    and t.split("-")[0] not in SERIES_ALLOW):
                _gd6 = {k: v - _pre_stats.get(k, 0) for k, v in qstats.items()
                        if type(v) is int and v - _pre_stats.get(k, 0) > 0}
                if any(k.startswith("gate_") or k == "empty_books" for k in _gd6):
                    m["_book_refused"] = True
            # EXPLORE PROBE SIZING (item E): a sampling market rests probe-sized accumulating
            # quotes — data budget, not an earnings seat. Unwind quotes are never shrunk.
            _q_fullsize = None                      # D8: pre-clamp copy for the score cache
            if q and EXPLORE_PROBE_CT > 0 and m.get("explore"):
                for _o5 in q:
                    if _o5.get("reason") != "unwind" and _o5["count"] > EXPLORE_PROBE_CT:
                        if _q_fullsize is None:
                            _q_fullsize = [dict(_o6) for _o6 in q]
                        qstats["explore_probe_capped"] = qstats.get("explore_probe_capped", 0) + 1
                        _o5["count"] = EXPLORE_PROBE_CT
            # W4/D3 SIZE RAMP + W7 NEW-SERIES CLAMP: accumulating counts capped by ticker age
            # (rungs) and series track record; unwind never touched. Same post-hoc shape as the
            # explore clamp above. The full-size copy rule matches it too: a ramped count is a
            # risk decision, not a measurement of the market.
            if q and D3_RAMP:
                global _D3_FIRST_SEEN, _D3_LAST_DESIRED
                if _D3_FIRST_SEEN is None:
                    try:
                        _st14 = load_state()
                        _D3_FIRST_SEEN = {str(k): float(v) for k, v in
                                          (_st14.get("d3_first_seen") or {}).items()}
                        # review #7: the belt restore must carry the F14 grace map too, or
                        # every restart under SELECT_BUDGET=0 restarts all absence clocks
                        _D3_LAST_DESIRED = {str(k): float(v) for k, v in
                                            (_st14.get("d3_last_desired") or {}).items()}
                    except Exception:
                        _D3_FIRST_SEEN = {}
                if any(_o8.get("reason") != "unwind" for _o8 in q):
                    _fb3 = _d3_feedback_cached(now.timestamp())
                    if not _fb3:
                        # review F1: an empty/missing feedback table binds the clamp for
                        # EVERYTHING — visible, never silent.
                        qstats["d3_feedback_empty"] = 1
                    _rct = _d3_ramp_ct(t, now.timestamp(), _D3_FIRST_SEEN, _fb3)
                    if any(_o8.get("reason") != "unwind" and _o8["count"] > _rct
                           for _o8 in q):
                        if _q_fullsize is None:
                            _q_fullsize = [dict(_o9) for _o9 in q]
                        _d3_apply_ramp(q, _rct, qstats)
            # LOSS GOVERNOR ENFORCEMENT: a tripped/cooling market keeps ONLY its reducing
            # quotes; accumulating ones are stripped here and the standing diff cancels any
            # already resting. Placed at the choke point every quoted market flows through.
            if q and (_exit_only_all or t in _exit_only_mkts):
                _kept4 = [o for o in q if o.get("reason") == "unwind"]
                if len(_kept4) != len(q):
                    qstats["loss_exitonly_stripped"] = (
                        qstats.get("loss_exitonly_stripped", 0) + len(q) - len(_kept4))
                    _q_fullsize = None              # stripped -> the full-size copy is fiction
                q = _kept4
            # INCUMBENT-ONLY enforcement: a market outside the captured set keeps ONLY its
            # unwind quotes (same strip as the loss governor above — de-risk never blocked,
            # accumulation never allowed). Flat non-incumbents strip to [] = never opened.
            if (q and _incumbent_only is not None and t not in _incumbent_only
                    and t not in MACRO_PROBE_TICKERS):   # D-C review #11: designation overrides
                _kept9 = [o for o in q if o.get("reason") == "unwind"]
                if len(_kept9) != len(q):
                    qstats["incumbent_only_stripped"] = (
                        qstats.get("incumbent_only_stripped", 0) + len(q) - len(_kept9))
                    _q_fullsize = None
                q = _kept9
            if q:
                desired[t] = q
            # D9 (operator-named 2026-08-02): stamp every explore ATTEMPT — priced, gated,
            # or stripped — so the full-sweep frontier advances. Deliberately OUTSIDE the
            # MKT_TELEMETRY block (D9 review fix #3): frontier progress must not depend on
            # telemetry config or telemetry write health. Attempt != measurement (D4).
            if SCORE_RANK and m.get("explore"):
                try:
                    import kalshi_market_scores as _kms
                    with SCORES_LOCK:
                        _kms.touch_attempt(SCORES, t, now=now.timestamp())
                except Exception:
                    _SILENT["explore_stamp_fail"] += 1
            # PER-MARKET REWARD TELEMETRY — observation only, and deliberately the LAST thing in the
            # loop body: it reads state, writes one line, and can never alter `desired`. Wrapped so a
            # telemetry fault (bad row, full disk) can never break a live trading cycle.
            if MKT_TELEMETRY:
                try:
                    _gates = {k: v - _pre_stats.get(k, 0) for k, v in qstats.items()
                              if type(v) is int and v - _pre_stats.get(k, 0) > 0}
                    _row = _market_telemetry_row(cyc, now, m, _byl, _bnl, q,
                                                 own.get(t), naked_by.get(t, 0.0), _gates,
                                                 own_orders=_own_order_levels(_f5_standing.get(t)))
                    with open(os.path.join(DATA_DIR,
                                           f"quotes-{now.strftime('%Y%m%d')}.jsonl"), "a") as _fh:
                        _fh.write(json.dumps(_row, separators=(",", ":")) + "\n")
                    # SCORE CACHE: fold this book into the rolling rank. Free — capture_usd_day and
                    # the reference price are already computed above. This is what lets the NEXT
                    # cycle rank on measured capture instead of pool size.
                    # D4 (selection review 2026-08-01): fold ONLY real quoting attempts. A
                    # gated-out market (q == []) is OUR decision, not a measurement — writing
                    # it as "capture $0, fresh ts" locked gated markets out of the explore
                    # quota for 30 min on a measurement that never happened (69.3% of all
                    # timestamped rows were these fake zeros). Ungated rows stay unknown/
                    # stale in the cache and remain explore-eligible, as documented.
                    # blind review 2026-08-01 (lens A #4): "q non-empty" was half the D4 rule —
                    # a market STRIPPED to unwind-only (loss governor / incumbent gate /
                    # holding_exit_only) still folded a halved capture as if measured. Fold
                    # only when at least one ACCUMULATING quote rested: that is the only case
                    # where capture was genuinely attempted.
                    if SCORE_RANK and any(_o7.get("reason") != "unwind" for _o7 in q):
                        _cache_row = _row
                        if _q_fullsize is not None:
                            # D8: the probe rests EXPLORE_PROBE_CT contracts, but the cache
                            # ranks markets by what NORMAL-size quoting would capture — the
                            # probe-sized value was stored as the market's full-size worth
                            # (sampled inflation up to 116x). Recompute at intended size;
                            # the telemetry ROW above still records what actually rested.
                            _cache_row = _market_telemetry_row(
                                cyc, now, m, _byl, _bnl, _q_fullsize,
                                own.get(t), naked_by.get(t, 0.0), _gates,
                                own_orders=_own_order_levels(_f5_standing.get(t)))
                        import kalshi_market_scores as _kms
                        with SCORES_LOCK:
                            _kms.update(SCORES, t, _cache_row.get("capture_usd_day"),
                                        _cache_row.get("y_ref"), now=now.timestamp())
                except Exception:
                    # audit lens 1 #7: this swallow also covers the score-cache FOLD — an
                    # uncounted fault here silently decays ranking to pool order. Count it;
                    # a telemetry fault still never breaks a trading cycle.
                    _SILENT["mkt_telemetry_fail"] += 1

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
        # FOOTPRINT RETENTION GAUGE (operator slate item D, 2026-07-29): % of this cycle's
        # footprint that was also in the last cycle's. THE stickiness meter — churn was only
        # visible by post-processing jsonl (measured 07-29: median market present <1% of
        # cycles). Written before any selection change ships so there is a before/after.
        _prev_fp = set(st.get("prev_footprint") or [])
        if _prev_fp and fp_tickers:
            plan["fp_retained_pct"] = round(
                100.0 * len(fp_tickers & _prev_fp) / len(fp_tickers), 1)
        st["prev_footprint"] = sorted(fp_tickers)
        # incumbency feed (item A): next cycle's incumbents = where we ACTUALLY rest now
        st["prev_standing_tickers"] = sorted(standing.keys())
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
            # LOSS GOVERNOR (self-audit F6b, 2026-07-29): this hatch OPENS a position on t2
            # (fresh collateral, its own comment below) after the loop that enforces the
            # exit-only set — so a loss-tripped or cooling t2 could be re-entered through it.
            # Gate it like any other opening order, and COUNT the gate so it is never silent.
            if _exit_only_all or t2 in _exit_only_mkts:
                plan["ladder_cross_gated"] = plan.get("ladder_cross_gated", 0) + 1
                continue
            # INCUMBENT-ONLY (blind review 2026-08-01, lens A #1 / lens B I5): this hatch is
            # an OPENING order and ran after the quote-loop incumbent strip — the one path
            # that could open a new market through the gate. Latent today (unwind pricing
            # rests at/above the touch, so the sub-touch trigger is unsatisfiable), but the
            # invariant must not depend on pricing internals. Same shape as F6b above.
            if _incumbent_only is not None and t2 not in _incumbent_only:
                plan["ladder_cross_gated"] = plan.get("ladder_cross_gated", 0) + 1
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
            # HOLDING => EXIT ONLY overrides KEEP_BOTH (operator directive 2026-07-27). KEEP_BOTH
            # exists to keep a HELD market two-sided so its snapshot still earns — but "held" is
            # exactly the state we have now decided must be exit-only, so the two rules are in
            # direct conflict and the risk rule wins. Left computed here rather than deleting
            # KEEP_BOTH, so setting KALSHI_HOLDING_EXIT_ONLY=0 restores the old pairing intact.
            # Under the breaker ONLY reducing quotes survive. The KEEP_BOTH "minjoin" branches
            # (a floor-sized accumulating quote on HELD markets so the snapshot still earned)
            # were removed with KALSHI_REDUCE_ONLY_KEEP_BOTH (operator Q1 decision 2026-07-28):
            # holding => exit only admits no accumulating side, breaker or not. The reward cost
            # of the dropped side is proportional, not a $0 cliff (R4 pays the side-share mean).
            def _shape(t, qs):
                return [q2 for q2 in qs if _keep_reducing(t, q2)]
            desired = {t: _shape(t, qs) for t, qs in desired.items()}
            desired = {t: qs for t, qs in desired.items() if qs}
            print(f"WARNING breaker: naked ${plan.get('naked_held_usd', 0):.2f} of "
                  f"${held_cost:.2f} held (growth>{BREAKER_HELD_GROWTH_USD:.0f}"
                  f"/{BREAKER_WINDOW_S}s or level>{HELD_MAX_USD:.0f}) — REDUCE-ONLY cycle")

        # L2 PAIR-CARRY MAKER UNWIND (dark, KALSHI_PAIR_UNWIND=1 to enable; see config block)
        if PAIR_UNWIND and held_by:
            try:
                _pu_n = 0
                _pu_edge = 0.0
                for _lt6, _st6, _qty6 in _ladder_pairs(held_by):
                    _ct6 = int(_qty6)                    # whole contracts only — never flips
                    if _ct6 < 1:
                        continue
                    _rl6, _rs6 = book_refs.get(_lt6), book_refs.get(_st6)
                    if not _rl6 or not _rs6:
                        continue                         # BOTH books must be fresh this cycle
                    _pn6 = _rl6[1]                       # buy NO on the long-yes leg (its touch)
                    _py6 = _rs6[0]                       # buy YES on the long-no leg (its touch)
                    if not (_ok_exit_price(_pn6) and _ok_exit_price(_py6)):
                        continue
                    _edge6 = (1.0 - _pn6) + (1.0 - _py6) - 1.0
                    if _edge6 < PAIR_UNWIND_MIN_EDGE:
                        continue                         # settlement floor already pays >= this
                    for _t6, _side6, _px6 in ((_lt6, "no", _pn6), (_st6, "yes", _py6)):
                        _same6 = [o for o in desired.get(_t6, [])
                                  if o.get("side") == _side6]
                        if _same6:
                            # never rest two prices on one ticker+side: top up an existing
                            # same-price unwind, else this leg stands down (its partner is
                            # still a touch-priced maker reduce on its own book — safe alone)
                            if (len(_same6) == 1 and _same6[0].get("reason") == "unwind"
                                    and abs(_same6[0]["price_dollars"] - _px6) < TICK / 2):
                                _same6[0]["count"] += _ct6
                            else:
                                continue
                        else:
                            desired.setdefault(_t6, []).append(
                                {"side": _side6, "price_dollars": _px6, "count": _ct6,
                                 "reason": "unwind"})
                        _pu_n += 1
                    _pu_edge += _edge6 * _ct6
                if _pu_n:
                    plan["pair_unwind_quotes"] = _pu_n
                    plan["pair_unwind_edge_usd"] = round(_pu_edge, 2)
            except Exception:
                _SILENT["pair_unwind_fail"] += 1

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

        # FIX P: score the probes' cycle — ONLY a BOOK-gate refusal (the _book_refused
        # marker set beside the gate diff in the quote loop) bumps the streak; resting
        # anything clears it; every other emptiness cause (governor/incumbent strips,
        # breaker, fetch-fail, untried tail) changes NOTHING (review F1: bumping those
        # rotated slots for non-book reasons). Bump re-inserts the key so dict order =
        # recency and the 500-bound evicts the least-recently-REFUSED ticker, never an
        # actively-rotating veteran (review F2).
        if ALLOW_PROBE_EXCEPTION:
            try:
                for _m6 in footprint:
                    _t6 = _m6["ticker"]
                    if (_m6.get("explore") and SERIES_ALLOW
                            and _t6.split("-")[0] not in SERIES_ALLOW):
                        if desired.get(_t6):
                            _PROBE_GATE_REFUSED.pop(_t6, None)
                        elif _m6.get("_book_refused"):
                            _v6 = min(int(_PROBE_GATE_REFUSED.get(_t6, 0)) + 1, 99)
                            _PROBE_GATE_REFUSED.pop(_t6, None)
                            _PROBE_GATE_REFUSED[_t6] = _v6
                while len(_PROBE_GATE_REFUSED) > 500:
                    _PROBE_GATE_REFUSED.pop(next(iter(_PROBE_GATE_REFUSED)))
                st["probe_gate_refused"] = dict(_PROBE_GATE_REFUSED)
            except Exception:
                _SILENT["probe_streak_fail"] += 1
        # DROP HYSTERESIS — before the diff, give a ROTATED-OUT ticker (absent from this cycle's
        # footprint, i.e. never looked at) a few cycles to come back instead of tearing its book
        # down and rebuilding it identically. Runs BEFORE cap_desired so a retained book is still
        # subject to the capital cap like anything else.
        grace_used = {}
        if DROP_GRACE > 0:
            try:
                # blind review C#4: grace covers "we didn't check", never "we said no" —
                # a budget-REFUSED ticker is a decision, so it must not be grace-retained.
                # Adding it to fp_now makes grace treat it as checked-and-rejected.
                _fp_now = {m["ticker"] for m in footprint} | _budget_said_no
                desired, grace_used = apply_drop_grace(
                    standing, desired, _fp_now,
                    (load_state().get("drop_grace") or {}), DROP_GRACE,
                    # A9-F4: retained held tickers keep their unwind tag; governed
                    # tickers keep only their unwind (F6a parity with fetch-fail).
                    held=naked_by,
                    exit_only=(set(standing) if _exit_only_all else _exit_only_mkts),
                    inv_tolerance=INV_TOLERANCE)
                _g_tagged = sum(1 for _tg in grace_used
                                if any(o.get("reason") == "unwind"
                                       for o in desired.get(_tg, [])))
                if _g_tagged:
                    # occurrence counter for the F4 composition (held book carried
                    # through grace) — zero on quiet cycles, absent pre-F4.
                    plan["grace_unwind_tagged"] = _g_tagged
            except Exception:
                grace_used = {}
        _fam_held = None
        if SERIES_MAX_USD > 0 or SERIES_PCT > 0:
            # HELD-inventory dollars per family, $1/contract conservative (same reserve
            # convention as committed capital) — blind-review fix: fills must not reopen
            # family headroom. Gate matches _series_cap()'s activation (blind-review D1
            # 2026-07-31: gating on SERIES_MAX_USD alone left the pure-SERIES_PCT config
            # seeding families with quote notional only — fills reopened headroom).
            _fam_held = defaultdict(float)
            for _t5, _p5 in held_by.items():
                _fam_held[_t5.split("-")[0]] += abs(_p5)
        desired, capped_markets = cap_desired(
            desired, alloc_prio,
            incumbents=_INCUMBENT_TICKERS if ALLOC_INCUMBENT_FIRST else None,
            fam_held=_fam_held)                                                # aggregate $ cap
        if SELECT_BUDGET and capped_markets:
            # BACKSTOP ALARM (Option C): with the budget walk on, the blunt cut should
            # almost never fire — every firing means est_commit under-read real demand.
            # Persistent nonzero = LOWER KALSHI_SELECT_BUDGET_MARGIN (more negative =
            # tighter walk limit = less admitted demand). The original "raise" advice was
            # inverted (double-blind audit 2026-08-02, lens 2 #1) — raising the margin
            # ADMITS more demand and makes the backstop fire more.
            plan["budget_backstop_fired"] = capped_markets
        try:
            _fam_denom = float(_TOTAL_CAP_EFF[0]) if _TOTAL_CAP_EFF[0] is not None else None
        except (TypeError, ValueError):
            _fam_denom = None
        fam_top, fam_top_usd, fam_top_pct = _fam_concentration(
            desired, denom=_fam_denom)    # pct on the SAME equity basis _series_cap() uses
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
        creates, budget_dropped = bound_creates(creates, cancels, alloc_prio)  # whole-ticker

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
            if _create_ratchet_blocked(c["ticker"], reducing):
                create_skipped += 1                     # J6: venue keeps rejecting this ticker
                plan["create_ratchet_skipped"] = plan.get("create_ratchet_skipped", 0) + 1
                continue
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
                    if funding_committed + cost > min(free_cash, _total_cap()):
                        create_skipped += 1
                        continue
                elif committed + cost > _total_cap():   # portfolio-tracking cap
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
                _CREATE_FAIL_RATCHET.pop(c["ticker"], None)   # J6: success clears the ratchet
                if funding_gate_on and not reducing:
                    funding_committed += cost           # a filled accumulating buy would draw cash
            except Exception as e:
                create_fail += 1
                _create_ratchet_fail(c["ticker"])   # J6
                # RENDER ONCE. _err_detail drains the HTTPError's one-shot body via e.read(),
                # so calling it twice on the same exception leaves the SECOND caller with
                # `body=` empty — and creates are sorted unwind-first, so the second caller was
                # the loud operator-facing print below, i.e. the common case (blind review
                # 2026-08-03). Verified: a second e.read() returns b"".
                _edet = _err_detail(e)
                if first_create_err is None:        # anonymous create_fail hid WHAT was rejected
                    # defect 10: {e!r} on an HTTPError renders only "400 Bad Request" — the
                    # venue's actual reason is in the body, which _err_detail extracts.
                    first_create_err = (f"{c['ticker']}/{c['side']}/{c.get('reason')}: "
                                        f"{_edet}")[:240]
                if c.get("reason") == "unwind":
                    # An UNWIND is an EXIT. A rejected exit leaves inventory we intended to be
                    # rid of, so it is printed as well as recorded — first_create_err is a
                    # single first-writer-wins slot SHARED with amends (:4307), so a later or
                    # second unwind rejection would otherwise be dropped entirely.
                    print(f"WARNING UNWIND create REJECTED on {c['ticker']} {c['side']} "
                          f"{c.get('count')}@{c.get('price_dollars')}: {_edet}")

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
                _pc_crossed = _preclose_naked_flatten(client, held_by, now, plan, grace_state,
                                                      costs_by=cost_by) or []
                # RE-ENTRY COOLDOWN feed (self-audit F5): pre-close taker exits count too.
                if REENTRY_COOLDOWN_S > 0 and _pc_crossed:
                    _cool7 = st.get("reentry_cool") or {}
                    _until7 = (now + timedelta(seconds=REENTRY_COOLDOWN_S)).isoformat()
                    for _t7 in _pc_crossed:
                        _cool7[_t7] = _until7
                    st["reentry_cool"] = _cool7
            except Exception as e:                          # a backstop bug must never abort the cycle
                plan["preclose_error"] = f"{e!r}"[:160]
                print(f"WARNING preclose flatten pass RAISED: {e!r} — cycle continues")
            st["preclose_grace"] = grace_state

        # STRAND CROSS (FIX 3, operator-approved 2026-07-27): bounded-time exit for a naked
        # residual whose maker exit is not filling — the same grace-then-taker mechanism as the
        # pre-close flatten, without the close-window gate. Runs AFTER the pre-close pass and
        # re-reads positions fresh inside, so anything the pre-close taker already crossed this
        # cycle is seen as reduced and skipped (never double-crossed). STRAND_CROSS_S=0 disables.
        if STRAND_CROSS_S > 0:
            strand_state = st.get("strand_grace", {})
            try:
                _crossed4 = _strand_cross(client, naked_by, cost_by, now, plan, strand_state,
                                          step_state=st.setdefault("strand_step", {}),
                                          touch_state=st.setdefault("strand_touch", {}))
                # RE-ENTRY COOLDOWN feed: a ticker we just PAID a taker to leave starts its
                # exit-only clock; the governor block above enforces it from next cycle on.
                if REENTRY_COOLDOWN_S > 0 and _crossed4:
                    _cool4 = st.get("reentry_cool") or {}
                    _until4 = (now + timedelta(seconds=REENTRY_COOLDOWN_S)).isoformat()
                    for _t5 in _crossed4:
                        _cool4[_t5] = _until4
                    st["reentry_cool"] = _cool4
            except Exception as e:                          # a backstop bug must never abort the cycle
                plan["strand_error"] = f"{e!r}"[:160]
                print(f"WARNING strand-cross pass RAISED: {e!r} — cycle continues")
            st["strand_grace"] = strand_state

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
            **({"series_cap_dropped": _SERIES_CAP_DROPS[0]}
               if (SERIES_MAX_USD > 0 or SERIES_PCT > 0) else {}),
            **({"series_cap_solo": _SERIES_CAP_SOLO[0]}
               if (SERIES_MAX_USD > 0 or SERIES_PCT > 0) and _SERIES_CAP_SOLO[0] else {}),
            **({"fam_top": fam_top, "fam_top_usd": round(fam_top_usd, 2),
                "fam_top_pct": round(fam_top_pct, 1)} if fam_top else {}),
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
                with SCORES_LOCK:
                    _kms.evict(SCORES, now=now.timestamp())   # J4: age-out + hard bound
                    # snapshot rows under the lock; the DISK write happens outside it so a
                    # slow disk cannot stall the sweeper thread (blind-review LOW 2026-07-31)
                    _snap = {t: dict(r) for t, r in SCORES.items()}
                # everything below runs on the consistent snapshot, lock released
                _kms.save(SCORE_PATH, _snap)
                # measurement-bearing rows only (D9 review fix #5): attempt-only rows would
                # inflate this gauge toward universe size regardless of measurement progress
                # DENOMINATORS, LABELLED (defect 9, Phase A3). scored_markets and
                # score_age_p50_m are computed from the SAME snapshot in the SAME cycle but
                # over DIFFERENT PREDICATES — scored_markets counts rows bearing ts OR pts,
                # score_age_* only rows bearing ts. Printed side by side with no denominator,
                # the pair invited the reading that measurement coverage was ~12% when the
                # gauge's own population was a different, smaller set.
                # (The master plan explained this gap as scored_markets being a
                # "session-START epoch". That is mechanically WRONG and is corrected here:
                # there is ONE snapshot, taken above, read by both. Confirmed by blind review
                # 2026-08-03.)
                plan["score_rows_total"] = len(_snap)
                plan["scored_markets"] = sum(
                    1 for _r8 in _snap.values()
                    if _r8.get("ts") is not None or _r8.get("pts") is not None)
                # FRESHNESS GAUGES (root-fix plan Phase 4; SPLIT per blind-review 2026-07-31:
                # the live rank consumes ONLY actual measurements (ts), so score_age_* must
                # not be pacified by sweeper model observations (pts) — those get their own
                # keys. A gauge fed by the tool built alongside it is a gauge that can lie.)
                _ages = sorted(now.timestamp() - float(r["ts"]) for r in _snap.values()
                               if r.get("ts") is not None)
                # The population score_age_* is actually computed over — ALWAYS emitted, so a
                # reader can never pair the percentile with the wrong denominator.
                plan["score_age_n"] = len(_ages)
                if _ages:
                    plan["score_age_p50_m"] = round(_ages[len(_ages) // 2] / 60.0, 1)
                    plan["score_age_p90_m"] = round(_ages[int(len(_ages) * 0.9)] / 60.0, 1)
                _pages = sorted(now.timestamp() - float(r["pts"]) for r in _snap.values()
                                if r.get("pts") is not None)
                plan["pcap_age_n"] = len(_pages)
                if _pages:
                    plan["pcap_age_p50_m"] = round(_pages[len(_pages) // 2] / 60.0, 1)
                    plan["pcap_age_p90_m"] = round(_pages[int(len(_pages) * 0.9)] / 60.0, 1)
                plan["score_explore"] = SCORE_EXPLORE
                if _SWEEPER is not None:
                    plan["sweep"] = dict(_SWEEPER.stats)      # additive keys, observation only
            except Exception:
                # defect 9: this swallowed silently, so a fault in the score telemetry made
                # every gauge above VANISH from the row while "cycle ok" still printed — the
                # always-emit guarantee is voidable without it being counted anywhere. The
                # module's own doctrine is count-don't-swallow; this is the last bare pass in
                # the telemetry path.
                _SILENT["score_telemetry_fail"] += 1
        # DROP GRACE: carry the per-ticker counter into the next cycle. Tickers absent from
        # grace_used are simply not written back, which RESETS them — correct, because they either
        # came back into the footprint or their grace ran out and the diff cancelled them.
        if DROP_GRACE > 0:
            st["drop_grace"] = grace_used
            plan["grace_retained"] = len(grace_used)
        st["close_cache"] = _close_cache_snapshot()      # B-2: clocks survive restarts
        # W4/D3: persist first-seen so a restart cannot amnesty a young market to full size.
        # F14: pruning is grace-aware — a one-cycle absence no longer resets the ramp.
        if D3_RAMP and _D3_FIRST_SEEN is not None:
            plan["d3_ramp_tracked"] = _d3_prune_first_seen(st, desired, now.timestamp())
        # audit batch 3 (J2, operator-approved 2026-07-29): the counters are LIFETIME under
        # the long-lived daemon, so one old failure made every later plan row look actively
        # failing. silent_failures is now what fired THIS cycle; the lifetime total keeps
        # its own key so nothing is lost.
        _silent_report(plan)
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
        # bookkeeping ALWAYS runs, even if the cycle body raised.
        # audit F6 (2026-07-29): each step individually guarded — in the LONG-LIVED daemon a
        # raise here (e.g. disk full in append_plan) used to skip save_state AND leak the flock
        # fd, wedging every later cycle into "another instance holds the lock" with live quotes
        # still resting. Telemetry loss is acceptable; a wedged daemon is not.
        try:
            append_plan(plan)
        except Exception as _fe:
            print(f"WARNING append_plan FAILED ({_fe!r}) — telemetry row lost, cycle continues")
        try:
            st["mkt_taker_xn"] = dict(_TAKER_XN)   # H1 re-review fix: crosses happen AFTER
            # the governor block; without this late re-sync every paid-exit count was
            # clobbered by the next cycle's reload (governor A was INERT — both blind
            # reviewers, live-verified 16:44Z: journal paid exits vs mkt_taker_xn {}).
            save_state(st)
        except Exception as _fe:
            print(f"WARNING save_state FAILED ({_fe!r}) — state not persisted this cycle")
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
          f"/{_total_cap():,.0f} held=${plan.get('held_cost_usd',0):,.2f}"
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


def _cancel_ticker_resting_confirmed(client, ticker, hint_oids=None):
    """FIX 4 (operator-approved 2026-07-27): cancel EVERY resting order on `ticker` and CONFIRM
    against the venue that none remain, before any taker cross is allowed to fire.

    Why confirmation and not best-effort: on 2026-07-27 the STOP escalation crossed KXNDQHUD flat
    at 19:40:03Z while its 41ct@0.73 maker offset was still resting — the cancel had been attempted
    (try/except -> _SILENT counter) and the cross proceeded anyway. The stale "exit" filled 7s
    later for +40.55 ct: a naked ENTRY the instant the position it was exiting stopped existing.
    An error-classification approach (is this failure a 404-already-gone?) is fragile; the venue's
    own resting book is the ground truth, so we RE-READ it and require the ticker to be absent.

    hint_oids supplements the read (an order created ms ago can lag into the listing). A cancel of
    an already-gone order failing is fine — the re-read is the arbiter, not the error. Eventual
    consistency can only produce a FALSE NEGATIVE (order actually gone, still listed) -> we refuse
    to cross this pass and the caller retries next pass/cycle: fail-closed in the safe direction.
    Returns (confirmed_clear, cancels_attempted). Blind reads => (False, n): never cross blind."""
    try:
        orders = client.get_orders("resting").get("orders") or []
    except Exception:
        _SILENT["flatten_cancel_fail"] += 1
        return False, 0
    oids = {o.get("order_id") for o in orders
            if o.get("ticker") == ticker and o.get("order_id")}
    oids.update(o for o in (hint_oids or []) if o)
    n = 0
    for oid in oids:
        try:
            client.cancel_order(oid)
            n += 1
        except Exception:
            _SILENT["flatten_cancel_fail"] += 1     # classified by the re-read below, not the error
    try:
        after = client.get_orders("resting").get("orders") or []
    except Exception:
        _SILENT["flatten_cancel_fail"] += 1
        return False, n                             # cannot PROVE the book is clear -> not confirmed
    return (not any(o.get("ticker") == ticker for o in after)), n


def _rest_maker_offset(client, ticker, pos, cost, tag):
    """Rest ONE passive maker offset on the reducing side of a signed position — the RE-REST leg of
    the cancel -> cross -> re-rest ordering (FIX 4). Same shape as `_flatten_all` pass 1 (kept
    inline there: the STOP path's prints/oid bookkeeping are load-bearing); this helper serves the
    in-cycle cross paths, where a partial/failed IOC must not leave the position with NO exit until
    the next cycle's unwind pass re-rests one. Returns the order_id or None (best-effort: the
    normal unwind path re-rests every cycle regardless, so a None here is a gap of one cycle,
    not a strand)."""
    # RF2 (1.1 review 2026-07-31, diagnosed 2026-08-01): 117 re-rest failures since 07-30
    # (journal), every one on a rout ticker later permanently banned — and the four None
    # paths below were indistinguishable in the tape, so the root cause could not be
    # established from logs. Each path now counts separately (_SILENT -> plan row).
    try:
        ob = public_get(f"/trade-api/v2/markets/{ticker}/orderbook").get("orderbook_fp") or {}
    except Exception:
        _SILENT["rerest_fail_book_read"] += 1
        return None
    by = max((p for p, _ in _levels(ob.get("yes_dollars") or [])[0]), default=None)
    bn = max((p for p, _ in _levels(ob.get("no_dollars") or [])[0]), default=None)
    if pos > 0 and _ok_exit_price(bn):
        side, price, other = "no", bn, (by or bn)
    elif pos < 0 and _ok_exit_price(by):
        side, price, other = "yes", by, (bn or by)
    else:
        _SILENT["rerest_fail_unpriceable"] += 1
        return None
    price = _unwind_price(price, cost)
    if not _ok_exit_price(price):
        _SILENT["rerest_fail_band_adjusted"] += 1
        return None
    cnt = _unwind_size(_capped_join(price, other), price, pos)
    try:
        r = client.create_quote(ticker, side, price, cnt, post_only=True,
                                client_order_id=f"mk-{tag}-{int(time.time())}-{side}")
        o = r.get("order") if isinstance(r, dict) and isinstance(r.get("order"), dict) else {}
        return o.get("order_id")
    except Exception as e:
        # DEFECT 10 (2026-08-02): this catch bound NOTHING — no `as e` — so a rejected EXIT
        # re-rest vanished into a flat global counter that cannot even name the ticker. Live
        # 2026-08-02T10:32:58Z the caller printed "...the re-rest FAILED — no working exit
        # until the next cycle unwind pass" for KXRAIN-26AUG03-BOS with no reason attached,
        # and the same rejection then repeated identically. An exit we cannot place is the
        # one failure that must never be quiet, and under STOP the journal is the ONLY
        # channel (run_once returns before append_plan), so this PRINTS rather than only
        # counting.
        _SILENT["rerest_fail_create"] += 1
        print(f"WARNING re-rest of EXIT offset REJECTED on {ticker} pos={pos:+.2f} "
              f"{side} {cnt}@{price}: {_err_detail(e)}")
        return None


def flatten_to_zero(client, ticker, standing_oids=None, tries=4):
    """LAST-RESORT taker de-risk of ONE ticker to flat — the sole taker path. Cancels our
    resting orders on the ticker first (avoid a self-trade cross), then crosses the residual
    with marketable IOC orders.

    OVERSHOOT-SAFE: reads the starting signed position ONCE and HARD-CAPS cumulative crossing
    at |pos0|, decrementing by the venue's CONFIRMED fill_count each pass (never by a possibly-
    lagging positions re-read — an eventually-consistent read could otherwise re-cross full
    size and flip a long into a short). The get_positions re-poll is a SECONDARY check only.

    FIX 4 (2026-07-27 live defect): the cancel phase must be CONFIRMED, not best-effort. The old
    loop cancelled only the CALLER-PASSED oids inside try/except and crossed regardless — on
    2026-07-27 that crossed KXNDQHUD flat while its maker offset still rested, and the stale order
    re-opened the position 7s later (+40.55 ct). Now: every resting order on the ticker is
    cancelled (venue read, hint oids supplementary) and the clear book is CONFIRMED by re-read;
    an unconfirmed cancel ABORTS the cross for this invocation. Behaviour change, deliberate:
    cross-despite-failed-cancel -> refuse-to-cross. Both callers handle a False return as
    "position kept, maker exit still working" (settle-taker falls through to maker unwind;
    STOP escalation prints RESIDUAL/check-manually) — the un-cancelled order IS the maker exit,
    so nothing is stranded by refusing.
    Returns (flat_bool, n_crossed)."""
    cleared, _n = _cancel_ticker_resting_confirmed(client, ticker, standing_oids)
    if not cleared:
        return False, 0                                    # never cross over a possibly-live exit
    try:
        pos0 = _held_cost(client)[1].get(ticker, 0.0)      # STARTING signed position, read ONCE
    except Exception:
        return False, 0                                    # blind -> stop (fail closed)
    if abs(pos0) < INV_TOLERANCE:
        return True, 0
    long_yes = pos0 > 0
    remaining = int(round(abs(pos0)))                      # hard cap on cumulative crossing
    crossed = 0
    first_px = None                                        # slippage anchor: the FIRST pass's touch
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
        # SLIPPAGE BOUND (Q7): each pass re-reads the touch, and on a thin book our own fills
        # move it — the 07-27 STOP escalation dumped into a 27c-worse touch on pass 4. Refuse a
        # pass whose price has deteriorated more than FLATTEN_MAX_SLIP from the first pass
        # (deterioration = a LOWER sell for long-yes, a HIGHER buy for long-no).
        if FLATTEN_MAX_SLIP > 0:
            if first_px is None:
                first_px = price
            elif (first_px - price if long_yes else price - first_px) > FLATTEN_MAX_SLIP:
                print(f"flatten: {ticker} touch moved {abs(price - first_px):.2f} against us "
                      f"(bound {FLATTEN_MAX_SLIP:.2f}) — refusing further crosses this pass")
                break
        try:
            resp = client.create_order_v2(ticker, side, remaining, price,
                                          time_in_force="immediate_or_cancel", post_only=False)
            o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
            o = o or {}
            fill = float(o.get("fill_count_fp") or o.get("fill_count") or 0)   # _fp-first: GET-orders
            # already migrated (probe 2026-07-29T18:50Z: fill_count=None, fill_count_fp set);
            # the create-response still serves the legacy key (live crosses print nonzero),
            # so the chain covers both dialects (self-audit A1-F1).           # CONFIRMED fill (venue-authoritative)
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
    # RE-REST (fix 4 leg 3, mirrored from _taker_cross_capped): the cancel phase above cleared
    # the ticker's resting exit; if the cross left a residual, put a maker exit back so the
    # position keeps a working exit inside this cycle (STOP/settle paths re-rest next cycle
    # regardless — this closes the one-cycle gap).
    if remaining >= max(1, int(INV_TOLERANCE)):
        _pos = float(remaining) if long_yes else -float(remaining)
        if _rest_maker_offset(client, ticker, _pos, 0.0, "flatrerest") is None:
            print(f"WARNING flatten residual {remaining} ct on {ticker} and the re-rest FAILED "
                  f"— no working exit until the next cycle")
    # SECONDARY consistency check (never the driver); fall back to our own confirmed count.
    try:
        return abs(_held_cost(client)[1].get(ticker, 0.0)) < INV_TOLERANCE, crossed
    except Exception:
        return remaining < max(1, int(INV_TOLERANCE)), crossed


def _err_detail(e, limit=240):
    """Render an exception for humans INCLUDING the HTTP status and response body.

    Defect 10 root fix (2026-08-02). `{e!r}` on an HTTPError renders
    `HTTPError(400, 'Bad Request')` — the venue's actual reason ("would cross", "market not
    active", a tick-grid complaint...) lives in the BODY and was thrown away at every catch
    site. That is why an UNWIND rejection could repeat identically for 19 minutes with nothing
    in the log explaining it.

    The body is already available: maker_kalshi_client._request raises
    urllib.error.HTTPError(url, status, reason, headers, io.BytesIO(raw)) on the pooled path
    (:159-163) and urlopen raises the same shape on the other, so `e.read()` returns the
    venue's payload without any client-side change.

    SECRETS: an HTTPError carries the RESPONSE headers, never our request headers, so the API
    key, the signature and the timestamp cannot appear here. Only the venue's own reply is
    rendered, bounded to `limit` chars. Reading the body is one-shot, so it is read once and
    tolerated as empty if something upstream already consumed it."""
    try:
        status = getattr(e, "code", None)
        if status is None:
            return repr(e)[:limit]
        body = ""
        try:
            raw = e.read()
            body = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        except Exception:
            body = "<body unavailable>"
        return f"HTTP {status} {getattr(e, 'reason', '')!s} body={body.strip()}"[:limit]
    except Exception:
        return "<unrenderable error>"


def _cancel_each(client, ids):
    """Cancel each id, isolating failures, and return how many succeeded.

    NEW helper 2026-08-02 (not a refactor of an existing one): the STOP flatten now cancels
    from three places instead of one, and the try/except-pass body must be identical at all
    three or the cover guarantee differs by path."""
    n = 0
    for oid in ids:
        try:
            client.cancel_order(oid)
            n += 1
        except Exception:
            pass
    return n


def _keep_verbatim(orders):
    """Re-express resting orders as DESIRED, so diff_orders emits neither cancel nor create.

    The retention idiom is apply_drop_grace's (:2426-2428), reused rather than reinvented.
    Operator decision 2026-08-02: on a STOP bail path — book unreadable, reducing side
    unpriceable, or the loss cap leaving no priceable offset — the existing exit is KEPT. We
    could not compute a replacement, and destroying an exit you cannot replace is strictly
    worse than holding a stale one; the staleness is bounded by pass 2's taker escalation,
    which re-reads positions and fires regardless. All three bail paths use this same rule."""
    return [{"side": o["side"], "price_dollars": o["price_dollars"], "count": o["count"]}
            for o in (orders or [])]


def _flatten_all(client):
    """EMERGENCY-STOP de-risk: MAKER-FIRST with BOUNDED ESCALATION (audit HIGH-1).
    Pure-taker STOP is a fire-sale (realizes the loss + pays the spread); pure-maker STOP can
    leave the book hanging on offsets that never fill — as wrong in the other direction. So:
      1. cancel every resting quote (stop making),
      2. rest a PASSIVE maker offset on the reducing side of each held position ($0 fee),
      3. WAIT STOP_ESCALATE_S for them to fill,
      4. re-read; whatever is STILL >= STOP_TAKER_MIN_CT gets taker-crossed — bounded, sized
         to the residual only, never the whole book. Below-threshold residue is left/reported."""
    # DIFF-AND-KEEP (defect 11 root fix, operator-named 2026-08-02). This block used to DESTROY
    # the whole resting book here, before computing what it wanted ~30 lines below — so it
    # structurally could not notice that a resting order already IS the order it is about to
    # place. Live evidence: 27 consecutive flattens in the 08-02 halt window each logged
    # "cancelled 2/2 resting quotes" and immediately re-created the same two offsets with fresh
    # client ids, surrendering queue position every 30 minutes for nothing. The fix gives the
    # STOP path the same DECIDE-THEN-ACT ordering the normal quoting path has had since
    # diff_orders(:2482) — the mechanism is REUSED, not reinvented.
    # The "stop making" guarantee is unchanged: every accumulating quote, every order on a
    # ticker we hold no material inventory in, and every unparseable row still dies below,
    # before any book read. Only REDUCING-side orders on held tickers are deferred, and those
    # are exits, which canon wants resting under STOP.
    try:
        orders = client.get_orders("resting").get("orders") or []
    except Exception as e:
        print(f"flatten: could NOT read resting orders ({e!r}) — run flatten_kalshi.py manually")
        orders = []
    standing = _standing_from_rows(orders)             # parsed from the SAME rows; no second GET
    all_ids = [o["order_id"] for o in orders if o.get("order_id")]
    # all_ids is the CANCEL COVER and is built from the raw rows, not from `standing`, so an
    # order we failed to parse is one we cannot reason about and is therefore always cancelled
    # (it can never enter keep_ids, which is derived from parsed rows only). That preserves the
    # old cancel-everything cover exactly.
    # Positions are read BEFORE any cancel now, because the keep/cancel split depends on them.
    # Both early exits stay cover-preserving: they cancel everything first, exactly as today.
    try:
        _tot, _by, _costs = _held_cost(client)
        _naked = ladder_pairing(_by)
        _paired = sum(abs(_by[t]) - abs(_naked.get(t, 0)) for t in _by)
        if _paired > 0:
            print(f"flatten: {_paired:.0f} ct held in FLOORED ladder pairs (risk ~ strike gap) "
                  f"— left to settle, offsetting only the naked remainder")
        held = {t: p for t, p in _naked.items() if abs(p) >= INV_TOLERANCE}
    except Exception as e:
        n = _cancel_each(client, all_ids)
        print(f"flatten: cancelled {n}/{len(all_ids)} resting quotes (stopped making)")
        print(f"flatten: could NOT read positions ({e!r}) — inventory MAY remain, check manually")
        return
    if not held:
        n = _cancel_each(client, all_ids)
        print(f"flatten: cancelled {n}/{len(all_ids)} resting quotes (stopped making)")
        print("flatten: no material inventory — book is flat")
        return
    # STOP MAKING. The reducing side follows from the SIGN alone, so this needs no book read:
    # long yes -> a NO bid reduces; long no -> a YES bid reduces.
    _red = {t: ("no" if p > 0 else "yes") for t, p in held.items()}
    deferred = {t: [o for o in standing.get(t, []) if o["side"] == _red[t]] for t in held}
    keep_ids = {o["order_id"] for ol in deferred.values() for o in ol}
    n = _cancel_each(client, [i for i in all_ids if i not in keep_ids])
    # --- pass 1: MAKER offsets on the reducing side ---
    # per-invocation nonce so a REPEATED STOP run (timer still firing while STOP sentinel present)
    # never reuses a client_order_id — Kalshi dedups on it, so a reused id would reject the fresh
    # offset and force the taker escalation, turning maker-first STOP into a metronomic taker
    # fire-sale on every cycle after the first (review C5).
    _nonce = int(time.time())
    # (offset_oids removed 2026-08-03 on operator ruling — it was written in two places and
    # read NOWHERE in the repo. Pass 2 re-derives through _cancel_ticker_resting_confirmed and
    # takes no hint ids, so the dict recorded state nobody consumed.)
    # DECIDE: build the desired offset per ticker. The computation below is UNCHANGED — only
    # its destination changed, from an immediate create to a `desired` dict the diff consumes.
    desired = {}
    for t, pos in held.items():
        try:
            ob = public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
        except Exception:
            print(f"flatten: {t} pos={pos:+.2f} — book unreadable, will re-check at escalation")
            desired[t] = _keep_verbatim(deferred.get(t))
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
            desired[t] = _keep_verbatim(deferred.get(t))
            continue
        price = _unwind_price(price, _costs.get(t, 0.0))             # loss-capped offset
        if not _ok_exit_price(price):
            print(f"flatten: {t} pos={pos:+.2f} — loss-cap leaves no priceable offset, "
                  f"will re-check at escalation")
            desired[t] = _keep_verbatim(deferred.get(t))
            continue
        cnt = _unwind_size(_capped_join(price, other), price, pos)   # <= |pos|, never overshoot
        desired[t] = [{"side": side, "price_dollars": price, "count": cnt, "reason": "unwind"}]
    # ACT: reuse diff_orders, exactly as the normal quoting path does. An offset survives only
    # if side+price+count match to the same 4dp the normal path uses.
    _std = {t: v for t, v in deferred.items() if v}
    cancels, creates = diff_orders(_std, desired)
    # AT MOST ONE EXIT PER TICKER. diff_orders keys on (side, price, count), so two IDENTICAL
    # resting offsets both survive as one key — leaving 2x|inventory| of resting reducing size,
    # which on a full fill crosses THROUGH flat into the opposite position. Today's
    # cancel-everything hid this; keeping orders exposes it, so it is closed here.
    # The survivor is chosen by DESIRED-KEY MATCH, never by cancel-list membership.
    # BLIND-REVIEW FIX 2026-08-03: keying off `cancels` was wrong because diff_orders builds
    # `have` as a dict comprehension (:2509-2510), so two IDENTICAL resting rows collapse to a
    # single key and only ONE cancel is emitted. The un-cancelled twin then looked like a
    # legitimate survivor, was kept, and a replacement was created on top — reproduced: two
    # stale 20-ct offsets against a 20-ct position left 40 ct resting, which on a full fill
    # crosses THROUGH flat into the opposite position, exactly what _unwind_size's own
    # docstring (:1691-1693) forbids. Cancel-everything used to hide this; keeping exposes it.
    # Now: at most ONE order per ticker survives, and only if it matches what we want to rest.
    _cancel_set = set(cancels)
    kept = 0
    for t, rows in _std.items():
        _wk = {(q["side"], round(q["price_dollars"], 4), q["count"])
               for q in desired.get(t, [])}
        keeper = None
        for o in rows:
            if keeper is None and (o["side"], round(o["price_dollars"], 4),
                                   o["count"]) in _wk:
                keeper = o
                continue
            if o["order_id"] not in _cancel_set:
                cancels.append(o["order_id"])
                _cancel_set.add(o["order_id"])
        if keeper is not None:
            kept += 1
    n += _cancel_each(client, cancels)
    print(f"flatten: cancelled {n}/{len(all_ids)} resting quotes (stopped making)")
    if kept:
        print(f"flatten: KEPT {kept} identical exit offset(s) — no cancel/re-create "
              f"(queue position preserved)")
    for i, c in enumerate(creates):
        t, side, price, cnt = c["ticker"], c["side"], c["price_dollars"], c["count"]
        pos = held.get(t, 0.0)
        try:
            client.create_quote(t, side, price, cnt, post_only=True,
                                client_order_id=f"mk-stopflat-{_nonce}-{i}-{side}")
            print(f"flatten: {t} pos={pos:+.2f} -> rested MAKER offset {side} {cnt}@{price} (passive)")
        except Exception as e:
            print(f"flatten: {t} pos={pos:+.2f} — offset REJECTED ({_err_detail(e)}), "
                  f"will re-check at escalation")
    # --- pass 2: bounded escalation — give passive a real chance, then taker the RESIDUAL ---
    if STOP_ESCALATE_S > 0:
        # POLLED WAIT (operator-named 2026-08-03). This was one straight-line sleep of
        # STOP_ESCALATE_S, executed while holding the single-instance run lock — so the daemon
        # loop stalled for the full window on every flatten, including the auto-halt cycle.
        # The window itself is a MONEY parameter (it is how long passive offsets get to fill
        # before we pay the spread), so it is NOT shortened: the wait still runs to
        # STOP_ESCALATE_S whenever inventory remains. It now simply STOPS EARLY once there is
        # nothing left to escalate, which is the only case where waiting buys nothing.
        # Strictly conservative by construction: the early exit requires a SUCCESSFUL read
        # showing no residual at or above the taker threshold; any read failure keeps waiting,
        # so a blind cycle can never cut the passive window short.
        print(f"flatten: waiting up to {STOP_ESCALATE_S}s for passive offsets to fill...")
        _waited = 0.0
        while _waited < STOP_ESCALATE_S:
            _step = min(5.0, STOP_ESCALATE_S - _waited)
            time.sleep(_step)
            _waited += _step
            try:
                _resid = {t: p for t, p in ladder_pairing(_held_cost(client)[1]).items()
                          if abs(p) >= STOP_TAKER_MIN_CT}
            except Exception:
                continue                      # unreadable -> keep waiting (fail conservative)
            if not _resid:
                print(f"flatten: passive offsets cleared the book after {_waited:.0f}s "
                      f"(of {STOP_ESCALATE_S}s) — ending the wait early")
                break
    try:
        # NAKED residual only (self-audit A2-F1, 2026-07-29): pass 1 correctly offset only the
        # unpaired inventory, but this escalation re-read the GROSS position and handed it to
        # flatten_to_zero — which crosses ALL of it, de-pairing floored ladder pairs and
        # cascading a second cross on the orphaned sibling. That is the exact defect audit F3
        # fixed in the settle-taker the same morning; this ports the same fix to the STOP path:
        # cap at |naked| via _taker_cross_capped so a paired leg is provably never crossed.
        residual = {t: p for t, p in ladder_pairing(_held_cost(client)[1]).items()
                    if abs(p) >= STOP_TAKER_MIN_CT}
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
        try:
            ok, c = _taker_cross_capped(client, t, int(round(abs(pos))), pos > 0,
                                        cost=_costs.get(t, 0.0))
        except Exception as e:
            print(f"flatten: ESCALATION RAISED on {t} pos={pos:+.2f}: {e!r} — check manually")
            continue
        print(f"flatten: ESCALATED {t} naked={pos:+.2f} -> taker residual "
              f"{'FLAT' if ok else 'RESIDUAL (check manually)'} ({c} ct crossed)")


def _taker_cross_capped(client, ticker, cap_ct, long_yes, tries=4, cost=0.0):
    """Cross AT MOST cap_ct contracts to the REDUCING side with marketable IOC orders.
    ORDERING (FIX 4, operator-approved 2026-07-27): CANCEL (confirmed) -> CROSS -> RE-REST.

    Contrast with flatten_to_zero, which reads the ticker's FULL venue position and crosses ALL
    of it — including a paired leg. Here the caller passes cap_ct = |naked| (the UNPAIRED residual
    only, from ladder_pairing). Crossing is HARD-CAPPED at cap_ct and decremented by the venue's
    CONFIRMED fill_count each pass (never a lagging positions re-read), so cumulative crossing can
    PROVABLY never exceed |naked| — a paired leg is never touched (reason 1).

    THE OLD CONTRACT WAS "ADDITIVE — cancels NOTHING", argued on no-self-trade grounds. Self-trade
    was never the risk. An exit order must not outlive its position: live 2026-07-27 19:40:03Z a
    taker crossed the position flat and the un-cancelled resting exit filled 7 SECONDS later
    (+40.55 ct) — a naked ENTRY at the exact moment we thought we were out. The venue's
    self_trade_prevention_type="taker_at_cross" already prevents self-match, so cancelling first
    costs nothing. Never-strand (old reason 3) is now provided by the RE-REST leg: a failed or
    partial cross re-rests the maker exit for the residual, so the position is never left with no
    working exit — and an UNCONFIRMED cancel aborts the cross entirely (the un-cancelled order IS
    the exit; refusing to cross strands nothing).
    `cost` feeds _unwind_price for the re-rested offset (vestigial since the Q1 decision; preserves
    the legacy loss-cap pricing exactly when the flag is off).
    Returns (flat_bool, n_contracts_crossed)."""
    remaining = int(round(abs(cap_ct)))
    if remaining < max(1, int(INV_TOLERANCE)):
        return True, 0
    cleared, _n = _cancel_ticker_resting_confirmed(client, ticker)
    if not cleared:
        return False, 0                                    # never cross over a possibly-live exit
    crossed = 0
    first_px = None                                        # slippage anchor (Q7), per invocation
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
        # SLIPPAGE BOUND (Q7, same knob as flatten_to_zero): never chase a collapsing touch
        # within one burst. The anchor is per-invocation, so successive cycles/clock periods can
        # still follow the market down — one bounded step at a time, with the maker exit
        # re-rested in between.
        if FLATTEN_MAX_SLIP > 0:
            if first_px is None:
                first_px = price
            elif (first_px - price if long_yes else price - first_px) > FLATTEN_MAX_SLIP:
                print(f"taker-cross: {ticker} touch moved {abs(price - first_px):.2f} against us "
                      f"(bound {FLATTEN_MAX_SLIP:.2f}) — refusing further crosses this pass")
                break
        try:
            resp = client.create_order_v2(ticker, side, remaining, price,
                                          time_in_force="immediate_or_cancel", post_only=False)
            o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
            o = o or {}
            fill = float(o.get("fill_count_fp") or o.get("fill_count") or 0)   # _fp-first: GET-orders
            # already migrated (probe 2026-07-29T18:50Z: fill_count=None, fill_count_fp set);
            # the create-response still serves the legacy key (live crosses print nonzero),
            # so the chain covers both dialects (self-audit A1-F1).             # CONFIRMED fill (venue-authoritative)
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
    if crossed > 0:
        # A-governor feed: ONE paid-exit episode per invocation with any fill (re-review L10:
        # per-IOC-pass counting made a single 3-slice flatten look like 3 episodes). Persisted
        # by the pre-save re-sync (H1 fix).
        _TAKER_XN[ticker] = _TAKER_XN.get(ticker, 0) + 1
    flat = remaining < max(1, int(INV_TOLERANCE))
    if not flat:
        # RE-REST (fix 4 leg 3): the cross failed or partially filled and we cancelled the maker
        # exit above — put one back for the residual so the position keeps a working exit inside
        # this cycle (the normal unwind path re-rests next cycle regardless).
        _pos = float(remaining) if long_yes else -float(remaining)
        if _rest_maker_offset(client, ticker, _pos, cost, "rerest") is None:
            print(f"WARNING taker-cross residual {remaining} ct on {ticker} and the re-rest "
                  f"FAILED — no working exit until the next cycle's unwind pass")
    return flat, crossed


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
                            close_time_of=_default_preclose_close_time, costs_by=None):
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
         same cycle) + a STOP_ESCALATE_S grace; the taker then runs CANCEL(confirmed) -> CROSS ->
         RE-REST (fix 4, 2026-07-27: the old additive/no-cancel contract let an exit outlive its
         position and re-open it). A cross that fails or partially fills re-rests the maker exit
         for the residual; an unconfirmed cancel refuses to cross at all.

    grace_state: {ticker: iso_first_seen_naked_in_window}, persisted in quoter_state across cycles
    (a per-cycle process; the clock cannot live in memory). Cleared when a ticker leaves the window
    or goes flat. Mutated in place. Guarded by PRECLOSE_FLATTEN + non-dry_run at the call site;
    telemetry (plan keys) is written ONLY when the mechanism actually engages, so a flag-OFF plan
    row is byte-identical to legacy."""
    if not held_by:
        return []
    crossed = []       # tickers we PAID a taker to leave -> the caller's re-entry cooldown feed
    naked_by = ladder_pairing(held_by)
    # STALE-STAMP PRUNE (self-audit A2-F3, 2026-07-29): a ticker that went fully FLAT vanishes
    # from held_by/naked_by, so the per-ticker loop below never visits it and its grace stamp
    # lived forever — re-entering that ticker days later, the ancient stamp made grace_elapsed
    # enormous and the maker-first grace was silently skipped (first window cycle went straight
    # to taker). Prune exactly like _strand_cross does.
    for t in list(grace_state):
        if abs(naked_by.get(t, 0.0)) < INV_TOLERANCE:
            grace_state.pop(t, None)
    _pc_taker_mkts = 0     # per-invocation market cap, same budget as the settle-taker
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
        if _pc_taker_mkts >= TAKER_MAX_MKTS:
            continue                                       # per-invocation cost cap (mirrors settle-taker)
        # TAKER: cross AT MOST |naked| — cancel-confirmed first, re-rest after (reasons 1 & 3).
        if client.mode == "dry_run":
            continue                                       # never taker in plan-only mode
        try:
            flat, nc = _taker_cross_capped(client, t, int(round(abs(npos))), npos > 0,
                                           cost=(costs_by or {}).get(t, 0.0))
        except Exception as e:
            plan["preclose_taker_failed"] = plan.get("preclose_taker_failed", 0) + 1
            print(f"WARNING preclose flatten RAISED on {t} (naked {npos:+.2f}): {e!r}")
            continue
        if nc:
            crossed.append(t)
        _pc_taker_mkts += 1
        plan["preclose_taker_ct"] = round(plan.get("preclose_taker_ct", 0.0) + nc, 2)
        print(f"preclose flatten {t}: naked {npos:+.2f}, {mins:.1f}min to close -> taker crossed "
              f"{nc} ct ({'FLAT' if flat else 'RESIDUAL — maker exit re-rested'})")
        if flat:
            grace_state.pop(t, None)                       # naked cleared -> forget the clock
        else:
            plan["preclose_taker_failed"] = plan.get("preclose_taker_failed", 0) + 1
            # RE-ARM (self-audit A2-F2, 2026-07-29): without this the elapsed grace stayed far
            # past STOP_ESCALATE_S forever, so the next daemon cycle (5-8s) fired ANOTHER
            # tries=4 IOC burst with a fresh slippage anchor — repetition defeated the Q7 slip
            # bound (the 07-27 STOP escalation walked DXY 0.52 -> 0.25 exactly this way).
            # Same mechanism as _strand_cross: consecutive bursts >= STOP_ESCALATE_S apart.
            grace_state[t] = now.isoformat()
    return crossed


def _strand_cross(client, naked_by, costs_by, now, plan, strand_state, step_state=None,
                  touch_state=None):
    """FIX 3 (operator-approved 2026-07-27): cross a STRANDED exit after a bounded wait.

    The 07-27 loss shape: the maker exit rests, the market trends, the exit never fills, and
    nothing forces the issue until the settlement window — 42 ct rode from a -$0.59 touch exit to
    -$15.29 at settlement. The pre-close flatten only arms near MARKET CLOSE; this is the same
    grace-then-taker mechanism WITHOUT the close-window gate, so a strand is bounded in TIME
    everywhere, not just at the end of a market's life.

    Mechanics, in order (each one is a pinned test):
      - per-ticker clock in `strand_state` (persisted as quoter_state['strand_grace']; cycles are
        fresh processes) stamped when a NAKED residual >= INV_TOLERANCE first appears; cleared
        when the ticker's naked residual drops below tolerance. The clock is a PROXY for "the
        exit has been resting unfilled this long" — the unwind path rests the exit on the same
        cycle the residual appears, so the two clocks are the same to within one cycle.
      - fires only after STRAND_CROSS_S seconds AND only on residuals >= STOP_TAKER_MIN_CT (the
        house taker threshold; smaller strands keep their maker exit only).
      - the taker leg needs live mode AND TAKER_FLATTEN=1 — clock and telemetry run regardless.
      - ONE capped IOC pass per ticker per firing (tries=1), then the clock RE-ARMS: consecutive
        crosses are >= STRAND_CROSS_S apart, so a thin book is walked at most one touch-level per
        period (the 07-27 STOP escalation walked DXY 0.52 -> 0.25 in 4 back-to-back IOCs; this
        path cannot).
      - the pass re-reads positions FRESH from the venue and re-derives the naked residual —
        the cap is min(fresh |naked|), never the cycle-start snapshot (a fill between the
        snapshot and the cross must shrink the cross, not be crossed again).
      - _taker_cross_capped supplies fix 4's cancel(confirmed) -> cross -> re-rest ordering, so
        the exit can never outlive the position and a partial cross never strands the residual."""
    for t in list(strand_state):
        if abs(naked_by.get(t, 0.0)) < INV_TOLERANCE:
            strand_state.pop(t, None)                      # cleared -> forget the clock
            if step_state is not None:
                step_state.pop(t, None)                    # ...and the exit ladder step with it
            if touch_state is not None:
                touch_state.pop(t, None)                   # H2: stale anchors poison future exits
    due = []
    for t, npos in naked_by.items():
        if abs(npos) < INV_TOLERANCE:
            continue
        first = strand_state.setdefault(t, now.isoformat())
        try:
            elapsed = (now - parse_iso(first)).total_seconds()
        except Exception:
            strand_state[t] = now.isoformat()              # unparseable stamp -> restart the clock
            continue
        if elapsed >= STRAND_CROSS_S and abs(npos) >= STOP_TAKER_MIN_CT:
            due.append(t)
    if not due:
        return []
    plan["strand_due"] = len(due)
    if client.mode == "dry_run" or not TAKER_FLATTEN:
        return []                                          # clock + telemetry only, never a taker
    try:
        fresh_naked = ladder_pairing(_held_cost(client)[1])
    except Exception:
        plan["strand_read_failed"] = plan.get("strand_read_failed", 0) + 1
        return []                                          # blind -> do not cross (fail closed)
    crossed = []       # tickers we PAID a taker to leave -> the caller's re-entry cooldown feed
    for t in due:
        npos = fresh_naked.get(t, 0.0)
        if abs(npos) < STOP_TAKER_MIN_CT:
            strand_state.pop(t, None)                      # reduced since the snapshot -> stand down
            if step_state is not None:
                step_state.pop(t, None)
            if touch_state is not None:
                touch_state.pop(t, None)
            continue
        # EXIT LOSS-MIN CALCULATOR (operator-named 2026-07-31): compute the EXACT cost of
        # crossing right now (half-spread + receipt-verified fee) before paying it. Cheap or
        # unavoidable -> cross exactly as before. Otherwise step the maker exit ladder: the
        # unwind quote re-prices one tick inside the spread next cycle (_STRAND_STEP mirror ->
        # desired_quotes), and the clock re-arms — bounded at EXIT_LADDER_STEPS periods, then
        # the taker backstop fires regardless. Receipt failure (one-sided/unreadable book)
        # falls through to the legacy cross: the calculator must never BLOCK a bounded exit.
        _rec = None
        _force_cross = False
        if step_state is not None and EXIT_LADDER_STEPS > 0:
            try:
                _ob5 = public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
                _yb5, _ya5 = _touch(_ob5)
                _rec = kalshi_exit_calc.cross_receipt(abs(npos), npos > 0, _yb5, _ya5)
            except Exception:
                _rec = None
            if (_rec is not None and touch_state is not None and SWEEP_VETO_TICKS > 0):
              try:                                   # L9: one corrupt entry must not kill the
                _prev5 = touch_state.get(t)          # taker backstop book-wide
                _mv5 = 0.0
                if _prev5:
                    # against-us motion: long yes exits at the yes BID (falling = against);
                    # long no exits at the yes ASK (rising = against)
                    _mv5 = (float(_prev5[0]) - _rec["exec_price"]) if npos > 0                         else (_rec["exec_price"] - float(_prev5[0]))
                _fast5 = _prev5 is not None and _mv5 >= SWEEP_VETO_TICKS * 0.01
                _consec5 = (int(_prev5[1]) + 1 if _fast5 else 0) if _prev5 else 0
                touch_state[t] = [_rec["exec_price"], _consec5]
                if _fast5 and _consec5 >= 2:
                    _force_cross = True              # TREND: pay now, skip the ladder
                    plan["exit_trend_cross"] = plan.get("exit_trend_cross", 0) + 1
                elif _fast5:
                    strand_state[t] = now.isoformat()    # SPIKE: defer ONE pass, clock re-arms
                    plan["exit_sweep_veto"] = plan.get("exit_sweep_veto", 0) + 1
                    print(f"exit-calc {t}: touch moved {_mv5:.2f} against in one period — "
                          f"sweep veto, deferring one pass (next fast move crosses)")
                    continue
              except Exception:
                touch_state.pop(t, None)             # self-heal the corrupt entry, cross legacy
                _SILENT["strand_touch_parse_fail"] += 1
            if _rec is not None and not _force_cross:
                _step5 = int(step_state.get(t, 0) or 0)
                if kalshi_exit_calc.decide(_rec, _step5, EXIT_LADDER_STEPS,
                                           EXIT_CHEAP_CROSS_USD) == "improve":
                    step_state[t] = _step5 + 1
                    _STRAND_STEP[t] = _step5 + 1           # same-cycle mirror for desired_quotes
                    strand_state[t] = now.isoformat()      # re-arm: a ladder step, not a cross
                    plan["exit_ladder_stepped"] = plan.get("exit_ladder_stepped", 0) + 1
                    plan["exit_ladder_would_pay_usd"] = round(
                        plan.get("exit_ladder_would_pay_usd", 0.0)
                        + _rec["taker_cost_usd"], 4)       # receipt-validation telemetry
                    print(f"exit-calc {t}: cross {abs(npos):.0f} ct would cost "
                          f"${_rec['taker_cost_usd']:.2f} (spread {_rec['spread_ticks']}t "
                          f"${_rec['half_spread_usd']:.2f} + fee ${_rec['fee_usd']:.2f}) — "
                          f"maker ladder step {_step5 + 1}/{EXIT_LADDER_STEPS} instead")
                    continue
        if _rec is None:
            plan["exit_cross_unpriced"] = plan.get("exit_cross_unpriced", 0) + 1
        try:
            flat, nc = _taker_cross_capped(client, t, int(round(abs(npos))), npos > 0,
                                           tries=1, cost=(costs_by or {}).get(t, 0.0))
        except Exception as e:
            plan["strand_cross_failed"] = plan.get("strand_cross_failed", 0) + 1
            print(f"WARNING strand cross RAISED on {t} (naked {npos:+.2f}): {e!r}")
            continue
        if nc:
            crossed.append(t)
        plan["strand_crossed_ct"] = round(plan.get("strand_crossed_ct", 0.0) + nc, 2)
        if _rec is not None and nc:
            # exact-cost receipt for the contracts actually crossed (auditable ledger line)
            _paid5 = round(_rec["per_ct_usd"] * nc, 4)
            plan["exit_cross_cost_usd"] = round(plan.get("exit_cross_cost_usd", 0.0) + _paid5, 4)
            print(f"exit-calc {t}: PAID ~${_paid5:.2f} to cross {nc} ct "
                  f"(spread {_rec['spread_ticks']}t, fee-rate exact)")
        print(f"strand cross {t}: naked {npos:+.2f} unfilled >= {STRAND_CROSS_S:.0f}s -> taker "
              f"crossed {nc} ct ({'FLAT' if flat else 'RESIDUAL — maker exit re-rested'})")
        if flat:
            strand_state.pop(t, None)
            if step_state is not None:
                step_state.pop(t, None)
            if touch_state is not None:
                touch_state.pop(t, None)
        else:
            plan["strand_cross_failed"] = plan.get("strand_cross_failed", 0) + 1
            strand_state[t] = now.isoformat()              # re-arm: paces the next pass
    return crossed


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
    never treat unknown inventory as $0 (matches the standing-read/reconcile guards).

    SIDE CHANNEL: _REALIZED_BY is refreshed from the same read (realized_pnl_dollars per
    ticker — the venue's own attribution) for the per-market loss governor. A side channel,
    not a 4th return value, so the 3-tuple contract every caller and test pins stays intact
    (Rule 2). Only tickers with a NONZERO position appear (count_filter=position) — a fully
    flat market drops out, which is why the governor LATCHES its trips for the day."""
    pos = client.get_positions()          # may raise -> caller defers all creates
    by, costs, total = {}, {}, 0.0
    _REALIZED_BY.clear()
    for p in (pos.get("market_positions") or []):
        # PROD-VERIFIED 2026-07-20: field is position_fp (string, fractional, signed);
        # 'position' does not exist -> reading it silently blinded the committed cap.
        try:
            _REALIZED_BY[p.get("ticker")] = float(p.get("realized_pnl_dollars") or 0.0)
        except (TypeError, ValueError):
            pass
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
    def _fail(key="strike_parse_failed"):
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1
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
    tail = "-".join(fields).lstrip("T")
    try:
        return float(tail)
    except (ValueError, TypeError):
        # RF3 counter split (1.1 review 2026-07-31): a digit-free tail is a CATEGORICAL
        # outcome (-HELLO, -LAL) — unpairable BY DESIGN, ~250/day, and it was drowning the
        # loud warning that exists for STRUCTURAL failures (a digit-bearing tail that still
        # won't parse = shape change = pairing gone dark). Two counters, one warning.
        if tail and any(c.isalpha() for c in tail):
            # audit 2026-08-02 (lens 1 #2): categorical outcome codes are frequently
            # ALPHANUMERIC (A5, H2, CLAU5, AUG05) — the digit-free rule left the live
            # held inventory warning every cycle, keeping the alarm fatigue RF3 exists
            # to kill. Any letter in the tail = categorical (silent counter); a tail of
            # digits/punctuation that still fails float = mangled numeric = structural.
            return _fail("strike_categorical")
        return _fail()      # numeric-looking OR empty tail = structural, stays loud


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


def _ladder_pairs(held_by):
    """The MATCHED pairs ladder_pairing nets out, as [(long_ticker, short_ticker, qty)] —
    the SAME greedy walk (lowest longs vs highest shorts, strictly long_strike <
    short_strike; the unfloored combo is never matched). Kept in lockstep with
    ladder_pairing by a property test: pairs + naked must reconstruct held exactly."""
    naked = dict(held_by)
    by_event = defaultdict(list)
    for t, n in (held_by or {}).items():
        if not n:
            continue
        s = _strike_of(t)
        if s is not None:
            by_event[_event_key(t)].append((s, t))
    out = []
    for rows in by_event.values():
        pos = sorted((s, t) for s, t in rows if naked.get(t, 0) > 0)
        neg = sorted(((s, t) for s, t in rows if naked.get(t, 0) < 0), reverse=True)
        i = j = 0
        while i < len(pos) and j < len(neg):
            ls, lt = pos[i]
            ss, st_ = neg[j]
            if ls >= ss:
                break
            m = min(naked[lt], -naked[st_])
            naked[lt] -= m
            naked[st_] += m
            out.append((lt, st_, m))
            if naked[lt] == 0:
                i += 1
            if naked[st_] == 0:
                j += 1
    return out


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


def _standing_from_rows(orders):
    """Parse raw resting V2 order rows into our (outcome, outcome-price) form.

    Extracted from _live_standing 2026-08-02 so a caller that ALREADY holds the rows can parse
    them without issuing a second GET — the STOP flatten needs exactly that. Per-order parse is
    ISOLATED so one malformed record cannot crash the cycle before cancels/wind-down run."""
    out = defaultdict(list)
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
    return dict(out)


def _live_standing(client):
    """Returns (standing_dict, raw_row_count). Reads resting V2 orders back into our
    (outcome, outcome-price) form. Per-order parse is ISOLATED so one malformed
    record cannot crash the cycle before cancels/wind-down run. The raw_row_count
    lets the caller reconcile (rows>0 but parsed==0 => parse failure => halt)."""
    orders = client.get_orders("resting").get("orders") or []
    return _standing_from_rows(orders), len(orders)


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

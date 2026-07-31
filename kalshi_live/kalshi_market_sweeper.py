#!/usr/bin/env python3
"""BACKGROUND VENUE SWEEPER — keep the market-score cache fresh for ALL active programs.

THE PROBLEM THIS SOLVES (freshness root-fix plan 2026-07-30, §2c: throughput starvation).
The rolling score cache (kalshi_market_scores.json) is fed only by books the trading cycle
already reads: ~7 REST reads per ~63s cycle (plans-20260730.jsonl, 1,339 cycles) against
3,582 active programs (API 14:31:32Z 2026-07-30). Net new scored markets in a full day: +10.
At that rate the sweep never completes AND never stays fresh — cache-at-rest median score age
was ~3.6 days with only 9% of scores under 30 minutes old (read 14:34:00Z 2026-07-30).

THE FIX. A single daemon thread that walks EVERY active program continuously at its own pace,
independent of the trading cycle, and folds a PROSPECTIVE capture measurement into the same
score cache under a shared lock. Verified budget (docs.kalshi.com/getting_started/rate_limits,
read 2026-07-30): Basic tier = 200 read tokens/s, most calls cost 10 tokens => 20 reads/s
allowed; READ and WRITE buckets are SEPARATE, so sweeping can never starve order operations.
Default pace here is 1 read/s = 10 tokens/s = 5% of the Basic read bucket; the trading
cycle's own reads ride on a different pacer and are unaffected.

WHAT IT STORES. Prospective capture (what we WOULD capture joining at reference — the same
M7 model the join gate uses; over-predicts 2-6x vs actual, a RELATIVE signal) goes into
SEPARATE row keys (pcap/pref/pts/pn) via kalshi_market_scores.update_prospective. It NEVER
touches the actual-capture keys (capture/ts/ref/ref_move/n) — model numbers must never
masquerade as measurements. Live ranking DOES NOT consume pcap yet: wiring it in is Phase 3
of the plan (haircut + age cutoff in one change, shadow-first, receipts-calibrated).

FUNCTION-LOSS GUARANTEES (operator constraint 2026-07-30: "quicker on scans but dont lose
function"):
  - Own urllib opener + own pacer: shares NOTHING with the cycle's public_get budget/pacing.
  - Read-only versus the venue: never places, amends, or cancels anything.
  - All cache writes go through the caller-supplied lock (the quoter wraps every one of its
    own SCORES touchpoints in the same lock).
  - The thread is daemon=True and every loop iteration is wrapped: a sweeper fault degrades
    to "cache stops getting fresher", never to a broken trading cycle.
  - Ships OFF: KALSHI_SWEEP_ENABLED=0 default => start() returns None and nothing runs.

429 handling: exponential backoff (SWEEP_BACKOFF_BASE_S doubling to SWEEP_MAX_BACKOFF_S),
reset on the next success. Other transport errors: counted, short fixed sleep, next ticker.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

PROD_BASE = "https://api.elections.kalshi.com"


def _envf(k, d):
    try:
        return float(os.environ.get(k, d))
    except (TypeError, ValueError):
        return d


def _envi(k, d):
    try:
        return int(os.environ.get(k, d))
    except (TypeError, ValueError):
        return d


SWEEP_ENABLED = _envi("KALSHI_SWEEP_ENABLED", 0)
SWEEP_SPACING_S = _envf("KALSHI_SWEEP_SPACING_S", 1.0)
SWEEP_PROGS_REFRESH_S = _envf("KALSHI_SWEEP_PROGS_REFRESH_S", 900.0)
SWEEP_BACKOFF_BASE_S = _envf("KALSHI_SWEEP_BACKOFF_BASE_S", 2.0)
SWEEP_MAX_BACKOFF_S = _envf("KALSHI_SWEEP_MAX_BACKOFF_S", 300.0)
# How many tickers to (re)plan per pass before re-consulting ages. Small enough that a
# just-quoted market's fresh actual score is honored soon; large enough to amortize sorting.
SWEEP_CHUNK = _envi("KALSHI_SWEEP_CHUNK", 50)


class Sweeper:
    """The schedule/pacing engine. Pure infrastructure: the MEASUREMENT (book -> prospective
    capture) is supplied by the quoter as `measure(m, ob) -> (pcap, ref_yes) | None`, and the
    STORE is supplied as `store(ticker, pcap, ref_yes) -> None` (the quoter binds it to
    kalshi_market_scores.update_prospective under its SCORES lock). This module never imports
    the quoter, so there is no circular import and the R4 walk exists in exactly one place."""

    def __init__(self, ages_fn, measure, store, get=None, now_fn=None, sleep_fn=None):
        self.ages_fn = ages_fn      # () -> {ticker: last_observed_epoch} (quoter, under lock)
        self.measure = measure
        self.store = store
        self.get = get or self._rest_get
        self.now = now_fn or time.time
        self.sleep = sleep_fn or time.sleep
        self.stats = {"reads": 0, "stored": 0, "err_429": 0, "err_other": 0,
                      "progs": 0, "passes": 0}
        self._progs = []            # [{ticker, usd_day, target, df}]
        self._progs_ts = 0.0
        self._backoff = 0.0
        self._last_req = 0.0
        self.stop_event = threading.Event()

    def _rest_get(self, path):
        req = urllib.request.Request(PROD_BASE + path,
                                     headers={"User-Agent": "kalshi-market-sweeper/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    # -- pure planning (unit-tested offline) -----------------------------------------------

    @staticmethod
    def rows_from_programs(progs):
        """Program list -> sweepable rows. Deliberately WIDER than select_footprint: no
        allowlist/deny/late-life/far-close cuts — the sweep's job is data on the WHOLE venue
        (a denied family stays measured so 'TABLED NOT DEAD' decisions have receipts).
        R1 CANON: period_reward/10000 IS the daily per-market pool — never divide by window
        length (see the refutation note at maker_kalshi_quoter.select_footprint)."""
        rows = []
        for p in progs or []:
            if (p.get("incentive_type") or "liquidity") != "liquidity":
                continue
            t = p.get("market_ticker")
            if not t or p.get("target_size_fp") is None:
                continue
            try:
                target = float(p["target_size_fp"])
            except (TypeError, ValueError):
                continue
            df = None
            if p.get("discount_factor_bps") is not None:
                try:
                    df = float(p["discount_factor_bps"]) / 10000.0
                except (TypeError, ValueError):
                    df = None
            rows.append({"ticker": t, "usd_day": (p.get("period_reward") or 0) / 10000,
                         "target": target, "df": df})
        return rows

    @staticmethod
    def order_worklist(rows, ages, now):
        """Oldest-first: never-observed tickers first (age = +inf), then by last observation
        (actual OR prospective, whichever is newer — the quoter's ages_fn owns that max)."""
        return sorted(rows, key=lambda r: -(now - ages.get(r["ticker"], float("-inf"))))

    # -- the loop --------------------------------------------------------------------------

    def _pace(self):
        wait = SWEEP_SPACING_S - (self.now() - self._last_req)
        if wait > 0:
            self.sleep(wait)
        self._last_req = self.now()

    def _refresh_programs(self):
        if self._progs and (self.now() - self._progs_ts) < SWEEP_PROGS_REFRESH_S:
            return
        # PAGINATED (blind-review HIGH, 2026-07-31): a single unfollowed page silently shrinks
        # "every active program" to "page 1 forever" the day the venue caps the page — the
        # exact coverage bias this module exists to kill. Cursor loop, bounded like the
        # quoter's own 5-page defensive walk (20 here: venue ~3.8k programs / page cap unknown).
        progs, cursor = [], ""
        for _ in range(20):
            self._pace()
            url = "/trade-api/v2/incentive_programs?status=active&limit=10000"
            data = self.get(url + (f"&cursor={cursor}" if cursor else ""))
            progs.extend(data.get("incentive_programs") or [])
            cursor = data.get("next_cursor") or ""
            if not cursor:
                break
        else:
            self.stats["progs_pages_capped"] = self.stats.get("progs_pages_capped", 0) + 1
        self._progs = self.rows_from_programs(progs)
        self._progs_ts = self.now()
        self.stats["progs"] = len(self._progs)

    def _sweep_one(self, m):
        self._pace()
        raw = self.get(f"/trade-api/v2/markets/{m['ticker']}/orderbook")
        ob = raw.get("orderbook_fp") or {}
        self.stats["reads"] += 1
        # RENAME TRIPWIRE (blind-review 2026-07-31): a venue rename of orderbook_fp/side keys
        # would make every book measure $0 while reads/stored look healthy. Count the shape.
        if not ob or (ob.get("yes_dollars") is None and ob.get("no_dollars") is None):
            self.stats["empty_or_unreadable_books"] = \
                self.stats.get("empty_or_unreadable_books", 0) + 1
        res = self.measure(m, ob)
        if res is not None:
            pcap, ref_yes = res
            self.store(m["ticker"], pcap, ref_yes)
            self.stats["stored"] += 1
        self._backoff = 0.0

    def run(self):
        """Forever loop (until stop_event). One iteration = refresh programs if stale, then
        sweep the SWEEP_CHUNK stalest tickers, re-consulting ages between chunks so freshly
        quoted markets fall to the back of the queue immediately."""
        while not self.stop_event.is_set():
            try:
                self._refresh_programs()
                if not self._progs:
                    self.sleep(min(SWEEP_PROGS_REFRESH_S, 60.0))
                    continue
                ages = self.ages_fn()
                work = self.order_worklist(self._progs, ages, self.now())[:SWEEP_CHUNK]
                for m in work:
                    if self.stop_event.is_set():
                        return
                    try:
                        self._sweep_one(m)
                    except urllib.error.HTTPError as e:
                        # PER-ITEM isolation (blind-review livelock fix 2026-07-31): one dead
                        # ticker (404 mid-window) must not abort the whole chunk every pass.
                        # 429 still escalates to the outer backoff; anything else: count, mark
                        # the ticker OBSERVED via the store (pcap 0) so it falls to the back of
                        # the oldest-first queue instead of pinning the head, and move on.
                        if getattr(e, "code", None) == 429:
                            raise
                        self.stats["err_other"] += 1
                        try:
                            self.store(m["ticker"], 0.0, None)
                        except Exception:
                            pass
                self.stats["passes"] += 1
            except urllib.error.HTTPError as e:
                if getattr(e, "code", None) == 429:
                    self.stats["err_429"] += 1
                    self._backoff = min(max(SWEEP_BACKOFF_BASE_S, self._backoff * 2.0),
                                        SWEEP_MAX_BACKOFF_S)
                else:
                    self.stats["err_other"] += 1
                    self._backoff = max(self._backoff, SWEEP_BACKOFF_BASE_S)
                self.sleep(self._backoff)
            except Exception:
                # A sweeper fault must degrade to "no fresher cache", never propagate.
                self.stats["err_other"] += 1
                self.sleep(max(self._backoff, SWEEP_BACKOFF_BASE_S))


def start(ages_fn, measure, store, score_rank_on=True):
    """Spawn the sweeper daemon thread. Returns the Sweeper (for stats) or None when disabled.
    Ships OFF (KALSHI_SWEEP_ENABLED=0) => provable no-op. Refuses to start when the score-rank
    persistence layer is off (blind-review 2026-07-31: SWEEP=1 + SCORE_RANK=0 would burn ~86K
    reads/day into an in-memory dict that is never saved and never surfaced)."""
    if not SWEEP_ENABLED:
        return None
    if not score_rank_on:
        print("WARNING KALSHI_SWEEP_ENABLED=1 but KALSHI_SCORE_RANK=0 — sweeper NOT started "
              "(its data would never be persisted or surfaced)")
        return None
    sw = Sweeper(ages_fn, measure, store)
    th = threading.Thread(target=sw.run, name="kalshi-market-sweeper", daemon=True)
    th.start()
    return sw

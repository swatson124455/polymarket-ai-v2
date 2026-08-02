"""Sel-D9 (selection review 2026-08-01; operator-named 2026-08-02: "proceed but explore
first full data set then rereview"). Unknown markets were explored in score order — an
unknown's score is pool x bonus, so the queue ran highest-pool-first forever while fresh
high-pool unknowns arrived daily, starving the low-pool tail (12.7% of 6,862 tracked rows
ever measured; review census, 4 reads). The explore queue is now a full sweep:
least-recently-ATTEMPTED first, never-attempted lead in deterministic ticker order, and
touch_attempt() stamps the try itself so gated/unpriceable markets cannot wedge the
frontier (attempt != measurement — the D4 rule is untouched)."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalshi_market_scores as kms                  # noqa: E402
import maker_kalshi_quoter as q                     # noqa: E402

T0 = 1_700_000_000.0


def _rows(*specs):
    return [{"ticker": t, "usd_day": p} for t, p in specs]


def _explore_set(markets, rows, n=2):
    ranked = kms.rank(markets, rows, now=T0 + 10_000, explore=n)
    return [r["ticker"] for r in ranked if r.get("explore")]


class TestFullSweepOrdering:
    def test_pool_no_longer_orders_the_unknown_queue(self):
        # highest pool used to win every slot; now never-attempted go in ticker order
        rows = _rows(("KXZZZ-1", 1000.0), ("KXAAA-1", 20.0), ("KXMMM-1", 500.0))
        assert _explore_set({}, rows) == ["KXAAA-1", "KXMMM-1"]

    def test_attempted_markets_yield_to_never_attempted(self):
        markets = {"KXAAA-1": {"ats": T0}}          # tried before, never priced
        rows = _rows(("KXAAA-1", 20.0), ("KXZZZ-1", 1000.0))
        assert _explore_set(markets, rows) == ["KXZZZ-1", "KXAAA-1"]

    def test_frontier_rotates_instead_of_wedging(self):
        # cycle 1 attempts A,B; cycle 2 must pick C,D — not A,B again. Rows are rebuilt
        # per cycle exactly as select_footprint rebuilds them in production (rank() marks
        # the row dicts in place, so reusing them would carry stale explore flags).
        markets = {}
        specs = (("KXA-1", 10.0), ("KXB-1", 10.0), ("KXC-1", 10.0), ("KXD-1", 10.0))
        first = _explore_set(markets, _rows(*specs))
        assert first == ["KXA-1", "KXB-1"]
        for t in first:
            kms.touch_attempt(markets, t, now=T0 + 10_001)
        assert _explore_set(markets, _rows(*specs)) == ["KXC-1", "KXD-1"]

    def test_oldest_attempt_recycles_first_once_all_attempted(self):
        markets = {"KXA-1": {"ats": T0 + 5}, "KXB-1": {"ats": T0 + 1}}
        rows = _rows(("KXA-1", 10.0), ("KXB-1", 10.0))
        assert _explore_set(markets, rows, n=1) == ["KXB-1"]


class TestTouchAttempt:
    def test_attempt_is_not_a_measurement(self):
        markets = {}
        kms.touch_attempt(markets, "KXA-1", now=T0)
        row = markets["KXA-1"]
        assert row == {"ats": T0}, "no capture/ts/n fields — the D4 rule holds"
        s, kind = kms.score(markets, "KXA-1", 100.0, now=T0 + 1)
        assert kind == "unknown", "an attempt never promotes a market out of unknown"

    def test_attempt_preserves_existing_measurements(self):
        markets = {}
        kms.update(markets, "KXA-1", 12.5, 0.40, now=T0)
        kms.touch_attempt(markets, "KXA-1", now=T0 + 60)
        assert markets["KXA-1"]["capture"] == 12.5 and markets["KXA-1"]["ts"] == T0

    def test_attempt_only_rows_survive_evict_within_age(self):
        # evict() used to require ts/pts — an attempt-only row died every cycle and the
        # frontier forgot everything it had tried
        markets = {"KXA-1": {"ats": T0}}
        kms.evict(markets, now=T0 + 60)
        assert "KXA-1" in markets
        kms.evict(markets, now=T0 + kms.EVICT_AGE_S + 61)
        assert "KXA-1" not in markets, "aged attempts still evict normally"


class TestQuoterWiring:
    def test_attempt_stamp_decoupled_from_telemetry(self):
        # D9 review fix #3: frontier progress must not depend on MKT_TELEMETRY or on
        # telemetry write health — the main stamp sits BEFORE the telemetry block.
        src = inspect.getsource(q)
        i = src.rindex("_kms.touch_attempt(SCORES, t, now=now.timestamp())")
        assert 'if SCORE_RANK and m.get("explore"):' in src[i - 600:i]
        assert "if MKT_TELEMETRY:" in src[i:i + 800], \
            "stamp precedes the telemetry block, not inside it"

    def test_fetch_fail_also_stamps(self):
        # D9 review fix #1: a fetch TRY is an attempt — dead tickers (404 mid-window) must
        # not pin the front of the sweep queue through the fetch-fail path.
        src = inspect.getsource(q)
        first = src.index("_kms.touch_attempt(SCORES, t, now=now.timestamp())")
        last = src.rindex("_kms.touch_attempt(SCORES, t, now=now.timestamp())")
        assert first != last, "both the fetch-fail path and the main loop stamp attempts"
        assert "fetch_failed += 1" in src[first:first + 700]

    def test_sweep_ages_excludes_attempt_stamps(self):
        # D9 review fix #2: the sweeper measures what the quoter COULDN'T — an attempt
        # stamp must not push a market to the back of the sweep queue.
        saved = dict(q.SCORES)
        try:
            q.SCORES.clear()
            q.SCORES.update({"KXM-1": {"ts": T0},
                             "KXP-1": {"pts": T0 + 5},
                             "KXA-1": {"ats": T0 + 99}})
            ages = q._sweep_ages()
            assert ages == {"KXM-1": T0, "KXP-1": T0 + 5}, \
                "attempt-only rows are invisible to the sweep queue"
        finally:
            q.SCORES.clear()
            q.SCORES.update(saved)

    def test_scored_markets_gauge_counts_measurements_only(self):
        # D9 review fix #5
        src = inspect.getsource(q)
        i = src.index('plan["scored_markets"]')
        assert '_r8.get("ts") is not None or _r8.get("pts") is not None' in src[i:i + 300]


class TestEvictPreference:
    def test_row_cap_kills_attempt_only_rows_before_measurements(self):
        # D9 review fix #4: a fresh ats must never evict an older measured row
        markets = {"KXOLD-MEAS": {"ts": T0 - 1000, "capture": 5.0},
                   "KXNEW-ATS": {"ats": T0 + 500}}
        kms.evict(markets, now=T0 + 600, max_age_s=kms.EVICT_AGE_S, max_rows=1)
        assert "KXOLD-MEAS" in markets and "KXNEW-ATS" not in markets

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
    def test_attempt_stamp_fires_for_explore_markets_priced_or_not(self):
        src = inspect.getsource(q)
        i = src.index("_kms.touch_attempt(SCORES, t, now=now.timestamp())")
        window = src[i - 500:i]
        assert 'if SCORE_RANK and m.get("explore"):' in window
        # and it sits BEFORE the accumulating-quote fold guard, not inside it
        after = src[i:i + 600]
        assert 'any(_o7.get("reason") != "unwind"' in after, \
            "stamp must be unconditional on pricing; the fold guard comes after"

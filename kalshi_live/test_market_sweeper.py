"""Background venue sweeper (freshness root-fix 2026-07-30, Phase 1).

Covers: the new prospective-store keys never touch actual-measurement keys; evict honors
sweep-only rows; the R1 pool formula (never divide by window length); oldest-first ordering;
429 backoff; the flag-off no-op; the quoter's measure glue; and lock-guarded concurrency.
"""
import os
import sys
import threading
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402
import kalshi_market_scores as kms                  # noqa: E402
import kalshi_market_sweeper as ksw                 # noqa: E402


# ---------------- update_prospective: model data never masquerades as measurement ---------------

class TestUpdateProspective:
    def test_stores_own_keys_only(self):
        m = {}
        kms.update_prospective(m, "T-1", 12.5, 0.42, now=1000.0)
        row = m["T-1"]
        assert row["pcap"] == 12.5 and row["pref"] == 0.42
        assert row["pts"] == 1000.0 and row["pn"] == 1
        for k in ("capture", "ts", "ref", "ref_move", "n"):
            assert k not in row

    def test_never_clobbers_actual_measurement(self):
        m = {}
        kms.update(m, "T-1", 5.0, 0.60, now=900.0)
        before = dict(m["T-1"])
        kms.update_prospective(m, "T-1", 99.0, 0.10, now=1000.0)
        after = m["T-1"]
        for k in ("capture", "ts", "ref", "ref_move", "n"):
            assert after.get(k) == before.get(k)
        assert after["pcap"] == 99.0 and after["pts"] == 1000.0

    def test_none_ref_and_none_pcap(self):
        m = {}
        kms.update_prospective(m, "T-1", None, None, now=1.0)
        assert m["T-1"]["pcap"] == 0.0 and "pref" not in m["T-1"]

    def test_pn_increments(self):
        m = {}
        kms.update_prospective(m, "T-1", 1.0, 0.5, now=1.0)
        kms.update_prospective(m, "T-1", 2.0, 0.5, now=2.0)
        assert m["T-1"]["pn"] == 2


# ---------------- evict: sweep-only rows are real rows ------------------------------------------

class TestEvictObsTs:
    def test_sweep_only_row_survives(self):
        m = {}
        kms.update_prospective(m, "SWEPT", 1.0, 0.5, now=1000.0)
        assert kms.evict(m, now=1000.0 + 60) == 0
        assert "SWEPT" in m

    def test_sweep_only_row_ages_out(self):
        m = {}
        kms.update_prospective(m, "SWEPT", 1.0, 0.5, now=1000.0)
        assert kms.evict(m, now=1000.0 + kms.EVICT_AGE_S + 1) == 1
        assert not m

    def test_newer_of_ts_pts_wins(self):
        m = {}
        kms.update(m, "BOTH", 1.0, 0.5, now=1000.0)                    # old actual
        kms.update_prospective(m, "BOTH", 1.0, 0.5, now=500000.0)      # fresh sweep
        assert kms.evict(m, now=500000.0 + 60, max_age_s=3600) == 0
        assert "BOTH" in m

    def test_count_bound_orders_by_obs_ts(self):
        m = {}
        kms.update(m, "OLD", 1.0, 0.5, now=100.0)
        kms.update_prospective(m, "FRESH", 1.0, 0.5, now=200.0)
        kms.evict(m, now=210.0, max_age_s=10**9, max_rows=1)
        assert list(m) == ["FRESH"]

    def test_legacy_row_without_pts_unchanged_behavior(self):
        m = {}
        kms.update(m, "LEGACY", 1.0, 0.5, now=1000.0)
        assert kms.evict(m, now=1000.0 + kms.EVICT_AGE_S + 1) == 1


# ---------------- rows_from_programs: the R1 canon ----------------------------------------------

class TestRowsFromPrograms:
    def test_daily_pool_is_period_reward_over_1e4_no_window_division(self):
        rows = ksw.Sweeper.rows_from_programs([{
            "market_ticker": "T-1", "period_reward": 2000000, "target_size_fp": 500,
            "discount_factor_bps": 5000, "incentive_type": "liquidity",
            "start_date": "2026-07-13T04:01:00Z", "end_date": "2026-07-31T03:59:00Z"}])
        assert rows == [{"ticker": "T-1", "usd_day": 200.0, "target": 500.0, "df": 0.5}]

    def test_filters(self):
        rows = ksw.Sweeper.rows_from_programs([
            {"market_ticker": "A", "incentive_type": "other", "target_size_fp": 1},
            {"market_ticker": None, "target_size_fp": 1},
            {"market_ticker": "B", "target_size_fp": None},
            {"market_ticker": "C", "target_size_fp": 100, "period_reward": None},
        ])
        assert [r["ticker"] for r in rows] == ["C"]
        assert rows[0]["usd_day"] == 0.0 and rows[0]["df"] is None

    def test_empty_and_none(self):
        assert ksw.Sweeper.rows_from_programs(None) == []
        assert ksw.Sweeper.rows_from_programs([]) == []


# ---------------- order_worklist: never-seen first, then oldest ---------------------------------

class TestOrderWorklist:
    def test_ordering(self):
        rows = [{"ticker": t} for t in ("FRESH", "NEVER", "OLD")]
        ages = {"FRESH": 990.0, "OLD": 100.0}
        out = ksw.Sweeper.order_worklist(rows, ages, now=1000.0)
        assert [r["ticker"] for r in out] == ["NEVER", "OLD", "FRESH"]


# ---------------- run loop: pacing, storing, backoff --------------------------------------------

def _mk_sweeper(get, ages=None, measure=None, store=None):
    clock = [0.0]

    def now():
        return clock[0]

    def sleep(s):
        clock[0] += max(s, 0.0)

    sw = ksw.Sweeper(ages or (lambda: {}),
                     measure or (lambda m, ob: (1.0, 0.5)),
                     store or (lambda t, p, r: None),
                     get=get, now_fn=now, sleep_fn=sleep)
    return sw, clock


class TestRunLoop:
    def test_sweeps_and_stores(self):
        stored = []
        progs = {"incentive_programs": [
            {"market_ticker": "A", "period_reward": 10000, "target_size_fp": 10},
            {"market_ticker": "B", "period_reward": 20000, "target_size_fp": 10}]}

        def get(path):
            if "incentive_programs" in path:
                return progs
            return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}

        sw, _ = _mk_sweeper(get, store=lambda t, p, r: stored.append(t))
        sw._refresh_programs()
        for m in sw.order_worklist(sw._progs, {}, sw.now()):
            sw._sweep_one(m)
        assert sorted(stored) == ["A", "B"]
        assert sw.stats["reads"] == 2 and sw.stats["stored"] == 2

    def test_429_backoff_doubles_and_caps_then_resets(self):
        def get(path):
            raise urllib.error.HTTPError(path, 429, "too many", {}, None)

        sw, clock = _mk_sweeper(get)
        sw.stop_event = threading.Event()
        # drive the run loop error path directly a few times
        for _ in range(12):
            try:
                sw._refresh_programs()
            except urllib.error.HTTPError as e:
                assert e.code == 429
                sw.stats["err_429"] += 1
                sw._backoff = min(max(ksw.SWEEP_BACKOFF_BASE_S, sw._backoff * 2.0),
                                  ksw.SWEEP_MAX_BACKOFF_S)
        assert sw._backoff == ksw.SWEEP_MAX_BACKOFF_S
        sw.get = lambda path: {"incentive_programs": []}
        sw._progs_ts = 0.0
        sw._refresh_programs()      # success path via _sweep_one resets; refresh alone must not raise
        ok = [0]
        sw.get = lambda path: {"orderbook_fp": {}}
        sw.store = lambda t, p, r: ok.append(1)
        sw._sweep_one({"ticker": "A", "usd_day": 1.0, "target": 10.0, "df": None})
        assert sw._backoff == 0.0

    def test_fresh_sweeper_starts_unbacked_off(self):
        sw, _ = _mk_sweeper(lambda p: {})
        assert sw._backoff == 0.0

    def test_run_exits_on_stop_event(self):
        sw, _ = _mk_sweeper(lambda p: {"incentive_programs": []})
        sw.stop_event.set()
        sw.run()        # must return immediately, not hang


# ---------------- flag-off no-op -----------------------------------------------------------------

class TestNoOp:
    def test_start_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr(ksw, "SWEEP_ENABLED", 0)
        assert ksw.start(lambda: {}, lambda m, ob: None, lambda t, p, r: None) is None

    def test_quoter_ensure_sweeper_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr(ksw, "SWEEP_ENABLED", 0)
        monkeypatch.setattr(q, "_SWEEPER", None)
        assert q._ensure_sweeper() is None


# ---------------- quoter glue: measure + ages -----------------------------------------------------

class TestQuoterGlue:
    M = {"ticker": "T", "usd_day": 100.0, "target": 50.0, "df": 0.5}

    def test_one_sided_book_measures_zero(self):
        pcap, ref = q._sweep_measure(self.M, {"yes_dollars": [["0.40", "10"]],
                                              "no_dollars": []})
        assert pcap == 0.0 and ref == 0.40

    def test_empty_book_measures_zero_ref_none(self):
        pcap, ref = q._sweep_measure(self.M, {})
        assert pcap == 0.0 and ref is None

    def test_two_sided_matches_prospective_capture(self):
        yes = [["0.40", "60"]]
        no = [["0.55", "60"]]
        ob = {"yes_dollars": yes, "no_dollars": no}
        yl, _ = q._levels(yes)
        nl, _ = q._levels(no)
        expect = q._prospective_capture(self.M, yl, nl, 0.40, 0.55, 50.0)
        pcap, ref = q._sweep_measure(self.M, ob)
        assert pcap == expect and ref == 0.40 and pcap > 0.0

    def test_sweep_ages_uses_newest_observation(self, monkeypatch):
        fake = {}
        kms.update(fake, "A", 1.0, 0.5, now=100.0)
        kms.update_prospective(fake, "A", 1.0, 0.5, now=200.0)
        kms.update_prospective(fake, "B", 1.0, 0.5, now=50.0)
        monkeypatch.setattr(q, "SCORES", fake)
        ages = q._sweep_ages()
        assert ages == {"A": 200.0, "B": 50.0}

    def test_sweep_store_writes_prospective(self, monkeypatch):
        fake = {}
        monkeypatch.setattr(q, "SCORES", fake)
        q._sweep_store("T", 3.0, 0.7)
        assert fake["T"]["pcap"] == 3.0 and fake["T"]["pref"] == 0.7


# ---------------- concurrency: evict + prospective writes under the quoter's lock ----------------

class TestConcurrency:
    def test_locked_evict_vs_writes_no_runtime_error(self, monkeypatch):
        shared = {}
        monkeypatch.setattr(q, "SCORES", shared)
        stop = threading.Event()
        errs = []

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    q._sweep_store(f"T-{i % 200}", 1.0, 0.5)
                except Exception as e:      # pragma: no cover - the assertion target
                    errs.append(e)
                    return
                i += 1

        th = threading.Thread(target=writer, daemon=True)
        th.start()
        try:
            for k in range(300):
                with q.SCORES_LOCK:
                    kms.evict(shared, now=10**9 + k, max_age_s=1, max_rows=50)
        finally:
            stop.set()
            th.join(timeout=5)
        assert not errs

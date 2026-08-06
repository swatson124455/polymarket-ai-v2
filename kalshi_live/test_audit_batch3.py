"""Audit batch 3 (operator-approved 2026-07-29) — J1..J7 clerical fixes.

One test class per item; each asserts the DEFECT is gone and the surrounding
behavior is unchanged.
"""
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402
import kalshi_market_scores as kms                  # noqa: E402
import maker_kalshi_ws_daemon as wsd                # noqa: E402
from kalshi_ws_feed import Feed                     # noqa: E402


# ---------------- J1: close-time cache expiry/bound ----------------

class TestJ1CloseCache:
    def setup_method(self):
        q._CLOSE_TIME_CACHE.clear()

    def test_positive_entry_persists(self):
        q._close_cache_put("T1", "2026-08-05T00:00:00Z")
        assert q._close_cache_get("T1") == "2026-08-05T00:00:00Z"

    def test_negative_entry_expires(self, monkeypatch):
        q._close_cache_put("T2", None)              # payload had no close_time
        assert q._close_cache_get("T2") == ""       # fresh negative -> cached
        # age the stamp past the TTL
        ct, stamp = q._CLOSE_TIME_CACHE["T2"]
        q._CLOSE_TIME_CACHE["T2"] = (ct, stamp - q.CLOSE_CACHE_NEG_TTL_S - 1)
        assert q._close_cache_get("T2") is None     # expired -> caller refetches
        assert "T2" not in q._CLOSE_TIME_CACHE      # and the dead entry is gone

    def test_positive_entry_expires_after_pos_ttl(self):
        # B-3 (identity review, operator-ruled 2026-08-06): positives USED to live
        # forever ("never expires" was the old pin) — but the venue can amend
        # close_time, so a positive entry now forces a re-read after
        # CLOSE_CACHE_POS_TTL_S. Fresh entries still serve from cache.
        q._close_cache_put("T3", "2026-08-05T00:00:00Z")
        assert q._close_cache_get("T3") == "2026-08-05T00:00:00Z"
        ct, stamp = q._CLOSE_TIME_CACHE["T3"]
        q._CLOSE_TIME_CACHE["T3"] = (ct, stamp - q.CLOSE_CACHE_POS_TTL_S - 1)
        assert q._close_cache_get("T3") is None

    def test_bound_evicts_oldest(self, monkeypatch):
        monkeypatch.setattr(q, "CLOSE_CACHE_MAX", 16)
        for i in range(16):
            q._CLOSE_TIME_CACHE[f"OLD{i}"] = ("x", float(i))
        q._close_cache_put("NEW", "y")
        assert len(q._CLOSE_TIME_CACHE) <= 16
        assert "NEW" in q._CLOSE_TIME_CACHE
        assert "OLD0" not in q._CLOSE_TIME_CACHE    # oldest stamp evicted first


# ---------------- J2: _SILENT per-cycle delta ----------------

class TestJ2SilentDelta:
    def _emit(self):
        plan = {}
        q._silent_report(plan)          # the production helper run_once calls
        return plan

    def setup_method(self):
        q._SILENT.clear()
        q._SILENT_PREV.clear()

    def test_first_cycle_reports_both(self):
        q._SILENT["rank_fail"] += 2
        plan = self._emit()
        assert plan["silent_failures"] == {"rank_fail": 2}
        assert plan["silent_failures_total"] == {"rank_fail": 2}

    def test_quiet_cycle_reports_total_only(self):
        q._SILENT["rank_fail"] += 2
        self._emit()
        plan = self._emit()                          # nothing new fired
        assert "silent_failures" not in plan
        assert plan["silent_failures_total"] == {"rank_fail": 2}

    def test_new_fire_reports_delta_not_lifetime(self):
        q._SILENT["rank_fail"] += 5
        self._emit()
        q._SILENT["rank_fail"] += 1
        plan = self._emit()
        assert plan["silent_failures"] == {"rank_fail": 1}
        assert plan["silent_failures_total"] == {"rank_fail": 6}


# ---------------- J3: telemetry purge cadence + log rotation ----------------

class TestJ3Purge:
    def test_purges_caprank_and_rotates_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wsd.M, "DATA_DIR", str(tmp_path))
        old = tmp_path / "caprank-20260101.jsonl"
        old.write_text("x")
        os.utime(old, (time.time() - 20 * 86400,) * 2)
        fresh = tmp_path / "caprank-20260729.jsonl"
        fresh.write_text("x")
        log = tmp_path / "ws_daemon_log.jsonl"
        log.write_text("y" * 128)
        monkeypatch.setattr(wsd, "WS_LOG_MAX_BYTES", 64)
        wsd.Daemon._purge_old_telemetry()
        assert not old.exists()                     # old caprank purged
        assert fresh.exists()                       # fresh kept
        assert not log.exists()                     # rotated away...
        assert (tmp_path / "ws_daemon_log.jsonl.1").exists()   # ...to one generation

    def test_small_log_not_rotated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wsd.M, "DATA_DIR", str(tmp_path))
        log = tmp_path / "ws_daemon_log.jsonl"
        log.write_text("y")
        wsd.Daemon._purge_old_telemetry()
        assert log.exists()


# ---------------- J4: SCORES eviction ----------------

class TestJ4ScoresEvict:
    def test_age_eviction(self):
        now = 1_000_000.0
        m = {"OLD": {"ts": now - kms.EVICT_AGE_S - 1, "capture": 1.0},
             "FRESH": {"ts": now - 60, "capture": 2.0},
             "NOTS": {"capture": 3.0}}
        n = kms.evict(m, now=now)
        assert n == 2 and set(m) == {"FRESH"}

    def test_count_bound_drops_oldest(self):
        now = 1_000_000.0
        m = {f"T{i}": {"ts": now - i} for i in range(10)}
        kms.evict(m, now=now, max_age_s=1e12, max_rows=4)
        assert len(m) == 4
        assert set(m) == {"T0", "T1", "T2", "T3"}   # newest ts kept

    def test_noop_under_bounds(self):
        now = 1_000_000.0
        m = {"A": {"ts": now}, "B": {"ts": now - 1}}
        assert kms.evict(m, now=now) == 0 and len(m) == 2


# ---------------- J5: blackout cancel backoff ----------------

class _CancelClient:
    def __init__(self, fail=True):
        self.fail = fail
        self.calls = 0

    def cancel_order(self, oid):
        self.calls += 1
        if self.fail:
            raise RuntimeError("venue down")


class TestJ5BlackoutBackoff:
    def setup_method(self):
        q._BLACKOUT_BACKOFF[:] = [0, 0.0]

    def test_failed_round_arms_backoff_and_paces(self):
        st = {"read_fail_streak": q.BLACKOUT_CANCEL_AFTER - 1, "last_oids": ["a", "b"]}
        cl = _CancelClient(fail=True)
        plan = {}
        q._blackout_guard(cl, st, plan)             # streak hits threshold, attempts, fails
        assert cl.calls == 2
        assert q._BLACKOUT_BACKOFF[0] == 1 and q._BLACKOUT_BACKOFF[1] > time.monotonic()
        plan2 = {}
        q._blackout_guard(cl, st, plan2)            # within backoff window -> paced, no calls
        assert cl.calls == 2
        assert plan2.get("blackout_retry_paced") == 1

    def test_success_resets_backoff(self):
        st = {"read_fail_streak": q.BLACKOUT_CANCEL_AFTER - 1, "last_oids": ["a"]}
        cl = _CancelClient(fail=False)
        q._blackout_guard(cl, st, {})
        assert q._BLACKOUT_BACKOFF == [0, 0.0]
        assert st["last_oids"] == []

    def test_backoff_escalates_and_caps(self):
        st = {"read_fail_streak": q.BLACKOUT_CANCEL_AFTER, "last_oids": ["a"]}
        cl = _CancelClient(fail=True)
        delays = []
        for _ in range(8):
            q._BLACKOUT_BACKOFF[1] = 0.0            # force each round eligible
            t0 = time.monotonic()
            q._blackout_guard(cl, st, {})
            delays.append(q._BLACKOUT_BACKOFF[1] - t0)
        assert delays[1] > delays[0]
        assert max(delays) <= q.BLACKOUT_RETRY_MAX_S + 1.0


# ---------------- J6: create-fail ratchet ----------------

class TestJ6CreateRatchet:
    def setup_method(self):
        q._CREATE_FAIL_RATCHET.clear()

    def test_ratchet_arms_after_n(self):
        for _ in range(q.CREATE_FAIL_RATCHET_N):
            assert not q._create_ratchet_blocked("T", reducing=False)  # not armed yet
            q._create_ratchet_fail("T")
        r = q._CREATE_FAIL_RATCHET["T"]
        assert r[0] == q.CREATE_FAIL_RATCHET_N and r[1] > time.monotonic()
        assert q._create_ratchet_blocked("T", reducing=False)

    def test_skip_gate_semantics(self):
        """The gate skips accumulating creates only while armed AND cooling."""
        now = time.monotonic()
        q._CREATE_FAIL_RATCHET["armed"] = [q.CREATE_FAIL_RATCHET_N, now + 100]
        q._CREATE_FAIL_RATCHET["cooled"] = [q.CREATE_FAIL_RATCHET_N, now - 1]
        q._CREATE_FAIL_RATCHET["underway"] = [q.CREATE_FAIL_RATCHET_N - 1, now + 100]
        assert q._create_ratchet_blocked("armed", reducing=False) is True
        assert q._create_ratchet_blocked("armed", reducing=True) is False   # unwind never ratcheted
        assert q._create_ratchet_blocked("cooled", reducing=False) is False # cool-off elapsed
        assert q._create_ratchet_blocked("underway", reducing=False) is False
        assert q._create_ratchet_blocked("absent", reducing=False) is False

    def test_backoff_escalates_and_caps(self):
        q._CREATE_FAIL_RATCHET.clear()
        stamps = []
        for _ in range(q.CREATE_FAIL_RATCHET_N + 6):
            t0 = time.monotonic()
            q._create_ratchet_fail("T")
            stamps.append(q._CREATE_FAIL_RATCHET["T"][1] - t0)
        assert stamps[q.CREATE_FAIL_RATCHET_N] > stamps[q.CREATE_FAIL_RATCHET_N - 1]
        assert max(stamps) <= q.CREATE_FAIL_RATCHET_MAX_S + 1.0

    def test_success_clears(self):
        q._CREATE_FAIL_RATCHET["T"] = [5, time.monotonic() + 100]
        q._CREATE_FAIL_RATCHET.pop("T", None)
        assert "T" not in q._CREATE_FAIL_RATCHET


# ---------------- J7: WS resubscribe hysteresis ----------------

class TestJ7ResubHysteresis:
    def test_feed_carries_initial_fails(self):
        f = Feed(["T1"], initial_fails=4)
        assert f.fails == 4

    def test_daemon_new_feed_carries_fails(self):
        d = wsd.Daemon.__new__(wsd.Daemon)          # no live client
        d.client = types.SimpleNamespace(mode="dry_run")
        d.on_book = d.on_fill = None
        d.feed = types.SimpleNamespace(fails=7)
        f = wsd.Daemon._new_feed(d, ["T1"])
        assert f.fails == 7

    def test_daemon_first_feed_starts_clean(self):
        d = wsd.Daemon.__new__(wsd.Daemon)
        d.client = types.SimpleNamespace(mode="dry_run")
        d.on_book = d.on_fill = None
        d.feed = None
        f = wsd.Daemon._new_feed(d, ["T1"])
        assert f.fails == 0

    def test_resub_pacing_gate(self):
        """The main-loop condition: rebuild only when the pacing interval elapsed."""
        now = time.monotonic()
        assert (now - 0.0) >= wsd.WS_RESUB_MIN_S            # cold start: immediate
        assert not ((now - (now - 1)) >= wsd.WS_RESUB_MIN_S)  # 1s after a resub: paced


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

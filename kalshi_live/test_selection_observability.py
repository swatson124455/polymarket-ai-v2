"""Selection-review no-brainer fixes (operator-named 2026-08-01): D10 — the funnel's
largest drop stage (rows past the last footprint slot) must emit a reason counter.
Before the fix ~3.4k rows/cycle vanished with zero reason codes and a viable market was
indistinguishable in telemetry from one never discovered."""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


def _prog(ticker, hours_to_close=4, reward_cents=1000000):
    now = q.utcnow()
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "period_reward": reward_cents, "target_size_fp": "1000.00",
            "discount_factor_bps": 5000,
            "start_date": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "end_date": (now + timedelta(hours=hours_to_close)).isoformat().replace("+00:00", "Z")}


def _base_env(monkeypatch, top):
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "SERIES_DENY", ())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0)   # keep the clock pre-filter out of scope
    monkeypatch.setattr(q, "SCORE_RANK", 0)
    monkeypatch.setattr(q, "FOOTPRINT_TOP", top)


class TestD10DropNotSelected:
    def test_overflow_rows_are_counted(self, monkeypatch):
        _base_env(monkeypatch, top=3)
        progs = [_prog("KXAAA-01"), _prog("KXBBB-01"), _prog("KXCCC-01"),
                 _prog("KXDDD-01"), _prog("KXEEE-01")]
        picked = q.select_footprint(progs, q.utcnow())
        assert len(picked) == 3
        assert q.FP_DROPS["drop_not_selected"] == 2

    def test_exact_fit_emits_no_counter(self, monkeypatch):
        _base_env(monkeypatch, top=5)
        progs = [_prog("KXAAA-01"), _prog("KXBBB-01")]
        picked = q.select_footprint(progs, q.utcnow())
        assert len(picked) == 2
        assert "drop_not_selected" not in q.FP_DROPS, \
            "no drop -> no counter (plan rows stay lean, matching every other drop_*)"

    def test_per_series_cap_overflow_is_also_counted(self, monkeypatch):
        # exhausted-series exit (progressed=False) is the same silent stage — count it too
        _base_env(monkeypatch, top=10)
        monkeypatch.setattr(q, "PER_SERIES_CAP", 1)
        progs = [_prog("KXAAA-01"), _prog("KXAAA-02"), _prog("KXAAA-03")]
        picked = q.select_footprint(progs, q.utcnow())
        assert len(picked) == 1
        assert q.FP_DROPS["drop_not_selected"] == 2

    def test_counter_reaches_the_plan_namespace(self, monkeypatch):
        # FP_DROPS is plan.update()'d verbatim in run_once (:2845) — key shape is the contract
        _base_env(monkeypatch, top=1)
        progs = [_prog("KXAAA-01"), _prog("KXBBB-01")]
        q.select_footprint(progs, q.utcnow())
        assert set(q.FP_DROPS) == {"drop_not_selected"}

"""Incumbent-first capital migration (operator-named 2026-07-30; BUILT NOT ENABLED).

cap_desired grows an optional `incumbents` set: incumbents outrank every non-incumbent for
capital; pool order within each group unchanged; unwind exemption untouched; default None is
byte-identical legacy. Flag KALSHI_ALLOC_INCUMBENT_FIRST default 0 => call site passes None.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


def _q(price, ct, reason=None):
    d = {"side": "yes", "price_dollars": price, "count": ct}
    if reason:
        d["reason"] = reason
    return d


def _desired(**mkts):
    return {t: [_q(p, c)] for t, (p, c) in mkts.items()}


class TestIncumbentFirst:
    def setup_method(self):
        self._cap = q.MAX_TOTAL_CAPITAL

    def teardown_method(self):
        q.MAX_TOTAL_CAPITAL = self._cap

    def test_default_none_is_legacy_order(self):
        q.MAX_TOTAL_CAPITAL = 100.0
        desired = _desired(BIG=(0.5, 100), SMALL=(0.5, 100))   # $50 each
        usd = {"BIG": 1000, "SMALL": 10}
        kept, dropped = q.cap_desired(dict(desired), usd)
        assert set(kept) == {"BIG", "SMALL"} and dropped == 0
        q.MAX_TOTAL_CAPITAL = 60.0
        kept, dropped = q.cap_desired(dict(desired), usd)
        assert set(kept) == {"BIG"} and dropped == 1           # legacy: pool wins

    def test_incumbent_small_pool_beats_big_pool_newcomer(self):
        q.MAX_TOTAL_CAPITAL = 60.0
        desired = _desired(BIG=(0.5, 100), SMALL=(0.5, 100))
        usd = {"BIG": 1000, "SMALL": 10}
        kept, dropped = q.cap_desired(dict(desired), usd, incumbents={"SMALL"})
        assert set(kept) == {"SMALL"} and dropped == 1

    def test_pool_order_within_groups_unchanged(self):
        q.MAX_TOTAL_CAPITAL = 110.0
        desired = _desired(I_LO=(0.5, 100), I_HI=(0.5, 100), N_HI=(0.5, 100))
        usd = {"I_LO": 5, "I_HI": 500, "N_HI": 9999}
        kept, _ = q.cap_desired(dict(desired), usd, incumbents={"I_LO", "I_HI"})
        # both incumbents funded, the huge-pool newcomer cut — and WITHIN the incumbent
        # group pool order must hold (blind-review: assert the ordering, not just the set):
        # with cap 110 both incumbents fit; shrink to 60 and only the HIGHER-pool one stays.
        assert set(kept) == {"I_HI", "I_LO"}
        q.MAX_TOTAL_CAPITAL = 60.0
        kept, _ = q.cap_desired(dict(desired), usd, incumbents={"I_LO", "I_HI"})
        assert set(kept) == {"I_HI"}          # pool order within the incumbent group

    def test_unwind_still_unconditional(self):
        q.MAX_TOTAL_CAPITAL = 10.0
        desired = {"EXIT": [_q(0.5, 100, reason="unwind")],
                   "INC": [_q(0.5, 100)]}
        kept, _ = q.cap_desired(dict(desired), {"EXIT": 0, "INC": 50},
                                incumbents={"INC"})
        assert "EXIT" in kept                                   # exempt regardless of flag

    def test_empty_incumbents_equals_legacy(self):
        q.MAX_TOTAL_CAPITAL = 60.0
        desired = _desired(BIG=(0.5, 100), SMALL=(0.5, 100))
        usd = {"BIG": 1000, "SMALL": 10}
        a = q.cap_desired(dict(desired), usd, incumbents=set())
        b = q.cap_desired(dict(desired), usd)
        assert set(a[0]) == set(b[0]) and a[1] == b[1]

    def test_flag_ships_off(self):
        assert q.ALLOC_INCUMBENT_FIRST == 0

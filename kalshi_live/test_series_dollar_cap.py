"""Per-family dollar cap (operator-named 2026-07-30; BUILT NOT ENABLED, default 0 = off).

cap_desired skips an accumulating sibling that would push its ticker-family past
KALSHI_SERIES_MAX_USD; capital flows on to other families. Unwinds never blocked but
count toward the family total. Off => byte-identical legacy.
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


class TestSeriesDollarCap:
    def setup_method(self):
        self._cap, self._ser = q.MAX_TOTAL_CAPITAL, q.SERIES_MAX_USD
        q.MAX_TOTAL_CAPITAL = 1000.0

    def teardown_method(self):
        q.MAX_TOTAL_CAPITAL, q.SERIES_MAX_USD = self._cap, self._ser

    def test_ships_off_and_off_is_legacy(self):
        assert q.SERIES_MAX_USD == 0.0
        desired = {"FAM-A": [_q(0.5, 100)], "FAM-B": [_q(0.5, 100)],
                   "OTH-C": [_q(0.5, 100)]}
        usd = {"FAM-A": 300, "FAM-B": 200, "OTH-C": 100}
        kept, dropped = q.cap_desired(dict(desired), usd)
        assert set(kept) == set(desired) and dropped == 0

    def test_family_cap_skips_sibling_funds_next_family(self):
        q.SERIES_MAX_USD = 60.0
        desired = {"FAM-A": [_q(0.5, 100)],    # $50
                   "FAM-B": [_q(0.5, 100)],    # $50 -> family would hit $100 > 60: skip
                   "OTH-C": [_q(0.5, 100)]}    # $50 -> different family: funded
        usd = {"FAM-A": 300, "FAM-B": 200, "OTH-C": 100}
        kept, dropped = q.cap_desired(dict(desired), usd)
        assert set(kept) == {"FAM-A", "OTH-C"} and dropped == 1

    def test_unwind_never_blocked_but_counts_toward_family(self):
        q.SERIES_MAX_USD = 60.0
        desired = {"FAM-EXIT": [_q(0.5, 100, reason="unwind")],   # $50, exempt, counts
                   "FAM-NEW": [_q(0.5, 100)]}                      # $50 -> family $100 > 60: skip
        kept, dropped = q.cap_desired(dict(desired), {"FAM-EXIT": 0, "FAM-NEW": 500})
        assert "FAM-EXIT" in kept and "FAM-NEW" not in kept and dropped == 1

    def test_families_independent(self):
        q.SERIES_MAX_USD = 60.0
        desired = {"A-1": [_q(0.5, 100)], "B-1": [_q(0.5, 100)],
                   "C-1": [_q(0.5, 100)]}
        kept, dropped = q.cap_desired(dict(desired), {"A-1": 3, "B-1": 2, "C-1": 1})
        assert set(kept) == {"A-1", "B-1", "C-1"} and dropped == 0

    def test_total_cap_still_binds_and_counts_family_skips(self):
        q.SERIES_MAX_USD = 60.0
        q.MAX_TOTAL_CAPITAL = 75.0
        desired = {"FAM-A": [_q(0.5, 100)],    # $50 funded
                   "FAM-B": [_q(0.5, 100)],    # family-skipped
                   "OTH-C": [_q(0.5, 100)],    # $50 -> total would be $100 > 75: tail cut
                   "OTH-D": [_q(0.5, 100)]}
        usd = {"FAM-A": 400, "FAM-B": 300, "OTH-C": 200, "OTH-D": 100}
        kept, dropped = q.cap_desired(dict(desired), usd)
        assert set(kept) == {"FAM-A"} and dropped == 3   # 1 family-skip + 2 tail-cut

    def test_composes_with_incumbent_first(self):
        q.SERIES_MAX_USD = 60.0
        q.MAX_TOTAL_CAPITAL = 55.0
        desired = {"FAM-INC": [_q(0.5, 100)], "OTH-BIG": [_q(0.5, 100)]}
        usd = {"FAM-INC": 1, "OTH-BIG": 999}
        kept, _ = q.cap_desired(dict(desired), usd, incumbents={"FAM-INC"})
        assert set(kept) == {"FAM-INC"}                  # incumbent funded first

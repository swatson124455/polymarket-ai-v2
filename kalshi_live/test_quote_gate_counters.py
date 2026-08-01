"""Sel-D2 observability (selection review 2026-08-01): 50.7% of quote rows emitted no
price and no gate counter. Every priceless exit in desired_quotes now increments a
gate_* stat; the run-loop's qstats diff turns each into that row's `gates` entry, so a
silently-excluded market (the KXMAMDANIEO longshot case) is attributable from telemetry.
Behavior contract: counters only — the returned quotes are byte-identical."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402

M = {"target": 100, "end": "2099-01-01T00:00:00Z", "ticker": "KXTEST-01"}
YL = [["0.50", "500"]]
NL = [["0.49", "500"]]


def _run(m=None, yl=YL, nl=NL, inv=0.0, **kw):
    stats = {}
    quotes = q.desired_quotes(m or M, yl, nl, q.utcnow(), inv=inv, stats=stats, **kw)
    return quotes, stats


class TestGateCounters:
    def test_bad_clock(self):
        quotes, stats = _run(m={"target": 100, "end": "not-a-date", "ticker": "KXTEST-01"})
        assert quotes == [] and stats.get("gate_bad_clock") == 1

    def test_wind_down_flat(self, monkeypatch):
        monkeypatch.setattr(q, "WIND_DOWN_MIN", 20)
        m = {"target": 100, "ticker": "KXTEST-01",
             "end": q.utcnow().isoformat()}          # closes now -> inside wind-down
        quotes, stats = _run(m=m)
        assert quotes == [] and stats.get("gate_wind_down_flat") == 1

    def test_crossed_book(self):
        quotes, stats = _run(yl=[["0.60", "500"]], nl=[["0.45", "500"]])
        assert quotes == [] and stats.get("gate_crossed_book") == 1

    def test_one_sided_book_flat(self):
        quotes, stats = _run(nl=[])
        assert quotes == [] and stats.get("gate_one_sided_book") == 1

    def test_one_sided_book_holding_still_counts_but_exits(self):
        # long NO -> the reducing quote rests on the YES side, which is the side present
        quotes, stats = _run(nl=[], inv=-10.0)
        assert stats.get("gate_one_sided_book") == 1
        assert quotes, "held inventory must still get its reducing exit"
        assert all(o.get("reason") == "unwind" for o in quotes)

    def test_entry_band_longshot_both_sides_fail_together(self, monkeypatch):
        # the KXMAMDANIEO mechanism at LIVE band values (live.env 0.04/0.96; code
        # defaults are looser 0.01/0.97): yes ref below MIN forces no above MAX
        monkeypatch.setattr(q, "MIN_PRICE_DOLLARS", 0.04)
        monkeypatch.setattr(q, "MAX_PRICE_DOLLARS", 0.96)
        quotes, stats = _run(yl=[["0.02", "500"]], nl=[["0.97", "500"]])
        assert quotes == [] and stats.get("gate_entry_band") == 1

    def test_wide_or_asym(self, monkeypatch):
        monkeypatch.setattr(q, "MAX_SPREAD_TICKS", 5)
        # spread 0.20 = 20 ticks wide, deep two-sided book, flat
        quotes, stats = _run(yl=[["0.40", "500"]], nl=[["0.40", "500"]])
        assert quotes == [] and stats.get("gate_wide_or_asym") == 1

    def test_priced_market_emits_no_gate_counters(self):
        quotes, stats = _run()
        assert quotes, "healthy book must quote"
        assert not any(k.startswith("gate_") for k in stats), stats

    def test_counters_require_stats_dict(self):
        # stats=None path (offline callers/tests) must not raise
        assert q.desired_quotes({"target": 100, "end": "not-a-date",
                                 "ticker": "KXTEST-01"}, YL, NL, q.utcnow()) == []

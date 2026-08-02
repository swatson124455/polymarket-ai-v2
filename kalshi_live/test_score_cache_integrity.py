"""Sel-D4 + Sel-D8 (selection review 2026-08-01) — score-cache measurement integrity.

D4: a gated-out market (q == []) is OUR decision, not a measurement. Folding it into the
score cache as "capture $0, fresh ts" made 69.3% of timestamped rows fake zeros and locked
gated markets out of the explore quota on a measurement that never happened. The cache now
folds only real quoting attempts; the telemetry ROW is still written for every market.

D8: explore probes rest EXPLORE_PROBE_CT contracts but the cache ranks by what normal-size
quoting would capture — the probe-sized value was stored as full-size worth (sampled
inflation up to 116x). Probed markets now fold a full-size recompute."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402

M = {"ticker": "KXTEST-01", "target": 100.0, "df": 0.5, "usd_day": 200.0}
YL = [(0.50, 500.0)]
NL = [(0.49, 500.0)]


def _quotes(ct):
    return [{"side": "yes", "price_dollars": 0.50, "count": ct, "reason": "join"},
            {"side": "no", "price_dollars": 0.49, "count": ct, "reason": "join"}]


def _capture(quotes):
    row = q._market_telemetry_row(1, q.utcnow(), M, YL, NL, quotes,
                                  {"yes": 0.0, "no": 0.0}, 0.0, {})
    return row["capture_usd_day"]


class TestD8FullSizeRecompute:
    def test_probe_size_understates_capture(self):
        # the defect's arithmetic: share is size-dependent, so a 5-ct measurement is not
        # a 100-ct market's worth. If these were equal the recompute would be pointless.
        assert _capture(_quotes(100)) > _capture(_quotes(5)) > 0.0

    def test_cache_fold_recomputes_at_full_size(self):
        src = inspect.getsource(q)
        i = src.index("_q_fullsize is not None")
        window = src[i:i + 900]
        assert "_market_telemetry_row(" in window, "full-size recompute must feed the cache"
        assert "_kms.update(SCORES, t, _cache_row.get" in window

    def test_stripped_market_never_folds_the_fiction(self):
        # exit-only strip after the probe clamp: the pre-clamp copy no longer describes
        # anything that rested — it must be discarded, not folded.
        src = inspect.getsource(q)
        i = src.index("loss_exitonly_stripped\"] = (")
        window = src[i:i + 300]
        assert "_q_fullsize = None" in window


class TestD4NoFakeZeros:
    def test_cache_fold_requires_a_quoting_attempt(self):
        # blind review lens A #4 tightened the guard from "q non-empty" to "at least one
        # ACCUMULATING quote" — a market stripped to unwind-only is our decision, not a
        # capture measurement.
        src = inspect.getsource(q)
        i = src.index('if SCORE_RANK and any(_o7.get("reason") != "unwind" for _o7 in q):')
        # the qstats->gates diff and the row write must come BEFORE this guard (row always
        # written; only the cache fold is conditional)
        pre = src[:i]
        assert "quotes-{now.strftime" in pre[pre.rindex("if MKT_TELEMETRY:"):], \
            "telemetry row must still be written for gated markets"

    def test_telemetry_row_still_pure_and_zero_when_gated(self):
        # the ROW keeps recording gated markets (capture 0 with gates attached) — that is
        # the observability contract; only the CACHE stops treating it as a measurement.
        row = q._market_telemetry_row(1, q.utcnow(), M, YL, NL, [],
                                      {"yes": 0.0, "no": 0.0}, 0.0,
                                      {"gate_entry_band": 1})
        assert row["capture_usd_day"] == 0.0 and row["gates"] == {"gate_entry_band": 1}

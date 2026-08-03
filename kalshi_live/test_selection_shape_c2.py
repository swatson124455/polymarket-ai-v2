"""SELECTION INPUTS ARE PERSISTED, NOT JUST THEIR VERDICTS (Phase C2, 2026-08-03).

The funnel counted HOW MANY candidates were dropped but never WHAT they looked like, so
"we passed over 200 markets" could not be turned into "we passed over 200 markets whose pools
were all under $20/day" — and those two have opposite implications for whether the cut costs
anything. Likewise the vol24h gate recorded its VERDICT (drop_high_activity) but discarded the
INPUT, so no later study could re-cut the threshold against our own candidates, or ask what
vol24h the markets we actually lost money on carried. (The live gate caught 0 of the 6 losers
on 2026-08-02.)

NAMESPACE: this lives in FP_SHAPE, not FP_DROPS. FP_DROPS means "why a candidate was dropped"
and test_selection_observability asserts that meaning by EXACT SET — measurement is kept
separate so that pin stays strict rather than being loosened to accommodate new keys.
"""
from test_live_hardening import q


def _prog(ticker, usd_day, end="2099-01-01T00:00:00Z"):
    return {"ticker": ticker, "usd_day": usd_day, "target": 1, "end": end}


def _rows(specs):
    return [{"ticker": t, "usd_day": u, "target": 1, "end": "2099-01-01T00:00:00Z"}
            for t, u in specs]


def test_pool_and_below_cut_histograms_are_recorded():
    shape = {}
    rows = _rows([("KXA-1", 0.0), ("KXA-2", 5.0), ("KXB-1", 25.0),
                  ("KXB-2", 200.0), ("KXC-1", 900.0)])
    picked = rows[:2]
    q._record_pool_shape(rows, picked, shape)
    assert sum(shape["pool_hist"]) == 5, "every candidate lands in exactly one bucket"
    assert sum(shape["below_hist"]) == 3, "and every below-cut candidate too"
    assert shape["below_cut_n"] == 3
    assert shape["pool_n"] == 5


def test_below_cut_value_is_recorded_not_just_the_count():
    # THE POINT: 3 dropped markets worth $1,125/day and 3 worth $3/day are the same COUNT and
    # completely different decisions.
    shape = {}
    rows = _rows([("KXA-1", 1.0), ("KXA-2", 2.0), ("KXB-1", 25.0), ("KXB-2", 1100.0)])
    q._record_pool_shape(rows, rows[:1], shape)
    assert shape["below_cut_n"] == 3
    assert shape["below_cut_usd_day_sum"] == 1127.0


def test_vol24h_coverage_carries_its_own_denominator():
    shape = {}
    rows = _rows([("KXA-1", 10.0), ("KXA-2", 10.0), ("KXB-1", 10.0)])
    rows[0]["vol24h_ct"] = 50.0
    rows[1]["vol24h_ct"] = 4000.0
    q._record_pool_shape(rows, rows[:1], shape)
    assert shape["pool_vol24h_known"] == 2, "2 of 3 candidates carry the input"
    assert shape["pool_n"] == 3, "...and the denominator is stated beside it"
    assert shape["pool_vol24h_p50"] == 4000.0
    assert shape["pool_vol24h_max"] == 4000.0


def test_absent_vol24h_reports_zero_coverage_not_a_fabricated_percentile():
    shape = {}
    rows = _rows([("KXA-1", 10.0), ("KXA-2", 10.0)])
    q._record_pool_shape(rows, [], shape)
    assert shape["pool_vol24h_known"] == 0
    assert "pool_vol24h_p50" not in shape, "no data -> no percentile, never a 0.0 that lies"


def test_one_malformed_row_does_not_destroy_the_whole_record():
    """Found by this pin on the first run (2026-08-03): a single unparseable usd_day raised
    into the outer handler and took the ENTIRE shape record with it — one bad row losing all
    the telemetry, which is exactly backwards for a measurement layer. Bad rows are now skipped
    and counted; everything else still reports."""
    shape = {}
    rows = [{"ticker": "X", "usd_day": 10.0}, {"usd_day": "not-a-number"}, {}]
    q._record_pool_shape(rows, [], shape)
    assert shape["pool_n"] == 3, "the record survives a malformed row"
    assert shape["below_cut_n"] == 3
    assert shape["below_cut_usd_day_sum"] == 10.0, "the good rows still sum"
    assert shape["shape_bad_rows"] == 1, "and the bad one is counted, not silent"


def test_shape_and_drops_stay_in_separate_namespaces():
    q.FP_DROPS.clear()
    q.FP_SHAPE.clear()
    shape = {}
    q._record_pool_shape(_rows([("KXA-1", 10.0)]), [], shape)
    assert q.FP_DROPS == {}, "measurement must not leak into the drop-reason namespace"
    assert "pool_hist" in shape

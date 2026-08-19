"""Pins for the MID-BAND EXCLUSION gate (KALSHI_MID_BAND_OUT) — concentrated-cliff build
2026-08-19: the F9 near-strike toxicity overlay (D3 ladder-near-strike mechanism) made
LIVE at the quote path. Book-mid proxy: mid = (best_y + (1 - best_n)) / 2.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402

M = {"target": 100, "end": "2099-01-01T00:00:00Z", "ticker": "KXTEST-01"}


def _run(yl, nl, inv=0.0, band=(0.10, 0.90)):
    stats = {}
    old = q.MID_BAND_OUT
    q.MID_BAND_OUT = band
    try:
        quotes = q.desired_quotes(M, yl, nl, q.utcnow(), inv=inv, stats=stats)
    finally:
        q.MID_BAND_OUT = old
    return quotes, stats


class TestMidBandGate:
    def test_mid_range_book_is_excluded_flat(self):
        # yes bid .45 / no bid .49 -> yes ask .51 -> mid .48: inside (0.10, 0.90)
        quotes, stats = _run([["0.45", "500"]], [["0.49", "500"]])
        assert quotes == [] and stats.get("gate_mid_band") == 1

    def test_extreme_book_passes_the_gate(self):
        # yes bid .97 / no bid .02 -> mid .975: outside the band. The gate itself must not
        # fire (whatever later gates decide about this book).
        _, stats = _run([["0.97", "500"]], [["0.02", "500"]])
        assert "gate_mid_band" not in stats

    def test_cheap_extreme_passes_too(self):
        _, stats = _run([["0.02", "500"]], [["0.97", "500"]])
        assert "gate_mid_band" not in stats

    def test_holding_still_gets_reducing_exit(self):
        quotes, stats = _run([["0.45", "500"]], [["0.49", "500"]], inv=-10.0)
        assert "gate_mid_band" not in stats, "counter fires only on the priceless [] path"
        assert quotes, "held inventory must still get its reducing exit"
        assert all(o.get("reason") == "unwind" for o in quotes)

    def test_default_off_is_a_provable_noop(self):
        stats = {}
        assert q.MID_BAND_OUT is None or isinstance(q.MID_BAND_OUT, tuple)
        old = q.MID_BAND_OUT
        q.MID_BAND_OUT = None
        try:
            q.desired_quotes(M, [["0.45", "500"]], [["0.49", "500"]], q.utcnow(),
                             inv=0.0, stats=stats)
        finally:
            q.MID_BAND_OUT = old
        assert "gate_mid_band" not in stats

    def test_shipped_default_is_off(self):
        # module was imported without KALSHI_MID_BAND_OUT in the environment
        if "KALSHI_MID_BAND_OUT" not in os.environ:
            assert q.MID_BAND_OUT is None


class TestBandParse:
    def test_good_band(self):
        assert q._parse_mid_band("0.10,0.90") == (0.10, 0.90)

    def test_empty_is_off(self):
        assert q._parse_mid_band("") is None
        assert q._parse_mid_band(None) is None

    def test_malformed_refused_loudly_stays_off(self, capsys):
        assert q._parse_mid_band("banana") is None
        assert "WARNING" in capsys.readouterr().out

    def test_inverted_band_refused(self, capsys):
        assert q._parse_mid_band("0.9,0.1") is None
        assert "WARNING" in capsys.readouterr().out

    def test_out_of_range_refused(self, capsys):
        assert q._parse_mid_band("0.1,1.5") is None
        assert "WARNING" in capsys.readouterr().out

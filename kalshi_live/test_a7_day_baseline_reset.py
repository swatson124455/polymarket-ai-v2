"""A7 (logic audit 2026-08-05, operator-ruled): daily_dd is UTC-day-scoped, so an
operator-NAMED restart inherited the whole day's peak including pre-restart tainted
fills — the 08-05 "halt 18.25 today-only" absorption hack existed only for this.
Now restart_bundle.sh drops a `day_baseline_reset` marker; the quoter consumes it
ONCE on the first untorn equity cycle and re-baselines day-start/peak at current
equity. Auto/crash restarts never create the marker — no amnesty for those.

Pins:
  P1 marker consumed exactly once (True then gone), missing -> False
  P2 unreadable/failing removal reads as absent (governor keeps the stricter meter)
  P3 the equity block consults the marker in its day-reset condition (source pin)
  P4 restart_bundle.sh creates the marker before the service restart (source pin)
"""
import os

import maker_kalshi_quoter as q


def test_p1_marker_consumed_exactly_once(tmp_path, monkeypatch):
    marker = tmp_path / "day_baseline_reset"
    monkeypatch.setattr(q, "DAY_BASELINE_RESET_MARKER", str(marker))
    assert q._consume_day_baseline_marker() is False
    marker.write_text("")
    assert q._consume_day_baseline_marker() is True
    assert not marker.exists()
    assert q._consume_day_baseline_marker() is False


def test_p2_failure_reads_as_absent(tmp_path, monkeypatch):
    # a marker path whose parent is a FILE -> os.path.exists is False on the child,
    # and any exception inside the helper must still read as absent
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(q, "DAY_BASELINE_RESET_MARKER", str(blocker / "deep"))
    assert q._consume_day_baseline_marker() is False


def test_p3_equity_block_consults_the_marker():
    src = open(q.__file__, encoding="utf-8", errors="replace").read()
    i = src.index('st.get("equity_day") != _day or _consume_day_baseline_marker()')
    assert 'st["equity_day_peak"] = _equity' in src[i:i + 1200], \
        "the marker must trigger the same start/peak re-baseline as a new day"


def test_p4_restart_bundle_creates_the_marker():
    p = os.path.join(os.path.dirname(q.__file__), "restart_bundle.sh")
    src = open(p, encoding="utf-8", errors="replace").read()
    assert "day_baseline_reset" in src
    # blind review F11 ordering: OLD daemon stopped -> marker touched -> NEW daemon
    # started, so only the new daemon can ever consume the reset.
    i_stop = src.index("systemctl stop polymarket-maker-kalshi-ws.service")
    i_touch = src.index("touch day_baseline_reset")
    i_start = src.index("systemctl start polymarket-maker-kalshi-ws.service")
    assert i_stop < i_touch < i_start, \
        "marker must be created while the daemon is down, before its first cycle"

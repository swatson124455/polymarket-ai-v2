"""Pins for KALSHI_QUALIFIABLE_GATE — concentrated-cliff build 2026-08-19.

The CFTC-snapshot "unqualifiable -> never open" skip assumed sub-target books pay nobody.
R1 refuted that live (5/7 probe programs accrued nonzero on far-sub-target books); the
real floor is the per-program $1 cliff at conclusion. Flag=1 keeps today's exact behavior
(the shipped default); flag=0 bypasses ONLY the skip — the stat still counts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402

M = {"target": 100000, "end": "2099-01-01T00:00:00Z", "ticker": "KXTEST-01"}
# tiny books vs target 100000 -> unqualifiable on both sides even with activate budget
YL = [["0.02", "50"]]
NL = [["0.97", "50"]]


def _run(flag, inv=0.0):
    stats = {}
    old = q.QUALIFIABLE_GATE
    q.QUALIFIABLE_GATE = flag
    try:
        quotes = q.desired_quotes(M, YL, NL, q.utcnow(), inv=inv, stats=stats)
    finally:
        q.QUALIFIABLE_GATE = old
    return quotes, stats


def test_shipped_default_is_on():
    if "KALSHI_QUALIFIABLE_GATE" not in os.environ:
        assert q.QUALIFIABLE_GATE is True


def test_flag_on_skips_and_counts():
    quotes, stats = _run(True)
    assert quotes == [] and stats.get("unqualifiable") == 1


def test_flag_off_bypasses_the_skip_but_still_counts():
    quotes, stats = _run(False)
    assert stats.get("unqualifiable") == 1, "telemetry must still show the would-be skip"
    # the book must fall through to the LATER gates rather than being refused here;
    # whatever they decide, the unqualifiable skip itself no longer ends the row.
    # (with defaults, presence/netev gates are off and this thin book yields quotes)
    assert quotes != [] or set(stats) - {"unqualifiable"}, \
        "flag=0 must hand the row to the downstream gates, not eat it silently"


def test_flag_off_holding_unchanged():
    # holding inventory never hit this gate on either flag value (inv branch precedes it)
    q_on, _ = _run(True, inv=-10.0)
    q_off, _ = _run(False, inv=-10.0)
    assert q_on == q_off

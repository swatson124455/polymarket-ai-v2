"""THE DAILY HALT MUST NAME THE LIMB THAT BREACHED (2026-07-26).

Two different measures guard the day, with two different limits:
  _dd    TRUE DRAWDOWN from the day's peak -- recovers when equity recovers.
  _down  CUMULATIVE sum of per-cycle decreases -- RATCHETS, never resets.

The message used to render `cumulative-down $X > $DAILY_LOSS_HALT_USD` unconditionally:
the wrong quantity against the wrong limit. Live at 2026-07-26T14:54:08Z it printed
"cumulative-down $8.98 > $10" -- not even arithmetically true -- when the real trigger
was drawdown $13.74 > $10. Misreading WHICH measure halted the bot is the most expensive
diagnostic error this lane has made, so it is pinned here.

These assert on the reason STRING, which is the artifact an operator actually reads.
"""
import os

from test_live_hardening import MockClient, _run, q


def _halt_reason(monkeypatch, tmp_path, dd_limit, down_limit, equities):
    """Drive successive cycles with a scripted equity path and return (reason, stop_text).

    The halt compares balance+held against the day's peak/start carried in quoter_state,
    so equity is stepped by changing the mock balance between cycles.
    """
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", dd_limit)
    monkeypatch.setattr(q, "DAILY_DOWN_HALT_USD", down_limit)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else
                        {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]],
                                          "no_dollars": [["0.49", "9999"]]}})
    row = {}
    for eq in equities:
        c = MockClient(mode="live", resting=[], positions=[])
        c.get_balance = lambda eq=eq: {"balance_dollars": f"{eq:.4f}"}
        row = _run(monkeypatch, c, str(tmp_path))
        stop = os.path.join(str(tmp_path), "STOP")
        if os.path.exists(stop):
            return row.get("daily_halt_reason"), open(stop).read()
    return row.get("daily_halt_reason"), None


def test_drawdown_breach_is_named_as_drawdown(monkeypatch, tmp_path):
    # HALT_CONFIRM_N=1: this test pins the METER (measurement/attribution), not the
    # operator-named 2026-07-29 sustained-breach confirmation — test_audit_batch2 pins that.
    monkeypatch.setattr(q, "HALT_CONFIRM_N", 1)
    # Monotone decline: drawdown grows to 20 while cumulative-down tracks it. With the
    # DOWN limit far away, only DRAWDOWN can be the trigger -- and it must say so.
    reason, stop = _halt_reason(monkeypatch, tmp_path, dd_limit=10.0, down_limit=1e9,
                                equities=[100.0, 95.0, 80.0])
    assert reason, "a halt should have fired"
    assert "DRAWDOWN" in reason
    assert "CUMULATIVE-DOWN" not in reason, \
        f"drawdown breach must not be reported as cumulative-down: {reason}"
    assert stop and "TRIGGER: DRAWDOWN" in stop


def test_cumulative_down_breach_is_named_as_cumulative_down(monkeypatch, tmp_path):
    # Saw-tooth: equity recovers each time, so true drawdown stays small while the
    # ratcheting sum accumulates. With the DD limit far away only CUMULATIVE-DOWN can fire.
    reason, stop = _halt_reason(monkeypatch, tmp_path, dd_limit=1e9, down_limit=8.0,
                                equities=[100.0, 95.0, 100.0, 95.0, 100.0, 95.0, 100.0])
    assert reason, "a halt should have fired"
    assert "CUMULATIVE-DOWN" in reason
    assert "DRAWDOWN" not in reason, \
        f"ratchet breach must not be reported as drawdown: {reason}"
    assert stop and "TRIGGER: CUMULATIVE-DOWN" in stop


def test_reason_states_the_limit_it_breached(monkeypatch, tmp_path):
    # HALT_CONFIRM_N=1: this test pins the METER (measurement/attribution), not the
    # operator-named 2026-07-29 sustained-breach confirmation — test_audit_batch2 pins that.
    monkeypatch.setattr(q, "HALT_CONFIRM_N", 1)
    # The live bug was comparing _down against the DD limit. The reason must carry the
    # limit that the named quantity was actually tested against.
    reason, _ = _halt_reason(monkeypatch, tmp_path, dd_limit=10.0, down_limit=1e9,
                             equities=[100.0, 95.0, 80.0])
    assert "> $10.00" in reason, f"must quote the limit it breached: {reason}"


def test_no_halt_and_no_reason_below_both_limits(monkeypatch, tmp_path):
    reason, stop = _halt_reason(monkeypatch, tmp_path, dd_limit=1e9, down_limit=1e9,
                                equities=[100.0, 95.0, 80.0])
    assert reason is None
    assert stop is None

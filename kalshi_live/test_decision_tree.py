"""DECISION-TREE INTEGRITY — is the logic fluid and seamless, or does it contradict itself?

Enumerates 360 states (5 book shapes x 6 inventory levels x 6 window positions x resting-or-not)
and toggles each of the four opportunity gates: 1,440 evaluations. Asserts three STRUCTURAL
properties that no amount of unit testing implies:

  TOTAL      every state yields a decision — the tree has no hole that raises.
  EXIT-SAFE  we never quote the ACCUMULATING side while offering no way out of inventory.
  MONOTONE   switching a gate ON can never make the bot quote MORE.

MONOTONE is the real "seamless" test. Each gate is written as a restriction, so combining them
must only ever narrow the set of markets quoted. If any gate ever INCREASED quoting, two gates
would be disagreeing about the same market and the combined behaviour would be unpredictable —
exactly the failure that unit tests miss because each gate passes in isolation.
"""
import datetime as dt

from test_live_hardening import q


BOOKS = {
    "healthy":   ([["0.50", "600"], ["0.49", "500"]], [["0.49", "600"], ["0.48", "500"]]),
    "thin":      ([["0.50", "5"]], [["0.49", "5"]]),
    "deep":      ([["0.50", "100000"]], [["0.49", "100000"]]),
    "one-sided": ([["0.50", "600"], ["0.49", "500"]], []),
    "wide":      ([["0.30", "5000"]], [["0.30", "5000"]]),
}
INVS = [0.0, 5.0, -5.0, 40.0, -40.0, 100.0]
WINDOWS = [(1, 0.9), (1, 0.1), (7, 0.9), (7, 0.1), (30, 0.9), (30, 0.1)]
GATES = ["PRESENCE_GATE", "CAPTURE_GATE", "NETEV_GATE", "STANDDOWN"]


def _mk(days, frac_left, now):
    return {"ticker": "TREE", "usd_day": 100.0, "target": 1,
            "end": (now + dt.timedelta(days=days * frac_left)).isoformat(),
            "life_min": days * 1440.0, "df": 0.5}


def _run(m, yl, nl, inv, own, now):
    try:
        return q.desired_quotes(m, yl, nl, now, own=own, inv=inv, stats={})
    except Exception as e:                     # a hole in the tree, not a decision
        return ("CRASH", repr(e))


def _sweep(monkeypatch):
    now = q.utcnow()
    for g in GATES:
        monkeypatch.setattr(q, g, 0)
    monkeypatch.setattr(q, "MIN_CREDIT_USD", 1.20)
    monkeypatch.setattr(q, "PRESENCE_TABLE", {})
    monkeypatch.setattr(q, "PRESENCE_DEFAULT", 1.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 15.0)

    crashes, no_exit, nonmono, states = [], [], [], 0
    for bname, (yl, nl) in BOOKS.items():
        for inv in INVS:
            for days, fr in WINDOWS:
                for own in (None, {"yes": 20.0, "no": 20.0}):
                    m = _mk(days, fr, now)
                    for g in GATES:
                        setattr(q, g, 0)
                    base = _run(m, yl, nl, inv, own, now)
                    states += 1
                    if isinstance(base, tuple):
                        crashes.append((bname, inv, days, fr, base))
                        continue
                    if abs(inv) >= 3.0:
                        red = "no" if inv > 0 else "yes"
                        if [x for x in base if x["side"] != red] and \
                           not [x for x in base if x["side"] == red]:
                            no_exit.append((bname, inv, days, fr, "gates-off"))
                    for gate in GATES:
                        setattr(q, gate, 1)
                        got = _run(m, yl, nl, inv, own, now)
                        setattr(q, gate, 0)
                        if isinstance(got, tuple):
                            crashes.append((bname, inv, days, fr, got))
                            continue
                        if len(got) > len(base):
                            nonmono.append((gate, bname, inv, days, fr, len(base), len(got)))
                        if abs(inv) >= 3.0:
                            red = "no" if inv > 0 else "yes"
                            if [x for x in got if x["side"] != red] and \
                               not [x for x in got if x["side"] == red]:
                                no_exit.append((bname, inv, days, fr, gate))
    return crashes, no_exit, nonmono, states


def test_decision_tree_is_total_exit_safe_and_monotone(monkeypatch):
    crashes, no_exit, nonmono, states = _sweep(monkeypatch)
    assert states >= 300, "enumeration collapsed — the sweep is not covering the state space"
    assert not crashes, f"tree has holes that raise: {crashes[:3]}"
    assert not no_exit, f"quoted the accumulating side with no exit: {no_exit[:3]}"
    assert not nonmono, f"a gate INCREASED quoting when switched on: {nonmono[:3]}"

"""THE STOP BRANCH MUST RESET THE PUBLIC READ BUDGET (root fix 2026-08-02).

`public_get` refuses once `_reads[0] >= READ_BUDGET_PER_CYCLE` (maker_kalshi_quoter.py:1305),
and the counter is reset once per cycle at the bottom of the pre-STOP block. The STOP branch
RETURNS before that reset, so under the long-lived WS daemon the counter is MONOTONE across
STOP cycles: every flatten spends from a budget that never refills.

On exhaustion each per-ticker book read in _flatten_all pass 1 raises, every ticker takes the
"book unreadable" bail (:4894-4896), and no MAKER offset is ever rested again — a halted bot
left holding inventory with no working exit, which is the opposite of what STOP is for.

MEASURED on the live daemon 2026-08-02 (journal, window 10:26:00Z onwards): 27 flatten runs,
1,389 paced skips, 0 exhaustions. Real, but not yet triggered in that halt window — the paced
skip branch returns without spending reads, so only flatten runs accumulate.

These pins drive a SMALL budget so the exhaustion the live daemon approaches slowly happens in
a few cycles.
"""
import os

from test_live_hardening import MockClient, _run, q

_BOOK = {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]],
                          "no_dollars": [["0.49", "9999"]]}}


def _held(pos="20", exposure="10.00", ticker="TT"):
    return [{"ticker": ticker, "position_fp": pos, "market_exposure_dollars": exposure}]


def _arm(monkeypatch, budget=3):
    """STOP present, pacing off, tiny read budget, and a COUNTER-FAITHFUL public_get.

    The stub reproduces the real accounting at :1304-1315 — refuse at the ceiling, otherwise
    increment — because the defect lives in that counter, not in the network call."""
    monkeypatch.setattr(q, "READ_BUDGET_PER_CYCLE", budget)
    monkeypatch.setattr(q, "STOPFLAT_REPEAT_S", 0.0)      # never skip on pacing
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)          # no real sleep in pass 2
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 1e9)      # keep pass 2 out of pass 1's way

    def pg(path):
        if q._reads[0] >= q.READ_BUDGET_PER_CYCLE:
            raise RuntimeError("read budget exhausted")
        q._reads[0] += 1
        if "incentive" in path:
            return {"incentive_programs": [], "next_cursor": ""}
        return _BOOK

    monkeypatch.setattr(q, "public_get", pg)


def _stop_cycle(monkeypatch, tmp_path):
    """One STOP cycle against a fresh client; returns it so creates can be inspected."""
    with open(os.path.join(str(tmp_path), "STOP"), "w") as fh:
        fh.write("test halt\n")
    c = MockClient(mode="live", resting=[], positions=_held())
    _run(monkeypatch, c, str(tmp_path))
    return c


def test_every_stop_cycle_rests_an_offset(monkeypatch, tmp_path):
    # THE DEFECT. Pre-fix the counter carries across cycles, so with a 3-read budget the later
    # cycles find it exhausted, every book read raises, and no offset is ever rested again.
    _arm(monkeypatch, budget=3)
    q._reads[0] = 0
    for cycle in range(5):
        c = _stop_cycle(monkeypatch, tmp_path)
        assert c.created, f"cycle {cycle + 1}: no MAKER offset rested (read budget starved?)"


def test_budget_is_reset_not_merely_large(monkeypatch, tmp_path):
    # Guards against "raise the budget" being mistaken for the fix: start the counter ALREADY
    # at the ceiling. Only an actual reset can recover.
    _arm(monkeypatch, budget=3)
    q._reads[0] = 999
    c = _stop_cycle(monkeypatch, tmp_path)
    assert c.created, "a pre-exhausted counter must be reset by the STOP branch itself"
    assert q._reads[0] <= 3


def test_reset_happens_before_the_flatten_reads(monkeypatch, tmp_path):
    # Placement pin: a reset AFTER _flatten_all would leave the flatten itself starved. The
    # counter must be low enough during pass 1 for the book read to succeed.
    _arm(monkeypatch, budget=3)
    q._reads[0] = 999
    c = _stop_cycle(monkeypatch, tmp_path)
    assert c.created, "the flatten's own book read must run inside a fresh budget"


def test_reset_also_covers_dry_run(monkeypatch, tmp_path):
    # The reset sits ABOVE the `client.mode != "dry_run"` guard, so the counter cannot creep in
    # a dry-run deployment either. dry_run performs no flatten, so assert the counter directly.
    _arm(monkeypatch, budget=3)
    q._reads[0] = 999
    with open(os.path.join(str(tmp_path), "STOP"), "w") as fh:
        fh.write("test halt\n")
    _run(monkeypatch, MockClient(mode="dry_run", resting=[], positions=_held()), str(tmp_path))
    assert q._reads[0] <= 3, "the STOP reset must not sit inside the non-dry_run branch"


def test_normal_path_budget_accounting_is_unchanged(monkeypatch, tmp_path):
    # No leak into the normal path: with STOP absent the pre-existing reset is still the one
    # that fires, and a cycle's reads stay within budget.
    _arm(monkeypatch, budget=50)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    q._reads[0] = 999
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert not os.path.exists(os.path.join(str(tmp_path), "STOP"))
    assert q._reads[0] <= 50
    assert row.get("reads") is not None, "the normal path still reports its read count"

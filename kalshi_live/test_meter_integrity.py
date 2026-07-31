"""F METER-INTEGRITY BUNDLE (operator-named 2026-07-31): F2 balance-fail -> reduce-only,
F3 governor fail-closed + last-good snapshot, F4 dd credit adjustment. (F1 N-of-5 halt
confirm is pinned in test_audit_batch2.test_halt_requires_sustained_breach.)"""
import json
import os

from test_live_hardening import q, MockClient, _run, _cfg


def _pos(t="T1", pos="0.50", realized="0.00"):
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": "0.25",
            "realized_pnl_dollars": realized}


def _fp1(monkeypatch):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])


def _state(tmp_path):
    return json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))


class _BalFailClient(MockClient):
    def get_balance(self):
        raise RuntimeError("balance 500")


def test_f2_balance_fail_streak_goes_reduce_only(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    c1 = _BalFailClient(mode="live", positions=[])
    row1 = _run(monkeypatch, c1, str(tmp_path))
    assert row1.get("balance_fail_reduce_only") is None, "first failure stays free (blips)"
    assert len(c1.created) > 0
    c2 = _BalFailClient(mode="live", positions=[])
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert row2.get("balance_fail_reduce_only") == 1, "streak>=2: halt blind -> stop acquiring"
    assert c2.created == []


def test_f2_recovery_restores_quoting(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    for _ in range(2):
        _run(monkeypatch, _BalFailClient(mode="live", positions=[]), str(tmp_path))
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("balance_fail_reduce_only") is None and len(c.created) > 0


def test_f3_last_good_snapshot_beats_side_channel(monkeypatch, tmp_path):
    """A read failure must fall back to the last-good ALL-TRADED snapshot (which still sees
    flat markets), not the position-filtered side channel."""
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 5.0)
    q._REALIZED_LAST_GOOD.clear()
    _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]), str(tmp_path))
    # burn-and-run WHILE the read is down: flat (positions empty) but last cycle's good
    # snapshot baselined T1 at 0; seed LAST_GOOD with the burned value as if seen pre-failure
    q._REALIZED_LAST_GOOD["T1"] = -21.0
    c2 = MockClient(mode="live", positions=[], get_realized_raises=True)
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert row2.get("loss_exitonly") == 1, "last-good snapshot must still catch the burn"
    q._REALIZED_LAST_GOOD.clear()


def test_f3_governor_fault_streak_goes_reduce_only(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 5.0)
    # force the governor block itself to raise: _two_strikes explodes inside the try
    monkeypatch.setattr(q, "_two_strikes", lambda *a, **k: (_ for _ in ()).throw(ValueError()))
    rows = []
    for _ in range(3):
        c = MockClient(mode="live", positions=[_pos(realized="0.00")])
        rows.append((_run(monkeypatch, c, str(tmp_path)), c))
    assert rows[0][0].get("governor_fail_reduce_only") is None
    assert rows[2][0].get("governor_fail_reduce_only") == 1, "3 faulted cycles -> fail closed"
    assert rows[2][1].created == []


# F4 (dd credit-adjustment) was REVERTED before deploy: the cycle-heuristic estimator
# misreads order-collateral releases (cancel waves) as credits -> inflated dd -> false-halt
# class. Needs the ledger-grade design (settlements + fills reconciliation). OPEN item.


def test_f2_reduce_only_survives_book_fetch_failure(monkeypatch, tmp_path):
    """The F6a defect class: on a transient book-fetch error the retained standing copy must
    not route around a WHOLE-BOOK reduce-only cycle (balance-fail streak >= 2)."""
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    def _pg(p):
        if "orderbook" in p:
            raise ValueError("book 500")
        return {"incentive_programs": [], "next_cursor": ""}
    from test_live_hardening import _order
    _run(monkeypatch, _BalFailClient(mode="live", positions=[]), str(tmp_path))
    monkeypatch.setattr(q, "public_get", _pg)
    c2 = _BalFailClient(mode="live", positions=[],
                        resting=[_order("acc1", "T1", "yes", 0.50, 10)])
    _run(monkeypatch, c2, str(tmp_path))
    assert "acc1" in c2.cancelled,         "reduce-only cycle must cancel retained accumulating orders even on a fetch error"

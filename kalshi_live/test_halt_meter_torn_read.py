"""TORN-READ ROOT FIX PINS (defect 1 of the 2026-08-02 halt post-mortem, operator-named).

The equity meter used to sum a positions read taken ~230 lines before the balance read; a
fill landing between them was seen by ONE half only. On 08-02 that manufactured $23.70 of
the $68.68 halt reading (34.51%) — the largest single artifact was 50 ct x $0.4300 = $21.50
exactly, round-tripping to the cent on the next cycle. The fix brackets the balance read
with a positions re-read: equal position digests prove no fill landed between them; one
bounded retry; still unstable -> the cycle is TORN — telemetry emits, but day-seed, peak
and the halt verdict are all skipped (counted + warned), and 2 consecutive torn cycles
stop acquisition (mirror of the balance-fail F2 rule).

These pins replay the artifact shape against the fixed meter.
"""
import json
import os

from test_live_hardening import MockClient, _run, q

_FLAT = []
_LONG = [{"ticker": "TT", "position_fp": "50", "market_exposure_dollars": "21.50"}]


class TearClient(MockClient):
    """get_positions returns seq[call#] (last entry repeats) — simulates fills landing
    between the cycle's reads. get_balance is pinned to the post-fill cash level."""

    def __init__(self, seq, bal, **kw):
        super().__init__(mode="live", resting=[], positions=list(seq[0]), **kw)
        self._seq = [list(s) for s in seq]
        self._balv = bal
        self._pcalls = 0

    def get_positions(self):
        i = min(self._pcalls, len(self._seq) - 1)
        self._pcalls += 1
        return {"market_positions": list(self._seq[i])}

    def get_balance(self):
        return {"balance_dollars": f"{self._balv:.4f}"}


def _arm(monkeypatch, dd_limit=5.0):
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", dd_limit)
    monkeypatch.setattr(q, "HALT_CONFIRM_N", 1)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else
                        {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]],
                                          "no_dollars": [["0.49", "9999"]]}})


def _seed_day(tmp_path, equity):
    day = q.utcnow().strftime("%Y%m%d")
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"equity_day": day, "equity_day_start": equity,
                   "equity_day_peak": equity, "equity_prev": equity,
                   "equity_basis": "mark"}, fh)


def test_consistent_snapshot_is_the_normal_path(monkeypatch, tmp_path):
    _arm(monkeypatch)
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("equity_torn") == 0, "consistent cycles must emit the counter as 0"
    assert "equity_snapshot_retried" not in row


def test_fill_between_reads_is_healed_by_the_retry(monkeypatch, tmp_path):
    # A sell lands right after the quote-loop positions read: cash already holds the
    # proceeds AND the sold position would still be marked — the naive sum double-counts
    # (the upward tear that would inflate the day-peak ratchet forever). The retry's
    # bracketing reads agree on the post-fill book, so equity comes out clean.
    _arm(monkeypatch)
    c = TearClient(seq=[_LONG, _FLAT, _FLAT], bal=125.0)   # P1 stale, P2/P3 post-fill
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("equity_snapshot_retried") == 1
    assert row.get("equity_torn") == 0
    assert row.get("equity_mark_usd") == 125.0, "no double-count: cash only, position gone"
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert st.get("equity_day_peak") == 125.0, \
        f"peak must seed at the consistent level, not the torn 150: {st.get('equity_day_peak')}"


def test_persistent_tear_skips_the_halt_verdict(monkeypatch, tmp_path):
    # The 08-02 artifact shape: a buy's cash leaves the balance while the positions read
    # still shows flat — naive equity 78.50 vs peak 100 reads as a $21.50 phantom drawdown
    # (>> the $5 limit; the old meter halts on it). Positions never stabilise across the
    # retry -> TORN: no STOP, no peak move, counter on the plan row.
    _arm(monkeypatch, dd_limit=5.0)
    _seed_day(tmp_path, 100.0)
    c = TearClient(seq=[_FLAT, _LONG, _FLAT, _LONG], bal=78.50)   # alternates every read
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("equity_torn") == 1
    assert not os.path.exists(os.path.join(str(tmp_path), "STOP")), \
        "a torn equity level must never write STOP"
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert st.get("equity_day_peak") == 100.0, "torn cycles must not move the peak"
    assert st.get("equity_torn_streak") == 1


def test_two_torn_cycles_stop_acquisition(monkeypatch, tmp_path):
    _arm(monkeypatch, dd_limit=5.0)
    _seed_day(tmp_path, 100.0)
    row1 = _run(monkeypatch, TearClient(seq=[_FLAT, _LONG, _FLAT, _LONG], bal=78.50),
                str(tmp_path))
    assert row1.get("equity_torn_streak") == 1
    assert "equity_torn_reduce_only" not in row1
    row2 = _run(monkeypatch, TearClient(seq=[_FLAT, _LONG, _FLAT, _LONG], bal=78.50),
                str(tmp_path))
    assert row2.get("equity_torn_streak") == 2
    assert row2.get("equity_torn_reduce_only") == 1, \
        "the guard may not stay quietly blind two cycles running"
    assert not os.path.exists(os.path.join(str(tmp_path), "STOP"))


def test_reread_failure_is_torn_not_a_crash(monkeypatch, tmp_path):
    # The confirming positions read fails outright: consistency cannot be PROVEN, so the
    # cycle is torn (fail safe) — never a crash, never a halt verdict on the unproven level.
    _arm(monkeypatch, dd_limit=5.0)
    _seed_day(tmp_path, 100.0)

    class RereadFails(TearClient):
        def get_positions(self):
            if self._pcalls >= 1:
                self._pcalls += 1
                raise RuntimeError("positions 503")
            return super().get_positions()

    row = _run(monkeypatch, RereadFails(seq=[_FLAT], bal=78.50), str(tmp_path))
    assert row.get("equity_reread_failed")
    assert row.get("equity_torn") == 1
    assert not os.path.exists(os.path.join(str(tmp_path), "STOP"))

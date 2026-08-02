"""TORN-READ ROOT FIX PINS (defect 1 of the 2026-08-02 halt post-mortem, operator-named).

The equity meter used to sum a positions read taken ~230 lines before the balance read; a
fill landing between them was seen by ONE half only. On 08-02 that manufactured $23.70 of
the $68.68 halt reading (34.51%) — the largest single artifact was 50 ct x $0.4300 = $21.50
exactly, round-tripping to the cent on the next cycle. The fix brackets the balance read
with a positions re-read: equal position digests prove no fill landed between them; one
bounded retry; still unstable -> the cycle is TORN — telemetry emits, but day-seed, peak
and the halt verdict are all skipped (counted + warned), and 2 consecutive torn cycles
stop acquisition (mirror of the balance-fail F2 rule).

PIN COVERAGE (hardened 2026-08-02 after the A1 blind review, which proved by mutation that
the first version of this file left three holes — each mutant kept the whole suite green):
  * DIGEST CONTENT. The original fixtures differed only by TICKER PRESENCE, so a digest
    weakened to keys-only, to abs(position), to no-cost, or to coarse rounding all survived.
    The modal maker events are the opposite shape: a partial fill on a ticker already held,
    a +50 -> -50 flip at identical |size|, and a cost-basis move at unchanged count. All
    three are pinned below.
  * RETRY'S FRESH BALANCE. Every get_balance mock in the suite returned a per-instance
    CONSTANT, so deleting the retry's second balance read survived. TearClient now accepts a
    SEQUENCE of balances, and a pin fails if the retry reuses the stale one.
  * REDUCE-ONLY BEHAVIOUR. The 2-torn escalation was pinned only by its telemetry key, so
    dropping `_exit_only_all = True` survived. The fixture now quotes a real footprint and
    asserts on orders actually created, mirroring the F2 pin at test_meter_integrity.py:39.
"""
import json
import os

from test_live_hardening import MockClient, _cfg, _run, q

_FLAT = []
_LONG = [{"ticker": "TT", "position_fp": "50", "market_exposure_dollars": "21.50"}]
# Same ticker, same |size|, OPPOSITE sign — a flip is a real fill, and a digest that drops
# the sign would call this "unchanged".
_SHORT = [{"ticker": "TT", "position_fp": "-50", "market_exposure_dollars": "21.50"}]
# Same ticker, same cost, ONE contract fewer — the modal maker event (partial fill).
_LONG_49 = [{"ticker": "TT", "position_fp": "49", "market_exposure_dollars": "21.07"}]
# Same ticker, same COUNT, different cost basis — the venue re-states market_exposure when
# an averaging fill lands; a digest that drops the cost element would miss it.
_LONG_COSTIER = [{"ticker": "TT", "position_fp": "50", "market_exposure_dollars": "30.00"}]


class TearClient(MockClient):
    """get_positions returns seq[call#] (last entry repeats) — simulates fills landing
    between the cycle's reads. `bal` is a scalar or a SEQUENCE consumed the same way, so a
    test can make the retry's fresh balance differ from the first read."""

    def __init__(self, seq, bal, **kw):
        super().__init__(mode="live", resting=[], positions=list(seq[0]), **kw)
        self._seq = [list(s) for s in seq]
        self._bals = list(bal) if isinstance(bal, (list, tuple)) else [bal]
        self._pcalls = 0
        self._bcalls = 0

    def get_positions(self):
        i = min(self._pcalls, len(self._seq) - 1)
        self._pcalls += 1
        return {"market_positions": list(self._seq[i])}

    def get_balance(self):
        i = min(self._bcalls, len(self._bals) - 1)
        self._bcalls += 1
        return {"balance_dollars": f"{float(self._bals[i]):.4f}"}


def _arm(monkeypatch, dd_limit=5.0):
    # _cfg supplies the quoting config (JOIN_SIZE, caps, family cap off, book mock) that the
    # F2 pin uses — without it the footprint is selected but nothing is ever created, which
    # would make every "must stop acquiring" assertion below vacuously true.
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", dd_limit)
    monkeypatch.setattr(q, "HALT_CONFIRM_N", 1)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)
    # A REAL footprint, so acquisition is observable as created orders (the F2 pin's shape at
    # test_meter_integrity.py:16-17). An empty footprint quotes nothing.
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])


def _seed_day(tmp_path, equity):
    day = q.utcnow().strftime("%Y%m%d")
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"equity_day": day, "equity_day_start": equity,
                   "equity_day_peak": equity, "equity_prev": equity,
                   "equity_basis": "mark"}, fh)


def _state(tmp_path):
    return json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))


# ---- the happy path -------------------------------------------------------------------

def test_consistent_snapshot_is_the_normal_path(monkeypatch, tmp_path):
    _arm(monkeypatch)
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("equity_torn") == 0, "consistent cycles must emit the counter as 0"
    assert "equity_snapshot_retried" not in row
    assert len(c.created) > 0, "a consistent cycle must still quote"


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
    assert _state(tmp_path).get("equity_day_peak") == 125.0, \
        "peak must seed at the consistent level, not the torn 150"


def test_retry_uses_a_FRESH_balance_not_the_stale_one(monkeypatch, tmp_path):
    # THE POINT OF THE SECOND BRACKET. P1 is pre-fill and the first balance is pre-fill too;
    # the fill then lands, so the retry must re-read BOTH. If the retry reuses the stale
    # $78.50 while pairing it with the post-fill flat book, equity reads 78.50 against a
    # day-peak of 100.00 -> a $21.50 phantom drawdown that halts at the $5 limit.
    _arm(monkeypatch, dd_limit=5.0)
    _seed_day(tmp_path, 100.0)
    c = TearClient(seq=[_LONG, _FLAT, _FLAT], bal=[78.50, 100.0])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("equity_snapshot_retried") == 1
    assert row.get("equity_torn") == 0
    assert row.get("equity_mark_usd") == 100.0, \
        "the retry must pair the RE-READ balance with the re-read positions"
    assert row.get("daily_dd") == 0.0
    assert not os.path.exists(os.path.join(str(tmp_path), "STOP"))


# ---- digest CONTENT: each of these differs from _LONG in exactly one field ---------------

def _torn_row(monkeypatch, tmp_path, second):
    """One cycle whose positions alternate _LONG -> `second` on every read: the digests can
    never agree, so a digest that SEES the changed field must declare the cycle torn."""
    _arm(monkeypatch, dd_limit=5.0)
    _seed_day(tmp_path, 100.0)
    return _run(monkeypatch,
                TearClient(seq=[_LONG, second, _LONG, second], bal=78.50), str(tmp_path))


def test_digest_catches_a_sign_flip_at_identical_size(monkeypatch, tmp_path):
    # +50 -> -50. A digest on abs(position) calls this unchanged and blesses a torn cycle.
    assert _torn_row(monkeypatch, tmp_path, _SHORT).get("equity_torn") == 1


def test_digest_catches_a_partial_fill_on_a_held_ticker(monkeypatch, tmp_path):
    # 50 -> 49, same ticker. A keys-only digest, or one rounding the position coarsely,
    # calls this unchanged — and this is the MODAL maker fill, not an edge case.
    assert _torn_row(monkeypatch, tmp_path, _LONG_49).get("equity_torn") == 1


def test_digest_catches_a_cost_basis_move_at_unchanged_count(monkeypatch, tmp_path):
    # count identical, market_exposure_dollars 21.50 -> 30.00. A digest that drops the cost
    # element misses it, and cost is what the mark-fallback path values inventory with.
    assert _torn_row(monkeypatch, tmp_path, _LONG_COSTIER).get("equity_torn") == 1


# ---- torn-cycle consequences ------------------------------------------------------------

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
    st = _state(tmp_path)
    assert st.get("equity_day_peak") == 100.0, "torn cycles must not move the peak"
    assert st.get("equity_torn_streak") == 1


def test_two_torn_cycles_stop_acquisition(monkeypatch, tmp_path):
    # BEHAVIOURAL, not just telemetry: the meter is provably blind, so the bot must stop
    # ACQUIRING (unwinds/exits untouched) — the same contract F2 pins for balance failures
    # at test_meter_integrity.py:39.
    _arm(monkeypatch, dd_limit=5.0)
    _seed_day(tmp_path, 100.0)
    c1 = TearClient(seq=[_FLAT, _LONG, _FLAT, _LONG], bal=78.50)
    row1 = _run(monkeypatch, c1, str(tmp_path))
    assert row1.get("equity_torn_streak") == 1
    assert "equity_torn_reduce_only" not in row1, "one tear stays free (blips)"
    assert len(c1.created) > 0, "a single torn cycle must NOT stop quoting"

    c2 = TearClient(seq=[_FLAT, _LONG, _FLAT, _LONG], bal=78.50)
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert row2.get("equity_torn_streak") == 2
    assert row2.get("equity_torn_reduce_only") == 1, \
        "the guard may not stay quietly blind two cycles running"
    assert c2.created == [], "streak>=2 must stop acquiring, not just log it"
    assert not os.path.exists(os.path.join(str(tmp_path), "STOP"))


def test_a_healthy_cycle_clears_the_torn_streak(monkeypatch, tmp_path):
    # Recovery must be automatic — a stuck streak would keep the bot reduce-only forever.
    _arm(monkeypatch, dd_limit=5.0)
    _seed_day(tmp_path, 100.0)
    for _ in range(2):
        _run(monkeypatch, TearClient(seq=[_FLAT, _LONG, _FLAT, _LONG], bal=78.50),
             str(tmp_path))
    assert _state(tmp_path).get("equity_torn_streak") == 2
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("equity_torn") == 0
    assert _state(tmp_path).get("equity_torn_streak") == 0
    assert "equity_torn_reduce_only" not in row
    assert len(c.created) > 0, "recovery must restore quoting"


def test_day_rollover_also_clears_the_torn_streak(monkeypatch, tmp_path):
    # The streak is cleared on BOTH consistent limbs — the same-day one above and the
    # new-day seed. Missed by the same-day pin alone (mutation M7, 2026-08-02): a bot that
    # ends the day torn twice would cross midnight still reduce-only, and the new day's
    # baselines would be seeded while it refused to quote.
    _arm(monkeypatch, dd_limit=5.0)
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"equity_day": "19700101", "equity_day_start": 100.0,
                   "equity_day_peak": 100.0, "equity_prev": 100.0,
                   "equity_basis": "mark", "equity_torn_streak": 2}, fh)
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    st = _state(tmp_path)
    assert st.get("equity_day") == q.utcnow().strftime("%Y%m%d"), "the new day must seed"
    assert st.get("equity_torn_streak") == 0, "rollover must clear the streak too"
    assert row.get("equity_torn") == 0
    assert "equity_torn_reduce_only" not in row
    assert len(c.created) > 0, "a fresh day on a healthy read must quote"


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

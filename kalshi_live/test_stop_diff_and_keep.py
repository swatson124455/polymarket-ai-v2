"""STOP FLATTEN IS DIFF-AND-KEEP (defect 11 root fix, operator-named 2026-08-02).

`_flatten_all` used to cancel the entire resting book BEFORE computing what it wanted, so it
structurally could not notice that a resting order already IS the order it is about to place.
Live evidence: 27 consecutive flattens in the 2026-08-02 halt window each logged
"cancelled 2/2 resting quotes" and re-created the same two offsets with fresh client ids,
surrendering queue position every 30 minutes for nothing.

The fix reuses diff_orders(:2482) — the same mechanism the normal quoting path uses — after
moving the position read above the cancel. Resting exit offsets under STOP are canon-confirmed
behaviour, NOT the defect; the churn is.

OPERATOR DECISION pinned here (2026-08-02): on all three bail paths — book unreadable,
reducing side unpriceable, loss cap leaving no priceable offset — the existing exit is KEPT.

WHAT MUST NOT REGRESS: "stop making" still kills every accumulating quote, every order on a
ticker with no material inventory, and every unparseable row, all before any book read; and no
ticker may ever end a flatten with two resting exits (2x inventory crosses THROUGH flat).
"""
import os

from test_live_hardening import MockClient, _order, _run, q

_BOOK = {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]],
                          "no_dollars": [["0.49", "9999"]]}}


def _held(pos="20", exposure="10.00", ticker="TT"):
    return [{"ticker": ticker, "position_fp": pos, "market_exposure_dollars": exposure}]


def _arm(monkeypatch, book=_BOOK):
    monkeypatch.setattr(q, "STOPFLAT_REPEAT_S", 0.0)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 1e9)     # keep pass 2 clear of pass 1
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else book)


def _flat(monkeypatch, tmp_path, client):
    with open(os.path.join(str(tmp_path), "STOP"), "w") as fh:
        fh.write("test halt\n")
    _run(monkeypatch, client, str(tmp_path))
    return client


def _the_offset(pos=20, price=0.49, cnt=20, oid="keep-me", ticker="TT"):
    """The exact offset the flatten computes for a +pos long-yes position on _BOOK:
    reducing side is 'no', price is the best NO bid 0.49, size is |pos|."""
    return _order(oid, ticker, "no", price, cnt)


# ---- the defect itself -------------------------------------------------------------------

def test_identical_offset_is_kept_not_churned(monkeypatch, tmp_path):
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[_the_offset()], positions=_held()))
    assert c.cancelled == [], f"an identical exit must not be cancelled: {c.cancelled}"
    assert c.created == [], f"...nor re-created: {c.created}"


def test_a_stale_offset_is_replaced(monkeypatch, tmp_path):
    # Price moved: the resting offset no longer matches, so it must be cancelled AND replaced.
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[_the_offset(price=0.42)], positions=_held()))
    assert c.cancelled == ["keep-me"]
    assert len(c.created) == 1 and c.created[0]["side"] == "no"


def test_wrong_size_offset_is_replaced(monkeypatch, tmp_path):
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[_the_offset(cnt=5)], positions=_held()))
    assert c.cancelled == ["keep-me"]
    assert len(c.created) == 1 and c.created[0]["count"] == 20


# ---- "stop making" must be intact ---------------------------------------------------------

def test_accumulating_quote_on_a_held_ticker_still_dies(monkeypatch, tmp_path):
    # A YES bid against a LONG-YES position accumulates; only the reducing side is deferred.
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live",
                         resting=[_order("acc", "TT", "yes", 0.50, 20), _the_offset()],
                         positions=_held()))
    assert "acc" in c.cancelled, "accumulating quotes must still be cancelled"
    assert "keep-me" not in c.cancelled


def test_order_on_a_ticker_with_no_inventory_still_dies(monkeypatch, tmp_path):
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live",
                         resting=[_order("other", "ZZ", "no", 0.49, 20), _the_offset()],
                         positions=_held()))
    assert "other" in c.cancelled
    assert "keep-me" not in c.cancelled


def test_unparseable_row_is_always_cancelled(monkeypatch, tmp_path):
    # No outcome_side -> unparseable -> we cannot reason about it -> it must die. This is the
    # cancel-cover guarantee that the old cancel-everything gave for free.
    _arm(monkeypatch)
    bad = {"order_id": "bad", "ticker": "TT"}
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[bad, _the_offset()], positions=_held()))
    assert "bad" in c.cancelled
    assert "keep-me" not in c.cancelled


# ---- never two exits on one ticker ---------------------------------------------------------

def test_duplicate_STALE_offsets_do_not_leave_double_inventory(monkeypatch, tmp_path):
    """BLIND-REVIEW FIX 2026-08-03. The pin below covers duplicates that MATCH the desired
    offset; this covers the one that broke. diff_orders builds `have` as a dict comprehension,
    so two IDENTICAL resting rows collapse to ONE key and only ONE cancel is emitted. Choosing
    the survivor by cancel-list membership therefore kept the un-cancelled twin AND created a
    replacement on top — reproduced at 40 ct resting against a 20 ct position, which on a full
    fill crosses THROUGH flat into the opposite position (_unwind_size:1691-1693 forbids
    exactly this). The survivor must be chosen by DESIRED-KEY match instead."""
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live",
                         resting=[_the_offset(price=0.42, oid="stale-a"),
                                  _the_offset(price=0.42, oid="stale-b")],
                         positions=_held()))
    assert set(c.cancelled) == {"stale-a", "stale-b"}, \
        f"BOTH stale twins must die, not just the one diff_orders noticed: {c.cancelled}"
    assert len(c.created) == 1
    resting = sum(o["count"] for o in c.created)
    assert resting == 20, f"resting size must never exceed the position: {resting} ct vs 20"


def test_three_stale_duplicates_still_leave_one_offset(monkeypatch, tmp_path):
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live",
                         resting=[_the_offset(price=0.42, oid="s1"),
                                  _the_offset(price=0.42, oid="s2"),
                                  _the_offset(price=0.42, oid="s3")],
                         positions=_held()))
    assert set(c.cancelled) == {"s1", "s2", "s3"}
    assert sum(o["count"] for o in c.created) == 20


def test_mixed_stale_and_matching_keeps_only_the_match(monkeypatch, tmp_path):
    # One resting offset matches what we want, another is stale. Keep the match, kill the
    # stale one, create nothing.
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live",
                         resting=[_the_offset(price=0.42, oid="stale"),
                                  _the_offset(oid="good")],
                         positions=_held()))
    assert c.cancelled == ["stale"]
    assert c.created == []


def test_duplicate_identical_offsets_leave_exactly_one(monkeypatch, tmp_path):
    # diff_orders keys on (side, price, count), so two IDENTICAL rows collapse to one key and
    # BOTH would survive — 2x inventory resting on the reducing side, which on a full fill
    # crosses THROUGH flat into the opposite position. Exactly one must remain.
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live",
                         resting=[_the_offset(oid="dup-a"), _the_offset(oid="dup-b")],
                         positions=_held()))
    assert len(c.cancelled) == 1 and c.cancelled[0] in ("dup-a", "dup-b")
    assert c.created == [], "the survivor must not be re-created"


# ---- bail paths: KEEP (operator decision) ---------------------------------------------------

def test_unreadable_book_keeps_the_existing_exit(monkeypatch, tmp_path):
    def pg(p):
        if "incentive" in p:
            return {"incentive_programs": [], "next_cursor": ""}
        raise RuntimeError("book 500")
    _arm(monkeypatch)
    monkeypatch.setattr(q, "public_get", pg)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[_the_offset()], positions=_held()))
    assert c.cancelled == [], "a blind cycle must not destroy the only working exit"
    assert c.created == []


def test_bail_path_keeps_the_EXIT_only_never_an_accumulating_quote(monkeypatch, tmp_path):
    # THE PIN MUTATION TESTING FOUND MISSING (2026-08-02). Deferring both sides, or deferring
    # every ticker, leaves the suite green on the happy path — because diff_orders is total over
    # set(standing) | set(desired) and cancels anything not desired, so the accumulating quote
    # still dies, just LATER. The property those mutants really break is ORDERING: "stop making"
    # must complete before any book read, so a hang, a crash or an exhausted read budget mid-loop
    # cannot leave an accumulating quote resting.
    # The bail path is where that difference becomes observable: whatever was deferred is what
    # _keep_verbatim can resurrect. Only the reducing side may ever be eligible.
    def pg(p):
        if "incentive" in p:
            return {"incentive_programs": [], "next_cursor": ""}
        raise RuntimeError("book 500")
    _arm(monkeypatch)
    monkeypatch.setattr(q, "public_get", pg)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live",
                         resting=[_order("acc", "TT", "yes", 0.50, 20), _the_offset()],
                         positions=_held()))
    assert "acc" in c.cancelled, \
        "an accumulating quote must be cancelled BEFORE the book read, so a bail cannot keep it"
    assert "keep-me" not in c.cancelled, "the exit is still kept on a blind cycle"
    assert c.created == []


def test_unpriceable_side_keeps_the_existing_exit(monkeypatch, tmp_path):
    # Empty NO side -> the reducing side for a long-yes position cannot be priced.
    _arm(monkeypatch, book={"orderbook_fp": {"yes_dollars": [["0.50", "9999"]],
                                             "no_dollars": []}})
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[_the_offset()], positions=_held()))
    assert c.cancelled == []
    assert c.created == []


def test_bail_with_no_resting_order_creates_nothing(monkeypatch, tmp_path):
    # KEEP semantics must not invent an order out of nothing when there was none to keep.
    def pg(p):
        if "incentive" in p:
            return {"incentive_programs": [], "next_cursor": ""}
        raise RuntimeError("book 500")
    _arm(monkeypatch)
    monkeypatch.setattr(q, "public_get", pg)
    c = _flat(monkeypatch, tmp_path, MockClient(mode="live", resting=[], positions=_held()))
    assert c.created == [] and c.cancelled == []


# ---- early exits keep the full cancel cover -------------------------------------------------

def test_position_read_failure_still_cancels_everything(monkeypatch, tmp_path):
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[_the_offset()], positions=_held(),
                         get_positions_raises=True))
    assert c.cancelled == ["keep-me"], "a blind position read must fall back to cancel-all"
    assert c.created == []


def test_flat_book_still_cancels_everything(monkeypatch, tmp_path):
    _arm(monkeypatch)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[_the_offset()], positions=[]))
    assert c.cancelled == ["keep-me"], "no inventory -> nothing to defer -> cancel all"
    assert c.created == []


# ---- escalation is untouched -----------------------------------------------------------------

def test_escalation_wait_ends_early_only_when_the_book_is_clear(monkeypatch, tmp_path):
    """The escalation wait is a MONEY window — how long passive offsets get before we pay the
    spread — so it must run its full length whenever inventory remains. It was one straight-line
    sleep holding the run lock; it now polls and stops early ONLY when nothing is left to
    escalate (operator-named 2026-08-03).

    Flat book: nothing to escalate, so the wait ends on the first poll rather than stalling the
    daemon for the whole window."""
    _arm(monkeypatch)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 30)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 1.0)
    slept = []
    monkeypatch.setattr(q.time, "sleep", lambda s: slept.append(s))
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[], positions=_held(pos="0")))
    assert sum(slept) < 30, f"a clear book must not burn the whole window: {slept}"


def test_escalation_wait_runs_full_length_while_inventory_remains(monkeypatch, tmp_path):
    # Inventory never clears, so the wait must run to STOP_ESCALATE_S — the passive window is
    # not shortened, only ended early when it buys nothing.
    _arm(monkeypatch)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 20)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 1.0)
    slept = []
    monkeypatch.setattr(q.time, "sleep", lambda s: slept.append(s))
    _flat(monkeypatch, tmp_path,
          MockClient(mode="live", resting=[], positions=_held(pos="20")))
    assert sum(slept) >= 20, f"the full passive window must be honoured: {slept}"


def test_unreadable_positions_keeps_waiting(monkeypatch, tmp_path):
    # Fail conservative: a blind cycle must never cut the passive window short.
    _arm(monkeypatch)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 20)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 1.0)
    slept = []
    monkeypatch.setattr(q.time, "sleep", lambda s: slept.append(s))

    class Flaky(MockClient):
        def __init__(self, **kw):
            super().__init__(**kw)
            self._n = 0

        def get_positions(self):
            self._n += 1
            if self._n > 1:                    # first read (the cycle's own) succeeds
                raise RuntimeError("positions 503")
            return {"market_positions": list(self._positions)}

    _flat(monkeypatch, tmp_path, Flaky(mode="live", resting=[], positions=_held(pos="20")))
    assert sum(slept) >= 20, f"an unreadable book must keep waiting, not escalate early: {slept}"


def test_kept_offset_does_not_suppress_taker_escalation(monkeypatch, tmp_path):
    # The keep must not become an early return: pass 2 still re-reads and crosses the residual.
    # NOTE on the cancel here — pass 2's _taker_cross_capped is CANCEL(confirmed) -> CROSS ->
    # RE-REST by design ("an exit order must not outlive its position", fix of 2026-07-27 after
    # a resting exit filled 7s after the position went flat). So a cancel appearing at this
    # point is that guard working, NOT churn: pass 1 is the scope of diff-and-keep, and it is
    # pinned by asserting nothing was re-created.
    _arm(monkeypatch)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 1.0)
    c = _flat(monkeypatch, tmp_path,
              MockClient(mode="live", resting=[_the_offset()], positions=_held()))
    assert c.created == [], "pass 1 must still keep, not re-create"
    assert c.total_crossed() > 0, "escalation must still fire on the residual"

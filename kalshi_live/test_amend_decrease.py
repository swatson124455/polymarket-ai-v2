"""Pins for AMEND-ON-DECREASE (KALSHI_AMEND_DECREASE).

THE DEFECT: diff_orders survives a resting order only on an exact (side, price, count) match, so
trimming it by ONE contract cancels it and rebuilds it at the BACK of the queue. Measured over the
clean slice of our own order history (478 zero-fill cancels from 2026-07-23T20:05Z): 100 were
same-price-different-size, 44 of them DECREASES — queue position discarded for nothing.

Kalshi preserves queue position for exactly one amendment, verbatim from Amend Order (V2):
  "Amending a resting order preserves queue position only when the amendment decreases size. All
   other amendments — like increasing size or changing price forfeit queue position and place the
   order at the back of the queue."
So ONLY same-price decreases are routed to amend. Increases and reprices forfeit queue either way
and must keep the existing cancel+create path — routing them here would be pure added risk.

  T1 DECREASE IS AMENDED, NOT CHURNED   — and disappears from both cancels and creates.
  T2 INCREASE IS NOT AMENDED            — no queue benefit exists; legacy path must handle it.
  T3 REPRICE IS NOT AMENDED             — same.
  T4 EXACT MATCH IS UNTOUCHED           — no amend, no cancel, no create.
  T5 FLAG OFF IS A NO-OP                — byte-identical cancels/creates, no plan keys.
  T6 AMBIGUOUS BOOK IS SKIPPED          — duplicate side+price must not be guessed at.
  T7 SIDE MAPPING                       — a 'no' amend must hit ask @ 1-p, exactly like create.
  T8 FAILURE IS BENIGN                  — an amend error never aborts the cycle.
"""
from test_live_hardening import q, MockClient, _run, _cfg


def _std(oid, side, price, count):
    return {"order_id": oid, "side": side, "price_dollars": price, "count": count}


def _want(side, price, count, reason=None):
    d = {"side": side, "price_dollars": price, "count": count}
    if reason:
        d["reason"] = reason
    return d


def test_same_price_decrease_is_amended_not_cancelled():
    standing = {"T": [_std("o1", "yes", 0.50, 100)]}
    desired = {"T": [_want("yes", 0.50, 60)]}
    amends, s_left, d_left = q.split_amends(standing, desired)
    assert len(amends) == 1
    a = amends[0]
    assert a["order_id"] == "o1" and a["count"] == 60 and a["from_count"] == 100
    assert s_left == {} and d_left == {}, "removed from BOTH sides so diff_orders never sees it"
    cancels, creates = q.split_amends(standing, desired)[1:] and q.diff_orders(s_left, d_left)
    assert cancels == [] and creates == []


def test_increase_is_not_amended():
    """An increase forfeits queue position anyway — amending buys nothing and adds an unproven
    call, so it must fall through to the existing path."""
    standing = {"T": [_std("o1", "yes", 0.50, 60)]}
    desired = {"T": [_want("yes", 0.50, 100)]}
    amends, s_left, d_left = q.split_amends(standing, desired)
    assert amends == []
    cancels, creates = q.diff_orders(s_left, d_left)
    assert cancels == ["o1"] and len(creates) == 1 and creates[0]["count"] == 100


def test_reprice_is_not_amended():
    standing = {"T": [_std("o1", "yes", 0.50, 100)]}
    desired = {"T": [_want("yes", 0.49, 100)]}
    amends, s_left, d_left = q.split_amends(standing, desired)
    assert amends == []
    cancels, creates = q.diff_orders(s_left, d_left)
    assert cancels == ["o1"] and len(creates) == 1


def test_exact_match_is_left_completely_alone():
    standing = {"T": [_std("o1", "yes", 0.50, 100)]}
    desired = {"T": [_want("yes", 0.50, 100)]}
    amends, s_left, d_left = q.split_amends(standing, desired)
    assert amends == []
    assert q.diff_orders(s_left, d_left) == ([], [])


def test_decrease_to_zero_is_a_cancel_not_an_amend():
    """count 0 is not a decrease, it is a withdrawal — must go through cancel."""
    standing = {"T": [_std("o1", "yes", 0.50, 100)]}
    desired = {"T": []}
    amends, s_left, d_left = q.split_amends(standing, desired)
    assert amends == []
    assert q.diff_orders(s_left, d_left)[0] == ["o1"]


def test_explicit_zero_count_quote_is_not_amended():
    """A desired count of 0 is a WITHDRAWAL, not a decrease. Amending to zero is not a documented
    queue-preserving operation and would leave an order in an undefined state — it must cancel.
    (The empty-book case above exercises a different path: no desired quote at all.)"""
    standing = {"T": [_std("o1", "yes", 0.50, 100)]}
    desired = {"T": [_want("yes", 0.50, 0)]}
    amends, s_left, d_left = q.split_amends(standing, desired)
    assert amends == [], "count 0 must never be routed to amend"
    assert q.diff_orders(s_left, d_left)[0] == ["o1"]


def test_shipped_default_is_off():
    """Pins the MODULE default, unpatched. The amend endpoint is unverified against the live venue,
    so installing this file must change nothing until the flag is deliberately switched on."""
    assert q.AMEND_DECREASE == 0


def test_ambiguous_duplicate_price_is_skipped_not_guessed():
    """Two desired quotes at the same side+price cannot be matched to one resting order without
    guessing which is which. Guessing would amend the wrong order — skip the pair instead."""
    standing = {"T": [_std("o1", "yes", 0.50, 100)]}
    desired = {"T": [_want("yes", 0.50, 60), _want("yes", 0.50, 40)]}
    amends, s_left, d_left = q.split_amends(standing, desired)
    assert amends == [], "ambiguity must fall through to the proven path"
    assert s_left["T"] and d_left["T"]


def test_both_sides_handled_independently():
    standing = {"T": [_std("o1", "yes", 0.50, 100), _std("o2", "no", 0.49, 80)]}
    desired = {"T": [_want("yes", 0.50, 60), _want("no", 0.49, 100)]}   # yes down, no UP
    amends, s_left, d_left = q.split_amends(standing, desired)
    assert len(amends) == 1 and amends[0]["order_id"] == "o1"
    cancels, creates = q.diff_orders(s_left, d_left)
    assert cancels == ["o2"] and len(creates) == 1 and creates[0]["side"] == "no"


# ---------------------------------------------------------------------------------------------
# client-side: the outcome->book_side mapping must match create_quote exactly
# ---------------------------------------------------------------------------------------------
class _Rec:
    def __init__(self):
        self.calls = []

    def _write(self, method, path, body):
        self.calls.append((method, path, body))
        return {"order": {"order_id": "x"}}


def test_amend_quote_side_mapping_matches_create_quote():
    """A 'no' quote rests as an ASK at 1-p. If amend used the yes-scale price it would move the
    order to the wrong level — worse than the churn it is meant to avoid."""
    from maker_kalshi_client import KalshiOrderClient
    c = KalshiOrderClient.__new__(KalshiOrderClient)
    rec = _Rec()
    c._write = rec._write
    c.amend_quote("oid1", "T", "yes", 0.50, 60)
    c.amend_quote("oid2", "T", "no", 0.49, 40)
    assert len(rec.calls) == 2
    (m1, p1, b1), (m2, p2, b2) = rec.calls
    assert m1 == "POST" and p1.endswith("/portfolio/events/orders/oid1/amend")
    assert b1["side"] == "bid" and b1["price"] == "0.5000" and b1["count"] == "60"
    assert b2["side"] == "ask" and b2["price"] == "0.5100", "no @0.49 -> ask @ 1-0.49"
    assert b2["count"] == "40"


# ---------------------------------------------------------------------------------------------
# cycle-level
# ---------------------------------------------------------------------------------------------
def test_flag_off_emits_no_amend_keys(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "AMEND_DECREASE", 0)
    monkeypatch.setattr(q, "select_footprint", lambda p, n: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00+00:00"}])
    row = _run(monkeypatch, MockClient(mode="live"), str(tmp_path))
    assert "amends" not in row and "amend_fail" not in row


def test_amend_failure_never_aborts_the_cycle(monkeypatch, tmp_path):
    """A failed amend leaves the order resting at its old, LARGER size — the size every capital
    check this cycle already assumed. So it must be counted and ignored, never raised."""
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "AMEND_DECREASE", 1)

    class _Boom(MockClient):
        def amend_quote(self, *a, **k):
            raise RuntimeError("amend 400")

    monkeypatch.setattr(q, "split_amends", lambda s, d: (
        [{"order_id": "o1", "ticker": "T1", "side": "yes", "price_dollars": 0.5,
          "count": 5, "from_count": 20, "reason": None}], s, d))
    monkeypatch.setattr(q, "select_footprint", lambda p, n: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00+00:00"}])
    c = _Boom(mode="live")
    row = _run(monkeypatch, c, str(tmp_path))
    assert row["amend_fail"] == 1 and row["amends"] == 1
    assert row["creates"] == 2, "the rest of the cycle proceeds untouched"
    assert "amend" in (row["first_create_err"] or "")

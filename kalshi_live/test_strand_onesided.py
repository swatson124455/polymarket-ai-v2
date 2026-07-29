"""STRAND UNWIND on a ONE-SIDED book (2026-07-26).

A reducing quote rests on exactly ONE side: a long-YES exit is a NO bid (needs the NO
book only), a long-NO exit is a YES bid (needs the YES book only). The old guard
required BOTH sides non-None and otherwise `continue`d with the comment "taker handles
it" -- but TAKER_FLATTEN=0 and PRECLOSE_FLATTEN defaulted OFF, so nothing did, and the
position rode to settlement with NO exit order resting anywhere.

Measured live 2026-07-26T14:27:46Z: the strand predicate skipped 6 of 6 held positions,
and 3 of those 6 had the side they needed (KXAAAGASW-26JUL27-4.120 and -4.140 long YES
with sbn=0.99; -4.080 long NO with sby=0.99).

These drive the REAL run_once() strand block through the shared MockClient harness --
not a re-expression of it -- so reverting the source change fails them.
"""
# NOTE: take `q` FROM test_live_hardening, do not `import maker_kalshi_quoter` here.
# That module loads the quoter via importlib and REPLACES sys.modules["maker_kalshi_quoter"],
# so a separate import binds a DIFFERENT module object and every monkeypatch lands on a copy
# _run() never touches. Getting this wrong makes the stubs silently no-op, the real venue gets
# read, and the tests pass for the wrong reason.
from test_live_hardening import MockClient, _run, q


def _book(yes_levels, no_levels):
    return {"orderbook_fp": {"yes_dollars": yes_levels, "no_dollars": no_levels}}


def _setup(monkeypatch, yes_levels, no_levels):
    """Held ticker ABSENT from the footprint -> only the strand path can quote it."""
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 15.0)
    monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", 40.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", False)      # isolate the strand path
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_DOWN_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])   # nothing footprinted
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else _book(yes_levels, no_levels))


T = "KXAAAGASW-26JUL27-4.140"


def _pos(qty, exposure):
    return [{"ticker": T, "position_fp": str(qty),
             "market_exposure_dollars": f"{exposure:.2f}"}]


def _unwinds(c):
    return [x for x in c.created]


def test_long_yes_exits_on_empty_yes_book(monkeypatch, tmp_path):
    # The live 4.140 shape: long YES, YES book EMPTY, NO book has depth.
    # The exit is a NO bid and needs ONLY the NO book. The old guard skipped it.
    _setup(monkeypatch, yes_levels=[], no_levels=[["0.30", "500"]])
    c = MockClient(mode="live", resting=[], positions=_pos(62, 33.64))
    row = _run(monkeypatch, c, str(tmp_path))
    got = _unwinds(c)
    assert got, "long-YES exit must rest on an empty YES book when the NO book has depth"
    assert got[0]["ticker"] == T
    assert got[0]["side"] == "no", f"exit must be a NO bid, got {got[0]}"
    assert row.get("strand_no_exit_side") in (None, 0)


def test_long_no_exits_on_empty_no_book(monkeypatch, tmp_path):
    # The live 4.080 shape: long NO, NO book EMPTY, YES book has depth -> exit is a YES bid.
    _setup(monkeypatch, yes_levels=[["0.40", "500"]], no_levels=[])
    c = MockClient(mode="live", resting=[], positions=_pos(-20, 1.00))
    _run(monkeypatch, c, str(tmp_path))
    got = _unwinds(c)
    assert got, "long-NO exit must rest on an empty NO book when the YES book has depth"
    assert got[0]["side"] == "yes", f"exit must be a YES bid, got {got[0]}"


def test_missing_exit_side_is_counted_not_silent(monkeypatch, tmp_path):
    # Long YES with the NO book gone -> the side we must rest ON is genuinely absent.
    # Still no order, but it must be COUNTED: the old code was silent, so "cycle ok"
    # printed while the position had no exit anywhere.
    _setup(monkeypatch, yes_levels=[["0.40", "500"]], no_levels=[])
    c = MockClient(mode="live", resting=[], positions=_pos(62, 33.64))
    row = _run(monkeypatch, c, str(tmp_path))
    assert not _unwinds(c)
    assert row.get("strand_no_exit_side") == 1


def test_both_books_empty_is_counted(monkeypatch, tmp_path):
    # The live A3/A20 shape: both sides gone, nothing placeable at any price -- but visible.
    _setup(monkeypatch, yes_levels=[], no_levels=[])
    c = MockClient(mode="live", resting=[], positions=_pos(20, 1.00))
    row = _run(monkeypatch, c, str(tmp_path))
    assert not _unwinds(c)
    assert row.get("strand_no_exit_side") == 1


def test_crossed_book_still_refused_and_counted(monkeypatch, tmp_path):
    # PRESERVED behaviour: a crossed/stale book must NOT be chased. Now counted too.
    _setup(monkeypatch, yes_levels=[["0.60", "100"]], no_levels=[["0.60", "100"]])
    c = MockClient(mode="live", resting=[], positions=_pos(62, 33.64))
    row = _run(monkeypatch, c, str(tmp_path))
    assert not _unwinds(c)
    assert row.get("strand_crossed_book") == 1


def test_exit_rests_at_the_touch_on_a_one_sided_book(monkeypatch, tmp_path):
    # REWRITTEN 2026-07-28, Q1 operator decision: old pin (test_loss_cap_still_binds_on_a_one_
    # sided_book) asserted the removed MAX_UNWIND_LOSS cap holding this price at 0.5774. Long
    # YES at the live 4.140 basis (0.5426) against a 0.99 NO bid pays 1.5326 for $1.00 — and
    # the exit must rest AT 0.99 anyway ("we can sell at a loss"): a capped price on a
    # one-sided trended book is exactly the unfillable exit that rode to settlement on 07-27.
    _setup(monkeypatch, yes_levels=[], no_levels=[["0.99", "5000"]])
    c = MockClient(mode="live", resting=[], positions=_pos(62, 0.5426 * 62))
    _run(monkeypatch, c, str(tmp_path))
    got = _unwinds(c)
    assert got, "an exit should still be placed"
    assert abs(got[0]["price"] - 0.99) < 1e-9, \
        f"the exit must rest AT the touch whatever the cost basis, got {got[0]['price']}"
    assert got[0]["count"] <= 62                        # still never through flat


def test_unwind_size_never_overshoots_flat(monkeypatch, tmp_path):
    # Invariant preserved on the newly-reachable path: never rest more than |inv|.
    _setup(monkeypatch, yes_levels=[], no_levels=[["0.30", "500"]])
    c = MockClient(mode="live", resting=[], positions=_pos(9, 6.93))
    _run(monkeypatch, c, str(tmp_path))
    got = _unwinds(c)
    assert got
    assert got[0]["count"] <= 9, f"must never rest more than |inv|, got {got[0]}"

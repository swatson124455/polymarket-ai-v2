"""Gov-RF2 (1.1 review 2026-07-31, diagnosed 2026-08-01): the taker re-rest helper had
four silent None paths — 117 journal failures since 07-30 could not be root-caused because
book-read / unpriceable / band / create-reject were indistinguishable. Each now counts.
Counters only; return values byte-identical on every path."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


class _Client:
    def __init__(self, create_raises=False):
        self._raise = create_raises
        self.created = []

    def create_quote(self, ticker, side, price, count, post_only=True, client_order_id=None):
        if self._raise:
            raise RuntimeError("post-only reject")
        self.created.append((ticker, side, price, count))
        return {"order": {"order_id": "oid-1"}}


def _silent(key):
    return q._SILENT[key]


def _book(y="0.30", n="0.60"):
    return {"orderbook_fp": {"yes_dollars": [[y, "500"]] if y else [],
                             "no_dollars": [[n, "500"]] if n else []}}


def test_book_read_failure_counts(monkeypatch):
    def boom(path):
        raise RuntimeError("500")
    monkeypatch.setattr(q, "public_get", boom)
    before = _silent("rerest_fail_book_read")
    assert q._rest_maker_offset(_Client(), "KXT-1", 10.0, 0.0, "t") is None
    assert _silent("rerest_fail_book_read") == before + 1


def test_unpriceable_side_counts(monkeypatch):
    # long yes needs the NO side; empty NO book -> unpriceable
    monkeypatch.setattr(q, "public_get", lambda p: _book(n=None))
    before = _silent("rerest_fail_unpriceable")
    assert q._rest_maker_offset(_Client(), "KXT-1", 10.0, 0.0, "t") is None
    assert _silent("rerest_fail_unpriceable") == before + 1


def test_create_reject_counts(monkeypatch):
    monkeypatch.setattr(q, "public_get", lambda p: _book())
    before = _silent("rerest_fail_create")
    assert q._rest_maker_offset(_Client(create_raises=True), "KXT-1", 10.0, 0.0, "t") is None
    assert _silent("rerest_fail_create") == before + 1


def test_success_counts_nothing_and_rests_reducing_side(monkeypatch):
    monkeypatch.setattr(q, "public_get", lambda p: _book())
    keys = ("rerest_fail_book_read", "rerest_fail_unpriceable",
            "rerest_fail_band_adjusted", "rerest_fail_create")
    before = {k: _silent(k) for k in keys}
    c = _Client()
    assert q._rest_maker_offset(c, "KXT-1", 10.0, 0.0, "t") == "oid-1"
    assert c.created and c.created[0][1] == "no"    # long yes -> NO side offset
    assert all(_silent(k) == before[k] for k in keys)

"""Tests for the estimand layer: first-entry selection, SELL-as-exit
pairing, token->outcome mapping, event-blocked splitting."""

from datetime import datetime, timedelta, timezone

from bots.mirror_scoring.estimand import (
    pair_exits, select_first_entries, split_events_by_resolution,
    token_outcome,
)

T0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def row(trader="0xabc", cond="c1", tok="yes1", side="BUY", price=0.40,
        ts=T0, resolution="YES", yes="yes1", no="no1", size=10.0):
    return {
        "user_address": trader, "condition_id": cond, "token_id": tok,
        "side": side, "price": price, "timestamp": ts, "size": size,
        "resolution": resolution, "yes_token_id": yes, "no_token_id": no,
    }


class TestTokenOutcome:
    def test_yes_token_wins_on_yes(self):
        assert token_outcome("yes1", "YES", "yes1", "no1") is True

    def test_no_token_wins_on_no(self):
        assert token_outcome("no1", "NO", "yes1", "no1") is True

    def test_yes_token_loses_on_no(self):
        assert token_outcome("yes1", "NO", "yes1", "no1") is False

    def test_unmappable_returns_none(self):
        assert token_outcome("zzz", "YES", "yes1", "no1") is None
        assert token_outcome("yes1", None, "yes1", "no1") is None


class TestFirstEntrySelection:
    def test_first_buy_per_market_only(self):
        rows = [
            row(price=0.40, ts=T0),
            row(price=0.55, ts=T0 + timedelta(hours=1)),  # re-entry ignored
            row(cond="c2", tok="yes2", yes="yes2", no="no2",
                price=0.30, ts=T0 + timedelta(hours=2)),
        ]
        entries = select_first_entries(rows)
        assert len(entries) == 2
        by_cond = {e.condition_id: e for e in entries}
        assert by_cond["c1"].price == 0.40  # FIRST entry, not the average

    def test_sell_never_creates_an_entry(self):
        rows = [row(side="SELL", price=0.70)]
        assert select_first_entries(rows) == []

    def test_edge_at_resolution(self):
        entries = select_first_entries([row(price=0.40, resolution="YES")])
        assert entries[0].edge_res == 0.60
        entries = select_first_entries([row(price=0.40, resolution="NO")])
        assert entries[0].edge_res == -0.40


class TestExitPairing:
    def test_sell_after_buy_pairs_fifo(self):
        rows = [
            row(price=0.40, ts=T0),
            row(side="SELL", price=0.65, ts=T0 + timedelta(hours=5)),
        ]
        entries = pair_exits(select_first_entries(rows), rows)
        assert entries[0].exit_price == 0.65
        assert entries[0].hold_seconds == 5 * 3600

    def test_sell_before_buy_is_ignored(self):
        rows = [
            row(side="SELL", price=0.65, ts=T0 - timedelta(hours=1)),
            row(price=0.40, ts=T0),
        ]
        entries = pair_exits(select_first_entries(rows), rows)
        assert entries[0].exit_time is None

    def test_sell_on_other_token_does_not_pair(self):
        rows = [
            row(price=0.40, ts=T0),
            row(side="SELL", tok="no1", price=0.65,
                ts=T0 + timedelta(hours=5)),
        ]
        entries = pair_exits(select_first_entries(rows), rows)
        assert entries[0].exit_time is None


class TestEventBlockedSplit:
    def test_whole_event_goes_one_side_and_unresolved_dropped(self):
        cutoff = T0 + timedelta(days=10)
        entries = select_first_entries([
            row(cond="c1", ts=T0),
            row(cond="c2", tok="yes2", yes="yes2", ts=T0),
            row(cond="c3", tok="yes3", yes="yes3", ts=T0),
        ])
        resolved_at = {
            "c1": cutoff - timedelta(days=1),
            "c2": cutoff + timedelta(days=1),
            # c3 missing => dropped
        }
        train, test = split_events_by_resolution(entries, cutoff, resolved_at)
        assert [e.condition_id for e in train] == ["c1"]
        assert [e.condition_id for e in test] == ["c2"]

"""Unit tests for the Polymarket match-winner index (GAP B PM price capture).

Fixtures are real live Gamma shapes captured 2026-07-09 (JSON-encoded string
arrays for outcomes/clobTokenIds/outcomePrices).
"""
import json

from esports_v2.data.pm_market_index import (
    PMMarketRef,
    build_pm_index,
    match_pm_ref,
    parse_gamma_market,
)


def _mw_market(**over):
    """A real, clean match-winner market (ZennIT vs The Bandits, BO3)."""
    m = {
        "question": "LoL: ZennIT vs The Bandits (BO3) - Road Of Legends Tier 1 Group",
        "conditionId": "0x872ccf45091283c1bb4f0b614f29d181c7af1044f3052b662fc1f12fa37efd04",
        "outcomes": json.dumps(["ZennIT", "The Bandits"]),
        "clobTokenIds": json.dumps(["36408350682438387457574261390236022714495760375683209804952463107329218427825",
                                    "26863304232839016056260116973220480793120213174636761498620284673683799359313"]),
        "outcomePrices": json.dumps(["0.62", "0.38"]),
        "gameStartTime": "2026-01-27 19:00:00+00",
    }
    m.update(over)
    return m


# ── parse_gamma_market ───────────────────────────────────────────────────────


def test_parse_match_winner_ok():
    ref = parse_gamma_market(_mw_market())
    assert isinstance(ref, PMMarketRef)
    assert ref.condition_id.startswith("0x872ccf45")
    assert ref.yes_token_id.startswith("36408350")   # index-0 token
    assert ref.yes_outcome == "ZennIT"                # index-0 outcome
    assert ref.market_price == 0.62                   # index-0 price
    assert (ref.team_a, ref.team_b) == ("ZennIT", "The Bandits")
    assert ref.day == "2026-01-27"                    # from gameStartTime


def test_reject_game_winner_prop():
    assert parse_gamma_market(_mw_market(question="LoL: ZennIT vs The Bandits - Game 1 Winner")) is None


def test_reject_kill_handicap_prop():
    m = _mw_market(
        question="Series: Anyone's Legend Kill Handicap (-10.5) vs Weibo Gaming (+10.5)",
        outcomes=json.dumps(["Anyone's Legend", "Weibo Gaming"]),
    )
    assert parse_gamma_market(m) is None


def test_reject_map_odd_even_prop():
    m = _mw_market(question="Map 1: Odd/Even Total Kills?", outcomes=json.dumps(["Odd", "Even"]))
    assert parse_gamma_market(m) is None


def test_reject_yes_no_shape1():
    m = _mw_market(question="Will T1 win the LCK 2026 season playoffs?",
                   outcomes=json.dumps(["Yes", "No"]))
    assert parse_gamma_market(m) is None


def test_reject_no_vs_in_title():
    assert parse_gamma_market(_mw_market(question="ZennIT crushes The Bandits tonight")) is None


def test_reject_missing_condition_id():
    assert parse_gamma_market(_mw_market(conditionId="")) is None


def test_reject_missing_game_start():
    assert parse_gamma_market(_mw_market(gameStartTime="")) is None


def test_reject_wrong_token_count():
    assert parse_gamma_market(_mw_market(clobTokenIds=json.dumps(["only_one"]))) is None


def test_degenerate_price_becomes_none_but_market_kept():
    for bad in (json.dumps(["1", "0"]), json.dumps(["0", "1"]),
                json.dumps(["oops", "0.5"]), "not-json"):
        ref = parse_gamma_market(_mw_market(outcomePrices=bad))
        assert ref is not None and ref.market_price is None


# ── build_pm_index ───────────────────────────────────────────────────────────


def test_build_index_from_pages_filters_props_and_dedupes_cid():
    page0 = [_mw_market(),
             _mw_market(question="LoL: ZennIT vs The Bandits - Game 2 Winner"),  # prop
             _mw_market()]  # duplicate cid (paging overlap)
    fetches = {0: page0, 100: []}
    refs = build_pm_index(fetch_page=lambda off, lim: fetches.get(off), max_pages=5)
    assert len(refs) == 1
    assert refs[0].yes_outcome == "ZennIT"


def test_build_index_fetch_failure_returns_empty():
    assert build_pm_index(fetch_page=lambda off, lim: None, max_pages=3) == []


# ── match_pm_ref (the coverage expansion) ────────────────────────────────────


def _refs(*markets):
    return [parse_gamma_market(m) for m in markets]


def test_match_exact_names_same_day():
    refs = _refs(_mw_market())
    r = match_pm_ref("ZennIT", "The Bandits", "2026-01-27T19:00:00Z", refs)
    assert r is not None and r.condition_id.startswith("0x872ccf45")


def test_match_is_order_invariant():
    """PinnOdds home/away order need not match PM outcome order."""
    refs = _refs(_mw_market())
    assert match_pm_ref("The Bandits", "ZennIT", "2026-01-27T20:00:00Z", refs) is not None


def test_match_token_subset_beyond_exact():
    """Coverage win: 'Team Vitality' (PinnOdds) <-> 'Vitality' (PM), 'G2 Esports' <-> 'G2'."""
    m = _mw_market(outcomes=json.dumps(["Vitality", "G2"]),
                   question="CS: Vitality vs G2 (BO3) - Major")
    r = match_pm_ref("Team Vitality", "G2 Esports", "2026-01-27T19:00:00Z", _refs(m))
    assert r is not None


def test_match_requires_both_teams_bijective():
    """A one-team match is rejected (no wrong attachment on a partial match)."""
    refs = _refs(_mw_market())  # ZennIT vs The Bandits
    assert match_pm_ref("ZennIT", "Some Other Team", "2026-01-27T19:00:00Z", refs) is None


def test_match_within_day_window():
    refs = _refs(_mw_market())  # PM day 2026-01-27
    assert match_pm_ref("ZennIT", "The Bandits", "2026-01-28T02:00:00Z", refs) is not None  # +1 day
    assert match_pm_ref("ZennIT", "The Bandits", "2026-01-30T02:00:00Z", refs) is None      # +3 days


def test_match_ambiguous_two_markets_dropped():
    """Same teams+day across two DISTINCT PM markets -> drop, never guess."""
    refs = _refs(_mw_market(conditionId="0xaaa"), _mw_market(conditionId="0xbbb"))
    assert match_pm_ref("ZennIT", "The Bandits", "2026-01-27T19:00:00Z", refs) is None


def test_match_no_candidates_returns_none():
    refs = _refs(_mw_market())
    assert match_pm_ref("FaZe", "NAVI", "2026-01-27T19:00:00Z", refs) is None


def test_match_alias_expand_injection():
    """Injected alias map links otherwise-unmatchable names (e.g. NAVI<->Natus Vincere)."""
    m = _mw_market(outcomes=json.dumps(["Natus Vincere", "FaZe Clan"]),
                   question="CS: Natus Vincere vs FaZe Clan (BO3)")
    aliases = {"NAVI": ["Natus Vincere"], "FaZe": ["FaZe Clan"]}
    r = match_pm_ref("NAVI", "FaZe", "2026-01-27T19:00:00Z", _refs(m),
                     alias_expand=lambda t: aliases.get(t, []))
    assert r is not None


# ── _default_fetch_page URL shape (gamma offset-cap regression) ──────────────


def test_default_fetch_pages_newest_first(monkeypatch):
    """Gamma 422-caps offset paging (~2100) while the esports tag holds ~3600
    active markets. The fetch MUST page newest-first (order=id&ascending=false)
    or the newest ~1500 markets — i.e. the upcoming match-winner slate — are
    unreachable (93/130 live match winners missed, measured 2026-07-10)."""
    import esports_v2.data.pm_market_index as mod

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return []

    def fake_get(url, **kw):
        captured["url"] = url
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    assert mod._default_fetch_page(0, 100) == []
    assert "order=id" in captured["url"]
    assert "ascending=false" in captured["url"]


def test_match_pm_ref_does_not_attach_academy_market():
    """Sibling-roster veto end-to-end: a PinnOdds row for the MAIN match must
    not attach the org's academy fixture on the same day (different teams)."""
    m = _mw_market(outcomes=json.dumps(["T1 Academy", "Gen.G Academy"]),
                   question="LoL: T1 Academy vs Gen.G Academy (BO3) - LCK CL")
    assert match_pm_ref("T1", "Gen.G", "2026-01-27T19:00:00Z", _refs(m)) is None


# ── GAP C: touch quotes (bid/ask + depth) from the live CLOB book ────────────


def _book(bids=None, asks=None):
    return {"bids": bids if bids is not None else [],
            "asks": asks if asks is not None else []}


def test_parse_book_takes_best_of_unsorted_levels():
    from esports_v2.data.pm_market_index import parse_book
    q = parse_book(_book(
        bids=[{"price": "0.60", "size": "50"}, {"price": "0.64", "size": "10"}],
        asks=[{"price": "0.70", "size": "5"}, {"price": "0.66", "size": "80"}]))
    assert (q.best_bid, q.bid_size) == (0.64, 10.0)
    assert (q.best_ask, q.ask_size) == (0.66, 80.0)


def test_parse_book_one_sided_and_junk_levels():
    from esports_v2.data.pm_market_index import parse_book
    q = parse_book(_book(
        bids=[{"price": "junk", "size": "5"}, {"price": "1.5", "size": "5"},
              {"price": "0.10", "size": "0"}],
        asks=[{"price": "0.92", "size": "7"}]))
    assert q.best_bid is None and q.bid_size is None   # all bid levels invalid
    assert (q.best_ask, q.ask_size) == (0.92, 7.0)


def test_parse_book_empty_or_malformed_is_none():
    from esports_v2.data.pm_market_index import parse_book
    assert parse_book(_book()) is None
    assert parse_book(None) is None
    assert parse_book([1, 2]) is None


def test_fetch_touch_quotes_dedups_isolates_and_drops_failures():
    from esports_v2.data.pm_market_index import fetch_touch_quotes
    calls = []
    def fake(tid):
        calls.append(tid)
        if tid == "boom":
            raise RuntimeError("x")
        if tid == "empty":
            return _book()
        return _book(bids=[{"price": "0.40", "size": "3"}],
                     asks=[{"price": "0.42", "size": "4"}])
    out = fetch_touch_quotes(["a", "a", "boom", "empty", "b", ""],
                             fetch_book=fake, workers=2)
    assert sorted(calls) == ["a", "b", "boom", "empty"]   # dedup + skip blank
    assert set(out) == {"a", "b"}                          # failures absent
    assert out["a"].best_bid == 0.40 and out["b"].ask_size == 4.0


# ── 2026-07-14 coverage fix: /events pages with nested markets ──────────────


def test_build_pm_index_flattens_event_pages():
    from esports_v2.data.pm_market_index import build_pm_index
    ev = {"title": "Road Of Legends", "markets": [
        _mw_market(),
        _mw_market(conditionId="0xdead", closed=True),           # resolved -> skip
        _mw_market(conditionId="0xbeef", active=False),          # inactive -> skip
        "junk",
    ]}
    refs = build_pm_index(fetch_page=lambda off, lim: [ev] if off == 0 else [])
    assert len(refs) == 1
    assert refs[0].condition_id.startswith("0x872ccf45")


def test_build_pm_index_bare_market_pages_still_work():
    # back-compat: a page of bare market dicts (the old /markets shape and all
    # existing injected tests) parses exactly as before.
    from esports_v2.data.pm_market_index import build_pm_index
    refs = build_pm_index(fetch_page=lambda off, lim: [_mw_market()] if off == 0 else [])
    assert len(refs) == 1


def test_flatten_page_item_shapes():
    from esports_v2.data.pm_market_index import _flatten_page_item
    m = _mw_market()
    assert _flatten_page_item(m) == [m]                      # bare market
    assert _flatten_page_item({"markets": [m]}) == [m]       # event wrapper
    assert _flatten_page_item({"markets": "junk"}) == [{"markets": "junk"}]
    assert _flatten_page_item(None) == []
    assert _flatten_page_item({"markets": [dict(m, closed=True)]}) == []


# ── silent-drop guard: full final page at max_pages cap ─────────────────────


def test_build_pm_index_warns_on_truncation_at_cap(caplog):
    import logging
    from esports_v2.data.pm_market_index import build_pm_index
    # every page full -> paging never hits a short page -> stops at the cap
    full_page = [_mw_market(conditionId=f"0x{i:064x}") for i in range(100)]
    def fetch(off, lim):
        return [_mw_market(conditionId=f"0x{off+j:064x}") for j in range(100)]
    with caplog.at_level(logging.WARNING):
        build_pm_index(fetch_page=fetch, max_pages=3)
    assert any("TRUNCATED" in rec.message for rec in caplog.records)


def test_build_pm_index_no_truncation_warning_on_short_final_page(caplog):
    import logging
    from esports_v2.data.pm_market_index import build_pm_index
    def fetch(off, lim):
        if off == 0:
            return [_mw_market(conditionId=f"0x{j:064x}") for j in range(100)]
        return [_mw_market(conditionId="0xaa")]   # short page -> clean end
    with caplog.at_level(logging.WARNING):
        build_pm_index(fetch_page=fetch, max_pages=5)
    assert not any("TRUNCATED" in rec.message for rec in caplog.records)

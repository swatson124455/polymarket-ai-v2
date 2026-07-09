"""Unit tests for the sharp-line evaluation metrics (Step 4)."""
from esports_v2.model.results_join import JoinedRecord
from esports_v2.model.sharp_eval import (
    edge_backtest,
    edge_backtest_from_joined,
    evaluate_sharp_line,
    no_vig_prob_a,
)


def _jr(match_key, odds_a, odds_b, home_won, open_a=None, open_b=None):
    return JoinedRecord(
        match_key=match_key, home="A", away="B", starts="2026-07-10T18:00:00Z",
        day="2026-07-10", odds_a=odds_a, odds_b=odds_b,
        open_odds_a=open_a if open_a is not None else odds_a,
        open_odds_b=open_b if open_b is not None else odds_b,
        winner="A" if home_won else "B", home_won=home_won,
        game="cs2", source="test", result_match_id="m",
    )


# ── no_vig_prob_a ────────────────────────────────────────────────────────────


def test_no_vig_prob_a_simple_symmetric():
    # equal odds -> 0.5 each
    assert abs(no_vig_prob_a(2.0, 2.0, "simple") - 0.5) < 1e-9


def test_no_vig_prob_a_simple_favorite():
    # 1.5 vs 2.6: home is favorite -> prob_a > 0.5
    p = no_vig_prob_a(1.5, 2.6, "simple")
    assert p > 0.5


def test_no_vig_prob_a_degenerate_none():
    assert no_vig_prob_a(1.0, 2.0, "simple") is None
    assert no_vig_prob_a(1.0, 2.0, "shin") is None


def test_no_vig_prob_a_shin_runs():
    p = no_vig_prob_a(1.5, 2.6, "shin")
    assert 0.0 < p < 1.0


# ── evaluate_sharp_line ──────────────────────────────────────────────────────


def test_evaluate_empty():
    rep = evaluate_sharp_line([])
    assert rep.n == 0
    assert "no labeled matches" in rep.summary()


def test_perfect_favorites_win():
    # strong favorite (home) always wins -> hit-rate 1.0, low Brier
    recs = [_jr(f"k{i}", 1.2, 5.0, home_won=True) for i in range(10)]
    rep = evaluate_sharp_line(recs)
    assert rep.n == 10
    assert rep.favorite_hit_rate == 1.0
    assert rep.n_favorite_decidable == 10
    assert rep.brier_closing < 0.1


def test_favorites_always_lose_high_brier():
    # home is favorite but always loses -> hit-rate 0.0, high Brier
    recs = [_jr(f"k{i}", 1.2, 5.0, home_won=False) for i in range(10)]
    rep = evaluate_sharp_line(recs)
    assert rep.favorite_hit_rate == 0.0
    assert rep.brier_closing > 0.5


def test_favorite_can_be_away_team():
    # away is the favorite (odds_b < odds_a); away wins -> favorite correct
    recs = [_jr(f"k{i}", 5.0, 1.2, home_won=False) for i in range(5)]
    rep = evaluate_sharp_line(recs)
    assert rep.favorite_hit_rate == 1.0


def test_closing_line_sharper_than_opening():
    # opening was a coin flip (2.0/2.0), closing correctly favored the winner
    recs = [_jr(f"k{i}", 1.2, 5.0, home_won=True, open_a=2.0, open_b=2.0)
            for i in range(10)]
    rep = evaluate_sharp_line(recs)
    assert rep.brier_improvement > 0  # closing Brier lower than opening


def test_coin_flip_not_favorite_decidable():
    recs = [_jr("k", 2.0, 2.0, home_won=True)]
    rep = evaluate_sharp_line(recs)
    assert rep.n_favorite_decidable == 0
    assert rep.favorite_hit_rate is None


def test_reliability_table_present():
    recs = [_jr(f"k{i}", 1.2, 5.0, home_won=True) for i in range(6)]
    rep = evaluate_sharp_line(recs)
    assert rep.reliability
    assert all("mean_pred" in b and "actual" in b for b in rep.reliability)


def test_degenerate_odds_skipped():
    recs = [_jr("bad", 1.0, 5.0, home_won=True), _jr("ok", 1.2, 5.0, home_won=True)]
    rep = evaluate_sharp_line(recs)
    assert rep.n == 1


# ── edge_backtest ────────────────────────────────────────────────────────────


def test_edge_backtest_no_market_price_reports_gap():
    recs = [{"match_key": "k", "yes_is_team_a": True, "home_won": True}]
    rep = edge_backtest(recs, {"k": (1.5, 2.6)})
    assert rep.n_with_market_price == 0
    assert "forward-collector captures" in rep.note.lower() or "market_price" in rep.note


def test_edge_backtest_with_prices_scores_pnl():
    # sharp says home ~0.65 (odds 1.5/2.6); PM prices home YES cheap at 0.40
    # -> big YES edge -> bet YES. If YES wins, positive P&L.
    recs = [{
        "match_key": "k",
        "market_price": 0.40,       # Polymarket YES price
        "yes_is_team_a": True,       # YES resolves on the odds_a/home team
        "home_won": True,            # home (=YES) won
    }]
    odds = {"k": (1.5, 2.6)}
    rep = edge_backtest(recs, odds, fee=0.02, min_edge=0.05)
    assert rep.n_with_market_price == 1
    assert rep.n_bets == 1
    assert rep.hit_rate == 1.0
    assert rep.total_pnl is not None and rep.total_pnl > 0


def test_edge_backtest_no_bets_when_no_edge():
    # PM price already equals sharp prob -> no edge clears min_edge
    recs = [{"match_key": "k", "market_price": 0.62, "yes_is_team_a": True, "home_won": True}]
    rep = edge_backtest(recs, {"k": (1.5, 2.6)}, min_edge=0.05)
    assert rep.n_bets == 0


# ── edge_backtest_from_joined (GAP B) ────────────────────────────────────────


def _jr_pm(match_key, odds_a, odds_b, home_won, market_price, cid="0xc", tok="t0"):
    return JoinedRecord(
        match_key=match_key, home="A", away="B", starts="2026-07-10T18:00:00Z",
        day="2026-07-10", odds_a=odds_a, odds_b=odds_b,
        open_odds_a=odds_a, open_odds_b=odds_b,
        winner="A" if home_won else "B", home_won=home_won,
        game="cs2", source="test", result_match_id="m",
        condition_id=cid, yes_token_id=tok, yes_outcome="A", market_price=market_price,
    )


def test_edge_from_joined_no_pm_price_reports_gap():
    rep = edge_backtest_from_joined([_jr("k", 2.0, 2.0, True)],
                                    resolve_orientation=lambda *a: True)
    assert rep.n_with_market_price == 0
    assert "PM price" in rep.note


def test_edge_from_joined_skips_unresolved_orientation():
    # market price present but resolver returns None -> record dropped, gap note.
    rep = edge_backtest_from_joined([_jr_pm("k", 1.5, 3.0, True, 0.40)],
                                    resolve_orientation=lambda *a: None)
    assert rep.n_with_market_price == 0


def test_edge_from_joined_prices_a_winning_yes_bet():
    # Sharp fair prob of A (odds 1.5 vs 3.0) ~0.667; YES=team A (yes_is_team_a=True);
    # PM YES price 0.40 -> edge ~0.667-0.40-0.02 > min_edge -> bet YES; A won -> win.
    rep = edge_backtest_from_joined([_jr_pm("k", 1.5, 3.0, True, 0.40)],
                                    resolve_orientation=lambda *a: True)
    assert rep.n_with_market_price == 1
    assert rep.n_bets == 1
    assert rep.hit_rate == 1.0
    assert rep.total_pnl is not None and rep.total_pnl > 0


def test_edge_from_joined_orientation_passed_home_side():
    # Resolver must receive (condition_id, yes_token_id, home, away).
    seen = {}
    def resolver(cid, tok, ta, tb):
        seen["args"] = (cid, tok, ta, tb)
        return True
    edge_backtest_from_joined([_jr_pm("k", 1.5, 3.0, True, 0.40, cid="0xZ", tok="TID")],
                              resolve_orientation=resolver)
    assert seen["args"] == ("0xZ", "TID", "A", "B")

"""Tests for the sharp-vs-PM gap diagnostic (edge_distribution)."""
from esports_v2.model.closing_line import ClosingLine
from esports_v2.scripts.edge_distribution import edge_rows, report, yes_side_sharp_prob


def _cl(home="T1", away="GAM Esports", yes="T1", price=0.60,
        odds_a=1.5, odds_b=2.6, key="k"):
    return ClosingLine(
        match_key=key, home=home, away=away, starts="2026-07-15T12:00:00Z",
        league_name="L", odds_a=odds_a, odds_b=odds_b,
        captured_at="2026-07-15T11:00:00Z", n_snapshots=1, n_before_start=1,
        open_odds_a=odds_a, open_odds_b=odds_b,
        condition_id="0xc", yes_token_id="t", yes_outcome=yes,
        market_price=price,
    )


def test_yes_on_home_side():
    p = yes_side_sharp_prob(_cl(yes="T1"))
    assert p is not None and abs(p - 0.634) < 0.01     # no-vig home prob


def test_yes_on_away_side_is_complement():
    p = yes_side_sharp_prob(_cl(yes="GAM Esports"))
    assert p is not None and abs(p - (1 - 0.634)) < 0.01


def test_ambiguous_or_missing_yields_none():
    assert yes_side_sharp_prob(_cl(yes="Unrelated Org")) is None
    assert yes_side_sharp_prob(_cl(price=None)) is None


def test_rows_and_report_shape():
    closing = {"a": _cl(key="a", price=0.40),           # big gap (~0.23)
               "b": _cl(key="b", price=0.4999),          # placeholder
               "c": _cl(key="c", price=0.63)}            # tiny gap
    rows = edge_rows(closing)
    assert len(rows) == 3
    r = report(rows)
    assert "placeholder-price matches" in r and "Top 10" in r
    assert "fee=0.02" in r and "fee=0.00" in r


def test_report_empty():
    assert "nothing to measure" in report([])


def test_fav_premium_sign_and_price_bin():
    # PM prices the home favorite 0.70 vs sharp fair ~0.634 -> premium ~ +0.066
    rows = edge_rows({"a": _cl(price=0.70)})
    r = rows[0]
    assert abs(r["pm_fav"] - 0.70) < 1e-9
    assert abs(r["fav_premium"] - (0.70 - r["sharp_yes"])) < 1e-9
    assert r["fav_premium"] > 0
    # YES on the dog side at 0.30: favorite = NO at 0.70, sharp fav ~0.634 ->
    # SAME premium (sign is favorite-relative, not YES-relative).
    rows2 = edge_rows({"a": _cl(price=0.30, yes="GAM Esports")})
    assert abs(rows2[0]["fav_premium"] - r["fav_premium"]) < 0.01
    rep = report(rows)
    assert "Favorite premium by PM favorite price" in rep
    assert "[0.70,0.80)" in rep


def test_de_vig_method_changes_sharp_prob():
    import pytest
    pytest.importorskip("shin")
    # extreme line: shin shifts fair prob toward the favorite vs simple
    simple = yes_side_sharp_prob(_cl(odds_a=1.103, odds_b=6.31))
    shin_p = yes_side_sharp_prob(_cl(odds_a=1.103, odds_b=6.31), method="shin")
    assert simple is not None and shin_p is not None
    assert shin_p > simple + 0.01

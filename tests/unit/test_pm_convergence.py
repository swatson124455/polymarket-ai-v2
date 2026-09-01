"""Tests for the PM-vs-sharp convergence diagnostic (pm_convergence)."""
from esports_v2.scripts.pm_convergence import convergence_rows, report


def _snap(key="k", home="T1", away="GAM Esports", yes="T1",
          cap="2026-07-15T06:00:00Z", starts="2026-07-15T12:00:00Z",
          odds_a=1.5, odds_b=2.6, price=0.55, cid="0xc"):
    return {
        "match_key": key, "home": home, "away": away, "starts": starts,
        "league_name": "L", "odds_a": odds_a, "odds_b": odds_b,
        "captured_at": cap, "condition_id": cid, "yes_token_id": "t",
        "yes_outcome": yes, "market_price": price,
    }


def test_pm_converging_toward_sharp_attributed_to_pm():
    # Sharp fair(home) ~0.634 with (1.5, 2.6). PM 0.55 -> 0.62, odds fixed:
    # gap shrinks and ALL closure comes from the PM leg.
    snaps = [_snap(cap="2026-07-15T06:00:00Z", price=0.55),
             _snap(cap="2026-07-15T11:00:00Z", price=0.62)]
    rows = convergence_rows(snaps)["rows"]
    assert len(rows) == 1
    r = rows[0]
    assert r["converged"] is True
    assert r["g0"] > r["g1"] > 0
    assert abs(r["pm_moved"] - 0.07) < 1e-9
    assert abs(r["sharp_moved"]) < 1e-9
    assert abs(r["closure"] - (r["pm_moved"] + r["sharp_moved"])) < 1e-12


def test_sharp_moving_to_pm_attributed_to_sharp():
    # PM fixed at 0.55; the LINE drifts down toward PM (1.5 -> 1.7 home odds).
    snaps = [_snap(cap="2026-07-15T06:00:00Z", odds_a=1.5, odds_b=2.6),
             _snap(cap="2026-07-15T11:00:00Z", odds_a=1.7, odds_b=2.2)]
    rows = convergence_rows(snaps)["rows"]
    assert len(rows) == 1
    r = rows[0]
    assert r["converged"] is True
    assert abs(r["pm_moved"]) < 1e-9
    assert r["sharp_moved"] > 0


def test_diverging_gap_counts_negative():
    # PM moves AWAY from sharp (0.55 -> 0.50... use 0.52 to dodge placeholder).
    snaps = [_snap(cap="2026-07-15T06:00:00Z", price=0.55),
             _snap(cap="2026-07-15T11:00:00Z", price=0.52)]
    r = convergence_rows(snaps)["rows"][0]
    assert r["converged"] is False
    assert r["pm_moved"] < 0 and r["closure"] < 0


def test_yes_on_away_side_flips_gap_sign():
    # YES = away team; sharp_yes ~0.366, PM 0.30 -> gap positive on the away side.
    snaps = [_snap(yes="GAM Esports", cap="2026-07-15T06:00:00Z", price=0.30),
             _snap(yes="GAM Esports", cap="2026-07-15T11:00:00Z", price=0.34)]
    r = convergence_rows(snaps)["rows"][0]
    assert r["g0"] > 0 and r["converged"] is True and r["pm_moved"] > 0


def test_placeholder_snaps_skipped_and_lt2_priced_dropped():
    snaps = [_snap(cap="2026-07-15T06:00:00Z", price=0.4999),   # placeholder
             _snap(cap="2026-07-15T11:00:00Z", price=0.62)]     # only 1 real
    res = convergence_rows(snaps)
    assert res["rows"] == []
    assert res["dropped"]["placeholder_snaps"] == 1
    assert res["dropped"]["lt2_priced"] == 1


def test_post_start_snapshots_ignored():
    snaps = [_snap(cap="2026-07-15T06:00:00Z", price=0.55),
             _snap(cap="2026-07-15T13:00:00Z", price=0.62)]     # after starts
    res = convergence_rows(snaps)
    assert res["rows"] == [] and res["dropped"]["lt2_priced"] == 1


def test_condition_id_flap_dropped_as_ambiguous():
    snaps = [_snap(cap="2026-07-15T06:00:00Z", cid="0xa"),
             _snap(cap="2026-07-15T11:00:00Z", cid="0xb", price=0.60)]
    res = convergence_rows(snaps)
    assert res["rows"] == [] and res["dropped"]["ambiguous_market"] == 1


def test_unresolvable_orientation_dropped():
    snaps = [_snap(yes="Unrelated Org", cap="2026-07-15T06:00:00Z"),
             _snap(yes="Unrelated Org", cap="2026-07-15T11:00:00Z", price=0.60)]
    res = convergence_rows(snaps)
    assert res["rows"] == [] and res["dropped"]["orientation"] == 1


def test_report_shape_and_empty():
    snaps = [_snap(cap="2026-07-15T06:00:00Z", price=0.55),
             _snap(cap="2026-07-15T11:00:00Z", price=0.62)]
    r = report(convergence_rows(snaps))
    assert "converged" in r and "price-CLV" in r and "Top 10" in r
    assert "nothing to measure" in report({"rows": [], "dropped": {}})

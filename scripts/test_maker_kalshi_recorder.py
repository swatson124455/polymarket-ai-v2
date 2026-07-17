"""Offline unit tests for the Kalshi recorder's scoring core (no network)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_recorder import (  # noqa: E402
    qualifying_walk, side_share, snapshot_scores, merge_orders, tick_size_for,
    JOIN_SIZE,
)


def test_walk_stops_at_target():
    levels = [(0.50, 600), (0.49, 600), (0.48, 600)]
    ref, q, tot = qualifying_walk(levels, 1000)
    assert ref == 0.50
    assert len(q) == 2            # 600 + 600 >= 1000, stop
    assert tot == 1200            # ALL size at each included price counts


def test_walk_insufficient_size_is_void():
    ref, q, tot = qualifying_walk([(0.50, 100)], 1000)
    assert ref is None and q == [] and tot == 0.0


def test_walk_ref_at_max_price_is_void():
    ref, q, tot = qualifying_walk([(1.00, 5000)], 1000)
    assert ref is None


def test_walk_empty():
    assert qualifying_walk([], 1000) == (None, [], 0.0)


def test_side_share_at_reference_full_weight():
    # book: 900 at 0.50; we add 100 at 0.50 -> qualifying = 1000 all at ref
    share, ref, tot, our_in = side_share([(0.50, 900)], [(0.50, 100)], 1000, 0.5, 0.01)
    assert ref == 0.50 and our_in
    assert abs(share - 100 / 1000) < 1e-9   # DF^0 = 1 for everyone


def test_side_share_distance_discount():
    # book: 1000 at 0.50 (ref, weight 1.0); us: 1000 at 0.48 (2 ticks, w=0.25)
    # walk: 0.50 alone reaches target -> our 0.48 order NOT in qualifying set
    share, ref, tot, our_in = side_share([(0.50, 1000)], [(0.48, 1000)], 1000, 0.5, 0.01)
    assert share == 0.0 and not our_in
    # if the touch is thin (500), walk continues: 0.50(500)+0.49(0)+0.48(ours)
    share2, ref2, _, our_in2 = side_share([(0.50, 500)], [(0.48, 1000)], 1000, 0.5, 0.01)
    assert ref2 == 0.50 and our_in2
    ours_w = 0.25 * 1000
    theirs_w = 1.0 * 500
    assert abs(share2 - ours_w / (ours_w + theirs_w)) < 1e-9


def test_help_article_example():
    # "500 contracts at best bid ... 20% of qualifying liquidity" -> share 0.20
    share, ref, _, _ = side_share([(0.50, 2000)], [(0.50, 500)], 1000, 0.5, 0.01)
    assert abs(share - 500 / 2500) < 1e-9


def test_snapshot_two_sided_void_and_activate():
    # thin market: yes 200 @ 0.10, no 300 @ 0.85 — both short of target 1000
    yes, no = [(0.10, 200)], [(0.85, 300)]
    res = snapshot_scores(yes, no, 1000, 0.5, 0.01)
    assert res["base_void"] is True
    # join 100ct still can't reach target on either side -> join invalid
    assert res["join"]["valid"] is False or res["join"]["score"] == 0.0
    # activate tops both sides to 1000 -> valid, near-dominant share
    assert res["act"]["valid"] is True
    assert res["act"]["add_yes"] == 800 and res["act"]["add_no"] == 700
    exp_cap = 0.10 * 800 + 0.85 * 700
    assert abs(res["act"]["capital_usd"] - exp_cap) < 0.01
    assert res["act"]["score"] > 1.0   # majority of both sides


def test_snapshot_join_on_thick_market():
    yes, no = [(0.50, 5000)], [(0.49, 5000)]
    res = snapshot_scores(yes, no, 1000, 0.5, 0.01)
    assert res["base_void"] is False
    assert res["join"]["valid"] is True
    exp = JOIN_SIZE / (5000 + JOIN_SIZE) * 2   # same share both sides
    assert abs(res["join"]["score"] - exp) < 1e-9


def test_snapshot_unpriceable_side():
    res = snapshot_scores([], [(0.50, 5000)], 1000, 0.5, 0.01)
    assert res["join"]["valid"] is False and res["act"]["valid"] is False


def test_merge_orders_aggregates():
    m = dict(merge_orders([(0.50, 100), ("0.50", 50)], [(0.50, 25)]))
    assert m[0.5] == 175


def test_tick_size_from_ranges():
    md = {"price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}]}
    assert tick_size_for(md, 0.5) == 0.01
    md2 = {"price_ranges": [{"start": "0.0000", "end": "0.1000", "step": "0.0010"},
                            {"start": "0.1000", "end": "1.0000", "step": "0.0100"}]}
    assert tick_size_for(md2, 0.05) == 0.001
    assert tick_size_for({}, 0.5) == 0.01


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)

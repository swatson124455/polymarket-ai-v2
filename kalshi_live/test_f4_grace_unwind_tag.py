"""A9-F4 (logic audit 2026-08-05, operator-authorized): drop-grace retention copied
standing orders WITHOUT their reason tag (quoter apply_drop_grace), so a held ticker
that rotated out of the footprint (strand pass failed) had its retained resting EXIT
treated as accumulating by cap_desired's unconditional-keep check — under a tight cap
the diff then CANCELLED a live reducing order on a held position. The in-loop
fetch-fail retention explicitly re-tags exactly this (quoter ~:4619-4632, review 07-22
skeptic); grace now mirrors it, including the loss-governor accumulating strip
(self-audit F6a parity).

Pins:
  P1 held long (naked>0): retained no-side orders tagged unwind; yes-side untagged
  P2 held short: mirrored
  P3 flat / below tolerance: copies stay verbatim (no reason key) — default behaviour
  P4 exit-only ticker: accumulating copies stripped; nothing retainable -> not retained
  P5 tagging is diff-neutral: no cancels/creates for the retained ticker
  P6 default args (held/exit_only omitted) = pre-fix behaviour, byte-identical
"""
import maker_kalshi_quoter as q


def _standing():
    return {"HELD-T": [{"side": "yes", "price_dollars": 0.40, "count": 10},
                       {"side": "no", "price_dollars": 0.55, "count": 5}]}


def test_p1_held_long_tags_reducing_no_side():
    d, g = q.apply_drop_grace(_standing(), {}, set(), {}, 2,
                              held={"HELD-T": 7.0}, inv_tolerance=1.0)
    by_side = {o["side"]: o for o in d["HELD-T"]}
    assert by_side["no"].get("reason") == "unwind"
    assert "reason" not in by_side["yes"]
    assert g == {"HELD-T": 1}


def test_p2_held_short_tags_reducing_yes_side():
    d, _ = q.apply_drop_grace(_standing(), {}, set(), {}, 2,
                              held={"HELD-T": -3.0}, inv_tolerance=1.0)
    by_side = {o["side"]: o for o in d["HELD-T"]}
    assert by_side["yes"].get("reason") == "unwind"
    assert "reason" not in by_side["no"]


def test_p3_flat_or_below_tolerance_stays_verbatim():
    for pos in (0.0, 0.5):
        d, _ = q.apply_drop_grace(_standing(), {}, set(), {}, 2,
                                  held={"HELD-T": pos}, inv_tolerance=1.0)
        assert all("reason" not in o for o in d["HELD-T"])


def test_p4_exit_only_ticker_strips_accumulating():
    d, g = q.apply_drop_grace(_standing(), {}, set(), {}, 2,
                              held={"HELD-T": 7.0}, inv_tolerance=1.0,
                              exit_only={"HELD-T"})
    assert [o["side"] for o in d["HELD-T"]] == ["no"], \
        "governed market keeps ONLY its unwind through grace (F6a parity)"
    # governed AND flat -> nothing retainable -> ticker not retained at all
    d2, g2 = q.apply_drop_grace(_standing(), {}, set(), {}, 2,
                                held={}, exit_only={"HELD-T"})
    assert "HELD-T" not in d2 and "HELD-T" not in g2


def test_p5_tagging_is_diff_neutral():
    standing = _standing()
    d, _ = q.apply_drop_grace(standing, {}, set(), {}, 2,
                              held={"HELD-T": 7.0}, inv_tolerance=1.0)
    cancels, creates = q.diff_orders(
        {t: list(os_) for t, os_ in standing.items()}, d)
    assert not cancels and not creates, \
        "the retained copy must still match standing exactly — no churn"


def test_p6_default_args_are_prefix_behaviour():
    a = q.apply_drop_grace(_standing(), {}, set(), {}, 2)
    b = q.apply_drop_grace(_standing(), {}, set(), {}, 2, held=None, exit_only=None)
    assert a == b
    assert all("reason" not in o for o in a[0]["HELD-T"])

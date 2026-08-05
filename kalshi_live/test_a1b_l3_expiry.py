"""A1b (logic audit 2026-08-05, operator-ruled: conviction expires at settlement).
The L3 series-probe insurance tainted a SERIES forever once any member entered
mkt_out — measured live 08-05: 6 of the 23 pilot payer series (TOPMODEL,
TRUMPENDORSEMENTS, TEMPAUSH, MLABELSHARE, CLAYTONDNI, TRUMPTIME) were permanently
probe-sized at 5ct off convictions on strikes that had ALREADY SETTLED (9 of 10
mkt_out members; quoter_state read 19:52Z), costing e.g. KXTOPMODEL-26AUG10-CLAUM
its full size on a $200/day pool. A settled strike cannot fill again, so it cannot
bleed again — its conviction no longer bounds anything.

Ruling: the taint is LIVE only while the convicted ticker's market has not closed.
Unknown close (cache miss + fetch failure) KEEPS the taint — the risk limiter fails
toward smaller size, never toward larger.

Pins:
  P1 conviction on a CLOSED market -> series not tainted
  P2 conviction on an OPEN market  -> series tainted (L3 unchanged where evidence lives)
  P3 unknown close                 -> tainted (fail-toward-small)
  P4 mixed series (one closed + one open conviction) -> tainted (any live member taints)
  P5 mkt_out itself is NOT pruned — per-ticker exit-only standing untouched
"""
import datetime as dt

import maker_kalshi_quoter as q

NOW = dt.datetime(2026, 8, 5, 20, 0, tzinfo=dt.timezone.utc)
PAST = (NOW - dt.timedelta(days=2)).isoformat()
FUTURE = (NOW + dt.timedelta(days=2)).isoformat()


def test_p1_closed_conviction_expires():
    out = q._l3_out_series(["KXTOPMODEL-26AUG03-CLAU5"], NOW,
                           close_of=lambda t: PAST)
    assert out == set()


def test_p2_open_conviction_still_taints():
    out = q._l3_out_series(["KXNETFLIXTOPVIEWSTV-26AUG10-18"], NOW,
                           close_of=lambda t: FUTURE)
    assert out == {"KXNETFLIXTOPVIEWSTV"}


def test_p3_unknown_close_keeps_the_taint():
    for bad in (lambda t: None, lambda t: "", lambda t: "garbage"):
        out = q._l3_out_series(["KXFOO-26AUG09-T1"], NOW, close_of=bad)
        assert out == {"KXFOO"}, "risk limiter must fail toward smaller size"


def test_p4_any_live_member_taints_the_series():
    closes = {"KXBAR-26AUG01-A": PAST, "KXBAR-26AUG09-B": FUTURE}
    out = q._l3_out_series(list(closes), NOW, close_of=closes.get)
    assert out == {"KXBAR"}


def test_p5_mkt_out_not_pruned():
    mkt_out = ["KXTOPMODEL-26AUG03-CLAU5"]
    q._l3_out_series(mkt_out, NOW, close_of=lambda t: PAST)
    assert mkt_out == ["KXTOPMODEL-26AUG03-CLAU5"], \
        "expiry gates the SERIES taint only; the per-ticker ban list is untouched"

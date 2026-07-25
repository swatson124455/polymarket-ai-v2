"""Pins for the PRESENCE / $1-FLOOR gate and its calibration plugin.

WHAT IT CORRECTS: LIP snapshots the book every second and SUMS the scores, so reward is an INTEGRAL
over the window. _prospective_capture returns an INSTANTANEOUS $/day — it assumes we rest the whole
window. Measured from the venue's order history (980 orders / 91 markets): median presence 5.7%.
And Kalshi pays $0 below a $1.00 credit, so a market that cannot clear a dollar for the time left
pays nothing while the fill risk is unchanged.

The pins:
  T1 FLAG-OFF NO-OP        — flag 0 => byte-identical quotes, no presence_* key anywhere.
  T2 LATE ENTRY            — same market, same book: early in the window it opens, late it SKIPS.
                             This is the structural half and needs no calibration to bite.
  T3 NEVER BLOCKS EXITS    — holding inventory in a sub-$1 market still rests the unwind at FULL
                             size. De-risk is never gated.
  T4 ABSENT TABLE IS NEUTRAL — no calibration file => presence factor 1.0, so a missing table can
                             never make the bot pessimistic. (Only a MEASURED number may.)
  T5 DEATH-SPIRAL COUNTER  — a market skipped only because measured presence dragged it under the
                             floor is counted separately from one the market itself cannot pay.
  T6 UNION NOT SUM         — two orders resting simultaneously is ONE second of presence.
  T7 TABLE FAILS OPEN      — corrupt / wrong-schema file yields {}, never an exception.
"""
import json

from test_live_hardening import q, MockClient, _run, _cfg
import kalshi_presence_calibrate as cal
import datetime as dt


_THIN = {"yes_dollars": [["0.50", "600"], ["0.49", "500"]],
         "no_dollars": [["0.49", "600"], ["0.48", "500"]]}


def _pg(book):
    def inner(path):
        if "incentive" in path:
            return {"incentive_programs": [], "next_cursor": ""}
        return {"orderbook_fp": book}
    return inner


def _mkt(end_iso, life_min, usd_day=975.0, target=1000):
    return {"ticker": "T1", "usd_day": usd_day, "target": target,
            "end": end_iso, "life_min": life_min, "df": 0.5}


def _cfg_gate(monkeypatch, *, on, floor=1.0, table=None, default=1.0):
    monkeypatch.setattr(q, "PRESENCE_GATE", 1 if on else 0)
    monkeypatch.setattr(q, "MIN_CREDIT_USD", floor)
    monkeypatch.setattr(q, "PRESENCE_TABLE", table or {})
    monkeypatch.setattr(q, "PRESENCE_DEFAULT", default)
    monkeypatch.setattr(q, "CAPTURE_GATE", 0)
    monkeypatch.setattr(q, "NETEV_GATE", 0)
    monkeypatch.setattr(q, "STANDDOWN", 0)
    monkeypatch.setattr(q, "JOIN_SIZE", 100)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 80.0)


def _levels(book):
    yl, _ = q._levels(book["yes_dollars"])
    nl, _ = q._levels(book["no_dollars"])
    return yl, nl


# ---------------------------------------------------------------------------------------------
def test_flag_off_is_a_no_op(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "PRESENCE_GATE", 0)
    monkeypatch.setattr(q, "public_get", _pg(_THIN))
    monkeypatch.setattr(q, "select_footprint", lambda p, n: [
        {"ticker": "T1", "usd_day": 975.0, "target": 1000, "end": "2099-01-01T00:00:00+00:00"}])
    c = MockClient(mode="live")
    row = _run(monkeypatch, c, str(tmp_path))
    assert len(c.created) == 2                       # opened normally
    assert not any(k.startswith("presence_") for k in row)


# A 30-day window on a modest pool. Deliberately chosen so the LATE case still has 10 HOURS left —
# far outside WIND_DOWN_MIN — and is therefore skipped by THIS gate's economics and nothing else.
# (An earlier draft used "28 minutes left", which both cleared $1 anyway and sat near the wind-down
# path, so the test passed without the gate ever firing.)
_LIFE_30D = 30 * 24 * 60.0
_POOL = 20.0


def _late(now, hours_left=10.0):
    return _mkt((now + dt.timedelta(hours=hours_left)).isoformat(), _LIFE_30D, usd_day=_POOL)


def _early(now, days_left=25.0):
    return _mkt((now + dt.timedelta(days=days_left)).isoformat(), _LIFE_30D, usd_day=_POOL)


def test_late_entry_skips_where_early_entry_opens(monkeypatch):
    """The STRUCTURAL half — no calibration involved. Same market, same book, same pool: with most
    of the window ahead it opens; with 10h of a 30-day window left the remaining credit cannot
    reach $1, so it must not open."""
    _cfg_gate(monkeypatch, on=True, floor=1.0)
    now = q.utcnow()
    yl, nl = _levels(_THIN)
    early, late = _early(now), _late(now)
    assert q._window_frac_left(early, now) > 0.8
    assert q._window_frac_left(late, now) < 0.02
    # prove the ECONOMICS are what differ, before asserting on quotes
    exp_e, _, _ = q._expected_credit_usd(early, yl, nl, 0.50, 0.49, 1000, now)
    exp_l, _, _ = q._expected_credit_usd(late, yl, nl, 0.50, 0.49, 1000, now)
    assert exp_e > 1.0 > exp_l

    s_e, s_l = {}, {}
    qs_early = q.desired_quotes(early, _THIN["yes_dollars"], _THIN["no_dollars"], now,
                                inv=0.0, stats=s_e)
    qs_late = q.desired_quotes(late, _THIN["yes_dollars"], _THIN["no_dollars"], now,
                               inv=0.0, stats=s_l)
    assert len(qs_early) == 2 and "presence_skipped" not in s_e, "window left -> open, gate silent"
    assert qs_late == [] and s_l["presence_skipped"] == 1, "gate must be WHY it skipped"


def test_never_blocks_a_de_risking_exit(monkeypatch):
    """The gate must FIRE here (proved via stats) and STILL return the unwind at full size —
    otherwise this test would pass on the ordinary inventory-unwind path without exercising the
    gate's exit branch at all."""
    _cfg_gate(monkeypatch, on=True, floor=1.0)
    now = q.utcnow()
    late = _late(now)
    stats = {}
    held = q.desired_quotes(late, _THIN["yes_dollars"], _THIN["no_dollars"], now,
                            inv=40.0, stats=stats)
    assert stats.get("presence_skipped") == 1, "the gate must actually fire on this market"
    assert len(held) == 1 and held[0]["reason"] == "unwind"
    assert held[0]["side"] == "no", "long yes -> reduce with a NO quote"
    assert held[0]["count"] >= 40, "de-risk is never down-sized by this gate"
    # and the mirror case: long NO must still get its reducing YES quote out
    stats2 = {}
    held2 = q.desired_quotes(late, _THIN["yes_dollars"], _THIN["no_dollars"], now,
                             inv=-40.0, stats=stats2)
    assert stats2.get("presence_skipped") == 1
    assert len(held2) == 1 and held2[0]["side"] == "yes" and held2[0]["reason"] == "unwind"


def test_window_fraction_is_clamped_to_zero_one():
    """Unclamped, this silently corrupts the credit estimate in both directions: a program whose
    end has passed gives a NEGATIVE fraction (negative expected credit), and one whose end is
    further out than its recorded life — clock skew, or a start_date that is really a row-insertion
    timestamp, which this venue does emit — gives >1 and INFLATES the estimate past the whole pool."""
    now = q.utcnow()
    life = 24 * 60.0
    past = _mkt((now - dt.timedelta(hours=5)).isoformat(), life)
    assert q._window_frac_left(past, now) == 0.0

    impossible = _mkt((now + dt.timedelta(days=10)).isoformat(), life)   # 10d left of a 24h window
    assert q._window_frac_left(impossible, now) == 1.0

    # and the credit estimate can never exceed the whole remaining pool because of it
    yl, nl = _levels(_THIN)
    exp, ideal, frac = q._expected_credit_usd(impossible, yl, nl, 0.50, 0.49, 1000, now)
    assert frac == 1.0 and ideal <= _mkt("", life)["usd_day"] * (life / 1440.0) + 1e-9

    # missing/garbage window data must not penalise: unknown -> 1.0, never 0
    assert q._window_frac_left({"end": None, "life_min": life}, now) == 1.0
    assert q._window_frac_left({"end": "not-a-date", "life_min": life}, now) == 1.0
    assert q._window_frac_left({"end": now.isoformat(), "life_min": 0}, now) == 1.0


def test_the_margin_band_between_venue_floor_and_gate_is_rejected(monkeypatch):
    """The whole point of the 20% margin: a market modelled at $1.05 clears the VENUE floor but is
    inside our error bars, so the gate must still refuse it. Pinning the band explicitly — a floor
    set at exactly $1.00 would let this through and it would be the first thing to pay zero."""
    _cfg_gate(monkeypatch, on=True, floor=q.MIN_CREDIT_USD)      # the SHIPPED floor, not 1.0
    now = q.utcnow()
    yl, nl = _levels(_THIN)
    # tune hours-left so the expected credit lands between $1.00 and $1.20
    band = None
    for hours in [h / 4.0 for h in range(40, 120)]:
        m = _late(now, hours_left=hours)
        exp, _, _ = q._expected_credit_usd(m, yl, nl, 0.50, 0.49, 1000, now)
        if q.VENUE_PAYOUT_FLOOR_USD < exp < q.MIN_CREDIT_USD:
            band = (m, exp)
            break
    assert band, "fixture must produce a credit inside the margin band"
    m, exp = band
    stats = {}
    assert q.desired_quotes(m, _THIN["yes_dollars"], _THIN["no_dollars"], now,
                            inv=0.0, stats=stats) == []
    assert stats["presence_skipped"] == 1
    assert q.VENUE_PAYOUT_FLOOR_USD < exp < q.MIN_CREDIT_USD


def test_absent_table_is_neutral_not_pessimistic(monkeypatch):
    """A missing calibration must NOT make the bot cautious — only a measured number may."""
    _cfg_gate(monkeypatch, on=True, floor=1.0, table={})
    assert q._presence_factor("KXAAAGASD-X", 24 * 60.0) == 1.0
    assert q._presence_factor("ANYTHING-X", None) == 1.0


def test_shipped_defaults_are_the_safe_ones():
    """Pins the MODULE defaults, unpatched. PRESENCE_DEFAULT must ship at 1.0: if it shipped at 0
    every uncalibrated market would score $0 and the gate would silently refuse to open anything.
    PRESENCE_GATE must ship OFF so installing this changes nothing until it is switched on."""
    assert q.PRESENCE_DEFAULT == 1.0
    assert q.PRESENCE_GATE == 0
    # The gate sits ABOVE the venue's $1.00 floor on purpose (operator decision 2026-07-25): the
    # estimate is a model and the floor is a cliff, so quoting right at the edge puts every
    # modelling error on the losing side of it. The 20% margin is worth pennies against the fill
    # risk of a market that was never going to pay.
    assert q.VENUE_PAYOUT_FLOOR_USD == 1.00
    assert q.MIN_CREDIT_USD == 1.20
    assert q.MIN_CREDIT_USD > q.VENUE_PAYOUT_FLOOR_USD, "margin must be above the cliff, never at it"


def test_measured_presence_lowers_the_estimate_and_is_counted_as_our_fault(monkeypatch):
    """T5: a market that WOULD clear $1 at perfect execution but is dragged under by measured
    presence must be tallied separately — that skip is our defect, not the market's economics."""
    now = q.utcnow()
    m = _early(now)
    yl, nl = _levels(_THIN)
    # perfect execution: comfortably clears the floor
    _cfg_gate(monkeypatch, on=True, floor=1.0, table={})
    exp_ideal, ideal, _ = q._expected_credit_usd(m, yl, nl, 0.50, 0.49, 1000, now)
    assert exp_ideal == ideal and ideal > 1.0
    # now calibrate presence to 1% -> same market, same book, drops under the floor
    # bucket must match the fixture's own life (30d window -> "14d+"); a mismatched key would fall
    # back to the 1.0 default and the test would silently prove nothing.
    assert q._life_bucket(_LIFE_30D / 60.0) == "14d+"
    tbl = {"gas|14d+": {"presence_median": 0.01}}
    _cfg_gate(monkeypatch, on=True, floor=1.0, table=tbl)
    m2 = dict(m, ticker="KXAAAGASD-26JUL26-4.105")
    exp, ideal2, _ = q._expected_credit_usd(m2, yl, nl, 0.50, 0.49, 1000, now)
    assert exp < 1.0 <= ideal2, "presence, not the market, is what pushes it under"
    stats = {}
    assert q.desired_quotes(m2, _THIN["yes_dollars"], _THIN["no_dollars"], now,
                            inv=0.0, stats=stats) == []
    assert stats["presence_skipped"] == 1
    assert stats["presence_skipped_execution_only"] == 1, "counted as OUR defect"


# ---------------------------------------------------------------------------------------------
# calibration plugin
# ---------------------------------------------------------------------------------------------
def test_union_not_sum_of_overlapping_orders():
    """Two orders resting at the same time is ONE second of presence. Summing them would report
    >100% presence and silently inflate every downstream estimate."""
    t0 = dt.datetime(2026, 7, 22, 0, 0, tzinfo=dt.timezone.utc)
    h = dt.timedelta(hours=1)
    # two fully-overlapping 1h orders
    assert cal.union_seconds([(t0, t0 + h), (t0, t0 + h)]) == 3600.0
    # partial overlap 0-2h and 1-3h => 3h, not 4h
    assert cal.union_seconds([(t0, t0 + 2 * h), (t0 + h, t0 + 3 * h)]) == 3 * 3600.0
    # disjoint stays additive
    assert cal.union_seconds([(t0, t0 + h), (t0 + 2 * h, t0 + 3 * h)]) == 2 * 3600.0


def test_measure_computes_presence_against_market_life():
    t0 = dt.datetime(2026, 7, 22, 0, 0, tzinfo=dt.timezone.utc)
    orders = [{"ticker": "KXAAAGASD-26JUL22-4.100",
               "created_time": t0.isoformat().replace("+00:00", "Z"),
               "last_update_time": (t0 + dt.timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
               "initial_count_fp": "20.00", "fill_count_fp": "0.00",
               "maker_fees_dollars": "0.25"}]
    res = cal.measure(orders, {"KXAAAGASD-26JUL22-4.100": 16 * 3600.0})
    row = res["markets"]["KXAAAGASD-26JUL22-4.100"]
    assert row["presence"] == 0.25 and row["bucket"] == "6-24h"
    fam = res["families"]["gas|6-24h"]
    assert fam["presence_median"] == 0.25
    assert fam["fees_per_resting_hour"] == 0.0625      # $0.25 over 4 resting hours


def test_table_fails_open_on_garbage(tmp_path):
    p = tmp_path / "t.json"
    p.write_text("{not json")
    assert cal.load_table(str(p)) == {}
    p.write_text(json.dumps({"schema": 999, "families": {"gas|6-24h": {"presence_median": 0.5}}}))
    assert cal.load_table(str(p)) == {}, "wrong schema must fail open, not be trusted"
    assert cal.load_table(str(tmp_path / "missing.json")) == {}

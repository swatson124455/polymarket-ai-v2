"""W4/D3 size ramp + W7 new-series clamp — pins.

The loss this exists for (2026-08-05, venue fills): 78% of the -$7.97 restart-window loss
was ONE 30-ct fill on a series this account had never traded. Pins:
  * flag ships OFF (KALSHI_D3_RAMP=0) — the clamp block is unreachable
  * rung walk 5-10-25-50 at >=10-min boundaries (operator-ruled 2026-08-02)
  * first sight registers rung 0; restart-persisted map is honored
  * W7: a series with NO settled evidence is held at D3_NEWSERIES_MAX_RUNG even at top age
    — the clamp that would have capped the NETFLIX fill at 10 ct
  * only series with actual CREDITS escape the W7 clamp; a convicted
    never-payer (due evidence, no credit) stays clamped (review F2)
  * missing/empty feedback table clamps DOWN (risk limiter fails toward smaller size)
  * unwind quotes are NEVER resized; accumulating quotes floor at 1
"""
import maker_kalshi_quoter as q

PAID = {"KXOLD": {"verdict": "paid", "credits_n": 3, "due_filled_events": 2},
        "KXCONVICT": {"verdict": "never_paid_due", "credits_n": 0, "due_filled_events": 4}}


def test_flag_ships_off():
    assert q.D3_RAMP == 0


def test_rung_walk_and_registration():
    fs = {}
    t0 = 1_000_000.0
    assert q._d3_ramp_ct("KXOLD-26AUG10-T1", t0, fs, PAID) == 5          # first sight
    assert fs["KXOLD-26AUG10-T1"] == t0                                   # registered
    assert q._d3_ramp_ct("KXOLD-26AUG10-T1", t0 + 599, fs, PAID) == 5
    assert q._d3_ramp_ct("KXOLD-26AUG10-T1", t0 + 600, fs, PAID) == 10
    assert q._d3_ramp_ct("KXOLD-26AUG10-T1", t0 + 1200, fs, PAID) == 25
    assert q._d3_ramp_ct("KXOLD-26AUG10-T1", t0 + 1800, fs, PAID) == 50
    assert q._d3_ramp_ct("KXOLD-26AUG10-T1", t0 + 99999, fs, PAID) == 50  # top rung sticks


def test_new_series_clamp_holds_at_max_rung():
    fs = {"KXNEW-26AUG10-18": 0.0}
    # 109 minutes old — the NETFLIX age at its fill. Unproven series: rung capped at 1 = 10ct.
    assert q._d3_ramp_ct("KXNEW-26AUG10-18", 109 * 60.0, fs, PAID) == 10
    # the same age on a PROVEN series walks to the top rung
    assert q._d3_ramp_ct("KXOLD-26AUG10-T1", 109 * 60.0, {"KXOLD-26AUG10-T1": 0.0},
                         PAID) == 50


def test_convicted_never_payer_stays_clamped():
    """Review F2: due evidence without a credit is a CONVICTION, not proof — size-trust
    requires a receipt. An OR-regression here would let the known losers ramp to 50."""
    fs = {"KXCONVICT-26AUG10-T1": 0.0}
    assert q._d3_ramp_ct("KXCONVICT-26AUG10-T1", 9999.0, fs, PAID) == 10


def test_apply_ramp_floors_at_one():
    quotes = [{"side": "yes", "count": 30, "reason": "join"}]
    q._d3_apply_ramp(quotes, 0)
    assert quotes[0]["count"] == 1


def test_missing_table_clamps_down_not_open():
    fs = {"KXANY-26AUG10-T1": 0.0}
    assert q._d3_ramp_ct("KXANY-26AUG10-T1", 9999.0, fs, {}) == 10
    assert q._d3_ramp_ct("KXANY-26AUG10-T1", 9999.0, fs, None) == 10


def test_apply_ramp_spares_unwind_and_floors_at_one():
    quotes = [{"side": "yes", "count": 30, "reason": "join"},
              {"side": "no", "count": 40, "reason": "unwind"},
              {"side": "no", "count": 4, "reason": "join"}]
    stats = {}
    q._d3_apply_ramp(quotes, 5, stats)
    assert quotes[0]["count"] == 5          # accumulating clamped
    assert quotes[1]["count"] == 40         # unwind untouched — de-risk never gated
    assert quotes[2]["count"] == 4          # already under the rung — untouched
    assert stats["d3_ramp_capped"] == 1


def test_restart_persisted_first_seen_is_honored():
    # a map restored from state keeps the earned rung — no amnesty, no reset-to-50 either
    fs = {"KXOLD-26AUG10-T1": 1_000_000.0}
    assert q._d3_ramp_ct("KXOLD-26AUG10-T1", 1_000_000.0 + 700, fs, PAID) == 10

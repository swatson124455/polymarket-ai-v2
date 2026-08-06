"""D-B/D-G reward-audit fixes (operator-ruled 2026-08-06) — failing-before tests.

Covers, one class each (audit doc KALSHI_REWARD_GAME_AUDIT_2026-08-06.md §4):
  F5  own-size double-count: live books INCLUDE our resting orders, but the share
      composition raw/(1+raw) assumes they don't -> an empty book we occupy alone
      measured ~0.5 share instead of ~1.0 (BLOCKER: rank flees the thesis books).
  F1  floor unit: _expected_credit_usd gated $1 against min(1 day, window) while the
      venue floor is per PROGRAM PERIOD (help article 13823851 updated 2026-08-05:
      "Time periods: Up to 31 days"; where-to-find article: "a final reward below $1
      for an individual program is not paid"; receipt: KXSENATEADJOURN-27 credited as
      ONE $6.44 lump 2026-08-02 for its multi-day program period).
  F3  floor gated at full join size while the D3 ramp then rests 5-10ct.
  F14 one-cycle absence from `desired` reset a ticker's ramp to rung 0.
  F17 (D-G) 45-min absolute wind-down forfeited ~78% of a sub-hour program window.
"""
import datetime as dt

import maker_kalshi_quoter as q


def _now():
    return dt.datetime(2026, 8, 6, 12, 0, 0, tzinfo=dt.timezone.utc)


def _mkt(usd_day=100.0, target=100.0, life_min=1440.0, end="2026-08-07T12:00:00Z"):
    return {"ticker": "KXTESTFIX-26AUG10-T1", "target": target, "df": 0.5,
            "usd_day": usd_day, "end": end, "life_min": life_min}


# ---------------- F5: own-size double-count ----------------

def test_f5_solo_book_prospective_capture_is_full_pool(monkeypatch):
    """A book whose ONLY depth is our own resting order: prospective capture with
    own_orders supplied must read ~the full pool (share ~1.0/side), not ~half."""
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 1e9)   # don't cap the join in this fixture
    m = _mkt(usd_day=100.0, target=50.0)
    # book = exactly our resting 50ct at 0.30 each side (public read includes us)
    yl = [(0.30, 50.0)]
    nl = [(0.30, 50.0)]
    own = {"yes": [(0.30, 50.0)], "no": [(0.30, 50.0)]}
    pc_naive = q._prospective_capture(m, yl, nl, 0.30, 0.30, 50.0)
    pc_fixed = q._prospective_capture(m, yl, nl, 0.30, 0.30, 50.0, own_orders=own)
    assert pc_fixed > 0.99 * 100.0, f"solo book must be ~full pool, got {pc_fixed}"
    # and the un-adjusted call keeps its old (understated) value — no silent change
    # (exact naive value depends on join sizing internals; the CONTRACT is understatement)
    assert pc_naive < 0.75 * pc_fixed, (pc_naive, pc_fixed)


def test_f5_rival_book_unchanged_when_not_resting(monkeypatch):
    """No resting orders -> own_orders empty -> byte-identical to the legacy model."""
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    m = _mkt(usd_day=100.0, target=50.0)
    yl = [(0.30, 200.0)]
    nl = [(0.30, 200.0)]
    a = q._prospective_capture(m, yl, nl, 0.30, 0.30, 50.0)
    b = q._prospective_capture(m, yl, nl, 0.30, 0.30, 50.0,
                               own_orders={"yes": [], "no": []})
    assert a == b


def test_f5_telemetry_share_solo_book(monkeypatch):
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    m = _mkt(usd_day=100.0, target=50.0)
    yl = [(0.30, 50.0)]
    nl = [(0.30, 50.0)]
    quotes = [{"side": "yes", "price_dollars": 0.30, "count": 50, "reason": "join"},
              {"side": "no", "price_dollars": 0.30, "count": 50, "reason": "join"}]
    own_ct = {"yes": 50.0, "no": 50.0}
    own_orders = {"yes": [(0.30, 50.0)], "no": [(0.30, 50.0)]}
    row = q._market_telemetry_row(1, _now(), m, yl, nl, quotes, own_ct, 0.0, {},
                                  own_orders=own_orders)
    assert row["y_share"] > 0.99, row
    assert row["n_share"] > 0.99, row
    assert abs(row["capture_usd_day"] - 100.0) < 1.0, row


# ---------------- F1 + F3: floor unit and clamped size ----------------

def test_f1_multiday_program_floor_uses_remaining_window(monkeypatch):
    """$0.50/day on a 5-day program = $2.50/period: must clear a $1.20 floor."""
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "PRESENCE_TABLE", {})
    monkeypatch.setattr(q, "PRESENCE_DEFAULT", 1.0)
    monkeypatch.setattr(q, "D3_RAMP", 0)      # isolate F1 from F3 scaling
    m = _mkt(usd_day=1.0, target=50.0, life_min=5 * 1440.0,
             end=(_now() + dt.timedelta(days=5)).isoformat())
    yl = [(0.30, 200.0)]
    nl = [(0.30, 200.0)]
    exp, ideal, frac = q._expected_credit_usd(m, yl, nl, 0.30, 0.30, 50.0, _now())
    # naive per-day pc here is ~$0.5/day (we get ~half the book); over 5 days >= $2
    assert exp >= 1.20, f"multi-day floor must integrate the period, got {exp}"


def test_f3_floor_gated_at_ramp_clamped_size(monkeypatch):
    """Unproven series held at rung<=1 rests 5-10ct, not the full join — the floor
    must see the clamped size (scaled down expectation)."""
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "PRESENCE_TABLE", {})
    monkeypatch.setattr(q, "PRESENCE_DEFAULT", 1.0)
    monkeypatch.setattr(q, "D3_RAMP", 1)
    monkeypatch.setattr(q, "D3_RUNGS", [5.0, 10.0, 25.0, 50.0])
    monkeypatch.setattr(q, "D3_NEWSERIES_MAX_RUNG", 1)
    monkeypatch.setattr(q, "_D3_FIRST_SEEN", {})          # unknown ticker -> rung 0
    monkeypatch.setattr(q, "_d3_feedback_cached", lambda ts: {})
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 1e9)
    m = _mkt(usd_day=100.0, target=1000.0, life_min=1440.0,
             end=(_now() + dt.timedelta(days=1)).isoformat())
    # deep rival book: our share is ~size-linear
    yl = [(0.30, 2000.0)]
    nl = [(0.30, 2000.0)]
    monkeypatch.setattr(q, "D3_RAMP", 0)
    exp_full, _, _ = q._expected_credit_usd(m, yl, nl, 0.30, 0.30, 1000.0, _now())
    monkeypatch.setattr(q, "D3_RAMP", 1)
    exp_clamped, _, _ = q._expected_credit_usd(m, yl, nl, 0.30, 0.30, 1000.0, _now())
    assert exp_clamped < exp_full, "clamped size must lower the floor expectation"
    # rung-0 rest is 5ct vs a much larger join: expect at least a 5x reduction
    assert exp_clamped <= exp_full / 5.0, (exp_full, exp_clamped)


# ---------------- F14: ramp keep-grace ----------------

def test_f14_one_cycle_absence_keeps_first_seen(monkeypatch):
    now_ts = _now().timestamp()
    monkeypatch.setattr(q, "D3_RAMP", 1)
    monkeypatch.setattr(q, "_D3_FIRST_SEEN", {"A": now_ts - 3600.0, "B": now_ts - 3600.0})
    st = {}
    # A still desired; B absent this cycle but seen recently -> BOTH kept
    q._d3_prune_first_seen(st, {"A": []}, now_ts)
    assert "B" in q._D3_FIRST_SEEN and "A" in q._D3_FIRST_SEEN, q._D3_FIRST_SEEN
    # B absent longer than the grace -> pruned
    q._d3_prune_first_seen(st, {"A": []}, now_ts + q.D3_KEEP_S + 1.0)
    assert "B" not in q._D3_FIRST_SEEN and "A" in q._D3_FIRST_SEEN, q._D3_FIRST_SEEN


# ---------------- F17 / D-G: wind-down proportionality ----------------

def test_f17_subhour_program_wind_down_is_proportional():
    # 58-minute program: effective wind-down must be a fraction of the window,
    # not the 45-min absolute (which forfeited ~78% of it)
    eff = q._effective_wind_down_min(58.0)
    assert eff < 20.0, eff
    assert eff >= 2.0, eff


def test_f17_long_program_wind_down_unchanged():
    assert q._effective_wind_down_min(7 * 1440.0) == float(q.WIND_DOWN_MIN)

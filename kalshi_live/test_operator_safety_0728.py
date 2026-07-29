#!/usr/bin/env python3
"""Pins for the 2026-07-28 operator-approved safety set (the "bonus — yes to all" batch):

  A. MARK-TO-MARKET LOSS METER — the daily halt's equity marks held inventory at liquidation
     value from the cycle's own books (the old cost-basis meter read -$1.00 live 2026-07-27
     while the marked number was ~-$22 and the operator was told "flat" three times).
     Per-ticker fallback to cost on an unreadable book; one-time day-baseline migration on the
     basis change so the bid-ask spread cannot fire a phantom halt.
  B. FAR-CLOSE CAP ON THE MARKET CLOCK — min(program end, market close_time) must be inside
     MAX_DAYS_TO_CLOSE (live 2026-07-27: KXNHPRIMARY28-28, resolving 2028, was quoted and
     filled through an 8-day cap because its weekly reward program ends soon).
  C. FLATTEN SLIPPAGE BOUND — a taker burst may not chase a collapsing touch further than
     FLATTEN_MAX_SLIP from its first pass (live 2026-07-27: the STOP escalation walked KXDXYDUD
     0.52 -> 0.25 in 4 chained IOCs, selling 23 ct at 0.25 that settled at 1.00).

Run: python -m pytest test_operator_safety_0728.py -q  (from the probe dir)
"""
import os

from test_live_hardening import q, MockClient, _order, _run, _cfg

_BOOK = {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]], "no_dollars": [["0.49", "9999"]]}}


def _fp(monkeypatch, tickers=("T1",)):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": t, "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}
        for t in tickers])


def _held(pos="10", exposure="5.00", ticker="OTHER"):
    return [{"ticker": ticker, "position_fp": pos, "market_exposure_dollars": exposure}]


# ---- A. MARK-TO-MARKET METER ------------------------------------------------------------------

def test_equity_is_marked_at_liquidation_not_cost(monkeypatch, tmp_path):
    # long yes 10 ct, cost 0.50/ct; the book's best YES bid is 0.50 in the shared mock ->
    # marked = cash 100 + 10 x 0.50 = 105.0, and the plan row carries it.
    _cfg(monkeypatch)
    _fp(monkeypatch)
    c = MockClient(mode="live", positions=_held())
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("equity_mark_usd") == 105.0


def test_mark_falls_back_to_cost_on_unreadable_book(monkeypatch, tmp_path):
    # OTHER's orderbook raises -> that ticker is valued at COST (10 x 0.50 = 5.00) and counted;
    # the meter must degrade to the old cost-basis behaviour, never disarm.
    _cfg(monkeypatch)
    _fp(monkeypatch)

    def pg(p):
        if "incentive" in p:
            return {"incentive_programs": [], "next_cursor": ""}
        if "OTHER" in p:
            raise RuntimeError("book 500")
        return _BOOK
    monkeypatch.setattr(q, "public_get", pg)
    c = MockClient(mode="live", positions=_held())
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("equity_mark_usd") == 105.0            # cash 100 + cost 5.00
    assert row.get("mark_fallback_tickers") == 1


def test_basis_migration_never_fires_a_phantom_halt(monkeypatch, tmp_path):
    # A pre-existing state file holds COST-BASIS day baselines far above today's marked equity.
    # Without the one-time migration the definition change itself would read as a -$95 drawdown
    # and write STOP. The migration re-seeds the day ONCE (this is NOT a deposit re-baseline).
    import json
    _cfg(monkeypatch)
    _fp(monkeypatch)
    day = q.utcnow().strftime("%Y%m%d")
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({"equity_day": day, "equity_day_start": 200.0, "equity_day_peak": 200.0,
                   "equity_day_down": 0.0, "equity_prev": 200.0}, f)
    c = MockClient(mode="live", positions=_held())
    _run(monkeypatch, c, str(tmp_path))
    assert not os.path.exists(os.path.join(str(tmp_path), "STOP")), \
        "the basis change alone must never halt"
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert st.get("equity_basis") == "mark"
    assert st.get("equity_day_start") == 105.0            # re-seeded at the marked value


def test_marked_drop_trips_the_halt_costs_basis_could_not_see(monkeypatch, tmp_path):
    # THE 07-27 GAP, closed: an open position collapses in MARK while nothing settles. Run 1
    # seeds baselines at bid 0.50 (equity 125); run 2's bid is 0.10 (equity 105) -> a -$20
    # unrealized drawdown the cost meter would have called "flat". Halt budget 5 -> STOP.
    _cfg(monkeypatch)
    _fp(monkeypatch)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 5.0)
    monkeypatch.setattr(q, "DAILY_DOWN_HALT_USD", 5.0)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)          # no real sleep in the halt flatten
    c = MockClient(mode="live", positions=_held(pos="50", exposure="25.00"))
    _run(monkeypatch, c, str(tmp_path))
    assert not os.path.exists(os.path.join(str(tmp_path), "STOP"))

    crashed = {"orderbook_fp": {"yes_dollars": [["0.10", "9999"]],
                                "no_dollars": [["0.89", "9999"]]}}
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else crashed)
    c2 = MockClient(mode="live", positions=_held(pos="50", exposure="25.00"))
    _run(monkeypatch, c2, str(tmp_path))
    assert os.path.exists(os.path.join(str(tmp_path), "STOP")), \
        "an unrealized collapse must now trip the daily halt"


# ---- B. FAR-CLOSE CAP ON THE MARKET CLOCK -----------------------------------------------------

def _pg_with_close(close_time):
    def pg(p):
        if "incentive" in p:
            return {"incentive_programs": [], "next_cursor": ""}
        if p.endswith("/orderbook"):
            return _BOOK
        if "/markets/" in p:
            return {"market": {"close_time": close_time}}
        return _BOOK
    return pg


def test_market_resolving_years_out_is_dropped(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    _fp(monkeypatch)
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 8.0)
    monkeypatch.setattr(q, "_CLOSE_TIME_CACHE", {})
    monkeypatch.setattr(q, "public_get", _pg_with_close("2028-01-01T00:00:00Z"))
    c = MockClient(mode="live")
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("drop_far_market_close") == 1
    assert c.created == [], "a 2028-resolving market must never be quoted under an 8-day cap"


def test_near_close_market_is_kept(monkeypatch, tmp_path):
    from datetime import timedelta
    _cfg(monkeypatch)
    _fp(monkeypatch)
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 8.0)
    monkeypatch.setattr(q, "_CLOSE_TIME_CACHE", {})
    monkeypatch.setattr(q, "public_get",
                        _pg_with_close((q.utcnow() + timedelta(days=1)).isoformat()))
    c = MockClient(mode="live")
    row = _run(monkeypatch, c, str(tmp_path))
    assert "drop_far_market_close" not in row
    assert len(c.created) == 2


def test_unreadable_market_clock_keeps_the_market(monkeypatch, tmp_path):
    # A transient /markets read failure must not evacuate the footprint — kept and counted.
    _cfg(monkeypatch)
    _fp(monkeypatch)
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 8.0)
    monkeypatch.setattr(q, "_CLOSE_TIME_CACHE", {})

    def pg(p):
        if "incentive" in p:
            return {"incentive_programs": [], "next_cursor": ""}
        if p.endswith("/orderbook"):
            return _BOOK
        if "/markets/" in p:
            raise RuntimeError("markets 500")
        return _BOOK
    monkeypatch.setattr(q, "public_get", pg)
    c = MockClient(mode="live")
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("farclose_check_failed") == 1
    assert len(c.created) == 2


# ---- C. FLATTEN SLIPPAGE BOUND ----------------------------------------------------------------

class _PartialFillClient(MockClient):
    """Fills exactly 5 ct per IOC pass so the tries-loop must take multiple passes."""
    def create_order_v2(self, ticker, book_side, count, price_dollars, **k):
        self.crosses.append({"ticker": ticker, "side": book_side, "count": count,
                             "price": price_dollars})
        for p in self._positions:
            if p["ticker"] == ticker:
                cur = float(p["position_fp"])
                fill = min(5.0, abs(cur))
                p["position_fp"] = str(cur - fill if cur > 0 else cur + fill)
        return {"order": {"order_id": "x", "fill_count": "5"}}


def _collapsing_books(monkeypatch, prices):
    seq = list(prices)

    def pg(p):
        px = seq.pop(0) if len(seq) > 1 else seq[0]
        return {"orderbook_fp": {"yes_dollars": [[f"{px:.2f}", "100"]],
                                 "no_dollars": [["0.40", "100"]]}}
    monkeypatch.setattr(q, "public_get", pg)


def test_flatten_to_zero_refuses_a_collapsing_touch(monkeypatch):
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "FLATTEN_MAX_SLIP", 0.10)
    _collapsing_books(monkeypatch, [0.52, 0.50, 0.25, 0.40])
    c = _PartialFillClient(mode="live", positions=[{"ticker": "T1", "position_fp": "20.00"}])
    flat, n = q.flatten_to_zero(c, "T1")
    prices = [x["price"] for x in c.crosses]
    assert 0.25 not in prices, "the 27c-worse touch must be refused (the 07-27 DXY dump)"
    assert len(c.crosses) == 2 and not flat
    assert any(o["ticker"] == "T1" for o in c.created), \
        "the refused residual must get its maker exit re-rested"


def test_taker_cross_capped_refuses_a_collapsing_touch(monkeypatch):
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "FLATTEN_MAX_SLIP", 0.10)
    _collapsing_books(monkeypatch, [0.52, 0.50, 0.25, 0.40])
    c = _PartialFillClient(mode="live", positions=[{"ticker": "T1", "position_fp": "20.00"}])
    flat, n = q._taker_cross_capped(c, "T1", 20, True, tries=4)
    prices = [x["price"] for x in c.crosses]
    assert 0.25 not in prices
    assert len(c.crosses) == 2 and not flat and n == 10
    assert any(o["ticker"] == "T1" for o in c.created)


# ---- D. SERIES DENY-LIST (operator decision 2026-07-29: exclude the fast index family) --------

def _prog(tk):
    return {"market_ticker": tk, "incentive_type": "liquidity", "target_size_fp": 1000,
            "discount_factor_bps": 5000, "period_reward": 800000,
            "start_date": "2026-07-28T00:00:00Z", "end_date": "2026-07-30T00:00:00Z"}


def test_series_deny_excludes_the_family_by_prefix(monkeypatch):
    monkeypatch.setattr(q, "SERIES_DENY", ["KXDXY", "KXNDQ", "KXINX"])
    monkeypatch.setattr(q, "SERIES_ALLOW", [])
    progs = [_prog("KXDXYDUD-26JUL29-T101.50"),      # daily variant  -> denied
             _prog("KXNDQHUD-26JUL291600-T28000"),   # hourly variant -> denied
             _prog("KXAAAGASD-26JUL29-4.110")]       # unrelated      -> kept
    picked = q.select_footprint(progs, q.utcnow())
    tickers = {m["ticker"] for m in picked}
    assert "KXAAAGASD-26JUL29-4.110" in tickers
    assert not any(t.startswith(("KXDXY", "KXNDQ")) for t in tickers), \
        "denied family prefixes must never be selected"
    assert q.FP_DROPS.get("drop_series_deny") == 2


def test_series_deny_empty_is_a_noop(monkeypatch):
    monkeypatch.setattr(q, "SERIES_DENY", [])
    monkeypatch.setattr(q, "SERIES_ALLOW", [])
    picked = q.select_footprint([_prog("KXDXYDUD-26JUL29-T101.50")], q.utcnow())
    assert any(m["ticker"].startswith("KXDXY") for m in picked), \
        "empty deny-list must change nothing"


# ---- E. JOIN_SIZE=0 = DOLLAR-GOVERNED QUOTES (operator decision 2026-07-29) -------------------

def test_join_size_zero_lets_dollars_govern(monkeypatch):
    # $40 market cap -> $20/side; at price 0.50 that is 40 ct — the old JOIN_SIZE=20 clipped it.
    monkeypatch.setattr(q, "JOIN_SIZE", 0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 40.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    assert q._capped_join(0.50, 0.49) == 40, "dollars must govern when JOIN_SIZE=0"


def test_join_size_zero_still_respects_the_hard_envelope(monkeypatch):
    # fix-H invariant survives: at price 0.10, $20/side would be 200 ct, but one fill must not
    # blow through the INV_HARD_CT position ceiling — the envelope is the only contract bound.
    monkeypatch.setattr(q, "JOIN_SIZE", 0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 40.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    assert q._capped_join(0.10, 0.90) == 80, "hard inventory envelope must still cap one fill"


def test_positive_join_size_keeps_legacy_cap(monkeypatch):
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 40.0)
    assert q._capped_join(0.50, 0.49) == 20, "positive JOIN_SIZE must behave exactly as before"


# ---- F. AUDIT F2/F10 REGRESSION FIXES (2026-07-29, JOIN_SIZE=0 follow-ups) --------------------

def test_dollar_count_is_quantized_against_tick_churn(monkeypatch):
    # F10: a 1-tick reference move must NOT change the desired count (queue position is the
    # earning). 0.50 -> 40 ct; 0.51 -> raw 39 quantizes to 35 only after ~5 ticks of drift.
    monkeypatch.setattr(q, "JOIN_SIZE", 0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 40.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    assert q._capped_join(0.50, 0.49) == 40
    assert q._capped_join(0.51, 0.48) == 35          # one boundary, not one-per-tick
    assert q._capped_join(0.52, 0.47) == 35          # ...and stable across the next ticks
    assert q._capped_join(0.53, 0.46) == 35
    assert q._capped_join(0.10, 0.89) == 80          # hard clamp unaffected (multiple of 5)
    assert q._capped_join(0.90, 0.09) >= 1           # tiny counts never quantize to zero


def test_activate_never_emits_zero_count_orders(monkeypatch):
    # F2: one side already meets Target (add=0) -> that side must be SKIPPED, not sent as a
    # count-0 order the venue rejects every cycle.
    monkeypatch.setattr(q, "JOIN_SIZE", 0)
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 150.0)
    m = {"ticker": "T1", "target": 100, "end": "2099-01-01T00:00:00Z"}
    yl = [["0.50", "500"]]                           # yes side already deep (ext >= target)
    nl = [["0.49", "10"]]                            # no side short by 90
    qs = q.desired_quotes(m, yl, nl, q.utcnow(), inv=0.0)
    assert all(x["count"] >= 1 for x in qs), f"zero-count order emitted: {qs}"
    sides = {x["side"] for x in qs if x["reason"] == "activate"}
    assert sides == {"no"}, f"only the SHORT side should activate, got {qs}"


# ---- G. AUDIT SET (operator "do all", 2026-07-29) ---------------------------------------------

def test_activate_counts_clamped_at_hard_envelope(monkeypatch):
    # F1 clamp: a $40 activate at low prices must never rest more than INV_HARD_CT contracts.
    monkeypatch.setattr(q, "JOIN_SIZE", 0)
    monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 40.0)
    m = {"ticker": "T1", "target": 300, "end": "2099-01-01T00:00:00Z"}
    yl = [["0.10", "5"]]                              # thin void book, cheap side
    nl = [["0.85", "5"]]
    qs = q.desired_quotes(m, yl, nl, q.utcnow(), inv=0.0)
    for x in qs:
        assert x["count"] <= 80, f"activate exceeded the hard envelope: {x}"


def test_unwind_size_never_clipped_by_dollars(monkeypatch):
    # F12: the FULL position must get a resting exit — 80 ct at an 0.75 exit price used to be
    # clipped to 53 by the room bound, stranding 27 ct for the taker to pay for.
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 40.0)
    assert q._unwind_size(20, 0.75, 80.0) == 80
    assert q._unwind_size(20, 0.75, -80.0) == 80      # short polarity identical
    assert q._unwind_size(20, 0.75, 1.6) == 1         # truncate-not-round overshoot guard intact


def test_settle_backstop_crosses_naked_only_keeps_the_pair(monkeypatch, tmp_path):
    # F3: near settle, a 40-ct pair + 6-ct naked must cross AT MOST 6 — the pair self-hedges.
    import json as _json
    _cfg(monkeypatch, join=20, mktcap=40, totcap=280)
    monkeypatch.setattr(q, "TAKER_FLATTEN", True)
    monkeypatch.setattr(q, "SETTLE_UNWIND_MIN", 30)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "STRAND_CROSS_S", 0)       # isolate the settle path from the strand
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    calls = []
    real_tcc = q._taker_cross_capped

    def rec_tcc(client, t, cap_ct, long_yes, **k):
        calls.append((t, cap_ct, long_yes))
        return True, cap_ct
    monkeypatch.setattr(q, "_taker_cross_capped", rec_tcc)

    from datetime import timedelta

    def pg(p):
        if "incentive" in p:
            return {"incentive_programs": [], "next_cursor": ""}
        if p.endswith("/orderbook"):
            return {"orderbook_fp": {"yes_dollars": [["0.50", "999"]],
                                     "no_dollars": [["0.49", "999"]]}}
        if "/markets/" in p:
            return {"market": {"close_time": (q.utcnow() + timedelta(minutes=10)).isoformat()}}
        return {}
    monkeypatch.setattr(q, "public_get", pg)
    # ladder pair: +46 low strike, -40 high strike -> 40 paired, +6 naked on the low strike
    c = MockClient(mode="live", positions=[
        {"ticker": "KXAAAGASW-26JUL29-4.140", "position_fp": "46.0",
         "market_exposure_dollars": "23.00"},
        {"ticker": "KXAAAGASW-26JUL29-4.160", "position_fp": "-40.0",
         "market_exposure_dollars": "20.00"}])
    _run(monkeypatch, c, str(tmp_path))
    assert calls, "settle backstop did not fire"
    for t, cap_ct, _ in calls:
        assert cap_ct <= 6, f"settle path crossed more than the naked residual: {calls}"

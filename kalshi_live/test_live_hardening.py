"""Tests for the LIVE-path capital-safety hardening (NO_GO must-fixes).
Run: python -m pytest test_live_hardening.py -q  (from the probe dir)."""
import importlib.util, os, sys, tempfile, json
import pytest

def _load(n):
    s = importlib.util.spec_from_file_location(n, f"{n}.py")
    m = importlib.util.module_from_spec(s); sys.modules[n] = m; s.loader.exec_module(m); return m

q = _load("maker_kalshi_quoter")


class MockClient:
    def __init__(self, mode="live", resting=None, positions=None,
                 cancel_fail_ids=(), create_raises=False, get_orders_raises=False,
                 get_positions_raises=False):
        self.mode = mode
        self._resting = resting or []
        self._positions = positions or []
        self._cancel_fail = set(cancel_fail_ids)
        self._create_raises = create_raises
        self._get_orders_raises = get_orders_raises
        self._get_positions_raises = get_positions_raises
        self.created = []
        self.cancelled = []
        self.crosses = []
    def get_orders(self, status="resting"):
        if self._get_orders_raises:
            raise RuntimeError("read timeout")
        return {"orders": list(self._resting)}
    def get_positions(self):
        if self._get_positions_raises:
            raise RuntimeError("positions read 500")
        # frozen = simulate an eventually-consistent read that LAGS fills (returns the
        # snapshot at construction, ignoring subsequent create_order_v2 reductions).
        if getattr(self, "_frozen_positions", None) is not None:
            return {"market_positions": list(self._frozen_positions)}
        return {"market_positions": list(self._positions)}
    def get_balance(self):
        return {"balance_dollars": "100.0000"}
    def cancel_order(self, oid):
        if oid in self._cancel_fail:
            raise RuntimeError("cancel 429")
        self.cancelled.append(oid)
        self._resting = [o for o in self._resting if o.get("order_id") != oid]
        return {"ok": True}
    def create_quote(self, ticker, side, price, count, post_only=True, client_order_id=None):
        if self._create_raises:
            raise RuntimeError("create rejected")
        self.created.append({"ticker": ticker, "side": side, "price": price, "count": count})
        return {"order": {"order_id": client_order_id}}
    def create_order_v2(self, ticker, book_side, count, price_dollars,
                        time_in_force="good_till_canceled", self_trade_prevention_type="taker_at_cross",
                        post_only=True, client_order_id=None):
        # simulate an IOC taker flatten reducing the position toward zero:
        # 'ask' sells yes (reduces long-yes>0); 'bid' buys yes (reduces long-no<0).
        self.crosses.append({"ticker": ticker, "side": book_side, "count": count, "post_only": post_only})
        for p in self._positions:                        # _positions = the TRUE (post-fill) state
            if p["ticker"] == ticker:
                cur = float(p["position_fp"])
                if book_side == "ask" and cur > 0:
                    p["position_fp"] = str(max(0.0, cur - count))
                elif book_side == "bid" and cur < 0:
                    p["position_fp"] = str(min(0.0, cur + count))
        return {"order": {"order_id": client_order_id, "fill_count": str(count)}}
    def total_crossed(self):
        return sum(x["count"] for x in self.crosses)


# (`legacy_inventory_mode` lived here until 2026-07-28. It flipped EXIT_AT_TOUCH /
# HOLDING_EXIT_ONLY off so pre-07-27 tests could keep exercising the loss cap and the
# holding-side skew. The operator's Q1 decision removed those flags — the inventory risk
# rule is UNCONDITIONAL — so the legacy regime no longer exists to switch into. Every test
# that called it was rewritten against the new contract; see test_exit_only.py for the pins.)


def _order(oid, ticker, outcome, price, cnt):
    return {"order_id": oid, "ticker": ticker, "outcome_side": outcome,
            f"{outcome}_price_dollars": f"{price:.4f}", "remaining_count_fp": f"{cnt:.2f}"}


def _run(monkeymod, client, tmpdir, footprint_env=None):
    """Drive run_once with a mock client + a temp data dir. Returns the plan row."""
    q.DATA_DIR = tmpdir; q.STOP_FILE = os.path.join(tmpdir, "STOP")
    q.STATE_FILE = os.path.join(tmpdir, "quoter_state.json")
    orig = q.KalshiOrderClient
    q.KalshiOrderClient = lambda *a, **k: client
    try:
        q.run_once()
    finally:
        q.KalshiOrderClient = orig
    rows = []
    for p in os.listdir(tmpdir):
        if p.startswith("plans-"):
            for line in open(os.path.join(tmpdir, p)):
                rows.append(json.loads(line))
    return rows[-1] if rows else {}


# ---- _live_standing: (dict,count) + crash-proof ----
def test_live_standing_returns_dict_and_count():
    c = MockClient(resting=[_order("a", "T1", "yes", 0.60, 10),
                            _order("b", "T1", "no", 0.30, 5)])
    st, n = q._live_standing(c)
    assert n == 2
    assert st["T1"] == [{"side": "yes", "price_dollars": 0.60, "count": 10, "order_id": "a"},
                        {"side": "no", "price_dollars": 0.30, "count": 5, "order_id": "b"}]

def test_live_standing_isolates_one_malformed_record():
    good = _order("a", "T1", "yes", 0.60, 10)
    bad = {"order_id": "b", "ticker": "T2", "outcome_side": "yes"}  # missing price
    worse = {"order_id": "c"}                                        # missing everything
    st, n = q._live_standing(MockClient(resting=[good, bad, worse]))
    assert n == 3 and "T1" in st and "T2" not in st  # bad rows skipped, good survives


# ---- series allowlist (pilot scoped to weather/temp) ----
def test_series_allowlist_filters_to_temp(monkeypatch):
    progs = [
        {"market_ticker": "KXTEMPNYCH-26JUL2014-T81.99", "incentive_type": "liquidity",
         "target_size_fp": 1000, "discount_factor_bps": 5000, "period_reward": 1000000,
         "start_date": "2026-07-20T17:00:00Z", "end_date": "2099-01-01T00:00:00Z"},
        {"market_ticker": "KXDXYDUD-26JUL20-T100", "incentive_type": "liquidity",
         "target_size_fp": 1000, "discount_factor_bps": 5000, "period_reward": 9000000,
         "start_date": "2026-07-20T17:00:00Z", "end_date": "2099-01-01T00:00:00Z"},
    ]
    # These fixtures end in 2099; the far-close cap (KALSHI_MAX_DAYS_TO_CLOSE, added 2026-07-25)
    # would reject both on horizon before the allowlist is ever consulted. This test pins the
    # ALLOWLIST, so the horizon gate is disabled here rather than the fixtures being re-dated.
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0.0)
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXTEMPNYCH", "KXTEMPDCH"])
    picked = q.select_footprint(progs, q.utcnow())
    assert [m["ticker"] for m in picked] == ["KXTEMPNYCH-26JUL2014-T81.99"]  # DXY excluded
    # empty allowlist = no filter (legacy behavior)
    monkeypatch.setattr(q, "SERIES_ALLOW", [])
    picked2 = q.select_footprint(progs, q.utcnow())
    assert len(picked2) == 2


# ---- taker de-risk backstop (flatten_to_zero, _flatten_all, de-risk pass) ----
_BOOK = {"orderbook_fp": {"yes_dollars": [["0.60", "500"]], "no_dollars": [["0.38", "500"]]}}

def test_flatten_to_zero_sells_long_yes(monkeypatch):
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    c = MockClient(mode="live", positions=[{"ticker": "T1", "position_fp": "20.00"}])
    flat, n = q.flatten_to_zero(c, "T1", standing_oids=["o1"])
    assert flat and n >= 1
    assert "o1" in c.cancelled                          # cancels our resting first (self-trade guard)
    assert all(x["side"] == "ask" and not x["post_only"] for x in c.crosses)  # sell yes, taker

def test_flatten_to_zero_buys_long_no(monkeypatch):
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    c = MockClient(mode="live", positions=[{"ticker": "T1", "position_fp": "-15.00"}])
    flat, n = q.flatten_to_zero(c, "T1")
    assert flat and all(x["side"] == "bid" for x in c.crosses)   # buy yes to cover short

def test_flatten_to_zero_no_overshoot_on_lagging_read(monkeypatch):
    # THE review blocker: an eventually-consistent positions read that LAGS the fill must
    # NOT cause re-crossing full size (which would flip long->short). The fix caps cumulative
    # crossing at |pos0| via the venue's confirmed fill_count, not the stale re-read.
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    c = MockClient(mode="live", positions=[{"ticker": "T1", "position_fp": "20.00"}])
    c._frozen_positions = [{"ticker": "T1", "position_fp": "20.00"}]   # read NEVER updates
    flat, n = q.flatten_to_zero(c, "T1")
    assert c.total_crossed() <= 20                       # never cross MORE than the initial position
    assert float(c._positions[0]["position_fp"]) >= 0.0  # TRUE position never flipped to short

def test_flatten_to_zero_fail_closed_on_blind_position():
    c = MockClient(mode="live", get_positions_raises=True)
    flat, n = q.flatten_to_zero(c, "T1")
    assert not flat and n == 0 and not c.crosses         # never cross blind

def test_flatten_to_zero_refuses_to_cross_on_unconfirmed_cancel(monkeypatch):
    # FIX 4 (live 2026-07-27 19:40:03Z): cancels were swallowed by try/except, the cross
    # proceeded, and the stale 41ct@0.73 exit refilled the position +40.55 ct seven seconds
    # after the taker got it flat. An UNCONFIRMED cancel must ABORT the cross — the resting
    # order IS the maker exit, so refusing to cross strands nothing.
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    c = MockClient(mode="live", resting=[_order("x1", "T1", "no", 0.38, 8)],
                   positions=[{"ticker": "T1", "position_fp": "20.00"}],
                   cancel_fail_ids=["x1"])                # the exit's cancel 429s
    flat, n = q.flatten_to_zero(c, "T1", standing_oids=["x1"])
    assert not flat and c.crosses == [], "unconfirmed cancel must abort the cross"
    assert any(o.get("order_id") == "x1" for o in c._resting)   # the exit keeps working the book

def test_flatten_all_maker_first_no_taker_below_threshold(monkeypatch):
    # STOP is MAKER-FIRST: cancel quotes, rest a passive offset. With the residual below the
    # taker threshold, escalation must NOT fire — no spread is crossed (no fire-sale).
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)         # no real sleep in tests
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 25.0)    # pos 20 < 25 -> below materiality
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    c = MockClient(mode="live",
                   resting=[_order("o1", "T1", "yes", 0.6, 10)],
                   positions=[{"ticker": "T1", "position_fp": "20.00"}])   # long yes
    q._flatten_all(c)
    assert "o1" in c.cancelled                           # cancelled the resting quote
    assert not c.crosses                                 # maker only — nothing crossed
    offs = [o for o in c.created if o["ticker"] == "T1"]
    assert len(offs) == 1 and offs[0]["side"] == "no"    # long yes -> maker NO offset rested
    assert offs[0]["count"] <= 20                        # capped at |pos|, no overshoot

def test_flatten_all_escalates_unfilled_material_residual(monkeypatch):
    # AUDIT HIGH-1: pure-maker STOP leaves you hanging if offsets never fill. After the bounded
    # wait, a STILL-material residual MUST be taker-crossed (sized to the residual only).
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)         # bounded wait, zeroed for tests
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 5.0)     # pos 20 >= 5 -> material
    monkeypatch.setattr(q, "TAKER_FLATTEN", True)
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    c = MockClient(mode="live", resting=[],
                   positions=[{"ticker": "T1", "position_fp": "20.00"}])   # offset won't fill (mock)
    q._flatten_all(c)
    assert c.crosses                                     # escalated: residual taken
    assert c.total_crossed() <= 20                       # sized to the residual, never more
    assert abs(float(c._positions[0]["position_fp"])) < 1.0   # actually flat after escalation

def test_derisk_pass_does_not_taker_on_hard_breach(monkeypatch, tmp_path):
    # NEW MODEL: a hard inventory breach alone must NOT taker (that reflex was the fire-sale).
    # Far-off close + big position -> the maker skew handles it, ZERO crosses.
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", True)
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        if p.endswith("/orderbook"): return _BOOK
        return {"market": {"close_time": "2099-01-01T00:00:00Z"}}   # far off -> not near settle
    monkeypatch.setattr(q, "public_get", pg)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    c = MockClient(mode="live", positions=[{"ticker": "T1", "position_fp": "150.00"}])  # > hard cap
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("taker_flattens", 0) == 0             # hard breach no longer takes
    assert not c.crosses

def test_derisk_pass_takers_only_near_settlement(monkeypatch, tmp_path):
    # The ONE genuine last resort: a material position on a market about to settle (can't
    # maker-unwind what's about to close) DOES taker-flatten.
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0); monkeypatch.setattr(q, "TAKER_FLATTEN", True)
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        if p.endswith("/orderbook"): return _BOOK
        return {"market": {"close_time": "2000-01-01T00:00:00Z"}}   # already at/past close -> settling
    monkeypatch.setattr(q, "public_get", pg)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    c = MockClient(mode="live", positions=[{"ticker": "T1", "position_fp": "20.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("taker_flattens", 0) >= 1             # near settlement = genuine last resort
    assert c.crosses


# ---- inventory-aware delta-neutral shaping (the P0 redesign core) ----
def _mkt(target=1000): return {"target": target, "end": "2099-01-01T00:00:00Z"}
_YL = [["0.50", "9999"]]      # deep external book, both sides >> target -> JOIN branch
_NL = [["0.49", "9999"]]

def test_flat_quotes_both_sides_at_reference(monkeypatch):
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    qs = q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=0.0)
    sides = {x["side"]: x for x in qs}
    assert sides["yes"]["price_dollars"] == 0.50 and sides["no"]["price_dollars"] == 0.49  # at ref

def test_long_yes_rests_only_the_reducing_no_at_ref(monkeypatch):
    # REWRITTEN 2026-07-28, Q1 operator decision: old pin (test_long_yes_throttles_yes_keeps_no_
    # at_ref) asserted the removed holding-side skew — a shrunk/stepped-inside YES resting NEXT TO
    # the unwind. Holding is now EXIT ONLY: the reducing NO rests AT the reference and nothing
    # else. A second side while holding is the accumulating re-post defect and must fail here.
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    qs = q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=50.0)
    assert len(qs) == 1, f"holding must be exit-only, got {qs}"
    assert qs[0]["side"] == "no" and qs[0]["reason"] == "unwind"
    assert qs[0]["price_dollars"] == 0.49              # AT the reference (fillable), not stepped
    assert qs[0]["count"] == 50                        # exactly |inv|: pure exit, never through flat

def test_long_yes_between_soft_and_hard_rests_no_accumulating_side(monkeypatch):
    # REWRITTEN 2026-07-28, Q1 operator decision: old pin (test_long_yes_between_soft_and_hard_
    # keeps_yes_live) asserted the removed keep-the-paycheck-side-live floor. Between SOFT and
    # HARD is still HOLDING, so the accumulating YES must not rest at ANY size — the MIN_QUOTE
    # floor coming back here is a regression.
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    qs = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=50.0)}
    assert "yes" not in qs, "no accumulating side may rest while holding, floor or not"
    assert "no" in qs and qs["no"]["reason"] == "unwind"

def test_long_yes_at_hard_pulls_accumulating_side(monkeypatch):
    # AUDIT MED-3 (risk envelope): AT/ABOVE HARD the MIN_QUOTE floor would keep leaking fills on
    # a one-way market, so the accumulating side IS pulled — HARD is the hard position envelope.
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    qs = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=100.0)}
    assert "yes" not in qs                             # envelope capped: leak stopped
    assert "no" in qs and qs["no"]["reason"] == "unwind"   # de-risk side still resting

def test_settlement_ramp_shrinks_join_not_unwind(monkeypatch):
    # AUDIT HIGH-2: be SMALL at settlement. Inside RAMP_MIN join sizes scale down with time
    # left; the unwind (reducing) quote is NOT ramped — de-risk capacity is preserved.
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "RAMP_MIN", 180); monkeypatch.setattr(q, "WIND_DOWN_MIN", 45)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    from datetime import timedelta as _td
    m_near = {"target": 1000, "end": (q.utcnow() + _td(minutes=60)).isoformat()}  # inside ramp
    m_far = {"target": 1000, "end": "2099-01-01T00:00:00Z"}                       # outside ramp
    far = {x["side"]: x for x in q.desired_quotes(m_far, _YL, _NL, q.utcnow(), inv=0.0)}
    near = {x["side"]: x for x in q.desired_quotes(m_near, _YL, _NL, q.utcnow(), inv=0.0)}
    assert near["yes"]["count"] < far["yes"]["count"]      # ramped down vs far market
    assert near["yes"]["count"] >= 2 and near["no"]["count"] >= 2   # both still LIVE
    # unwind side not ramped: with a position, the reducing quote still sizes toward |inv|
    nearu = {x["side"]: x for x in q.desired_quotes(m_near, _YL, _NL, q.utcnow(), inv=20.0)}
    # >= |inv| rather than == |inv| since 2026-07-26: the reducing half of a two-sided quote is
    # ADD+|inv| (ADD already ramped), so de-risk capacity is preserved MORE strongly than before.
    assert nearu["no"]["reason"] == "unwind" and nearu["no"]["count"] >= 20

def test_long_no_mirror(monkeypatch):
    # REWRITTEN 2026-07-28, Q1 operator decision: old pin asserted the removed holding-side skew
    # on the short polarity. Long NO is HOLDING -> exit only: the reducing YES at reference.
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    qs = q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=-50.0)
    assert len(qs) == 1, f"holding must be exit-only, got {qs}"
    assert qs[0]["side"] == "yes" and qs[0]["reason"] == "unwind"
    assert qs[0]["price_dollars"] == 0.50              # YES reducing -> at ref
    assert qs[0]["count"] == 50

def test_event_delta_throttles_when_ticker_below_soft(monkeypatch):
    # SUBJECT STILL LIVE (the event-delta throttle shapes FLAT tickers); the HELD half of the
    # old pin was rewritten 2026-07-28 (Q1): any per-ticker inventory >= tolerance is now
    # exit-only, so the event aggregate can no longer keep an accumulating side resting there.
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    qs = q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=20.0, event_delta=55.0)
    assert len(qs) == 1 and qs[0]["side"] == "no" and qs[0]["reason"] == "unwind", \
        f"holding +20 inside a directional event must still be exit-only, got {qs}"
    # flat ticker in a directional event: throttle the event-accumulating side, no unwind created
    qs2 = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=0.0, event_delta=-55.0)}
    assert qs2["no"]["price_dollars"] == 0.48          # event short -> throttle NO (accumulating)
    assert qs2["yes"]["price_dollars"] == 0.50         # non-accumulating side stays at ref
    assert all(x["reason"] != "unwind" for x in qs2.values())   # nothing to unwind (flat)

def test_activate_market_rests_reducing_side_when_carrying_inventory(monkeypatch):
    # thin book (both sides < target) -> ACTIVATE branch; carrying inventory -> DO NOT blanket-pull;
    # rest ONLY the reducing side as a passive maker unwind (fix D).
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0)
    thin_y = [["0.50", "10"]]; thin_n = [["0.49", "10"]]   # depth 10 << target 1000 -> void/activate
    out = q.desired_quotes(_mkt(1000), thin_y, thin_n, q.utcnow(), inv=50.0)  # long yes
    assert len(out) == 1 and out[0]["side"] == "no" and out[0]["reason"] == "unwind"
    # event directional but flat inventory -> don't ADD via activate
    assert q.desired_quotes(_mkt(1000), thin_y, thin_n, q.utcnow(), inv=0.0, event_delta=55.0) == []
    # flat + neutral -> activates normally (if affordable)
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 100000.0)
    assert q.desired_quotes(_mkt(1000), thin_y, thin_n, q.utcnow(), inv=0.0)  # non-empty


# ---- crossed-book gate ----
def test_desired_quotes_gates_crossed_book():
    m = {"target": 1, "end": "2099-01-01T00:00:00Z"}
    # best_y 0.60 + best_n 0.60 = 1.20 >= 1 -> crossed -> gated
    assert q.desired_quotes(m, [["0.60", "9999"]], [["0.60", "9999"]], q.utcnow()) == []
    # healthy book best_y 0.55 + best_n 0.40 = 0.95 < 1 -> quotes
    assert q.desired_quotes(m, [["0.55", "9999"]], [["0.40", "9999"]], q.utcnow())


# ---- committed pre-check + failed-cancel deferral (via run_once) ----
def _cfg(monkeypatch, join=20, mktcap=15, totcap=40):
    monkeypatch.setattr(q, "JOIN_SIZE", join)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", float(mktcap))
    monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", float(totcap))
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else
                        {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]], "no_dollars": [["0.49", "9999"]]}})

def test_committed_precheck_creates_when_room(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("mode") == "live"
    assert len(c.created) == 2                        # both sides placed
    assert 0 < row.get("committed_usd", 0) <= 40.0

def test_held_cost_reads_prod_position_fp():
    # PROD payload shape (verified 2026-07-20): position_fp string, no 'position' key
    c = MockClient(mode="live", positions=[
        {"ticker": "T1", "position_fp": "18.71"},
        {"ticker": "T2", "position_fp": "-6.12", "market_exposure_dollars": "3.06"},
        {"ticker": "T3", "position_fp": "0.00"},
    ])
    total, by, costs = q._held_cost(c)
    assert abs(total - (18.71 + 3.06)) < 1e-9  # exposure when present, |pos| fallback
    assert by == {"T1": 18.71, "T2": -6.12}
    # avg cost per contract ONLY where the venue reports exposure — never fabricated
    assert abs(costs["T2"] - 0.50) < 1e-9 and "T1" not in costs


def test_committed_precheck_skips_over_cap(monkeypatch, tmp_path):
    _cfg(monkeypatch, totcap=40)
    # standing survivor that CANNOT be cancelled (cancel 429) worth ~$38 -> only ~$2
    # of headroom left, so the fresh ~$20 desired book must be SKIPPED, not stacked.
    survivor = _order("S", "TOLD", "yes", 0.95, 40)   # 0.95*40 = $38 committed, uncancellable
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[survivor], cancel_fail_ids=["S"])
    row = _run(monkeypatch, c, str(tmp_path))
    # T1 is a different ticker (not deferred by failed-cancel), but committed starts
    # at ~$38 (survivor) so the ~$20 T1 book breaches $40 and is skipped.
    assert row.get("create_skipped", 0) >= 1
    assert row.get("committed_usd", 0) <= 40.0
    assert len(c.created) == 0                         # nothing stacked on top of survivor

def test_positions_read_failure_defers_all_creates(monkeypatch, tmp_path):
    # FAIL CLOSED: if held inventory can't be read, committed is unknown -> defer
    # every create rather than admit orders at held_cost=0 (the fix4 regression).
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[], get_positions_raises=True)
    row = _run(monkeypatch, c, str(tmp_path))
    # new behavior: the signed-inventory read is AHEAD of the quote loop and fails CLOSED —
    # the whole cycle halts (delta unknown => never shape/create blind), not just creates.
    assert "positions_read_failed" in row
    assert len(c.created) == 0 and len(c.cancelled) == 0

def test_failed_cancel_defers_same_ticker(monkeypatch, tmp_path):
    _cfg(monkeypatch, totcap=200)                     # cap not the binding constraint here
    # standing on T1 (old price) that fails to cancel; desired re-quotes T1 (new price).
    old = _order("O", "T1", "yes", 0.40, 5)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[old], cancel_fail_ids=["O"])
    row = _run(monkeypatch, c, str(tmp_path))
    # cancel of O fails -> T1 is a failed-cancel ticker -> its fresh creates deferred
    assert row.get("cancel_fail", 0) == 1
    assert row.get("create_skipped", 0) >= 1
    assert all(cr["ticker"] != "T1" for cr in c.created)


def test_reconcile_guard_halts(monkeypatch, tmp_path):
    # get_orders returns rows but they are all unparseable -> raw>0 parsed==0 -> halt
    bad = [{"order_id": "x", "ticker": "T", "outcome_side": "yes"}]  # missing price
    c = MockClient(mode="live", resting=bad)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""})
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("reconcile_fail") == 1
    assert not c.created and not c.cancelled            # halted: no order ops


def test_standing_read_failure_skips_cycle(monkeypatch, tmp_path):
    c = MockClient(mode="live", get_orders_raises=True)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""})
    row = _run(monkeypatch, c, str(tmp_path))
    assert "standing_read_failed" in row
    assert not c.created and not c.cancelled            # acted on nothing


def test_stop_sentinel_flattens(monkeypatch, tmp_path):
    c = MockClient(mode="live", resting=[_order("a", "T1", "yes", 0.6, 10),
                                         _order("b", "T2", "no", 0.3, 5)])
    d = str(tmp_path); open(os.path.join(d, "STOP"), "w").close()
    q.DATA_DIR = d; q.STOP_FILE = os.path.join(d, "STOP"); q.STATE_FILE = os.path.join(d, "s.json")
    orig = q.KalshiOrderClient; q.KalshiOrderClient = lambda *a, **k: c
    try:
        q.run_once()
    finally:
        q.KalshiOrderClient = orig
    assert set(c.cancelled) == {"a", "b"}               # STOP cancelled everything


# ---- per-event aggregate delta (fix B) ----
def test_unwind_size_never_overshoots_inventory(monkeypatch):
    # A1 REGRESSION: unwinding a SMALL position must not rest MORE than |inv| — resting a full
    # capped-join floor (e.g. 80) to unwind +5 would, on full fill, flip +5 -> -75.
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    assert q._unwind_size(80, 0.50, 5) == 5            # capped at |inv|, NOT floored at base=80
    assert q._unwind_size(80, 0.50, 3.4) == 3          # fractional inv rounds, still <= |inv|
    assert q._unwind_size(80, 0.50, 50) == 50          # normal case: rest exactly the position
    # REWRITTEN 2026-07-29 (audit F12, operator "do all"): the room DOLLAR bound is deleted —
    # it clipped big exits (80 ct @ 0.75 -> 53) and stranded the tail for the taker. The exit
    # always sizes the FULL |inv|; the overshoot guard (never > |inv|) above is the only cap.
    assert q._unwind_size(80, 0.99, 400) == 400        # dollars must never clip the exit
    assert q._unwind_size(80, 0.50, 200) == 200        # |inv| within room -> exactly the position
    # end-to-end: a +5 long-yes ticker rests a NO unwind of exactly 5 (no overshoot).
    # (Pre-Q1 the reducing half of a TWO-SIDED quote was ADD+|inv| so a double fill landed
    # paired; since 2026-07-28 holding is exit-only, so EVERY unwind is a pure exit == |inv|.)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    qs = q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=5.0)
    assert len(qs) == 1 and qs[0]["side"] == "no" and qs[0]["reason"] == "unwind"
    assert qs[0]["count"] == 5                         # exactly the position, never through flat

def test_event_deltas_aggregates_correlated_strikes():
    # SERIES-EVENT-STRIKE: the first two dash-fields identify the event; strikes aggregate.
    held = {"KXTEMPNYCH-26JUL2014-T81": 12.0, "KXTEMPNYCH-26JUL2014-T83": 18.0,
            "KXTEMPNYCH-26JUL2014-T85": 20.0, "KXAAAGASD-26JUL20-T3": -40.0}
    ed = q.event_deltas(held)
    assert ed["KXTEMPNYCH-26JUL2014"] == 50.0            # 12+18+20 aggregate (each under SOFT)
    assert ed["KXAAAGASD-26JUL20"] == -40.0
    assert q._event_key("KXTEMPNYCH-26JUL2014-T81") == "KXTEMPNYCH-26JUL2014"


# ---- polarity-aware capital gates (fix A) ----
def test_cap_desired_keeps_unwind_market_over_cap(monkeypatch):
    monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", 10.0)
    desired = {
        "ACC": [{"side": "yes", "price_dollars": 0.5, "count": 40, "reason": "join"}],   # $20 accum
        "RED": [{"side": "no", "price_dollars": 0.5, "count": 40, "reason": "unwind"}],   # $20 reduce
    }
    kept, dropped = q.cap_desired(desired, {"ACC": 100.0, "RED": 1.0})
    assert "RED" in kept                                 # reducing kept despite breaching cap
    assert "ACC" not in kept and dropped == 1            # accumulating tail cut

def test_bound_creates_prioritizes_unwind(monkeypatch):
    monkeypatch.setattr(q, "WRITE_BUDGET_PER_CYCLE", 1)
    creates = [{"ticker": "ACC", "side": "yes", "price_dollars": 0.5, "count": 1, "reason": "join"},
               {"ticker": "RED", "side": "no", "price_dollars": 0.5, "count": 1, "reason": "unwind"}]
    kept, dropped = q.bound_creates(creates, [], {"ACC": 100.0, "RED": 1.0})
    assert [c["ticker"] for c in kept] == ["RED"]        # unwind kept, higher-usd_day accum dropped
    assert dropped == 1

def test_unwind_create_exempt_from_committed_cap(monkeypatch, tmp_path):
    # THE STUCK-BOT FIX: held inventory ALONE exceeds MAX_TOTAL_CAPITAL. The REDUCING (unwind)
    # side must STILL be placed — otherwise the bot can never flatten (cap blocks the only
    # de-risking order). Post-Q1 (2026-07-28) the held ticker no longer PROPOSES an accumulating
    # side at all (exit-only), so the cap-gates-accumulating prong is pinned via a FLAT sibling
    # ticker T2 whose join book must be skipped while the cap is breached.
    # (HELD ceiling disabled here to pin the committed-cap mechanism in ISOLATION — at defaults
    # the $50 held would trip the level breaker first, which handles this scenario earlier.)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    _cfg(monkeypatch, join=20, mktcap=250, totcap=10)     # cap $10, held will be ~$50
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": "T2", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[{"ticker": "T1", "position_fp": "50"}])  # long yes
    row = _run(monkeypatch, c, str(tmp_path))
    reds = [o for o in c.created if o["ticker"] == "T1" and o["side"] == "no"]
    assert len(reds) == 1                                 # reducing NO placed despite committed>cap
    assert len([o for o in c.created if o["ticker"] == "T1"]) == 1   # ...and it is T1's ONLY order
    assert not [o for o in c.created if o["ticker"] == "T2"]         # flat T2's join gated by the cap
    assert row.get("capped_markets", 0) >= 1              # cap_desired cut the accumulating tail

def test_read_blackout_cancels_last_known_quotes(monkeypatch, tmp_path):
    # AUDIT MED-4: fail-closed alone leaves resting quotes LIVE and fillable while the bot is
    # blind. After BLACKOUT_CANCEL_AFTER consecutive failed read cycles, the last-known order
    # ids (persisted from the last good read) are best-effort cancelled.
    monkeypatch.setattr(q, "BLACKOUT_CANCEL_AFTER", 2)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""})
    # seed state: one prior blind cycle + last-known oids from the last good cycle
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({"read_fail_streak": 1, "last_oids": ["a", "b"]}, f)
    c = MockClient(mode="live", get_orders_raises=True)   # reads fail again this cycle
    row = _run(monkeypatch, c, str(tmp_path))
    assert "standing_read_failed" in row
    assert row.get("read_fail_streak") == 2
    assert set(c.cancelled) == {"a", "b"}                # blind quotes cancelled best-effort

def test_strand_inventory_gets_maker_unwind(monkeypatch, tmp_path):
    # fix E: inventory on a ticker NOT in this cycle's footprint (dropped from selection) must
    # still get a passive maker unwind — not sit unmanaged until the taker backstop fires.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])   # empty footprint
    c = MockClient(mode="live", resting=[], positions=[{"ticker": "STRAND", "position_fp": "40"}])
    row = _run(monkeypatch, c, str(tmp_path))
    unwinds = [o for o in c.created if o["ticker"] == "STRAND" and o["side"] == "no"]
    assert len(unwinds) == 1                              # long-yes strand -> reducing NO bid rested


# ================= 2026-07-21 review fixes (C1..C18) ==============================================
# EMPIRICALLY MEASURED against the pre-fix build (ea28fa38): 15 of these 16 tests FAIL on it and so
# genuinely pin their fix. The exception is test_gtc_canceled_still_raises, which passes on BOTH
# builds by design — it is a REGRESSION GUARD proving the new IOC exemption did not leak into the
# GTC/post_only resting-quote path, not a fix-pin. Do not "fix" it to fail on pre-fix.
from maker_kalshi_client import KalshiOrderClient


# ---- C1: IOC partial fill (status 'canceled' + fill_count) must NOT be treated as a rejection ----
def _live_client_with_write(write_fn):
    c = KalshiOrderClient(mode="dry_run")   # constructs without creds
    c.mode = "live"                          # flip so the create-order status guard runs
    c._write = write_fn                      # no network; guard sees this response
    return c

def test_ioc_partial_fill_not_treated_as_rejection():
    c = _live_client_with_write(
        lambda m, p, b: {"order": {"order_id": "x", "status": "canceled", "fill_count": "15"}})
    # IOC 'canceled' WITH a fill is a partial fill (success) -> must NOT raise (review C1)
    r = c.create_order_v2("T1", "ask", 40, 0.60,
                          time_in_force="immediate_or_cancel", post_only=False)
    assert r["order"]["fill_count"] == "15"

def test_gtc_canceled_still_raises():
    # a resting (GTC/post_only) quote that fails to REST is still a fatal non-fill -> raise
    c = _live_client_with_write(lambda m, p, b: {"order": {"order_id": "x", "status": "canceled"}})
    with pytest.raises(RuntimeError):
        c.create_order_v2("T1", "bid", 10, 0.50)      # default TIF = good_till_canceled

def test_ioc_rejected_zero_fill_returns_for_caller_to_break():
    # a rejected IOC carries zero fill; it must return (not raise) so flatten's fill<=0 break runs
    c = _live_client_with_write(
        lambda m, p, b: {"order": {"order_id": "x", "status": "rejected"}})
    r = c.create_order_v2("T1", "ask", 5, 0.60, time_in_force="immediate_or_cancel", post_only=False)
    assert (r.get("order") or {}).get("status") == "rejected"


# ---- C4: get_positions/get_orders paginate (follow cursor) + count_filter ----
def test_get_positions_paginates_and_filters():
    pages = [
        {"market_positions": [{"ticker": "A", "position_fp": "5"}], "cursor": "c1"},
        {"market_positions": [{"ticker": "B", "position_fp": "-3"}], "cursor": ""},
    ]
    calls = []
    c = KalshiOrderClient(mode="dry_run")
    def fake(method, path, body=None, authed=True):
        calls.append(path); return pages[len(calls) - 1]
    c._request = fake
    out = c.get_positions()
    assert [p["ticker"] for p in out["market_positions"]] == ["A", "B"]  # BOTH pages aggregated
    assert "count_filter=position" in calls[0]                           # settled/zero rows dropped
    assert "cursor=c1" in calls[1]                                       # followed the cursor

def test_get_orders_paginates():
    pages = [{"orders": [{"order_id": "o1"}], "cursor": "n"},
             {"orders": [{"order_id": "o2"}], "cursor": ""}]
    calls = []
    c = KalshiOrderClient(mode="dry_run")
    def fake(method, path, body=None, authed=True):
        calls.append(path); return pages[len(calls) - 1]
    c._request = fake
    out = c.get_orders("resting")
    assert [o["order_id"] for o in out["orders"]] == ["o1", "o2"]
    assert "status=resting" in calls[0] and "cursor=n" in calls[1]


# ---- C2: a positions-ONLY blackout must accumulate the streak and eventually cancel ----
def test_positions_only_blackout_escalates(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "BLACKOUT_CANCEL_AFTER", 2)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""})
    resting = [_order("a", "T1", "yes", 0.6, 10), _order("b", "T1", "no", 0.3, 5)]
    c = MockClient(mode="live", resting=resting, get_positions_raises=True)  # standing OK, positions 500
    row1 = _run(monkeypatch, c, str(tmp_path))
    assert row1.get("read_fail_streak") == 1 and not c.cancelled        # cycle 1: no cancel yet
    row2 = _run(monkeypatch, c, str(tmp_path))
    assert row2.get("read_fail_streak") == 2                            # streak ACCUMULATED (was pinned at 1)
    assert set(c.cancelled) == {"a", "b"}                              # blind quotes cancelled


# ---- C3: last_oids must include THIS cycle's created venue ids ----
def test_last_oids_includes_this_cycle_creates(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[])
    _run(monkeypatch, c, str(tmp_path))
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert len(c.created) == 2
    assert len(st.get("last_oids", [])) == 2                            # fresh creates now tracked
    assert all(not str(o).startswith("sim-") for o in st["last_oids"])  # real venue ids, not sim


# ---- C15: blackout guard keeps ids whose cancel FAILED (does not wipe) ----
def test_blackout_keeps_failed_cancel_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "BLACKOUT_CANCEL_AFTER", 2)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""})
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({"read_fail_streak": 1, "last_oids": ["a", "b"]}, f)
    c = MockClient(mode="live", get_orders_raises=True, cancel_fail_ids=["a", "b"])  # all cancels fail
    _run(monkeypatch, c, str(tmp_path))
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert set(st.get("last_oids", [])) == {"a", "b"}                   # kept for retry, NOT wiped


# ---- C7: a same-side unwind is NOT stacked on a failed-cancel stale reducing order ----
def test_unwind_not_stacked_on_failed_same_side_cancel(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    stale_no = _order("N", "T1", "no", 0.40, 15)          # stale NO unwind at an old price
    c = MockClient(mode="live", resting=[stale_no],
                   positions=[{"ticker": "T1", "position_fp": "15"}],  # long yes -> wants NO unwind
                   cancel_fail_ids=["N"])                 # cancel of the stale NO fails (429)
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("cancel_fail", 0) == 1
    assert all(cr["side"] != "no" for cr in c.created)    # fresh NO unwind DEFERRED (would stack)
    assert row.get("create_skipped", 0) >= 1


# ---- C13: ramp window is per-market lifetime-relative, not a flat 180 ----
def test_ramp_min_is_lifetime_relative(monkeypatch):
    monkeypatch.setattr(q, "RAMP_MIN", 180); monkeypatch.setattr(q, "RAMP_LIFE_FRAC", 0.5)
    monkeypatch.setattr(q, "WIND_DOWN_MIN", 20)
    from datetime import timedelta as _td
    now = q.utcnow()
    def prog(tk, end):
        return {"market_ticker": tk, "incentive_type": "liquidity", "target_size_fp": 1000,
                "discount_factor_bps": 5000, "period_reward": 800000,
                "start_date": now.isoformat(), "end_date": end}
    hourly = q.select_footprint([prog("KXTEMPNYCH-26JUL21-T80", (now + _td(minutes=60)).isoformat())], now)
    assert len(hourly) == 1 and abs(hourly[0]["ramp_min"] - 30.0) < 1.0   # min(180, 0.5*60)=30
    daily = q.select_footprint([prog("KXAAAGASD-26JUL22-4.1", (now + _td(days=2)).isoformat())], now)
    assert abs(daily[0]["ramp_min"] - 180.0) < 1.0                        # long market keeps full 180


# ---- C18: footprint round-robins across series so gas is not starved by high-pot temp ----
def test_footprint_round_robin_covers_all_series(monkeypatch):
    monkeypatch.setattr(q, "FOOTPRINT_TOP", 4); monkeypatch.setattr(q, "PER_SERIES_CAP", 10)
    monkeypatch.setattr(q, "SERIES_ALLOW", [])
    from datetime import timedelta as _td
    now = q.utcnow(); start = now.isoformat(); end = (now + _td(days=1)).isoformat()
    def prog(tk, rew):
        return {"market_ticker": tk, "incentive_type": "liquidity", "target_size_fp": 1000,
                "discount_factor_bps": 5000, "period_reward": rew, "start_date": start, "end_date": end}
    progs = [prog(f"KXTEMPNYCH-26JUL21-T{i}", 9000000) for i in range(5)]   # 5 high-pot temp
    progs.append(prog("KXAAAGASD-26JUL22-4.1", 100000))                     # 1 low-pot gas
    picked = {m["ticker"].split("-")[0] for m in q.select_footprint(progs, now)}
    assert "KXAAAGASD" in picked and "KXTEMPNYCH" in picked                 # gas no longer crowded out


# ---- C12: per-market HELD-$ cap pulls the accumulating side before the contract HARD ----
def test_held_dollar_cap_pulls_accumulating_below_contract_hard(monkeypatch):
    monkeypatch.setattr(q, "INV_SOFT_CT", 15.0); monkeypatch.setattr(q, "INV_HARD_CT", 60.0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 15.0); monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    m = {"target": 1, "end": "2099-01-01T00:00:00Z"}
    yl = [["0.90", "9999"]]; nl = [["0.09", "9999"]]     # high yes price
    # inv=+20: held $ = 20*0.90 = $18 >= $15 cap, but 20 < contract HARD 60 -> $ cap must pull YES
    qs = {x["side"]: x for x in q.desired_quotes(m, yl, nl, q.utcnow(), inv=20.0)}
    assert "yes" not in qs                                # accumulating YES pulled by held-$ cap
    assert "no" in qs and qs["no"]["reason"] == "unwind"


# ---- C16: settle-taker close_time read failure is COUNTED (was silently swallowed) ----
def test_settle_check_failure_is_counted(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0); monkeypatch.setattr(q, "TAKER_FLATTEN", True)
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        if p.endswith("/orderbook"): return _BOOK
        raise RuntimeError("market endpoint 502")         # close_time fetch fails
    monkeypatch.setattr(q, "public_get", pg)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    c = MockClient(mode="live", positions=[{"ticker": "T1", "position_fp": "20.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("settle_check_failed", 0) >= 1         # blind spot surfaced, not swallowed
    assert row.get("taker_flattens", 0) == 0 and not c.crosses  # did NOT taker on an unknown clock


# ---- C8: settle-taker window clamped to <= wind-down (maker-first ordering) ----
def test_clamp_settle_window():
    assert q._clamp_settle_window(30, 20) == 20           # inverted -> clamped to wind-down
    assert q._clamp_settle_window(15, 45) == 15           # already coherent -> unchanged


# ---- C17: run lock skips a second concurrent run instead of double-placing the book ----
def test_run_lock_skips_when_held(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "_acquire_lock", lambda: False)  # simulate another instance holding it
    d = str(tmp_path)
    q.DATA_DIR = d; q.STOP_FILE = os.path.join(d, "STOP"); q.STATE_FILE = os.path.join(d, "s.json")
    c = MockClient(mode="live", resting=[_order("a", "T1", "yes", 0.6, 10)])
    orig = q.KalshiOrderClient; q.KalshiOrderClient = lambda *a, **k: c
    try:
        rc = q.run_once()
    finally:
        q.KalshiOrderClient = orig
    assert rc == 0 and not c.cancelled and not c.created   # did nothing while the lock was held


# ---- blind-review follow-up: a total parse failure must ALSO drive the blackout streak ----
def test_reconcile_fail_escalates_blackout(monkeypatch, tmp_path):
    # raw resting rows exist but ALL fail to parse -> we hold orders we cannot interpret (blind to
    # our own book) while they keep filling. Sustained, that must escalate to cancel-by-known-id.
    monkeypatch.setattr(q, "BLACKOUT_CANCEL_AFTER", 2)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""})
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({"read_fail_streak": 1, "last_oids": ["a", "b"]}, f)
    bad = [{"order_id": "x", "ticker": "T", "outcome_side": "yes"}]   # raw>0 but parses to 0
    c = MockClient(mode="live", resting=bad)
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("reconcile_fail") == 1
    assert row.get("read_fail_streak") == 2            # parse failure now drives the streak
    assert set(c.cancelled) == {"a", "b"}              # sustained -> last-known quotes cancelled


# ---- fractional positions: unwind size TRUNCATES, never rounds up (never overshoots flat) ----
def test_unwind_size_truncates_fractional_never_rounds_up(monkeypatch):
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    # round(1.6)=2 would rest MORE than held -> full fill crosses through flat by 0.4 ct.
    # int(1.6)=1 rests less, leaving 0.6 ct sub-minimum dust no order can act on (venue min 1 ct).
    assert q._unwind_size(80, 0.50, 1.6) == 1
    assert q._unwind_size(80, 0.50, 3.6) == 3          # round() would give 4 (overshoot)
    assert q._unwind_size(80, 0.50, 2.0) == 2          # exact integers unchanged


# ============ 2026-07-22 live-loss prevention (late-life gate, loss cap, breaker) ============
def test_late_life_entry_gate(monkeypatch):
    # 2026-07-22 loss: entered hourly temp with 34 of 58 min left (toxic final stretch).
    # Gate: no entry past LATE_LIFE_FRAC(0.6) of a market's own life; abs-capped at 120 min.
    monkeypatch.setattr(q, "LATE_LIFE_FRAC", 0.6)
    monkeypatch.setattr(q, "MAX_ENTRY_CUTOFF_MIN", 120.0)
    monkeypatch.setattr(q, "WIND_DOWN_MIN", 20)
    monkeypatch.setattr(q, "SERIES_ALLOW", [])
    from datetime import timedelta as _td
    now = q.utcnow()
    def prog(tk, start_min_ago, end_min_ahead):
        return {"market_ticker": tk, "incentive_type": "liquidity", "target_size_fp": 1000,
                "discount_factor_bps": 5000, "period_reward": 800000,
                "start_date": (now - _td(minutes=start_min_ago)).isoformat(),
                "end_date": (now + _td(minutes=end_min_ahead)).isoformat()}
    # 58-min temp market, 34 min left: cutoff = max(20, 0.6*58=34.8) -> 34 < 34.8 -> EXCLUDED
    assert q.select_footprint([prog("KXTEMPDCH-1-T1", 24, 34)], now) == []
    # same market, 45 min left -> early-life, included
    assert len(q.select_footprint([prog("KXTEMPDCH-1-T1", 13, 45)], now)) == 1
    # 2-day gas market: frac would be 1728 min; abs cap 120 binds. 3h left -> in; 90min -> out
    assert len(q.select_footprint([prog("KXAAAGASD-1-4.1", 2700, 180)], now)) == 1
    assert q.select_footprint([prog("KXAAAGASD-1-4.1", 2790, 90)], now) == []


def test_unwind_price_rests_at_the_touch_never_capped(monkeypatch):
    # REWRITTEN 2026-07-28, Q1 operator decision: old pin (test_unwind_price_loss_capped)
    # asserted the removed MAX_UNWIND_LOSS ceiling (exit never above 1-cost+MUL). The cap
    # priced the exit BEHIND the touch exactly when the market moved most (live 07-27: exit
    # pinned 0.73 vs market 0.82, 42 ct rode to settlement). The contract is now: the exit
    # rests AT the book reference REGARDLESS of cost basis ("we can sell at a loss"); loss is
    # bounded in TIME by the strand cross, never by refusing to price the exit. If anyone
    # reintroduces a price cap, the 0.85/0.62 case below fails first.
    assert q._unwind_price(0.85, 0.62) == 0.85     # old cap returned 0.48 here — 37c behind
    assert q._unwind_price(0.82, 0.364) == 0.82    # the live 07-27 defect shape, verbatim
    assert q._unwind_price(0.30, 0.10) == 0.30     # normal bleed zone: reference, unchanged
    assert q._unwind_price(0.49, 0.0) == 0.49      # unknown basis: reference, unchanged
    # end-to-end through desired_quotes: long yes at cost 0.62, book no-ref 0.49 -> AT 0.49
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    qs = q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=20.0, cost=0.62)
    assert len(qs) == 1 and qs[0]["reason"] == "unwind" and qs[0]["price_dollars"] == 0.49
    # and the price cannot depend on the basis at all (same book, cheap basis, same price)
    qs2 = q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=20.0, cost=0.10)
    assert qs2[0]["price_dollars"] == 0.49


def test_velocity_breaker_reduce_only(monkeypatch, tmp_path):
    # Exit-only under the breaker is the ONLY behaviour since the Q1 decision (2026-07-28):
    # KALSHI_REDUCE_ONLY_KEEP_BOTH is deleted, so this pin now runs the code as it ships.
    # 2026-07-22 loss: held $0->$28 in 3 'cycle ok' cycles. Rapid held-$ growth must flip the
    # book to REDUCE-ONLY: accumulating creates dropped, unwind still placed, plan flagged.
    import time as _time
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "BREAKER_HELD_GROWTH_USD", 20.0)
    monkeypatch.setattr(q, "BREAKER_WINDOW_S", 600)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": "T2", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({"held_hist": [[_time.time() - 120, 0.0]]}, f)   # held was $0 two min ago
    c = MockClient(mode="live", resting=[],
                   positions=[{"ticker": "T1", "position_fp": "50",
                               "market_exposure_dollars": "25.00"}])  # now $25 held -> breaker
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("breaker_reduce_only") == 1
    assert all(cr["side"] == "no" for cr in c.created)   # ONLY the reducing unwind placed
    assert len(c.created) == 1                           # T2 (flat market) got NOTHING
    # ...and with no prior history AND held below the LEVEL ceiling, no trip
    monkeypatch.setattr(q, "HELD_MAX_USD", 20.0)
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({}, f)
    c2 = MockClient(mode="live", resting=[],
                    positions=[{"ticker": "T1", "position_fp": "50",
                                "market_exposure_dollars": "15.00"}])
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert row2.get("breaker_reduce_only") is None


def test_held_ceiling_level_trigger(monkeypatch, tmp_path):
    # Exit-only under the breaker is the ONLY behaviour since the Q1 decision (2026-07-28):
    # KALSHI_REDUCE_ONLY_KEEP_BOTH is deleted, so this pin now runs the code as it ships.
    # Operator invariant ("never lose more than the reward"): total unpaired held-$ above
    # HELD_MAX_USD flips reduce-only even with ZERO growth (flat history) — a LEVEL lid on the
    # only uncapped loss channel (settlement risk), sized to ~one day's measured rewards.
    import time as _time
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "BREAKER_HELD_GROWTH_USD", 20.0)
    monkeypatch.setattr(q, "HELD_MAX_USD", 20.0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": "T2", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({"held_hist": [[_time.time() - 120, 25.0]]}, f)   # held FLAT at $25 (no growth)
    c = MockClient(mode="live", resting=[],
                   positions=[{"ticker": "T1", "position_fp": "50",
                               "market_exposure_dollars": "25.00"}])  # $25 > $20 ceiling
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("breaker_reduce_only") == 1           # level trigger, growth was zero
    assert all(cr["side"] == "no" for cr in c.created)   # only the reducing unwind rests
    assert len(c.created) == 1                           # flat T2 gets nothing while over the lid


# ============ 07-22 review-response fixes (daily kill, retained-unwind, strand cap, clamps) ======
def test_daily_loss_halt_writes_stop_and_flattens(monkeypatch, tmp_path):
    # Treadmill guard: equity (cash + held cost) dropping > DAILY_LOSS_HALT_USD within the UTC
    # day writes STOP (operator must clear) and maker-flattens immediately. Mock balance=100,
    # held=25 -> equity 125; seed day-start at 150 -> drop 25 > 20 -> HALT.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 20.0)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)          # isolate the daily-kill mechanism
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T2", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    # equity_basis "mark" seeded: the Q1 build (2026-07-28) marks held inventory to book and
    # re-baselines ONCE on the basis change — a state file without the key is deliberately
    # re-baselined instead of halted, so this test seeds post-migration state to pin the meter.
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({"equity_day": q.utcnow().strftime("%Y%m%d"), "equity_day_start": 150.0,
                   "equity_basis": "mark"}, f)
    resting = [_order("a", "T1", "yes", 0.6, 10)]
    c = MockClient(mode="live", resting=resting,
                   positions=[{"ticker": "T1", "position_fp": "50",
                               "market_exposure_dollars": "25.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("daily_loss_halt") == 25.0
    assert os.path.exists(os.path.join(str(tmp_path), "STOP"))   # STOP written (sticky halt)
    assert "a" in c.cancelled                                    # flatten cancelled resting
    assert not any(cr["ticker"] == "T2" for cr in c.created)     # no new quoting after halt
    # fresh day -> baseline set, NO halt even though equity is far below yesterday's start
    os.remove(os.path.join(str(tmp_path), "STOP"))
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({"equity_day": "19990101", "equity_day_start": 500.0,
                   "equity_basis": "mark"}, f)
    c2 = MockClient(mode="live", resting=[],
                    positions=[{"ticker": "T1", "position_fp": "50",
                                "market_exposure_dollars": "15.00"}])
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert row2.get("daily_loss_halt") is None
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert st["equity_day"] == q.utcnow().strftime("%Y%m%d")     # re-baselined to today


def test_breaker_keeps_retained_reducing_side(monkeypatch, tmp_path):
    # Review new-bug: on a breaker cycle, a held ticker whose orderbook fetch failed had its
    # RETAINED standing (incl. the live unwind) cancelled. Now: the reducing side survives,
    # the accumulating side is still cancelled.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "HELD_MAX_USD", 20.0)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        raise ValueError("orderbook 502")   # transient fetch fail (RuntimeError = budget!)
    monkeypatch.setattr(q, "public_get", pg)
    resting = [_order("uw", "T1", "no", 0.40, 15),               # the live unwind (reducing)
               _order("acc", "T1", "yes", 0.55, 8)]              # stale accumulating join
    c = MockClient(mode="live", resting=resting,
                   positions=[{"ticker": "T1", "position_fp": "30",   # long yes, $25 > ceiling
                               "market_exposure_dollars": "25.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("breaker_reduce_only") == 1
    assert "acc" in c.cancelled                                  # accumulating side cancelled
    assert "uw" not in c.cancelled                               # reducing side SURVIVES


def test_strand_unwind_rests_at_the_touch_not_loss_capped(monkeypatch, tmp_path):
    # REWRITTEN 2026-07-28, Q1 operator decision: old pin (test_strand_unwind_is_loss_capped)
    # asserted the removed MAX_UNWIND_LOSS cap on the strand path (exit parked at 0.48 vs a
    # 0.85 book). The cap parked the exit BEHIND the touch on exactly the trended books where
    # the strand path fires; the contract is now: the strand exit rests AT the reference
    # whatever the cost basis. Long yes 30 @ cost 0.62; book no-ref 0.85 -> rest AT 0.85.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])   # STRAND path only
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        return {"orderbook_fp": {"yes_dollars": [["0.13", "500"]], "no_dollars": [["0.85", "500"]]}}
    monkeypatch.setattr(q, "public_get", pg)
    c = MockClient(mode="live", resting=[],
                   positions=[{"ticker": "STR", "position_fp": "30",
                               "market_exposure_dollars": "18.60"}])   # cost 0.62/ct
    _run(monkeypatch, c, str(tmp_path))
    unw = [o for o in c.created if o["ticker"] == "STR"]
    assert len(unw) == 1 and unw[0]["side"] == "no"
    assert unw[0]["price"] == 0.85                               # AT the touch — fillable
    assert unw[0]["count"] <= 30                                 # still never through flat


def test_env_clamps_footguns():
    # LATE_LIFE_FRAC >= 1 would zero the short-market universe; MAX_ENTRY_CUTOFF < WIND_DOWN
    # would violate gate ordering. Both clamp at import.
    import importlib.util as _ilu
    os.environ["KALSHI_LATE_LIFE_FRAC"] = "1.5"
    os.environ["KALSHI_MAX_ENTRY_CUTOFF_MIN"] = "5"
    os.environ["KALSHI_WIND_DOWN_MIN"] = "20"
    try:
        src = os.path.join(os.path.dirname(os.path.abspath(q.__file__)), "maker_kalshi_quoter.py")
        s = _ilu.spec_from_file_location("q_clamp_test", src)
        m = _ilu.module_from_spec(s); s.loader.exec_module(m)
        assert m.LATE_LIFE_FRAC == 0.9
        assert m.MAX_ENTRY_CUTOFF_MIN == 20.0
    finally:
        for k in ("KALSHI_LATE_LIFE_FRAC", "KALSHI_MAX_ENTRY_CUTOFF_MIN", "KALSHI_WIND_DOWN_MIN"):
            os.environ.pop(k, None)


def test_retained_reducing_survives_tight_capital_cap(monkeypatch, tmp_path):
    # Skeptic repro (07-22): a fetch-fail-RETAINED reducing quote was reason-less, so
    # cap_desired's unwind-keep did not protect it; at CANARY cap ($20) with the unwind book
    # already filling the cap, the ticker was dropped and the diff CANCELLED the live reducing
    # order. Now the retained reducing side is tagged 'unwind' at the source. NOTE the tight
    # totcap here -- the earlier test used totcap=200, which masked exactly this.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=20)          # canary-scale cap
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "FETCHFAIL", "usd_day": 1.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": "BIGUNWIND", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        if "FETCHFAIL" in p: raise ValueError("orderbook 502")   # transient fetch fail
        return {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]], "no_dollars": [["0.49", "9999"]]}}
    monkeypatch.setattr(q, "public_get", pg)
    resting = [_order("keepme", "FETCHFAIL", "no", 0.30, 20)]   # live reducing order, $6
    c = MockClient(mode="live", resting=resting, positions=[
        {"ticker": "FETCHFAIL", "position_fp": "20", "market_exposure_dollars": "6.00"},
        {"ticker": "BIGUNWIND", "position_fp": "40", "market_exposure_dollars": "18.00"}])
    _run(monkeypatch, c, str(tmp_path))
    assert "keepme" not in c.cancelled       # live reducing order NOT cancelled under tight cap


def test_breaker_cycle_is_not_systematic_failure(monkeypatch, tmp_path, capsys):
    # Observed live 07-22 03:35Z: a reduce-only cycle with a flat footprint quoted 0 markets and
    # printed "WARNING systematic failure" — crying wolf on a working guard. A breaker cycle must
    # report as 'cycle ok' (the breaker's own WARNING already says what happened).
    _cfg(monkeypatch, join=20, mktcap=250, totcap=20)
    monkeypatch.setattr(q, "HELD_MAX_USD", 20.0)             # held below trips level breaker
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "FLAT1", "usd_day": 10.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    # held position sits on an ALREADY-CLOSED market (the live 07-22 03:35Z shape): its book is
    # unfetchable, so the strand path rests nothing and the cycle legitimately quotes 0.
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        if "CLOSED" in p: raise ValueError("market closed")
        return {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]], "no_dollars": [["0.49", "9999"]]}}
    monkeypatch.setattr(q, "public_get", pg)
    c = MockClient(mode="live", resting=[], positions=[
        {"ticker": "CLOSED-MKT", "position_fp": "60", "market_exposure_dollars": "29.31"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("breaker_reduce_only") == 1 and row.get("quoted_markets") == 0
    out = capsys.readouterr().out
    assert "systematic failure" not in out                   # no false alarm
    assert "cycle ok" in out and "REDUCE-ONLY" in out        # honest: ok + the breaker's warning


# ================= 07-22 LADDER SELF-HEDGE (pairing + escape hatch) =================
def test_ladder_pairing_floored_pairs():
    # +yes LOWER strike matched vs -no HIGHER strike = floored pair -> excluded from naked
    held = {"KXAAAGASD-26JUL22-4.050": 20.0, "KXAAAGASD-26JUL22-4.060": -20.0}
    naked = q.ladder_pairing(held)
    assert naked["KXAAAGASD-26JUL22-4.050"] == 0 and naked["KXAAAGASD-26JUL22-4.060"] == 0
    # WRONG direction (+yes HIGHER, -no LOWER) has NO floor -> never matched
    held2 = {"KXAAAGASD-26JUL22-4.060": 20.0, "KXAAAGASD-26JUL22-4.050": -20.0}
    naked2 = q.ladder_pairing(held2)
    assert naked2 == held2
    # partial: +30 low vs -20 high -> +10 naked low, 0 high
    held3 = {"KXAAAGASD-26JUL22-4.050": 30.0, "KXAAAGASD-26JUL22-4.060": -20.0}
    naked3 = q.ladder_pairing(held3)
    assert naked3["KXAAAGASD-26JUL22-4.050"] == 10.0 and naked3["KXAAAGASD-26JUL22-4.060"] == 0
    # temp T-prefix strikes parse; CROSS-event never pairs; unparseable untouched
    held4 = {"KXTEMPDCH-26JUL2214-T73.99": 10.0, "KXTEMPDCH-26JUL2215-T75.99": -10.0,
             "KXWEIRD-26JUL22-XYZ": 5.0}
    naked4 = q.ladder_pairing(held4)
    assert naked4 == held4                                   # different events + unparseable
    # pairing conserves the event's signed sum (event_deltas unchanged)
    assert sum(naked3.values()) == sum(held3.values())


def test_paired_positions_not_unwound_or_takered(monkeypatch, tmp_path):
    # A fully-paired event must produce NO unwind quotes and NO settle-taker crossing.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0); monkeypatch.setattr(q, "TAKER_FLATTEN", True)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])   # strand path would fire
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        if p.endswith("/orderbook"): return _BOOK
        return {"market": {"close_time": "2000-01-01T00:00:00Z"}}       # already settling!
    monkeypatch.setattr(q, "public_get", pg)
    c = MockClient(mode="live", resting=[], positions=[
        {"ticker": "KXAAAGASD-26JUL22-4.050", "position_fp": "20", "market_exposure_dollars": "9.00"},
        {"ticker": "KXAAAGASD-26JUL22-4.060", "position_fp": "-20", "market_exposure_dollars": "8.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("taker_flattens", 0) == 0 and not c.crosses   # floored pair rides settlement
    assert not c.created                                          # no strand unwinds either


def test_ladder_hatch_never_fires_when_the_exit_rests_at_the_touch(monkeypatch, tmp_path):
    # REWRITTEN 2026-07-28, Q1 operator decision: old pin (test_ladder_escape_hatch_splits_
    # parked_unwind) asserted the hatch splitting a PARKED unwind — an unwind held below the
    # touch by the removed MAX_UNWIND_LOSS cap. Parked unwinds no longer exist: _unwind_price
    # returns the reference unconditionally, so the hatch's own trigger ("price < touch") can
    # never arm on this path. This pins that consequence on the SAME fixture: the exit rests
    # AT the touch at FULL size (not halved into lanes) and no cross lane is planned. If a
    # price cap ever comes back, the unwind parks again, the hatch fires, and ladder_cross==1
    # fails this test.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "KXAAAGASD-26JUL22-4.050", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": "KXAAAGASD-26JUL22-4.060", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        if "4.050" in p:   # collapsed book: yes 0.13 / no 0.85
            return {"orderbook_fp": {"yes_dollars": [["0.13", "500"]], "no_dollars": [["0.85", "500"]]}}
        return {"orderbook_fp": {"yes_dollars": [["0.06", "500"]], "no_dollars": [["0.92", "500"]]}}
    monkeypatch.setattr(q, "public_get", pg)
    c = MockClient(mode="live", resting=[], positions=[
        {"ticker": "KXAAAGASD-26JUL22-4.050", "position_fp": "20", "market_exposure_dollars": "12.40"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("ladder_cross") is None                    # never armed: nothing is parked
    ups = [o for o in c.created if o["ticker"].endswith("4.050") and o["side"] == "no"]
    assert len(ups) == 1 and ups[0]["price"] == 0.85          # AT the touch, expensive basis or not
    assert ups[0]["count"] == 20                              # FULL |naked| — not halved into lanes
    # cheap basis, same book: identical shape (the basis cannot move the exit price at all)
    c2 = MockClient(mode="live", resting=[], positions=[
        {"ticker": "KXAAAGASD-26JUL22-4.050", "position_fp": "20", "market_exposure_dollars": "2.00"}])
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert row2.get("ladder_cross") is None
    ups2 = [o for o in c2.created if o["ticker"].endswith("4.050") and o["side"] == "no"]
    assert len(ups2) == 1 and ups2[0]["price"] == 0.85


# ============ 07-22 MASKING AUDIT fixes (honest failure reporting) ============
def test_failed_taker_not_counted_as_success(monkeypatch, tmp_path):
    # HIGH (masking audit): flatten_to_zero's result was discarded — a FAILED flatten (book
    # unreadable / IOCs rejected) still counted a flatten, popped the ticker (so NO passive
    # unwind rested) and suppressed sysfail. Now: failure keeps the position for maker unwind,
    # counts taker_failed, and raises the alarm.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0); monkeypatch.setattr(q, "TAKER_FLATTEN", True)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        if p.endswith("/orderbook"): raise ValueError("book unreadable")   # flatten CANNOT work
        return {"market": {"close_time": "2000-01-01T00:00:00Z"}}          # near settle -> try
    monkeypatch.setattr(q, "public_get", pg)
    c = MockClient(mode="live", resting=[], positions=[
        {"ticker": "T1", "position_fp": "20.00", "market_exposure_dollars": "12.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("taker_flattens", 0) == 0        # NOT counted as a successful flatten
    assert row.get("taker_failed", 0) == 1          # counted honestly as a failure
    assert not c.crosses                            # nothing crossed (book unreadable)


def test_empty_footprint_is_flagged(monkeypatch, tmp_path, capsys):
    # HIGH (masking audit): programs existed but ALL were dropped -> footprint=0 printed
    # 'cycle ok' forever while the bot was functionally dead. Now: sysfail + drop reasons.
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXONLYTHIS"])
    prog = {"market_ticker": "KXOTHER-26JUL22-T1", "incentive_type": "liquidity",
            "target_size_fp": 1000, "discount_factor_bps": 5000, "period_reward": 100000,
            "start_date": "2026-07-22T00:00:00Z", "end_date": "2099-01-01T00:00:00Z"}
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [prog], "next_cursor": ""}
                        if "incentive" in p else {"orderbook_fp": {}})
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("footprint") == 0 and row.get("programs_seen") == 1
    assert row.get("drop_allowlist") == 1                    # WHY it emptied is recorded
    assert "systematic failure" in capsys.readouterr().out   # dead selection now alarms


def test_balance_read_failure_is_loud(monkeypatch, tmp_path, capsys):
    # HIGH (masking audit): the balance read was the only fail-OPEN guard input — failure
    # silently disarmed the daily-loss kill with no print. Now it warns + tracks a streak.
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""})
    class NoBalance(MockClient):
        def get_balance(self): raise RuntimeError("balance 503")
    c = NoBalance(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert "balance_read_failed" in row and row.get("balance_fail_streak") == 1
    assert "DAILY LOSS KILL DISARMED" in capsys.readouterr().out


def test_strike_parse_preserves_negative_sign():
    # Ladder review (07-22): last-dash-field parsing returned +0.4 for the live ticker shape
    # KXCPI-26SEP-T-0.4, inverting strike ORDER — which marks a genuinely UNFLOORED combo as
    # paired and strips every guard from it. Sub-zero-F winter temp strikes are the live risk.
    assert q._strike_of("KXCPI-26SEP-T-0.4") == -0.4
    assert q._strike_of("KXTEMPCHIH-27JAN1507-T-4.01") == -4.01
    assert q._strike_of("KXAAAGASD-26JUL22-4.055") == 4.055      # positives unchanged
    assert q._strike_of("KXTEMPNYCH-26JUL2117-T81.99") == 81.99
    assert q._strike_of("KXWEIRD-26JUL22") is None and q._strike_of(None) is None
    # UNFLOORED winter combo (+yes on the HIGHER strike) must stay NAKED = guards stay on
    bad = {"KXTEMPCHIH-27JAN1507-T-4.01": 10.0, "KXTEMPCHIH-27JAN1507-T-9.01": -10.0}
    assert q.ladder_pairing(bad) == bad
    # FLOORED winter combo (+yes on the LOWER strike) still pairs correctly
    good = {"KXTEMPCHIH-27JAN1507-T-9.01": 10.0, "KXTEMPCHIH-27JAN1507-T-4.01": -10.0}
    assert all(v == 0 for v in q.ladder_pairing(good).values())


def test_touch_exit_exempt_from_tight_cap_while_adjacent_join_is_gated(monkeypatch, tmp_path):
    # REWRITTEN 2026-07-28, Q1 operator decision: old pin (test_ladder_cross_is_capital_gated_
    # not_unwind_exempt) drove the hatch's 'ladder' cross lane under a tight cap — reachable
    # only through a PARKED unwind, which the removed MAX_UNWIND_LOSS cap created. With exits
    # unconditionally AT the touch the hatch cannot arm, so the polarity split this fixture
    # still proves is: under a $1 total cap, the true reducing exit on the collapsed 4.050
    # book is STILL placed (unwind-exempt) while the flat 4.060 sibling's accumulating join
    # is capital-gated to nothing.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=1)      # cap so tight nothing accumulating fits
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "KXAAAGASD-26JUL22-4.050", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": "KXAAAGASD-26JUL22-4.060", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    def pg(p):
        if "incentive" in p: return {"incentive_programs": [], "next_cursor": ""}
        if "4.050" in p:
            return {"orderbook_fp": {"yes_dollars": [["0.13", "500"]], "no_dollars": [["0.85", "500"]]}}
        return {"orderbook_fp": {"yes_dollars": [["0.06", "500"]], "no_dollars": [["0.92", "500"]]}}
    monkeypatch.setattr(q, "public_get", pg)
    c = MockClient(mode="live", resting=[], positions=[
        {"ticker": "KXAAAGASD-26JUL22-4.050", "position_fp": "20", "market_exposure_dollars": "12.40"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("ladder_cross") is None                 # hatch cannot arm: nothing is parked
    crossed = [o for o in c.created if o["ticker"].endswith("4.060")]
    assert not crossed                                     # flat sibling fully capital-gated
    exits = [o for o in c.created if o["ticker"].endswith("4.050") and o["side"] == "no"]
    assert len(exits) == 1 and exits[0]["price"] == 0.85   # the reducing exit still exempt, at touch
    assert len(c.created) == 1                             # ...and it is the ONLY order placed


def test_reward_qualification_gate(monkeypatch):
    # CFTC Feb-2026 LIP amendment (verified 07-22): a snapshot is EXCLUDED unless BOTH sides
    # can meet Target Size — so quoting a one-sided book earns $0 while still taking fill risk.
    # Live probe found 5 of 8 of our own allowlisted programs with one side at ZERO depth.
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 15.0)   # deployed: can add ~30 ct, not 1000
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    m = {"target": 1000, "end": "2099-01-01T00:00:00Z"}
    deep_y = [["0.50", "6000"]]; thin_n = [["0.49", "5"]]       # 5 ct + ~30 addable << 1000
    st = {}
    assert q.desired_quotes(m, deep_y, thin_n, q.utcnow(), inv=0.0, stats=st) == []
    assert st.get("unqualifiable") == 1
    # both sides deep enough -> quotes normally
    deep_n = [["0.49", "6000"]]
    assert q.desired_quotes(m, deep_y, deep_n, q.utcnow(), inv=0.0)
    # HELD inventory still unwinds even in an unqualifiable market (de-risk is never reward-gated)
    out = q.desired_quotes(m, deep_y, thin_n, q.utcnow(), inv=20.0)
    assert out and all(x["reason"] == "unwind" for x in out)


def test_throttle_step_ticks_knob(monkeypatch):
    # Reward economics (CFTC-verified): score = 0.50^(ticks from reference) x size, so the
    # throttle's 1-tick step HALVES that side's credit (or zeroes it if the book already meets
    # Target Size above). Knob lets us A/B it; default 1 preserves existing behaviour exactly.
    # SETUP REWRITTEN 2026-07-28 (Q1): per-ticker inventory can no longer reach the throttle
    # (holding => exit-only), so the knob is exercised through its surviving driver — the
    # EVENT-delta throttle on a FLAT ticker inside a directional event.
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "THROTTLE_STEP_TICKS", 1)          # default
    qs = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=0.0, event_delta=50.0)}
    assert qs["yes"]["price_dollars"] == 0.49                 # 1 tick inside best (0.50)
    monkeypatch.setattr(q, "THROTTLE_STEP_TICKS", 0)          # A/B: stay at reference
    qs0 = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=0.0, event_delta=50.0)}
    assert qs0["yes"]["price_dollars"] == 0.50                # full 1.0x credit
    assert qs0["yes"]["count"] == qs["yes"]["count"]          # size throttle unchanged either way
    assert qs0["no"]["price_dollars"] == 0.49                 # non-accumulating side unaffected


def test_breakers_measure_naked_not_gross(monkeypatch, tmp_path):
    # Live 07-22: $27.76 held of which $11.84 was a FLOORED ladder pair. Gating the risk
    # breakers on GROSS pinned the bot in reduce-only over risk it does not carry, blocking all
    # reward earning. Breakers now measure NAKED cost; the CAPITAL cap still uses gross.
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    # +10 on the LOWER strike vs -10 on the HIGHER = fully floored -> zero naked risk
    held = {"KXAAAGASD-26JUL23-4.090": 10.0, "KXAAAGASD-26JUL23-4.100": -10.0}
    cost = {"KXAAAGASD-26JUL23-4.090": 0.83, "KXAAAGASD-26JUL23-4.100": 0.58}
    assert q.naked_held_cost(held, cost) == 0.0
    # an unpaired position is measured at its real cost basis
    held2 = {"KXAAAGASD-26JUL23-4.095": 20.0}
    assert abs(q.naked_held_cost(held2, {"KXAAAGASD-26JUL23-4.095": 0.73}) - 14.6) < 1e-9
    # missing cost field -> conservative |naked| x $1 fallback, never understated
    assert q.naked_held_cost(held2, {}) == 20.0
    # end-to-end: fully-paired inventory far above HELD_MAX must NOT trip the breaker
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "HELD_MAX_USD", 5.0); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    c = MockClient(mode="live", resting=[], positions=[
        {"ticker": "KXAAAGASD-26JUL23-4.090", "position_fp": "10", "market_exposure_dollars": "8.30"},
        {"ticker": "KXAAAGASD-26JUL23-4.100", "position_fp": "-10", "market_exposure_dollars": "5.80"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("held_cost_usd") == 14.1          # gross unchanged (capital accounting)
    assert row.get("naked_held_usd") == 0.0          # risk correctly measured as zero
    assert row.get("breaker_reduce_only") is None    # so it does NOT sit out


def test_throttle_skips_step_when_it_would_zero_credit(monkeypatch):
    # Sandbox A/B (n=612): the 1-tick step ZEROED reward credit in 12% of snapshots — when the
    # depth AT the best price already meets Target Size, the qualifying walk stops there and a
    # quote one tick back is outside the scored set. Free win: stay at reference in exactly that
    # case and take the risk reduction from SIZE instead.
    # SETUP REWRITTEN 2026-07-28 (Q1): driven via the EVENT-delta throttle on a FLAT ticker
    # (per-ticker inventory is exit-only and can no longer reach the throttle).
    monkeypatch.setattr(q, "THROTTLE_STEP_TICKS", 1); monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "THROTTLE_SMART", True)   # opt-in; DEFAULT OFF in production
    # top level ALONE meets target(1000) -> stepping back scores zero -> stay at ref, shrink more
    fat = [["0.50", "5000"]]
    m = {"target": 1000, "end": "2099-01-01T00:00:00Z"}
    qs = {x["side"]: x for x in q.desired_quotes(m, fat, [["0.49", "5000"]], q.utcnow(),
                                                 inv=0.0, event_delta=50.0)}
    assert qs["yes"]["price_dollars"] == 0.50                 # AT reference (full credit kept)
    # thin top level -> a tick back is still inside the qualifying set -> step as before
    thin_top = [["0.50", "50"], ["0.49", "5000"]]
    qs2 = {x["side"]: x for x in q.desired_quotes(m, thin_top, [["0.49", "5000"]], q.utcnow(),
                                                  inv=0.0, event_delta=50.0)}
    assert qs2["yes"]["price_dollars"] == 0.49                # stepped (unchanged behaviour)
    # the risk brake survives either way: throttled size is below the un-throttled join
    flat = {x["side"]: x for x in q.desired_quotes(m, fat, [["0.49", "5000"]], q.utcnow(), inv=0.0)}
    assert qs["yes"]["count"] < flat["yes"]["count"]


def test_ladder_invariants_flagged_live(monkeypatch, tmp_path, capsys):
    # Live stress test: pairing must never flip a sign, never report naked>held, and must
    # conserve each event's signed sum. A violation must be LOUD and counted, not silent.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    # stub must accept the optional stats arg added 2026-07-23 (ladder_pairing(held, stats=None))
    monkeypatch.setattr(q, "ladder_pairing",
                        lambda h, stats=None: {t: -v for t, v in h.items()})           # sign flip
    c = MockClient(mode="live", resting=[], positions=[
        {"ticker": "KXAAAGASD-26JUL23-4.090", "position_fp": "10", "market_exposure_dollars": "8.30"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("ladder_violation", 0) >= 1
    assert "LADDER INVARIANT VIOLATED" in capsys.readouterr().out


def test_reduce_only_holds_exit_only_and_pulls_flat_markets(monkeypatch, tmp_path):
    # REWRITTEN 2026-07-28, Q1 operator decision: old pin (test_reduce_only_keeps_market_two_
    # sided) asserted the removed KALSHI_REDUCE_ONLY_KEEP_BOTH plug-in — a floor-sized
    # accumulating quote kept resting on a HELD market under the breaker so the snapshot still
    # earned. That is the accumulating re-post defect wearing a reward argument (KXDXYDUD ran
    # -20 -> +17 THROUGH flat under reduce-only, live 07-27). The pin is now the OPPOSITE:
    # under the breaker a held market keeps ONLY its reducing quote (one-sided, forfeiting the
    # snapshot — by design) and a flat market is fully pulled. A second side reappearing on
    # the held market, at any size, must fail here.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0); monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "HELD_MAX_USD", 5.0)      # force the breaker on
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": "T2", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    pos = [{"ticker": "T1", "position_fp": "20", "market_exposure_dollars": "12.00"}]
    c = MockClient(mode="live", resting=[], positions=list(pos))
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("breaker_reduce_only") == 1
    assert {o["side"] for o in c.created} == {"no"}                # the exit quote and NOTHING else
    assert all(o["ticker"] == "T1" for o in c.created)             # flat T2 fully pulled
    assert len(c.created) == 1
    assert row.get("one_sided_markets") == 1 and row.get("two_sided_markets") == 0


# ================= MIRROR SYMMETRY (operator guardrail 2026-07-23) =================
# Every quoting decision must behave IDENTICALLY for a long-NO position as for the mirrored
# long-YES one. Kalshi is symmetric (a NO bid at p is a YES ask at 1-p), so any asymmetry is a
# BUG — and a polarity bug is the worst kind here: it would throttle the wrong side, or unwind
# in the direction that GROWS the position. This asserts the property directly rather than
# spot-checking one polarity, so it also guards every future change to the shaping logic.
def _mirror(qs):
    """Map a quote list to what the mirrored book should produce: sides swapped."""
    return sorted((("no" if x["side"] == "yes" else "yes"), round(x["price_dollars"], 4),
                   x["count"], x.get("reason")) for x in qs)


def _plain(qs):
    return sorted((x["side"], round(x["price_dollars"], 4), x["count"], x.get("reason"))
                  for x in qs)


@pytest.mark.parametrize("inv,cost", [
    (0.0, 0.0),        # flat
    (5.0, 0.0),        # small position -> exit only
    (50.0, 0.0),       # above SOFT -> still exit only (same rule, any held size)
    (100.0, 0.0),      # at/above HARD -> still exit only
    (20.0, 0.62),      # deep-underwater exit -> rests at the touch (Q1: no loss cap)
    (2.0, 0.0),        # below tolerance
])
def test_mirror_symmetry_long_yes_vs_long_no(monkeypatch, inv, cost):
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0); monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "THROTTLE_STEP_TICKS", 1)
    m = {"target": 1, "end": "2099-01-01T00:00:00Z"}
    yl = [["0.55", "9999"]]; nl = [["0.42", "9999"]]
    a = q.desired_quotes(m, yl, nl, q.utcnow(), inv=inv, cost=cost)          # long yes
    b = q.desired_quotes(m, nl, yl, q.utcnow(), inv=-inv, cost=cost)         # mirrored long no
    assert _plain(a) == _mirror(b), f"polarity asymmetry at inv={inv} cost={cost}: {a} vs {b}"


def test_mirror_symmetry_event_delta_and_reduce_only(monkeypatch, tmp_path):
    # event-driven throttle must mirror too (flat ticker inside a directional event)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0); monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    m = {"target": 1, "end": "2099-01-01T00:00:00Z"}
    yl = [["0.55", "9999"]]; nl = [["0.42", "9999"]]
    a = q.desired_quotes(m, yl, nl, q.utcnow(), inv=0.0, event_delta=55.0)
    b = q.desired_quotes(m, nl, yl, q.utcnow(), inv=0.0, event_delta=-55.0)
    assert _plain(a) == _mirror(b)
    # ladder pairing must mirror: +low/-high floors, and so must -low/+high under sign flip
    lo, hi = "KXAAAGASD-26JUL23-4.050", "KXAAAGASD-26JUL23-4.060"
    fwd = q.ladder_pairing({lo: 10.0, hi: -10.0})     # floored -> fully paired
    rev = q.ladder_pairing({lo: -10.0, hi: 10.0})     # NOT floored -> untouched
    assert all(v == 0 for v in fwd.values())
    assert rev == {lo: -10.0, hi: 10.0}


# ============ 2026-07-23 defect fixes: Q1 categorical netting, Q2 strike parse, Q3 loss meter ====

class _BalClient(MockClient):
    """MockClient with a settable cash balance (the daily-loss meter's numerator)."""
    def __init__(self, bal, **kw):
        super().__init__(**kw)
        self._bal = float(bal)
    def get_balance(self):
        return {"balance_dollars": f"{self._bal:.4f}"}


# ---- Q1: event_deltas must NOT net independent (categorical) risks ----
_CAT = {"KXWORDLE-26JUL23-HELLO": 20.0, "KXWORDLE-26JUL23-WORLD": -20.0}

def test_event_deltas_does_not_net_categorical_event():
    # PIN: a categorical series (independent word-binaries, strike_type custom) holds N
    # INDEPENDENT risks. Netting +20 of one against -20 of another reported FLAT while two
    # naked exposures were live. Pre-fix: {"KXWORDLE-26JUL23": 0.0}.
    ed = q.event_deltas(_CAT)
    assert 0.0 not in ed.values()                    # nothing may read FLAT while inventory is live
    assert q.event_delta_for(ed, "KXWORDLE-26JUL23-HELLO") == 20.0
    assert q.event_delta_for(ed, "KXWORDLE-26JUL23-WORLD") == -20.0

def test_event_deltas_still_nets_a_proven_threshold_ladder():
    # counter-pin: a genuine additive ladder MUST still aggregate (that is the whole point of
    # the signal) — the guard must not degrade every event to per-ticker.
    held = {"KXTEMPNYCH-26JUL2014-T81": 12.0, "KXTEMPNYCH-26JUL2014-T83": 18.0,
            "KXTEMPNYCH-26JUL2014-T85": 20.0}
    ed = q.event_deltas(held)
    assert ed == {"KXTEMPNYCH-26JUL2014": 50.0}
    assert q.event_delta_for(ed, "KXTEMPNYCH-26JUL2014-T81") == 50.0
    # a FLAT sibling strike inside the same proven ladder still inherits the aggregate
    assert q.event_delta_for(ed, "KXTEMPNYCH-26JUL2014-T99") == 50.0

def test_is_ladder_event_requires_numeric_and_distinct_strikes():
    assert q._is_ladder_event(["KXAAAGASD-26JUL23-4.050", "KXAAAGASD-26JUL23-4.060"])
    assert not q._is_ladder_event(["KXWORDLE-26JUL23-HELLO", "KXWORDLE-26JUL23-WORLD"])
    # ONE non-numeric member poisons the event: mixed => not provably additive => no netting
    assert not q._is_ladder_event(["KXAAAGASD-26JUL23-4.050", "KXAAAGASD-26JUL23-LATE"])
    # duplicate strikes => 'strike' is not the discriminator => not a ladder
    assert not q._is_ladder_event(["KXX-26JUL23-4.050", "KXX-26JUL23-4.050"])

def test_categorical_inventory_is_flagged_in_the_plan_row(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    c = MockClient(mode="live", resting=[], positions=[
        {"ticker": "KXWORDLE-26JUL23-HELLO", "position_fp": "20", "market_exposure_dollars": "10.00"},
        {"ticker": "KXWORDLE-26JUL23-WORLD", "position_fp": "-20", "market_exposure_dollars": "10.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("nonladder_events") == 1          # un-nettable event visible in telemetry
    assert row.get("strike_parse_failed") == 2       # and the unpairable inventory is LOUD


# ---- Q2: _strike_of must parse the real ticker shapes, and fail LOUDLY ----
def test_strike_of_parses_legacy_region_qualified_ticker():
    # PIN: legacy 4-part gas ticker. Pre-fix float("US-4.00") raised -> None -> ladder pairing
    # went DARK SILENTLY on 100% of that inventory.
    assert q._strike_of("KXAAAGASM-25MAR31-US-4.00") == 4.00
    assert q._strike_of("KXAAAGASM-25MAR31-US-T-1.50") == -1.50   # qualifier + signed T-strike
    # regressions (pass on BOTH builds — regression guards, not pins):
    assert q._strike_of("KXCPI-26SEP-T-0.4") == -0.4              # sign must survive
    assert q._strike_of("KXAAAGASD-26JUL22-4.055") == 4.055
    # categorical strikes must STAY None (never force a numeric reading)
    assert q._strike_of("KXWORDLE-26JUL23-HELLO") is None
    assert q._strike_of("KXNBAFINALS-26-LAL") is None

def test_ladder_pairing_works_on_legacy_region_qualified_tickers():
    # PIN: +low / -high on the legacy shape is a FLOORED pair and must pair to zero.
    held = {"KXAAAGASM-25MAR31-US-4.00": 10.0, "KXAAAGASM-25MAR31-US-4.50": -10.0}
    assert all(v == 0 for v in q.ladder_pairing(held).values())

def test_strike_parse_failure_is_counted_not_silent():
    # PIN: the counter is the ONLY way a fully-dark pairing pass is distinguishable from
    # "no pairs were available".
    stats = {}
    naked = q.ladder_pairing({"KXWORDLE-26JUL23-HELLO": 5.0,
                              "KXAAAGASD-26JUL23-4.050": 5.0}, stats)
    assert stats.get("strike_parse_failed") == 1     # only the categorical one
    assert naked["KXWORDLE-26JUL23-HELLO"] == 5.0    # and it stays fully naked, by design
    # zero-position tickers are not counted (no inventory => nothing went dark)
    stats2 = {}
    q.ladder_pairing({"KXWORDLE-26JUL23-HELLO": 0.0}, stats2)
    assert stats2 == {}


# ---- Q3: the daily-loss quota must measure an UNINFLATABLE drawdown ----
def _q3cfg(monkeypatch):
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 20.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else
                        {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]], "no_dollars": [["0.49", "9999"]]}})

def test_daily_loss_measures_drawdown_from_intraday_high_water_mark(monkeypatch, tmp_path):
    # PIN (defect a): reward credits inflated the numerator while day-start stayed FROZEN, so
    # room grew monotonically all day. Measured live: equity 99.76 vs day_start 63.34 => the
    # halt only tripped at 23.34 on an $85 account. Post-fix the quota is a DRAWDOWN.
    _q3cfg(monkeypatch)
    d = str(tmp_path)
    assert _run(monkeypatch, _BalClient(100.0, mode="live"), d).get("daily_loss_halt") is None
    assert _run(monkeypatch, _BalClient(150.0, mode="live"), d).get("daily_loss_halt") is None
    row = _run(monkeypatch, _BalClient(125.0, mode="live"), d)
    # pre-fix: day_start=100, equity=125 -> "up $25" -> NO halt, with $45 of extra room banked.
    assert row.get("daily_loss_halt") == 25.0
    assert os.path.exists(os.path.join(d, "STOP"))

def test_daily_loss_cumulative_downticks_survive_a_deposit(monkeypatch, tmp_path):
    # PIN (defect c): a mid-day deposit used to add room 1:1 (and, under a naive high-water
    # mark alone, would also FORGIVE the drawdown already taken). The cumulative adverse-move
    # meter only ever counts DOWN moves, so income and deposits cannot pay it back.
    _q3cfg(monkeypatch)
    d = str(tmp_path)
    assert _run(monkeypatch, _BalClient(100.0, mode="live"), d).get("daily_loss_halt") is None
    assert _run(monkeypatch, _BalClient(85.0, mode="live"), d).get("daily_loss_halt") is None   # -15
    assert _run(monkeypatch, _BalClient(200.0, mode="live"), d).get("daily_loss_halt") is None  # deposit
    row = _run(monkeypatch, _BalClient(190.0, mode="live"), d)                                  # -10
    assert row.get("daily_loss_halt") == 25.0        # 15 + 10, NOT reset by the +115 inflow
    assert row.get("daily_down") == 25.0 and row.get("daily_dd") == 10.0

def test_daily_loss_legacy_state_file_still_halts_on_day_start_drop(monkeypatch, tmp_path):
    # migration guard: a state file with no peak/prev keys (the pre-Q3 shape). The peak
    # must seed from equity_day_start so the old drop-from-day-start behaviour is a FLOOR, never
    # weaker. equity_basis "mark" is seeded because the Q1 build (2026-07-28) deliberately
    # re-baselines ONCE when the key is absent (cost->mark meter migration) — that one-time
    # re-baseline is intended behaviour, not the subject here; the subject is peak-seeding.
    _q3cfg(monkeypatch)
    d = str(tmp_path)
    with open(os.path.join(d, "quoter_state.json"), "w") as f:
        json.dump({"equity_day": q.utcnow().strftime("%Y%m%d"), "equity_day_start": 150.0,
                   "equity_basis": "mark"}, f)
    row = _run(monkeypatch, _BalClient(100.0, mode="live"), d)
    assert row.get("daily_loss_halt") == 50.0


# ---- findings NOT fixed here (design changes needing their own review) ----
@pytest.mark.xfail(strict=True, reason="FINDING 1: a floored pair's real downside is the STRIKE "
                                       "GAP x contracts, and naked_held_cost reports it as $0 — "
                                       "a wide-gap pair carries real risk no breaker can see")
def test_finding_paired_downside_invisible_to_naked_held_cost():
    lo, hi = "KXAAAGASD-26JUL23-4.00", "KXAAAGASD-26JUL23-6.00"      # $2.00 strike gap
    held = {lo: 10.0, hi: -10.0}
    cost = {lo: 0.60, hi: 0.55}
    assert q.naked_held_cost(held, cost) >= 10 * 2.00

@pytest.mark.xfail(strict=True, reason="FINDING 2: a fully MATCHED pair on a ticker whose program "
                                       "has expired (dropped from the footprint) has naked=0, so "
                                       "the strand-unwind and the settle-taker both skip it — no "
                                       "exit path exists if the legs stop being jointly exitable")
def test_finding_no_exit_path_for_matched_pair_off_footprint(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9); monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])       # program expired
    c = MockClient(mode="live", resting=[], positions=[
        {"ticker": "KXAAAGASD-26JUL23-4.00", "position_fp": "10", "market_exposure_dollars": "6.00"},
        {"ticker": "KXAAAGASD-26JUL23-6.00", "position_fp": "-10", "market_exposure_dollars": "5.50"}])
    _run(monkeypatch, c, str(tmp_path))
    assert c.created, "no exit order was emitted for the stranded matched pair"

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

def test_long_yes_throttles_yes_keeps_no_at_ref(monkeypatch):
    # long yes above SOFT on THIS ticker -> YES (its own accumulating side) skewed 1 tick inside
    # + shrunk; NO (reducing) grows at reference as the passive unwind. Direction is per-ticker,
    # so this holds even with event_delta defaulting to 0.
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    qs = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=50.0)}
    assert qs["yes"]["price_dollars"] == 0.49          # 1 tick inside (0.50 - 0.01)
    assert qs["yes"]["reason"] == "join"
    assert qs["no"]["price_dollars"] == 0.49           # reducing side stays at ref (maker unwind)
    assert qs["no"]["reason"] == "unwind"
    assert qs["yes"]["count"] < qs["no"]["count"]      # accumulating shrunk, reducing grown
    assert qs["no"]["count"] >= 50                     # reducing side grown toward |inv|=50 to flatten

def test_long_yes_between_soft_and_hard_keeps_yes_live(monkeypatch):
    # Below HARD a side is never pulled to zero (quotes are the paycheck): the accumulating YES
    # shrinks toward the MIN_QUOTE floor + steps 1 tick inside, but stays LIVE.
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    qs = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=50.0)}
    assert "yes" in qs and qs["yes"]["count"] >= 2     # live at/above the floor
    assert qs["yes"]["price_dollars"] == 0.49          # stepped 1 tick inside so it fills last
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
    assert nearu["no"]["reason"] == "unwind" and nearu["no"]["count"] == 20

def test_long_no_mirror(monkeypatch):
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    qs = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=-50.0)}
    assert qs["no"]["price_dollars"] == 0.48           # NO (this ticker's accumulating) -> 1 tick inside
    assert qs["yes"]["price_dollars"] == 0.50          # YES reducing -> at ref
    assert qs["yes"]["reason"] == "unwind" and qs["no"]["reason"] == "join"
    assert qs["no"]["count"] < qs["yes"]["count"]

def test_event_delta_throttles_when_ticker_below_soft(monkeypatch):
    # THE ACCUMULATION FIX: this ticker is under SOFT (inv=20) so per-ticker alone would NOT
    # throttle — but the EVENT aggregate is over SOFT, which must still throttle the accumulating
    # side (correlated 'above X' strikes each small, additive to a large directional short/long).
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    qs = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=20.0, event_delta=55.0)}
    assert qs["yes"]["price_dollars"] == 0.49          # event pushed us over SOFT -> throttle YES
    assert qs["no"]["reason"] == "unwind"              # our +20 still unwinds via NO
    # flat ticker in a directional event: throttle the event-accumulating side, no unwind created
    qs2 = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=0.0, event_delta=-55.0)}
    assert qs2["no"]["price_dollars"] == 0.48          # event short -> throttle NO (accumulating)
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
    # room bound = FULL MAX_MARKET_CAPITAL now (review C6/C10: reducing side has no paired side to
    # share the per-market budget). Pinned with == (not <=): the old /2 room returned 126 here, so
    # a <= assertion would pass on BOTH builds and pin nothing.
    assert q._unwind_size(80, 0.99, 400) == int(250 / 0.99)         # 252 post-fix; 126 pre-fix
    assert q._unwind_size(80, 0.50, 200) == 200        # |inv| within room -> exactly the position
    # end-to-end: a +5 long-yes ticker rests a NO unwind of exactly 5 (no overshoot)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    qs = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=5.0)}
    assert qs["no"]["count"] == 5 and qs["no"]["reason"] == "unwind"

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
    # THE STUCK-BOT FIX: held inventory ALONE exceeds MAX_TOTAL_CAPITAL. The accumulating
    # (join) side is correctly gated, but the REDUCING (unwind) side must STILL be placed —
    # otherwise the bot can never flatten (cap blocks the only de-risking order).
    # (HELD ceiling disabled here to pin the committed-cap mechanism in ISOLATION — at defaults
    # the $50 held would trip the level breaker first, which handles this scenario earlier.)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    _cfg(monkeypatch, join=20, mktcap=250, totcap=10)     # cap $10, held will be ~$50
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[{"ticker": "T1", "position_fp": "50"}])  # long yes
    row = _run(monkeypatch, c, str(tmp_path))
    reds = [o for o in c.created if o["side"] == "no"]
    accs = [o for o in c.created if o["side"] == "yes"]
    assert len(reds) == 1                                 # reducing NO placed despite committed>cap
    assert len(accs) == 0                                 # accumulating YES gated by the cap
    assert row.get("create_skipped", 0) >= 1

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


def test_unwind_price_loss_capped(monkeypatch):
    # 2026-07-22 loss: unwind chased the ref and realized ~50c/pair. Cap: exit-side price never
    # rests above (1 - cost + MAX_UNWIND_LOSS); at/below the cap it rests at reference.
    monkeypatch.setattr(q, "MAX_UNWIND_LOSS", 0.10)
    assert q._unwind_price(0.85, 0.62) == 0.48     # cap 1-0.62+0.10 binds (was resting 0.85)
    assert q._unwind_price(0.30, 0.10) == 0.30     # cap 1.00 -> reference (normal bleed zone)
    assert q._unwind_price(0.49, 0.0) == 0.49      # unknown basis -> cap disabled (legacy)
    # end-to-end through desired_quotes: long yes at cost 0.62, book no-ref 0.49 -> capped 0.48
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0); monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    qs = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=20.0, cost=0.62)}
    assert qs["no"]["reason"] == "unwind" and qs["no"]["price_dollars"] == 0.48
    # and with a cheap basis the unwind still rests at reference (no behavior change)
    qs2 = {x["side"]: x for x in q.desired_quotes(_mkt(), _YL, _NL, q.utcnow(), inv=20.0, cost=0.10)}
    assert qs2["no"]["price_dollars"] == 0.49


def test_velocity_breaker_reduce_only(monkeypatch, tmp_path):
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
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as f:
        json.dump({"equity_day": q.utcnow().strftime("%Y%m%d"), "equity_day_start": 150.0}, f)
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
        json.dump({"equity_day": "19990101", "equity_day_start": 500.0}, f)
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


def test_strand_unwind_is_loss_capped(monkeypatch, tmp_path):
    # Review test-weak (MED): the incident path is the STRAND unwind; pin its loss cap.
    # Long yes 30 @ cost 0.62; book no-ref 0.85 -> capped at 1-0.62+0.10=0.48, NOT 0.85.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "MAX_UNWIND_LOSS", 0.10)
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
    assert unw[0]["price"] == 0.48                               # capped, not chased to 0.85


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

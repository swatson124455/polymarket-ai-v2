"""Offline tests for the Kalshi order client (auth + safety locks) and the
quoter's pure planning functions. No network; loads via importlib."""
import base64
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

import pytest

_S = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _S / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # quoter imports client by name
    spec.loader.exec_module(mod)
    return mod


kc = _load("maker_kalshi_client")
kq = _load("maker_kalshi_quoter")


# ---------------- client: auth ----------------

def _throwaway_key(tmp):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_key_bytes if False else key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    path = os.path.join(tmp, "k.pem")
    with open(path, "wb") as f:
        f.write(pem)
    return key, path


def test_signature_verifies_and_message_shape():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    with tempfile.TemporaryDirectory() as tmp:
        key, path = _throwaway_key(tmp)
        auth = kc.KalshiAuth("key-id-1", path)
        sig = auth.sign("post", "/trade-api/v2/portfolio/orders", 1752800000000)
        # exact message per docs: "{ts}{METHOD}{path}", method uppercased
        msg = b"1752800000000POST/trade-api/v2/portfolio/orders"
        key.public_key().verify(
            base64.b64decode(sig), msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256())  # raises on mismatch


def test_auth_headers_shape():
    with tempfile.TemporaryDirectory() as tmp:
        _, path = _throwaway_key(tmp)
        h = kc.KalshiAuth("kid", path).headers("GET", "/trade-api/v2/portfolio/balance")
        assert set(h) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-SIGNATURE",
                          "KALSHI-ACCESS-TIMESTAMP", "Content-Type"}
        assert h["KALSHI-ACCESS-KEY"] == "kid"
        assert h["KALSHI-ACCESS-TIMESTAMP"].isdigit()


# ---------------- client: safety locks ----------------

def test_dry_run_is_default_and_records_intents(monkeypatch):
    monkeypatch.delenv("KALSHI_TRADING_MODE", raising=False)
    c = kc.KalshiOrderClient()
    assert c.mode == "dry_run"
    r = c.create_quote("KXT-1", "yes", 0.42, 100)
    assert r["dry_run"] is True
    b = c.intents[0]["body"]
    assert b["side"] == "bid" and b["price"] == "0.4200" and b["post_only"] is True
    # batch_cancel now loops the verified single-cancel (V2), one intent per id
    r2 = c.batch_cancel(["a", "b"])
    assert len(r2["cancelled"]) == 2 and len(c.intents) == 3
    assert c.intents[1]["path"].endswith("/portfolio/events/orders/a")
    assert c.intents[2]["path"].endswith("/portfolio/events/orders/b")


def test_create_quote_no_side_maps_to_ask_complement(monkeypatch):
    monkeypatch.delenv("KALSHI_TRADING_MODE", raising=False)
    c = kc.KalshiOrderClient()
    # a NO bid at 0.30 must post as an ASK at the yes-scale price 0.70
    c.create_quote("KXT-1", "no", 0.30, 50)
    b = c.intents[0]["body"]
    assert b["side"] == "ask" and b["price"] == "0.7000" and b["count"] == "50"
    # a YES bid stays a bid at its own price
    c.create_quote("KXT-1", "yes", 0.61, 50)
    assert c.intents[1]["body"]["side"] == "bid" and c.intents[1]["body"]["price"] == "0.6100"


def test_batch_cancel_isolates_a_failure(monkeypatch):
    monkeypatch.delenv("KALSHI_TRADING_MODE", raising=False)
    c = kc.KalshiOrderClient()
    calls = []

    def flaky(oid):
        calls.append(oid)
        if oid == "bad":
            raise RuntimeError("404 already gone")
        return {"order_id": oid}
    monkeypatch.setattr(c, "cancel_order", flaky)
    r = c.batch_cancel(["a", "bad", "b"])
    # one bad id does NOT abort the rest — all three attempted
    assert calls == ["a", "bad", "b"]
    assert len(r["cancelled"]) == 2 and len(r["failed"]) == 1
    assert r["failed"][0]["order_id"] == "bad"


def test_cancel_uses_v2_events_path(monkeypatch):
    monkeypatch.delenv("KALSHI_TRADING_MODE", raising=False)
    c = kc.KalshiOrderClient()
    c.cancel_order("abc-123")
    it = c.intents[0]
    assert it["method"] == "DELETE"
    assert it["path"] == "/trade-api/v2/portfolio/events/orders/abc-123"


def test_v2_order_body_shape(monkeypatch):
    monkeypatch.delenv("KALSHI_TRADING_MODE", raising=False)
    c = kc.KalshiOrderClient()
    r = c.create_order_v2("KXT-1", "bid", 3, 0.42,
                          client_order_id="cid-1")
    b = r["intent"]["body"]
    assert r["intent"]["path"].endswith("/portfolio/events/orders")
    assert b["side"] == "bid" and b["count"] == "3" and b["price"] == "0.4200"
    assert b["self_trade_prevention_type"] == "taker_at_cross"
    assert b["time_in_force"] == "good_till_canceled"


def test_live_requires_arming_phrase(monkeypatch):
    monkeypatch.setenv("KALSHI_TRADING_MODE", "live")
    monkeypatch.delenv("KALSHI_LIVE_ARMED", raising=False)
    with pytest.raises(RuntimeError, match="KALSHI_LIVE_ARMED"):
        kc.KalshiOrderClient()
    monkeypatch.setenv("KALSHI_LIVE_ARMED", kc.LIVE_ARM_PHRASE)
    with pytest.raises(RuntimeError, match="credentials"):
        kc.KalshiOrderClient()   # armed but keyless still refuses


def test_demo_requires_credentials(monkeypatch):
    monkeypatch.setenv("KALSHI_TRADING_MODE", "demo")
    with pytest.raises(RuntimeError, match="demo mode requires"):
        kc.KalshiOrderClient()


def test_authed_read_without_creds_raises_not_sends(monkeypatch):
    monkeypatch.delenv("KALSHI_TRADING_MODE", raising=False)
    c = kc.KalshiOrderClient()
    with pytest.raises(RuntimeError, match="requires credentials"):
        c.get_balance()


# ---------------- quoter: planning ----------------

def M(end_min=600, target=1000.0, usd_day=100.0, t="KXQ-1"):
    from datetime import timedelta
    end = (kq.utcnow() + timedelta(minutes=end_min)).isoformat()
    return {"ticker": t, "target": target, "usd_day": usd_day, "end": end}


def test_join_on_healthy_market():
    q = kq.desired_quotes(M(), [(0.50, 2000)], [(0.49, 2000)], kq.utcnow())
    assert [x["reason"] for x in q] == ["join", "join"]
    assert q[0]["count"] == kq.JOIN_SIZE and q[0]["price_dollars"] == 0.50


def test_wind_down_gate():
    assert kq.desired_quotes(M(end_min=kq.WIND_DOWN_MIN - 5),
                             [(0.50, 2000)], [(0.49, 2000)], kq.utcnow()) == []


def test_activate_within_capital_cap():
    # cheap void market: yes 0.05x900 short of 1000, no 0.90x900
    q = kq.desired_quotes(M(), [(0.05, 900)], [(0.90, 900)], kq.utcnow())
    assert [x["reason"] for x in q] == ["activate", "activate"]
    assert q[0]["count"] == 100 and q[1]["count"] == 100  # max(JOIN, target-tot)


def test_activate_blocked_by_capital_cap():
    # expensive void: needs 800 at 0.50 both sides -> $800 >> cap
    assert kq.desired_quotes(M(), [(0.50, 200)], [(0.49, 200)], kq.utcnow()) == []


def test_spread_sanity_gate():
    assert kq.desired_quotes(M(), [(0.99, 2000)], [(0.005, 2000)], kq.utcnow()) == []


def test_unpriceable_side_gate():
    assert kq.desired_quotes(M(), [], [(0.49, 2000)], kq.utcnow()) == []


def test_footprint_selection_skips_ending_and_paramless():
    from datetime import timedelta
    now = kq.utcnow()
    soon = (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    far = (now + timedelta(days=3)).isoformat().replace("+00:00", "Z")
    start = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    progs = [
        {"market_ticker": "KXA-1", "period_reward": 1000000, "target_size_fp": "1000",
         "discount_factor_bps": 5000, "incentive_type": "liquidity",
         "start_date": start, "end_date": far},
        {"market_ticker": "KXA-2", "period_reward": 1000000, "target_size_fp": "1000",
         "discount_factor_bps": 5000, "incentive_type": "liquidity",
         "start_date": start, "end_date": soon},              # ends inside wind-down
        {"market_ticker": "KXA-3", "period_reward": 1000000,
         "incentive_type": "liquidity", "start_date": start, "end_date": far},  # paramless
    ]
    fp = kq.select_footprint(progs, now)
    assert [m["ticker"] for m in fp] == ["KXA-1"]


def test_diff_orders_cancel_create():
    standing = {"KXA-1": [{"side": "yes", "price_dollars": 0.50, "count": 100,
                           "order_id": "o1"},
                          {"side": "no", "price_dollars": 0.48, "count": 100,
                           "order_id": "o2"}]}
    desired = {"KXA-1": [{"side": "yes", "price_dollars": 0.50, "count": 100,
                          "reason": "join"},                       # unchanged -> keep
                         {"side": "no", "price_dollars": 0.49, "count": 100,
                          "reason": "join"}],                      # moved -> replace
               "KXB-1": [{"side": "yes", "price_dollars": 0.10, "count": 100,
                          "reason": "join"}]}                      # new market
    cancels, creates = kq.diff_orders(standing, desired)
    assert cancels == ["o2"]
    assert sorted(c["ticker"] for c in creates) == ["KXA-1", "KXB-1"]


def test_join_always_switch_places_on_void_market(monkeypatch):
    # drill switch: even a thin/void market (below target) gets a 1x join both sides
    monkeypatch.setattr(kq, "JOIN_ALWAYS", True)
    monkeypatch.setattr(kq, "JOIN_SIZE", 1)
    q = kq.desired_quotes(M(target=1000), [(0.50, 5)], [(0.49, 5)], kq.utcnow())
    assert [x["reason"] for x in q] == ["join", "join"]
    assert q[0]["count"] == 1 and q[1]["count"] == 1
    # still respects wind-down even with the switch on
    assert kq.desired_quotes(M(end_min=kq.WIND_DOWN_MIN - 5, target=1000),
                             [(0.5, 5)], [(0.49, 5)], kq.utcnow()) == []


def test_join_always_default_off():
    assert kq.JOIN_ALWAYS is False   # production default: economics gates apply


def test_diff_orders_full_exit():
    standing = {"KXA-1": [{"side": "yes", "price_dollars": 0.50, "count": 100,
                           "order_id": "o1"}]}
    cancels, creates = kq.diff_orders(standing, {})
    assert cancels == ["o1"] and creates == []


# ---------------- review-fix regressions ----------------

def test_price_lower_bound_gate():
    # sub-min-tick best bid on a NON-void, cheap book: only the MIN_PRICE gate can
    # reject it (the activate cap is not involved on a join) — so this isolates F17.
    # target=1 -> non-void -> join branch; best_y=0.005 <= MIN_PRICE_DOLLARS(0.01) -> []
    assert kq.desired_quotes(M(target=1), [(0.005, 5000)], [(0.49, 5000)], kq.utcnow()) == []
    # sanity: same book with a valid best price DOES quote (proves it's the price gate)
    assert kq.desired_quotes(M(target=1), [(0.20, 5000)], [(0.49, 5000)], kq.utcnow()) != []


def test_join_capital_capped_per_market(monkeypatch):
    # at a high price, 100 contracts would exceed the per-market $ cap -> count trimmed
    monkeypatch.setattr(kq, "MAX_MARKET_CAPITAL", 20.0)   # $10/side budget
    monkeypatch.setattr(kq, "JOIN_SIZE", 100)
    q = kq.desired_quotes(M(target=1), [(0.50, 9999)], [(0.49, 9999)], kq.utcnow())
    # $10/side / $0.50 = 20 contracts, well under 100
    assert q[0]["count"] == 20 and q[0]["count"] * 0.50 <= 10.0


def test_activate_sizes_against_external_depth_stable(monkeypatch):
    # our own 100 resting must NOT inflate tot -> add stays stable across cycles
    monkeypatch.setattr(kq, "JOIN_SIZE", 100)
    monkeypatch.setattr(kq, "MAX_ACTIVATE_CAPITAL", 10000.0)
    # book shows 900 external + our 100 = 1000 total; target 1000
    q1 = kq.desired_quotes(M(target=1000), [(0.50, 1000)], [(0.49, 1000)],
                           kq.utcnow(), own={"yes": 100, "no": 100})
    # external = 1000-100 = 900 < 1000 -> void, add = target - external = 100 (NOT 0/chasing)
    assert q1[0]["reason"] == "activate" and q1[0]["count"] == 100


def test_cap_desired_drops_lowest_value_markets(monkeypatch):
    monkeypatch.setattr(kq, "MAX_TOTAL_CAPITAL", 100.0)
    desired = {
        "KXA-1": [{"side": "yes", "price_dollars": 0.50, "count": 100}],   # $50
        "KXB-1": [{"side": "yes", "price_dollars": 0.60, "count": 100}],   # $60
        "KXC-1": [{"side": "yes", "price_dollars": 0.40, "count": 100}],   # $40
    }
    usd = {"KXA-1": 300, "KXB-1": 100, "KXC-1": 50}
    kept, dropped = kq.cap_desired(desired, usd)
    # keep highest usd_day within $100: KXA($50) then KXB($60)=$110 over -> drop KXB and KXC
    assert "KXA-1" in kept and dropped == 2
    assert sum(kq._mkt_capital(v) for v in kept.values()) <= 100.0


def test_bound_creates_whole_ticker_no_split(monkeypatch):
    monkeypatch.setattr(kq, "WRITE_BUDGET_PER_CYCLE", 2)
    creates = [
        {"ticker": "KXA-1", "side": "yes"}, {"ticker": "KXA-1", "side": "no"},
        {"ticker": "KXB-1", "side": "yes"}, {"ticker": "KXB-1", "side": "no"},
    ]
    usd = {"KXA-1": 300, "KXB-1": 100}
    kept, dropped = kq.bound_creates(creates, [], usd)
    # budget 2 -> keep KXA's BOTH sides (never split), drop KXB entirely
    assert {c["ticker"] for c in kept} == {"KXA-1"} and len(kept) == 2 and dropped == 1


def test_own_resting_aggregates():
    standing = {"KXA-1": [{"side": "yes", "count": 50, "order_id": "o1"},
                          {"side": "yes", "count": 30, "order_id": "o2"},
                          {"side": "no", "count": 10, "order_id": "o3"}]}
    o = kq.own_resting(standing)
    assert o["KXA-1"]["yes"] == 80 and o["KXA-1"]["no"] == 10


def test_levels_drops_malformed_and_counts():
    lv, malformed = kq._levels([["0.50", "5"], ["abc", "5"], ["0.40", None], [], ["0.30", "0"]])
    assert lv == [(0.50, 5.0)]   # only the one valid, positive-size level survives
    # 3 PARSE failures counted (abc / None / empty-row); the size-0 row is a legit
    # empty level, NOT counted as malformed -> systematic parse failure is visible
    assert malformed == 3


def test_desired_quotes_surfaces_dropped_book_rows():
    stats = {}
    kq.desired_quotes(M(target=1), [("0.50", "5000"), ["bad"]], [("0.49", "5000")],
                      kq.utcnow(), stats=stats)
    assert stats["dropped_book_rows"] == 1   # the malformed yes row was counted, not silent


def test_create_order_v2_rejected_status_raises(monkeypatch):
    # a 200 carrying status=rejected must raise, not be counted as placed
    monkeypatch.delenv("KALSHI_TRADING_MODE", raising=False)
    c = kc.KalshiOrderClient()
    monkeypatch.setattr(c, "_write", lambda *a, **k: {"order": {"status": "rejected"}})
    with pytest.raises(RuntimeError, match="not resting"):
        c.create_order_v2("KXT-1", "bid", 1, 0.42)
    # a resting order passes through
    monkeypatch.setattr(c, "_write", lambda *a, **k: {"order": {"status": "resting",
                                                                "order_id": "x"}})
    assert c.create_order_v2("KXT-1", "bid", 1, 0.42)["order"]["order_id"] == "x"


def test_order_id_for_unique_within_and_across_cycles():
    # exercises the ACTUAL generator the quoter uses (order_id_for), not an inline copy
    within = [kq.order_id_for(1000, i, s) for i in range(3) for s in ("yes", "no")]
    assert len(set(within)) == 6            # unique per (index, side) within a cycle
    # different cycle nonce -> different ids even at same index/side (no reuse)
    assert kq.order_id_for(1000, 0, "yes") != kq.order_id_for(1001, 0, "yes")
    # no ticker prefix -> two different long tickers can never collide (F3 root cause)
    assert "mk-1000-0-yes" == kq.order_id_for(1000, 0, "yes")

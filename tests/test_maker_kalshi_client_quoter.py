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
    r = c.create_order("KXT-1", "yes", "buy", 100, 0.42)
    assert r["dry_run"] is True
    assert c.intents[0]["body"]["yes_price"] == 42
    assert c.intents[0]["body"]["post_only"] is True
    r2 = c.batch_cancel(["a", "b"])
    assert r2["dry_run"] and len(c.intents) == 2


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


def test_diff_orders_full_exit():
    standing = {"KXA-1": [{"side": "yes", "price_dollars": 0.50, "count": 100,
                           "order_id": "o1"}]}
    cancels, creates = kq.diff_orders(standing, {})
    assert cancels == ["o1"] and creates == []

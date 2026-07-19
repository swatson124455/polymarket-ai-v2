#!/usr/bin/env python3
"""Kalshi venue ORDER CLIENT for the Maker lane — demo/prod switchable, DRY-RUN default.

SAFETY MODEL (three independent locks, ALL must open before a real order):
  1. mode: DRY_RUN (default) logs intended calls and sends nothing.
     KALSHI_TRADING_MODE=demo  -> demo environment (fake money)
     KALSHI_TRADING_MODE=live  -> production. Requires lock 3.
  2. credentials: without KALSHI_API_KEY_ID + KALSHI_RSA_PRIVATE_KEY_PATH the
     client is structurally unable to authenticate (operator creates these;
     sessions never handle raw key material).
  3. live arming: KALSHI_LIVE_ARMED must contain the literal string
     "operator-approved-live-pilot" for mode=live. A session cannot flip this
     silently; it is an explicit operator act.

Auth: RSA-PSS/SHA-256 over "{timestamp_ms}{METHOD}{path}" per
docs.kalshi.com/getting_started/api_keys. Signing core adapted from
sports/markets/kalshi_client.py (`b0c0da3`, SB lane) — same exchange, same
scheme; vendored to keep the Maker arm dependency-free of other lanes.

Sync urllib (arm-style); rate limiting is the QUOTER's job — this client only
enforces a floor spacing. Every request has a timeout. No retries on writes
(an ambiguous timeout on an order MUST surface, never auto-repeat).
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

# Recommended hosts (docs.kalshi.com/getting_started/api_environments, 2026-07-18).
# Legacy still works: prod api.elections.kalshi.com / demo demo-api.kalshi.co.
# Credentials are NOT shared across environments (demo keys only hit demo).
PROD_BASE = "https://external-api.kalshi.com"
DEMO_BASE = "https://external-api.demo.kalshi.co"
API_ROOT = "/trade-api/v2"

WRITE_SPACING_S = 0.12        # floor between writes (~8/s < Basic 10/s)
HTTP_TIMEOUT_S = 15
LIVE_ARM_PHRASE = "operator-approved-live-pilot"


class KalshiAuth:
    """RSA-PSS request signer. Lazily loads the PEM key; never logs key material."""

    def __init__(self, api_key_id, private_key_path):
        self.api_key_id = api_key_id
        self._path = private_key_path
        self._key = None

    def _load(self):
        if self._key is None:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            with open(self._path, "rb") as fh:
                self._key = load_pem_private_key(fh.read(), password=None)
        return self._key

    def sign(self, method, path, timestamp_ms):
        """base64(RSA-PSS-SHA256("{ts}{METHOD}{path}")) — path EXCLUDES query."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        message = f"{timestamp_ms}{method.upper()}{path}".encode()
        sig = self._load().sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    def headers(self, method, path):
        ts = int(time.time() * 1000)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": self.sign(method, path, ts),
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
            "Content-Type": "application/json",
        }


class KalshiOrderClient:
    """Order/portfolio client. mode in {'dry_run','demo','live'}.

    In dry_run, write methods return {"dry_run": True, "intent": ...} and
    append the intent to `intents` (the quoter logs these — that IS the
    plan-only dress rehearsal). Reads pass through unauthenticated where the
    endpoint allows, else raise in dry_run without credentials.
    """

    def __init__(self, mode=None, api_key_id=None, private_key_path=None):
        self.mode = (mode or os.environ.get("KALSHI_TRADING_MODE") or "dry_run").lower()
        key_id = api_key_id or os.environ.get("KALSHI_API_KEY_ID")
        pem = private_key_path or os.environ.get("KALSHI_RSA_PRIVATE_KEY_PATH")
        if self.mode == "live":
            if os.environ.get("KALSHI_LIVE_ARMED") != LIVE_ARM_PHRASE:
                raise RuntimeError("live mode requires KALSHI_LIVE_ARMED (operator act)")
            if not (key_id and pem):
                raise RuntimeError("live mode requires credentials")
        if self.mode == "demo" and not (key_id and pem):
            raise RuntimeError("demo mode requires demo credentials")
        if self.mode not in ("dry_run", "demo", "live"):
            raise RuntimeError(f"unknown mode {self.mode}")
        self.base = {"dry_run": PROD_BASE, "demo": DEMO_BASE, "live": PROD_BASE}[self.mode]
        self.auth = KalshiAuth(key_id, pem) if (key_id and pem) else None
        self.intents = []
        self._last_write = 0.0

    # ---------------- transport ----------------

    def _request(self, method, path, body=None, authed=True):
        url = self.base + path
        headers = {"User-Agent": "maker-kalshi-client/1.0"}
        if authed:
            if self.auth is None:
                raise RuntimeError(f"{method} {path} requires credentials (mode={self.mode})")
            headers.update(self.auth.headers(method, path.split("?")[0]))
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            return json.loads(r.read() or b"{}")

    def _write(self, method, path, body):
        """Writes: dry_run records intent; demo/live send ONCE, no retry."""
        if self.mode == "dry_run":
            intent = {"method": method, "path": path, "body": body, "ts": time.time()}
            self.intents.append(intent)
            return {"dry_run": True, "intent": intent}
        wait = WRITE_SPACING_S - (time.time() - self._last_write)
        if wait > 0:
            time.sleep(wait)
        try:
            return self._request(method, path, body)
        finally:
            self._last_write = time.time()

    # ---------------- orders ----------------
    # #1 DEMO-VERIFICATION ITEM (docs check 2026-07-18): Kalshi now documents a
    # NEWER V2 order surface — `POST /portfolio/events/orders` with side="bid"|"ask"
    # (bid==yes, ask==no), price as a DOLLAR STRING, count as string,
    # time_in_force, and native `self_trade_prevention_type` (e.g. "taker_at_cross").
    # The methods below use the LEGACY shape (`/portfolio/orders`, side="yes"/"no",
    # action, integer-cent yes_price/no_price, post_only) — the same shape the SB
    # lane's client uses (`b0c0da3`) and still accepted, but marked deprecated.
    # DO NOT blind-switch: confirm which shape the DEMO endpoint accepts (and the
    # exact string/int formatting) against real responses in the demo session,
    # then pin one. Self-trade prevention is a REQUIRED add on the live path (we
    # quote both sides of the same market — a taker_at_cross STP is the guard).

    def create_order(self, ticker, side, action, count, price_dollars,
                     post_only=True, client_order_id=None, expiration_ts=None):
        """side: 'yes'|'no'; action: 'buy'|'sell'; count: contracts (int);
        price in DOLLARS (converted to integer cents for the API)."""
        body = {
            "ticker": ticker, "side": side, "action": action, "type": "limit",
            "count": int(count),
            f"{side}_price": int(round(price_dollars * 100)),
            "post_only": bool(post_only),
        }
        if client_order_id:
            body["client_order_id"] = client_order_id
        if expiration_ts:
            body["expiration_ts"] = int(expiration_ts)
        return self._write("POST", f"{API_ROOT}/portfolio/orders", body)

    def batch_create(self, orders):
        """orders: list of create_order-style dicts (already API-shaped).
        NB: rate-limit billing is PER ITEM (10 tokens each) — batching saves
        round-trips, NOT write budget. Quota math must count orders, not calls."""
        return self._write("POST", f"{API_ROOT}/portfolio/orders/batched",
                           {"orders": orders})

    def cancel_order(self, order_id):
        return self._write("DELETE", f"{API_ROOT}/portfolio/orders/{order_id}", None)

    def batch_cancel(self, order_ids):
        return self._write("DELETE", f"{API_ROOT}/portfolio/orders/batched",
                           {"ids": list(order_ids)})

    # ---------------- portfolio reads ----------------

    def get_balance(self):
        return self._request("GET", f"{API_ROOT}/portfolio/balance")

    def get_orders(self, status="resting"):
        return self._request("GET", f"{API_ROOT}/portfolio/orders?status={status}")

    def get_fills(self, limit=200):
        return self._request("GET", f"{API_ROOT}/portfolio/fills?limit={limit}")

    def get_positions(self):
        return self._request("GET", f"{API_ROOT}/portfolio/positions")

    # ---------------- public reads (no auth) ----------------

    def get_orderbook(self, ticker):
        return self._request("GET", f"{API_ROOT}/markets/{ticker}/orderbook",
                             authed=False)

    def exchange_status(self):
        return self._request("GET", f"{API_ROOT}/exchange/status", authed=False)


if __name__ == "__main__":
    c = KalshiOrderClient()
    print(f"mode={c.mode} base={c.base} authed={c.auth is not None}")
    r = c.create_order("KXTEST-1", "yes", "buy", 100, 0.42)
    print("dry-run order:", json.dumps(r))
    print("exchange:", json.dumps(c.exchange_status()))

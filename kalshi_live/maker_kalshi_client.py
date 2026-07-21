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

# 0.16s => 6.25 writes/s; all-creates peak = 62.5 tok/s (create=10 tok), a ~37%
# margin under the Basic write ceiling of 100 tok/s (no burst credit). Widened
# from 0.12 (~83 tok/s, only ~17% margin) for cold-start safety; latency is a
# non-issue on minute-scale reward sampling.
WRITE_SPACING_S = 0.16        # floor between writes
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
    # ORDER SURFACE — V2, VERIFIED against demo 2026-07-19 (KALSHI_KEY cc7845…):
    #  - LEGACY /portfolio/orders is DEAD (410 deprecated_v1_order_endpoint). Removed.
    #  - Create: POST /portfolio/events/orders. side='bid'|'ask'. `price` is ALWAYS
    #    the YES-scale price (verified: an ask@0.90 rested as a NO order @0.10).
    #    So: YES bid @p  -> side='bid', price=p ;  NO  bid @p -> side='ask',
    #    price=(1-p). count/price are STRINGS. Native self_trade_prevention_type.
    #  - Cancel: DELETE /portfolio/events/orders/{id} (verified).
    #  - Reads (GET /portfolio/orders, balance, fills) still work unchanged.
    #  - maker_fees_dollars observed 0.000000 on a temp market (maker-free confirmed).

    def create_order_v2(self, ticker, book_side, count, price_dollars,
                        time_in_force="good_till_canceled",
                        self_trade_prevention_type="taker_at_cross",
                        post_only=True, client_order_id=None):
        """POST /portfolio/events/orders. book_side='bid'(=yes) | 'ask'(=no).
        price_dollars is the YES-scale price as a decimal (0..1). Verified demo."""
        body = {
            "ticker": ticker, "side": book_side, "count": str(int(count)),
            "price": f"{price_dollars:.4f}",
            "time_in_force": time_in_force,
            "self_trade_prevention_type": self_trade_prevention_type,
            "post_only": bool(post_only),
        }
        if client_order_id:
            body["client_order_id"] = client_order_id
        resp = self._write("POST", f"{API_ROOT}/portfolio/events/orders", body)
        # a 200 can still carry a rejected order — surface it, don't count it placed.
        # BUT an IOC that (partially) fills terminates as status 'canceled' WITH a real fill_count
        # — that is SUCCESS, not a rejection (the remainder is auto-canceled by design). Raising on
        # it discarded the confirmed fill and aborted flatten_to_zero's retry ladder on the first
        # partial fill, exactly on thin books where the taker backstop matters most (review C1).
        # So: only a resting (GTC/post_only) order that fails to REST is a fatal non-fill; for an
        # IOC we never raise on status — the caller inspects fill_count and breaks on zero itself.
        if isinstance(resp, dict) and not resp.get("dry_run"):
            o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
            status = (o or {}).get("status")
            ioc = time_in_force == "immediate_or_cancel"
            fatal = set() if ioc else {"rejected", "canceled", "cancelled"}
            if status in fatal:
                raise RuntimeError(f"order not resting: status={status} resp={resp}")
        return resp

    def create_quote(self, ticker, outcome, price_dollars, count,
                     post_only=True, client_order_id=None):
        """Maker-friendly wrapper: rest a resting BID on the given OUTCOME side at
        that outcome's own price. outcome='yes'|'no'. Maps to the V2 bid/ask +
        yes-scale price (no @p -> ask @ 1-p). Never crosses (post_only)."""
        if outcome == "yes":
            return self.create_order_v2(ticker, "bid", count, price_dollars,
                                        post_only=post_only, client_order_id=client_order_id)
        if outcome == "no":
            return self.create_order_v2(ticker, "ask", count, round(1.0 - price_dollars, 4),
                                        post_only=post_only, client_order_id=client_order_id)
        raise ValueError(f"outcome must be 'yes'|'no', got {outcome!r}")

    def batch_create(self, orders):
        """orders: list of V2-shaped order dicts. Rate-limit billing is PER ITEM
        (create=10 tok) — batch saves round-trips, NOT budget. NOTE: V2 batch path
        not yet demo-verified — confirm before the live batch path is used."""
        return self._write("POST", f"{API_ROOT}/portfolio/events/orders/batched",
                           {"orders": orders})

    def cancel_order(self, order_id):
        """DELETE /portfolio/events/orders/{id} — V2, verified demo 2026-07-19."""
        return self._write("DELETE", f"{API_ROOT}/portfolio/events/orders/{order_id}", None)

    def batch_cancel(self, order_ids):
        """Cancel many orders. The legacy /portfolio/orders/batched path is DEAD
        (410); the V2 batched-cancel shape is not demo-verified, so cancel one at
        a time via the verified V2 single-cancel — safe, never silently 410s.
        (Rate-limit billing is per-item anyway, so no round-trip savings lost that
        matter for correctness.)"""
        results, failed = [], []
        for oid in order_ids:
            try:
                results.append(self.cancel_order(oid))
            except Exception as e:
                # one bad id (e.g. 404 already-gone) must not abort the rest
                failed.append({"order_id": oid, "error": str(e)})
        return {"cancelled": results, "failed": failed}

    # ---------------- portfolio reads ----------------

    def _get_paginated(self, base_path, item_key, params=None):
        """GET a cursor-paginated portfolio endpoint, following `cursor` until exhausted, and
        return {item_key: [ALL rows], "cursor": ""}. Kalshi paginates positions/orders at
        default limit=100 (probe-verified 2026-07-21: response carries a `cursor`, empty = done);
        a single un-cursored GET silently truncates to page 1, so real inventory/orders fall off
        as settled rows accumulate and the bot goes delta-blind with NO error (review C4). The
        50-page bound (50k rows) is a runaway guard, far above any real book."""
        params = dict(params or {})
        params.setdefault("limit", 1000)
        items, cursor = [], ""
        for _ in range(50):
            p = dict(params)
            if cursor:
                p["cursor"] = cursor
            qs = "&".join(f"{k}={v}" for k, v in p.items())
            d = self._request("GET", f"{base_path}?{qs}")
            items.extend(d.get(item_key) or [])
            cursor = d.get("cursor") or ""
            if not cursor:
                break
        return {item_key: items, "cursor": cursor}

    def get_balance(self):
        return self._request("GET", f"{API_ROOT}/portfolio/balance")

    def get_orders(self, status="resting"):
        return self._get_paginated(f"{API_ROOT}/portfolio/orders", "orders", {"status": status})

    def get_fills(self, limit=200):
        return self._request("GET", f"{API_ROOT}/portfolio/fills?limit={limit}")

    def get_positions(self):
        # count_filter=position drops settled/zero rows (probe-verified: 1 stale row -> 0) so real
        # inventory can never be pushed off a page by settled noise; cursor-follow gets the rest.
        return self._get_paginated(f"{API_ROOT}/portfolio/positions", "market_positions",
                                   {"count_filter": "position"})

    # ---------------- public reads (no auth) ----------------

    def get_orderbook(self, ticker):
        return self._request("GET", f"{API_ROOT}/markets/{ticker}/orderbook",
                             authed=False)

    def exchange_status(self):
        return self._request("GET", f"{API_ROOT}/exchange/status", authed=False)


if __name__ == "__main__":
    c = KalshiOrderClient()
    print(f"mode={c.mode} base={c.base} authed={c.auth is not None}")
    r = c.create_quote("KXTEST-1", "yes", 0.42, 100)
    print("dry-run order:", json.dumps(r))
    print("exchange:", json.dumps(c.exchange_status()))

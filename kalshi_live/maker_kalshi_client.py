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
import io
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
# Times _get_paginated hit its 50-page runaway bound while the venue still had more to give,
# i.e. handed a caller a PARTIAL list that looks complete. Unreachable at today's volumes
# (50 pages x limit=1000 = 50k rows); exists so it can never become a silent one.
PAGINATION_TRUNCATED = [0]

# 0.16s => 6.25 writes/s; all-creates peak = 62.5 tok/s (create=10 tok), a ~37%
# margin under the Basic write ceiling of 100 tok/s (no burst credit). Widened
# from 0.12 (~83 tok/s, only ~17% margin) for cold-start safety; latency is a
# non-issue on minute-scale reward sampling.
WRITE_SPACING_S = 0.16        # floor between writes
HTTP_TIMEOUT_S = 15
LIVE_ARM_PHRASE = "operator-approved-live-pilot"

# --- CONNECTION POOLING (KALSHI_HTTP_POOL, default 0 = OFF, provable no-op) ---
# urllib.request.urlopen opens a FRESH connection per call, so every request pays
# DNS + TCP + TLS. Measured 2026-07-25 against the live API: TTFB 127ms cold vs
# 40ms on a reused connection locally (253 -> 127ms from the VPS) — a 2-3x cut on
# EVERY read and write, with no change to request semantics.
# Two things this MUST get right, or it is dangerous rather than fast:
#   1. retries=False — urllib3 retries by default. A retried POST could DOUBLE
#      SUBMIT AN ORDER. Writes are already "send ONCE, no retry" by design.
#   2. urllib3 does NOT raise on 4xx/5xx, but urlopen DOES, and every caller is
#      written against that raise (a non-raising 4xx would read as a successful
#      order). The pooled path re-raises urllib.error.HTTPError so behaviour is
#      byte-identical to the legacy path.
HTTP_POOL = os.environ.get("KALSHI_HTTP_POOL", "0") == "1"
_POOL = None


def _pool():
    """Lazy PoolManager. Import is inside the function so flag-off never even
    imports urllib3 (keeps the live import graph unchanged when OFF)."""
    global _POOL
    if _POOL is None:
        import urllib3
        _POOL = urllib3.PoolManager(
            maxsize=8, block=False,
            retries=False,                    # NEVER auto-retry: see note 1 above
            timeout=urllib3.Timeout(connect=5.0, read=HTTP_TIMEOUT_S),
            headers={"User-Agent": "maker-kalshi-client/1.0"},
        )
    return _POOL


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
        if HTTP_POOL:
            if data is not None:
                headers["Content-Type"] = "application/json"
            r = _pool().request(method, url, body=data, headers=headers,
                               preload_content=True, redirect=False)
            raw = r.data or b"{}"
            if not (200 <= r.status < 300):
                # preserve the legacy raise EXACTLY: callers are written against
                # urlopen's HTTPError and would otherwise read a 4xx as success.
                raise urllib.error.HTTPError(url, r.status, str(r.reason or ""),
                                             r.headers, io.BytesIO(raw))
            return json.loads(raw)
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

    def amend_order_v2(self, order_id, ticker, book_side, count, price_dollars,
                       client_order_id=None):
        """POST /portfolio/events/orders/{order_id}/amend — update price and/or max fillable count.

        WHY IT EXISTS HERE — Kalshi docs, Amend Order (V2), verbatim:
          "Amending a resting order preserves queue position only when the amendment decreases
           size. All other amendments — like increasing size or changing price forfeit queue
           position and place the order at the back of the queue."
        So this is worth calling INSTEAD of cancel+create in exactly one case: an unchanged price
        with a SMALLER count. Anywhere else it is equivalent to what we already do, and the
        existing path stays.

        ⚠ NOT VERIFIED AGAINST THE LIVE VENUE. Exercising it would mutate real resting orders on a
        parked real-money account, so it is unit-tested only and its caller ships behind
        KALSHI_AMEND_DECREASE (default 0). The body mirrors create_order_v2's proven encoding
        (yes-scale decimal price, integer-string count); confirm on the first live cycle."""
        body = {"ticker": ticker, "side": book_side, "count": str(int(count)),
                "price": f"{price_dollars:.4f}"}
        if client_order_id:
            body["client_order_id"] = client_order_id
        return self._write("POST", f"{API_ROOT}/portfolio/events/orders/{order_id}/amend", body)

    def amend_quote(self, order_id, ticker, outcome, price_dollars, count, client_order_id=None):
        """Maker-friendly wrapper mirroring create_quote's outcome->book_side mapping EXACTLY
        (no @p -> ask @ 1-p). Getting this mapping wrong would amend the opposite side of the book,
        so it is a deliberate copy of the proven create path rather than a re-derivation."""
        if outcome == "yes":
            return self.amend_order_v2(order_id, ticker, "bid", count, price_dollars,
                                       client_order_id=client_order_id)
        if outcome == "no":
            return self.amend_order_v2(order_id, ticker, "ask", count,
                                       round(1.0 - price_dollars, 4),
                                       client_order_id=client_order_id)
        raise ValueError(f"outcome must be 'yes'|'no', got {outcome!r}")

    def cancel_order(self, order_id):
        """DELETE /portfolio/events/orders/{id} — V2, verified demo 2026-07-19."""
        return self._write("DELETE", f"{API_ROOT}/portfolio/events/orders/{order_id}", None)

    @staticmethod
    def cancel_remaining_ct(resp):
        """Contracts STILL RESTING at cancel time, per the venue's own cancel
        response — ground truth for how much of an order was UNFILLED.
        Returns None when the response does not state it (caller must then
        treat the remaining size as UNKNOWN and never re-create blind).
        Added for the WS hot path (re-review BLOCKER B3-1): inferring remaining
        size from book depth is wrong whenever other makers share our level."""
        if not isinstance(resp, dict):
            return None
        o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
        for k in ("remaining_count_fp", "remaining_count"):
            if k in o and o[k] is not None:
                try:
                    return float(o[k])
                except (TypeError, ValueError):
                    return None
        return None

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
        if cursor:
            # SILENT TRUNCATION, now LOUD (2026-08-03). Hitting the 50-page bound with a live
            # cursor means the caller is being handed a PARTIAL list that looks complete: the
            # leftover cursor is returned, but no caller in this repo inspects it and nothing
            # counted it. That is the same silent-degradation class this module counts
            # everywhere else, and it is the shape the burn-and-run and settled-row blind spots
            # both took — a feed that quietly stops mentioning things.
            # Runaway guard is unchanged (still 50 pages); only the silence is fixed.
            PAGINATION_TRUNCATED[0] += 1
            print(f"WARNING {base_path} paginated to the {50}-page cap with a LIVE cursor — "
                  f"{len(items)} {item_key} returned but the list is INCOMPLETE; every "
                  f"consumer of this call is now reading a partial view")
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

    def get_realized_by_market(self):
        """{ticker: venue realized_pnl_dollars} over all traded markets that are STILL OPEN,
        including fully flat ones. count_filter=total_traded keeps flat rows with their
        realized_pnl_dollars — the venue's own attribution — so the per-market loss governor
        can see a market that burned and fully flattened within one cycle (the burn-and-run
        hole: count_filter=position drops the row before the loss is ever read). Probe
        2026-07-31T13:15:36Z: 12/12 flat rows carry realized_pnl_dollars. That part stands.

        ⚠ SETTLED MARKETS ARE NOT IN THIS FEED — CORRECTED 2026-08-03. This docstring used to
        claim "a settled row was still present 10h after settlement". That does NOT reproduce
        and it is the sentence that made the governor's blind spot look closed. Measured
        read-only 2026-08-03T01:35:04Z over the complete account history: of 127 settled
        tickers, **0** appear in this feed (42 rows returned, none of them settled). The
        endpoint drops a market on settlement, and count_filter has no bearing on that — it
        governs zero-vs-nonzero POSITION rows, not settlement state.
        Consequence: a loss that only becomes real at settlement is invisible here, so the
        governor cannot see it. Live 2026-08-02 the largest single loss of the day
        (KXTEMPAUSH, -$9.4939 all-in) was absent from this read entirely.
        Reconciliation therefore lives in the quoter, not here: see mkt_realized_carry (a
        departed ticker's last value is carried for the rest of the day) and the settlement
        top-up that corrects it from /portfolio/settlements.

        The inventory read above stays count_filter=position ON PURPOSE — settled noise must
        never push real inventory off a page; this is a separate governor-only feed."""
        d = self._get_paginated(f"{API_ROOT}/portfolio/positions", "market_positions",
                                {"count_filter": "total_traded"})
        out = {}
        for p in (d.get("market_positions") or []):
            try:
                out[p.get("ticker")] = float(p.get("realized_pnl_dollars") or 0.0)
            except (TypeError, ValueError):
                pass
        return out

    def get_settlements(self, min_ts=None):
        """Settled markets, newest-inclusive from `min_ts` (UNIX seconds) when given.

        ADDITIVE 2026-08-03 (defect 5b). This is the ONLY feed that can see a loss which only
        becomes real at settlement: get_realized_by_market above is a /portfolio/positions read
        and that endpoint drops a market the moment it settles (measured 2026-08-03T01:35:04Z:
        0 of 127 settled tickers present).

        VENUE SHAPES, measured 2026-08-03T02:30:57Z over the complete history (n=127):
          * `revenue` is CENTS — it matches the value/counts payout model on 127/127 when
            divided by 100 (a bare-dollars reading matches only 109/127, and only where both
            are zero). Divide by 100.
          * `settled_time` is an ISO-8601 STRING ('2026-08-02T14:58:20.874705Z'), 127/127 —
            NOT an epoch int, though `min_ts` itself takes epoch seconds.
          * `min_ts` IS honoured: min_ts at the median settle time returned 66 rows against
            127 total, exactly the count at or after it. So a watermark really does bound the
            work.
          * ⚠ `yes_total_cost_dollars` / `no_total_cost_dollars` are GROSS LIFETIME costs on
            BOTH sides and are NOT a P&L basis. KXTRUMPENDORSEMENTS-26AUG01-A5 carries
            $73.10 + $68.05 = $141.15 of "cost" on a position that was 125.00 yes against
            124.89 no — almost entirely PAIRED, with real exposure of a fraction of a
            contract. Subtracting those fields from revenue manufactures enormous phantom
            losses. The correct basis is the venue's own market_exposure_dollars for the net
            position, snapshotted while the market was still alive.
        """
        params = {} if min_ts is None else {"min_ts": int(min_ts)}
        return self._get_paginated(f"{API_ROOT}/portfolio/settlements", "settlements", params)

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

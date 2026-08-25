"""Typed parse layer for Kalshi venue responses (R5 part 3, operator-approved 2026-08-25).

Purpose: kill the parse-defect class. Incidents this layer would have caught:
  - the `_rest_maker_offset` V2 dual-shape bug (08-21: `else {}` swallowed a top-level
    response -> lost order_id -> 49 false naked alarms);
  - anchor v1/v2 (08-24: crossing leg silently rejected post-only in a loop);
  - the fills-direction and `position` vs `position_fp` field traps (canon).

Contract: every function either returns a normalized dataclass (floats, canonical field
names) or raises ApiShapeError NAMING the missing/odd piece with a bounded raw snippet.
No silent {} fallbacks, no .get() chains that turn shape drift into wrong numbers.

Fixtures: test_kalshi_api_types.py pins every parser against RESPONSES RECORDED FROM THE
LIVE VENUE on 2026-08-25 (this session's reads/writes) — real shapes, not invented ones.

ADOPTION PLAN (separate, per-callsite, operator-gated): live modules keep their current
parsing until each call site is migrated with its own pin; this module ships unused by
the live path so its arrival is provably behavior-neutral.
"""
from dataclasses import dataclass, field


class ApiShapeError(ValueError):
    def __init__(self, what, raw):
        super().__init__(f"{what} — raw={str(raw)[:240]!r}")
        self.what = what


def _f(d, key, what):
    try:
        v = d[key]
    except (KeyError, TypeError):
        raise ApiShapeError(f"{what}: missing {key!r}", d)
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ApiShapeError(f"{what}: {key!r} not numeric ({v!r})", d)


def _s(d, key, what):
    v = d.get(key) if isinstance(d, dict) else None
    if not v or not isinstance(v, str):
        raise ApiShapeError(f"{what}: missing/non-string {key!r}", d)
    return v


@dataclass
class OrderCreate:
    order_id: str
    status: str            # may be "" — V2 create sometimes omits status on success
    raw: dict = field(repr=False, default_factory=dict)


def parse_order_create(resp):
    """V2 create response — BOTH shapes (nested {'order': {...}} and top-level). The
    08-21 shape-fix incident is exactly a one-shape assumption here."""
    if not isinstance(resp, dict):
        raise ApiShapeError("order-create: response not a dict", resp)
    o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
    return OrderCreate(order_id=_s(o, "order_id", "order-create"),
                       status=str(o.get("status") or ""), raw=resp)


@dataclass
class Amend:
    order_id: str
    remaining_count: float
    fill_count: float


def parse_amend(resp):
    """Amend response (recorded live 2026-08-25T20:21Z: fill_count/order_id/
    remaining_count/ts_ms at top level)."""
    if not isinstance(resp, dict):
        raise ApiShapeError("amend: response not a dict", resp)
    o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
    return Amend(order_id=_s(o, "order_id", "amend"),
                 remaining_count=_f(o, "remaining_count", "amend"),
                 fill_count=_f(o, "fill_count", "amend"))


@dataclass
class Cancel:
    order_id: str
    reduced_by: float


def parse_cancel(resp):
    if not isinstance(resp, dict):
        raise ApiShapeError("cancel: response not a dict", resp)
    o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
    return Cancel(order_id=_s(o, "order_id", "cancel"),
                  reduced_by=_f(o, "reduced_by", "cancel"))


def parse_balance(resp):
    """-> balance in DOLLARS (float). Canonical field is the string `balance_dollars`;
    the integer `balance` is cents and MUST NOT be mistaken for dollars."""
    if not isinstance(resp, dict):
        raise ApiShapeError("balance: response not a dict", resp)
    return _f(resp, "balance_dollars", "balance")


@dataclass
class Position:
    ticker: str
    position: float        # signed contracts; SOURCE FIELD IS position_fp (plain absent)
    exposure_dollars: float
    realized_pnl_dollars: float


def parse_positions(resp):
    """/portfolio/positions -> [Position]. Canon traps enforced: rows live under
    `market_positions`; the count field is `position_fp` (plain `position` absent);
    settled markets are BLIND here (defect 14) — callers must not treat absence as flat
    for settled tickers."""
    if not isinstance(resp, dict) or "market_positions" not in resp:
        raise ApiShapeError("positions: missing market_positions", resp)
    out = []
    for r in resp["market_positions"]:
        out.append(Position(ticker=_s(r, "ticker", "positions"),
                            position=_f(r, "position_fp", "positions"),
                            exposure_dollars=_f(r, "market_exposure_dollars", "positions"),
                            realized_pnl_dollars=_f(r, "realized_pnl_dollars", "positions")))
    return out


@dataclass
class RestingOrder:
    order_id: str
    ticker: str
    side: str
    yes_price_dollars: float
    no_price_dollars: float


def parse_orders(resp):
    """/portfolio/orders -> [RestingOrder]. Rows under `orders`; prices are the
    *_dollars strings (plain-name price fields return None — canon)."""
    if not isinstance(resp, dict) or "orders" not in resp:
        raise ApiShapeError("orders: missing orders", resp)
    out = []
    for r in resp["orders"]:
        out.append(RestingOrder(order_id=_s(r, "order_id", "orders"),
                                ticker=_s(r, "ticker", "orders"),
                                side=_s(r, "side", "orders"),
                                yes_price_dollars=_f(r, "yes_price_dollars", "orders"),
                                no_price_dollars=_f(r, "no_price_dollars", "orders")))
    return out


@dataclass
class Program:
    program_id: str
    market_ticker: str
    target_size: float
    discount_factor: float   # decimal (bps/10000)
    pool_usd_day: float      # period_reward/10000 per R1 pool canon
    start_date: str
    end_date: str


def parse_incentive_program(row):
    """One /incentive_programs row. Fields per canon: target_size_fp (string),
    discount_factor_bps (int), period_reward in centicents (/10000 = $/day pool)."""
    return Program(program_id=_s(row, "id", "program") if "id" in (row or {})
                   else _s(row, "program_id", "program"),
                   market_ticker=_s(row, "market_ticker", "program"),
                   target_size=_f(row, "target_size_fp", "program"),
                   discount_factor=_f(row, "discount_factor_bps", "program") / 10000.0,
                   pool_usd_day=_f(row, "period_reward", "program") / 10000.0,
                   start_date=str((row or {}).get("start_date") or ""),
                   end_date=_s(row, "end_date", "program"))


def parse_estimates(snapshot):
    """One est-feed snapshot line -> {program_id: usd}. Unit canon: reward_centicents
    /10000 = dollars. Rows at 0 are REAL (feed displays $0.0000 — sweep F4)."""
    if not isinstance(snapshot, dict) or "estimates" not in snapshot:
        raise ApiShapeError("estimates: missing estimates", snapshot)
    out = {}
    for e in snapshot["estimates"]:
        pid = _s(e, "program_id", "estimates")
        out[pid] = _f(e, "reward_centicents", "estimates") / 10000.0
    return out

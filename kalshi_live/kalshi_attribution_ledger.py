#!/usr/bin/env python3
"""Kalshi ATTRIBUTION LEDGER — READ-ONLY collector (Stage 1 of the long-term plan).

Splits every dollar of account movement into REWARDS vs TRADING vs SETTLEMENT, per snapshot,
and allocates the rewards residual across series by our measured resting presence. Kalshi has
NO ledger API endpoint (probed 2026-07-21: /portfolio/{ledger,transactions,rewards} all 404),
so rewards are observable ONLY as the balance-delta residual after fills and settlements are
accounted:

    rewards_residual = delta_balance - fills_cashflow - settlements_revenue

between two consecutive snapshots (all three terms measured from the API). Labels:
  MEASURED  = read directly from the venue (balance, fills, settlements, resting book)
  ALLOCATED = rewards residual split across series pro-rata by our resting-quote presence
              (presence IS what Kalshi pays for, but the split is ours, not the venue's)

Writes one JSON row per run to ledger-YYYYMM.jsonl next to this script; state (cursor
watermarks + last snapshot) in ledger_state.json. GETs only — this script can never trade,
cancel, or place anything. Safe to run any time, any cadence (designed for an hourly timer).

Report: python kalshi_attribution_ledger.py --report [days]
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(DATA_DIR, "ledger_state.json")

KID = os.environ.get("KALSHI_API_KEY_ID", "89314df3-b170-4d3d-9a7c-fc49336365f2")
PEM = os.environ.get("KALSHI_RSA_PRIVATE_KEY_PATH", os.path.expanduser("~/.kalshi/prod_key.pem"))
BASE = "https://external-api.kalshi.com"
P = "/trade-api/v2"
SPACING_S = 0.6                      # read spacing (public rate-limit hygiene)
_last = [0.0]


def utcnow():
    return datetime.now(timezone.utc)


def _sign(method, path):
    ts = str(int(time.time() * 1000))
    msg = f"{ts}{method}{path.split('?')[0]}".encode()
    raw = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", PEM,
         "-sigopt", "rsa_padding_mode:pss", "-sigopt", "rsa_pss_saltlen:-2"],
        input=msg, capture_output=True).stdout
    sig = subprocess.run(["openssl", "base64", "-A"], input=raw, capture_output=True).stdout.decode()
    return {"KALSHI-ACCESS-KEY": KID, "KALSHI-ACCESS-TIMESTAMP": ts, "KALSHI-ACCESS-SIGNATURE": sig}


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    h = {"User-Agent": "kalshi-ledger/1.0", "Content-Type": "application/json", **_sign("GET", path)}
    req = urllib.request.Request(BASE + path, headers=h, method="GET")
    with urllib.request.urlopen(req, timeout=20) as r:
        _last[0] = time.time()
        return json.loads(r.read() or b"{}")


def get_paginated(path_base, item_key, extra=""):
    """Follow `cursor` until exhausted (same prod-verified semantics as the quoter client)."""
    items, cursor = [], ""
    for _ in range(50):
        qs = f"limit=200{extra}" + (f"&cursor={cursor}" if cursor else "")
        d = get(f"{path_base}?{qs}")
        items.extend(d.get(item_key) or [])
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    return items


def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def series_of(ticker):
    return (ticker or "").split("-")[0]


def fill_cashflow(f):
    """Signed CASH flow of one fill from OUR account's perspective (negative = cash out).
    Kalshi fill: action buy/sell, side yes/no, count, yes_price. Buying any side costs that
    side's price x count; selling receives it. NO-side price = 1 - yes_price.

    FEES ARE SUBTRACTED (external review 2026-07-22, accepted): the live fill payload carries
    `fee_cost` (probe-verified 07-22 18:05Z). Omitting it pushed every fee into the
    rewards_residual bucket, i.e. a taker-fee leak would have been reported as REWARDS —
    corrupting the one number the whole strategy is judged on. Maker fills are fee-free
    (receipt-verified), so today's impact is ~0; a taker fill would not have been."""
    cnt = _f(f.get("count_fp") or f.get("count"))
    yp = _f(f.get("yes_price_dollars") or f.get("yes_price"))
    if yp > 1.0:                      # some payloads carry cents — normalize
        yp = yp / 100.0
    price = yp if (f.get("side") == "yes") else (1.0 - yp)
    cash = -price * cnt if f.get("action") == "buy" else price * cnt
    fee = _f(f.get("fee_cost"))
    if fee > 1000:                    # cents-scale guard (fee_cost lacks a _dollars suffix)
        fee = fee / 100.0
    return cash - abs(fee)


def settlement_revenue(s):
    """Settlement payout in dollars from the `revenue` field (cents on prod).

    2026-07-22 INVESTIGATION — read before "fixing" this again. A day showed rewards_residual
    +$57.82 (~89% daily return on a $65 account: impossible). Two hypotheses were tested and
    BOTH were wrong; the residual gap is NOT here:
      H1 "revenue under-reports payouts": rows showed revenue=0 alongside yes_count_fp=20 /
         no_count_fp=20. REFUTED — yes_count_fp/no_count_fp are GROSS traded counts, not the
         settled position. Reconstructing the position from the fill tape showed we held +40 YES
         into a result=no settlement, i.e. the contracts genuinely expired worthless and
         revenue=0 is CORRECT. Substituting winning-side-count x $1 produced $274.50 of
         "payouts" on a $65 account — a worse number that merely looked authoritative.
      H2 "matched pairs net and return $1/pair at trade time, unmodelled": REFUTED — replaying
         the tape found 0.00 contracts netted against opposite inventory that day.
    REMAINING SUSPECT (unverified, do NOT code on faith): fill_cashflow's sell-side convention.
    Kalshi has no naked short, so an OPENING "sell no" is economically a BUY of yes and costs
    yes_price x count, while this function books it as RECEIVING no_price x count. That
    asymmetry would create exactly this shape of unexplained inflow. Proving it needs
    position-aware (opening vs closing) cash modelling per fill.
    UNTIL THEN: rewards_residual is NOT a trustworthy rewards figure. Ground truth for rewards
    is the Kalshi web UI (the only receipt-grade number to date: ~$18.60, operator-confirmed)."""
    return _f(s.get("revenue")) / 100.0


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh)
    os.replace(tmp, STATE_FILE)


def collect():
    st = load_state()
    now = utcnow()
    bal = _f(get(f"{P}/portfolio/balance").get("balance_dollars"))

    # resting book -> per-series presence (contracts and $) — what rewards are paid FOR
    orders = get_paginated(f"{P}/portfolio/orders", "orders", extra="&status=resting")
    presence = defaultdict(float)     # series -> resting $ (collateral-side approximation)
    for o in orders:
        outcome = o.get("outcome_side")
        pstr = o.get(f"{outcome}_price_dollars") if outcome else None
        cnt = _f(o.get("remaining_count_fp") or o.get("remaining_count"))
        if pstr is None or cnt <= 0:
            continue
        presence[series_of(o.get("ticker"))] += _f(pstr) * cnt

    # positions -> exposure (for equity, MEASURED)
    pos = get_paginated(f"{P}/portfolio/positions", "market_positions", extra="&count_filter=position")
    exposure = sum(_f(p.get("market_exposure_dollars")) for p in pos)

    # fills/settlements SINCE the last processed watermark (created_time ISO strings sort fine).
    # FIRST RUN is BASELINE-ONLY: no last_balance means no delta to decompose, and ingesting the
    # whole fill history would mis-attribute months of trading cash to "today" — set watermarks
    # to the newest existing records and start attributing from the NEXT snapshot.
    baseline = "last_balance" not in st
    all_fills = get_paginated(f"{P}/portfolio/fills", "fills")
    all_settles = get_paginated(f"{P}/portfolio/settlements", "settlements")
    fw = st.get("fills_watermark") or "1970-01-01T00:00:00Z"
    sw = st.get("settlements_watermark") or "1970-01-01T00:00:00Z"
    if baseline:
        fw = max([f.get("created_time") or fw for f in all_fills] + [fw])
        sw = max([s.get("settled_time") or sw for s in all_settles] + [sw])
    fills = [f for f in all_fills if (f.get("created_time") or "") > fw]
    settles = [s for s in all_settles if (s.get("settled_time") or "") > sw]

    by_series_trade = defaultdict(lambda: {"cash": 0.0, "maker_ct": 0.0, "taker_ct": 0.0})
    fills_cash = 0.0
    for f in fills:
        c = fill_cashflow(f)
        fills_cash += c
        row = by_series_trade[series_of(f.get("ticker"))]
        row["cash"] += c
        cnt = _f(f.get("count_fp") or f.get("count"))
        row["taker_ct" if f.get("is_taker") else "maker_ct"] += cnt
    settle_rev = 0.0
    by_series_settle = defaultdict(float)
    for s in settles:
        r = settlement_revenue(s)
        settle_rev += r
        by_series_settle[series_of(s.get("ticker"))] += r

    prev_bal = st.get("last_balance")
    rewards_residual = None
    if prev_bal is not None:
        # MEASURED decomposition between snapshots; residual = rewards credits (+noise)
        rewards_residual = round(bal - _f(prev_bal) - fills_cash - settle_rev, 4)

    # ALLOCATED: split the rewards residual by resting-$ presence share (this snapshot)
    total_presence = sum(presence.values())
    alloc = {}
    if rewards_residual is not None and rewards_residual > 0 and total_presence > 0:
        alloc = {s: round(rewards_residual * v / total_presence, 4) for s, v in presence.items()}

    row = {
        "ts": now.isoformat(),
        "balance": round(bal, 4),                       # MEASURED
        "position_exposure": round(exposure, 4),        # MEASURED
        "equity_lower_bound": round(bal + exposure, 4),  # exposure = cost basis, not value
        "resting_orders": len(orders),
        "presence_usd_by_series": {k: round(v, 2) for k, v in sorted(presence.items())},
        "new_fills": len(fills), "fills_cash": round(fills_cash, 4),          # MEASURED
        "new_settlements": len(settles), "settle_revenue": round(settle_rev, 4),  # MEASURED
        "trade_by_series": {k: {kk: round(vv, 4) for kk, vv in v.items()}
                            for k, v in sorted(by_series_trade.items())},
        "rewards_residual": rewards_residual,           # MEASURED (None on first run)
        "rewards_alloc_by_series": alloc,               # ALLOCATED (presence pro-rata)
    }
    path = os.path.join(DATA_DIR, f"ledger-{now.strftime('%Y%m')}.jsonl")
    with open(path, "a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    st["last_balance"] = bal
    st["fills_watermark"] = max([f.get("created_time") or fw for f in fills] + [fw])
    st["settlements_watermark"] = max([s.get("settled_time") or sw for s in settles] + [sw])
    save_state(st)
    print(f"ledger ok balance=${bal:.2f} exposure=${exposure:.2f} fills={len(fills)} "
          f"(cash {fills_cash:+.2f}) settles={len(settles)} (+{settle_rev:.2f}) "
          f"rewards_residual={'n/a-first-run' if rewards_residual is None else f'{rewards_residual:+.2f}'} "
          f"presence=${total_presence:.2f}/{len(presence)} series")
    return 0


def report(days=14):
    import glob
    rows = []
    for p in sorted(glob.glob(os.path.join(DATA_DIR, "ledger-*.jsonl"))):
        for line in open(p):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        print("no ledger data yet")
        return 0
    cutoff = utcnow().timestamp() - days * 86400
    rows = [r for r in rows if datetime.fromisoformat(r["ts"]).timestamp() >= cutoff]
    day_rewards, day_trade, series_rewards, series_trade = (defaultdict(float), defaultdict(float),
                                                            defaultdict(float), defaultdict(float))
    for r in rows:
        d = r["ts"][:10]
        if r.get("rewards_residual") is not None:
            day_rewards[d] += r["rewards_residual"]
        day_trade[d] += _f(r.get("fills_cash")) + _f(r.get("settle_revenue"))
        for s, v in (r.get("rewards_alloc_by_series") or {}).items():
            series_rewards[s] += v
        for s, v in (r.get("trade_by_series") or {}).items():
            series_trade[s] += _f(v.get("cash"))
    print(f"window: last {days}d, {len(rows)} snapshots")

    # ---- CLEAN-INTERVAL REWARDS (the trustworthy measurement) ----------------------------
    # equity = balance + cost-basis exposure (market_exposure_dollars is EXACT cost basis:
    # verified 2026-07-22, 8.11 ct x $0.83 = $6.7313 to the cent). Book value therefore moves
    # ONLY on realized events (fills/settlements) or on money appearing from nowhere = REWARDS.
    # So an interval with ZERO fills and ZERO settlements attributes its whole equity change to
    # rewards with no cash modelling and no assumptions — which is why this replaces the
    # residual number that produced an impossible +$57.82 (see settlement_revenue docstring).
    clean_rw, clean_h, total_h, dirty = 0.0, 0.0, 0.0, 0
    for a, b in zip(rows, rows[1:]):
        dt = (datetime.fromisoformat(b["ts"]) - datetime.fromisoformat(a["ts"])).total_seconds() / 3600
        if dt <= 0:
            continue
        total_h += dt
        eq_a = _f(a.get("balance")) + _f(a.get("position_exposure"))
        eq_b = _f(b.get("balance")) + _f(b.get("position_exposure"))
        # resting orders RESERVE collateral out of balance_dollars (probe-verified 07-22:
        # balance $42.93 -> $24.79 on placing 8 quotes). So an interval is only clean if the
        # resting book ALSO held still — otherwise place/cancel churn moves balance and would
        # be misread as rewards. (Our running tab's old note "resting orders do NOT deduct
        # from balance_dollars" is WRONG; this filter is why that matters.)
        pa = sum((a.get("presence_usd_by_series") or {}).values())
        pb = sum((b.get("presence_usd_by_series") or {}).values())
        quiet_book = abs(pb - pa) < 0.005 and a.get("resting_orders") == b.get("resting_orders")
        if (int(b.get("new_fills") or 0) == 0 and int(b.get("new_settlements") or 0) == 0
                and quiet_book):
            clean_rw += eq_b - eq_a
            clean_h += dt
        else:
            dirty += 1
    cov = (clean_h / total_h * 100) if total_h else 0
    print(f"\nREWARDS, clean-interval method (no fill modelling, no assumptions):")
    print(f"  ${clean_rw:+.2f} measured across {clean_h:.1f}h of quiet intervals "
          f"({cov:.0f}% of the {total_h:.1f}h window; {dirty} intervals had trading and are excluded)")
    if clean_h > 0:
        print(f"  implied rate: ${clean_rw / clean_h * 24:+.2f}/day  (extrapolated from quiet time only)")
    print(f"  NOTE: excludes reward accrual during trading intervals, so this is a FLOOR, not a total.")
    print("\nper-day (MEASURED): rewards_residual | trading+settle cashflow")
    for d in sorted(day_rewards | day_trade):
        print(f"  {d}  rewards {day_rewards.get(d, 0):+8.2f}   trade {day_trade.get(d, 0):+8.2f}")
    print("\nper-series: rewards (ALLOCATED by presence) | trade cash (MEASURED)")
    for s in sorted(set(series_rewards) | set(series_trade)):
        print(f"  {s:14} rewards {series_rewards.get(s, 0):+8.2f}   trade {series_trade.get(s, 0):+8.2f}")
    print("\nNOTE: rewards_residual is the balance delta unexplained by fills+settlements —")
    print("the only rewards observable (no venue ledger endpoint). Per-series rewards split")
    print("is ALLOCATED pro-rata by resting-$ presence, not venue-attributed.")
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 14)
        sys.exit(report(n))
    sys.exit(collect())

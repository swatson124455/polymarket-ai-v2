#!/usr/bin/env python3
"""Kalshi ATTRIBUTION LEDGER — READ-ONLY collector (Stage 1 of the long-term plan).

Splits every dollar of account movement into REWARDS vs TRADING vs SETTLEMENT, per snapshot,
and allocates the rewards residual across series by our measured resting presence. Kalshi has
NO ledger API endpoint (probed 2026-07-21: /portfolio/{ledger,transactions,rewards} all 404),
so rewards are observable ONLY as the balance-delta residual after fills and settlements are
accounted:

    rewards_residual = delta_balance - fills_cashflow - settlements_revenue

between two consecutive snapshots (all three terms measured from the API).

⚠ THE RESIDUAL CANNOT SEE EXTERNAL MONEY. A deposit or withdrawal lands in `balance` with no
fill and no settlement, so it is booked as REWARDS. This is not hypothetical: the operator's
+$20 deposit on 2026-07-22 pm shows up here as a +$19.60 residual step at ~19:34Z, and it is
the entire remaining gap between the corrected residual ($26.18 over the 07-22→23 window) and
the receipts ($6.58 of credits in that window). Kalshi exposes no transactions endpoint, so
this cannot be automated — after ANY money movement, note it, the same way the quoter's loss
meter has to be re-baselined.

Labels:
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


# =======================================================================================
# CASH-FLOW MODEL — ROOT FIX 2026-07-23. Read this before touching anything below.
# =======================================================================================
# WHAT A FILL IS. We only ever place V2 orders with side='bid' or side='ask'
# (maker_kalshi_client.create_order_v2 / create_quote). Kalshi renders those back on
# /portfolio/fills as exactly two shapes — 317/317 live fills, no exceptions:
#     book_side='bid'  <->  outcome_side='yes'  <->  legacy (action='buy',  side='yes')
#     book_side='ask'  <->  outcome_side='no'   <->  legacy (action='sell', side='no')
# The legacy (action, side) pair describes the ORDER, not the direction of cash. An 'ask' IS
# our NO bid, so "sell no" is us BUYING NO — cash OUT. This function read it literally and
# booked it as cash IN, on 156 of 317 fills. That is the whole reason rewards_residual
# printed +$57.82/day and +$75.17/day on a ~$65 account.
#
# WHY A SIGN FLIP IS NOT THE FIX (measured, do not repeat this). Kalshi has no naked short
# and nets per market, so an 'ask' fill against a long-YES position opens nothing: it closes
# pairs, and each closed pair releases $1. Cash on the offsetting quantity is therefore
# +(1 - price_paid) — an INFLOW. Booking every fill as an outflow ("just flip the sign")
# scored the live 07-22→23 window at -$741.15 of fill cash against a balance-implied
# -$60.91. The old code scored -$86.54. The position-aware model below scores -$60.91, exact
# in every one of the 27 intervals. THE CASH MODEL MUST BE POSITION-AWARE.
#
# VERIFICATION (read-only API pull 2026-07-23; every receipt independent of this code):
#   open positions   3/3  exact vs /portfolio/positions `position_fp`   (legacy 0/3)
#   settlements     51/51 exact — reconstructed net x market_result predicts `revenue`
#                          to the cent                                  (legacy 18/51,
#                          and all 18 are trivial revenue=0 rows)
#   per market      51/51 flat-or-settled tickers reproduce the transactions-CSV
#                          realized_pnl_with_fees from replayed cash + settlement revenue
#   per interval    27/27 ledger intervals reproduce the balance-implied cash exactly
#   RECEIPTS        rewards_residual then isolates the LIP credits to the cent:
#                          +$4.35 in the 07-22T06:29Z interval ($1.88 + $2.47) and
#                          +$2.23 in the 07-22T06:58Z one. CSV total to date $25.21.
# Pinned in test_attribution.py against those same venue payloads.
# =======================================================================================


def fill_outcome(f):
    """Which OUTCOME this fill ACQUIRES: 'yes' or 'no'.

    RAISES on any shape we have not verified against the venue. Emitting a wrong rewards
    number is strictly worse than emitting none — a crash leaves the watermark unadvanced
    and self-heals once the new shape is understood."""
    bs = f.get("book_side")
    if bs in ("bid", "ask"):
        return "yes" if bs == "bid" else "no"
    oc = f.get("outcome_side")        # 317/317 agreement with book_side on the live tape
    if oc in ("yes", "no"):
        return oc
    raise ValueError(f"unrecognised fill shape (no book_side/outcome_side): {f!r}")


def fill_count(f):
    return _f(f.get("count_fp") or f.get("count"))


def fill_price(f):
    """Dollar price of the outcome this fill ACQUIRES.
    yes_price_dollars + no_price_dollars == 1.0000 on 317/317 live fills, so the derived
    fallback is exact when the outcome's own field is absent."""
    oc = fill_outcome(f)
    raw = f.get(f"{oc}_price_dollars")
    if raw is None:
        yp = _f(f.get("yes_price_dollars") or f.get("yes_price"))
        if yp > 1.0:                  # some payloads carry cents — normalize
            yp = yp / 100.0
        return yp if oc == "yes" else 1.0 - yp
    p = _f(raw)
    if p > 1.0:
        p = p / 100.0
    return p


def fill_fee(f):
    """Fee of one fill, in DOLLARS, always >= 0 (a fee is never income).

    `fee_cost` is dollars despite lacking the _dollars suffix — RECEIPT-VERIFIED: the 59
    fee-bearing fills on the live tape sum to $2.5823, exactly the transactions-CSV
    open+close fee total ($2.5823). Canon §M10: maker fills are free by default, so a
    fee-bearing row is a TAKER trade. Omitting this pushed taker fees into rewards_residual,
    i.e. reported a fee leak as REWARDS."""
    return abs(_f(f.get("fee_cost")))


def fill_position_delta(f):
    """Signed contract delta of one fill: + = more YES, - = more NO.
    Matches the venue's own `position_fp` convention (signed, string, fractional)."""
    return fill_count(f) if fill_outcome(f) == "yes" else -fill_count(f)


def fill_cashflow(f, position_before):
    """Signed CASH flow of one fill from OUR account's perspective (negative = cash out).

    SIGNATURE CHANGED 2026-07-23 — the signature WAS the bug. A position-independent
    function cannot express Kalshi cash: the same fill is an outflow when it opens and an
    inflow when it nets against opposite inventory. `position_before` is the signed net
    position in THIS market immediately before this fill (use replay_fills, which computes
    it). The second argument is deliberately required: defaulting it to 0 would silently
    reproduce the "flip the sign" model that scored 12x too much cash out.

    opening quantity : pay the acquired outcome's price          -> -price * qty
    offsetting qty   : closes a pair, venue releases $1 each     -> +(1 - price) * qty
    fees             : always subtracted (see fill_fee)"""
    cnt = fill_count(f)
    price = fill_price(f)
    delta = fill_position_delta(f)
    reduces = bool(position_before) and (position_before > 0) != (delta > 0)
    offset = min(cnt, abs(position_before)) if reduces else 0.0
    opened = cnt - offset
    return -price * opened + (1.0 - price) * offset - fill_fee(f)


def replay_fills(fills):
    """Chronologically replay a fill tape, returning (events, positions).

    events   list of {"fill", "cash", "delta", "position_before", "position_after"} in
             created_time order — cash is position-aware, so the tape must be replayed from
             a point where every ticker in it was flat. Callers pass the FULL tape.
    positions {ticker: signed net contracts} after the last fill.

    Settled markets never trade again, so no settlement reset is needed: per ticker the tape
    is the complete history. Ordering matters (netting is path-dependent), hence the sort."""
    events, positions = [], defaultdict(float)
    for f in sorted(fills, key=lambda x: ((x.get("created_time") or ""), (x.get("fill_id") or ""))):
        t = f.get("ticker") or f.get("market_ticker")
        before = positions[t]
        cash = fill_cashflow(f, before)
        delta = fill_position_delta(f)
        positions[t] = before + delta
        events.append({"fill": f, "cash": cash, "delta": delta,
                       "position_before": before, "position_after": positions[t]})
    return events, dict(positions)


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
    2026-07-23 — RESOLVED, and it was NOT here. The docstring's own third suspect was right:
    fill_cashflow's sell-side convention. See the CASH-FLOW MODEL block above. This function
    is CORRECT and is now independently confirmed: reconstructing the net position from the
    fill tape with the corrected convention predicts `revenue` to the cent on 51/51
    settlements, including every non-zero one ($22.00, $2.93, $0.31, $0.29, $0.23, $0.12,
    $0.06). H1's "revenue under-reports payouts" is refuted a second time, from a new
    direction. Do NOT substitute winning-side-count x $1 here."""
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
    # AUDIT PROBE (2026-07-30, watermark rename hazard): `or ""` makes a venue rename of the
    # timestamp fields silently drop EVERY row forever ("" is never > watermark). Count and warn.
    _no_ts = sum(1 for s in all_settles if s.get("settled_time") is None) + \
             sum(1 for f in all_fills if f.get("created_time") is None)
    if _no_ts:
        print(f"WARNING {_no_ts} fills/settlements rows have NO timestamp field — venue "
              f"rename? Those rows are being DROPPED from the ledger window.")
    settles = [s for s in all_settles if (s.get("settled_time") or "") > sw]

    # Cash is POSITION-AWARE (see the CASH-FLOW MODEL block), so it must be replayed from the
    # FULL tape — the position a windowed fill nets against was built by earlier fills.
    all_events, recon_pos = replay_fills(all_fills)
    events = [e for e in all_events if (e["fill"].get("created_time") or "") > fw]
    fills = [e["fill"] for e in events]

    # SELF-CHECK, every run. The cash model and the position reconstruction share one sign
    # convention, so if the venue's own position_fp disagrees with the replay, the cash number
    # in this row is wrong too — and that is exactly the failure that ran undetected for days.
    # Also catches a TRUNCATED tape (get_paginated caps at 50 x 200 fills; truncation drops the
    # OLDEST fills, which silently corrupts the starting position of long-lived tickers).
    settled_tickers = {s.get("ticker") for s in all_settles}
    venue_pos = {p.get("ticker"): _f(p.get("position_fp") or p.get("position")) for p in pos}
    checkable = set(venue_pos) | {t for t, v in recon_pos.items()
                                  if abs(v) > 0.005 and t not in settled_tickers}
    recon_mismatch = {t: [round(venue_pos.get(t, 0.0), 4), round(recon_pos.get(t, 0.0), 4)]
                      for t in sorted(checkable)
                      if abs(venue_pos.get(t, 0.0) - recon_pos.get(t, 0.0)) > 0.005}
    if recon_mismatch:
        print(f"WARNING position reconstruction disagrees with the venue on "
              f"{len(recon_mismatch)} ticker(s) [venue, replayed]: {recon_mismatch} — "
              f"fills_cash and rewards_residual in this row are NOT trustworthy")

    by_series_trade = defaultdict(lambda: {"cash": 0.0, "maker_ct": 0.0, "taker_ct": 0.0})
    fills_cash = 0.0
    fills_fees = 0.0
    for e in events:
        f, c = e["fill"], e["cash"]
        fills_cash += c
        fills_fees += fill_fee(f)
        row = by_series_trade[series_of(f.get("ticker"))]
        row["cash"] += c
        cnt = fill_count(f)
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
        "fills_fees": round(fills_fees, 4),             # MEASURED (already inside fills_cash)
        "new_settlements": len(settles), "settle_revenue": round(settle_rev, 4),  # MEASURED
        "position_recon_mismatch": recon_mismatch,      # {} = cash model agrees with venue
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
          f"(cash {fills_cash:+.2f}, fees {fills_fees:.4f}) settles={len(settles)} (+{settle_rev:.2f}) "
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
        # ⚠ 2026-07-23 — THE PREMISE OF THIS FILTER IS REFUTED, the filter is kept anyway.
        # The claim was: resting orders RESERVE collateral out of balance_dollars (probe
        # 07-22: balance $42.93 -> $24.79 on placing 8 quotes), so quote churn would be
        # misread as rewards. Measured against the live ledger with the corrected cash model:
        # 24 of 27 intervals residual EXACTLY $0.0000, including 07-22T13:44 (presence
        # $14.10 -> $31.32, 1 -> 5 resting orders, ZERO fills) and 07-22T16:44 (presence
        # $27.22 -> $46.78, 3 -> 8 orders, ZERO fills). Balance did not move on either. The
        # original probe was confounded by fills. So the running tab's old note was RIGHT:
        # resting orders do NOT deduct from balance_dollars.
        # Left in place because it only costs coverage, never correctness, and because the
        # residual above is now the trustworthy number — this whole clean-interval method was
        # a workaround for the residual being broken and is superseded, not load-bearing.
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

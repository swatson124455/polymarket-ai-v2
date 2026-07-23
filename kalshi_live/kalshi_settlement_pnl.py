#!/usr/bin/env python3
"""Kalshi SETTLEMENT P&L ATTRIBUTOR -- READ-ONLY. GETs only; can never trade or cancel.

WHY THIS FILE EXISTS
--------------------
A straightforward reading of `/portfolio/settlements` scored the KXAAAGASD-26JUL23 event at
**-$442** on an $85 account. That number is impossible, therefore it is wrong (it is exactly the
event's `yes_total_cost_dollars + no_total_cost_dollars` = $442.4256, see `naive_row_pnl`).

ROOT CAUSE -- THE FIELDS ARE CUMULATIVE LIFETIME GROSS, NOT NET POSITION.
`yes_count_fp` / `no_count_fp` / `yes_total_cost_dollars` / `no_total_cost_dollars` are the
whole-life tallies of everything that ever traded on that contract. Verified against the fill
tape, 51/51 settlements, exactly:

    yes_count_fp            == SUM(count_fp)                    over action=buy , side=yes
    no_count_fp             == SUM(count_fp)                    over action=sell, side=no
    yes_total_cost_dollars  == SUM(yes_price_dollars * count)   over action=buy , side=yes
    no_total_cost_dollars   == SUM(no_price_dollars  * count)   over action=sell, side=no
    fee_cost                == SUM(fee_cost)  over ALL fills on that contract  (51/51)
                               i.e. it is LIFETIME TRADING fees, NOT a settlement fee
                               (canon sec.M9 verbatim: "There is no settlement fee")

So KXAAAGASD-26JUL23-4.100 with yes_count 233.52 / no_count 233.75 is a book that was traded
flat 233 times over, not a $234 position. Its NET is 0.23 contracts.

UNITS (canon sec.M7f, same trap family as R1): `revenue` and `value` are integer **CENTS**;
every `*_dollars` field is a **dollar string**. Divide `revenue` by 100 exactly once.

THE THREE IDENTITIES THIS MODULE IS BUILT ON (all measured, none assumed)
------------------------------------------------------------------------
(1) NET POSITION is yes-signed:      net = yes_count_fp - no_count_fp
    positive = holding YES, negative = holding NO. A `sell no` fill DECREMENTS the YES
    position -- receipt-proof: `kalshi_transactions_2026-07-23.csv` records the 18:06:47Z
    `sell no @ no=0.40` on KXAAAGASD-26JUL21-4.020 as a YES lot exiting at **0.60**.

(2) REVENUE RECONCILES to the net position on the winning side:
        revenue/100 == max(0, +net) if result == "yes"
        revenue/100 == max(0, -net) if result == "no"
    **51/51 exact** on live data (2026-07-23). This is the check that kills the -$442 reading:
    -4.100 nets 0.23 NO into a "no" result and the venue pays exactly $0.23.

(3) LIFETIME P&L of a settled contract -- two independent derivations, agreeing to the cent on
    51/51 contracts and totalling -$87.5946 either way:

    MODEL A (settlement row alone, `settlement_row_pnl`):
        pnl = revenue$ + min(yes_count, no_count) * $1
              - yes_total_cost - no_total_cost - fee_cost
      Rationale: a YES and a NO on the same contract auto-net and pay $1 the moment they meet,
      and over a contract's life the number of such pairs is exactly min(gross_yes, gross_no).
      Everything else is the gross cash paid out.

    MODEL B (fill tape replay, `tape_pnl`): weighted-average-cost lot accounting in yes-price
      space, then mark the surviving position to the settlement outcome.
      Model B is independently validated against the VENUE'S OWN `realized_pnl_dollars` on
      `/portfolio/positions`: **6/6 markets exact**, and `position_fp` **6/6 exact**.

CROSS-VALIDATION AGAINST THE RECEIPT-GRADE CSV
---------------------------------------------
`kalshi_transactions_2026-07-23.csv` `trade` rows carry `realized_pnl_with_fees_dollars` per
closed lot, and Kalshi books a settlement as a closing lot at 0.00/1.00 -- so for a contract that
settled inside the export window the CSV sum IS the full lifetime P&L. Measured: **47/47 exact
(<=$0.01)** on the 47 contracts that settled at/before the export cutoff 2026-07-23T03:38:05Z.
The other 4 (the whole KXAAAGASD-26JUL23 event) settled at 12:25:36Z, AFTER the export was
taken, so the CSV is missing their settlement leg -- they are excluded by timestamp, not by
convenience. `crossvalidate_csv` enforces that cutoff rule in code.

!! INTERIM realized P&L differs by CONVENTION and that is not an error: the CSV export uses FIFO
lots, `/portfolio/positions` and this module use weighted-average cost. They disagree while a
position is open (e.g. -4.105 interim: CSV -$0.2300 vs WAC +$0.0150) and agree exactly once the
position is flat or settled. Only compare LIFETIME totals across the two sources.

DECOMPOSITION -- "settlement leg" vs "lifetime"
----------------------------------------------
    lifetime_pnl = realized_pnl(lots closed while trading) + settlement_leg_pnl(what was carried)
    settlement_leg_pnl = revenue$ - carried_cost_basis
These are DIFFERENT NUMBERS and both get quoted; say which one you mean.
For KXAAAGASD-26JUL23: carried basis $8.4932, leg **-$8.2032**, realized +$0.7376,
lifetime **-$7.4656**.

Usage (read-only):
    python kalshi_settlement_pnl.py                 # live GETs, per-event table + all checks
    python kalshi_settlement_pnl.py --snapshot DIR  # offline: DIR/settlements.json, DIR/fills.json
    python kalshi_settlement_pnl.py --save DIR      # live, and write the snapshot for replay
"""
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "kalshi_transactions_2026-07-23.csv")

# Export cutoff of kalshi_transactions_2026-07-23.csv = its newest `close_timestamp`
# (2026-07-22T23:38:05-04:00). Contracts that settled after this are NOT comparable to the CSV
# because their settlement leg had not happened when the operator pulled the export.
CSV_EXPORT_CUTOFF_UTC = "2026-07-23T03:38:05"

CENT = 0.005          # half a cent -- the tolerance for "matches to the cent"


def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def event_of(ticker):
    """sec.T: event = first two dash-segments. ONE CORRELATED RISK."""
    return "-".join((ticker or "").split("-")[:2])


def series_of(ticker):
    return (ticker or "").split("-")[0]


# --------------------------------------------------------------------------------------------
# settlement-row primitives
# --------------------------------------------------------------------------------------------
def revenue_dollars(row):
    """`revenue` is integer CENTS (canon sec.M7f). Divide once, here, and nowhere else."""
    return _f(row.get("revenue")) / 100.0


def net_position(row):
    """Yes-signed NET contracts carried into settlement: + = held YES, - = held NO.

    The whole -$442 class of error is reading yes_count_fp/no_count_fp as a position instead of
    as cumulative lifetime gross. They are gross; the difference is the position."""
    return _f(row.get("yes_count_fp")) - _f(row.get("no_count_fp"))


def matched_pairs(row):
    """Contracts that met an opposite contract over the contract's life and auto-netted at $1.

    min(gross_yes, gross_no) is exact regardless of interleaving order: every unit of one side
    either meets a unit of the other side exactly once, or survives to settlement."""
    return min(_f(row.get("yes_count_fp")), _f(row.get("no_count_fp")))


def expected_revenue_dollars(row):
    """What the venue MUST have paid, derived from net position + market_result."""
    net = net_position(row)
    result = row.get("market_result")
    if result == "yes":
        return max(0.0, net)
    if result == "no":
        return max(0.0, -net)
    return 0.0          # void / unknown result: no directional payout to predict


def revenue_reconciles(row, tol=CENT):
    return abs(expected_revenue_dollars(row) - revenue_dollars(row)) <= tol


def reconcile_revenue(rows, tol=CENT):
    """-> (matched, total, [(ticker, expected, actual), ...]) for every settlement row."""
    bad = []
    for r in rows:
        if not revenue_reconciles(r, tol):
            bad.append((r.get("ticker"), expected_revenue_dollars(r), revenue_dollars(r)))
    return len(rows) - len(bad), len(rows), bad


def settlement_row_pnl(row):
    """MODEL A -- lifetime realised P&L of one settled contract, AFTER fees, from the row alone.

    fee_cost on a settlement row is the contract's LIFETIME TRADING fees (verified 51/51 against
    SUM(fill.fee_cost)), not a settlement charge -- subtracting it here is correct and complete."""
    return (revenue_dollars(row)
            + matched_pairs(row)
            - _f(row.get("yes_total_cost_dollars"))
            - _f(row.get("no_total_cost_dollars"))
            - _f(row.get("fee_cost")))


def naive_row_pnl(row):
    """THE BUG, KEPT ON PURPOSE AS A PINNED COUNTER-EXAMPLE. DO NOT USE FOR ANY REPORT.

    Reads the cumulative lifetime cost as if it were the cost of a held position -- i.e. omits
    `matched_pairs`, the $1 that every auto-netted YES/NO pair already paid back. This is the
    reading that scored KXAAAGASD-26JUL23 at -$442.14 on an $85 account. Exported so tests can
    assert the impossible number is produced by the naive formula and NOT by the real one."""
    return (revenue_dollars(row)
            - _f(row.get("yes_total_cost_dollars"))
            - _f(row.get("no_total_cost_dollars"))
            - _f(row.get("fee_cost")))


# --------------------------------------------------------------------------------------------
# fill-tape replay (MODEL B) -- weighted-average cost in YES-price space
# --------------------------------------------------------------------------------------------
def fill_yes_price(fill):
    """Effective YES price of a fill. Every fill we have ever generated is buy-yes or sell-no,
    and Kalshi itself books `sell no @ no=q` as a YES exit at 1-q (receipt: the CSV). The payload
    already carries that number in `yes_price_dollars`, so read it rather than deriving it."""
    yp = _f(fill.get("yes_price_dollars"))
    if yp > 1.0:                                  # cents-scale guard, same family as R1
        yp = yp / 100.0
    return yp


def fill_signed_qty(fill):
    """+ = increases the yes-signed position, - = decreases it (crossing zero into NO is legal:
    `position_fp` is negative for NO holdings -- venue-verified 6/6)."""
    c = _f(fill.get("count_fp") or fill.get("count"))
    return c if fill.get("action") == "buy" else -c


def replay(fills):
    """-> (position, avg_yes_cost, realized_pnl_before_fees, fees_paid) for ONE contract.

    Weighted-average cost, yes-signed. Matches the venue's own `realized_pnl_dollars` and
    `position_fp` on `/portfolio/positions` exactly (6/6 open markets, 2026-07-23)."""
    pos = avg = realized = fees = 0.0
    for f in sorted(fills, key=lambda r: (r.get("created_time") or "", r.get("fill_id") or "")):
        q = fill_signed_qty(f)
        px = fill_yes_price(f)
        fee = abs(_f(f.get("fee_cost")))
        if fee > 1000:                            # cents-scale guard (no _dollars suffix)
            fee = fee / 100.0
        fees += fee
        if q == 0:
            continue
        if pos == 0 or (pos > 0) == (q > 0):      # opening / adding
            total = abs(pos) + abs(q)
            avg = (avg * abs(pos) + px * abs(q)) / total
            pos += q
        else:                                     # reducing (possibly through zero)
            closed = min(abs(pos), abs(q))
            realized += (px - avg) * closed if pos > 0 else (avg - px) * closed
            new = pos + q
            if new == 0:
                avg = 0.0
            elif (new > 0) != (pos > 0):          # flipped sides: the remainder opens at px
                avg = px
            pos = new
    return pos, avg, realized, fees


def carried_cost_basis(position, avg_yes_cost):
    """Cost basis of the position carried into settlement, in the venue's own terms:
    a NO holding's basis is (1 - avg_yes_cost) per contract."""
    if position > 0:
        return position * avg_yes_cost
    if position < 0:
        return (-position) * (1.0 - avg_yes_cost)
    return 0.0


def settlement_leg_pnl(position, avg_yes_cost, market_result):
    """P&L of the SETTLEMENT LEG ONLY = revenue - carried cost basis.

    This is NOT the contract's lifetime P&L -- it excludes everything realised while trading.
    Quoting one for the other is how -$8.20 and -$7.47 both became "the" KXAAAGASD-26JUL23
    number in the same day."""
    win = 1.0 if market_result == "yes" else 0.0
    return position * (win - avg_yes_cost)


def tape_pnl(fills, market_result):
    """MODEL B -- lifetime P&L of one settled contract from the fill tape, AFTER fees."""
    pos, avg, realized, fees = replay(fills)
    return realized + settlement_leg_pnl(pos, avg, market_result) - fees


def models_agree(row, fills, tol=CENT):
    return abs(settlement_row_pnl(row) - tape_pnl(fills, row.get("market_result"))) <= tol


# --------------------------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------------------------
def per_contract(rows, fills_by_ticker=None):
    """-> list of dicts, one per settled contract, richest form available."""
    out = []
    for r in rows:
        t = r.get("ticker")
        rec = {
            "ticker": t,
            "event": event_of(t),
            "series": series_of(t),
            "settled_time": r.get("settled_time"),
            "result": r.get("market_result"),
            "gross_yes": _f(r.get("yes_count_fp")),
            "gross_no": _f(r.get("no_count_fp")),
            "net_position": net_position(r),
            "gross_cost": _f(r.get("yes_total_cost_dollars")) + _f(r.get("no_total_cost_dollars")),
            "fees": _f(r.get("fee_cost")),
            "revenue": revenue_dollars(r),
            "revenue_expected": expected_revenue_dollars(r),
            "revenue_ok": revenue_reconciles(r),
            "pnl": settlement_row_pnl(r),
            "naive_pnl": naive_row_pnl(r),
        }
        fl = (fills_by_ticker or {}).get(t)
        if fl:
            pos, avg, realized, fees = replay(fl)
            rec.update({
                "tape_position": pos,
                "avg_yes_cost": avg,
                "realized_while_trading": realized,
                "carried_cost_basis": carried_cost_basis(pos, avg),
                "settlement_leg_pnl": settlement_leg_pnl(pos, avg, r.get("market_result")),
                "tape_pnl": realized + settlement_leg_pnl(pos, avg, r.get("market_result")) - fees,
            })
        out.append(rec)
    return out


def per_event(contracts):
    """sec.T: risk aggregates at the EVENT. -> {event: {...}} """
    ev = defaultdict(lambda: {"contracts": 0, "pnl": 0.0, "revenue": 0.0, "gross_cost": 0.0,
                              "fees": 0.0, "naive_pnl": 0.0, "settlement_leg_pnl": 0.0,
                              "realized_while_trading": 0.0, "carried_cost_basis": 0.0,
                              "has_tape": True})
    for c in contracts:
        e = ev[c["event"]]
        e["contracts"] += 1
        for k in ("pnl", "revenue", "gross_cost", "fees", "naive_pnl"):
            e[k] += c[k]
        if "tape_pnl" in c:
            for k in ("settlement_leg_pnl", "realized_while_trading", "carried_cost_basis"):
                e[k] += c[k]
        else:
            e["has_tape"] = False
    return dict(ev)


# --------------------------------------------------------------------------------------------
# CSV cross-validation (receipt-grade)
# --------------------------------------------------------------------------------------------
def load_csv_realized(path=CSV_PATH):
    """-> {ticker: sum(realized_pnl_with_fees_dollars)} over `trade` rows.

    Kalshi books a settlement as a closing lot at 0.00/1.00, so for a contract that settled
    inside the export window this sum is the full LIFETIME P&L after fees."""
    import csv
    tot = defaultdict(float)
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("type") != "trade":
                continue
            tot[r.get("market_ticker")] += _f(r.get("realized_pnl_with_fees_dollars"))
    return dict(tot)


def crossvalidate_csv(rows, csv_totals=None, cutoff=CSV_EXPORT_CUTOFF_UTC, tol=0.011):
    """-> (matched, compared, diffs, excluded_after_cutoff).

    Only contracts that settled at/before the export cutoff are comparable -- after it, the CSV
    has the trading but not the settlement leg, and a mismatch there is the EXPORT being
    incomplete, not the model being wrong. Excluding by timestamp is a rule, not a judgement
    call: it is applied before any P&L is looked at."""
    csv_totals = load_csv_realized() if csv_totals is None else csv_totals
    diffs, matched, compared, excluded = [], 0, 0, []
    for r in rows:
        t = r.get("ticker")
        if t not in csv_totals:
            continue
        if (r.get("settled_time") or "")[:19] > cutoff:
            excluded.append(t)
            continue
        compared += 1
        d = csv_totals[t] - settlement_row_pnl(r)
        if abs(d) <= tol:
            matched += 1
        else:
            diffs.append((t, csv_totals[t], settlement_row_pnl(r), d))
    return matched, compared, diffs, excluded


# --------------------------------------------------------------------------------------------
# I/O -- read-only
# --------------------------------------------------------------------------------------------
def fetch_live():
    """GETs only. Imports the ledger's signed-GET helpers rather than duplicating them."""
    import kalshi_attribution_ledger as L
    settlements = L.get_paginated(L.P + "/portfolio/settlements", "settlements")
    fills = L.get_paginated(L.P + "/portfolio/fills", "fills")
    return settlements, fills


def load_snapshot(d):
    with open(os.path.join(d, "settlements.json")) as fh:
        settlements = json.load(fh)
    fills = []
    fp = os.path.join(d, "fills.json")
    if os.path.exists(fp):
        with open(fp) as fh:
            fills = json.load(fh)
    if isinstance(settlements, dict):
        settlements = settlements.get("settlements") or []
    if isinstance(fills, dict):
        fills = fills.get("fills") or []
    return settlements, fills


def group_fills(fills):
    by = defaultdict(list)
    for f in fills:
        by[f.get("ticker") or f.get("market_ticker")].append(f)
    return dict(by)


def main(argv):
    snap = None
    save = None
    if "--snapshot" in argv:
        snap = argv[argv.index("--snapshot") + 1]
    if "--save" in argv:
        save = argv[argv.index("--save") + 1]

    if snap:
        settlements, fills = load_snapshot(snap)
        src = f"snapshot {snap}"
    else:
        settlements, fills = fetch_live()
        src = "live GET /portfolio/{settlements,fills}"
        if save:
            os.makedirs(save, exist_ok=True)
            json.dump(settlements, open(os.path.join(save, "settlements.json"), "w"))
            json.dump(fills, open(os.path.join(save, "fills.json"), "w"))

    byt = group_fills(fills)
    contracts = per_contract(settlements, byt)
    events = per_event(contracts)

    print(f"source: {src}")
    print(f"settled contracts: {len(settlements)}   fills: {len(fills)}")

    ok, tot, bad = reconcile_revenue(settlements)
    print(f"\n[1] REVENUE RECONCILIATION  revenue == net_position_on_winning_side x $1")
    print(f"    {ok}/{tot} exact ({100.0 * ok / tot if tot else 0:.1f}%)")
    for t, e, a in bad:
        print(f"      UNEXPLAINED {t}: expected ${e:.2f} actual ${a:.2f}")

    agree = sum(1 for c in contracts if "tape_pnl" in c and abs(c["pnl"] - c["tape_pnl"]) <= CENT)
    withtape = sum(1 for c in contracts if "tape_pnl" in c)
    print(f"\n[2] MODEL A (row) vs MODEL B (tape replay): {agree}/{withtape} agree to the cent")

    m, comp, diffs, excl = crossvalidate_csv(settlements)
    print(f"\n[3] CSV CROSS-VALIDATION (receipt-grade, {os.path.basename(CSV_PATH)})")
    print(f"    {m}/{comp} exact on contracts settled at/before the export cutoff {CSV_EXPORT_CUTOFF_UTC}Z")
    if excl:
        print(f"    excluded {len(excl)} settled AFTER the cutoff (CSV lacks their settlement leg): "
              f"{', '.join(sorted(excl))}")
    for t, c, m_, d in diffs:
        print(f"      DISAGREE {t}: csv {c:+.4f} model {m_:+.4f} delta {d:+.4f}")

    print(f"\n[4] PER-EVENT LIFETIME P&L (after fees)   sec.T event = one correlated risk")
    print(f"    {'event':<28} {'n':>3} {'lifetime':>10} {'revenue':>9} {'grosscost':>10} "
          f"{'settle-leg':>11} {'realized':>10} {'NAIVE(bad)':>11}")
    for e in sorted(events, key=lambda k: events[k]["pnl"]):
        v = events[e]
        leg = f"{v['settlement_leg_pnl']:+11.4f}" if v["has_tape"] else f"{'n/a':>11}"
        real = f"{v['realized_while_trading']:+10.4f}" if v["has_tape"] else f"{'n/a':>10}"
        print(f"    {e:<28} {v['contracts']:>3} {v['pnl']:+10.4f} {v['revenue']:9.2f} "
              f"{v['gross_cost']:10.2f} {leg} {real} {v['naive_pnl']:+11.2f}")
    tot_pnl = sum(v["pnl"] for v in events.values())
    tot_naive = sum(v["naive_pnl"] for v in events.values())
    print(f"    {'TOTAL':<28} {len(contracts):>3} {tot_pnl:+10.4f} "
          f"{sum(v['revenue'] for v in events.values()):9.2f} "
          f"{sum(v['gross_cost'] for v in events.values()):10.2f} {'':>11} {'':>10} {tot_naive:+11.2f}")
    print(f"\n    NAIVE(bad) is `naive_row_pnl` -- the cumulative-cost misreading, shown ONLY so the")
    print(f"    size of the error stays visible. It is not a P&L. Never quote that column.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Kalshi Maker NET-EV CALIBRATION ENGINE — receipt-grade per-family "does this make us money".

READ-ONLY. Re-runnable each transaction-CSV export. Automates canon §M8: from the Kalshi
transaction CSV it computes, per FAMILY (gas / temp / ...), the REALIZED NET over the credit-settled
window:

    NET     = credits(family)  +  trading_P&L(family, in-window, maker-only)      [fees already in trading_P&L]
    net_%   = NET / notional(family, in-window)

and emits a calibration table (JSON) the live quoter loads to gate quoting on ACTUAL receipts rather
than the R4 model (which §M7 shows over-predicts 2–6×). The model stays a FALLBACK only, for a NEW
family with no receipt history (flagged confidence="unproven").

WHY THIS SHAPE — the honest data constraint (canon §M8 / §M11 / §M13):

  * FILL P&L is measurable per-SERIES precisely: every `trade` row carries `market_ticker` and
    `realized_pnl_with_fees_dollars` (venue tape). We sum it per family.
  * CREDITS are NOT per-series attributable from the CSV: `credit` rows carry an EMPTY
    `market_ticker` (Kalshi's §M11 fix is unshipped). Only the CSV credit TOTAL is exact. Per-FAMILY
    credit attribution therefore comes from the operator's web-UI screenshots (§M8 attributed the
    $25.21 total to GAS $2.15 / TEMP $23.06 that way). This engine takes that attribution as an
    INPUT (`credit_attribution`) and CROSS-CHECKS its sum against the CSV credit total — the same
    validation §M8 did by hand ($25.21 == $25.21). => calibration is reliable at the FAMILY level.

  * §M13 EXCLUSION 1 — one-off emergency-unwind TAKER rows are not steady state. On our fee-exempt
    series (canon §M10) *any fee-bearing row is a TAKER trade* (a forced exit). `exclude_taker=True`
    drops them so a 4-minute operator IOC flatten cannot poison the family verdict.
  * §M13 EXCLUSION 2 — credits LAG a Time Period (post after the period closes). So trading is scored
    only over the CREDIT-SETTLED window: the date range spanned by the credit-posting rows in THIS
    export (auto-derived; overridable). Days outside it (e.g. the pre-fix 07-20 go-live day, which
    carries no credits) are excluded. A family whose latest period had not closed at export time
    (e.g. gas-weekly, §M13) is flagged `credit_lag=True` — its credits are under-counted, biasing its
    net PESSIMISTIC (a conservative direction for a gate).

VALIDATION: on `kalshi_transactions_2026-07-23.csv` with the §M8 screenshot attribution this
reproduces the §M8 signs — GAS net +1.1% (positive), TEMP net −9.2% (negative) — exactly
(+0.25 trading / 214.85 notional GAS ; −36.12 / 142.67 TEMP). See test_netev_gate.py::T5.

CLI:
    python kalshi_netev_calibrate.py kalshi_transactions_2026-07-23.csv        # print JSON
    python kalshi_netev_calibrate.py <csv> --out kalshi_netev_table.json       # + write table
    python kalshi_netev_calibrate.py <csv> --credits credits.json              # override attribution
"""
import argparse
import csv as _csv
import json
import os
from datetime import datetime, timezone

# ---- family mapping (shared contract with the quoter's _netev_family; pinned equal in tests) ----
# Ordered longest-first so a more specific prefix wins. A ticker matching no rule is its own family
# (series root = text before the first '-'), which the gate treats as UNPROVEN until it earns
# receipt history.
DEFAULT_FAMILY_RULES = (
    ("KXAAAGAS", "gas"),
    ("KXTEMP", "temp"),
)

# §M8 screenshot-derived credit attribution for the 2026-07-23 export. Credits carry an EMPTY
# market_ticker in the CSV (§M11), so this family split is the operator-UI attribution, NOT
# CSV-derived. The engine CROSS-CHECKS that its sum equals the CSV credit total (exact in §M8).
# Refresh this map from the web-UI screenshots on every new export.
M8_CREDIT_ATTRIBUTION = {"gas": 2.15, "temp": 23.06}

# a family is "receipt"-grade only with at least this many in-window trades AND attributed credits;
# fewer trades -> "thin"; no attributed credits -> "unproven" (gate falls back to the R4 model).
MIN_RECEIPT_TRADES = 20


def family_of(ticker, rules=DEFAULT_FAMILY_RULES):
    """Family bucket for a market ticker. E.g. KXAAAGASD-26JUL23-4.020 -> 'gas',
    KXTEMPCHIH-26JUL2212-T69.99 -> 'temp'. Unmatched -> series root (text before first '-')."""
    t = ticker or ""
    for prefix, fam in rules:
        if t.startswith(prefix):
            return fam
    return t.split("-")[0]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _date(ts):
    """YYYY-MM-DD of an ISO timestamp string (e.g. 2026-07-21T01:36:39-04:00 -> '2026-07-21')."""
    return (ts or "")[:10]


def read_rows(csv_path):
    with open(csv_path, newline="") as fh:
        return list(_csv.DictReader(fh))


def calibrate(csv_path, credit_attribution=None, window_start=None, window_end=None,
              exclude_taker=True, rules=DEFAULT_FAMILY_RULES, credit_lag_families=("gas",)):
    """Compute the per-family net-EV calibration table from a Kalshi transaction CSV.

    credit_attribution : {family: dollars} from operator UI screenshots (empty-ticker credits can't
                         be auto-attributed). Defaults to the §M8 map. Its sum is cross-checked
                         against the CSV credit total.
    window_start/_end  : 'YYYY-MM-DD' inclusive trading-day bounds. Default = the credit-settled
                         window = the date range spanned by this export's credit rows.
    exclude_taker      : drop fee-bearing (=taker, on our fee-exempt series) rows (§M13 exclusion 1).
    credit_lag_families: families whose latest Time Period may not have closed at export (gas-weekly,
                         §M13) -> flagged credit_lag=True (their net is biased pessimistic).

    Returns the JSON-serializable calibration dict (schema documented at module bottom)."""
    if credit_attribution is None:
        credit_attribution = dict(M8_CREDIT_ATTRIBUTION)
    rows = read_rows(csv_path)

    # --- credits: total is exact from the CSV; per-family split is the screenshot attribution ---
    credit_total_csv = sum(_f(r["realized_pnl_with_fees_dollars"])
                           for r in rows if r.get("type") == "credit")
    credit_total_attr = sum(credit_attribution.values())
    credit_ok = abs(credit_total_csv - credit_total_attr) < 0.01

    # --- credit-settled window: default to the span of credit-posting dates in this export ---
    credit_dates = sorted({_date(r.get("close_timestamp"))
                           for r in rows if r.get("type") == "credit" and _date(r.get("close_timestamp"))})
    if window_start is None:
        window_start = credit_dates[0] if credit_dates else None
    if window_end is None:
        window_end = credit_dates[-1] if credit_dates else None

    fam = {}     # family -> accumulator

    def acc(f):
        return fam.setdefault(f, {"n_trades": 0, "trading_pnl": 0.0, "notional": 0.0, "fees": 0.0,
                                  "excluded_taker_trades": 0, "excluded_taker_pnl": 0.0,
                                  "out_of_window_trades": 0})

    for r in rows:
        if r.get("type") != "trade":
            continue
        f = family_of(r.get("market_ticker", ""), rules)
        a = acc(f)
        d = _date(r.get("close_timestamp"))
        fees = _f(r.get("open_fees_dollars")) + _f(r.get("close_fees_dollars"))
        pnl = _f(r.get("realized_pnl_with_fees_dollars"))
        # window filter (credit-settled window; excludes e.g. the pre-fix emergency-unwind day)
        if (window_start and d < window_start) or (window_end and d > window_end):
            a["out_of_window_trades"] += 1
            continue
        # §M13 taker exclusion: fee-bearing == taker on our fee-exempt series == forced exit
        if exclude_taker and fees > 0:
            a["excluded_taker_trades"] += 1
            a["excluded_taker_pnl"] += pnl
            continue
        a["n_trades"] += 1
        a["trading_pnl"] += pnl
        a["notional"] += _f(r.get("entry_price_dollars")) * _f(r.get("quantity_fp"))
        a["fees"] += fees

    # window day-span (inclusive) for net_per_day
    days = 1
    if window_start and window_end:
        try:
            days = max(1, (datetime.strptime(window_end, "%Y-%m-%d")
                           - datetime.strptime(window_start, "%Y-%m-%d")).days + 1)
        except ValueError:
            days = 1

    families = {}
    for f, a in sorted(fam.items()):
        credits = float(credit_attribution.get(f, 0.0))
        has_credit = f in credit_attribution
        net = round(a["trading_pnl"] + credits, 4)
        notional = round(a["notional"], 4)
        net_pct = round(net / notional, 6) if notional > 0 else None
        if not has_credit:
            conf = "unproven"                      # no receipt credits -> gate uses the R4 model
        elif a["n_trades"] >= MIN_RECEIPT_TRADES:
            conf = "receipt"
        else:
            conf = "thin"
        families[f] = {
            "net": net,
            "net_pct_notional": net_pct,
            "net_per_day": round(net / days, 4),
            "n_trades": a["n_trades"],
            "notional": notional,
            "trading_pnl": round(a["trading_pnl"], 4),
            "credits": round(credits, 4),
            "fees": round(a["fees"], 4),
            "confidence": conf,
            "credit_attributed": has_credit,
            "credit_lag": f in credit_lag_families,
            "excluded_taker_trades": a["excluded_taker_trades"],
            "excluded_taker_pnl": round(a["excluded_taker_pnl"], 4),
            "out_of_window_trades": a["out_of_window_trades"],
        }

    caveats = [
        "Credits LAG a Time Period (§M13): trading is scored only over the credit-settled window "
        f"[{window_start}..{window_end}] = the span of this export's credit-posting dates.",
        "Per-FAMILY credit attribution is operator-UI screenshot-derived (credit rows carry an EMPTY "
        "market_ticker, §M11); only the CSV credit TOTAL is exact. Per-SERIES credits are NOT "
        "available. The engine cross-checks the attribution sum against the CSV total.",
        "Taker (fee-bearing) rows excluded as one-off forced exits (§M13 exclusion 1)."
        if exclude_taker else "Taker rows INCLUDED (exclude_taker=False).",
    ]
    lag = [f for f in families if families[f]["credit_lag"]]
    if lag:
        caveats.append("credit_lag families (period may be open at export -> credits under-counted, "
                       "net biased pessimistic): " + ", ".join(sorted(lag)) + ".")
    if not credit_ok:
        caveats.append(f"⚠ CREDIT ATTRIBUTION MISMATCH: attributed {credit_total_attr:.2f} != CSV "
                       f"total {credit_total_csv:.2f} — refresh the screenshot attribution.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "csv_path": os.path.basename(csv_path),
        "window": {"start": window_start, "end": window_end, "days": days},
        "exclude_taker": exclude_taker,
        "credit_total_csv": round(credit_total_csv, 4),
        "credit_total_attributed": round(credit_total_attr, 4),
        "credit_attribution_ok": credit_ok,
        "min_receipt_trades": MIN_RECEIPT_TRADES,
        "caveats": caveats,
        "families": families,
    }


def load_table(path):
    """Load the {family -> row} map the quoter gate consults. Fail-OPEN: any error -> {} (the gate
    then treats every family as UNPROVEN and falls back to the model — never blocks on a bad file)."""
    try:
        with open(path) as fh:
            data = json.load(fh)
        fams = data.get("families", data)
        return fams if isinstance(fams, dict) else {}
    except Exception:
        return {}


def write_table(result, out_path):
    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kalshi net-EV per-family calibration (read-only).")
    ap.add_argument("csv", help="Kalshi transaction CSV export")
    ap.add_argument("--out", help="write the calibration table JSON to this path")
    ap.add_argument("--credits", help="JSON {family: dollars} credit attribution (override §M8 map)")
    ap.add_argument("--window-start", help="YYYY-MM-DD inclusive (default: first credit date)")
    ap.add_argument("--window-end", help="YYYY-MM-DD inclusive (default: last credit date)")
    ap.add_argument("--include-taker", action="store_true", help="do NOT drop taker rows (§M13)")
    a = ap.parse_args(argv)
    credit_attribution = None
    if a.credits:
        with open(a.credits) as fh:
            credit_attribution = json.load(fh)
    res = calibrate(a.csv, credit_attribution=credit_attribution,
                    window_start=a.window_start, window_end=a.window_end,
                    exclude_taker=not a.include_taker)
    print(json.dumps(res, indent=2, sort_keys=True))
    if a.out:
        write_table(res, a.out)
    return res


# Output JSON schema (families[<family>]):
#   net                     realized net $ over the window  = trading_pnl + credits
#   net_pct_notional        net / in-window notional        <-- the GATE SIGNAL (None if no notional)
#   net_per_day             net / window days
#   n_trades                in-window, taker-excluded trade count
#   notional, trading_pnl, credits, fees
#   confidence              "receipt" (>=MIN_RECEIPT_TRADES + attributed credits) | "thin" | "unproven"
#   credit_attributed       family had a screenshot credit entry
#   credit_lag              latest period may be open at export (net biased pessimistic)
#   excluded_taker_trades / _pnl, out_of_window_trades   (audit trail for the exclusions)
if __name__ == "__main__":
    main()

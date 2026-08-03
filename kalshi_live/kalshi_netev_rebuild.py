#!/usr/bin/env python3
"""NET-EV TABLE REBUILD FROM RECEIPTS — API-sourced, no CSV and no screenshots.

Companion to kalshi_netev_calibrate.py (which stays as the CSV path and its validation).
This builds the SAME table shape from pure venue reads, which closes the two data gaps the
CSV engine had to work around:

  * PER-FAMILY CREDIT ATTRIBUTION was operator-UI screenshot-derived, because CSV credit rows
    carry an EMPTY market_ticker (canon §M11). credit_history carries
    `reason = "Liquidity Incentive for event <EVENT_TICKER>"`, so attribution is now EXACT and
    per-EVENT — the family follows from the ticker prefix with no human step.
  * PER-TRADE REALIZED P&L had "no direct API substitute". It does now: the position-aware fill
    cashflow model (kalshi_attribution_ledger.replay_fills) plus settlement revenue reproduces
    realized P&L per market. VALIDATED against the plan's own proof criterion —
    KXAAAGASD-26JUL21 computes to -$5.2676 vs the canon -$5.27, to the cent, from 36 fills
    across 5 markets (API read 2026-08-03T15:00:40Z, full tape n=1,234 fills / 132 settlements).

⚠ THE WINDOW IS A POLICY CHOICE, NOT A DEFAULT. `window` is REQUIRED and has no fallback,
deliberately. Most of this account's realized losses were AGENT DEFECTS, not family economics
(canon: roughly 61-77% of the -$122.57 basis; launch-day taker crossing and a naked settlement
tail). A table built over the defect era would encode OUR bugs as a verdict on the venue's
families and then permanently gate on it — laundering agent defects into "this family loses
money". Whoever runs this states the window and owns that choice.

READ-ONLY against the venue. Writes only the table file, and only when asked.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")

SCHEMA = 1
_EVENT_RE = re.compile(r"for event\s+([A-Za-z0-9_.\-]+)")


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _iso(s):
    """Parse an ISO timestamp. A value carrying no UTC offset IS UTC.

    ⚠ THE UTC DEFAULT IS THE FIX, NOT A CONVENIENCE. A date-only bound ("2026-07-21" — the
    exact form main()'s own --since help offers as its example) parses NAIVE, and _in_window
    then compares it against tz-aware fill created_time, which raises
    "TypeError: can't compare offset-naive and offset-aware datetimes". The CLI therefore
    crashed on its own documented example, on every invocation, for the whole window it
    existed. The suite never caught it because every test passes a tz-AWARE tuple straight to
    build_table (WIN in test_netev_rebuild.py), so the CLI's own parsing path had zero
    coverage — the -2.74% figure in the 08-03 handoff was produced by calling build_table
    directly, never through main(). Measured 2026-08-03.
    """
    d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo is not None else d.replace(tzinfo=dt.timezone.utc)


def credit_event(row):
    """EVENT ticker a credit belongs to, or None. Kalshi phrases the reason as
    'Liquidity Incentive for event KXTRUMPTIME-26AUG01' (verified on 58 of 58 credit rows,
    read 2026-08-03T12:40:11Z). Per-EVENT, never per-strike — canon §M11."""
    m = _EVENT_RE.search(str(row.get("reason") or ""))
    return m.group(1) if m else None


def _in_window(ts, window):
    lo, hi = window
    try:
        d = _iso(ts)
    except Exception:
        return False
    return (lo is None or d >= lo) and (hi is None or d <= hi)


def build_table(fills, settlements, credits, family_of, window, now=None,
                exclude_taker=False):
    """Pure aggregation -> the table document. No I/O, fully testable.

    family_of(ticker) -> family name or None (the quoter's own _netev_family is passed in, so
    the live mapping is never duplicated here and cannot drift).
    window = (lo, hi) tz-aware datetimes or None-ended. REQUIRED — see the module docstring.

    NET = credits(family, in-window) + trading_pnl(family, in-window)
    trading_pnl = position-aware fill cash - fees + settlement revenue
    net_pct_notional = NET / notional, notional = sum(price * count) over in-window fills.
    """
    if window is None:
        raise ValueError("window is required: a net-EV table built over an unstated period "
                         "encodes whatever defects that period contained")
    now = now or dt.datetime.now(dt.timezone.utc)
    from kalshi_attribution_ledger import replay_fills

    fam_pnl, fam_notional, fam_fees, fam_fills = {}, {}, {}, {}
    fam_taker_excl, fam_taker_ct = {}, {}
    # ⚠ REPLAY THE FULL TAPE, ALWAYS. replay_fills is POSITION-AWARE and path-dependent, and its
    # own docstring requires "a point where every ticker in it was flat — callers pass the FULL
    # tape". Filtering the INPUT (by window or by taker) breaks that: a position opened before
    # the window starts at zero in the replay, so the fill that CLOSES it is scored as an OPEN
    # (a cost) instead of releasing the $1 collateral, and every such market reads as a loss.
    # MEASURED 2026-08-03 while getting this wrong: input-filtering produced gas -12.50% to
    # -17.10% on the CSV-era window against the canon's +1.1%, and made the taker exclusion
    # look like it made things WORSE — both artifacts of the broken position path, not findings.
    # The window and the §M13 taker exclusion are applied to the EVENTS instead, after the
    # replay, so the position path stays intact and only ATTRIBUTION is scoped.
    # ATTRIBUTE WHOLE MARKETS, NOT HALF ROUND TRIPS. A market is scored into the window if it
    # was TRADED in the window; once in, ALL of its cash and ALL of its settlement revenue
    # count, whenever they happened.
    # WHY (root-caused 2026-08-03, and it explains the whole gas disagreement): a fill-windowed
    # CASH model books the full cost of any position still open at the window edge and gives it
    # no offsetting value, while the CSV canon summed venue REALIZED P&L, which is 0 for an open
    # position. Measured on the canon's own gas window: 121.62 gas contracts were still open at
    # the edge and 7 of the 9 markets traded in-window settled OUTSIDE it, so the cash model
    # read -$23.29 where canon read +$0.25. Neither engine was wrong — they answered different
    # questions, and only one of them is a fair verdict on a family.
    # A family cannot be judged on half a round trip, so the market is the unit.
    events, _pos = replay_fills(fills)
    _traded_in_window = set()
    for e in events:
        _t = e["fill"].get("ticker") or e["fill"].get("market_ticker")
        if _t and _in_window(e["fill"].get("created_time"), window):
            _traded_in_window.add(_t)
    for e in events:
        row = e["fill"]
        t = row.get("ticker") or row.get("market_ticker")
        if t not in _traded_in_window:
            continue
        fam = family_of(t) if t else None
        if not fam:
            continue
        # §M13 EXCLUSION 1 DOES NOT PORT TO THIS MODEL — default OFF, and here is why.
        # The CSV engine could drop taker ROWS because each row carried its own
        # realized_pnl_with_fees_dollars: dropping one removed a COMPLETE round trip. In a
        # position-aware CASH model a taker fill is usually the CLOSING leg, an INFLOW whose
        # matching open sits elsewhere in the tape — so excluding it strips the inflow and
        # keeps the cost. MEASURED 2026-08-03, post-governor window: excluding takers moved gas
        # from -3.89% to -85.58%, an artifact of removing exits, not a cleaner signal.
        # The flag is kept for experiments and is honestly labelled in the document, but ON is
        # NOT equivalent to §M13 and produces a downward-biased verdict.
        if exclude_taker and bool(row.get("is_taker")):
            fam_taker_ct[fam] = fam_taker_ct.get(fam, 0) + 1
            fam_taker_excl[fam] = fam_taker_excl.get(fam, 0.0) + float(e["cash"])
            continue
        # e["cash"] is ALREADY NET OF FEES — fill_cashflow subtracts fill_fee(f) itself. The
        # fee total below is therefore REPORTING ONLY and must never be subtracted again; doing
        # so double-counts every fee, which is the exact defect that was found in the
        # settlement leg of the cash recorder on 2026-08-03 (fee charged once, booked twice).
        # Caught here by test_fees_reduce_the_net before this table was ever built.
        fam_pnl[fam] = fam_pnl.get(fam, 0.0) + float(e["cash"])
        fam_fees[fam] = fam_fees.get(fam, 0.0) + abs(_f(row.get("fee_cost")))
        fam_fills[fam] = fam_fills.get(fam, 0) + 1
        # notional = what we actually put at risk on the acquired side
        _o = (row.get("outcome_side") or row.get("side") or "").lower()
        _px = _f(row.get(f"{_o}_price_dollars")) or _f(row.get("yes_price_dollars"))
        fam_notional[fam] = fam_notional.get(fam, 0.0) + abs(_px * _f(row.get("count_fp")))

    fam_settle = {}
    for s in settlements:
        # Windowed by the MARKET, not by settle date: a market we traded in-window is scored
        # complete, including a settlement that landed after the window closed. Windowing the
        # two legs on different clocks is what made the cash model read every window-edge
        # position as a pure loss.
        if s.get("ticker") not in _traded_in_window:
            continue
        fam = family_of(s.get("ticker")) if s.get("ticker") else None
        if not fam:
            continue
        fam_settle[fam] = fam_settle.get(fam, 0.0) + _f(s.get("revenue")) / 100.0

    fam_credits, credits_unattributed = {}, 0.0
    for c in credits:
        if not _in_window(c.get("created_at"), window):
            continue
        amt = _f(c.get("amount_cents")) / 100.0
        ev = credit_event(c)
        fam = family_of(ev) if ev else None
        if not fam:
            credits_unattributed += amt          # e.g. the referral credit: real, not a family's
            continue
        fam_credits[fam] = fam_credits.get(fam, 0.0) + amt

    families = {}
    for fam in sorted(set(fam_pnl) | set(fam_credits) | set(fam_settle)):
        # NOT `- fam_fees`: fees are already inside fam_pnl (see the note at the fill loop).
        trading = fam_pnl.get(fam, 0.0) + fam_settle.get(fam, 0.0)
        cred = fam_credits.get(fam, 0.0)
        notional = fam_notional.get(fam, 0.0)
        net = cred + trading
        families[fam] = {
            "confidence": "receipt" if cred > 0 else "unproven",
            "credits": round(cred, 4),
            "trading_pnl": round(trading, 4),
            "fees": round(fam_fees.get(fam, 0.0), 4),   # REPORTING ONLY — already in trading_pnl
            "settlement_revenue": round(fam_settle.get(fam, 0.0), 4),
            "n_fills": fam_fills.get(fam, 0),
            "notional": round(notional, 4),
            "net": round(net, 4),
            "net_pct_notional": round(net / notional, 6) if notional else None,
            "excluded_taker_fills": fam_taker_ct.get(fam, 0),
            "excluded_taker_cash": round(fam_taker_excl.get(fam, 0.0), 4),
        }
    return {
        "schema": SCHEMA,
        "source": "api-receipts",
        "generated_at": now.isoformat(),
        "window": [window[0].isoformat() if window[0] else None,
                   window[1].isoformat() if window[1] else None],
        "credits_unattributed": round(credits_unattributed, 4),
        "exclude_taker": bool(exclude_taker),
        "caveats": [
            "Window is an OPERATOR CHOICE, not a default. Most of this account's realized "
            "losses were AGENT DEFECTS rather than family economics, so a window covering the "
            "defect era encodes our own bugs as a verdict on the venue's families.",
            "Credits are attributed PER EVENT from credit_history reason strings — exact, no "
            "screenshots. A credit whose event maps to no family lands in "
            "credits_unattributed (the referral credit is the known case).",
            "Credits LAG their program window, so a family whose period had not closed at read "
            "time is under-credited and its net is biased PESSIMISTIC — conservative for a gate.",
            "confidence='receipt' requires credits > 0 in-window; otherwise 'unproven', which "
            "routes the family to the quoter's model fallback rather than to a verdict.",
            "exclude_taker defaults OFF: canon §M13's taker exclusion CANNOT be ported to a "
            "position-aware cash model. The CSV engine dropped rows carrying self-contained "
            "round-trip P&L; here a taker fill is usually the CLOSING leg, so excluding it "
            "strips an inflow and keeps its cost. Measured 2026-08-03: ON moved gas from "
            "-3.89% to -85.58% on the post-governor window. ON is NOT equivalent to §M13.",
            "⚠ FAMILY-LEVEL DISAGREEMENT WITH THE CSV CANON, UNRESOLVED: over 07-21..07-23 this "
            "engine puts gas at -8.70% where the CSV table records +1.1% (temp agrees in sign: "
            "-7.58% vs -9.2%). The per-MARKET proof criterion passes to the cent, so the "
            "divergence is in family aggregation or the CSV's own exclusions — do not arm a "
            "gate on the gas sign until it is explained.",
        ],
        "families": families,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rebuild the net-EV table from venue receipts.")
    ap.add_argument("--since", required=True, help="window start, ISO (e.g. 2026-07-21)")
    ap.add_argument("--until", default=None, help="window end, ISO (default: now)")
    ap.add_argument("--out", default=None, help="write the table here (default: print only)")
    a = ap.parse_args(argv)

    import maker_kalshi_client as MK
    from maker_kalshi_client import KalshiOrderClient
    from maker_kalshi_quoter import _netev_family        # the LIVE mapping, never duplicated

    c = KalshiOrderClient(mode="live")
    fills = c._get_paginated(f"{MK.API_ROOT}/portfolio/fills", "fills")["fills"]
    setts = c.get_settlements().get("settlements") or []
    creds = c.get_credit_history().get("credits") or []
    lo = _iso(a.since) if a.since else None
    hi = _iso(a.until) if a.until else None
    doc = build_table(fills, setts, creds, _netev_family, (lo, hi))
    print(json.dumps(doc, indent=2, sort_keys=True))
    if a.out:
        tmp = a.out + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        os.replace(tmp, a.out)
        print(f"\nwrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

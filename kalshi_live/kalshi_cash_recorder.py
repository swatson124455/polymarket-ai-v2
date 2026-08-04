#!/usr/bin/env python3
"""KALSHI CASH RECORDER — forward-looking, exact reward attribution from now on.

READ-ONLY against the venue: balance, orders, positions, fills, settlements.
Places nothing, cancels nothing, amends nothing. Safe under STOP / while parked.

WHY IT EXISTS
  No reward line-item has been found on the trade API. ESTABLISHED 2026-07-27: of 13 candidate
  money endpoints probed, only /portfolio/balance, /portfolio/fills and /portfolio/settlements
  exist; the other 10 (transactions, account_history, ledger, rewards, incentive_payouts,
  incentive_rewards, payouts, resting_order_total_value, incentive_programs/payouts,
  incentive_program_payouts) all return 404. That is 13 probed paths, NOT a proof that the
  full API surface has none. On the evidence we have, a reward is observable only as the
  UNEXPLAINED part of a cash move. Operator rule: any unexplained POSITIVE step is a REWARD
  unless the operator states it was a deposit.

THE CONFOUND THIS EXISTS TO RESOLVE  -- OPEN QUESTION, NOT AN ESTABLISHED FACT
  Reconciling on `balance` alone leaves large per-interval errors that mostly cancel over a
  long window, so something moves cash without appearing in fills or settlements.
  HYPOTHESIS (untested): `balance` is net of collateral reserved by our own resting orders,
  so d(balance) mixes trading flow with reservation changes. A first check against the
  ledger's `resting_orders` COUNT did NOT confirm it -- count is a poor proxy, since
  reservation should scale with notional, which nothing recorded.

  So this records `cash` and `resting_reservation` SEPARATELY every cycle and asserts
  neither. Whichever holds, consecutive rows settle it empirically:
      if the hypothesis holds:  d(cash + resting_reservation) - fills - settlement == 0
      if it does not:           d(cash) - fills - settlement == 0
  and the remainder in the correct form is deposit-or-reward. Raw orders are stored too, so
  the reservation model can be revised later WITHOUT re-collecting.

CONVENTIONS
  fills       POSITION-AWARE, delegated to kalshi_attribution_ledger.replay_fills (the canon
              model, validated there: predicts settlement `revenue` to the cent on 51/51).
              A fill is an OUTFLOW at the acquired outcome's own price when it OPENS, and an
              INFLOW at (1 - price) when it OFFSETS opposite inventory (the venue releases the
              $1 collateral on the closed pair).
              WAS "ACTION-ONLY, YES-SIGNED; priced on yes_price_dollars" — ROOT-FIXED
              2026-08-02, see the fills block below. That convention could not express Kalshi
              cash: the venue prints a NO ACQUISITION as action="sell", so buying NO was booked
              as selling YES, i.e. cash IN where real collateral went OUT.
  settlements payout on NET position only, GROSS of fees: net = yes_count_fp - no_count_fp;
              payout = net*v if net>0 else -net*(1-v), where v = value/100.
              The gross "paired pays $1/pair" model is REFUTED (implied a -$1,977 residual).

OUTPUT  cash-YYYYMM.jsonl, one object per run, append-only. Raw rows are kept so every
        derived number stays re-derivable from the record itself.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")

ENV_FILE = "/opt/pa2-maker-kalshi-live/live.env"


def _load_env(path=ENV_FILE):
    """Systemd supplies these via EnvironmentFile; a standalone run needs the same values.
    Never OVERRIDES an already-set variable, so an explicit env always wins."""
    try:
        with open(path) as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


_load_env()

import maker_kalshi_client as MK
from maker_kalshi_client import KalshiOrderClient
# THE cash model, not a copy of it (root fix 2026-08-02). Import-time side effects: none —
# the module defines constants and functions only, makes no network call and reads no file at
# import. Deployed alongside this script in the FLAT layout at /opt/pa2-maker-kalshi-live/,
# which both sys.path entries above cover.
from kalshi_attribution_ledger import replay_fills

R = MK.API_ROOT


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def order_reservation(o):
    """MODEL of the collateral Kalshi holds for one resting order. NOT a venue-published
    figure, and NOT yet validated against observed cash — validating it is the whole point of
    recording it. The raw order is stored alongside so this can be revised without re-collecting.

    The bot quotes post-only bids, so a resting order is modelled as a BID for `outcome_side`
    at that side's own price, costing at most count x that price. `action` is reported
    YES-signed (observed 2026-07-27: fills carry only ('buy','yes') and ('sell','no'), i.e. a
    NO bid prints as action=sell), so the reserving price is keyed off outcome_side, NOT
    action — the one place in this file where action is the wrong key."""
    ct = _f(o.get("remaining_count_fp"), _f(o.get("initial_count_fp")))
    px = _f(o.get("yes_price_dollars")) if o.get("outcome_side") == "yes" \
        else _f(o.get("no_price_dollars"))
    return ct * px


def cum_fills_cash(fills):
    """Cumulative cash flow of the whole fill tape, POSITION-AWARE.

    ROOT FIX 2026-08-02 (operator-named; defect found by the A2 blind review). This REPLACES a
    local `fill_cash(f)` that signed cash off `action` alone. Kalshi prints a NO ACQUISITION as
    action="sell" (census over the complete tape, API read 2026-08-02T22:42:49Z:
    {('buy','yes'): 582, ('sell','no'): 651} of 1,233 fills), so buying NO was booked as selling
    YES — cash IN where real collateral went OUT, and the $1/contract collateral a naked NO
    posts was never booked at all. The sibling module had already root-fixed exactly this on
    2026-07-23 and says so verbatim at kalshi_attribution_ledger.py:196-201: "the signature WAS
    the bug. A position-independent function cannot express Kalshi cash... would silently
    reproduce the 'flip the sign' model that scored 12x too much cash out." The recorder never
    got that fix, so it is DELEGATED here rather than reimplemented — one model, one place.

    MEASURED IMPACT over the complete history (n=1,233 fills / 127 settlements, API reads
    2026-08-02T22:01:01Z and T21:28:21Z; credits $191.67 from credit_history at T20:40:43Z):
    cumulative fill cash moves -$229.8397 -> -$595.4397, a step of -$365.6000.

    THE STEP IS AN EXACT IDENTITY, not an estimate — use it to self-check any re-derivation:
        new_cum - old_cum  ==  -$1 x SUM over tickers of max(0, N_t - Y_t)
    where per ticker Y_t and N_t are the TOTAL contracts on YES-acquiring and NO-acquiring
    fills. Verified on the complete history (read 2026-08-02T23:22:54Z): both sides -$365.6000,
    absolute error 2e-12. It needs four preconditions, each of which holds 1,233/1,233 on that
    read: (a) action=='buy' iff outcome=='yes'; (b) yes_px + no_px == 1; (c) count_fp present;
    (d) fee_cost >= 0.
    CORRECTION (2026-08-02, same session): an earlier version of this docstring said the
    per-contract formula was NOT CONFIRMED and quoted $2,465.13 as a counter-example. That was
    MY error and the retraction was too broad — $2,465.13 comes from summing only the OPENED
    portion of each fill instead of total contracts per side. The identity above is correct;
    the earlier formulation was not. Note it is a tautology in its own inputs, so it is
    invariant to tape truncation and carries NO diagnostic power over feed completeness — the
    truncation guard for that lives at kalshi_attribution_ledger.py:328-338.

    RECONCILIATION (corroborating, not decisive). The residual moves $190.1000 -> $555.7000
    against venue-verified deposits of $565.00 / 7 rows
    (docs/maker_handoffs/KALSHI_HANDOFF_2026-08-02.md:67) — from $374.90 out to $9.30 out.
    That $9.30 is the CASH form. The identity behind it is
        cash - credits - cum_fills - cum_settle  ==  deposits - withdrawals - reservation
    i.e. it carries a RESERVATION term whenever `balance` is net of collateral held against our
    own resting orders. At the measured read the reservation was $3.5300 over 2 resting orders,
    so the FUNDED form leaves $559.2300 vs $565.00 = $5.77 out. Open positions need no term:
    their cost is already inside cum_fills as -p*q and their payout reaches neither `cash` nor
    cum_settle until settlement, so the identity holds continuously.
    NEITHER form is asserted — this module records both every run
    (unexplained_todate_cash / _funded) precisely because which one is the true invariant is
    still open, and $9.30 / $5.77 is unexplained under either.

    FEED CONTRACT verified before delegating (same read, n=1,233): `book_side` is present and in
    {bid,ask} on 1,233/1,233 and `outcome_side` in {yes,no} on 1,233/1,233, so
    kalshi_attribution_ledger.fill_outcome() — which RAISES on an unverified shape rather than
    guessing — never raises on this feed; `fill_id` and `created_time` are present on
    1,233/1,233, so replay_fills' sort key is total and the netting order is deterministic.
    Direction of the OLD error, for anyone re-reading historical rows: it over-credited
    cum_fills, which LOWERS unexplained_todate_*, so it UNDERSTATED rewards and implied
    deposits. It could not manufacture a phantom reward."""
    events, _positions = replay_fills(fills)
    return sum(e["cash"] for e in events)


MISSING_VALUE_FIELDS = [0]   # audit probe 2026-07-30 (re-pointed W8 2026-08-04: guards `revenue`,
                             # the load-bearing field since the venue-revenue fix) — a venue
                             # rename would silently zero every payout in the cash model


def settlement_payout(s):
    """NET position only, GROSS of fees. Gross/paired model refuted 2026-07-27.

    FIXED 2026-08-04 (W8, operator-named pull-forward): this function now returns the venue's own
    `revenue`/100, agreeing with kalshi_attribution_ledger.settlement_revenue and
    kalshi_netev_rebuild. History of the defect it replaces:
    Measured over the complete settlement history (n=147, snapshot
    cash_identity_snapshot_2026-08-03T233338Z.json):
        sum(revenue/100)       = 74.410000     <- kalshi_attribution_ledger.settlement_revenue
                                                  and kalshi_netev_rebuild both use this
        sum(settlement_payout) = 74.413000     <- this function
        EXACTLY 1 ROW of 147 differs: KXCLUBFBTTS-26JUL26ERKHIL-BTTS,
        market_result="scalar", yes_count_fp=19.00, no_count_fp=18.86, value=45
        -> this model gives net(0.14) * v(0.45) = 0.0630; the venue paid revenue = 0.0600.
    The binary net*value reconstruction does not describe a SCALAR settlement. Worse, it leans on
    yes_count_fp/no_count_fp, which kalshi_attribution_ledger.settlement_revenue documents as
    GROSS TRADED COUNTS rather than the settled position — and that same docstring records
    `revenue` as validated to the cent on 51/51 settlements, ending "Do NOT substitute
    winning-side-count x $1 here."
    Applied under the full protocol: failing-before pin (test_w8_scalar_settlement.py),
    scratch-copy validation over the complete frozen history (sum(settlement_payout) moved
    74.413000 -> 74.410000 exactly, n=147), blind review. The field-rename alarm now guards
    `revenue`, the load-bearing field; `value` and the traded counts are no longer read here.
    Shifts cum_settle_payout (and unexplained_todate_*) by $0.0030 lifetime.

    NO FEE TERM (root fix 2026-08-02, operator-named). A settlement's `fee_cost` is a REPORTING
    ROLL-UP of the fees already charged on that market's fills — not a fee levied at settlement —
    so subtracting it here double-counted every one of them against fill_cash(), which already
    subtracts the same dollars. Measured against the live account 2026-08-02T21:27:34Z
    (n=1,233 fills / 127 settlements, the complete history):
      * settlement.fee_cost == SUM(fills.fee_cost) on the same ticker for 127 of 127, to the cent;
      * 0 settlements carry a fee with zero fills on that ticker, so the roll-up is never a
        novel charge that would otherwise go uncounted;
      * the double-count was worth $35.5619 lifetime, and made 56 of 127 settlements contribute
        NEGATIVE payout — which is what drove cum_settle_payout DOWN over time (a payout is
        >= 0 by construction, so the cumulative sum must be monotone non-decreasing; 0 of 127
        are negative without the term);
      * corroborating (NOT decisive — see the correction below): the reconciliation residual
        cash - credits - cum_fills - cum_settle lands on exactly $190.1000 without the term,
        versus $225.6619 with it, under the non-reservation (`cash`) form; the funded form gives
        $193.6300 vs $229.1919. Only the CONTRAST is evidence here: it is a difference, so any
        error in cum_fills cancels identically between the two variants.
    CORRECTION (blind review 2026-08-02, same session): an earlier version of this docstring
    called that residual "implied deposits" and the whole-cent landing "decisive". BOTH ARE
    REFUTED. Deposits are venue-verified at $565.00 across 7 rows, withdrawals $0
    (docs/maker_handoffs/KALSHI_HANDOFF_2026-08-02.md:67) — so $190.1000 is the cash model's OPEN
    RESIDUAL, not a deposit total, and the ~$374.90 gap is unexplained. The leading candidate is
    the position-independent fill_cash() at :100-103, which cannot express Kalshi cash for a NO
    acquisition (the venue prints one as action="sell"); kalshi_attribution_ledger.fill_cashflow
    (:193-212) is the already-validated position-aware model and its own docstring records that
    this exact signature "WAS the bug". The whole-cent test is structurally blind to that error
    class: count_fp is 2 dp, so a $1-per-contract mistake is always whole cents.
    Bullets 1 and 3 above carry this fix on their own and are unaffected by that residual.
    Rows already written to cash-YYYYMM.jsonl carry the old column; the recorder re-derives from
    the venue's full cumulative history every run, so every FUTURE row is correct with no state
    to migrate. Rewriting historical rows is a separate operator-named action."""
    if s.get("revenue") is None:
        MISSING_VALUE_FIELDS[0] += 1
        print(f"WARNING settlement missing `revenue` field (ticker={s.get('ticker')}) — "
              f"payout treated as 0; venue field rename? total={MISSING_VALUE_FIELDS[0]}")
    return _f(s.get("revenue")) / 100.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/opt/pa2-maker-kalshi-live")
    ap.add_argument("--print-only", action="store_true")
    a = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc)
    c = KalshiOrderClient(mode="live")

    bal = c.get_balance()
    cash = _f(bal.get("balance_dollars"))

    orders = c._get_paginated(f"{R}/portfolio/orders", "orders", {"status": "resting"})["orders"]
    reservation = sum(order_reservation(o) for o in orders)

    pos = c._get_paginated(f"{R}/portfolio/positions", "market_positions",
                           {"count_filter": "position"})["market_positions"]
    pos = [p for p in pos if _f(p.get("position_fp"))]
    open_cost = sum(_f(p.get("market_exposure_dollars")) for p in pos)

    # cumulative-to-date; differencing between rows is done offline so a missed run
    # never loses money (no incremental cursor state to corrupt).
    fills = c._get_paginated(f"{R}/portfolio/fills", "fills", {})["fills"]
    setts = c._get_paginated(f"{R}/portfolio/settlements", "settlements", {})["settlements"]
    cum_fills = cum_fills_cash(fills)
    cum_settle = sum(settlement_payout(s) for s in setts)

    row = {
        "ts": now.isoformat(),
        "cash": round(cash, 6),
        "resting_reservation": round(reservation, 6),
        # candidate reconciling quantity IF the reservation hypothesis holds; `cash` alone is
        # the candidate if it does not. Both are recorded so neither is assumed.
        "funded_cash": round(cash + reservation, 6),
        "n_resting": len(orders),
        "open_position_cost": round(open_cost, 6),
        "n_positions": len(pos),
        "portfolio_value_cents": bal.get("portfolio_value"),
        "cum_fills_cash": round(cum_fills, 6),
        "cum_settle_payout": round(cum_settle, 6),
        # CONVENTION MARKERS (2026-08-02): rows are differenced OFFLINE, and BOTH cumulative
        # columns changed meaning mid-series — cum_settle_payout when the settlement fee
        # roll-up was dropped (net-of-fee -> gross, +$35.5619 step) and cum_fills_cash when the
        # position-independent fill model was replaced (-$365.6000 step, measured over the
        # complete tape). Without in-row markers those one-time steps are indistinguishable
        # from real money at the boundary. Bump these strings if a convention changes again;
        # rows written before a marker existed are that column's PRE-fix era by definition.
        #
        # ⚠ THE DEPLOY BOUNDARY IS A SINGLE +$330.0381 JUMP IN unexplained_todate_*.
        # VERIFIED 2026-08-02: the DEPLOYED recorder (md5 9d842c41c12afc8de804cab4013bd2c2)
        # is NOT an ancestor of either fix — it still carries the action-signed fill model AND
        # still subtracts the settlement fee, and has neither marker key. So both corrections
        # land in the SAME 5-minute interval: unexplained moves by -(-365.6000) - (+35.5619)
        # = +$330.0381. This module's standing operator rule above (see WHY IT EXISTS) reads
        # any unexplained POSITIVE step as a REWARD unless told otherwise — so that jump MUST
        # be booked as a convention change, not income. It is recognisable without trusting
        # these markers: at the boundary d(cash), d(n_fills_todate) and d(n_settlements_todate)
        # are all 0 (the bot is halted), which is structurally impossible for real money.
        "settle_payout_basis": "gross_venue_revenue",   # W8 2026-08-04: net*value model ->
        # venue `revenue`/100; lifetime step in cum_settle_payout is -$0.0030 (n=147), so
        # the boundary is far below any real-money signal, but the marker rule above is
        # unconditional: the string bumps on every convention change.
        "fills_cash_basis": "position_aware",
        "n_fills_todate": len(fills),
        "n_settlements_todate": len(setts),
        # unexplained-to-date under BOTH candidate forms; per-interval deltas taken offline.
        # Neither is asserted — whichever holds constant across rows is the true invariant.
        "unexplained_todate_funded": round(cash + reservation - cum_fills - cum_settle, 6),
        "unexplained_todate_cash": round(cash - cum_fills - cum_settle, 6),
        "resting_raw": [
            {"ticker": o.get("ticker"), "outcome_side": o.get("outcome_side"),
             "action": o.get("action"),
             "yes_price_dollars": o.get("yes_price_dollars"),
             "no_price_dollars": o.get("no_price_dollars"),
             "remaining_count_fp": o.get("remaining_count_fp"),
             "order_id": o.get("order_id"), "created_time": o.get("created_time")}
            for o in orders
        ],
        "positions_raw": [
            {"ticker": p.get("ticker"), "position_fp": p.get("position_fp"),
             "market_exposure_dollars": p.get("market_exposure_dollars")}
            for p in pos
        ],
    }

    line = json.dumps(row, separators=(",", ":"))
    if a.print_only:
        print(json.dumps({k: v for k, v in row.items()
                          if k not in ("resting_raw", "positions_raw")}, indent=1))
        print(f"(resting_raw {len(row['resting_raw'])} rows, "
              f"positions_raw {len(row['positions_raw'])} rows suppressed)")
        return 0

    path = os.path.join(a.out_dir, f"cash-{now.strftime('%Y%m')}.jsonl")
    with open(path, "a") as f:
        f.write(line + "\n")
    print(f"cash-recorder ok {now.isoformat()} cash={cash:.4f} "
          f"reservation={reservation:.4f} funded={cash+reservation:.4f} "
          f"unexplained_todate funded={row['unexplained_todate_funded']:.4f} "
          f"cash={row['unexplained_todate_cash']:.4f} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

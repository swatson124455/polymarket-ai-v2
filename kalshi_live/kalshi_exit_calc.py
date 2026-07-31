"""EXIT LOSS-MIN CALCULATOR (operator-named 2026-07-31: "make one for exits that is beyond
reproach").

Pure, deterministic arithmetic — no I/O, no state, no models. Every number an exit decision
uses is EXACT and receipt-grounded:

  * Taker fee = 0.07 * contracts * price * (1 - price), NO rounding. RECEIPT-VERIFIED
    2026-07-31T15:09:52Z against every fee the account has ever paid: 279/279 fills with
    fees match to <$0.001, worst diff $0.0000 (922 lifetime fills read via
    /portfolio/fills). The commonly-cited "round up to the cent" variant is WRONG for this
    account's receipts (61/128 mismatches on the last-200 sample) — do not "fix" this back.
  * Crossing cost vs the mid = half the spread per contract, plus the fee at the actual
    execution price (long yes sells at the yes bid; long no buys at the yes ask).

The DECISION is a fixed policy over exact numbers, chosen so the bot only ever pays when
paying is provably cheap or provably necessary:
  cross now  when the spread is 1 tick (a maker improve is impossible post-only), OR the
             exact cross cost is at/below the cheap threshold (not worth 15s of ladder
             delay), OR the maker ladder is exhausted (bounded-time exit preserved — the
             strand clock still guarantees the position cannot ride).
  improve    otherwise: give up one tick of exit price for another strand period instead of
             paying half the spread + fee. The ladder is bounded (KALSHI_EXIT_LADDER_STEPS),
             so worst-case added exposure time is steps * STRAND_CROSS_S.

The quoter logs the full receipt (spread, fee, cost, lane) on every decision, so each paid
exit is auditable after the fact against this module's arithmetic.
"""

TAKER_FEE_RATE = 0.07
TICK = 0.01


def taker_fee(ct, price):
    """Exact venue taker fee in dollars. RECEIPT-VERIFIED (see module docstring)."""
    ct = float(ct)
    price = float(price)
    if ct <= 0 or not (0.0 < price < 1.0):
        return 0.0
    return TAKER_FEE_RATE * ct * price * (1.0 - price)


def cross_receipt(ct, long_yes, yes_bid, yes_ask):
    """Exact cost of taker-crossing `ct` contracts flat RIGHT NOW, from the live touches.

    long yes -> sell yes at the yes BID; long no -> buy yes at the yes ASK. Cost is measured
    against the mid: half-spread per contract, plus the exact fee at the execution price.
    Returns None when the book has no two-sided touch (cost is uncomputable — the caller
    must fall back to its legacy behavior, never to a guessed number)."""
    try:
        ct = float(ct)
        yes_bid = float(yes_bid)
        yes_ask = float(yes_ask)
    except (TypeError, ValueError):
        return None
    if ct <= 0 or not (0.0 < yes_bid < 1.0) or not (0.0 < yes_ask < 1.0) or yes_ask < yes_bid:
        return None
    exec_price = yes_bid if long_yes else yes_ask
    spread = yes_ask - yes_bid
    fee = taker_fee(ct, exec_price)
    half_spread_cost = 0.5 * spread * ct
    total = half_spread_cost + fee
    return {
        "ct": ct,
        "long_yes": bool(long_yes),
        "exec_price": round(exec_price, 4),
        "spread_ticks": int(round(spread / TICK)),
        "fee_usd": round(fee, 4),
        "half_spread_usd": round(half_spread_cost, 4),
        "taker_cost_usd": round(total, 4),
        "per_ct_usd": round(total / ct, 6),
    }


def decide(receipt, step, ladder_steps, cheap_usd):
    """Fixed lane policy over an exact receipt: 'cross' or 'improve'. See module docstring."""
    if receipt["spread_ticks"] <= 1:
        return "cross"                      # improve impossible post-only at a 1-tick spread
    if receipt["taker_cost_usd"] <= float(cheap_usd):
        return "cross"                      # provably cheap — not worth the ladder delay
    if int(step) >= int(ladder_steps):
        return "cross"                      # ladder exhausted — bounded-time exit preserved
    return "improve"

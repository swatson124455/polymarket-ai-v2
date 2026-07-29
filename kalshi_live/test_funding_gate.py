"""Pin tests for KALSHI_FUNDING_GATE (the capital-accounting root-fix).

Design: docs/maker_handoffs/KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md.
The fix is the SIMPLIFIED, dual-safe version: gate the resting BUY book against
min(free_cash, MAX_TOTAL_CAPITAL) and STOP counting already-spent held_cost. No
naked-coverage netting term (the prior draft's double-count bug), gross vs free_cash only.

Run: python -m pytest test_funding_gate.py -q   (from the probe dir)

The four pins the operator asked for:
  1. FLAG-OFF NO-OP        — a scenario the legacy gross+held gate blocks is STILL blocked
                             identically when KALSHI_FUNDING_GATE is off.
  2. THE FIX PIN           — SAME scenario, flag ON, free_cash ample: held_cost no longer
                             inflates the gate, so the create the old gate refused is ADMITTED.
                             (Blocked on the legacy code -> pins the real behaviour change.)
  3. HARD CEILING          — free_cash small, MAX_TOTAL_CAPITAL huge: the funding gate refuses
                             creates that would draw more than free cash. Never overdraws.
  4. BALANCE-READ-FAIL     — flag ON but the balance read raised this cycle: free_cash is None,
                             so the gate FAILS CLOSED to the legacy gross+held gate (does not loosen).
"""
import os
import pytest

# Reuse the live-hardening harness (module handle + run_once driver + order/config helpers).
from test_live_hardening import q, MockClient, _order, _run, _cfg


class BalMockClient(MockClient):
    """MockClient with a controllable /portfolio/balance (free cash) that can also raise,
    so the funding gate and its fail-closed path can be exercised end-to-end via run_once."""
    def __init__(self, *a, balance="100.0000", balance_raises=False, **k):
        super().__init__(*a, **k)
        self._balance = balance
        self._balance_raises = balance_raises

    def get_balance(self):
        if self._balance_raises:
            raise RuntimeError("balance read 500")
        return {"balance_dollars": self._balance}


# A single held-inflation scenario shared by pins #1, #2 and #4: inventory on a ticker whose
# COST BASIS ($300) alone dwarfs MAX_TOTAL_CAPITAL ($100). The legacy gate adds that spent cost
# to `committed`, so every fresh accumulating T1 buy is refused. free_cash is ample -> the venue
# could easily fund the small T1 book; only the double-count blocks it.
_HELD_TICKER = "OTHER"
_HELD_POS = [{"ticker": _HELD_TICKER, "position_fp": "300", "market_exposure_dollars": "300.00"}]


def _held_scenario(monkeypatch, tmp_path, *, funding_gate, balance="500.0000",
                   balance_raises=False, cap=100):
    """Footprint = one fresh market T1; held inventory = $300 cost basis on OTHER.
    HELD_MAX_USD disabled so the LEVEL breaker does not fire first (isolates the capital gate,
    same technique as test_unwind_create_exempt_from_committed_cap)."""
    _cfg(monkeypatch, join=20, mktcap=250, totcap=cap)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "FUNDING_GATE", funding_gate)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = BalMockClient(mode="live", resting=[], positions=list(_HELD_POS),
                      balance=balance, balance_raises=balance_raises)
    row = _run(monkeypatch, c, str(tmp_path))
    return c, row


def _t1(created, side=None):
    return [o for o in created if o["ticker"] == "T1" and (side is None or o["side"] == side)]


# ---- Pin #1: FLAG-OFF NO-OP -------------------------------------------------------------------
# REWRITTEN 2026-07-28 (operator Q2 decision). Under the unconditional risk rule the held OTHER
# position now gets a FULL-SIZE exit at the touch (300 ct @ 0.49 = ~$147 reservation, cap-EXEMPT),
# so `committed` carries held + exit and T1 is refused at the market-cap stage (capped_markets)
# rather than per-create (create_skipped). The OUTCOME pinned is unchanged: no accumulating T1
# order may be placed, and the exit always is.
def test_flag_off_is_noop_legacy_gate_still_blocks(monkeypatch, tmp_path):
    c, row = _held_scenario(monkeypatch, tmp_path, funding_gate=0)
    assert len(_t1(c.created)) == 0                       # no accumulating T1 buy placed
    assert row.get("capped_markets", 0) >= 1 or row.get("create_skipped", 0) >= 1
    assert row.get("committed_usd", 0) >= 300.0           # committed still carries the spent held cost
    assert "funding_gate" not in row                      # no funding telemetry emitted (flag off)
    reds = [o for o in c.created if o["ticker"] == _HELD_TICKER and o["side"] == "no"]
    assert len(reds) == 1 and reds[0]["count"] <= 300     # the exit rests, capped at |inv|


# ---- Pin #2: EXIT PRECEDENCE (operator Q2 decision, 2026-07-28) -------------------------------
# REWRITTEN: the old pin asserted that with the funding gate on and free cash ample, both T1
# sides are admitted. Under holding => exit-only the OTHER exit rests at FULL |inv| at the touch
# and is deliberately cap-exempt — its ~$147 reservation exceeds the $100 funding allowance, so
# EVERY accumulating create is crowded out while the unwind works. The operator ruled this
# CORRECT: getting out outranks getting in. The crowding is bounded in TIME (the strand cross
# forces the exit after STRAND_CROSS_S; a filled exit FREES the capital), so the cost is a
# reward pause of seconds-to-minutes against the alternative the 07-27 tape priced at
# -$15.29 ridden vs -$0.59 exited (one 42-ct position).
def test_exit_precedence_crowds_out_new_quotes_while_unwinding(monkeypatch, tmp_path):
    c, row = _held_scenario(monkeypatch, tmp_path, funding_gate=1, balance="500.0000")
    reds = [o for o in c.created if o["ticker"] == _HELD_TICKER and o["side"] == "no"]
    assert len(reds) == 1 and reds[0]["count"] <= 300     # the exit rests at full remaining size
    assert len(_t1(c.created)) == 0, \
        "while a large exit reserves the book, accumulating creates must be refused"
    assert row.get("funding_gate") == 1                   # gate active
    assert row.get("free_cash_usd") == 500.0


# ---- Pin #3: HARD CEILING (never overdraw free cash even with a huge cap) ----------------------
def test_hard_ceiling_refuses_beyond_free_cash_even_with_huge_cap(monkeypatch, tmp_path):
    # No held inventory; MAX_TOTAL_CAPITAL huge; free_cash only $5. A T1 buy costs ~$10 > $5, so the
    # funding gate must refuse it: the resting BUY book can never exceed what free cash could fund.
    _cfg(monkeypatch, join=20, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "FUNDING_GATE", 1)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = BalMockClient(mode="live", resting=[], positions=[], balance="5.0000")
    row = _run(monkeypatch, c, str(tmp_path))
    assert len(_t1(c.created)) == 0                       # refused: would draw more than $5 free
    assert row.get("create_skipped", 0) >= 1
    assert row.get("funding_gate") == 1 and row.get("free_cash_usd") == 5.0


# ---- Pin #4: BALANCE-READ-FAIL -> FAIL CLOSED (does not loosen) --------------------------------
def test_balance_read_failure_fails_closed_to_legacy_gate(monkeypatch, tmp_path):
    # KALSHI_FUNDING_GATE=1 but the balance read raised this cycle -> free_cash is None -> the gate
    # FALLS BACK to the legacy gross+held gate (must NOT loosen). Held $300 > $100 cap -> the fresh
    # accumulating T1 buy is refused exactly as with the flag off. The reducing OTHER unwind, being
    # exempt, is still placed.
    c, row = _held_scenario(monkeypatch, tmp_path, funding_gate=1, balance_raises=True)
    assert "balance_read_failed" in row                  # the read did fail this cycle
    assert len(_t1(c.created)) == 0                       # accumulating T1 buy still blocked (no loosening)
    # (post-Q2 rewrite: the block lands at the market-cap stage now that the exit's reservation
    # inflates committed — either counter is acceptable evidence, the outcome above is the pin)
    assert row.get("capped_markets", 0) >= 1 or row.get("create_skipped", 0) >= 1
    assert "funding_gate" not in row                      # gate inactive (failed closed)
    reds = [o for o in c.created if o["ticker"] == _HELD_TICKER and o["side"] == "no"]
    assert len(reds) == 1                                 # reducing OTHER unwind exempt -> still placed


# ---- Bonus: reducing/unwind creates never blocked by the funding gate --------------------------
def test_reducing_create_exempt_from_funding_gate(monkeypatch, tmp_path):
    # free_cash $0 (gate would block every accumulating buy) but the held OTHER position must STILL
    # get its passive maker unwind — a risk-reducing order can never over-commit the account.
    c, row = _held_scenario(monkeypatch, tmp_path, funding_gate=1, balance="0.0000", cap=100000)
    assert len(_t1(c.created)) == 0                       # accumulating refused (free_cash exhausted)
    reds = [o for o in c.created if o["ticker"] == _HELD_TICKER and o["side"] == "no"]
    assert len(reds) == 1                                 # reducing unwind admitted regardless

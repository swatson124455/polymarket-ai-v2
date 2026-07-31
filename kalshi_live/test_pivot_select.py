"""Pin tests for KALSHI_PIVOT_SELECT (pivot-select — backfill the footprint with EARNING markets).

Design: the operator directive "we NEVER gate any markets; if the rewards aren't there we PIVOT
to another trade." The GATES stay (they correctly identify non-earning books); what changes is
that a gated market no longer just SHRINKS the footprint — the selection over-selects a
density-weighted, near-money-ordered candidate pool and the quote loop PIVOTS past gated markets,
pulling the next eligible candidate into the slot until FOOTPRINT_TOP markets are actually QUOTED.

Flag KALSHI_PIVOT_SELECT default 0 = provable no-op (legacy select_footprint + legacy quote loop
run byte-for-byte). Run: python -m pytest test_pivot_select.py -q  (from the probe dir).

The four pins:
  T1 FLAG-OFF NO-OP        — flag 0 => select_footprint returns the identical legacy round-robin
                             list, and a full cycle emits no pivot_* telemetry.
  T2 THE FIX PIN           — today's gas book (deep-ITM strikes gate on the price bound, near-money
                             strikes qualify but sort LATER by ticker). Flag OFF quotes ~1; flag ON
                             backfills to the near-money earners => many more quoted. FAILS legacy.
  T3 NEVER QUOTE A NON-EARNER — flag ON still returns [] (never quotes) a genuinely lopsided /
                             price-bound book; pivot means quoting a DIFFERENT earner, not the bad one.
  T4 BOUNDED READS/POOL    — flag ON caps the candidate pool (pool_cap) and never issues an
                             unbounded number of reads; the loop terminates even when ALL gate.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

# Reuse the live-hardening harness (module handle + run_once driver + mock client/config helpers).
from test_live_hardening import q, MockClient, _run, _cfg


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def _prog(ticker, reward, *, start, end, target=1):
    """A minimal incentive_program row that passes every select_footprint filter."""
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "target_size_fp": target, "discount_factor_bps": 5000, "period_reward": reward,
            "start_date": _iso(start), "end_date": _iso(end)}


def _book(y, n, depth=1000):
    """orderbook_fp payload with a single best level per side (best_yes=y, best_no=n)."""
    return {"orderbook_fp": {"yes_dollars": [[f"{y:.4f}", str(depth)]],
                             "no_dollars": [[f"{n:.4f}", str(depth)]]}}


# A symmetric, in-bounds book that JOINS (both bids inside (0.01,0.97], sum<1, symmetric depth).
_QUOTE_BOOK = _book(0.50, 0.48)
# A deep-ITM / lopsided book: yes bid above the 0.97 bound AND no bid below the 0.01 bound ->
# the price-bound gate (:482-484) drops it. This is the "gas deep-ITM strike" that never earns.
_GATE_BOOK = _book(0.98, 0.005)


def _gas_progs(strikes, *, reward=3_000_000):
    """One gas series (KXAAAGASD) with the given numeric strikes, all equal reward (so legacy
    tie-breaks on the ticker => lowest strike first) and all comfortably inside their own life."""
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(minutes=60), now + timedelta(minutes=240)
    return [_prog(f"KXAAAGASD-26JUL24-{s:.3f}", reward, start=start, end=end) for s in strikes]


def _ob_router(progs, book_for):
    """A public_get replacement: incentive path -> progs; orderbook path -> book_for(ticker)."""
    def _get(path):
        if "incentive" in path:
            return {"incentive_programs": list(progs), "next_cursor": ""}
        if "/orderbook" in path:
            t = path.split("/markets/")[1].split("/orderbook")[0]
            return book_for(t)
        return {}
    return _get


def _pivot_cfg(monkeypatch, *, pivot, foot=5, per_series=10, pool_mult=2, totcap=100000.0):
    """Common config: enable/disable the flag, size the footprint, keep capital non-binding."""
    monkeypatch.setattr(q, "PIVOT_SELECT", pivot)
    monkeypatch.setattr(q, "FOOTPRINT_TOP", foot)
    monkeypatch.setattr(q, "PER_SERIES_CAP", per_series)
    monkeypatch.setattr(q, "PIVOT_POOL_MULT", pool_mult)
    monkeypatch.setattr(q, "SERIES_ALLOW", [])
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    # keep capital non-binding: the 25%-of-capital family cap (operator 2026-07-31) would trim
    # a 5-sibling gas footprint at mock equity; its ON-path is pinned in test_loss_ladder_v2
    monkeypatch.setattr(q, "SERIES_PCT", 0.0)
    monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", float(totcap))


# ---------------------------------------------------------------------------------------------
# T1 — FLAG-OFF NO-OP
# ---------------------------------------------------------------------------------------------
def _legacy_expected(rows_progs, foot, per_series_cap):
    """Reimplement the frozen legacy round-robin over the SAME eligible tickers, to assert the
    flag-off select_footprint output byte-for-byte (same tickers, same order). All progs here
    share start/end/reward-shape so usd_day ordering collapses to the reward, tie-broken by ticker
    exactly as the module does — this mirror is independent of the module's fill code."""
    # each prog: (ticker, usd_day-proxy) ; here usd_day-proxy = reward (equal life) so we can sort.
    from collections import defaultdict
    rows = sorted(rows_progs, key=lambda r: (-r[1], r[0]))
    by = defaultdict(list)
    for t, _ in rows:
        by[t.split("-")[0]].append(t)
    order = sorted(by, key=lambda s: (-max(r[1] for r in rows_progs if r[0].split("-")[0] == s), s))
    picked, per = [], defaultdict(int)
    progressed = True
    while len(picked) < foot and progressed:
        progressed = False
        for s in order:
            i = per[s]
            if i >= per_series_cap or i >= len(by[s]):
                continue
            picked.append(by[s][i]); per[s] += 1; progressed = True
            if len(picked) >= foot:
                break
    return picked


def test_flag_off_select_is_identical_to_legacy_roundrobin(monkeypatch):
    # 3 series, distinct reward tiers (A>B>C), FOOTPRINT_TOP=4 => legacy round-robin.
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(minutes=60), now + timedelta(minutes=240)
    progs = [
        _prog("KXAAAGASD-26JUL24-4.10", 3_000_000, start=start, end=end),
        _prog("KXAAAGASD-26JUL24-4.11", 2_900_000, start=start, end=end),
        _prog("KXAAAGASD-26JUL24-4.12", 2_800_000, start=start, end=end),
        _prog("KXTEMPNYCH-26JUL24-T80", 1_000_000, start=start, end=end),
        _prog("KXTEMPNYCH-26JUL24-T81", 900_000, start=start, end=end),
        _prog("KXH100MON-26JUL24-T5", 300_000, start=start, end=end),
        _prog("KXH100MON-26JUL24-T6", 250_000, start=start, end=end),
    ]
    monkeypatch.setattr(q, "PIVOT_SELECT", 0)
    monkeypatch.setattr(q, "FOOTPRINT_TOP", 4)
    monkeypatch.setattr(q, "PER_SERIES_CAP", 10)
    monkeypatch.setattr(q, "SERIES_ALLOW", [])
    picked = [m["ticker"] for m in q.select_footprint(progs, q.utcnow())]
    expected = _legacy_expected([(p["market_ticker"], p["period_reward"]) for p in progs], 4, 10)
    assert picked == expected           # byte-for-byte legacy order (flag off)
    # round-robin fairness: one from each of the 3 series in the first round, then the 4th from A.
    assert picked == ["KXAAAGASD-26JUL24-4.10", "KXTEMPNYCH-26JUL24-T80",
                      "KXH100MON-26JUL24-T5", "KXAAAGASD-26JUL24-4.11"]


def test_flag_off_cycle_emits_no_pivot_telemetry(monkeypatch, tmp_path):
    # A full run_once with the flag off must not emit any pivot_* key (plan row byte-identical).
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "PIVOT_SELECT", 0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert "pivot_select" not in row and "pivot_pool" not in row and "pivot_quoted" not in row
    assert "gated_out" in row           # legacy telemetry still present


# ---------------------------------------------------------------------------------------------
# T2 — THE FIX PIN (today's gas book)
# ---------------------------------------------------------------------------------------------
# 17 strikes 4.065..4.145 (step 0.005). The 4 lowest (4.065-4.080) and the 2 highest (4.140-4.145)
# are deep-ITM/OTM -> price-bound gate. The 11 middle strikes (4.085-4.135) are symmetric+in-bounds
# -> they QUOTE. Legacy selects the 5 LOWEST by ticker, so only 4.085 survives (~1 quoted). Pivot
# over-selects near-money-first and backfills to the middle strikes -> FOOTPRINT_TOP quoted.
_GAS_STRIKES = [round(4.065 + 0.005 * i, 3) for i in range(17)]


def _gas_book_for(t):
    s = float(t.split("-")[-1])
    return _QUOTE_BOOK if 4.085 <= s <= 4.135 else _GATE_BOOK


def _run_gas(monkeypatch, tmp_path, *, pivot):
    _pivot_cfg(monkeypatch, pivot=pivot, foot=5, per_series=10, pool_mult=2)
    progs = _gas_progs(_GAS_STRIKES)
    monkeypatch.setattr(q, "public_get", _ob_router(progs, _gas_book_for))
    c = MockClient(mode="live", resting=[], positions=[])
    return _run(monkeypatch, c, str(tmp_path))


def test_fix_pin_gas_book_backfills_to_near_money_earners(monkeypatch, tmp_path):
    off = _run_gas(monkeypatch, tmp_path, pivot=0)
    on = _run_gas(monkeypatch, tmp_path, pivot=1)
    # Flag OFF (legacy): the 5 lowest strikes are picked; 4 gate -> ~1 earner quoted.
    assert off.get("quoted_markets", 0) <= 2
    assert "pivot_select" not in off
    # Flag ON: pivot backfills past the deep-ITM drops to the near-money earners -> FOOTPRINT_TOP.
    assert on.get("pivot_select") == 1
    assert on.get("quoted_markets", 0) >= 5                 # filled the footprint with earners
    assert on.get("quoted_markets", 0) > off.get("quoted_markets", 0)   # strictly more than legacy
    assert on.get("pivot_pool", 0) >= 5                     # over-selected pool was consumed


# ---------------------------------------------------------------------------------------------
# T3 — NEVER QUOTE A NON-EARNER
# ---------------------------------------------------------------------------------------------
def test_pivot_never_quotes_a_lopsided_or_pricebound_book(monkeypatch, tmp_path):
    # Pool of 3: two genuinely non-earning books + one clean earner. FOOTPRINT_TOP=5 (want more
    # than exist) so the loop reaches every candidate. Pivot must quote ONLY the clean one.
    _pivot_cfg(monkeypatch, pivot=1, foot=5, totcap=200.0)
    pool = [{"ticker": "KXAAAGASD-26JUL24-BAD1", "usd_day": 50.0, "target": 1,
             "end": "2099-01-01T00:00:00Z"},
            {"ticker": "KXAAAGASD-26JUL24-BAD2", "usd_day": 40.0, "target": 1,
             "end": "2099-01-01T00:00:00Z"},
            {"ticker": "KXAAAGASD-26JUL24-CLEAN", "usd_day": 30.0, "target": 1,
             "end": "2099-01-01T00:00:00Z"}]
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: list(pool))
    books = {"KXAAAGASD-26JUL24-BAD1": _GATE_BOOK,          # yes 0.98 / no 0.005 -> price bound
             "KXAAAGASD-26JUL24-BAD2": _book(0.60, 0.55),   # sum 1.15 >= 1 -> crossed/degenerate
             "KXAAAGASD-26JUL24-CLEAN": _QUOTE_BOOK}
    monkeypatch.setattr(q, "public_get", _ob_router([], lambda t: books[t]))
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("pivot_select") == 1
    assert row.get("quoted_markets", 0) == 1               # only the clean earner
    made = {o["ticker"] for o in c.created}
    assert "KXAAAGASD-26JUL24-CLEAN" in made               # the earner was quoted
    assert "KXAAAGASD-26JUL24-BAD1" not in made            # the bad books were pivoted PAST,
    assert "KXAAAGASD-26JUL24-BAD2" not in made            # never quoted (gate untouched)


# ---------------------------------------------------------------------------------------------
# T4 — BOUNDED READS / POOL
# ---------------------------------------------------------------------------------------------
def test_pivot_pool_and_reads_are_bounded_when_everything_gates(monkeypatch, tmp_path):
    # 40 eligible strikes in ONE series, ALL gate (every book lopsided). pool_cap = min(2*5, 40,
    # READ_BUDGET-RESERVE) = 10. Selection must return <=10; the cycle must read a bounded number
    # of books and TERMINATE (never an unbounded loop), quoting nothing.
    _pivot_cfg(monkeypatch, pivot=1, foot=5, per_series=50, pool_mult=2)   # PER_SERIES high -> pool_cap binds
    strikes = [round(1.00 + 0.01 * i, 2) for i in range(40)]
    progs = _gas_progs(strikes)
    # selection alone (direct call) is capped at pool_cap.
    picked = q.select_footprint(progs, q.utcnow())
    pool_cap = min(q.PIVOT_POOL_MULT * q.FOOTPRINT_TOP, len(progs),
                   q.READ_BUDGET_PER_CYCLE - q.PIVOT_READ_RESERVE)
    assert pool_cap == 10 and len(picked) <= 10            # over-select is bounded

    monkeypatch.setattr(q, "public_get", _ob_router(progs, lambda t: _GATE_BOOK))
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("pivot_select") == 1
    assert row.get("quoted_markets", 0) == 0               # everything gated -> nothing quoted
    assert row.get("reads", 0) < q.READ_BUDGET_PER_CYCLE   # never approached the hard ceiling
    assert row.get("reads", 0) <= pool_cap + 6             # ~ pool books + a few program pages
    # gated_out == markets actually TRIED (== pivot_pool == consumed), all skipped, none quoted.
    assert row.get("gated_out", 0) == row.get("pivot_pool", 0)
    assert row.get("pivot_pool", 0) <= pool_cap

"""Pin tests for KALSHI_MKT_TELEMETRY (per-market-per-cycle reward telemetry).

THE DEFECT THIS FIXES: plan rows are per-CYCLE. In the hour closing 2026-07-22T10:00Z we quoted
three events that paid $12.94 / $1.51 / $0.00 while sharing the same cycles — so they share ONE
at_ref_pct and the log physically cannot discriminate them. Pool size does not separate them either
(975 vs 942 $/day). R4 pays pro-rata by DF^N-weighted qualifying score, so the variable that
separates paid from zero is the COMPETING QUALIFYING DEPTH — the score denominator, which nothing
recorded. This writes one row per market per cycle with our intended size, our price vs the
reference, and the rival qualifying book.

The pins:
  T1 DENOMINATOR PIN    — _qualifying_breakdown returns the EXACT denominator _qualifying_score
                          divides by. The two walks can never drift.
  T2 THE FIX PIN        — two markets quoted in the SAME cycle emit TWO DISTINCT rows carrying
                          DIFFERENT competing depth. FAILS on legacy (no per-market row exists).
  T3 WORKS WHILE PARKED — at KALSHI_MAX_TOTAL_CAPITAL=1 nothing rests and zero orders are created,
                          yet rival depth is still measured and our resting size is honestly 0.
                          This is the pin the whole temp-return plan rests on.
  T4 CANNOT BREAK A CYCLE — a telemetry fault leaves the plan row and the orders untouched.
  T5 FLAG OFF NO-OP     — no file, and the plan row is unchanged.
  T6 GATE ATTRIBUTION   — a gated market still emits a row, tagged with WHICH gate skipped it, and
                          scores capture $0 (an unqualifiable book pays nobody).

Run: python -m pytest test_market_telemetry.py -q  (from the probe dir)
"""
import json
import os

from test_live_hardening import q, MockClient, _run, _cfg


def _books(mapping, default=None):
    """public_get stub: empty programs; per-TICKER orderbooks so one cycle can carry markets with
    genuinely different rival depth (the whole point of the fix)."""
    def inner(path):
        if "incentive" in path:
            return {"incentive_programs": [], "next_cursor": ""}
        for tick, book in mapping.items():
            if f"/markets/{tick}/" in path:
                return {"orderbook_fp": book}
        return {"orderbook_fp": default or {"yes_dollars": [["0.50", "9999"]],
                                            "no_dollars": [["0.49", "9999"]]}}
    return inner


def _rows(tmpdir):
    out = []
    for p in sorted(os.listdir(tmpdir)):
        if p.startswith("quotes-"):
            for line in open(os.path.join(tmpdir, p)):
                out.append(json.loads(line))
    return out


def _mkt(ticker, usd_day=100.0, target=1):
    return {"ticker": ticker, "usd_day": usd_day, "target": target,
            "end": "2099-01-01T00:00:00Z"}


# THIN book: 600 at the touch + 500 one tick back = 1100, just clears target 1000.
_THIN = {"yes_dollars": [["0.50", "600"], ["0.49", "500"]],
         "no_dollars": [["0.49", "600"], ["0.48", "500"]]}
# DEEP book: 100k a side — our join is a rounding error in the denominator.
_DEEP = {"yes_dollars": [["0.50", "100000"]], "no_dollars": [["0.49", "100000"]]}


# ---------------------------------------------------------------------------------------------
# T1 — DENOMINATOR PIN
# ---------------------------------------------------------------------------------------------
def test_breakdown_returns_the_exact_qualifying_score_denominator():
    """_qualifying_breakdown must expose the SAME book total _qualifying_score divides by —
    otherwise the logged denominator is not the denominator the venue (and our gates) use."""
    bids = [(0.50, 600.0), (0.49, 500.0), (0.48, 400.0)]
    target, df, our_size = 1000.0, 0.5, 100.0
    total, cum, ref, lowq, qual = q._qualifying_breakdown(bids, target, df)
    assert qual and ref == 0.50
    # walk stops the moment cum >= target: 600 + 500 = 1100 -> the 0.48 level is NOT in the set
    assert cum == 1100.0 and lowq == 0.49
    assert total == 600.0 + 0.5 * 500.0                     # DF^0*600 + DF^1*500
    # joining AT reference: _qualifying_score's share must equal our_score / this same total
    share, ok = q._qualifying_score(bids, ref, our_size, target, df)
    assert ok and abs(share - (our_size / total)) < 1e-12
    # and one tick BACK, where DF^1 halves our score — same denominator
    share_back, ok2 = q._qualifying_score(bids, 0.49, our_size, target, df)
    assert ok2 and abs(share_back - ((df * our_size) / total)) < 1e-12


def test_breakdown_reports_a_book_that_cannot_reach_target_as_not_qualifying():
    # cum < target -> nobody scores that snapshot; the row must say so rather than imply depth.
    total, cum, ref, lowq, qual = q._qualifying_breakdown([(0.50, 10.0)], 1000.0, 0.5)
    assert qual is False and cum == 10.0


# ---------------------------------------------------------------------------------------------
# T2 — THE FIX PIN (fails on legacy: no per-market row exists at all)
# ---------------------------------------------------------------------------------------------
def test_two_markets_one_cycle_emit_distinct_rows_with_different_competing_depth(monkeypatch,
                                                                                tmp_path):
    """The exact shape that defeated the old telemetry: two markets, SAME cycle, same pool. The
    per-cycle log gives them one at_ref_pct; the per-market log must separate them by the variable
    that actually drives R4 — the competing qualifying depth."""
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "public_get", _books({"THIN": _THIN, "DEEP": _DEEP}))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        _mkt("THIN", usd_day=975.0, target=1000), _mkt("DEEP", usd_day=942.0, target=1000)])
    row = _run(monkeypatch, MockClient(mode="live"), str(tmp_path))

    rows = {r["ticker"]: r for r in _rows(str(tmp_path))}
    assert set(rows) == {"THIN", "DEEP"}, "one row per market per cycle"
    assert rows["THIN"]["cyc"] == rows["DEEP"]["cyc"], "same cycle — the confound being fixed"

    # The competing denominator separates them even though the per-cycle at_ref_pct cannot.
    assert rows["THIN"]["y_book_df"] == 850.0                # 600 + 0.5*500
    assert rows["DEEP"]["y_book_df"] == 100000.0
    # ...and so does our modelled share of it, by >2 orders of magnitude.
    assert rows["THIN"]["y_share"] > 50 * rows["DEEP"]["y_share"]
    # EXACT share pin: once we rest, the R4 denominator is book_total + OUR score — not book_total
    # alone. 100ct joined at reference against an 850 book => 100/950, not 100/850.
    assert rows["THIN"]["y_score"] == 100.0
    assert rows["THIN"]["y_share"] == round(100.0 / 950.0, 6)
    assert abs(rows["THIN"]["capture_usd_day"] - (100.0 / 950.0) * 975.0) < 0.01
    assert rows["THIN"]["capture_usd_day"] > rows["DEEP"]["capture_usd_day"]
    # Pool size does NOT separate them — which is exactly why pool was the wrong variable.
    assert rows["THIN"]["usd_day"] == 975.0 and rows["DEEP"]["usd_day"] == 942.0
    # our price vs the reference is recoverable on every row
    for r in rows.values():
        assert r["y_ref"] == 0.50 and r["y_px"] == 0.50      # joined AT reference this cycle
    assert row["at_ref_pct"] == 100.0                        # legacy per-cycle field still emitted


# ---------------------------------------------------------------------------------------------
# T3 — WORKS WHILE PARKED  (the pin the temp-return plan depends on)
# ---------------------------------------------------------------------------------------------
def test_parked_at_cap_one_still_measures_rival_depth_with_our_size_zero(monkeypatch, tmp_path):
    """KALSHI_MAX_TOTAL_CAPITAL=1 is what parks the bot: a 20ct order costs >> $1 so every
    accumulating create is skipped. The book is still fetched and the desired book still computed
    BEFORE that gate — so the competition denominator is observable on markets we never quote,
    at zero risk. Without this, nothing can be learned about temp until it is un-parked."""
    _cfg(monkeypatch, join=20, mktcap=15, totcap=1)          # <-- the live wind-down setting
    monkeypatch.setattr(q, "public_get", _books({"KXTEMPNYCH-X": _THIN}))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        _mkt("KXTEMPNYCH-X", usd_day=975.0, target=1000)])
    c = MockClient(mode="live")
    row = _run(monkeypatch, c, str(tmp_path))

    assert len(c.created) == 0, "parked: nothing may rest"
    # At cap=1 the aggregate cap_desired drops the market before the create loop is ever reached,
    # so the parking shows up as capped_markets (not create_skipped) — the same signature the live
    # parked bot emits (plans-20260725: capped_markets=5, create_skipped=0, creates=0). Telemetry
    # fires UPSTREAM of that cap, which is why it still has content.
    assert row["capped_markets"] >= 1 and row["creates"] == 0

    rows = _rows(str(tmp_path))
    assert len(rows) == 1, "telemetry must still fire while parked"
    r = rows[0]
    assert r["series"] == "KXTEMPNYCH"
    assert r["y_book_df"] == 850.0 and r["y_qual"] is True     # rival depth MEASURED
    assert r["y_rest_ct"] == 0.0 and r["n_rest_ct"] == 0.0     # honest: nothing actually resting
    assert r["y_ct"] > 0                                       # our INTENDED size, clearly labelled
    assert r["capture_usd_day"] > 0                            # the counterfactual is quantified


# ---------------------------------------------------------------------------------------------
# T4 — CANNOT BREAK A CYCLE
# ---------------------------------------------------------------------------------------------
def test_telemetry_failure_never_breaks_the_trading_cycle(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "public_get", _books({"T1": _THIN}))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [_mkt("T1", target=1000)])

    def _boom(*a, **k):
        raise RuntimeError("telemetry exploded")
    monkeypatch.setattr(q, "_market_telemetry_row", _boom)

    c = MockClient(mode="live")
    row = _run(monkeypatch, c, str(tmp_path))
    assert len(c.created) == 2, "orders unaffected by a telemetry fault"
    assert row["quote_fail"] == 0 and row["creates"] == 2
    assert _rows(str(tmp_path)) == []


# ---------------------------------------------------------------------------------------------
# T5 — FLAG OFF NO-OP
# ---------------------------------------------------------------------------------------------
def test_flag_off_writes_nothing_and_leaves_the_plan_row_alone(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "MKT_TELEMETRY", 0)
    monkeypatch.setattr(q, "public_get", _books({"T1": _THIN}))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [_mkt("T1", target=1000)])
    c = MockClient(mode="live")
    row = _run(monkeypatch, c, str(tmp_path))
    assert _rows(str(tmp_path)) == []
    assert len(c.created) == 2 and row["creates"] == 2


# ---------------------------------------------------------------------------------------------
# T6 — GATE ATTRIBUTION
# ---------------------------------------------------------------------------------------------
def test_gated_market_still_emits_a_row_naming_the_gate(monkeypatch, tmp_path):
    """A market we SKIP is exactly the market we most need a row for — 12 of 26 closed windows paid
    zero. One side too thin to reach Target Size means the snapshot pays NOBODY, so the row must
    record capture $0 and name the gate that skipped it."""
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 0.0)      # cannot bridge the gap
    thin_side = {"yes_dollars": [["0.50", "600"], ["0.49", "500"]],
                 "no_dollars": [["0.49", "5"]]}              # NO side cannot reach target 1000
    monkeypatch.setattr(q, "public_get", _books({"GATED": thin_side}))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        _mkt("GATED", usd_day=975.0, target=1000)])
    c = MockClient(mode="live")
    row = _run(monkeypatch, c, str(tmp_path))

    assert len(c.created) == 0                                # gate held
    rows = _rows(str(tmp_path))
    assert len(rows) == 1
    r = rows[0]
    assert r["gates"].get("unqualifiable") == 1, "the row names WHICH gate skipped this market"
    assert r["y_qual"] is True and r["n_qual"] is False       # the asymmetry that caused the skip
    assert r["capture_usd_day"] == 0.0                        # R3: no two-sided book -> pays nobody
    assert r["y_ct"] == 0.0                                   # we intended nothing here
    assert row["unqualifiable"] == 1                          # legacy aggregate still emitted


# ---------------------------------------------------------------------------------------------
# T7 — R3 RULE: one qualifying side is not half a reward, it is NO reward
# ---------------------------------------------------------------------------------------------
def test_one_sided_qualification_pays_zero_even_with_a_full_score_on_the_good_side():
    """CFTC Feb-2026 LIP amendment: a snapshot is EXCLUDED unless BOTH sides carry resting size
    sufficient to meet Target Size. So a market where we score heavily on the YES side but the NO
    book cannot reach Target must be logged as capture $0 — averaging the good side to a positive
    number would invent income that the venue never pays. Unit-level because in the run_once path
    such a market is gated before we ever quote it, which masks the arithmetic."""
    m = {"ticker": "ONESIDED-X", "usd_day": 975.0, "target": 1000, "df": 0.5}
    yl = [(0.50, 600.0), (0.49, 500.0)]      # qualifies: 1100 >= 1000
    nl = [(0.49, 5.0)]                       # cannot reach Target Size
    quotes = [{"side": "yes", "price_dollars": 0.50, "count": 100}]
    row = q._market_telemetry_row(1, q.utcnow(), m, yl, nl, quotes, None, 0.0, {})
    assert row["y_qual"] is True and row["y_share"] == round(100.0 / 950.0, 6)
    assert row["n_qual"] is False
    assert row["capture_usd_day"] == 0.0, "R3: one-sided book pays NOBODY"


def test_capture_averages_the_two_sides_it_does_not_take_the_better_one():
    """A two-sided snapshot scores (share_yes + share_no)/2. With an ASYMMETRIC book — thin rivals
    on YES, a wall on NO — taking the better side instead of the average would overstate capture
    ~2x, which is exactly the direction that would wrongly justify quoting a family."""
    m = {"ticker": "ASYM-X", "usd_day": 975.0, "target": 1000, "df": 0.5}
    yl = [(0.50, 600.0), (0.49, 500.0)]            # thin: our 100ct -> share 100/950
    nl = [(0.49, 100000.0)]                        # wall: our 100ct -> share 100/100100
    quotes = [{"side": "yes", "price_dollars": 0.50, "count": 100},
              {"side": "no", "price_dollars": 0.49, "count": 100}]
    row = q._market_telemetry_row(1, q.utcnow(), m, yl, nl, quotes, None, 0.0, {})
    assert row["y_qual"] is True and row["n_qual"] is True
    y, n = 100.0 / 950.0, 100.0 / 100100.0
    assert abs(row["capture_usd_day"] - ((y + n) / 2.0) * 975.0) < 0.01
    # and it is materially BELOW the better side alone — the overstatement being guarded against
    assert row["capture_usd_day"] < 0.6 * (y * 975.0)

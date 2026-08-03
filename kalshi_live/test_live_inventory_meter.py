"""LIVE PER-MARKET INVENTORY METER (defect 2, Phase C1 — 2026-08-03).

The loss ladder is fed by the venue's realized_pnl_dollars, which is 0.0 for as long as a
position is OPEN. So a market can bleed all day and the governor sees nothing until it closes.
Live 2026-08-02: KXTEMPAUSH-26AUG0203-T81.99 was first seen by the governor at -$9.00 having
never tripped the $3 rung — exactly two lifetime fills, 108.81 s apart, the entire loss
realized inside one round trip.

THIS IS MEASUREMENT ONLY AND GATES NOTHING, by design. The plan's own framing: a live meter
buys honest measurement and earlier cross-market visibility, NOT loss limitation on a one-tick
adverse fill — the reduce path had already flipped in the same cycle, so a rung firing on an
unrealized mark would have banned a market that was already exiting. Whether unrealized should
FEED the rungs is an operator policy call and is deliberately not taken here; these pins exist
partly to make a later change to that policy visible.
"""
import os

from test_live_hardening import MockClient, _cfg, _run, q


def _pos(t="TT", pos="50", expo="25.00"):
    # 50 ct at $25.00 total = $0.50/ct cost basis
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": expo}


def _book(monkeypatch, yes_bid="0.50", no_bid="0.49"):
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else
                        {"orderbook_fp": {"yes_dollars": [[yes_bid, "9999"]],
                                          "no_dollars": [[no_bid, "9999"]]}})


def _arm(monkeypatch):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    _book(monkeypatch)


def test_meter_is_emitted_even_with_no_inventory(monkeypatch, tmp_path):
    # A3 contract: 0 must mean "measured, flat", never "did not look".
    _arm(monkeypatch)
    row = _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
    assert row.get("mkt_unreal_measured") == 1, "the meter ran"
    assert row.get("mkt_unreal_n") == 0
    assert row.get("mkt_unreal_usd") == 0.0
    assert row.get("mkt_unreal_worst_usd") == 0.0
    assert row.get("mkt_unreal_worst") == ""


def test_measured_flag_separates_flat_from_never_ran(monkeypatch, tmp_path):
    """Found by mutation 2026-08-03. A3 seeds these gauges to 0, so a mutant that emitted them
    only when non-empty passed the whole suite — the seed supplied the 0 either way. That hides
    the one distinction THIS gauge exists to make: "measured, nothing underwater" and "the mark
    block never ran" are different facts that both read 0.
    mkt_unreal_measured is seeded 0 and set 1 only inside the mark block, so it separates them.
    Here the balance read fails, the whole block is skipped, and the flag must stay 0."""
    _arm(monkeypatch)

    class NoBalance(MockClient):
        def get_balance(self):
            raise RuntimeError("balance 503")

    row = _run(monkeypatch, NoBalance(mode="live", positions=[_pos()]), str(tmp_path))
    assert row.get("mkt_unreal_measured") == 0, "the meter did NOT run and must say so"
    assert row.get("mkt_unreal_usd") == 0.0, "...while the seeded gauge still reads 0"
    assert row.get("balance_read_failed"), "sanity: this is the blind-cycle path"


def test_open_position_at_cost_shows_flat(monkeypatch, tmp_path):
    # 50 ct bought at $0.50, best bid still $0.50 -> unrealized 0.
    _arm(monkeypatch)
    row = _run(monkeypatch, MockClient(mode="live", positions=[_pos()]), str(tmp_path))
    assert row.get("mkt_unreal_n") == 1
    assert row.get("mkt_unreal_usd") == 0.0


def test_open_position_bleeding_is_VISIBLE_before_it_realizes(monkeypatch, tmp_path):
    # THE DEFECT. 50 ct at $0.50 cost, bid collapses to $0.20 -> -$15.00 unrealized, while the
    # venue's realized_pnl_dollars is still 0.00 and the ladder therefore sees nothing.
    _arm(monkeypatch)
    _book(monkeypatch, yes_bid="0.20", no_bid="0.79")
    c = MockClient(mode="live", positions=[_pos()],
                   traded=[{"ticker": "TT", "realized_pnl_dollars": "0.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("mkt_unreal_worst") == "TT"
    assert row.get("mkt_unreal_worst_usd") == -15.0
    assert row.get("mkt_unreal_neg_usd") == -15.0
    # ...and the ladder still sees nothing, which is the point being measured
    assert row.get("loss_exitonly") in (None, 0)


def test_bleeding_past_the_rung_is_LOUD(monkeypatch, tmp_path, capsys):
    _arm(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    _book(monkeypatch, yes_bid="0.20", no_bid="0.79")
    _run(monkeypatch, MockClient(mode="live", positions=[_pos()]), str(tmp_path))
    out = capsys.readouterr().out
    assert "UNREALIZED" in out and "TT" in out
    assert "measurement only" in out, "the line must say it gates nothing"


def test_the_meter_gates_NOTHING(monkeypatch, tmp_path):
    """The load-bearing safety property: a deeply underwater OPEN market must not be banned,
    marked exit-only, or have its quoting curtailed by this meter. If a future change wires
    unrealized into the rungs, this pin fails and forces the decision into the open."""
    _arm(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    monkeypatch.setattr(q, "MKT_OUT_LOSS_USD", 5.0)
    _book(monkeypatch, yes_bid="0.01", no_bid="0.98")     # -$24.50 unrealized on a $25 basis
    c = MockClient(mode="live", positions=[_pos()],
                   traded=[{"ticker": "TT", "realized_pnl_dollars": "0.00"}])
    _run(monkeypatch, c, str(tmp_path))
    import json
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert st.get("mkt_out") in (None, []), "unrealized must NOT mint a permanent ban"
    assert st.get("mkt_loss_tripped") in (None, []), "unrealized must NOT trip the day latch"


def test_unpriceable_market_is_excluded_not_reported_flat(monkeypatch, tmp_path):
    # A market we cannot price falls back to COST, which would read as exactly 0.00 unrealized
    # by construction — that is not a measurement, so it is excluded and counted elsewhere.
    _arm(monkeypatch)

    def pg(p):
        if "incentive" in p:
            return {"incentive_programs": [], "next_cursor": ""}
        raise RuntimeError("book 500")
    monkeypatch.setattr(q, "public_get", pg)
    row = _run(monkeypatch, MockClient(mode="live", positions=[_pos()]), str(tmp_path))
    assert row.get("mkt_unreal_n") == 0, "an unpriceable market must not dilute the gauge"
    assert row.get("mark_fallback_tickers") == 1, "...it is counted as a mark fallback instead"


def test_worst_is_the_most_negative_across_markets(monkeypatch, tmp_path):
    _arm(monkeypatch)
    _book(monkeypatch, yes_bid="0.20", no_bid="0.79")
    c = MockClient(mode="live", positions=[_pos("AA", "50", "25.00"),
                                           _pos("BB", "10", "5.00")])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("mkt_unreal_n") == 2
    assert row.get("mkt_unreal_worst") == "AA"       # -15.00 vs -3.00
    assert row.get("mkt_unreal_worst_usd") == -15.0
    assert row.get("mkt_unreal_usd") == -18.0


def test_a_profitable_open_position_does_not_become_the_worst(monkeypatch, tmp_path):
    _arm(monkeypatch)
    _book(monkeypatch, yes_bid="0.80", no_bid="0.19")
    row = _run(monkeypatch, MockClient(mode="live", positions=[_pos()]), str(tmp_path))
    assert row.get("mkt_unreal_usd") == 15.0
    assert row.get("mkt_unreal_worst") == "", "no market is underwater"
    assert row.get("mkt_unreal_neg_usd") == 0.0

"""Pin tests for KALSHI_NETEV_GATE (net-EV flagging — receipt-calibrated per-FAMILY net gate).

THE PROBLEM every prior gate misses: unqualifiable/selection/capture/stand-down all ask a REWARD
question ("can the book pay / can WE capture a slice"). None asks the NET question. A family can
capture reward and STILL lose money if its adverse-fill bleed exceeds the reward — canon §M8: TEMP
earned the BIGGEST credits ($23.06, 91% of all reward income) and still netted −9.2% of notional,
while GAS netted +1.1%. This gate loads the RECEIPT-calibrated per-family net (kalshi_netev_calibrate
over the transaction CSV) and gates on it: a net-negative family is skipped when FLAT, reduce-only
when HOLDING; de-risk is never blocked. An UNPROVEN family (no receipt history) falls back to the
conservative §M7-haircut R4 model. This SUPERSEDES the pool-only KALSHI_STANDDOWN and the reward-only
KALSHI_CAPTURE_GATE (both answer a strictly weaker question).

Flag KALSHI_NETEV_GATE default 0 = provable no-op (byte-for-byte legacy; telemetry emitted only when
on; zero file IO when off). Run: python -m pytest test_netev_gate.py -q (from the probe dir).

The five pins:
  T1 FLAG-OFF NO-OP     — table says TEMP net-negative; flag 0 => the temp market quotes full-size
                          exactly as legacy, and a full cycle emits no netev_* key.
  T2 THE FIX PIN        — table TEMP net = −9.2%. Flag OFF quotes temp; flag ON SKIPS it (flat) /
                          reduce-only (holding). FAILS legacy, PASSES fix.
  T3 KEEPS-NET-POS-GAS  — table GAS net = +1.1%. Flag ON keeps quoting gas full-size (not starved).
  T4 NEVER-BLOCKS-EXITS — holding inventory in the net-negative family, flag ON STILL rests the
                          reducing quote at full |inv|.
  T5 CALIBRATION-ENGINE — kalshi_netev_calibrate on the 07-23 CSV reproduces gas-positive /
                          temp-negative (the §M8 signs) with the credit-lag caveat surfaced.
Plus: _netev_family is pinned byte-equivalent to kalshi_netev_calibrate.family_of.
"""
import os

import pytest

from test_live_hardening import q, MockClient, _run, _cfg
import kalshi_netev_calibrate as cal


# --------------------------------------------------------------------------------------------
# fixtures — a NON-VOID, symmetric, two-sided JOIN book (both sides deep past target, spread 1
# tick, symmetric) so the legacy path opens the full two-sided book: passes unqualifiable + the
# selection gate. The gate's decision is driven entirely by the family net-EV, not the book shape.
# --------------------------------------------------------------------------------------------
_YL = [["0.50", "1200"]]
_NL = [["0.49", "1200"]]
_TEMP_TKR = "KXTEMPCHIH-26JUL2212-T69.99"     # _netev_family -> "temp"
_GAS_TKR = "KXAAAGASD-26JUL23-4.020"          # _netev_family -> "gas"

# receipt-calibrated table (the §M8 signs): temp net-negative, gas net-positive.
_TABLE = {
    "temp": {"net_pct_notional": -0.092, "confidence": "receipt"},
    "gas": {"net_pct_notional": 0.011, "confidence": "receipt"},
}


def _mkt(ticker, usd_day=50.0, target=1000):
    return {"ticker": ticker, "target": target, "end": "2099-01-01T00:00:00Z", "usd_day": usd_day}


def _netev_cfg(monkeypatch, *, on, table=_TABLE, margin=0.0):
    monkeypatch.setattr(q, "NETEV_GATE", 1 if on else 0)
    monkeypatch.setattr(q, "NETEV_TABLE", table)
    # 2026-07-30 mtime-reload fix: point the reload at a nonexistent file so the in-cycle
    # refresh cannot replace this fixture table with the repo's real one mid-test.
    monkeypatch.setattr(q, "NETEV_TABLE_PATH", "/nonexistent/netev_table_test.json")
    monkeypatch.setattr(q, "NETEV_MIN_MARGIN_PCT", margin)
    monkeypatch.setattr(q, "NETEV_MODEL_HAIRCUT", 3.0)
    monkeypatch.setattr(q, "NETEV_FINGERPRINT_USD_DAY", 5.0)
    # isolate this gate from every other market-quality gate
    monkeypatch.setattr(q, "CAPTURE_GATE", 0)
    monkeypatch.setattr(q, "STANDDOWN", 0)
    # sizing dials so a full join = 100 ct (identical to the capture-gate harness)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "JOIN_SIZE", 100)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)


def _sides(qs):
    return {x["side"]: x for x in qs}


def _pg(book, ticker):
    def inner(path):
        if "incentive" in path:
            return {"incentive_programs": [], "next_cursor": ""}
        return {"orderbook_fp": book}
    return inner


# --------------------------------------------------------------------------------------------
# T1 — FLAG-OFF NO-OP
# --------------------------------------------------------------------------------------------
def test_flag_off_is_byte_for_byte_legacy_on_negative_family(monkeypatch):
    # Flag OFF: the net-EV gate never runs, so even a calibrated net-NEGATIVE family (temp) opens
    # the full two-sided join exactly as legacy — the table is ignored entirely.
    _netev_cfg(monkeypatch, on=False)
    off = _sides(q.desired_quotes(_mkt(_TEMP_TKR), _YL, _NL, q.utcnow(), inv=0.0))
    assert off["yes"]["count"] == 100 and off["no"]["count"] == 100
    assert off["yes"]["price_dollars"] == 0.50 and off["no"]["price_dollars"] == 0.49
    # the presence/contents of the table must not change the flag-off result
    monkeypatch.setattr(q, "NETEV_TABLE", {})
    empty = _sides(q.desired_quotes(_mkt(_TEMP_TKR), _YL, _NL, q.utcnow(), inv=0.0))
    assert {s: x["count"] for s, x in off.items()} == {s: x["count"] for s, x in empty.items()}


def test_flag_off_cycle_emits_no_netev_telemetry(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "NETEV_GATE", 0)
    monkeypatch.setattr(q, "NETEV_TABLE", _TABLE)
    monkeypatch.setattr(q, "public_get", _pg({"yes_dollars": _YL, "no_dollars": _NL}, _TEMP_TKR))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": _TEMP_TKR, "usd_day": 50.0, "target": 1000, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert "netev_gate" not in row and "netev_skipped_markets" not in row
    assert "gated_out" in row                                  # legacy telemetry intact
    assert len(c.created) == 2                                 # full-size book placed (unchanged)


# --------------------------------------------------------------------------------------------
# T2 — THE FIX PIN
# --------------------------------------------------------------------------------------------
def test_fix_pin_negative_family_flat(monkeypatch):
    # Flag OFF: legacy opens the full two-sided book on a family that RECEIPTS show loses money net.
    _netev_cfg(monkeypatch, on=False)
    off = q.desired_quotes(_mkt(_TEMP_TKR), _YL, _NL, q.utcnow(), inv=0.0)
    assert len(off) == 2 and _sides(off)["yes"]["count"] == 100
    # Flag ON: same book, temp net = −9.2% < 0 -> SKIP entirely when flat.
    _netev_cfg(monkeypatch, on=True)
    on = q.desired_quotes(_mkt(_TEMP_TKR), _YL, _NL, q.utcnow(), inv=0.0)
    assert on == []                                            # net-negative-for-us + flat -> skip


def test_fix_pin_negative_family_holding_is_reduce_only(monkeypatch):
    # HOLDING long +40 in the net-negative family: flag ON must go REDUCE-ONLY (reducing NO side
    # only, full |inv|), not skip and not two-sided.
    _netev_cfg(monkeypatch, on=True)
    out = q.desired_quotes(_mkt(_TEMP_TKR), _YL, _NL, q.utcnow(), inv=40.0)
    assert len(out) == 1
    assert out[0]["side"] == "no" and out[0]["reason"] == "unwind" and out[0]["count"] == 40


def test_fix_pin_via_cycle_skips_and_reports(monkeypatch, tmp_path):
    def _cycle(on):
        _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
        monkeypatch.setattr(q, "NETEV_GATE", 1 if on else 0)
        monkeypatch.setattr(q, "NETEV_TABLE", _TABLE)
        # 2026-07-30 mtime-reload fix: pin path so the in-cycle refresh can't swap the fixture
        monkeypatch.setattr(q, "NETEV_TABLE_PATH", "/nonexistent/netev_table_test.json")
        monkeypatch.setattr(q, "NETEV_MIN_MARGIN_PCT", 0.0)
        monkeypatch.setattr(q, "public_get", _pg({"yes_dollars": _YL, "no_dollars": _NL}, _TEMP_TKR))
        monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
            {"ticker": _TEMP_TKR, "usd_day": 50.0, "target": 1000, "end": "2099-01-01T00:00:00Z"}])
        c = MockClient(mode="live", resting=[], positions=[])
        return _run(monkeypatch, c, str(tmp_path)), c

    off_row, off_c = _cycle(False)
    on_row, on_c = _cycle(True)
    assert "netev_gate" not in off_row                         # flag off: no telemetry
    assert len(off_c.created) == 2                             # legacy opens the losing family
    assert on_row.get("netev_gate") == 1
    assert on_row.get("netev_skipped_markets") == 1
    assert on_row.get("netev_skipped_families") == {"temp": 1}
    assert on_row.get("netev_min_signal") == pytest.approx(-0.092)
    assert len(on_c.created) == 0                              # net-negative family NOT opened
    assert on_row.get("quoted_markets") == 0


# --------------------------------------------------------------------------------------------
# T3 — KEEPS NET-POSITIVE GAS (not starved)
# --------------------------------------------------------------------------------------------
def test_keeps_net_positive_gas_full_size(monkeypatch):
    _netev_cfg(monkeypatch, on=True)
    on = _sides(q.desired_quotes(_mkt(_GAS_TKR), _YL, _NL, q.utcnow(), inv=0.0))
    assert on["yes"]["count"] == 100 and on["no"]["count"] == 100   # gas kept, full two-sided
    _netev_cfg(monkeypatch, on=False)
    off = _sides(q.desired_quotes(_mkt(_GAS_TKR), _YL, _NL, q.utcnow(), inv=0.0))
    assert {s: x["count"] for s, x in on.items()} == {s: x["count"] for s, x in off.items()}


def test_gas_via_cycle_keeps_quoting(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "NETEV_GATE", 1)
    monkeypatch.setattr(q, "NETEV_TABLE", _TABLE)
    monkeypatch.setattr(q, "NETEV_MIN_MARGIN_PCT", 0.0)
    monkeypatch.setattr(q, "public_get", _pg({"yes_dollars": _YL, "no_dollars": _NL}, _GAS_TKR))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": _GAS_TKR, "usd_day": 150.0, "target": 1000, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("netev_gate") == 1
    assert row.get("netev_skipped_markets") == 0              # gas NOT skipped
    assert len(c.created) == 2                                # full two-sided book placed


# --------------------------------------------------------------------------------------------
# T4 — NEVER BLOCKS EXITS (full-size reducing quote survives)
# --------------------------------------------------------------------------------------------
def test_never_blocks_exits_full_size(monkeypatch):
    _netev_cfg(monkeypatch, on=True)
    # long +40 -> reducing NO at full |inv|
    out = q.desired_quotes(_mkt(_TEMP_TKR), _YL, _NL, q.utcnow(), inv=40.0)
    assert len(out) == 1 and out[0]["side"] == "no" and out[0]["count"] == 40
    assert out[0]["reason"] == "unwind"
    # symmetry: short -40 -> reducing YES at full |inv|
    out_s = q.desired_quotes(_mkt(_TEMP_TKR), _YL, _NL, q.utcnow(), inv=-40.0)
    assert len(out_s) == 1 and out_s[0]["side"] == "yes" and out_s[0]["count"] == 40


def test_exit_size_matches_legacy_unwind(monkeypatch):
    # the reduce-only quote the gate rests is sized identically to the legacy maker unwind on the
    # SAME held position — the gate clones the proven path, it does not invent a smaller exit.
    _netev_cfg(monkeypatch, on=False)
    off = _sides(q.desired_quotes(_mkt(_TEMP_TKR), _YL, _NL, q.utcnow(), inv=40.0))
    _netev_cfg(monkeypatch, on=True)
    on = _sides(q.desired_quotes(_mkt(_TEMP_TKR), _YL, _NL, q.utcnow(), inv=40.0))
    # INVARIANT CHANGED 2026-07-26 (hold-both-sides): gate path = pure unwind capped at |inv|;
    # flag-OFF path = two-sided quote whose reducing half is ADD+|inv|. Not equal by design.
    # The safety property stands: the gate never invents a smaller exit than the position.
    assert on["no"]["count"] >= 40
    assert off["no"]["count"] >= on["no"]["count"]
    assert on["no"]["price_dollars"] == off["no"]["price_dollars"]


# --------------------------------------------------------------------------------------------
# UNPROVEN family fallback (no receipt history -> conservative R4 model)
# --------------------------------------------------------------------------------------------
def test_unproven_family_uses_model_and_skips_when_thin(monkeypatch):
    # A family absent from the table is UNPROVEN. On a deep-rival book our prospective capture ~ $0,
    # so model_net = pc/3 - fingerprint <= 0 -> unproven-skip when flat.
    _netev_cfg(monkeypatch, on=True, table={"gas": {"net_pct_notional": 0.011, "confidence": "receipt"}})
    monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
    deep_y = [["0.50", "100000"]]
    deep_n = [["0.49", "100000"]]
    out = q.desired_quotes(_mkt("KXNEWSERIES-26JUL23-T1", usd_day=50.0), deep_y, deep_n,
                           q.utcnow(), inv=0.0)
    assert out == []                                          # unproven + model<=0 -> skip
    # holding inventory in the unproven family is STILL reduce-only, never blocked
    held = q.desired_quotes(_mkt("KXNEWSERIES-26JUL23-T1", usd_day=50.0), deep_y, deep_n,
                            q.utcnow(), inv=40.0)
    assert len(held) == 1 and held[0]["reason"] == "unwind" and held[0]["count"] == 40


# --------------------------------------------------------------------------------------------
# T5 — CALIBRATION ENGINE reproduces the §M8 signs on the 07-23 CSV
# --------------------------------------------------------------------------------------------
_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kalshi_transactions_2026-07-23.csv")


def test_calibration_engine_reproduces_m8_signs():
    res = cal.calibrate(_CSV)
    fams = res["families"]
    # §M8: GAS net +1.1% (positive), TEMP net −9.2% (negative)
    assert fams["gas"]["net_pct_notional"] > 0
    assert fams["temp"]["net_pct_notional"] < 0
    # exact §M8 magnitudes (+0.25 trading / 214.85 notional ; −36.12 / 142.67)
    assert fams["gas"]["net_pct_notional"] == pytest.approx(0.011, abs=0.001)
    assert fams["temp"]["net_pct_notional"] == pytest.approx(-0.092, abs=0.001)
    assert fams["gas"]["trading_pnl"] == pytest.approx(0.25, abs=0.01)
    assert fams["temp"]["trading_pnl"] == pytest.approx(-36.12, abs=0.01)
    assert fams["gas"]["notional"] == pytest.approx(214.85, abs=0.01)
    assert fams["temp"]["notional"] == pytest.approx(142.67, abs=0.01)
    # credit cross-check exact (§M8: attributed $25.21 == CSV total $25.21)
    assert res["credit_attribution_ok"] is True
    assert res["credit_total_csv"] == pytest.approx(25.21, abs=0.01)
    # credit-lag caveat surfaced (§M13: credits lag a Time Period; gas-weekly may be open)
    assert any("lag" in c.lower() for c in res["caveats"])
    assert res["window"]["start"] == "2026-07-21" and res["window"]["end"] == "2026-07-22"


def test_family_of_matches_quoter_replica():
    # the calibration family_of and the quoter's local _netev_family must agree byte-for-byte
    for t in [_GAS_TKR, _TEMP_TKR, "KXAAAGASW-26JUL27-4.5", "KXTEMPAUSH-26JUL2021-T71.99",
              "KXNEWSERIES-26JUL23-T1", "KXFUNDRAISING-26-A15", ""]:
        assert cal.family_of(t) == q._netev_family(t)


# ---- DEFECT 7: ARMED WITH AN EMPTY TABLE MUST NEVER BE SILENT (2026-08-03) --------------------
# The loader fail-opens to {}, which does NOT disable the gate — it makes every family
# "unproven", routing them to the MODEL fallback and the conservative fingerprint cost. The gate
# then still SKIPS markets, on a table that does not exist, saying nothing. Live 2026-07-31/08-01
# it skipped 640 of 2,195 evaluations, including 71 for a family its own (undeployed) table rates
# net-POSITIVE. Silence was the whole bug.

def _fp1(monkeypatch, tkr=_TEMP_TKR):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": tkr, "usd_day": 50.0, "target": 1000, "end": "2099-01-01T00:00:00Z"}])


def test_armed_with_empty_table_alarms(monkeypatch, tmp_path, capsys):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "NETEV_GATE", 1)
    monkeypatch.setattr(q, "NETEV_TABLE", {})          # the live 07-31/08-01 state
    monkeypatch.setattr(q, "public_get", _pg({"yes_dollars": _YL, "no_dollars": _NL}, _TEMP_TKR))
    _fp1(monkeypatch)
    row = _run(monkeypatch, MockClient(mode="live", resting=[], positions=[]), str(tmp_path))
    assert row.get("netev_gate") == 1
    assert row.get("netev_table_families") == 0
    assert row.get("netev_table_empty") == 1, "gate armed + no table is the actionable alarm"
    assert "EMPTY TABLE" in capsys.readouterr().out, "and it must be loud, not only recorded"


def test_armed_with_a_real_table_does_not_alarm(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "NETEV_GATE", 1)
    monkeypatch.setattr(q, "NETEV_TABLE", {"families": _TABLE})
    monkeypatch.setattr(q, "public_get", _pg({"yes_dollars": _YL, "no_dollars": _NL}, _TEMP_TKR))
    _fp1(monkeypatch)
    row = _run(monkeypatch, MockClient(mode="live", resting=[], positions=[]), str(tmp_path))
    assert row.get("netev_table_families") == 2
    assert row.get("netev_table_empty") == 0, "0 = checked and populated, not 'nobody looked'"


def test_the_alarm_does_not_break_the_flag_off_no_op(monkeypatch, tmp_path):
    """Flag-off emits ZERO net-EV telemetry — a deliberate provable-no-op property of this
    subsystem. The alarm is scoped to the armed path for exactly this reason: seeding these
    keys the way A3 seeds counters would write them on every flag-off cycle and destroy the
    proof. Pinned here as well as in test_flag_off_cycle_emits_no_netev_telemetry so a future
    always-emit sweep cannot quietly reintroduce it."""
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "NETEV_GATE", 0)
    monkeypatch.setattr(q, "NETEV_TABLE", {})
    monkeypatch.setattr(q, "public_get", _pg({"yes_dollars": _YL, "no_dollars": _NL}, _TEMP_TKR))
    _fp1(monkeypatch)
    row = _run(monkeypatch, MockClient(mode="live", resting=[], positions=[]), str(tmp_path))
    for k in ("netev_gate", "netev_table_families", "netev_table_empty"):
        assert k not in row, f"{k} leaked into a flag-off row"

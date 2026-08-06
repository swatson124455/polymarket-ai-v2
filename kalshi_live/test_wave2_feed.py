"""Wave-2 (operator "go" 2026-08-06): feed-trust hardening A-1/A-2/B-4 + the
grain-consistency pin (identity-review detector 6) + probe-streak harness (F6a).

  T1 A-2: metadata event grouping stops due-ness fragmentation — one true event with a
     settled strike and a fresh strike is NOT due (string fallback wrongly fragments it)
  T2 B-4: last_credit_ts emitted per paid series
  T3 A-1: parse-rate, truncation, and paid->convicted regression alarms fire; healthy
     feed = no alarms
  T4 grain corpus: every series/event extractor pinned to golden outputs over real
     venue ticker shapes — any drift fails here first
  T5 F6a: run_once streak state machine — a one-sided-book probe bumps
     probe_gate_refused (persisted), a healthy book clears it
"""
import datetime as dt
import json
import os
import sys

import kalshi_credit_feedback as kcf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_live_hardening import MockClient, _cfg, _run, q  # noqa: E402

NOW = dt.datetime(2026, 8, 6, 3, 0, tzinfo=dt.timezone.utc)
OLD = (NOW - dt.timedelta(hours=100)).isoformat()      # settled well past 72h


def _order(t, fills=5.0):
    return {"ticker": t, "fill_count_fp": str(fills)}


def test_t1_metadata_grouping_stops_dueness_fragmentation():
    orders = [_order("KXCPI-26SEP-T-0.4"), _order("KXCPI-26SEP-T0.9")]
    setts = [{"ticker": "KXCPI-26SEP-T-0.4", "settled_time": OLD}]
    ev = {"KXCPI-26SEP-T-0.4": "KXCPI-26SEP", "KXCPI-26SEP-T0.9": "KXCPI-26SEP"}
    fb_meta = kcf.build_feedback([], orders, setts, NOW, event_of=ev.get)
    assert fb_meta["KXCPI"]["verdict"] == "insufficient", \
        "true-event grouping: sibling still open -> event NOT due -> no conviction"
    fb_str = kcf.build_feedback([], orders, setts, NOW)          # string fallback
    assert fb_str["KXCPI"]["verdict"] == "never_paid_due", \
        "the fallback fragments the event — the exact A-2 defect, pinned as fallback-only"


def test_t2_last_credit_ts_emitted():
    creds = [{"reason": "Liquidity Incentive for event KXTOP-26AUG10",
              "amount_cents": 150, "created_at": "2026-08-03T06:00:00Z"},
             {"reason": "Liquidity Incentive for event KXTOP-26AUG03",
              "amount_cents": 100, "created_at": "2026-08-01T06:00:00Z"}]
    fb = kcf.build_feedback(creds, [], [], NOW)
    assert fb["KXTOP"]["last_credit_ts"] == "2026-08-03T06:00:00Z"
    assert fb["KXTOP"]["verdict"] == "paid"


def test_t3_integrity_alarms():
    creds = [{"reason": "Liquidity Incentive for event KXA-1"},
             {"reason": "Totally Reworded Bonus KXB-1"}]
    al = kcf.feed_integrity_alarms(creds, 1, 1000, {}, {})
    assert any("PARSE RATE" in x for x in al)
    al2 = kcf.feed_integrity_alarms([{}] * 1000, 1000, 1000, {}, {})
    assert any("TRUNCATION" in x for x in al2)
    al3 = kcf.feed_integrity_alarms([], 0, 1000,
                                    {"KXA": {"verdict": "paid"}},
                                    {"KXA": {"verdict": "insufficient"}})
    assert any("VERDICT REGRESSION KXA" in x for x in al3)
    ok = kcf.feed_integrity_alarms(
        [{"reason": "Liquidity Incentive for event KXA-1"}], 1, 1000,
        {"KXA": {"verdict": "paid"}}, {"KXA": {"verdict": "paid"}})
    assert ok == []


CORPUS = {
    # ticker: (series, quoter_event_key, feedback_string_event)
    "KXAAAGASD-26AUG06-4.065": ("KXAAAGASD", "KXAAAGASD-26AUG06", "KXAAAGASD-26AUG06"),
    "KXAAAGASM-25MAR31-US-4.00": ("KXAAAGASM", "KXAAAGASM-25MAR31",
                                  "KXAAAGASM-25MAR31-US"),
    "KXCPI-26SEP-T-0.4": ("KXCPI", "KXCPI-26SEP", "KXCPI-26SEP-T"),
    "KXADJOURNRECESS-26AUG-AUG10": ("KXADJOURNRECESS", "KXADJOURNRECESS-26AUG",
                                    "KXADJOURNRECESS-26AUG"),
    "KXTOPMODEL-26AUG10-CLAUM": ("KXTOPMODEL", "KXTOPMODEL-26AUG10",
                                 "KXTOPMODEL-26AUG10"),
}


def test_t4_grain_corpus_pins_every_extractor():
    """Identity-review detector 6: the repo's ticker parsers are pinned to golden
    outputs over REAL venue shapes. The two event grains legitimately differ on
    4-field/T-negative tickers (documented A-2; metadata resolution is the live
    source of truth) — this pin exists so any FURTHER drift fails loudly here."""
    for t, (series, ev_key, str_ev) in CORPUS.items():
        assert kcf._series(t) == series
        assert t.split("-")[0] == series, "the universal split assumption"
        assert q._event_key(t) == ev_key, f"quoter event grain drifted for {t}"
        assert kcf._event(t) == str_ev, f"feedback fallback grain drifted for {t}"


def test_t5_probe_streak_state_machine(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=20, mktcap=40, totcap=100)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXALLOW"])
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 1)
    monkeypatch.setattr(q, "_PROBE_GATE_REFUSED", {})
    fp = [{"ticker": "KXPRB-26AUG-A", "usd_day": 50.0, "target": 1,
           "end": "2099-01-01T00:00:00Z", "explore": True}]
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: fp)
    one_sided = {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]], "no_dollars": []}}
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else one_sided)
    _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert st.get("probe_gate_refused", {}).get("KXPRB-26AUG-A") == 1, \
        "a book-gate refusal must bump the persisted streak"
    # healthy two-sided book -> the streak clears
    two_sided = {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]],
                                  "no_dollars": [["0.49", "9999"]]}}
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else two_sided)
    _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
    st2 = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert "KXPRB-26AUG-A" not in st2.get("probe_gate_refused", {}), \
        "resting anything clears the streak"

"""Fix H (operator-ruled 2026-08-06 "we should add"): FAR-CLOSE PAYING EXCEPTION.

Measured 2026-08-06T00:05Z: KXCHIPBURRITO — receipt-proven allowlist payer — carries a
$990/day program (6 mkts x $165) paying 2026-08-03→08-09, but its markets close
2026-09-02, so the 8-day market-clock cap dropped all 6 at selection every cycle (0
telemetry rows all day). The horizon rule's lockup fear is answered by existing
machinery: entry cutoffs key on the PROGRAM window, and when the program expires the
rows leave the harvest and strand-unwind flattens anything held.

Rule: with KALSHI_FARCLOSE_PAYING_EXCEPTION=1, a row whose series has venue credit
RECEIPTS (credits_n>0, same proof the W7 ramp trusts) and whose PROGRAM window ends
inside MAX_DAYS_TO_CLOSE is KEPT past the market-close horizon. Probes/unknowns and
flag-off keep the hard rule byte-identically.

Pins:
  P1 helper truth table (proven+near program end / unproven / far program end / flag off)
  P2 selection integration: proven far-close row KEPT + counted; unproven still dropped
  P3 flag ships OFF
  P4 both drop sites route through the ONE helper (source pin)
"""
import datetime as dt

import maker_kalshi_quoter as q

NOW = dt.datetime(2026, 8, 6, 0, 0, tzinfo=dt.timezone.utc)


def _prog(ticker, prog_end_days=3.0):
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "target_size_fp": "100.00", "discount_factor_bps": 5000,
            "period_reward": 1650000,
            "start_date": (NOW - dt.timedelta(days=3)).isoformat(),
            "end_date": (NOW + dt.timedelta(days=prog_end_days)).isoformat()}


def test_p1_helper_truth_table(monkeypatch):
    monkeypatch.setattr(q, "FARCLOSE_PAYING_EXCEPTION", 1)
    monkeypatch.setattr(q, "_d3_feedback_cached",
                        lambda ts: {"KXCHIPBURRITO": {"credits_n": 3}})
    near_end = (NOW + dt.timedelta(days=3)).isoformat()
    far_end = (NOW + dt.timedelta(days=30)).isoformat()
    assert q._farclose_paying_keep("KXCHIPBURRITO", near_end, NOW) is True
    assert q._farclose_paying_keep("KXUNKNOWN", near_end, NOW) is False
    assert q._farclose_paying_keep("KXCHIPBURRITO", far_end, NOW) is False
    assert q._farclose_paying_keep("KXCHIPBURRITO", None, NOW) is False
    assert q._farclose_paying_keep("KXCHIPBURRITO", "garbage", NOW) is False
    monkeypatch.setattr(q, "FARCLOSE_PAYING_EXCEPTION", 0)
    assert q._farclose_paying_keep("KXCHIPBURRITO", near_end, NOW) is False


def test_p2_selection_keeps_proven_paying_farclose_row(monkeypatch):
    monkeypatch.setattr(q, "FARCLOSE_PAYING_EXCEPTION", 1)
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXCHIPBURRITO"])
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 1)
    monkeypatch.setattr(q, "_d3_feedback_cached",
                        lambda ts: {"KXCHIPBURRITO": {"credits_n": 3}})
    far_close = (NOW + dt.timedelta(days=27)).isoformat()
    monkeypatch.setattr(q, "_close_cache_get", lambda t: far_close)
    monkeypatch.setattr(q, "_vol24_cache_get", lambda t: 0.0)
    progs = [_prog("KXCHIPBURRITO-26SEP02-T9.82"), _prog("KXFARPROBE-26SEP02-T1")]
    rows = q.select_footprint(progs, NOW)
    tickers = {r["ticker"] for r in rows}
    drops = dict(q.FP_DROPS)
    assert "KXCHIPBURRITO-26SEP02-T9.82" in tickers, \
        "receipt-proven + paying program must survive the market-clock cap"
    assert "KXFARPROBE-26SEP02-T1" not in tickers, \
        "unproven far-close row keeps the hard 8-day rule"
    assert drops.get("farclose_paying_kept") == 1
    assert drops.get("drop_far_market_close_sel") == 1


def test_p3_flag_ships_off(monkeypatch):
    assert q.FARCLOSE_PAYING_EXCEPTION == 0
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXCHIPBURRITO"])
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 1)
    monkeypatch.setattr(q, "_d3_feedback_cached",
                        lambda ts: {"KXCHIPBURRITO": {"credits_n": 3}})
    far_close = (NOW + dt.timedelta(days=27)).isoformat()
    monkeypatch.setattr(q, "_close_cache_get", lambda t: far_close)
    monkeypatch.setattr(q, "_vol24_cache_get", lambda t: 0.0)
    rows = q.select_footprint([_prog("KXCHIPBURRITO-26SEP02-T9.82")], NOW)
    assert not rows, "flag off = the hard horizon rule, byte-identical"


def test_p4_both_drop_sites_use_the_helper():
    src = open(q.__file__, encoding="utf-8", errors="replace").read()
    assert src.count("_farclose_paying_keep(") >= 3, \
        "def + selection site + run_once belt site expected"

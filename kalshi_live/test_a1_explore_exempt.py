"""A1 (logic audit 2026-08-05, CONFIRMED live): rank()'s SCORE_EXPLORE quota tagged
ALLOWLIST payer rows as stale/unknown sampling picks, so the EXPLORE_PROBE_CT clamp sized
proven earners at probe scale — KXTOPMODEL-26AUG10-CLAUM measured explore_probe_capped 28x
(5ct both sides, ramp_capped 0) in the 14:13-19:41Z pilot window (quotes-20260805.jsonl).
Explore exists to measure UNKNOWNS; a receipt-proven allowlist series must never spend its
earning size on a sampling slot. The selection-level pin (test_pilot_allowlist: "allowlist
series must quote FULL size, never probe") was violated one stage later, inside rank().

Pins:
  P1 rank(explore_exempt=...) never tags rows of exempt series (stale OR unknown)
  P2 freed slots go to non-exempt candidates — the quota is redirected, not shrunk
  P3 a row pre-tagged explore=True (pilot probe containment, quoter select) is never
     untagged by rank, exempt set or not
  P4 explore_exempt=None keeps the pre-fix behaviour: stale allowlist rows ARE tagged
     (this is the defect, pinned so the default stays inert for non-pilot callers)
  P5 the quoter passes SERIES_ALLOW as the exemption set to rank()
"""
import datetime as dt

import kalshi_market_scores as ks
import maker_kalshi_quoter as q


def _row(t, pool):
    return {"ticker": t, "usd_day": pool}


def _stale_cache(*tickers, now=100000.0):
    """Cache where every ticker was measured STALE_S+1 seconds ago -> label 'stale'."""
    m = {}
    for t in tickers:
        ks.update(m, t, 5.0, 0.50, now=now - ks.STALE_S - 1.0)
    return m


def test_p1_exempt_series_rows_are_never_explore_tagged():
    now = 100000.0
    m = _stale_cache("KXTOPMODEL-26AUG10-CLAUM", now=now)
    rows = [_row("KXTOPMODEL-26AUG10-CLAUM", 100), _row("KXNEW-26AUG10-T1", 1)]
    out = ks.rank(m, rows, now=now, explore=2, explore_exempt={"KXTOPMODEL"})
    by_t = {r["ticker"]: r for r in out}
    assert not by_t["KXTOPMODEL-26AUG10-CLAUM"].get("explore"), \
        "a receipt-proven payer took a sampling slot — the A1 defect"
    assert by_t["KXNEW-26AUG10-T1"].get("explore") is True, \
        "the unknown must still be sampled"


def test_p2_freed_slot_goes_to_the_next_non_exempt_candidate():
    now = 100000.0
    m = _stale_cache("KXOTHER-26AUG10-T1", now=now)
    # The exempt payer is stamped strictly OLDER, so the stale queue's (ts, ticker) order
    # puts it FIRST — pre-fix it absorbs the single slot (adversarial review 2026-08-05
    # reproduced exactly that against HEAD); post-fix the slot must land on the
    # non-exempt stale row instead. Equal timestamps would let the ticker tie-break give
    # KXOTHER the slot even without the fix — a non-discriminating pin.
    ks.update(m, "KXTOPMODEL-26AUG10-CLAUM", 5.0, 0.50, now=now - ks.STALE_S - 100.0)
    rows = [_row("KXTOPMODEL-26AUG10-CLAUM", 100), _row("KXOTHER-26AUG10-T1", 1)]
    out = ks.rank(m, rows, now=now, explore=1, explore_exempt={"KXTOPMODEL"})
    by_t = {r["ticker"]: r for r in out}
    assert not by_t["KXTOPMODEL-26AUG10-CLAUM"].get("explore")
    assert by_t["KXOTHER-26AUG10-T1"].get("explore") is True


def test_p3_pretagged_probe_rows_are_never_untagged():
    now = 100000.0
    probe = _row("KXPROBE-26AUG10-T1", 1)
    probe["explore"] = True                      # pilot containment tag from select
    out = ks.rank({}, [probe, _row("KXTOPMODEL-26AUG10-CLAUM", 100)], now=now,
                  explore=0, explore_exempt={"KXTOPMODEL"})
    by_t = {r["ticker"]: r for r in out}
    assert by_t["KXPROBE-26AUG10-T1"].get("explore") is True, \
        "rank must never strip the probe containment tag"


def test_p4_default_none_keeps_prefix_behaviour():
    now = 100000.0
    m = _stale_cache("KXTOPMODEL-26AUG10-CLAUM", now=now)
    rows = [_row("KXTOPMODEL-26AUG10-CLAUM", 100)]
    out = ks.rank(m, rows, now=now, explore=1, explore_exempt=None)
    assert out[0].get("explore") is True, \
        "with no exemption the stale row still gets sampled (non-pilot behaviour unchanged)"


def test_p5_quoter_passes_series_allow_as_exemption(monkeypatch):
    captured = {}
    real_rank = ks.rank

    def spy(markets, rows, **kw):
        captured.update(kw)
        return real_rank(markets, rows, **kw)

    monkeypatch.setattr(ks, "rank", spy)
    monkeypatch.setattr(q, "SCORE_RANK", 1)
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXTOPMODEL"])
    monkeypatch.setattr(q, "SCORES", {})
    now = dt.datetime.now(dt.timezone.utc)
    close = (now + dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(q, "_close_cache_get", lambda t: close)
    monkeypatch.setattr(q, "_vol24_cache_get", lambda t: 0.0)
    prog = {"market_ticker": "KXTOPMODEL-26AUG10-CLAUM", "incentive_type": "liquidity",
            "target_size_fp": "100.00", "discount_factor_bps": 5000,
            "period_reward": 1000000,
            "start_date": (now - dt.timedelta(days=1)).isoformat(),
            "end_date": (now + dt.timedelta(days=2)).isoformat()}
    q.select_footprint([prog], now)
    assert captured.get("explore_exempt") == frozenset({"KXTOPMODEL"}), \
        "the quoter must exempt the pilot allowlist from explore tagging"

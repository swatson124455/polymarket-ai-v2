"""Closed-world pilot (operator-ruled 2026-08-05) — allowlist + probe-exception pins.

Rulings pinned: universe = allowlist series at full size; non-allowlist series pass ONLY
as explore-tagged probe rows when KALSHI_ALLOW_PROBE_EXCEPTION=1 (sized by the existing
EXPLORE_PROBE_CT clamp + the armed D3 ramp); flag off = the allowlist is absolute.
"""
import datetime as dt

import maker_kalshi_quoter as q


def _prog(ticker, end_days=2.0):
    now = dt.datetime.now(dt.timezone.utc)
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "target_size_fp": "100.00", "discount_factor_bps": 5000,
            "period_reward": 1000000,
            "start_date": (now - dt.timedelta(days=1)).isoformat(),
            "end_date": (now + dt.timedelta(days=end_days)).isoformat()}


def _run(monkeypatch, exception_on):
    monkeypatch.setattr(q, "SERIES_ALLOW", {"KXAAAGASD"})
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 1 if exception_on else 0)
    now = dt.datetime.now(dt.timezone.utc)
    # B-2 (2026-08-06): probe slots need a KNOWN close clock — stub it
    close = (now + dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(q, "_close_cache_get", lambda t: close)
    monkeypatch.setattr(q, "_vol24_cache_get", lambda t: 0.0)
    progs = [_prog("KXAAAGASD-26AUG09-4.100"), _prog("KXOTHER-26AUG09-T1")]
    return q.select_footprint(progs, now), dict(q.FP_DROPS)


def test_flag_off_allowlist_is_absolute(monkeypatch):
    rows, drops = _run(monkeypatch, exception_on=False)
    tickers = [r["ticker"] for r in rows]
    assert any(t.startswith("KXAAAGASD") for t in tickers)
    assert not any(t.startswith("KXOTHER") for t in tickers)
    assert drops.get("drop_allowlist") == 1


def test_flag_on_nonallowlist_passes_probe_only(monkeypatch):
    rows, drops = _run(monkeypatch, exception_on=True)
    by_t = {r["ticker"]: r for r in rows}
    allowed = next(r for t, r in by_t.items() if t.startswith("KXAAAGASD"))
    probe = next(r for t, r in by_t.items() if t.startswith("KXOTHER"))
    assert not allowed.get("explore"), "allowlist series must quote FULL size, never probe"
    assert probe.get("explore") is True, "non-allowlist series must be probe-tagged"
    assert drops.get("allow_probe_passed") == 1
    assert not drops.get("drop_allowlist")


def test_flag_ships_off():
    assert q.ALLOW_PROBE_EXCEPTION == 0


def test_probe_slot_cap_binds_best_pool_first(monkeypatch):
    """Operator 2026-08-05: probes as small as possible — at most PROBE_MAX_SLOTS probe
    markets survive selection; allowlist rows are never capped."""
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXAAAGASD"])
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 1)
    monkeypatch.setattr(q, "PROBE_MAX_SLOTS", 2)
    import datetime as dt2
    now = dt2.datetime.now(dt2.timezone.utc)
    _close = (now + dt2.timedelta(days=1)).isoformat()
    monkeypatch.setattr(q, "_close_cache_get", lambda t: _close)   # B-2 stub
    monkeypatch.setattr(q, "_vol24_cache_get", lambda t: 0.0)
    progs = [_prog("KXAAAGASD-26AUG09-4.100")] +             [_prog(f"KXPROBE{i}-26AUG09-T1") for i in range(5)]
    rows = q.select_footprint(progs, now)
    probes = [r for r in rows if r.get("explore")]
    allowed = [r for r in rows if not r.get("explore")]
    assert len(probes) == 2
    assert len(allowed) == 1 and allowed[0]["ticker"].startswith("KXAAAGASD")
    assert dict(q.FP_DROPS).get("probe_slots_dropped") == 3


def test_activity_gate_exempts_allowlist_but_blocks_probes(monkeypatch):
    """Option A (operator 2026-08-05): a receipts-proven allowlist series passes the
    high-activity gate at any volume (its own payment history is the screen — gas paid
    13 credits at 1k-21.6k ct); a non-allowlist probe row above the ceiling still drops.
    Also pins the probe-cap ORDER fix: the cap now runs after the pre-filter, so kept
    probe slots are survivors, not deterministically-doomed rows."""
    import datetime as dtx
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXAAAGASD"])
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 1)
    monkeypatch.setattr(q, "PROBE_MAX_SLOTS", 1)
    monkeypatch.setattr(q, "MAX_VOL24H_CT", 1000)
    now = dtx.datetime.now(dtx.timezone.utc)
    close = (now + dtx.timedelta(days=1)).isoformat()
    vols = {"KXAAAGASD-26AUG09-4.100": 21636.0,   # gas at real measured activity
            "KXHOT-26AUG09-T1": 32000.0,          # doomed hot probe
            "KXCALM-26AUG09-T1": 50.0}            # quiet probe — should take the slot
    monkeypatch.setattr(q, "_close_cache_get", lambda t: close)
    monkeypatch.setattr(q, "_vol24_cache_get", lambda t: vols.get(t, 0.0))
    progs = [_prog("KXAAAGASD-26AUG09-4.100"),
             _prog("KXHOT-26AUG09-T1"), _prog("KXCALM-26AUG09-T1")]
    rows = q.select_footprint(progs, now)
    tickers = {r["ticker"] for r in rows}
    assert "KXAAAGASD-26AUG09-4.100" in tickers, "allowlist must pass the activity gate"
    assert "KXHOT-26AUG09-T1" not in tickers, "hot probe must still drop"
    assert "KXCALM-26AUG09-T1" in tickers, "the probe slot must go to a SURVIVOR"
    assert dict(q.FP_DROPS).get("drop_high_activity") == 1

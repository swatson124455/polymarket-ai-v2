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
    progs = [_prog("KXAAAGASD-26AUG09-4.100")] +             [_prog(f"KXPROBE{i}-26AUG09-T1") for i in range(5)]
    rows = q.select_footprint(progs, now)
    probes = [r for r in rows if r.get("explore")]
    allowed = [r for r in rows if not r.get("explore")]
    assert len(probes) == 2
    assert len(allowed) == 1 and allowed[0]["ticker"].startswith("KXAAAGASD")
    assert dict(q.FP_DROPS).get("probe_slots_dropped") == 3

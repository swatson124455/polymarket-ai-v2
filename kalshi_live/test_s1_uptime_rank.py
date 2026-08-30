"""Pins for S1 UPTIME RANK (operator-approved 2026-08-30): candidate order = pool x
measured qualifying-uptime; flag off = exact legacy order; missing/stale census rows
fall back to the neutral default so discovery survives. Census writer pinned separately
by running it over a synthetic tape."""
import json
import time

from test_live_hardening import q


def _prog(t, pool_cc, end="2026-09-06T03:59:00Z"):
    return {"market_ticker": t, "incentive_type": "liquidity",
            "target_size_fp": "1000.00", "discount_factor_bps": 5000,
            "period_reward": pool_cc, "start_date": "2026-08-30T00:00:00Z",
            "end_date": end}


def _cfg(monkeypatch, rank=1):
    monkeypatch.setattr(q, "UPTIME_RANK", rank)
    monkeypatch.setattr(q, "UPTIME_RANK_DEFAULT", 0.25)
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "SERIES_DENY", set())
    monkeypatch.setattr(q, "MACRO_PROBE_TICKERS", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0)      # skip the market-clock prefilter
    monkeypatch.setattr(q, "FOOTPRINT_TOP", 3)


def test_u1_flag_off_orders_by_pool(monkeypatch):
    _cfg(monkeypatch, rank=0)
    fp = q.select_footprint([_prog("KXA-EV-1", 500000), _prog("KXB-EV-1", 1200000)],
                            q.utcnow())
    assert [r["ticker"] for r in fp[:2]] == ["KXB-EV-1", "KXA-EV-1"]   # pure pool order


def test_u2_zero_uptime_pool_giant_ranks_below_measured_payer(monkeypatch):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "_uptime_census_cached",
                        lambda ts, **k: {"KXB-EV-1": 0.0, "KXA-EV-1": 0.9})
    fp = q.select_footprint([_prog("KXA-EV-1", 500000), _prog("KXB-EV-1", 1200000)],
                            q.utcnow())
    # $120-pool at 0% uptime earns $0; $50-pool at 90% earns ~$45-equivalent -> A first
    assert [r["ticker"] for r in fp[:2]] == ["KXA-EV-1", "KXB-EV-1"]


def test_u3_unmeasured_ticker_gets_default_multiplier(monkeypatch):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "_uptime_census_cached", lambda ts, **k: {"KXA-EV-1": 0.9})
    fp = q.select_footprint([_prog("KXA-EV-1", 500000), _prog("KXC-EV-1", 1200000)],
                            q.utcnow())
    # unmeasured C: 120 x 0.25 = 30 < A's 50 x 0.9 = 45 -> A first, C still present
    assert [r["ticker"] for r in fp[:2]] == ["KXA-EV-1", "KXC-EV-1"]


def test_u4_census_loader_drops_stale_rows(tmp_path, monkeypatch):
    now = time.time()
    p = tmp_path / "census.json"
    json.dump({"KXFRESH": {"uptime": 0.8, "ts": now - 60},
               "KXSTALE": {"uptime": 0.8, "ts": now - 200000}}, open(p, "w"))
    monkeypatch.setattr(q, "UPTIME_CENSUS_PATH", str(p))
    monkeypatch.setattr(q, "_UPTIME_CACHE", {"ts": 0.0, "table": {}})
    t = q._uptime_census_cached(now)
    assert t.get("KXFRESH") == 0.8 and "KXSTALE" not in t


def test_u5_census_writer_over_synthetic_tape(tmp_path, monkeypatch):
    import kalshi_uptime_census as cen
    d4 = tmp_path / ("d4_books-%s.jsonl" %
                     __import__("datetime").datetime.now(
                         __import__("datetime").timezone.utc).strftime("%Y%m%d"))
    rows = []
    for i in range(10):
        rows.append({"ticker": "KXQ-EV-1", "bid_depth": [[0.5, 1200]],
                     "ask_depth": [[0.49, 1100]]})           # qualifies
        rows.append({"ticker": "KXZ-EV-1", "bid_depth": [[0.98, 900]],
                     "ask_depth": [[0.99, 40]]})             # never qualifies
    open(d4, "w").write("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(cen, "HERE", str(tmp_path))
    monkeypatch.setattr(cen, "OUT", str(tmp_path / "kalshi_uptime_census.json"))
    monkeypatch.setattr(cen, "ARCHIVE", str(tmp_path))       # no archive files present
    out = cen.run(days=1)
    assert out["KXQ-EV-1"]["uptime"] == 1.0 and out["KXQ-EV-1"]["snaps"] == 10
    assert out["KXZ-EV-1"]["uptime"] == 0.0
    assert json.load(open(tmp_path / "kalshi_uptime_census.json"))["KXQ-EV-1"]["uptime"] == 1.0

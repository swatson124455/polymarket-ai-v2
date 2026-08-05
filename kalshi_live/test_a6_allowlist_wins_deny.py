"""A6 (logic audit 2026-08-05, operator-ruled: allowlist wins): SERIES_DENY is a PREFIX
match (quoter select) that ran unconditionally after the allowlist, so a deny prefix
could drop an exactly-allowlisted payer series — live.env carries
SERIES_DENY=KXDXY,KXNDQ,KXINX,KXDJI while SERIES_ALLOW contains KXDXYDUD, KXNDQHUD,
KXINXHUD (armed-to-misfire; drop_series_deny=0 on 2026-08-05 only because those
programs were inactive). Ruling: exact SERIES_ALLOW membership beats a deny prefix;
deny keeps full force for every non-allowlisted series, probe rows included."""
import datetime as dt

import maker_kalshi_quoter as q


def _prog(ticker, end_days=2.0):
    now = dt.datetime.now(dt.timezone.utc)
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "target_size_fp": "100.00", "discount_factor_bps": 5000,
            "period_reward": 1000000,
            "start_date": (now - dt.timedelta(days=1)).isoformat(),
            "end_date": (now + dt.timedelta(days=end_days)).isoformat()}


def _select(monkeypatch, progs, allow, deny, probe_exception=0):
    monkeypatch.setattr(q, "SERIES_ALLOW", allow)
    monkeypatch.setattr(q, "SERIES_DENY", deny)
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", probe_exception)
    now = dt.datetime.now(dt.timezone.utc)
    close = (now + dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(q, "_close_cache_get", lambda t: close)
    monkeypatch.setattr(q, "_vol24_cache_get", lambda t: 0.0)
    rows = q.select_footprint(progs, now)
    return {r["ticker"] for r in rows}, dict(q.FP_DROPS)


def test_allowlisted_series_survives_a_deny_prefix(monkeypatch):
    tickers, drops = _select(monkeypatch, [_prog("KXINXHUD-26AUG09-T1")],
                             allow=["KXINXHUD"], deny=["KXINX"])
    assert "KXINXHUD-26AUG09-T1" in tickers, \
        "an exact allowlist member must not be deny-dropped by a prefix"
    assert not drops.get("drop_series_deny")


def test_deny_still_drops_non_allowlisted_series(monkeypatch):
    tickers, drops = _select(monkeypatch, [_prog("KXINXFOO-26AUG09-T1"),
                                           _prog("KXAAAGASD-26AUG09-4.100")],
                             allow=["KXAAAGASD"], deny=["KXINX"],
                             probe_exception=1)
    assert "KXINXFOO-26AUG09-T1" not in tickers, \
        "deny keeps full force for non-allowlisted series, probe exception or not"
    assert drops.get("drop_series_deny") == 1
    assert "KXAAAGASD-26AUG09-4.100" in tickers


def test_deny_unchanged_when_no_allowlist(monkeypatch):
    tickers, drops = _select(monkeypatch, [_prog("KXINXHUD-26AUG09-T1")],
                             allow=[], deny=["KXINX"])
    assert not tickers
    assert drops.get("drop_series_deny") == 1

"""HTTP failure-path tests for the odds collectors (canonical + standalone).

These are the network edges the synthetic stress harness cannot reach: what the
collector does on 429 (Retry-After numeric / HTTP-date / huge), 401/403, raw
transport errors, and 200-with-non-JSON bodies (WAF challenge pages). The
contract everywhere is correct-or-absent: a bad tick yields NO rows (empty
result), never a crash and never garbage rows.

All network calls are monkeypatched — no real egress, CI-safe.
"""
import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest
import requests

from esports_v2.data.pinnodds_loader import PinnOddsLoader

_STANDALONE = (Path(__file__).resolve().parents[2]
               / "deploy" / "vps" / "collect_pinnodds_standalone.py")


def _load_standalone():
    spec = importlib.util.spec_from_file_location("eb_standalone_http", _STANDALONE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── canonical PinnOddsLoader._get ────────────────────────────────────────────


class _Resp:
    def __init__(self, status=200, body=None, headers=None, bad_json=False):
        self.status_code = status
        self._body = body if body is not None else {"events": []}
        self.headers = headers or {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise requests.exceptions.JSONDecodeError("bad", "<html>", 0)
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")


def _loader_with(responses, monkeypatch, sleeps):
    loader = PinnOddsLoader(api_key="k")
    it = iter(responses)
    monkeypatch.setattr(loader._session, "get", lambda *a, **k: next(it))
    import esports_v2.data.pinnodds_loader as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    return loader


def test_canonical_429_numeric_retry_after_then_success(monkeypatch):
    sleeps = []
    loader = _loader_with(
        [_Resp(429, headers={"Retry-After": "2"}), _Resp(200, {"ok": 1})],
        monkeypatch, sleeps)
    assert loader._get("/markets") == {"ok": 1}
    assert sleeps == [2]


def test_canonical_429_http_date_retry_after_does_not_crash(monkeypatch):
    # RFC 7231 allows an HTTP-date Retry-After; int() on it must not blow up.
    sleeps = []
    loader = _loader_with(
        [_Resp(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
         _Resp(200, {"ok": 1})],
        monkeypatch, sleeps)
    assert loader._get("/markets") == {"ok": 1}
    assert sleeps and all(s <= 60 for s in sleeps)


def test_canonical_429_huge_retry_after_is_capped(monkeypatch):
    # an honest Retry-After: 3600 must not hang a cron tick for an hour
    sleeps = []
    loader = _loader_with(
        [_Resp(429, headers={"Retry-After": "3600"}), _Resp(200, {"ok": 1})],
        monkeypatch, sleeps)
    assert loader._get("/markets") == {"ok": 1}
    assert sleeps and max(sleeps) <= 60


def test_canonical_auth_failure_returns_none_no_retry(monkeypatch):
    sleeps = []
    loader = _loader_with([_Resp(403)], monkeypatch, sleeps)
    assert loader._get("/markets") is None
    assert sleeps == []


def test_canonical_non_json_200_returns_none(monkeypatch):
    # WAF/challenge pages: 200 with an HTML body -> absent, not a crash
    sleeps = []
    loader = _loader_with(
        [_Resp(200, bad_json=True), _Resp(200, bad_json=True),
         _Resp(200, bad_json=True)],
        monkeypatch, sleeps)
    assert loader._get("/markets") is None


def test_canonical_transport_errors_exhaust_to_none(monkeypatch):
    sleeps = []
    loader = PinnOddsLoader(api_key="k")

    def boom(*a, **k):
        raise requests.ConnectionError("nope")

    monkeypatch.setattr(loader._session, "get", boom)
    import esports_v2.data.pinnodds_loader as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    assert loader._get("/markets") is None


# ── standalone fetch() / gamma_page() ────────────────────────────────────────


class _Hdrs:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k, default)


class _Reader:
    """urlopen context manager yielding a file-like body."""

    def __init__(self, payload: bytes):
        self._fp = io.BytesIO(payload)

    def __enter__(self):
        return self._fp

    def __exit__(self, *a):
        return False


def _http_error(code, retry_after=None):
    hdrs = _Hdrs({"Retry-After": retry_after} if retry_after else {})
    return urllib.error.HTTPError("u", code, "msg", hdrs, io.BytesIO(b""))


def _patch_urlopen(monkeypatch, sa, outcomes):
    it = iter(outcomes)

    def fake(req, timeout=0):
        o = next(it)
        if isinstance(o, Exception):
            raise o
        return _Reader(o)

    monkeypatch.setattr(sa.urllib.request, "urlopen", fake)
    monkeypatch.setattr(sa.time, "sleep", lambda s: None)


def test_standalone_429_then_success(monkeypatch):
    sa = _load_standalone()
    _patch_urlopen(monkeypatch, sa,
                   [_http_error(429, "1"), b'{"events": [1]}'])
    assert sa.fetch("k") == {"events": [1]}


def test_standalone_persistent_429_gives_empty(monkeypatch):
    sa = _load_standalone()
    _patch_urlopen(monkeypatch, sa, [_http_error(429, "1")] * 4)
    assert sa.fetch("k") == {}


def test_standalone_403_gives_empty_immediately(monkeypatch):
    sa = _load_standalone()
    calls = []
    it = iter([_http_error(403)])

    def fake(req, timeout=0):
        calls.append(1)
        raise next(it)

    monkeypatch.setattr(sa.urllib.request, "urlopen", fake)
    assert sa.fetch("k") == {}
    assert len(calls) == 1


def test_standalone_transport_retry_then_success(monkeypatch):
    sa = _load_standalone()
    _patch_urlopen(monkeypatch, sa,
                   [urllib.error.URLError("boom"), b'{"events": []}'])
    assert sa.fetch("k") == {"events": []}


def test_standalone_non_json_200_gives_empty(monkeypatch):
    # WAF challenge page: HTTP 200 with HTML -> a lost tick, never a crash
    sa = _load_standalone()
    _patch_urlopen(monkeypatch, sa, [b"<html>challenge</html>"])
    assert sa.fetch("k") == {}


def test_standalone_gamma_page_422_gives_none(monkeypatch):
    sa = _load_standalone()
    _patch_urlopen(monkeypatch, sa, [_http_error(422)])
    assert sa.gamma_page(2100) is None


def test_standalone_gamma_page_non_json_gives_none(monkeypatch):
    sa = _load_standalone()
    _patch_urlopen(monkeypatch, sa, [b"<html>oops</html>"])
    assert sa.gamma_page(0) is None

"""Tests for the pooled HTTP transport (KALSHI_HTTP_POOL).

The pooling itself is just speed (measured 2026-07-25 live: p50 173ms -> 63ms,
2.7x). These tests pin the two things that make it SAFE rather than merely fast:

  1. urllib3 does NOT raise on 4xx/5xx but urlopen DOES, and every caller in
     this codebase is written against that raise — a non-raising 4xx would read
     as a SUCCESSFUL ORDER. The pooled path must raise urllib.error.HTTPError.
  2. urllib3 retries by default. A retried POST could DOUBLE SUBMIT AN ORDER.
     The pool must be constructed with retries=False.

Each test is written so that breaking the property makes it fail (mutation-
checked alongside the WS suite).
"""
import io
import json
import urllib.error

import pytest

import maker_kalshi_client as C


class FakeResp:
    def __init__(self, status=200, data=b'{"ok":true}', reason="OK"):
        self.status = status
        self.data = data
        self.reason = reason
        self.headers = {}


class FakePool:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def request(self, method, url, body=None, headers=None, preload_content=True,
                redirect=False):
        self.calls.append({"method": method, "url": url, "body": body,
                           "headers": dict(headers or {}), "redirect": redirect})
        return self.resp


@pytest.fixture
def pooled(monkeypatch):
    monkeypatch.setattr(C, "HTTP_POOL", True)
    return monkeypatch


def _client():
    return C.KalshiOrderClient(mode="dry_run")


def test_pooled_success_returns_parsed_json(pooled, monkeypatch):
    fp = FakePool(FakeResp(200, b'{"balance_dollars":"12.34"}'))
    monkeypatch.setattr(C, "_pool", lambda: fp)
    out = _client()._request("GET", C.API_ROOT + "/portfolio/balance", authed=False)
    assert out == {"balance_dollars": "12.34"}
    assert fp.calls[0]["method"] == "GET"


def test_pooled_4xx_raises_httperror_kills_broken_variant(pooled, monkeypatch):
    """THE dangerous one: a 4xx that does not raise reads as a successful order."""
    fp = FakePool(FakeResp(400, b'{"error":"bad"}', "Bad Request"))
    monkeypatch.setattr(C, "_pool", lambda: fp)
    with pytest.raises(urllib.error.HTTPError) as ei:
        _client()._request("POST", C.API_ROOT + "/portfolio/events/orders",
                           body={"x": 1}, authed=False)
    assert ei.value.code == 400


def test_pooled_5xx_raises_httperror(pooled, monkeypatch):
    fp = FakePool(FakeResp(503, b"upstream", "Service Unavailable"))
    monkeypatch.setattr(C, "_pool", lambda: fp)
    with pytest.raises(urllib.error.HTTPError) as ei:
        _client()._request("GET", C.API_ROOT + "/exchange/status", authed=False)
    assert ei.value.code == 503


def test_pooled_httperror_body_is_readable(pooled, monkeypatch):
    """Callers that read the error body must still be able to."""
    fp = FakePool(FakeResp(422, b'{"error":"detail"}', "Unprocessable"))
    monkeypatch.setattr(C, "_pool", lambda: fp)
    try:
        _client()._request("POST", C.API_ROOT + "/x", body={}, authed=False)
        raise AssertionError("should have raised")
    except urllib.error.HTTPError as e:
        assert json.loads(e.read())["error"] == "detail"


def test_pooled_empty_body_parses_as_empty_object(pooled, monkeypatch):
    fp = FakePool(FakeResp(200, b""))
    monkeypatch.setattr(C, "_pool", lambda: fp)
    assert _client()._request("DELETE", C.API_ROOT + "/x", authed=False) == {}


def test_pooled_sets_content_type_on_writes(pooled, monkeypatch):
    fp = FakePool(FakeResp(200))
    monkeypatch.setattr(C, "_pool", lambda: fp)
    _client()._request("POST", C.API_ROOT + "/x", body={"a": 1}, authed=False)
    assert fp.calls[0]["headers"].get("Content-Type") == "application/json"
    assert json.loads(fp.calls[0]["body"]) == {"a": 1}


def test_pooled_no_redirect_following(pooled, monkeypatch):
    fp = FakePool(FakeResp(200))
    monkeypatch.setattr(C, "_pool", lambda: fp)
    _client()._request("GET", C.API_ROOT + "/x", authed=False)
    assert fp.calls[0]["redirect"] is False


def test_pool_is_built_with_retries_disabled_kills_broken_variant(monkeypatch):
    """A retried POST could DOUBLE SUBMIT AN ORDER. urllib3's default is to
    retry; this pins retries=False at construction."""
    captured = {}

    class FakeU3:
        class Timeout:
            def __init__(self, connect=None, read=None):
                self.connect, self.read = connect, read

        @staticmethod
        def PoolManager(**kw):
            captured.update(kw)
            return object()

    monkeypatch.setattr(C, "_POOL", None)
    monkeypatch.setitem(__import__("sys").modules, "urllib3", FakeU3)
    C._pool()
    assert captured.get("retries") is False, captured
    monkeypatch.setattr(C, "_POOL", None)


def test_flag_off_uses_legacy_urlopen_path(monkeypatch):
    """Provable no-op when the flag is off: the pool must never be touched."""
    monkeypatch.setattr(C, "HTTP_POOL", False)

    def boom():
        raise AssertionError("pool must not be used when KALSHI_HTTP_POOL=0")
    monkeypatch.setattr(C, "_pool", boom)
    seen = {}

    class FakeCtx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"legacy":true}'

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return FakeCtx()
    monkeypatch.setattr(C.urllib.request, "urlopen", fake_urlopen)
    out = _client()._request("GET", C.API_ROOT + "/x", authed=False)
    assert out == {"legacy": True} and "url" in seen

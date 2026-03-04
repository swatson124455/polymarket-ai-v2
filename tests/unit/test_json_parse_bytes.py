"""Verify json_parse.loads accepts bytes (WebSocket can pass raw bytes to orjson)."""
import pytest
from base_engine.data.json_parse import loads


def test_loads_accepts_bytes():
    """loads() should accept bytes and return parsed dict (orjson handles bytes natively)."""
    out = loads(b'{"a": 1, "b": "x"}')
    assert out == {"a": 1, "b": "x"}


def test_loads_accepts_str():
    """loads() should accept str as before."""
    out = loads('{"a": 1}')
    assert out == {"a": 1}

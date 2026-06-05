"""Regression tests for the end_date_iso population fix.

Root cause (2026-06-01): markets were being stored with NULL end_date_iso for ~89%
of rows because the CLOB market formatter omitted the field and the Gamma API keys
it under different spellings than the write chokepoint read. NULL end-dates made
markets invisible to resolution_backfill (ORDER BY end_date_iso NULLS LAST), so
they were never marked resolved — which left MirrorBot trying to sell into
already-resolved markets and tripping the circuit breaker.

These tests lock the source-dict fix in _clob_to_market_format so the regression
cannot silently return.
"""
from base_engine.data.resolution_backfill import _clob_to_market_format


def _clob_base(**overrides):
    base = {
        "question": "Will X happen?",
        "market_slug": "will-x-happen",
        "closed": False,
        "tokens": [
            {"outcome": "Yes", "token_id": "1", "price": 0.5},
            {"outcome": "No", "token_id": "2", "price": 0.5},
        ],
    }
    base.update(overrides)
    return base


def test_clob_format_extracts_snake_case_end_date_iso():
    """CLOB returns the end-date under snake_case 'end_date_iso' — it must survive."""
    out = _clob_to_market_format(_clob_base(end_date_iso="2026-06-04T00:00:00Z"), "0xabc")
    assert out["end_date_iso"] == "2026-06-04T00:00:00Z"


def test_clob_format_falls_back_to_camel_keys():
    """If only camelCase variants are present, still capture the date."""
    out = _clob_to_market_format(_clob_base(endDateISO="2026-07-01T00:00:00Z"), "0xdef")
    assert out["end_date_iso"] == "2026-07-01T00:00:00Z"
    out2 = _clob_to_market_format(_clob_base(endDate="2026-08-01T00:00:00Z"), "0xfed")
    assert out2["end_date_iso"] == "2026-08-01T00:00:00Z"


def test_clob_format_missing_end_date_is_none_not_absent():
    """No end-date in the source → key present and None (never KeyError downstream)."""
    out = _clob_to_market_format(_clob_base(), "0xghi")
    assert out.get("end_date_iso") is None

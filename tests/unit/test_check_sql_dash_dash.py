"""Contract tests for scripts/check_sql_dash_dash.py.

Pins the detector's true/false-positive boundary against the S195 bug shape
and the false-positive shapes that bit it during initial implementation
(self-flagging on its own f-string, the `--` text inside a `/* */` comment in
the post-fix INSERT, etc.).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DETECTOR = _REPO / "scripts" / "check_sql_dash_dash.py"


@pytest.fixture(scope="module")
def detector():
    spec = importlib.util.spec_from_file_location("_check_sql_dash_dash", _DETECTOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_check_sql_dash_dash"] = mod
    spec.loader.exec_module(mod)
    return mod


def _check_source(detector, src: str, tmp_path: Path) -> list[str]:
    f = tmp_path / "snippet.py"
    f.write_text(src, encoding="utf-8")
    return detector.check_file(f)


# ───── positive cases (must flag) ─────────────────────────────────────────────

def test_s195_bug_shape_flags(detector, tmp_path: Path) -> None:
    src = (
        'sql = (\n'
        '    "SELECT * FROM trade_events "\n'
        '    "WHERE bot_name = :bot -- S167: side removed from dedup"\n'
        '    "AND market_id = :mkt"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert len(violations) == 1


def test_dash_in_middle_fragment_flags(detector, tmp_path: Path) -> None:
    src = (
        'q = (\n'
        '    "INSERT INTO foo (a) "\n'
        '    "SELECT 1 -- comment without newline"\n'
        '    "WHERE NOT EXISTS (SELECT 1)"\n'
        '    "RETURNING a"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert len(violations) == 1


# ───── negative cases (must NOT flag) ─────────────────────────────────────────

def test_dash_inside_block_comment_does_not_flag(detector, tmp_path: Path) -> None:
    src = (
        'q = (\n'
        '    "INSERT INTO foo (a) SELECT 1 "\n'
        '    "/* explainer mentions -- as a doc reference */ "\n'
        '    "WHERE NOT EXISTS (SELECT 1)"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert violations == []


def test_dash_inside_string_literal_does_not_flag(detector, tmp_path: Path) -> None:
    src = (
        'q = (\n'
        '    "INSERT INTO foo (a) "\n'
        '    "VALUES (\'has -- inside text\') "\n'
        '    "RETURNING a"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert violations == []


def test_dash_with_trailing_newline_does_not_flag(detector, tmp_path: Path) -> None:
    src = (
        'q = (\n'
        '    "SELECT 1 "\n'
        r'    "FROM foo -- legitimate single-line comment\n"' "\n"
        '    "WHERE bar = 1"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert violations == []


def test_dash_only_in_final_fragment_does_not_flag(detector, tmp_path: Path) -> None:
    src = (
        'q = (\n'
        '    "SELECT 1 FROM foo "\n'
        '    "WHERE bar = 1 -- trailing"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert violations == []


def test_english_prose_concat_does_not_flag(detector, tmp_path: Path) -> None:
    """Detector's own help text used to self-flag on `with` matching `WITH`."""
    src = (
        'msg = (\n'
        '    "Found 2 SQL `--`-in-adjacent-string violation(s) "\n'
        '    "found. Replace with `/* ... */` block comments."\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert violations == []


def test_single_string_with_dash_does_not_flag(detector, tmp_path: Path) -> None:
    src = (
        'q = """\n'
        'SELECT 1\n'
        'FROM foo\n'
        '-- single string, not adjacent concat\n'
        'WHERE bar = 1\n'
        '"""\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert violations == []


def test_two_fragments_no_sql_keyword_does_not_flag(detector, tmp_path: Path) -> None:
    src = (
        'msg = (\n'
        '    "Pre-flight: database not configured -- running in API-only mode "\n'
        '    "(DATABASE_URL empty)"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert violations == []


# ───── dollar-quoting (S195 Day 2 follow-up) ──────────────────────────────────

def test_dash_inside_untagged_dollar_block_does_not_flag(
    detector, tmp_path: Path
) -> None:
    """`$$ ... $$` is a dollar-quoted body; `--` inside is part of the body
    text, not a SQL line comment from the outer parser's view."""
    src = (
        'q = (\n'
        '    "CREATE FUNCTION f() RETURNS int AS $$ "\n'
        '    "BEGIN -- inside body, harmless "\n'
        '    "RETURN 1; END $$ LANGUAGE plpgsql"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert violations == []


def test_dash_inside_tagged_dollar_block_does_not_flag(
    detector, tmp_path: Path
) -> None:
    """`$body$ ... $body$` matches close on the same tag only."""
    src = (
        'q = (\n'
        '    "CREATE FUNCTION f() RETURNS int AS $body$ "\n'
        '    "BEGIN -- inside tagged body "\n'
        '    "RETURN 2; END $body$ LANGUAGE plpgsql"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert violations == []


def test_asyncpg_placeholder_does_not_open_dollar_block(
    detector, tmp_path: Path
) -> None:
    """`$1`, `$2` are positional-parameter placeholders, not dollar-quote
    openers. The bug shape (--in non-final fragment, no \\n) must still flag
    when placeholders are present.
    """
    src = (
        'q = (\n'
        '    "SELECT * FROM trade_events "\n'
        '    "WHERE bot_name = $1 -- S167 silently swallowing "\n'
        '    "AND market_id = $2"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert len(violations) == 1


def test_dash_after_dollar_block_close_still_flags(
    detector, tmp_path: Path
) -> None:
    """If the dollar-quoted block closes mid-fragment and a real `--` follows
    in the still-non-final fragment with no trailing newline, that's the bug.
    """
    src = (
        'q = (\n'
        '    "INSERT INTO foo SELECT 1 FROM (SELECT $$ a $$ AS x) -- consumed"\n'
        '    "WHERE bar = 2"\n'
        ')\n'
    )
    violations = _check_source(detector, src, tmp_path)
    assert len(violations) == 1

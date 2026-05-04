"""Contract tests for scripts/config_drift_audit.py extractors.

Pins the AST-walk behaviour for both `os.getenv` / `os.environ` and the
pydantic-settings `getattr(settings, "KEY", default)` pattern (added per
S208 Hygiene Backlog #7 to suppress ENV-ORPHAN false positives — many env
vars in this codebase are read via the Settings object, not os.getenv,
and the original audit only saw the latter).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "config_drift_audit.py"


@pytest.fixture(scope="module")
def audit():
    spec = importlib.util.spec_from_file_location("_config_drift_audit", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_config_drift_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(tmp_path: Path, name: str, src: str) -> Path:
    f = tmp_path / name
    f.write_text(src, encoding="utf-8")
    return f


# ───── extract_getenv_calls — preserved behavior ──────────────────────────────

def test_getenv_with_default(audit, tmp_path: Path) -> None:
    f = _write(tmp_path, "g1.py", 'import os\nx = os.getenv("FOO", "bar")\n')
    out = list(audit.extract_getenv_calls(f))
    # ast.unparse normalizes string literals to single quotes
    assert out == [("FOO", "'bar'", 2)]


def test_getenv_no_default(audit, tmp_path: Path) -> None:
    f = _write(tmp_path, "g2.py", 'import os\nx = os.getenv("FOO")\n')
    out = list(audit.extract_getenv_calls(f))
    assert out == [("FOO", None, 2)]


def test_environ_get_with_default(audit, tmp_path: Path) -> None:
    f = _write(tmp_path, "g3.py", 'import os\nx = os.environ.get("BAZ", "1")\n')
    out = list(audit.extract_getenv_calls(f))
    assert out == [("BAZ", "'1'", 2)]


def test_environ_subscript_recorded(audit, tmp_path: Path) -> None:
    f = _write(tmp_path, "g4.py", 'import os\nx = os.environ["REQUIRED_KEY"]\n')
    out = list(audit.extract_getenv_calls(f))
    assert out == [("REQUIRED_KEY", "<no-default-subscript>", 2)]


def test_dynamic_key_skipped(audit, tmp_path: Path) -> None:
    src = (
        "import os\n"
        'name = "FOO"\n'
        "x = os.getenv(name)\n"
        'y = os.getenv(f"PREFIX_{name}")\n'
    )
    f = _write(tmp_path, "g5.py", src)
    out = list(audit.extract_getenv_calls(f))
    assert out == []


def test_getenv_does_not_match_settings_pattern(audit, tmp_path: Path) -> None:
    """Sanity: getenv extractor must not pick up getattr(settings, ...)."""
    f = _write(
        tmp_path,
        "g6.py",
        'x = getattr(settings, "WEATHER_MIN_EDGE", 0.08)\n',
    )
    out = list(audit.extract_getenv_calls(f))
    assert out == []


# ───── extract_getattr_settings_calls — new pattern ───────────────────────────

def test_getattr_settings_with_default(audit, tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "s1.py",
        'x = getattr(settings, "WEATHER_MIN_EDGE", 0.08)\n',
    )
    out = list(audit.extract_getattr_settings_calls(f))
    assert out == [("WEATHER_MIN_EDGE", "0.08", 1)]


def test_getattr_settings_no_default(audit, tmp_path: Path) -> None:
    f = _write(tmp_path, "s2.py", 'x = getattr(settings, "DATABASE_URL")\n')
    out = list(audit.extract_getattr_settings_calls(f))
    assert out == [("DATABASE_URL", None, 1)]


def test_getattr_settings_string_default(audit, tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "s3.py",
        'x = getattr(settings, "MIRROR_REGIME_START", "2026-01-01")\n',
    )
    out = list(audit.extract_getattr_settings_calls(f))
    assert out == [("MIRROR_REGIME_START", "'2026-01-01'", 1)]


def test_getattr_settings_bool_default(audit, tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "s4.py",
        'x = getattr(settings, "ASOS_1MIN_ENABLED", False)\n',
    )
    out = list(audit.extract_getattr_settings_calls(f))
    assert out == [("ASOS_1MIN_ENABLED", "False", 1)]


def test_getattr_non_settings_skipped(audit, tmp_path: Path) -> None:
    """getattr(self, "x") is a class-attr lookup, not an env var — must skip."""
    src = (
        'a = getattr(self, "_cached_value", None)\n'
        'b = getattr(other_obj, "FOO", 1)\n'
    )
    f = _write(tmp_path, "s5.py", src)
    out = list(audit.extract_getattr_settings_calls(f))
    assert out == []


def test_getattr_settings_dynamic_key_skipped(audit, tmp_path: Path) -> None:
    src = (
        'name = "WEATHER_MIN_EDGE"\n'
        "x = getattr(settings, name, 0.08)\n"
        'y = getattr(settings, f"PREFIX_{name}", 0.0)\n'
    )
    f = _write(tmp_path, "s6.py", src)
    out = list(audit.extract_getattr_settings_calls(f))
    assert out == []


def test_getattr_settings_yields_lineno(audit, tmp_path: Path) -> None:
    src = (
        '# header\n'
        'import x\n'
        '\n'
        'a = getattr(settings, "ALPHA", 1)\n'
        'b = getattr(settings, "BETA", 2)\n'
    )
    f = _write(tmp_path, "s7.py", src)
    out = list(audit.extract_getattr_settings_calls(f))
    assert out == [("ALPHA", "1", 4), ("BETA", "2", 5)]


# ───── extract_settings_attribute_calls — S211 Lead 7 (settings.KEY direct) ──

def test_settings_attribute_load_match(audit, tmp_path: Path) -> None:
    f = _write(tmp_path, "a1.py", "x = settings.MY_KEY\n")
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == [("MY_KEY", None, 1)]


def test_settings_attribute_assignment_target_skipped(audit, tmp_path: Path) -> None:
    """`settings.KEY = value` is a Store ctx — must not be flagged as a read."""
    f = _write(tmp_path, "a2.py", 'settings.MY_KEY = "x"\n')
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == []


def test_settings_attribute_del_skipped(audit, tmp_path: Path) -> None:
    f = _write(tmp_path, "a3.py", "del settings.MY_KEY\n")
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == []


def test_settings_attribute_nested_chain_skipped(audit, tmp_path: Path) -> None:
    """`obj.settings.KEY` — outer Attribute's value is an Attribute, not a
    bare Name with id='settings'. Must not match (it's a different object)."""
    f = _write(tmp_path, "a4.py", "x = obj.settings.MY_KEY\n")
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == []


def test_settings_attribute_mock_settings_skipped(audit, tmp_path: Path) -> None:
    """`mock_settings.KEY` is a Name with id='mock_settings', not 'settings'."""
    f = _write(tmp_path, "a5.py", 'mock_settings.MY_KEY = 5\nx = mock_settings.OTHER_KEY\n')
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == []


def test_settings_attribute_lowercase_skipped(audit, tmp_path: Path) -> None:
    """Pydantic builtins (settings.dict, .json, .copy) and lowercase fields
    don't match the env-var UPPER_SNAKE_CASE convention — must skip."""
    src = (
        "a = settings.dict\n"
        "b = settings.json\n"
        "c = settings.copy\n"
        "d = settings.lowercase_field\n"
    )
    f = _write(tmp_path, "a6.py", src)
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == []


def test_settings_attribute_dunder_skipped(audit, tmp_path: Path) -> None:
    """Dunder attrs start with `_` — first-char isupper() is False, skipped."""
    f = _write(tmp_path, "a7.py", "x = settings.__class__\n")
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == []


def test_settings_attribute_private_skipped(audit, tmp_path: Path) -> None:
    """Private attrs starting with `_` — also filtered by uppercase check."""
    f = _write(tmp_path, "a8.py", "x = settings._cached\n")
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == []


def test_settings_attribute_multiple_in_expression(audit, tmp_path: Path) -> None:
    """Multiple `settings.X` references in one expression — all yielded."""
    f = _write(tmp_path, "a9.py", "x = settings.ALPHA + settings.BETA\n")
    out = sorted(audit.extract_settings_attribute_calls(f))
    assert out == [("ALPHA", None, 1), ("BETA", None, 1)]


def test_settings_attribute_lineno(audit, tmp_path: Path) -> None:
    src = (
        "# header\n"
        "import x\n"
        "\n"
        "a = settings.ALPHA\n"
        "b = settings.BETA\n"
    )
    f = _write(tmp_path, "a10.py", src)
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == [("ALPHA", None, 4), ("BETA", None, 5)]


def test_settings_attribute_call_form_matches(audit, tmp_path: Path) -> None:
    """`settings.METHOD()` — the Attribute is in Load ctx as the call's func.
    It still references an attr name; if uppercase, we treat it as a key."""
    f = _write(tmp_path, "a11.py", "x = settings.HEALTH_PORT\n")
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == [("HEALTH_PORT", None, 1)]


def test_settings_attribute_chained_access(audit, tmp_path: Path) -> None:
    """`settings.KEY.foo` — inner is settings.KEY (matches), outer is
    KEY.foo (settings is the value of the inner; outer's value is an
    Attribute, so outer doesn't match). Inner yields once."""
    f = _write(tmp_path, "a12.py", "x = settings.MY_KEY.upper()\n")
    out = list(audit.extract_settings_attribute_calls(f))
    assert out == [("MY_KEY", None, 1)]


# ───── integration: combined collect_code_defaults sees all shapes ──────────

def test_collect_code_defaults_includes_both_patterns(
    audit, tmp_path: Path, monkeypatch
) -> None:
    """Both extractors should feed the same `code` dict, so a key referenced
    only via getattr(settings, ...) is no longer an ENV-ORPHAN candidate."""
    src = (
        'import os\n'
        'x = os.getenv("FROM_GETENV", "1")\n'
        'y = getattr(settings, "FROM_GETATTR", "2")\n'
    )
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write(pkg, "mod.py", src)

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    refs = audit.collect_code_defaults()

    assert "FROM_GETENV" in refs
    assert "FROM_GETATTR" in refs
    # Both must be classified as "has default" (i.e. not None) so the
    # categorize() step doesn't bucket them as no-default keys.
    assert refs["FROM_GETENV"][0][0] == "'1'"
    assert refs["FROM_GETATTR"][0][0] == "'2'"


def test_collect_code_defaults_includes_settings_attribute(
    audit, tmp_path: Path, monkeypatch
) -> None:
    """S211 Lead 7: keys referenced only via settings.KEY direct attribute
    reads should appear in `code`, joining the existing two extractors."""
    src = (
        "x = settings.FROM_DIRECT_ATTR\n"
        'y = getattr(settings, "FROM_GETATTR", "2")\n'
    )
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write(pkg, "mod.py", src)

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    refs = audit.collect_code_defaults()

    assert "FROM_DIRECT_ATTR" in refs
    assert "FROM_GETATTR" in refs
    # Direct-attr access has no static default — recorded as None
    assert refs["FROM_DIRECT_ATTR"][0][0] is None


def test_settings_attribute_prevents_env_orphan_verdict(
    audit, tmp_path: Path, monkeypatch
) -> None:
    """S211 Lead 7 verdict-level (Protocol 15): a key referenced only via
    settings.KEY and present in .env is NOT flagged as ENV-ORPHAN.

    This is the user-facing behavior change — the bucket-level test that
    pins the audit's verdict for the pattern Lead 7 targeted."""
    src = "x = settings.POLYMARKET_GAMMA_API\n"
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write(pkg, "mod.py", src)

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    refs = audit.collect_code_defaults()

    # Simulate .env that sets this key
    env = {"POLYMARKET_GAMMA_API": ("https://example.com", 1)}
    buckets = audit.categorize(refs, env)

    orphan_keys = {entry[0] for entry in buckets["env_orphan"]}
    assert "POLYMARKET_GAMMA_API" not in orphan_keys, \
        f"Key flagged as ENV-ORPHAN despite settings.KEY reference: {orphan_keys}"


# ───── SKIP_DIRS / SKIP_DIRS_AT_ROOT — S212 over-broad-skip fix ──────────────

def test_nested_data_dir_not_skipped(audit, tmp_path: Path, monkeypatch) -> None:
    """S212: `base_engine/data/foo.py` (and similar nested source dirs whose
    basename collides with the top-level data/ convention) must be walked,
    not silently skipped. This is the regression fix for ~37 source files
    that the pre-S212 basename-anywhere rule was masking."""
    src = 'import os\nx = os.getenv("FROM_NESTED_DATA", "1")\n'
    pkg = tmp_path / "base_engine" / "data"
    pkg.mkdir(parents=True)
    _write(pkg, "foo.py", src)

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    refs = audit.collect_code_defaults()
    assert "FROM_NESTED_DATA" in refs


def test_nested_output_dir_not_skipped(audit, tmp_path: Path, monkeypatch) -> None:
    """S212: nested 'output' directories likewise must be walked."""
    src = 'import os\nx = os.getenv("FROM_NESTED_OUTPUT", "1")\n'
    pkg = tmp_path / "pkg" / "output"
    pkg.mkdir(parents=True)
    _write(pkg, "foo.py", src)

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    refs = audit.collect_code_defaults()
    assert "FROM_NESTED_OUTPUT" in refs


def test_root_data_dir_still_skipped(audit, tmp_path: Path, monkeypatch) -> None:
    """S212: top-level data/ at REPO_ROOT remains skipped (its only purpose
    is data files; any .py inside should not contribute to the audit)."""
    src = 'import os\nx = os.getenv("FROM_ROOT_DATA", "1")\n'
    pkg = tmp_path / "data"
    pkg.mkdir()
    _write(pkg, "foo.py", src)

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    refs = audit.collect_code_defaults()
    assert "FROM_ROOT_DATA" not in refs


def test_root_output_dir_still_skipped(audit, tmp_path: Path, monkeypatch) -> None:
    """S212: top-level output/ at REPO_ROOT remains skipped (scratch dir)."""
    src = 'import os\nx = os.getenv("FROM_ROOT_OUTPUT", "1")\n'
    pkg = tmp_path / "output"
    pkg.mkdir()
    _write(pkg, "foo.py", src)

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    refs = audit.collect_code_defaults()
    assert "FROM_ROOT_OUTPUT" not in refs


def test_pycache_skipped_anywhere(audit, tmp_path: Path, monkeypatch) -> None:
    """SKIP_DIRS (skip-anywhere) preserved: __pycache__ at any depth skipped."""
    src = 'import os\nx = os.getenv("FROM_PYCACHE", "1")\n'
    deep = tmp_path / "pkg" / "sub" / "__pycache__"
    deep.mkdir(parents=True)
    _write(deep, "foo.py", src)

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    refs = audit.collect_code_defaults()
    assert "FROM_PYCACHE" not in refs


def test_venv_skipped_anywhere(audit, tmp_path: Path, monkeypatch) -> None:
    """SKIP_DIRS (skip-anywhere) preserved: venv at any depth skipped."""
    src = 'import os\nx = os.getenv("FROM_VENV", "1")\n'
    deep = tmp_path / "pkg" / "venv" / "lib"
    deep.mkdir(parents=True)
    _write(deep, "foo.py", src)

    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    refs = audit.collect_code_defaults()
    assert "FROM_VENV" not in refs

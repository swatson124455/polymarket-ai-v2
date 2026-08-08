"""FAILING-BEFORE pins for the w16/w17 config-source defect (found live 2026-08-08).

MECHANISM (class, not instance): an offline report tool re-read the root-owned 0600 `live.env`
directly, even though its systemd unit already carries
`EnvironmentFile=/opt/pa2-maker-kalshi-live/live.env` and systemd (root) had injected KALSHI_*
into the process. The unit runs as `polymarket`, which cannot open that file, so EVERY timer run
died with PermissionError before emitting a line — w17 from 2026-08-06T14:00:01Z, w16 from at
least 2026-08-07T14:00Z (journal + w16_report.log/w17_report.log, read 2026-08-08T22:2xZ).

Before the fix, test_env_prefers_environ FAILS on both modules: `_env` did not exist on w16 at
all, and w17's `_env` ignored os.environ and went straight to the unreadable file.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODS = ["w16_successor_finder", "w17_coverage_ledger"]


@pytest.mark.parametrize("modname", MODS)
def test_env_prefers_environ_over_unreadable_file(modname, monkeypatch, tmp_path, capsys):
    """systemd already injected the vars -> the tool must use them and never touch the file."""
    mod = importlib.import_module(modname)
    monkeypatch.setenv("KALSHI_SERIES_ALLOW", "KXAAAGASD,KXTEMPAUSH")
    monkeypatch.setenv("KALSHI_SERIES_DENY", "KXDXY")

    # Point the fallback at a path that cannot be opened; if the tool reaches for it, it raises.
    missing = str(tmp_path / "definitely-not-here.env")
    if hasattr(mod, "DATA"):
        monkeypatch.setattr(mod, "DATA", str(tmp_path))

    def _boom(*a, **k):  # any file access at all is a regression
        raise AssertionError("_env() touched the filesystem despite KALSHI_* being in environ")

    monkeypatch.setattr(mod, "open", _boom, raising=False)

    env = mod._env()
    assert env["KALSHI_SERIES_ALLOW"] == "KXAAAGASD,KXTEMPAUSH"
    assert env["KALSHI_SERIES_DENY"] == "KXDXY"
    assert missing  # path unused; kept to document intent
    out = capsys.readouterr().out
    assert "config source: environ" in out


@pytest.mark.parametrize("modname", MODS)
def test_env_alarms_loudly_when_config_is_unreachable(modname, monkeypatch, tmp_path, capsys):
    """STANDING DETECTOR: empty config must announce itself, never look like a clean run."""
    mod = importlib.import_module(modname)
    for k in list(os.environ):
        if k.startswith("KALSHI_"):
            monkeypatch.delenv(k, raising=False)
    if hasattr(mod, "DATA"):
        monkeypatch.setattr(mod, "DATA", str(tmp_path))  # no live.env inside tmp_path

    env = mod._env()
    out = capsys.readouterr().out
    assert env == {} or "KALSHI_SERIES_ALLOW" not in env
    assert "ALARM config unreadable" in out, "silent empty config is the defect being pinned"

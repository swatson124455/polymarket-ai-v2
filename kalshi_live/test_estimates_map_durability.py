"""FAILING-BEFORE pins for M-16 / m-3 — program-map durability (kalshi_estimates_recorder).

MECHANISM: the map is the MERGE-ONLY cache of program_id -> {market_ticker, end_date,
period_reward}. Ended programs leave /incentive_programs?status=active but their estimate rows
persist until payout, so the map is the ONLY way to resolve them — it is what joins an estimate
to the credit that eventually pays it (the 2026-08-09T03:59:59Z FIX-H endings depend on it).

Two defects, one loss:
  1. `json.dump(pmap, open(MAP_PATH, "w"))` truncated the live file before writing a byte, so a
     crash / OOM-kill / reboot mid-dump left invalid JSON on disk.
  2. `except Exception: pmap = {}` then treated that torn file exactly like a MISSING one and
     silently restarted the cache empty — and because the ended programs are gone from the
     active feed, the re-merge could never restore them.

Before the fix both `load_map` and `save_map` do not exist; the tests below fail.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
rec = pytest.importorskip("kalshi_estimates_recorder")


def test_corrupt_map_is_preserved_and_alarms_not_silently_emptied(tmp_path, capsys):
    p = tmp_path / "kalshi_program_map.json"
    p.write_text('{"123": {"market_ticker": "KXTOPMODEL-26AUG31-CLAUM"')  # torn write
    pmap, status = rec.load_map(str(p))
    out = capsys.readouterr().out
    assert status == "corrupt"
    assert "ALARM" in out and "LOST" in out, "a torn map must never fail silently"
    preserved = list(tmp_path.glob("kalshi_program_map.json.corrupt-*"))
    assert preserved, "the damaged file must be preserved for recovery, not overwritten"


def test_absent_map_is_not_an_alarm(tmp_path, capsys):
    pmap, status = rec.load_map(str(tmp_path / "nope.json"))
    assert (pmap, status) == ({}, "absent")
    assert "ALARM" not in capsys.readouterr().out


def test_save_is_atomic_no_truncated_file_on_disk(tmp_path):
    p = tmp_path / "m.json"
    assert rec.save_map({"1": {"market_ticker": "A"}}, 0, str(p)) is True
    assert rec.save_map({"1": {"market_ticker": "A"}, "2": {"market_ticker": "B"}}, 1, str(p))
    assert json.loads(p.read_text()).keys() == {"1", "2"}
    assert not list(tmp_path.glob("*.tmp")), "temp file must be renamed, never left behind"


def test_save_refuses_to_shrink_the_merge_only_map(tmp_path, capsys):
    """The invariant that protects ended-program history: the map may only grow."""
    p = tmp_path / "m.json"
    rec.save_map({"1": {"market_ticker": "A"}, "2": {"market_ticker": "B"}}, 0, str(p))
    ok = rec.save_map({"1": {"market_ticker": "A"}}, 2, str(p))   # active feed only
    assert ok is False
    assert "refusing to SHRINK" in capsys.readouterr().out
    assert json.loads(p.read_text()).keys() == {"1", "2"}, "on-disk history must survive"

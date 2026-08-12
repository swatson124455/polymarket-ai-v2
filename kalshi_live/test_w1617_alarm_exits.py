"""§6 defect-set pins (handoff §6.1/.3/.4/.5, operator "proceed" 2026-08-12).

The class: offline standing detectors that ALARM in prose but exit 0 — machine-invisible
(pre-fix an unreadable config exited 1 via crash -> systemd `failed`; the 08-08 fix made
them exit 0 with a `#` line nothing parses). And silent-empty file reads that shrink the
very universe the detector exists to cover. And the credit-feedback builder's regression
alarm reading its baseline from --out, so building to /tmp bypassed it (live 08-09).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import w16_successor_finder as w16
import w17_coverage_ledger as w17
import kalshi_credit_feedback as cfb


# ---- §6.1: alarms must be machine-visible (nonzero exit) ----

@pytest.mark.parametrize("mod", [w16, w17])
def test_alarm_helper_counts_and_prints(mod, capsys):
    mod._ALARMS[0] = 0
    mod._alarm("test alarm line")
    assert mod._ALARMS[0] == 1
    assert "# ALARM test alarm line" in capsys.readouterr().out


@pytest.mark.parametrize("mod", [w16, w17])
def test_exit_code_nonzero_after_alarm(mod):
    mod._ALARMS[0] = 0
    assert mod._exit_code() == 0
    mod._alarm("x")
    assert mod._exit_code() == 1


@pytest.mark.parametrize("mod", [w16, w17])
def test_unreachable_config_is_an_alarm(mod, monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("KALSHI_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(mod, "DATA", str(tmp_path))     # no live.env inside
    mod._ALARMS[0] = 0
    mod._env()
    assert mod._ALARMS[0] >= 1, "unreadable config must be a counted alarm, not prose"


# ---- §6.4: empty allowlist from a PARTIAL environ must alarm before any network ----

def test_w16_empty_allowlist_exits_nonzero_before_network(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("KALSHI_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KALSHI_TRADING_MODE", "live")   # partial environ: KALSHI_* present,
    monkeypatch.setattr(w16, "DATA", str(tmp_path))     # allowlist missing
    monkeypatch.setattr(sys, "argv", ["w16"])

    def _no_net(*a, **k):
        raise AssertionError("network touched despite empty allowlist")
    monkeypatch.setattr(w16, "_get", _no_net)
    w16._ALARMS[0] = 0
    with pytest.raises(SystemExit) as e:
        w16.main()
    assert e.value.code == 1


# ---- §6.3: silent-empty side reads become counted alarms ----

@pytest.mark.parametrize("mod", [w16, w17])
def test_read_json_alarms_on_missing_and_corrupt(mod, tmp_path, capsys):
    mod._ALARMS[0] = 0
    assert mod._read_json(str(tmp_path / "absent.json"), "testlabel") is None
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not json")
    assert mod._read_json(str(bad), "testlabel") is None
    assert mod._ALARMS[0] == 2
    assert "testlabel" in capsys.readouterr().out


def test_read_json_good_file_no_alarm(tmp_path):
    w17._ALARMS[0] = 0
    p = tmp_path / "ok.json"
    p.write_text(json.dumps({"series": {"KXA": {"credits_n": 2}}}))
    assert w17._read_json(str(p), "x")["series"]["KXA"]["credits_n"] == 2
    assert w17._ALARMS[0] == 0


# ---- §6.5: builder baseline provenance — the /tmp bypass is dead ----

def test_baseline_prefers_canonical_over_out(tmp_path):
    """THE 08-09 BYPASS PIN: building to a scratch path must still diff against the
    CANONICAL deployed table, so a paid->convicted regression cannot slip through."""
    canon = tmp_path / "kalshi_credit_feedback.json"
    canon.write_text(json.dumps({"series": {"KXPAID": {"verdict": "paid"}}}))
    out = tmp_path / "scratch" / "rebuild.json"          # --out elsewhere, file absent
    prev, prov, alarms = cfb._load_baseline(str(out), None, canonical=str(canon))
    assert prev["KXPAID"]["verdict"] == "paid"
    assert "canonical" in prov
    assert alarms == []


def test_baseline_corrupt_canonical_alarms(tmp_path):
    canon = tmp_path / "kalshi_credit_feedback.json"
    canon.write_text("{torn")
    prev, prov, alarms = cfb._load_baseline(str(tmp_path / "out.json"), None,
                                            canonical=str(canon))
    assert prev == {} and alarms, "unreadable baseline must alarm, never silently pass {}"


def test_baseline_first_build_no_alarm(tmp_path):
    prev, prov, alarms = cfb._load_baseline(str(tmp_path / "out.json"), None,
                                            canonical=str(tmp_path / "nope.json"))
    assert prev == {} and alarms == [] and "first build" in prov

"""CONFIG VISIBILITY (2026-07-26).

A KALSHI_* knob absent from live.env silently takes its code default. Nothing logged it,
so KALSHI_THROTTLE_SMART sat OFF in production and KALSHI_PRECLOSE_FLATTEN -- the
purpose-built defence against riding naked inventory into settlement -- was built,
tested, documented and never switched on. Audited 2026-07-26: 67 knobs read, 34 set.

These pin (a) that _envb preserves the EXACT semantics of the inline expressions it
replaced -- getting that wrong would silently flip TAKER_FLATTEN or REDUCE_ONLY_KEEP_BOTH
on a live money path -- and (b) that absence is reported.
"""
import os

from test_live_hardening import MockClient, _run, q


# ---- _envb semantics: byte-identical to the expressions it replaced -------------------

def test_envb_default_off_absent_is_false(monkeypatch):
    # replaced: os.environ.get(NAME) == "1"   -> absent -> None == "1" -> False
    monkeypatch.delenv("KALSHI_UNIT_TEST_FLAG", raising=False)
    assert q._envb("KALSHI_UNIT_TEST_FLAG") is False


def test_envb_default_on_absent_is_true(monkeypatch):
    # replaced: os.environ.get(NAME, "1") == "1" -> absent -> "1" == "1" -> True
    monkeypatch.delenv("KALSHI_UNIT_TEST_FLAG", raising=False)
    assert q._envb("KALSHI_UNIT_TEST_FLAG", True) is True


def test_envb_explicit_zero_turns_off_even_when_default_on(monkeypatch):
    # This is how TAKER_FLATTEN=0 is expressed in live.env. It MUST win over default_on.
    monkeypatch.setenv("KALSHI_UNIT_TEST_FLAG", "0")
    assert q._envb("KALSHI_UNIT_TEST_FLAG", True) is False


def test_envb_explicit_one_turns_on(monkeypatch):
    monkeypatch.setenv("KALSHI_UNIT_TEST_FLAG", "1")
    assert q._envb("KALSHI_UNIT_TEST_FLAG") is True


def test_envb_non_one_value_is_off(monkeypatch):
    # "true"/"yes" are NOT accepted by the original expression; preserve that exactly.
    monkeypatch.setenv("KALSHI_UNIT_TEST_FLAG", "true")
    assert q._envb("KALSHI_UNIT_TEST_FLAG", True) is False


# ---- registration + absence reporting ------------------------------------------------

def test_the_real_flags_are_registered():
    # Flags routed through _envb must appear in the declared set, otherwise they drop out of
    # the absence report and become invisible again. (KALSHI_REDUCE_ONLY_KEEP_BOTH left this
    # list 2026-07-28 — operator Q1 decision deleted the knob; pinned as REMOVED below.)
    for k in ("KALSHI_THROTTLE_SMART", "KALSHI_TAKER_FLATTEN", "KALSHI_JOIN_ALWAYS"):
        assert k in q._ENV_DECLARED, f"{k} not registered -> its absence would be silent"


def test_the_q1_deleted_knobs_stay_deleted():
    # Operator Q1 decision 2026-07-28: the money-losing legacy behaviour must not be one env
    # var away. If any of these re-registers, someone has resurrected a deleted switch.
    for k in ("KALSHI_EXIT_AT_TOUCH", "KALSHI_HOLDING_EXIT_ONLY",
              "KALSHI_MAX_UNWIND_LOSS", "KALSHI_REDUCE_ONLY_KEEP_BOTH"):
        assert k not in q._ENV_DECLARED, f"{k} was deleted by operator decision and is BACK"


def test_envi_and_envf_register_too():
    assert "KALSHI_FOOTPRINT_TOP" in q._ENV_DECLARED
    assert "KALSHI_PRECLOSE_FLATTEN" in q._ENV_DECLARED
    assert len(q._ENV_DECLARED) > 50, "expected the full knob surface to register"


def test_env_absent_reports_unset_knob(monkeypatch):
    monkeypatch.delenv("KALSHI_PRECLOSE_FLATTEN", raising=False)
    assert "KALSHI_PRECLOSE_FLATTEN" in q.env_absent()


def test_env_absent_omits_a_set_knob(monkeypatch):
    monkeypatch.setenv("KALSHI_PRECLOSE_FLATTEN", "1")
    assert "KALSHI_PRECLOSE_FLATTEN" not in q.env_absent()


# ---- it reaches the plan row and the log ---------------------------------------------

def _cycle(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else
                        {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]],
                                          "no_dollars": [["0.49", "9999"]]}})
    c = MockClient(mode="live", resting=[], positions=[])
    return _run(monkeypatch, c, str(tmp_path))


def test_plan_row_carries_absent_knobs(monkeypatch, tmp_path):
    monkeypatch.delenv("KALSHI_PRECLOSE_FLATTEN", raising=False)
    row = _cycle(monkeypatch, tmp_path)
    assert row.get("env_absent_n", 0) > 0
    assert "KALSHI_PRECLOSE_FLATTEN" in row.get("env_absent", [])


def test_unset_protection_knob_is_named_in_the_log(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("KALSHI_PRECLOSE_FLATTEN", raising=False)
    row = _cycle(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert "PROTECTION knob" in out
    assert "KALSHI_PRECLOSE_FLATTEN" in out, "a bare count is as ignorable as silence"
    assert "KALSHI_PRECLOSE_FLATTEN" in row.get("env_absent_safety", [])


def test_no_warning_when_every_protection_knob_is_set(monkeypatch, tmp_path, capsys):
    for k in q._SAFETY_KNOBS:
        monkeypatch.setenv(k, "1")
    _cycle(monkeypatch, tmp_path)
    assert "PROTECTION knob" not in capsys.readouterr().out

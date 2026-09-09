#!/usr/bin/env python3
"""[chain] watchdog — GO-precondition #2 (operator "build all 5", 2026-09-06).

The grader crashed silently on 7/7 daily runs 2026-08-26..09-01 because
nothing alarmed on a mid-chain traceback. The [grader] heartbeat closed
that one stage; THIS closes the class: it parses TODAY's section of the
11:40Z cron log and prints one [chain] line grading EVERY stage:

    OK      — stage header present today, no Traceback in its section
    CRASHED — header present but its section contains a Traceback
    MISSING — expected header absent from today's log entirely

Any non-OK stage makes the line start with "[chain] !!" — loud by
position, and MISSING is distinct from CRASHED (a stage deleted from the
cron would otherwise vanish silently — the empty-set false-pass).
Runs LAST in the cron; grades everything before it. Read-only.

## AMENDMENT 2026-09-08 — THE WATCHDOG MUST NOT BE NARROWABLE BY THE THING
## IT WATCHES (operator "fix watchdog move if needed")

Measured failure: this file lives INSIDE /opt/pa2-shared/mb_readout, a git
clone whose 12:30Z refresh `reset --hard`s to a pinned branch. When that
reverted the clone to a pre-crawl-stage commit, EXPECTED silently lost its
`crawl` entry — so the 2026-09-08 11:40Z run printed "all stages ran clean"
while a stage had not run at all. A watchdog whose definition of "everything"
can be silently narrowed reports clean at exactly the moment it matters.

Two defences, both fail-LOUD and both checked in the self-test:
 1. STAGE LIST OUTSIDE THE CLONE. `--stages` (default
    /opt/pa2-shared/mb_chain_stages.json) is AUTHORITATIVE when readable; a
    clone reset cannot touch it. Any disagreement with the built-in list is
    reported as `stages=DRIFT(...)` — in BOTH directions, because the file
    being stale is as interesting as the code being reverted. If the file is
    absent/unparseable we fall back to built-in and say so (`stages=BUILTIN`);
    we never silently narrow.
 2. CODE-DRIFT. `--clone` (default the readout clone) is compared HEAD vs its
    tracked remote ref; a mismatch prints `code=DRIFT(head!=remote)`. That
    catches "this watchdog is itself running stale code", which is the root
    condition that produced defence 1's failure.
Both are ALARM conditions: either makes the line start with "[chain] !!".
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

# Stage markers as they appear in label_and_fee_refresh_cron.sh output.
# ADD new stages here when the cron gains them - the self-test pins the
# count so a cron/watchlist drift shows up in review. NOTE (2026-09-08): this
# built-in list is now the FALLBACK; the authoritative copy lives outside the
# clone (see the amendment above). Update BOTH, or the drift check will say so.
EXPECTED = [
    ("labels", "label supplement"),
    ("fees", "fee map"),
    ("grader", "cohort5 qualification"),
    ("band", "band 0.65-0.85 forward test"),
    ("scoreboard", "MB SCOREBOARD"),
    ("canon", "canon verification"),
    ("funnel", "trader funnel"),
    ("hypo", "hypothetical dollar ledger"),
    ("backtest", "backtest daily leaderboard"),
    ("crawl", "gamma window crawl"),
    ("tailable", "tailable list"),    # stage 11, 2026-09-08 (plan P1)
    ("pipeline", "candidate pipeline"),  # stage 12, 2026-09-08 (plan P4)
]


def parse_stages(text: str) -> list[tuple[str, str]]:
    """External stage-list JSON -> [(key, marker)]. Accepts either a bare
    list of [key, marker] pairs or {"stages": [{"key":..,"marker":..}]}.
    Raises on anything else — a malformed authority file must never be
    silently reinterpreted as a shorter list. Pure."""
    blob = json.loads(text)
    rows = blob.get("stages") if isinstance(blob, dict) else blob
    if not isinstance(rows, list) or not rows:
        raise ValueError("stage file holds no stages")
    out = []
    for r in rows:
        if isinstance(r, dict):
            key, marker = r.get("key"), r.get("marker")
        elif isinstance(r, (list, tuple)) and len(r) == 2:
            key, marker = r
        else:
            raise ValueError(f"unparsable stage entry: {r!r}")
        if not (isinstance(key, str) and isinstance(marker, str)
                and key and marker):
            raise ValueError(f"empty key/marker in: {r!r}")
        out.append((key, marker))
    return out


def stage_source(external: list | None, builtin: list) -> tuple[list, str]:
    """(stages to grade, note). The EXTERNAL list wins when present — it
    lives outside the clone, so a `reset --hard` cannot narrow it. Any
    disagreement is reported in BOTH directions: code reverted (external has
    stages the built-in lacks) or file stale (built-in has extras). Pure."""
    if external is None:
        return builtin, "stages=BUILTIN(external unreadable)"
    ext_keys = [k for k, _ in external]
    own_keys = [k for k, _ in builtin]
    only_ext = [k for k in ext_keys if k not in own_keys]
    only_own = [k for k in own_keys if k not in ext_keys]
    if only_ext or only_own:
        bits = []
        if only_ext:
            bits.append("code-missing:" + ",".join(only_ext))
        if only_own:
            bits.append("file-missing:" + ",".join(only_own))
        return external, "stages=DRIFT(" + "; ".join(bits) + ")"
    return external, ""


def code_drift(head: str | None, remote: str | None) -> str:
    """Note for the clone's HEAD vs its tracked remote ref. Empty when they
    agree. Unknown refs are reported, never assumed equal. Pure."""
    if not head or not remote:
        return "code=UNKNOWN(no git)"
    if head.strip() != remote.strip():
        return f"code=DRIFT(head {head.strip()[:7]}!=remote {remote.strip()[:7]})"
    return ""


def grade_chain(log_text: str, today: str,
                stages: list | None = None) -> tuple[str, int]:
    """(printable [chain] line, count of non-OK stages). Pure.
    `stages` defaults to the built-in EXPECTED (signature preserved for the
    existing callers); main() passes the authoritative external list."""
    stages = EXPECTED if stages is None else stages
    lines = log_text.splitlines()
    # index of each header line for TODAY (headers carry the UTC date)
    starts = {}
    for i, ln in enumerate(lines):
        if ln.startswith("=====") and today in ln:
            for key, marker in stages:
                if marker in ln and key not in starts:
                    starts[key] = i
    results = []
    bad = 0
    for key, _marker in stages:
        if key not in starts:
            results.append(f"{key}=MISSING")
            bad += 1
            continue
        i = starts[key]
        j = len(lines)
        for k in range(i + 1, len(lines)):
            if lines[k].startswith("====="):
                j = k
                break
        section = "\n".join(lines[i + 1:j])
        # a stage that exits early with a FATAL line (SystemExit text, no
        # traceback) is just as dead as a traceback (re-review C3)
        if ("Traceback (most recent call last)" in section
                or re.search(r"(^|\W)FATAL\b", section)):
            results.append(f"{key}=CRASHED")
            bad += 1
        else:
            results.append(f"{key}=OK")
    prefix = "[chain] !!" if bad else "[chain]"
    line = (f"{prefix} {today} {' '.join(results)}"
            + (f" - {bad} stage(s) NOT OK - read the cron log sections above"
               if bad else " - all stages ran clean"))
    return line, bad


def _git(clone: str, *args: str) -> str | None:
    """git output or None. Never raises — a missing git must not stop the
    chain grade, only downgrade it to code=UNKNOWN."""
    try:
        r = subprocess.run(("git", "-C", clone) + args, capture_output=True,
                           text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def read_code_note(clone: str) -> str:
    """code-drift note for the clone: HEAD vs what the last fetch delivered.

    Ref resolution order matters here (measured 2026-09-08): the refresh cron
    runs `git fetch --depth 1 origin <branch>` with NO refspec, which does NOT
    create refs/remotes/origin/<branch> — so `origin/master` is unresolvable in
    this clone and a naive lookup returns UNKNOWN forever. FETCH_HEAD is always
    written by that fetch, and HEAD != FETCH_HEAD is exactly the condition we
    care about: the clone did not end up on what it last fetched.
    """
    head = _git(clone, "rev-parse", "HEAD")
    branch = _git(clone, "rev-parse", "--abbrev-ref", "HEAD")
    remote = _git(clone, "rev-parse", f"origin/{branch}") if branch else None
    if remote is None:
        remote = _git(clone, "rev-parse", "FETCH_HEAD")
    return code_drift(head, remote)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive/"
                            "label_fee_refresh.log")
    ap.add_argument("--date", default=None,
                    help="UTC date YYYY-MM-DD (default: today)")
    ap.add_argument("--stages",
                    default="/opt/pa2-shared/mb_chain_stages.json",
                    help="AUTHORITATIVE stage list, kept OUTSIDE the readout "
                         "clone so a `git reset --hard` cannot narrow this "
                         "watchdog (amendment 2026-09-08)")
    ap.add_argument("--clone", default="/opt/pa2-shared/mb_readout",
                    help="clone to check for code drift (HEAD vs remote)")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    today = a.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        text = open(a.log, errors="replace").read()
    except OSError as e:
        print(f"[chain] !! LOG UNREADABLE ({e!r}) - treat the whole chain "
              f"as unverified")
        return 1
    external = None
    try:
        external = parse_stages(open(a.stages).read())
    except (OSError, ValueError, json.JSONDecodeError):
        external = None          # reported as stages=BUILTIN, never silent
    stages, stage_note = stage_source(external, EXPECTED)
    code_note = read_code_note(a.clone)
    line, bad = grade_chain(text, today, stages)
    notes = [n for n in (stage_note, code_note) if n]
    if notes:
        # integrity notes are ALARMS in their own right: a narrowed watchdog
        # or stale code invalidates the grade above it
        if not line.startswith("[chain] !!"):
            line = line.replace("[chain]", "[chain] !!", 1)
        line += " | INTEGRITY: " + " ".join(notes)
        bad += len(notes)
    print(line)
    return 0 if bad == 0 else 1


def _mk_log(day: str, stages, crash_in=None) -> str:
    out = []
    for key, marker in stages:
        out.append(f"===== {day}T11:41:00Z {marker} =====")
        out.append("some ordinary output")
        if key == crash_in:
            out.append("Traceback (most recent call last):")
            out.append("  boom")
    return "\n".join(out)


def _self_test() -> int:
    print("SELF-TEST - mb_chain_watch (offline)\n")
    ok = True
    day = "2026-09-06"
    full = _mk_log(day, EXPECTED)
    line, bad = grade_chain(full, day)
    ok1 = bad == 0 and line.startswith("[chain] 2026") and "!!" not in line \
        and line.count("=OK") == len(EXPECTED)
    print(f"  [green] all stages present+clean -> no alarm, {len(EXPECTED)}"
          f" OKs : {ok1}")
    ok &= ok1
    line, bad = grade_chain(_mk_log(day, EXPECTED, crash_in="grader"), day)
    ok2 = bad == 1 and line.startswith("[chain] !!") and "grader=CRASHED" in line
    # FATAL early exit (no traceback) is CRASHED too (re-review C3)
    lf = _mk_log(day, EXPECTED).replace(
        "===== " + day + "T11:41:00Z tailable list =====\nsome ordinary output",
        "===== " + day + "T11:41:00Z tailable list =====\n[tailable] FATAL: both boards empty")
    line_f, bad_f = grade_chain(lf, day)
    ok2 = ok2 and bad_f == 1 and "tailable=CRASHED" in line_f
    print(f"  [crash] traceback inside one section -> that stage CRASHED,"
          f" loud : {ok2}")
    ok &= ok2
    line, bad = grade_chain(_mk_log(day, EXPECTED[:-1]), day)
    ok3 = bad == 1 and "pipeline=MISSING" in line \
        and line.startswith("[chain] !!")
    print(f"  [missing] absent stage reported MISSING, never silently OK :"
          f" {ok3}")
    ok &= ok3
    line, bad = grade_chain(_mk_log("2026-09-05", EXPECTED), day)
    ok4 = bad == len(EXPECTED) and line.startswith("[chain] !!")
    print(f"  [stale] yesterday's sections do NOT count for today : {ok4}")
    ok &= ok4
    ok5 = (len(EXPECTED) == 12
           and EXPECTED[-2:] == [("tailable", "tailable list"),
                                 ("pipeline", "candidate pipeline")])
    print(f"  [pin] watchlist covers the 12 cron stages (update BOTH on cron"
          f" change; backtest 2026-09-06, crawl 2026-09-07, tailable +"
          f" pipeline 2026-09-08) : {ok5}")
    ok &= ok5
    # ── amendment 2026-09-08: the watchdog must not be narrowable ──────────
    # [stages] external list parses in both accepted shapes; junk RAISES
    # rather than degrading to a shorter list
    ok6 = (parse_stages('[["a","A"],["b","B"]]') == [("a", "A"), ("b", "B")]
           and parse_stages('{"stages":[{"key":"a","marker":"A"}]}')
           == [("a", "A")])
    for junk in ('[]', '{"stages":[]}', '[["a"]]', '[{"key":"a"}]',
                 '[["a",""]]', '"nope"'):
        try:
            parse_stages(junk)
            ok6 = False
        except (ValueError, json.JSONDecodeError):
            pass
    print(f"  [stages] both shapes parse; empty/malformed RAISE : {ok6}")
    ok &= ok6
    # [stages] external is AUTHORITATIVE and a reverted code list is caught
    full = [("a", "A"), ("b", "B"), ("c", "C")]
    reverted = [("a", "A"), ("b", "B")]            # the crawl-stage failure
    used, note = stage_source(full, reverted)
    ok7 = used == full and "DRIFT" in note and "code-missing:c" in note
    used2, note2 = stage_source(reverted, full)    # stale FILE, live code
    ok7 = ok7 and used2 == reverted and "file-missing:c" in note2
    used3, note3 = stage_source(full, full)
    ok7 = ok7 and used3 == full and note3 == ""
    used4, note4 = stage_source(None, full)
    ok7 = ok7 and used4 == full and "BUILTIN" in note4
    print(f"  [stages] external authoritative; drift caught BOTH ways; "
          f"absent file disclosed : {ok7}")
    ok &= ok7
    # [stages] a narrowed list can no longer report clean on a missing stage
    day2 = "2026-09-08"
    log_missing_c = _mk_log(day2, reverted)        # only a,b ran
    line_n, bad_n = grade_chain(log_missing_c, day2, full)   # graded vs full
    ok8 = bad_n == 1 and "c=MISSING" in line_n and line_n.startswith("[chain] !!")
    line_o, bad_o = grade_chain(log_missing_c, day2, reverted)  # the old bug
    ok8 = ok8 and bad_o == 0      # proves the narrowed list WOULD say clean
    print(f"  [stages] grading vs the external list catches the stage a "
          f"narrowed list would miss : {ok8}")
    ok &= ok8
    # [code] drift note only when refs disagree; unknown never reads as equal
    ok9 = (code_drift("abc123456", "abc123456") == ""
           and "DRIFT" in code_drift("abc1234", "def5678")
           and "UNKNOWN" in code_drift(None, "abc")
           and "UNKNOWN" in code_drift("abc", None))
    print(f"  [code] HEAD!=remote alarms; missing git = UNKNOWN not OK : {ok9}")
    ok &= ok9
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

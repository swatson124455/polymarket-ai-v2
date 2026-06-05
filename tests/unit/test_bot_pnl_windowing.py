"""Static regression guard: every windowed `<col> > NOW() - INTERVAL ...` or
`<col> >= NOW() - INTERVAL ...` clause (where col is event_time or resolved_at)
must be paired with an upper-bound `<= NOW()` clause within 5 lines, using the
same alias.

Without the upper bound, forward-dated rows (the temporal-corruption bug class)
inflate windowed results. This test fails if any caller forgets the upper
bound, including new code added in the future.

Scope: scripts/, bots/, base_engine/, ui/ — every directory where code queries
resolution-observation timestamps. Tests/ excluded.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCAN_DIRS = ["scripts", "bots", "base_engine", "ui"]

# Files explicitly known to NOT contain the windowing pattern (skip optimization).
# Empty for now — every file with the pattern should be covered.
KNOWN_CLEAN: set[str] = set()


def _find_unguarded_windowing(content: str) -> list[tuple[int, str]]:
    """Scan file content for `<col> > NOW() - ...` patterns that lack a
    matching `<col> <= NOW()` upper bound within 5 lines, in the same SQL block.

    Alias semantics: when the lower bound uses an alias (e.g. `te.event_time`),
    the upper bound must use the SAME alias or no alias. A different alias
    means a different table reference and does NOT count as a match.

    Returns a list of (line_number, line_text) for each unguarded match.
    """
    lines = content.splitlines()
    unguarded: list[tuple[int, str]] = []

    lower_bound_re = re.compile(
        r"(\w+\.)?(event_time|resolved_at)\s*>=?\s*NOW\(\)\s*-"
    )

    for i, line in enumerate(lines):
        m = lower_bound_re.search(line)
        if not m:
            continue
        prefix = m.group(1) or ""
        col = m.group(2)

        # Build upper-bound regex matching either the same prefix or no prefix.
        # `(?<![\w.])` rejects any other alias (e.g. `xyz.event_time`).
        if prefix:
            upper_re = re.compile(
                rf"(?:{re.escape(prefix)}|(?<![\w.])){re.escape(col)}\s*<=\s*NOW\(\)"
            )
        else:
            upper_re = re.compile(
                rf"(?<![\w.]){re.escape(col)}\s*<=\s*NOW\(\)"
            )

        scan_window = lines[i : i + 6]
        if any(upper_re.search(ln) for ln in scan_window):
            continue

        unguarded.append((i + 1, line.strip()))

    return unguarded


def _all_scan_paths() -> list[Path]:
    """Return .py files under SCAN_DIRS that are tracked in git.

    Filtering to git-tracked files (instead of rglob over disk) prevents
    untracked scratch/WIP scripts from triggering this regression guard.
    Untracked files are by definition outside the safety-relevant codebase
    — they're not on master, not deployed, not in CI. Failing the guard on
    a developer's local scratch script blocks unrelated deploys.

    The eb/main splinter (commit 2eb264f, 2026-05-24) applied this same
    fix on the EB branch when five untracked S159/rc_*/esports_72h scratch
    scripts blocked the EB Phase 4 deploy. Per the WB/EB-never-merge-to-
    master policy, the fix is re-applied here as an MB-session commit so
    master gets the equivalent guard without cherry-picking from a
    splinter branch.

    Falls back to disk-rglob if git is unavailable (e.g. CI environments
    that unpack a tarball without .git history). On fallback the guard
    behaves as before.
    """
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--", *SCAN_DIRS],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        # Fallback: scan disk (git unavailable / not a repo)
        paths: list[Path] = []
        for d in SCAN_DIRS:
            root = REPO_ROOT / d
            if root.is_dir():
                paths.extend(sorted(root.rglob("*.py")))
        return paths
    return sorted(
        REPO_ROOT / p for p in out.splitlines() if p.endswith(".py")
    )


@pytest.mark.parametrize("path", _all_scan_paths())
def test_files_have_upper_bound_on_event_time_windowing(path: Path):
    """For every .py file in scripts/, bots/, base_engine/, ui/, every
    `<col> > NOW() - ...` or `<col> >= NOW() - ...` lower bound must be
    paired with an upper bound `<= NOW()` within 5 lines, using the same
    alias.

    This guards against the temporal-corruption bug class: forward-dated rows
    bypass lower-only bounds and inflate windowed P&L / diagnostics, and can
    pollute live trading control flow (calibration, dedup, drift tracking).
    """
    if path.name in KNOWN_CLEAN:
        pytest.skip(f"{path.name} explicitly marked clean")
    content = path.read_text(encoding="utf-8", errors="replace")
    unguarded = _find_unguarded_windowing(content)
    assert not unguarded, (
        f"{path.relative_to(REPO_ROOT)}: {len(unguarded)} unguarded windowing site(s):\n"
        + "\n".join(f"  line {ln}: {txt}" for ln, txt in unguarded)
    )


# Spot-check: verify the test itself catches a deliberately bad pattern.
def test_detector_catches_bad_pattern():
    bad = """
        WHERE bot_name = 'X'
          AND event_time > NOW() - INTERVAL '1 hour' * :hours
        ORDER BY event_time DESC
    """
    unguarded = _find_unguarded_windowing(bad)
    assert len(unguarded) == 1
    assert "NOW()" in unguarded[0][1]


def test_detector_accepts_guarded_pattern():
    good = """
        WHERE bot_name = 'X'
          AND event_time > NOW() - INTERVAL '1 hour' * :hours
          AND event_time <= NOW()
        ORDER BY event_time DESC
    """
    unguarded = _find_unguarded_windowing(good)
    assert unguarded == []


def test_detector_accepts_inline_upper_bound():
    """The mirror_realistic_pnl.py / weather_*.py f-string pattern: both
    bounds on one line. Detector must accept this."""
    good = '''
        time_filter = "AND event_time > NOW() - INTERVAL '1 hour' * :hours AND event_time <= NOW()"
    '''
    unguarded = _find_unguarded_windowing(good)
    assert unguarded == []


def test_detector_respects_alias_prefix():
    """Upper bound must match the alias prefix. `te.event_time > NOW()` paired
    with bare `event_time <= NOW()` (different alias) is NOT a match."""
    bad_mismatched_alias = """
        WHERE te.event_time > NOW() - INTERVAL '1 hour'
          AND xyz.event_time <= NOW()
    """
    unguarded = _find_unguarded_windowing(bad_mismatched_alias)
    assert len(unguarded) == 1

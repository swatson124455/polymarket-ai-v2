"""Static regression guard: every windowed `event_time > NOW() - INTERVAL ...`
or `resolved_at > NOW() - INTERVAL ...` clause in scripts/ must be paired with
an upper-bound `<= NOW()` clause nearby.

Without the upper bound, forward-dated rows (the temporal-corruption bug class)
inflate windowed results. This test fails if any caller forgets the upper
bound, including new scripts added in the future.

Scope: scripts/ — diagnostic + reporting tools. Live bot reader queries are
covered by separate tests in their own test files.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Files explicitly known to NOT contain the windowing pattern (skip optimization).
# Empty for now — every script with the pattern should be covered.
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
        r"(\w+\.)?(event_time|resolved_at)\s*>\s*NOW\(\)\s*-"
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


@pytest.mark.parametrize("path", sorted(SCRIPTS_DIR.glob("*.py")))
def test_scripts_have_upper_bound_on_event_time_windowing(path: Path):
    """For every script in scripts/, every `event_time > NOW() - ...` lower
    bound must be paired with an upper bound `<= NOW()` within 5 lines.

    This guards against the temporal-corruption bug class: forward-dated rows
    bypass lower-only bounds and inflate windowed P&L / diagnostics.
    """
    if path.name in KNOWN_CLEAN:
        pytest.skip(f"{path.name} explicitly marked clean")
    content = path.read_text(encoding="utf-8", errors="replace")
    unguarded = _find_unguarded_windowing(content)
    assert not unguarded, (
        f"{path.name}: found {len(unguarded)} unguarded windowing site(s):\n"
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

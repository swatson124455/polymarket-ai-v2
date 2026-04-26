#!/usr/bin/env python3
"""S195 regression guard: forbid SQL `--` line comments inside Python adjacent
string-literal concatenation.

The bug shape (database.py:5530, fixed in commit b82ad68 — undetected for ~17
days, blocked all RESOLUTION events from emitting):

    sql = (
        "SELECT ... "
        "WHERE foo = 1 -- comment with no trailing newline"
        "AND bar = 2"  # silently consumed by the `--` above
    )

Python concatenates without inserting newlines, so PostgreSQL parses the result
as one logical line; the `--` runs to end-of-input and eats the rest of the
query. The exception was only logged at debug level, so the silent-zero ran for
17 calendar days before anyone noticed.

This detector flags the pattern using libcst. Detection rule:
  - >=2 adjacent string-literal fragments (libcst.ConcatenatedString chain)
  - Joined string contains a SQL keyword (case-insensitive, word-boundary)
  - Some non-final fragment contains `--` with no `\n` after it in that
    fragment — i.e. the `--` would consume content from the next fragment

Triple-quoted strings, f-strings, and single string literals are ignored.

Usage:
    python scripts/check_sql_dash_dash.py [FILES...]
    python scripts/check_sql_dash_dash.py --all   # walk the repo

Exit codes:
    0 — no violations
    1 — violations found (output is grep-friendly: path:line: message)
    2 — usage error or parse failure
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List

try:
    import libcst as cst
except ImportError:
    print("ERROR: libcst not installed. `pip install libcst==1.8.6`", file=sys.stderr)
    sys.exit(2)


# Strong DML/DDL keywords + multi-word phrases that are distinctive enough to
# survive prose. Single-word weak ones (WITH, SET, INTO, FROM, WHERE, JOIN,
# UNION, RETURNING, HAVING, VALUES, EXISTS) match common English and self-flag
# the detector's own help text — they are dropped.
SQL_KEYWORDS = re.compile(
    r"\b("
    r"SELECT|INSERT|UPDATE|DELETE|"
    r"CREATE|DROP|ALTER|TRUNCATE|"
    r"GROUP\s+BY|ORDER\s+BY|NOT\s+EXISTS|ON\s+CONFLICT"
    r")\b",
    re.IGNORECASE,
)

# Default repo subtrees to walk in --all mode. Excludes tests so the regression
# guards in tests/unit/test_trade_events_resolution_backfill.py (which use
# `--` strings *to describe* the bug) are not flagged.
_WALK_ROOTS = (
    "base_engine",
    "bots",
    "esports",
    "esports_v2",
    "scripts",
    "sports",
)


def _flatten_concat(node: cst.BaseExpression) -> List[cst.BaseString]:
    """Flatten a (potentially nested) ConcatenatedString into a fragment list."""
    if isinstance(node, cst.ConcatenatedString):
        return _flatten_concat(node.left) + _flatten_concat(node.right)
    if isinstance(node, (cst.SimpleString, cst.FormattedString)):
        return [node]
    return []


def _fragment_text(node: cst.BaseString) -> str:
    """Decoded content of a string fragment.

    For SimpleString returns evaluated_value (no quotes, escapes resolved).
    For FormattedString concatenates the literal text parts only; expression
    parts are replaced with a placeholder so `--` detection still works on the
    surface form. f-strings are uncommon in SQL (injection risk) but still
    valid concat participants.
    """
    if isinstance(node, cst.SimpleString):
        return node.evaluated_value
    if isinstance(node, cst.FormattedString):
        chunks: list[str] = []
        for part in node.parts:
            if isinstance(part, cst.FormattedStringText):
                chunks.append(part.value)
            else:
                chunks.append("{}")
        return "".join(chunks)
    return ""


def _find_sql_dash_dash_offsets(joined: str) -> list[int]:
    """Return offsets in `joined` where a SQL `--` line comment actually starts.

    Skips `--` occurrences inside `/* ... */` block comments, single-quoted
    string literals (with PG-standard `''` escape), and `"..."` identifiers.
    These are the four parser states PostgreSQL recognises around `--`.
    """
    offsets: list[int] = []
    i = 0
    n = len(joined)
    in_block = False
    in_squote = False
    in_dquote = False
    while i < n:
        ch = joined[i]
        nxt = joined[i + 1] if i + 1 < n else ""
        if in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            i += 1
            continue
        if in_squote:
            # PostgreSQL escapes `'` by doubling it.
            if ch == "'" and nxt == "'":
                i += 2
                continue
            if ch == "'":
                in_squote = False
            i += 1
            continue
        if in_dquote:
            if ch == '"':
                in_dquote = False
            i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        if ch == "'":
            in_squote = True
            i += 1
            continue
        if ch == '"':
            in_dquote = True
            i += 1
            continue
        if ch == "-" and nxt == "-":
            offsets.append(i)
            # Skip to end-of-line so a single line comment isn't double-flagged.
            nl = joined.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            continue
        i += 1
    return offsets


def _has_dash_dash_bug(fragments: List[cst.BaseString]) -> bool:
    """Return True iff the concat chain matches the S195 bug shape.

    Bug shape: SQL `--` line comment in a non-final fragment with no `\n` after
    the `--` inside that fragment, so adjacent-string concat splices the next
    fragment into the comment's tail and PG eats it.
    """
    if len(fragments) < 2:
        return False
    fragment_texts = [_fragment_text(f) for f in fragments]
    joined = "".join(fragment_texts)
    if not SQL_KEYWORDS.search(joined):
        return False
    dash_offsets = _find_sql_dash_dash_offsets(joined)
    if not dash_offsets:
        return False

    # Build cumulative offsets to map each `--` back to its fragment.
    # Fragment k spans [bounds[k], bounds[k+1]).
    bounds = [0]
    for t in fragment_texts:
        bounds.append(bounds[-1] + len(t))

    last_idx = len(fragments) - 1
    for off in dash_offsets:
        # Which fragment contains this `--`?
        frag_idx = next(
            k for k in range(len(fragments)) if bounds[k] <= off < bounds[k + 1]
        )
        if frag_idx == last_idx:
            continue  # `--` in the final fragment is harmless
        # Is there a `\n` after the `--` but before the fragment ends?
        end_of_frag = bounds[frag_idx + 1]
        tail = joined[off:end_of_frag]
        if "\n" in tail:
            continue
        return True
    return False


class _Visitor(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (
        cst.metadata.PositionProvider,
        cst.metadata.ParentNodeProvider,
    )

    def __init__(self) -> None:
        self.violations: List[tuple[int, int]] = []  # (line, col)

    def visit_ConcatenatedString(self, node: cst.ConcatenatedString) -> None:
        # Only walk the OUTER concat node — its fragments cover the full chain.
        # Skip nested ConcatenatedString to avoid double-reporting.
        parent = self.get_metadata(cst.metadata.ParentNodeProvider, node, None)
        if isinstance(parent, cst.ConcatenatedString):
            return
        fragments = _flatten_concat(node)
        if _has_dash_dash_bug(fragments):
            pos = self.get_metadata(cst.metadata.PositionProvider, node).start
            self.violations.append((pos.line, pos.column))


def check_file(path: Path) -> List[str]:
    """Return a list of human-readable violation messages for `path`."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path}: read failed: {exc}"]
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        # Don't fail the hook on unrelated syntax errors; let the linter handle it.
        return []
    wrapper = cst.metadata.MetadataWrapper(module)
    visitor = _Visitor()
    wrapper.visit(visitor)
    return [
        f"{path}:{line}: SQL `--` inside adjacent-string concat (S195 bug shape)"
        for (line, _col) in visitor.violations
    ]


def _iter_python_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix == ".py":
                yield root
            continue
        for path in root.rglob("*.py"):
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Python files to check. With --all, ignored.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Walk the canonical repo subtrees instead of using FILES.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    if args.all:
        roots = [repo_root / r for r in _WALK_ROOTS]
    elif args.files:
        roots = args.files
    else:
        parser.error("provide files or --all")

    all_violations: List[str] = []
    for path in _iter_python_files(roots):
        all_violations.extend(check_file(path))

    if all_violations:
        for v in all_violations:
            print(v)
        print(
            f"\n{len(all_violations)} SQL `--`-in-adjacent-string violation(s) "
            f"found. Replace with `/* ... */` block comments.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

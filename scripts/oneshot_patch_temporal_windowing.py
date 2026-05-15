"""HISTORICAL ARTIFACT — one-shot patcher used to generate Commit 1d82060.

Committed for provenance: if a regression surfaces in a windowed-query site
and you wonder "why was this specific patch written that way?", this is the
generator. Re-running today is a no-op because every site already has its
upper bound (`if not sites: return 0`).

Purpose: insert `event_time <= NOW()` / `resolved_at <= NOW()` upper-bound
clauses after every unguarded windowing lower bound across the codebase.

Handles 3 SQL-fragment shapes (the docstring uses <COL> as a placeholder to
keep its own examples from matching the detector regex):
  (a) Triple-quoted SQL block: bare SQL line like  AND <COL> >= NOW - INTERVAL '...'
       → insert a new line below with same indent + matching upper bound
  (b) Python string concat: quoted SQL fragment like " AND <COL> >= NOW - INTERVAL '...'"
       → insert a new string-element line below
  (c) Inline f-string / assignment ending with closing quote
       → append the upper bound before the closing quote

The detector logic lives in tests/unit/test_bot_pnl_windowing.py. This patcher
imports `_find_unguarded_windowing` from there as the single source of truth
for what constitutes an "unguarded" site.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "unit"))
from test_bot_pnl_windowing import _find_unguarded_windowing, _all_scan_paths  # type: ignore


def patch_file(path: Path) -> int:
    content = path.read_text(encoding="utf-8")
    sites = _find_unguarded_windowing(content)
    if not sites:
        return 0

    lines = content.splitlines(keepends=False)
    # Bottom-up so inserted lines don't shift line numbers above
    sites_sorted = sorted(sites, key=lambda s: -s[0])
    patched = 0

    for line_no, _txt in sites_sorted:
        idx = line_no - 1
        line = lines[idx]

        m = re.search(r"(\w+\.)?(event_time|resolved_at)\s*>=?\s*NOW\(\)\s*-", line)
        if not m:
            continue
        prefix = m.group(1) or ""
        col = m.group(2)
        upper_clause = f"AND {prefix}{col} <= NOW()"

        # Case (c): inline string with both opening + closing quote on this line
        stripped = line.rstrip()
        quote_char = None
        for q in ('"', "'"):
            if stripped.endswith(q):
                quote_char = q
                break
        if quote_char and quote_char in line[: m.start()]:
            insert_pt = stripped.rfind(quote_char)
            new_line = (
                stripped[:insert_pt]
                + (" " if stripped[insert_pt - 1] != " " else "")
                + upper_clause
                + stripped[insert_pt:]
            )
            tail = line[len(stripped):]
            lines[idx] = new_line + tail
            patched += 1
            continue

        # Case (b): Python string concat — line is a quoted SQL fragment
        py_concat_match = re.match(r"^(\s*)([\"\'])(\s*AND\s+.+)\2(,?\s*)$", line)
        if py_concat_match:
            indent, qch, _body, trailing = py_concat_match.groups()
            new_line = f"{indent}{qch} {upper_clause}{qch}{trailing}"
            lines.insert(idx + 1, new_line)
            patched += 1
            continue

        # Case (a): triple-quoted SQL block
        indent_match = re.match(r"^(\s*)", line)
        indent = indent_match.group(1) if indent_match else ""
        new_line = f"{indent}{upper_clause}"
        lines.insert(idx + 1, new_line)
        patched += 1

    if patched:
        new_content = "\n".join(lines)
        if content.endswith("\n"):
            new_content += "\n"
        path.write_text(new_content, encoding="utf-8")
    return patched


def main():
    total = 0
    for path in _all_scan_paths():
        try:
            n = patch_file(path)
        except Exception as e:
            print(f"FAIL {path}: {e}")
            continue
        if n:
            print(f"  {path.relative_to(ROOT)}: patched {n} site(s)")
            total += n
    if total == 0:
        print("No unguarded windowing sites found — codebase is clean.")
    else:
        print(f"\nTOTAL: {total} sites patched")


if __name__ == "__main__":
    main()

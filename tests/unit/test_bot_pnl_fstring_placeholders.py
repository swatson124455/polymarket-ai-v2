"""Static regression guard: every text(...) SQL block in scripts/bot_pnl.py
that embeds a brace placeholder (e.g. mode_exec_clause_r) MUST be an f-string.

A plain (non-f) text() string that contains a {placeholder} ships the literal
brace to Postgres, which raises 'syntax error at or near "{"' at execute time.
The WB per-confidence-bin query (r_wb_conf) regressed exactly this way when its
text() opener lost the f-prefix while its siblings kept it.

The check is AST-based, so it is immune to comment/whitespace noise:
  - an f-string arg parses to ast.JoinedStr (placeholders interpolated -> safe)
  - a plain string arg parses to ast.Constant (placeholders shipped raw -> bug)
A Constant string argument that contains a {name} token is the bug.
"""
import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGET = REPO_ROOT / "scripts" / "bot_pnl.py"

# {identifier} -- the interpolation form used for the mode_exec_clause* knobs.
PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_]\w*\}")


def _unsubstituted_placeholder_calls(source: str) -> list[tuple[int, str]]:
    """Return (lineno, placeholder) for every text(<plain-str>) call whose
    string literal still contains a {name} placeholder -- i.e. a call that
    should have been an f-string but was not.
    """
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "text"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        # f-strings parse to JoinedStr and are safe; only plain Constant strs
        # ship their braces verbatim.
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            for m in PLACEHOLDER_RE.finditer(arg.value):
                hits.append((node.lineno, m.group(0)))
    return hits


def test_bot_pnl_text_placeholders_are_fstrings():
    source = TARGET.read_text(encoding="utf-8")
    hits = _unsubstituted_placeholder_calls(source)
    assert not hits, (
        f"{TARGET.relative_to(REPO_ROOT)}: {len(hits)} text() SQL block(s) with an "
        f"unsubstituted brace placeholder (missing f-prefix -> Postgres syntax "
        f"error near a brace):\n"
        + "\n".join(f"  line {ln}: {ph}" for ln, ph in hits)
    )


# --- detector self-checks (defense against the guard silently breaking) ---

def test_detector_catches_plain_text_with_placeholder():
    bad = 'r = text("SELECT 1 WHERE x=1 {mode_exec_clause_r}")'
    assert _unsubstituted_placeholder_calls(bad) == [(1, "{mode_exec_clause_r}")]


def test_detector_accepts_fstring_with_placeholder():
    good = 'r = text(f"SELECT 1 WHERE x=1 {mode_exec_clause_r}")'
    assert _unsubstituted_placeholder_calls(good) == []


def test_detector_accepts_plain_text_without_placeholder():
    fine = 'r = text("SELECT 1 WHERE x = :bot_family")'
    assert _unsubstituted_placeholder_calls(fine) == []

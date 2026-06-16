"""S245 R1 — engine-tree parity guard (S239 dead-tree-trap prevention).

WeatherBot runs a HYBRID tree: the live systemd unit runs main.py (TOP-LEVEL
base_engine), but bots/weather_bot.py imports the weather PRODUCER engines from the
SILO (bots.weather.engine.base_engine.weather.*). So a fix must land in the tree that
actually runs, and the two trees must not silently drift. That drift IS the bug class
that cost five sessions: S231/S232/S237/S245 all first landed in the dead silo and ran
NOWHERE until ported. This guard fails loudly on drift so a fix can never again hide in
the dead copy.

Two kinds of assertion:
  (1) Byte-parity (EOL-normalized) for files that MUST be identical across trees — the
      live PRODUCER engine (precipitation_engine, shared by precip+snow; the S245 fix
      lives here) and liquidity_guardian. An edit to one copy that forgets the other
      fails here. probability_engine is allowed exactly ONE legitimate diff (its
      settings import path).
  (2) Live-tree fix-marker presence for the execution files that LEGITIMATELY differ
      across trees today (paper_trading/order_gateway carry silo-path imports + some
      silo-only WIP — see CROSS_BRANCH_LANDMINES / project_wb_slippage_anchor_deadtree_ports):
      assert the critical S2xx/S245 fixes are present in the LIVE (top-level) tree that
      actually runs, so a fix cannot be reverted or hide only in the dead silo.

NOTE (tracked drift, intentionally NOT byte-asserted): silo paper_trading.py currently
carries a directional slippage guard + S238 phantom-entry retry that the live top-level
lacks — a separate port (deferred). When that lands, consider upgrading paper_trading to
a byte-parity assertion too.
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]  # tests/unit -> tests -> worktree root
TOP = ROOT / "base_engine"
SILO = ROOT / "bots" / "weather" / "engine" / "base_engine"


def _lines(p: pathlib.Path):
    return p.read_text(encoding="utf-8").splitlines()  # splitlines() normalizes CRLF/LF


def _first_diff_line(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i + 1
    return min(len(a), len(b)) + 1


@pytest.mark.parametrize("rel", [
    "weather/precipitation_engine.py",  # live PRODUCER (precip + snow); S245 fix lives here
    "risk/liquidity_guardian.py",       # S237 HIGH-1 anchor
])
def test_engine_trees_byte_identical(rel):
    """These files are byte-identical across trees today; keep them so."""
    top, silo = _lines(TOP / rel), _lines(SILO / rel)
    assert top == silo, (
        f"DEAD-TREE DRIFT (S239 trap): base_engine/{rel} and "
        f"bots/weather/engine/base_engine/{rel} diverged; a fix in one copy must be "
        f"mirrored to the other. First diff near line {_first_diff_line(top, silo)}."
    )


def test_probability_engine_differs_only_by_settings_import():
    """probability_engine differs across trees ONLY by its settings import path."""
    top, silo = _lines(TOP / "weather/probability_engine.py"), _lines(SILO / "weather/probability_engine.py")
    assert len(top) == len(silo), (
        f"probability_engine line-count drift across trees ({len(top)} vs {len(silo)}) — "
        f"more than the expected single settings-import diff."
    )
    allowed = {(
        "from config.settings import settings",
        "from bots.weather.engine.config.settings import settings",
    )}
    extra = [
        (i + 1, a, b)
        for i, (a, b) in enumerate(zip(top, silo))
        if a != b and (a.strip(), b.strip()) not in allowed
    ]
    assert not extra, (
        f"probability_engine has UNEXPECTED cross-tree drift beyond the settings import "
        f"(S239 trap): {extra}"
    )


# Files that legitimately differ across trees (silo-path imports etc.) — assert the LIVE
# (top-level) tree, the one main.py actually injects, still carries each critical fix.
LIVE_FIX_MARKERS = {
    "base_engine/execution/paper_trading.py": ["S231", "_shadow_best_ask"],
    "base_engine/execution/order_gateway.py": [
        "S232", "S237 HIGH-2", 'result.get("price") or effective_price',
    ],
    "base_engine/risk/liquidity_guardian.py": ["S237 HIGH-1"],
    "bots/weather_bot.py": ["S245 ENGINE-BOUNDARY INVARIANT", 'side="SELL"'],
    "base_engine/weather/precipitation_engine.py": ["S245 CANONICAL"],
    "bots/weather/engine/base_engine/weather/precipitation_engine.py": ["S245 CANONICAL"],
}


@pytest.mark.parametrize("rel,markers", list(LIVE_FIX_MARKERS.items()))
def test_live_tree_contains_fix_markers(rel, markers):
    """A critical fix must be present in the tree that runs — not reverted, not stranded
    in the dead silo (S239 trap)."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    missing = [m for m in markers if m not in text]
    assert not missing, (
        f"LIVE tree {rel} is missing critical fix marker(s) {missing} — a fix was reverted "
        f"or only landed in the dead silo (S239 dead-tree trap)."
    )

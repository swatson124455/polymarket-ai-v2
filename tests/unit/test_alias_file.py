"""Unit tests for the file-backed alias_expand (esports_team_aliases dump).

Covers the canonical loader (esports_v2/data/alias_file.py), its injection into
match_pm_ref, and the standalone collector's stdlib mirror (load_aliases /
same_team alias path) — all correct-or-absent: any file problem -> None ->
matching runs exactly as strict as before.
"""
import importlib.util
import json
from pathlib import Path

from esports_v2.data.alias_file import build_alias_expand, load_alias_expand
from esports_v2.data.pm_market_index import PMMarketRef, match_pm_ref

_STANDALONE = (Path(__file__).resolve().parents[2]
               / "deploy" / "vps" / "collect_pinnodds_standalone.py")


def _load_standalone():
    spec = importlib.util.spec_from_file_location("eb_standalone", _STANDALONE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── build_alias_expand ───────────────────────────────────────────────────────


def test_build_groups_link_both_directions():
    expand = build_alias_expand([["Natus Vincere", "NAVI"]])
    assert "NAVI" in expand("Natus Vincere")
    assert "Natus Vincere" in expand("NAVI")


def test_build_lookup_is_normalized():
    expand = build_alias_expand([["Natus Vincere", "NAVI"]])
    assert "Natus Vincere" in expand("navi")          # case-insensitive key
    assert expand("unknown team") == []


def test_build_identity_only_groups_dropped():
    assert build_alias_expand([["Fnatic"], ["G2 Esports"]]) is None


def test_build_malformed_returns_none():
    assert build_alias_expand(None) is None
    assert build_alias_expand("not a list") is None
    assert build_alias_expand([{"a": 1}, "x", []]) is None


def test_build_name_in_two_groups_gets_union():
    expand = build_alias_expand([["FaZe Clan", "FaZe"], ["FaZe", "FaZe Esports"]])
    got = set(expand("FaZe"))
    assert {"FaZe Clan", "FaZe Esports"} <= got


# ── load_alias_expand (correct-or-absent on any file problem) ────────────────


def test_load_missing_file_returns_none(tmp_path):
    assert load_alias_expand(tmp_path / "nope.json") is None
    assert load_alias_expand(None) is None
    assert load_alias_expand("") is None


def test_load_malformed_json_returns_none(tmp_path):
    p = tmp_path / "aliases.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_alias_expand(p) is None


def test_load_wrong_shape_returns_none(tmp_path):
    p = tmp_path / "aliases.json"
    p.write_text(json.dumps([["NAVI", "Natus Vincere"]]), encoding="utf-8")
    assert load_alias_expand(p) is None  # top level must be an object


def test_load_good_file_round_trip(tmp_path):
    p = tmp_path / "aliases.json"
    p.write_text(json.dumps({"groups": [["Natus Vincere", "NAVI"]]}),
                 encoding="utf-8")
    expand = load_alias_expand(p)
    assert expand is not None
    assert "Natus Vincere" in expand("NAVI")


# ── injection into match_pm_ref (the pm_matched booster) ─────────────────────


def _navi_ref():
    return PMMarketRef(
        condition_id="0xabc", yes_token_id="1", yes_outcome="Natus Vincere",
        market_price=0.6, question="CS: Natus Vincere vs FaZe Clan (BO3)",
        game_start="2026-07-15 12:00:00+00", team_a="Natus Vincere",
        team_b="FaZe Clan", day="2026-07-15",
    )


def test_match_pm_ref_without_aliases_misses_navi():
    # baseline: token matcher alone cannot link NAVI <-> Natus Vincere
    assert match_pm_ref("NAVI", "FaZe", "2026-07-15T12:00:00Z",
                        [_navi_ref()]) is None


def test_match_pm_ref_with_file_aliases_hits(tmp_path):
    p = tmp_path / "aliases.json"
    p.write_text(json.dumps({"groups": [["Natus Vincere", "NAVI"],
                                        ["FaZe Clan", "FaZe"]]}),
                 encoding="utf-8")
    expand = load_alias_expand(p)
    got = match_pm_ref("NAVI", "FaZe", "2026-07-15T12:00:00Z", [_navi_ref()],
                       alias_expand=expand)
    assert got is not None and got.condition_id == "0xabc"


# ── standalone stdlib mirror stays in behavioral parity ──────────────────────


def test_standalone_load_aliases_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("EB_ALIASES_PATH", str(tmp_path / "nope.json"))
    sa = _load_standalone()
    assert sa.load_aliases() is None


def test_standalone_alias_map_links_navi(tmp_path, monkeypatch):
    p = tmp_path / "aliases.json"
    p.write_text(json.dumps({"groups": [["Natus Vincere", "NAVI"],
                                        ["FaZe Clan", "FaZe"]]}),
                 encoding="utf-8")
    monkeypatch.setenv("EB_ALIASES_PATH", str(p))
    sa = _load_standalone()
    am = sa.load_aliases()
    assert am is not None
    # same_team via aliases, both directions
    assert sa.same_team("NAVI", "Natus Vincere", am)
    assert sa.same_team("Natus Vincere", "NAVI", am)
    # without the map the pair still does NOT match (no behavior drift)
    assert not sa.same_team("NAVI", "Natus Vincere")
    # full bijective ref match with the map
    ref = ("0xabc", "1", "Natus Vincere", 0.6, "Natus Vincere", "FaZe Clan",
           "2026-07-15")
    assert sa.match_ref("NAVI", "FaZe", "2026-07-15T12:00:00Z", [ref],
                        am=am) == ref
    assert sa.match_ref("NAVI", "FaZe", "2026-07-15T12:00:00Z", [ref]) is None


def test_standalone_identity_only_and_malformed_are_none(tmp_path, monkeypatch):
    p = tmp_path / "aliases.json"
    p.write_text(json.dumps({"groups": [["Fnatic"]]}), encoding="utf-8")
    monkeypatch.setenv("EB_ALIASES_PATH", str(p))
    sa = _load_standalone()
    assert sa.load_aliases() is None
    p.write_text("{broken", encoding="utf-8")
    sa2 = _load_standalone()
    assert sa2.load_aliases() is None


def test_standalone_qualifier_veto_parity(tmp_path, monkeypatch):
    """The stdlib mirror applies the same sibling-roster veto, alias or not."""
    p = tmp_path / "aliases.json"
    p.write_text(json.dumps({"groups": [["T1", "T1 Academy",
                                         "T1 Esports Academy"]]}),
                 encoding="utf-8")
    monkeypatch.setenv("EB_ALIASES_PATH", str(p))
    sa = _load_standalone()
    am = sa.load_aliases()
    assert am is not None
    assert not sa.same_team("T1", "T1 Academy")
    assert not sa.same_team("T1", "T1 Academy", am)   # veto outranks alias
    assert sa.same_team("T1 Academy", "T1 Esports Academy")
    assert sa.same_team("G2 Esports", "G2")           # decorations untouched
    ref = ("0xacad", "1", "T1 Academy", 0.5, "T1 Academy", "Gen.G Academy",
           "2026-07-15")
    assert sa.match_ref("T1", "Gen.G", "2026-07-15T12:00:00Z", [ref], am=am) is None

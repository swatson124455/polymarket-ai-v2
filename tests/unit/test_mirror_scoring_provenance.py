"""A4 — assumption provenance ledger (operator directive 2026-07-06).

Every ScoringConfig knob must be labeled MEASURED / CONVENTION / ASSUMED.
The enforcement test makes adding an unlabeled knob a test failure, so
assumption debt can only ever be explicit.
"""
from __future__ import annotations

from dataclasses import fields

from bots.mirror_scoring.config import (
    PROVENANCE, ScoringConfig, unmeasured_assumptions,
)

_VALID_PREFIXES = ("MEASURED", "CONVENTION", "ASSUMED")


def test_every_config_knob_has_provenance():
    """New knob without a ledger entry -> this fails -> debt stays explicit."""
    knobs = {f.name for f in fields(ScoringConfig)} - {"extra"}
    missing = knobs - set(PROVENANCE)
    assert not missing, f"knobs without provenance labels: {sorted(missing)}"


def test_every_label_uses_a_valid_prefix():
    bad = {k: v for k, v in PROVENANCE.items()
           if not v.startswith(_VALID_PREFIXES)}
    assert not bad, f"labels must start with {_VALID_PREFIXES}: {bad}"


def test_no_stale_ledger_entries():
    """Ledger entries for deleted knobs must be cleaned up."""
    knobs = {f.name for f in fields(ScoringConfig)}
    stale = set(PROVENANCE) - knobs
    assert not stale, f"ledger entries for nonexistent knobs: {sorted(stale)}"


def test_unmeasured_list_is_the_assumed_subset():
    assumed = unmeasured_assumptions()
    assert assumed == sorted(
        k for k, v in PROVENANCE.items() if v.startswith("ASSUMED")
    )
    # The two headline inherited numbers are still on the books until measured:
    assert "DELTA_SECONDS" in assumed
    assert "FEE_ROUNDTRIP" in assumed


def test_measured_entries_cite_a_source():
    """MEASURED without a source is just ASSUMED wearing a costume."""
    for k, v in PROVENANCE.items():
        if v.startswith("MEASURED"):
            assert ":" in v and len(v.split(":", 1)[1].strip()) > 10, (
                f"{k}: MEASURED label must cite its source/date"
            )

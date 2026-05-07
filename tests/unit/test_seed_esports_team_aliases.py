"""Tests for scripts/seed_esports_team_aliases.py.

The seed script populates esports_team_aliases by cross-referencing
PandaScore canonical names against Polymarket question text. Two known
bugs limited its coverage:

  Bug 1: question loader filtered WHERE LOWER(category) = 'esports', missing
         the ~half of esports markets Polymarket miscategorizes as
         'sports' / 'crypto' / etc.
  Bug 2: variant regex required 2+ capitalized words, missing single-word
         acronyms (T1, G2, KT) and dotted names (Gen.G, devils.one).

These tests are static-source/pure-function checks — no DB integration.
"""
import inspect

from scripts import seed_esports_team_aliases as seed


# ── Bug 1: question loader keyword filter ──────────────────────────────────


def test_question_loader_drops_category_filter():
    """The buggy `WHERE LOWER(category) = 'esports'` clause must be gone."""
    src = inspect.getsource(seed._load_polymarket_esports_questions)
    assert "LOWER(category) = 'esports'" not in src, (
        "Seed script still narrows by category — sports/crypto-tagged "
        "esports markets will be missed."
    )


def test_question_loader_uses_matcher_keyword_filter():
    """Loader must mirror the matcher's keyword universe so the seed
    sees the same markets the runtime scanner does."""
    src = inspect.getsource(seed._load_polymarket_esports_questions)
    # Sample of must-have ILIKE markers from the matcher's keyword set
    for marker in (
        "question ILIKE '%esports%'",
        "question ILIKE '%league of legends%'",
        "question ILIKE '%counter-strike%'",
        "question ILIKE '%dota%'",
        "question ILIKE '%valorant%'",
    ):
        assert marker in src, f"missing keyword filter clause: {marker}"
    # Acronym regex (Postgres word-boundary form)
    assert "lol|lck|lec|lpl|lcs" in src, (
        "missing acronym regex from matcher's keyword filter"
    )


def test_question_loader_keeps_active_filter():
    """Variant extraction should still scope to active markets — historical
    markets carry stale org names that have since rebranded."""
    src = inspect.getsource(seed._load_polymarket_esports_questions)
    assert "active = TRUE" in src

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


# ── Bug 2: variant regex (single-word acronyms + dotted names) ─────────────


def test_extract_variants_finds_multi_word_orgs():
    """Regression: pre-Bug-2 behavior preserved for multi-word org names."""
    variants = seed._extract_variants("LoL: Hanwha Life Esports vs DN SOOPers - Game 1 Winner")
    assert "Hanwha Life Esports" in variants


def test_extract_variants_finds_single_word_acronyms():
    """Bug 2: T1, G2, KT, DRX, FNC are valid LoL teams that pre-fix
    were never extracted because the regex required 2+ capitalized words."""
    q = "LoL: T1 vs DRX - Game 1 Winner"
    variants = seed._extract_variants(q)
    assert "T1" in variants
    assert "DRX" in variants


def test_extract_variants_finds_dotted_names():
    """Bug 2: Polymarket uses 'Gen.G' and 'devils.one' style names that
    pre-fix were never extracted (period broke word-run matching)."""
    q = "LoL: GLORE vs devils.one - Game 1 Winner"
    variants = seed._extract_variants(q)
    assert "devils.one" in variants


def test_extract_variants_dotted_capitalized():
    """Variant of dotted-name shape: capitalized both sides ('Gen.G')."""
    variants = seed._extract_variants("LoL: T1 vs Gen.G - Game 2 Winner")
    assert "Gen.G" in variants


def test_extract_variants_empty_string():
    """Defensive: empty input produces empty output, no exception."""
    assert seed._extract_variants("") == []
    assert seed._extract_variants(None) == []  # type: ignore[arg-type]


def test_extract_variants_deduplicates_repeated_match():
    """A variant matched twice by the same pattern appears only once."""
    variants = seed._extract_variants(
        "LoL: Hanwha Life Esports vs Hanwha Life Esports - Game 1 Winner"
    )
    assert variants.count("Hanwha Life Esports") == 1


def test_extract_variants_known_gap_acronym_plus_word():
    """Documented gap, NOT a feature: 'KT Rolster' currently extracts
    only 'KT' (pattern b) — pattern (a) requires lowercase after the
    first capital so 'KT' fails it, and the second word 'Rolster' alone
    fails pattern (a)'s 2+ word requirement. This is acceptable for the
    current seed pass: the canonical name 'KT Rolster' will still match
    Polymarket questions that say 'KT Rolster' verbatim via the
    matcher's substring path, and the 'KT' alias gets seeded if the
    question uses 'KT' bare. A future pattern (d) for
    ACRONYM+capitalized-word would close this gap; out of scope here."""
    variants = seed._extract_variants("LoL: KT Rolster vs Drx - Game 1")
    assert "KT" in variants
    assert "KT Rolster" not in variants  # documented gap

"""Basic regression tests for interview helper utilities."""

from app.services import interview_service


def test_normalize_manual_question_inserts_prompt():
    """Words unknown to the DB should expand into a full question template."""
    # xyzunknownterm99 is not in the DB → gets expanded
    expanded = interview_service.normalize_manual_question("xyzunknownterm99")
    assert "xyzunknownterm99" in expanded.lower()
    # DB-known short words (e.g. "lambda") are left as-is; the DB handles them directly
    unchanged = interview_service.normalize_manual_question("lambda")
    assert "lambda" in unchanged.lower()


def test_normalize_manual_question_leaves_full_question():
    """Verbose questions containing question words should remain unchanged."""
    question = "How does Python's lambda work?"
    assert interview_service.normalize_manual_question(question) == question


def test_expand_short_keyword_matches():
    """DB-known keywords stay unchanged; unknown short words get expanded."""
    # "lambda" IS in the DB → no expansion, returned as-is
    result = interview_service.expand_short_keyword("lambda")
    assert "lambda" in result.lower()
    # Unknown word → expands to "What is X? Explain with examples."
    result2 = interview_service.expand_short_keyword("xyzunknownterm99")
    assert "xyzunknownterm99" in result2.lower()
    assert "What is" in result2


def test_expand_short_keyword_ignores_long_text():
    """Long inputs or already detailed sentences should not change."""
    long_text = "Explain how to tune a bubble sort implementation in Python with complexity O(n^2)."
    assert interview_service.expand_short_keyword(long_text) == long_text

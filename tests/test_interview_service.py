"""Basic regression tests for interview helper utilities."""

from app.services import interview_service


def test_normalize_manual_question_inserts_prompt():
    """Short one-word inputs should expand into a full question template."""
    expanded = interview_service.normalize_manual_question("lambda")
    assert expanded.startswith("What is lambda?")
    assert "Explain in detail with examples" in expanded


def test_normalize_manual_question_leaves_full_question():
    """Verbose questions containing question words should remain unchanged."""
    question = "How does Python's lambda work?"
    assert interview_service.normalize_manual_question(question) == question


def test_expand_short_keyword_matches():
    """Known keywords should expand to their configured full question."""
    assert interview_service.expand_short_keyword("lambda") == "What is a lambda function in Python?"
    assert interview_service.expand_short_keyword("bubble sort") == "Write a bubble sort algorithm."


def test_expand_short_keyword_ignores_long_text():
    """Long inputs or already detailed sentences should not change."""
    long_text = "Explain how to tune a bubble sort implementation in Python with complexity O(n^2)."
    assert interview_service.expand_short_keyword(long_text) == long_text

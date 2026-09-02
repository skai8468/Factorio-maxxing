"""Tests for policy extraction.

Required coverage (build-plan section 21, Policy parsing): fenced Python, bare
Python, malformed responses. A malformed response yields empty code rather than
raising, so the failure stays in the trajectory as data (approved by the research
lead).
"""

import pytest

from factorio_maxxing.llm import extract_policy


def test_fenced_python_block():
    response = "Here is my policy:\n```python\nplace_entity('drill')\n```"
    assert extract_policy(response) == "place_entity('drill')"


@pytest.mark.parametrize("tag", ["python", "py", "python3", "Python", "PY"])
def test_python_tag_variants(tag):
    assert extract_policy(f"```{tag}\nx = 1\n```") == "x = 1"


def test_untagged_fence_is_treated_as_code():
    assert extract_policy("```\nx = 1\n```") == "x = 1"


def test_a_tagged_python_block_wins_over_an_untagged_one():
    response = "```\nnot the policy\n```\n```python\nx = 1\n```"
    assert extract_policy(response) == "x = 1"


def test_last_python_block_wins():
    response = "Example:\n```python\nold = 1\n```\nPOLICY:\n```python\nnew = 2\n```"
    assert extract_policy(response) == "new = 2"


def test_indentation_inside_a_block_is_preserved():
    response = "```python\nfor i in range(3):\n    place_entity(i)\n```"
    assert extract_policy(response) == "for i in range(3):\n    place_entity(i)"


def test_crlf_response_is_handled():
    assert extract_policy("```python\r\nx = 1\r\n```") == "x = 1"


def test_unterminated_fence_from_a_truncated_response():
    """A response cut off at max_tokens still yields its code."""
    assert extract_policy("```python\nplace_entity('drill')\nfuel(") == (
        "place_entity('drill')\nfuel("
    )


def test_broken_code_inside_a_fence_is_returned_verbatim():
    """The environment reports the SyntaxError; extraction does not judge code."""
    assert extract_policy("```python\ndef broken(:\n```") == "def broken(:"


def test_bare_python_without_fences():
    assert extract_policy("place_entity('drill')\nfuel_entity('drill')") == (
        "place_entity('drill')\nfuel_entity('drill')"
    )


@pytest.mark.parametrize(
    "response",
    [
        "",
        "   \n  ",
        "I will place a drill on the iron ore patch, then fuel it.",
        "Sorry, I cannot help with that request!",
    ],
)
def test_malformed_responses_yield_empty_code(response):
    assert extract_policy(response) == ""


def test_empty_fenced_block_yields_empty_code():
    assert extract_policy("```python\n```") == ""


def test_extraction_is_deterministic():
    response = "```python\nx = 1\n```"
    assert extract_policy(response) == extract_policy(response)

"""Tests for verification.

Required coverage (build-plan section 21, Verifier): DONE, NOT DONE, reasons.
The prompt cases pin decisions.md D6: the verifier never sees a bare snapshot.
"""

import pytest

from factorio_maxxing.goal import Goal
from factorio_maxxing.llm import StubLLMClient
from factorio_maxxing.verifier import (
    VERIFICATION_WINDOW,
    LLMVerifier,
    StubVerifier,
    VerificationResult,
    Verifier,
    build_verification_prompt,
    parse_verdict,
)

GOAL = Goal(description="Produce iron plates")
OBSERVATION = "INVENTORY\n  iron-plate 12"


def test_verification_window_is_more_than_one_tick():
    assert VERIFICATION_WINDOW > 1


def test_result_carries_a_verdict_and_a_reason():
    result = VerificationResult(done=True, reason="12 iron plates in inventory")
    assert result.done is True
    assert result.reason == "12 iron plates in inventory"


def test_parse_done():
    result = parse_verdict("DONE: 12 iron plates are in the inventory.")
    assert result.done is True
    assert result.reason == "12 iron plates are in the inventory."


def test_parse_not_done():
    result = parse_verdict("NOT DONE - no furnace is producing plates yet.")
    assert result.done is False
    assert result.reason == "no furnace is producing plates yet."


@pytest.mark.parametrize("verdict", ["NOT DONE", "NOT_DONE", "not done", "NotDone"])
def test_not_done_spellings_are_never_read_as_done(verdict):
    """'NOT DONE' contains 'DONE'; the negative must win."""
    assert parse_verdict(f"{verdict}\nThe drill has no fuel.").done is False


def test_multiline_verdict_keeps_the_reason():
    result = parse_verdict("DONE\nThe furnace has produced 12 plates.")
    assert result.done is True
    assert result.reason == "The furnace has produced 12 plates."


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_response_is_not_done(text):
    result = parse_verdict(text)
    assert result.done is False
    assert result.reason == "verifier returned no response"


def test_unreadable_response_is_not_done_and_keeps_the_text():
    """A verifier that cannot state a verdict must never read as claiming success."""
    result = parse_verdict("I am not sure what to make of this.")
    assert result.done is False
    assert result.reason == "I am not sure what to make of this."


def test_verdict_without_a_reason_says_so():
    assert parse_verdict("DONE").reason == "no reason given"


def test_prompt_contains_goal_and_current_observation():
    prompt = build_verification_prompt(GOAL, OBSERVATION)
    assert "Produce iron plates" in prompt
    assert "  INVENTORY" in prompt


def test_prompt_includes_the_observation_window():
    """D6: never a bare snapshot."""
    window = ["FLOWS\n  output: iron-plate 2", "FLOWS\n  output: iron-plate 6"]
    prompt = build_verification_prompt(GOAL, OBSERVATION, window)
    assert "RECENT OBSERVATIONS" in prompt
    assert "observation -2:" in prompt
    assert "observation -1:" in prompt
    assert prompt.index("iron-plate 2") < prompt.index("iron-plate 6")


def test_prompt_omits_the_window_section_when_empty():
    assert "RECENT OBSERVATIONS" not in build_verification_prompt(GOAL, OBSERVATION)


def test_prompt_does_not_invite_help_or_next_steps():
    """The verifier must not request assistance or plan; that is not its job."""
    prompt = build_verification_prompt(GOAL, OBSERVATION)
    assert "do not offer help" in prompt.lower()
    assert "do not suggest next steps" in prompt.lower()


def test_llm_verifier_returns_the_parsed_verdict():
    verifier = LLMVerifier(StubLLMClient(["DONE: 12 plates produced."]))
    result = verifier.check(GOAL, OBSERVATION)
    assert result.done is True
    assert result.reason == "12 plates produced."


def test_llm_verifier_sends_goal_and_observation_to_its_model():
    client = StubLLMClient(["NOT DONE: nothing yet."])
    LLMVerifier(client).check(GOAL, OBSERVATION, ["FLOWS\n  output: iron-ore 3"])
    assert "Produce iron plates" in client.prompts[0]
    assert "iron-ore 3" in client.prompts[0]


def test_llm_verifier_retains_usage_for_the_recorder():
    verifier = LLMVerifier(StubLLMClient(["DONE: yes."], model="stub-verifier"))
    assert verifier.last_response is None
    verifier.check(GOAL, OBSERVATION)
    assert verifier.last_response is not None
    assert verifier.last_response.model == "stub-verifier"
    assert verifier.last_response.input_tokens > 0


def test_verifier_uses_a_model_independent_of_the_policy():
    """D5: policy and verifier models stay independently configurable."""
    policy = StubLLMClient(["```python\nx = 1\n```"], model="stub-policy")
    verifier = LLMVerifier(StubLLMClient(["DONE: done."], model="stub-verifier"))
    verifier.check(GOAL, OBSERVATION)
    assert verifier.last_response.model == "stub-verifier"
    assert policy.call_count == 0


def test_stub_verifier_satisfies_the_protocol():
    assert isinstance(StubVerifier([VerificationResult(False, "no")]), Verifier)


def test_stub_verifier_returns_results_in_call_order():
    verifier = StubVerifier(
        [VerificationResult(False, "not yet"), VerificationResult(True, "done")]
    )
    assert verifier.check(GOAL, OBSERVATION).done is False
    assert verifier.check(GOAL, OBSERVATION).done is True


def test_stub_verifier_repeats_its_final_result():
    verifier = StubVerifier([VerificationResult(False, "not yet")])
    assert [verifier.check(GOAL, OBSERVATION).done for _ in range(3)] == [False] * 3
    assert verifier.call_count == 3


def test_stub_verifier_requires_at_least_one_result():
    with pytest.raises(ValueError, match="at least one result"):
        StubVerifier([])

"""Tests for context assembly.

Required coverage (build-plan section 21, Context): assembles goal, observation,
history, guidance and errors, and respects the window. The guidance cases also pin
decisions.md D12: guidance accumulates and earlier hints survive later ones.
"""

from factorio_maxxing.context import HISTORY_WINDOW, build
from factorio_maxxing.goal import Goal

GOAL = Goal(description="Build a working iron mining setup", max_steps=8)
OBSERVATION = "INVENTORY\n  coal 3"


def headers(prompt: str) -> list[str]:
    return [line for line in prompt.splitlines() if line and not line.startswith(" ")]


def test_history_window_default_matches_fle():
    assert HISTORY_WINDOW == 16


def test_minimal_prompt_has_goal_observation_and_instructions_only():
    assert headers(build(GOAL, OBSERVATION)) == [
        "GOAL",
        "CURRENT OBSERVATION",
        "INSTRUCTIONS",
    ]


def test_goal_description_and_step_budget_are_present():
    prompt = build(GOAL, OBSERVATION)
    assert "Build a working iron mining setup" in prompt
    assert "step budget: 8" in prompt


def test_goal_notes_are_included_when_present():
    goal = Goal(description="Produce iron plates", notes="ore is north")
    assert "notes: ore is north" in build(goal, OBSERVATION)


def test_notes_are_omitted_when_absent():
    assert "notes:" not in build(GOAL, OBSERVATION)


def test_rendered_observation_is_included_verbatim():
    prompt = build(GOAL, "INVENTORY\n  coal 3\nENTITIES\n  (none)")
    assert "  INVENTORY" in prompt
    assert "    coal 3" in prompt


def test_full_section_order():
    prompt = build(
        GOAL,
        OBSERVATION,
        history=[("x = 1", "obs")],
        guidance=["fuel it"],
        errors=["NameError"],
    )
    assert headers(prompt) == [
        "GOAL",
        "RECENT HISTORY",
        "EXECUTION ERRORS",
        "CURRENT OBSERVATION",
        "HUMAN GUIDANCE",
        "INSTRUCTIONS",
    ]


def test_guidance_sits_immediately_before_the_instructions():
    """Guidance is the research subject: it must be prominent, not buried."""
    prompt = build(GOAL, OBSERVATION, guidance=["fuel it"])
    assert (
        headers(prompt).index("HUMAN GUIDANCE")
        == headers(prompt).index("INSTRUCTIONS") - 1
    )


def test_history_is_numbered_from_zero_like_the_trajectory():
    prompt = build(GOAL, OBSERVATION, history=[("a = 1", "obs a"), ("b = 2", "obs b")])
    assert "step 0 policy:" in prompt
    assert "step 1 policy:" in prompt


def test_history_shows_policy_and_result_for_each_step():
    prompt = build(GOAL, OBSERVATION, history=[("place_entity()", "INVENTORY\n  coal 3")])
    assert "    place_entity()" in prompt
    assert "step 0 result:" in prompt


def test_empty_policy_in_history_is_labelled():
    """An empty policy is legitimate recorded data (D17), not a missing field."""
    assert "(no code)" in build(GOAL, OBSERVATION, history=[("", "obs")])


def test_history_respects_the_default_window():
    history = [(f"step_{i}()", f"obs {i}") for i in range(20)]
    prompt = build(GOAL, OBSERVATION, history=history)
    assert "step_3()" not in prompt
    assert "step_4()" in prompt
    assert "step_19()" in prompt


def test_truncated_history_reports_what_was_dropped():
    history = [(f"step_{i}()", f"obs {i}") for i in range(20)]
    assert "RECENT HISTORY (last 16 of 20 steps)" in build(
        GOAL, OBSERVATION, history=history
    )


def test_untruncated_history_has_a_plain_header():
    prompt = build(GOAL, OBSERVATION, history=[("x = 1", "obs")])
    assert "RECENT HISTORY\n" in prompt


def test_window_is_configurable():
    history = [(f"step_{i}()", f"obs {i}") for i in range(5)]
    prompt = build(GOAL, OBSERVATION, history=history, history_length=2)
    assert "step_2()" not in prompt
    assert "step_3()" in prompt
    assert "step 3 policy:" in prompt


def test_guidance_accumulates_in_order_most_recent_last():
    prompt = build(GOAL, OBSERVATION, guidance=["first hint", "second hint"])
    assert "1. first hint" in prompt
    assert "2. second hint" in prompt
    assert prompt.index("first hint") < prompt.index("second hint")


def test_earlier_guidance_survives_a_later_hint():
    """D12: guidance is a list, not a slot; a later hint never erases an earlier one."""
    prompt = build(GOAL, OBSERVATION, guidance=["place the drill on ore", "then fuel it"])
    assert "place the drill on ore" in prompt
    assert "then fuel it" in prompt


def test_guidance_is_rendered_verbatim():
    hint = "Use place_entity_next_to(); don't compute offsets by hand."
    assert hint in build(GOAL, OBSERVATION, guidance=[hint])


def test_multiline_guidance_is_preserved():
    prompt = build(GOAL, OBSERVATION, guidance=["line one\nline two"])
    assert "1. line one" in prompt
    assert "   line two" in prompt


def test_execution_errors_are_listed():
    prompt = build(GOAL, OBSERVATION, errors=["NameError: place_entity", "SyntaxError"])
    assert "  NameError: place_entity" in prompt
    assert "  SyntaxError" in prompt


def test_build_is_deterministic():
    args = (GOAL, OBSERVATION)
    kwargs = {"history": [("x = 1", "obs")], "guidance": ["hint"], "errors": ["err"]}
    assert build(*args, **kwargs) == build(*args, **kwargs)

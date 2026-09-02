"""Tests for the human backends.

Required coverage (build-plan section 21, Human): NoHuman, InteractiveHuman,
ScriptedHuman, deterministic replay, sequence-order matching when stuck steps differ,
exhausted hint list degrades to NoHuman.
"""

import logging

from factorio_maxxing.goal import Goal
from factorio_maxxing.human import (
    Hint,
    HumanProtocol,
    InteractiveHuman,
    NoHuman,
    ScriptedHuman,
)

GOAL = Goal(description="Produce iron plates")
OBSERVATION = "INVENTORY\n  coal 3"
REASON = "3 consecutive non-DONE verifications"


def scripted_replies(lines):
    """An input_fn yielding the given lines, then EOF."""
    remaining = list(lines)

    def read() -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return read


def test_backends_satisfy_the_protocol():
    assert isinstance(NoHuman(), HumanProtocol)
    assert isinstance(InteractiveHuman(), HumanProtocol)
    assert isinstance(ScriptedHuman([]), HumanProtocol)


def test_no_human_never_assists():
    human = NoHuman()
    assert human.ask(GOAL, OBSERVATION, REASON) is None
    assert human.ask(GOAL, OBSERVATION, REASON) is None
    assert human.call_count == 2


def test_interactive_human_returns_typed_guidance():
    human = InteractiveHuman(input_fn=scripted_replies(["fuel the drill", ""]))
    assert human.ask(GOAL, OBSERVATION, REASON) == "fuel the drill"


def test_interactive_human_returns_text_verbatim():
    hint = "Use place_entity_next_to(); DON'T compute offsets by hand.  "
    human = InteractiveHuman(input_fn=scripted_replies([hint, ""]))
    assert human.ask(GOAL, OBSERVATION, REASON) == hint


def test_interactive_human_accepts_multiline_guidance():
    human = InteractiveHuman(input_fn=scripted_replies(["line one", "line two", ""]))
    assert human.ask(GOAL, OBSERVATION, REASON) == "line one\nline two"


def test_interactive_human_declines_on_a_blank_line():
    human = InteractiveHuman(input_fn=scripted_replies([""]))
    assert human.ask(GOAL, OBSERVATION, REASON) is None


def test_interactive_human_survives_end_of_input():
    """An unattended run must not crash when stdin closes."""
    human = InteractiveHuman(input_fn=scripted_replies([]))
    assert human.ask(GOAL, OBSERVATION, REASON) is None


def test_interactive_human_shows_the_goal_reason_and_observation():
    printed: list[str] = []
    human = InteractiveHuman(input_fn=scripted_replies([""]), output_fn=printed.append)
    human.ask(GOAL, OBSERVATION, REASON)
    shown = "\n".join(printed)
    assert "Produce iron plates" in shown
    assert REASON in shown
    assert "coal 3" in shown


def test_scripted_human_replays_in_sequence_order():
    human = ScriptedHuman(["first", "second"])
    assert human.ask(GOAL, OBSERVATION, REASON) == "first"
    assert human.ask(GOAL, OBSERVATION, REASON) == "second"


def test_scripted_replay_is_deterministic():
    first, second = ScriptedHuman(["a", "b"]), ScriptedHuman(["a", "b"])
    assert [first.ask(GOAL, OBSERVATION, REASON) for _ in range(2)] == [
        second.ask(GOAL, "a different observation", "a different reason")
        for _ in range(2)
    ]


def test_matching_is_by_sequence_order_not_step_index():
    """D8: harness A got stuck at 7/15/22, harness B at 9/20. B must still get help."""
    hints = [
        Hint("place the drill on ore", original_step=7),
        Hint("fuel it with coal", original_step=15),
        Hint("check the drop position", original_step=22),
    ]
    human = ScriptedHuman(hints)
    assert human.ask(GOAL, OBSERVATION, REASON) == "place the drill on ore"
    assert human.ask(GOAL, OBSERVATION, REASON) == "fuel it with coal"
    assert human.used == 2


def test_original_step_is_retained_for_analysis_not_matching():
    human = ScriptedHuman([Hint("fuel it", original_step=15)])
    human.ask(GOAL, OBSERVATION, REASON)
    assert human.last_hint.original_step == 15
    assert human.last_hint.text == "fuel it"


def test_intervention_index_is_reported_for_the_recorder():
    human = ScriptedHuman(["a", "b"])
    assert human.next_intervention_index == 0
    human.ask(GOAL, OBSERVATION, REASON)
    assert human.next_intervention_index == 1


def test_plain_strings_are_accepted_as_hints():
    human = ScriptedHuman(["just text"])
    human.ask(GOAL, OBSERVATION, REASON)
    assert human.last_hint == Hint("just text", original_step=None)


def test_exhausted_hints_degrade_to_no_human():
    human = ScriptedHuman(["only one"])
    assert human.ask(GOAL, OBSERVATION, REASON) == "only one"
    assert human.ask(GOAL, OBSERVATION, REASON) is None
    assert human.ask(GOAL, OBSERVATION, REASON) is None
    assert human.exhausted_calls == 2
    assert human.last_hint is None


def test_exhaustion_is_logged(caplog):
    human = ScriptedHuman([])
    with caplog.at_level(logging.INFO, logger="factorio_maxxing.human"):
        human.ask(GOAL, OBSERVATION, REASON)
    assert "exhausted" in caplog.text
    assert "NoHuman" in caplog.text


def test_underuse_is_logged(caplog):
    """'harness B used 2 of harness A's 3 hints' is itself a result."""
    human = ScriptedHuman(["a", "b", "c"])
    human.ask(GOAL, OBSERVATION, REASON)
    human.ask(GOAL, OBSERVATION, REASON)
    with caplog.at_level(logging.INFO, logger="factorio_maxxing.human"):
        human.log_usage()
    assert "used 2 of 3 scripted hints" in caplog.text
    assert "1 scripted hint(s) were never needed" in caplog.text
    assert human.unused == 1


def test_usage_report_counts_unanswered_requests():
    human = ScriptedHuman(["a"])
    human.ask(GOAL, OBSERVATION, REASON)
    human.ask(GOAL, OBSERVATION, REASON)
    assert (
        human.usage_report()
        == "used 1 of 1 scripted hints (1 request(s) went unanswered)"
    )


def test_no_backend_receives_the_environment():
    """The human provides text only: ask() takes strings and returns a string."""
    for human in (
        NoHuman(),
        InteractiveHuman(input_fn=scripted_replies([""])),
        ScriptedHuman(["a"]),
    ):
        assert human.ask(GOAL, OBSERVATION, REASON) in (None, "a")

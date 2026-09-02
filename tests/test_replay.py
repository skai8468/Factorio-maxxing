"""Replay regression tests.

Build-plan section 19 item 13. These exercise the D8 safeguard end to end: an
intervention set recorded against one harness configuration is replayed against a
differently shaped one, whose stuck steps fall elsewhere. Step-keyed replay would fail
silently here; sequence-order replay must not.
"""

import logging

from factorio_maxxing.envs import MockFactorioEnv, MockFrame
from factorio_maxxing.goal import Goal
from factorio_maxxing.human import Hint, ScriptedHuman
from factorio_maxxing.llm import StubLLMClient
from factorio_maxxing.loop import run_goal
from factorio_maxxing.stuck import ConsecutiveNonDoneDetector
from factorio_maxxing.trajectory import TrajectoryRecorder, read_trajectory
from factorio_maxxing.verifier import StubVerifier, VerificationResult

GOAL = Goal(description="Research automation", max_steps=6)
NOT_DONE = VerificationResult(False, "no science packs yet")
POLICY = "```python\nplace_entity('lab')\n```"

HINTS_FROM_RUN_A = [
    "Copper plates come before gears; smelt copper first.",
    "An assembler needs power before it will run.",
    "Ten science packs are needed, not one.",
]


def make_env(steps: int = 8):
    frames = [
        MockFrame(observation={"inventory": {"iron-plate": i + 1}}) for i in range(steps)
    ]
    return MockFactorioEnv(reset_observation={"inventory": {}}, frames=frames)


def run(path, human, *, threshold: int, goal: Goal = GOAL):
    """Run one goal, returning the result and its trajectory records."""
    with TrajectoryRecorder(path, run_id="run") as recorder:
        result = run_goal(
            goal,
            make_env(),
            StubLLMClient([POLICY]),
            StubVerifier([NOT_DONE]),
            human,
            ConsecutiveNonDoneDetector(threshold),
            recorder,
            max_interventions=9,
        )
    return result, read_trajectory(path)


def hints_from(records) -> list[Hint]:
    """Recover a replayable intervention set from a recorded trajectory.

    Sequence order is the record order; ``original_step`` is carried for analysis.
    """
    return [
        Hint(text=r["text"], original_step=r["step"])
        for r in records
        if r["type"] == "intervention" and r["text"] is not None
    ]


def test_run_a_records_its_interventions(tmp_path):
    _, records = run(tmp_path / "a.jsonl", ScriptedHuman(HINTS_FROM_RUN_A), threshold=2)
    interventions = [r for r in records if r["type"] == "intervention"]
    assert [r["step"] for r in interventions] == [1, 3, 5]
    assert [r["intervention_index"] for r in interventions] == [0, 1, 2]
    assert [r["text"] for r in interventions] == HINTS_FROM_RUN_A


def test_a_recorded_run_is_replayable(tmp_path):
    _, records_a = run(tmp_path / "a.jsonl", ScriptedHuman(HINTS_FROM_RUN_A), threshold=2)
    hints = hints_from(records_a)
    assert [h.text for h in hints] == HINTS_FROM_RUN_A
    assert [h.original_step for h in hints] == [1, 3, 5]


def test_replay_matches_by_sequence_order_when_stuck_steps_differ(tmp_path):
    """D8: harness A got stuck at 1/3/5, harness B at 2/5. B must still get help."""
    _, records_a = run(tmp_path / "a.jsonl", ScriptedHuman(HINTS_FROM_RUN_A), threshold=2)
    hints = hints_from(records_a)

    _, records_b = run(tmp_path / "b.jsonl", ScriptedHuman(hints), threshold=3)
    interventions_b = [r for r in records_b if r["type"] == "intervention"]

    assert [r["step"] for r in interventions_b] == [2, 5]
    assert [r["text"] for r in interventions_b] == HINTS_FROM_RUN_A[:2]
    assert [r["intervention_index"] for r in interventions_b] == [0, 1]


def test_original_step_is_preserved_and_differs_from_the_replay_step(tmp_path):
    """Both numbers are recorded: index matches, original_step explains when."""
    _, records_a = run(tmp_path / "a.jsonl", ScriptedHuman(HINTS_FROM_RUN_A), threshold=2)
    _, records_b = run(
        tmp_path / "b.jsonl", ScriptedHuman(hints_from(records_a)), threshold=3
    )
    first = next(r for r in records_b if r["type"] == "intervention")
    assert first["step"] == 2
    assert first["original_step"] == 1


def test_a_harness_that_needs_less_help_leaves_hints_unused(tmp_path):
    """'Harness B used 2 of harness A's 3 hints' is itself a result."""
    _, records_a = run(tmp_path / "a.jsonl", ScriptedHuman(HINTS_FROM_RUN_A), threshold=2)
    human_b = ScriptedHuman(hints_from(records_a))
    result_b, _ = run(tmp_path / "b.jsonl", human_b, threshold=3)

    assert result_b.interventions == 2
    assert human_b.used == 2
    assert human_b.unused == 1
    assert (
        human_b.usage_report()
        == "used 2 of 3 scripted hints (0 request(s) went unanswered)"
    )


def test_underuse_is_reported_in_the_log(tmp_path, caplog):
    _, records_a = run(tmp_path / "a.jsonl", ScriptedHuman(HINTS_FROM_RUN_A), threshold=2)
    human_b = ScriptedHuman(hints_from(records_a))
    run(tmp_path / "b.jsonl", human_b, threshold=3)

    with caplog.at_level(logging.INFO, logger="factorio_maxxing.human"):
        human_b.log_usage()
    assert "used 2 of 3 scripted hints" in caplog.text
    assert "1 scripted hint(s) were never needed" in caplog.text


def test_a_harness_that_needs_more_help_exhausts_the_set(tmp_path):
    """On exhaustion the backend degrades to NoHuman rather than repeating a hint."""
    human_b = ScriptedHuman(HINTS_FROM_RUN_A[:1])
    result_b, records_b = run(
        tmp_path / "b.jsonl",
        human_b,
        threshold=2,
        goal=Goal(description="Research automation", max_steps=6),
    )
    interventions = [r for r in records_b if r["type"] == "intervention"]

    assert [r["step"] for r in interventions] == [1, 3, 5]
    assert [r["text"] for r in interventions] == [HINTS_FROM_RUN_A[0], None, None]
    assert result_b.interventions == 1
    assert human_b.exhausted_calls == 2


def test_replay_is_deterministic(tmp_path):
    """The same hint set against the same harness produces the same trajectory."""
    _, first = run(tmp_path / "first.jsonl", ScriptedHuman(HINTS_FROM_RUN_A), threshold=2)
    _, second = run(
        tmp_path / "second.jsonl", ScriptedHuman(HINTS_FROM_RUN_A), threshold=2
    )
    assert first == second


def test_replayed_text_is_identical_to_what_was_recorded(tmp_path):
    """Intervention text is the source of truth and survives a round trip verbatim."""
    text = "  Copper plates come first.\nDo not skip the smelter.  "
    _, records_a = run(tmp_path / "a.jsonl", ScriptedHuman([text]), threshold=2)
    _, records_b = run(
        tmp_path / "b.jsonl", ScriptedHuman(hints_from(records_a)), threshold=2
    )
    replayed = next(r for r in records_b if r["type"] == "intervention")
    assert replayed["text"] == text

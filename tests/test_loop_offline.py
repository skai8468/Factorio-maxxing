"""Offline end-to-end regression tests.

Build-plan section 19 item 13. These run the whole M0 path - goal, policy, Python,
mock environment, observation, verification - and the M1 path on top of it, asserting
against the trajectory rather than against internal state. Everything here is offline:
no API key, no Docker, no Factorio.
"""

import pytest

from factorio_maxxing.envs import MockFactorioEnv, MockFrame
from factorio_maxxing.goal import Goal, open_ended_goal
from factorio_maxxing.human import NoHuman, ScriptedHuman
from factorio_maxxing.llm import StubLLMClient
from factorio_maxxing.loop import run_goal
from factorio_maxxing.stuck import (
    ConsecutiveNonDoneDetector,
    default_detector,
    open_ended_detector,
)
from factorio_maxxing.trajectory import TrajectoryRecorder, read_trajectory
from factorio_maxxing.verifier import LLMVerifier, StubVerifier, VerificationResult

GOAL = Goal(description="Build a working iron mining setup", max_steps=4)
NOT_DONE = VerificationResult(False, "no drill is producing yet")
DONE = VerificationResult(True, "the drill is working and ore is accumulating")

POLICIES = [
    "PLANNING: place a drill.\n```python\nplace_entity('burner-mining-drill')\n```",
    "PLANNING: fuel it.\n```python\ninsert_item('coal')\n```",
    "PLANNING: check it.\n```python\nprint(get_entities())\n```",
]


def mining_env(steps: int = 6):
    """A scripted run in which ore accumulates and the drill starts working."""
    frames = [
        MockFrame(
            observation={
                "inventory": {"iron-ore": i + 1},
                "entities": [
                    {
                        "name": "burner-mining-drill",
                        "position": {"x": 12, "y": -3},
                        "status": "WORKING" if i else "NO_FUEL",
                    }
                ],
                "flows": {"output": {"iron-ore": float(i + 1)}},
                "raw_text": f"step {i} executed",
            },
            reward=float(i),
        )
        for i in range(steps)
    ]
    return MockFactorioEnv(reset_observation={"inventory": {}}, frames=frames)


def run(path, *, verifier=None, human=None, detector=None, goal=GOAL, **kwargs):
    with TrajectoryRecorder(path, run_id="run-1") as recorder:
        result = run_goal(
            goal,
            mining_env(),
            StubLLMClient(POLICIES),
            verifier or StubVerifier([NOT_DONE, NOT_DONE, DONE]),
            human or NoHuman(),
            detector or default_detector(3),
            recorder,
            **kwargs,
        )
    return result, read_trajectory(path)


def test_m0_goal_completes_end_to_end(tmp_path):
    result, records = run(tmp_path / "run.jsonl")
    assert result.completed is True
    assert result.steps_used == 3
    assert result.reason == "the drill is working and ore is accumulating"
    assert result.interventions == 0
    assert result.trajectory_path == tmp_path / "run.jsonl"


def test_trajectory_record_sequence_for_a_completed_goal(tmp_path):
    _, records = run(tmp_path / "run.jsonl")
    assert [(r["type"], r["step"]) for r in records] == [
        ("step", 0),
        ("llm_call", 0),
        ("verification", 0),
        ("step", 1),
        ("llm_call", 1),
        ("verification", 1),
        ("step", 2),
        ("llm_call", 2),
        ("verification", 2),
    ]


def test_the_policy_reaching_the_environment_is_the_extracted_code(tmp_path):
    _, records = run(tmp_path / "run.jsonl")
    assert [r["policy"] for r in records if r["type"] == "step"] == [
        "place_entity('burner-mining-drill')",
        "insert_item('coal')",
        "print(get_entities())",
    ]


def test_observations_are_recorded_objectively(tmp_path):
    """The recorder stores environment state, not the rendering shown to the model."""
    _, records = run(tmp_path / "run.jsonl")
    first = next(r for r in records if r["type"] == "step")
    assert first["observation"]["inventory"] == {"iron-ore": 1}
    assert first["observation"]["entities"][0]["status"] == "NO_FUEL"
    assert first["reward"] == 0.0


def test_the_verifier_verdict_is_recorded_alongside_the_run(tmp_path):
    _, records = run(tmp_path / "run.jsonl")
    verdicts = [(r["done"], r["reason"]) for r in records if r["type"] == "verification"]
    assert verdicts == [
        (False, "no drill is producing yet"),
        (False, "no drill is producing yet"),
        (True, "the drill is working and ore is accumulating"),
    ]


def test_a_failed_goal_still_records_a_complete_trajectory(tmp_path):
    result, records = run(tmp_path / "run.jsonl", verifier=StubVerifier([NOT_DONE]))
    assert result.completed is False
    assert result.reason == "max_steps reached"
    assert len([r for r in records if r["type"] == "step"]) == 4
    assert len([r for r in records if r["type"] == "verification"]) == 4


def test_sparse_verification_records_no_skipped_verdicts(tmp_path):
    _, records = run(
        tmp_path / "run.jsonl",
        verifier=StubVerifier([NOT_DONE]),
        verification_interval=2,
    )
    assert [r["step"] for r in records if r["type"] == "verification"] == [0, 2]
    assert len([r for r in records if r["type"] == "step"]) == 4


def test_cost_is_derivable_from_the_trajectory_alone(tmp_path):
    """D22: one sum over llm_call records, whatever the roles."""
    _, records = run(
        tmp_path / "run.jsonl",
        verifier=LLMVerifier(
            StubLLMClient(["NOT DONE: nothing yet."], model="stub-verifier")
        ),
    )
    calls = [r for r in records if r["type"] == "llm_call"]
    assert {r["role"] for r in calls} == {"policy", "verifier"}
    assert sum(r["input_tokens"] for r in calls) > 0
    assert all("cost" not in r for r in calls)


def test_two_identical_runs_produce_identical_trajectories(tmp_path):
    _, first = run(tmp_path / "a.jsonl")
    _, second = run(tmp_path / "b.jsonl")
    assert first == second


def test_m1_path_end_to_end_with_a_scripted_human(tmp_path):
    result, records = run(
        tmp_path / "run.jsonl",
        goal=Goal(description="Build a working iron mining setup", max_steps=4),
        verifier=StubVerifier([NOT_DONE]),
        human=ScriptedHuman(["Fuel the drill with coal before checking its status."]),
        detector=ConsecutiveNonDoneDetector(2),
    )
    interventions = [r for r in records if r["type"] == "intervention"]
    assert result.interventions == 1
    assert interventions[0]["step"] == 1
    assert interventions[0]["text"] == (
        "Fuel the drill with coal before checking its status."
    )
    assert interventions[0]["stuck_reason"] == "2 consecutive non-DONE verifications"


def test_the_autonomous_baseline_records_where_help_would_have_been_asked(tmp_path):
    """NoHuman vs InteractiveHuman over one goal set is the M1 result."""
    result, records = run(
        tmp_path / "run.jsonl",
        verifier=StubVerifier([NOT_DONE]),
        detector=ConsecutiveNonDoneDetector(2),
    )
    requests = [r for r in records if r["type"] == "intervention"]
    assert result.interventions == 0
    assert [r["step"] for r in requests] == [1, 3]
    assert all(r["text"] is None for r in requests)


def run_open_ended(path, *, max_steps=6, verifier=None, human=None, detector=None, **kw):
    with TrajectoryRecorder(path, run_id="run-1") as recorder:
        result = run_goal(
            open_ended_goal(max_steps),
            mining_env(),
            StubLLMClient(POLICIES),
            verifier,
            human or NoHuman(),
            detector or open_ended_detector(3, backstop_steps=20),
            recorder,
            **kw,
        )
    return result, read_trajectory(path)


def test_an_open_ended_run_ends_only_at_its_step_cap(tmp_path):
    result, records = run_open_ended(tmp_path / "run.jsonl", max_steps=6)
    assert result.completed is False
    assert result.steps_used == 6
    assert result.reason == "max_steps reached"
    assert [r["step"] for r in records if r["type"] == "step"] == list(range(6))


def test_an_open_ended_run_never_consults_the_verifier(tmp_path):
    """D67: a verifier that would say DONE cannot end an open-ended run early."""
    verifier = StubVerifier([DONE])
    result, records = run_open_ended(tmp_path / "run.jsonl", verifier=verifier)
    assert verifier.call_count == 0
    assert result.steps_used == 6
    assert not [r for r in records if r["type"] == "verification"]
    assert not [r for r in records if r.get("role") == "verifier"]


def test_a_goal_that_can_complete_still_needs_a_verifier(tmp_path):
    with pytest.raises(ValueError, match="needs a verifier"):
        with TrajectoryRecorder(tmp_path / "run.jsonl", run_id="run-1") as recorder:
            run_goal(
                GOAL,
                mining_env(),
                StubLLMClient(POLICIES),
                None,
                NoHuman(),
                default_detector(3),
                recorder,
            )


def test_interventions_never_abort_an_open_ended_run(tmp_path):
    """D67: a guided run lasts as long as its unguided twin."""
    result, records = run_open_ended(
        tmp_path / "run.jsonl",
        human=ScriptedHuman([f"hint {i}" for i in range(10)]),
        detector=open_ended_detector(3, backstop_steps=1),
        max_interventions=1,
    )
    assert result.steps_used == 6
    assert result.reason == "max_steps reached"
    assert result.interventions == 6


def test_the_backstop_asks_after_each_quiet_stretch(tmp_path):
    _, records = run_open_ended(
        tmp_path / "run.jsonl", detector=open_ended_detector(3, backstop_steps=2)
    )
    requests = [r for r in records if r["type"] == "intervention"]
    assert [r["step"] for r in requests] == [1, 3, 5]
    assert all(
        r["stuck_reason"] == "2 steps without a request for help" for r in requests
    )
    assert all(r["text"] is None for r in requests)

"""Tests for the control loop.

Required coverage (build-plan section 21): the Loop row - normal completion, failure
after max steps, stuck to human to continue, NoHuman, ScriptedHuman, verification
interval, immediate termination on DONE with no extra LLM call - and the Intervention
lifecycle row.
"""

import pytest

from factorio_maxxing.envs import MockFactorioEnv, MockFrame
from factorio_maxxing.goal import Goal
from factorio_maxxing.human import Hint, NoHuman, ScriptedHuman
from factorio_maxxing.llm import StubLLMClient
from factorio_maxxing.loop import run_goal
from factorio_maxxing.stuck import ConsecutiveNonDoneDetector, default_detector
from factorio_maxxing.trajectory import TrajectoryRecorder, read_trajectory
from factorio_maxxing.verifier import (
    LLMVerifier,
    StubVerifier,
    VerificationResult,
)

POLICY = "```python\nplace_entity()\n```"
NOT_DONE = VerificationResult(False, "not yet")
DONE = VerificationResult(True, "12 iron plates in inventory")


class AlwaysStuck:
    def is_stuck(self, history, verifications, flows, errors):
        return True, "always stuck"


class NeverStuck:
    def is_stuck(self, history, verifications, flows, errors):
        return False, ""


def make_env(steps: int = 8, observation: dict | None = None, terminated_at=None):
    frames = [
        MockFrame(
            observation=observation or {"inventory": {"iron-plate": i + 1}},
            terminated=(terminated_at == i),
        )
        for i in range(steps)
    ]
    return MockFactorioEnv(reset_observation={"inventory": {}}, frames=frames)


@pytest.fixture
def recorder(tmp_path):
    with TrajectoryRecorder(tmp_path / "run.jsonl", run_id="run-1") as rec:
        yield rec


def run(
    recorder,
    *,
    goal=None,
    env=None,
    client=None,
    verifier=None,
    human=None,
    detector=None,
    **kwargs,
):
    return run_goal(
        goal or Goal(description="Produce iron plates", max_steps=4),
        env or make_env(),
        client or StubLLMClient([POLICY]),
        verifier or StubVerifier([NOT_DONE]),
        human or NoHuman(),
        detector or NeverStuck(),
        recorder,
        **kwargs,
    )


def records_of(recorder, record_type):
    return [r for r in read_trajectory(recorder.path) if r["type"] == record_type]


def test_normal_completion(recorder):
    result = run(recorder, verifier=StubVerifier([DONE]))
    assert result.completed is True
    assert result.steps_used == 1
    assert result.reason == "12 iron plates in inventory"
    assert result.interventions == 0


def test_result_carries_the_goal_and_trajectory_path(recorder):
    goal = Goal(description="Produce iron plates", max_steps=2)
    result = run(recorder, goal=goal, verifier=StubVerifier([DONE]))
    assert result.goal is goal
    assert result.trajectory_path == recorder.path


def test_termination_on_done_makes_no_further_policy_call(recorder):
    """The loop must stop immediately on DONE, with no extra LLM call."""
    client = StubLLMClient([POLICY])
    result = run(
        recorder,
        client=client,
        verifier=StubVerifier([NOT_DONE, NOT_DONE, DONE]),
    )
    assert result.steps_used == 3
    assert client.call_count == 3


def test_failure_after_max_steps(recorder):
    goal = Goal(description="Produce iron plates", max_steps=4)
    client = StubLLMClient([POLICY])
    result = run(recorder, goal=goal, client=client)
    assert result.completed is False
    assert result.steps_used == 4
    assert result.reason == "max_steps reached"
    assert client.call_count == 4


def test_environment_termination_stops_the_run(recorder):
    result = run(recorder, env=make_env(terminated_at=1))
    assert result.completed is False
    assert result.steps_used == 2
    assert result.reason == "environment terminated"


def test_verification_interval_skips_steps(recorder):
    run(recorder, verification_interval=2)
    assert [r["step"] for r in records_of(recorder, "verification")] == [0, 2]


def test_every_step_records_a_step_and_a_policy_call(recorder):
    run(recorder)
    assert [r["step"] for r in records_of(recorder, "step")] == [0, 1, 2, 3]
    calls = records_of(recorder, "llm_call")
    assert [r["role"] for r in calls] == ["policy"] * 4


def test_a_stub_verifier_records_no_verifier_usage(recorder):
    """D22: no call made stays distinguishable from call made, zero tokens."""
    run(recorder)
    assert not [r for r in records_of(recorder, "llm_call") if r["role"] == "verifier"]
    assert len(records_of(recorder, "verification")) == 4


def test_an_llm_verifier_records_its_usage_separately(recorder):
    verifier = LLMVerifier(
        StubLLMClient(["NOT DONE: nothing yet."], model="stub-verifier")
    )
    run(recorder, verifier=verifier)
    verifier_calls = [
        r for r in records_of(recorder, "llm_call") if r["role"] == "verifier"
    ]
    assert len(verifier_calls) == 4
    assert verifier_calls[0]["model"] == "stub-verifier"


def test_an_empty_policy_is_recorded_as_data(recorder):
    """D17: a response with no usable code is recorded, not dropped."""
    run(recorder, client=StubLLMClient(["I am not sure what to do."]))
    assert [r["policy"] for r in records_of(recorder, "step")] == [""] * 4


def test_execution_errors_are_recorded_and_fed_back(recorder):
    client = StubLLMClient([POLICY])
    run(recorder, client=client, env=make_env(observation={"stderr": "NameError: x"}))
    assert records_of(recorder, "step")[0]["execution_errors"] == ["NameError: x"]
    assert "EXECUTION ERRORS" in client.prompts[1]
    assert "NameError: x" in client.prompts[1]


def test_no_human_is_never_asked_when_the_agent_is_not_stuck(recorder):
    human = NoHuman()
    run(recorder, human=human)
    assert human.call_count == 0
    assert records_of(recorder, "intervention") == []


def test_no_human_records_the_request_but_counts_no_intervention(recorder):
    """The baseline must still show where the harness would have asked for help."""
    human = NoHuman()
    result = run(recorder, human=human, detector=AlwaysStuck())
    interventions = records_of(recorder, "intervention")
    assert human.call_count == 4
    assert len(interventions) == 4
    assert all(r["text"] is None for r in interventions)
    assert all(r["intervention_index"] is None for r in interventions)
    assert interventions[0]["stuck_reason"] == "always stuck"
    assert result.interventions == 0
    assert result.reason == "max_steps reached"


def test_stuck_then_human_then_continue(recorder):
    client = StubLLMClient([POLICY])
    human = ScriptedHuman(["Fuel the drill with coal."])
    result = run(recorder, client=client, human=human, detector=AlwaysStuck())
    assert result.interventions == 1
    assert "HUMAN GUIDANCE" in client.prompts[1]
    assert "Fuel the drill with coal." in client.prompts[1]


def test_intervention_is_recorded_with_index_and_original_step(recorder):
    human = ScriptedHuman([Hint("Fuel the drill.", original_step=7)])
    run(recorder, human=human, detector=AlwaysStuck())
    record = records_of(recorder, "intervention")[0]
    assert record["text"] == "Fuel the drill."
    assert record["intervention_index"] == 0
    assert record["original_step"] == 7


def test_scripted_human_replays_in_sequence_order(recorder):
    human = ScriptedHuman(["first hint", "second hint"])
    run(recorder, human=human, detector=AlwaysStuck(), max_interventions=2)
    texts = [r["text"] for r in records_of(recorder, "intervention") if r["text"]]
    assert texts == ["first hint", "second hint"]


def test_guidance_accumulates_and_earlier_guidance_survives(recorder):
    """D12: guidance is a list; a later hint never erases an earlier one."""
    client = StubLLMClient([POLICY])
    human = ScriptedHuman(["place the drill on ore", "then fuel it"])
    run(
        recorder,
        client=client,
        human=human,
        detector=AlwaysStuck(),
        max_interventions=3,
        goal=Goal(description="Produce iron plates", max_steps=4),
    )
    final_prompt = client.prompts[-1]
    assert "place the drill on ore" in final_prompt
    assert "then fuel it" in final_prompt


def test_an_intervention_resets_the_stuck_window(recorder):
    """D12: no re-fire on the next step; the agent gets a fresh window."""
    run(
        recorder,
        goal=Goal(description="Produce iron plates", max_steps=4),
        human=ScriptedHuman(["a", "b", "c", "d"]),
        detector=ConsecutiveNonDoneDetector(2),
    )
    fired_at = [r["step"] for r in records_of(recorder, "intervention")]
    assert fired_at == [1, 3]


def test_interventions_abort_the_goal_at_the_threshold(recorder):
    result = run(
        recorder,
        goal=Goal(description="Produce iron plates", max_steps=8),
        human=ScriptedHuman(["a", "b", "c", "d"]),
        detector=AlwaysStuck(),
        max_interventions=2,
    )
    assert result.completed is False
    assert result.interventions == 2
    assert result.reason == "aborted after 2 interventions without progress"
    assert result.steps_used == 2


def test_a_declined_request_does_not_count_towards_the_abort(recorder):
    """Only answered requests are interventions, so NoHuman never aborts early."""
    result = run(
        recorder,
        goal=Goal(description="Produce iron plates", max_steps=6),
        human=NoHuman(),
        detector=AlwaysStuck(),
        max_interventions=2,
    )
    assert result.interventions == 0
    assert result.steps_used == 6


def test_exhausted_scripted_hints_degrade_to_no_human(recorder):
    result = run(
        recorder,
        goal=Goal(description="Produce iron plates", max_steps=4),
        human=ScriptedHuman(["only one"]),
        detector=AlwaysStuck(),
        max_interventions=3,
    )
    interventions = records_of(recorder, "intervention")
    assert [r["text"] for r in interventions] == ["only one", None, None, None]
    assert result.interventions == 1
    assert result.reason == "max_steps reached"


def test_default_detector_stays_quiet_during_a_short_healthy_run(recorder):
    human = NoHuman()
    run(
        recorder,
        goal=Goal(description="Produce iron plates", max_steps=2),
        human=human,
        detector=default_detector(3),
    )
    assert human.call_count == 0


def test_history_pairs_a_policy_with_the_observation_that_followed_it(recorder):
    client = StubLLMClient([POLICY])
    run(recorder, client=client, env=make_env())
    second_prompt = client.prompts[1]
    assert "step 0 policy:" in second_prompt
    assert "step 0 result:" in second_prompt
    assert "iron-plate 1" in second_prompt


def test_the_loop_submits_the_extracted_policy_to_the_environment(recorder):
    env = make_env()
    run(recorder, env=env, verifier=StubVerifier([DONE]))
    assert env.submitted_actions[0].code == "place_entity()"

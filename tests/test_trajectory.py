"""Tests for trajectory recording.

Required coverage (build-plan section 21, Trajectory): token counts, latency,
verification, verbatim intervention text, execution errors. The record-type cases pin
decisions.md D22, and the passivity cases pin D6.
"""

import json

import pytest

from factorio_maxxing.goal import Goal
from factorio_maxxing.llm import LLMResponse
from factorio_maxxing.trajectory import TrajectoryRecorder, read_trajectory
from factorio_maxxing.verifier import VerificationResult

GOAL = Goal(description="Produce iron plates")
OBSERVATION = {"inventory": {"iron-plate": 12}}


def policy_response(**overrides):
    fields = {
        "text": "x = 1",
        "model": "claude-haiku-4-5",
        "input_tokens": 412,
        "output_tokens": 88,
        "cache_read_tokens": 64,
        "cache_write_tokens": 8,
        "latency_seconds": 1.25,
    }
    return LLMResponse(**{**fields, **overrides})


@pytest.fixture
def recorder(tmp_path):
    with TrajectoryRecorder(tmp_path / "run.jsonl", run_id="run-1") as rec:
        yield rec


def test_records_are_one_json_object_per_line(recorder):
    recorder.record_step(0, GOAL, "x = 1", OBSERVATION, 0.0)
    recorder.record_verification(0, VerificationResult(False, "not yet"))
    lines = recorder.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(json.loads(line) for line in lines)


def test_every_record_carries_type_run_id_and_step(recorder):
    recorder.record_step(0, GOAL, "x = 1", OBSERVATION, 0.0)
    recorder.record_llm_call(0, "policy", policy_response())
    recorder.record_verification(0, VerificationResult(False, "not yet"))
    recorder.record_intervention(0, "stuck", "fuel it", 0)
    records = read_trajectory(recorder.path)
    assert [r["type"] for r in records] == [
        "step",
        "llm_call",
        "verification",
        "intervention",
    ]
    assert all(r["run_id"] == "run-1" and r["step"] == 0 for r in records)


def test_step_record_fields(recorder):
    recorder.record_step(3, GOAL, "x = 1", OBSERVATION, 2.5, ["NameError: x"])
    record = read_trajectory(recorder.path)[0]
    assert record == {
        "type": "step",
        "run_id": "run-1",
        "step": 3,
        "goal": "Produce iron plates",
        "policy": "x = 1",
        "observation": OBSERVATION,
        "reward": 2.5,
        "execution_errors": ["NameError: x"],
    }


def test_step_record_keeps_an_empty_policy(recorder):
    """D17: a response with no usable code is data, not a missing field."""
    recorder.record_step(0, GOAL, "", OBSERVATION, 0.0)
    assert read_trajectory(recorder.path)[0]["policy"] == ""


def test_step_record_defaults_to_no_errors(recorder):
    recorder.record_step(0, GOAL, "x = 1", OBSERVATION, 0.0)
    assert read_trajectory(recorder.path)[0]["execution_errors"] == []


def test_llm_call_records_raw_usage_and_latency(recorder):
    recorder.record_llm_call(0, "policy", policy_response())
    record = read_trajectory(recorder.path)[0]
    assert record == {
        "type": "llm_call",
        "run_id": "run-1",
        "step": 0,
        "role": "policy",
        "model": "claude-haiku-4-5",
        "input_tokens": 412,
        "output_tokens": 88,
        "cache_read_tokens": 64,
        "cache_write_tokens": 8,
        "latency_seconds": 1.25,
    }


def test_llm_call_never_records_a_computed_cost(recorder):
    """D9: cost is derived at analysis time from a pricing table."""
    recorder.record_llm_call(0, "policy", policy_response())
    assert "cost" not in read_trajectory(recorder.path)[0]


def test_policy_and_verifier_usage_share_one_record_type(recorder):
    recorder.record_llm_call(0, "policy", policy_response())
    recorder.record_llm_call(0, "verifier", policy_response(model="claude-sonnet-5"))
    records = read_trajectory(recorder.path)
    assert [r["role"] for r in records] == ["policy", "verifier"]
    assert [r["model"] for r in records] == ["claude-haiku-4-5", "claude-sonnet-5"]


def test_cost_analysis_is_one_sum_over_llm_calls(recorder):
    """D22: the analysis expression must not change when a caller is added."""
    recorder.record_step(0, GOAL, "x = 1", OBSERVATION, 0.0)
    recorder.record_llm_call(0, "policy", policy_response(input_tokens=400))
    recorder.record_llm_call(0, "verifier", policy_response(input_tokens=260))
    recorder.record_llm_call(1, "future_caller", policy_response(input_tokens=100))
    records = read_trajectory(recorder.path)
    total = sum(r["input_tokens"] for r in records if r["type"] == "llm_call")
    assert total == 760


def test_verification_record_holds_the_verdict_only(recorder):
    recorder.record_verification(2, VerificationResult(True, "12 plates in inventory"))
    record = read_trajectory(recorder.path)[0]
    assert record == {
        "type": "verification",
        "run_id": "run-1",
        "step": 2,
        "done": True,
        "reason": "12 plates in inventory",
    }
    assert "input_tokens" not in record


def test_a_skipped_verification_writes_nothing(recorder):
    """verification_interval > 1: no record, rather than null columns."""
    recorder.record_step(0, GOAL, "x = 1", OBSERVATION, 0.0)
    recorder.record_step(1, GOAL, "x = 2", OBSERVATION, 0.0)
    recorder.record_verification(1, VerificationResult(False, "not yet"))
    records = read_trajectory(recorder.path)
    assert [r["step"] for r in records if r["type"] == "verification"] == [1]


def test_a_verifier_making_no_call_records_no_usage(recorder):
    """StubVerifier: no call made stays distinct from call made, zero tokens."""
    recorder.record_verification(0, VerificationResult(False, "not yet"))
    records = read_trajectory(recorder.path)
    assert [r["type"] for r in records] == ["verification"]
    assert not [r for r in records if r["type"] == "llm_call"]


def test_intervention_record_fields(recorder):
    recorder.record_intervention(
        step=3,
        stuck_reason="3 consecutive non-DONE verifications",
        text="Fuel the drill before checking its status.",
        intervention_index=0,
        original_step=7,
    )
    assert read_trajectory(recorder.path)[0] == {
        "type": "intervention",
        "run_id": "run-1",
        "step": 3,
        "stuck_reason": "3 consecutive non-DONE verifications",
        "text": "Fuel the drill before checking its status.",
        "intervention_index": 0,
        "original_step": 7,
    }


def test_intervention_text_is_stored_verbatim(recorder):
    text = "  Use place_entity_next_to().\nDo not compute offsets by hand.  "
    recorder.record_intervention(1, "stuck", text, 0)
    assert read_trajectory(recorder.path)[0]["text"] == text


def test_intervention_text_keeps_non_ascii(recorder):
    text = "the drop position is off — move the drill north"
    recorder.record_intervention(1, "stuck", text, 0)
    assert read_trajectory(recorder.path)[0]["text"] == text


def test_original_step_is_null_for_a_live_intervention(recorder):
    recorder.record_intervention(1, "stuck", "fuel it", 0)
    assert read_trajectory(recorder.path)[0]["original_step"] is None


def test_recorder_returns_nothing_and_judges_nothing(recorder):
    """D6: the recorder is passive. A DONE verdict is written, not acted on."""
    assert recorder.record_verification(0, VerificationResult(True, "done")) is None
    assert recorder.record_step(0, GOAL, "x = 1", OBSERVATION, 0.0) is None
    assert len(read_trajectory(recorder.path)) == 2


def test_records_are_flushed_as_they_are_written(recorder):
    """A run that dies mid-goal keeps everything recorded up to that point."""
    recorder.record_step(0, GOAL, "x = 1", OBSERVATION, 0.0)
    assert len(read_trajectory(recorder.path)) == 1


def test_records_are_appended_not_overwritten(tmp_path):
    path = tmp_path / "run.jsonl"
    with TrajectoryRecorder(path, run_id="run-1") as first:
        first.record_step(0, GOAL, "x = 1", OBSERVATION, 0.0)
    with TrajectoryRecorder(path, run_id="run-1") as second:
        second.record_step(1, GOAL, "x = 2", OBSERVATION, 0.0)
    assert [r["step"] for r in read_trajectory(path)] == [0, 1]


def test_missing_directories_are_created(tmp_path):
    path = tmp_path / "nested" / "deeper" / "run.jsonl"
    with TrajectoryRecorder(path) as recorder:
        recorder.record_step(0, GOAL, "x = 1", OBSERVATION, 0.0)
    assert path.exists()


def test_run_id_is_generated_when_not_supplied(tmp_path):
    first = TrajectoryRecorder(tmp_path / "a.jsonl")
    second = TrajectoryRecorder(tmp_path / "b.jsonl")
    assert first.run_id != second.run_id
    first.close()
    second.close()


def test_an_unserialisable_observation_does_not_kill_the_run(recorder):
    recorder.record_step(0, GOAL, "x = 1", {"entity": object()}, 0.0)
    recorded = read_trajectory(recorder.path)[0]["observation"]["entity"]
    assert "object object at" in recorded


def test_read_trajectory_ignores_blank_lines(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text('{"type": "step"}\n\n{"type": "llm_call"}\n', encoding="utf-8")
    assert [r["type"] for r in read_trajectory(path)] == ["step", "llm_call"]

"""Tests for goal contracts: construction, defaults, validation."""

import dataclasses
from pathlib import Path

import pytest

from factorio_maxxing.goal import Goal, GoalResult


def test_goal_construction():
    goal = Goal(
        description="Build a working iron mining setup", max_steps=8, notes="hint"
    )
    assert goal.description == "Build a working iron mining setup"
    assert goal.max_steps == 8
    assert goal.notes == "hint"


def test_goal_defaults():
    goal = Goal(description="Produce iron plates")
    assert goal.max_steps == 32
    assert goal.notes is None


def test_goal_is_frozen():
    goal = Goal(description="Produce iron plates")
    with pytest.raises(dataclasses.FrozenInstanceError):
        goal.max_steps = 64


def test_goals_with_equal_fields_are_equal():
    assert Goal(description="Produce iron plates") == Goal(
        description="Produce iron plates"
    )


@pytest.mark.parametrize("description", ["", "   ", "\n\t"])
def test_goal_rejects_empty_description(description):
    with pytest.raises(ValueError, match="non-empty"):
        Goal(description=description)


@pytest.mark.parametrize("max_steps", [0, -1])
def test_goal_rejects_non_positive_max_steps(max_steps):
    with pytest.raises(ValueError, match="max_steps"):
        Goal(description="Produce iron plates", max_steps=max_steps)


def test_goal_accepts_max_steps_of_one():
    assert Goal(description="Produce iron plates", max_steps=1).max_steps == 1


def test_goal_result_construction():
    goal = Goal(description="Produce iron plates")
    result = GoalResult(
        goal=goal,
        completed=True,
        steps_used=4,
        interventions=1,
        reason="verifier: iron plates present in inventory",
        trajectory_path=Path("trajectories/run-1.jsonl"),
    )
    assert result.goal is goal
    assert result.completed is True
    assert result.steps_used == 4
    assert result.interventions == 1
    assert result.reason == "verifier: iron plates present in inventory"
    assert result.trajectory_path == Path("trajectories/run-1.jsonl")


def test_goal_result_records_failure_without_deciding_anything():
    """GoalResult is a passive record: a failed run is representable verbatim."""
    result = GoalResult(
        goal=Goal(description="Research automation", max_steps=32),
        completed=False,
        steps_used=32,
        interventions=3,
        reason="max_steps reached",
        trajectory_path=Path("trajectories/run-2.jsonl"),
    )
    assert result.completed is False
    assert result.steps_used == 32

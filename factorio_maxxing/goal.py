"""Goal contracts.

Deliberately minimal: no subtasks, no decomposition, no planning graphs.
See docs/contracts.md (goal.py) and docs/architecture.md (component table).
"""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_STEPS = 32


@dataclass(frozen=True)
class Goal:
    """A single objective handed to the harness.

    At M0/M1 goals are supplied by a human; a Goal Manager takes over at M3+.
    """

    description: str
    max_steps: int = DEFAULT_MAX_STEPS
    notes: str | None = None
    """Optional human hints supplied up front. Distinct from mid-run guidance,
    which reaches the policy through the human backend, not through the Goal."""

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Goal.description must be a non-empty string")
        if self.max_steps < 1:
            raise ValueError(f"Goal.max_steps must be >= 1, got {self.max_steps}")


@dataclass
class GoalResult:
    """The outcome of running one goal.

    A passive record produced by the loop; it decides nothing.
    """

    goal: Goal
    completed: bool
    steps_used: int
    interventions: int
    reason: str
    """The verifier's stated reason, or the abort reason."""
    trajectory_path: Path

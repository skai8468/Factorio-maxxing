"""Environment protocol and the deterministic offline mock.

See docs/contracts.md (envs.py) and docs/decisions.md D10.

MockFactorioEnv is a fixture, not a simulator. It is scripted by step index and
deliberately does not read the submitted Python: deciding transitions from arbitrary
code would mean building a fake Factorio. Real Factorio behaviour belongs in FLE.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

Observation = dict[str, Any]
Info = dict[str, Any]


@dataclass(frozen=True)
class Action:
    """A unit of work submitted to the environment.

    Mirrors fle/env/gym_env/action.py. FLE's third field, game_state, is the
    checkpoint/restore mechanism and is future scope, so it is omitted here.
    """

    code: str
    agent_idx: int = 0


@runtime_checkable
class EnvProtocol(Protocol):
    def reset(self) -> Observation: ...

    def step(self, action: Action) -> tuple[Observation, float, bool, bool, Info]: ...


@dataclass(frozen=True)
class MockFrame:
    """One scripted environment response, returned by the step at its index."""

    observation: Observation
    reward: float = 0.0
    terminated: bool = False
    truncated: bool = False
    info: Info = field(default_factory=dict)


class MockFactorioEnv:
    """A deterministic environment fixture keyed by step index.

    The Nth call to step() returns the Nth frame, whatever code was submitted.
    Once the script is exhausted the final frame repeats, so a short script can
    back a long run without inventing transitions.

    Submitted actions are retained in ``submitted_actions`` for test inspection
    only. They never influence what the environment returns.
    """

    def __init__(self, reset_observation: Observation, frames: Sequence[MockFrame]):
        if not frames:
            raise ValueError("MockFactorioEnv requires at least one frame")
        self._reset_observation = reset_observation
        self._frames = tuple(frames)
        self.step_index = 0
        self.submitted_actions: list[Action] = []

    def reset(self) -> Observation:
        """Rewind to the start of the script and return the initial observation."""
        self.step_index = 0
        self.submitted_actions = []
        return self._reset_observation

    def step(self, action: Action) -> tuple[Observation, float, bool, bool, Info]:
        self.submitted_actions.append(action)
        frame = self._frames[min(self.step_index, len(self._frames) - 1)]
        self.step_index += 1
        return (
            frame.observation,
            frame.reward,
            frame.terminated,
            frame.truncated,
            frame.info,
        )

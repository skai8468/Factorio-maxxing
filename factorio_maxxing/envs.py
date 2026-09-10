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


DEFAULT_TASK_KEY = "open_play"


class RealFactorioEnv:
    """Adapter over FLE's ``FactorioGymEnv``, satisfying ``EnvProtocol``.

    FLE is imported inside ``__init__``, never at module scope, so the offline suite
    still collects and passes on a machine where FLE is not installed - which is the
    normal case, since FLE lives in the WSL virtualenv and the tests run on Windows
    (D29). Importing this module therefore costs nothing.

    Construction goes through ``make_factorio_env`` rather than ``gym.make``: gym would
    wrap the environment in ``OrderEnforcing``/``PassiveEnvChecker``, and FLE's
    ``reset()`` does not follow the Gymnasium contract closely enough to survive the
    checker.

    ``enable_vision`` defaults off. Measured on a live world, a rendered map image adds
    roughly 1.1 MB to every observation and 1.2 s to every step, and it is recorded but
    never shown to the policy (docs/fle-integration.md).

    ``pause_after_action`` defaults to FLE's own ``True``, which freezes the game tick
    between steps so no world time passes while the policy and verifier are being
    called. Turning it off lets the world keep running - which is what makes a run
    watchable through a game client, since a paused server reads as an unresponsive one
    - at the cost of the observation being slightly stale by the time the next action
    lands. Off is for demonstrations; measured runs keep the default (D36).
    """

    def __init__(
        self,
        task_key: str = DEFAULT_TASK_KEY,
        run_idx: int = 0,
        *,
        enable_vision: bool = False,
        pause_after_action: bool = True,
    ):
        from fle.env.gym_env.action import Action as FLEAction
        from fle.env.gym_env.registry import (
            GymEnvironmentSpec,
            get_environment_info,
            make_factorio_env,
        )

        info = get_environment_info(task_key)
        if info is None:
            raise ValueError(f"unknown FLE task key: {task_key}")
        info["enable_vision"] = enable_vision

        self.task_key = task_key
        self.pause_after_action = pause_after_action
        self._fle_action = FLEAction
        self._env = make_factorio_env(spec=GymEnvironmentSpec(**info), run_idx=run_idx)

        # Set after construction rather than passed in: make_factorio_env builds
        # FactorioGymEnv(instance=, task=, enable_vision=) and forwards nothing else,
        # so there is no constructor route to this flag short of reimplementing the
        # factory. FLE reads the attribute once per step, so assignment is enough.
        self._env.pause_after_action = pause_after_action

        self._force_unpause()

    def _force_unpause(self) -> None:
        """Clear a pause left in the game by an earlier run (D39).

        FLE keeps pause state in a Python attribute initialised to ``False`` and never
        reconciled against the game, and ``unpause()`` returns early when that attribute
        says the game is already running. A run that ended with ``pause_after_action``
        leaves ``game.tick_paused = true`` set in the *game*, so the next process - a
        fresh object, flag ``False`` - silently declines to clear it. The world then
        never ticks: path requests never resolve, and the agent cannot move.

        Setting the flag before unpausing defeats the guard. This runs on every
        construction, not only when the pause is disabled, because a paused game is
        inherited the same way whatever this run intends to do afterwards.
        """
        instance = getattr(self._env, "instance", None)
        if instance is None:
            return
        instance._is_paused = True
        instance.unpause()

    def reset(self) -> Observation:
        """Return the observation alone, discarding FLE's ``info``.

        FLE annotates ``reset()`` as returning a bare dict, but it actually returns the
        Gymnasium ``(observation, info)`` pair with ``info`` empty - confirmed against a
        live container. Both shapes are accepted so that an upstream fix to the
        annotation, in either direction, does not break the harness.
        """
        result = self._env.reset()
        if isinstance(result, tuple):
            return result[0]
        return result

    def step(self, action: Action) -> tuple[Observation, float, bool, bool, Info]:
        """Translate our Action into FLE's and pass it through.

        FLE's ``step`` asserts ``isinstance(action, Action)`` against its own class, so
        a structurally identical object will not do. ``game_state`` is left unset:
        checkpoint and restore is future scope (D15).
        """
        return self._env.step(
            self._fle_action(code=action.code, agent_idx=action.agent_idx)
        )

    def close(self) -> None:
        """Release the Factorio instance. Not part of EnvProtocol; run.py calls it."""
        self._env.close()

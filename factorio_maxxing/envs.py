"""Environment protocol and the deterministic offline mock.

See docs/contracts.md (envs.py) and docs/decisions.md D10.

MockFactorioEnv is a fixture, not a simulator. It is scripted by step index and
deliberately does not read the submitted Python: deciding transitions from arbitrary
code would mean building a fake Factorio. Real Factorio behaviour belongs in FLE.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
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

WALKING_HANDLER_LUA = (
    "/sc script.on_nth_tick(5, function(event) if storage.walking_queues then "
    "storage.actions.update_walking_queues() end end)"
)
"""FLE's own slow-mode walking handler, as ``move_to/server.lua`` registers it.

FLE registers it only if ``storage.fast`` is unset when the tools load, and it sets the
flag straight afterwards, so whether a running game has it depends on history. Without
it a slow-mode walking queue never advances and ``move_to`` waits forever (D48)."""

FAST_ACTIONS = ("inspect_inventory", "place_entity", "craft_item", "harvest_resource")
"""Tools whose Lua keeps FLE's fast-mode behaviour even in slow mode (D48 correction).

Slow mode is only wanted for walking. In the rest of FLE it is unmaintained and
dangerous: slow ``inspect_inventory`` opens a GUI on the agent's character and closes it
from a tick handler that raises ``Not a player`` - a script error in an event, which
killed the server on the first watched run to inspect a furnace. Slow ``place_entity``
builds from a tick handler as well, and slow ``craft_item`` stops after one item.
``harvest_resource`` is here so it accounts mining time exactly as a measured run
does."""

WALK_TIMEOUT = 120.0
"""Seconds ``move_to`` may wait for a walk to finish before giving up. A long walk at 1x
is tens of seconds; this only exists so a stuck queue cannot hang a run."""


def wait_for_walk(
    send: Callable[[str], object],
    player_index: int,
    *,
    poll: float = 0.25,
    timeout: float = WALK_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Block until the agent's walking queue is empty; False if it timed out.

    Slow-mode ``move_to`` starts a walk and returns at once. FLE's own wait for it lives
    behind its Python-side slow flag, which also swallows placement errors and makes
    harvesting walk off after more ore - so the Python side stays fast and this is
    hooked onto ``move_to`` instead (D48 correction).
    """
    command = f"/sc rcon.print(storage.actions.get_walking_queue_length({player_index}))"
    deadline = clock() + timeout
    while str(send(command)).strip() not in ("0", ""):
        if clock() >= deadline:
            return False
        sleep(poll)
    return True


FAST_ACTIONS_LUA = " ".join(
    f"""
storage.harness_fast_wrapped = storage.harness_fast_wrapped or {{}}
for _, name in pairs({{{", ".join(f'"{name}"' for name in FAST_ACTIONS)}}}) do
  local current = storage.actions[name]
  if current and storage.harness_fast_wrapped[name] ~= current then
    local original = current
    local wrapper = function(...)
      local saved = storage.fast
      storage.fast = true
      local result = table.pack(pcall(original, ...))
      storage.fast = saved
      if not result[1] then error(result[2], 0) end
      return table.unpack(result, 2, result.n)
    end
    storage.actions[name] = wrapper
    storage.harness_fast_wrapped[name] = wrapper
  end
end
""".split()
)
"""Run each of ``FAST_ACTIONS`` with ``storage.fast`` set for the length of the call.

FLE reads the flag at call time and looks the action up at call time too, so a wrapper
changes the behaviour without patching FLE's files. Errors pass through unchanged, with
level 0 so no position is prepended to FLE's own message. Idempotent: a tool FLE has
reloaded is wrapped again, and one already wrapped is left alone."""


PEACEFUL_LUA = (
    "/sc local s = game.surfaces[1] "
    "s.peaceful_mode = true "
    "game.map_settings.enemy_expansion.enabled = false "
    "game.map_settings.enemy_evolution.enabled = false "
    "local n = 0 "
    'for _, e in pairs(s.find_entities_filtered{force = "enemy"}) do '
    "e.destroy() n = n + 1 end "
    "rcon.print(n)"
)
"""Make the world free of enemies, and print how many enemy entities were removed (D53).

FLE's ``peaceful=True`` intends this and does not achieve it: ``remove_enemies()`` runs
before the start area's chunks are generated and leaves worms, and the per-step
``background_step()`` FLE's own evaluator calls indexes ``game.player``, which is nil
over RCON, so it fails silently. This addresses the surface, not a player, removes
biters, nests and worms alike, and sets ``peaceful_mode`` so that nests in chunks
generated later - every walk into new ground generates some - do not attack either."""


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

    ``starting_inventory`` stocks the agent after reset. Empty by default, which is what
    ``open_play`` gives: nothing at all. A goal phrased as "place a burner mining drill"
    therefore silently includes crafting one from raw stone and ore, so what the agent
    begins with decides what the goal actually measures (D40).

    ``game_speed`` of ``None`` keeps FLE's own, which ``reset()`` sets to 10x. A game
    client cannot follow that: it must simulate every tick the server does, and at 10x
    the server itself only manages about 150 ticks a second, so a watching client falls
    behind and reports the server as not responding. Watched runs set 1 (D46).

    ``fast_mode`` defaults to FLE's own ``True``, in which the character teleports
    between path points and crafting and placement are instant, with the time they would
    have taken added to a counter. ``False`` is FLE's slow mode: the character walks,
    and nothing else changes: placing, crafting, harvesting and inventory inspection keep
    their fast behaviour, because FLE's slow versions of them crash the server, hide
    errors or wander off. It still
    changes what the agent sees - a slow ``move_to`` returns where the walk *started* -
    so it is for watching, never for measurement (D48).

    ``peaceful`` defaults on, which is what FLE's own ``peaceful=True`` intends: no
    enemies. FLE does not deliver it - the live map held 2,052 biters, 919 nests and
    1,119 worms, the nearest 170 tiles from spawn - and an agent that walked into them
    died with its whole inventory, invisibly to itself and the verifier. Enemies are
    removed after every reset and every step, since walking generates new ground with
    new nests in it (D53).
    """

    def __init__(
        self,
        task_key: str = DEFAULT_TASK_KEY,
        run_idx: int = 0,
        *,
        enable_vision: bool = False,
        pause_after_action: bool = True,
        starting_inventory: Mapping[str, int] | None = None,
        game_speed: float | None = None,
        fast_mode: bool = True,
        peaceful: bool = True,
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
        if game_speed is not None and game_speed <= 0:
            raise ValueError(f"game_speed must be positive, got {game_speed}")

        self.task_key = task_key
        self.game_speed = game_speed
        self.fast_mode = fast_mode
        self.peaceful = peaceful
        self.pause_after_action = pause_after_action
        self.starting_inventory = dict(starting_inventory or {})
        self._fle_action = FLEAction
        self._env = make_factorio_env(spec=GymEnvironmentSpec(**info), run_idx=run_idx)

        # Set after construction rather than passed in: make_factorio_env builds
        # FactorioGymEnv(instance=, task=, enable_vision=) and forwards nothing else,
        # so there is no constructor route to this flag short of reimplementing the
        # factory. FLE reads the attribute once per step, so assignment is enough.
        self._env.pause_after_action = pause_after_action

        self._force_unpause()
        self._apply_speed()
        self._apply_fast_mode()

    def _apply_peaceful(self) -> None:
        """Remove every enemy and keep new ones passive (D53). Logs what it removed.

        A failure is logged, not raised: a step that has already run must not be lost
        because the cleanup after it could not reach the server.
        """
        if not self.peaceful:
            return
        try:
            removed = self._env.instance.rcon_client.send_command(PEACEFUL_LUA)
        except Exception as error:  # noqa: BLE001 - any transport failure is non-fatal
            logging.warning("could not clear enemies: %s", error)
            return
        if str(removed or "").strip() not in ("", "0"):
            logging.info("cleared %s enemy entities", str(removed).strip())

    def _apply_fast_mode(self) -> None:
        """Make the agent walk, and change nothing else (D48 and its correction).

        FLE keeps the mode twice: ``storage.fast`` decides whether a tool's Lua
        teleports or walks, and ``instance.fast`` whether its Python side waits for
        the action. Only walking is wanted, so:

        - ``storage.fast`` is cleared, and every tool that reads it except ``move_to``
          is wrapped back to fast behaviour (``FAST_ACTIONS_LUA``);
        - the walking handler is registered explicitly, not trusted to load order;
        - ``instance.fast`` is left alone, and a post-tool hook - FLE's own extension
          point - makes ``move_to`` wait for the walk to finish instead.

        Re-applied after reset, where it is harmless; the hook is added once.
        """
        if self.fast_mode:
            return
        instance = self._env.instance
        instance.rcon_client.send_command("/sc storage.fast = false")
        instance.rcon_client.send_command(WALKING_HANDLER_LUA)
        instance.rcon_client.send_command(f"/sc {FAST_ACTIONS_LUA}")
        hooks = instance.post_tool_hooks.setdefault("move_to", [])
        if self._walk_hook not in hooks:
            hooks.append(self._walk_hook)

    def _walk_hook(self, tool, _result) -> None:
        """Post-hook on ``move_to``: return control only once the character arrives."""
        if not wait_for_walk(
            self._env.instance.rcon_client.send_command, tool.player_index
        ):
            logging.warning("walk did not finish within %.0fs", WALK_TIMEOUT)

    def _apply_speed(self) -> None:
        """Set the configured game speed through FLE's own setter (D46).

        FLE's setter, not a raw ``game.speed`` command, because FLE keeps the speed in
        Python too: ``unpause()`` restores it from there, and the agent's ``sleep``
        tool divides by it to turn ticks into wall-clock time. Called after every
        reset as well as here, since FLE's reset puts the speed back to its own.

        ``instance_speed`` too, because FLE's gym env copies the speed once at
        construction - 10x, at that point - and re-applies that copy at the start of
        every ``step()``. Without it the speed holds through construction and reset
        and reverts on the first step (measured: 1, 1, then 10).
        """
        if self.game_speed is None:
            return
        self._env.instance.set_speed(self.game_speed)
        self._env.instance_speed = self.game_speed

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
        observation = result[0] if isinstance(result, tuple) else result
        self._apply_speed()
        self._apply_fast_mode()
        self._apply_peaceful()
        if self.starting_inventory:
            observation = self._stock_inventory()
        return observation

    def _stock_inventory(self) -> Observation:
        """Give the agent its starting inventory, and re-observe so it can see it.

        Applied *after* reset, not before: FLE's reset runs the task's setup, which
        installs that task's own starting inventory - empty for ``open_play`` - and
        would undo anything set earlier.

        Re-observing matters as much as the stocking. The observation reset() returned
        was built before this ran, so it still reports an empty inventory, and the
        policy's first prompt would tell the agent it owns nothing - sending it off to
        craft what it is already holding (D40).
        """
        try:
            namespace = self._env.instance.namespaces[0]
            namespace._set_inventory(self.starting_inventory)
            return self._env.get_observation().to_dict()
        except (AttributeError, IndexError) as error:
            raise RuntimeError(
                "could not stock the starting inventory: FLE's namespace or "
                f"observation shape has changed ({error}). Clear starting_inventory "
                "to run without it."
            ) from error

    def step(self, action: Action) -> tuple[Observation, float, bool, bool, Info]:
        """Translate our Action into FLE's and pass it through.

        FLE's ``step`` asserts ``isinstance(action, Action)`` against its own class, so
        a structurally identical object will not do. ``game_state`` is left unset:
        checkpoint and restore is future scope (D15).

        Enemies are cleared after the step, because the step's walking may have
        generated new ground with new nests in it (D53).
        """
        result = self._env.step(
            self._fle_action(code=action.code, agent_idx=action.agent_idx)
        )
        self._apply_peaceful()
        return result

    def close(self) -> None:
        """Release the Factorio instance. Not part of EnvProtocol; run.py calls it."""
        self._env.close()

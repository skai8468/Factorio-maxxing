"""Tests for the environment protocol and the deterministic mock.

Required coverage (build-plan section 21, Mock env): deterministic transitions,
reset, step. The determinism cases also pin decisions.md D10: the mock must not
interpret submitted Python.
"""

import pytest

from factorio_maxxing.envs import Action, EnvProtocol, MockFactorioEnv, MockFrame


def make_env(n_frames: int = 3) -> MockFactorioEnv:
    return MockFactorioEnv(
        reset_observation={"inventory": {}},
        frames=[
            MockFrame(observation={"inventory": {"iron-plate": i}}, reward=float(i))
            for i in range(1, n_frames + 1)
        ],
    )


def test_action_defaults():
    action = Action(code="print('hello')")
    assert action.code == "print('hello')"
    assert action.agent_idx == 0


def test_mock_env_satisfies_the_protocol():
    assert isinstance(make_env(), EnvProtocol)


def test_reset_returns_the_initial_observation():
    env = make_env()
    assert env.reset() == {"inventory": {}}


def test_step_returns_frames_in_index_order():
    env = make_env()
    env.reset()
    observations = [env.step(Action(code="pass"))[0] for _ in range(3)]
    assert observations == [
        {"inventory": {"iron-plate": 1}},
        {"inventory": {"iron-plate": 2}},
        {"inventory": {"iron-plate": 3}},
    ]


def test_step_returns_the_full_gym_tuple():
    env = MockFactorioEnv(
        reset_observation={},
        frames=[
            MockFrame(
                observation={"inventory": {"iron-plate": 1}},
                reward=2.5,
                terminated=True,
                truncated=False,
                info={"note": "scripted"},
            )
        ],
    )
    env.reset()
    obs, reward, terminated, truncated, info = env.step(Action(code="pass"))
    assert obs == {"inventory": {"iron-plate": 1}}
    assert reward == 2.5
    assert terminated is True
    assert truncated is False
    assert info == {"note": "scripted"}


def test_transitions_ignore_the_submitted_code():
    """D10: the mock is a fixture. Different code, identical transitions."""
    first, second = make_env(), make_env()
    first.reset()
    second.reset()

    from_valid = [first.step(Action(code=f"place_entity({i})"))[0] for i in range(3)]
    from_garbage = [second.step(Action(code="!!! not python"))[0] for _ in range(3)]

    assert from_valid == from_garbage


def test_two_envs_with_the_same_script_are_identical():
    first, second = make_env(), make_env()
    first.reset()
    second.reset()
    assert [first.step(Action(code="a")) for _ in range(3)] == [
        second.step(Action(code="b")) for _ in range(3)
    ]


def test_script_exhaustion_repeats_the_final_frame():
    env = make_env(n_frames=2)
    env.reset()
    observations = [env.step(Action(code="pass"))[0] for _ in range(5)]
    assert observations[1:] == [{"inventory": {"iron-plate": 2}}] * 4


def test_reset_rewinds_the_script():
    env = make_env()
    env.reset()
    first_pass = [env.step(Action(code="pass"))[0] for _ in range(3)]

    env.reset()
    second_pass = [env.step(Action(code="pass"))[0] for _ in range(3)]

    assert first_pass == second_pass
    assert env.step_index == 3


def test_reset_clears_recorded_actions():
    env = make_env()
    env.reset()
    env.step(Action(code="first"))
    env.reset()
    assert env.submitted_actions == []


def test_submitted_actions_are_recorded_verbatim():
    env = make_env()
    env.reset()
    env.step(Action(code="place_entity()"))
    env.step(Action(code="insert_item()", agent_idx=1))
    assert env.submitted_actions == [
        Action(code="place_entity()"),
        Action(code="insert_item()", agent_idx=1),
    ]


def test_env_requires_at_least_one_frame():
    with pytest.raises(ValueError, match="at least one frame"):
        MockFactorioEnv(reset_observation={}, frames=[])


# --- RealFactorioEnv --------------------------------------------------------------
#
# FLE is not installed on the machine that runs this suite (D29: the working copy is on
# Windows, FLE lives in the WSL virtualenv), so the adapter is exercised against a fake
# `fle` package injected into sys.modules. That is the point of the lazy import: these
# tests prove the translation contract without FLE present.


class _FLEAction:
    """Stands in for fle.env.gym_env.action.Action, which FLE's step asserts on."""

    def __init__(self, code, agent_idx=0, game_state=None):
        self.code = code
        self.agent_idx = agent_idx
        self.game_state = game_state


class _FakeInstance:
    """Stands in for FLE's FactorioInstance, whose pause flag is the point here.

    Mirrors the real guard: unpause() does nothing unless the flag says paused, which
    is exactly the bug D39 works around.
    """

    def __init__(self):
        self._is_paused = False
        self.unpause_calls = 0
        self.rcon_unpaused = False

    def unpause(self):
        self.unpause_calls += 1
        if self._is_paused:
            self._is_paused = False
            self.rcon_unpaused = True


class _FakeGymEnv:
    def __init__(self, reset_result):
        self._reset_result = reset_result
        self.stepped: list[_FLEAction] = []
        self.closed = False
        self.instance = _FakeInstance()

    def reset(self):
        return self._reset_result

    def step(self, action):
        assert isinstance(action, _FLEAction), "FLE asserts on its own Action class"
        self.stepped.append(action)
        return {"inventory": {}}, 1.5, False, True, {"note": "ok"}

    def close(self):
        self.closed = True


def install_fake_fle(monkeypatch, reset_result, spec_info=None):
    """Inject a minimal fake `fle` package and return the env the adapter will wrap."""
    import sys
    import types

    fake_env = _FakeGymEnv(reset_result)
    captured: dict = {}

    action_mod = types.ModuleType("fle.env.gym_env.action")
    action_mod.Action = _FLEAction

    registry_mod = types.ModuleType("fle.env.gym_env.registry")

    class _Spec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def _get_environment_info(task_key):
        if spec_info is not None and task_key not in spec_info:
            return None
        return {"task_key": task_key, "num_agents": 1, "enable_vision": True}

    def _make_factorio_env(spec, run_idx):
        captured["spec"] = spec
        captured["run_idx"] = run_idx
        return fake_env

    registry_mod.GymEnvironmentSpec = _Spec
    registry_mod.get_environment_info = _get_environment_info
    registry_mod.make_factorio_env = _make_factorio_env

    for name, mod in [
        ("fle", types.ModuleType("fle")),
        ("fle.env", types.ModuleType("fle.env")),
        ("fle.env.gym_env", types.ModuleType("fle.env.gym_env")),
        ("fle.env.gym_env.action", action_mod),
        ("fle.env.gym_env.registry", registry_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)
    return fake_env, captured


def test_importing_envs_does_not_require_fle():
    """The lazy import is the contract that keeps this suite runnable on Windows."""
    import factorio_maxxing.envs as envs

    assert "fle" not in getattr(envs, "__dict__", {})
    source = __import__("inspect").getsource(envs)
    module_level = [
        line
        for line in source.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    assert not [line for line in module_level if "fle" in line]


def test_reset_unpacks_fle_observation_info_pair(monkeypatch):
    """FLE annotates reset() as a dict but returns (observation, info)."""
    from factorio_maxxing.envs import RealFactorioEnv

    install_fake_fle(monkeypatch, reset_result=({"inventory": {"coal": 3}}, {}))
    env = RealFactorioEnv()
    assert env.reset() == {"inventory": {"coal": 3}}


def test_reset_also_accepts_a_bare_observation(monkeypatch):
    """Tolerate the annotation becoming true upstream."""
    from factorio_maxxing.envs import RealFactorioEnv

    install_fake_fle(monkeypatch, reset_result={"inventory": {"coal": 3}})
    env = RealFactorioEnv()
    assert env.reset() == {"inventory": {"coal": 3}}


def test_step_translates_our_action_into_fles(monkeypatch):
    from factorio_maxxing.envs import RealFactorioEnv

    fake, _ = install_fake_fle(monkeypatch, reset_result=({}, {}))
    env = RealFactorioEnv()
    result = env.step(Action(code="place_entity()", agent_idx=1))

    assert result == ({"inventory": {}}, 1.5, False, True, {"note": "ok"})
    submitted = fake.stepped[0]
    assert submitted.code == "place_entity()"
    assert submitted.agent_idx == 1
    assert submitted.game_state is None, "checkpointing is future scope (D15)"


def test_vision_is_off_by_default(monkeypatch):
    """A rendered image costs ~1.1 MB per observation and is never shown to the policy."""
    from factorio_maxxing.envs import RealFactorioEnv

    _, captured = install_fake_fle(monkeypatch, reset_result=({}, {}))
    RealFactorioEnv()
    assert captured["spec"].enable_vision is False


def test_vision_can_be_enabled(monkeypatch):
    from factorio_maxxing.envs import RealFactorioEnv

    _, captured = install_fake_fle(monkeypatch, reset_result=({}, {}))
    RealFactorioEnv(enable_vision=True)
    assert captured["spec"].enable_vision is True


def test_the_game_is_paused_between_steps_by_default(monkeypatch):
    """D36: FLE's own default, and the one measured runs keep."""
    from factorio_maxxing.envs import RealFactorioEnv

    fake_env, _ = install_fake_fle(monkeypatch, reset_result=({}, {}))
    env = RealFactorioEnv()
    assert fake_env.pause_after_action is True
    assert env.pause_after_action is True


def test_the_pause_can_be_turned_off_for_a_watchable_run(monkeypatch):
    """The flag has to land on FLE's env, not just on ours - FLE is what reads it."""
    from factorio_maxxing.envs import RealFactorioEnv

    fake_env, _ = install_fake_fle(monkeypatch, reset_result=({}, {}))
    env = RealFactorioEnv(pause_after_action=False)
    assert fake_env.pause_after_action is False
    assert env.pause_after_action is False


def test_construction_clears_a_pause_left_by_an_earlier_run(monkeypatch):
    """D39: FLE's unpause no-ops when its own flag disagrees with the game.

    The flag is False on a fresh instance whatever the game is actually doing, so
    without this the world never ticks and the agent cannot move.
    """
    from factorio_maxxing.envs import RealFactorioEnv

    fake_env, _ = install_fake_fle(monkeypatch, reset_result=({}, {}))
    RealFactorioEnv()
    assert fake_env.instance.rcon_unpaused is True
    assert fake_env.instance._is_paused is False


def test_the_pause_is_cleared_even_when_pausing_stays_on(monkeypatch):
    """A paused game is inherited the same way whatever this run intends afterwards."""
    from factorio_maxxing.envs import RealFactorioEnv

    fake_env, _ = install_fake_fle(monkeypatch, reset_result=({}, {}))
    RealFactorioEnv(pause_after_action=True)
    assert fake_env.instance.rcon_unpaused is True


def test_an_environment_without_an_instance_is_tolerated(monkeypatch):
    """Defensive: FLE moving the attribute must not take every live run with it."""
    from factorio_maxxing.envs import RealFactorioEnv

    fake_env, _ = install_fake_fle(monkeypatch, reset_result=({}, {}))
    del fake_env.instance
    RealFactorioEnv()  # must not raise


def test_task_key_and_run_idx_reach_the_factory(monkeypatch):
    from factorio_maxxing.envs import RealFactorioEnv

    _, captured = install_fake_fle(monkeypatch, reset_result=({}, {}))
    RealFactorioEnv(task_key="iron_plate_throughput", run_idx=2)
    assert captured["spec"].task_key == "iron_plate_throughput"
    assert captured["run_idx"] == 2


def test_an_unknown_task_key_is_rejected(monkeypatch):
    from factorio_maxxing.envs import RealFactorioEnv

    install_fake_fle(monkeypatch, reset_result=({}, {}), spec_info={"open_play"})
    with pytest.raises(ValueError, match="unknown FLE task key"):
        RealFactorioEnv(task_key="not_a_task")


def test_close_releases_the_instance(monkeypatch):
    from factorio_maxxing.envs import RealFactorioEnv

    fake, _ = install_fake_fle(monkeypatch, reset_result=({}, {}))
    env = RealFactorioEnv()
    env.close()
    assert fake.closed is True


def test_real_env_satisfies_the_protocol(monkeypatch):
    from factorio_maxxing.envs import RealFactorioEnv

    install_fake_fle(monkeypatch, reset_result=({}, {}))
    assert isinstance(RealFactorioEnv(), EnvProtocol)

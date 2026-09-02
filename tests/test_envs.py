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

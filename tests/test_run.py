"""Tests for the CLI and configuration loading.

Build-plan section 19 item 14. run.py owns argument parsing and component
construction only; the behaviour it wires together is tested elsewhere.
"""

import json
import logging
from dataclasses import fields
from pathlib import Path

import pytest

from factorio_maxxing.human import InteractiveHuman, NoHuman, ScriptedHuman
from factorio_maxxing.run import (
    Config,
    ConfigError,
    build_detector,
    build_environment,
    build_human,
    build_parser,
    build_verifier,
    load_api_reference,
    load_config,
    load_hints,
    main,
    resolve_config,
)
from factorio_maxxing.trajectory import read_trajectory
from factorio_maxxing.verifier import LLMVerifier

EXAMPLE_CONFIG = Path("configs/harness.example.json")

SMOKE_ARGS = [
    "--goal",
    "Build a working iron mining setup",
    "--mock",
    "--policy-model",
    "stub",
    "--verifier-model",
    "stub",
    "--human",
    "none",
]


def parse(*argv):
    return build_parser().parse_args(list(argv))


def write_config(tmp_path, **values):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_defaults_match_the_documented_config():
    config = Config()
    assert config.policy_model == "claude-haiku-4-5"
    assert config.verifier_model == "claude-haiku-4-5"
    assert config.human == "interactive"
    assert config.stuck_detector == "consecutive_failures+error_signature"
    assert config.stuck_threshold == 3
    assert config.max_interventions_without_progress == 3
    assert config.verification_interval == 1
    assert config.max_steps == 32
    assert config.history_length == 16
    assert config.environment == "mock"
    assert config.pause_after_action is True
    assert config.enable_vision is False
    assert config.trajectory_dir == "trajectories"


def test_the_example_config_matches_the_config_fields():
    data = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert set(data) == {field.name for field in fields(Config)}


def test_the_example_config_is_the_defaults_plus_the_api_reference():
    """The example is a complete working config, so it points at the API reference.

    `api_reference` is the one field that cannot default in code: the default would
    have to be a relative path, and `load_api_reference` exits 2 on a missing file,
    so any run from outside the repository root would fail (D28, D31).
    """
    data = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert Config(**{**data, "api_reference": ""}) == Config()
    assert data["api_reference"] == "configs/fle_api_reference.md"


def test_the_example_config_api_reference_file_exists():
    """Guards the shipped reference: a missing file would only surface in a live run."""
    path = Path(json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))["api_reference"])
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "```types" in text
    assert "```methods" in text
    assert "manual for the tools" in text


def test_no_api_key_field_exists():
    """API keys come from the environment, never a config file (D27).

    "key" alone is too crude a signal - `task_key` names an FLE task, not a credential -
    so credential-shaped words are matched directly and the "key" fields are pinned by
    name. A future `openai_key` fails on the second assertion.
    """
    names = {field.name for field in fields(Config)}
    credentialish = ("secret", "token", "password", "credential", "api_key", "apikey")
    assert not [n for n in names if any(word in n for word in credentialish)]
    assert {n for n in names if "key" in n} == {"task_key"}


def test_config_file_is_loaded(tmp_path):
    path = write_config(tmp_path, max_steps=8, human="none")
    config = resolve_config(parse("--goal", "g", "--config", str(path)))
    assert config.max_steps == 8
    assert config.human == "none"


def test_command_line_overrides_the_config_file(tmp_path):
    path = write_config(tmp_path, max_steps=8, policy_model="claude-haiku-4-5")
    config = resolve_config(
        parse(
            "--goal",
            "g",
            "--config",
            str(path),
            "--max-steps",
            "4",
            "--policy-model",
            "stub",
        )
    )
    assert config.max_steps == 4
    assert config.policy_model == "stub"


def test_unknown_config_keys_are_rejected(tmp_path):
    path = write_config(tmp_path, max_stpes=8)
    with pytest.raises(ConfigError, match="unknown config key"):
        load_config(path)


def test_a_config_that_is_not_an_object_is_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a JSON object"):
        load_config(path)


def test_mock_and_live_flags_select_the_environment():
    assert resolve_config(parse("--goal", "g", "--mock")).environment == "mock"
    assert resolve_config(parse("--goal", "g", "--live")).environment == "live"


def _live_failure(monkeypatch, exc):
    """Force RealFactorioEnv to fail a given way, whether or not FLE is installed.

    Monkeypatching keeps these tests identical on Windows, where FLE is absent, and
    inside WSL, where it is present and would otherwise open a real connection.
    """

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr("factorio_maxxing.run.RealFactorioEnv", boom)


def test_live_without_fle_installed_names_the_extra(monkeypatch):
    _live_failure(monkeypatch, ImportError("No module named 'fle'"))
    with pytest.raises(ConfigError, match=r"\.\[fle\]"):
        build_environment(Config(environment="live"))


def test_live_without_a_running_cluster_names_the_command(monkeypatch):
    _live_failure(monkeypatch, RuntimeError("No Factorio containers available"))
    with pytest.raises(ConfigError, match="fle cluster start"):
        build_environment(Config(environment="live"))


def test_live_with_an_unknown_task_key_is_refused(monkeypatch):
    _live_failure(monkeypatch, ValueError("unknown FLE task key: nope"))
    with pytest.raises(ConfigError, match="unknown FLE task key"):
        build_environment(Config(environment="live", task_key="nope"))


def test_live_passes_the_configured_task_key(monkeypatch):
    seen = {}

    def record(task_key, **kwargs):
        seen["task_key"] = task_key
        return object()

    monkeypatch.setattr("factorio_maxxing.run.RealFactorioEnv", record)
    build_environment(Config(environment="live", task_key="iron_plate_throughput"))
    assert seen["task_key"] == "iron_plate_throughput"


def test_live_passes_the_configured_pause(monkeypatch):
    """D36: FLE reads this flag, so it has to survive the trip through run.py."""
    seen = {}

    def record(task_key, pause_after_action, **kwargs):
        seen["pause_after_action"] = pause_after_action
        return object()

    monkeypatch.setattr("factorio_maxxing.run.RealFactorioEnv", record)
    build_environment(Config(environment="live", pause_after_action=False))
    assert seen["pause_after_action"] is False

    build_environment(Config(environment="live"))
    assert seen["pause_after_action"] is True


def test_live_passes_the_configured_vision(monkeypatch):
    seen = {}

    def record(task_key, pause_after_action, enable_vision, **kwargs):
        seen["enable_vision"] = enable_vision
        return object()

    monkeypatch.setattr("factorio_maxxing.run.RealFactorioEnv", record)
    build_environment(Config(environment="live", enable_vision=True))
    assert seen["enable_vision"] is True

    build_environment(Config(environment="live"))
    assert seen["enable_vision"] is False


def test_vision_flag_turns_rendering_on():
    assert resolve_config(parse("--goal", "g")).enable_vision is False
    assert resolve_config(parse("--goal", "g", "--vision")).enable_vision is True


def test_an_absent_vision_flag_does_not_override_a_config_file(tmp_path):
    path = tmp_path / "c.json"
    path.write_text('{"enable_vision": true}', encoding="utf-8")
    assert resolve_config(parse("--goal", "g", "--config", str(path))).enable_vision


def test_no_pause_flag_turns_the_pause_off():
    assert resolve_config(parse("--goal", "g")).pause_after_action is True
    assert resolve_config(parse("--goal", "g", "--no-pause")).pause_after_action is False


def test_an_absent_no_pause_flag_does_not_override_a_config_file(tmp_path):
    """store_true defaults to False, which would silently beat a config file's True."""
    path = tmp_path / "c.json"
    path.write_text('{"pause_after_action": true}', encoding="utf-8")
    config = resolve_config(parse("--goal", "g", "--config", str(path)))
    assert config.pause_after_action is True


def test_the_watchable_config_is_live_unpaused_and_slower_to_ask(tmp_path):
    """The demonstration config: a run you can watch, that does not ask every 3 steps."""
    data = json.loads(Path("configs/live-watchable.json").read_text(encoding="utf-8"))
    assert set(data) == {field.name for field in fields(Config)}

    config = Config(**data)
    assert config.environment == "live"
    assert config.pause_after_action is False
    assert config.enable_vision is True
    assert config.stuck_threshold == 8
    assert config.max_interventions_without_progress == 5
    assert Path(config.api_reference).is_file()


def test_task_key_defaults_to_open_play():
    assert Config().task_key == "open_play"
    assert resolve_config(parse("--goal", "g")).task_key == "open_play"


def test_task_key_is_overridable_from_the_command_line():
    args = parse("--goal", "g", "--live", "--task-key", "steel_plate_throughput")
    assert resolve_config(args).task_key == "steel_plate_throughput"


def test_an_unknown_environment_is_refused():
    with pytest.raises(ConfigError, match="unknown environment"):
        build_environment(Config(environment="sandbox"))


def test_human_backends_are_selected_by_name():
    assert isinstance(build_human(Config(human="none"), None), NoHuman)
    assert isinstance(build_human(Config(human="interactive"), None), InteractiveHuman)


def test_scripted_human_requires_hints():
    with pytest.raises(ConfigError, match="requires --hints"):
        build_human(Config(human="scripted"), None)


def test_an_unknown_human_backend_is_refused():
    with pytest.raises(ConfigError, match="unknown human backend"):
        build_human(Config(human="telepathy"), None)


def test_hints_are_read_as_strings_or_objects(tmp_path):
    path = tmp_path / "hints.json"
    path.write_text(
        json.dumps(["fuel it", {"text": "place it on ore", "original_step": 7}]),
        encoding="utf-8",
    )
    hints = load_hints(path)
    assert [h.text for h in hints] == ["fuel it", "place it on ore"]
    assert [h.original_step for h in hints] == [None, 7]


def test_malformed_hints_are_refused(tmp_path):
    path = tmp_path / "hints.json"
    path.write_text(json.dumps([{"note": "wrong key"}]), encoding="utf-8")
    with pytest.raises(ConfigError, match="text field"):
        load_hints(path)


def test_a_scripted_human_is_built_from_a_hint_file(tmp_path):
    path = tmp_path / "hints.json"
    path.write_text(json.dumps(["fuel it"]), encoding="utf-8")
    human = build_human(Config(human="scripted"), str(path))
    assert isinstance(human, ScriptedHuman)
    assert human.hints[0].text == "fuel it"


def test_an_unknown_stuck_detector_is_refused():
    with pytest.raises(ConfigError, match="unknown stuck detector"):
        build_detector(Config(stuck_detector="vibes"))


def test_offline_smoke_run(tmp_path, capsys):
    """build-plan section 22, executed."""
    exit_code = main([*SMOKE_ARGS, "--trajectory-dir", str(tmp_path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "completed:     True" in output
    assert "interventions: 0" in output


def test_the_smoke_run_writes_a_trajectory(tmp_path):
    main([*SMOKE_ARGS, "--trajectory-dir", str(tmp_path)])
    written = list(tmp_path.glob("*.jsonl"))
    assert len(written) == 1

    records = read_trajectory(written[0])
    assert [r["type"] for r in records[:3]] == ["step", "llm_call", "verification"]
    assert records[-1]["done"] is True
    assert len({r["run_id"] for r in records}) == 1


def test_a_real_model_without_a_key_exits_with_the_variable_name(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    exit_code = main(
        [
            "--goal",
            "g",
            "--mock",
            "--policy-model",
            "claude-haiku-4-5",
            "--human",
            "none",
            "--trajectory-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_an_unroutable_model_exits_cleanly(tmp_path, capsys):
    exit_code = main(
        [
            "--goal",
            "g",
            "--mock",
            "--policy-model",
            "some-unknown-model",
            "--human",
            "none",
            "--trajectory-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 2
    assert "cannot route model" in capsys.readouterr().err


def test_a_stub_policy_can_run_against_a_real_verifier_model(monkeypatch):
    """D5: policy and verifier models stay independently configurable."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    verifier = build_verifier(Config(verifier_model="claude-haiku-4-5"))
    assert isinstance(verifier, LLMVerifier)


def test_a_bad_config_path_exits_cleanly(tmp_path, capsys):
    exit_code = main([*SMOKE_ARGS, "--config", str(tmp_path / "missing.json")])
    assert exit_code == 2
    assert "error:" in capsys.readouterr().err


def test_an_invalid_goal_exits_cleanly(tmp_path, capsys):
    exit_code = main(
        [
            "--goal",
            "   ",
            "--mock",
            "--policy-model",
            "stub",
            "--verifier-model",
            "stub",
            "--human",
            "none",
            "--trajectory-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 2
    assert "non-empty" in capsys.readouterr().err


def test_replay_coverage_is_reported_at_end_of_run(tmp_path, caplog):
    """D23: the loop stays backend-agnostic, so run.py reports hint usage."""
    hints = tmp_path / "hints.json"
    hints.write_text(json.dumps(["fuel it", "unused hint"]), encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="factorio_maxxing.human"):
        main(
            [
                "--goal",
                "Build a working iron mining setup",
                "--mock",
                "--policy-model",
                "stub",
                "--verifier-model",
                "stub",
                "--human",
                "scripted",
                "--hints",
                str(hints),
                "--trajectory-dir",
                str(tmp_path),
            ]
        )
    assert "scripted hints" in caplog.text


def test_goal_notes_reach_the_goal(tmp_path, capsys):
    main([*SMOKE_ARGS, "--notes", "ore is north", "--trajectory-dir", str(tmp_path)])
    assert "completed:     True" in capsys.readouterr().out


def test_an_api_reference_file_is_loaded(tmp_path):
    path = tmp_path / "api.md"
    path.write_text("place_entity(name, position)", encoding="utf-8")
    assert load_api_reference(Config(api_reference=str(path))) == (
        "place_entity(name, position)"
    )


def test_no_api_reference_configured_means_empty():
    assert load_api_reference(Config()) == ""


def test_a_missing_api_reference_file_exits_cleanly(tmp_path, capsys):
    exit_code = main(
        [
            *SMOKE_ARGS,
            "--api-reference",
            str(tmp_path / "missing.md"),
            "--trajectory-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 2
    assert "error:" in capsys.readouterr().err

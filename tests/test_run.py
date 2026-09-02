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
    load_config,
    load_hints,
    main,
    resolve_config,
)
from factorio_maxxing.trajectory import read_trajectory

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
    assert config.trajectory_dir == "trajectories"


def test_the_example_config_matches_the_config_fields():
    data = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert set(data) == {field.name for field in fields(Config)}
    assert Config(**data) == Config()


def test_no_api_key_field_exists():
    """API keys come from the environment, never a config file."""
    names = {field.name for field in fields(Config)}
    assert not [name for name in names if "key" in name or "token" in name]


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


def test_live_is_refused_with_a_pointer_to_phase_5():
    with pytest.raises(ConfigError, match="Phase 5 item 17"):
        build_environment(Config(environment="live"))


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


def test_a_real_model_is_refused_with_a_pointer_to_phase_4(tmp_path, capsys):
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
    assert "Phase 4 item 15" in capsys.readouterr().err


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

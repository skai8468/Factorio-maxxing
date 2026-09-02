"""Command-line entry point.

See docs/build-plan.md sections 12, 19 (item 14), 22 and 24.

This module owns argument parsing, configuration loading and component construction.
It holds no business logic: every decision it makes is which object to build, never
what the harness should do.

API keys come from environment variables. They are never read from a config file.

Offline smoke run (build-plan section 22):

    python -m factorio_maxxing.run --goal "Build a working iron mining setup" \\
        --mock --policy-model stub --verifier-model stub --human none
"""

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from factorio_maxxing.envs import MockFactorioEnv, MockFrame
from factorio_maxxing.goal import Goal
from factorio_maxxing.human import Hint, InteractiveHuman, NoHuman, ScriptedHuman
from factorio_maxxing.llm import APIClient, LLMClient, StubLLMClient
from factorio_maxxing.loop import run_goal
from factorio_maxxing.stuck import default_detector
from factorio_maxxing.trajectory import TrajectoryRecorder
from factorio_maxxing.verifier import LLMVerifier, StubVerifier, VerificationResult

STUB_MODEL = "stub"
DEFAULT_DETECTOR = "consecutive_failures+error_signature"

# A three-step demonstration fixture for the offline smoke run: a drill is placed,
# fuelled, and starts working. It exercises the loop; it does not simulate Factorio.
DEMO_OBSERVATIONS: list[dict[str, Any]] = [
    {
        "inventory": {"burner-mining-drill": 1, "coal": 4},
        "entities": [],
        "raw_text": "placed nothing yet",
    },
    {
        "inventory": {"coal": 4},
        "entities": [
            {
                "name": "burner-mining-drill",
                "position": {"x": 12, "y": -3},
                "status": "NO_FUEL",
            }
        ],
        "raw_text": "placed burner-mining-drill at (12, -3)",
    },
    {
        "inventory": {"iron-ore": 6},
        "entities": [
            {
                "name": "burner-mining-drill",
                "position": {"x": 12, "y": -3},
                "status": "WORKING",
            }
        ],
        "flows": {"output": {"iron-ore": 6.0}},
        "raw_text": "inserted coal; the drill is running",
    },
]

DEMO_POLICIES = [
    "PLANNING: place the drill on ore.\n"
    "```python\nplace_entity('burner-mining-drill', position=(12, -3))\n```",
    "PLANNING: fuel it.\n```python\ninsert_item('coal', 'burner-mining-drill')\n```",
    "PLANNING: confirm it is running.\n```python\nprint(get_entities())\n```",
]

DEMO_VERDICTS = [
    VerificationResult(False, "the drill is not placed yet"),
    VerificationResult(False, "the drill has no fuel"),
    VerificationResult(True, "the drill is WORKING and iron ore is accumulating"),
]


class ConfigError(Exception):
    """A configuration or command line the harness cannot act on."""


@dataclass
class Config:
    """The build-plan section 12 configuration. API keys are never fields here."""

    policy_model: str = "claude-haiku-4-5"
    verifier_model: str = "claude-haiku-4-5"
    human: str = "interactive"
    stuck_detector: str = DEFAULT_DETECTOR
    stuck_threshold: int = 3
    max_interventions_without_progress: int = 3
    verification_interval: int = 1
    max_steps: int = 32
    history_length: int = 16
    environment: str = "mock"
    trajectory_dir: str = "trajectories"
    api_reference: str = ""
    """Path to a file describing the functions the environment provides. Empty
    means the policy is told nothing about the API and will invent one."""


def load_config(path: Path | str) -> dict[str, Any]:
    """Read a config file, rejecting keys the harness does not understand."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: config must be a JSON object")

    known = {field.name for field in fields(Config)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"{path}: unknown config key(s): {', '.join(unknown)}")
    return data


def load_hints(path: Path | str) -> list[Hint]:
    """Read a scripted intervention set.

    Accepts plain strings or ``{"text": ..., "original_step": ...}`` objects. Order in
    the file is replay order: the Nth stuck event receives the Nth hint (D8).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ConfigError(f"{path}: hints must be a JSON list")

    hints = []
    for entry in data:
        if isinstance(entry, str):
            hints.append(Hint(text=entry))
        elif isinstance(entry, dict) and "text" in entry:
            hints.append(
                Hint(text=entry["text"], original_step=entry.get("original_step"))
            )
        else:
            raise ConfigError(f"{path}: each hint must be a string or have a text field")
    return hints


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m factorio_maxxing.run",
        description="Run one goal through the human-assisted Factorio harness.",
    )
    parser.add_argument("--goal", required=True, help="the goal description")
    parser.add_argument("--config", help="path to a JSON config file")
    parser.add_argument("--notes", help="optional hints supplied with the goal")

    environment = parser.add_mutually_exclusive_group()
    environment.add_argument(
        "--mock", action="store_true", help="run against MockFactorioEnv (offline)"
    )
    environment.add_argument(
        "--live", action="store_true", help="run against a real Factorio server"
    )

    parser.add_argument("--policy-model")
    parser.add_argument("--verifier-model")
    parser.add_argument("--human", choices=["none", "interactive", "scripted"])
    parser.add_argument("--hints", help="JSON hint list, required by --human scripted")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--verification-interval", type=int)
    parser.add_argument("--stuck-threshold", type=int)
    parser.add_argument("--history-length", type=int)
    parser.add_argument("--trajectory-dir")
    parser.add_argument(
        "--api-reference", help="file describing the environment API for the policy"
    )
    return parser


def resolve_config(args: argparse.Namespace) -> Config:
    """Merge defaults, then the config file, then command-line overrides."""
    values = load_config(args.config) if args.config else {}

    if args.mock:
        values["environment"] = "mock"
    if args.live:
        values["environment"] = "live"

    overrides = {
        "policy_model": args.policy_model,
        "verifier_model": args.verifier_model,
        "human": args.human,
        "max_steps": args.max_steps,
        "verification_interval": args.verification_interval,
        "stuck_threshold": args.stuck_threshold,
        "history_length": args.history_length,
        "trajectory_dir": args.trajectory_dir,
        "api_reference": args.api_reference,
    }
    values.update({k: v for k, v in overrides.items() if v is not None})
    return Config(**values)


def build_environment(config: Config) -> MockFactorioEnv:
    if config.environment == "live":
        raise ConfigError(
            "a live Factorio environment arrives at Phase 5 item 17; "
            "use --mock until then"
        )
    if config.environment != "mock":
        raise ConfigError(f"unknown environment: {config.environment}")
    return MockFactorioEnv(
        reset_observation={"inventory": {"burner-mining-drill": 1, "coal": 4}},
        frames=[MockFrame(observation=obs) for obs in DEMO_OBSERVATIONS],
    )


def build_policy_client(config: Config) -> LLMClient:
    if config.policy_model == STUB_MODEL:
        return StubLLMClient(DEMO_POLICIES, model=config.policy_model)
    return _api_client(config.policy_model)


def build_verifier(config: Config):
    """Build the verifier on its own model, configured independently (D5)."""
    if config.verifier_model == STUB_MODEL:
        return StubVerifier(DEMO_VERDICTS)
    return LLMVerifier(_api_client(config.verifier_model))


def build_human(config: Config, hints_path: str | None):
    if config.human == "none":
        return NoHuman()
    if config.human == "interactive":
        return InteractiveHuman()
    if config.human == "scripted":
        if not hints_path:
            raise ConfigError("--human scripted requires --hints")
        return ScriptedHuman(load_hints(hints_path))
    raise ConfigError(f"unknown human backend: {config.human}")


def load_api_reference(config: Config) -> str:
    """Read the environment API description handed to the policy.

    Empty is allowed and means the policy is told nothing about the API - correct
    against the mock, which ignores submitted code, and wrong against live Factorio.
    """
    if not config.api_reference:
        return ""
    return Path(config.api_reference).read_text(encoding="utf-8")


def build_detector(config: Config):
    if config.stuck_detector != DEFAULT_DETECTOR:
        raise ConfigError(
            f"unknown stuck detector: {config.stuck_detector}; "
            f"the only detector is {DEFAULT_DETECTOR}"
        )
    return default_detector(config.stuck_threshold)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)

    try:
        config = resolve_config(args)
        goal = Goal(description=args.goal, max_steps=config.max_steps, notes=args.notes)
        env = build_environment(config)
        policy_client = build_policy_client(config)
        verifier = build_verifier(config)
        human = build_human(config, args.hints)
        detector = build_detector(config)
        api_reference = load_api_reference(config)
    except (ConfigError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    run_id = f"{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    path = Path(config.trajectory_dir) / f"{run_id}.jsonl"

    with TrajectoryRecorder(path, run_id=run_id) as recorder:
        result = run_goal(
            goal,
            env,
            policy_client,
            verifier,
            human,
            detector,
            recorder,
            api_reference=api_reference,
            verification_interval=config.verification_interval,
            history_length=config.history_length,
            max_interventions=config.max_interventions_without_progress,
        )

    # D23: the loop stays backend-agnostic, so replay coverage is reported here.
    if isinstance(human, ScriptedHuman):
        human.log_usage()

    _print_summary(result)
    return 0


def _api_client(model: str) -> APIClient:
    """Build a live client, turning a routing or key failure into a clean CLI error."""
    try:
        return APIClient(model)
    except ValueError as error:
        raise ConfigError(str(error)) from error


def _print_summary(result) -> None:
    print("")
    print(f"goal:          {result.goal.description}")
    print(f"completed:     {result.completed}")
    print(f"steps used:    {result.steps_used} of {result.goal.max_steps}")
    print(f"interventions: {result.interventions}")
    print(f"reason:        {result.reason}")
    print(f"trajectory:    {result.trajectory_path}")


if __name__ == "__main__":
    raise SystemExit(main())

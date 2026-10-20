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
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from factorio_maxxing.envs import (
    DEFAULT_TASK_KEY,
    EnvProtocol,
    MockFactorioEnv,
    MockFrame,
    RealFactorioEnv,
)
from factorio_maxxing.goal import Goal, open_ended_goal
from factorio_maxxing.history_report import HistoryReporter
from factorio_maxxing.human import Hint, InteractiveHuman, NoHuman, ScriptedHuman
from factorio_maxxing.llm import APIClient, LLMClient, StubLLMClient
from factorio_maxxing.loop import run_goal
from factorio_maxxing.pricing import SpendMeter, format_summary, has_price, summarize
from factorio_maxxing.stuck import default_detector, open_ended_detector
from factorio_maxxing.trajectory import TrajectoryRecorder, read_trajectory
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
    non_done_threshold: int | None = None
    """Consecutive non-DONE verifications before help is requested. None uses
    `stuck_threshold`, as every run before D50 did.

    `stuck_threshold` then governs only repeated errors. A long goal is non-DONE for
    most of its length, so at 3 the detector asked for help every three steps of a
    healthy build; a long goal sets this higher, as a backstop (D50)."""
    max_interventions_without_progress: int = 3
    verification_interval: int = 1
    max_steps: int = 32
    open_ended: bool = False
    """Run the open-ended evaluation goal instead of a ``--goal`` (D67). No verifier is
    built, the run ends only at ``max_steps`` (or if the environment ends it), and
    `max_interventions_without_progress`, `verification_interval`, `verifier_model` and
    `non_done_threshold` have no effect."""
    backstop_steps: int = 20
    max_spend_usd: float | None = 50.0
    """Stop the run once its estimated spend reaches this many US dollars (D65, D68).

    Every model call is priced as it is made, from raw tokens and the pricing table;
    the figure is kept in memory and printed, never recorded. None turns the ceiling
    off. With a ceiling set, a model missing from the pricing table is refused before
    the run starts, since its calls could not be counted."""
    """Open-ended runs only: steps without a request for help before the harness asks
    anyway - the stand-in for the non-DONE backstop, which needs a verifier (D67)."""
    history_length: int = 8
    """Steps shown verbatim; older steps reach the policy through the history report
    (D66). FLE's agent keeps 8."""
    history_report_interval: int = 8
    """Steps between history reports; 0 turns the report off (D66)."""
    environment: str = "mock"
    task_key: str = DEFAULT_TASK_KEY
    """Which FLE task backs a live run. Used only when environment is "live".

    `open_play` is a neutral sandbox, not a task whose success criteria we adopt: the
    Goal drives the policy and our own verifier decides completion (D6). FLE's task
    verification stays unused at M0/M1."""
    starting_inventory: dict[str, int] = field(default_factory=dict)
    """What the agent holds at reset. Used only when environment is "live".

    Empty by default, which is what `open_play` gives. This is a scenario parameter,
    not a hint: it decides what a goal actually measures, since "place a burner mining
    drill" with an empty inventory silently includes crafting one from raw stone and
    ore (D40)."""
    enable_vision: bool = False
    """Whether FLE renders a map image into every observation. Used only when live.

    Off by default: the image is never shown to the policy, so it buys nothing during
    a run. It is what a timelapse is built from afterwards, and it costs render time
    per step plus a base64 PNG in every recorded observation (D38)."""
    pause_after_action: bool = True
    """Whether FLE freezes the game tick between steps. Used only when live.

    FLE's default is True, which keeps world time from passing while the policy and
    verifier are called. False lets the world run on, which is what makes a run
    watchable through a game client, at the cost of a slightly stale observation
    (D36). Demonstrations turn it off; measured runs do not."""
    game_speed: float | None = None
    """Game speed multiplier for a live run. None keeps FLE's own, which is 10x.

    A watching game client cannot keep up with 10x and reports the server as not
    responding, so watched runs set 1. Measured runs keep FLE's default (D46)."""
    fast_mode: bool = True
    """FLE's fast mode, in which the agent teleports and builds instantly. Live only.

    False is FLE's slow mode: the character visibly walks. It changes what the agent's
    tools return, so it is for watching a run, never for measuring one (D48)."""
    step_timeout: float | None = None
    """Seconds FLE may spend executing one step. Live only. None keeps FLE's 120.

    At 1x a step that harvests and sleeps can outlive 120 s, and FLE's timeout cannot
    stop a running step - it keeps executing beside the next one (D53, D58). Watched
    runs at 1x set it higher; measured runs at 10x keep FLE's."""
    peaceful: bool = True
    """Remove all enemies after every reset and step. Live only.

    On by default, which is what FLE's own ``peaceful=True`` intends and fails to
    deliver: an agent walked into a nest, died, and lost its whole inventory without
    being told (D53). False leaves the map's enemies alone."""
    trajectory_dir: str = "trajectories"
    dump_prompts: str = ""
    """Directory to write every prompt sent to the policy's model into, one file per
    call, for auditing exactly what a model read. Empty writes nothing."""
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
    parser.add_argument(
        "--goal", help="the goal description; required unless the run is open-ended"
    )
    parser.add_argument(
        "--open-ended",
        action="store_true",
        help="run the open-ended evaluation goal, ending only at the step cap (D67)",
    )
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
    parser.add_argument(
        "--non-done-threshold",
        type=int,
        help="non-DONE verdicts in a row before help is requested (D50)",
    )
    parser.add_argument(
        "--backstop-steps",
        type=int,
        help="open-ended runs: steps without a request for help before asking (D67)",
    )
    parser.add_argument(
        "--max-spend-usd",
        type=float,
        help="stop once the run's estimated spend reaches this many dollars (D68)",
    )
    parser.add_argument("--history-length", type=int)
    parser.add_argument(
        "--history-report-interval",
        type=int,
        help="steps between history reports; 0 turns them off (D66)",
    )
    parser.add_argument(
        "--dump-prompts",
        metavar="DIR",
        help="write every prompt sent to the policy's model into DIR, one file per call",
    )
    parser.add_argument("--trajectory-dir")
    parser.add_argument(
        "--api-reference", help="file describing the environment API for the policy"
    )
    parser.add_argument("--task-key", help="FLE task backing a live run")
    parser.add_argument(
        "--vision",
        action="store_true",
        help="render a map image each step, so a timelapse can be built afterwards",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="let the world run between steps, so a live run can be watched",
    )
    parser.add_argument(
        "--game-speed",
        type=float,
        help="game speed for a live run; 1 lets a game client keep up (FLE uses 10)",
    )
    parser.add_argument(
        "--step-timeout",
        type=float,
        help="seconds FLE may spend on one step (FLE uses 120)",
    )
    parser.add_argument(
        "--slow",
        action="store_true",
        help="FLE's slow mode, so the agent walks instead of teleporting (watching only)",
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
        "non_done_threshold": args.non_done_threshold,
        "open_ended": True if args.open_ended else None,
        "backstop_steps": args.backstop_steps,
        "max_spend_usd": args.max_spend_usd,
        "history_length": args.history_length,
        "history_report_interval": args.history_report_interval,
        "dump_prompts": args.dump_prompts,
        "trajectory_dir": args.trajectory_dir,
        "api_reference": args.api_reference,
        "task_key": args.task_key,
        "pause_after_action": False if args.no_pause else None,
        "enable_vision": True if args.vision else None,
        "game_speed": args.game_speed,
        "fast_mode": False if args.slow else None,
        "step_timeout": args.step_timeout,
    }
    values.update({k: v for k, v in overrides.items() if v is not None})
    return Config(**values)


def build_goal(config: Config, description: str | None, notes: str | None) -> Goal:
    """The run's goal: the open-ended evaluation goal, or the ``--goal`` given (D67)."""
    if config.open_ended:
        if description:
            raise ConfigError("--goal cannot be combined with an open-ended run")
        return open_ended_goal(config.max_steps, notes=notes)
    if description is None:
        raise ConfigError("--goal is required unless the run is open-ended")
    return Goal(description=description, max_steps=config.max_steps, notes=notes)


def build_environment(config: Config) -> EnvProtocol:
    if config.environment == "live":
        return _build_live_environment(config)
    if config.environment != "mock":
        raise ConfigError(f"unknown environment: {config.environment}")
    return MockFactorioEnv(
        reset_observation={"inventory": {"burner-mining-drill": 1, "coal": 4}},
        frames=[MockFrame(observation=obs) for obs in DEMO_OBSERVATIONS],
    )


def _build_live_environment(config: Config) -> EnvProtocol:
    """Connect to a running Factorio cluster, translating setup failures.

    The two ways this fails are both operator problems with specific fixes, so neither
    is allowed to surface as a bare traceback.
    """
    try:
        return RealFactorioEnv(
            task_key=config.task_key,
            pause_after_action=config.pause_after_action,
            enable_vision=config.enable_vision,
            starting_inventory=config.starting_inventory,
            game_speed=config.game_speed,
            fast_mode=config.fast_mode,
            peaceful=config.peaceful,
            step_timeout=config.step_timeout,
        )
    except ImportError as exc:
        raise ConfigError(
            "live Factorio needs FLE installed: pip install -e '.[fle]' inside the "
            f"WSL2 virtualenv (docs/fle-integration.md) - {exc}"
        ) from exc
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    except RuntimeError as exc:
        raise ConfigError(
            f"could not reach a Factorio container ({exc}). Start one with "
            "'fle cluster start -n 1 -s open_world' from ~/fle-work"
        ) from exc


def build_policy_client(config: Config) -> LLMClient:
    if config.policy_model == STUB_MODEL:
        client: LLMClient = StubLLMClient(DEMO_POLICIES, model=config.policy_model)
    else:
        client = _api_client(config.policy_model)
    if config.dump_prompts:
        client = PromptDumpingClient(client, Path(config.dump_prompts))
    return client


def build_reporter(config: Config, policy_client: LLMClient) -> HistoryReporter | None:
    """The history report uses the policy's own model and client, as FLE's agent does."""
    if config.history_report_interval < 0:
        raise ConfigError("history_report_interval must be 0 (off) or positive")
    if config.history_report_interval == 0:
        return None
    return HistoryReporter(policy_client, config.history_report_interval)


class PromptDumpingClient:
    """Writes each prompt to ``directory`` before passing it on, for auditing (D66)."""

    def __init__(self, inner: LLMClient, directory: Path):
        self.inner = inner
        self.directory = directory
        self.calls = 0
        directory.mkdir(parents=True, exist_ok=True)

    def generate(self, prompt):
        path = self.directory / f"call-{self.calls:04d}.txt"
        path.write_text(str(prompt), encoding="utf-8")
        self.calls += 1
        return self.inner.generate(prompt)


def build_verifier(config: Config):
    """Build the verifier on its own model, configured independently (D5).

    An open-ended run has nothing to verify, so it builds none (D67).
    """
    if config.open_ended:
        return None
    if config.verifier_model == STUB_MODEL:
        return StubVerifier(DEMO_VERDICTS)
    return LLMVerifier(_api_client(config.verifier_model))


def build_spend_meter(config: Config) -> SpendMeter:
    """The run's spend meter, refusing a ceiling it could not enforce (D68).

    The policy's model also writes the history report; the verifier's model is in use
    unless the run is open-ended. The offline stub costs nothing and needs no price.
    """
    if config.max_spend_usd is not None:
        models = [config.policy_model]
        if not config.open_ended:
            models.append(config.verifier_model)
        unpriced = [m for m in models if m != STUB_MODEL and not has_price(m)]
        if unpriced:
            raise ConfigError(
                f"no price for {', '.join(unpriced)}, so the spend ceiling cannot be "
                "enforced: add it to pricing.PRICES, or set max_spend_usd to null"
            )
    return SpendMeter(config.max_spend_usd)


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
    if config.open_ended:
        return open_ended_detector(config.stuck_threshold, config.backstop_steps)
    return default_detector(config.stuck_threshold, config.non_done_threshold)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)

    try:
        config = resolve_config(args)
        goal = build_goal(config, args.goal, args.notes)
        env = build_environment(config)
        policy_client = build_policy_client(config)
        reporter = build_reporter(config, policy_client)
        verifier = build_verifier(config)
        human = build_human(config, args.hints)
        detector = build_detector(config)
        spend = build_spend_meter(config)
        api_reference = load_api_reference(config)
    except (ConfigError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    run_id = f"{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    path = Path(config.trajectory_dir) / f"{run_id}.jsonl"

    try:
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
                reporter=reporter,
                spend=spend,
            )
    except KeyboardInterrupt:
        # The steps already taken were paid for: report them before exiting.
        print("\ninterrupted - usage so far:", file=sys.stderr)
        _print_usage(path)
        return 130
    finally:
        _close_environment(env)

    # D23: the loop stays backend-agnostic, so replay coverage is reported here.
    if isinstance(human, ScriptedHuman):
        human.log_usage()

    _print_summary(result)
    _print_usage(path)
    return 0


def _close_environment(env: EnvProtocol) -> None:
    """Release a live Factorio instance, if this environment holds one.

    Read by attribute rather than by type, so the mock needs no close() and any future
    environment that holds a connection gets cleaned up for free. A failure here must
    not mask the run's own outcome: the goal is finished and the trajectory is written
    by this point, so a cleanup problem is logged, never raised.
    """
    close = getattr(env, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception as error:  # noqa: BLE001 - cleanup must not mask the result
        logging.warning("closing the environment failed: %s", error)


def _api_client(model: str) -> APIClient:
    """Build a live client, turning a routing or key failure into a clean CLI error."""
    try:
        return APIClient(model)
    except ValueError as error:
        raise ConfigError(str(error)) from error


def _print_summary(result) -> None:
    print("")
    print(f"goal:          {result.goal.description}")
    completed = "n/a (open-ended)" if result.goal.open_ended else result.completed
    print(f"completed:     {completed}")
    print(f"steps used:    {result.steps_used} of {result.goal.max_steps}")
    print(f"interventions: {result.interventions}")
    print(f"reason:        {result.reason}")
    print(f"trajectory:    {result.trajectory_path}")


def _print_usage(path: Path) -> None:
    """Print the run's token usage and an estimated cost, read back from its trajectory.

    Priced here, at analysis time, and printed only: the trajectory keeps raw counts and
    no dollar figure (D9, D55). A trajectory that cannot be read is reported, never
    raised - the run's outcome is already decided.
    """
    try:
        records = read_trajectory(path)
    except (OSError, ValueError) as error:
        print(f"usage:         unavailable ({error})")
        return
    for line in format_summary(summarize(records)):
        print(line)


if __name__ == "__main__":
    raise SystemExit(main())

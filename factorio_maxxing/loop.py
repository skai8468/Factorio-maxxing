"""The M0/M1 control loop.

See docs/build-plan.md section 11, docs/architecture.md, and docs/decisions.md D12,
D20, D21 and D22.

This module owns control flow, termination and the counters, and nothing else. It
assembles no prompts (context.py) and formats no observations (rendering.py).

Two counters, per D12:

- the stuck window resets whenever help is requested, answered or not, so the agent
  gets a fresh window instead of the detector re-firing on the next step;
- ``interventions`` does not reset, and aborts the goal at ``max_interventions``. With
  no progress signal defined at M0/M1 this is a hard cap on interventions per goal.
"""

import logging
import re
from collections.abc import Sequence
from typing import Any

from factorio_maxxing import context
from factorio_maxxing.envs import Action, EnvProtocol
from factorio_maxxing.goal import Goal, GoalResult
from factorio_maxxing.history_report import HistoryReporter
from factorio_maxxing.human import HumanProtocol
from factorio_maxxing.llm import LLMClient, extract_policy
from factorio_maxxing.rendering import render_observation
from factorio_maxxing.stuck import StuckDetector
from factorio_maxxing.trajectory import TrajectoryRecorder
from factorio_maxxing.verifier import VERIFICATION_WINDOW, Verifier

MAX_INTERVENTIONS = 3
ERROR_KEYS = ("error", "stderr")
FLE_ERROR_FLAG = "error_occurred"
FLE_ERROR_TEXT = "result"


FORBIDDEN = re.compile(
    r"\binstance\b|rcon|send_command"
    r"|__globals__|__builtins__|__subclasses__|__dict__|__class__|__mro__"
    r"|[\"']\s*/(?:c|sc|silent-command|command)\b"
)
"""Ways out of the game's rules (D66). FLE's namespace hands policy code ``instance``,
the live ``FactorioInstance``: through ``instance.rcon_client`` a program can run any
Lua as an admin - spawn items, finish research - and through ``instance.namespaces`` it
reaches FLE's admin tools (measured 2026-10-20; the admin tools are not reachable by
name). Dunder introspection is how a program would rebuild those names indirectly."""

BLOCKED = (
    "Blocked by the harness: this program refers to {found!r}. The game instance, RCON "
    "and console commands give admin access to the game and are not available; use the "
    "API functions instead. The program was not run."
)


def forbidden_access(policy: str) -> str | None:
    """The first forbidden reference in a program, or None if it may run."""
    match = FORBIDDEN.search(policy or "")
    return match.group(0) if match else None


def execute_policy(
    policy: str, env: EnvProtocol, last_observation: dict[str, Any] | None = None
) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
    """Submit a policy to the environment and observe the result.

    A program that reaches for admin access is not submitted (D66): the step returns
    the previous observation unchanged, with the refusal as its execution error, and
    still counts as a step. This is also the boundary where repair strategies attach
    in later scope (architecture.md, extension points).
    """
    found = forbidden_access(policy)
    if found is not None:
        message = BLOCKED.format(found=found)
        observation = {**(last_observation or {}), "raw_text": message}
        return (
            observation,
            0.0,
            False,
            False,
            {FLE_ERROR_FLAG: True, FLE_ERROR_TEXT: message},
        )
    return env.step(Action(code=policy))


def execution_errors(
    observation: dict[str, Any], info: dict[str, Any] | None = None
) -> list[str]:
    """Pull execution errors out of a step's result.

    Measured against a live container, FLE reports failure in the step's ``info``
    dict - ``error_occurred`` as a bool beside the offending output in ``result`` -
    and populates neither of the observation keys this once looked for. The
    observation instead carries the same text in ``raw_text``, indistinguishable
    there from a successful step's output. ``info`` is therefore the authoritative
    signal and is read first (D35).

    The observation keys are kept for the mock and any environment that reports
    errors that way. An unrecognised shape yields no errors rather than raising.
    """
    if info and info.get(FLE_ERROR_FLAG):
        text = str(info.get(FLE_ERROR_TEXT) or observation.get("raw_text") or "").strip()
        if text:
            return [text]
    return [str(observation[key]) for key in ERROR_KEYS if observation.get(key)]


def run_goal(
    goal: Goal,
    env: EnvProtocol,
    policy_client: LLMClient,
    verifier: Verifier,
    human: HumanProtocol,
    detector: StuckDetector,
    recorder: TrajectoryRecorder,
    *,
    api_reference: str = "",
    verification_interval: int = 1,
    history_length: int = context.HISTORY_WINDOW,
    verification_window: int = VERIFICATION_WINDOW,
    max_interventions: int = MAX_INTERVENTIONS,
    reporter: HistoryReporter | None = None,
) -> GoalResult:
    """Run one goal to completion, failure, or abort.

    Returns as soon as the verifier says DONE, with no further policy call.

    History entries carry each step's observation rendered without entities - only the
    current observation lists them (D66). With a ``reporter``, a history report is
    rewritten after every ``reporter.interval`` steps and shown to the policy; its
    call is recorded as a ``summarizer`` llm_call and its text as a ``report`` record.
    """
    observation = env.reset()
    rendered = render_observation(observation)

    history: list[tuple[str, str]] = []
    report, report_until = "", None
    guidance: list[str] = []
    verifications: list[Any] = []
    errors: list[str] = []
    flows: list[Any] = []
    window: list[str] = []

    # Start of the current stuck window. Requesting help moves these forward, which is
    # how D12's reset is implemented: the detector holds no counters (D20).
    marks = dict(history=0, verifications=0, errors=0, flows=0)
    interventions = 0

    def result(completed: bool, steps_used: int, reason: str) -> GoalResult:
        return GoalResult(
            goal=goal,
            completed=completed,
            steps_used=steps_used,
            interventions=interventions,
            reason=reason,
            trajectory_path=recorder.path,
        )

    for step in range(goal.max_steps):
        prompt = context.build_prompt(
            goal,
            rendered,
            history,
            guidance,
            _latest_errors(errors),
            api_reference=api_reference,
            history_length=history_length,
            report=report,
            report_until=report_until,
        )
        response = policy_client.generate(prompt)
        policy = extract_policy(response.text)

        observation, reward, terminated, truncated, info = execute_policy(
            policy, env, observation
        )
        rendered = render_observation(observation)
        compact = render_observation(observation, include_entities=False)
        step_errors = execution_errors(observation, info)

        recorder.record_step(step, goal, policy, observation, reward, step_errors)
        recorder.record_llm_call(step, "policy", response)

        errors.append("\n".join(step_errors))
        flows.append(observation.get("flows"))

        if step % verification_interval == 0:
            verdict = verifier.check(goal, rendered, window[-verification_window:])
            _record_verifier_usage(recorder, step, verifier)
            verifications.append(verdict)
            recorder.record_verification(step, verdict)
            if verdict.done:
                return result(True, step + 1, verdict.reason)

        window.append(rendered)
        history.append((policy, compact))

        if reporter is not None and reporter.due(step):
            first = 0 if report_until is None else report_until + 1
            steps = [(i, *history[i]) for i in range(first, step + 1)]
            try:
                written = reporter.update(report, steps, rendered)
            except Exception as error:  # noqa: BLE001 - memory must not end a run
                logging.warning("history report failed at step %d: %s", step, error)
            else:
                recorder.record_llm_call(step, "summarizer", written)
                if written.text.strip():
                    report, report_until = written.text.strip(), step
                    recorder.record_report(step, report)

        if terminated or truncated:
            state = "terminated" if terminated else "truncated"
            return result(False, step + 1, f"environment {state}")

        stuck, why = detector.is_stuck(
            history[marks["history"] :],
            verifications[marks["verifications"] :],
            flows[marks["flows"] :],
            errors[marks["errors"] :],
        )
        if not stuck:
            continue

        text = _ask_and_record(recorder, human, goal, rendered, why, step, interventions)

        # Any request opens a fresh window, answered or not. The storm section 8a warns
        # about is the detector re-firing on the next step, which happens identically
        # when the human declines - and leaving it unreset would make a NoHuman run
        # report more requests than an assisted run over the same goal.
        marks.update(
            history=len(history),
            verifications=len(verifications),
            errors=len(errors),
            flows=len(flows),
        )
        if text is None:
            continue

        guidance.append(text)
        interventions += 1

        if interventions >= max_interventions:
            return result(
                False,
                step + 1,
                f"aborted after {interventions} interventions without progress",
            )

    return result(False, goal.max_steps, "max_steps reached")


def _latest_errors(errors: Sequence[str]) -> list[str]:
    """The most recent step's errors, for the prompt's EXECUTION ERRORS section.

    Only the latest step is shown: the whole run's errors would crowd out history, and
    the observation the model is being asked to act on is the current one.
    """
    return [errors[-1]] if errors and errors[-1] else []


def _record_verifier_usage(
    recorder: TrajectoryRecorder, step: int, verifier: Verifier
) -> None:
    """Record verifier usage when the verifier actually called a model.

    A verifier exposing ``last_response`` is taken to have set it during the check just
    made. One that makes no API call exposes nothing, and no llm_call record is written
    - "no call made" stays distinguishable from "call made, zero tokens" (D22).
    """
    response = getattr(verifier, "last_response", None)
    if response is not None:
        recorder.record_llm_call(step, "verifier", response)


def _ask_and_record(
    recorder: TrajectoryRecorder,
    human: HumanProtocol,
    goal: Goal,
    rendered: str,
    why: str,
    step: int,
    interventions: int,
) -> str | None:
    """Request help and record the request, whether or not it is answered.

    A request the human declines is recorded with a null ``text``, so a NoHuman run
    still shows where the harness would have asked - that is the baseline half of the
    M1 comparison. Interventions are counted as records whose text is not null.

    ``next_intervention_index`` is read before asking and ``last_hint`` after, per D21.
    """
    index = getattr(human, "next_intervention_index", interventions)
    text = human.ask(goal, rendered, why)
    hint = getattr(human, "last_hint", None)

    answered = text is not None
    recorder.record_intervention(
        step=step,
        stuck_reason=why,
        text=text,
        intervention_index=index if answered else None,
        original_step=hint.original_step if answered and hint is not None else None,
    )
    return text

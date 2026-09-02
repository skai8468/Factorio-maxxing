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

from collections.abc import Sequence
from typing import Any

from factorio_maxxing import context
from factorio_maxxing.envs import Action, EnvProtocol
from factorio_maxxing.goal import Goal, GoalResult
from factorio_maxxing.human import HumanProtocol
from factorio_maxxing.llm import LLMClient, extract_policy
from factorio_maxxing.rendering import render_observation
from factorio_maxxing.stuck import StuckDetector
from factorio_maxxing.trajectory import TrajectoryRecorder
from factorio_maxxing.verifier import VERIFICATION_WINDOW, Verifier

MAX_INTERVENTIONS = 3
ERROR_KEYS = ("error", "stderr")


def execute_policy(
    policy: str, env: EnvProtocol
) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
    """Submit a policy to the environment and observe the result.

    Thin at M0. This is the boundary where repair strategies attach in later scope
    (architecture.md, extension points); it is not the place for them now.
    """
    return env.step(Action(code=policy))


def execution_errors(observation: dict[str, Any]) -> list[str]:
    """Pull execution errors out of an observation.

    Which key a live FLE observation populates is unverified until Phase 5, the same
    caveat as D16. An unrecognised shape yields no errors rather than raising.
    """
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
) -> GoalResult:
    """Run one goal to completion, failure, or abort.

    Returns as soon as the verifier says DONE, with no further policy call.
    """
    observation = env.reset()
    rendered = render_observation(observation)

    history: list[tuple[str, str]] = []
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
        prompt = context.build(
            goal,
            rendered,
            history,
            guidance,
            _latest_errors(errors),
            api_reference=api_reference,
            history_length=history_length,
        )
        response = policy_client.generate(prompt)
        policy = extract_policy(response.text)

        observation, reward, terminated, truncated, _ = execute_policy(policy, env)
        rendered = render_observation(observation)
        step_errors = execution_errors(observation)

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
        history.append((policy, rendered))

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

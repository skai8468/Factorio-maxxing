"""Context assembly.

See docs/contracts.md (context.py) and docs/decisions.md D11 and D12.

One job: turn goal, observation, history, guidance and execution errors into a prompt.
It owns no control flow, calls no LLM, and formats no observations itself - the loop
decides what to pass and rendering.py decides how an observation looks. This is the
component later experiments vary most, which is why it is not inside loop.py.
"""

from collections.abc import Sequence

from factorio_maxxing.goal import Goal

HISTORY_WINDOW = 16
"""Steps of history shown to the policy. Matches FLE's RecursiveReportFormatter
chunk size so a later baseline comparison stays apples-to-apples."""

POLICY_INSTRUCTIONS = """Work towards the goal one step at a time.
First give a short PLANNING note: what you will do next, and why.
Then give exactly one fenced python block containing only the code to run now.
Keep the code under 50 lines, and prefer a small verifiable step over a long script.
Do not repeat steps that have already succeeded."""


def build(
    goal: Goal,
    rendered_observation: str,
    history: Sequence[tuple[str, str]] = (),
    guidance: Sequence[str] = (),
    errors: Sequence[str] = (),
    *,
    api_reference: str = "",
    history_length: int = HISTORY_WINDOW,
) -> str:
    """Assemble the policy prompt.

    ``history`` is a sequence of ``(policy, rendered_observation)`` pairs in step order;
    only the last ``history_length`` are shown, and steps are numbered as the loop and
    the trajectory number them, from zero.

    ``guidance`` accumulates across the goal and is rendered most recent last (D12).
    Empty history, guidance and errors sections are omitted rather than rendered as
    placeholders - absent context should cost no tokens - while GOAL, CURRENT
    OBSERVATION and INSTRUCTIONS are always present.

    ``api_reference`` describes the functions the environment actually provides. Without
    it a policy model invents a plausible API and every call fails; it is passed in
    rather than hard-coded because the authority on that surface is the environment,
    not this module. It renders first, being the most stable content across a goal.
    """
    blocks = []
    if api_reference.strip():
        blocks.append(_section("ENVIRONMENT API", api_reference.splitlines()))
    blocks.append(_goal_block(goal))

    if history:
        blocks.append(_history_block(history, history_length))
    if errors:
        blocks.append(_section("EXECUTION ERRORS", list(errors)))

    blocks.append(_section("CURRENT OBSERVATION", rendered_observation.splitlines()))

    if guidance:
        blocks.append(_guidance_block(guidance))

    blocks.append(_section("INSTRUCTIONS", POLICY_INSTRUCTIONS.splitlines()))
    return "\n\n".join(blocks)


def _section(header: str, lines: Sequence[str]) -> str:
    return "\n".join([header, *(f"  {line}" for line in lines)])


def _goal_block(goal: Goal) -> str:
    lines = [goal.description, f"step budget: {goal.max_steps}"]
    if goal.notes:
        lines.insert(1, f"notes: {goal.notes}")
    return _section("GOAL", lines)


def _history_block(history: Sequence[tuple[str, str]], history_length: int) -> str:
    window = list(history)[-history_length:] if history_length > 0 else []
    first_step = len(history) - len(window)

    header = "RECENT HISTORY"
    if len(window) < len(history):
        header += f" (last {len(window)} of {len(history)} steps)"

    lines: list[str] = []
    for offset, (policy, observation) in enumerate(window):
        step = first_step + offset
        lines.append(f"step {step} policy:")
        lines.extend(f"  {line}" for line in (policy or "(no code)").splitlines())
        lines.append(f"step {step} result:")
        lines.extend(f"  {line}" for line in observation.splitlines())
    return _section(header, lines)


def _guidance_block(guidance: Sequence[str]) -> str:
    """Render accumulated human guidance verbatim, most recent last."""
    lines: list[str] = []
    for index, hint in enumerate(guidance, start=1):
        rendered = hint.splitlines() or [""]
        lines.append(f"{index}. {rendered[0]}")
        lines.extend(f"   {line}" for line in rendered[1:])
    return _section("HUMAN GUIDANCE", lines)

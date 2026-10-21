"""Context assembly.

See docs/contracts.md (context.py) and docs/decisions.md D11 and D12.

One job: turn goal, observation, history, guidance and execution errors into a prompt.
It owns no control flow, calls no LLM, and formats no observations itself - the loop
decides what to pass and rendering.py decides how an observation looks. This is the
component later experiments vary most, which is why it is not inside loop.py.
"""

from collections.abc import Sequence

from factorio_maxxing.goal import Goal
from factorio_maxxing.llm import Prompt

HISTORY_WINDOW = 8
"""Steps of history shown verbatim. FLE's agent keeps its last 16 *messages* - 8 steps,
each a program and its result - and a report of everything older; the M1 harness had
mistaken that for 16 steps and dropped the rest (D66). Older steps reach the policy
through the HISTORY REPORT."""

HOW_TO_PLAY = """You are an AI agent designed to play Factorio through a gym environment,
specializing in:
- Long-horizon planning
- Spatial reasoning
- Systematic automation

You play by writing Python programs that call the game API documented below. Each
step you send one program. It runs in the game, and you then see what it printed and
how the game changed. Variables and functions you define persist between steps.

Response format:
1. PLANNING - think it through in prose:
   - State: what is the current game state? What resources and entities do you have?
   - Next step: what is the most useful next step of reasonable size, and why?
   - Actions: what specific actions does it need, and what resources do they require?
2. POLICY - exactly one fenced python block containing only the code to run now.

Best practices:
- Keep each program small and modular: at most 50 lines, one clear purpose, easy to
  debug and modify.
- Build incrementally and verify each step. Inspect the game state before changing it.
- Track entities across inventories and watch for missing requirements.
- Avoid breaking existing automated structures; preserve working automated structures.
- Write direct interpreter commands; only wrap code in a function when it is a utility
  you will reuse.
- Do not repeat steps that already succeeded - continue from where you left off.

Reading the observation:
- INVENTORY is what you carry. CHARACTER is where you stand.
- ENTITIES lists everything you have built, with position, status, fuel, contents and
  warnings. Read the warnings to see why something is not working.
- RESEARCH, FLOWS (production), FUNCTIONS (utilities you defined), GAME (time and
  score) and EXECUTION (what your last program printed, including errors).
- HISTORY REPORT summarises steps older than RECENT HISTORY.
- HUMAN GUIDANCE, when present, is advice from a human expert who can see the game.

Factorio tips:
- Use transport belts to keep burner machines fed with coal.
- Your inventory holds roughly 2000 items; if it fills up, put items into a chest.
- Arrange the factory on a grid, and consider the long-term effect of each layout.
- Fuel boilers manually."""
"""Standing instructions, adapted from FLE's ``GYM_AGENT_INSTRUCTIONS``
(fle/agents/gym_agent.py, MIT - see configs/FLE_LICENSE) to this harness's sections
(D66). FLE's role line and its "automated structures" wording are kept verbatim, and
nothing is added that FLE's agent is not told (D78). Stable across a run, so they sit
in the cached part beside the API reference."""

POLICY_INSTRUCTIONS = """Work towards the goal one step at a time.
First give a short PLANNING note: what you will do next, and why.
Then give exactly one fenced python block containing only the code to run now.
Keep the code under 50 lines, and prefer a small verifiable step over a long script.
Do not repeat steps that have already succeeded."""
"""The closing reminder: the response format, restated where the model reads last."""


def build_prompt(
    goal: Goal,
    rendered_observation: str,
    history: Sequence[tuple[str, str]] = (),
    guidance: Sequence[str] = (),
    errors: Sequence[str] = (),
    *,
    api_reference: str = "",
    history_length: int = HISTORY_WINDOW,
    report: str = "",
    report_until: int | None = None,
) -> Prompt:
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
    not this module. It renders first, being the most stable content across a goal,
    and is returned as the prompt's cached part so a provider can cache it (D51).

    The standing instructions (``HOW_TO_PLAY``) open the cached part, ahead of the API
    reference: both are identical on every step of a run (D66).

    ``report`` is the running summary of steps up to ``report_until``, written by
    ``history_report`` every few steps (D66); empty until the first one exists.
    """
    cached_blocks = [_section("HOW TO PLAY", HOW_TO_PLAY.splitlines())]
    if api_reference.strip():
        cached_blocks.append(_section("ENVIRONMENT API", api_reference.splitlines()))
    cached = "\n\n".join(cached_blocks)
    blocks = [_goal_block(goal)]

    if report.strip():
        header = "HISTORY REPORT"
        if report_until is not None:
            header += f" (steps 0-{report_until})"
        blocks.append(_section(header, report.splitlines()))
    if history:
        blocks.append(_history_block(history, history_length))
    if errors:
        blocks.append(_section("EXECUTION ERRORS", list(errors)))

    blocks.append(_section("CURRENT OBSERVATION", rendered_observation.splitlines()))

    if guidance:
        blocks.append(_guidance_block(guidance))

    blocks.append(_section("INSTRUCTIONS", POLICY_INSTRUCTIONS.splitlines()))
    return Prompt(cached=cached, rest="\n\n".join(blocks))


def build(
    goal: Goal,
    rendered_observation: str,
    history: Sequence[tuple[str, str]] = (),
    guidance: Sequence[str] = (),
    errors: Sequence[str] = (),
    *,
    api_reference: str = "",
    history_length: int = HISTORY_WINDOW,
    report: str = "",
    report_until: int | None = None,
) -> str:
    """The policy prompt as the exact text the model reads: ``str(build_prompt(...))``."""
    return str(
        build_prompt(
            goal,
            rendered_observation,
            history,
            guidance,
            errors,
            api_reference=api_reference,
            history_length=history_length,
            report=report,
            report_until=report_until,
        )
    )


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

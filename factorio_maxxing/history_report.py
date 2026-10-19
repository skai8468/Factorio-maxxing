"""The running history report: memory for steps older than the history window.

See docs/decisions.md D66. FLE's own agent keeps its last 8 steps verbatim and folds
everything older into an LLM-written report; the M1 harness dropped those steps
entirely, so after 16 steps an agent forgot what it had built. Every ``interval`` steps
the policy's own model rewrites the report from the previous report, the steps since,
and the current observation - its tokens are recorded under the role ``summarizer``
and count toward the run's spend like any other call (D65).

The instructions are adapted from FLE's ``DEFAULT_INSTRUCTIONS``
(fle/agents/formatters/recursive_report_formatter.py, MIT - see configs/FLE_LICENSE):
the same three sections - existing structures with positions, error tips, and the
agent's utility functions.
"""

from collections.abc import Sequence

from factorio_maxxing.llm import LLMClient, LLMResponse

REPORT_INTERVAL = 8
"""Steps between reports - FLE's 16-message chunk, which is 8 steps (D66)."""

REPORT_INSTRUCTIONS = """You write the historical report for an agent playing Factorio.
You are given the previous report, the programs the agent ran since then with their
results, and the current observation. Write a new report that replaces the previous
one. It has three sections.

EXISTING STRUCTURES
Summarise the structures the agent has built and what each is for, with the entities
and positions that make it up. Group by structure, not by entity, for example:
### Electricity generator at (x, y)
Steam engine (x1, y1), boiler (x2, y2) and offshore pump (x3, y3).
### Iron plate line at (x, y)
- Burner mining drill (x1, y1) feeding a stone furnace at (x2, y2)
- Burner mining drill (x3, y3) feeding a stone furnace at (x4, y4)
- If structures are connected, describe them as one.
- Do not mention harvesting or crafting; the agent can see its own inventory.
- Keep every structure from the previous report that has not changed.
- Use the current observation as the authority on what exists now.

ERROR TIPS
Analyse the errors the agent made and give tips for avoiding them: incorrect API use,
misplaced entities, being out of reach, and so on. Group similar mistakes into one
point. Keep the tips from the previous report and add new ones.

NAMESPACE
List the utility functions the agent has defined: signature with type hints and a
one-line description, and how calls to them have succeeded or failed. List important
variables the agent has assigned, with their types. Leave this section out if the
agent has defined none.

Output only the report. Make it accurate, concise and informative - under 500 words."""


def is_due(step: int, interval: int = REPORT_INTERVAL) -> bool:
    """Whether a report is written after ``step`` (zero-based): after steps 7, 15, ..."""
    return interval > 0 and (step + 1) % interval == 0


def report_prompt(
    previous: str,
    steps: Sequence[tuple[int, str, str]],
    current_observation: str,
) -> str:
    """The summarizer's prompt: instructions, previous report, new steps, current state.

    ``steps`` holds ``(step, program, result)``, each result rendered without entities;
    the current observation carries the entity list the report's positions come from.
    """
    lines = [REPORT_INSTRUCTIONS, "", "PREVIOUS REPORT"]
    lines.append(previous.strip() or "(none - these are the agent's first steps)")
    lines += ["", "STEPS SINCE THE PREVIOUS REPORT"]
    for step, program, result in steps:
        lines.append(f"step {step} program:")
        lines.extend(f"  {line}" for line in (program or "(no code)").splitlines())
        lines.append(f"step {step} result:")
        lines.extend(f"  {line}" for line in result.splitlines())
    lines += ["", "CURRENT OBSERVATION", current_observation]
    return "\n".join(lines)


class HistoryReporter:
    """Writes the report with the policy's own client, as FLE's agent does."""

    def __init__(self, client: LLMClient, interval: int = REPORT_INTERVAL):
        if interval < 1:
            raise ValueError("interval must be at least 1")
        self.client = client
        self.interval = interval

    def due(self, step: int) -> bool:
        return is_due(step, self.interval)

    def update(
        self,
        previous: str,
        steps: Sequence[tuple[int, str, str]],
        current_observation: str,
    ) -> LLMResponse:
        """Return the new report as the model's response; its text is the report."""
        return self.client.generate(report_prompt(previous, steps, current_observation))

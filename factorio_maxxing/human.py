"""Human backends.

See docs/contracts.md (human.py) and docs/decisions.md D8.

The human provides text and nothing else. No backend here executes actions, touches the
environment, or sees an Action - `ask` takes strings and returns a string. Returned text
is passed back unaltered: intervention text is recorded verbatim and is the source of
truth.

Three backends behind one interface: NoHuman is the autonomous baseline, and the delta
between it and InteractiveHuman over one goal set is the M1 result. ScriptedHuman is a
methodological safeguard, replaying one intervention set against two harness versions so
an improvement can be attributed to the harness rather than to a better hint.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from factorio_maxxing.goal import Goal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hint:
    """One recorded intervention, replayed by ScriptedHuman.

    `original_step` is the step of the run the hint was recorded in. It is kept for
    analysing *when* help was needed and is never used for matching (D8).
    """

    text: str
    original_step: int | None = None


@runtime_checkable
class HumanProtocol(Protocol):
    def ask(self, goal: Goal, observation: str, reason: str) -> str | None: ...


class NoHuman:
    """The autonomous baseline: never assists."""

    def __init__(self) -> None:
        self.call_count = 0

    def ask(self, goal: Goal, observation: str, reason: str) -> str | None:
        self.call_count += 1
        return None


class InteractiveHuman:
    """CLI backend for development and real runs.

    Reads lines until a blank line or end of input. A blank first line means no
    assistance, so an operator can decline without aborting the run. EOF and interrupt
    are treated the same way rather than crashing a run in progress.
    """

    def __init__(
        self,
        input_fn: Callable[[], str] = input,
        output_fn: Callable[[str], None] = print,
    ):
        self._input = input_fn
        self._output = output_fn
        self.call_count = 0

    def ask(self, goal: Goal, observation: str, reason: str) -> str | None:
        self.call_count += 1
        self._output("")
        self._output("=== The harness is asking for help ===")
        self._output(f"GOAL: {goal.description}")
        self._output(f"WHY: {reason}")
        self._output("OBSERVATION:")
        self._output(observation)
        self._output("")
        self._output("Type guidance. Blank line to finish, or blank line to decline.")

        lines: list[str] = []
        while True:
            try:
                line = self._input()
            except (EOFError, KeyboardInterrupt):
                break
            if not line.strip():
                break
            lines.append(line)

        return "\n".join(lines) if lines else None


class ScriptedHuman:
    """Replays recorded interventions in sequence order.

    The Nth stuck event receives the Nth hint, never the hint recorded at this step
    index. Step-keyed replay fails silently in exactly the case the safeguard exists
    for: if harness A got stuck at steps 7/15/22 and harness B at 9/20, step matching
    hands B nothing.

    Once the hints run out the backend degrades to NoHuman and logs the exhaustion.
    Underuse is logged too - "harness B used 2 of harness A's 3 hints" is itself a
    result.
    """

    def __init__(self, hints: Sequence[Hint | str]):
        self.hints = tuple(
            hint if isinstance(hint, Hint) else Hint(text=hint) for hint in hints
        )
        self.call_count = 0
        self.used = 0
        self.exhausted_calls = 0
        self.last_hint: Hint | None = None

    @property
    def unused(self) -> int:
        return len(self.hints) - self.used

    @property
    def next_intervention_index(self) -> int:
        """The index the next replayed hint will carry, for the recorder."""
        return self.used

    def ask(self, goal: Goal, observation: str, reason: str) -> str | None:
        self.call_count += 1
        if self.used >= len(self.hints):
            self.exhausted_calls += 1
            self.last_hint = None
            logger.info(
                "scripted hints exhausted after %d of %d; degrading to NoHuman",
                self.used,
                len(self.hints),
            )
            return None

        hint = self.hints[self.used]
        self.used += 1
        self.last_hint = hint
        return hint.text

    def usage_report(self) -> str:
        """A one-line summary of replay coverage, for logs and analysis."""
        return (
            f"used {self.used} of {len(self.hints)} scripted hints"
            f" ({self.exhausted_calls} request(s) went unanswered)"
        )

    def log_usage(self) -> None:
        logger.info("%s", self.usage_report())
        if self.unused:
            logger.info("%d scripted hint(s) were never needed", self.unused)

"""Goal verification.

See docs/contracts.md (verifier.py) and docs/decisions.md D6.

The verifier decides one thing: is the goal complete? It never requests human
assistance, never modifies state, and never decides stuckness. It is separate from the
recorder so that "the verifier said DONE" can later be compared against "objective
state shows success".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from factorio_maxxing.goal import Goal
from factorio_maxxing.llm import LLMClient, LLMResponse

VERIFICATION_WINDOW = 4
"""Recent observations shown alongside the current one. A sustained-production goal
cannot be judged from a single tick - FLE's own lab tasks sleep and measure throughput
for exactly this reason - so the verifier never sees a bare snapshot."""

VERIFIER_INSTRUCTIONS = """Decide whether the goal is complete, judging only what the
observations show. Progress towards the goal is not completion.
Reply with DONE or NOT DONE on the first line, then one sentence of reason.
Do not suggest next steps, and do not offer help."""

_NOT_DONE_MARKERS = ("NOT DONE", "NOT_DONE", "NOTDONE")


@dataclass
class VerificationResult:
    done: bool
    reason: str


@runtime_checkable
class Verifier(Protocol):
    def check(
        self, goal: Goal, observation: str, window: Sequence[str] = ()
    ) -> VerificationResult: ...


def build_verification_prompt(
    goal: Goal, observation: str, window: Sequence[str] = ()
) -> str:
    """Assemble the verifier prompt.

    ``window`` holds recent rendered observations in step order, most recent last, not
    including the current one. Each carries its own FLOWS section, so production flows
    reach the verifier through the same path as everything else rather than by a
    parallel one.
    """
    blocks = [_section("GOAL", [goal.description])]

    if window:
        lines: list[str] = []
        for offset, rendered in enumerate(window):
            lines.append(f"observation -{len(window) - offset}:")
            lines.extend(f"  {line}" for line in rendered.splitlines())
        blocks.append(_section("RECENT OBSERVATIONS", lines))

    blocks.append(_section("CURRENT OBSERVATION", observation.splitlines()))
    blocks.append(_section("INSTRUCTIONS", VERIFIER_INSTRUCTIONS.splitlines()))
    return "\n\n".join(blocks)


def parse_verdict(text: str) -> VerificationResult:
    """Read a verdict out of a model response.

    An unreadable response verifies as NOT DONE: a verifier that cannot state a verdict
    must never be read as claiming the goal is finished.
    """
    stripped = (text or "").strip()
    if not stripped:
        return VerificationResult(done=False, reason="verifier returned no response")

    upper = stripped.upper()
    marker = next((m for m in _NOT_DONE_MARKERS if m in upper), None)
    if marker is not None:
        done = False
        cut = upper.index(marker) + len(marker)
    elif "DONE" in upper:
        done = True
        cut = upper.index("DONE") + len("DONE")
    else:
        return VerificationResult(done=False, reason=stripped)

    reason = stripped[cut:].lstrip(" \t:.-—").strip()
    return VerificationResult(done=done, reason=reason or "no reason given")


class LLMVerifier:
    """An LLM-based verifier. Its model is configured independently of the policy's."""

    def __init__(self, client: LLMClient):
        self._client = client
        self.last_response: LLMResponse | None = None
        """Usage from the most recent check, kept so the recorder can attribute
        verifier tokens separately from policy tokens (D9)."""

    def check(
        self, goal: Goal, observation: str, window: Sequence[str] = ()
    ) -> VerificationResult:
        response = self._client.generate(
            build_verification_prompt(goal, observation, window)
        )
        self.last_response = response
        return parse_verdict(response.text)


class StubVerifier:
    """A deterministic verifier scripted by call index.

    The Nth check returns the Nth scripted result, repeating the final one once
    exhausted, matching MockFactorioEnv (D15) and StubLLMClient.
    """

    def __init__(self, results: Sequence[VerificationResult]):
        if not results:
            raise ValueError("StubVerifier requires at least one result")
        self._results = tuple(results)
        self.call_count = 0

    def check(
        self, goal: Goal, observation: str, window: Sequence[str] = ()
    ) -> VerificationResult:
        result = self._results[min(self.call_count, len(self._results) - 1)]
        self.call_count += 1
        return result


def _section(header: str, lines: Sequence[str]) -> str:
    return "\n".join([header, *(f"  {line}" for line in lines)])

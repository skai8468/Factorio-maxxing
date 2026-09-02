"""LLM client abstraction and the deterministic offline stub.

See docs/contracts.md (llm.py) and docs/decisions.md D5 and D9.

The harness contains no provider or model conditionals: a model is a configuration
string. Usage is stored raw - never a computed dollar cost - so trajectories stay
re-priceable when pricing changes.

A real API client is Phase 4 item 15.
"""

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class LLMResponse:
    """One model response, with raw usage attached.

    Cost is derived at analysis time from a pricing table, never stored here (D9).
    """

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_seconds: float


@runtime_checkable
class LLMClient(Protocol):
    def generate(self, prompt: str) -> LLMResponse: ...


class StubLLMClient:
    """A deterministic client scripted by call index.

    The Nth call to generate() returns the Nth scripted response, whatever the prompt
    says. Once the script is exhausted the final response repeats, matching
    MockFactorioEnv (D15) so a short script can back a long run.

    Token counts are word counts, not a real tokeniser: they are non-zero and
    deterministic, which is what the recorder's plumbing needs to be tested against.
    Latency is fixed rather than measured, so replay tests stay reproducible.

    Prompts are retained in ``prompts`` for test inspection - notably for asserting
    that the loop stops calling the policy once the verifier says DONE.
    """

    def __init__(
        self,
        responses: Sequence[str],
        *,
        model: str = "stub",
        latency_seconds: float = 0.0,
    ):
        if not responses:
            raise ValueError("StubLLMClient requires at least one response")
        self._responses = tuple(responses)
        self.model = model
        self.latency_seconds = latency_seconds
        self.prompts: list[str] = []

    @property
    def call_count(self) -> int:
        return len(self.prompts)

    def generate(self, prompt: str) -> LLMResponse:
        text = self._responses[min(self.call_count, len(self._responses) - 1)]
        self.prompts.append(prompt)
        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=len(prompt.split()),
            output_tokens=len(text.split()),
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_seconds=self.latency_seconds,
        )


_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
_UNCLOSED_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*)\Z", re.DOTALL)
_PYTHON_TAGS = frozenset({"python", "py", "python3"})


def extract_policy(text: str) -> str:
    """Extract submittable Python from a model response.

    Handles fenced ``python`` blocks, untagged fences, an unterminated fence left by a
    truncated response, and bare Python. A response carrying no usable code yields an
    empty string rather than raising: the failure then travels through the trajectory
    and the EXECUTION section as observable data instead of as control flow.

    Where a response contains several blocks the last is taken - reasoning, plans and
    worked examples precede the final policy, and FLE's own GymAgent format puts its
    POLICY stage last.

    Fenced code is returned verbatim without a syntax check, so a model's broken code
    reaches the environment and its SyntaxError is fed back. Unfenced text must parse
    as Python to count as code at all, which is what separates bare Python from prose.
    """
    if not text or not text.strip():
        return ""

    blocks = _FENCE.findall(text)
    tagged = [body for tag, body in blocks if tag.lower() in _PYTHON_TAGS]
    if tagged:
        return tagged[-1].strip()
    if blocks:
        return blocks[-1][1].strip()

    unclosed = _UNCLOSED_FENCE.search(text)
    if unclosed and unclosed.group(1).lower() in _PYTHON_TAGS | {""}:
        return unclosed.group(2).strip()

    candidate = text.strip()
    try:
        ast.parse(candidate)
    except SyntaxError:
        return ""
    return candidate

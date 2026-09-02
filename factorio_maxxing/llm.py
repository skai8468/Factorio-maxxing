"""LLM client abstraction and the deterministic offline stub.

See docs/contracts.md (llm.py) and docs/decisions.md D5 and D9.

The harness contains no provider or model conditionals: a model is a configuration
string. Usage is stored raw - never a computed dollar cost - so trajectories stay
re-priceable when pricing changes.

Policy extraction is build-order item 6 and does not live here yet. A real API client
is Phase 4 item 15.
"""

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

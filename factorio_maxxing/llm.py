"""LLM client abstraction and the deterministic offline stub.

See docs/contracts.md (llm.py) and docs/decisions.md D5 and D9.

The harness contains no provider or model conditionals: a model is a configuration
string. Usage is stored raw - never a computed dollar cost - so trajectories stay
re-priceable when pricing changes.

APIClient is a generic client: every provider is a base URL, a key environment
variable and a wire protocol. Most providers speak the OpenAI-compatible protocol;
Anthropic's is used where a provider offers it, because only it supports prompt
caching (D51). Keys come from the environment, never a config file.
"""

import ast
import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


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


@dataclass(frozen=True)
class Prompt:
    """A prompt with a stable leading part a provider may cache (D51).

    ``str(prompt)`` is the exact text a model reads, so marking a part cacheable never
    changes what the model sees - only what the request costs. A client that cannot
    cache sends ``str(prompt)``.
    """

    cached: str
    rest: str

    def __str__(self) -> str:
        return f"{self.cached}\n\n{self.rest}" if self.cached else self.rest


@runtime_checkable
class LLMClient(Protocol):
    def generate(self, prompt: str | Prompt) -> LLMResponse: ...


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

    def generate(self, prompt: str | Prompt) -> LLMResponse:
        text = self._responses[min(self.call_count, len(self._responses) - 1)]
        prompt = str(prompt)
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


@dataclass(frozen=True)
class Provider:
    """Where a provider lives and which environment variable holds its key."""

    base_url: str
    api_key_env: str
    model_prefix: str = ""
    """Stripped from the model string before the request, matching FLE's
    model_transform (open-router-, ollama-)."""
    api: str = "openai"
    """The wire protocol: "openai" (chat completions) or "anthropic" (messages).
    Chosen per provider in this table, so no code branches on a model (D5, D51)."""
    cache_parts: bool = False
    """On the "openai" protocol, send a ``Prompt``'s cached part as a content part
    marked ``cache_control``. OpenRouter honours the marker for Claude and Gemini and
    caches other models automatically; a plain OpenAI-compatible endpoint may reject
    it, so it is opt-in per provider (D54)."""


PROVIDERS: dict[str, Provider] = {
    "claude": Provider("https://api.anthropic.com", "ANTHROPIC_API_KEY", api="anthropic"),
    "openai": Provider("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "deepseek": Provider("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "gemini": Provider(
        "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"
    ),
    "together": Provider("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "open-router": Provider(
        "https://openrouter.ai/api/v1",
        "OPEN_ROUTER_API_KEY",
        "open-router-",
        cache_parts=True,
    ),
    "ollama": Provider("http://localhost:11434/v1", "OLLAMA_API_KEY", "ollama-"),
}

# Routing table, longest prefix first. This is a lookup, not an allowlist: any model
# string reaching a known provider passes through untouched (D5).
MODEL_ROUTES: tuple[tuple[str, str], ...] = (
    ("open-router-", "open-router"),
    ("ollama-", "ollama"),
    ("claude-", "claude"),
    ("deepseek", "deepseek"),
    ("gemini", "gemini"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
)

MAX_TOKENS = 4096
WORKSPACE_ID_ENV = "ANTHROPIC_WORKSPACE_ID"


def resolve_provider(model: str, provider: str | None = None) -> tuple[Provider, str]:
    """Route a model string to a provider, returning the provider and the model to send.

    Routing is by prefix, as FLE's APIFactory does. An unrecognised model is a routing
    failure, not a rejected model: pass ``provider`` explicitly and it goes through.
    """
    if provider is None:
        provider = next(
            (name for prefix, name in MODEL_ROUTES if model.startswith(prefix)), None
        )
    if provider is None:
        raise ValueError(
            f"cannot route model {model!r} to a provider; "
            f"pass provider= explicitly (known: {', '.join(sorted(PROVIDERS))})"
        )
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")

    entry = PROVIDERS[provider]
    sent = model.removeprefix(entry.model_prefix) if entry.model_prefix else model
    return entry, sent


class APIClient:
    """A generic client over two wire protocols.

    Every provider is a base URL, a key environment variable and a protocol, routed
    through one client; the harness holds no provider or model conditionals (D5) - the
    protocol is a field of the routing table. Usage is recorded raw, never as a cost
    (D9).

    A ``Prompt``'s cached part is marked for prompt caching on the Anthropic protocol;
    the OpenAI-compatible protocol cannot cache and sends the joined text (D51).

    ``temperature`` is omitted from the request unless set, because some reasoning
    models reject it (see docs/fle-integration.md).
    """

    def __init__(
        self,
        model: str,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float | None = None,
        workspace_id: str | None = None,
        client: Any = None,
    ):
        self.model = model
        self.provider, self._sent_model = resolve_provider(model, provider)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.workspace_id = workspace_id or os.environ.get(WORKSPACE_ID_ENV)
        if client is None:
            build = _anthropic_client if self._anthropic else _openai_client
            client = build(self.provider, api_key, self.workspace_id)
        self._client = client

    @property
    def _anthropic(self) -> bool:
        return self.provider.api == "anthropic"

    def generate(self, prompt: str | Prompt) -> LLMResponse:
        request: dict[str, Any] = {
            "model": self._sent_model,
            "max_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature

        started = time.perf_counter()
        if self._anthropic:
            content = _anthropic_content(prompt)
            request["messages"] = [{"role": "user", "content": content}]
            reply = self._client.messages.create(**request)
        else:
            content = _cached_parts(prompt) if self.provider.cache_parts else None
            request["messages"] = [{"role": "user", "content": content or str(prompt)}]
            reply = self._client.chat.completions.create(**request)
        latency = time.perf_counter() - started

        usage = getattr(reply, "usage", None)
        if self._anthropic:
            text = _read_anthropic_text(reply)
            tokens = _read_anthropic_usage(usage)
        else:
            text = _read_text(reply)
            tokens = _read_usage(usage)
        input_tokens, output_tokens, cache_read, cache_write = tokens
        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            latency_seconds=latency,
        )


def _cached_parts(prompt: str | Prompt) -> list[dict[str, Any]] | None:
    """The prompt as two text parts, the cached one marked ``cache_control``.

    The parts concatenate to ``str(prompt)`` exactly - the separator travels at the
    start of the second - so the model reads what it would read uncached. None when
    there is nothing to cache. The Messages API and OpenRouter's chat completions take
    the same text-part shape (D51, D54).
    """
    if not isinstance(prompt, Prompt) or not prompt.cached:
        return None
    return [
        {"type": "text", "text": prompt.cached, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"\n\n{prompt.rest}"},
    ]


def _anthropic_content(prompt: str | Prompt) -> str | list[dict[str, Any]]:
    """The user message content, with the cached part as its own marked block."""
    return _cached_parts(prompt) or str(prompt)


def auth_headers(workspace_id: str | None = None) -> dict[str, str]:
    """Extra headers an account may require.

    An identity-linked API key must name the workspace the request acts in, so a
    workspace id is sent whenever one is configured - on the Anthropic protocol only.
    It identifies an Anthropic account, and no other provider has a use for it, so
    sending it to OpenRouter or any OpenAI-compatible endpoint would only hand a third
    party an identifier (D54). The protocol is a routing-table field, so this is not a
    branch on a model.
    """
    return {"anthropic-workspace-id": workspace_id} if workspace_id else {}


def _openai_client(
    provider: Provider, api_key: str | None, workspace_id: str | None = None
) -> Any:
    key = api_key or os.environ.get(provider.api_key_env)
    if not key:
        raise ValueError(
            f"no API key: set {provider.api_key_env} in the environment "
            "(keys are never read from a config file)"
        )
    try:
        from openai import OpenAI
    except ImportError as error:  # pragma: no cover - depends on the install
        raise ValueError(
            "the openai package is required for live model calls; "
            'install it with pip install -e ".[api]"'
        ) from error
    # No workspace header: it identifies an Anthropic account (D54).
    return OpenAI(base_url=provider.base_url, api_key=key)


def _anthropic_client(
    provider: Provider, api_key: str | None, workspace_id: str | None = None
) -> Any:
    key = api_key or os.environ.get(provider.api_key_env)
    if not key:
        raise ValueError(
            f"no API key: set {provider.api_key_env} in the environment "
            "(keys are never read from a config file)"
        )
    try:
        from anthropic import Anthropic
    except ImportError as error:  # pragma: no cover - depends on the install
        raise ValueError(
            "the anthropic package is required for live Claude calls; "
            'install it with pip install -e ".[api]"'
        ) from error
    return Anthropic(
        base_url=provider.base_url,
        api_key=key,
        default_headers=auth_headers(workspace_id) or None,
    )


def _read_anthropic_text(message: Any) -> str:
    blocks = getattr(message, "content", None) or []
    return "".join(
        getattr(block, "text", "") or ""
        for block in blocks
        if getattr(block, "type", None) == "text"
    )


def _read_anthropic_usage(usage: Any) -> tuple[int, int, int, int]:
    """Read Messages API usage, with ``input_tokens`` meaning *all* input.

    The Messages API's own ``input_tokens`` excludes cached tokens. The harness's field
    has always meant every input token the model processed - the OpenAI-compatible
    ``prompt_tokens`` - so the cached tokens are added back here, keeping runs before
    and after caching comparable. The uncached share is ``input - read - write`` (D51).
    """
    if usage is None:
        return 0, 0, 0, 0
    cache_read = _as_int(getattr(usage, "cache_read_input_tokens", 0))
    cache_write = _as_int(getattr(usage, "cache_creation_input_tokens", 0))
    uncached = _as_int(getattr(usage, "input_tokens", 0))
    return (
        uncached + cache_read + cache_write,
        _as_int(getattr(usage, "output_tokens", 0)),
        cache_read,
        cache_write,
    )


def _read_text(completion: Any) -> str:
    choices = getattr(completion, "choices", None) or []
    if not choices:
        return ""
    return getattr(choices[0].message, "content", None) or ""


def _read_usage(usage: Any) -> tuple[int, int, int, int]:
    """Read raw token usage, defaulting anything a provider omits to zero.

    ``prompt_tokens`` counts every input token, cached or not, as the harness's
    ``input_tokens`` always has. OpenRouter reports cache reads as
    ``prompt_tokens_details.cached_tokens`` and writes as
    ``prompt_tokens_details.cache_write_tokens`` (D54); other providers' names are
    read defensively and confirmed on the first live call (docs/fle-integration.md).
    """
    if usage is None:
        return 0, 0, 0, 0

    details = getattr(usage, "prompt_tokens_details", None)
    cache_read = _as_int(getattr(details, "cached_tokens", 0)) or _as_int(
        getattr(usage, "cache_read_input_tokens", 0)
    )
    cache_write = _as_int(getattr(details, "cache_write_tokens", 0)) or _as_int(
        getattr(usage, "cache_creation_input_tokens", 0)
    )
    return (
        _as_int(getattr(usage, "prompt_tokens", 0)),
        _as_int(getattr(usage, "completion_tokens", 0)),
        cache_read,
        cache_write,
    )


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) else 0

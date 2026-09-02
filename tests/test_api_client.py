"""Tests for the generic OpenAI-compatible client.

Build-plan section 19 item 15. Every case here is offline: the transport is injected,
so no request leaves the machine and no key is needed.
"""

from types import SimpleNamespace

import pytest

from factorio_maxxing.llm import (
    PROVIDERS,
    APIClient,
    LLMClient,
    resolve_provider,
)


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(request)
        return self.response


class FakeOpenAI:
    """Stands in for openai.OpenAI: only .chat.completions.create is used."""

    def __init__(self, response):
        self.completions = FakeCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)


def completion(
    text="```python\nx = 1\n```",
    prompt_tokens=412,
    completion_tokens=88,
    cached_tokens=64,
    cache_creation=8,
):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
            cache_creation_input_tokens=cache_creation,
        ),
    )


def client(model="claude-haiku-4-5", response=None, **kwargs):
    fake = FakeOpenAI(response if response is not None else completion())
    return APIClient(model, client=fake, **kwargs), fake


def test_client_satisfies_the_protocol():
    api, _ = client()
    assert isinstance(api, LLMClient)


def test_every_provider_has_a_base_url_and_a_key_variable():
    assert set(PROVIDERS) == {
        "claude",
        "openai",
        "deepseek",
        "gemini",
        "together",
        "open-router",
        "ollama",
    }
    for provider in PROVIDERS.values():
        assert provider.base_url.startswith("http")
        assert provider.api_key_env.endswith("_API_KEY")


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-haiku-4-5", "claude"),
        ("gpt-4o", "openai"),
        ("o3-mini", "openai"),
        ("deepseek-chat", "deepseek"),
        ("gemini-2.0-flash", "gemini"),
        ("open-router-anthropic/claude-3.5-sonnet", "open-router"),
        ("ollama-llama3.3", "ollama"),
    ],
)
def test_models_route_to_providers_by_prefix(model, expected):
    provider, _ = resolve_provider(model)
    assert provider is PROVIDERS[expected]


@pytest.mark.parametrize(
    ("model", "sent"),
    [
        ("open-router-anthropic/claude-3.5-sonnet", "anthropic/claude-3.5-sonnet"),
        ("ollama-llama3.3", "llama3.3"),
        ("claude-haiku-4-5", "claude-haiku-4-5"),
    ],
)
def test_routing_prefixes_are_stripped_before_sending(model, sent):
    _, transformed = resolve_provider(model)
    assert transformed == sent


def test_there_is_no_model_allowlist():
    """D5: an unreleased model string routes and passes through untouched."""
    provider, sent = resolve_provider("claude-something-not-released-yet")
    assert provider is PROVIDERS["claude"]
    assert sent == "claude-something-not-released-yet"


def test_an_unroutable_model_names_the_known_providers():
    with pytest.raises(ValueError, match="cannot route model"):
        resolve_provider("mystery-model-9")


def test_an_explicit_provider_routes_anything():
    provider, sent = resolve_provider("mystery-model-9", provider="together")
    assert provider is PROVIDERS["together"]
    assert sent == "mystery-model-9"


def test_an_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="unknown provider"):
        resolve_provider("gpt-4o", provider="skynet")


def test_generate_returns_the_response_text():
    api, _ = client()
    assert api.generate("prompt").text == "```python\nx = 1\n```"


def test_generate_records_raw_usage():
    api, _ = client()
    response = api.generate("prompt")
    assert response.input_tokens == 412
    assert response.output_tokens == 88
    assert response.cache_read_tokens == 64
    assert response.cache_write_tokens == 8
    assert not hasattr(response, "cost")


def test_the_response_reports_the_configured_model_not_the_sent_one():
    api, fake = client("open-router-anthropic/claude-3.5-sonnet")
    response = api.generate("prompt")
    assert response.model == "open-router-anthropic/claude-3.5-sonnet"
    assert fake.completions.requests[0]["model"] == "anthropic/claude-3.5-sonnet"


def test_latency_is_measured():
    api, _ = client()
    assert api.generate("prompt").latency_seconds >= 0


def test_the_prompt_is_sent_as_a_single_user_message():
    api, fake = client()
    api.generate("build a drill")
    assert fake.completions.requests[0]["messages"] == [
        {"role": "user", "content": "build a drill"}
    ]


def test_max_tokens_is_sent():
    api, fake = client(max_tokens=512)
    api.generate("prompt")
    assert fake.completions.requests[0]["max_tokens"] == 512


def test_temperature_is_omitted_unless_set():
    """Some reasoning models reject temperature (docs/fle-integration.md)."""
    api, fake = client()
    api.generate("prompt")
    assert "temperature" not in fake.completions.requests[0]


def test_temperature_is_sent_when_set():
    api, fake = client(temperature=0.2)
    api.generate("prompt")
    assert fake.completions.requests[0]["temperature"] == 0.2


def test_missing_usage_records_zeros_rather_than_failing():
    api, _ = client(response=SimpleNamespace(choices=[], usage=None))
    response = api.generate("prompt")
    assert (response.input_tokens, response.output_tokens) == (0, 0)
    assert response.text == ""


def test_a_provider_without_cache_fields_records_zeros():
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2)
    api, _ = client(
        response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
            usage=usage,
        )
    )
    response = api.generate("prompt")
    assert response.input_tokens == 10
    assert response.cache_read_tokens == 0
    assert response.cache_write_tokens == 0


def test_a_null_message_content_becomes_empty_text():
    api, _ = client(
        response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
            usage=None,
        )
    )
    assert api.generate("prompt").text == ""


def test_a_missing_key_names_the_environment_variable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        APIClient("claude-haiku-4-5")


def test_a_key_from_the_environment_is_used(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    api = APIClient("claude-haiku-4-5")
    assert api.model == "claude-haiku-4-5"


def test_no_request_is_made_when_the_client_is_constructed():
    api, fake = client()
    assert fake.completions.requests == []
    assert api.provider is PROVIDERS["claude"]

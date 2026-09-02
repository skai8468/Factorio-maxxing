"""Tests for the LLM abstraction and the deterministic stub.

Policy extraction is build-order item 6 and is tested separately.
"""

import pytest

from factorio_maxxing.llm import LLMClient, LLMResponse, StubLLMClient


def test_response_carries_raw_usage_and_no_cost():
    response = LLMResponse(
        text="print('hi')",
        model="claude-haiku-4-5",
        input_tokens=120,
        output_tokens=18,
        cache_read_tokens=64,
        cache_write_tokens=8,
        latency_seconds=1.25,
    )
    assert response.input_tokens == 120
    assert response.cache_read_tokens == 64
    assert response.cache_write_tokens == 8
    assert response.latency_seconds == 1.25
    assert not hasattr(response, "cost")


def test_stub_satisfies_the_client_protocol():
    assert isinstance(StubLLMClient(["a"]), LLMClient)


def test_stub_returns_responses_in_call_order():
    client = StubLLMClient(["first", "second"])
    assert client.generate("p1").text == "first"
    assert client.generate("p2").text == "second"


def test_stub_repeats_the_final_response_once_exhausted():
    client = StubLLMClient(["only"])
    assert [client.generate("p").text for _ in range(3)] == ["only"] * 3


def test_stub_ignores_the_prompt():
    """The stub is a fixture: the prompt does not select the response."""
    first, second = StubLLMClient(["a", "b"]), StubLLMClient(["a", "b"])
    assert [first.generate("build a drill").text for _ in range(2)] == [
        second.generate("!!! nonsense").text for _ in range(2)
    ]


def test_stub_reports_the_configured_model():
    assert StubLLMClient(["x"], model="stub-policy").generate("p").model == "stub-policy"


def test_stub_usage_is_deterministic_and_non_zero():
    client = StubLLMClient(["one two three"])
    response = client.generate("alpha beta")
    assert response.input_tokens == 2
    assert response.output_tokens == 3
    assert response.cache_read_tokens == 0
    assert response.cache_write_tokens == 0


def test_stub_latency_is_fixed_for_reproducible_replay():
    client = StubLLMClient(["x"], latency_seconds=0.5)
    assert [client.generate("p").latency_seconds for _ in range(2)] == [0.5, 0.5]


def test_stub_records_prompts_verbatim():
    client = StubLLMClient(["x"])
    client.generate("prompt one")
    client.generate("prompt two")
    assert client.prompts == ["prompt one", "prompt two"]
    assert client.call_count == 2


def test_stub_call_count_starts_at_zero():
    assert StubLLMClient(["x"]).call_count == 0


def test_stub_requires_at_least_one_response():
    with pytest.raises(ValueError, match="at least one response"):
        StubLLMClient([])

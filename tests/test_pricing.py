"""Tests for the run's estimated cost (D9, D55).

Dollars are computed from raw token counts at analysis time and printed, never stored.
"""

import json

import pytest

from factorio_maxxing.pricing import (
    PRICES,
    call_cost,
    format_summary,
    main,
    price_key,
    summarize,
)


def call(role="policy", model="claude-haiku-4-5", inp=0, read=0, write=0, out=0):
    return {
        "type": "llm_call",
        "role": role,
        "model": model,
        "input_tokens": inp,
        "cache_read_tokens": read,
        "cache_write_tokens": write,
        "output_tokens": out,
        "latency_seconds": 0.0,
    }


@pytest.mark.parametrize(
    ("model", "key"),
    [
        ("claude-haiku-4-5", "claude-haiku-4-5"),
        ("open-router-anthropic/claude-haiku-4.5", "claude-haiku-4-5"),
        ("open-router-anthropic/claude-sonnet-5.5", "claude-sonnet-5-5"),
        ("claude-opus-5-5", "claude-opus-5-5"),
    ],
)
def test_direct_and_openrouter_names_share_a_price(model, key):
    assert price_key(model) == key
    assert key in PRICES


def test_a_million_uncached_input_tokens_cost_the_input_price():
    assert call_cost(call(inp=1_000_000)) == pytest.approx(1.00)


def test_cached_tokens_are_priced_at_their_own_rates_not_as_input():
    """input_tokens counts all input (D51); reads and writes are shares of it."""
    record = call(inp=1_000_000, read=600_000, write=400_000)
    assert call_cost(record) == pytest.approx(0.6 * 0.10 + 0.4 * 1.25)


def test_output_is_priced_at_the_output_rate():
    assert call_cost(call(model="claude-sonnet-5-5", out=1_000_000)) == pytest.approx(
        10.0
    )


def test_an_unknown_model_has_no_price():
    assert call_cost(call(model="stub")) is None


def test_summarize_totals_each_role_policy_first():
    records = [
        {"type": "step", "step": 0},
        call(role="verifier", model="claude-sonnet-5-5", inp=1000, out=10),
        call(inp=40_000, read=36_000, out=500),
        call(inp=41_000, read=36_000, out=400),
    ]
    policy, verifier = summarize(records)
    assert (policy.role, policy.calls, policy.input_tokens) == ("policy", 2, 81_000)
    assert policy.cache_read_tokens == 72_000
    assert policy.output_tokens == 900
    assert policy.models == ["claude-haiku-4-5"]
    assert verifier.role == "verifier"


def test_the_summary_says_the_cost_is_an_estimate():
    lines = format_summary(summarize([call(inp=1_000_000)]))
    assert "estimated" in lines[0]
    assert lines[-1].strip() == "total      ~$1.00"


def test_an_unpriced_model_is_named_and_the_total_marked_partial():
    lines = format_summary(
        summarize([call(model="stub", inp=10, out=2), call(inp=1_000_000)])
    )
    text = "\n".join(lines)
    assert "no price for stub" in text
    assert "(priced calls only)" in lines[-1]


def test_no_calls_says_so():
    assert format_summary(summarize([{"type": "step"}])) == [
        "usage:         no model calls recorded"
    ]


def test_main_prices_a_trajectory_file(tmp_path, capsys):
    path = tmp_path / "run.jsonl"
    path.write_text(json.dumps(call(inp=1_000_000)) + "\n", encoding="utf-8")
    assert main([str(path)]) == 0
    assert "~$1.00" in capsys.readouterr().out


def test_main_without_a_path_is_a_usage_error(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err


# --- D64/D66: the grid's models -----------------------------------------------------


def _call(model, input_tokens, output_tokens=0, read=0, write=0):
    return {
        "type": "llm_call",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": read,
        "cache_write_tokens": write,
    }


def test_haiku_5_5_is_priced_at_its_short_prompt_rate():
    cost = call_cost(_call("claude-haiku-5-5", 50_000, 2_000, read=40_000))
    assert cost == pytest.approx((10_000 * 0.10 + 40_000 * 0.01 + 2_000 * 0.50) / 1e6)


def test_haiku_5_5_above_100k_input_tokens_is_billed_five_times():
    cost = call_cost(_call("claude-haiku-5-5", 120_000, 1_000))
    assert cost == pytest.approx((120_000 * 0.50 + 1_000 * 2.50) / 1e6)


def test_gpt_6_luna_is_priced_through_openrouter():
    cost = call_cost(_call("open-router-openai/gpt-6-luna", 10_000, 1_000))
    assert cost == pytest.approx((10_000 * 0.10 + 1_000 * 0.50) / 1e6)


def test_opus_5_5_thinking_bills_as_output():
    cost = call_cost(_call("claude-opus-5-5", 1_000, 3_000))
    assert cost == pytest.approx((1_000 * 4.00 + 3_000 * 20.00) / 1e6)

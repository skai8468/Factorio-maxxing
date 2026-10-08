"""Tests for spend per API key (D56). Offline: no request leaves the machine."""

import json

from factorio_maxxing.llm import APIClient, key_fingerprint
from factorio_maxxing.spend import (
    UNTRACKED,
    current_keys,
    format_openrouter,
    format_spend,
    main,
    openrouter_usage,
    total_by_key,
)
from tests.test_api_client import FakeAnthropic, FakeOpenAI, completion, message


def call(key_id=None, run="run-a", model="claude-haiku-4-5", inp=1_000_000, out=0):
    record = {
        "type": "llm_call",
        "run_id": run,
        "role": "policy",
        "model": model,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "latency_seconds": 0.0,
    }
    if key_id:
        record["key_id"] = key_id
    return record


def test_a_fingerprint_names_the_variable_and_hides_the_key():
    fp = key_fingerprint("OPEN_ROUTER_API_KEY", "sk-or-v1-secret-value")
    assert fp.startswith("OPEN_ROUTER_API_KEY:")
    assert len(fp.split(":")[1]) == 8
    assert "secret" not in fp
    assert fp == key_fingerprint("OPEN_ROUTER_API_KEY", "sk-or-v1-secret-value")
    assert fp != key_fingerprint("OPEN_ROUTER_API_KEY", "sk-or-v1-other-value")


def test_the_client_stamps_every_response_with_its_key_fingerprint(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    api = APIClient("claude-haiku-4-5", client=FakeAnthropic(message()))
    expected = key_fingerprint("ANTHROPIC_API_KEY", "sk-ant-test")
    assert api.key_id == expected
    assert api.generate("p").key_id == expected


def test_an_openrouter_client_is_stamped_with_the_openrouter_key(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test")
    api = APIClient(
        "open-router-anthropic/claude-haiku-4.5", client=FakeOpenAI(completion())
    )
    assert api.generate("p").key_id == key_fingerprint(
        "OPEN_ROUTER_API_KEY", "sk-or-test"
    )


def test_a_client_without_a_key_has_no_fingerprint(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert APIClient("gpt-4o", client=FakeOpenAI(completion())).key_id is None


def test_spend_is_totalled_per_key_and_untracked_runs_last():
    records = [
        call("ANTHROPIC_API_KEY:aaaaaaaa", run="r1"),
        call("ANTHROPIC_API_KEY:aaaaaaaa", run="r2"),
        call(
            "OPEN_ROUTER_API_KEY:bbbbbbbb",
            run="r3",
            model="open-router-anthropic/claude-sonnet-5.5",
        ),
        call(None, run="old"),
        {"type": "step"},
    ]
    anthropic, openrouter, untracked = total_by_key(records)
    assert (anthropic.key_id, len(anthropic.runs), anthropic.calls) == (
        "ANTHROPIC_API_KEY:aaaaaaaa",
        2,
        2,
    )
    assert anthropic.cost == 2.0
    assert openrouter.cost == 2.0
    assert untracked.key_id == UNTRACKED


def test_keys_set_now_are_marked(monkeypatch):
    environ = {"ANTHROPIC_API_KEY": "sk-ant-test", "UNRELATED": "x"}
    current = current_keys(environ)
    fp = key_fingerprint("ANTHROPIC_API_KEY", "sk-ant-test")
    assert current == {fp: "ANTHROPIC_API_KEY"}
    lines = format_spend(total_by_key([call(fp)]), current, 1, "trajectories")
    assert any(fp + " (set now)" in line for line in lines)


def test_the_report_never_prints_a_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-very-secret")
    fp = key_fingerprint("ANTHROPIC_API_KEY", "sk-ant-very-secret")
    (tmp_path / "run.jsonl").write_text(json.dumps(call(fp)) + "\n", encoding="utf-8")
    assert main(["--dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "very-secret" not in output
    assert fp in output
    assert "~$1.00" in output


def test_openrouter_usage_reads_the_key_endpoint():
    seen = {}

    def fetch(request):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        return json.dumps(
            {
                "data": {
                    "label": "sk-or-v1-abc...xyz",
                    "usage": 1.5,
                    "usage_daily": 0.25,
                    "usage_weekly": 1.0,
                    "usage_monthly": 1.5,
                    "limit_remaining": None,
                }
            }
        ).encode()

    data = openrouter_usage("sk-or-test", fetch=fetch)
    assert seen == {
        "url": "https://openrouter.ai/api/v1/key",
        "auth": "Bearer sk-or-test",
    }
    lines = format_openrouter("OPEN_ROUTER_API_KEY:cccccccc", data)
    text = "\n".join(lines)
    assert "all time 1.5000" in text
    assert "remaining unlimited" in text
    assert "sk-or-v1-abc" not in text, "the label is a masked key and is not printed"

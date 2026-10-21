"""Tests for the terminal progress follower."""

import json

from factorio_maxxing import progress
from factorio_maxxing.watch import HudState, TrajectoryTail

RUN = {"type": "run", "run_id": "r", "step": None, "goal": "g", "max_steps": 3}


def step(n, **extra):
    return {
        "type": "step",
        "run_id": "r",
        "step": n,
        "goal": "g",
        "policy": "pass",
        "observation": {"inventory": [{"type": "iron-ore", "quantity": "1"}]},
        "execution_errors": [],
        **extra,
    }


CALL = {
    "type": "llm_call",
    "run_id": "r",
    "step": 0,
    "role": "policy",
    "model": "claude-haiku-5-5",
    "input_tokens": 100_000,  # $0.01 at Haiku 5.5's base rate
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
}
REQUEST = {"type": "help_request", "run_id": "r", "step": 1, "stuck_reason": "stuck"}
ANSWER = {
    "type": "intervention",
    "run_id": "r",
    "step": 1,
    "stuck_reason": "stuck",
    "text": "craft a lab",
    "intervention_index": 0,
}


def write(path, records, mode="a"):
    with path.open(mode, encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def run(tmp_path, ticks=1, once=False):
    lines = []
    progress.follow(
        TrajectoryTail(tmp_path, since=0),
        lines.append,
        once=once,
        sleep=lambda _: None,
        ticks=ticks,
    )
    return lines


def test_the_status_line_has_step_milestone_cost_and_help():
    state = HudState()
    state.feed([RUN, step(0), CALL, REQUEST, ANSWER])
    assert progress.status_line(state) == (
        "step 1/3 · milestone 1/24 · ~$0.01 · help asked 1x"
    )


def test_the_status_line_shows_a_skipped_rung_and_cuts_a_long_error():
    state = HudState()
    lab_science = [{"type": "logistic-science-pack", "quantity": "1"}]
    state.feed(
        [
            {
                **step(0),
                "observation": {"inventory": lab_science},
                "execution_errors": ["x" * 300],
            }
        ]
    )
    line = progress.status_line(state)
    assert "milestone 13/24 (contiguous 0)" in line
    assert line.endswith("...") and len(line.split("error: ")[1]) == progress.ERROR_CHARS


def test_once_prints_where_the_run_is_without_replaying_every_step(tmp_path):
    write(tmp_path / "20261021T100000-a.jsonl", [RUN, step(0), step(1)])
    assert run(tmp_path, once=True) == [
        "following 20261021T100000-a.jsonl",
        "step 2/3 · milestone 1/24 · ~$0.00",
    ]


def test_following_prints_a_line_per_new_step_and_the_help_cycle(tmp_path):
    path = tmp_path / "20261021T100000-a.jsonl"
    write(path, [RUN, step(0)])
    lines = []
    tail = TrajectoryTail(tmp_path, since=0)
    batches = iter([[step(1), REQUEST], [ANSWER], [step(2)]])

    def sleep(_):
        write(path, next(batches, []))

    progress.follow(tail, lines.append, sleep=sleep, ticks=6)
    assert lines == [
        "following 20261021T100000-a.jsonl",
        "step 1/3 · milestone 1/24 · ~$0.00",
        "step 2/3 · milestone 1/24 · ~$0.00",
        "  ⚠ asking for help at step 2: stuck",
        "  answered",
        "step 3/3 · milestone 1/24 · ~$0.00 · help asked 1x",
        "run reached its step cap",
    ]


def test_a_declined_request_says_no_guidance(tmp_path):
    path = tmp_path / "20261021T100000-a.jsonl"
    write(path, [RUN, step(0), step(1), REQUEST])
    lines = []
    tail = TrajectoryTail(tmp_path, since=0)

    def sleep(_):
        write(path, [{**ANSWER, "text": None}])

    progress.follow(tail, lines.append, sleep=sleep, ticks=2)
    assert lines[-1] == "  no guidance"


def test_once_with_no_trajectory_says_so(tmp_path):
    assert run(tmp_path, once=True) == ["no trajectory yet"]


def test_main_rejects_a_non_positive_interval(capsys):
    assert progress.main(["--interval", "0"]) == 2
    assert "interval" in capsys.readouterr().err

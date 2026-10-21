"""Tests for the panel help handoff and the PanelHuman backend (D72)."""

import pytest

from factorio_maxxing.goal import Goal
from factorio_maxxing.help_channel import ANSWER, REQUEST, HelpChannel
from factorio_maxxing.human import HumanProtocol, PanelHuman

GOAL = Goal(description="Progress as far as you can")


def test_a_posted_request_is_pending_until_answered(tmp_path):
    channel = HelpChannel(tmp_path)
    assert channel.pending() is None
    request = channel.post("Progress", "stuck", 12.5)
    assert channel.pending() == request
    assert channel.answer(request.id, "craft a lab")
    assert channel.pending() is None
    assert channel.take_answer(request) == (True, "craft a lab")
    assert not (tmp_path / REQUEST).exists() and not (tmp_path / ANSWER).exists()


def test_no_answer_yet_is_not_an_answer(tmp_path):
    channel = HelpChannel(tmp_path)
    request = channel.post("Progress", "stuck", 0.0)
    assert channel.take_answer(request) == (False, None)
    assert channel.pending() == request


def test_an_answer_to_another_request_is_never_taken(tmp_path):
    channel = HelpChannel(tmp_path)
    first = channel.post("Progress", "stuck", 0.0)
    second = channel.post("Progress", "stuck again", 0.0)
    assert not channel.answer(first.id, "late answer")
    assert channel.take_answer(second) == (False, None)
    assert channel.take_answer(first) == (False, None)


def test_posting_clears_a_stale_answer(tmp_path):
    channel = HelpChannel(tmp_path)
    old = channel.post("Progress", "stuck", 0.0)
    channel.answer(old.id, "old")
    new = channel.post("Progress", "stuck", 0.0)
    assert channel.take_answer(new) == (False, None)


def test_text_is_kept_verbatim_including_unicode_and_newlines(tmp_path):
    channel = HelpChannel(tmp_path)
    request = channel.post("Progress", "stuck", 0.0)
    text = 'Place the boiler ]] "here" —\n  then the engine ✓'
    channel.answer(request.id, text)
    assert channel.take_answer(request) == (True, text)


def test_a_declined_answer_carries_no_text(tmp_path):
    channel = HelpChannel(tmp_path)
    request = channel.post("Progress", "stuck", 0.0)
    channel.answer(request.id, None)
    assert channel.take_answer(request) == (True, None)


def test_a_half_written_file_is_not_read(tmp_path):
    (tmp_path / REQUEST).write_text('{"id": "x", "rea', encoding="utf-8")
    assert HelpChannel(tmp_path).pending() is None


# --- PanelHuman ------------------------------------------------------------------


def answering(channel, text, after=1):
    """A sleep that answers the open request on its ``after``-th call."""
    calls = []

    def sleep(_):
        calls.append(1)
        if len(calls) == after:
            channel.answer(channel.pending().id, text)

    return sleep, calls


def test_panel_human_satisfies_the_protocol(tmp_path):
    assert isinstance(PanelHuman(tmp_path), HumanProtocol)


def test_panel_human_waits_for_the_panel_and_returns_the_text_verbatim(tmp_path):
    human = PanelHuman(tmp_path, output_fn=lambda _: None)
    sleep, calls = answering(human.channel, "  Craft a lab first.\n", after=3)
    human._sleep = sleep
    assert human.ask(GOAL, "obs", "stuck") == "  Craft a lab first.\n"
    assert len(calls) == 3
    assert human.call_count == 1
    assert not (tmp_path / REQUEST).exists()


@pytest.mark.parametrize("text", [None, "", "   \n "])
def test_a_declined_or_blank_panel_answer_is_no_guidance(tmp_path, text):
    human = PanelHuman(tmp_path, output_fn=lambda _: None)
    human._sleep, _ = answering(human.channel, text)
    assert human.ask(GOAL, "obs", "stuck") is None


def test_ctrl_c_in_the_terminal_declines_and_closes_the_request(tmp_path):
    def interrupted(_):
        raise KeyboardInterrupt

    human = PanelHuman(tmp_path, output_fn=lambda _: None, sleep=interrupted)
    assert human.ask(GOAL, "obs", "stuck") is None
    assert HelpChannel(tmp_path).pending() is None


def test_panel_human_tells_the_terminal_where_to_answer(tmp_path):
    said = []
    human = PanelHuman(tmp_path, output_fn=said.append)
    human._sleep, _ = answering(human.channel, "ok")
    human.ask(GOAL, "obs", "3 repeated execution errors: x")
    assert any("answer in the game panel" in line for line in said)
    assert "WHY: 3 repeated execution errors: x" in said


def test_a_new_panel_human_clears_requests_left_by_an_earlier_run(tmp_path):
    HelpChannel(tmp_path).post("old run", "stuck", 0.0)
    PanelHuman(tmp_path)
    assert HelpChannel(tmp_path).pending() is None

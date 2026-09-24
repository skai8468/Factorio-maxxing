"""Tests for the live-watching camera (decisions.md D45).

The Lua itself was exercised against a live cluster; these pin the Python around it:
what is sent, when it logs, and that a failure never stops the camera.
"""

import logging
import subprocess
import sys

import pytest

from factorio_maxxing import watch
from factorio_maxxing.watch import FOLLOW_LUA, follow_once


class FakeRCON:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def __call__(self, command):
        self.sent.append(command)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_importing_watch_does_not_import_fle():
    probe = "import sys, factorio_maxxing.watch; print('fle' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


def test_follow_lua_is_a_single_line():
    assert "\n" not in FOLLOW_LUA


def test_follow_lua_targets_the_agent_and_every_connected_player():
    assert "storage.agent_characters[1]" in FOLLOW_LUA
    assert "game.connected_players" in FOLLOW_LUA
    assert "defines.controllers.spectator" in FOLLOW_LUA
    assert "teleport(c.position, c.surface)" in FOLLOW_LUA


def test_follow_lua_keeps_its_status_strings_intact():
    for status in watch.STATUS_MESSAGES:
        assert f'"{status}"' in FOLLOW_LUA


def test_follow_once_sends_a_silent_command_and_strips_the_reply():
    rcon = FakeRCON(["following\n"])
    assert follow_once(rcon) == "following"
    assert rcon.sent == [f"/sc {FOLLOW_LUA}"]


def test_follow_once_treats_no_reply_as_empty():
    assert follow_once(FakeRCON([None])) == ""


def test_watch_moves_the_camera_once_per_tick_and_sleeps_between(caplog):
    rcon = FakeRCON(["following"] * 3)
    sleeps = []
    watch.watch(rcon, 0.5, sleep=sleeps.append, ticks=3)
    assert len(rcon.sent) == 3
    assert sleeps == [0.5, 0.5]


def test_watch_logs_only_when_the_status_changes(caplog):
    rcon = FakeRCON(["no client", "no client", "following", "following", "no agent"])
    with caplog.at_level(logging.INFO):
        watch.watch(rcon, 0, sleep=lambda _: None, ticks=5)
    assert caplog.messages == [
        watch.STATUS_MESSAGES["no client"],
        watch.STATUS_MESSAGES["following"],
        watch.STATUS_MESSAGES["no agent"],
    ]


def test_watch_survives_a_failed_move(caplog):
    rcon = FakeRCON([ConnectionError("blip"), "following"])
    with caplog.at_level(logging.INFO):
        watch.watch(rcon, 0, sleep=lambda _: None, ticks=2)
    assert len(rcon.sent) == 2
    assert caplog.messages == ["error: blip", watch.STATUS_MESSAGES["following"]]


def test_watch_reports_an_unknown_status_verbatim(caplog):
    with caplog.at_level(logging.INFO):
        watch.watch(FakeRCON(["something odd"]), 0, sleep=lambda _: None, ticks=1)
    assert caplog.messages == ["something odd"]


def test_main_rejects_a_non_positive_interval_before_connecting(monkeypatch, capsys):
    def refuse():
        raise AssertionError("must not connect")

    monkeypatch.setattr(watch, "connect", refuse)
    assert watch.main(["--interval", "0"]) == 2
    assert "--interval" in capsys.readouterr().err


def test_main_reports_a_missing_cluster(monkeypatch, capsys):
    def no_cluster():
        raise ConnectionError("no running Factorio container")

    monkeypatch.setattr(watch, "connect", no_cluster)
    assert watch.main([]) == 2
    assert "no running Factorio container" in capsys.readouterr().err


def test_main_stops_cleanly_on_interrupt(monkeypatch):
    monkeypatch.setattr(watch, "connect", lambda: FakeRCON([]))

    def interrupted(send, interval):
        raise KeyboardInterrupt

    monkeypatch.setattr(watch, "watch", interrupted)
    assert watch.main([]) == 0


@pytest.mark.parametrize("interval", ["1", "0.25"])
def test_parser_accepts_an_interval(interval):
    assert watch.build_parser().parse_args(["--interval", interval]).interval == float(
        interval
    )

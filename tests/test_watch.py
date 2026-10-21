"""Tests for watching a live run (decisions.md D45, D47, D59, D63).

The Lua itself is exercised against a live cluster; these pin the Python around it and
the properties of the Lua that keep a run safe: what is sent and when, that nothing
touches FLE's handlers or ``storage``, that drawings are for watchers only, and that a
failure never stops the watcher.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from factorio_maxxing import watch
from factorio_maxxing.watch import (
    CHECK_LUA,
    CameraSettings,
    HudState,
    TrajectoryTail,
    camera_lua,
    check_once,
    hud_lines,
    hud_lua,
    intent_of,
    last_error_line,
)


class FakeRCON:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def __call__(self, command):
        self.sent.append(command)
        response = self.responses.pop(0) if self.responses else ""
        if isinstance(response, Exception):
            raise response
        return response


def quiet(_):
    return None


def test_importing_watch_does_not_import_fle():
    probe = "import sys, factorio_maxxing.watch; print('fle' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


# --- The check -----------------------------------------------------------------------


def test_check_lua_is_a_single_line_without_comments():
    assert "\n" not in CHECK_LUA and "--" not in CHECK_LUA


def test_check_lua_keeps_watchers_out_of_the_game():
    """The human provides text only; the permission group makes the game enforce it."""
    assert f'create_group("{watch.WATCHER_GROUP}")' in CHECK_LUA
    assert "set_allows_action(a, false)" in CHECK_LUA
    assert "p.permission_group = g" in CHECK_LUA
    assert CHECK_LUA.index("p.permission_group = g") < CHECK_LUA.index(
        "storage.agent_characters"
    )


def test_watchers_are_in_god_mode_before_any_agent_exists():
    """No character for FLE's reset to destroy - losing one dropped a client (D38)."""
    assert "{type = defines.controllers.god}" in CHECK_LUA
    assert CHECK_LUA.index("defines.controllers.god") < CHECK_LUA.index('"no agent "')


def test_check_lua_creates_no_global_so_it_is_safe_before_a_join():
    """A Lua global set before a client joins is missing from the joiner's state."""
    assert "harness_watch =" not in CHECK_LUA
    assert "harness_watch_beat" in CHECK_LUA  # read only


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("no client\n", ("no client", False)),
        ("no agent 0", ("no agent", False)),
        ("following 1", ("following", True)),
        ("following 0", ("following", False)),
        (None, ("", False)),
        ("something odd", ("something odd", False)),
    ],
)
def test_check_once_parses_status_and_camera(reply, expected):
    rcon = FakeRCON([reply])
    assert check_once(rcon) == expected
    assert rcon.sent == [f"/sc {CHECK_LUA}"]


# --- The camera ----------------------------------------------------------------------


def test_the_camera_never_takes_fles_on_tick_handler():
    """FLE registers on_tick itself; one handler per event means taking it switches
    FLE's off (D63). FLE's nth-tick periods are 5, 15 and 60."""
    lua = camera_lua(CameraSettings())
    assert "script.on_nth_tick(1," in lua
    assert "defines.events.on_tick" not in lua
    for period in (5, 15, 60):
        assert f"on_nth_tick({period}" not in lua


def test_the_camera_handler_runs_under_pcall():
    """A script error inside an event kills a headless server (D48)."""
    lua = camera_lua(CameraSettings())
    assert "pcall(harness_watch_tick, event)" in lua


def test_the_camera_writes_nothing_to_storage():
    """Lua functions in storage break the save a joining client needs (D43)."""
    lua = camera_lua(CameraSettings())
    assert "storage.agent_characters[1]" in lua  # read only
    assert "storage." not in lua.replace("storage.agent_characters", "")


def test_every_drawing_is_for_watchers_only():
    lua = camera_lua(CameraSettings())
    for call in (
        "rendering.draw_line{",
        "rendering.draw_rectangle{",
        "rendering.draw_text{",
    ):
        start = lua.index(call)
        assert (
            "players = players"
            in lua[start : lua.index("}", lua.index("time_to_live", start))]
        )
    assert "create_local_flying_text" in lua
    assert "create_entity" not in lua and ".insert" not in lua and ".destroy()" not in lua


def test_camera_settings_reach_the_lua():
    lua = camera_lua(CameraSettings(zoom=0.5, glide=0.2, snap=30, cues=False))
    assert "p.zoom = 0.5" in lua
    assert "glide = 0.2" in lua and "snap = 30" in lua and "cues = false" in lua


def test_camera_lua_is_one_line_without_comments_or_placeholders():
    lua = camera_lua(CameraSettings())
    assert "\n" not in lua and "--" not in lua and "$" not in lua


# --- The loop ------------------------------------------------------------------------


def test_the_camera_is_installed_only_after_a_client_joins():
    rcon = FakeRCON(["no client", "no client", "no agent 0", "installed", "no agent 1"])
    watch.watch(rcon, 0, sleep=quiet, ticks=4)
    installs = [c for c in rcon.sent if "on_nth_tick(1," in c]
    assert len(installs) == 1
    assert rcon.sent.index(installs[0]) == 3  # after the third check


def test_a_running_camera_is_not_reinstalled():
    rcon = FakeRCON(["following 1"] * 3)
    watch.watch(rcon, 0, sleep=quiet, ticks=3)
    assert not any("on_nth_tick(1," in c for c in rcon.sent)


def test_a_dead_camera_is_reinstalled():
    """A server restart drops runtime handlers; the heartbeat notices."""
    rcon = FakeRCON(["following 1", "following 0", "installed", "following 1"])
    watch.watch(rcon, 0, sleep=quiet, ticks=3)
    assert sum("on_nth_tick(1," in c for c in rcon.sent) == 1


def test_watch_checks_once_per_tick_and_sleeps_between():
    rcon = FakeRCON(["following 1"] * 3)
    sleeps = []
    watch.watch(rcon, 0.5, sleep=sleeps.append, ticks=3)
    assert len(rcon.sent) == 3
    assert sleeps == [0.5, 0.5]


def test_watch_logs_only_when_the_status_changes(caplog):
    replies = ["no client", "no client", "following 1", "following 1", "no agent 1"]
    with caplog.at_level(logging.INFO):
        watch.watch(FakeRCON(replies), 0, sleep=quiet, ticks=5)
    assert caplog.messages == [
        watch.STATUS_MESSAGES["no client"],
        watch.STATUS_MESSAGES["following"],
        watch.STATUS_MESSAGES["no agent"],
    ]


def test_watch_survives_a_failed_check(caplog):
    rcon = FakeRCON([ConnectionError("blip"), "following 1"])
    with caplog.at_level(logging.INFO):
        watch.watch(rcon, 0, sleep=quiet, ticks=2)
    assert len(rcon.sent) == 2
    assert caplog.messages == ["error: blip", watch.STATUS_MESSAGES["following"]]


def test_watch_reports_an_unknown_status_verbatim(caplog):
    with caplog.at_level(logging.INFO):
        watch.watch(FakeRCON(["something odd"]), 0, sleep=quiet, ticks=1)
    assert caplog.messages == ["something odd"]


# --- The HUD -------------------------------------------------------------------------


def write_records(path, records, mode="a"):
    with path.open(mode, encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


STEP = {
    "type": "step",
    "run_id": "r",
    "step": 13,
    "goal": "Unlock steam power and build a working steam power setup",
    "policy": "# Place boiler next to the offshore pump\nplace_entity(Prototype.Boiler)",
    "observation": {},
    "execution_errors": [
        "1: ('\\nException: Could not place boiler at (-5.5, 33.5), No boiler in "
        "inventory. Current inventory: coal=5',)"
    ],
}
CALL = {
    "type": "llm_call",
    "run_id": "r",
    "step": 13,
    "role": "policy",
    "model": "claude-haiku-4-5",
    "input_tokens": 1000,
    "output_tokens": 100,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
}
HELP = {
    "type": "intervention",
    "run_id": "r",
    "step": 8,
    "stuck_reason": "x",
    "text": 'craft the "offshore pump", boiler]] and pipes first',
    "intervention_index": 0,
}
VERDICT = {"type": "verification", "step": 13, "done": False, "reason": "boiler idle"}


def text_of(state):
    return [line for line, _ in hud_lines(state)]


def line_starting(state, prefix):
    return next(line for line in text_of(state) if line.startswith(prefix))


def test_hud_shows_the_example_lines():
    state = HudState()
    state.feed([HELP, STEP, CALL, VERDICT])
    text = text_of(state)
    assert text[0].startswith("Step 14 · claude-haiku-4-5 · ~$")
    assert text[0].endswith("help asked 1×")
    assert text[1].startswith("Goal: Unlock steam power")
    assert text[2] == "Milestone 0/13"
    assert text[3] == "▶ Place boiler next to the offshore pump"
    assert "No boiler in inventory" in text[4] and text[4].startswith("✗ ")
    assert text[5] == "? Verifier: not done - boiler idle"
    assert (
        text[6] == 'Human (step 9): craft the "offshore pump", boiler]] and pipes first'
    )


def test_hud_shows_a_declined_request_and_a_clean_step():
    state = HudState()
    state.feed(
        [STEP, {**HELP, "text": None}, {**STEP, "step": 14, "execution_errors": []}]
    )
    text = text_of(state)
    assert not any(line.startswith("✗") for line in text)
    assert "Human (step 9): (no guidance)" in text


def test_hud_shows_the_whole_goal_and_cuts_only_long_free_text():
    long = "word " * 200
    state = HudState()
    state.feed([{**STEP, "goal": long, "policy": "# " + long}, {**HELP, "text": long}])
    goal = line_starting(state, "Goal:")
    intent = line_starting(state, "▶")
    human = line_starting(state, "Human")
    assert goal == "Goal: " + " ".join(long.split())
    assert intent.endswith("…") and len(intent) <= len("▶ ") + watch.HUD_WIDTH
    assert human.endswith("…") and human.startswith("Human (step 9): word word")


OBSERVATION = {
    "inventory": [
        {"quantity": "57", "type": "iron-plate"},
        {"quantity": "22", "type": "coal"},
        {"quantity": "5", "type": "copper-plate"},
    ],
    "flows": {
        "input": [{"type": "coal", "rate": 4}, {"type": "water", "rate": 35.49}],
        "output": [{"type": "steam", "rate": 355}, {"type": "iron-plate", "rate": 2.5}],
    },
    "entities": [
        {"name": "burner-mining-drill", "status": "EntityStatus.WORKING"},
        {"name": "burner-mining-drill", "status": "EntityStatus.WORKING"},
        {"name": "burner-mining-drill", "status": "EntityStatus.NO_FUEL"},
        {"name": "stone-furnace", "status": "EntityStatus.NO_INGREDIENTS"},
        {"name": "pipe-group", "status": "EntityStatus.FULL_OUTPUT"},
    ],
    "research": {
        "technologies": [
            {"name": "automation", "researched": 0},
            {"name": "steam-power", "researched": 1},
            {"name": "electronics", "researched": 1},
        ],
        "current_research": "None",
    },
    "game_info": {"tick": 30797, "time": 513.28, "speed": 10},
}
RUN = {"type": "run", "run_id": "r", "step": None, "goal": "Progress", "max_steps": 300}


def panel(observation=OBSERVATION):
    state = HudState()
    state.feed([RUN, {**STEP, "observation": observation}, CALL])
    return state


def after(state, heading):
    text = text_of(state)
    return text[text.index(heading) + 1]


def test_the_header_counts_against_the_step_cap_and_shows_game_time():
    header = text_of(panel())[0]
    assert header.startswith("Step 14/300 · claude-haiku-4-5 · ~$")
    assert "game 8:33" in header


def test_the_panel_lists_the_inventory_largest_first_with_icons():
    assert after(panel(), "Inventory") == (
        "[item=iron-plate] 57   [item=coal] 22   [item=copper-plate] 5"
    )


def test_the_panel_shows_this_steps_production_with_fluid_icons():
    state = panel()
    text = text_of(state)
    made = text[text.index("Production this step") + 1]
    used = text[text.index("Production this step") + 2]
    assert made == "Made: [fluid=steam] +355   [item=iron-plate] +2.5"
    assert used == "Used: [fluid=water] -35   [item=coal] -4"


def test_the_factory_line_counts_working_entities_and_names_problems():
    assert after(panel(), "Factory") == (
        "[item=burner-mining-drill] 2/3 working (1 no fuel)   "
        "pipe-group 0/1 working (1 output full)   "
        "[item=stone-furnace] 0/1 working (1 no ingredients)"
    )


def test_the_research_line_lists_what_is_done_and_in_progress():
    assert after(panel(), "Research") == (
        "2 done: steam-power, electronics · researching: nothing"
    )
    busy = {**OBSERVATION["research"], "current_research": "automation"}
    assert after(panel({**OBSERVATION, "research": busy}), "Research").endswith(
        "researching: automation"
    )


def test_the_milestone_line_follows_the_scorer():
    state = panel()
    milestone = line_starting(state, "Milestone")
    assert milestone == "Milestone 5/13: Copper plate (step 14)"


def test_long_lists_are_cut_with_a_count_of_the_rest():
    many = [{"quantity": str(100 - i), "type": f"item-{i:02}"} for i in range(20)]
    line = after(panel({**OBSERVATION, "inventory": many}), "Inventory")
    assert line.count("[item=") == watch.HUD_ITEMS
    assert line.endswith("+4 more")


def test_missing_or_odd_observation_fields_leave_their_lines_empty():
    state = panel({"inventory": "not a list", "flows": None, "entities": [42]})
    assert after(state, "Inventory") == "(empty)"
    assert after(state, "Factory") == "(nothing built)"
    assert line_starting(state, "Made:") == "Made: nothing"


def test_the_default_trajectory_dir_is_the_repository_s():
    """The watcher is started from ~/fle-work; a relative default found nothing."""
    assert watch.DEFAULT_TRAJECTORY_DIR == Path(watch.__file__).resolve().parents[1] / (
        "trajectories"
    )
    assert watch.build_parser().parse_args([]).trajectory_dir == str(
        watch.DEFAULT_TRAJECTORY_DIR
    )


def test_hud_lua_survives_quotes_and_closing_brackets():
    lua = hud_lua([('say "hi" ]] and ]=] here', "human")])
    assert '[==[say "hi" ]] and ]=] here]==]' in lua
    assert "harness_hud" in lua and "game.connected_players" in lua


def test_intent_and_error_extraction():
    assert intent_of("x = 1\n# not first\n") == "not first"
    assert intent_of("move_to(p)\n") == "move_to(p)"
    assert intent_of("") == ""
    assert last_error_line(STEP["execution_errors"][0]).startswith(
        "Could not place boiler"
    )
    assert last_error_line("") == ""


def test_tail_reads_only_appended_lines_and_waits_for_a_complete_line(tmp_path):
    tail = TrajectoryTail(tmp_path, since=0)
    run = tmp_path / "20261019T100000-a.jsonl"
    write_records(run, [STEP])
    assert tail.poll() == (True, [STEP])
    assert tail.poll() == (False, [])
    with run.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(CALL)[:20])
    assert tail.poll() == (False, [])
    with run.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(CALL)[20:] + "\n")
    assert tail.poll() == (False, [CALL])


def test_tail_moves_to_the_next_run_and_ignores_older_files(tmp_path):
    old = tmp_path / "20261018T000000-old.jsonl"
    write_records(old, [STEP])
    started = old.stat().st_mtime
    os.utime(old, (started - 100, started - 100))  # written before the watcher started
    tail = TrajectoryTail(tmp_path, since=started - 1)
    assert tail.poll() == (False, [])
    write_records(tmp_path / "20261019T100000-a.jsonl", [STEP])
    assert tail.poll()[0] is True
    write_records(tmp_path / "20261019T110000-b.jsonl", [CALL])
    switched, records = tail.poll()
    assert switched and records == [CALL]


def test_the_hud_is_pushed_when_the_trajectory_grows(tmp_path):
    write_records(tmp_path / "20261019T100000-a.jsonl", [STEP, CALL])
    rcon = FakeRCON(["following 1", "", "following 1"])
    watch.watch(rcon, 0, tail=TrajectoryTail(tmp_path, since=0), sleep=quiet, ticks=2)
    huds = [c for c in rcon.sent if "harness_hud" in c]
    assert len(huds) == 1 and "Step 14" in huds[0]


def test_the_hud_waits_for_a_client(tmp_path):
    write_records(tmp_path / "20261019T100000-a.jsonl", [STEP])
    rcon = FakeRCON(["no client", "no agent 0", "installed", ""])
    watch.watch(rcon, 0, tail=TrajectoryTail(tmp_path, since=0), sleep=quiet, ticks=2)
    assert "harness_hud" not in " ".join(rcon.sent[:1])
    assert any("harness_hud" in c for c in rcon.sent[1:])


# --- main ----------------------------------------------------------------------------


def test_pregenerate_generates_the_start_area_when_nobody_is_connected():
    rcon = FakeRCON(["0", ""])
    assert watch.pregenerate(rcon) == "generated"
    assert rcon.sent[1] == watch.PREGENERATE_LUA
    assert "request_to_generate_chunks({x = 0, y = 0}, 25)" in watch.PREGENERATE_LUA


def test_pregenerate_is_skipped_with_a_client_connected():
    rcon = FakeRCON(["1"])
    assert watch.pregenerate(rcon) == "skipped"
    assert len(rcon.sent) == 1


def test_main_generates_the_map_before_watching(monkeypatch):
    order = []
    monkeypatch.setattr(watch, "connect", lambda: FakeRCON([]))
    monkeypatch.setattr(watch, "pregenerate", lambda send: order.append("map") or "ok")
    monkeypatch.setattr(
        watch, "watch", lambda send, interval, **kw: order.append("watch")
    )
    assert watch.main([]) == 0
    assert order == ["map", "watch"]


def test_main_passes_camera_and_hud_options(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(watch, "connect", lambda: FakeRCON([]))
    monkeypatch.setattr(watch, "watch", lambda send, interval, **kw: seen.update(kw))
    args = ["--no-pregenerate", "--zoom", "0.5", "--no-cues"]
    assert watch.main([*args, "--trajectory-dir", str(tmp_path)]) == 0
    assert seen["camera"] == CameraSettings(zoom=0.5, cues=False)
    assert seen["tail"].directory == tmp_path
    assert watch.main([*args, "--no-hud"]) == 0
    assert seen["tail"] is None


@pytest.mark.parametrize("flag", ["--interval", "--zoom"])
def test_main_rejects_non_positive_values_before_connecting(monkeypatch, capsys, flag):
    def refuse():
        raise AssertionError("must not connect")

    monkeypatch.setattr(watch, "connect", refuse)
    assert watch.main([flag, "0"]) == 2
    assert flag in capsys.readouterr().err


def test_main_reports_a_missing_cluster(monkeypatch, capsys):
    def no_cluster():
        raise ConnectionError("no running Factorio container")

    monkeypatch.setattr(watch, "connect", no_cluster)
    assert watch.main([]) == 2
    assert "no running Factorio container" in capsys.readouterr().err


def test_main_stops_cleanly_on_interrupt(monkeypatch):
    monkeypatch.setattr(watch, "connect", lambda: FakeRCON(["0", ""]))

    def interrupted(send, interval, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(watch, "watch", interrupted)
    assert watch.main([]) == 0


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("iron-plate", "[item=iron-plate]"),
        ("steam", "[fluid=steam]"),
        ("pipe-group", "pipe-group"),
        ("science", "science"),
    ],
)
def test_icons_only_for_real_items_and_fluids(name, expected):
    assert watch.icon(name) == expected


# --- Waiting for help (D72) ------------------------------------------------------

REQUEST_RECORD = {
    "type": "help_request",
    "run_id": "r",
    "step": 19,
    "stuck_reason": "20 steps without a request for help",
}


def test_the_panel_knows_the_run_is_waiting_until_the_answer_arrives():
    state = HudState()
    state.feed([STEP, REQUEST_RECORD])
    assert state.waiting == (19, "20 steps without a request for help")
    assert watch.hud_help(state, None) == (
        "⚠ The harness is asking for help (step 20): 20 steps without a request for "
        "help. Answer in the run's terminal."
    )
    state.feed([{**HELP, "step": 19}])
    assert state.waiting is None
    assert watch.hud_help(state, None) is None


def test_a_panel_request_asks_for_the_answer_in_the_panel():
    from factorio_maxxing.help_channel import Request

    state = HudState()
    state.feed([STEP, REQUEST_RECORD])
    text = watch.hud_help(state, Request("id", "g", "stuck", 0.0))
    assert text.startswith("⚠ The harness is asking for help (step 20): stuck.")
    assert "press Send" in text


def test_the_title_keeps_step_and_milestone_for_a_hidden_panel():
    state = HudState()
    state.feed([{**RUN}, STEP])
    assert watch.hud_title(state) == "Agent · step 14/300 · milestone 0/13"


def test_the_panel_buttons_are_installed_with_the_camera(tmp_path):
    rcon = FakeRCON(["following 0"])
    watch.watch(rcon, 0.01, tail=TrajectoryTail(tmp_path), sleep=quiet, ticks=1)
    assert any("on_gui_click" in command for command in rcon.sent)


def test_no_answer_is_polled_while_no_request_is_open(tmp_path):
    rcon = FakeRCON(["following 1", "following 1", "following 1"])
    watch.watch(rcon, 0.01, tail=TrajectoryTail(tmp_path), sleep=quiet, ticks=3)
    assert not any("harness_panel.answer" in command for command in rcon.sent)

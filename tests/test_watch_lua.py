"""The watcher's panel Lua, run in a real Lua interpreter against a small mock of
Factorio's GUI API (D72).

The panel can only be seen in a live game, but its logic - rebuilding without losing
what the watcher typed, the Hide/Show toggle, Send and Decline, the permission change -
runs here offline. The mock models only what the panel touches: elements with ``add``,
``destroy``, children by name, ``style``, ``tags``; one connected player; the watchers'
permission group; ``on_gui_click``; ``helpers.table_to_json``; ``rcon.print``.
"""

import json

import pytest

from factorio_maxxing import watch
from factorio_maxxing.help_channel import HelpChannel

lupa = pytest.importorskip("lupa")

MOCK = r"""
function new_element(spec, parent)
  local el = {type = spec.type, name = spec.name, caption = spec.caption,
    text = spec.text or "", tags = spec.tags or {}, style = {}, children = {},
    parent = parent, valid = true, visible = true, _by_name = {}}
  el.add = function(s)
    local c = new_element(s, el)
    table.insert(el.children, c)
    if s.name then el._by_name[s.name] = c end
    return c
  end
  el.destroy = function()
    el.valid = false
    local p = el.parent
    if p then
      if el.name then p._by_name[el.name] = nil end
      for i, c in ipairs(p.children) do
        if c == el then table.remove(p.children, i) break end
      end
    end
  end
  return setmetatable(el, {__index = function(t, k) return rawget(t, "_by_name")[k] end})
end
GROUP = {allowed = {}}
GROUP.set_allows_action = function(a, v) GROUP.allowed[a] = v end
PLAYER = {index = 1, name = "watcher", gui = {screen = new_element({type = "screen"})}}
game = {tick = 0, connected_players = {PLAYER},
  permissions = {get_group = function(name) return GROUP end},
  get_player = function(i) return PLAYER end}
defines = {
  input_action = {gui_click = 1, gui_text_changed = 2, gui_confirmed = 3,
    gui_location_changed = 4, build = 5, begin_mining = 6, open_gui = 7},
  events = {on_gui_click = 100}}
HANDLERS = {}
script = {on_event = function(e, f) HANDLERS[e] = f end}
PRINTED = {}
rcon = {print = function(s) table.insert(PRINTED, s) end}
function find(el, name)
  if el.name == name then return el end
  for _, c in ipairs(el.children) do
    local hit = find(c, name)
    if hit then return hit end
  end
end
function click(name)
  HANDLERS[defines.events.on_gui_click]({element = find(PLAYER.gui.screen, name),
    player_index = 1})
end
function labels(el, out)
  out = out or {}
  if el.type == "label" then table.insert(out, el.caption) end
  for _, c in ipairs(el.children) do labels(c, out) end
  return out
end
"""


class Game:
    """A Lua state with the mock loaded; ``send`` runs a ``/sc`` command like RCON."""

    def __init__(self):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(MOCK)
        self.lua.globals().helpers = self.lua.table_from(
            {"table_to_json": lambda t: json.dumps(dict(t.items()))}
        )

    def run(self, code: str) -> str:
        g = self.lua.globals()
        g.PRINTED = self.lua.table_from([])
        self.lua.execute(code)
        return "\n".join(str(v) for v in g.PRINTED.values())

    def send(self, command: str) -> str:
        return self.run(command.removeprefix("/sc "))

    def el(self, name):
        return self.lua.globals().find(self.lua.globals().PLAYER.gui.screen, name)

    def click(self, name):
        self.lua.globals().click(name)

    def labels(self):
        frame = self.el("harness_hud")
        return list(self.lua.globals().labels(frame).values())


LINES = [("Step 40/300 · claude-haiku-5-5", "plain"), ("Inventory", "heading")]


def panel(game, request_id=None, help_text=None):
    game.run(watch.hud_lua(LINES, "Agent · step 40/300", help_text, request_id))
    return game.el("harness_hud")


@pytest.fixture
def game():
    g = Game()
    g.run(watch.PANEL_LUA)
    return g


def test_installing_the_panel_allows_gui_input_and_nothing_else(game):
    allowed = dict(game.lua.globals().GROUP.allowed.items())
    actions = game.lua.globals().defines.input_action
    assert {a: allowed.get(actions[a]) for a in watch.PANEL_ACTIONS} == dict.fromkeys(
        watch.PANEL_ACTIONS, True
    )
    for blocked in ("build", "begin_mining", "open_gui"):
        assert allowed.get(actions[blocked]) is None


def test_the_panel_has_a_title_bar_a_toggle_and_its_lines(game):
    panel(game)
    assert game.el("harness_bar").drag_target.name == "harness_hud"
    assert game.el("harness_hud_toggle").caption == "Hide"
    assert game.labels() == ["Agent · step 40/300", *[text for text, _ in LINES]]
    assert game.el("harness_help") is None


def test_hide_collapses_the_body_and_survives_a_rebuild(game):
    panel(game)
    game.click("harness_hud_toggle")
    assert game.el("harness_body").visible is False
    assert game.el("harness_hud_toggle").caption == "Show"
    panel(game)
    assert game.el("harness_body").visible is False
    assert game.el("harness_hud_toggle").caption == "Show"
    game.click("harness_hud_toggle")
    assert game.el("harness_body").visible is True


def test_the_help_section_shows_even_when_the_panel_is_hidden(game):
    panel(game)
    game.click("harness_hud_toggle")
    panel(game, "req-1", "⚠ The harness is asking for help: stuck")
    assert game.el("harness_body").visible is False
    assert game.el("harness_help").visible is True
    assert game.el("harness_help_text") is not None


def test_a_rebuild_keeps_the_panels_position(game):
    panel(game).location = game.lua.table_from({"x": 300, "y": 40})
    location = panel(game).location
    assert (location.x, location.y) == (300, 40)


def test_a_rebuild_keeps_half_typed_text_for_the_same_request_only(game):
    panel(game, "req-1", "help?")
    game.el("harness_help_text").text = "Craft a lab"
    panel(game, "req-1", "help?")
    assert game.el("harness_help_text").text == "Craft a lab"
    panel(game, "req-2", "help again?")
    assert game.el("harness_help_text").text == ""


def test_send_hands_over_the_typed_text_verbatim_once(game):
    panel(game, "req-1", "help?")
    typed = 'Craft a lab ]] first,\nthen "power" it.'
    game.el("harness_help_text").text = typed
    game.click("harness_help_send")
    assert json.loads(game.run(watch.TAKE_ANSWER_LUA)) == {"id": "req-1", "text": typed}
    assert game.run(watch.TAKE_ANSWER_LUA) == ""


def test_decline_hands_over_no_text(game):
    panel(game, "req-1", "help?")
    game.el("harness_help_text").text = "half a thought"
    game.click("harness_help_decline")
    assert json.loads(game.run(watch.TAKE_ANSWER_LUA)) == {"id": "req-1"}


def test_a_second_click_on_the_same_request_is_ignored(game):
    panel(game, "req-1", "help?")
    game.click("harness_help_send")
    game.run(watch.TAKE_ANSWER_LUA)
    panel(game, "req-1", "help?")
    assert game.el("harness_help_send") is None
    assert "Sent - waiting for the harness to take it." in game.labels()


def test_a_waiting_terminal_request_shows_without_a_text_box(game):
    panel(game, None, "⚠ asking for help. Answer in the run's terminal.")
    assert game.el("harness_help") is not None
    assert game.el("harness_help_text") is None


def test_the_watcher_relays_a_panel_answer_to_the_harness(game, tmp_path):
    """End to end, minus the waiting: harness posts, watcher shows, watcher answers."""
    channel = HelpChannel(tmp_path)
    posted = channel.post("Progress", "20 steps without a request for help", 0.0)

    shown, changed = watch.relay_help(game.send, channel, None)
    assert shown == posted and changed
    panel(game, shown.id, watch.hud_help(watch.HudState(), shown))
    game.el("harness_help_text").text = "Smelt more iron before building labs."
    game.click("harness_help_send")

    shown, changed = watch.relay_help(game.send, channel, shown)
    assert shown is None and changed
    assert channel.take_answer(posted) == (True, "Smelt more iron before building labs.")
    assert channel.pending() is None


def test_a_declined_panel_answer_reaches_the_harness_as_none(game, tmp_path):
    channel = HelpChannel(tmp_path)
    posted = channel.post("Progress", "stuck", 0.0)
    shown, _ = watch.relay_help(game.send, channel, None)
    panel(game, shown.id, "help?")
    game.click("harness_help_decline")
    watch.relay_help(game.send, channel, shown)
    assert channel.take_answer(posted) == (True, None)

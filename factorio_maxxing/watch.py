"""Watch a live run in a connected Factorio client: camera, action cues and a HUD.

See docs/decisions.md D45, D47, D59, D63 and docs/fle-integration.md ("Watching a run
live").

This is a side channel beside the harness, not part of it. It opens its own RCON
connection, generates the map around spawn before anyone joins, and then, ten times a
second, makes sure every connected player is locked out of the game and in god mode.
Once a client has joined it installs a per-tick handler that glides the watchers'
view onto the agent and draws what the agent just did - flying text for inventory
changes, a highlight on each entity it places, a trail where it teleports - and it
tails the run's trajectory file to keep a small HUD up to date.

Everything it does is read or draw. It never touches the loop, the recorder, the policy
or the agent: FLE's agent character is a free-standing entity in
``storage.agent_characters``, attached to no player, no FLE tool reads a connected
player, and every rendering, flying text and GUI element is drawn for watchers only.
It writes nothing to ``storage``.

The client must join **before** the run starts and stay connected. A join forces a map
save, and a save fails once FLE has loaded its tools into ``storage`` (D43). Joining a
freshly started cluster works; rejoining mid-session does not. The per-tick handler is
installed only *after* the join: a handler registered from the console is not in the
save, so a client joining after it would be refused (D63).

    python -m factorio_maxxing.watch
"""

import argparse
import json
import logging
import re
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

from factorio_maxxing.help_channel import HelpChannel, Request
from factorio_maxxing.scorer import ScoreTracker

DEFAULT_INTERVAL = 0.1
"""Seconds between checks. Each check keeps watchers locked out and in god mode,
installs the camera if it is not running, and refreshes the HUD. The camera itself
moves every game tick, inside the game (D63)."""

PREGENERATE_RADIUS = 25
"""Chunks around spawn generated before anyone joins - the radius FLE's own
``_generate_chunks`` forces at the start of every run."""

PREGENERATE_LUA = (
    "/silent-command local s = game.surfaces[1] "
    f"s.request_to_generate_chunks({{x = 0, y = 0}}, {PREGENERATE_RADIUS}) "
    "s.force_generate_chunk_requests()"
)
"""Generate the start area now, while nobody is connected (D59).

FLE forces this same generation when a run starts. Measured on a fresh map it stops the
server for 10.4 s; a watching client that is already lagging gives up, reconnects, and
the reconnect's map save fails once FLE has loaded its tools (D43). Done in advance, the
run's own call finds the chunks generated and returns in 0.01 s."""

WATCHER_GROUP = "watchers"

DEFAULT_TRAJECTORY_DIR = Path(__file__).resolve().parent.parent / "trajectories"
"""The repository's ``trajectories/``, where ``run.py`` writes by default. Resolved
from this file, not the working directory: the watcher is started from ``~/fle-work``
(fle-integration.md), where a relative ``trajectories`` finds nothing and the HUD
silently never appears."""

BEAT_TICKS = 60
"""The camera counts as running if its handler ran within this many ticks. A paused
game does not advance ``game.tick``, so a pause does not look like a dead camera."""


def _one_line(lua: str) -> str:
    """Lua is whitespace-insensitive outside strings; one line keeps RCON simple. Never
    put a ``--`` comment in Lua passed through this."""
    return " ".join(lua.split())


CHECK_LUA = _one_line(
    f"""
local g = game.permissions.get_group("{WATCHER_GROUP}")
if not g then
  g = game.permissions.create_group("{WATCHER_GROUP}")
  for _, a in pairs(defines.input_action) do g.set_allows_action(a, false) end
end
local players = 0
for _, p in pairs(game.connected_players) do
  if not (p.permission_group and p.permission_group.group_id == g.group_id) then
    p.permission_group = g
  end
  if p.controller_type ~= defines.controllers.god then
    p.set_controller{{type = defines.controllers.god}}
  end
  players = players + 1
end
if players == 0 then rcon.print("no client") return end
local camera = (harness_watch_beat and game.tick - harness_watch_beat <= {BEAT_TICKS})
  and "1" or "0"
local c = storage.agent_characters and storage.agent_characters[1]
if not (c and c.valid) then rcon.print("no agent " .. camera) return end
rcon.print("following " .. camera)
"""
)
"""One check: lock watchers out, put them in god mode, and report the state.

- **Locked out first**, whether or not a run exists: the ``watchers`` permission group
  allows no input action, so a watcher can look but cannot build, mine, walk or open a
  machine. The human provides text only, and this makes the game enforce it.
- **God mode before any run**, because it has no character for FLE's reset to destroy -
  losing one dropped a client in D38 - so it is set even while there is no agent yet.
  A god-mode player renders the real world wherever it stands; remote view does not,
  because FLE's agent, attached to no player, gives the force no vision (D59).

Prints ``no client``, or ``no agent`` / ``following`` followed by ``1`` if the camera
handler is running and ``0`` if it needs installing. It creates no Lua global, so it is
safe to run before a client joins."""

STATUS_MESSAGES = {
    "following": "following the agent",
    "no agent": "waiting for a run to create the agent's character",
    "no client": "waiting for a Factorio client to connect",
}


@dataclass(frozen=True)
class CameraSettings:
    zoom: float = 0.75
    """Watchers' zoom; below 1 shows more of the factory around the agent."""
    glide: float = 0.15
    """Fraction of the remaining distance the view moves each tick: ~0.5 s to settle."""
    snap: float = 60.0
    """Beyond this many tiles the view jumps instead of gliding."""
    cues: bool = True
    """Draw what the agent does: inventory changes, placed entities, teleports."""
    cue_radius: float = 40.0
    """Tiles around the agent scanned for entities it placed or picked up."""


CAMERA_LUA = Template(
    """
harness_watch = {glide = $glide, snap = $snap, cues = $cues, radius = $radius}
harness_watch_beat = game.tick
for _, p in pairs(game.connected_players) do
  pcall(function() p.zoom = $zoom end)
  pcall(function()
    local v = p.game_view_settings
    v.show_entity_info = true
    p.game_view_settings = v
  end)
end
function harness_watch_follow(c)
  local t = c.position
  local a, s = harness_watch.glide, harness_watch.snap
  for _, p in pairs(game.connected_players) do
    local q = p.position
    local dx, dy = t.x - q.x, t.y - q.y
    local d = dx * dx + dy * dy
    if p.surface ~= c.surface or d > s * s then
      p.teleport(t, c.surface)
    elseif d > 0.0025 then
      p.teleport({x = q.x + dx * a, y = q.y + dy * a})
    end
  end
end
function harness_watch_say(players, text, pos)
  for _, p in pairs(players) do
    p.create_local_flying_text{text = text, position = pos, time_to_live = 90}
  end
end
function harness_watch_cues(c, tick)
  local w = harness_watch
  local players = game.connected_players
  local pos = c.position
  if w.unit ~= c.unit_number then
    w.unit = c.unit_number
    w.pos = nil
    w.inv = nil
    w.known = nil
  end
  if w.pos then
    local dx, dy = pos.x - w.pos.x, pos.y - w.pos.y
    if dx * dx + dy * dy > 9 then
      rendering.draw_line{color = {r = 0.3, g = 0.8, b = 1, a = 0.7}, width = 2,
        dash_length = 0.6, gap_length = 0.4, from = {x = w.pos.x, y = w.pos.y},
        to = {x = pos.x, y = pos.y}, surface = c.surface, players = players,
        time_to_live = 90}
    end
  end
  w.pos = {x = pos.x, y = pos.y}
  local now = {}
  local inv = c.get_main_inventory()
  if inv then
    for k, v in pairs(inv.get_contents()) do
      if type(k) == "string" then
        now[k] = (now[k] or 0) + v
      else
        now[v.name] = (now[v.name] or 0) + v.count
      end
    end
  end
  if w.inv then
    local shown = 0
    local function show(name, delta)
      if delta ~= 0 and shown < 4 then
        local sign = delta > 0 and "+" or ""
        harness_watch_say(players, sign .. delta .. " [item=" .. name .. "]",
          {x = pos.x, y = pos.y - 1.5 - shown * 0.7})
        shown = shown + 1
      end
    end
    for name, n in pairs(now) do show(name, n - (w.inv[name] or 0)) end
    for name, n in pairs(w.inv) do if not now[name] then show(name, -n) end end
  end
  w.inv = now
  if tick % 30 ~= 0 then return end
  local r = w.radius
  local found = c.surface.find_entities_filtered{
    area = {{pos.x - r, pos.y - r}, {pos.x + r, pos.y + r}}, force = c.force}
  local seen = {}
  for _, e in pairs(found) do
    if e.unit_number and e.type ~= "character" then
      seen[e.unit_number] = true
      if w.known and not w.known[e.unit_number] then
        local b = e.bounding_box
        rendering.draw_rectangle{color = {r = 1, g = 0.8, b = 0.2, a = 0.9}, width = 3,
          filled = false, left_top = {x = b.left_top.x - 0.2, y = b.left_top.y - 0.2},
          right_bottom = {x = b.right_bottom.x + 0.2, y = b.right_bottom.y + 0.2},
          surface = c.surface, players = players, time_to_live = 90}
        rendering.draw_text{text = "[entity=" .. e.name .. "] placed",
          target = {x = e.position.x, y = b.left_top.y - 0.8}, surface = c.surface,
          color = {r = 1, g = 0.9, b = 0.4}, scale = 1.2, alignment = "center",
          use_rich_text = true, players = players, time_to_live = 90}
      end
    end
  end
  if w.known then
    for un, e in pairs(w.known) do
      if not seen[un] and math.abs(e.x - pos.x) <= r and math.abs(e.y - pos.y) <= r then
        harness_watch_say(players, "[entity=" .. e.name .. "] picked up",
          {x = e.x, y = e.y})
        w.known[un] = nil
      end
    end
  end
  w.known = w.known or {}
  for _, e in pairs(found) do
    if seen[e.unit_number or -1] then
      w.known[e.unit_number] = {name = e.name, x = e.position.x, y = e.position.y}
    end
  end
end
function harness_watch_tick(event)
  harness_watch_beat = event.tick
  local c = storage.agent_characters and storage.agent_characters[1]
  if not (c and c.valid) then return end
  harness_watch_follow(c)
  if harness_watch.cues and event.tick % 6 == 0 then harness_watch_cues(c, event.tick) end
end
script.on_nth_tick(1, function(event)
  local ok, err = pcall(harness_watch_tick, event)
  if not ok then harness_watch_error = tostring(err) end
end)
rcon.print("installed")
"""
)
"""The per-tick camera and cues, installed once a client has joined (D63).

- ``on_nth_tick(1)``, never ``on_tick``: FLE registers ``on_tick`` itself, and Factorio
  keeps one handler per event, so taking it would silently switch FLE's off. FLE's
  ``on_nth_tick`` periods are 5, 15 and 60.
- The body runs under ``pcall``. A script error inside an event is fatal to a headless
  server (D48); a failing camera must cost the camera, not the run.
- State lives in plain Lua globals, never ``storage``, which would break the save (D43).
- Every drawing passes ``players``: watchers see it, the world does not change."""


PANEL_ACTIONS = ("gui_click", "gui_text_changed", "gui_confirmed", "gui_location_changed")
"""The only input actions watchers are given back (D72): clicking, typing into and
moving the harness panel. Building, mining, walking and opening machines stay blocked,
so the human still provides text and nothing else."""

PANEL_LUA = _one_line(
    f"""
harness_panel = harness_panel or {{}}
harness_panel.min = harness_panel.min or {{}}
local g = game.permissions.get_group("{WATCHER_GROUP}")
if g then
  for _, name in pairs({{{", ".join(f'"{a}"' for a in PANEL_ACTIONS)}}}) do
    local a = defines.input_action[name]
    if a then g.set_allows_action(a, true) end
  end
end
function harness_panel_click(e)
  local el = e.element
  if not (el and el.valid) then return end
  local p = game.get_player(e.player_index)
  if el.name == "harness_hud_toggle" then
    local m = not harness_panel.min[p.index]
    harness_panel.min[p.index] = m
    local f = p.gui.screen.harness_hud
    if f and f.harness_body then f.harness_body.visible = not m end
    el.caption = m and "Show" or "Hide"
  elseif el.name == "harness_help_send" or el.name == "harness_help_decline" then
    local id = el.tags and el.tags.request
    if not id or harness_panel.sent == id then return end
    local h = el.parent and el.parent.parent
    local box = h and h.harness_help_text
    local text = nil
    if el.name == "harness_help_send" and box then text = box.text end
    harness_panel.answer = {{id = id, text = text}}
    harness_panel.sent = id
    if box then box.read_only = true end
    el.parent.visible = false
  end
end
script.on_event(defines.events.on_gui_click, function(e)
  local ok, err = pcall(harness_panel_click, e)
  if not ok then harness_watch_error = tostring(err) end
end)
rcon.print("panel")
"""
)
"""The panel's buttons, installed with the camera once a client has joined (D72).

Hide/Show collapses the panel to its title bar; Send and Decline answer the open help
request. An answer is held in ``harness_panel.answer`` until the watcher takes it with
``TAKE_ANSWER_LUA``. FLE registers no GUI event, so ``on_gui_click`` is free; the body
runs under ``pcall`` like the camera's, and state lives in plain Lua globals (D43)."""

TAKE_ANSWER_LUA = _one_line(
    """
local a = harness_panel and harness_panel.answer
if a then harness_panel.answer = nil rcon.print(helpers.table_to_json(a))
else rcon.print("") end
"""
)


def camera_lua(settings: CameraSettings) -> str:
    return _one_line(
        CAMERA_LUA.substitute(
            zoom=settings.zoom,
            glide=settings.glide,
            snap=settings.snap,
            cues="true" if settings.cues else "false",
            radius=settings.cue_radius,
        )
    )


Send = Callable[[str], object]


def pregenerate(send: Send) -> str:
    """Generate the start area unless a client is already connected (D59).

    A connected client would sit through the 10-second stall this avoids, so with
    anyone connected it is skipped and the run's own generation happens as before.
    """
    players = str(send("/sc rcon.print(#game.connected_players)") or "").strip()
    if players not in ("", "0"):
        return "skipped"
    send(PREGENERATE_LUA)
    return "generated"


def check_once(send: Send) -> tuple[str, bool]:
    """Check the watchers once: the status, and whether the camera is running."""
    reply = str(send(f"/sc {CHECK_LUA}") or "").strip()
    status, _, camera = reply.rpartition(" ")
    if status in STATUS_MESSAGES and camera in ("0", "1"):
        return status, camera == "1"
    return reply, False


# --- HUD -----------------------------------------------------------------------------

COLOURS = {
    "plain": (0.9, 0.9, 0.9),
    "heading": (1.0, 0.75, 0.4),
    "milestone": (0.55, 1.0, 0.55),
    "intent": (0.55, 0.85, 1.0),
    "error": (1.0, 0.45, 0.4),
    "verifier": (0.75, 0.75, 0.75),
    "human": (1.0, 0.85, 0.35),
}

HUD_WIDTH = 240
"""Characters per free-text HUD field (intent, error, verdict) before it is cut. The
labels wrap, so this bounds the panel's height, not its width. The goal is never cut."""

HUMAN_CHARS = 320
"""The human's guidance is shown verbatim up to this length, then cut."""

HUD_ITEMS = 16
"""Entries per inventory, production or factory line, largest first."""

FLUIDS = frozenset(
    {
        "water",
        "steam",
        "crude-oil",
        "heavy-oil",
        "light-oil",
        "petroleum-gas",
        "lubricant",
        "sulfuric-acid",
    }
)

PSEUDO_ITEMS = frozenset({"science"})
"""Names FLE reports in flows that are not game items, so have no icon."""

_PROBLEMS = {
    "NO_FUEL": "no fuel",
    "NO_INGREDIENTS": "no ingredients",
    "NO_POWER": "no power",
    "LOW_POWER": "low power",
    "FULL_OUTPUT": "output full",
    "NO_MINABLE_RESOURCES": "no ore",
    "NOT_PLUGGED_IN_ELECTRIC_NETWORK": "unpowered",
    "NO_RECIPE": "no recipe",
    "WAITING_FOR_SOURCE_ITEMS": "waiting",
    "NOT_CONNECTED": "not connected",
}
"""FLE entity statuses worth flagging on the factory line, in plain words."""


@dataclass
class FactoryLine:
    name: str
    total: int = 0
    working: int = 0
    problems: dict[str, int] = field(default_factory=dict)


@dataclass
class HudState:
    """What the HUD shows, built from the trajectory records seen so far."""

    goal: str = ""
    max_steps: int | None = None
    step: int | None = None
    intent: str = ""
    error: str = ""
    model: str = ""
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    help_requests: int = 0
    human: tuple[int, str | None] | None = None
    waiting: tuple[int, str] | None = None
    """(step, reason) of a help request not yet answered (D72)."""
    verdict: tuple[bool, str] | None = None
    game_seconds: float | None = None
    inventory: dict[str, float] = field(default_factory=dict)
    made: dict[str, float] = field(default_factory=dict)
    used: dict[str, float] = field(default_factory=dict)
    factory: list[FactoryLine] = field(default_factory=list)
    researched: list[str] = field(default_factory=list)
    researching: str = ""
    scorer: ScoreTracker = field(default_factory=ScoreTracker)

    def feed(self, records: Iterable[dict[str, Any]]) -> None:
        for record in records:
            kind = record.get("type")
            if kind == "run":
                self.goal = str(record.get("goal") or self.goal)
                self.max_steps = record.get("max_steps") or self.max_steps
            elif kind == "step":
                self.step = record.get("step")
                self.goal = str(record.get("goal") or self.goal)
                self.intent = intent_of(str(record.get("policy") or ""))
                errors = record.get("execution_errors") or []
                self.error = last_error_line(str(errors[-1])) if errors else ""
                self.scorer.feed(record)
                obs = record.get("observation")
                if isinstance(obs, dict):
                    self._observe(obs)
            elif kind == "llm_call":
                self.llm_calls.append(record)
                if record.get("role") == "policy":
                    self.model = str(record.get("model") or self.model)
            elif kind == "help_request":
                self.waiting = (
                    int(record.get("step") or 0),
                    str(record.get("stuck_reason") or ""),
                )
            elif kind == "intervention":
                self.help_requests += 1
                self.human = (int(record.get("step") or 0), record.get("text"))
                self.waiting = None
            elif kind == "verification":
                self.verdict = (bool(record.get("done")), str(record.get("reason") or ""))

    def _observe(self, obs: dict[str, Any]) -> None:
        """The control-panel fields, from one step's recorded observation. Every
        shape is read defensively: a field FLE did not send leaves its line empty."""
        self.inventory = _counts(obs.get("inventory"), "quantity")
        flows = obs.get("flows") if isinstance(obs.get("flows"), dict) else {}
        self.made = _counts(flows.get("output"), "rate")
        self.used = _counts(flows.get("input"), "rate")
        self.factory = _factory(obs.get("entities"))
        research = obs.get("research") if isinstance(obs.get("research"), dict) else {}
        self.researched = [
            str(t.get("name"))
            for t in research.get("technologies") or []
            if isinstance(t, dict) and t.get("researched")
        ]
        current = str(research.get("current_research") or "")
        self.researching = "" if current in ("", "None") else current
        game = obs.get("game_info") if isinstance(obs.get("game_info"), dict) else {}
        seconds = game.get("time")
        self.game_seconds = float(seconds) if isinstance(seconds, int | float) else None


def _counts(entries: Any, amount_key: str) -> dict[str, float]:
    """{name: amount} from FLE's [{"type": name, amount_key: n}, ...] lists."""
    counts: dict[str, float] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not entry.get("type"):
            continue
        try:
            amount = float(entry.get(amount_key) or 0)
        except (TypeError, ValueError):
            continue
        name = str(entry["type"])
        counts[name] = counts.get(name, 0.0) + amount
    return counts


def _factory(entities: Any) -> list[FactoryLine]:
    """Entities grouped by name: how many, how many WORKING, and what ails the rest."""
    lines: dict[str, FactoryLine] = {}
    for entity in entities if isinstance(entities, list) else []:
        if not isinstance(entity, dict) or not entity.get("name"):
            continue
        name = str(entity["name"])
        line = lines.setdefault(name, FactoryLine(name))
        line.total += 1
        status = str(entity.get("status") or "").removeprefix("EntityStatus.")
        if status == "WORKING":
            line.working += 1
        elif status in _PROBLEMS:
            word = _PROBLEMS[status]
            line.problems[word] = line.problems.get(word, 0) + 1
    return sorted(lines.values(), key=lambda line: (-line.total, line.name))


def icon(name: str) -> str:
    """Factorio rich text for an item or fluid. FLE's pseudo-names - grouped
    entities (``pipe-group``) and research progress in flows (``science``) - have no
    icon and are written out."""
    if name.endswith("-group") or name in PSEUDO_ITEMS:
        return name
    return f"[fluid={name}]" if name in FLUIDS else f"[item={name}]"


def _amount(value: float) -> str:
    if value >= 10 or float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:.1f}"


def _clock(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def _top(counts: dict[str, float], sign: str = "") -> str:
    ranked = [
        (n, a) for n, a in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if a
    ]
    shown = [f"{icon(name)} {sign}{_amount(n)}" for name, n in ranked[:HUD_ITEMS]]
    more = len(ranked) - len(shown)
    return "   ".join(shown) + (f"   +{more} more" if more > 0 else "")


def _cut(text: str, limit: int = HUD_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def intent_of(policy: str) -> str:
    """What the model says it is doing: its code's first comment, else its first line."""
    lines = [line.strip() for line in policy.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return lines[0] if lines else ""


_LINE_NUMBER = re.compile(r"^\d+:\s*")


def last_error_line(error: str) -> str:
    """The informative tail of an FLE error: the last line, without FLE's line-number
    prefix or the tuple-repr wrapping (D35)."""
    lines = [line.strip() for line in error.replace("\\n", "\n").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    line = _LINE_NUMBER.sub("", lines[-1])
    return line.strip(" (),'\"").removeprefix("Exception:").strip()


def hud_lines(state: HudState) -> list[tuple[str, str]]:
    """(text, colour) per HUD line. Steps are shown 1-based, as the run summary counts.

    The top half is the run - goal, milestone, what the agent is doing and what went
    wrong; the bottom half is the factory, from the latest recorded observation:
    inventory, this step's production, entities and research.
    """
    from factorio_maxxing.pricing import summarize

    cost = sum(usage.cost for usage in summarize(state.llm_calls))
    step = "-" if state.step is None else str(state.step + 1)
    if state.max_steps:
        step += f"/{state.max_steps}"
    header = f"Step {step} · {state.model or '?'} · ~${cost:.2f}"
    if state.game_seconds is not None:
        header += f" · game {_clock(state.game_seconds)}"
    if state.help_requests:
        header += f" · help asked {state.help_requests}×"
    lines = [(header, "plain")]
    if state.goal:
        lines.append(("Goal: " + " ".join(state.goal.split()), "plain"))

    ladder = state.scorer.ladder
    score = state.scorer.score()
    if score.highest:
        rung = ladder[score.highest - 1]
        lines.append(
            (
                f"Milestone {score.highest}/{len(ladder)}: {rung.description} "
                f"(step {score.reached[rung.key] + 1})"
                + (
                    f" · contiguous {score.contiguous}"
                    if score.contiguous != score.highest
                    else ""
                ),
                "milestone",
            )
        )
    elif state.step is not None:
        lines.append((f"Milestone 0/{len(ladder)}", "milestone"))

    if state.intent:
        lines.append(("▶ " + _cut(state.intent), "intent"))
    if state.error:
        lines.append(("✗ " + _cut(state.error), "error"))
    if state.verdict is not None:
        done, reason = state.verdict
        lines.append(
            (
                ("✓ Verifier: done - " if done else "? Verifier: not done - ")
                + _cut(reason),
                "verifier",
            )
        )
    if state.human is not None:
        at, text = state.human
        said = _cut(text, HUMAN_CHARS) if text else "(no guidance)"
        lines.append((f"Human (step {at + 1}): {said}", "human"))

    if state.step is None:
        return lines
    research = (
        f"{score.research_count} researched this run ({score.research_units:,} units)"
    )
    if state.researched:
        research += " · done: " + ", ".join(state.researched)
    research += " · researching: " + (state.researching or "nothing")
    lines += [
        ("Inventory", "heading"),
        (_top(state.inventory) or "(empty)", "plain"),
        ("Production this step", "heading"),
        ("Made: " + (_top(state.made, "+") or "nothing"), "plain"),
        ("Used: " + (_top(state.used, "-") or "nothing"), "plain"),
        ("Factory", "heading"),
        (_factory_text(state.factory) or "(nothing built)", "plain"),
        ("Research", "heading"),
        (research, "plain"),
    ]
    return lines


def _factory_text(factory: Sequence[FactoryLine]) -> str:
    parts = []
    for line in factory[:HUD_ITEMS]:
        text = f"{icon(line.name)} {line.working}/{line.total} working"
        if line.problems:
            ills = ", ".join(f"{n} {word}" for word, n in line.problems.items())
            text += f" ({ills})"
        parts.append(text)
    more = len(factory) - len(parts)
    return "   ".join(parts) + (f"   +{more} more" if more > 0 else "")


def _lua_string(text: str) -> str:
    """A Lua long-bracket string that cannot be closed early by the text itself."""
    level = 0
    while f"]{'=' * level}]" in text:
        level += 1
    eq = "=" * level
    return f"[{eq}[{text}]{eq}]"


def hud_title(state: HudState) -> str:
    """The title bar, which stays visible when the panel is hidden: step and milestone."""
    step = "-" if state.step is None else str(state.step + 1)
    if state.max_steps:
        step += f"/{state.max_steps}"
    score = state.scorer.score()
    return f"Agent · step {step} · milestone {score.highest}/{len(state.scorer.ladder)}"


def hud_help(state: HudState, request: Request | None) -> str | None:
    """The help line: shown, even on a hidden panel, while the run waits for a human."""
    if request is not None:
        at = f" (step {state.waiting[0] + 1})" if state.waiting else ""
        return (
            f"⚠ The harness is asking for help{at}: {request.reason}. "
            "Type your guidance below and press Send, or Decline."
        )
    if state.waiting is not None:
        step, reason = state.waiting
        return (
            f"⚠ The harness is asking for help (step {step + 1}): {reason}. "
            "Answer in the run's terminal."
        )
    return None


def hud_lua(
    lines: list[tuple[str, str]],
    title: str = "Factorio agent",
    help_text: str | None = None,
    request_id: str | None = None,
) -> str:
    """Rebuild the watchers' panel (D63, D71, D72).

    A draggable title bar with a Hide/Show button; the body of ``lines``, each collapsed
    to one wrapped label; and, while help is requested, a help section that stays visible
    when the body is hidden - with a text box, Send and Decline when ``request_id`` names
    a panel request. The rebuild keeps where the watcher dragged the panel, whether they
    hid it, and anything half-typed for the same request.
    """
    entries = []
    for text, colour in lines:
        r, g, b = COLOURS.get(colour, COLOURS["plain"])
        entries.append(
            f"{{{_lua_string(' '.join(text.split()))}, {{r={r}, g={g}, b={b}}}}}"
        )
    hr, hg, hb = COLOURS["human"]
    help_lua = _lua_string(" ".join(help_text.split())) if help_text else "nil"
    request_lua = _lua_string(request_id) if request_id else "nil"
    return _one_line(
        f"""
local L = {{{", ".join(entries)}}}
local TITLE = {_lua_string(title)}
local HELP = {help_lua}
local REQ = {request_lua}
for _, p in pairs(game.connected_players) do
  local old = p.gui.screen.harness_hud
  local loc, typed = nil, nil
  if old then
    loc = old.location
    local oh = old.harness_help
    local ob = oh and oh.harness_help_text
    if ob and REQ and ob.tags.request == REQ then typed = ob.text end
    old.destroy()
  end
  local min = harness_panel and harness_panel.min and harness_panel.min[p.index]
  local sent = harness_panel and REQ and harness_panel.sent == REQ
  local f = p.gui.screen.add{{type = "frame", name = "harness_hud",
    direction = "vertical"}}
  f.location = loc or {{x = 12, y = 80}}
  local bar = f.add{{type = "flow", name = "harness_bar", direction = "horizontal"}}
  bar.drag_target = f
  local t = bar.add{{type = "label", caption = TITLE, style = "frame_title"}}
  t.ignored_by_interaction = true
  local d = bar.add{{type = "empty-widget", style = "draggable_space_header"}}
  d.style.horizontally_stretchable = true
  d.style.height = 24
  d.style.minimal_width = 24
  d.drag_target = f
  bar.add{{type = "button", name = "harness_hud_toggle",
    caption = min and "Show" or "Hide"}}
  if HELP then
    local h = f.add{{type = "flow", name = "harness_help", direction = "vertical"}}
    local w = h.add{{type = "label", caption = HELP}}
    w.style.single_line = false
    w.style.maximal_width = 680
    w.style.font_color = {{r={hr}, g={hg}, b={hb}}}
    if REQ then
      if sent then
        h.add{{type = "label", caption = "Sent - waiting for the harness to take it."}}
      else
        local box = h.add{{type = "text-box", name = "harness_help_text",
          text = typed or "", tags = {{request = REQ}}}}
        box.word_wrap = true
        box.style.width = 680
        box.style.height = 110
        local b = h.add{{type = "flow", direction = "horizontal"}}
        b.add{{type = "button", name = "harness_help_send", caption = "Send",
          tags = {{request = REQ}}}}
        b.add{{type = "button", name = "harness_help_decline", caption = "Decline",
          tags = {{request = REQ}}}}
      end
    end
  end
  local body = f.add{{type = "flow", name = "harness_body", direction = "vertical"}}
  body.visible = not min
  for _, l in ipairs(L) do
    local w = body.add{{type = "label", caption = l[1]}}
    w.style.single_line = false
    w.style.maximal_width = 680
    w.style.font_color = l[2]
  end
end
rcon.print("hud")
"""
    )


class TrajectoryTail:
    """Follow the newest trajectory written since the watcher started.

    Reads only what was appended since the last poll - observations make the file
    large - and keeps a partial last line until it is complete. Switches to a newer
    file when one appears, which is the next run.
    """

    def __init__(self, directory: Path | str, since: float | None = None):
        self.directory = Path(directory)
        self.since = time.time() if since is None else since
        self.path: Path | None = None
        self.offset = 0
        self.partial = b""

    def _newest(self) -> Path | None:
        try:
            candidates = [
                p
                for p in self.directory.glob("*.jsonl")
                if p.stat().st_mtime >= self.since
            ]
        except OSError:
            return None
        return max(candidates, key=lambda p: p.name, default=None)

    def poll(self) -> tuple[bool, list[dict[str, Any]]]:
        """(switched to a new file, records appended since the last poll)."""
        newest = self._newest()
        switched = newest is not None and newest != self.path
        if switched:
            self.path, self.offset, self.partial = newest, 0, b""
        if self.path is None:
            return False, []
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                data = handle.read()
        except OSError:
            return switched, []
        self.offset += len(data)
        *complete, self.partial = (self.partial + data).split(b"\n")
        records = []
        for line in complete:
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return switched, records


# --- The loop ------------------------------------------------------------------------


def watch(
    send: Send,
    interval: float = DEFAULT_INTERVAL,
    *,
    camera: CameraSettings | None = None,
    tail: TrajectoryTail | None = None,
    sleep: Callable[[float], None] = time.sleep,
    ticks: int | None = None,
) -> None:
    """Check watchers, keep the camera installed and the HUD current, until interrupted
    or for ``ticks`` checks.

    Logs only when something changes, so a long run leaves a short log. A failed check
    is logged and retried on the next tick rather than raised: watching is a
    convenience, and losing it must never look like the run failing.
    """
    camera = camera or CameraSettings()
    hud = HudState()
    hud_pending = False
    channel = HelpChannel(tail.directory / "help") if tail is not None else None
    request: Request | None = None
    last = None
    count = 0
    while ticks is None or count < ticks:
        try:
            status, camera_running = check_once(send)
            connected = status in ("following", "no agent")
            if connected and not camera_running:
                send(f"/sc {camera_lua(camera)}")
                send(f"/sc {PANEL_LUA}")
                logging.info("camera and panel installed")
                hud_pending = True
            if channel is not None and connected:
                request, changed = relay_help(send, channel, request)
                hud_pending = hud_pending or changed
            if tail is not None:
                switched, records = tail.poll()
                if switched:
                    hud = HudState()
                    logging.info("HUD following %s", tail.path)
                if records:
                    hud.feed(records)
                    hud_pending = True
                if connected and hud_pending and hud.step is not None:
                    lua = hud_lua(
                        hud_lines(hud),
                        hud_title(hud),
                        hud_help(hud, request),
                        request.id if request else None,
                    )
                    send(f"/sc {lua}")
                    hud_pending = False
        except Exception as error:  # noqa: BLE001 - watching must not stop on a blip
            status = f"error: {error}"
        if status != last:
            logging.info(STATUS_MESSAGES.get(status, status))
            last = status
        count += 1
        if ticks is None or count < ticks:
            sleep(interval)


def relay_help(
    send: Send, channel: HelpChannel, shown: Request | None
) -> tuple[Request | None, bool]:
    """Carry a panel help request into the game and its answer back (D72).

    Returns the open request and whether the panel needs redrawing. The answer is
    relayed exactly as typed; the harness decides what a blank one means.
    """
    request = channel.pending()
    changed = (request.id if request else None) != (shown.id if shown else None)
    if changed and request is not None:
        logging.info("help requested: %s - answer in the panel", request.reason)
    if request is None:
        return None, changed
    reply = str(send(f"/sc {TAKE_ANSWER_LUA}") or "").strip()
    if not reply:
        return request, changed
    try:
        answer = json.loads(reply)
    except ValueError:
        return request, changed
    if isinstance(answer, dict) and answer.get("id") == request.id:
        text = answer.get("text")
        text = text if isinstance(text, str) else None
        channel.answer(request.id, text)
        logging.info(
            "answer relayed (%s)", "declined" if text is None else f"{len(text)} chars"
        )
        return None, True
    return request, changed


def connect() -> Send:
    """Open an RCON connection to the local cluster, using FLE's own helpers.

    FLE is imported here rather than at module scope, for the same reason as
    ``RealFactorioEnv``: the offline suite runs on Windows, where FLE is not installed.
    """
    from fle.commons.cluster_ips import get_local_container_ips
    from fle.env import FactorioInstance

    found = get_local_container_ips()
    if not found or not found[0]:
        raise ConnectionError("no running Factorio container; start the cluster first")
    ips, _udp_ports, tcp_ports = found
    client, _address = FactorioInstance.connect_to_server(ips[0], tcp_ports[0])
    return client.send_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m factorio_maxxing.watch",
        description="Watch a live run: smooth camera, action cues and a HUD.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"seconds between checks (default {DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--no-pregenerate",
        action="store_true",
        help="do not generate the start area before a client joins",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=CameraSettings.zoom,
        help=f"watchers' zoom (default {CameraSettings.zoom}; below 1 shows more)",
    )
    parser.add_argument(
        "--no-cues",
        action="store_true",
        help="do not draw inventory changes, placed entities or teleport trails",
    )
    parser.add_argument(
        "--trajectory-dir",
        default=str(DEFAULT_TRAJECTORY_DIR),
        help="where the run writes its trajectory, for the HUD "
        "(default: this repository's trajectories/)",
    )
    parser.add_argument("--no-hud", action="store_true", help="do not show the HUD")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = build_parser().parse_args(argv)
    if args.interval <= 0:
        print("error: --interval must be positive", file=sys.stderr)
        return 2
    if args.zoom <= 0:
        print("error: --zoom must be positive", file=sys.stderr)
        return 2
    try:
        send = connect()
    except (ConnectionError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not args.no_pregenerate:
        logging.info("generating the map around spawn before anyone joins (~10 s)")
        try:
            outcome = pregenerate(send)
        except Exception as error:  # noqa: BLE001 - the camera must still start
            outcome = f"failed ({error})"
        if outcome == "skipped":
            logging.warning("a client is already connected; map generation skipped")
        else:
            logging.info("map generation %s", outcome)
    camera = CameraSettings(zoom=args.zoom, cues=not args.no_cues)
    tail = None if args.no_hud else TrajectoryTail(args.trajectory_dir)
    try:
        watch(send, args.interval, camera=camera, tail=tail)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

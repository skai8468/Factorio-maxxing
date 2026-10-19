"""Observation rendering.

See docs/contracts.md (rendering.py). Raw FLE observations are never sent to a model:
this module compacts one into labelled sections. It calls no LLM and knows nothing
about goals, so compact/full/compressed variants can be compared later without
touching the environment or LLM interfaces.

Observation keys follow the FLE schema recorded in docs/fle-integration.md.

Since D66 the entity section carries what FLE's own agent sees - every entity, with its
fuel, contents, recipe, drop position and warnings - and the observation adds the
character's position, game time and score, and the agent's saved functions. History
renders the same observation without entities (``include_entities=False``), FLE's
"stale entity data" trimming: only the current step pays for the entity list.
"""

import re
from collections import defaultdict
from enum import Enum
from typing import Any

EMPTY = "(none)"
MAX_ENTITIES = 150
"""Entities rendered in full detail. Beyond this, entities without a problem (status
WORKING or NORMAL, no warnings) collapse into one line per name and status, with a count
and a few positions; entities with a problem are always detailed first (D66)."""
GROUP_POSITIONS = 6
"""Positions listed on a collapsed line before the rest are counted."""
MAX_TECHNOLOGIES = 32
"""Researched technology names rendered before truncation."""
STALE_ENTITIES = "(omitted - the current observation lists every entity)"
"""What a history entry shows instead of its stale entity list (D66)."""


def render_observation(
    obs: dict[str, Any],
    *,
    max_entities: int = MAX_ENTITIES,
    include_entities: bool = True,
) -> str:
    """Render an observation as compact labelled sections.

    Every section header is always emitted, with ``(none)`` where the observation
    carries nothing, so the policy sees a stable structure across steps. With
    ``include_entities=False`` the entity section says it was omitted, for history
    entries whose entity data is stale.
    """
    entities = (
        _render_entities(obs.get("entities"), max_entities)
        if include_entities
        else [STALE_ENTITIES]
    )
    sections = (
        ("INVENTORY", _render_inventory(obs.get("inventory"))),
        ("CHARACTER", _render_character(obs.get("character_positions"))),
        ("ENTITIES", entities),
        ("RESEARCH", _render_research(obs.get("research"))),
        ("FLOWS", _render_flows(obs.get("flows"))),
        ("FUNCTIONS", _render_functions(obs.get("serialized_functions"))),
        ("GAME", _render_game(obs)),
        ("EXECUTION", _render_execution(obs)),
    )
    lines: list[str] = []
    for header, body in sections:
        lines.append(header)
        # Execution output spans lines; indenting each keeps it inside its section.
        lines.extend(
            f"  {part}"
            for line in (body or [EMPTY])
            for part in line.splitlines() or [""]
        )
    return "\n".join(lines)


def _format_number(value: Any) -> str:
    """Render 12.0 as '12' and 0.5 as '0.5'; pass anything else through."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


LABEL_KEYS = ("name", "type", "item")
"""Keys that may carry an item's name. FLE's observation uses ``type``, not ``name``."""

VALUE_KEYS = ("count", "amount", "quantity", "rate", "value")
"""Keys that may carry an item's quantity. Inventory uses ``quantity``, production
flows use ``rate``, and harvested flows use ``amount`` - all in the same observation."""


def _as_counts(value: Any) -> list[tuple[str, Any]]:
    """Normalise an item mapping, or a list of item records, to (label, count) pairs.

    Both shapes occur. The measured FLE observation carries lists of typed dicts -
    ``{"type": "coal", "quantity": 7}`` for inventory, ``{"type": "coal", "rate": 7}``
    for flows - while fixtures and older notes used ``{name: count}`` mappings. Reading
    several spellings keeps one renderer working against both (docs/fle-integration.md).
    """
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        pairs = []
        for item in value:
            if not isinstance(item, dict):
                continue
            label = next((item[key] for key in LABEL_KEYS if key in item), None)
            if label is None:
                continue
            count = next((item[key] for key in VALUE_KEYS if key in item), 0)
            pairs.append((str(label), count))
        return pairs
    return []


ABSENT_STRINGS = frozenset({"", "none", "null"})
"""Values that mean "nothing here". The gym observation space is typed, so FLE
serialises an absent ``current_research`` as the literal string ``"None"`` rather than
as ``None`` - which is truthy, and rendered as ``current: None`` until this was fixed."""


def _present(value: Any) -> Any:
    """Return the value, or None if it is absent or a string standing in for absent."""
    if isinstance(value, str) and value.strip().lower() in ABSENT_STRINGS:
        return None
    return value


def _join_counts(pairs: list[tuple[str, Any]]) -> str:
    kept = [(name, count) for name, count in pairs if count]
    if not kept:
        return EMPTY
    return ", ".join(f"{name} {_format_number(count)}" for name, count in kept)


def _render_counts(value: Any) -> str:
    return _join_counts(_as_counts(value))


def _crafted_outputs(value: Any) -> list[tuple[str, Any]]:
    """Flatten ``flows.crafted``, which records craft events rather than item counts.

    Each record is ``{"crafted_count": n, "inputs": {...}, "outputs": {...}}``. The
    outputs are what the policy needs to see: what it now has, not what it consumed.
    """
    if not isinstance(value, list):
        return []
    pairs: list[tuple[str, Any]] = []
    for item in value:
        outputs = item.get("outputs") if isinstance(item, dict) else None
        if isinstance(outputs, dict):
            pairs.extend((str(name), count) for name, count in outputs.items())
        elif isinstance(outputs, list):
            pairs.extend(_as_counts(outputs))
    return pairs


def _render_inventory(inventory: Any) -> list[str]:
    rendered = _render_counts(inventory)
    return [] if rendered == EMPTY else [rendered]


_ENUM_STRING = re.compile(r"^[A-Z][A-Za-z]+\.([A-Z][A-Z0-9_]*)$")


def _label(value: Any) -> str:
    """Render an enum by its bare name: ``EntityStatus.NO_FUEL`` becomes ``NO_FUEL``.

    A live observation carries real Python objects, so direction and status arrive as
    enum members whose ``str()`` includes the class name; a recorded one carries the
    same thing as a string, ``"EntityStatus.WORKING"``.
    """
    if isinstance(value, Enum):
        return str(value.name)
    text = str(value)
    match = _ENUM_STRING.match(text)
    return match.group(1) if match else text


_COORDINATES = re.compile(r"\bx\s*=\s*(-?[\d.]+)[,\s]+y\s*=\s*(-?[\d.]+)")


def _render_position(position: Any) -> str:
    if isinstance(position, dict) and "x" in position and "y" in position:
        return f"({_format_number(position['x'])}, {_format_number(position['y'])})"
    if isinstance(position, list | tuple) and len(position) == 2:
        return f"({_format_number(position[0])}, {_format_number(position[1])})"
    if isinstance(position, str):
        # A recorded observation carries FLE's string form, "x=3.0 y=-73.0".
        match = _COORDINATES.search(position)
        if match:
            x, y = (float(v) for v in match.groups())
            return f"({_format_number(x)}, {_format_number(y)})"
        return position
    # A live observation carries Position objects rather than dicts.
    x, y = getattr(position, "x", None), getattr(position, "y", None)
    if x is not None and y is not None:
        return f"({_format_number(x)}, {_format_number(y)})"
    return str(position)


ENTITY_DETAILS: tuple[tuple[str, str], ...] = (
    ("fuel", "fuel"),
    ("inventory", "contents"),
    ("furnace_source", "in"),
    ("furnace_result", "out"),
    ("assembling_machine_input", "in"),
    ("assembling_machine_output", "out"),
    ("lab_input", "in"),
    ("recipe", "recipe"),
    ("research", "researching"),
    ("resources", "mining"),
    ("pickup_position", "picks up at"),
    ("drop_position", "drops at"),
    ("warnings", "warnings"),
)
"""Entity fields shown after name, position, direction and status, in this order - the
detail FLE's own agent sees (D66). Empty values are skipped."""

_RESOURCE = re.compile(r"name='([^']+)'(?:\s+count=(\d+))?")
OK_STATUSES = frozenset({"WORKING", "NORMAL"})


def _detail_value(key: str, value: Any) -> str:
    if key.endswith("_position"):
        return _render_position(value)
    if key == "warnings":
        return "; ".join(str(w) for w in value) if isinstance(value, list) else str(value)
    if key == "resources" and isinstance(value, list):
        parts = []
        for item in value:
            match = _RESOURCE.search(str(item))
            if match and match.group(2):
                parts.append(f"{match.group(1)} ({match.group(2)} left)")
            else:
                parts.append(match.group(1) if match else str(item))
        return ", ".join(parts)
    if isinstance(value, dict):
        return _render_counts(value)
    return _label(value)


def _entity_line(entity: dict[str, Any]) -> str:
    line = str(entity.get("name", "unknown"))
    if "position" in entity:
        line += f" at {_render_position(entity['position'])}"
    if entity.get("direction") is not None:
        line += f" facing {_label(entity['direction'])}"
    if entity.get("status") is not None:
        line += f" [{_label(entity['status'])}]"
    details = []
    for key, label in ENTITY_DETAILS:
        value = entity.get(key)
        if value in (None, "", [], {}):
            continue
        rendered = _detail_value(key, value)
        if rendered and rendered != EMPTY:
            details.append(f"{label} {rendered}")
    return " | ".join([line, *details])


def _status(entity: dict[str, Any]) -> str:
    return _label(entity["status"]) if entity.get("status") is not None else ""


def _has_problem(entity: dict[str, Any]) -> bool:
    status = _status(entity)
    return bool(entity.get("warnings")) or (bool(status) and status not in OK_STATUSES)


def _render_entities(entities: Any, max_entities: int) -> list[str]:
    """Every entity, in full detail up to ``max_entities``. Beyond that, entities
    without a problem collapse by name and status, and problems are listed first, so a
    large base stays readable without hiding what is broken (D66)."""
    if not isinstance(entities, list) or not entities:
        return []
    records = [e for e in entities if isinstance(e, dict)]
    others = [str(e) for e in entities if not isinstance(e, dict)]
    if len(records) <= max_entities:
        return [_entity_line(e) for e in records] + others

    problems = [e for e in records if _has_problem(e)]
    healthy = [e for e in records if not _has_problem(e)]
    detailed, collapsed = problems[:max_entities], problems[max_entities:] + healthy
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for entity in collapsed:
        groups[(str(entity.get("name", "unknown")), _status(entity))].append(entity)

    lines = [_entity_line(e) for e in detailed]
    for (name, status), members in groups.items():
        where = [
            _render_position(m["position"])
            for m in members[:GROUP_POSITIONS]
            if "position" in m
        ]
        if len(members) > GROUP_POSITIONS:
            where.append(f"+{len(members) - GROUP_POSITIONS} more")
        line = f"{name} x{len(members)}" + (f" [{status}]" if status else "")
        lines.append(f"{line} at {', '.join(where)}" if where else line)
    return lines + others


def _render_character(positions: Any) -> list[str]:
    """Where the agent stands; placement reach is measured from here."""
    if isinstance(positions, list):
        for entry in positions:
            if isinstance(entry, dict) and "x" in entry and "y" in entry:
                return [f"position {_render_position(entry)}"]
    return []


def _render_functions(functions: Any) -> list[str]:
    """Functions the agent has defined in earlier steps and can call again."""
    if not isinstance(functions, list) or not functions:
        return []
    names = [
        str(item.get("name") or item) if isinstance(item, dict) else str(item)
        for item in functions
    ]
    return [", ".join(names)]


def _render_game(obs: dict[str, Any]) -> list[str]:
    info = obs.get("game_info")
    info = info if isinstance(info, dict) else {}
    parts = []
    tick, seconds = info.get("tick"), info.get("time")
    if isinstance(tick, int | float) and not isinstance(tick, bool):
        part = f"tick {_format_number(tick)}"
        if isinstance(seconds, int | float) and not isinstance(seconds, bool):
            part += f" ({_format_number(round(seconds))} s game time)"
        parts.append(part)
    for key, label in (("score", "production score"), ("automated_score", "automated")):
        value = obs.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            parts.append(f"{label} {_format_number(value)}")
    return [", ".join(parts)] if parts else []


def _technology_states(technologies: Any) -> list[tuple[str, Any]]:
    """(name, state) pairs. FLE's dataclass types this as a dict keyed by name; its
    gym observation delivers a list of records that carry the name themselves."""
    if isinstance(technologies, dict):
        return [(str(name), state) for name, state in technologies.items()]
    if isinstance(technologies, list):
        return [
            (str(state.get("name", "?")) if isinstance(state, dict) else "?", state)
            for state in technologies
        ]
    return []


def _researched_names(states: list[tuple[str, Any]]) -> str:
    """The researched technologies by name, truncated like the entity list.

    A count alone cannot tell a verifier whether *the* technology a goal names is
    researched - measured live, a verifier declared "Research automation" done with
    automation unresearched (D49)."""
    names = [
        name
        for name, state in states
        if isinstance(state, dict) and state.get("researched")
    ]
    if not names:
        return "none"
    shown = ", ".join(names[:MAX_TECHNOLOGIES])
    hidden = len(names) - MAX_TECHNOLOGIES
    return f"{shown}, ... and {hidden} more" if hidden > 0 else shown


def _render_research(research: Any) -> list[str]:
    if not isinstance(research, dict):
        return []

    lines = []
    current = _present(research.get("current_research")) or _present(
        research.get("current")
    )
    lines.append(f"current: {current}" if current else "current: none")

    # Only rendered while something is actually being researched. FLE reports
    # research_progress as 0 when nothing is active, and the old code showed that as
    # "remaining: 0", which reads as a finished research rather than an absent one.
    if current:
        progress = research.get("research_progress")
        if isinstance(progress, int | float) and not isinstance(progress, bool):
            lines.append(f"progress: {_format_number(progress)}")
        else:
            # Either key may carry remaining ingredients, depending on the source.
            rendered = _render_counts(progress)
            if rendered == EMPTY:
                rendered = _render_counts(research.get("progress"))
            if rendered != EMPTY:
                lines.append(f"remaining: {rendered}")

    states = _technology_states(research.get("technologies"))
    if states:
        done = sum(
            1
            for _, state in states
            if isinstance(state, dict) and state.get("researched")
        )
        lines.append(f"researched: {done}/{len(states)} ({_researched_names(states)})")
    return lines


def _render_flows(flows: Any) -> list[str]:
    """Render production flows. ``price_list`` is deliberately not rendered."""
    if not isinstance(flows, dict):
        return []

    lines = []
    for key in ("input", "output", "crafted", "harvested"):
        if key not in flows:
            continue
        value = flows[key]
        pairs = _as_counts(value)
        if not pairs and key == "crafted":
            pairs = _crafted_outputs(value)
        rendered = _join_counts(pairs)
        # Last resort: say how many records there were rather than dropping the
        # section silently, so an unrecognised shape is visible instead of invisible.
        if rendered == EMPTY and isinstance(value, list) and value:
            rendered = f"{len(value)} items"
        if rendered != EMPTY:
            lines.append(f"{key}: {rendered}")
    return lines


def _render_execution(obs: dict[str, Any]) -> list[str]:
    """Render execution feedback so the policy can see what its own code did."""
    lines = []
    for key, label in (
        ("raw_text", "output"),
        ("stdout", "stdout"),
        ("stderr", "stderr"),
    ):
        value = obs.get(key)
        if value:
            lines.append(f"{label}: {value}")
    return lines

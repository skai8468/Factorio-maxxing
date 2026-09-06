"""Observation rendering.

See docs/contracts.md (rendering.py). Raw FLE observations are never sent to a model:
this module compacts one into labelled sections. It calls no LLM and knows nothing
about goals, so compact/full/compressed variants can be compared later without
touching the environment or LLM interfaces.

Observation keys follow the FLE schema recorded in docs/fle-integration.md.
"""

from enum import Enum
from typing import Any

EMPTY = "(none)"
MAX_ENTITIES = 32
"""Entity lines rendered before truncation. A large base would otherwise dominate the
policy's context; the remainder is summarised as a count."""


def render_observation(obs: dict[str, Any], *, max_entities: int = MAX_ENTITIES) -> str:
    """Render an observation as compact labelled sections.

    Every section header is always emitted, with ``(none)`` where the observation
    carries nothing, so the policy sees a stable structure across steps.
    """
    sections = (
        ("INVENTORY", _render_inventory(obs.get("inventory"))),
        ("ENTITIES", _render_entities(obs.get("entities"), max_entities)),
        ("RESEARCH", _render_research(obs.get("research"))),
        ("FLOWS", _render_flows(obs.get("flows"))),
        ("EXECUTION", _render_execution(obs)),
    )
    lines: list[str] = []
    for header, body in sections:
        lines.append(header)
        lines.extend(f"  {line}" for line in (body or [EMPTY]))
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


def _label(value: Any) -> str:
    """Render an enum by its bare name: ``EntityStatus.NO_FUEL`` becomes ``NO_FUEL``.

    A live observation carries real Python objects, not JSON, so entity direction and
    status arrive as enum members whose ``str()`` includes the class name.
    """
    if isinstance(value, Enum):
        return str(value.name)
    return str(value)


def _render_position(position: Any) -> str:
    if isinstance(position, dict) and "x" in position and "y" in position:
        return f"({_format_number(position['x'])}, {_format_number(position['y'])})"
    if isinstance(position, list | tuple) and len(position) == 2:
        return f"({_format_number(position[0])}, {_format_number(position[1])})"
    # A live observation carries Position objects rather than dicts.
    x, y = getattr(position, "x", None), getattr(position, "y", None)
    if x is not None and y is not None:
        return f"({_format_number(x)}, {_format_number(y)})"
    return str(position)


def _render_entities(entities: Any, max_entities: int) -> list[str]:
    if not isinstance(entities, list) or not entities:
        return []

    lines = []
    for entity in entities[:max_entities]:
        if not isinstance(entity, dict):
            lines.append(str(entity))
            continue
        name = entity.get("name", "unknown")
        line = name
        if "position" in entity:
            line += f" at {_render_position(entity['position'])}"
        if entity.get("direction") is not None:
            line += f" facing {_label(entity['direction'])}"
        if entity.get("status") is not None:
            line += f" [{_label(entity['status'])}]"
        lines.append(line)

    hidden = len(entities) - max_entities
    if hidden > 0:
        lines.append(f"... and {hidden} more")
    return lines


def _technology_states(technologies: Any) -> list[Any]:
    """FLE's dataclass types this as a dict; its gym observation delivers a list."""
    if isinstance(technologies, dict):
        return list(technologies.values())
    if isinstance(technologies, list):
        return technologies
    return []


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
            1 for state in states if isinstance(state, dict) and state.get("researched")
        )
        lines.append(f"researched: {done}/{len(states)}")
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

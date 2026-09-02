"""Observation rendering.

See docs/contracts.md (rendering.py). Raw FLE observations are never sent to a model:
this module compacts one into labelled sections. It calls no LLM and knows nothing
about goals, so compact/full/compressed variants can be compared later without
touching the environment or LLM interfaces.

Observation keys follow the FLE schema recorded in docs/fle-integration.md.
"""

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


def _as_counts(value: Any) -> list[tuple[str, Any]]:
    """Normalise an item mapping, or a list of {name, count} records, to pairs."""
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        pairs = []
        for item in value:
            if isinstance(item, dict) and "name" in item:
                pairs.append((item["name"], item.get("count", item.get("amount", 0))))
        return pairs
    return []


def _render_counts(value: Any) -> str:
    pairs = [(name, count) for name, count in _as_counts(value) if count]
    if not pairs:
        return EMPTY
    return ", ".join(f"{name} {_format_number(count)}" for name, count in pairs)


def _render_inventory(inventory: Any) -> list[str]:
    rendered = _render_counts(inventory)
    return [] if rendered == EMPTY else [rendered]


def _render_position(position: Any) -> str:
    if isinstance(position, dict) and "x" in position and "y" in position:
        return f"({_format_number(position['x'])}, {_format_number(position['y'])})"
    if isinstance(position, list | tuple) and len(position) == 2:
        return f"({_format_number(position[0])}, {_format_number(position[1])})"
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
            line += f" facing {entity['direction']}"
        if entity.get("status") is not None:
            line += f" [{entity['status']}]"
        lines.append(line)

    hidden = len(entities) - max_entities
    if hidden > 0:
        lines.append(f"... and {hidden} more")
    return lines


def _render_research(research: Any) -> list[str]:
    if not isinstance(research, dict):
        return []

    lines = []
    current = research.get("current_research") or research.get("current")
    lines.append(f"current: {current}" if current else "current: none")

    progress = research.get("research_progress", research.get("progress"))
    if progress not in (None, [], {}):
        rendered = _render_counts(progress)
        lines.append(f"remaining: {rendered if rendered != EMPTY else progress}")

    technologies = research.get("technologies")
    if isinstance(technologies, dict) and technologies:
        done = sum(
            1
            for state in technologies.values()
            if isinstance(state, dict) and state.get("researched")
        )
        lines.append(f"researched: {done}/{len(technologies)}")
    return lines


def _render_flows(flows: Any) -> list[str]:
    """Render production flows. ``price_list`` is deliberately not rendered."""
    if not isinstance(flows, dict):
        return []

    lines = []
    for key in ("input", "output", "crafted", "harvested"):
        if key not in flows:
            continue
        rendered = _render_counts(flows[key])
        if rendered == EMPTY and isinstance(flows[key], list) and flows[key]:
            rendered = f"{len(flows[key])} items"
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

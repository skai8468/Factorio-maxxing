"""How far a run got: milestones reached, read from recorded FLE state.

See docs/build-plan.md section 5d and docs/decisions.md D60, D62. The Scorer is the
experiment's capability measure. It is passive, like the Recorder: it reads a finished
trajectory's ``step`` records - the observation FLE returned after each action - and
nothing else. It never reads ``verification`` records, so a verifier that declares a goal
done cannot move the score (the verifier was fooled twice on goal 5: D49, D52), and it
never runs inside the loop, so it cannot drive control flow.

A milestone counts as reached at the first step whose observation satisfies it, and
stays reached. Some milestones are cumulative - "a gear and a circuit crafted" holds once
both have appeared at any step, not necessarily the same one - and the rest are checked
on one observation. The headline is the highest milestone reached (section 5); the
first step of each is kept so steps-to-milestone can be reported too.

The ladder is the research lead's draft and will change, so it is data: each milestone
is a key, a description and a predicate, and ``LADDER_VERSION`` is stamped on every
result so scores from different ladders are never compared by accident.

    python -m factorio_maxxing.scorer trajectories/<run>.jsonl ...
"""

import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factorio_maxxing.rendering import _as_counts, _crafted_outputs, _technology_states
from factorio_maxxing.trajectory import read_trajectory

LADDER_VERSION = "v0-draft"
"""Bump whenever a milestone is added, removed, reordered or redefined."""


# --- Reading one observation ---------------------------------------------------------


def _number(value: Any) -> float:
    """FLE records inventory quantities as strings ('10'), flows as numbers."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def inventory(obs: dict[str, Any]) -> dict[str, float]:
    counts: dict[str, float] = {}
    for name, count in _as_counts(obs.get("inventory")):
        counts[name] = counts.get(name, 0.0) + _number(count)
    return counts


def entities(obs: dict[str, Any]) -> list[dict[str, Any]]:
    value = obs.get("entities")
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


def status(entity: dict[str, Any]) -> str:
    """'EntityStatus.WORKING', an enum member, or a bare 'WORKING' -> 'WORKING'."""
    raw = entity.get("status")
    raw = getattr(raw, "name", raw)
    return str(raw).rsplit(".", 1)[-1].upper() if raw is not None else ""


def working(obs: dict[str, Any], *names: str) -> list[dict[str, Any]]:
    return [e for e in entities(obs) if e.get("name") in names and status(e) == "WORKING"]


def researched(obs: dict[str, Any]) -> set[str]:
    research = obs.get("research")
    if not isinstance(research, dict):
        return set()
    return {
        name
        for name, state in _technology_states(research.get("technologies"))
        if isinstance(state, dict) and state.get("researched")
    }


def produced_items(obs: dict[str, Any]) -> set[str]:
    """Items this step shows the agent having or making: inventory, flow outputs,
    crafted outputs and harvests. Flows cover one step, so this is per-observation;
    the cumulative set is kept by the scoring pass."""
    flows = obs.get("flows") if isinstance(obs.get("flows"), dict) else {}
    pairs = [
        *inventory(obs).items(),
        *_as_counts(flows.get("output")),
        *_crafted_outputs(flows.get("crafted")),
        *_as_counts(flows.get("harvested")),
    ]
    return {name for name, count in pairs if _number(count) > 0}


_COORDINATE = re.compile(r"\b([xy])\s*=\s*(-?\d+(?:\.\d+)?)")


def position(value: Any) -> tuple[float, float] | None:
    """A position as recorded: a dict, a pair, a Position object, or FLE's string
    form 'x=3.0 y=-73.0' (also inside 'Position(x=3.0, y=-73.0)')."""
    if isinstance(value, dict) and "x" in value and "y" in value:
        return _number(value["x"]), _number(value["y"])
    if isinstance(value, list | tuple) and len(value) == 2:
        return _number(value[0]), _number(value[1])
    if isinstance(value, str):
        found = dict(_COORDINATE.findall(value))
        if "x" in found and "y" in found:
            return float(found["x"]), float(found["y"])
        return None
    x, y = getattr(value, "x", None), getattr(value, "y", None)
    return (_number(x), _number(y)) if x is not None and y is not None else None


def _tile_size(entity: dict[str, Any]) -> tuple[float, float]:
    found = dict(
        re.findall(
            r"tile_(width|height)\s*=\s*(\d+(?:\.\d+)?)",
            str(entity.get("tile_dimensions", "")),
        )
    )
    return float(found.get("width", 1)), float(found.get("height", 1))


def covers(entity: dict[str, Any], point: tuple[float, float]) -> bool:
    """Whether a point lies on the tiles an entity occupies."""
    centre = position(entity.get("position"))
    if centre is None:
        return False
    width, height = _tile_size(entity)
    return (
        abs(point[0] - centre[0]) <= width / 2 and abs(point[1] - centre[1]) <= height / 2
    )


# --- The ladder ----------------------------------------------------------------------


@dataclass
class History:
    """What the scoring pass has seen so far, for cumulative milestones."""

    items: set[str] = field(default_factory=set)


Check = Callable[[dict[str, Any], History], bool]


@dataclass(frozen=True)
class Milestone:
    key: str
    description: str
    check: Check


DRILLS = ("burner-mining-drill", "electric-mining-drill")
ASSEMBLERS = ("assembling-machine-1", "assembling-machine-2", "assembling-machine-3")


def _drill_feeds_furnace(obs: dict[str, Any], _: History) -> bool:
    """A working drill on iron ore drops into a working furnace - automation, not a
    furnace the agent filled by hand. Checked from the drill's own drop position."""
    furnaces = working(obs, "stone-furnace", "steel-furnace", "electric-furnace")
    for drill in working(obs, *DRILLS):
        drop = position(drill.get("drop_position"))
        mines_iron = "iron-ore" in str(drill.get("resources", ""))
        if drop and mines_iron and any(covers(f, drop) for f in furnaces):
            return True
    return False


def _assembler_makes_red_science(obs: dict[str, Any], _: History) -> bool:
    # Unverified live: no recorded run has an assembler yet. FLE types the field as a
    # Recipe, so the name is matched anywhere in its recorded form.
    return any(
        "automation-science-pack" in str(e.get("recipe", ""))
        for e in working(obs, *ASSEMBLERS)
    )


LADDER: tuple[Milestone, ...] = (
    Milestone(
        "iron-ore",
        "Iron ore in inventory",
        lambda o, h: inventory(o).get("iron-ore", 0) > 0,
    ),
    Milestone("iron-plate", "Iron plate smelted", lambda o, h: "iron-plate" in h.items),
    Milestone(
        "burner-drill-working",
        "Burner mining drill WORKING",
        lambda o, h: bool(working(o, "burner-mining-drill")),
    ),
    Milestone(
        "automated-iron",
        "Drill feeding a furnace: automated iron plates",
        _drill_feeds_furnace,
    ),
    Milestone("copper-plate", "Copper plate", lambda o, h: "copper-plate" in h.items),
    Milestone(
        "gear-and-circuit",
        "Iron gear wheel and electronic circuit crafted",
        lambda o, h: {"iron-gear-wheel", "electronic-circuit"} <= h.items,
    ),
    Milestone(
        "steam-power",
        "Steam power producing electricity",
        lambda o, h: bool(working(o, "steam-engine")),
    ),
    Milestone(
        "lab-working", "Lab powered and WORKING", lambda o, h: bool(working(o, "lab"))
    ),
    Milestone(
        "automation", "automation researched", lambda o, h: "automation" in researched(o)
    ),
    Milestone(
        "red-science-assembler",
        "Assembler producing automation science packs",
        _assembler_makes_red_science,
    ),
    Milestone(
        "electric-drill-working",
        "Electric mining drill WORKING",
        lambda o, h: bool(working(o, "electric-mining-drill")),
    ),
    Milestone(
        "logistics", "logistics researched", lambda o, h: "logistics" in researched(o)
    ),
    Milestone(
        "green-science",
        "Logistic science pack produced",
        lambda o, h: "logistic-science-pack" in h.items,
    ),
)
"""build-plan.md section 5d, milestones 1-13. Index 1 is the first rung; 0 means none."""


# --- Scoring a run -------------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    run_id: str | None
    steps: int
    reached: dict[str, int]
    """Milestone key -> first step (as recorded) at which it held."""
    highest: int
    """1-based index of the highest milestone reached; 0 if none."""
    ladder_version: str = LADDER_VERSION

    @property
    def highest_key(self) -> str | None:
        return LADDER[self.highest - 1].key if self.highest else None


def score_records(
    records: Iterable[dict[str, Any]], ladder: Sequence[Milestone] = LADDER
) -> Score:
    """Score a trajectory's records. Only ``step`` records are read (D62)."""
    history = History()
    reached: dict[str, int] = {}
    run_id = None
    steps = 0
    for record in records:
        if record.get("type") != "step":
            continue
        run_id = run_id or record.get("run_id")
        steps += 1
        obs = record.get("observation")
        if not isinstance(obs, dict):
            continue
        history.items |= produced_items(obs)
        for milestone in ladder:
            if milestone.key not in reached and milestone.check(obs, history):
                reached[milestone.key] = record.get("step", steps - 1)
    highest = max(
        (index for index, m in enumerate(ladder, 1) if m.key in reached), default=0
    )
    return Score(run_id=run_id, steps=steps, reached=reached, highest=highest)


def score_trajectory(path: Path | str) -> Score:
    return score_records(read_trajectory(path))


def format_score(score: Score, ladder: Sequence[Milestone] = LADDER) -> list[str]:
    top = ladder[score.highest - 1].description if score.highest else "none"
    lines = [
        f"run:           {score.run_id}",
        f"steps:         {score.steps}",
        f"highest:       {score.highest}/{len(ladder)} - {top}",
        f"ladder:        {score.ladder_version}",
    ]
    for index, milestone in enumerate(ladder, 1):
        step = score.reached.get(milestone.key)
        mark = f"step {step}" if step is not None else "-"
        lines.append(f"  {index:>2}. {milestone.description:<50} {mark}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    paths = list(sys.argv[1:] if argv is None else argv)
    if not paths:
        print(
            "usage: python -m factorio_maxxing.scorer TRAJECTORY.jsonl ...",
            file=sys.stderr,
        )
        return 2
    for index, path in enumerate(paths):
        if index:
            print("")
        try:
            score = score_trajectory(path)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(f"trajectory:    {path}")
        for line in format_score(score):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

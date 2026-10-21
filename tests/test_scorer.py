"""Scorer: milestones from recorded state, never from the verifier (5d, D62)."""

import enum
import json
import re

import pytest

from factorio_maxxing.scorer import (
    LADDER,
    LADDER_VERSION,
    covers,
    format_score,
    main,
    position,
    score_records,
    score_trajectory,
    status,
)

# Copied from a live FLE 0.4.8 run (20261018T190420-5948ff61, goal 3): the shapes the
# scorer must read - string positions, string quantities, enum-named statuses.
LIVE_DRILL = {
    "name": "burner-mining-drill",
    "position": "x=3.0 y=-73.0",
    "tile_dimensions": "tile_width=2.0 tile_height=2.0",
    "status": "EntityStatus.WORKING",
    "drop_position": "x=3.5 y=-71.5",
    "resources": ["name='iron-ore' count=9988 type=None"],
}
LIVE_FURNACE = {
    "name": "stone-furnace",
    "position": "x=4.0 y=-71.0",
    "tile_dimensions": "tile_width=2.0 tile_height=2.0",
    "status": "EntityStatus.WORKING",
}


def step(n, **observation):
    return {"type": "step", "run_id": "run-1", "step": n, "observation": observation}


def rank(key):
    return [m.key for m in LADDER].index(key) + 1


def test_an_empty_run_reaches_nothing():
    score = score_records([])
    assert (score.highest, score.reached, score.steps) == (0, {}, 0)
    assert score.ladder_version == LADDER_VERSION


def test_a_verifier_done_cannot_move_the_score():
    """The invariant (CLAUDE.md, D62): the scorer never reads verification records.
    Measured case: a verifier declared 'Research automation' done with automation
    unresearched (D49)."""
    records = [
        step(0, inventory=[]),
        {
            "type": "verification",
            "run_id": "run-1",
            "step": 0,
            "done": True,
            "reason": "automation is researched",
        },
    ]
    assert score_records(records).highest == 0


def test_inventory_quantities_recorded_as_strings_are_read():
    score = score_records([step(0, inventory=[{"type": "iron-ore", "quantity": "5"}])])
    assert score.reached == {"iron-ore": 0}


def test_a_zero_quantity_is_not_possession():
    score = score_records([step(0, inventory=[{"type": "iron-ore", "quantity": "0"}])])
    assert "iron-ore" not in score.reached


def test_a_milestone_keeps_its_first_step():
    records = [
        step(0, inventory=[]),
        step(1, inventory={"iron-plate": 3}),
        step(2, inventory={}),
        step(3, inventory={"iron-plate": 9}),
    ]
    assert score_records(records).reached["iron-plate"] == 1


def test_items_seen_in_flows_count_even_when_not_kept():
    flows = {"crafted": [{"crafted_count": 1, "outputs": {"copper-plate": 1}}]}
    assert "copper-plate" in score_records([step(0, flows=flows)]).reached


def test_gear_and_circuit_are_cumulative_across_steps():
    records = [
        step(0, flows={"crafted": [{"outputs": {"iron-gear-wheel": 2}}]}),
        step(1, inventory=[{"type": "electronic-circuit", "quantity": "1"}]),
    ]
    assert score_records(records).reached["gear-and-circuit"] == 1


def test_the_live_drill_feeding_a_furnace_is_automated_iron():
    score = score_records([step(0, entities=[LIVE_DRILL, LIVE_FURNACE])])
    assert {"burner-drill-working", "automated-iron"} <= set(score.reached)
    assert score.highest == rank("automated-iron")


def test_a_hand_filled_furnace_beside_a_drill_is_not_automation():
    drill = {**LIVE_DRILL, "drop_position": "x=9.5 y=-71.5"}  # drops on the ground
    score = score_records([step(0, entities=[drill, LIVE_FURNACE])])
    assert "automated-iron" not in score.reached
    assert "burner-drill-working" in score.reached


def test_a_drill_on_copper_is_not_automated_iron():
    drill = {**LIVE_DRILL, "resources": ["name='copper-ore' count=500 type=None"]}
    assert (
        "automated-iron"
        not in score_records([step(0, entities=[drill, LIVE_FURNACE])]).reached
    )


def test_an_idle_furnace_is_not_automation():
    furnace = {**LIVE_FURNACE, "status": "EntityStatus.NO_INGREDIENTS"}
    assert (
        "automated-iron"
        not in score_records([step(0, entities=[LIVE_DRILL, furnace])]).reached
    )


def test_a_steam_engine_must_be_working_not_merely_built():
    """A built engine with nothing drawing power is never WORKING; the boiler alone
    is not electricity."""
    idle = [
        {"name": "boiler", "status": "EntityStatus.WORKING"},
        {
            "name": "steam-engine",
            "status": "EntityStatus.NOT_PLUGGED_IN_ELECTRIC_NETWORK",
        },
    ]
    assert "steam-power" not in score_records([step(0, entities=idle)]).reached
    running = [{"name": "steam-engine", "status": "EntityStatus.WORKING"}]
    assert "steam-power" in score_records([step(0, entities=running)]).reached


def test_research_is_read_by_name_from_the_technologies_list():
    research = {
        "technologies": [
            {"name": "automation", "researched": 1},
            {"name": "logistics", "researched": 0},
        ]
    }
    reached = score_records([step(0, research=research)]).reached
    assert "automation" in reached and "logistics" not in reached


def test_the_highest_rung_counts_even_with_lower_rungs_missing():
    research = {"technologies": [{"name": "automation", "researched": 1}]}
    assert score_records([step(0, research=research)]).highest == rank("automation")


def test_an_assembler_making_red_science():
    assembler = {
        "name": "assembling-machine-1",
        "status": "EntityStatus.WORKING",
        "recipe": "name='automation-science-pack' ingredients=[...]",
    }
    other = {**assembler, "recipe": "iron-gear-wheel"}
    assert (
        "red-science-assembler" in score_records([step(0, entities=[assembler])]).reached
    )
    assert (
        "red-science-assembler" not in score_records([step(0, entities=[other])]).reached
    )


def test_status_reads_every_recorded_form():
    class EntityStatus(enum.Enum):
        WORKING = 1

    assert status({"status": "EntityStatus.WORKING"}) == "WORKING"
    assert status({"status": "working"}) == "WORKING"
    assert status({"status": EntityStatus.WORKING}) == "WORKING"
    assert status({}) == ""


def test_position_reads_every_recorded_form():
    assert position("x=3.0 y=-73.0") == (3.0, -73.0)
    assert position("Position(x=4.0, y=-71.0)") == (4.0, -71.0)
    assert position({"x": 1, "y": 2}) == (1.0, 2.0)
    assert position([1, 2]) == (1.0, 2.0)
    assert position("nowhere") is None


def test_covers_uses_the_entity_tile_size():
    assert covers(LIVE_FURNACE, (3.5, -71.5))
    assert not covers(LIVE_FURNACE, (5.5, -71.0))


def test_malformed_observations_are_skipped_not_fatal():
    records = [
        {"type": "step", "step": 0, "observation": None},
        step(1, entities="garbage", inventory=5, research=[]),
    ]
    score = score_records(records)
    assert (score.steps, score.highest) == (2, 0)


def test_score_trajectory_and_cli(tmp_path, capsys):
    path = tmp_path / "run.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in [step(0, entities=[LIVE_DRILL, LIVE_FURNACE])]),
        encoding="utf-8",
    )
    assert score_trajectory(path).highest == rank("automated-iron")
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert f"highest:       {rank('automated-iron')}/{len(LADDER)}" in out
    assert main([]) == 2


def test_format_score_lists_every_rung():
    lines = format_score(score_records([]))
    rungs = [line for line in lines if re.match(r"\s+\d+\. ", line)]
    assert len(rungs) == len(LADDER)


# --- Ladder v1: rungs 14-24 and the contiguous count (D76) ------------------------------


def test_the_ladder_runs_to_automated_blue_science():
    assert LADDER_VERSION == "v1"
    assert len(LADDER) == 24
    assert [m.key for m in LADDER[13:]] == [
        "steel",
        "green-science-assembler",
        "pumpjack-working",
        "refinery-working",
        "chemical-plant-working",
        "plastic",
        "sulfur",
        "advanced-circuit",
        "engine-unit",
        "blue-science",
        "blue-science-assembler",
    ]


@pytest.mark.parametrize(
    ("key", "item"),
    [
        ("steel", "steel-plate"),
        ("plastic", "plastic-bar"),
        ("sulfur", "sulfur"),
        ("advanced-circuit", "advanced-circuit"),
        ("engine-unit", "engine-unit"),
        ("blue-science", "chemical-science-pack"),
    ],
)
def test_item_rungs_are_reached_when_the_item_appears(key, item):
    score = score_records([step(0, inventory=[{"type": item, "quantity": "1"}])])
    assert key in score.reached


@pytest.mark.parametrize(
    ("key", "entity"),
    [
        ("pumpjack-working", "pumpjack"),
        ("refinery-working", "oil-refinery"),
        ("chemical-plant-working", "chemical-plant"),
    ],
)
def test_oil_rungs_need_the_machine_working_not_just_built(key, entity):
    working_one = {"name": entity, "status": "EntityStatus.WORKING"}
    idle_one = {"name": entity, "status": "EntityStatus.NO_INPUT_FLUID"}
    assert key in score_records([step(0, entities=[working_one])]).reached
    assert key not in score_records([step(0, entities=[idle_one])]).reached


@pytest.mark.parametrize(
    ("key", "pack"),
    [
        ("green-science-assembler", "logistic-science-pack"),
        ("blue-science-assembler", "chemical-science-pack"),
    ],
)
def test_science_assembler_rungs_read_the_recipe(key, pack):
    assembler = {
        "name": "assembling-machine-2",
        "status": "EntityStatus.WORKING",
        "recipe": f"name='{pack}' ingredients=[...]",
    }
    assert key in score_records([step(0, entities=[assembler])]).reached
    red = {**assembler, "recipe": "name='automation-science-pack'"}
    assert key not in score_records([step(0, entities=[red])]).reached


def test_contiguous_stops_at_the_first_missing_rung():
    """D76: the live cell-5 shape - rungs 10 and 11 skipped on the way to 13."""
    tracker_steps = [
        step(0, inventory=[{"type": "iron-ore", "quantity": "1"}]),
        step(1, inventory=[{"type": "iron-plate", "quantity": "1"}]),
    ]
    score = score_records(tracker_steps)
    assert (score.highest, score.contiguous) == (2, 2)
    skipped = score_records(
        [step(0, inventory=[{"type": "logistic-science-pack", "quantity": "1"}])]
    )
    assert (skipped.highest, skipped.contiguous) == (rank("green-science"), 0)


def test_format_score_shows_the_contiguous_count():
    lines = format_score(
        score_records([step(0, inventory=[{"type": "iron-ore", "quantity": "1"}])])
    )
    assert "contiguous:    1/24" in lines

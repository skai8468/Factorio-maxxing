"""Tests for observation rendering.

Required coverage (build-plan section 21, Rendering): expected sections, compact
formatting, missing/empty fields.
"""

from enum import Enum

from factorio_maxxing.rendering import render_observation

FULL_OBSERVATION = {
    "inventory": {"iron-plate": 12, "coal": 3},
    "entities": [
        {
            "name": "burner-mining-drill",
            "position": {"x": 12.5, "y": -3.0},
            "status": "WORKING",
            "direction": "north",
        },
        {
            "name": "stone-furnace",
            "position": {"x": 14.0, "y": -3.0},
            "status": "NO_FUEL",
        },
    ],
    "research": {
        "current_research": "automation",
        "research_progress": {"automation-science-pack": 3},
        "technologies": {
            "automation": {"researched": False},
            "steel-processing": {"researched": True},
        },
    },
    "flows": {
        "input": {"coal": 4},
        "output": {"iron-ore": 12.0},
        "crafted": [],
        "price_list": {"iron-plate": 3.2},
    },
    "raw_text": "Placed burner-mining-drill at (12.5, -3.0)",
}


def sections(rendered: str) -> list[str]:
    return [line for line in rendered.splitlines() if not line.startswith("  ")]


def body_of(rendered: str, header: str) -> list[str]:
    lines = rendered.splitlines()
    start = lines.index(header) + 1
    body = []
    for line in lines[start:]:
        if not line.startswith("  "):
            break
        body.append(line[2:])
    return body


def test_all_sections_are_emitted_in_contract_order():
    assert sections(render_observation(FULL_OBSERVATION)) == [
        "INVENTORY",
        "ENTITIES",
        "RESEARCH",
        "FLOWS",
        "EXECUTION",
    ]


def test_sections_are_emitted_even_for_an_empty_observation():
    rendered = render_observation({})
    assert sections(rendered) == [
        "INVENTORY",
        "ENTITIES",
        "RESEARCH",
        "FLOWS",
        "EXECUTION",
    ]
    assert body_of(rendered, "INVENTORY") == ["(none)"]
    assert body_of(rendered, "ENTITIES") == ["(none)"]
    assert body_of(rendered, "FLOWS") == ["(none)"]
    assert body_of(rendered, "EXECUTION") == ["(none)"]


def test_inventory_is_a_single_compact_line():
    assert body_of(render_observation(FULL_OBSERVATION), "INVENTORY") == [
        "iron-plate 12, coal 3"
    ]


def test_inventory_omits_zero_counts():
    obs = {"inventory": {"iron-plate": 12, "coal": 0}}
    assert body_of(render_observation(obs), "INVENTORY") == ["iron-plate 12"]


def test_inventory_accepts_a_list_of_records():
    obs = {"inventory": [{"name": "coal", "count": 3}]}
    assert body_of(render_observation(obs), "INVENTORY") == ["coal 3"]


def test_entities_render_position_and_status():
    assert body_of(render_observation(FULL_OBSERVATION), "ENTITIES") == [
        "burner-mining-drill at (12.5, -3) facing north [WORKING]",
        "stone-furnace at (14, -3) [NO_FUEL]",
    ]


def test_entity_without_position_or_status_still_renders():
    obs = {"entities": [{"name": "wooden-chest"}]}
    assert body_of(render_observation(obs), "ENTITIES") == ["wooden-chest"]


def test_entity_position_accepts_a_pair():
    obs = {"entities": [{"name": "boiler", "position": [1, 2]}]}
    assert body_of(render_observation(obs), "ENTITIES") == ["boiler at (1, 2)"]


def test_entities_are_truncated_with_a_remainder_count():
    obs = {"entities": [{"name": f"chest-{i}"} for i in range(5)]}
    body = body_of(render_observation(obs, max_entities=2), "ENTITIES")
    assert body == ["chest-0", "chest-1", "... and 3 more"]


def test_research_renders_current_progress_and_totals():
    assert body_of(render_observation(FULL_OBSERVATION), "RESEARCH") == [
        "current: automation",
        "remaining: automation-science-pack 3",
        "researched: 1/2",
    ]


def test_research_with_nothing_active_says_so():
    obs = {"research": {"technologies": {}}}
    assert body_of(render_observation(obs), "RESEARCH") == ["current: none"]


def test_flows_render_inputs_and_outputs_and_omit_price_list():
    body = body_of(render_observation(FULL_OBSERVATION), "FLOWS")
    assert body == ["input: coal 4", "output: iron-ore 12"]
    assert "price_list" not in render_observation(FULL_OBSERVATION)


def test_execution_renders_environment_output():
    assert body_of(render_observation(FULL_OBSERVATION), "EXECUTION") == [
        "output: Placed burner-mining-drill at (12.5, -3.0)"
    ]


def test_execution_renders_stdout_and_stderr():
    obs = {"stdout": "iron count 12", "stderr": "NameError: place_entity"}
    assert body_of(render_observation(obs), "EXECUTION") == [
        "stdout: iron count 12",
        "stderr: NameError: place_entity",
    ]


def test_rendering_is_deterministic():
    assert render_observation(FULL_OBSERVATION) == render_observation(FULL_OBSERVATION)


def test_malformed_sections_do_not_raise():
    obs = {"inventory": None, "entities": "not-a-list", "research": 7, "flows": []}
    rendered = render_observation(obs)
    assert body_of(rendered, "ENTITIES") == ["(none)"]
    assert body_of(rendered, "RESEARCH") == ["(none)"]


# --- Real FLE observation shapes ---------------------------------------------------
#
# The fixture above uses {name: count} mappings. A live FLE observation does not: it
# carries lists of typed dicts, with a different quantity key per section. The renderer
# silently emitted "INVENTORY (none)" against real data while the agent held 5 coal,
# because every test used the mapping shape. These observations are transcribed from a
# measured live step (docs/fle-integration.md).

LIVE_OBSERVATION = {
    "inventory": [
        {"quantity": 7, "type": "coal"},
        {"quantity": 3, "type": "stone"},
    ],
    "entities": [],
    "research": {
        "technologies": [
            {"name": "advanced-circuit", "researched": 0, "enabled": 1},
            {"name": "automation", "researched": 1, "enabled": 1},
        ],
        "current_research": None,
        "research_progress": 0,
        "research_queue": [],
        "progress": {},
    },
    "flows": {
        "input": [],
        "output": [{"type": "coal", "rate": 7}, {"type": "stone", "rate": 3}],
        "crafted": [],
        "harvested": [{"type": "coal", "amount": 7}, {"type": "stone", "amount": 3}],
        "price_list": [],
        "static_items": [],
    },
    "raw_text": "3: ('harvested 7',)",
}


def test_live_inventory_is_not_invisible():
    """The regression this whole section exists for: a held inventory rendered (none)."""
    body = body_of(render_observation(LIVE_OBSERVATION), "INVENTORY")
    assert body == ["coal 7, stone 3"]


def test_live_flows_render_item_names_not_a_bare_count():
    body = body_of(render_observation(LIVE_OBSERVATION), "FLOWS")
    assert "output: coal 7, stone 3" in body
    assert "harvested: coal 7, stone 3" in body
    assert not [line for line in body if "items" in line]


def test_live_technologies_list_is_counted():
    """FLE's dataclass types technologies as a dict; the observation delivers a list."""
    body = body_of(render_observation(LIVE_OBSERVATION), "RESEARCH")
    assert "researched: 1/2" in body


def test_no_active_research_does_not_report_zero_remaining():
    """`remaining: 0` read as a finished research rather than an absent one."""
    body = body_of(render_observation(LIVE_OBSERVATION), "RESEARCH")
    assert body[0] == "current: none"
    assert not [line for line in body if line.startswith("remaining")]
    assert not [line for line in body if line.startswith("progress")]


def test_numeric_research_progress_is_labelled_progress_not_remaining():
    obs = {
        "research": {
            "current_research": "automation",
            "research_progress": 0.5,
            "technologies": [],
        }
    }
    body = body_of(render_observation(obs), "RESEARCH")
    assert "progress: 0.5" in body
    assert not [line for line in body if line.startswith("remaining")]


def test_price_list_and_static_items_are_never_rendered():
    body = body_of(render_observation(LIVE_OBSERVATION), "FLOWS")
    assert not [line for line in body if "price" in line or "static" in line]


def test_live_observation_still_emits_every_section():
    rendered = render_observation(LIVE_OBSERVATION)
    assert sections(rendered) == [
        "INVENTORY",
        "ENTITIES",
        "RESEARCH",
        "FLOWS",
        "EXECUTION",
    ]


def test_mapping_and_list_shapes_render_identically():
    """One renderer, both shapes - fixtures and live data must not diverge."""
    as_mapping = {"inventory": {"coal": 7, "stone": 3}}
    as_list = {
        "inventory": [{"type": "coal", "quantity": 7}, {"type": "stone", "quantity": 3}]
    }
    assert body_of(render_observation(as_mapping), "INVENTORY") == body_of(
        render_observation(as_list), "INVENTORY"
    )


def test_item_records_without_a_recognised_label_are_skipped():
    obs = {"inventory": [{"quantity": 4}, {"type": "coal", "quantity": 2}]}
    assert body_of(render_observation(obs), "INVENTORY") == ["coal 2"]


def test_the_literal_string_none_is_treated_as_no_research():
    """The gym observation space is typed, so absent research arrives as "None"."""
    obs = {"research": {"current_research": "None", "research_progress": 0}}
    body = body_of(render_observation(obs), "RESEARCH")
    assert body == ["current: none"]


def test_crafted_renders_what_was_produced_not_consumed():
    """flows.crafted records craft events, not item counts."""
    obs = {
        "flows": {
            "crafted": [
                {
                    "crafted_count": 2,
                    "inputs": {"stone": 10},
                    "outputs": {"stone-furnace": 2},
                }
            ]
        }
    }
    body = body_of(render_observation(obs), "FLOWS")
    assert body == ["crafted: stone-furnace 2"]
    assert not [line for line in body if "stone 10" in line]


def test_an_unrecognised_flow_shape_is_counted_not_dropped():
    obs = {"flows": {"output": [{"mystery": 1}, {"mystery": 2}]}}
    assert body_of(render_observation(obs), "FLOWS") == ["output: 2 items"]


# A live observation carries real Python objects, not JSON-decoded dicts.


class _Position:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _Status(Enum):
    NO_FUEL = "no_fuel"


class _Direction(Enum):
    UP = 0


def test_entity_objects_from_a_live_observation_render_cleanly():
    obs = {
        "entities": [
            {
                "name": "stone-furnace",
                "position": _Position(63.0, -52.0),
                "status": _Status.NO_FUEL,
                "direction": _Direction.UP,
            }
        ]
    }
    assert body_of(render_observation(obs), "ENTITIES") == [
        "stone-furnace at (63, -52) facing UP [NO_FUEL]"
    ]


def test_enums_render_as_bare_names():
    obs = {"entities": [{"name": "drill", "status": _Status.NO_FUEL}]}
    rendered = body_of(render_observation(obs), "ENTITIES")[0]
    assert "NO_FUEL" in rendered
    assert "_Status." not in rendered

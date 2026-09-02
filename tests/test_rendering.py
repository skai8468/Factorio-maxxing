"""Tests for observation rendering.

Required coverage (build-plan section 21, Rendering): expected sections, compact
formatting, missing/empty fields.
"""

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

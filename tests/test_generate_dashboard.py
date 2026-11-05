"""Verify generic templates, private overrides, and preservation of existing views."""

import json
import re
import subprocess
import sys
from copy import deepcopy

import pytest
import yaml

from custom_components.remeha_home.generate_dashboard import TEMPLATE, generate_dashboard


def fixture_data():
    """Use synthetic bindings only; fixtures must never contain household data."""
    bindings = json.loads(TEMPLATE.with_name("dashboard-bindings.example.json").read_text())
    source = {
        "title": "Example",
        "views": [{"path": "home", "cards": []}, {"path": "other", "subview": True, "cards": []}],
    }
    return source, bindings


def test_preserve_and_repeat():
    """Append generic views, preserve inputs and unrelated content, then update in place."""
    source, bindings = fixture_data()
    before = deepcopy(source)
    result = generate_dashboard(source, bindings)
    assert source == before
    assert result["views"][:2] == before["views"]
    assert [v["path"] for v in result["views"]] == [
        "home",
        "other",
        "remeha-heating",
        "remeha-heating-details",
    ]
    assert generate_dashboard(result, bindings) == result
    assert result["views"][3]["subview"] is True
    assert result["views"][3]["back_path"] == "/lovelace/remeha-heating"
    assert "@@" not in json.dumps(result)


def test_missing_or_invalid_binding():
    """Incomplete or malformed private inputs must not produce a broken proposal."""
    source, bindings = fixture_data()
    del bindings["POWER_HP"]
    with pytest.raises(ValueError, match="POWER_HP"):
        generate_dashboard(source, bindings)
    bindings["POWER_HP"] = "invalid entity"
    with pytest.raises(ValueError, match="POWER_HP"):
        generate_dashboard(source, bindings)
    bindings["POWER_HP"] = "sensor.example_power"
    bindings["DASHBOARD_BASE"] = "//external-host"
    with pytest.raises(ValueError, match="DASHBOARD_BASE"):
        generate_dashboard(source, bindings)


def test_duplicate_view_rejected():
    """Reject ambiguous targets without silently deleting either existing view."""
    source, bindings = fixture_data()
    source["views"] += [{"path": "remeha-heating"}, {"path": "remeha-heating"}]
    with pytest.raises(ValueError, match="Duplicate"):
        generate_dashboard(source, bindings)


def test_prices_are_excluded_without_removing_energy_or_controls():
    """Retired price roles must not return when regenerating either view."""
    source, bindings = fixture_data()
    result = generate_dashboard(source, bindings)
    text = json.dumps(result)
    assert "HEATING_COSTS" not in bindings and "ELECTRICITY_PRICE" not in bindings
    assert "remeha_heating_costs" not in text and "remeha_heating_rate" not in text
    assert "CZK" not in text and "Kč" not in text
    assert bindings["ENERGY_HP"] in text and bindings["ENERGY_BACKUP"] in text
    assert bindings["TEMPORARY_START"] in text and bindings["CLIMATE"] in text


def test_timed_temperature_bindings():
    """Ovladače používají správné role i po rozdělení do samostatné sekce."""
    source, bindings = fixture_data()
    main = generate_dashboard(source, bindings)["views"][2]
    cards = [card for section in main["sections"] for card in section["cards"]]
    controls = next(
        card
        for card in cards
        if card.get("type") == "entities"
        and any(
            row.get("entity") == bindings["TEMPORARY_TEMPERATURE"]
            for row in card.get("entities", [])
        )
    )
    assert [row["entity"] for row in controls["entities"]] == [
        bindings["TEMPORARY_TEMPERATURE"],
        bindings["TEMPORARY_DURATION"],
    ]
    actions = [
        button["tap_action"]
        for card in cards
        if card.get("type") == "grid"
        for button in card.get("cards", [])
        if button.get("tap_action", {}).get("perform_action")
        in ("button.press", "climate.set_hvac_mode")
    ]
    start = next(action for action in actions if action["perform_action"] == "button.press")
    end = next(action for action in actions if action["perform_action"] == "climate.set_hvac_mode")
    assert start["target"]["entity_id"] == bindings["TEMPORARY_START"]
    assert end["target"]["entity_id"] == bindings["CLIMATE"]
    assert end["data"] == {"hvac_mode": "auto"} and end["confirmation"]["text"]
    bindings["TEMPORARY_START"] = "number.example_wrong"
    with pytest.raises(ValueError, match="Invalid domain.*TEMPORARY_START"):
        generate_dashboard(source, bindings)


def test_duration_presets_only_set_draft_minutes():
    """Všech 12 délek zůstává dostupných; rychlé volby pouze mění návrh minut."""
    source, bindings = fixture_data()
    generated = generate_dashboard(source, bindings)
    actions = []

    def collect(value):
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            action = value.get("tap_action", {})
            if (
                action.get("perform_action") == "number.set_value"
                and action.get("target", {}).get("entity_id") == bindings["TEMPORARY_DURATION"]
            ):
                actions.append((value["name"], action))
            for item in value.values():
                collect(item)

    collect(generated)
    buttons = dict(actions)
    assert {name: action["data"]["value"] for name, action in buttons.items()} == {
        "30 min": 30,
        "1 h": 60,
        "2 h": 120,
        "3 h": 180,
        "4 h": 240,
        "6 h": 360,
        "8 h": 480,
        "12 h": 720,
        "16 h": 960,
        "20 h": 1200,
        "24 h": 1440,
        "48 h": 2880,
    }
    assert all(action["perform_action"] == "number.set_value" for _, action in actions)
    main = generated["views"][2]
    temporary = main["sections"][1]
    preset_grid = next(
        card
        for card in temporary["cards"]
        if card.get("type") == "grid"
        and all(
            button.get("tap_action", {}).get("perform_action") == "number.set_value"
            for button in card.get("cards", [])
        )
    )
    assert [button["tap_action"]["data"]["value"] for button in preset_grid["cards"]] == [
        60,
        120,
        240,
        480,
    ]


def test_private_template_and_explicit_anchor():
    """Private names and paths come from the caller, never from public defaults."""
    source, _ = fixture_data()
    template = {
        "views": [
            {"path": "custom-main", "title": "Custom heading", "cards": []},
            {
                "path": "custom-details",
                "subview": True,
                "back_path": "@@DASHBOARD_BASE@@/custom-main",
                "cards": [],
            },
        ]
    }
    before = deepcopy(template)
    result = generate_dashboard(
        source, {"DASHBOARD_BASE": "/example-dashboard"}, template, insert_after="home"
    )
    assert template == before
    assert [v["path"] for v in result["views"]] == [
        "home",
        "custom-main",
        "custom-details",
        "other",
    ]
    assert result["views"][1]["title"] == "Custom heading"
    assert result["views"][2]["back_path"] == "/example-dashboard/custom-main"
    assert generate_dashboard(result, {"DASHBOARD_BASE": "/example-dashboard"}, template) == result
    with pytest.raises(ValueError, match="anchor"):
        generate_dashboard(source, {}, template, insert_after="missing")


def test_public_templates_and_examples_are_synthetic():
    """Keep JSON/YAML equivalent and require symbolic roles, not concrete entities."""
    text = TEMPLATE.read_text(encoding="utf-8")
    template = json.loads(text)
    assert yaml.safe_load(TEMPLATE.with_suffix(".yaml").read_text(encoding="utf-8")) == template
    assert not re.search(
        r"\b(?:sensor|climate|number|water_heater|switch|automation|binary_sensor|input_boolean|button|update)\.[a-z0-9_]+",
        text.replace('"number.set_value"', '"__DRAFT_NUMBER_SERVICE__"')
        .replace('"button.press"', '"__START_BUTTON_SERVICE__"')
        .replace('"climate.set_hvac_mode"', '"__RETURN_SCHEDULE_SERVICE__"'),
    )
    _, bindings = fixture_data()
    assert set(re.findall(r"@@([A-Z0-9_]+)@@", text)) == set(bindings)
    for key, value in bindings.items():
        assert key == "DASHBOARD_BASE" or re.fullmatch(r"[a-z_]+\.example_[a-z0-9_]+", value)
    assert template["views"][0]["title"] == "Remeha Heating"
    assert all(v["path"].startswith("remeha-heating") for v in template["views"])


def test_cli_private_template(tmp_path):
    """The standalone CLI must preserve an explicitly supplied private composition."""
    source, bindings = fixture_data()
    template = {
        "views": [
            {"path": "custom-main", "cards": []},
            {"path": "custom-details", "subview": True, "cards": []},
        ]
    }
    for name, data in [("input", source), ("bindings", bindings), ("template", template)]:
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
    subprocess.run(
        [
            sys.executable,
            str(TEMPLATE.with_name("generate_dashboard.py")),
            "--input",
            str(tmp_path / "input.json"),
            "--bindings",
            str(tmp_path / "bindings.json"),
            "--template",
            str(tmp_path / "template.json"),
            "--insert-after",
            "home",
            "--output",
            str(tmp_path / "output.json"),
        ],
        check=True,
    )
    result = json.loads((tmp_path / "output.json").read_text())
    assert [v["path"] for v in result["views"]] == [
        "home",
        "custom-main",
        "custom-details",
        "other",
    ]

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
        text,
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

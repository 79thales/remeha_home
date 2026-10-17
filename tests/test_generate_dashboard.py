"""Ověřit uchování cizích pohledů a selhání při neúplných místních vazbách."""

import json
import re
from copy import deepcopy

import pytest

from custom_components.remeha_home.generate_dashboard import TEMPLATE, generate_dashboard


def fixture_data():
    """Syntetické názvy: žádné entity ani identifikátory skutečné domácnosti."""
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    keys = set(re.findall(r"@@([A-Z0-9_]+)@@", json.dumps(template)))
    bindings = {key: f"sensor.test_{key.lower()}" for key in keys}
    bindings["DASHBOARD_BASE"] = "/lovelace"
    source = {
        "title": "Test",
        "views": [
            {"path": "home", "cards": []},
            {"path": "tepelne-cerpadlo", "cards": []},
            {"path": "other", "subview": True, "cards": []},
        ],
    }
    return source, bindings


def test_preserve_and_repeat():
    """Obnovení nezmění sousedy, zdroj, navigaci ani počet vlastních záložek."""
    source, bindings = fixture_data()
    before = deepcopy(source)
    result = generate_dashboard(source, bindings)
    assert source == before
    assert [v["path"] for v in result["views"]] == [
        "home",
        "tepelne-cerpadlo",
        "vytapeni-66-new",
        "vytapeni-66-new-detaily",
        "other",
    ]
    assert [
        v for v in result["views"] if v["path"] in {"home", "tepelne-cerpadlo", "other"}
    ] == before["views"]
    assert generate_dashboard(result, bindings) == result
    assert result["views"][3]["subview"] is True
    assert result["views"][3]["back_path"] == "/lovelace/vytapeni-66-new"
    assert "@@" not in json.dumps(result)


def test_missing_or_invalid_binding():
    """Neúplný nebo nepřípustný vstup nesmí vytvořit poškozený dashboard."""
    source, bindings = fixture_data()
    del bindings["POWER_TC"]
    with pytest.raises(ValueError, match="POWER_TC"):
        generate_dashboard(source, bindings)
    bindings["POWER_TC"] = "invalid entity"
    with pytest.raises(ValueError, match="POWER_TC"):
        generate_dashboard(source, bindings)


def test_duplicate_view_rejected():
    """Nejednoznačnou existující konfiguraci generátor tiše nesmaže."""
    source, bindings = fixture_data()
    source["views"] += [{"path": "vytapeni-66-new"}, {"path": "vytapeni-66-new"}]
    with pytest.raises(ValueError, match="Duplicitní"):
        generate_dashboard(source, bindings)

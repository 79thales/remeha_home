"""Generate a generic English heating overview and its linked detail subview.

Inputs are a full Lovelace configuration, private entity bindings, and an optional
private template. The result is a new configuration; unrelated views, ordering,
and the input objects are preserved. This standard-library tool works offline
and never calls Home Assistant services or writes to a running instance.

The public template contains symbolic roles and generic paths only. Real entity
IDs, names, room layouts, local automations, and instance-specific navigation
belong in private files outside the repository. A custom template keeps those
extensions available without publishing them or hard-coding them in this module.

The read-only system diagram contains a compact thermometer overview.
Heating and temporary temperature controls follow, along with hot water and energy. Start/stop controls
share a layout; all durations and existing
diagnostics remain in Details. Confirmed Stop requests the current schedule;
cloud acceptance is not proof of device execution. Generation sends no command.

Duration presets edit draft minutes in the themed override card. It invokes a
shared service with the matching climate entity after confirmation. Power is
measured in W; negative values are clamped only for display. Missing or
older-than-180-second readings remain unavailable. Calendar energy is in kWh,
using changes in import counters. Raw measurements, read-only appliance status, and diagnostics are in Details.
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

TOKEN = re.compile(r"@@([A-Z0-9_]+)@@")
ENTITY_ID = re.compile(r"[a-z_]+\.[a-z0-9_]+")
# Validate control domains before generating the dashboard.
# Prices are deliberately excluded; only W and calendar kWh are displayed.
TIMED_CONTROL_DOMAINS = {
    "TEMPORARY_TEMPERATURE": "number",
    "TEMPORARY_DURATION": "number",
    "TEMPORARY_START": "button",
}
VIEW_PATH = re.compile(r"[a-zA-Z0-9_-]+")
DASHBOARD_PATH = re.compile(r"/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*")
TEMPLATE = Path(__file__).with_name("dashboard-template.json")
CONTROL_ENTITY = re.compile(r"^(number|input_number|switch|input_boolean|automation)\.")
SCHEMATIC_CARD = "custom:remeha-heating-schematic-card"
# Každá role bere stav z HA. Snímače poskytují hodnotu a jednotku, termostat
# a ohřívač atribut žádané teploty a příznak online povoluje animaci.
SCHEMATIC_DOMAINS = {
    "thermal": "sensor",
    "online": "binary_sensor",
    "power": "sensor",
    "backup_power": "sensor",
    "hot_water": "sensor",
    "water_heater": "water_heater",
    "climate": "climate",
    "room": "sensor",
    "outdoor": "sensor",
    "pressure": "sensor",
    "slope": "number",
    "base": "number",
}


def confirm_controls(value: Any) -> Any:
    """Ochrání posuvníky a vypínače výstupu bez změny ostatních karet.

    Vstupem jsou již vyřešené entity a hodnoty konfigurace Lovelace; výstupem
    je nový strom se stejným pořadím a rozvržením. Karta v4 potvrzuje hodnotu
    před voláním služby HA. Opakované zabalení se přeskočí; generátor sám žádné
    služby nevolá. Při chybějícím frontendovém zdroji zobrazí HA chybu karty.
    """
    if isinstance(value, list):
        return [confirm_controls(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("type") == "custom:confirm-controls-card-v4":
        return deepcopy(value)
    if value.get("type") == "custom:remeha-override-card":
        entity = value.get("entity")
        if (
            not isinstance(entity, str)
            or not ENTITY_ID.fullmatch(entity)
            or not entity.startswith("climate.")
        ):
            raise ValueError("The Remeha override card requires a climate entity")
        return deepcopy(value)
    if value.get("type") == SCHEMATIC_CARD:
        # Schéma je pouze ke čtení. Ověříme rozhraní karty už v návrhu, aby
        # neplatná soukromá vazba nezpůsobila chybu až v prohlížeči.
        entities = value.get("entities")
        if not isinstance(entities, dict):
            raise ValueError("The heating schematic requires an entities object")
        for role, domain in SCHEMATIC_DOMAINS.items():
            entity = entities.get(role)
            if (
                not isinstance(entity, str)
                or not ENTITY_ID.fullmatch(entity)
                or not entity.startswith(f"{domain}.")
            ):
                raise ValueError(f"Invalid heating schematic binding: {role} requires {domain}")
        # Pokojové přídavné teploměry mohou být senzory nebo naměřený
        # atribut termostatu. Každá položka má vlastní entitu a čitelný popisek.
        thermometers = value.get("thermometers", [])
        if not isinstance(thermometers, list):
            raise ValueError("The heating schematic requires a thermometers list")
        for item in thermometers:
            if not isinstance(item, dict):
                raise ValueError("Invalid thermometer configuration")
            entity = item.get("entity")
            name = item.get("name")
            if (
                not isinstance(entity, str)
                or not ENTITY_ID.fullmatch(entity)
                or not isinstance(name, str)
                or not name.strip()
            ):
                raise ValueError("Invalid thermometer entity or name")
            if "attribute" in item:
                if item["attribute"] != "current_temperature" or not entity.startswith("climate."):
                    raise ValueError("A thermometer attribute must be climate.current_temperature")
            elif not entity.startswith("sensor."):
                raise ValueError("A thermometer without an attribute must use a sensor")
        return deepcopy(value)
    card = {key: confirm_controls(item) for key, item in value.items()}

    def is_control(item: Any) -> bool:
        entity = (
            item
            if isinstance(item, str)
            else item.get("entity")
            if isinstance(item, dict)
            else None
        )
        return isinstance(entity, str) and bool(CONTROL_ENTITY.match(entity))

    kind = card.get("type")
    protected = kind == "entities" and any(is_control(item) for item in card.get("entities", []))
    if kind == "tile" and is_control(card):
        protected = card.get("tap_action", {}).get("action") != "none" or any(
            feature.get("type") == "toggle" for feature in card.get("features", [])
        )
    if not protected:
        return card
    result = {"type": "custom:confirm-controls-card-v4", "card": card}
    if "grid_options" in card:
        result["grid_options"] = deepcopy(card["grid_options"])
    return result


def generate_dashboard(
    source: dict[str, Any],
    bindings: dict[str, str],
    template: dict[str, Any] | None = None,
    *,
    insert_after: str | None = None,
) -> dict[str, Any]:
    """Insert or update a template's main view and detail subview atomically.

    Soukromá šablona: Po restartu HA oba přepínače obnoví poslední zvolený stav.
    Původ zásahu se z bezpečnostních důvodů zapomene.

    Kruhový termostat přes celou šířku, °C, režimy, otevření nic nenastavuje.

    Accept either a Lovelace configuration or a connector wrapper containing
    ``config``. Bindings must be entity IDs, except DASHBOARD_BASE, which is a
    relative dashboard URL without a trailing slash. Missing/invalid bindings,
    invalid view paths, duplicate target paths, and missing explicit anchors fail
    before returning a result. Existing unrelated views retain content and order.

    Target paths come from the template, so private compositions can keep their
    existing titles and navigation. New main views are appended by default or
    inserted after an explicitly named existing view; new Details follows main.
    Repeated generation updates the two target views in place without duplicates.
    The diagram reads HA state updates through the registered frontend module.
    The main thermostat takes its native modes from the bound climate entity.
    Older private templates with a fixed mode list use that same behavior.
    Generation validates its entity roles and preserves the position of the
    diagram and thermometer cards. Private templates can add room sensors
    beside the generic room and outdoor temperature bindings.
    Each target view is replaced by its current template composition, so retired
    automation controls and instructions removed from a private template do not
    survive from an older dashboard. Update both private main/Details templates
    when retiring local controls; unrelated views are preserved without cleanup.
    """
    if not isinstance(source, dict) or not isinstance(bindings, dict):
        raise ValueError("Source and bindings must be objects")
    source = deepcopy(source.get("config", source))
    if not isinstance(source, dict) or not isinstance(source.get("views"), list):
        raise ValueError("Configuration must contain a views list")
    if not all(isinstance(view, dict) for view in source["views"]):
        raise ValueError("Every existing view must be an object")
    if template is None:
        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw_views = template.get("views") if isinstance(template, dict) else None
    if not isinstance(raw_views, list) or len(raw_views) != 2:
        raise ValueError("Template must contain a main view and one Details subview")
    if not all(isinstance(view, dict) for view in raw_views):
        raise ValueError("Template views must be objects")
    paths = [view.get("path") for view in raw_views]
    if not all(isinstance(path, str) and VIEW_PATH.fullmatch(path) for path in paths):
        raise ValueError("Template view paths must be non-empty slugs")
    if paths[0] == paths[1] or raw_views[0].get("subview") or not raw_views[1].get("subview"):
        raise ValueError("Template requires distinct main and Details paths")
    for path in paths:
        if sum(view.get("path") == path for view in source["views"]) > 1:
            raise ValueError(f"Duplicate view path: {path}")
    if insert_after is not None and not any(
        view.get("path") == insert_after for view in source["views"]
    ):
        raise ValueError(f"Insertion anchor does not exist: {insert_after}")

    def substitute(value: Any) -> Any:
        """Resolve symbolic roles, including Jinja text, without changing inputs."""
        if isinstance(value, dict):
            resolved = {key: substitute(item) for key, item in value.items()}
            # Termostat Remeha má jedinou variantu ručního režimu podle zóny.
            # Historický seznam by skryl heat_cool při změně integrace.
            if resolved.get("type") == "thermostat" and resolved.get("entity") == bindings.get(
                "CLIMATE"
            ):
                for feature in resolved.get("features", []):
                    if feature.get("type") == "climate-hvac-modes":
                        feature.pop("hvac_modes", None)
            return resolved
        if isinstance(value, list):
            return [substitute(item) for item in value]
        if not isinstance(value, str):
            return value

        def resolve(match: re.Match) -> str:
            key = match[1]
            item = bindings.get(key)
            if not isinstance(item, str):
                raise ValueError(f"Missing text binding: {key}")
            valid = (
                DASHBOARD_PATH.fullmatch(item)
                if key == "DASHBOARD_BASE"
                else ENTITY_ID.fullmatch(item)
            )
            if not valid:
                raise ValueError(f"Invalid binding: {key}")
            expected_domain = TIMED_CONTROL_DOMAINS.get(key)
            if expected_domain and not item.startswith(f"{expected_domain}."):
                raise ValueError(f"Invalid domain for binding: {key}")
            return item

        return TOKEN.sub(resolve, value)

    # Ochranu přidáváme až po dosazení rolí, aby fungovaly i vlastní šablony.
    # Starší profily mohou postrádat ověřené vazby na hodinové senzory.
    # V takovém případě vynechat samotnou volitelnou kartu bez odhadu entity_id.
    raw_views = deepcopy(raw_views)
    for view in raw_views:
        for section in view.get("sections", []):
            cards = section.get("cards", [])
            section["cards"] = [
                c
                for c in cards
                if c.get("type") != "custom:remeha-calendar-card"
                or all(
                    role in bindings for role in ("HEATING_CLOCK", "DHW_CLOCK", "HOLIDAY_STATUS")
                )
            ]
    views = confirm_controls(substitute(raw_views))
    # Build the whole result in memory before returning it. Each target view is
    # replaced in place; only new views require insertion or the optional anchor.
    for view in views:
        existing = next(
            (i for i, old in enumerate(source["views"]) if old.get("path") == view["path"]),
            None,
        )
        if existing is not None:
            source["views"][existing] = view
            continue
        anchor = insert_after if view["path"] == paths[0] else paths[0]
        position = next(
            (i + 1 for i, old in enumerate(source["views"]) if old.get("path") == anchor),
            len(source["views"]),
        )
        source["views"].insert(position, view)
    return source


def main() -> None:
    """Write a local JSON proposal; backups and deployment remain separate steps."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--template", type=Path, help="Optional private JSON template")
    parser.add_argument("--insert-after", help="Optional existing view path used as an anchor")
    args = parser.parse_args()
    result = generate_dashboard(
        json.loads(args.input.read_text(encoding="utf-8-sig")),
        json.loads(args.bindings.read_text(encoding="utf-8-sig")),
        json.loads(args.template.read_text(encoding="utf-8-sig")) if args.template else None,
        insert_after=args.insert_after,
    )
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

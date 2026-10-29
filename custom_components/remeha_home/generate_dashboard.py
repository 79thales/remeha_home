"""Generate a generic English heating overview and its linked detail subview.

Inputs are a full Lovelace configuration, private entity bindings, and an optional
private template. The result is a new configuration; unrelated views, ordering,
and the input objects are preserved. This standard-library tool works offline
and never calls Home Assistant services or writes to a running instance.

The public template contains symbolic roles and generic paths only. Real entity
IDs, names, room layouts, local automations, and instance-specific navigation
belong in private files outside the repository. A custom template keeps those
extensions available without publishing them or hard-coding them in this module.

Duration presets bind to the same zone's duration number and set draft minutes
only; starting an override remains a separate explicit action. Power is measured
in W; negative values are clamped only for display. Missing or
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
# These controls are created by the integration per climate zone. Reject an
# accidentally swapped binding before producing a dashboard with a broken button.
TIMED_CONTROL_DOMAINS = {
    "TEMPORARY_TEMPERATURE": "number",
    "TEMPORARY_DURATION": "number",
    "TEMPORARY_START": "button",
}
VIEW_PATH = re.compile(r"[a-zA-Z0-9_-]+")
DASHBOARD_PATH = re.compile(r"/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*")
TEMPLATE = Path(__file__).with_name("dashboard-template.json")


def generate_dashboard(
    source: dict[str, Any],
    bindings: dict[str, str],
    template: dict[str, Any] | None = None,
    *,
    insert_after: str | None = None,
) -> dict[str, Any]:
    """Insert or update a template's main view and detail subview atomically.

    Accept either a Lovelace configuration or a connector wrapper containing
    ``config``. Bindings must be entity IDs, except DASHBOARD_BASE, which is a
    relative dashboard URL without a trailing slash. Missing/invalid bindings,
    invalid view paths, duplicate target paths, and missing explicit anchors fail
    before returning a result. Existing unrelated views retain content and order.

    Target paths come from the template, so private compositions can keep their
    existing titles and navigation. New main views are appended by default or
    inserted after an explicitly named existing view; new Details follows main.
    Repeated generation updates the two target views in place without duplicates.
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
            return {key: substitute(item) for key, item in value.items()}
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

    views = substitute(raw_views)
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

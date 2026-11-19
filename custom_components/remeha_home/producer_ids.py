"""Stabilní identita zdroje energie kompatibilní s uložištěm Hugo v0.10.2."""

import json
from collections import Counter


def is_valid_producer(item):
    """API občas vrací neúplnou položku. Bez typu, média a indexu ji nelze přiřadit."""
    if not isinstance(item, dict):
        return False
    index = item.get("instanceWithinDevice")
    return (type(index) is int or isinstance(index, str) and index.isdecimal()) and all(
        isinstance(item.get(field), str) and item[field].strip()
        for field in ("energyType", "producerType")
    )


def identity(appliance, item):
    """Serializace včetně typu indexu odpovídá klíčům v Hugově uložišti."""
    return json.dumps(
        [appliance, item["instanceWithinDevice"], item["producerType"], item["energyType"]],
        separators=(",", ":"),
    )


class ProducerIdResolver:
    """Přidělené ID zůstane rezervované i při chybějícím zdroji v dalším čtení."""

    def __init__(self, assignments=None):
        self.assignments = dict(assignments or {})

    def get_id(self, appliance, item):
        if is_valid_producer(item):
            return self.assignments.get(identity(appliance, item))
        return None

    def resolve(
        self, appliance, producers, *, appliance_type=None, existing_electric_legacy_ids=()
    ):
        """Při kolizi indexu ponechat identitu elektřině a použít typový suffix.

        Dvojici nerozlišitelných odpovědí vynechat. Záměna plynu za elektřinu
        by smíchala m³ a kWh v již existující statistice recorderu.
        """
        valid = [item for item in producers if is_valid_producer(item)]
        counts = Counter(identity(appliance, item) for item in valid)
        used = set(self.assignments.values())
        resolved = []
        changed = False
        for item in sorted(
            valid,
            key=lambda p: (
                p["energyType"] != "Electric",
                str(p["instanceWithinDevice"]),
                p["producerType"],
            ),
        ):
            key = identity(appliance, item)
            if counts[key] != 1:
                continue
            assigned = self.assignments.get(key)
            if assigned is None:
                base = f"{appliance}_{item['instanceWithinDevice']}"
                reserved = (
                    appliance_type == "HeatPump"
                    and item["energyType"] == "NaturalGas"
                    and base in existing_electric_legacy_ids
                )
                assigned = base
                if reserved or base in used:
                    suffix = "".join(c if c.isalnum() else "_" for c in item["producerType"])
                    assigned = f"{base}_{suffix}"
                if assigned in used:
                    continue
                self.assignments[key] = assigned
                used.add(assigned)
                changed = True
            resolved.append((item, assigned))
        return resolved, changed

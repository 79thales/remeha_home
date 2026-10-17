"""Vygenerovat hlavní přehled vytápění a jeho detailní podhled z místních vazeb.

Vstupy: úplná konfigurace Lovelace, slovník symbol -> entity_id/URL a přibalená
JSON šablona. Výstup: nová konfigurace se dvěma pohledy; ostatní pohledy včetně
jejich pořadí a obsahu zůstanou zachovány. Generátor pracuje offline a neposílá
služby ani zápisy do HA. Při chybějící vazbě či neplatné šabloně skončí chybou.

Hlavní pohled ukazuje ruční ovládání Remeha, W a kalendářní kWh z čítačů odběru.
Záporné W se jen zobrazí jako nula, chybějící/starší než 180 s jako nedostupné.
Detail ukazuje původní Shelly hodnoty, diagnostiku, cloudovou energii a kontrolu
upstream commitů. Vazby UPSTREAM_ORIGINAL/COMMUNITY/CHECK patří dvěma senzorům
a tlačítku kontroly; jejich nedostupnost nesmí být zaměněna za nulový počet změn. YAML je
komentovaná kopie JSON šablony; konfigurace storage v HA používá JSON.
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
MAIN_PATH = "vytapeni-66-new"
DETAIL_PATH = "vytapeni-66-new-detaily"
TEMPLATE = Path(__file__).with_name("dashboard-template.json")


def generate_dashboard(
    source: dict[str, Any],
    bindings: dict[str, str],
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Vložit/obnovit oba pohledy společně a zachovat všechny ostatní pohledy.

    Zpracuje prostou konfiguraci i obálku konektoru s položkou config. Vazby
    musí být entity_id a DASHBOARD_BASE relativní adresa dashboardu bez lomítka
    na konci. Nové pohledy se vloží za původní vytápění; při opakování zůstanou
    na svých místech. Duplicity vlastních cest se odmítnou, aby se neztratil obsah.
    """
    source = deepcopy(source.get("config", source))
    if not isinstance(source.get("views"), list):
        raise ValueError("Konfigurace nemá seznam views")
    if template is None:
        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    paths = [MAIN_PATH, DETAIL_PATH]
    if [view.get("path") for view in template.get("views", [])] != paths:
        raise ValueError("Šablona musí obsahovat hlavní pohled a jeho Detaily")
    for path in paths:
        if sum(view.get("path") == path for view in source["views"]) > 1:
            raise ValueError(f"Duplicitní cesta pohledu: {path}")

    def substitute(value: Any) -> Any:
        """Nahradit i vazby v Jinja textech; nepřidávat soukromá data do šablony."""
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
                raise ValueError(f"Chybí textová vazba {key}")
            valid = (
                re.fullmatch(r"/[a-zA-Z0-9_/-]+", item) and not item.endswith("/")
                if key == "DASHBOARD_BASE"
                else ENTITY_ID.fullmatch(item)
            )
            if not valid:
                raise ValueError(f"Neplatná vazba {key}")
            return item

        return TOKEN.sub(resolve, value)

    views = substitute(template["views"])
    # Při obnově nahradit jen vlastní pohledy. Nové umístit vedle původního
    # vytápění, aby nevznikly duplicitní záložky ani změny jiných dashboardů.
    for view in views:
        existing = next(
            (i for i, old in enumerate(source["views"]) if old.get("path") == view["path"]),
            None,
        )
        if existing is not None:
            source["views"][existing] = view
            continue
        anchor = "tepelne-cerpadlo" if view["path"] == MAIN_PATH else MAIN_PATH
        position = next(
            (i + 1 for i, old in enumerate(source["views"]) if old.get("path") == anchor),
            len(source["views"]),
        )
        source["views"].insert(position, view)
    return source


def main() -> None:
    """CLI uloží pouze místní JSON; záloha a nasazení jsou samostatné kroky."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate_dashboard(
        json.loads(args.input.read_text(encoding="utf-8-sig")),
        json.loads(args.bindings.read_text(encoding="utf-8-sig")),
    )
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

"""Vytvořit instalovatelný HACS ZIP pouze ze zdrojů integrace.

Vstup je tag vX.Y.Z a cesta výstupu. Verze se musí shodovat s manifestem.
Balíček zachovává soubory v kořeni ZIP tak, jak je očekává zip_release v HACS.
Testy, historie, konfigurace domácnosti ani pycache se nikdy nezabalují.
"""

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    """Zkontrolovat verzi a zapsat malý archiv s předvídatelným seznamem cest."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "custom_components" / "remeha_home"
    version = json.loads((source / "manifest.json").read_text())["version"]
    if args.tag.removeprefix("v") != version:
        raise SystemExit("Tag does not match manifest version")
    with ZipFile(args.output, "w", ZIP_DEFLATED) as archive:
        for file in sorted(source.rglob("*")):
            if (
                file.is_file()
                and file.suffix in {".py", ".json", ".yaml"}
                and "__pycache__" not in file.parts
            ):
                archive.write(file, file.relative_to(source))


if __name__ == "__main__":
    main()

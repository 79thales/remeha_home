"""Build the HACS installer from integration sources only.

Inputs are a vX.Y.Z tag and output path. The manifest must match the tag. Files
are stored at the ZIP root for HACS zip_release. Tests, caches and recognized
private profiles/bindings are excluded; keep household files outside the source
tree as well. Build and publication never install code or restart Home Assistant.
"""

import argparse
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def is_release_file(file: Path, source: Path) -> bool:
    """Accept source formats while excluding private profiles and generated caches."""
    relative = file.relative_to(source)
    return (
        file.is_file()
        and file.suffix in {".py", ".json", ".yaml"}
        and not any(
            part in {"__pycache__", "private", "dashboard-private"} for part in relative.parts
        )
        and not re.search(r"(?:^|[-_])private(?:[._-]|$)", file.name)
        and not (
            file.suffix in {".json", ".yaml"}
            and "bindings" in file.stem
            and not file.name.endswith(".example.json")
        )
    )


def main():
    """Validate the version and write a deterministic list of allowed archive paths."""
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
            if is_release_file(file, source):
                archive.write(file, file.relative_to(source))


if __name__ == "__main__":
    main()

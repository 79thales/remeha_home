"""Keep recognized private dashboard inputs out of public installer archives."""

import pytest

from scripts.build_release import is_release_file


@pytest.mark.parametrize(
    "name,expected",
    [
        ("generate_dashboard.py", True),
        ("dashboard-template.json", True),
        ("dashboard-template.yaml", True),
        ("dashboard-bindings.example.json", True),
        ("translations/en.json", True),
        ("dashboard-private/template.json", False),
        ("private/profile.json", False),
        ("template-private.json", False),
        ("bindings.json", False),
        ("entity_bindings.yaml", False),
        ("__pycache__/module.py", False),
    ],
)
def test_release_profile_exclusions(tmp_path, name, expected):
    file = tmp_path / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("{}")
    assert is_release_file(file, tmp_path) is expected

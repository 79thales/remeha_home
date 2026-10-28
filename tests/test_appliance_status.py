"""Read-only appliance status must never manufacture a false boolean."""

import pytest

from custom_components.remeha_home.binary_sensor import RemehaHomeBinarySensor
from custom_components.remeha_home.const import APPLIANCE_BINARY_SENSOR_TYPES


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("applianceOnline", True, True),
        ("waterPressureOK", True, False),
        ("waterPressureOK", False, True),
        ("waterPressureOK", None, None),
        ("waterPressureOK", "false", None),
        ("preHeat.active", None, None),
        ("holidaySchedule.active", True, True),
    ],
)
def test_appliance_boolean_values(ready, key, value, expected):
    coordinator, _ = ready
    appliance = coordinator.items["appliance-1"]
    target = appliance
    parts = key.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value
    desc, transform = next(item for item in APPLIANCE_BINARY_SENSOR_TYPES if item[0].key == key)
    assert RemehaHomeBinarySensor(coordinator, "appliance-1", desc, transform).is_on is expected


def test_missing_nested_field_is_unknown(ready):
    coordinator, _ = ready
    desc, transform = next(
        item for item in APPLIANCE_BINARY_SENSOR_TYPES if item[0].key == "holidaySchedule.active"
    )
    assert RemehaHomeBinarySensor(coordinator, "appliance-1", desc, transform).is_on is None

"""Timed override behavior with synthetic zones and a mocked cloud."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.remeha_home.temporary import start_override


def draft(zone):
    """Build a per-zone command target without a real device or account."""
    coordinator = Mock()
    coordinator.command_locks = {}
    coordinator.last_update_success = True
    coordinator.get_by_id.return_value = zone
    coordinator.async_refresh = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    api = Mock()
    api.async_set_temporary_override = AsyncMock()
    return Mock(coordinator=coordinator, api=api, climate_zone_id="synthetic-zone")


@pytest.mark.asyncio
async def test_timed_override_sends_utc_and_retains_pending():
    target = draft({"zoneMode": "Scheduling", "capabilityTemporaryOverrideEndTime": True,
                    "setPointMin": 5, "setPointMax": 30})
    with patch("custom_components.remeha_home.temporary.dt_util.utcnow", return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)):
        await start_override(target, 21.5, 90)
    target.api.async_set_temporary_override.assert_awaited_once_with(
        "synthetic-zone", 21.5, "2026-01-01T01:30:00Z"
    )
    assert target.coordinator.temporary_pending["synthetic-zone"] == (21.5, "2026-01-01T01:30:00Z")


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", [
    {"zoneMode": "Scheduling", "setPointMin": 5, "setPointMax": 30},
    {"zoneMode": "Manual", "capabilityTemporaryOverrideEndTime": True, "setPointMin": 5, "setPointMax": 30},
])
async def test_unconfirmed_capability_or_manual_mode_never_writes(zone):
    target = draft(zone)
    with pytest.raises(ServiceValidationError):
        await start_override(target, 21, 60)
    target.api.async_set_temporary_override.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_cloud_result_stays_pending_without_retry():
    target = draft({"zoneMode": "Scheduling", "capabilityTemporaryOverrideEndTime": True,
                    "setPointMin": 5, "setPointMax": 30})
    target.api.async_set_temporary_override.side_effect = TimeoutError
    with pytest.raises(TimeoutError):
        await start_override(target, 21, 60)
    assert "synthetic-zone" in target.coordinator.temporary_pending
    target.api.async_set_temporary_override.assert_awaited_once()

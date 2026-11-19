"""Timed override behavior with synthetic zones and a mocked cloud."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.remeha_home.temporary import TemporaryOverrideJournal, start_override


@pytest.fixture
def journal(tmp_path):
    """Použije skutečný soubor a nahradí jen plánování práce v HA."""
    hass = Mock()
    hass.config.path.side_effect = lambda *parts: str(tmp_path / parts[-1])

    async def run(function, *args):
        return function(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=run)
    return TemporaryOverrideJournal(hass, "test-entry")


def draft(zone, journal):
    """Build a per-zone command target without a real device or account."""
    coordinator = Mock()
    coordinator.command_locks = {}
    coordinator.last_update_success = True
    coordinator.get_by_id.return_value = zone
    coordinator.async_refresh = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.temporary_pending = journal.records
    coordinator.temporary_override_journal = journal
    api = Mock()
    api.async_set_temporary_override = AsyncMock()
    return Mock(coordinator=coordinator, api=api, climate_zone_id="synthetic-zone")


@pytest.mark.asyncio
async def test_timed_override_sends_utc_and_retains_pending(journal):
    target = draft(
        {
            "zoneMode": "Scheduling",
            "capabilityTemporaryOverrideEndTime": True,
            "setPointMin": 5,
            "setPointMax": 30,
        },
        journal,
    )
    with patch(
        "custom_components.remeha_home.temporary.dt_util.utcnow",
        return_value=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ):
        await start_override(target, 21.5, 90)
    target.api.async_set_temporary_override.assert_awaited_once_with(
        "synthetic-zone", 21.5, "2026-01-01T01:30:00Z"
    )
    assert journal.records["synthetic-zone"] == (21.5, "2026-01-01T01:30:00Z")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "zone",
    [
        {"zoneMode": "Scheduling", "setPointMin": 5, "setPointMax": 30},
        {
            "zoneMode": "Manual",
            "capabilityTemporaryOverrideEndTime": True,
            "setPointMin": 5,
            "setPointMax": 30,
        },
    ],
)
async def test_unconfirmed_capability_or_manual_mode_never_writes(zone, journal):
    target = draft(zone, journal)
    with pytest.raises(ServiceValidationError):
        await start_override(target, 21, 60)
    target.api.async_set_temporary_override.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_cloud_result_stays_pending_without_retry(journal):
    target = draft(
        {
            "zoneMode": "Scheduling",
            "capabilityTemporaryOverrideEndTime": True,
            "setPointMin": 5,
            "setPointMax": 30,
        },
        journal,
    )
    target.api.async_set_temporary_override.side_effect = TimeoutError
    with pytest.raises(TimeoutError):
        await start_override(target, 21, 60)
    assert "synthetic-zone" in journal.records
    target.api.async_set_temporary_override.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_survives_restart_and_clears_only_on_fresh_evidence(journal):
    """Stará odpověď a cizí override nesmějí uvolnit čekající zónu."""
    end = datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
    await journal.async_mark_pending("zone-a", 21.5, end.isoformat())
    await journal.async_mark_pending("zone-b", 20, end.isoformat())
    restarted = TemporaryOverrideJournal(journal.hass, "test-entry")
    await restarted.async_load()
    assert restarted.records == journal.records

    target = draft(
        {
            "zoneMode": "Scheduling",
            "capabilityTemporaryOverrideEndTime": True,
            "setPointMin": 5,
            "setPointMax": 30,
        },
        restarted,
    )
    target.climate_zone_id = "zone-a"
    with pytest.raises(ServiceValidationError, match="awaiting confirmation"):
        await start_override(target, 21.5, timedelta(minutes=60))
    target.api.async_set_temporary_override.assert_not_awaited()

    zone = {"climateZoneId": "zone-a", "zoneMode": "Scheduling"}
    report = [{"climateZones": [zone]}]
    await restarted.async_observe(report, end - timedelta(seconds=1))
    assert set(restarted.records) == {"zone-a", "zone-b"}
    zone.update(
        zoneMode="TemporaryOverride", setPoint=20, temporaryOverride={"endTime": end.isoformat()}
    )
    await restarted.async_observe(report, end - timedelta(seconds=1))
    assert set(restarted.records) == {"zone-a", "zone-b"}
    zone["setPoint"] = 21.5
    await restarted.async_observe(report, end - timedelta(seconds=1))
    assert set(restarted.records) == {"zone-b"}
    zone.update(climateZoneId="zone-b", zoneMode="Scheduling")
    await restarted.async_observe(report, end)
    await journal.async_load()
    assert journal.records == {}


@pytest.mark.asyncio
async def test_storage_failure_prevents_cloud_command(journal):
    """Selhání zápisu na disk nesmí zanechat odeslaný nepokrytý povel."""
    target = draft(
        {
            "zoneMode": "Scheduling",
            "capabilityTemporaryOverrideEndTime": True,
            "setPointMin": 5,
            "setPointMax": 30,
        },
        journal,
    )
    journal.hass.async_add_executor_job.side_effect = OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        await start_override(target, 21.5, 60)
    target.api.async_set_temporary_override.assert_not_awaited()
    assert journal.records == {}

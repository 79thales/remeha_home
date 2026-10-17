"""Diagnostika bez přihlašovacích údajů, identifikátorů a názvů domácnosti.

Export používá explicitní seznam technických polí. Nepřidává surová data API,
config entry, časové programy ani výjimky, které by mohly obsahovat osobní data.
"""

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    """Vrátit pouze strukturu funkcí a stav posledního načtení, bez řízení."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    appliances = []
    for appliance in (coordinator.data or {}).get("appliances", []):
        zones = []
        for zone in appliance.get("climateZones", []):
            zone_id = zone["climateZoneId"]
            zones.append(
                {
                    "heating_curve_available": coordinator.get_heating_curve(zone_id) is not None,
                    "activities_available": coordinator.get_activities(zone_id) is not None,
                    "mode": zone.get("zoneMode"),
                }
            )
        appliances.append(
            {
                "type": appliance.get("applianceType"),
                "climate_zones": zones,
                "hot_water_zones": [
                    {
                        "type": zone.get("dhwType"),
                        "boost_supported": zone.get("capabilityBoostMode"),
                        "mode": zone.get("dhwZoneMode"),
                    }
                    for zone in appliance.get("hotWaterZones", [])
                ],
                "consumption_fields_available": sorted(
                    key
                    for key, value in appliance.get("consumptionData", {}).items()
                    if key
                    in (
                        "heatingEnergyConsumed",
                        "hotWaterEnergyConsumed",
                        "coolingEnergyConsumed",
                        "heatingEnergyDelivered",
                        "hotWaterEnergyDelivered",
                        "coolingEnergyDelivered",
                    )
                    and value is not None
                ),
            }
        )
    return {"last_update_success": coordinator.last_update_success, "appliances": appliances}

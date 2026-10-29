"""Číselné ovladače TUV, topné křivky a teplot aktivit s mezemi dodanými API.

Vznikají až po potvrzení hodnot a mezí. Nepodporovaný endpoint nebo chybějící
rozsah nevytvoří aktivní ovladač. Nově dostupné funkce se přidají při obnově.
Zápisy jsou na zónu sériové; instalace ani restart nic nenastavuje.
"""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .util import serialized_command, supports_reduced_setpoint, valid_bounds, validate_value

# Klíče API jsou zachovány, proto jedinečná ID existujících ovladačů neměníme.
HEATING_CURVE_PARAMS = {
    "slope": ("slope", "minimumSlope", "maximumSlope", "incrementSlope", "Heating Curve Slope"),
    "base_setpoint": (
        "baseSetpoint",
        "minimumBaseSetpoint",
        "maximumBaseSetpoint",
        "incrementBaseSetpoint",
        "Heating Curve Base Setpoint",
    ),
}


def dhw_bounds(zone, kind):
    """Vrátit meze TUV v °C; krok UI 0,5 °C zachovává převzaté ovládání."""
    ranges = zone.get("setPointRanges") or {}
    prefix = "comfortSetpoint" if kind == "comfort" else "reducedSetpoint"
    return (
        ranges.get(prefix + "Min", zone.get("setPointMin")),
        ranges.get(prefix + "Max", zone.get("setPointMax")),
        0.5,
    )


async def async_setup_entry(hass, entry, async_add_entities):
    """Přidat podpořené ovladače nyní i po pozdější úspěšné obnově API.

    Posluchač se při unload odpojí. Registr se nemaže, ID brání duplicitám.
    Rozsah se kontroluje před vytvořením, nikoli odhadem modelu zařízení.
    """
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    api = hass.data[DOMAIN][entry.entry_id]["api"]
    known = set()

    @callback
    def add_supported():
        entities = []
        for appliance in coordinator.data.get("appliances", []):
            for zone in appliance.get("hotWaterZones", []):
                for kind, key, name in [
                    ("comfort", "comfortSetPoint", "Comfort Temperature"),
                    ("eco", "reducedSetpoint", "Eco Temperature"),
                ]:
                    if kind == "eco" and not supports_reduced_setpoint(zone):
                        continue
                    if not valid_bounds(*dhw_bounds(zone, kind)) or zone.get(key) is None:
                        continue
                    entities.append(
                        RemehaHomeDhwSetpointNumber(
                            api, coordinator, zone["hotWaterZoneId"], kind, key, name
                        )
                    )
            for zone in appliance.get("climateZones", []):
                zone_id = zone["climateZoneId"]
                if zone.get("capabilityTemporaryOverrideEndTime") is True and valid_bounds(
                    zone.get("setPointMin"), zone.get("setPointMax"), 0.5
                ):
                    entities.extend(
                        TemporaryInput(coordinator, zone_id, kind)
                        for kind in ("temperature", "duration")
                    )
                curve = coordinator.get_heating_curve(zone_id) or {}
                for parameter, config in HEATING_CURVE_PARAMS.items():
                    if (
                        valid_bounds(*(curve.get(k) for k in config[1:4]))
                        and curve.get(config[0]) is not None
                    ):
                        entities.append(
                            RemehaHomeHeatingCurveNumber(api, coordinator, zone_id, parameter)
                        )
                for activity in coordinator.get_activities(zone_id) or []:
                    if (
                        activity.get("type") == "Heating"
                        and activity.get("temperature") is not None
                        and valid_bounds(
                            activity.get("setPointMin"),
                            activity.get("setPointMax"),
                            activity.get("increment"),
                        )
                    ):
                        entities.append(
                            RemehaHomeActivityNumber(
                                api,
                                coordinator,
                                zone_id,
                                activity["activityNumber"],
                                activity["name"],
                            )
                        )
        fresh = [entity for entity in entities if entity.unique_id not in known]
        known.update(entity.unique_id for entity in fresh)
        if fresh:
            async_add_entities(fresh)

    add_supported()
    entry.async_on_unload(coordinator.async_add_listener(add_supported))


class TemporaryInput(CoordinatorEntity, RestoreEntity, NumberEntity):
    """Remember a per-zone draft; editing this number sends no cloud command.

    Duration is minutes (1–10080, a UI bound rather than a device limit).
    The temperature range is read from the zone in °C. Restored drafts never
    start an override; a fresh capability and range check occurs at the button.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, zone_id, kind):
        super().__init__(coordinator)
        self.climate_zone_id, self.kind = zone_id, kind
        self._attr_unique_id = f"{DOMAIN}_{zone_id}_temporary_{kind}"
        self._attr_name = f"Temporary {kind}"
        self._attr_native_step = 0.5 if kind == "temperature" else 1
        self._attr_native_unit_of_measurement = (
            UnitOfTemperature.CELSIUS if kind == "temperature" else "min"
        )
        self._attr_device_info = coordinator.get_device_info(zone_id)
        self._attr_native_value = None

    @property
    def native_min_value(self):
        return (
            (self.coordinator.get_by_id(self.climate_zone_id) or {}).get("setPointMin", 5)
            if self.kind == "temperature"
            else 1
        )

    @property
    def native_max_value(self):
        return (
            (self.coordinator.get_by_id(self.climate_zone_id) or {}).get("setPointMax", 30)
            if self.kind == "temperature"
            else 10080
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        try:
            value = float(last.state) if last else None
            validate_value(value, self.native_min_value, self.native_max_value)
        except (ValueError, ServiceValidationError):
            value = None
        if value is None:
            value = (
                (self.coordinator.get_by_id(self.climate_zone_id) or {}).get("setPoint")
                if self.kind == "temperature"
                else 60
            )
        self._set_draft(value)

    def _set_draft(self, value):
        self._attr_native_value = value
        drafts = self.coordinator.__dict__.setdefault("temporary_drafts", {})
        drafts.setdefault(self.climate_zone_id, {})[self.kind] = value

    async def async_set_native_value(self, value):
        validate_value(value, self.native_min_value, self.native_max_value)
        if self.kind == "duration" and not float(value).is_integer():
            raise ServiceValidationError("Duration must be a whole number of minutes")
        self._set_draft(value)
        self.async_write_ha_state()


class RemehaNumber(CoordinatorEntity, NumberEntity):
    """Sdílet potvrzené meze a při výpadku ovládání znepřístupnit.

    Poslední známé meze slouží jen atributům UI během výpadku. Odeslání povelu
    znovu vyžaduje současná platná data a rozsah; cache není oprávnění k zápisu.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def _remember_bounds(self):
        bounds = self._bounds
        if valid_bounds(*bounds):
            self._confirmed_bounds = bounds
        return self._confirmed_bounds

    @property
    def available(self):
        return super().available and valid_bounds(*self._bounds) and self.native_value is not None

    @property
    def native_min_value(self):
        return self._remember_bounds()[0]

    @property
    def native_max_value(self):
        return self._remember_bounds()[1]

    @property
    def native_step(self):
        return self._remember_bounds()[2]

    def _validate(self, value):
        if not self.available:
            raise ServiceValidationError("Current control data are unavailable")
        validate_value(value, *self._bounds[:2])


class RemehaHomeDhwSetpointNumber(RemehaNumber):
    """Zapsat výslovně Comfort/Eco cíl, nikoli hádat právě aktivní program."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    def __init__(self, api, coordinator, zone_id, kind, key, name):
        super().__init__(coordinator)
        self.api, self.hot_water_zone_id, self.kind, self.key = api, zone_id, kind, key
        self._attr_unique_id = "_".join([DOMAIN, zone_id, key])
        self._attr_name = name
        self._confirmed_bounds = self._bounds

    @property
    def _data(self):
        return self.coordinator.get_by_id(self.hot_water_zone_id) or {}

    @property
    def _bounds(self):
        return dhw_bounds(self._data, self.kind)

    @property
    def native_value(self):
        return self._data.get(self.key)

    @property
    def available(self):
        return super().available and (
            self.kind == "comfort" or supports_reduced_setpoint(self._data)
        )

    @property
    def device_info(self):
        return self.coordinator.get_device_info(self.hot_water_zone_id)

    @serialized_command("hot_water_zone_id")
    async def async_set_native_value(self, value):
        """Odeslat validovaný cíl v °C, po úspěchu načíst potvrzení z cloudu."""
        self._validate(value)
        setter = (
            self.api.async_set_dhw_comfort_setpoint
            if self.kind == "comfort"
            else self.api.async_set_dhw_reduced_setpoint
        )
        await setter(self.hot_water_zone_id, value)
        await self.coordinator.async_request_refresh()


class RemehaHomeHeatingCurveNumber(RemehaNumber):
    """Ovládat sklon (bez jednotky) či základ křivky (°C) v mezích API."""

    def __init__(self, api, coordinator, zone_id, parameter):
        super().__init__(coordinator)
        self.api, self.climate_zone_id, self.parameter = api, zone_id, parameter
        self.config = HEATING_CURVE_PARAMS[parameter]
        self._attr_unique_id = "_".join([DOMAIN, zone_id, "heating_curve_" + parameter])
        self._attr_name = self.config[4]
        if parameter == "base_setpoint":
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
        self._confirmed_bounds = self._bounds

    @property
    def _data(self):
        return self.coordinator.get_heating_curve(self.climate_zone_id) or {}

    @property
    def _bounds(self):
        return tuple(self._data.get(key) for key in self.config[1:4])

    @property
    def native_value(self):
        return self._data.get(self.config[0])

    @property
    def device_info(self):
        return self.coordinator.get_device_info(self.climate_zone_id)

    @serialized_command("climate_zone_id")
    async def async_set_native_value(self, value):
        """Nejdříve načíst oba aktuální parametry, potom změnit pouze vybraný.

        API vyžaduje oba parametry zároveň. Sériový zámek a nové čtení chrání
        před přepsáním druhého parametru jiným ovladačem nebo mobilní aplikací
        před čtením. Změnu provedenou externě mezi GET a POST API neumí zamknout.
        """
        data = await self.api.async_get_heating_curve(self.climate_zone_id)
        self.coordinator.heating_curve_data[self.climate_zone_id] = data
        self._validate(value)
        slope = value if self.parameter == "slope" else data.get("slope")
        base = value if self.parameter == "base_setpoint" else data.get("baseSetpoint")
        validate_value(slope, data.get("minimumSlope"), data.get("maximumSlope"))
        validate_value(base, data.get("minimumBaseSetpoint"), data.get("maximumBaseSetpoint"))
        await self.api.async_set_heating_curve(self.climate_zone_id, slope, base)
        self.coordinator.heating_curve_data.pop(self.climate_zone_id, None)
        await self.coordinator.async_request_refresh()


class RemehaHomeActivityNumber(RemehaNumber):
    """Upravit jedinou topnou aktivitu a zachovat ostatní teploty programu."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    def __init__(self, api, coordinator, zone_id, number, name):
        super().__init__(coordinator)
        self.api, self.climate_zone_id, self.activity_number = api, zone_id, number
        self._attr_unique_id = "_".join([DOMAIN, zone_id, f"activity_{number}_temperature"])
        self._attr_name = name + " Temperature"
        self._confirmed_bounds = self._bounds

    @property
    def _data(self):
        return next(
            (
                a
                for a in self.coordinator.get_activities(self.climate_zone_id) or []
                if a.get("type") == "Heating" and a.get("activityNumber") == self.activity_number
            ),
            {},
        )

    @property
    def _bounds(self):
        return tuple(self._data.get(key) for key in ("setPointMin", "setPointMax", "increment"))

    @property
    def native_value(self):
        return self._data.get("temperature")

    @property
    def device_info(self):
        return self.coordinator.get_device_info(self.climate_zone_id)

    @serialized_command("climate_zone_id")
    async def async_set_native_value(self, value):
        """Načíst čerstvé aktivity, validovat °C a zachovat zbývající Heating položky."""
        activities = await self.api.async_get_activities(self.climate_zone_id)
        self.coordinator.activities_data[self.climate_zone_id] = activities
        self._validate(value)
        payload = [
            {
                "activityNumber": a["activityNumber"],
                "temperature": value
                if a["activityNumber"] == self.activity_number
                else a["temperature"],
            }
            for a in activities
            if a.get("type") == "Heating"
        ]
        await self.api.async_set_heating_activities(self.climate_zone_id, payload)
        self.coordinator.activities_data.pop(self.climate_zone_id, None)
        await self.coordinator.async_request_refresh()

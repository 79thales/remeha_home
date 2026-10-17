"""Utility helpers for the Remeha Home integration."""

from __future__ import annotations

import asyncio
from functools import wraps
from math import isfinite

from homeassistant.exceptions import ServiceValidationError


def detect_dhw_setpoint_activity(
    target: float | None,
    comfort: float | None,
    reduced: float | None,
    tolerance: float = 0.25,
) -> str | None:
    """Return 'Comfort', 'Eco', or None based on which setpoint matches the target."""
    if comfort is not None and target is not None and abs(target - comfort) < tolerance:
        return "Comfort"

    if reduced is not None and target is not None and abs(target - reduced) < tolerance:
        return "Eco"

    return None


def supports_reduced_setpoint(hot_water_zone: dict) -> bool:
    """Return whether the eco (reduced) setpoint of a hot water zone can be changed.

    Remeha only accepts it for a zone with a tank. A combi boiler doesn't keep
    water warm in eco mode, so it has no eco temperature to set.
    """
    return hot_water_zone.get("dhwType") == "Tank"


def valid_bounds(minimum, maximum, step) -> bool:
    """Potvrdit konečný rozsah API a kladný krok; žádné limity neodhadovat."""
    return (
        all(type(x) in (int, float) and isfinite(x) for x in (minimum, maximum, step))
        and minimum <= maximum
        and step > 0
    )


def validate_value(value, minimum, maximum) -> None:
    """Odmítnout neznámé meze, NaN/inf a hodnotu mimo potvrzený rozsah API.

    Jednotkou je °C, u sklonu křivky bezrozměrný poměr. Krok zadávání řeší
    číselná entita; neposouváme ani nezaokrouhlujeme uživatelský požadavek.
    Chyba zabrání odeslání fyzického povelu a zobrazí se jako chyba služby HA.
    """
    if (
        not valid_bounds(minimum, maximum, 1)
        or type(value) not in (int, float)
        or not isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ServiceValidationError("Value or device limits are unavailable or out of range")


def serialized_command(id_attribute):
    """Serializovat zápisy stejné zóny, které mohou měnit sdílené hodnoty.

    Zámek žije v koordinátoru; souběžné TUV/boost či číselné zápisy se odbaví
    postupně. Chyba i zrušení úlohy jej uvolní, restart zahodí čekající povely.
    Dekorátor nic neopakuje a při spuštění/restartu sám žádný povel nevytváří.
    """

    def decorate(method):
        @wraps(method)
        async def wrapped(self, *args, **kwargs):
            zone_id = getattr(self, id_attribute)
            lock = self.coordinator.command_locks.setdefault(zone_id, asyncio.Lock())
            async with lock:
                return await method(self, *args, **kwargs)

        return wrapped

    return decorate

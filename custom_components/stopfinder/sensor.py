"""Stopfinder sensor platform – scheduled/actual times, one set per bus."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    TRIP_ACTUAL_ICONS,
    TRIP_ICONS,
)
from .coordinator import StopfinderCoordinator
from .entity import StopfinderBusEntity, async_setup_bus_entities

_LOGGER = logging.getLogger(__name__)

TRIP_POINTS: list[tuple[str, str]] = [
    ("home_pickup",    "Home Pickup"),
    ("school_dropoff", "School Dropoff"),
    ("school_pickup",  "School Pickup"),
    ("home_dropoff",   "Home Dropoff"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: StopfinderCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    actual_sensors_by_bus: dict[str, dict[str, ActualTimeSensor]] = (
        hass.data[DOMAIN][config_entry.entry_id].setdefault("actual_sensors", {})
    )

    def _make(bus_key: str) -> list[Entity]:
        entities: list[Entity] = []
        actuals: dict[str, ActualTimeSensor] = {}
        for trip_point, label in TRIP_POINTS:
            entities.append(ScheduledTimeSensor(coordinator, config_entry, bus_key, trip_point, label))
            actual = ActualTimeSensor(coordinator, config_entry, bus_key, trip_point, label)
            actuals[trip_point] = actual
            entities.append(actual)

        actual_sensors_by_bus[bus_key] = actuals
        return entities

    async_setup_bus_entities(coordinator, config_entry, async_add_entities, _make)


# ---------------------------------------------------------------------------
# Scheduled-time sensor
# ---------------------------------------------------------------------------

class ScheduledTimeSensor(StopfinderBusEntity, SensorEntity):
    """Shows the API-scheduled time for one trip event."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        bus_key: str,
        trip_point: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, config_entry, bus_key)
        self._trip_point   = trip_point
        self._attr_name      = label
        self._attr_unique_id = f"{config_entry.entry_id}_scheduled_{trip_point}_{bus_key}"
        self._attr_icon      = TRIP_ICONS.get(trip_point, "mdi:clock")

    @property
    def native_value(self) -> datetime | None:
        bd = self._bus_data
        return getattr(bd, self._trip_point, None) if bd else None


# ---------------------------------------------------------------------------
# Actual-time sensor
# ---------------------------------------------------------------------------

class ActualTimeSensor(StopfinderBusEntity, SensorEntity, RestoreEntity):
    """Records actual bus arrival times. Always available; persists across restarts."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        bus_key: str,
        trip_point: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, config_entry, bus_key)
        self._trip_point   = trip_point
        self._attr_name      = f"{label} Actual"
        self._attr_unique_id = f"{config_entry.entry_id}_actual_{trip_point}_{bus_key}"
        self._attr_icon      = TRIP_ACTUAL_ICONS.get(trip_point, "mdi:clock-outline")
        self._actual_time: datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                restored = dt_util.parse_datetime(last.state)
                if restored and restored.date() == dt_util.now().date():
                    self._actual_time = restored
            except (ValueError, TypeError):
                pass

    @property
    def available(self) -> bool:
        return True  # always available; value is None until bus arrives

    @property
    def native_value(self) -> datetime | None:
        return self._actual_time

    def record_arrival(self, timestamp: datetime) -> None:
        self._actual_time = timestamp
        self.async_write_ha_state()
        _LOGGER.info("Bus %s actual %s: %s", self._bus_key, self._trip_point, timestamp)

    def reset(self) -> None:
        self._actual_time = None
        self.async_write_ha_state()

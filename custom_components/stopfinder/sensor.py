"""Stopfinder sensor platform – scheduled/actual times and schedule type, one set per bus."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SCHEDULE_EARLY,
    SCHEDULE_HALFDAY,
    SCHEDULE_NORMAL,
    TRIP_ACTUAL_ICONS,
    TRIP_ICONS,
)
from .coordinator import BusData, StopfinderCoordinator, StopfinderCoordinatorData, bus_display_name

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
    known: set[str] = set()
    actual_sensors_by_bus: dict[str, dict[str, ActualTimeSensor]] = (
        hass.data[DOMAIN][config_entry.entry_id].setdefault("actual_sensors", {})
    )

    def _add_new_buses(data: StopfinderCoordinatorData) -> None:
        new_entities: list[SensorEntity] = []
        for bus_number in data:
            if bus_number in known:
                continue
            known.add(bus_number)

            actuals: dict[str, ActualTimeSensor] = {}
            for key, label in TRIP_POINTS:
                new_entities.append(ScheduledTimeSensor(coordinator, config_entry, bus_number, key, label))
                actual = ActualTimeSensor(coordinator, config_entry, bus_number, key, label)
                actuals[key] = actual
                new_entities.append(actual)

            new_entities.append(ScheduleTypeSensor(coordinator, config_entry, bus_number))
            actual_sensors_by_bus[bus_number] = actuals

        if new_entities:
            async_add_entities(new_entities)

    if coordinator.data:
        _add_new_buses(coordinator.data)

    @callback
    def _on_update() -> None:
        if coordinator.data:
            _add_new_buses(coordinator.data)

    config_entry.async_on_unload(coordinator.async_add_listener(_on_update))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _device_info(config_entry: ConfigEntry, bus_key: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{config_entry.entry_id}_{bus_key}")},
        name=bus_display_name(bus_key),
        manufacturer="Transfinder",
        model="Stopfinder",
    )


# ---------------------------------------------------------------------------
# Scheduled-time sensor
# ---------------------------------------------------------------------------

class ScheduledTimeSensor(CoordinatorEntity[StopfinderCoordinator], SensorEntity):
    """Shows the API-scheduled time for one trip event."""

    _attr_device_class  = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        bus_number: str,
        trip_point: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._bus_number   = bus_number
        self._trip_point   = trip_point
        self._attr_name      = label
        self._attr_unique_id = f"{config_entry.entry_id}_scheduled_{trip_point}_{bus_number}"
        self._attr_icon      = TRIP_ICONS.get(trip_point, "mdi:clock")

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._config_entry, self._bus_number)

    @property
    def _bus_data(self) -> BusData | None:
        d = self.coordinator.data
        return d.get(self._bus_number) if d else None

    @property
    def available(self) -> bool:
        # Available whenever coordinator has ever returned data for this bus.
        # coordinator.data persists across failed polls, so we stay available
        # during transient errors.  Unavailable only on no-school days (bus
        # absent from data) or before the very first successful fetch.
        d = self.coordinator.data
        return d is not None and self._bus_number in d

    @property
    def native_value(self) -> datetime | None:
        bd = self._bus_data
        return getattr(bd, self._trip_point, None) if bd else None


# ---------------------------------------------------------------------------
# Actual-time sensor
# ---------------------------------------------------------------------------

class ActualTimeSensor(
    CoordinatorEntity[StopfinderCoordinator], SensorEntity, RestoreEntity
):
    """Records actual bus arrival times. Always available; persists across restarts."""

    _attr_device_class    = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        bus_number: str,
        trip_point: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._bus_number   = bus_number
        self._trip_point   = trip_point
        self._attr_name      = f"{label} Actual"
        self._attr_unique_id = f"{config_entry.entry_id}_actual_{trip_point}_{bus_number}"
        self._attr_icon      = TRIP_ACTUAL_ICONS.get(trip_point, "mdi:clock-outline")
        self._actual_time: datetime | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._config_entry, self._bus_number)

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
        _LOGGER.info("Bus %s actual %s: %s", self._bus_number, self._trip_point, timestamp)

    def reset(self) -> None:
        self._actual_time = None
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Schedule-type sensor
# ---------------------------------------------------------------------------

class ScheduleTypeSensor(CoordinatorEntity[StopfinderCoordinator], SensorEntity):
    """Indicates normal / early / halfday for this bus's route."""

    _attr_has_entity_name   = True
    _attr_name              = "Schedule Type"
    _attr_icon              = "mdi:calendar-clock"
    _attr_device_class      = SensorDeviceClass.ENUM
    _attr_options           = [SCHEDULE_NORMAL, SCHEDULE_EARLY, SCHEDULE_HALFDAY]
    _attr_translation_key   = "schedule_type"

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        bus_number: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._bus_number   = bus_number
        self._attr_unique_id = f"{config_entry.entry_id}_schedule_type_{bus_number}"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._config_entry, self._bus_number)

    @property
    def _bus_data(self) -> BusData | None:
        d = self.coordinator.data
        return d.get(self._bus_number) if d else None

    @property
    def available(self) -> bool:
        d = self.coordinator.data
        return d is not None and self._bus_number in d

    @property
    def native_value(self) -> str | None:
        bd = self._bus_data
        return bd.schedule_type if bd else None

    @property
    def extra_state_attributes(self) -> dict:
        bd = self._bus_data
        if not bd:
            return {}
        return {
            "tracking_active": bd.tracking_active,
            "active_trip":     bd.active_trip,
        }

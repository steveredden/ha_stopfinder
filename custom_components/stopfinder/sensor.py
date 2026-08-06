"""Stopfinder sensor platform – scheduled times, actual arrivals, schedule type."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_STUDENT_LABEL,
    DOMAIN,
    SCHEDULE_EARLY,
    SCHEDULE_HALFDAY,
    SCHEDULE_NORMAL,
    TRIP_ACTUAL_ICONS,
    TRIP_ICONS,
)
from .coordinator import StopfinderCoordinator

_LOGGER = logging.getLogger(__name__)

# (coordinator attribute name, display label)
TRIP_POINTS: list[tuple[str, str]] = [
    ("home_pickup", "Home Pickup"),
    ("school_dropoff", "School Dropoff"),
    ("school_pickup", "School Pickup"),
    ("home_dropoff", "Home Dropoff"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: StopfinderCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]

    entities: list[SensorEntity] = []
    actual_sensors: dict[str, ActualTimeSensor] = {}

    for key, label in TRIP_POINTS:
        entities.append(ScheduledTimeSensor(coordinator, config_entry, key, label))
        actual = ActualTimeSensor(coordinator, config_entry, key, label)
        actual_sensors[key] = actual
        entities.append(actual)

    entities.append(ScheduleTypeSensor(coordinator, config_entry))

    # Store actual sensor references so __init__.py can call record_arrival / reset
    hass.data[DOMAIN][config_entry.entry_id]["actual_sensors"] = actual_sensors

    async_add_entities(entities)


def _device_info(config_entry: ConfigEntry) -> DeviceInfo:
    label = config_entry.data.get(CONF_STUDENT_LABEL, "Student")
    return DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id)},
        name=label,
        manufacturer="Transfinder",
        model="Stopfinder",
    )


# ---------------------------------------------------------------------------
# Scheduled-time sensor (read-only, driven by coordinator)
# ---------------------------------------------------------------------------


class ScheduledTimeSensor(CoordinatorEntity[StopfinderCoordinator], SensorEntity):
    """Shows the API-scheduled time for one trip event (e.g. home pickup)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        trip_point: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._trip_point = trip_point
        self._attr_name = label
        self._attr_unique_id = f"{config_entry.entry_id}_scheduled_{trip_point}"
        self._attr_icon = TRIP_ICONS.get(trip_point, "mdi:clock")

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._config_entry)

    @property
    def native_value(self) -> datetime | None:
        d = self.coordinator.data
        return getattr(d, self._trip_point, None) if d else None

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and not self.coordinator.data.no_school
        )


# ---------------------------------------------------------------------------
# Actual-time sensor (persisted, reset daily, written by zone tracking)
# ---------------------------------------------------------------------------


class ActualTimeSensor(
    CoordinatorEntity[StopfinderCoordinator], SensorEntity, RestoreEntity
):
    """Records the actual time the bus arrived at a trip point.

    Persists across HA restarts (cleared if the restored date differs from today).
    Reset at 00:05 daily and updated automatically when the bus enters configured zones.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        trip_point: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._trip_point = trip_point
        self._attr_name = f"{label} Actual"
        self._attr_unique_id = f"{config_entry.entry_id}_actual_{trip_point}"
        self._attr_icon = TRIP_ACTUAL_ICONS.get(trip_point, "mdi:clock-outline")
        self._actual_time: datetime | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._config_entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                restored = dt_util.parse_datetime(last_state.state)
                if restored and restored.date() == dt_util.now().date():
                    self._actual_time = restored
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> datetime | None:
        return self._actual_time

    @property
    def available(self) -> bool:
        return True  # always available; value is None until the bus arrives

    def record_arrival(self, timestamp: datetime) -> None:
        """Called by zone tracking or an automation service to stamp an arrival."""
        self._actual_time = timestamp
        self.async_write_ha_state()
        _LOGGER.info("Recorded actual %s: %s", self._trip_point, timestamp)

    def reset(self) -> None:
        """Clear the stored timestamp (called at midnight)."""
        self._actual_time = None
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Schedule-type sensor
# ---------------------------------------------------------------------------


class ScheduleTypeSensor(CoordinatorEntity[StopfinderCoordinator], SensorEntity):
    """Indicates whether today is a normal day, early release, or half day."""

    _attr_has_entity_name = True
    _attr_name = "Schedule Type"
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [SCHEDULE_NORMAL, SCHEDULE_EARLY, SCHEDULE_HALFDAY]
    _attr_translation_key = "schedule_type"

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_schedule_type"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._config_entry)

    @property
    def native_value(self) -> str | None:
        d = self.coordinator.data
        if not d or d.no_school:
            return None
        return d.schedule_type

    @property
    def extra_state_attributes(self) -> dict:
        d = self.coordinator.data
        if not d:
            return {}
        return {
            "tracking_active": d.tracking_active,
            "active_trip": d.active_trip,
        }

"""Stopfinder device_tracker platform – GPS position of the school bus."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STUDENT_LABEL, DOMAIN
from .coordinator import StopfinderCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: StopfinderCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]
    async_add_entities([StopfinderBusTracker(coordinator, config_entry)])


class StopfinderBusTracker(CoordinatorEntity[StopfinderCoordinator], TrackerEntity):
    """Represents the real-time GPS position of the tracked school bus."""

    _attr_icon = "mdi:bus-school"
    _attr_has_entity_name = True
    _attr_name = None  # entity name == device name

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_tracker"

    @property
    def device_info(self) -> DeviceInfo:
        label = self._config_entry.data.get(CONF_STUDENT_LABEL, "Student")
        return DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.entry_id)},
            name=label,
            manufacturer="Transfinder",
            model="Stopfinder",
        )

    @property
    def latitude(self) -> float | None:
        return self.coordinator.data.latitude if self.coordinator.data else None

    @property
    def longitude(self) -> float | None:
        return self.coordinator.data.longitude if self.coordinator.data else None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def extra_state_attributes(self) -> dict:
        d = self.coordinator.data
        if not d:
            return {}
        return {
            "bus_number": d.bus_number,
            "schedule_type": d.schedule_type,
            "tracking_active": d.tracking_active,
            "active_trip": d.active_trip,
        }

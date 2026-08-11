"""Stopfinder device_tracker platform – one entity per unique school bus."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import StopfinderCoordinator
from .entity import StopfinderBusEntity, async_setup_bus_entities


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: StopfinderCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    async_setup_bus_entities(
        coordinator,
        config_entry,
        async_add_entities,
        lambda bus_key: [StopfinderBusTracker(coordinator, config_entry, bus_key)],
    )


class StopfinderBusTracker(StopfinderBusEntity, TrackerEntity):
    """GPS tracker for one school bus."""

    _attr_icon = "mdi:bus-school"
    _attr_name = None  # entity name = device name

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        bus_key: str,
    ) -> None:
        super().__init__(coordinator, config_entry, bus_key)
        self._attr_unique_id = f"{config_entry.entry_id}_tracker_{bus_key}"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def latitude(self) -> float | None:
        bd = self._bus_data
        return bd.latitude if bd else None

    @property
    def longitude(self) -> float | None:
        bd = self._bus_data
        return bd.longitude if bd else None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def extra_state_attributes(self) -> dict:
        bd = self._bus_data
        if not bd:
            return {"bus_number": self._bus_key}
        return {
            "bus_number":      bd.bus_number,
            "schedule_type":   bd.schedule_type,
            "tracking_active": bd.tracking_active,
            "active_trip":     bd.active_trip,
        }

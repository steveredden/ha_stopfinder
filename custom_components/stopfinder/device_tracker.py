"""Stopfinder device_tracker platform – one entity per unique school bus."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BusData, StopfinderCoordinator, StopfinderCoordinatorData, bus_display_name


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: StopfinderCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    known: set[str] = set()

    def _add_new_buses(data: StopfinderCoordinatorData) -> None:
        new = [
            StopfinderBusTracker(coordinator, config_entry, key)
            for key in data
            if key not in known
        ]
        if new:
            known.update(t._bus_key for t in new)
            async_add_entities(new)

    if coordinator.data:
        _add_new_buses(coordinator.data)

    @callback
    def _on_update() -> None:
        if coordinator.data:
            _add_new_buses(coordinator.data)

    config_entry.async_on_unload(coordinator.async_add_listener(_on_update))


class StopfinderBusTracker(CoordinatorEntity[StopfinderCoordinator], TrackerEntity):
    """GPS tracker for one school bus."""

    _attr_icon = "mdi:bus-school"
    _attr_has_entity_name = True
    _attr_name = None  # entity name = device name

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        bus_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._bus_key      = bus_key
        self._attr_unique_id = f"{config_entry.entry_id}_tracker_{bus_key}"

    @property
    def _bus_data(self) -> BusData | None:
        d = self.coordinator.data
        return d.get(self._bus_key) if d else None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._config_entry.entry_id}_{self._bus_key}")},
            name=bus_display_name(self._bus_key),
            manufacturer="Transfinder",
            model="Stopfinder",
        )

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

"""Stopfinder device_tracker platform – one entity per student."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import StopfinderCoordinator
from .entity import StopfinderStudentEntity, async_setup_student_entities


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: StopfinderCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    async_setup_student_entities(
        coordinator,
        config_entry,
        async_add_entities,
        lambda student_key: [StopfinderStudentTracker(coordinator, config_entry, student_key)],
    )


class StopfinderStudentTracker(StopfinderStudentEntity, TrackerEntity):
    """GPS tracker for one student's school bus."""

    _attr_icon = "mdi:bus-school"
    _attr_name = None  # entity name = device name ("John Stopfinder")

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        student_key: str,
    ) -> None:
        super().__init__(coordinator, config_entry, student_key)
        self._attr_unique_id = f"{config_entry.entry_id}_tracker_{student_key}"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def latitude(self) -> float | None:
        sd = self._student_data
        return sd.latitude if sd else None

    @property
    def longitude(self) -> float | None:
        sd = self._student_data
        return sd.longitude if sd else None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def extra_state_attributes(self) -> dict:
        sd = self._student_data
        if not sd:
            return {"rider_id": self._student_key}

        def _iso(dt):
            return dt.isoformat() if dt else None

        bus_number = (
            sd.morning_bus_number if sd.active_trip == "morning"
            else sd.afternoon_bus_number if sd.active_trip == "afternoon"
            else sd.morning_bus_number or sd.afternoon_bus_number
        )

        group_name = (
            f"{sd.client_id}_{sd.data_source_id}_{bus_number}"
            if bus_number else None
        )

        return {
            "rider_id":                sd.rider_id,
            "student_name":            f"{sd.first_name} {sd.last_name}",
            "grade":                   sd.grade,
            "school":                  sd.school,
            "bus_number":              bus_number,
            "morning_bus_number":      sd.morning_bus_number,
            "afternoon_bus_number":    sd.afternoon_bus_number,
            "gps_group_name":          group_name,
            "tracking_active":         sd.tracking_active,
            "active_trip":             sd.active_trip,
            "morning_window_start":    _iso(sd.morning_window_start),
            "morning_window_end":      _iso(sd.morning_window_end),
            "afternoon_window_start":  _iso(sd.afternoon_window_start),
            "afternoon_window_end":    _iso(sd.afternoon_window_end),
            "home_pickup_stop_name":    sd.home_pickup_stop_name,
            "school_dropoff_stop_name": sd.school_dropoff_stop_name,
            "school_pickup_stop_name":  sd.school_pickup_stop_name,
            "home_dropoff_stop_name":   sd.home_dropoff_stop_name,
        }

"""Shared base entity for Stopfinder student entities.

Every entity in this integration is bound to a single student key
(str(rider_id)) and shares the same device grouping, coordinator-data
lookup, and availability rule.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StudentData, StopfinderCoordinator, student_display_name


def async_setup_student_entities(
    coordinator: StopfinderCoordinator,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[str], Iterable[Entity]],
) -> None:
    """Add entities for each student key as it first appears in coordinator data.

    Students show up once the daily schedule is fetched; both platforms
    register a listener and add entities lazily.  ``factory`` is called once
    per newly-seen student key and returns its entities.
    """
    known: set[str] = set()

    @callback
    def _add_new(data: dict[str, StudentData]) -> None:
        new_entities: list[Entity] = []
        for student_key in data:
            if student_key in known:
                continue
            known.add(student_key)
            new_entities.extend(factory(student_key))
        if new_entities:
            async_add_entities(new_entities)

    if coordinator.data:
        _add_new(coordinator.data)

    @callback
    def _on_update() -> None:
        if coordinator.data:
            _add_new(coordinator.data)

    config_entry.async_on_unload(coordinator.async_add_listener(_on_update))


class StopfinderStudentEntity(CoordinatorEntity[StopfinderCoordinator]):
    """Base for all entities that represent one student (keyed by rider_id)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        student_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry  = config_entry
        self._student_key   = student_key

    @property
    def _student_data(self) -> StudentData | None:
        d = self.coordinator.data
        return d.get(self._student_key) if d else None

    @property
    def device_info(self) -> DeviceInfo:
        sd = self._student_data
        name = (
            student_display_name(sd.first_name, sd.last_name)
            if sd else f"Student {self._student_key}"
        )
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._config_entry.entry_id}_{self._student_key}")},
            name=name,
            manufacturer="Transfinder",
            model="Stopfinder",
        )

    @property
    def available(self) -> bool:
        d = self.coordinator.data
        return d is not None and self._student_key in d

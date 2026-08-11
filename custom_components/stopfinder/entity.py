"""Shared base entity for Stopfinder bus entities.

Every entity in this integration is bound to a single bus *key* (see
``bus_display_name``) and shares the same device grouping, coordinator-data
lookup, and availability rule.  Subclasses override ``available`` where their
lifecycle differs (the GPS tracker and the restore-backed actual-time sensor).
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
from .coordinator import BusData, StopfinderCoordinator, bus_display_name


def async_setup_bus_entities(
    coordinator: StopfinderCoordinator,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[str], Iterable[Entity]],
) -> None:
    """Add entities for each bus key as it first appears in coordinator data.

    Buses show up over the course of a day (morning vs. afternoon routes), so
    both platforms register a listener and add entities lazily.  ``factory``
    is called once per newly-seen bus key and returns its entities.
    """
    known: set[str] = set()

    @callback
    def _add_new(data: dict[str, BusData]) -> None:
        new_entities: list[Entity] = []
        for bus_key in data:
            if bus_key in known:
                continue
            known.add(bus_key)
            new_entities.extend(factory(bus_key))
        if new_entities:
            async_add_entities(new_entities)

    if coordinator.data:
        _add_new(coordinator.data)

    @callback
    def _on_update() -> None:
        if coordinator.data:
            _add_new(coordinator.data)

    config_entry.async_on_unload(coordinator.async_add_listener(_on_update))


class StopfinderBusEntity(CoordinatorEntity[StopfinderCoordinator]):
    """Base for all entities that represent one bus key."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StopfinderCoordinator,
        config_entry: ConfigEntry,
        bus_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._bus_key = bus_key

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
        # Available whenever the coordinator has ever returned data for this bus.
        # coordinator.data persists across failed polls, so we stay available
        # during transient errors.  Unavailable only on no-school days (bus
        # absent from data) or before the very first successful fetch.
        d = self.coordinator.data
        return d is not None and self._bus_key in d

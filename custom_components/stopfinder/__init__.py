"""Stopfinder integration setup."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ZONE_NEIGHBORHOOD,
    CONF_ZONE_SCHOOL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import StopfinderCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Stopfinder from a config entry."""
    coordinator = StopfinderCoordinator(hass, config_entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = {
        "coordinator": coordinator,
        "actual_sensors": {},   # populated by sensor platform after setup
    }

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    # Wire zone-based arrival detection now that entities exist in the registry
    _setup_zone_tracking(hass, config_entry, coordinator)

    # Schedule the daily reset of actual timestamps at 00:05 local time
    _schedule_daily_reset(hass, config_entry)

    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_options_updated)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )
    if unload_ok:
        hass.data[DOMAIN].pop(config_entry.entry_id)
    return unload_ok


async def _async_options_updated(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Reload the integration when the user changes options."""
    await hass.config_entries.async_reload(config_entry.entry_id)


# ---------------------------------------------------------------------------
# Zone-based arrival detection
# ---------------------------------------------------------------------------


def _setup_zone_tracking(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: StopfinderCoordinator,
) -> None:
    """Subscribe to device_tracker state changes and record actual timestamps."""
    entry_id = config_entry.entry_id
    registry = er.async_get(hass)

    tracker_entity_id = registry.async_get_entity_id(
        "device_tracker", DOMAIN, f"{entry_id}_tracker"
    )
    if not tracker_entity_id:
        _LOGGER.debug(
            "Device tracker not found in entity registry; zone detection unavailable"
        )
        return

    @callback
    def _on_tracker_state_change(event: object) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return

        new_zone = new_state.state
        if new_zone == old_state.state or new_zone in (
            "not_home",
            "unknown",
            "unavailable",
        ):
            return

        actual_sensors = (
            hass.data.get(DOMAIN, {}).get(entry_id, {}).get("actual_sensors", {})
        )
        if not actual_sensors:
            return

        cdata = coordinator.data
        if not cdata or not cdata.tracking_active:
            return

        arrived_at = new_state.last_changed
        zone_nbhd = config_entry.options.get(CONF_ZONE_NEIGHBORHOOD)
        zone_school = config_entry.options.get(CONF_ZONE_SCHOOL)

        if zone_nbhd:
            zs = hass.states.get(zone_nbhd)
            if zs and new_zone == zs.name:
                if cdata.active_trip == "morning":
                    _record(actual_sensors, "home_pickup", arrived_at)
                elif cdata.active_trip == "afternoon":
                    _record(actual_sensors, "home_dropoff", arrived_at)

        if zone_school:
            zs = hass.states.get(zone_school)
            if zs and new_zone == zs.name:
                if cdata.active_trip == "morning":
                    _record(actual_sensors, "school_dropoff", arrived_at)
                elif cdata.active_trip == "afternoon":
                    _record(actual_sensors, "school_pickup", arrived_at)

    config_entry.async_on_unload(
        async_track_state_change_event(
            hass, [tracker_entity_id], _on_tracker_state_change
        )
    )


def _record(sensors: dict, key: str, ts: datetime) -> None:
    sensor = sensors.get(key)
    if sensor:
        sensor.record_arrival(ts)


# ---------------------------------------------------------------------------
# Daily midnight reset
# ---------------------------------------------------------------------------


def _schedule_daily_reset(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Schedule a reset of all actual sensors at 00:05 local time each day."""
    entry_id = config_entry.entry_id

    @callback
    def _do_reset(now: datetime) -> None:
        actual_sensors = (
            hass.data.get(DOMAIN, {}).get(entry_id, {}).get("actual_sensors", {})
        )
        for sensor in actual_sensors.values():
            sensor.reset()
        _LOGGER.debug("Daily actual-timestamp reset fired for %s", entry_id)
        # Reschedule for the next night
        _schedule_daily_reset(hass, config_entry)

    now = dt_util.now()
    next_reset = (now + timedelta(days=1)).replace(
        hour=0, minute=5, second=0, microsecond=0
    )
    cancel = async_track_point_in_time(hass, _do_reset, next_reset)
    config_entry.async_on_unload(cancel)

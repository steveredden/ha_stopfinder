"""Stopfinder integration setup."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance as haversine_m

from .const import (
    DOMAIN,
    PLATFORMS,
    STOP_RADIUS_M,
)
from .coordinator import StopfinderCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Stopfinder from a config entry."""
    coordinator = StopfinderCoordinator(hass, config_entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = {
        "coordinator": coordinator,
        "actual_sensors": {},  # bus_key → {trip_point → ActualTimeSensor}
    }

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    # GPS-based stop proximity detection — runs on every coordinator update.
    # Uses the bus stop coordinates returned by the API so no user zone
    # configuration is required.
    _setup_stop_detection(hass, config_entry, coordinator)

    _schedule_daily_reset(hass, config_entry)

    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_options_updated)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )
    if unload_ok:
        hass.data[DOMAIN].pop(config_entry.entry_id)
    return unload_ok


async def _async_options_updated(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    await hass.config_entries.async_reload(config_entry.entry_id)


# ---------------------------------------------------------------------------
# Stop proximity detection
# ---------------------------------------------------------------------------

def _setup_stop_detection(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: StopfinderCoordinator,
) -> None:
    """Subscribe to coordinator updates and stamp actual-time sensors when the
    bus GPS enters the radius of a scheduled stop.

    Stop coordinates come directly from the API (pickUpStop*/dropOffStop*), so
    no user zone configuration is needed.  Detection uses a Haversine check
    rather than HA zone state changes to avoid the entity-registry timing race.
    """
    entry_id   = config_entry.entry_id
    prev_stop: dict[str, str | None] = {}   # bus_key → stop_key or None

    @callback
    def _on_coordinator_update() -> None:
        data = coordinator.data
        if not data:
            return

        actual_sensors_all = (
            hass.data.get(DOMAIN, {}).get(entry_id, {}).get("actual_sensors", {})
        )

        for bus_key, bd in data.items():
            if not bd.tracking_active or bd.latitude is None or bd.longitude is None:
                prev_stop[bus_key] = None
                continue

            if bd.active_trip == "morning":
                candidates = [
                    ("home_pickup",    bd.home_pickup_stop),
                    ("school_dropoff", bd.school_dropoff_stop),
                ]
            elif bd.active_trip == "afternoon":
                candidates = [
                    ("school_pickup", bd.school_pickup_stop),
                    ("home_dropoff",  bd.home_dropoff_stop),
                ]
            else:
                prev_stop[bus_key] = None
                continue

            current_stop: str | None = None
            for stop_key, coords in candidates:
                if coords is None:
                    continue
                slat, slon = coords
                if haversine_m(bd.latitude, bd.longitude, slat, slon) <= STOP_RADIUS_M:
                    current_stop = stop_key
                    break

            last_stop = prev_stop.get(bus_key)
            prev_stop[bus_key] = current_stop

            if current_stop and current_stop != last_stop:
                actual_sensors = actual_sensors_all.get(bus_key, {})
                _LOGGER.debug(
                    "Bus %s arrived at stop %s (trip=%s)",
                    bus_key, current_stop, bd.active_trip,
                )
                _stamp(actual_sensors, current_stop, dt_util.now())

    config_entry.async_on_unload(
        coordinator.async_add_listener(_on_coordinator_update)
    )


def _stamp(sensors: dict, key: str, ts: datetime) -> None:
    s = sensors.get(key)
    if s:
        s.record_arrival(ts)


# ---------------------------------------------------------------------------
# Daily reset at 00:05
# ---------------------------------------------------------------------------

def _schedule_daily_reset(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    entry_id = config_entry.entry_id

    @callback
    def _do_reset(now: datetime) -> None:
        actual_sensors = (
            hass.data.get(DOMAIN, {}).get(entry_id, {}).get("actual_sensors", {})
        )
        for bus_actuals in actual_sensors.values():
            for sensor in bus_actuals.values():
                sensor.reset()
        _LOGGER.debug("Daily actual-timestamp reset for %s", entry_id)
        _schedule_daily_reset(hass, config_entry)

    next_reset = (dt_util.now() + timedelta(days=1)).replace(
        hour=0, minute=5, second=0, microsecond=0
    )
    cancel = async_track_point_in_time(hass, _do_reset, next_reset)
    config_entry.async_on_unload(cancel)

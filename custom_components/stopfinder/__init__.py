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
        "actual_sensors": {},  # bus_key → {trip_point → ActualTimeSensor}
    }

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    # GPS-based zone detection — runs on every coordinator update.
    # Avoids the entity-registry timing race that the previous approach suffered:
    # async_add_entities schedules registration as a task, so the entity_id lookup
    # immediately after async_forward_entry_setups can return None and silently bail.
    _setup_gps_zone_detection(hass, config_entry, coordinator)

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
# GPS-based zone detection
# ---------------------------------------------------------------------------

def _setup_gps_zone_detection(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: StopfinderCoordinator,
) -> None:
    """Subscribe to coordinator updates and detect zone entry from raw GPS.

    For each bus with active GPS, compute the distance to each configured zone
    center.  When the bus crosses inside a zone it wasn't in on the previous
    poll, stamp the matching actual-time sensor.

    Using GPS + Haversine rather than watching device_tracker state changes
    avoids a race condition: async_add_entities schedules entity registration as
    a future task, so the entity may not be in the registry when we try to
    subscribe immediately after async_forward_entry_setups.
    """
    entry_id  = config_entry.entry_id
    prev_zone: dict[str, str | None] = {}   # bus_key → zone_entity_id or None

    @callback
    def _on_coordinator_update() -> None:
        data = coordinator.data
        if not data:
            return

        zone_nbhd   = config_entry.options.get(CONF_ZONE_NEIGHBORHOOD)
        zone_school  = config_entry.options.get(CONF_ZONE_SCHOOL)
        zones_to_check = [(zone_nbhd, "neighborhood"), (zone_school, "school")]
        zones_to_check = [(eid, role) for eid, role in zones_to_check if eid]

        if not zones_to_check:
            return

        actual_sensors_all = (
            hass.data.get(DOMAIN, {}).get(entry_id, {}).get("actual_sensors", {})
        )

        for bus_key, bd in data.items():
            # Only check during an active tracking window with live GPS
            if not bd.tracking_active or bd.latitude is None or bd.longitude is None:
                prev_zone[bus_key] = None
                continue

            current_zone: str | None = None
            for zone_eid, _ in zones_to_check:
                zs = hass.states.get(zone_eid)
                if not zs:
                    continue
                zlat = zs.attributes.get("latitude")
                zlon = zs.attributes.get("longitude")
                zrad = zs.attributes.get("radius", 100)   # metres, HA default 100
                if zlat is None or zlon is None:
                    continue
                if haversine_m(bd.latitude, bd.longitude, zlat, zlon) <= zrad:
                    current_zone = zone_eid
                    break

            last_zone = prev_zone.get(bus_key)
            prev_zone[bus_key] = current_zone

            # Only act on a fresh zone entry (not already inside the same zone)
            if current_zone is None or current_zone == last_zone:
                continue

            actual_sensors = actual_sensors_all.get(bus_key, {})
            now = dt_util.now()

            _LOGGER.debug(
                "Bus %s entered zone %s (trip=%s)", bus_key, current_zone, bd.active_trip
            )

            if current_zone == zone_nbhd:
                if bd.active_trip == "morning":
                    _stamp(actual_sensors, "home_pickup", now)
                elif bd.active_trip == "afternoon":
                    _stamp(actual_sensors, "home_dropoff", now)

            elif current_zone == zone_school:
                if bd.active_trip == "morning":
                    _stamp(actual_sensors, "school_dropoff", now)
                elif bd.active_trip == "afternoon":
                    _stamp(actual_sensors, "school_pickup", now)

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

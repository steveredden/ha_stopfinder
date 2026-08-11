"""DataUpdateCoordinator for the Stopfinder integration.

NOTE: This integration uses the Transfinder Stopfinder mobile API.  These endpoints
are undocumented, not officially supported for third-party use, and may change or
break without notice.  Transfinder provides no SLA or compatibility guarantee for
external consumers of this API.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    _CONF_MINUTES_AFTER_LEGACY,
    _CONF_MINUTES_BEFORE_LEGACY,
    API_BASE,
    CONF_EARLY_HOUR,
    CONF_HALFDAY_HOUR,
    CONF_MINUTES_AFTER_HOME_DROPOFF,
    CONF_MINUTES_AFTER_SCHOOL_DROPOFF,
    CONF_MINUTES_BEFORE_HOME_PICKUP,
    CONF_MINUTES_BEFORE_SCHOOL_PICKUP,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_USER_AGENT,
    CONF_USERNAME,
    DEFAULT_MINUTES_AFTER_HOME_DROPOFF,
    DEFAULT_MINUTES_AFTER_SCHOOL_DROPOFF,
    DEFAULT_MINUTES_BEFORE_HOME_PICKUP,
    DEFAULT_MINUTES_BEFORE_SCHOOL_PICKUP,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_USER_AGENT,
    DOMAIN,
    EARLY_THRESHOLD_HOUR,
    HALFDAY_THRESHOLD_HOUR,
    SCHEDULE_EARLY,
    SCHEDULE_HALFDAY,
    SCHEDULE_NORMAL,
)

_LOGGER = logging.getLogger(__name__)

# Coordinator interval outside of any tracking window
_IDLE_INTERVAL = timedelta(hours=1)


@dataclass
class BusData:
    """All runtime state for one school bus."""

    bus_number: str       # raw number from the API (e.g. "108", "201ABC")
    client_id: str = ""
    data_source_id: str = ""

    # Scheduled times (API value + adjustMinutes, localised)
    home_pickup:    datetime | None = None
    school_dropoff: datetime | None = None
    school_pickup:  datetime | None = None
    home_dropoff:   datetime | None = None

    schedule_type:   str = SCHEDULE_NORMAL
    latitude:        float | None = None
    longitude:       float | None = None
    tracking_active: bool = False
    active_trip:     str | None = None   # "morning" | "afternoon" | None


def bus_display_name(key: str) -> str:
    """Human-readable device name for a bus key.

    "108"   → "Bus 108"
    "108_2" → "Bus 108 (2)"   (two different routes share the same number)
    """
    parts = key.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return f"Bus {parts[0]} ({parts[1]})"
    return f"Bus {key}"


# coordinator.data type alias: bus_number → BusData.
# Empty dict means the API returned no schedules for today (weekend / holiday).
StopfinderCoordinatorData = dict[str, BusData]


class StopfinderCoordinator(DataUpdateCoordinator[StopfinderCoordinatorData]):
    """Polls the Stopfinder API for all students on the account.

    Groups results by bus number.  Each unique bus gets a BusData entry;
    students sharing a bus are merged into one entry.
    """

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._token: str | None = None
        self._auth_headers: dict[str, str] = {}
        self._cached_buses: StopfinderCoordinatorData | None = None
        self._cached_date: str | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=_IDLE_INTERVAL,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _opt(self, key: str, default: Any) -> Any:
        return self._config_entry.options.get(
            key, self._config_entry.data.get(key, default)
        )

    def _edge(self, new_key: str, legacy_key: str, default: int) -> int:
        return int(self._opt(new_key, self._opt(legacy_key, default)))

    def _session_headers(self) -> dict[str, str]:
        ua = self._config_entry.data.get(CONF_USER_AGENT, DEFAULT_USER_AGENT)
        return {"User-Agent": ua, "Content-Type": "application/json"}

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def async_authenticate(self) -> None:
        session = async_get_clientsession(self.hass)
        async with session.post(
            f"{API_BASE}/tokens",
            json={
                "deviceId": "",
                "grantType": "password",
                "username": self._config_entry.data[CONF_USERNAME],
                "password": self._config_entry.data[CONF_PASSWORD],
                "rfApiVersion": "1.1",
            },
            headers=self._session_headers(),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        self._token = data["token"]
        self._auth_headers = {**self._session_headers(), "token": self._token}

    async def _api_get(self, path: str) -> Any:
        if not self._token:
            await self.async_authenticate()
        session = async_get_clientsession(self.hass)
        url = f"{API_BASE}/{path}"
        async with session.get(url, headers=self._auth_headers) as resp:
            if resp.status == 401:
                await self.async_authenticate()
                async with session.get(url, headers=self._auth_headers) as r2:
                    r2.raise_for_status()
                    return await r2.json()
            resp.raise_for_status()
            return await resp.json()

    # ------------------------------------------------------------------
    # Schedule parsing — groups all students by bus number
    # ------------------------------------------------------------------

    def _parse_all_buses(self, raw: list[Any]) -> StopfinderCoordinatorData:
        """Return a BusData dict keyed by unique bus key for every route in raw.

        Two trips with the same bus number AND clientId are the same physical bus
        (e.g. siblings on the same route) and share one entry.  If the same bus
        number appears under a different clientId the key is suffixed (_2, _3 …)
        so each route gets its own device.
        """
        buses: dict[str, BusData] = {}
        halfday_h = int(self._opt(CONF_HALFDAY_HOUR, HALFDAY_THRESHOLD_HOUR))
        early_h   = int(self._opt(CONF_EARLY_HOUR,   EARLY_THRESHOLD_HOUR))

        def _adjust(time_str: str | None, adj: int | None) -> datetime | None:
            if not time_str:
                return None
            naive    = datetime.fromisoformat(time_str.replace("T", " "))
            adjusted = naive + timedelta(minutes=int(adj or 0))
            return dt_util.as_local(adjusted)

        def _key_for(bus_no: str, client_id: str) -> str:
            """Return existing key if same bus, else a suffixed key for a collision."""
            if bus_no not in buses or buses[bus_no].client_id == client_id:
                return bus_no
            n = 2
            while f"{bus_no}_{n}" in buses:
                n += 1
            return f"{bus_no}_{n}"

        def _merge_trip(
            t: dict, client_id: str, data_source_id: str,
            guard_attr: str, pickup_attr: str, dropoff_attr: str,
        ) -> str | None:
            """Merge one trip's pickup/dropoff into its bus; return the bus key.

            The guard attribute keeps the first schedule to claim this bus (e.g.
            siblings on the same route) authoritative — later schedules don't
            overwrite times already set.  Returns None when the trip has no bus.
            """
            bus_no = t.get("busNumber", "")
            if not bus_no:
                return None
            key = _key_for(bus_no, client_id)
            bd  = buses.setdefault(key, BusData(
                bus_number=bus_no,
                client_id=client_id,
                data_source_id=data_source_id,
            ))
            if getattr(bd, guard_attr) is None:
                setattr(bd, pickup_attr,  _adjust(t.get("pickUpTime"),  t.get("adjustMinutes")))
                setattr(bd, dropoff_attr, _adjust(t.get("dropOffTime"), t.get("adjustMinutes")))
            return key

        for student in raw:
            for schedule in student.get("studentSchedules", []):
                client_id      = schedule.get("clientId", "")
                data_source_id = schedule.get("dataSourceId", "")
                trips          = schedule.get("trips", [])
                to_school      = [t for t in trips if t.get("toSchool")]
                from_school    = [t for t in trips if not t.get("toSchool")]

                # Morning trip
                if to_school:
                    _merge_trip(to_school[0], client_id, data_source_id,
                                "home_pickup", "home_pickup", "school_dropoff")

                # Afternoon trip (may be a different bus)
                if from_school:
                    key = _merge_trip(from_school[0], client_id, data_source_id,
                                      "school_pickup", "school_pickup", "home_dropoff")
                    if key is not None:
                        sp = buses[key].school_pickup
                        if sp and buses[key].schedule_type == SCHEDULE_NORMAL:
                            if sp.hour < halfday_h:
                                buses[key].schedule_type = SCHEDULE_HALFDAY
                            elif sp.hour < early_h:
                                buses[key].schedule_type = SCHEDULE_EARLY

        return buses

    # ------------------------------------------------------------------
    # Tracking window — per bus
    # ------------------------------------------------------------------

    def _tracking_window(self, bd: BusData) -> tuple[bool, str | None]:
        """Return (in_window, trip_type) for the current time and this bus."""
        now  = dt_util.now()
        e1   = self._edge(CONF_MINUTES_BEFORE_HOME_PICKUP,   _CONF_MINUTES_BEFORE_LEGACY, DEFAULT_MINUTES_BEFORE_HOME_PICKUP)
        e2   = self._edge(CONF_MINUTES_AFTER_SCHOOL_DROPOFF, _CONF_MINUTES_AFTER_LEGACY,  DEFAULT_MINUTES_AFTER_SCHOOL_DROPOFF)
        e3   = self._edge(CONF_MINUTES_BEFORE_SCHOOL_PICKUP, _CONF_MINUTES_BEFORE_LEGACY, DEFAULT_MINUTES_BEFORE_SCHOOL_PICKUP)
        e4   = self._edge(CONF_MINUTES_AFTER_HOME_DROPOFF,   _CONF_MINUTES_AFTER_LEGACY,  DEFAULT_MINUTES_AFTER_HOME_DROPOFF)

        # Morning : home_pickup − e1  →  school_dropoff + e2
        if bd.home_pickup and bd.school_dropoff:
            if bd.home_pickup - timedelta(minutes=e1) <= now <= bd.school_dropoff + timedelta(minutes=e2):
                return True, "morning"

        # Afternoon : school_pickup − e3  →  home_dropoff + e4
        if bd.school_pickup and bd.home_dropoff:
            if bd.school_pickup - timedelta(minutes=e3) <= now <= bd.home_dropoff + timedelta(minutes=e4):
                return True, "afternoon"

        return False, None

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> StopfinderCoordinatorData:
        today = dt_util.now().date().isoformat()

        try:
            if self._cached_buses is None or self._cached_date != today:
                raw = await self._api_get(f"students?dateStart={today}&dateEnd={today}")
                self._cached_buses = self._parse_all_buses(raw)
                self._cached_date  = today

            buses = self._cached_buses
            if not buses:
                # Weekend / holiday — no schedule
                self.update_interval = _IDLE_INTERVAL
                return {}

            poll_s = int(self._opt(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
            any_active = False

            for bd in buses.values():
                in_window, trip = self._tracking_window(bd)
                bd.tracking_active = in_window
                bd.active_trip     = trip
                bd.latitude        = None
                bd.longitude       = None

                if in_window:
                    any_active = True
                    group = f"{bd.client_id}_{bd.data_source_id}_{bd.bus_number}"
                    gps_raw = await self._api_get(f"gps?groupName={group}")
                    gps = (gps_raw[0] if isinstance(gps_raw, list) else gps_raw) or {}
                    lat = gps.get("latitude")
                    lon = gps.get("longitude")
                    if lat is not None and lon is not None:
                        bd.latitude  = float(lat)
                        bd.longitude = float(lon)

            self.update_interval = (
                timedelta(seconds=poll_s) if any_active else _IDLE_INTERVAL
            )
            return buses

        except Exception as err:
            raise UpdateFailed(f"Stopfinder API error: {err}") from err

    def invalidate_schedule_cache(self) -> None:
        self._cached_buses = None
        self._cached_date  = None

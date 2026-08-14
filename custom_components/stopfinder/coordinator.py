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
    API_BASE,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_USER_AGENT,
    CONF_USERNAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_USER_AGENT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Coordinator interval outside of any tracking window
_IDLE_INTERVAL = timedelta(hours=1)

# How long past the nominal window close to keep tracking when zones are configured
# but the expected arrival hasn't been detected yet (handles late buses).
_EXTEND_HOURS = 2


@dataclass
class BusData:
    """All runtime state for one school bus."""

    bus_number: str       # raw number from the API (e.g. "108", "201ABC")
    client_id: str = ""
    data_source_id: str = ""

    # Scheduled stop times (API value + adjustMinutes, localised)
    home_pickup:    datetime | None = None
    school_dropoff: datetime | None = None
    school_pickup:  datetime | None = None
    home_dropoff:   datetime | None = None

    # Tracking windows derived from startTime/finishTime ± beforeTrip/afterTrip
    morning_window_start:   datetime | None = None
    morning_window_end:     datetime | None = None
    afternoon_window_start: datetime | None = None
    afternoon_window_end:   datetime | None = None

    # Bus stop coordinates (lat, lon) for automatic arrival detection
    home_pickup_stop:    tuple[float, float] | None = None
    school_dropoff_stop: tuple[float, float] | None = None
    school_pickup_stop:  tuple[float, float] | None = None
    home_dropoff_stop:   tuple[float, float] | None = None

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
            # The Stopfinder API returns 203 with a non-JSON body when the token
            # has expired rather than a standard 401, so treat any non-JSON 2xx
            # response as a stale-token signal and re-authenticate.
            if resp.status == 401 or "json" not in (resp.content_type or ""):
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

        def _adjust(time_str: str | None, adj: int | None) -> datetime | None:
            if not time_str:
                return None
            naive    = datetime.fromisoformat(time_str.replace("T", " "))
            adjusted = naive + timedelta(minutes=int(adj or 0))
            return dt_util.as_local(adjusted)

        def _stop(t: dict, y_key: str, x_key: str) -> tuple[float, float] | None:
            """Return (lat, lon) from a trip's stop coordinates, or None."""
            lat = t.get(y_key)
            lon = t.get(x_key)
            if lat is None or lon is None:
                return None
            return (float(lat), float(lon))

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
            pickup_stop_attr: str, dropoff_stop_attr: str,
            window_start_attr: str, window_end_attr: str,
            before_min: int, after_min: int,
        ) -> str | None:
            """Merge one trip into its bus entry; return the bus key.

            The guard attribute keeps the first schedule to claim this bus
            authoritative — later schedules (e.g. siblings on the same route)
            don't overwrite times already set.  Returns None when the trip has
            no bus number.
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
                setattr(bd, pickup_stop_attr,  _stop(t, "pickUpStopYCoord",  "pickUpStopXCoord"))
                setattr(bd, dropoff_stop_attr, _stop(t, "dropOffStopYCoord", "dropOffStopXCoord"))
                start  = _adjust(t.get("startTime"),  0)
                finish = _adjust(t.get("finishTime"), 0)
                if start and finish:
                    setattr(bd, window_start_attr, start  - timedelta(minutes=before_min))
                    setattr(bd, window_end_attr,   finish + timedelta(minutes=after_min))
            return key

        for student in raw:
            before_min = int(student.get("beforeTrip", 0))
            after_min  = int(student.get("afterTrip",  0))

            for schedule in student.get("studentSchedules", []):
                client_id      = schedule.get("clientId", "")
                data_source_id = schedule.get("dataSourceId", "")
                trips          = schedule.get("trips", [])
                to_school      = [t for t in trips if t.get("toSchool")]
                from_school    = [t for t in trips if not t.get("toSchool")]

                # Morning trip
                if to_school:
                    _merge_trip(
                        to_school[0], client_id, data_source_id,
                        guard_attr="home_pickup",
                        pickup_attr="home_pickup",    dropoff_attr="school_dropoff",
                        pickup_stop_attr="home_pickup_stop", dropoff_stop_attr="school_dropoff_stop",
                        window_start_attr="morning_window_start", window_end_attr="morning_window_end",
                        before_min=before_min, after_min=after_min,
                    )

                # Afternoon trip (may be a different bus)
                if from_school:
                    _merge_trip(
                        from_school[0], client_id, data_source_id,
                        guard_attr="school_pickup",
                        pickup_attr="school_pickup",  dropoff_attr="home_dropoff",
                        pickup_stop_attr="school_pickup_stop", dropoff_stop_attr="home_dropoff_stop",
                        window_start_attr="afternoon_window_start", window_end_attr="afternoon_window_end",
                        before_min=before_min, after_min=after_min,
                    )

        return buses

    # ------------------------------------------------------------------
    # Tracking window — per bus
    # ------------------------------------------------------------------

    def _arrival_recorded(self, bus_key: str, trip_point: str) -> bool:
        """True if the actual sensor for this trip point was stamped today."""
        sensor = (
            self.hass.data.get(DOMAIN, {})
            .get(self._config_entry.entry_id, {})
            .get("actual_sensors", {})
            .get(bus_key, {})
            .get(trip_point)
        )
        if sensor is None:
            return False
        val = sensor.native_value
        return val is not None and val.date() == dt_util.now().date()

    def _tracking_window(self, bd: BusData, bus_key: str) -> tuple[bool, str | None]:
        """Return (in_window, trip_type) for the current time and this bus.

        Windows come directly from the API's startTime/finishTime ± the API's
        own beforeTrip/afterTrip offsets.  If the bus hasn't been detected at
        the final stop yet, the window is extended up to _EXTEND_HOURS past the
        nominal close so severely late buses remain visible.
        """
        now = dt_util.now()

        # Morning : morning_window_start → morning_window_end
        if bd.morning_window_start and bd.morning_window_end:
            if bd.morning_window_start <= now <= bd.morning_window_end:
                return True, "morning"
            if (now > bd.morning_window_end
                    and now <= bd.morning_window_end + timedelta(hours=_EXTEND_HOURS)
                    and not self._arrival_recorded(bus_key, "school_dropoff")):
                return True, "morning"

        # Afternoon : afternoon_window_start → afternoon_window_end
        if bd.afternoon_window_start and bd.afternoon_window_end:
            if bd.afternoon_window_start <= now <= bd.afternoon_window_end:
                return True, "afternoon"
            if (now > bd.afternoon_window_end
                    and now <= bd.afternoon_window_end + timedelta(hours=_EXTEND_HOURS)
                    and not self._arrival_recorded(bus_key, "home_dropoff")):
                return True, "afternoon"

        return False, None

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> StopfinderCoordinatorData:
        today = dt_util.now().date().isoformat()

        # Schedule fetch is fatal — no schedule means no data.
        try:
            if self._cached_buses is None or self._cached_date != today:
                raw = await self._api_get(f"students?dateStart={today}&dateEnd={today}")
                self._cached_buses = self._parse_all_buses(raw)
                self._cached_date  = today
        except Exception as err:
            raise UpdateFailed(f"Stopfinder schedule fetch error: {err}") from err

        buses = self._cached_buses
        if not buses:
            # Weekend / holiday — no schedule
            self.update_interval = _IDLE_INTERVAL
            return {}

        poll_s = int(self._opt(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        any_active = False

        for bus_key, bd in buses.items():
            in_window, trip = self._tracking_window(bd, bus_key)
            bd.tracking_active = in_window
            bd.active_trip     = trip

            if not in_window:
                # Only clear coordinates when outside the window so a transient
                # GPS fetch error mid-ride doesn't blank the last known position.
                bd.latitude  = None
                bd.longitude = None
                continue

            any_active = True
            group = f"{bd.client_id}_{bd.data_source_id}_{bd.bus_number}"
            try:
                gps_raw = await self._api_get(f"gps?groupName={group}")
                gps = (gps_raw[0] if isinstance(gps_raw, list) else gps_raw) or {}
                lat = gps.get("latitude")
                lon = gps.get("longitude")
                if lat is not None and lon is not None:
                    bd.latitude  = float(lat)
                    bd.longitude = float(lon)
                else:
                    bd.latitude  = None
                    bd.longitude = None
            except Exception as err:
                # GPS errors are non-fatal: log and keep the last known position.
                _LOGGER.warning("GPS fetch failed for bus %s, keeping last position: %s", bus_key, err)

        self.update_interval = (
            timedelta(seconds=poll_s) if any_active else _IDLE_INTERVAL
        )
        return buses

    def invalidate_schedule_cache(self) -> None:
        self._cached_buses = None
        self._cached_date  = None

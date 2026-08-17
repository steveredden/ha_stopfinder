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
    CONF_MINUTES_BEFORE,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_USER_AGENT,
    CONF_USERNAME,
    DEFAULT_MINUTES_BEFORE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_USER_AGENT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = timedelta(minutes=10)
_EXTEND_HOURS = 2


@dataclass
class StudentData:
    """All runtime state for one student."""

    rider_id: int
    first_name: str
    last_name: str
    grade: str = ""
    school: str = ""
    client_id: str = ""
    data_source_id: str = ""

    # Bus number per direction — may differ between AM and PM routes
    morning_bus_number:   str = ""
    afternoon_bus_number: str = ""

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

    # Bus stop coordinates (lat, lon) and display names for arrival detection
    home_pickup_stop:         tuple[float, float] | None = None
    school_dropoff_stop:      tuple[float, float] | None = None
    school_pickup_stop:       tuple[float, float] | None = None
    home_dropoff_stop:        tuple[float, float] | None = None
    home_pickup_stop_name:    str | None = None
    school_dropoff_stop_name: str | None = None
    school_pickup_stop_name:  str | None = None
    home_dropoff_stop_name:   str | None = None

    latitude:        float | None = None
    longitude:       float | None = None
    tracking_active: bool = False
    active_trip:     str | None = None   # "morning" | "afternoon" | None


def student_display_name(first: str, last: str) -> str:
    """Human-readable device name for a student."""
    return f"{first} Stopfinder"


# coordinator.data type alias: str(rider_id) → StudentData.
# Empty dict means the API returned no schedules for today (weekend / holiday).
StopfinderCoordinatorData = dict[str, StudentData]


class StopfinderCoordinator(DataUpdateCoordinator[StopfinderCoordinatorData]):
    """Polls the Stopfinder API for all students on the account.

    Indexes results by rider_id.  Each student gets their own StudentData entry;
    bus number is resolved dynamically from the student's daily trip schedule.
    """

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._token: str | None = None
        self._auth_headers: dict[str, str] = {}
        self._cached_students: StopfinderCoordinatorData | None = None
        self._cached_date: str | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=_HEARTBEAT_INTERVAL,
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
    # Schedule parsing — one StudentData per rider_id
    # ------------------------------------------------------------------

    def _parse_all_students(self, raw: list[Any]) -> StopfinderCoordinatorData:
        """Return a StudentData dict keyed by str(rider_id).

        Each student gets their own entry; bus number is taken directly from
        the student's trips for that day so it reflects any substitutions.
        """
        students: dict[str, StudentData] = {}

        def _adjust(time_str: str | None, adj: int | None) -> datetime | None:
            if not time_str:
                return None
            naive    = datetime.fromisoformat(time_str.replace("T", " "))
            adjusted = naive + timedelta(minutes=int(adj or 0))
            return dt_util.as_local(adjusted)

        def _stop(t: dict, y_key: str, x_key: str) -> tuple[float, float] | None:
            lat = t.get(y_key)
            lon = t.get(x_key)
            if lat is None or lon is None:
                return None
            return (float(lat), float(lon))

        cfg = self.config_entry
        extra_before = int(
            cfg.options.get(CONF_MINUTES_BEFORE,
            cfg.data.get(CONF_MINUTES_BEFORE, DEFAULT_MINUTES_BEFORE))
        )

        for day in raw:
            for schedule in day.get("studentSchedules", []):
                rider_id = schedule.get("riderId")
                if not rider_id:
                    continue
                key = str(rider_id)

                sd = students.setdefault(key, StudentData(
                    rider_id=int(rider_id),
                    first_name=schedule.get("firstName", ""),
                    last_name=schedule.get("lastName", ""),
                    grade=schedule.get("grade", ""),
                    school=schedule.get("school", ""),
                    client_id=str(schedule.get("clientId", "")),
                    data_source_id=str(schedule.get("dataSourceId", "")),
                ))

                before_min = int(schedule.get("beforeTrip", 0)) + extra_before
                after_min  = int(schedule.get("afterTrip",  0))

                trips       = schedule.get("trips", [])
                to_school   = [t for t in trips if t.get("toSchool")]
                from_school = [t for t in trips if not t.get("toSchool")]

                if to_school:
                    t = to_school[0]
                    sd.morning_bus_number  = t.get("busNumber", "")
                    sd.home_pickup         = _adjust(t.get("pickUpTime"),  t.get("adjustMinutes"))
                    sd.school_dropoff      = _adjust(t.get("dropOffTime"), t.get("adjustMinutes"))
                    sd.home_pickup_stop    = _stop(t, "pickUpStopYCoord",  "pickUpStopXCoord")
                    sd.school_dropoff_stop = _stop(t, "dropOffStopYCoord", "dropOffStopXCoord")
                    sd.home_pickup_stop_name    = t.get("pickUpStopName")
                    sd.school_dropoff_stop_name = t.get("dropOffStopName")
                    start  = _adjust(t.get("startTime"),  0)
                    finish = _adjust(t.get("finishTime"), 0)
                    if start and finish:
                        sd.morning_window_start = start  - timedelta(minutes=before_min)
                        sd.morning_window_end   = finish + timedelta(minutes=after_min)

                if from_school:
                    t = from_school[0]
                    sd.afternoon_bus_number = t.get("busNumber", "")
                    sd.school_pickup        = _adjust(t.get("pickUpTime"),  t.get("adjustMinutes"))
                    sd.home_dropoff         = _adjust(t.get("dropOffTime"), t.get("adjustMinutes"))
                    sd.school_pickup_stop   = _stop(t, "pickUpStopYCoord",  "pickUpStopXCoord")
                    sd.home_dropoff_stop    = _stop(t, "dropOffStopYCoord", "dropOffStopXCoord")
                    sd.school_pickup_stop_name = t.get("pickUpStopName")
                    sd.home_dropoff_stop_name  = t.get("dropOffStopName")
                    start  = _adjust(t.get("startTime"),  0)
                    finish = _adjust(t.get("finishTime"), 0)
                    if start and finish:
                        sd.afternoon_window_start = start  - timedelta(minutes=before_min)
                        sd.afternoon_window_end   = finish + timedelta(minutes=after_min)

        return students

    # ------------------------------------------------------------------
    # Tracking window — per student
    # ------------------------------------------------------------------

    def _arrival_recorded(self, student_key: str, trip_point: str) -> bool:
        """True if the actual sensor for this trip point was stamped today."""
        sensor = (
            self.hass.data.get(DOMAIN, {})
            .get(self._config_entry.entry_id, {})
            .get("actual_sensors", {})
            .get(student_key, {})
            .get(trip_point)
        )
        if sensor is None:
            return False
        val = sensor.native_value
        return val is not None and val.date() == dt_util.now().date()

    def _tracking_window(self, sd: StudentData, student_key: str) -> tuple[bool, str | None]:
        """Return (in_window, trip_type) for the current time and this student."""
        now = dt_util.now()

        if sd.morning_window_start and sd.morning_window_end:
            if sd.morning_window_start <= now <= sd.morning_window_end:
                return True, "morning"
            if (now > sd.morning_window_end
                    and now <= sd.morning_window_end + timedelta(hours=_EXTEND_HOURS)
                    and not self._arrival_recorded(student_key, "school_dropoff")):
                return True, "morning"

        if sd.afternoon_window_start and sd.afternoon_window_end:
            if sd.afternoon_window_start <= now <= sd.afternoon_window_end:
                return True, "afternoon"
            if (now > sd.afternoon_window_end
                    and now <= sd.afternoon_window_end + timedelta(hours=_EXTEND_HOURS)
                    and not self._arrival_recorded(student_key, "home_dropoff")):
                return True, "afternoon"

        return False, None

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> StopfinderCoordinatorData:
        today = dt_util.now().date().isoformat()

        try:
            if self._cached_students is None or self._cached_date != today:
                raw = await self._api_get(f"students?dateStart={today}&dateEnd={today}")
                self._cached_students = self._parse_all_students(raw)
                self._cached_date     = today
        except Exception as err:
            raise UpdateFailed(f"Stopfinder schedule fetch error: {err}") from err

        students = self._cached_students
        if not students:
            self.update_interval = _HEARTBEAT_INTERVAL
            return {}

        poll_s = int(self._opt(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        any_active = False

        for student_key, sd in students.items():
            in_window, trip = self._tracking_window(sd, student_key)
            sd.tracking_active = in_window
            sd.active_trip     = trip

            if not in_window:
                sd.latitude  = None
                sd.longitude = None
                continue

            any_active = True
            bus_number = sd.morning_bus_number if trip == "morning" else sd.afternoon_bus_number
            group = f"{sd.client_id}_{sd.data_source_id}_{bus_number}"
            try:
                gps_raw = await self._api_get(f"gps?groupName={group}")
                gps = (gps_raw[0] if isinstance(gps_raw, list) else gps_raw) or {}
                lat = gps.get("latitude")
                lon = gps.get("longitude")
                if lat is not None and lon is not None:
                    sd.latitude  = float(lat)
                    sd.longitude = float(lon)
                else:
                    sd.latitude  = None
                    sd.longitude = None
            except Exception as err:
                _LOGGER.warning(
                    "GPS fetch failed for student %s (bus %s), keeping last position: %s",
                    student_key, bus_number, err,
                )

        self.update_interval = (
            timedelta(seconds=poll_s) if any_active else _HEARTBEAT_INTERVAL
        )
        return students

    def invalidate_schedule_cache(self) -> None:
        self._cached_students = None
        self._cached_date     = None

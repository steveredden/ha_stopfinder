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
    CONF_EARLY_HOUR,
    CONF_HALFDAY_HOUR,
    CONF_MINUTES_AFTER,
    CONF_MINUTES_BEFORE,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_USER_AGENT,
    CONF_USERNAME,
    DEFAULT_MINUTES_AFTER,
    DEFAULT_MINUTES_BEFORE,
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


@dataclass
class StopfinderData:
    """All parsed state from one API refresh cycle."""

    # Scheduled times (from student schedule API, adjusted by adjustMinutes)
    home_pickup: datetime | None = None
    school_dropoff: datetime | None = None
    school_pickup: datetime | None = None
    home_dropoff: datetime | None = None

    # Bus / routing meta
    bus_number: str | None = None
    client_id: str | None = None
    data_source_id: str | None = None

    # Derived
    schedule_type: str = SCHEDULE_NORMAL   # normal | early | halfday
    latitude: float | None = None
    longitude: float | None = None
    tracking_active: bool = False
    active_trip: str | None = None          # "morning" | "afternoon" | None
    no_school: bool = False                 # True on weekends/holidays


class StopfinderCoordinator(DataUpdateCoordinator[StopfinderData]):
    """Polls the Stopfinder API; caches auth token and daily schedule."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._token: str | None = None
        self._auth_headers: dict[str, str] = {}
        self._cached_schedule: dict[str, Any] | None = None
        self._cached_date: str | None = None

        poll_seconds = self._opt(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=int(poll_seconds)),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _opt(self, key: str, default: Any) -> Any:
        """Read from options first, then data, then fall back to default."""
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
        """Authenticate and cache the bearer token."""
        session = async_get_clientsession(self.hass)
        payload = {
            "deviceId": "",
            "grantType": "password",
            "username": self._config_entry.data[CONF_USERNAME],
            "password": self._config_entry.data[CONF_PASSWORD],
            "rfApiVersion": "1.1",
        }
        async with session.post(
            f"{API_BASE}/tokens",
            json=payload,
            headers=self._session_headers(),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        self._token = data["token"]
        self._auth_headers = {**self._session_headers(), "token": self._token}

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    async def _api_get(self, path: str) -> Any:
        """Authenticated GET; re-authenticates once on 401."""
        if not self._token:
            await self.async_authenticate()

        session = async_get_clientsession(self.hass)
        url = f"{API_BASE}/{path}"

        async with session.get(url, headers=self._auth_headers) as resp:
            if resp.status == 401:
                _LOGGER.debug("Token expired – re-authenticating")
                await self.async_authenticate()
                async with session.get(url, headers=self._auth_headers) as retry:
                    retry.raise_for_status()
                    return await retry.json()
            resp.raise_for_status()
            return await resp.json()

    # ------------------------------------------------------------------
    # Schedule parsing
    # ------------------------------------------------------------------

    def _parse_schedule(self, raw: list[Any]) -> dict[str, Any]:
        """Extract and time-adjust trip timestamps from the students API response."""
        if not raw or not raw[0].get("studentSchedules"):
            return {}

        schedules = raw[0]["studentSchedules"][0]
        trips = schedules.get("trips", [])
        to_school = [t for t in trips if t.get("toSchool")]
        from_school = [t for t in trips if not t.get("toSchool")]

        def adjust(time_str: str | None, adj_min: int | None) -> datetime | None:
            if not time_str:
                return None
            naive = datetime.fromisoformat(time_str.replace("T", " "))
            adjusted = naive + timedelta(minutes=int(adj_min or 0))
            return dt_util.as_local(adjusted)

        result: dict[str, Any] = {
            "home_pickup": None,
            "school_dropoff": None,
            "school_pickup": None,
            "home_dropoff": None,
            "bus_number": None,
            "client_id": schedules.get("clientId"),
            "data_source_id": schedules.get("dataSourceId"),
            "schedule_type": SCHEDULE_NORMAL,
        }

        if to_school:
            t = to_school[0]
            result["home_pickup"] = adjust(t.get("pickUpTime"), t.get("adjustMinutes"))
            result["school_dropoff"] = adjust(t.get("dropOffTime"), t.get("adjustMinutes"))
            result["bus_number"] = t.get("busNumber")

        if from_school:
            t = from_school[0]
            result["school_pickup"] = adjust(t.get("pickUpTime"), t.get("adjustMinutes"))
            result["home_dropoff"] = adjust(t.get("dropOffTime"), t.get("adjustMinutes"))
            if not result["bus_number"]:
                result["bus_number"] = t.get("busNumber")

            sp: datetime | None = result["school_pickup"]
            if sp:
                halfday_h = int(self._opt(CONF_HALFDAY_HOUR, HALFDAY_THRESHOLD_HOUR))
                early_h = int(self._opt(CONF_EARLY_HOUR, EARLY_THRESHOLD_HOUR))
                if sp.hour < halfday_h:
                    result["schedule_type"] = SCHEDULE_HALFDAY
                elif sp.hour < early_h:
                    result["schedule_type"] = SCHEDULE_EARLY

        return result

    # ------------------------------------------------------------------
    # Tracking window
    # ------------------------------------------------------------------

    def _tracking_window(self, sched: dict[str, Any]) -> tuple[bool, str | None]:
        """Return (in_window, trip_type) given the current local time."""
        now = dt_util.now()
        mb = int(self._opt(CONF_MINUTES_BEFORE, DEFAULT_MINUTES_BEFORE))
        ma = int(self._opt(CONF_MINUTES_AFTER, DEFAULT_MINUTES_AFTER))

        hp: datetime | None = sched.get("home_pickup")
        sd: datetime | None = sched.get("school_dropoff")
        sp: datetime | None = sched.get("school_pickup")
        hd: datetime | None = sched.get("home_dropoff")

        if hp and sd:
            if hp - timedelta(minutes=mb) <= now <= sd + timedelta(minutes=ma):
                return True, "morning"
        if sp and hd:
            if sp - timedelta(minutes=mb) <= now <= hd + timedelta(minutes=ma):
                return True, "afternoon"

        return False, None

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> StopfinderData:
        data = StopfinderData()
        today = dt_util.now().date().isoformat()

        try:
            # Refresh schedule once per calendar day
            if self._cached_schedule is None or self._cached_date != today:
                raw = await self._api_get(f"students?dateStart={today}&dateEnd={today}")
                self._cached_schedule = self._parse_schedule(raw)
                self._cached_date = today

            sched = self._cached_schedule
            if not sched:
                data.no_school = True
                return data

            data.home_pickup = sched.get("home_pickup")
            data.school_dropoff = sched.get("school_dropoff")
            data.school_pickup = sched.get("school_pickup")
            data.home_dropoff = sched.get("home_dropoff")
            data.bus_number = sched.get("bus_number")
            data.client_id = sched.get("client_id")
            data.data_source_id = sched.get("data_source_id")
            data.schedule_type = sched.get("schedule_type", SCHEDULE_NORMAL)

            in_window, trip = self._tracking_window(sched)
            data.tracking_active = in_window
            data.active_trip = trip

            if in_window and sched.get("client_id") and sched.get("bus_number"):
                group = (
                    f"{sched['client_id']}_"
                    f"{sched['data_source_id']}_"
                    f"{sched['bus_number']}"
                )
                gps_raw = await self._api_get(f"gps?groupName={group}")

                # API may return a single object or a list
                if isinstance(gps_raw, list):
                    gps = gps_raw[0] if gps_raw else {}
                elif isinstance(gps_raw, dict):
                    gps = gps_raw
                else:
                    gps = {}

                lat = gps.get("latitude")
                lon = gps.get("longitude")
                if lat is not None and lon is not None:
                    data.latitude = float(lat)
                    data.longitude = float(lon)

        except Exception as err:
            raise UpdateFailed(f"Stopfinder API error: {err}") from err

        return data

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def invalidate_schedule_cache(self) -> None:
        """Force a fresh schedule fetch on the next update cycle."""
        self._cached_schedule = None
        self._cached_date = None

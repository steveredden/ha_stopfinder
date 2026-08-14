"""Constants for the Stopfinder integration."""

DOMAIN = "stopfinder"

# Config / options keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USER_AGENT = "user_agent"
CONF_POLL_INTERVAL = "poll_interval"

# API
API_BASE = "https://stopfinderapi.transfinder.com/StopfinderAPI"

# Defaults
DEFAULT_USER_AGENT = "Stopfinder/1.0 HomeAssistant"
DEFAULT_POLL_INTERVAL = 5   # seconds, used inside tracking windows

# Radius (metres) used for Haversine proximity check against API stop coordinates.
STOP_RADIUS_M = 100

# A stop arrival is only stamped when the current time is within this many
# minutes of the scheduled stop time.  Prevents an earlier bus run to the
# same coordinates (e.g. early-dismissal middle school) from being counted
# as this student's trip event.
STOP_TIME_GUARD_MIN = 30

PLATFORMS = ["device_tracker", "sensor"]

# Icons per trip-point
TRIP_ICONS: dict[str, str] = {
    "home_pickup":    "mdi:home",
    "school_dropoff": "mdi:school",
    "school_pickup":  "mdi:school",
    "home_dropoff":   "mdi:home",
}
TRIP_ACTUAL_ICONS: dict[str, str] = {
    "home_pickup":    "mdi:home-clock-outline",
    "school_dropoff": "mdi:school-outline",
    "school_pickup":  "mdi:school-outline",
    "home_dropoff":   "mdi:home-clock-outline",
}

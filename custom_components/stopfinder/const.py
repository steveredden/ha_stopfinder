"""Constants for the Stopfinder integration."""

DOMAIN = "stopfinder"

# Config / options keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USER_AGENT = "user_agent"
CONF_STUDENT_LABEL = "student_label"
CONF_POLL_INTERVAL = "poll_interval"
CONF_MINUTES_BEFORE = "minutes_before"
CONF_MINUTES_AFTER = "minutes_after"
CONF_HALFDAY_HOUR = "halfday_hour"
CONF_EARLY_HOUR = "early_hour"
CONF_ZONE_NEIGHBORHOOD = "zone_neighborhood"
CONF_ZONE_SCHOOL = "zone_school"

# API
API_BASE = "https://stopfinderapi.transfinder.com/StopfinderAPI"

# Defaults
DEFAULT_POLL_INTERVAL = 5   # seconds
DEFAULT_MINUTES_BEFORE = 15
DEFAULT_MINUTES_AFTER = 15
DEFAULT_USER_AGENT = "Stopfinder/1.0 HomeAssistant"

# Schedule type values
SCHEDULE_NORMAL = "normal"
SCHEDULE_EARLY = "early"
SCHEDULE_HALFDAY = "halfday"

# Default thresholds for early-release detection (24-hour, matches original Node-RED logic).
# Both are user-configurable per-entry via CONF_HALFDAY_HOUR / CONF_EARLY_HOUR.
HALFDAY_THRESHOLD_HOUR = 13   # pickup before 1 pm  → half day
EARLY_THRESHOLD_HOUR = 14     # pickup before 2 pm  → early release

PLATFORMS = ["device_tracker", "sensor"]

# Icons per trip-point
TRIP_ICONS: dict[str, str] = {
    "home_pickup": "mdi:home",
    "school_dropoff": "mdi:school",
    "school_pickup": "mdi:school",
    "home_dropoff": "mdi:home",
}
TRIP_ACTUAL_ICONS: dict[str, str] = {
    "home_pickup": "mdi:home-clock-outline",
    "school_dropoff": "mdi:school-outline",
    "school_pickup": "mdi:school-outline",
    "home_dropoff": "mdi:home-clock-outline",
}

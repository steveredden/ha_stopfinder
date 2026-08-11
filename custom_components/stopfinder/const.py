"""Constants for the Stopfinder integration."""

DOMAIN = "stopfinder"

# Config / options keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USER_AGENT = "user_agent"
CONF_POLL_INTERVAL = "poll_interval"
CONF_HALFDAY_HOUR = "halfday_hour"
CONF_EARLY_HOUR = "early_hour"
CONF_ZONE_NEIGHBORHOOD = "zone_neighborhood"
CONF_ZONE_SCHOOL = "zone_school"

# Four independent tracking-window boundary controls (one per trip edge).
# Morning window:   home_pickup  - EDGE1  →  school_dropoff + EDGE2
# Afternoon window: school_pickup - EDGE3  →  home_dropoff   + EDGE4
CONF_MINUTES_BEFORE_HOME_PICKUP   = "minutes_before_home_pickup"    # edge 1
CONF_MINUTES_AFTER_SCHOOL_DROPOFF = "minutes_after_school_dropoff"  # edge 2
CONF_MINUTES_BEFORE_SCHOOL_PICKUP = "minutes_before_school_pickup"  # edge 3
CONF_MINUTES_AFTER_HOME_DROPOFF   = "minutes_after_home_dropoff"    # edge 4

# Legacy unified keys kept only for migration fall-through in coordinator._opt()
_CONF_MINUTES_BEFORE_LEGACY = "minutes_before"
_CONF_MINUTES_AFTER_LEGACY  = "minutes_after"

# API
API_BASE = "https://stopfinderapi.transfinder.com/StopfinderAPI"

# Defaults
DEFAULT_POLL_INTERVAL = 5   # seconds, used inside tracking windows
DEFAULT_MINUTES_BEFORE_HOME_PICKUP   = 15
DEFAULT_MINUTES_AFTER_SCHOOL_DROPOFF = 15
DEFAULT_MINUTES_BEFORE_SCHOOL_PICKUP = 15
DEFAULT_MINUTES_AFTER_HOME_DROPOFF   = 15

# Schedule type values
SCHEDULE_NORMAL  = "normal"
SCHEDULE_EARLY   = "early"
SCHEDULE_HALFDAY = "halfday"

# Default thresholds for early-release detection (24-hour, matches original Node-RED logic).
# Both are user-configurable per-entry via CONF_HALFDAY_HOUR / CONF_EARLY_HOUR.
HALFDAY_THRESHOLD_HOUR = 13   # pickup before 1 pm  → half day
EARLY_THRESHOLD_HOUR   = 14   # pickup before 2 pm  → early release

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

# Stopfinder Bus Tracker

> **⚠️ Unofficial API**  Transfinder Stopfinder endpoints are undocumented, unsupported for third-party use, and may break without notice.

HACS integration that tracks school buses via the Transfinder Stopfinder mobile API.

## Features

- **One device per bus** — each unique bus number on the account gets its own HA device. Students sharing a bus are merged into one device; same bus number on a different route gets a `(2)` suffix.
- **Live GPS** — `device_tracker.bus_<number>` updates during active tracking windows; HA handles zone detection natively from the coordinates.
- **Scheduled time sensors** — four per bus (`home_pickup`, `school_dropoff`, `school_pickup`, `home_dropoff`), adjusted by the API's `adjustMinutes` offset. Always visible; uses cached data so they never go unavailable due to a transient poll failure.
- **Actual arrival sensors** — four matching `*_actual` sensors, stamped automatically when the bus GPS enters a configured zone. Persisted across restarts (restored if date matches today); reset at 00:05 each morning.
- **Schedule type** — `normal` / `early` / `halfday`, derived automatically from the afternoon school-pickup time vs. configurable hour thresholds (defaults: half day < 1 PM, early release < 2 PM).
- **Smart polling** — GPS is fetched only within four independently configurable tracking windows (before/after each pickup and dropoff event). Outside windows the coordinator polls hourly to keep schedules current. If zones are configured and the bus hasn't been detected yet, the window automatically extends up to 2 hours past the nominal close — late buses remain visible without manual intervention.
- **GPS zone proximity detection** — zone entry is detected by Haversine distance against the zone's center + radius, avoiding the HA entity-registry timing race that afflicts state-listener approaches.
- **No personal data stored** — devices and entities are named by bus number only.

## Installation

1. HACS → Integrations → ⋮ → Custom repositories → add `https://github.com/steveredden/ha_stopfinder` (Integration)
2. Install **Stopfinder**, restart HA
3. Settings → Devices & Services → Add Integration → **Stopfinder**

## Configuration

**Step 1 – Credentials:** email, password, optional User-Agent.

**Step 2 – Preferences** (apply to all buses on the account):

| Setting | Default | Description |
|---|---|---|
| Poll Interval | 5 s (1–300 s) | GPS fetch rate inside a tracking window |
| Before Home Pickup | 25 min | Morning window opens this many minutes before scheduled pickup |
| After School Dropoff | 5 min | Morning window closes this many minutes after scheduled dropoff |
| Before School Pickup | 25 min | Afternoon window opens this many minutes before scheduled pickup |
| After Home Dropoff | 5 min | Afternoon window closes this many minutes after scheduled dropoff |
| Half-Day Threshold | 13 (1 PM) | School pickup before this hour → half day |
| Early Release Threshold | 14 (2 PM) | School pickup before this hour → early release |

**Options (post-install Configure button):** all of the above, plus optional zone selectors for automatic actual-sensor stamping.

## Entities

For bus number `108`:

| Entity | Description |
|---|---|
| `device_tracker.bus_108` | Live GPS; `bus_number`, `schedule_type`, `tracking_active`, `active_trip` as attributes |
| `sensor.bus_108_home_pickup` | Scheduled morning pickup |
| `sensor.bus_108_school_dropoff` | Scheduled morning school dropoff |
| `sensor.bus_108_school_pickup` | Scheduled afternoon school pickup |
| `sensor.bus_108_home_dropoff` | Scheduled afternoon home dropoff |
| `sensor.bus_108_home_pickup_actual` | Actual morning pickup timestamp |
| `sensor.bus_108_school_dropoff_actual` | Actual morning school dropoff timestamp |
| `sensor.bus_108_school_pickup_actual` | Actual afternoon school pickup timestamp |
| `sensor.bus_108_home_dropoff_actual` | Actual afternoon home dropoff timestamp |
| `sensor.bus_108_schedule_type` | `normal` / `early` / `halfday` |

Bus devices appear in HA the first time a school-day schedule is fetched. Installing on a weekend defers device creation to the first weekday morning hourly poll.

## Dashboard

A ready-made dashboard YAML (map with history trail, schedule vs. actual tables) is included at [`docs/dashboard.yaml`](docs/dashboard.yaml). Replace `BUS_NUMBER` with your bus number(s) and import via the HA Raw Configuration Editor.

## License

[MIT](LICENSE)

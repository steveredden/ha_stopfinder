# Stopfinder Bus Tracker

> **⚠️ Unofficial API**  Transfinder Stopfinder endpoints are undocumented, unsupported for third-party use, and may break without notice.

HACS integration that tracks school buses via the Transfinder Stopfinder mobile API.

## Features

- **One device per bus** — each unique bus number on the account gets its own HA device. Students sharing a bus are merged into one device; same bus number on a different route gets a `(2)` suffix.
- **Live GPS** — `device_tracker.bus_<number>` updates during active tracking windows; HA handles zone detection natively from the coordinates.
- **Scheduled time sensors** — four per bus (`home_pickup`, `school_dropoff`, `school_pickup`, `home_dropoff`), adjusted by the API's `adjustMinutes` offset. Always visible; uses cached data so they never go unavailable due to a transient poll failure.
- **Actual arrival sensors** — four matching `*_actual` sensors, stamped automatically when the bus GPS enters the bus-stop radius. Stop coordinates come directly from the API — no manual zone setup required. Persisted across restarts (restored if date matches today); reset at 00:05 each morning.
- **API-derived tracking windows** — windows come from the API's own `startTime`/`finishTime` ± `beforeTrip`/`afterTrip` offsets, so they always match the operator's intent. Outside windows the coordinator polls hourly to keep schedules current.
- **Automatic late-bus extension** — if the bus hasn't been detected at the final stop yet, the window extends up to 2 hours past the nominal close so severely late buses stay visible without any intervention.
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

Tracking windows and stop proximity detection are derived automatically from the API schedule — no zones or manual window configuration needed.

**Options (post-install Configure button):** poll interval.

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
Bus devices appear in HA the first time a school-day schedule is fetched. Installing on a weekend defers device creation to the first weekday morning hourly poll.

## Dashboard

A ready-made dashboard YAML (map with history trail, schedule vs. actual tables) is included at [`docs/dashboard.yaml`](docs/dashboard.yaml). Replace `BUS_NUMBER` with your bus number(s) and import via the HA Raw Configuration Editor.

## License

[MIT](LICENSE)

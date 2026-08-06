# Stopfinder Bus Tracker

A Home Assistant HACS integration that tracks your child's school bus in real time via the Transfinder Stopfinder mobile API.

> **⚠️ Unofficial API disclaimer**
> This integration reverse-engineers the undocumented REST API used by the Transfinder Stopfinder mobile app. These endpoints are **not officially supported for third-party use** and carry no compatibility guarantee. Transfinder may change, rate-limit, or remove them at any time without notice. Use at your own risk.

## Features

- **Live GPS map** – a `device_tracker` entity plots the bus position on any HA map card; HA handles zone detection automatically.
- **Scheduled time sensors** – four sensors show the API-scheduled times for Home Pickup, School Dropoff, School Pickup, and Home Dropoff, including the day's `adjustMinutes` offset.
- **Actual arrival sensors** – four matching sensors record when the bus *actually* arrived at each location. They persist across HA restarts, reset automatically at 00:05 each morning, and can be stamped automatically via zone detection (see Options).
- **Schedule-type sensor** – automatically detects **Normal**, **Early Release**, and **Half Day** schedules by comparing the afternoon school-pickup time against configurable hour thresholds.
- **Smart polling window** – GPS is only fetched during configurable windows around pickup and dropoff times, reducing unnecessary API calls.

## Installation (HACS)

1. In HACS → **Integrations** → ⋮ → **Custom repositories**, add `https://github.com/steveredden/ha_stopfinder` (category: **Integration**).
2. Search for **Stopfinder** and install.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration → Stopfinder**.

## Configuration

### Step 1 – Credentials

| Field | Required | Description |
|---|---|---|
| Email Address | ✓ | Your Stopfinder login email |
| Password | ✓ | Your Stopfinder password |
| User Agent | | Custom HTTP User-Agent (leave as default) |

### Step 2 – Tracking Preferences

| Field | Default | Description |
|---|---|---|
| **Student Label** | (from API) | Name used for the device and entity IDs (e.g. `Jack`). **Choose carefully** — this determines the entity ID prefix and cannot be changed without re-adding the integration. |
| Poll Interval | 5 s | How often GPS is fetched during a tracking window |
| Start Tracking Before Pickup | 15 min | Open the GPS window this many minutes before scheduled pickup |
| Stop Tracking After Dropoff | 15 min | Close the GPS window this many minutes after scheduled dropoff |
| Half-Day Threshold | 13 (1 PM) | School pickup hour (24h) **below which** the schedule is flagged as a half day |
| Early Release Threshold | 14 (2 PM) | School pickup hour (24h) **below which** the schedule is flagged as early release (must be > half-day threshold) |

## Options (post-install)

Access via the integration's **Configure** button. Includes all preferences from Step 2 plus:

| Field | Description |
|---|---|
| Neighborhood / Pickup Zone | Optional HA `zone` entity at the bus stop. When the bus enters it the integration stamps **Home Pickup Actual** (morning) or **Home Dropoff Actual** (afternoon) automatically. |
| School Zone | Optional HA `zone` entity at the school. Stamps **School Dropoff Actual** (morning) or **School Pickup Actual** (afternoon). |

## Created Entities

Entity IDs are based on the **Student Label** you set during configuration. For a label of `Jack`:

| Entity | Type | Description |
|---|---|---|
| `device_tracker.jack` | Device Tracker | Live GPS position; bus number shown as attribute |
| `sensor.jack_home_pickup` | Sensor (timestamp) | Scheduled home pickup time |
| `sensor.jack_school_dropoff` | Sensor (timestamp) | Scheduled school dropoff time |
| `sensor.jack_school_pickup` | Sensor (timestamp) | Scheduled school pickup time |
| `sensor.jack_home_dropoff` | Sensor (timestamp) | Scheduled home dropoff time |
| `sensor.jack_home_pickup_actual` | Sensor (timestamp) | Actual home pickup time |
| `sensor.jack_school_dropoff_actual` | Sensor (timestamp) | Actual school dropoff time |
| `sensor.jack_school_pickup_actual` | Sensor (timestamp) | Actual school pickup time |
| `sensor.jack_home_dropoff_actual` | Sensor (timestamp) | Actual home dropoff time |
| `sensor.jack_schedule_type` | Sensor (enum) | `normal` / `early` / `halfday` |

> **Bus number changes:** Because entity IDs are based on the student label (not the bus number), a bus reassignment has no effect on your entity IDs or automations. The current bus number is always visible as an attribute on `device_tracker.jack`.

## Example Dashboard

See [`docs/example-stopfinder-dashboard.yaml`](docs/example-stopfinder-dashboard.yaml) for a ready-made dashboard card showing the bus on a map alongside the scheduled and actual time tables.

## Example Automation (TTS alert)

```yaml
trigger:
  - platform: zone
    entity_id: device_tracker.jack
    zone: zone.bus_pickup
    event: enter
action:
  - service: tts.speak
    data:
      message: "The school bus is in the neighborhood!"
```

## Early Release / Half Day Detection

The schedule type is computed automatically each morning from the **school pickup time** returned by the Stopfinder API. Both thresholds are configurable:

| School pickup time | Schedule type |
|---|---|
| Before Half-Day threshold (default 1:00 PM) | `halfday` |
| Before Early Release threshold (default 2:00 PM) | `early` |
| At or after Early Release threshold | `normal` |

Automate on this with a state trigger on `sensor.jack_schedule_type`.

## License

[MIT](LICENSE)

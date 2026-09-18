# Solar Frontier Turbo for Home Assistant

Home Assistant integration for the Solar Frontier **Turbo 1P** solar inverter over
its local web interface. Read-only, no cloud.

The hardware is an OEM-rebranded Steca `coolcept` / StecaGrid. It exposes an XML API
that nothing in its own web UI links to, and this integration uses that API rather
than scraping HTML.

## What it does

- Polls `/all.xml` every 30 seconds by default for live measurements, operating
  state, the event log and firmware versions — one request per cycle, and the only
  request it makes once configured. Setup and reconfigure read `measurements.xml`
  once, to confirm the address really is an inverter.
- Computes the Energy Dashboard figure itself by integrating AC power, because the
  XML API reports no energy totals.
- Treats the inverter being unreachable overnight as normal rather than as a fault.

## Installation

### HACS (recommended)

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/luukdobber/ha-solar-frontier-turbo` with category
   **Integration**.
3. Find **Solar Frontier Turbo** in HACS and download it.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration** and search for
   **Solar Frontier Turbo**.

### Manual

1. Copy `custom_components/solar_frontier_turbo/` into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.
3. Add the integration from **Settings → Devices & services**.

### Configuration

The setup dialog asks for one thing: the inverter's IP address (for example
`192.168.1.100`). The address is validated by reading `measurements.xml` and
requiring a parseable serial number, which is then used as the config entry's
unique ID.

Add the integration in daylight. The inverter is powered from the solar array and
does not answer after sunset, so setup cannot read its serial number then; see
[Night-time behaviour](#night-time-behaviour). Once configured it survives the
nightly outage on its own.

Under **Configure** you can change the polling interval (default 30 s). Increase it
if you also use the inverter's own web UI: the device is slow and serialises
requests internally.

Requires Home Assistant **2026.9.0** or newer (older releases are untested).

## Entities

All entities live on a single device. The inverter reports no friendly name of its
own, only its model in the XML `@Name` attribute, so the device is named from that:
**Solar Frontier Turbo 1P**. Entity IDs are derived from the device name, hence the
`solar_frontier_turbo_1p_` prefix below. A different model names itself
accordingly.

You can rename the device once after setup (**device page → pencil icon**) and Home
Assistant will offer to rename its entity IDs to match.

### Live measurements

| Entity | Device class | State class | Unit |
|---|---|---|---|
| `sensor.solar_frontier_turbo_1p_ac_power` | power | measurement | W |
| `sensor.solar_frontier_turbo_1p_ac_voltage` | voltage | measurement | V |
| `sensor.solar_frontier_turbo_1p_ac_current` | current | measurement | A |
| `sensor.solar_frontier_turbo_1p_ac_frequency` | frequency | measurement | Hz |
| `sensor.solar_frontier_turbo_1p_dc_power` | power | measurement | W |
| `sensor.solar_frontier_turbo_1p_dc_voltage` | voltage | measurement | V |
| `sensor.solar_frontier_turbo_1p_dc_current` | current | measurement | A |
| `sensor.solar_frontier_turbo_1p_temperature` | temperature | measurement | °C |

If a variant model reports a measurement type this integration has no sensor for, it is logged at debug level.

### Energy

| Entity | Source | Notes |
|---|---|---|
| `sensor.solar_frontier_turbo_1p_energy_total` | integrated locally from AC power | **Use this one in the Energy Dashboard.** |

### Status and diagnostics

| Entity | Notes |
|---|---|
| `sensor.solar_frontier_turbo_1p_operating_state` | Plain text. `Active` and `Standby` are the only values seen so far. |
| `sensor.solar_frontier_turbo_1p_last_event` | Newest event, with severity and timestamps as attributes. |
| `sensor.solar_frontier_turbo_1p_clock` | The inverter's own clock. Disabled by default. |

## Setting up the Energy Dashboard

**Settings → Dashboards → Energy → Solar panels → Add solar production**, then pick
`sensor.solar_frontier_turbo_1p_energy_total`.

## Values exposed with caveats

| Value | Caveat |
|---|---|
| Clock | No timezone, no DST handling. Never used to timestamp anything, and disabled by default. |
| Event log | A fixed 60-entry ring buffer, saturated by one recurring event on this unit. A newest-event indicator, not a history. |

## Night-time behaviour

The inverter is powered from the PV array, not from the grid. After sunset its DC
input voltage collapses and the whole device, web server included, shuts down. It comes back at dawn.

This integration treats that as normal operation:

- Being unreachable is logged once at info level per transition, not once per poll.
- Entities stay available across a few missed polls and only go unavailable after
  three consecutive failures.
- Recovery is automatic. No reload, no user action.
- Restarting Home Assistant while the inverter is asleep loads the integration
  anyway. Its entities are unavailable until the device answers, and the device's
  model and firmware details are kept from the last time it did. Home Assistant
  logs one error for the failed first poll, then goes quiet.

`sensor.solar_frontier_turbo_1p_energy_total` stays available right through the night,
including across a restart, because the accumulator belongs to Home Assistant rather
than to the inverter.

## How the energy total is calculated

Trapezoidal integration of AC power into a watt-hour accumulator, exposed in kWh
as a `total_increasing` energy sensor.

- Uses the **actual** elapsed wall-clock time between samples, never the nominal
  interval. Request latency varies, and a busy or suspended host can stretch an
  interval well beyond its nominal length.
- Gaps longer than 300 seconds are skipped rather than integrated across, so a
  restart or an overnight outage cannot invent energy. A clock correction backwards
  is skipped too rather than subtracting energy.
- The accumulator is restored after a Home Assistant restart, but the last
  (timestamp, power) pair deliberately is not — restoring it would integrate
  across the downtime.
- Unknown power breaks the chain instead of contributing zero.

## Troubleshooting

**Everything is unavailable and it is dark outside.** Expected. The inverter has
powered down. It will return at dawn.

**Everything is unavailable in daylight.** Check the IP address, and that Home
Assistant can reach it: `curl http://192.168.1.100/all.xml`. The device is
HTTP-only, GET-only, and endpoint paths are case-sensitive.

To gather detail for a bug report, download diagnostics from the device page. It
includes the current measurement set, the resolved AC power and the firmware
inventory, with the serial number and addresses redacted. For more,
add to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.solar_frontier_turbo: debug
```

## Removing the integration

**Settings → Devices & services → Solar Frontier Turbo → three-dot menu → Delete.**
Entities and the device are removed with it. If you installed via HACS, remove the
download there too and restart.

## Known limitations

- **Read-only.** Over Ethernet the device only answers `GET`; `POST` returns 404.
  There is no way to set a power limit or the clock. That needs RS485.
- The inverter's front-panel buttons *can* be pressed over HTTP. This integration
  deliberately does not, because feedback is a 128×64 bitmap, the menu is globally
  stateful, and one of the buttons opens the service menu where grid-protection
  parameters live.
- The IP address must be entered by hand. Give the inverter a static lease.
- The energy total starts from zero when you add the integration. The inverter's own
  day, month and lifetime totals live behind its web interface, which this
  integration does not read, so none of that history is imported.

## Trademarks

Solar Frontier and Steca are trademarks of their respective owners. The logo in
`custom_components/solar_frontier_turbo/brand/` is used for identification purposes
only. This integration is not affiliated with, endorsed by, or supported by either
company.

## License

MIT

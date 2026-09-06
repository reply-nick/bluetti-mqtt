# bluetti-mqtt

Standalone daemon that publishes [Bluetti](https://www.bluettipower.com/) power-station
stats to an MQTT broker (Home Assistant) over Bluetooth.

It reads the device with [bluetti-bt-lib](https://github.com/reply-nick/bluetti-bt-lib) (branch
`rn/addFridgePower`, required for the FP fridge-powerstation), then:

- publishes one **retained JSON state document** per poll to `bluetti/<device_id>/state`
- pushes **Home Assistant MQTT discovery** config for each supported field
  (`<prefix>/sensor/<device_id>_<field>/config`), with `device_class`,
  units, `value_template`, and a device block
- writes every poll result to a local JSON file (`[bluetti] state_file`,
  default `/run/bluetti/state.json`) used by
  [bluetti-mqtt-nut-bridge](https://github.com/reply-nick/bluetti-mqtt-nut-bridge):
  failures keep the last-known-good fields and add a `connection` block
  (`status: offline`, `fail_streak`, `last_error`, ...)
- maintains an **availability topic** (`bluetti/<device_id>/availability`, LWT) so
  Home Assistant marks the device *unavailable* during BLE stalls instead of
  showing stale numbers
- aborts any BLE read that hangs past `read_timeout` and keeps the
  last-known-good data

## Hardware single-connection constraint

Bluetti power-stations allow **only one active BLE connection**. Do not run
this alongside another BLE reader for the same device (e.g.
`bluetti-nut`). The installer stops `bluetti-nut` for you.

## Install (on the box)

```bash
sudo bash install.sh
```

`install.sh` reuses an existing venv (e.g. `/root/bluetti-nut` from
`bluetti-nut-server`, which already contains `bluetti-bt-lib`), installs
`aiomqtt`, copies the daemon to `/root/bluetti-mqtt/`, creates
`/root/bluetti-mqtt/config.ini` **from the example if missing** (your real
credentials are never touched), and installs the systemd unit.

Then fill in the broker details:

```bash
nano /root/bluetti-mqtt/config.ini
systemctl enable --now bluetti-mqtt
journalctl -u bluetti-mqtt -f
```

## Config

```ini
[bluetti]
mac = 1C:DB:D4:52:6D:D2      # BLE address of the power station
model = FP                    # empty = auto-detect at startup
name = Bluetti FP             # Home Assistant device name
poll_interval = 20            # seconds between BLE reads
read_timeout = 40             # hard timeout per read (BLE stalls)

[mqtt]
host = CHANGE_ME              # broker address (required)
port = 1883
username = mqtt
password =                    # leave broker creds out of git
tls = false                   # set true + port 8883 for TLS
discovery_prefix = homeassistant
```

## Published data

State topic payload (all FP fields + timestamp):

```json
{
  "total_battery_percent": 90,
  "ac_input_power": -447,
  "dc_output_power": 0,
  "ac_input_voltage": 230,
  "time_remaining": 12.5,
  "ctrl_ups_mode": "ON",
  "timestamp": "2026-09-06T21:00:00+00:00"
}
```

Watch a live read:

```bash
mosquitto_sub -h <broker> -t 'bluetti/#' -v
mosquitto_sub -h <broker> -t '<prefix>/sensor/bluetti/#' -v
```

## Uninstall

```bash
systemctl disable --now bluetti-mqtt
rm -rf /root/bluetti-mqtt
```

## Limitations

- Read-only for now: control fields (`ctrl_ac`, `ctrl_dc`, `ctrl_ups_mode`, ...)
  are published as sensors; writing is not wired up (encrypted devices aren't
  supported by the library's writer yet).
- `ctrl_ups_mode` reads `null` on the FP even with UPS mode active: the register
  value doesn't map to a known `UpsMode` enum entry. The discovery sensor exists
  but will show *unknown* until the enum mapping is completed in the library.
- One device per instance; clone the service for more.

MIT License.
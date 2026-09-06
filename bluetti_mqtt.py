#!/usr/bin/env python3
"""Publish Bluetti power-station stats to an MQTT broker (Home Assistant).

Reads the power-station over Bluetooth (using bluetti-bt-lib), publishes a
retained JSON state document every poll and pushes Home Assistant MQTT
discovery configuration for each supported field.

The power-station supports only ONE active BLE connection. Do not run this
next to another BLE reader for the same device (e.g. bluetti-nut).
"""

import argparse
import asyncio
import configparser
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path

import aiomqtt

from bluetti_bt_lib import build_device, recognize_device
from bluetti_bt_lib.bluetooth.device_reader import DeviceReader, DeviceReaderConfig

_LOGGER = logging.getLogger("bluetti-mqtt")

# field name -> discovery metadata. "label" is the entity name shown in HA.
FIELD_META: dict[str, dict] = {
    # Core battery
    "total_battery_percent": {
        "label": "Battery",
        "device_class": "battery",
        "unit": "%",
        "icon": "mdi:battery",
        "measurement": True,
    },
    "time_remaining": {
        "label": "Time Remaining",
        "device_class": "duration",
        "unit": "min",
        "measurement": True,
    },
    # DC/AC input (charging side)
    "dc_input_power": {
        "label": "DC Input Power",
        "device_class": "power",
        "unit": "W",
        "measurement": True,
    },
    "ac_input_power": {
        "label": "AC Input Power",
        "device_class": "power",
        "unit": "W",
        "measurement": True,
    },
    "dc_input_voltage": {
        "label": "DC Input Voltage",
        "device_class": "voltage",
        "unit": "V",
        "measurement": True,
    },
    "dc_input_current": {
        "label": "DC Input Current",
        "device_class": "current",
        "unit": "A",
        "measurement": True,
    },
    "ac_input_frequency": {
        "label": "AC Input Frequency",
        "device_class": "frequency",
        "unit": "Hz",
        "measurement": True,
    },
    "ac_input_voltage": {
        "label": "AC Input Voltage",
        "device_class": "voltage",
        "unit": "V",
        "measurement": True,
    },
    "ac_input_current": {
        "label": "AC Input Current",
        "device_class": "current",
        "unit": "A",
        "measurement": True,
    },
    # Output side
    "dc_output_power": {
        "label": "DC Output Power",
        "device_class": "power",
        "unit": "W",
        "measurement": True,
    },
    "ac_output_power": {
        "label": "AC Output Power",
        "device_class": "power",
        "unit": "W",
        "measurement": True,
    },
    "ac_output_frequency": {
        "label": "AC Output Frequency",
        "device_class": "frequency",
        "unit": "Hz",
        "measurement": True,
    },
    "ac_output_voltage": {
        "label": "AC Output Voltage",
        "device_class": "voltage",
        "unit": "V",
        "measurement": True,
    },
    # Controls (published read-only, no command topic)
    "ctrl_ac": {"label": "AC Output", "icon": "mdi:power"},
    "ctrl_dc": {"label": "DC Output", "icon": "mdi:power"},
    "ctrl_eco_ac": {"label": "ECO AC", "icon": "mdi:leaf"},
    "ctrl_eco_time_mode_ac": {"label": "ECO Time Mode AC", "icon": "mdi:clock"},
    "ctrl_eco_min_power_ac": {
        "label": "ECO Min Power AC",
        "unit": "W",
        "measurement": True,
    },
    "ctrl_charging_mode": {"label": "Charging Mode", "icon": "mdi:battery-charging"},
    "ctrl_power_lifting": {"label": "Power Lifting", "icon": "mdi:flash"},
    "ctrl_ups_mode": {"label": "UPS Mode", "icon": "mdi:power-plug"},
    "device_type": {"label": "Device Type", "icon": "mdi:information-outline"},
}


@dataclass
class Config:
    mac: str
    model: str
    name: str
    poll_interval: float
    read_timeout: float
    state_file: str
    discovery_prefix: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_tls: bool


def load_config(path: str) -> Config:
    if not Path(path).exists():
        raise SystemExit(f"Config file not found: {path}")

    cp = configparser.ConfigParser()
    cp.read(path)

    bluetti = cp["bluetti"]
    mqtt = cp["mqtt"]

    cfg = Config(
        mac=str(bluetti.get("mac", "")).strip() or bluetti.get("mac", ""),
        model=bluetti.get("model", "").strip(),
        name=bluetti.get("name", "").strip(),
        poll_interval=float(bluetti.get("poll_interval", "20")),
        read_timeout=float(bluetti.get("read_timeout", "40")),
        state_file=bluetti.get("state_file", "").strip(),
        discovery_prefix=mqtt.get("discovery_prefix", "homeassistant").strip(),
        mqtt_host=mqtt.get("host", "").strip(),
        mqtt_port=int(mqtt.get("port", "1883")),
        mqtt_username=mqtt.get("username", "").strip(),
        mqtt_password=mqtt.get("password", ""),
        mqtt_tls=mqtt.getboolean("tls", fallback=False),
    )

    if not cfg.mac:
        raise SystemExit("Config error: [bluetti] mac is required")
    if not cfg.mqtt_host or cfg.mqtt_host == "CHANGE_ME":
        raise SystemExit(
            "Config error: [mqtt] host is not set. "
            f"Edit {path} and set the broker address (and credentials)."
        )
    return cfg


def device_id(mac: str) -> str:
    return "bluetti_" + mac.replace(":", "").lower()


def _json_value(value):
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Decimal):
        val = float(value)
        return int(val) if val.is_integer() else val
    if isinstance(value, str):
        try:
            val = float(value)
        except (TypeError, ValueError):
            return value
        return int(val) if val.is_integer() else val
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_state_file(path: str, payload: dict) -> None:
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        fh.write(json.dumps(payload, default=str) + "\n")
        os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def connection_block(status: str, fail_streak: int, last_ok: str, err: str | None = None) -> dict:
    block = {
        "status": status,
        "fail_streak": fail_streak,
        "last_ok": last_ok,
    }
    if err:
        block["last_error"] = err
        block["last_fail"] = now_iso()
    return block


async def poll_once(reader: DeviceReader, timeout: float) -> tuple[dict | None, str | None]:
    try:
        data = await asyncio.wait_for(reader.read(), timeout=timeout)
        return data, None
    except (asyncio.TimeoutError, TimeoutError) as err:
        return None, f"BLE read timed out after {timeout}s ({type(err).__name__})"


async def publish_discovery(
    client: aiomqtt.Client,
    cfg: Config,
    device,
    state_topic: str,
    availability_topic: str,
    dev_id: str,
) -> None:
    known = {f.name for f in device.fields}
    device_name = cfg.name or f"Bluetti {cfg.model}"

    for field_name, meta in FIELD_META.items():
        if field_name not in known:
            continue

        topic = (
            f"{cfg.discovery_prefix}/sensor/{dev_id}_{field_name}/config"
        )
        config: dict = {
            "name": meta["label"],
            "unique_id": f"{dev_id}_{field_name}",
            "object_id": f"{dev_id}_{field_name}",
            "state_topic": state_topic,
            "value_template": "{{ value_json.%s }}" % field_name,
            "availability_topic": availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": [dev_id],
                "name": device_name,
                "manufacturer": "Bluetti",
                "model": cfg.model,
                "sw_version": "bluetti-mqtt",
            },
        }

        if meta.get("device_class"):
            config["device_class"] = meta["device_class"]
        if meta.get("unit"):
            config["unit_of_measurement"] = meta["unit"]
        if meta.get("icon"):
            config["icon"] = meta["icon"]
        if meta.get("measurement"):
            config["state_class"] = "measurement"

        await client.publish(topic, json.dumps(config), qos=1, retain=True)
        _LOGGER.debug("Published discovery for %s", field_name)


async def publisher_loop(
    cfg: Config, reader: DeviceReader, device, dev_id: str
) -> None:
    state_topic = f"bluetti/{dev_id}/state"
    availability_topic = f"bluetti/{dev_id}/availability"
    will = aiomqtt.Will(availability_topic, "offline", qos=1, retain=True)

    connect_kwargs = {
        "hostname": cfg.mqtt_host,
        "port": cfg.mqtt_port,
        "username": cfg.mqtt_username or None,
        "password": cfg.mqtt_password or None,
        "will": will,
    }
    if cfg.mqtt_tls:
        connect_kwargs["tls_params"] = aiomqtt.TLSParameters()

    fail_streak = 0
    last_state: dict | None = None
    while True:
        try:
            async with aiomqtt.Client(**connect_kwargs) as client:
                _LOGGER.info(
                    "Connected to MQTT broker %s:%s",
                    cfg.mqtt_host,
                    cfg.mqtt_port,
                )
                await client.publish(
                    availability_topic, "online", qos=1, retain=True
                )
                await publish_discovery(
                    client, cfg, device, state_topic, availability_topic, dev_id
                )

                fail_streak = 0
                while True:
                    data, err = await poll_once(reader, cfg.read_timeout)
                    if data:
                        fail_streak = 0
                        fields = {key: _json_value(value) for key, value in data.items()}
                        ts = now_iso()
                        conn = connection_block("online", 0, ts)
                        file_state = {**fields, "timestamp": ts, "connection": conn}
                        last_state = file_state
                        write_state_file(cfg.state_file, file_state)

                        await client.publish(
                            availability_topic, "online", qos=1, retain=True
                        )
                        await client.publish(
                            state_topic,
                            json.dumps({key: value for key, value in file_state.items() if key != "connection"}, default=str),
                            retain=True,
                        )
                        _LOGGER.info(
                            "Published %d fields (soc=%s%% ac_in=%sW)",
                            len(data),
                            data.get("total_battery_percent"),
                            data.get("ac_input_power"),
                        )
                    else:
                        fail_streak += 1
                        offline_now = fail_streak >= 2
                        if offline_now:
                            await client.publish(
                                availability_topic, "offline", qos=1, retain=True
                            )

                        base = {k: v for k, v in (last_state or {}).items() if k != "connection"}
                        if not base:
                            base = {"timestamp": now_iso()}
                        last_ok = (last_state or {}).get("connection", {}).get("last_ok", base.get("timestamp", ""))
                        file_state = {
                            **base,
                            "connection": connection_block(
                                "offline" if offline_now else "online",
                                fail_streak,
                                last_ok,
                                err,
                            ),
                        }
                        write_state_file(cfg.state_file, file_state)
                        _LOGGER.warning(
                            "Read failed (streak %d, %s) - keeping last-known-good%s",
                            fail_streak,
                            err,
                            "" if last_state else " (no data yet)",
                        )
                    await asyncio.sleep(cfg.poll_interval)
        except aiomqtt.MqttError as err:
            _LOGGER.error("MQTT error: %s - reconnecting in 5s", err)
            await asyncio.sleep(5)


def get_device(model: str):
    # build_device matches DEVICE_NAME_RE, which requires trailing digits
    # (model + serial). The serial is irrelevant, so pad with digits.
    return build_device(model + "123456789")


async def run(cfg: Config) -> int:
    device = get_device(cfg.model) if cfg.model else None

    if device is None:
        _LOGGER.info("Auto-detecting device at %s", cfg.mac)
        result = await recognize_device(cfg.mac, asyncio.Future)
        if result is None:
            _LOGGER.error("Could not recognize device at %s", cfg.mac)
            return 1
        device = get_device(result.name)
        if device is None:
            _LOGGER.error("No device implementation for model '%s'", result.name)
            return 1
        _LOGGER.info(
            "Recognized %s (sn=%s, encryption=%s)",
            result.name,
            result.sn,
            result.encrypted,
        )

    encryption = True  # FP and other v2 devices use encryption
    reader = DeviceReader(
        cfg.mac,
        device,
        asyncio.Future,
        DeviceReaderConfig(timeout=cfg.read_timeout, use_encryption=encryption),
    )

    _LOGGER.info(
        "Polling %s (%s) every %ss",
        cfg.mac,
        cfg.model or device.__class__.__name__,
        cfg.poll_interval,
    )

    await publisher_loop(cfg, reader, device, device_id(cfg.mac))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish Bluetti power-station stats to MQTT (Home Assistant)."
    )
    parser.add_argument(
        "--config",
        default="config.ini",
        help="Path to the INI config file (default: config.ini)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    raise SystemExit(asyncio.run(run(cfg)))


if __name__ == "__main__":
    main()
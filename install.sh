#!/usr/bin/env bash
# Install bluetti-mqtt onto a DietPi box that already has bluetti-bt-lib
# installed in a venv at /root/bluetti-nut (created by bluetti-nut-server).
#
#   sudo bash install.sh
#
# What it does:
#   1. Reuses (or creates) the python venv and installs aiomqtt
#   2. Copies bluetti_mqtt.py to /root/bluetti-mqtt/
#   3. Creates config.ini from config.example.ini if it does not exist yet
#      (your broker host/credentials are NEVER touched or committed)
#   4. Stops and disables bluetti-nut (the FP allows only one BLE reader)
#   5. Installs + starts the bluetti-mqtt systemd unit
#
# Set these env vars before running if your setup differs:
#   VENV, MODEL, MAC, INTERVAL.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${DEST:-/root/bluetti-mqtt}"

VENV="${VENV:-/root/bluetti-nut}"
BT_LIB_REF="${BT_LIB_REF:-git+https://github.com/reply-nick/bluetti-bt-lib.git@rn/addFridgePower}"
SERVICE_NAME="bluetti-mqtt"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

MODEL="${MODEL:-FP}"
MAC="${MAC:-1C:DB:D4:52:6D:D2}"
INTERVAL="${INTERVAL:-20}"
READ_TIMEOUT="${READ_TIMEOUT:-40}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run as root (sudo bash install.sh)" >&2
    exit 1
fi

echo ">> Ensuring venv at $VENV"
if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip
    "$VENV/bin/pip" install "$BT_LIB_REF" bleak async-timeout
else
    echo "   (reusing existing venv)"
fi
"$VENV/bin/pip" install "aiomqtt>=2.0"

echo ">> Installing bluetti-mqtt to $DEST"
if [[ ! -f "$DEST/bluetti_mqtt.py" ]] \
        || ! cmp -s "$DIR/bluetti_mqtt.py" "$DEST/bluetti_mqtt.py"; then
    mkdir -p "$DEST"
    cp "$DIR/bluetti_mqtt.py" "$DEST/bluetti_mqtt.py"
else
    echo "   (bluetti_mqtt.py already up to date)"
fi

if [[ -f "$DEST/config.ini" ]]; then
    echo "   keeping existing $DEST/config.ini"
elif [[ -f "$DIR/config.example.ini" ]]; then
    cp "$DIR/config.example.ini" "$DEST/config.ini"
    echo "   created $DEST/config.ini from example - EDIT IT and set the broker host/credentials"
else
    echo "   ERROR: no config.example.ini found" >&2
    exit 1
fi

if systemctl is-active --quiet bluetti-nut 2>/dev/null; then
    echo ">> Stopping and disabling bluetti-nut (FP allows only ONE BLE reader)"
    systemctl stop bluetti-nut
    systemctl disable bluetti-nut || true
fi

echo ">> Writing service unit $SERVICE_FILE"
cat > "$SERVICE_FILE" <<UNIT
[Unit]
Description=bluetti-mqtt (${MODEL} -> Home Assistant MQTT)
After=bluetooth.service network.target
Wants=bluetooth.service

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV}/bin/python ${DEST}/bluetti_mqtt.py --config ${DEST}/config.ini
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload

if grep -q "CHANGE_ME" "$DEST/config.ini"; then
    echo ""
    echo ">> config.ini still has placeholder values."
    echo "   Edit $DEST/config.ini (broker host/user/password) then run:"
    echo "   systemctl enable --now $SERVICE_NAME"
else
    echo ">> Enabling and starting service"
    systemctl enable --now "$SERVICE_NAME"
fi

echo ">> Done."
echo "   Watch it: journalctl -u $SERVICE_NAME -f"
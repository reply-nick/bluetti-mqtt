#!/usr/bin/env bash
# Package the local /root/bluetti-mqtt install into a zip for repo setup.
#
# Run ON THE BOX (DietPi):
#   bash ~/bluetti-mqtt/stage_for_zip.sh
#
# Produces: ~/bluetti-mqtt.zip  (public build, config.ini excluded on purpose)
set -euo pipefail

RUN_DIR="${RUN_DIR:-/root/bluetti-mqtt}"
DEST=~/bluetti-mqtt-stage
ZIP=~/bluetti-mqtt.zip

if [[ ! -d "$RUN_DIR" ]]; then
    echo "Cannot find $RUN_DIR" >&2
    exit 1
fi
if ! command -v zip >/dev/null 2>&1; then
    echo "zip is not installed: apt install zip" >&2
    exit 1
fi

rm -rf "$DEST"
mkdir -p "$DEST"

cp "$RUN_DIR/bluetti_mqtt.py" "$DEST/"
cp "$RUN_DIR/install.sh" "$DEST/"
cp "$RUN_DIR/stage_for_zip.sh" "$DEST/"

if [[ -f /etc/systemd/system/bluetti-mqtt.service ]]; then
    cp /etc/systemd/system/bluetti-mqtt.service "$DEST/bluetti-mqtt.service"
    echo "Staged bluetti-mqtt.service"
fi

if [[ -f "$RUN_DIR/config.example.ini" ]]; then
    cp "$RUN_DIR/config.example.ini" "$DEST/config.example.ini"
    echo "Staged config.example.ini"
fi
# NEVER stage config.ini - it holds broker credentials.

# Minimal README (a fuller one is written on the Mac during repo setup)
cat > "$DEST/README.md" <<'EOF'
# bluetti-mqtt

Standalone daemon publishing Bluetti FP power-station stats to an MQTT broker
with Home Assistant auto-discovery. Reads via bluetti-bt-lib (branch
rn/addFridgePower), publishes one retained JSON state document per poll plus
per-field MQTT discovery configs and an availability (LWT) topic.

The FP supports only one active BLE connection, so it replaces bluetti-nut on
the same box.

Install:
  sudo bash install.sh        # reuses /root/bluetti-nut venv, installs aiomqtt
  nano /root/bluetti-mqtt/config.ini   # set broker host/user/pass
  systemctl enable --now bluetti-mqtt

Layout:
  bluetti_mqtt.py       daemon
  config.example.ini    config template (real config.ini is never committed)
  bluetti-mqtt.service  systemd unit
EOF

rm -f "$ZIP"
(cd ~ && zip -rq "$ZIP" bluetti-mqtt-stage)
echo "Created $ZIP"
echo "Copy it to your Mac and create the repo from it."
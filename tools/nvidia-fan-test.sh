#!/usr/bin/env bash
#
# Can anything on this machine set the GPU fan speed?
#
#   sudo ./tools/nvidia-fan-test.sh
#
# Runs the same NVML calls the daemon makes, first as plain root with no
# sandbox at all and then under everything the unit sets. That separates "the
# driver will not allow it" from "the service is being restricted", which look
# identical from inside the daemon.
#
# The fan is set to the speed it is already running at, so nothing changes
# audibly, and automatic control is handed back at the end either way.
#
set -u

PYTHON="${PYTHON:-/usr/lib/fancontrol-linux/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="$(command -v python3)"
    export PYTHONPATH="${PYTHONPATH:-}:$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

if [[ $EUID -ne 0 ]]; then
    echo "Setting a fan speed needs root:"
    echo "    sudo $0"
    exit 1
fi

WORK="$(mktemp -d)"; chmod 755 "$WORK"
trap 'rm -rf "$WORK"' EXIT

read -r -d '' PROBE <<'PY' || true
import sys
from fancontrol.hw.nvml import NVML_FAN_POLICY_MANUAL, Nvml, NvmlError

out = open(sys.argv[1], "w")
def say(text):
    out.write(text + "\n")
    out.flush()

nvml = Nvml()
if not nvml.init():
    say("FAIL libnvidia-ml could not be loaded or initialised")
    raise SystemExit

try:
    say(f"driver {nvml.driver_version()}")
except NvmlError as exc:
    say(f"driver version unavailable: {exc}")

handle = nvml.device_handle(0)
say(f"card {nvml.device_name(handle)}")
fans = nvml.num_fans(handle)
say(f"fans {fans}")

for fan in range(fans):
    try:
        current = int(nvml.fan_percent(handle, fan))
        say(f"fan {fan}: currently {current}%")
    except NvmlError as exc:
        say(f"fan {fan}: cannot even read the speed: {exc}")
        continue

    try:
        nvml.set_fan_control_policy(handle, fan, NVML_FAN_POLICY_MANUAL)
        say(f"fan {fan}: manual policy accepted")
    except NvmlError as exc:
        say(f"fan {fan}: manual policy refused: {exc}")

    # Ask for the speed it is already doing, so nothing actually changes.
    try:
        nvml.set_fan_percent(handle, fan, current)
        say(f"fan {fan}: SET WORKS")
    except NvmlError as exc:
        say(f"fan {fan}: SET REFUSED: {exc}")

    try:
        nvml.set_fan_default(handle, fan)
        say(f"fan {fan}: handed back to the driver")
    except NvmlError as exc:
        say(f"fan {fan}: could not hand back: {exc}")

nvml.shutdown()
PY

UNIT_SETTINGS=(
    "NoNewPrivileges=yes" "ProtectHome=yes" "PrivateTmp=yes"
    "ProtectSystem=strict" "ProtectControlGroups=yes" "RestrictNamespaces=yes"
    "RestrictRealtime=yes" "RestrictSUIDSGID=yes" "LockPersonality=yes"
    "SystemCallArchitectures=native"
)

echo
echo "=== As plain root, no sandbox at all ==="
"$PYTHON" -c "$PROBE" "$WORK/bare" >/dev/null 2>&1
sed 's/^/  /' "$WORK/bare" 2>/dev/null || echo "  the probe produced nothing"

echo
echo "=== Under everything the unit sets ==="
properties=()
for setting in "${UNIT_SETTINGS[@]}"; do properties+=(-p "$setting"); done
systemd-run --wait --collect --quiet "${properties[@]}" \
    "$PYTHON" -c "$PROBE" "$WORK/sandboxed" >/dev/null 2>&1
sed 's/^/  /' "$WORK/sandboxed" 2>/dev/null || echo "  the probe produced nothing"

echo
if grep -q "SET WORKS" "$WORK/bare" 2>/dev/null; then
    if grep -q "SET WORKS" "$WORK/sandboxed" 2>/dev/null; then
        echo "Fan control works both ways, so the daemon should manage it too."
        echo "If it still does not, the difference is somewhere else - send both"
        echo "halves of this output."
    else
        echo "It works as plain root but not under the unit, so the sandbox is"
        echo "what is stopping it. Send this output and the unit will be fixed."
    fi
else
    echo "The driver refuses to let anything set this card's fan speed, sandbox"
    echo "or not. That is a driver and card limitation rather than a fault in"
    echo "this program: NVIDIA only allows it on some cards and driver versions."
    echo "Board fans are unaffected; switch the two GPU entries off and the GPU"
    echo "keeps running its own curve."
fi
echo

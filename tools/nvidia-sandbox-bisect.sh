#!/usr/bin/env bash
#
# Find which systemd hardening setting stops NVML from initialising.
#
# The daemon runs as root and still gets NVML_ERROR_NO_PERMISSION, which means
# the sandbox is in the way rather than privileges. This runs the same NVML
# probe the daemon uses under each setting on its own, so the culprit names
# itself instead of being guessed at.
#
#   sudo ./tools/nvidia-sandbox-bisect.sh
#
set -u

PYTHON="${PYTHON:-/usr/lib/fancontrol-linux/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="$(command -v python3)"
    export PYTHONPATH="${PYTHONPATH:-}:$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

if [[ $EUID -ne 0 ]]; then
    echo "This has to run as root, the same way the daemon does:"
    echo "    sudo $0"
    exit 1
fi

PROBE='
import sys
from fancontrol.hw.nvml import Nvml
nvml = Nvml()
if not nvml.init():
    sys.exit(2)
try:
    sys.exit(0 if nvml.device_count() else 3)
except Exception:
    sys.exit(4)
'

# Every hardening line the unit sets, one per entry.
SETTINGS=(
    "NoNewPrivileges=yes"
    "ProtectHome=yes"
    "PrivateTmp=yes"
    "ProtectSystem=strict"
    "ProtectControlGroups=yes"
    "RestrictNamespaces=yes"
    "RestrictRealtime=yes"
    "RestrictSUIDSGID=yes"
    "LockPersonality=yes"
    "SystemCallArchitectures=native"
    "SystemCallFilter=@system-service"
    "CapabilityBoundingSet="
)

run_probe() {
    local -a properties=()
    for setting in "$@"; do
        properties+=(-p "$setting")
    done
    systemd-run --wait --collect --quiet --service-type=exec \
        "${properties[@]}" \
        "$PYTHON" -c "$PROBE" >/dev/null 2>&1
}

describe() {
    case "$1" in
        0) echo "NVML works" ;;
        2) echo "nvmlInit failed" ;;
        3) echo "no GPUs visible" ;;
        *) echo "failed ($1)" ;;
    esac
}

echo
echo "Probing NVML with no hardening at all"
run_probe
BASE=$?
echo "  baseline: $(describe $BASE)"
if [[ $BASE -ne 0 ]]; then
    echo
    echo "NVML does not work even unsandboxed, so the unit is not the problem."
    echo "Check that the driver is loaded and /dev/nvidia* exist."
    exit 1
fi

echo
echo "Now one setting at a time"
CULPRITS=()
for setting in "${SETTINGS[@]}"; do
    run_probe "$setting"
    code=$?
    if [[ $code -eq 0 ]]; then
        printf '  \033[32mok\033[0m    %s\n' "$setting"
    else
        printf '  \033[31mno\033[0m    %-36s %s\n' "$setting" "$(describe $code)"
        CULPRITS+=("$setting")
    fi
done

echo
echo "All of them together"
run_probe "${SETTINGS[@]}"
echo "  combined: $(describe $?)"

echo
if [[ ${#CULPRITS[@]} -eq 0 ]]; then
    echo "No single setting breaks NVML. If the combination does, it is an"
    echo "interaction; send this whole output."
else
    echo "These are what stop NVML:"
    for setting in "${CULPRITS[@]}"; do
        echo "    $setting"
    done
fi
echo

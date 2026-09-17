#!/usr/bin/env bash
#
# Work out what stops the daemon from driving an NVIDIA GPU's fans.
#
#   sudo ./tools/nvidia-diagnose.sh
#
# Runs the same NVML calls the daemon makes - start up, read a speed, set a
# speed - first as plain root with no sandbox, then under each setting the unit
# applies, one at a time, then under all of them. Whatever breaks names itself.
#
# Output comes back over a pipe rather than through a file, because several of
# the settings under test (PrivateTmp above all) give the probe its own /tmp,
# and a result written there is never seen again. An earlier version did
# exactly that and reported "nothing happened" for every sandboxed case, which
# is why a control case now runs first and why this one uses --pipe.
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
    echo "Setting a fan speed needs root, the same way the daemon has it:"
    echo "    sudo $0"
    exit 1
fi

green() { printf '\033[32m%s\033[0m' "$1"; }
red()   { printf '\033[31m%s\033[0m' "$1"; }
dim()   { printf '\033[2m%s\033[0m' "$1"; }

read -r -d '' PROBE <<'PY' || true
import sys
from fancontrol.hw.nvml import NVML_FAN_POLICY_MANUAL, Nvml, NvmlError

mode = sys.argv[1] if len(sys.argv) > 1 else "full"

def say(text):
    print(text, flush=True)

if mode == "control":
    say("RESULT fail deliberately failing control case")
    raise SystemExit

nvml = Nvml()
if not nvml.init():
    say("RESULT fail nvmlInit: the library would not start")
    raise SystemExit

try:
    handle = nvml.device_handle(0)
    name = nvml.device_name(handle)
    fans = nvml.num_fans(handle)
except NvmlError as exc:
    say(f"RESULT fail enumerating the GPU: {exc}")
    raise SystemExit

if mode == "details":
    try:
        say(f"DETAIL driver {nvml.driver_version()}")
    except NvmlError as exc:
        say(f"DETAIL driver version unavailable: {exc}")
    say(f"DETAIL card {name}, {fans} fan(s)")

if not fans:
    say("RESULT fail the GPU reports no controllable fans")
    raise SystemExit

try:
    current = int(nvml.fan_percent(handle, 0))
except NvmlError as exc:
    say(f"RESULT fail reading the speed: {exc}")
    raise SystemExit
if mode == "details":
    say(f"DETAIL fan 0 currently at {current}%")

try:
    nvml.set_fan_control_policy(handle, 0, NVML_FAN_POLICY_MANUAL)
    policy = "accepted"
except NvmlError as exc:
    policy = f"refused ({exc})"
if mode == "details":
    say(f"DETAIL manual policy: {policy}")

# Ask for the speed it is already doing, so nothing actually changes.
try:
    nvml.set_fan_percent(handle, 0, current)
except NvmlError as exc:
    say(f"RESULT fail setting the speed: {exc}")
else:
    say("RESULT ok start, read and set all work")
finally:
    try:
        nvml.set_fan_default(handle, 0)
    except NvmlError:
        pass
nvml.shutdown()
PY

run_case() {
    local label="$1"; shift
    local mode="$1"; shift
    local output
    if [[ $# -gt 0 ]]; then
        local -a properties=()
        for setting in "$@"; do properties+=(-p "$setting"); done
        output="$(systemd-run --pipe --wait --collect --quiet "${properties[@]}" \
                  "$PYTHON" -c "$PROBE" "$mode" 2>/dev/null)"
    else
        output="$("$PYTHON" -c "$PROBE" "$mode" 2>/dev/null)"
    fi

    echo "$output" | grep '^DETAIL ' | sed 's/^DETAIL /    /'

    local verdict
    verdict="$(echo "$output" | grep '^RESULT ' | head -1)"
    if [[ -z "$verdict" ]]; then
        printf '  %s  %-38s %s\n' "$(red '??')" "$label" "$(dim 'the probe said nothing at all')"
        return 2
    fi
    verdict="${verdict#RESULT }"
    if [[ "$verdict" == ok* ]]; then
        printf '  %s  %-38s %s\n' "$(green 'ok')" "$label" "$(dim "${verdict#ok }")"
        return 0
    fi
    printf '  %s  %-38s %s\n' "$(red 'no')" "$label" "${verdict#fail }"
    return 1
}

UNIT_SETTINGS=(
    "NoNewPrivileges=yes" "ProtectHome=yes" "PrivateTmp=yes"
    "ProtectSystem=strict" "ProtectControlGroups=yes" "RestrictNamespaces=yes"
    "RestrictRealtime=yes" "RestrictSUIDSGID=yes" "LockPersonality=yes"
    "SystemCallArchitectures=native" "SystemCallFilter=@system-service"
    "CapabilityBoundingSet="
)

echo
echo "Can this harness see a failure at all?"
if run_case "control case (must fail)" control; then
    echo; red "The control case passed, so nothing below means anything."; echo
    exit 1
fi

echo
echo "As plain root, no sandbox"
run_case "baseline" details
if [[ $? -ne 0 ]]; then
    echo
    echo "The driver refuses fan control even unsandboxed, so this is a driver"
    echo "and card limitation rather than anything systemd is doing."
    exit 1
fi

echo
echo "Under each setting the unit applies, one at a time"
CULPRITS=()
for setting in "${UNIT_SETTINGS[@]}"; do
    run_case "$setting" full "$setting" || CULPRITS+=("$setting")
done

echo
echo "Under all of them together"
run_case "combined" full "${UNIT_SETTINGS[@]}"
COMBINED=$?

# Once the empty capability set is implicated, find out which capability the
# driver actually wants, rather than settling for "all of them".
CAPABILITY=""
if printf '%s\n' "${CULPRITS[@]}" | grep -qx "CapabilityBoundingSet="; then
    echo
    echo "Capabilities, one at a time, since dropping them all is what broke it"
    for cap in CAP_SYS_ADMIN CAP_SYS_RAWIO CAP_DAC_OVERRIDE CAP_MKNOD \
               CAP_SYS_MODULE CAP_IPC_LOCK CAP_SYS_NICE CAP_SYS_RESOURCE \
               CAP_SYS_PTRACE CAP_SYS_BOOT; do
        if run_case "only $cap" full "CapabilityBoundingSet=$cap"; then
            CAPABILITY="$cap"
            break
        fi
    done
fi

echo
echo "What the installed unit actually resolves to"
systemctl show fancontrold.service \
    -p FragmentPath -p DropInPaths -p CapabilityBoundingSet \
    -p SystemCallFilter -p PrivateTmp -p ProtectSystem -p DevicePolicy \
    2>/dev/null | sed 's/^/  /'

echo
if [[ -n "$CAPABILITY" ]]; then
    echo "Dropping every capability is what stops the daemon driving the GPU,"
    echo "and $CAPABILITY on its own is enough to put it right. The drop-in can"
    echo "be narrowed to exactly that:"
    echo
    echo "    CapabilityBoundingSet=$CAPABILITY"
elif [[ ${#CULPRITS[@]} -gt 0 ]]; then
    echo "These stop the daemon driving the GPU:"
    printf '    %s\n' "${CULPRITS[@]}"
    echo
    echo "Send this and the drop-in will be narrowed to exactly those."
elif [[ $COMBINED -ne 0 ]]; then
    echo "No single setting breaks it but the combination does, so it is an"
    echo "interaction between them. Send this output."
else
    echo "Everything works here, including the full set. If the daemon still"
    echo "cannot drive the GPU, the difference is not the sandbox - send this"
    echo "along with:  journalctl -u fancontrold -n 30 --no-pager"
fi
echo

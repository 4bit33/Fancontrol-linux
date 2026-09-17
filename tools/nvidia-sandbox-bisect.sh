#!/usr/bin/env bash
#
# Work out why NVML fails for the daemon but not from a shell.
#
#   sudo ./tools/nvidia-sandbox-bisect.sh
#
# Results are written to files by the probe itself rather than taken from exit
# codes, because systemd-run does not always propagate them - and a bisect
# whose every case says "fine" is worthless. A deliberately failing control
# case runs first to prove the harness can see a failure at all.
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

WORK="$(mktemp -d)"
chmod 755 "$WORK"
trap 'rm -rf "$WORK"' EXIT

green() { printf '\033[32m%s\033[0m' "$1"; }
red()   { printf '\033[31m%s\033[0m' "$1"; }
dim()   { printf '\033[2m%s\033[0m' "$1"; }

# Probe: reports what NVML did, into a file whose name it is given.
read -r -d '' PROBE <<'PY' || true
import sys, traceback
out = sys.argv[1]
mode = sys.argv[2]

def write(text):
    with open(out, "w") as handle:
        handle.write(text)

try:
    if mode == "control":
        write("FAIL deliberately failing control case")
        sys.exit(1)

    if mode == "registry":
        # Exactly what the daemon does at startup, not just a bare NVML call.
        from fancontrol.hw.registry import HardwareRegistry
        registry = HardwareRegistry()
        registry.discover()
        gpu = [c for c in registry.controls.values() if c.device.backend == "nvidia"]
        write(f"OK registry: {len(registry.controls)} controls, {len(gpu)} of them NVIDIA"
              if gpu else
              f"FAIL registry saw {len(registry.controls)} controls and no NVIDIA fan")
        sys.exit(0)

    from fancontrol.hw.nvml import Nvml, NvmlError
    nvml = Nvml()
    if not nvml.init():
        write("FAIL nvmlInit did not succeed")
        sys.exit(0)
    try:
        count = nvml.device_count()
    except NvmlError as exc:
        write(f"FAIL {exc}")
        sys.exit(0)
    write(f"OK {count} GPU(s)" if count else "FAIL NVML started but reported no GPU")
except Exception:
    write("FAIL " + traceback.format_exc().strip().splitlines()[-1])
PY

CASE=0
run_probe() {
    local label="$1"; shift
    local mode="$1"; shift
    CASE=$((CASE + 1))
    local result="$WORK/case-$CASE"
    local -a properties=()
    for setting in "$@"; do
        properties+=(-p "$setting")
    done

    systemd-run --wait --collect --quiet "${properties[@]}" \
        "$PYTHON" -c "$PROBE" "$result" "$mode" >/dev/null 2>&1

    if [[ ! -f "$result" ]]; then
        printf '  %s  %-38s %s\n' "$(red '??')" "$label" "$(dim 'the probe never ran')"
        return 2
    fi
    local text; text="$(cat "$result")"
    if [[ "$text" == OK* ]]; then
        printf '  %s  %-38s %s\n' "$(green 'ok')" "$label" "$(dim "${text#OK }")"
        return 0
    fi
    printf '  %s  %-38s %s\n' "$(red 'no')" "$label" "${text#FAIL }"
    return 1
}

SETTINGS=(
    "NoNewPrivileges=yes" "ProtectHome=yes" "PrivateTmp=yes"
    "ProtectSystem=strict" "ProtectControlGroups=yes" "RestrictNamespaces=yes"
    "RestrictRealtime=yes" "RestrictSUIDSGID=yes" "LockPersonality=yes"
    "SystemCallArchitectures=native" "SystemCallFilter=@system-service"
    "CapabilityBoundingSet="
)

echo
echo "Can this harness see a failure at all?"
if run_probe "control case (must fail)" control; then
    echo
    red "The control case passed, so these results mean nothing."; echo
    echo "systemd-run is not reporting what the probe did. Stopping."
    exit 1
fi

echo
echo "Bare NVML, no hardening"
run_probe "baseline" nvml
BASE=$?

echo
echo "Bare NVML, one setting at a time"
CULPRITS=()
for setting in "${SETTINGS[@]}"; do
    run_probe "$setting" nvml "$setting" || CULPRITS+=("$setting")
done

echo
echo "Bare NVML, everything the unit sets"
run_probe "combined" nvml "${SETTINGS[@]}"
COMBINED=$?

echo
echo "The daemon's own startup, which is what actually fails"
run_probe "registry, no hardening" registry
REG_BARE=$?
run_probe "registry, everything the unit sets" registry "${SETTINGS[@]}"
REG_HARD=$?

echo
echo "SELinux"
if command -v getenforce >/dev/null; then
    echo "  mode: $(getenforce)"
    # A denial reaching the driver's device nodes looks exactly like
    # NVML_ERROR_NO_PERMISSION from inside the process.
    if command -v ausearch >/dev/null; then
        DENIALS="$(ausearch -m avc,user_avc -ts recent 2>/dev/null \
                   | grep -Ei 'fancontrol|nvidia' | tail -8)"
        if [[ -n "$DENIALS" ]]; then
            red "  recent denials mentioning fancontrold or nvidia:"; echo
            echo "$DENIALS" | sed 's/^/    /'
        else
            echo "  no recent denials mentioning fancontrold or nvidia"
        fi
    else
        dim "  ausearch not installed (dnf install audit) - cannot check denials"; echo
    fi
    echo "  label on the daemon:"
    ls -Z "$PYTHON" 2>/dev/null | sed 's/^/    /'
else
    echo "  not in use"
fi

echo
echo "What the installed unit is really running with"
systemctl show fancontrold.service \
    -p FragmentPath -p DropInPaths -p CapabilityBoundingSet \
    -p SystemCallFilter -p PrivateTmp -p ProtectSystem -p Environment \
    2>/dev/null | sed 's/^/  /'

echo
if [[ $BASE -ne 0 ]]; then
    echo "NVML fails even unsandboxed, so the unit is not the problem at all."
elif [[ $REG_BARE -ne 0 && $COMBINED -eq 0 ]]; then
    echo "NVML works but the daemon's own discovery does not, with no hardening"
    echo "involved. The problem is in the program, not in systemd."
elif [[ ${#CULPRITS[@]} -gt 0 ]]; then
    echo "These stop NVML:"
    printf '    %s\n' "${CULPRITS[@]}"
elif [[ $REG_HARD -ne 0 ]]; then
    echo "Only the full combination breaks it; it is an interaction."
else
    echo "Everything passed here, so whatever the daemon hits is not reproduced"
    echo "by this script. The next suspect is SELinux, which denies the real"
    echo "unit but need not deny a transient one. Restart the daemon and look:"
    echo "    sudo systemctl restart fancontrold"
    echo "    sudo ausearch -m avc -ts recent | grep -i nvidia"
    echo "    sudo setenforce 0 && sudo systemctl restart fancontrold && fanctl doctor"
    echo "    sudo setenforce 1     # put it back either way"
fi
echo

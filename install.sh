#!/usr/bin/env bash
#
# Install (or remove) fancontrol-linux on a Fedora-like system.
#
#   sudo ./install.sh              install everything and enable the daemon
#   sudo ./install.sh --no-enable  install without starting the daemon
#   sudo ./install.sh --uninstall  remove it again
#
set -euo pipefail

PREFIX="${PREFIX:-/usr}"
SYSCONFDIR="${SYSCONFDIR:-/etc}"
DATADIR="$PREFIX/share"
UNIT_DIR="${UNIT_DIR:-/usr/lib/systemd/system}"
DBUS_DIR="$DATADIR/dbus-1/system.d"
DESKTOP_DIR="$DATADIR/applications"
CONFIG_DIR="$SYSCONFDIR/fancontrol-linux"

# Fedora marks its system Python as externally managed, so pip refuses to
# install into it. A virtual environment built with --system-site-packages
# keeps the dnf-installed PySide6 and dasbus visible while leaving the system
# Python untouched.
VENV_DIR="${VENV_DIR:-/usr/lib/fancontrol-linux}"
ENTRY_POINTS=(fancontrold fanctl fancontrol-gui fancontrol-sim)
DROPIN_DIR="$UNIT_DIR/fancontrold.service.d"

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
info()  { printf '\033[1m%s\033[0m\n' "$*"; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        red "This needs root. Run: sudo $0 $*"
        exit 1
    fi
}

uninstall() {
    info "Stopping and disabling the daemon"
    systemctl disable --now fancontrold.service 2>/dev/null || true

    rm -f "$UNIT_DIR/fancontrold.service"
    rm -f "$DROPIN_DIR/nvidia.conf"
    rmdir "$DROPIN_DIR" 2>/dev/null || true
    rm -f "$DBUS_DIR/org.fancontrol.Daemon.conf"
    rm -f "$DESKTOP_DIR/io.github.fancontrol_linux.gui.desktop"

    info "Removing the program"
    for entry in "${ENTRY_POINTS[@]}"; do
        rm -f "$PREFIX/bin/$entry"
    done
    rm -rf "$VENV_DIR"

    systemctl daemon-reload
    green "Removed. Your configuration is still at $CONFIG_DIR — delete it yourself if you want it gone."
}

check_dependencies() {
    local missing=()
    python3 -c 'import dasbus' 2>/dev/null || missing+=("python3-dasbus")
    python3 -c 'import PySide6' 2>/dev/null || missing+=("python3-pyside6")
    python3 -c 'import venv' 2>/dev/null || missing+=("python3-libs")
    command -v sensors-detect >/dev/null || missing+=("lm_sensors")

    if [[ ${#missing[@]} -eq 0 ]]; then
        return
    fi

    info "Installing missing dependencies: ${missing[*]}"
    if ! command -v dnf >/dev/null; then
        red "Install these yourself, then run this script again: ${missing[*]}"
        exit 1
    fi
    if ! dnf install -y "${missing[@]}"; then
        red "Could not install: ${missing[*]}"
        echo
        echo "If dnf does not know python3-pyside6, the window needs it from pip"
        echo "instead; the daemon itself will still work without it."
        exit 1
    fi
}

install_program() {
    info "Building the program environment in $VENV_DIR"
    # --system-site-packages so the dnf builds of PySide6 and dasbus are used
    # rather than pulled in again from PyPI.
    python3 -m venv --system-site-packages --upgrade-deps "$VENV_DIR" >/dev/null

    if ! "$VENV_DIR/bin/pip" install --upgrade "$SOURCE_DIR"; then
        red "Installation failed."
        exit 1
    fi

    for entry in "${ENTRY_POINTS[@]}"; do
        ln -sf "$VENV_DIR/bin/$entry" "$PREFIX/bin/$entry"
    done
}

install_all() {
    check_dependencies

    install_program

    info "Installing the system files"
    install -Dm644 "$SOURCE_DIR/data/systemd/fancontrold.service" \
        "$UNIT_DIR/fancontrold.service"
    install -Dm644 "$SOURCE_DIR/data/dbus/org.fancontrol.Daemon.conf" \
        "$DBUS_DIR/org.fancontrol.Daemon.conf"
    install -Dm644 "$SOURCE_DIR/data/applications/io.github.fancontrol_linux.gui.desktop" \
        "$DESKTOP_DIR/io.github.fancontrol_linux.gui.desktop"
    install -dm755 "$CONFIG_DIR"

    # NVML cannot initialise under the unit's strict defaults, so a machine
    # with an NVIDIA card gets a drop-in that relaxes just enough for it.
    rm -f "$DROPIN_DIR/nvidia.conf"
    # install_program already put the package in the venv, so this runs the
    # daemon's own NVML binding rather than guessing from lsmod.
    if "$VENV_DIR/bin/python" - <<'PY' 2>/dev/null
import sys
from fancontrol.hw.nvml import Nvml
nvml = Nvml()
sys.exit(0 if nvml.init() and nvml.device_count() else 1)
PY
    then
        info "NVIDIA GPU detected, relaxing the sandbox so NVML can reach it"
        install -Dm644 "$SOURCE_DIR/data/systemd/fancontrold-nvidia.conf" \
            "$DROPIN_DIR/nvidia.conf"
    else
        info "No NVIDIA GPU found, keeping the strict sandbox"
    fi

    systemctl daemon-reload
    # The bus reads its policy directory on SIGHUP; without this the new rules
    # only take effect after a reboot.
    systemctl reload dbus.service 2>/dev/null || true

    if [[ "${1:-}" != "--no-enable" ]]; then
        if systemctl is-active --quiet fancontrold.service; then
            # enable --now leaves an already-running daemon alone, so a
            # reinstall would keep serving the code it started with.
            info "Restarting the daemon onto the new version"
            systemctl restart fancontrold.service
        else
            info "Enabling the daemon"
            systemctl enable --now fancontrold.service
        fi
        sleep 2
        if systemctl is-active --quiet fancontrold.service; then
            green "fancontrold is running"
        else
            red "fancontrold did not start. What it said:"
            journalctl -u fancontrold.service -n 25 --no-pager || true
        fi
    fi

    green ""
    green "Installed."
    echo
    echo "  fancontrol-gui        open the window"
    echo "  fanctl status         see what the fans are doing"
    echo "  fanctl import f.json  import a FanControl userConfig.json"
    echo
    if ! ls /sys/class/hwmon/hwmon*/pwm1 >/dev/null 2>&1; then
        red "No PWM outputs are visible yet."
        echo "Most desktop boards need their super-I/O driver loaded first:"
        echo
        echo "    sudo sensors-detect          # answer yes to the safe defaults"
        echo "    sudo modprobe nct6775        # or whichever module it names"
        echo
        echo "Then run: sudo fanctl rescan"
    fi
}

case "${1:-}" in
    --uninstall) require_root "$@"; uninstall ;;
    -h|--help)   sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
    *)           require_root "$@"; install_all "${1:-}" ;;
esac

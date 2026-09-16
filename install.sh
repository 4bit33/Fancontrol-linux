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
    rm -f "$DBUS_DIR/org.fancontrol.Daemon.conf"
    rm -f "$DESKTOP_DIR/io.github.fancontrol_linux.gui.desktop"

    info "Removing the Python package"
    python3 -m pip uninstall -y fancontrol-linux >/dev/null 2>&1 || true

    systemctl daemon-reload
    green "Removed. Your configuration is still at $CONFIG_DIR — delete it yourself if you want it gone."
}

check_dependencies() {
    local missing=()
    python3 -c 'import dasbus' 2>/dev/null || missing+=("python3-dasbus")
    python3 -c 'import PySide6' 2>/dev/null || missing+=("python3-pyside6")

    if [[ ${#missing[@]} -gt 0 ]]; then
        info "Installing missing dependencies: ${missing[*]}"
        if command -v dnf >/dev/null; then
            dnf install -y "${missing[@]}"
        else
            red "Install these yourself, then run this script again: ${missing[*]}"
            exit 1
        fi
    fi
}

install_all() {
    check_dependencies

    info "Installing the Python package"
    python3 -m pip install --prefix="$PREFIX" --upgrade "$SOURCE_DIR"

    info "Installing the system files"
    install -Dm644 "$SOURCE_DIR/data/systemd/fancontrold.service" \
        "$UNIT_DIR/fancontrold.service"
    install -Dm644 "$SOURCE_DIR/data/dbus/org.fancontrol.Daemon.conf" \
        "$DBUS_DIR/org.fancontrol.Daemon.conf"
    install -Dm644 "$SOURCE_DIR/data/applications/io.github.fancontrol_linux.gui.desktop" \
        "$DESKTOP_DIR/io.github.fancontrol_linux.gui.desktop"
    install -dm755 "$CONFIG_DIR"

    systemctl daemon-reload
    # The bus reads its policy directory on SIGHUP; without this the new rules
    # only take effect after a reboot.
    systemctl reload dbus.service 2>/dev/null || true

    if [[ "${1:-}" != "--no-enable" ]]; then
        info "Enabling the daemon"
        systemctl enable --now fancontrold.service
        sleep 2
        systemctl --no-pager --lines=0 status fancontrold.service || true
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

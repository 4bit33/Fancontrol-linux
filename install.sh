#!/usr/bin/env bash
#
# Install (or remove) fancontrol-linux.
#
# Works with dnf (Fedora), apt (Debian, Ubuntu), pacman (Arch) and zypper
# (openSUSE). Needs systemd.
#
#   sudo ./install.sh              install everything and enable the daemon
#   sudo ./install.sh --no-enable  install without starting the daemon
#   sudo ./install.sh --uninstall  remove it again
#   sudo ./install.sh --program-only  just the program, no service (CI, packaging)
#
set -euo pipefail

PREFIX="${PREFIX:-/usr}"
SYSCONFDIR="${SYSCONFDIR:-/etc}"
DATADIR="$PREFIX/share"
UNIT_DIR="${UNIT_DIR:-/usr/lib/systemd/system}"
DBUS_DIR="$DATADIR/dbus-1/system.d"
DESKTOP_DIR="$DATADIR/applications"
CONFIG_DIR="$SYSCONFDIR/fancontrol-linux"

# Most distributions mark their system Python as externally managed, so pip
# refuses to install into it. A virtual environment built with
# --system-site-packages uses what the distribution packages - PyGObject above
# all, which is impractical to build from PyPI - and takes the rest from PyPI
# without touching the system Python.
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

# Package names for what the program needs, per package manager. PyGObject
# has to come from the distribution; PySide6 and dasbus are tried there first
# and fetched from PyPI into the program's own environment if that fails, since
# several distributions do not package one or the other.
detect_package_manager() {
    local manager
    for manager in dnf apt-get pacman zypper; do
        command -v "$manager" >/dev/null && { echo "$manager"; return; }
    done
    echo none
}

packages_for() {  # $1 manager, $2 what
    case "$1:$2" in
        dnf:gi)          echo python3-gobject ;;
        dnf:venv)        echo python3-libs ;;
        dnf:sensors)     echo lm_sensors ;;
        dnf:pyside6)     echo python3-pyside6 ;;
        dnf:dasbus)      echo python3-dasbus ;;
        apt-get:gi)      echo python3-gi ;;
        apt-get:venv)    echo python3-venv ;;
        apt-get:sensors) echo lm-sensors ;;
        apt-get:pyside6) echo python3-pyside6.qtwidgets python3-pyside6.qtdbus ;;
        apt-get:dasbus)  echo python3-dasbus ;;
        pacman:gi)       echo python-gobject ;;
        pacman:venv)     echo python ;;
        pacman:sensors)  echo lm_sensors ;;
        pacman:pyside6)  echo pyside6 ;;
        zypper:gi)       echo python3-gobject ;;
        zypper:venv)     echo python3 ;;
        zypper:sensors)  echo sensors ;;
        zypper:pyside6)  echo python3-pyside6 ;;
        zypper:dasbus)   echo python3-dasbus ;;
        # What a PySide6 wheel from PyPI expects the system to provide. A
        # distribution's own PySide6 pulls these in; a wheel cannot.
        dnf:qtlibs)      echo mesa-libGL mesa-libEGL libxkbcommon fontconfig dbus-libs xcb-util-cursor ;;
        apt-get:qtlibs)  echo libgl1 libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libxcb-cursor0 ;;
        pacman:qtlibs)   echo libglvnd libxkbcommon fontconfig dbus xcb-util-cursor ;;
        zypper:qtlibs)   echo Mesa-libGL1 Mesa-libEGL1 libxkbcommon0 fontconfig libdbus-1-3 libxcb-cursor0 ;;
    esac
}

pkg_install() {  # $1 manager, rest: packages. Non-zero on failure.
    local manager="$1"; shift
    [[ $# -eq 0 ]] && return 1
    case "$manager" in
        dnf)     dnf install -y "$@" ;;
        apt-get) apt-get install -y "$@" ;;
        pacman)  pacman -S --needed --noconfirm "$@" ;;
        zypper)  zypper --non-interactive install "$@" ;;
        *)       return 1 ;;
    esac
}

require_systemd() {
    if ! command -v systemctl >/dev/null || [[ ! -d /run/systemd/system ]]; then
        red "This needs systemd, which is not running here."
        red "(--program-only installs the program without the service.)"
        exit 1
    fi
}

check_dependencies() {
    local manager what module
    local -a names packages required=()
    manager="$(detect_package_manager)"
    if [[ "$manager" == apt-get ]]; then
        apt-get update -qq || true
    fi

    python3 -c 'import gi' 2>/dev/null || required+=(gi)
    python3 -c 'import venv, ensurepip' 2>/dev/null || required+=(venv)
    command -v sensors-detect >/dev/null || required+=(sensors)

    if [[ ${#required[@]} -gt 0 ]]; then
        packages=()
        for what in "${required[@]}"; do
            read -ra names <<< "$(packages_for "$manager" "$what")"
            packages+=("${names[@]}")
        done
        info "Installing: ${packages[*]:-nothing known for $manager}"
        if ! pkg_install "$manager" "${packages[@]}"; then
            red "Could not install what this needs. Install PyGObject, Python's"
            red "venv support and lm-sensors yourself, then run this again."
            exit 1
        fi
    fi

    # Optional from the distribution: the environment fetches them otherwise.
    for what in pyside6 dasbus; do
        module=$([[ $what == pyside6 ]] && echo PySide6 || echo dasbus)
        python3 -c "import $module" 2>/dev/null && continue
        read -ra names <<< "$(packages_for "$manager" "$what")"
        [[ ${#names[@]} -eq 0 ]] && continue
        info "Trying the distribution's ${names[*]}"
        pkg_install "$manager" "${names[@]}" >/dev/null 2>&1 \
            || echo "    not available, it will come from PyPI instead"
    done
}

install_program() {
    info "Building the program environment in $VENV_DIR"
    # --system-site-packages so the distribution's PyGObject, and PySide6 and
    # dasbus where it has them, are used rather than built again.
    python3 -m venv --system-site-packages --upgrade-deps "$VENV_DIR" >/dev/null

    if ! "$VENV_DIR/bin/pip" install --upgrade "$SOURCE_DIR"; then
        red "Installation failed."
        exit 1
    fi
    # The window's update notice tells the user where to run git pull.
    if [[ -d "$SOURCE_DIR/.git" ]]; then
        printf '%s\n' "$SOURCE_DIR" > "$VENV_DIR/source-dir"
    else
        rm -f "$VENV_DIR/source-dir"
    fi

    # Whatever the distribution could not provide comes from PyPI, into this
    # environment only.
    local -a fetch=()
    "$VENV_DIR/bin/python" -c 'import dasbus' 2>/dev/null || fetch+=(dasbus)
    "$VENV_DIR/bin/python" -c 'from PySide6 import QtWidgets, QtDBus' 2>/dev/null \
        || fetch+=(PySide6-Essentials)
    if [[ ${#fetch[@]} -gt 0 ]]; then
        info "Fetching from PyPI: ${fetch[*]}"
        if ! "$VENV_DIR/bin/pip" install "${fetch[@]}"; then
            red "Could not fetch ${fetch[*]}."
            exit 1
        fi
    fi

    # A PySide6 wheel links against system libraries that a minimal install
    # may lack, and then fails with "libGL.so.1: cannot open shared object".
    if ! "$VENV_DIR/bin/python" -c 'from PySide6 import QtWidgets' 2>/dev/null; then
        local manager
        local -a libs
        manager="$(detect_package_manager)"
        read -ra libs <<< "$(packages_for "$manager" qtlibs)"
        info "Installing the libraries the window needs: ${libs[*]:-unknown for $manager}"
        pkg_install "$manager" "${libs[@]}" >/dev/null 2>&1 || true
        if ! "$VENV_DIR/bin/python" -c 'from PySide6 import QtWidgets' 2>/dev/null; then
            red "The window will not start: $("$VENV_DIR/bin/python" -c 'from PySide6 import QtWidgets' 2>&1 | tail -1)"
            red "The daemon and fanctl work without it."
        fi
    fi

    for entry in "${ENTRY_POINTS[@]}"; do
        ln -sf "$VENV_DIR/bin/$entry" "$PREFIX/bin/$entry"
    done
}

install_bus_policy() {
    # Keep only the administrators' groups this machine actually has, so the
    # bus never deals with a policy naming a group that does not exist.
    local target="$DBUS_DIR/org.fancontrol.Daemon.conf" group
    local -a present=()
    for group in wheel sudo admin; do
        getent group "$group" >/dev/null && present+=("$group")
    done
    install -d "$DBUS_DIR"
    python3 -c '
import re, sys
source, target, *groups = sys.argv[1:]
text = open(source).read()
keep = lambda m: m.group(0) if m.group(1) in groups else ""
text = re.sub(r"  <policy group=\"([^\"]+)\">.*?</policy>\n?", keep, text, flags=re.S)
open(target, "w").write(text)
' "$SOURCE_DIR/data/dbus/org.fancontrol.Daemon.conf" "$target" "${present[@]}"
    chmod 644 "$target"
    if [[ ${#present[@]} -eq 0 ]]; then
        red "None of wheel, sudo or admin exists here, so only root can change"
        red "the fans. Edit $target to name your administrators' group."
    else
        echo "    fan settings can be changed by members of: ${present[*]}"
    fi
}

install_all() {
    require_systemd
    check_dependencies

    install_program

    info "Installing the system files"
    install -Dm644 "$SOURCE_DIR/data/systemd/fancontrold.service" \
        "$UNIT_DIR/fancontrold.service"
    install_bus_policy
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
        echo "    $DROPIN_DIR/nvidia.conf"
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
    --program-only)
        # Dependencies and the program itself, without the service, the bus
        # policy or the menu entry: for containers, CI and packagers.
        require_root "$@"; check_dependencies; install_program
        green "Program installed in $VENV_DIR" ;;
    -h|--help)   sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' ;;
    *)           require_root "$@"; install_all "${1:-}" ;;
esac

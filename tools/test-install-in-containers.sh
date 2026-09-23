#!/usr/bin/env bash
#
# Run install.sh --program-only in clean containers of several distributions
# and check the result actually imports and starts.
#
#   ./tools/test-install-in-containers.sh            # all of them
#   ./tools/test-install-in-containers.sh ubuntu     # just the ones matching
#
# Needs podman (or docker, via ENGINE=docker). Containers have no systemd, so
# this covers the dependency and program half of the installer - the half
# that differs between distributions.
set -u

ENGINE="${ENGINE:-podman}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FILTER="${1:-}"

# Mount a copy of the tracked (and new, unignored) files rather than the
# working tree: relabelling for SELinux would touch every file in the repo, and
# stray root-owned build directories make it fail outright.
SOURCE="$(mktemp -d)"
trap 'rm -rf "$SOURCE"' EXIT
(cd "$REPO" && git ls-files -co --exclude-standard -z | xargs -0 cp --parents -t "$SOURCE")

# image | command that makes bash and python3 available
IMAGES=(
    "registry.fedoraproject.org/fedora:44|dnf install -y -q python3 findutils >/dev/null"
    "docker.io/library/ubuntu:24.04|apt-get update -qq && apt-get install -y -qq python3 >/dev/null"
    "docker.io/library/debian:trixie|apt-get update -qq && apt-get install -y -qq python3 >/dev/null"
    "docker.io/library/archlinux:latest|pacman -Sy --noconfirm --needed python >/dev/null"
    "registry.opensuse.org/opensuse/tumbleweed:latest|zypper -q --non-interactive install python3 >/dev/null"
)

CHECK='
set -e
export QT_QPA_PLATFORM=offscreen
fanctl --version
fancontrold --version
/usr/lib/fancontrol-linux/bin/python -c "import dasbus.connection; print(\"dasbus ok\")"
/usr/lib/fancontrol-linux/bin/python -c "from PySide6 import QtWidgets, QtDBus; print(\"PySide6 ok\")"
/usr/lib/fancontrol-linux/bin/python -c "import fancontrol.daemon.dbus_service; print(\"daemon imports ok\")"
'

status=0
for entry in "${IMAGES[@]}"; do
    image="${entry%%|*}"
    prepare="${entry#*|}"
    [[ -n "$FILTER" && "$image" != *"$FILTER"* ]] && continue
    echo "=== $image"
    # Copy the source in, so the build never writes into the working tree.
    if "$ENGINE" run --rm -v "$SOURCE:/src:ro,Z" "$image" bash -c "
        $prepare
        cp -r /src /tmp/src && cd /tmp/src
        ./install.sh --program-only >/tmp/install.log 2>&1 || { tail -30 /tmp/install.log; exit 1; }
        grep -E 'Installing:|Trying|not available|Fetching' /tmp/install.log | sed 's/\x1b\[[0-9;]*m//g'
        $CHECK
    "; then
        echo "--- $image: OK"
    else
        echo "--- $image: FAILED"
        status=1
    fi
done
exit $status

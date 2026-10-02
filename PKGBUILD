# Maintainer: 4bit33 <luisadgg5@gmail.com>
# An AUR package for fancontrol-linux. To publish a release (needs an AUR
# account with an SSH key, see https://wiki.archlinux.org/title/AUR):
#
#   tools/aur-publish.sh v1.2.0
#
# which copies this file together with a fresh .SRCINFO into your AUR clone.
# To install from a checkout instead: makepkg -si
pkgbase=fancontrol-linux
pkgname=('fancontrol-linux' 'fancontrol-linux-nvidia')
pkgver=1.2.0
pkgrel=1
pkgdesc="Fan control for Linux, in the shape of FanControl for Windows"
arch=('any')
url="https://github.com/4bit33/Fancontrol-linux"
license=('GPL-3.0-or-later')
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
checkdepends=('python-pytest')
source=("$pkgbase-$pkgver.tar.gz::$url/archive/v$pkgver/$pkgbase-$pkgver.tar.gz")
# The tag is not out yet; tools/aur-publish.sh fills in the real checksum when
# it publishes the release.
sha256sums=('SKIP')

build() {
    cd "Fancontrol-linux-$pkgver"
    python -m build --wheel --no-isolation
}

check() {
    cd "Fancontrol-linux-$pkgver"
    QT_QPA_PLATFORM=offscreen python -m pytest -q
}

package_fancontrol-linux() {
    pkgdesc="Fan control for Linux, in the shape of FanControl for Windows"
    depends=('python' 'python-gobject' 'python-dasbus' 'pyside6' 'systemd')
    optdepends=('lm_sensors: detect motherboard sensors and load super-I/O drivers')
    cd "Fancontrol-linux-$pkgver"

    python -m installer --destdir="$pkgdir" dist/*.whl

    # The unit in the tree points at install.sh's own environment; a package
    # uses the system interpreter.
    sed 's|^ExecStart=.*|ExecStart=/usr/bin/python -m fancontrol.daemon|' \
        data/systemd/fancontrold.service > fancontrold.service
    install -Dpm644 fancontrold.service "$pkgdir/usr/lib/systemd/system/fancontrold.service"

    # Arch administrators are the wheel group; drop the Debian and Ubuntu
    # blocks.
    python - data/dbus/org.fancontrol.Daemon.conf org.fancontrol.Daemon.conf <<'EOF'
import re, sys
text = open(sys.argv[1]).read()
keep = lambda m: m.group(0) if m.group(1) == "wheel" else ""
open(sys.argv[2], "w").write(
    re.sub(r'  <policy group="([^"]+)">.*?</policy>\n?', keep, text, flags=re.S))
EOF
    install -Dpm644 org.fancontrol.Daemon.conf \
        "$pkgdir/usr/share/dbus-1/system.d/org.fancontrol.Daemon.conf"

    install -Dpm644 data/applications/io.github.fancontrol_linux.gui.desktop \
        "$pkgdir/usr/share/applications/io.github.fancontrol_linux.gui.desktop"

    # Restarting on upgrade is what makes an update take effect.
    install -Dm644 data/pacman/fancontrold-restart.hook \
        "$pkgdir/usr/share/libalpm/hooks/fancontrold-restart.hook"

    # Tells the window's update notice to point at the AUR rather than git.
    install -d "$pkgdir/usr/share/$pkgbase"
    echo aur > "$pkgdir/usr/share/$pkgbase/installed-by"

    # The daemon writes config.json here; ProtectSystem=strict needs it to exist.
    install -dm755 "$pkgdir/etc/$pkgbase"

    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgbase/LICENSE"
}

package_fancontrol-linux-nvidia() {
    pkgdesc="Let fancontrol-linux drive NVIDIA graphics card fans"
    depends=("fancontrol-linux=$pkgver-$pkgrel")
    cd "Fancontrol-linux-$pkgver"

    install -Dpm644 data/systemd/fancontrold-nvidia.conf \
        "$pkgdir/usr/lib/systemd/system/fancontrold.service.d/nvidia.conf"
}

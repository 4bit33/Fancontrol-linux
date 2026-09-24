"""Telling the user that a newer release is out.

Once a day the window asks GitHub which release is the newest and, if it is
newer than this one, shows a notice with the commands to update. It never
downloads or installs anything itself: the daemon runs as root, and code that
fetches and runs other code as root is exactly the kind of thing to leave to
the user and their package manager.

The check can be switched off in Settings. It is the program's only network
request, and sends nothing but the usual HTTP headers.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import __version__
from . import i18n

log = logging.getLogger(__name__)

REPOSITORY = "4bit33/Fancontrol-linux"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"

#: Seconds between two checks.
CHECK_EVERY = 24 * 60 * 60


def parse_version(text: str) -> tuple[int, ...] | None:
    """``"v1.2.0"`` → ``(1, 2, 0)``; None for anything that is not a version."""

    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def source_dir() -> Path | None:
    """The git clone the program was installed from, if the installer noted it."""

    try:
        text = (Path(sys.prefix) / "source-dir").read_text().strip()
    except OSError:
        return None
    return Path(text) if text and Path(text, ".git").is_dir() else None


def installed_by_package() -> bool:
    """True when the program came from the RPM rather than install.sh."""
    return (Path(sys.prefix) / "share" / "fancontrol-linux" / "installed-by").exists()


def update_command(source: Path | None = None, packaged: bool | None = None) -> str:
    """What to type to update. Both install.sh and the package restart the
    daemon themselves."""

    if packaged if packaged is not None else installed_by_package():
        return "sudo dnf upgrade --refresh fancontrol-linux"
    folder = shlex.quote(str(source)) if source else "<the folder you installed from>"
    return f"cd {folder} && git pull && sudo ./install.sh"


def settings() -> QSettings:
    return QSettings(*i18n.SETTINGS_SCOPE)


def checking_enabled(store: QSettings | None = None) -> bool:
    value = (store or settings()).value("updates/check", True)
    # QSettings hands back strings from the ini file.
    return value not in (False, "false", "0", 0)


def set_checking_enabled(enabled: bool, store: QSettings | None = None) -> None:
    store = store or settings()
    store.setValue("updates/check", bool(enabled))
    store.sync()


class UpdateChecker(QObject):
    """Asks GitHub for the newest release, at most once a day."""

    #: version, page with the release notes
    updateAvailable = Signal(str, str)

    def __init__(self, parent: QObject | None = None, current: str = __version__,
                 store: QSettings | None = None) -> None:
        super().__init__(parent)
        self.current = current
        self.store = store or settings()
        self._network: QNetworkAccessManager | None = None

    def check(self, force: bool = False) -> None:
        """Announce a known newer release, and ask again if a day has passed."""

        if not force and not checking_enabled(self.store):
            return
        # What the last check found stays valid until the next one.
        self._announce(str(self.store.value("updates/latest", "")),
                       str(self.store.value("updates/url", "")))

        last = float(self.store.value("updates/last_check", 0) or 0)
        if not force and time.time() - last < CHECK_EVERY:
            return

        if self._network is None:
            self._network = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(LATEST_URL))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"fancontrol-linux/{self.current}".encode())
        request.setTransferTimeout(15000)
        reply = self._network.get(request)
        reply.finished.connect(lambda: self._on_reply(reply))

    def _on_reply(self, reply: QNetworkReply) -> None:
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            # Offline, or GitHub unreachable: say nothing, try again next time.
            log.debug("update check failed: %s", reply.errorString())
            return
        try:
            payload = json.loads(bytes(reply.readAll().data()).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        self.handle(payload)

    def handle(self, payload: dict) -> None:
        """Remember what GitHub said and announce it if it is newer."""

        if not isinstance(payload, dict):
            return
        tag = str(payload.get("tag_name") or "")
        url = str(payload.get("html_url") or RELEASES_PAGE)
        if payload.get("draft") or payload.get("prerelease") or parse_version(tag) is None:
            return
        self.store.setValue("updates/last_check", time.time())
        self.store.setValue("updates/latest", tag)
        self.store.setValue("updates/url", url)
        self.store.sync()
        self._announce(tag, url)

    def _announce(self, tag: str, url: str) -> None:
        if not tag or not is_newer(tag, self.current):
            return
        if str(self.store.value("updates/dismissed", "")) == tag:
            return
        self.updateAvailable.emit(tag.lstrip("v"), url or RELEASES_PAGE)

    def dismiss(self, version: str) -> None:
        """Do not mention this version again; a later one will still show."""

        latest = str(self.store.value("updates/latest", "")) or version
        self.store.setValue("updates/dismissed", latest)
        self.store.sync()

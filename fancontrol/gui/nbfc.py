"""Fan speed percent from nbfc, where the kernel tachometers read zero.

On many laptops (HP Pavilion included) the ``fan*_input`` files never move,
while nbfc talks to the embedded controller and knows the real speed level
in percent. The window can show that instead of revolutions - see the "Fan
speeds" row in Settings. When nbfc is not installed or its service is not
running, the window quietly falls back to revolutions.

This module needs no Qt, so its parsing is tested without it.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time

log = logging.getLogger(__name__)

#: "Current Fan Speed        : 47.50" in the output of ``nbfc status``.
SPEED_RE = re.compile(r"^Current Fan Speed\s*:\s*([-+]?\d+(?:\.\d+)?)\s*$", re.M)

DISPLAY_KEY = "fans/display"

#: Re-asking nbfc on every daemon tick would spawn a process per second.
_QUERY_EVERY = 5.0
_last_at = 0.0
_last: list[float] | None = None


def parse_speeds(text: str) -> list[float]:
    """Every "Current Fan Speed" value in ``nbfc status`` output, in order."""

    return [float(match) for match in SPEED_RE.findall(text or "")]


def query() -> list[float] | None:
    """Ask nbfc for its fan speeds, or None when it cannot answer."""

    exe = shutil.which("nbfc")
    if exe is None:
        return None
    try:
        completed = subprocess.run(
            [exe, "status"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("nbfc status failed: %s", exc)
        return None
    if completed.returncode != 0:
        return None
    return parse_speeds(completed.stdout) or None


def current_speed() -> float | None:
    """The first nbfc fan speed, re-queried at most every few seconds."""

    global _last_at, _last
    now = time.monotonic()
    if now - _last_at > _QUERY_EVERY:
        _last_at, _last = now, query()
    return _last[0] if _last else None


def settings():
    from PySide6.QtCore import QSettings

    from . import i18n

    return QSettings(*i18n.SETTINGS_SCOPE)


#: What the fan-speed chooser offers.
DISPLAYS = ("rpm", "percent")


def saved_display(store=None) -> str:
    """"rpm" or "percent": what the fan tiles show."""

    value = (store or settings()).value(DISPLAY_KEY, "rpm")
    return value if value in DISPLAYS else "rpm"


def save_display(mode: str, store=None) -> None:
    """Remember how the fan tiles show speed."""

    store = store or settings()
    if mode == "percent":
        store.setValue(DISPLAY_KEY, mode)
    else:
        store.remove(DISPLAY_KEY)
    store.sync()

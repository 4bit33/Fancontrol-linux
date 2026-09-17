"""Remembering which outputs the daemon holds, in case it does not stop cleanly.

Stopping normally puts every ``pwm*_enable`` back the way the firmware had it.
Being killed does not, and the fan then stays pinned at whatever percentage it
was last given - a stuck fan that no later run will touch, because a fresh
daemon has no reason to think that output is anything to do with it.

So while an output is held, it is recorded here along with what restoring it
needs. The next start reads the file, hands back anything listed and clears it.

The file lives under ``/run``, which is exactly the right lifetime: it survives
a crash, and a reboot wipes it because a reboot reloads the driver and resets
the outputs anyway.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("/run/fancontrold/acquired.json")


def state_path() -> Path:
    return Path(os.environ.get("FANCONTROL_STATE", DEFAULT_PATH))


def load(path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = path or state_path()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if isinstance(value, dict)}


def save(held: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    path = path or state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if held:
            path.write_text(json.dumps(held))
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        # Not fatal: it only costs the recovery, not the fan control.
        log.debug("cannot write %s: %s", path, exc)


def clear(path: Path | None = None) -> None:
    save({}, path)

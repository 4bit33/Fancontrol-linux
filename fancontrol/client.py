"""D-Bus client used by the command line tool.

The GUI uses Qt's own D-Bus bindings so it does not need a GLib main loop; this
client exists for the terminal, where dasbus is the simplest option.
"""

from __future__ import annotations

import json
from typing import Any


class DaemonError(Exception):
    pass


class DaemonClient:
    """A thin, synchronous wrapper around the daemon's D-Bus interface."""

    def __init__(self, session: bool = False) -> None:
        try:
            from dasbus.connection import SessionMessageBus, SystemMessageBus
        except ImportError as exc:  # pragma: no cover - depends on the system
            raise DaemonError(
                "dasbus is not installed. Install it with "
                "'sudo dnf install python3-dasbus'."
            ) from exc

        from .daemon.dbus_service import BUS_NAME, OBJECT_PATH

        bus = SessionMessageBus() if session else SystemMessageBus()
        try:
            self._proxy = bus.get_proxy(BUS_NAME, OBJECT_PATH)
        except Exception as exc:
            raise DaemonError(
                f"cannot reach the fan control daemon ({exc}). Is it running? "
                "Try 'systemctl status fancontrold'."
            ) from exc

    def _call(self, method: str, *args: Any) -> Any:
        try:
            raw = getattr(self._proxy, method)(*args)
        except Exception as exc:
            raise DaemonError(f"{method} failed: {exc}") from exc
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    # -- read ---------------------------------------------------------

    def version(self) -> str:
        return self._call("GetVersion")

    def inventory(self) -> dict:
        return self._call("GetInventory")

    def status(self) -> dict:
        return self._call("GetStatus")

    def config(self) -> dict:
        return self._call("GetConfig")

    # -- write --------------------------------------------------------

    def set_config(self, config: dict) -> dict:
        return self._call("SetConfig", json.dumps(config))

    def reload(self) -> dict:
        return self._call("ReloadConfig")

    def rescan(self) -> dict:
        return self._call("Rescan")

    def set_override(self, control_id: str, percent: float | None) -> dict:
        return self._call("SetOverride", control_id, -1.0 if percent is None else float(percent))

    def set_control_enabled(self, enabled: bool) -> dict:
        return self._call("SetControlEnabled", bool(enabled))

    def calibrate(self, control_id: str) -> dict:
        return self._call("Calibrate", control_id)

    def import_fancontrol(self, path: str = "", text: str = "") -> dict:
        return self._call("ImportFanControl", path, text)

    def apply_import(self, config: dict, mapping: dict, merge: bool = False) -> dict:
        return self._call("ApplyImport", json.dumps(config), json.dumps(mapping), bool(merge))

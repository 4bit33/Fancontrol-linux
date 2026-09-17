"""How the window talks to the daemon.

Two interchangeable proxies:

``DBusProxy``   the normal case - the daemon runs as root and we call it over
                the system bus. Qt's own D-Bus bindings are used so the GUI
                keeps a single Qt event loop.
``LocalProxy``  runs the service inside the GUI process. Needed for
                ``--local``, which is how the application is tried against the
                simulator and how it can be used before anything is installed.

Both emit the same signals, so nothing above this module knows which is in use.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, SLOT, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

from ..dbus_names import BUS_NAME, INTERFACE, OBJECT_PATH

log = logging.getLogger(__name__)


class ProxyError(Exception):
    pass


class BaseProxy(QObject):
    """The interface the window codes against."""

    statusChanged = Signal(dict)
    configChanged = Signal(dict)
    failed = Signal(str)

    def inventory(self) -> dict:
        raise NotImplementedError

    def status(self) -> dict:
        raise NotImplementedError

    def config(self) -> dict:
        raise NotImplementedError

    def set_config(self, config: dict) -> dict:
        raise NotImplementedError

    def set_override(self, control_id: str, percent: float | None) -> dict:
        raise NotImplementedError

    def set_control_enabled(self, enabled: bool) -> dict:
        raise NotImplementedError

    def rescan(self) -> dict:
        raise NotImplementedError

    def calibrate(self, control_id: str) -> dict:
        raise NotImplementedError

    def import_fancontrol(self, text: str) -> dict:
        raise NotImplementedError

    def apply_import(self, config: dict, mapping: dict, merge: bool) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # -- shared helpers ------------------------------------------------

    def push_config(self, config: dict) -> bool:
        """Send a configuration and report the outcome through ``failed``."""

        result = self.set_config(config)
        if not result.get("ok"):
            message = result.get("error", "the daemon rejected the configuration")
            problems = result.get("problems") or []
            if problems:
                message += ":\n  " + "\n  ".join(problems)
            self.failed.emit(message)
            return False
        self.configChanged.emit(self.config())
        return True


class DBusProxy(BaseProxy):
    """Talks to ``org.fancontrol.Daemon`` over D-Bus."""

    def __init__(self, session: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = QDBusConnection.sessionBus() if session else QDBusConnection.systemBus()
        if not self._bus.isConnected():
            raise ProxyError("cannot connect to the D-Bus daemon")

        self._iface = QDBusInterface(BUS_NAME, OBJECT_PATH, INTERFACE, self._bus, self)
        # Importing a configuration and rescanning the hardware both take
        # longer than Qt's 25 second default.
        self._iface.setTimeout(120_000)
        if not self._iface.isValid():
            raise ProxyError(
                "the fan control daemon is not running.\n\n"
                "Start it with:\n    sudo systemctl start fancontrold"
            )

        connected = self._bus.connect(
            BUS_NAME, OBJECT_PATH, INTERFACE, "StatusChanged",
            self, SLOT("_onStatusChanged(QString)"),
        )
        if not connected:
            # Not fatal: fall back to polling so the window still updates.
            log.warning("could not subscribe to StatusChanged, falling back to polling")
            timer = QTimer(self)
            timer.timeout.connect(lambda: self.statusChanged.emit(self.status()))
            timer.start(1000)

    @Slot(str)
    def _onStatusChanged(self, payload: str) -> None:
        try:
            self.statusChanged.emit(json.loads(payload))
        except json.JSONDecodeError:
            log.warning("ignoring a malformed status message")

    def _call(self, method: str, *args: Any) -> Any:
        reply = self._iface.call(method, *args)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            raise ProxyError(f"{method} failed: {reply.errorMessage()}")
        arguments = reply.arguments()
        if not arguments:
            return {}
        value = arguments[0]
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    def inventory(self) -> dict:
        return self._call("GetInventory")

    def status(self) -> dict:
        return self._call("GetStatus")

    def config(self) -> dict:
        return self._call("GetConfig")

    def set_config(self, config: dict) -> dict:
        return self._call("SetConfig", json.dumps(config))

    def set_override(self, control_id: str, percent: float | None) -> dict:
        return self._call("SetOverride", control_id, -1.0 if percent is None else float(percent))

    def set_control_enabled(self, enabled: bool) -> dict:
        return self._call("SetControlEnabled", bool(enabled))

    def rescan(self) -> dict:
        return self._call("Rescan")

    def calibrate(self, control_id: str) -> dict:
        return self._call("Calibrate", control_id)

    def import_fancontrol(self, text: str) -> dict:
        return self._call("ImportFanControl", "", text)

    def apply_import(self, config: dict, mapping: dict, merge: bool) -> dict:
        return self._call("ApplyImport", json.dumps(config), json.dumps(mapping), bool(merge))


class LocalProxy(BaseProxy):
    """Runs the service in this process, ticking from a Qt timer."""

    def __init__(self, config_path: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from ..daemon.service import FanControlService

        self._service = FanControlService(config_path=config_path)
        self._service.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(max(0.2, self._service.config.settings.update_interval) * 1000))
        self._tick()

    def _tick(self) -> None:
        try:
            self.statusChanged.emit(self._service.tick())
        except Exception:
            log.exception("control loop tick failed")

    def inventory(self) -> dict:
        return self._service.get_inventory()

    def status(self) -> dict:
        return self._service.get_status()

    def config(self) -> dict:
        return self._service.get_config()

    def set_config(self, config: dict) -> dict:
        result = self._service.set_config(config)
        if result.get("ok"):
            # The tick rate is part of the configuration, so follow it.
            self._timer.setInterval(
                int(max(0.2, self._service.config.settings.update_interval) * 1000)
            )
        return result

    def set_override(self, control_id: str, percent: float | None) -> dict:
        return self._service.set_override(control_id, percent)

    def set_control_enabled(self, enabled: bool) -> dict:
        return self._service.set_control_enabled(enabled)

    def rescan(self) -> dict:
        return self._service.rescan()

    def calibrate(self, control_id: str) -> dict:
        return self._service.calibrate(control_id)

    def import_fancontrol(self, text: str) -> dict:
        return self._service.import_fancontrol(text=text)

    def apply_import(self, config: dict, mapping: dict, merge: bool) -> dict:
        return self._service.apply_import(config, mapping, merge=merge)

    def close(self) -> None:
        self._timer.stop()
        self._service.shutdown()

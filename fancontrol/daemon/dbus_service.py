"""D-Bus front end for :class:`~fancontrol.daemon.service.FanControlService`.

Everything that is not a scalar crosses the bus as a JSON string. Fan
configurations are deeply nested and change shape as curve types are added, and
a JSON payload keeps the interface stable instead of forcing a new D-Bus
signature every time a curve gains a field.

Bus name      ``org.fancontrol.Daemon``
Object path   ``/org/fancontrol/Daemon``
Interface     ``org.fancontrol.Daemon1``

Who may call which method is decided by the bus policy in
``data/dbus/org.fancontrol.Daemon.conf``: reading is open to everyone, changing
the configuration is restricted to the ``wheel`` group.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from dasbus.connection import SessionMessageBus, SystemMessageBus
from dasbus.loop import EventLoop
from dasbus.server.interface import dbus_interface, dbus_signal
from dasbus.typing import Bool, Double, Str

from ..dbus_names import BUS_NAME, INTERFACE, OBJECT_PATH
from .service import FanControlService, fail

log = logging.getLogger(__name__)


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _loads(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} is not valid JSON: {exc}") from exc


@dbus_interface(INTERFACE)
class FanControlDBusInterface:
    """The published object. Each method mirrors one service method."""

    def __init__(self, service: FanControlService) -> None:
        self._service = service
        service.add_listener(self._on_status)

    # -- signals ------------------------------------------------------

    @dbus_signal
    def StatusChanged(self, status: Str):
        """Emitted once per control loop tick with the full status as JSON."""

    def _on_status(self, status: dict[str, Any]) -> None:
        try:
            self.StatusChanged.emit(_dumps(status))
        except Exception:
            log.exception("could not emit StatusChanged")

    # -- read ---------------------------------------------------------

    def GetVersion(self) -> Str:
        from .. import __version__

        return __version__

    def GetInventory(self) -> Str:
        """Every sensor and control the daemon found, as JSON."""
        return _dumps(self._service.get_inventory())

    def GetStatus(self) -> Str:
        """The most recent control loop tick, as JSON."""
        return _dumps(self._service.get_status())

    def GetConfig(self) -> Str:
        """The active configuration, as JSON."""
        return _dumps(self._service.get_config())

    # -- write --------------------------------------------------------

    def SetConfig(self, config_json: Str) -> Str:
        try:
            data = _loads(config_json, "configuration")
        except ValueError as exc:
            return _dumps(fail(str(exc)))
        return _dumps(self._service.set_config(data))

    def SaveConfig(self) -> Str:
        return _dumps(self._service.save_config())

    def ReloadConfig(self) -> Str:
        return _dumps(self._service.reload_config())

    def Rescan(self) -> Str:
        return _dumps(self._service.rescan())

    def SetOverride(self, control_id: Str, percent: Double) -> Str:
        """Drive a control by hand. A negative percentage clears the override."""
        value = None if percent < 0 else float(percent)
        return _dumps(self._service.set_override(control_id, value))

    def SetControlEnabled(self, enabled: Bool) -> Str:
        return _dumps(self._service.set_control_enabled(bool(enabled)))

    def Calibrate(self, control_id: Str) -> Str:
        """Measure a fan's start and stop points. Takes about a minute."""
        return _dumps(self._service.calibrate(control_id))

    # -- import -------------------------------------------------------

    def ImportFanControl(self, path: Str, text: Str) -> Str:
        """Convert a Windows FanControl config and report the proposed mapping.

        Pass either a path the daemon can read or the file's contents; nothing
        is applied until :meth:`ApplyImport` is called.
        """
        return _dumps(self._service.import_fancontrol(path=path, text=text))

    def ApplyImport(self, config_json: Str, mapping_json: Str, merge: Bool) -> Str:
        try:
            config_data = _loads(config_json, "imported configuration")
            mapping = _loads(mapping_json, "mapping")
        except ValueError as exc:
            return _dumps(fail(str(exc)))
        if not isinstance(mapping, dict):
            return _dumps(fail("mapping must be an object of identifier to identifier"))
        return _dumps(self._service.apply_import(config_data, mapping, merge=bool(merge)))


def serve(service: FanControlService, session: bool = False) -> int:
    """Publish the service and run the GLib main loop until interrupted."""

    bus = SessionMessageBus() if session else SystemMessageBus()
    interface = FanControlDBusInterface(service)

    bus.publish_object(OBJECT_PATH, interface)
    bus.register_service(BUS_NAME)
    log.info("published %s on the %s bus", BUS_NAME, "session" if session else "system")

    loop = EventLoop()
    service.start_background()
    try:
        loop.run()
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        service.shutdown()
        try:
            bus.unregister_service(BUS_NAME)
            bus.disconnect()
        except Exception:
            log.debug("bus teardown failed", exc_info=True)
    return 0

"""NVIDIA GPU backend built on NVML.

Identifier scheme: ``nvidia:<uuid>:<channel>`` where the UUID is the one NVML
reports (``GPU-xxxxxxxx-...``), so identifiers survive a reboot even if the PCI
enumeration order changes. Channels are ``temp``, ``fan<N>`` and ``pwm<N>``.

Writing a fan speed needs a driver that exposes the fan control API and a
process running as root. When either is missing the GPU still shows up, but its
controls are reported as read-only instead of disappearing, so the UI can tell
the user why.
"""

from __future__ import annotations

import logging
from ctypes import c_void_p
from dataclasses import dataclass, field

from .base import DeviceInfo, FanSensor, PwmOutput, TempSensor, sanitize
from .nvml import NVML_FAN_POLICY_MANUAL, PERMANENT_SET_FAILURES, Nvml, NvmlError

log = logging.getLogger(__name__)


@dataclass
class NvidiaTempSensor(TempSensor):
    nvml: Nvml = field(default_factory=Nvml)
    handle: c_void_p = field(default_factory=c_void_p)

    def read(self) -> float | None:
        try:
            return self.nvml.temperature(self.handle)
        except NvmlError:
            return None


@dataclass
class NvidiaFanSensor(FanSensor):
    nvml: Nvml = field(default_factory=Nvml)
    handle: c_void_p = field(default_factory=c_void_p)
    fan_index: int = 0

    def read(self) -> float | None:
        try:
            return self.nvml.fan_rpm(self.handle, self.fan_index)
        except NvmlError:
            return None


@dataclass
class NvidiaPwmOutput(PwmOutput):
    nvml: Nvml = field(default_factory=Nvml)
    handle: c_void_p = field(default_factory=c_void_p)
    fan_index: int = 0
    #: Cleared the first time the driver refuses a speed for a reason that will
    #: not change, so the loop stops asking every second.
    writable: bool = True
    refusal: str = ""
    _acquired: bool = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    def read_percent(self) -> float | None:
        try:
            return self.nvml.fan_percent(self.handle, self.fan_index)
        except NvmlError:
            return None

    def adopt(self, state: dict) -> None:
        # There is nothing to remember: handing a GPU fan back is one call.
        self._acquired = True

    def acquire(self) -> None:
        # NVML has no separate "take control" call: writing a speed switches
        # the fan to manual, and nvmlDeviceSetDefaultFanSpeed_v2 gives it back.
        # Some drivers want the policy set first; older ones have no such call,
        # so a failure here is not worth reporting.
        try:
            self.nvml.set_fan_control_policy(
                self.handle, self.fan_index, NVML_FAN_POLICY_MANUAL
            )
        except NvmlError as exc:
            log.debug("%s: fan control policy not set: %s", self.id, exc)
        self._acquired = True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            self.nvml.set_fan_default(self.handle, self.fan_index)
        except NvmlError as exc:
            log.warning("cannot restore automatic fan control on %s: %s", self.id, exc)
        self._acquired = False

    def set_percent(self, percent: float) -> None:
        if not self.writable:
            raise OSError(self.refusal)
        if not self._acquired:
            self.acquire()

        value = int(round(max(0.0, min(100.0, percent))))
        try:
            self.nvml.set_fan_percent(self.handle, self.fan_index, value)
        except NvmlError as exc:
            if exc.code in PERMANENT_SET_FAILURES:
                # Asking again every second would only repeat the same refusal,
                # so stop, and say it once in terms that suggest what it means.
                self.writable = False
                self.refusal = (
                    f"the driver will not let this program set the speed of "
                    f"{self.name}: {exc}"
                )
                log.warning("%s: %s", self.id, self.refusal)
                raise OSError(self.refusal) from exc
            raise OSError(str(exc)) from exc
        self._acquired = True

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["writable"] = self.writable
        if self.refusal:
            data["refusal"] = self.refusal
        return data


class NvidiaBackend:
    """Enumerates NVIDIA GPUs and their fans through NVML."""

    name = "nvidia"

    def __init__(self) -> None:
        self.nvml = Nvml()
        self._outputs: list[NvidiaPwmOutput] = []

    def discover(self) -> tuple[list[TempSensor], list[FanSensor], list[PwmOutput]]:
        temps: list[TempSensor] = []
        fans: list[FanSensor] = []
        pwms: list[PwmOutput] = []
        self._outputs = []

        if not self.nvml.init():
            return temps, fans, pwms

        try:
            count = self.nvml.device_count()
        except NvmlError as exc:
            log.warning("cannot enumerate NVIDIA GPUs: %s", exc)
            return temps, fans, pwms

        for index in range(count):
            try:
                handle = self.nvml.device_handle(index)
                name = self.nvml.device_name(handle)
                uuid = self.nvml.device_uuid(handle)
            except NvmlError as exc:
                log.warning("skipping NVIDIA GPU %d: %s", index, exc)
                continue

            device = DeviceInfo(
                key=sanitize(uuid),
                chip="nvidia",
                label=name,
                backend=self.name,
                details={"uuid": uuid, "index": str(index)},
            )

            temps.append(
                NvidiaTempSensor(
                    id=f"nvidia:{device.key}:temp",
                    name=f"{name} GPU",
                    device=device,
                    nvml=self.nvml,
                    handle=handle,
                )
            )

            try:
                fan_count = self.nvml.num_fans(handle)
            except NvmlError:
                fan_count = 0

            for fan_index in range(fan_count):
                suffix = "" if fan_count == 1 else f" #{fan_index + 1}"
                if self.nvml.fan_rpm(handle, fan_index) is not None:
                    fans.append(
                        NvidiaFanSensor(
                            id=f"nvidia:{device.key}:fan{fan_index}",
                            name=f"{name} fan{suffix}",
                            device=device,
                            nvml=self.nvml,
                            handle=handle,
                            fan_index=fan_index,
                        )
                    )
                pwms.append(
                    NvidiaPwmOutput(
                        id=f"nvidia:{device.key}:pwm{fan_index}",
                        name=f"{name} fan{suffix}",
                        device=device,
                        # Most NVIDIA cards refuse anything below roughly 30%
                        # and stop the fan themselves in idle.
                        can_stop=False,
                        nvml=self.nvml,
                        handle=handle,
                        fan_index=fan_index,
                    )
                )

        self._outputs = [p for p in pwms if isinstance(p, NvidiaPwmOutput)]
        return temps, fans, pwms

    def close(self) -> None:
        for output in self._outputs:
            try:
                output.release()
            except OSError:
                log.exception("failed to release %s", output.id)
        self.nvml.shutdown()

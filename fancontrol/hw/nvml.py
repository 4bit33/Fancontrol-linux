"""A small ctypes binding for the handful of NVML calls we need.

Using ctypes directly keeps the daemon free of any Python package dependency
for NVIDIA support: ``libnvidia-ml.so.1`` ships with the driver itself, so if
the driver is installed the binding works, and if it is not the backend simply
reports no devices.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import POINTER, byref, c_char, c_uint, c_void_p

log = logging.getLogger(__name__)

NVML_SUCCESS = 0
NVML_ERROR_UNINITIALIZED = 1
NVML_ERROR_INVALID_ARGUMENT = 2
NVML_ERROR_NOT_SUPPORTED = 3
NVML_ERROR_NO_PERMISSION = 4
NVML_ERROR_NOT_FOUND = 6

NVML_TEMPERATURE_GPU = 0

_ERROR_NAMES = {
    NVML_ERROR_UNINITIALIZED: "NVML was not initialised",
    NVML_ERROR_INVALID_ARGUMENT: "invalid argument",
    NVML_ERROR_NOT_SUPPORTED: "not supported by this GPU or driver",
    NVML_ERROR_NO_PERMISSION: "no permission (run as root)",
    NVML_ERROR_NOT_FOUND: "not found",
}

_LIB_NAMES = ("libnvidia-ml.so.1", "libnvidia-ml.so")


class NvmlError(Exception):
    def __init__(self, code: int, function: str) -> None:
        self.code = code
        self.function = function
        detail = _ERROR_NAMES.get(code, f"error {code}")
        super().__init__(f"{function} failed: {detail}")


class _FanSpeedInfo(ctypes.Structure):
    """``nvmlFanSpeedInfo_t`` from newer drivers (RPM readback)."""

    _fields_ = [("version", c_uint), ("fan", c_uint), ("speed", c_uint)]


class Nvml:
    """Thin wrapper around the NVML shared library."""

    def __init__(self) -> None:
        self._lib: ctypes.CDLL | None = None
        self._initialised = False

    @property
    def available(self) -> bool:
        return self._initialised

    def init(self) -> bool:
        """Load and initialise NVML. Returns False when it is not present."""

        if self._initialised:
            return True
        for name in _LIB_NAMES:
            try:
                self._lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if self._lib is None:
            log.info("libnvidia-ml not found; NVIDIA support disabled")
            return False
        try:
            self._check(self._lib.nvmlInit_v2(), "nvmlInit_v2")
        except (NvmlError, AttributeError) as exc:
            log.warning("NVML initialisation failed: %s", exc)
            self._lib = None
            return False
        self._initialised = True
        return True

    def shutdown(self) -> None:
        if self._lib is not None and self._initialised:
            try:
                self._lib.nvmlShutdown()
            except OSError:
                pass
        self._initialised = False

    def _check(self, code: int, function: str) -> None:
        if code != NVML_SUCCESS:
            raise NvmlError(code, function)

    def _fn(self, name: str):
        if self._lib is None:
            raise NvmlError(NVML_ERROR_UNINITIALIZED, name)
        try:
            return getattr(self._lib, name)
        except AttributeError as exc:
            raise NvmlError(NVML_ERROR_NOT_SUPPORTED, name) from exc

    def device_count(self) -> int:
        count = c_uint()
        self._check(self._fn("nvmlDeviceGetCount_v2")(byref(count)), "nvmlDeviceGetCount_v2")
        return int(count.value)

    def device_handle(self, index: int) -> c_void_p:
        handle = c_void_p()
        self._check(
            self._fn("nvmlDeviceGetHandleByIndex_v2")(c_uint(index), byref(handle)),
            "nvmlDeviceGetHandleByIndex_v2",
        )
        return handle

    def _string(self, function: str, handle: c_void_p, size: int = 96) -> str:
        buf = ctypes.create_string_buffer(size)
        self._check(self._fn(function)(handle, buf, c_uint(size)), function)
        return buf.value.decode("utf-8", "replace")

    def device_name(self, handle: c_void_p) -> str:
        return self._string("nvmlDeviceGetName", handle)

    def device_uuid(self, handle: c_void_p) -> str:
        return self._string("nvmlDeviceGetUUID", handle)

    def temperature(self, handle: c_void_p) -> float:
        value = c_uint()
        self._check(
            self._fn("nvmlDeviceGetTemperature")(
                handle, c_uint(NVML_TEMPERATURE_GPU), byref(value)
            ),
            "nvmlDeviceGetTemperature",
        )
        return float(value.value)

    def num_fans(self, handle: c_void_p) -> int:
        value = c_uint()
        self._check(self._fn("nvmlDeviceGetNumFans")(handle, byref(value)), "nvmlDeviceGetNumFans")
        return int(value.value)

    def fan_percent(self, handle: c_void_p, fan: int) -> float:
        value = c_uint()
        self._check(
            self._fn("nvmlDeviceGetFanSpeed_v2")(handle, c_uint(fan), byref(value)),
            "nvmlDeviceGetFanSpeed_v2",
        )
        return float(value.value)

    def fan_rpm(self, handle: c_void_p, fan: int) -> float | None:
        """RPM readback. Only newer drivers implement it."""

        info = _FanSpeedInfo()
        info.version = ctypes.sizeof(_FanSpeedInfo) | (1 << 24)
        info.fan = c_uint(fan).value
        try:
            self._check(
                self._fn("nvmlDeviceGetFanSpeedRPM")(handle, byref(info)),
                "nvmlDeviceGetFanSpeedRPM",
            )
        except NvmlError:
            return None
        return float(info.speed)

    def set_fan_percent(self, handle: c_void_p, fan: int, percent: int) -> None:
        self._check(
            self._fn("nvmlDeviceSetFanSpeed_v2")(handle, c_uint(fan), c_uint(percent)),
            "nvmlDeviceSetFanSpeed_v2",
        )

    def set_fan_default(self, handle: c_void_p, fan: int) -> None:
        self._check(
            self._fn("nvmlDeviceSetDefaultFanSpeed_v2")(handle, c_uint(fan)),
            "nvmlDeviceSetDefaultFanSpeed_v2",
        )

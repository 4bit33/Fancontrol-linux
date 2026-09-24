# Changelog

## Unreleased

### Window

* Redesigned after FanControl: one scrolling page of cards. Every curve is a
  card with a small graph, the temperature it reads and the speed it outputs,
  and the fans it drives; sensors are tiles coloured by temperature.
* The window is translated: English, and Ukrainian on a Ukrainian desktop,
  including Qt's own buttons and dialogs. `--lang` or `FANCONTROL_LANG`
  choose explicitly.

## 1.0.0 — 2026-09-23

First release.

### Control

* Seven curve types, as in FanControl for Windows: graph, flat, linear,
  target, trigger, mix and sync.
* Hysteresis and response time separately for rising and falling
  temperatures.
* Per fan: minimum, maximum, offset, stop point, spin-up kick, and limits on
  how fast the speed may rise and fall.
* The firmware runs a fan while the temperature is below a threshold. For an
  NVIDIA card that is its own curve, including stopping the fans at idle.
* Calibration finds where a fan stops and starts, waiting for the speed to
  settle at every step.

### Safety

* An unreadable sensor sends the fan to a failsafe speed (100% by default); a
  critical temperature sends every fan to 100%.
* Stopping the daemon hands control back to the firmware; if the daemon is
  killed, the next start does it instead.
* A fan standing still while being driven is flagged as stalled.

### Hardware

* Any chip exposed through `hwmon`: Nuvoton, ITE, `amdgpu`, `k10temp`,
  `coretemp`, NVMe.
* NVIDIA graphics cards through NVML, with no X server and no Coolbits — works
  on Wayland.

### Importing from Windows

* Reads FanControl's `userConfig.json` (tested with version 270) and matches
  LibreHardwareMonitor and NvAPI identifiers to the hardware Linux sees.
  Ambiguous cases are left to the user rather than guessed.

### Programs

* `fancontrold` — the systemd service; `fancontrol-gui` — the Qt6 window;
  `fanctl` — the terminal; `fancontrol-sim` — a hardware simulator for trying
  it out without risk.
* Diagnostics: `fanctl doctor`, `tools/pwm-check.sh`,
  `tools/identify-fans.sh`, `tools/nvidia-diagnose.sh`.

### Installing

* `install.sh` for Fedora, Debian, Ubuntu, Arch and openSUSE; the program
  lives in its own environment and leaves the system Python alone.

### What has been tested

Live: Gigabyte B760 Gaming X AX (ITE IT8689E), Intel, NVIDIA RTX 3070,
Fedora 44 with KDE Plasma. Automatically: 171 tests on Ubuntu and Fedora, and
installing the program in containers of five distributions. Reports from
other hardware are very welcome.

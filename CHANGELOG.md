# Changelog

## Unreleased (1.2.0)

### Window

* Every fan card has a switch for who drives the fan: **Firmware**, **My
  curve**, or **Both** — the firmware while it is cool and your curve from a
  temperature you set on the card. In "Both" the card says who has the fan
  right now and when that changes. This replaces the enable checkbox and the
  "firmware runs it below" field that was tucked away in the fan's settings;
  configurations are unchanged.

## 1.1.1 — 2026-09-24

### Installing

* A Fedora package, built in COPR for Fedora 43, 44, 45 and rawhide:
  `sudo dnf copr enable 4bit33/fancontrol-linux`, then
  `sudo dnf install fancontrol-linux`. Updates arrive with `dnf upgrade` and
  restart the daemon. `fancontrol-linux-nvidia` is pulled in automatically
  where the NVIDIA driver is installed.
* The update notice gives the `dnf` command when the program came from the
  package, and `install.sh` refuses to write over the package.

## 1.1.0 — 2026-09-24

### Window

* Redesigned after FanControl: one scrolling page of cards. Every curve is a
  card with a small graph, the temperature it reads and the speed it outputs,
  and the fans it drives; sensors are tiles coloured by temperature.
* The window is translated: English, and Ukrainian on a Ukrainian desktop,
  including Qt's own buttons and dialogs. The language can be chosen in
  Settings, per user, or with `--lang` or `FANCONTROL_LANG`.
* Any card — fan, curve or sensor — can be hidden from its right-click menu,
  and brought back with "Show hidden" in the section header. Hiding changes
  only the window, never what the fans do.
* Cards size themselves to their text in the style and language in use, and
  the window opens to fit the screen; nothing is cut off.

### Updates

* The window says when a newer release is out, with the command to update
  ready to copy. It checks GitHub once a day and installs nothing itself; the
  check can be switched off in Settings.

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

# fancontrol-linux

**English** · [Українська](README.uk.md)

Fan control for Linux in the spirit of
[FanControl](https://github.com/Rem0o/FanControl.Releases) for Windows, without
.NET: a Python daemon that drives `hwmon`, and a Qt6 window that looks at home
in KDE Plasma.

The headline feature: **it reads your `userConfig.json` from FanControl on
Windows** and works out which Linux hardware each fan and sensor corresponds
to.

![Main window](docs/screenshots/main-window.png)

---

## What it does

* **Curves** — all seven kinds FanControl has:
  * `Graph` — points you place with the mouse
  * `Flat` — one fixed speed
  * `Linear` — a ramp between two temperatures
  * `Target` — holds a temperature rather than mapping it to a speed
  * `Trigger` — two speeds with a gap between the thresholds, so the fan does not hunt
  * `Mix` — combines other curves (max / min / average / sum / subtract)
  * `Sync` — follows another fan, with an offset or multiplier
* **Smoothing** — hysteresis and response time **separately for rising and
  falling** temperatures: react to a spike at once, slow down gently. Plus
  "ignore hysteresis past the ends of the curve".
* **Per-fan settings** — minimum, maximum, offset, stop point, spin-up kick,
  and limits on how fast the speed may rise and fall.
* **Firmware while cool** — below a temperature you choose, a fan is handed
  back to the firmware. For a graphics card that means its own curve,
  including stopping the fans at idle; from the threshold up, this program
  takes over.
* **Calibration** — steps each fan down and up, waits for the speed to settle
  at every step, finds where it stops and where it starts, and suggests a
  minimum, start and stop point. Runs in the background.
* **Safety**:
  * a sensor a curve needs goes missing → the fan goes to a failsafe speed
    (100% by default) rather than staying where it was;
  * above a critical temperature every managed fan goes to 100%;
  * stopping the daemon puts every `pwm*_enable` back as it was, i.e. hands
    control back to the motherboard firmware;
  * if the daemon is killed rather than stopped, the next start hands the fans
    back anyway, because it records what it holds under `/run`;
  * a fan reading 0 rpm while being driven is flagged as stalled.
* **Hardware** — motherboard chips through `hwmon` (Nuvoton, ITE and others),
  `amdgpu`, `k10temp`/`coretemp`, NVMe, and NVIDIA graphics cards through NVML.

---

## What has been tested, and what has not

Plainly, so you know what to expect.

**Tested live** — on one machine:

| | |
|---|---|
| Motherboard | Gigabyte B760 Gaming X AX DDR4, ITE IT8689E, out-of-tree `it87` driver |
| CPU | Intel (`coretemp`) |
| Graphics | NVIDIA GeForce RTX 3070, driver 610 |
| System | Fedora 44, KDE Plasma (Wayland), SELinux enforcing |

Everything works there: four motherboard fans, both graphics card fans,
calibration, importing a real Windows configuration, and a full install with
the service.

**Tested automatically, without real hardware:**

* 171 tests on every push, on Ubuntu and Fedora — curves, the importer, the
  control loop against simulated hardware, D-Bus, the daemon's lifecycle, the
  window;
* installing the program (without the service) in clean Fedora 44,
  Ubuntu 24.04, Debian trixie, Arch and openSUSE Tumbleweed containers.

**Not yet tested live:** Nuvoton chips (`nct6775`), AMD CPUs and graphics
cards, other NVIDIA cards, a full install with the service on anything other
than Fedora. The code for them is the same and should work — but "should" is
not "does".

**Not supported:** USB-connected AIO pumps and fan hubs (Corsair, NZXT and
similar — `liquidctl`), distributions without systemd.

### Different hardware? Please tell us

The most useful thing you can do for the project is to run it on your machine
and open an [issue](../../issues/new?template=hardware-report.md) — even if
everything works:

```bash
fanctl doctor
sudo ./tools/pwm-check.sh
```

The issue template asks for the rest.

---

## Installing

```bash
git clone https://github.com/4bit33/Fancontrol-linux.git
cd Fancontrol-linux
sudo ./install.sh
```

The script knows `dnf` (Fedora), `apt` (Debian, Ubuntu), `pacman` (Arch) and
`zypper` (openSUSE). It installs what the distribution packages, and fetches
PySide6 and dasbus from PyPI into the program's own environment where the
distribution has none — the system Python is left alone. Then the systemd
unit, the D-Bus policy and the menu entry, and the daemon is started.

systemd is required. Fan settings can be changed by the administrators'
group — `wheel` (Fedora, Arch, openSUSE) or `sudo` (Debian, Ubuntu); the
installer uses whichever the machine has. Anyone can read the status.

```bash
sudo ./install.sh --uninstall        # remove it (the configuration stays in /etc)
```

---

## First steps

1. **Check that everything is in place:**

   ```bash
   fanctl doctor
   ```

   Shows the chips found, whether they have PWM outputs, which modules are
   loaded, and the state of NVIDIA and the daemon. If anything is wrong, see
   [When it does not work](#when-it-does-not-work).

2. **Which fan is on which channel.** The numbers `pwm1..pwmN` say nothing
   about where a fan physically is. This raises each channel in turn and
   watches which tachometer reacts — empty headers show up too, and
   everything is put back as it was at the end:

   ```bash
   sudo ./tools/identify-fans.sh
   ```

3. **Configure** — import from Windows (below), or start from scratch in
   `fancontrol-gui`.

4. **Calibrate** each fan — a few minutes each:

   ```bash
   fanctl calibrate "Fan name"
   ```

---

## Using it

### The window

```bash
fancontrol-gui
```

One page of cards, laid out like FanControl: **Controls** (a card per fan),
**Curves** (a card per curve with a small graph and what it outputs right now;
click one to edit it, or the dashed card to add one), then **Temperatures**
and **Fan speeds**. The **Fan control on** switch in the toolbar hands every
fan back to the firmware — handy for checking whether a problem is in your
curve.

On the graph: drag a point to move it, double-click to add one, right-click to
remove one. ⚙ on a fan's card opens its minimum, start, rate limits and the
temperature below which the firmware runs it. Double-click a sensor to rename
it.

Right-click any card to hide it — an unused curve, a sensor you never look
at, an empty fan header. Hiding only tidies the window: a hidden curve still
drives its fans. **Show hidden** in a section's header brings them back.

The window speaks English, or Ukrainian on a Ukrainian desktop. To choose
yourself: `fancontrol-gui --lang uk` or `FANCONTROL_LANG=en`. Adding a
language is one file in `fancontrol/gui/locales/`; `tests/test_i18n.py`
tells you which strings are still missing.

### The terminal

```bash
fanctl doctor                    # can this machine work at all
fanctl status                    # what every fan is doing now
fanctl list                      # all the hardware found
fanctl set "CPU cooler" 60       # drive a fan by hand
fanctl auto "CPU cooler"         # hand it back to its curve
fanctl calibrate "CPU cooler"    # measure where it starts and stops
fanctl disable                   # hand every fan back to the firmware
fanctl config -o backup.json     # save the configuration
```

---

## Importing from FanControl on Windows

```bash
fanctl import ~/userConfig.json          # just show what it would do
fanctl import ~/userConfig.json --apply  # apply it
```

Or in the window: **Import from FanControl…** — which also lets you choose for
the sensors the program would not match by itself.

### What carries over

FanControl's file format has changed between releases, so the importer does
not rely on one schema; it finds objects **by what they contain**. It is
tested against a real version 270 `userConfig.json`, kept in `tests/data/` as
a regression test:

* curve points in all three encodings — `"20.4,21.0"` strings (v270), a
  `{"30": 20}` dictionary, and `[{"X": 30, "Y": 20}]` objects;
* curves referenced by GUID, by name, and as `{"Name": "…"}`;
* hysteresis and response time **separately for rising and falling**
  (`HysteresisConfig`), with "ignore at the ends of the curve";
* the graph's axis range — a graphics card curve up to 120 °C stays that way;
* which tachometer belongs to which header (`PairedFanSensor`);
* `SelectedStart` / `SelectedStop` — a fan's start and stop points;
* the calibration table (`Calibration`).

If you have moved fans around since, the calibration from Windows describes
the old wiring — calibrate again.

### How the hardware is matched

FanControl stores LibreHardwareMonitor identifiers (`/lpc/it8689e/control/0`),
and its own scheme for NVIDIA (`NVApiWrapper/0-GA104-A/control/0`). They mean
nothing to Linux:

| From Windows | Becomes on Linux | Why |
|---|---|---|
| `/lpc/it8689e/control/0` | `hwmon:it8689-…:pwm1` | same chip; LHM counts from zero, hwmon from one |
| `/lpc/it8689e/fan/0` | `hwmon:it8689-…:fan1` | the same for tachometers |
| `/amdcpu/0/temperature/2` | `hwmon:k10temp-…:temp1` | `amdcpu` → `k10temp`, the channel labelled `Tctl` |
| `NVApiWrapper/0-GA104-A/control/1` | `nvidia:GPU-…:pwm1` | NVIDIA card through NVML, fan number matches |

Only a match that is both confident and clearly ahead of the runner-up is
applied automatically. **It deliberately does not guess:**

* two equally good candidates → the choice is left to you;
* two different Windows sensors that both want **one** Linux sensor → both go
  to you. This happens with Intel: `/intelcpu/0/temperature/0` and `/1` both
  match `Package id 0` best, but they were different sensors;
* anything that cannot be matched is imported switched off, and you are told
  **why**.

The one thing a file cannot confirm is how FanControl numbers the mix
functions, so importing a mix curve always leaves a warning.

---

## When it does not work

### No fans are found

The usual reason is that the kernel does not know about the motherboard's
super-I/O chip yet:

```bash
sudo sensors-detect        # accept the safe defaults
sudo modprobe nct6775      # or whichever module sensors-detect names
sudo fanctl rescan
echo nct6775 | sudo tee /etc/modules-load.d/fancontrol.conf   # load it at boot
```

On many boards (Gigabyte with ITE chips especially) ACPI claims these ports
and `it87` refuses to load with `Device or resource busy`. The out-of-tree
driver ([frankcrawford/it87](https://github.com/frankcrawford/it87)) knows more
boards and has its own parameter for this, which affects only itself:

```bash
echo "options it87 ignore_resource_conflict=1" | sudo tee /etc/modprobe.d/it87.conf
```

For the in-kernel driver, it is the kernel parameter
`acpi_enforce_resources=lax`:

```bash
sudo grubby --update-kernel=ALL --args="acpi_enforce_resources=lax"   # Fedora, RHEL
# Debian, Ubuntu, Arch: add it to GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub,
# then  sudo update-grub  (Debian, Ubuntu)
# or    sudo grub-mkconfig -o /boot/grub/grub.cfg  (Arch)
```

Both lift the kernel's protection against ACPI and the driver using the same
ports at once. Usually harmless, but not risk-free — your call.

### Fans are found but do not respond

```bash
sudo ./tools/pwm-check.sh
```

Sweeps every channel from 0 to 255 and tells apart causes that look the same
from outside:

* **the value is not held** — the chip or driver rejects the write;
* **manual mode is refused** — the chip will not hand the channel over;
* **held, but the speed does not change** — the fan ignores PWM (typical of a
  3-pin fan on a header in PWM mode), or something else drives it;
* **speed rises with the value** — control works.

It also shows the driver's options from `/etc/modprobe.d` and what it said
when it loaded.

#### The driver is told it has a different chip

`it87` has a `force_id` option that makes it treat the chip as another model —
often recommended in guides for particular boards. With the wrong ID it uses
the wrong register map: some channels work, the rest do nothing.

That is exactly what happened on the Gigabyte B760 Gaming X AX:
`/etc/modprobe.d/it87.conf` had `force_id=0x8628`, but the chip is an IT8689E.
With `0x8689` every header worked.

```bash
grep -r it87 /etc/modprobe.d/               # what the driver is being told
journalctl -k -b | grep -i "Found.*chip"    # what it ended up detecting
```

### NVIDIA graphics cards

To check whether the driver allows fan control and whether the service is in
the way:

```bash
sudo ./tools/nvidia-diagnose.sh
```

Two things the installer already handles, but which are worth knowing:

* **Capabilities.** The unit deliberately gives the daemon no capabilities —
  writing to PWM needs none. The NVIDIA driver does: without them
  `nvmlDeviceSetFanSpeed_v2` answers "no permission". On machines with an
  NVIDIA card the installer adds a one-line drop-in that restores them; the
  rest of the sandbox stays.
* **SELinux.** systemd picks a service's domain from the label of the file it
  runs. The venv's script is `lib_t`, which would leave the daemon in
  `init_t`, from where the policy keeps it away from `/dev/nvidia*`. So the
  unit runs the interpreter (`bin_t`) and the daemon lands in the ordinary
  `unconfined_service_t`. `fanctl doctor` shows the domain.

**Coolbits is not needed.** The advice to set `Option "Coolbits" "4"` in
`xorg.conf` is about `nvidia-settings` and the X server's NV-CONTROL
extension. This program uses NVML, which never asks the X server — and on
Wayland nothing reads `xorg.conf` anyway.

---

## Try it without risk

There is a hardware simulator: it builds a tree that looks like
`/sys/class/hwmon` and answers PWM writes the way a real machine would — the
temperature falls as the fans speed up.

```bash
fancontrol-sim --root /tmp/fake-hwmon &
FANCONTROL_HWMON_ROOT=/tmp/fake-hwmon \
FANCONTROL_DISABLE=nvidia \
FANCONTROL_CONFIG=/tmp/fake-config.json \
  fancontrol-gui --local
```

No real fan is touched.

---

## How it fits together

```
fancontrol-gui ──D-Bus──► fancontrold ──► /sys/class/hwmon/*/pwm*
   (you)           │        (root)     └─► NVML (libnvidia-ml.so)
                   │
                fanctl
```

The daemon runs as root, because writing to PWM requires it. The window runs
as you and talks to it over the system D-Bus (`org.fancontrol.Daemon`).
Anyone may read the status; changing settings takes the administrators'
group.

More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| What | Where |
|---|---|
| Configuration | `/etc/fancontrol-linux/config.json` (+ `.bak` of the previous one) |
| Program | `/usr/lib/fancontrol-linux/` |
| systemd unit | `/usr/lib/systemd/system/fancontrold.service` |
| D-Bus policy | `/usr/share/dbus-1/system.d/org.fancontrol.Daemon.conf` |

The configuration is plain JSON and can be edited by hand;
`sudo systemctl reload fancontrold` re-reads it.

---

## Development

```bash
python3 -m venv --system-site-packages .venv     # PyGObject from the distribution
.venv/bin/pip install -e '.[dev,gui,daemon]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest   # no real hardware needed
```

The tests cover the curves, the importer against a **real** version 270
`userConfig.json`, the control loop against simulated hardware (including an
end-to-end "the machine actually cools down"), the D-Bus interface's
signatures, the daemon's whole lifecycle on a real bus (start, SIGTERM,
SIGKILL, handing control back to the firmware), and the window under Qt's
offscreen platform. GitHub Actions runs them on Ubuntu and Fedora for every
push.

The installer is checked on different distributions in clean containers
(podman, or `ENGINE=docker`):

```bash
./tools/test-install-in-containers.sh           # Fedora, Ubuntu, Debian, Arch, openSUSE
./tools/test-install-in-containers.sh ubuntu    # just one
```

Containers have no systemd, so this covers the half of the installer that
differs between distributions: dependencies and the program itself
(`install.sh --program-only`).

---

## Licence

[GPL-3.0-or-later](LICENSE).

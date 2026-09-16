# Architecture

## The shape of it

```
fancontrol-gui  ──D-Bus──►  fancontrold  ──►  /sys/class/hwmon/*/pwm*
   (your user)      │         (root)      └►  NVML (libnvidia-ml.so.1)
                    │
   fanctl ──────────┘
```

Writing a PWM value needs root, and fan control has to keep working when no
one is logged in, so the loop lives in a daemon and the window is a client.
That is also how FanControl on Windows is put together: a service plus a
window.

## Modules

| Module | What it owns |
|---|---|
| `core/models.py` | The configuration: curves, controls, settings, and validation |
| `core/curves.py` | Turning sensor readings into a percentage, with per-curve state |
| `core/engine.py` | One tick of the control loop, and everything that makes it safe |
| `core/config.py` | Reading and writing the configuration file, and bootstrapping one |
| `hw/base.py` | The `TempSensor` / `FanSensor` / `PwmOutput` interfaces |
| `hw/hwmon.py` | The sysfs backend |
| `hw/nvidia.py`, `hw/nvml.py` | NVIDIA, through a ctypes binding to NVML |
| `hw/registry.py` | Aggregates the backends into one lookup table |
| `hw/simulator.py` | A fake hwmon tree that responds to PWM writes |
| `importer/fancontrol_json.py` | Reading FanControl's `userConfig.json`, whichever of its schemas it uses |
| `importer/mapping.py` | Pointing Windows identifiers at Linux hardware |
| `daemon/service.py` | The daemon's logic, with no transport attached |
| `daemon/dbus_service.py` | The same methods, published on D-Bus |
| `gui/` | The window |

`daemon/service.py` deliberately knows nothing about D-Bus. That is what lets
the test-suite drive it directly, and what lets `fanctl --local` and
`fancontrol-gui --local` run the whole stack in one process.

## Identifiers

A saved configuration refers to hardware by string id, so those strings have to
survive a reboot.

```
hwmon:<device-key>:<channel>     hwmon:nct6798-platform-nct6775.656:temp2
nvidia:<uuid>:<channel>          nvidia:GPU-1a2b3c4d-...:pwm0
```

The `hwmonN` number is **not** part of the id: the kernel hands those out in
probe order and they move between boots. The device key is built from the chip
name plus the directory that names the underlying device (a platform device
such as `nct6775.656`, or a PCI address). NVIDIA GPUs use the UUID NVML
reports, so a card keeps its id even if the PCI enumeration order changes.

Channels keep the kernel's own names — `temp2`, `fan1`, `pwm3` — because that
is what a user comparing against `sensors` output will see.

## The control loop

One `ControlEngine.tick()`:

1. Read every temperature and every tachometer.
2. Evaluate the curves in dependency order, so a mix curve always sees its
   inputs. Sync curves read the *previous* tick's applied values, which is what
   breaks the otherwise circular dependency between a control and a curve that
   follows it.
3. For each enabled control, shape the curve's number into the value actually
   written:
   * add the offset, apply the floor (stop the fan only if that was asked for),
     clamp to the maximum;
   * if the fan was stopped, hold `start_percent` briefly so it actually starts
     turning;
   * limit how fast the value may change, unless we are mid spin-up.
4. Write it, then check whether the tachometer agrees that the fan is moving.

### What happens when things go wrong

| Situation | What the daemon does |
|---|---|
| A curve's sensor cannot be read | That control goes to `failsafe_percent` (100% by default) and the reason is reported |
| A sensor reads something implausible (below −50 °C or above 200 °C) | Treated as unreadable, as above |
| Any sensor a curve uses is above `critical_temperature` | Every managed fan goes to 100% |
| A control's output has disappeared | Reported; nothing is written |
| A fan reads 0 rpm while being driven | Flagged as stalled after five ticks |
| The daemon is stopped | Every `pwm*_enable` is put back to the value the firmware had |
| The daemon is killed with SIGKILL | The fans stay where they were — the kernel cannot undo that. This is why `TimeoutStopSec` in the unit is generous |

The failsafe is deliberately *high*. A fan running faster than it needs to is
noisy; a fan that stopped because a sensor went away is a dead chip.

## D-Bus interface

Bus name `org.fancontrol.Daemon`, path `/org/fancontrol/Daemon`, interface
`org.fancontrol.Daemon1`.

| Member | Signature | What it does |
|---|---|---|
| `GetVersion` | `() → s` | |
| `GetInventory` | `() → s` | Every sensor and control found, as JSON |
| `GetStatus` | `() → s` | The most recent tick, as JSON |
| `GetConfig` | `() → s` | The active configuration, as JSON |
| `SetConfig` | `(s) → s` | Validate, apply and save |
| `SaveConfig` | `() → s` | |
| `ReloadConfig` | `() → s` | Re-read the file from disk |
| `Rescan` | `() → s` | Enumerate the hardware again |
| `SetOverride` | `(sd) → s` | Drive a control by hand; a negative percentage clears it |
| `SetControlEnabled` | `(b) → s` | The master switch |
| `Calibrate` | `(s) → s` | Measure a fan's start and stop points |
| `ImportFanControl` | `(ss) → s` | Convert a Windows config and propose a mapping |
| `ApplyImport` | `(ssb) → s` | Apply it once the mapping is confirmed |
| `StatusChanged` | signal `(s)` | Emitted once per tick |

Anything nested crosses the bus as a JSON string. Fan configurations are deeply
nested and gain fields as curve types grow; a JSON payload keeps the signature
stable instead of forcing a new D-Bus type every time.

Access is decided by the bus policy in
`data/dbus/org.fancontrol.Daemon.conf`: the four `Get*` methods are open to
every local user, and everything else is restricted to the `wheel` group.

## Importing a Windows configuration

FanControl's file format has changed across releases, so the importer does not
parse against a schema. It walks the whole document and recognises objects by
what they carry: something with `Points` and a temperature source is a graph
curve wherever it sits.

Telling a control from a sensor reference is the part that needs care. A
`PairedFanSensor` reference, an entry in `FanSensors` and a real control all
carry an identifier, and treating every identifier as a fan produced three
phantom controls per real one. A control is therefore recognised by `/control/`
in its identifier, or by a field only a control has (`SelectedStart`,
`ManualControl`, `Calibration`, and the rest of `CONTROL_MARKERS`).

Mapping the identifiers onto Linux hardware is scored rather than decided.
Something is applied automatically only when it is both confident and clearly
ahead of the runner-up, and two identifiers are never pointed at the same Linux
sensor: they were separate things on Windows, so at most one of them can be
right. Everything else goes to the user with a ranked list.

## Testing

```bash
python3 -m pytest
```

No real hardware is involved:

* `hw/simulator.py` writes a directory that looks exactly like
  `/sys/class/hwmon` and then models a plausible thermal system — each PWM
  drives a fan, each fan cools a zone, each zone heats up under load. The hwmon
  backend is pointed at it with `FANCONTROL_HWMON_ROOT`.
* The engine tests include an end-to-end one that starts the simulated machine
  at 85 °C, runs 200 ticks of the real control loop, and asserts the
  temperature came down.
* The GUI tests run under Qt's offscreen platform.
* `test_dbus_interface.py` asserts the introspection XML dasbus generates,
  which catches signature mistakes that would otherwise only surface when the
  daemon tries to register on the bus.

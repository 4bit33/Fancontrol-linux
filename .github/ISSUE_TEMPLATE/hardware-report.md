---
name: Hardware report
about: Tell us how fancontrol-linux does on your machine — working or not
title: "Hardware: <board> / <distribution>"
labels: hardware
---

Reports from machines that **work** are just as useful as ones that do not:
they are how we learn which boards and drivers are actually fine.

## Machine

- Motherboard:
- Super-I/O chip (from `sudo sensors-detect`, the manual, or HWiNFO on Windows):
- CPU:
- Graphics card and driver version:
- Distribution and version:
- Desktop (KDE, GNOME, …) and X11 or Wayland:

## Does it work?

- [ ] Board fans are found
- [ ] Board fans respond to the curves
- [ ] Calibration gives sensible numbers
- [ ] Graphics card fans respond (if you have an NVIDIA card)
- [ ] Imported a FanControl `userConfig.json` from Windows

What went wrong, if anything:

## Output

<details><summary><code>fanctl doctor</code></summary>

```
paste here
```

</details>

<details><summary><code>sudo ./tools/pwm-check.sh</code></summary>

```
paste here
```

</details>

<details><summary>Any <code>it87</code> / <code>nct6775</code> options: <code>grep -r . /etc/modprobe.d/ | grep -E 'it87|nct67'</code></summary>

```
paste here
```

</details>

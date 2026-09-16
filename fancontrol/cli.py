"""``fanctl`` — the terminal client.

Useful on a headless machine, and the quickest way to see what the daemon
thinks is going on when something is not behaving.

By default it talks to the running daemon over D-Bus. ``--local`` skips the
daemon and drives the hardware directly in this process, which needs root but
works before anything is installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .client import DaemonClient, DaemonError


# ----------------------------------------------------------------------
# output helpers


def _colour(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return _colour(text, "1")


def dim(text: str) -> str:
    return _colour(text, "2")


def red(text: str) -> str:
    return _colour(text, "31")


def green(text: str) -> str:
    return _colour(text, "32")


def yellow(text: str) -> str:
    return _colour(text, "33")


def bar(percent: float, width: int = 20) -> str:
    filled = int(round(percent / 100 * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def table(rows: list[list[str]], headers: list[str]) -> str:
    if not rows:
        return dim("  (nothing)")
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = ["  " + "  ".join(bold(h.ljust(widths[i])) for i, h in enumerate(headers))]
    for row in rows:
        lines.append("  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# backend selection


class LocalBackend:
    """Runs the service in this process instead of talking to the daemon."""

    def __init__(self, config_path: Path | None) -> None:
        from .daemon.service import FanControlService

        self._service = FanControlService(config_path=config_path)
        self._service.start()
        self._service.tick()

    def __getattr__(self, name: str):
        mapping = {
            "inventory": "get_inventory",
            "status": "get_status",
            "config": "get_config",
            "reload": "reload_config",
        }
        return getattr(self._service, mapping.get(name, name))

    def version(self) -> str:
        return __version__


def connect(args: argparse.Namespace):
    if args.local:
        return LocalBackend(args.config)
    return DaemonClient(session=args.session)


# ----------------------------------------------------------------------
# commands


def cmd_status(client, args) -> int:
    status = client.status()
    if not status:
        print(dim("the daemon has not completed a tick yet"))
        return 0

    config = client.config()
    names = {c["id"]: c["name"] for c in config["controls"]}
    sensor_names = config.get("sensor_names", {})
    inventory = client.inventory()
    labels = {entry["id"]: entry["name"] for entry in inventory["temperatures"]}
    labels.update({k: v for k, v in sensor_names.items() if v})

    if not status.get("control_enabled", True):
        print(yellow("  fan control is switched off; the firmware is driving the fans"))
    if status.get("critical"):
        for message in status.get("messages", []):
            print(red("  " + message))

    print(bold("\nControls"))
    rows = []
    for control_id, entry in status.get("controls", {}).items():
        percent = entry.get("applied_percent") or 0.0
        rpm = entry.get("rpm")
        note = ""
        if entry.get("error"):
            note = red(entry["error"])
        elif entry.get("stalled"):
            note = red("stalled: reads 0 RPM while being driven")
        elif entry.get("kicking"):
            note = yellow("spinning up")
        elif not entry.get("managed"):
            note = dim("not managed")
        if not entry.get("available", True) and not entry.get("enabled"):
            note = dim("no matching hardware on this machine")
        rows.append([
            names.get(control_id, control_id),
            f"{percent:5.1f}%",
            bar(percent),
            # A fan with no tachometer reads None; one that is standing still
            # reads zero, and the two mean very different things.
            "-" if rpm is None else f"{int(rpm)} rpm",
            note,
        ])
    print(table(rows, ["control", "speed", "", "fan", "note"]))

    print(bold("\nTemperatures"))
    rows = []
    for sensor_id, value in sorted(status.get("temperatures", {}).items()):
        if value is None:
            rows.append([labels.get(sensor_id, sensor_id), red("unavailable")])
        else:
            rows.append([labels.get(sensor_id, sensor_id), f"{value:5.1f} °C"])
    print(table(rows, ["sensor", "value"]))
    print()
    return 0


def cmd_list(client, args) -> int:
    inventory = client.inventory()
    for section, headers in (
        ("temperatures", ["id", "name", "chip"]),
        ("fans", ["id", "name", "chip"]),
        ("controls", ["id", "name", "chip"]),
    ):
        print(bold(f"\n{section.capitalize()}"))
        rows = [
            [entry["id"], entry["name"], entry["device"]["chip"]]
            for entry in inventory.get(section, [])
        ]
        print(table(rows, headers))
    print(dim(f"\n  config: {inventory.get('config_path', '?')}"))
    print()
    return 0


def cmd_set(client, args) -> int:
    control_id = _resolve_control(client, args.control)
    result = client.set_override(control_id, args.percent)
    return _report(result, f"{args.control} set to {args.percent:.0f}%")


def cmd_auto(client, args) -> int:
    control_id = _resolve_control(client, args.control)
    result = client.set_override(control_id, None)
    return _report(result, f"{args.control} handed back to its curve")


def cmd_enable(client, args) -> int:
    return _report(client.set_control_enabled(True), "fan control enabled")


def cmd_disable(client, args) -> int:
    return _report(
        client.set_control_enabled(False),
        "fan control disabled; the firmware is driving the fans again",
    )


def cmd_calibrate(client, args) -> int:
    control_id = _resolve_control(client, args.control)
    print(dim("stepping the fan up and down, this takes about a minute ..."))
    result = client.calibrate(control_id)
    if not result.get("ok"):
        print(red(result.get("error", "calibration failed")))
        return 1
    print(green(f"\n  {args.control}"))
    stop = result.get("stop_percent")
    start = result.get("start_percent")
    print(f"  stops turning below : {stop:.0f}%" if stop is not None else
          "  never stopped, even at 0%")
    print(f"  starts turning at   : {start:.0f}%" if start is not None else
          "  did not start from standstill")
    if result.get("suggested_min_percent") is not None:
        print(bold(f"\n  suggested minimum   : {result['suggested_min_percent']:.0f}%"))
    if result.get("suggested_start_percent") is not None:
        print(bold(f"  suggested start     : {result['suggested_start_percent']:.0f}%"))
    print(dim("\n  samples (percent -> rpm):"))
    for sample in result.get("samples", []):
        print(dim(f"    {sample['percent']:3.0f}% -> {sample['rpm']:.0f}"))
    return 0


def cmd_import(client, args) -> int:
    path = Path(args.file).expanduser()
    if not path.exists():
        print(red(f"{path} does not exist"))
        return 1

    # Hand the text over rather than the path: the daemon runs as root and may
    # not be able to read a file in the user's home directory.
    result = client.import_fancontrol(text=path.read_text(encoding="utf-8-sig", errors="replace"))
    if not result.get("ok"):
        print(red(result.get("error", "import failed")))
        return 1

    summary = result["summary"]
    print(bold("\nImported from FanControl"))
    print(f"  curves   : {summary['curves']}")
    print(f"  controls : {summary['controls']}")
    print(green(f"  mapped automatically : {summary['mapped']}"))
    if summary["needs_attention"]:
        print(yellow(f"  need your attention  : {summary['needs_attention']}"))

    for warning in result.get("warnings", []):
        print(yellow(f"  ! {warning}"))

    mapping = result["mapping"]
    inventory = client.inventory()
    labels = {}
    for section in ("temperatures", "fans", "controls"):
        labels.update({e["id"]: e["name"] for e in inventory.get(section, [])})

    if mapping["applied"]:
        print(bold("\nMapped"))
        rows = [
            [win.removeprefix("win:"), "->", f"{target}  ({labels.get(target, '?')})"]
            for win, target in sorted(mapping["applied"].items())
        ]
        print(table(rows, ["windows", "", "linux"]))

    chosen = dict(mapping["applied"])
    if mapping["pending"]:
        print(bold("\nAmbiguous"))
        for win, candidates in sorted(mapping["pending"].items()):
            print(f"  {win.removeprefix('win:')}")
            for candidate in candidates[:4]:
                target = candidate["target_id"]
                print(dim(f"      {candidate['score']:.2f}  {target}  "
                          f"({labels.get(target, '?')})  {candidate['reason']}"))
        if args.best_guess:
            for win, candidates in mapping["pending"].items():
                if candidates:
                    chosen[win] = candidates[0]["target_id"]
            print(yellow("\n  --best-guess: taking the highest scoring candidate for each"))

    if mapping["unmatched"]:
        print(bold("\nNo Linux counterpart"))
        for win in sorted(mapping["unmatched"]):
            print(red(f"  {win.removeprefix('win:')}"))
        print(dim("  controls using these are imported but left disabled"))

    if args.output:
        Path(args.output).write_text(json.dumps(result["config"], indent=2, ensure_ascii=False))
        print(green(f"\nconverted configuration written to {args.output}"))

    if not args.apply:
        print(dim("\nnothing was applied; re-run with --apply to use this configuration"))
        return 0

    outcome = client.apply_import(result["config"], chosen, merge=args.merge)
    if not outcome.get("ok"):
        print(red(outcome.get("error", "could not apply")))
        for problem in outcome.get("problems", []):
            print(red(f"  {problem}"))
        return 1
    print(green("\nconfiguration applied and saved"))
    if outcome.get("skipped"):
        print(yellow("  left disabled (no hardware to drive): " + ", ".join(outcome["skipped"])))
    return 0


def cmd_config(client, args) -> int:
    config = client.config()
    if args.output:
        Path(args.output).write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
        print(green(f"written to {args.output}"))
        return 0
    print(json.dumps(config, indent=2, ensure_ascii=False))
    return 0


def cmd_load(client, args) -> int:
    try:
        config = json.loads(Path(args.file).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(red(str(exc)))
        return 1
    result = client.set_config(config)
    if not result.get("ok"):
        print(red(result.get("error", "rejected")))
        for problem in result.get("problems", []):
            print(red(f"  {problem}"))
        return 1
    print(green("configuration applied and saved"))
    return 0


def cmd_reload(client, args) -> int:
    return _report(client.reload(), "configuration reloaded from disk")


def cmd_rescan(client, args) -> int:
    return _report(client.rescan(), "hardware re-enumerated")


# ----------------------------------------------------------------------


def _resolve_control(client, needle: str) -> str:
    """Accept a control id or a (case-insensitive) control name."""

    config = client.config()
    for control in config["controls"]:
        if control["id"] == needle:
            return needle
    matches = [c for c in config["controls"] if c["name"].lower() == needle.lower()]
    if len(matches) == 1:
        return matches[0]["id"]
    if not matches:
        partial = [c for c in config["controls"] if needle.lower() in c["name"].lower()]
        if len(partial) == 1:
            return partial[0]["id"]
        names = ", ".join(repr(c["name"]) for c in config["controls"])
        raise SystemExit(red(f"no control matches {needle!r}. Known controls: {names}"))
    raise SystemExit(red(f"{needle!r} matches more than one control; use its id instead"))


def _report(result: dict[str, Any], success: str) -> int:
    if result.get("ok"):
        print(green(success))
        return 0
    print(red(result.get("error", "failed")))
    for problem in result.get("problems", []):
        print(red(f"  {problem}"))
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fanctl",
        description="Talk to the fan control daemon from the terminal.",
    )
    parser.add_argument("--version", action="version", version=f"fanctl {__version__}")
    parser.add_argument("--session", action="store_true",
                        help="use the session bus (for a daemon started with --session)")
    parser.add_argument("--local", action="store_true",
                        help="drive the hardware directly instead of using the daemon "
                             "(needs root)")
    parser.add_argument("-c", "--config", type=Path, default=None,
                        help="configuration file, with --local")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show what every fan is doing right now").set_defaults(
        func=cmd_status)
    sub.add_parser("list", help="list the sensors and controls that were found").set_defaults(
        func=cmd_list)

    p = sub.add_parser("set", help="drive a fan at a fixed percentage")
    p.add_argument("control", help="control name or id")
    p.add_argument("percent", type=float)
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("auto", help="hand a fan back to its curve")
    p.add_argument("control")
    p.set_defaults(func=cmd_auto)

    sub.add_parser("enable", help="let the daemon drive the fans").set_defaults(func=cmd_enable)
    sub.add_parser("disable", help="give the fans back to the firmware").set_defaults(
        func=cmd_disable)

    p = sub.add_parser("calibrate", help="measure where a fan starts and stops")
    p.add_argument("control")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("import", help="import a FanControl (Windows) userConfig.json")
    p.add_argument("file")
    p.add_argument("--apply", action="store_true", help="actually use the result")
    p.add_argument("--merge", action="store_true",
                   help="add to the current configuration instead of replacing it")
    p.add_argument("--best-guess", action="store_true",
                   help="also accept the top candidate for ambiguous identifiers")
    p.add_argument("-o", "--output", help="write the converted configuration here")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("config", help="print the active configuration")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("load", help="apply a configuration file")
    p.add_argument("file")
    p.set_defaults(func=cmd_load)

    sub.add_parser("reload", help="re-read the configuration from disk").set_defaults(
        func=cmd_reload)
    sub.add_parser("rescan", help="look for hardware again").set_defaults(func=cmd_rescan)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = connect(args)
    except DaemonError as exc:
        print(red(str(exc)))
        return 1
    try:
        return args.func(client, args)
    except DaemonError as exc:
        print(red(str(exc)))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

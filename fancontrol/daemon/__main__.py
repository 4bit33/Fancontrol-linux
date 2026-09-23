"""Entry point for ``fancontrold``."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from .. import __version__
from ..core import config as config_module
from ..sdnotify import notify as _notify_systemd
from .service import FanControlService

log = logging.getLogger("fancontrold")


def _setup_logging(verbose: bool) -> None:
    # Under systemd the journal adds its own timestamps, so only print our own
    # when running in a terminal.
    fmt = "%(levelname)s %(name)s: %(message)s"
    if sys.stderr.isatty():
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fancontrold",
        description="Fan control daemon. Reads temperatures, evaluates curves "
        "and drives PWM outputs; the GUI talks to it over D-Bus.",
    )
    parser.add_argument("--version", action="version", version=f"fancontrold {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug messages")
    parser.add_argument(
        "-c", "--config", type=Path, default=None,
        help="configuration file (default: /etc/fancontrol-linux/config.json)",
    )
    parser.add_argument(
        "--session", action="store_true",
        help="use the session bus and the user's config file, for testing "
             "without root",
    )
    parser.add_argument(
        "--no-dbus", action="store_true",
        help="run the control loop only, without publishing a D-Bus service",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="read sensors and evaluate curves, but never write a PWM value",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    if not args.session and not args.no_dbus and os.geteuid() != 0:
        log.warning(
            "not running as root: PWM outputs are not writable and registering "
            "on the system bus will fail. Use --session for a test run."
        )

    config_path = args.config or config_module.default_config_path(args.session)
    service = FanControlService(config_path=config_path, session=args.session)

    try:
        service.start()
    except Exception:
        log.exception("could not start")
        return 1

    if args.dry_run:
        service.config.settings.control_enabled = False
        log.info("dry run: no PWM value will be written")

    if not service.registry.controls:
        log.warning(
            "no controllable fans were found. On most boards this means the "
            "super-I/O driver is missing: try 'sudo modprobe nct6775' or run "
            "'sudo sensors-detect'."
        )

    def handle_stop(signum, _frame):
        log.info("received %s, shutting down", signal.Signals(signum).name)
        _notify_systemd("STOPPING=1")
        service.stop()

    def handle_reload(_signum, _frame):
        log.info("reloading configuration")
        result = service.reload_config()
        if not result.get("ok"):
            log.error("reload failed: %s", result.get("error"))

    try:
        if args.no_dbus:
            # With a D-Bus service the signals belong to its main loop, which
            # installs its own handlers; here the loop is ours.
            signal.signal(signal.SIGTERM, handle_stop)
            signal.signal(signal.SIGINT, handle_stop)
            signal.signal(signal.SIGHUP, handle_reload)
            # Nothing registers on the bus in this mode, so announce readiness
            # here instead.
            _notify_systemd("READY=1")
            service.run_forever()
            return 0
        from .dbus_service import serve

        return serve(service, session=args.session)
    except ImportError:
        log.error(
            "dasbus is not installed, so the D-Bus service cannot be published. "
            "Re-run ./install.sh, which fetches it if the distribution has none, "
            "or run with "
            "--no-dbus to use the control loop on its own."
        )
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        # serve() shuts the service down on its own way out; calling it again
        # is harmless and covers the error paths that never reach it.
        service.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

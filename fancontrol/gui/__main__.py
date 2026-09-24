"""Entry point for the ``fancontrol-gui`` application."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import __version__
from . import i18n
from .i18n import tr

log = logging.getLogger("fancontrol-gui")

STYLE = """
QFrame#controlCard, QFrame#card {
    border: 1px solid palette(mid);
    border-radius: 10px;
    background: palette(base);
}
QFrame#card:hover {
    border-color: palette(highlight);
}
QFrame#addCard {
    border: 2px dashed palette(mid);
    border-radius: 10px;
    background: transparent;
}
QFrame#addCard:hover {
    border-color: palette(highlight);
}
QFrame#banner {
    background: palette(highlight);
    color: palette(highlighted-text);
}
QFrame#banner QLabel {
    color: palette(highlighted-text);
}
QToolButton#segment {
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 2px 10px;
    background: palette(button);
}
QToolButton#segment:checked {
    background: palette(highlight);
    color: palette(highlighted-text);
    border-color: palette(highlight);
}
QLabel#chip {
    border-radius: 8px;
    padding: 1px 8px;
    background: palette(alternate-base);
    color: palette(text);
}
QProgressBar {
    border: none;
    border-radius: 3px;
    background: palette(alternate-base);
}
QProgressBar::chunk {
    border-radius: 3px;
    background: palette(highlight);
}
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fancontrol-gui",
        description="Graphical fan control for Linux.",
    )
    parser.add_argument("--version", action="version", version=f"fancontrol-gui {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--session", action="store_true",
        help="talk to a daemon started with --session, on the session bus",
    )
    parser.add_argument(
        "--local", action="store_true",
        help="drive the hardware from this process instead of using the daemon "
             "(needs root, or a simulated hwmon tree)",
    )
    parser.add_argument("--lang", default=None,
                        help="language of the window, such as en or uk "
                             "(default: the system's; also FANCONTROL_LANG)")
    parser.add_argument("-c", "--config", type=Path, default=None,
                        help="configuration file, with --local")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Anything we do not recognise is handed to Qt, so the usual -style,
    # -platform and friends keep working.
    args, qt_args = build_parser().parse_known_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    app = QApplication([sys.argv[0], *qt_args])
    i18n.install(app, args.lang)
    app.setApplicationName("Fan Control")
    app.setApplicationDisplayName(tr("Fan Control"))
    app.setDesktopFileName("io.github.fancontrol_linux.gui")
    app.setWindowIcon(QIcon.fromTheme("sensors-fan", QIcon.fromTheme("computer")))
    app.setStyleSheet(STYLE)

    from .client import DBusProxy, LocalProxy, ProxyError

    try:
        proxy = LocalProxy(args.config) if args.local else DBusProxy(session=args.session)
    except ProxyError as exc:
        QMessageBox.critical(None, tr("Fan Control"), str(exc))
        return 1
    except PermissionError:
        QMessageBox.critical(
            None, tr("Fan Control"),
            tr("Direct hardware access needs root. Run the daemon instead:\n\n"
            "    sudo systemctl enable --now fancontrold"),
        )
        return 1

    from .main_window import MainWindow

    window = MainWindow(proxy)
    window.show()

    try:
        return app.exec()
    finally:
        proxy.close()


if __name__ == "__main__":
    raise SystemExit(main())

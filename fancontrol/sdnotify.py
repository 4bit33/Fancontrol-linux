"""Minimal sd_notify, so systemd units can use ``Type=notify``.

Implemented directly rather than pulling in a dependency: the protocol is one
datagram to the socket named in ``NOTIFY_SOCKET``.
"""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger(__name__)


def notify(state: str) -> bool:
    """Send a state string to systemd. Returns False when there is nothing to notify."""

    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        # An abstract socket is spelled with a leading NUL.
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(state.encode())
        return True
    except OSError:
        log.debug("sd_notify(%s) failed", state, exc_info=True)
        return False

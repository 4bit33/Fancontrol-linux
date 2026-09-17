"""The daemon's start and stop, exercised as a real process on a real bus.

This is the only test that runs the daemon the way systemd does. It exists
because of a bug nothing else could have caught: SIGTERM ran the handler and
logged "shutting down", but the GLib main loop was never told to quit, so the
process sat there until systemd killed it twenty seconds later - and the step
that hands every fan back to the firmware never ran. The machine was left with
its fans stuck wherever the daemon had last set them, on every reboot.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("dasbus", reason="the daemon needs dasbus to publish itself")

HELPER = Path(__file__).resolve().parent / "daemon_lifecycle_helper.py"

pytestmark = pytest.mark.skipif(
    shutil.which("dbus-run-session") is None,
    reason="dbus-run-session is needed to give the daemon a private bus",
)


@pytest.fixture(scope="module")
def lifecycle(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("lifecycle")
    completed = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(HELPER), str(workdir)],
        capture_output=True, text=True, timeout=180,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        pytest.fail(f"helper failed: {completed.returncode}\n{completed.stderr[-2000:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_the_daemon_publishes_itself(lifecycle):
    assert lifecycle["published"] is True, lifecycle.get("log", "")


def test_it_takes_the_fan_over_while_running(lifecycle):
    assert lifecycle["enable_while_running"] == "1"


def test_sigterm_ends_it_promptly(lifecycle):
    """Without loop.quit() this sat until systemd aborted it."""

    assert lifecycle["exit_seconds"] is not None, "the daemon did not exit within 10s"
    assert lifecycle["exit_seconds"] < 5


def test_it_exits_cleanly_rather_than_being_killed(lifecycle):
    assert lifecycle["returncode"] == 0


def test_the_firmware_gets_the_fan_back(lifecycle):
    """The part that actually matters: a stuck fan is a hot machine."""

    assert lifecycle["enable_after_stop"] == "2"


def test_it_says_so_in_the_log(lifecycle):
    assert "handed back to the firmware" in lifecycle["log"]

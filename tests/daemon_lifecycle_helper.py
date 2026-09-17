"""Driver for the daemon lifecycle test; run under dbus-run-session.

Starts a real daemon against a simulated hwmon tree, lets it take a fan over,
sends it SIGTERM, and reports how long it took to go and whether it handed the
hardware back. Prints one JSON object.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main(workdir: str) -> int:
    root = Path(workdir)
    hwmon = root / "hwmon"
    config = root / "config.json"

    env = dict(os.environ)
    env["FANCONTROL_HWMON_ROOT"] = str(hwmon)
    env["FANCONTROL_DISABLE"] = "nvidia"
    env["FANCONTROL_CONFIG"] = str(config)

    from fancontrol.hw.simulator import Simulator

    sim = Simulator(hwmon)
    sim.default_layout()
    sim.build()

    log = root / "daemon.log"
    with open(log, "w") as handle:
        daemon = subprocess.Popen(
            [sys.executable, "-m", "fancontrol.daemon", "--session", "-v"],
            stdout=handle, stderr=subprocess.STDOUT, env=env,
        )

    result: dict[str, object] = {"published": False}
    for _ in range(120):
        if "published org.fancontrol.Daemon" in log.read_text(errors="replace"):
            result["published"] = True
            break
        if daemon.poll() is not None:
            break
        time.sleep(0.25)

    if not result["published"]:
        daemon.kill()
        result["log"] = log.read_text(errors="replace")[-2000:]
        print(json.dumps(result))
        return 0

    # Turn one control on so the daemon actually takes the hardware over.
    data = json.loads(config.read_text())
    data["controls"][0]["enabled"] = True
    config.write_text(json.dumps(data))
    subprocess.run(
        [sys.executable, "-m", "fancontrol.cli", "--session", "reload"],
        env=env, capture_output=True,
    )
    time.sleep(2)

    enable_file = hwmon / "hwmon0" / "pwm1_enable"
    result["enable_while_running"] = enable_file.read_text().strip()

    started = time.monotonic()
    daemon.send_signal(signal.SIGTERM)
    try:
        daemon.wait(timeout=10)
        result["exit_seconds"] = round(time.monotonic() - started, 2)
        result["returncode"] = daemon.returncode
    except subprocess.TimeoutExpired:
        daemon.kill()
        daemon.wait(timeout=5)
        result["exit_seconds"] = None

    result["enable_after_stop"] = enable_file.read_text().strip()
    result["log"] = log.read_text(errors="replace")[-2000:]

    result.update(_crash_recovery(root, env, config, enable_file))
    print(json.dumps(result))
    return 0


def _crash_recovery(root, env, config, enable_file) -> dict[str, object]:
    """Kill a daemon outright, then check the next one cleans up after it.

    A daemon that is killed cannot hand the fan back, so the output stays in
    manual mode at whatever it was last set to. Nothing else on the system will
    ever put it right, so the next start has to.
    """

    out: dict[str, object] = {}
    log = root / "crash.log"
    state = root / "acquired.json"
    env = dict(env)
    env["FANCONTROL_STATE"] = str(state)

    with open(log, "w") as handle:
        daemon = subprocess.Popen(
            [sys.executable, "-m", "fancontrol.daemon", "--session", "-v"],
            stdout=handle, stderr=subprocess.STDOUT, env=env,
        )
    for _ in range(120):
        if "published org.fancontrol.Daemon" in log.read_text(errors="replace"):
            break
        time.sleep(0.25)
    time.sleep(2)

    out["crash_enable_while_running"] = enable_file.read_text().strip()
    out["crash_state_file_written"] = state.exists()

    daemon.kill()
    daemon.wait(timeout=5)
    # Nobody cleaned up, so the fan is still ours and still pinned.
    out["crash_enable_after_kill"] = enable_file.read_text().strip()

    # Now start again, with every control switched off, so the only reason the
    # output could be released is the recovery.
    data = json.loads(config.read_text())
    for control in data["controls"]:
        control["enabled"] = False
    config.write_text(json.dumps(data))

    log2 = root / "recover.log"
    with open(log2, "w") as handle:
        daemon = subprocess.Popen(
            [sys.executable, "-m", "fancontrol.daemon", "--session", "-v"],
            stdout=handle, stderr=subprocess.STDOUT, env=env,
        )
    for _ in range(120):
        if "published org.fancontrol.Daemon" in log2.read_text(errors="replace"):
            break
        time.sleep(0.25)
    time.sleep(1)

    out["crash_enable_after_recovery"] = enable_file.read_text().strip()
    out["crash_recovery_log"] = log2.read_text(errors="replace")[-1500:]

    daemon.send_signal(signal.SIGTERM)
    try:
        daemon.wait(timeout=10)
    except subprocess.TimeoutExpired:
        daemon.kill()
    return out


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))

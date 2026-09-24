"""The RPM spec and the Python metadata agree."""

from __future__ import annotations

import re
from pathlib import Path

from fancontrol import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_the_spec_carries_the_programs_version():
    spec = (ROOT / "fancontrol-linux.spec").read_text()
    assert re.search(r"^Version:\s*(\S+)", spec, re.M).group(1) == __version__
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert f'version = "{__version__}"' in pyproject


def test_the_spec_changelog_starts_with_this_version():
    spec = (ROOT / "fancontrol-linux.spec").read_text()
    changelog = spec.split("%changelog", 1)[1]
    first = next(line for line in changelog.splitlines() if line.startswith("*"))
    assert first.rstrip().endswith(f"- {__version__}-1")

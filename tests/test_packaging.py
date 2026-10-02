"""The Fedora spec, the Arch PKGBUILD and the Python metadata agree."""

from __future__ import annotations

import re
from pathlib import Path

from fancontrol import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_the_spec_carries_the_programs_version():
    spec = (ROOT / "packaging" / "fedora" / "fancontrol-linux.spec").read_text()
    assert re.search(r"^Version:\s*(\S+)", spec, re.M).group(1) == __version__
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert f'version = "{__version__}"' in pyproject


def test_the_spec_changelog_starts_with_this_version():
    spec = (ROOT / "packaging" / "fedora" / "fancontrol-linux.spec").read_text()
    changelog = spec.split("%changelog", 1)[1]
    first = next(line for line in changelog.splitlines() if line.startswith("*"))
    assert first.rstrip().endswith(f"- {__version__}-1")


def test_the_pkgbuild_carries_the_programs_version():
    pkgbuild = (ROOT / "packaging" / "arch" / "PKGBUILD").read_text()
    assert re.search(r"^pkgver=(\S+)", pkgbuild, re.M).group(1) == __version__


def test_the_pkgbuild_installs_files_that_exist():
    """Paths are relative to the source tree the PKGBUILD unpacks."""

    lines = (ROOT / "packaging" / "arch" / "PKGBUILD").read_text().splitlines()
    pkgbuild = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    for path in re.findall(r"\b((?:data|packaging)/[\w./-]+)", pkgbuild):
        assert (ROOT / path).exists(), path

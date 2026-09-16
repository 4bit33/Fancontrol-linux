"""Checks on the published D-Bus interface.

These need no bus: dasbus builds the introspection XML from the annotations
when the class is created, so asserting on that XML catches the whole class of
mistakes that only show up when the daemon tries to register.

The one that prompted this file: adding ``from __future__ import annotations``
to the D-Bus module turns every annotation into a string, dasbus reads
``"Str"`` instead of the type, and the daemon dies with "Invalid DBus type
'Str'" the moment it publishes.
"""

from __future__ import annotations

import re

import pytest

dasbus = pytest.importorskip("dasbus", reason="the D-Bus tests need dasbus")

from fancontrol.dbus_names import BUS_NAME, INTERFACE, OBJECT_PATH  # noqa: E402


@pytest.fixture(scope="module")
def xml() -> str:
    from fancontrol.daemon.dbus_service import FanControlDBusInterface

    return FanControlDBusInterface.__dbus_xml__


def signature_of(xml: str, member: str) -> tuple[list[str], list[str]]:
    """The in and out argument types of one method, in order."""

    match = re.search(rf'<method name="{member}">(.*?)</method>', xml, re.S)
    assert match, f"{member} is not on the interface"
    body = match.group(1)
    args = re.findall(r'<arg [^>]*type="([^"]+)" direction="([^"]+)"', body)
    return (
        [t for t, d in args if d == "in"],
        [t for t, d in args if d == "out"],
    )


def test_the_interface_is_named_as_the_clients_expect(xml):
    assert f'<interface name="{INTERFACE}">' in xml
    assert BUS_NAME == "org.fancontrol.Daemon"
    assert OBJECT_PATH == "/org/fancontrol/Daemon"


@pytest.mark.parametrize(
    "member, expected_in, expected_out",
    [
        ("GetVersion", [], ["s"]),
        ("GetInventory", [], ["s"]),
        ("GetStatus", [], ["s"]),
        ("GetConfig", [], ["s"]),
        ("SetConfig", ["s"], ["s"]),
        ("SaveConfig", [], ["s"]),
        ("ReloadConfig", [], ["s"]),
        ("Rescan", [], ["s"]),
        ("SetOverride", ["s", "d"], ["s"]),
        ("SetControlEnabled", ["b"], ["s"]),
        ("Calibrate", ["s"], ["s"]),
        ("ImportFanControl", ["s", "s"], ["s"]),
        ("ApplyImport", ["s", "s", "b"], ["s"]),
    ],
)
def test_every_method_has_the_signature_the_clients_use(xml, member, expected_in, expected_out):
    assert signature_of(xml, member) == (expected_in, expected_out)


def test_the_status_signal_carries_one_string(xml):
    match = re.search(r'<signal name="StatusChanged">(.*?)</signal>', xml, re.S)
    assert match, "StatusChanged is not on the interface"
    assert re.findall(r'type="([^"]+)"', match.group(1)) == ["s"]


def test_no_annotation_leaked_through_as_a_string(xml):
    """The failure mode that postponed annotations produce."""

    for bad in ("Str", "Bool", "Double", "Any"):
        assert f'type="{bad}"' not in xml


def test_internal_helpers_are_not_published(xml):
    # Anything underscore-prefixed must stay off the bus.
    assert "_on_status" not in xml
    assert 'name="_' not in xml


def test_the_bus_policy_opens_reading_and_restricts_writing():
    """The shipped policy must match the interface it is written for."""

    from pathlib import Path

    policy = Path(__file__).resolve().parent.parent / "data/dbus/org.fancontrol.Daemon.conf"
    text = policy.read_text()

    for member in ("GetVersion", "GetStatus", "GetConfig", "GetInventory"):
        assert f'send_member="{member}"' in text, f"{member} should be readable by anyone"
    # The write methods must not be granted in the default context.
    default = text.split('<policy context="default">')[1].split("</policy>")[0]
    for member in ("SetConfig", "SetOverride", "ApplyImport", "Calibrate"):
        assert member not in default, f"{member} must not be open to every user"
    assert '<policy group="wheel">' in text

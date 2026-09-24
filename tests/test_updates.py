"""The update notice: which versions count as newer, and when it speaks up.

No network here: GitHub's answer is handed to the checker directly.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtNetwork", reason="the update check needs QtNetwork")

from PySide6.QtCore import QSettings  # noqa: E402

from fancontrol.gui import updates  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return QSettings(str(tmp_path / "gui.conf"), QSettings.IniFormat)


@pytest.fixture
def checker(qapp, store):
    checker = updates.UpdateChecker(current="1.1.0", store=store)
    checker.seen = []
    checker.updateAvailable.connect(lambda version, url: checker.seen.append((version, url)))
    return checker


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("candidate, newer", [
    ("v1.1.1", True),
    ("v1.2", True),
    ("2.0.0", True),
    ("v1.1.0", False),
    ("v1.1", False),
    ("v1.0.9", False),
    ("nightly", False),
])
def test_what_counts_as_newer(candidate, newer):
    assert updates.is_newer(candidate, "1.1.0") is newer


def test_a_newer_release_is_announced_once_dismissed_never_again(checker):
    release = {"tag_name": "v1.2.0", "html_url": "https://example/1.2.0"}
    checker.handle(release)
    assert checker.seen == [("1.2.0", "https://example/1.2.0")]

    checker.dismiss("1.2.0")
    checker.handle(release)
    assert len(checker.seen) == 1

    # A later release is news again.
    checker.handle({"tag_name": "v1.3.0", "html_url": "https://example/1.3.0"})
    assert checker.seen[-1][0] == "1.3.0"


def test_the_same_or_an_older_release_says_nothing(checker):
    checker.handle({"tag_name": "v1.1.0"})
    checker.handle({"tag_name": "v1.0.0"})
    checker.handle({"tag_name": "v9.0.0", "prerelease": True})
    checker.handle({"message": "API rate limit exceeded"})
    assert checker.seen == []


def test_it_asks_at_most_once_a_day_but_remembers_the_answer(checker, store, monkeypatch):
    asked = []
    monkeypatch.setattr(updates, "QNetworkAccessManager", lambda parent: _Recorder(asked))
    checker.handle({"tag_name": "v1.2.0", "html_url": "u"})
    checker.seen.clear()

    checker.check()
    assert asked == []  # checked a moment ago
    assert checker.seen == [("1.2.0", "u")]  # but the known answer still shows

    store.setValue("updates/last_check", time.time() - updates.CHECK_EVERY - 1)
    checker.check()
    assert asked == [updates.LATEST_URL]


def test_switched_off_means_no_request_and_no_notice(checker, store, monkeypatch):
    asked = []
    monkeypatch.setattr(updates, "QNetworkAccessManager", lambda parent: _Recorder(asked))
    checker.handle({"tag_name": "v1.2.0"})
    checker.seen.clear()
    store.setValue("updates/last_check", 0)

    updates.set_checking_enabled(False, store)
    checker.check()
    assert asked == [] and checker.seen == []


def test_the_command_points_at_the_clone(tmp_path):
    command = updates.update_command(tmp_path / "my clone")
    assert command.startswith(f"cd '{tmp_path}/my clone' && git pull")
    assert command.endswith("sudo ./install.sh")


class _Recorder:
    """Stands in for QNetworkAccessManager and remembers what was fetched."""

    def __init__(self, asked):
        self.asked = asked

    def get(self, request):
        self.asked.append(request.url().toString())

        class _Reply:
            class finished:  # noqa: N801 - mimics a Qt signal
                @staticmethod
                def connect(_slot):
                    pass

        return _Reply()

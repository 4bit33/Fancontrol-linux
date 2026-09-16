"""The dialog that walks through importing a Windows FanControl config.

The interesting part is not the conversion but the mapping: a Windows
identifier such as ``/lpc/nct6798d/control/1`` has to end up pointing at a real
PWM output. The daemon resolves what it confidently can; this dialog shows
every identifier, pre-selects the automatic answer where there was one, and
makes the user choose for the rest rather than guessing on their behalf.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

WIN_PREFIX = "win:"

AUTOMATIC = "automatic"
AMBIGUOUS = "ambiguous"
UNMATCHED = "unmatched"

SECTION_TITLES = {
    AUTOMATIC: "Matched automatically",
    AMBIGUOUS: "Needs a decision",
    UNMATCHED: "Nothing on this machine matches",
}


class ImportDialog(QDialog):
    def __init__(self, result: dict, inventory: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import from FanControl")
        self.resize(860, 560)

        self._result = result
        self._inventory = inventory
        self._combos: dict[str, QComboBox] = {}

        summary = result["summary"]
        mapping = result["mapping"]

        layout = QVBoxLayout(self)

        heading = QLabel(
            f"Found <b>{summary['curves']}</b> curves and "
            f"<b>{summary['controls']}</b> fans in the file."
        )
        heading.setTextFormat(Qt.RichText)
        layout.addWidget(heading)

        explanation = QLabel(
            "Windows names hardware differently from Linux, so every sensor and "
            "fan has to be pointed at the real thing on this machine. Anything "
            "left unassigned is imported but stays switched off."
        )
        explanation.setWordWrap(True)
        explanation.setEnabled(False)
        layout.addWidget(explanation)

        for warning in result.get("warnings", []):
            item = QLabel("⚠  " + warning)
            item.setWordWrap(True)
            item.setStyleSheet("color: #f67400;")
            layout.addWidget(item)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["From Windows", "Use on this machine", "Why"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        layout.addWidget(self.tree, 1)

        self._populate(mapping)

        options = QHBoxLayout()
        self.merge = QCheckBox("Add to the current configuration instead of replacing it")
        self.merge.setToolTip(
            "Keeps the fans you have already set up. A fan the imported file "
            "also drives is replaced by the imported one."
        )
        options.addWidget(self.merge)
        options.addStretch(1)
        layout.addLayout(options)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------

    def _kind_of(self, win_id: str) -> str:
        identifier = win_id.removeprefix(WIN_PREFIX).lower()
        return "control" if "/control" in identifier else "temperature"

    def _targets_for(self, kind: str) -> list[tuple[str, str]]:
        section = "controls" if kind == "control" else "temperatures"
        return [
            (entry["id"], f"{entry['name']}  ({entry['device']['chip']})")
            for entry in self._inventory.get(section, [])
        ]

    def _section(self, key: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.tree, [SECTION_TITLES[key], "", ""])
        font = QFont(item.font(0))
        font.setBold(True)
        item.setFont(0, font)
        item.setFirstColumnSpanned(True)
        return item

    def _add_row(self, parent: QTreeWidgetItem, win_id: str, selected: str,
                 candidates: list[dict], reason: str) -> None:
        kind = self._kind_of(win_id)
        item = QTreeWidgetItem(parent, [win_id.removeprefix(WIN_PREFIX), "", reason])

        combo = QComboBox()
        combo.addItem("— leave unassigned —", "")
        # Offer the scored candidates first, then everything else of the right
        # kind, so a wrong automatic answer is still easy to correct.
        seen: set[str] = set()
        for candidate in candidates:
            target = candidate["target_id"]
            label = self._label_for(target)
            combo.addItem(f"{label}   ·  {candidate['score']:.0%} match", target)
            seen.add(target)
        for target, label in self._targets_for(kind):
            if target not in seen:
                combo.addItem(label, target)

        index = combo.findData(selected)
        combo.setCurrentIndex(max(0, index))
        self.tree.setItemWidget(item, 1, combo)
        self._combos[win_id] = combo

    def _label_for(self, target_id: str) -> str:
        for section in ("temperatures", "fans", "controls"):
            for entry in self._inventory.get(section, []):
                if entry["id"] == target_id:
                    return f"{entry['name']}  ({entry['device']['chip']})"
        return target_id

    def _populate(self, mapping: dict) -> None:
        applied = mapping.get("applied", {})
        pending = mapping.get("pending", {})
        unmatched = mapping.get("unmatched", [])

        if applied:
            section = self._section(AUTOMATIC)
            for win_id, target in sorted(applied.items()):
                self._add_row(section, win_id, target, [], "confident match")
            section.setExpanded(True)

        if pending:
            section = self._section(AMBIGUOUS)
            for win_id, candidates in sorted(pending.items()):
                best = candidates[0]["target_id"] if candidates else ""
                reason = candidates[0]["reason"] if candidates else ""
                # Deliberately not pre-selected: more than one candidate scored
                # about the same, and driving the wrong fan is worse than
                # importing one that is switched off.
                self._add_row(section, win_id, "", candidates, reason)
            section.setExpanded(True)

        if unmatched:
            section = self._section(UNMATCHED)
            for win_id in sorted(unmatched):
                self._add_row(section, win_id, "", [], "no comparable hardware found")
            section.setExpanded(True)

    # ------------------------------------------------------------------

    def mapping(self) -> dict[str, str]:
        return {
            win_id: combo.currentData()
            for win_id, combo in self._combos.items()
            if combo.currentData()
        }

    def merge_requested(self) -> bool:
        return self.merge.isChecked()

    def config(self) -> dict:
        return self._result["config"]

"""Live list of every temperature and fan reading, grouped by chip."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class SensorPanel(QWidget):
    """Shows what every sensor reads, and lets the user rename them."""

    renamed = Signal(str, str)  # sensor id, new name

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Sensor", "Reading"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree)

        self._items: dict[str, QTreeWidgetItem] = {}
        self._loading = False

    # ------------------------------------------------------------------

    def rebuild(self, inventory: dict, names: dict[str, str]) -> None:
        self._loading = True
        self.tree.clear()
        self._items.clear()

        groups: dict[str, QTreeWidgetItem] = {}

        def group_for(device: dict) -> QTreeWidgetItem:
            key = device["key"]
            if key not in groups:
                item = QTreeWidgetItem(self.tree, [device["label"], ""])
                font = QFont(item.font(0))
                font.setBold(True)
                item.setFont(0, font)
                item.setFirstColumnSpanned(False)
                item.setExpanded(True)
                groups[key] = item
            return groups[key]

        for section, suffix in (("temperatures", "°C"), ("fans", "rpm")):
            for entry in inventory.get(section, []):
                parent = group_for(entry["device"])
                label = names.get(entry["id"]) or entry["name"]
                item = QTreeWidgetItem(parent, [label, "—"])
                item.setData(0, Qt.UserRole, entry["id"])
                item.setData(1, Qt.UserRole, suffix)
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                item.setToolTip(0, f"{entry['id']}\nDouble-click to rename")
                self._items[entry["id"]] = item

        self.tree.expandAll()
        self._loading = False

    def update_status(self, status: dict) -> None:
        self._loading = True
        for sensor_id, value in status.get("temperatures", {}).items():
            item = self._items.get(sensor_id)
            if item is None:
                continue
            item.setText(1, "unavailable" if value is None else f"{value:.1f} °C")
            item.setForeground(1, self.palette().windowText() if value is not None
                               else self.palette().placeholderText())
        for sensor_id, value in status.get("fans", {}).items():
            item = self._items.get(sensor_id)
            if item is None:
                continue
            item.setText(1, "—" if value is None else f"{int(value)} rpm")
        self._loading = False

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._loading or column != 0:
            return
        sensor_id = item.data(0, Qt.UserRole)
        if sensor_id:
            self.renamed.emit(sensor_id, item.text(0).strip())

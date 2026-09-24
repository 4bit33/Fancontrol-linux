"""A layout that places widgets left to right and wraps to the next row.

The card grid of the main window: as many cards per row as the window is wide.
A port of Qt's own flow layout example, which Qt does not ship as a class.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 10) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(QMargins(0, 0, 0, 0))
        self.setSpacing(spacing)

    def __del__(self) -> None:
        while self.takeAt(0) is not None:
            pass

    # -- QLayout interface ---------------------------------------------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt name
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    # -- placement -----------------------------------------------------

    def _arrange(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y = area.x(), area.y()
        row_height = 0
        spacing = self.spacing()

        for item in self._items:
            widget = item.widget()
            if widget is not None and not widget.isVisible():
                continue
            hint = item.sizeHint()
            if item.hasHeightForWidth():
                # Wrapped text: the height the card needs at its own width.
                hint.setHeight(max(hint.height(), item.heightForWidth(hint.width())))
            if x + hint.width() > area.right() + 1 and row_height > 0:
                x = area.x()
                y += row_height + spacing
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + spacing
            row_height = max(row_height, hint.height())

        return y + row_height - rect.y() + margins.bottom()


def card_policy() -> QSizePolicy:
    """Cards keep the width they ask for, so the grid lines up."""
    return QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

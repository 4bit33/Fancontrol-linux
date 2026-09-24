"""The cards the main window is made of, apart from the fan cards.

Laid out the way FanControl on Windows does it: every curve and every sensor
is a small card in a grid, so the whole state of the machine fits on one
scrolling page instead of hiding behind list selections.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.models import (
    BaseCurve,
    Config,
    CurvePoint,
    MixCurve,
    SyncCurve,
    TargetCurve,
)
from ..i18n import tr
from .curve_editor import MIX_FUNCTION_LABELS, curve_range, curve_type_title, sample_curve
from .curve_graph import CurveGraph
from .flow_layout import FlowLayout, card_policy

CURVE_CARD_WIDTH = 300
SENSOR_CARD_WIDTH = 190

#: Object names of the widgets that are cards, for the style sheet and for
#: Section.equalize(); a plain label in a grid is left alone.
CARD_NAMES = {"card", "addCard", "controlCard"}


def small_font(widget: QWidget, delta: float = -1.0) -> QFont:
    font = QFont(widget.font())
    font.setPointSizeF(max(7.0, font.pointSizeF() + delta))
    return font


def bold_font(widget: QWidget, delta: float = 0.0) -> QFont:
    font = QFont(widget.font())
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() + delta)
    return font


def temperature_colour(value: float | None) -> str:
    """Green while comfortable, amber when warm, red when hot."""

    if value is None:
        return "#7f8c8d"
    if value < 50:
        return "#27ae60"
    if value < 75:
        return "#f39c12"
    return "#da4453"


def icon_button(theme_icon: str, fallback: str, tooltip: str) -> QToolButton:
    button = QToolButton()
    icon = QIcon.fromTheme(theme_icon)
    if icon.isNull():
        button.setText(fallback)
    else:
        button.setIcon(icon)
    button.setToolTip(tooltip)
    button.setAutoRaise(True)
    return button


class Card(QFrame):
    """A rounded panel; the look comes from the application style sheet.

    ``width`` is only the least it takes: a card grows to fit its text in the
    current style and font, and :meth:`Section.equalize` then lines the cards
    of one grid up at the widest.
    """

    clicked = Signal()

    def __init__(self, width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumWidth(width)
        self.setSizePolicy(card_policy())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Section(QWidget):
    """A titled group of cards that wraps to the window's width."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        header = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setFont(bold_font(self, 3))
        self.count = QLabel("")
        self.count.setEnabled(False)
        header.addWidget(self.title)
        header.addWidget(self.count)
        header.addStretch(1)
        self.actions = header
        outer.addLayout(header)

        self.body = QWidget()
        self.flow = FlowLayout(self.body, spacing=10)
        outer.addWidget(self.body)

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)

    def clear(self) -> None:
        while self.flow.count():
            item = self.flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def add(self, widget: QWidget) -> None:
        self.flow.addWidget(widget)

    def cards(self) -> list[QWidget]:
        items = (self.flow.itemAt(i) for i in range(self.flow.count()))
        return [item.widget() for item in items
                if item is not None and item.widget() is not None
                and item.widget().objectName() in CARD_NAMES]

    def equalize(self) -> None:
        """Give every card the width of the widest, so the grid lines up.

        Measured, not guessed: the same card needs more room in Breeze than in
        Fusion, and more in Ukrainian than in English.
        """

        cards = self.cards()
        if not cards:
            return
        for card in cards:
            card.ensurePolished()
            card.setMaximumWidth(16777215)
        width = max(max(card.minimumWidth(), card.sizeHint().width()) for card in cards)
        for card in cards:
            card.setFixedWidth(width)

    def set_count(self, count: int) -> None:
        self.count.setText(f"({count})")


class CurveCard(Card):
    """One curve: its shape, where it gets its temperature, what it outputs."""

    editRequested = Signal(str)
    removeRequested = Signal(str)

    def __init__(self, curve: BaseCurve, config: Config, inventory: dict,
                 parent: QWidget | None = None) -> None:
        super().__init__(CURVE_CARD_WIDTH, parent)
        self.curve = curve
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("Click to edit"))
        self.clicked.connect(lambda: self.editRequested.emit(self.curve.id))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.name = QLabel(curve.name)
        self.name.setFont(bold_font(self))
        self.name.setWordWrap(True)
        self.kind = QLabel(curve_type_title(curve.type))
        self.kind.setObjectName("chip")
        self.kind.setFont(small_font(self))
        edit = icon_button("document-edit", "✎", tr("Edit this curve"))
        edit.clicked.connect(lambda: self.editRequested.emit(self.curve.id))
        remove = icon_button("edit-delete", "✕", tr("Remove this curve"))
        remove.clicked.connect(lambda: self.removeRequested.emit(self.curve.id))
        header.addWidget(self.name, 1)
        header.addWidget(self.kind)
        header.addWidget(edit)
        header.addWidget(remove)
        layout.addLayout(header)

        self.source = QLabel(self._describe_source(config, inventory))
        self.source.setFont(small_font(self))
        self.source.setEnabled(False)
        self.source.setWordWrap(True)
        layout.addWidget(self.source)

        samples = sample_curve(curve)
        self.graph: CurveGraph | None = None
        if samples:
            self.graph = CurveGraph()
            self.graph.set_compact(True)
            self.graph.setFixedHeight(90)
            self.graph.set_temperature_range(*curve_range(curve))
            self.graph.set_points([CurvePoint(t, p) for t, p in samples])
            layout.addWidget(self.graph)

        footer = QHBoxLayout()
        self.reading = QLabel("—")
        self.reading.setFont(bold_font(self, 4))
        self.users = QLabel(self._describe_users(config))
        self.users.setFont(small_font(self))
        self.users.setEnabled(False)
        self.users.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.users.setWordWrap(True)
        footer.addWidget(self.reading)
        footer.addWidget(self.users, 1)
        layout.addLayout(footer)

    def _sensor_name(self, sensor_id: str, config: Config, inventory: dict) -> str:
        if sensor_id in config.sensor_names:
            return config.sensor_names[sensor_id]
        for entry in inventory.get("temperatures", []):
            if entry["id"] == sensor_id:
                return entry["name"]
        return sensor_id or tr("no sensor chosen")

    def _describe_source(self, config: Config, inventory: dict) -> str:
        curve = self.curve
        if isinstance(curve, MixCurve):
            names = [c.name for c in config.curves if c.id in curve.curve_ids]
            function = MIX_FUNCTION_LABELS.get(curve.function, curve.function)
            return tr("{function}: {names}", function=tr(function),
                      names=", ".join(names) or tr("nothing yet"))
        if isinstance(curve, SyncCurve):
            control = config.control_by_id(curve.control_id)
            return tr("Follows {fan}", fan=control.name if control else "—")
        sensor_id = getattr(curve, "sensor_id", "")
        if not sensor_id:
            return tr("Fixed speed, no sensor")
        text = "🌡 " + self._sensor_name(sensor_id, config, inventory)
        if isinstance(curve, TargetCurve):
            text += "  ·  " + tr("holds {temperature} °C",
                                 temperature=f"{curve.target_temperature:.0f}")
        return text

    def _describe_users(self, config: Config) -> str:
        users = [c.name for c in config.controls if c.curve_id == self.curve.id and not c.hidden]
        return tr("Drives: {fans}", fans=", ".join(users)) if users else ""

    def update_status(self, status: dict) -> None:
        value = status.get("curve_values", {}).get(self.curve.id)
        error = status.get("curve_errors", {}).get(self.curve.id)
        sensor_id = getattr(self.curve, "sensor_id", "")
        temperature = status.get("temperatures", {}).get(sensor_id) if sensor_id else None

        if error:
            self.reading.setText(tr("unavailable"))
            self.reading.setStyleSheet("color: #da4453;")
            self.reading.setToolTip(error)
        elif value is not None:
            text = f"{value:.0f}%"
            if temperature is not None:
                text = f"{temperature:.0f} °C → " + text
            self.reading.setText(text)
            self.reading.setStyleSheet("")
            self.reading.setToolTip("")
        if self.graph is not None:
            self.graph.set_reading(temperature, value)


class AddCard(Card):
    """The dashed "+ add" card at the end of a grid."""

    def __init__(self, text: str, width: int, height: int, parent: QWidget | None = None) -> None:
        super().__init__(width, parent)
        self.setObjectName("addCard")
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        label = QLabel("＋\n" + text)
        label.setAlignment(Qt.AlignCenter)
        label.setFont(bold_font(self, 1))
        label.setEnabled(False)
        layout.addWidget(label)


class SensorCard(Card):
    """A temperature or a fan speed, with a colour cue for temperatures."""

    renameRequested = Signal(str)

    def __init__(self, sensor_id: str, name: str, chip: str, kind: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(SENSOR_CARD_WIDTH, parent)
        self.sensor_id = sensor_id
        self.kind = kind
        self.setToolTip(tr("{id}\nDouble-click to rename", id=sensor_id))

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 10, 0)
        outer.setSpacing(8)
        self.stripe = QFrame()
        self.stripe.setFixedWidth(4)
        outer.addWidget(self.stripe)

        text = QVBoxLayout()
        text.setContentsMargins(0, 8, 0, 8)
        text.setSpacing(0)
        self.name = QLabel()
        self.name.setFont(small_font(self, 0))
        self.value = QLabel("—")
        self.value.setFont(bold_font(self, 4))
        self.chip = QLabel(chip)
        self.chip.setFont(small_font(self, -1.5))
        self.chip.setEnabled(False)
        text.addWidget(self.name)
        text.addWidget(self.value)
        text.addWidget(self.chip)
        outer.addLayout(text, 1)
        self.set_name(name)
        self._paint_stripe(None)

    def set_name(self, name: str) -> None:
        self.name.setWordWrap(True)
        self.name.setText(name)
        self.name.setToolTip(name)

    def _paint_stripe(self, value: float | None) -> None:
        colour = temperature_colour(value) if self.kind == "temperature" else "#3daee9"
        self.stripe.setStyleSheet(
            f"background: {QColor(colour).name()}; border-top-left-radius: 8px;"
            " border-bottom-left-radius: 8px;"
        )

    def update_value(self, value: float | None) -> None:
        if value is None:
            self.value.setText(tr("unavailable") if self.kind == "temperature" else "—")
        elif self.kind == "temperature":
            self.value.setText(f"{value:.1f} °C")
        else:
            self.value.setText(tr("{rpm} rpm", rpm=int(value)))
        self._paint_stripe(value)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.renameRequested.emit(self.sensor_id)

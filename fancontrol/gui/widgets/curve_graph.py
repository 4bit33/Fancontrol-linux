"""The interactive temperature/speed graph.

Editable for graph curves - drag a point, double click to add one, right click
to remove one - and read-only for the curve types whose shape is computed
rather than drawn (flat, linear, trigger).

Colours come from the application palette so the widget follows the Plasma
colour scheme instead of hard-coding a light theme.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core.models import CurvePoint
from ..i18n import tr

#: Default temperature range. Individual curves override it: a GPU curve is
#: commonly drawn up to 120 C, and its points have to stay reachable.
MIN_TEMP = 0.0
MAX_TEMP = 100.0
#: Click tolerance, in pixels, for grabbing a point.
GRAB_RADIUS = 11.0
MARGIN_LEFT = 44
MARGIN_BOTTOM = 26
MARGIN_TOP = 12
MARGIN_RIGHT = 12


class CurveGraph(QWidget):
    """Draws, and optionally edits, a temperature to fan-speed curve."""

    pointsChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[CurvePoint] = []
        self._editable = True
        self._dragging: int | None = None
        self._hovered: int | None = None
        self._current_temp: float | None = None
        self._current_percent: float | None = None
        self._accent = QColor("#3daee9")
        self._min_temp = MIN_TEMP
        self._max_temp = MAX_TEMP
        self._compact = False

        self.setMinimumSize(340, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # ------------------------------------------------------------------
    # data

    def set_points(self, points: list[CurvePoint]) -> None:
        self._points = sorted((CurvePoint(p.temperature, p.percent) for p in points),
                              key=lambda p: p.temperature)
        self.update()

    def points(self) -> list[CurvePoint]:
        return [CurvePoint(p.temperature, p.percent) for p in self._points]

    def set_temperature_range(self, low: float, high: float) -> None:
        """Set the horizontal range. Points outside it would be unreachable."""

        if high - low < 10:
            high = low + 10
        self._min_temp = float(low)
        self._max_temp = float(high)
        self.update()

    def temperature_range(self) -> tuple[float, float]:
        return self._min_temp, self._max_temp

    def set_compact(self, compact: bool) -> None:
        """A small read-only preview: no axis labels, no value tag, thin margins."""

        self._compact = compact
        if compact:
            self.set_editable(False)
            self.setMinimumSize(120, 60)
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.update()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def set_accent(self, colour: QColor) -> None:
        self._accent = colour
        self.update()

    def set_reading(self, temperature: float | None, percent: float | None) -> None:
        """Show where the machine currently sits on the curve."""

        self._current_temp = temperature
        self._current_percent = percent
        self.update()

    # ------------------------------------------------------------------
    # coordinate mapping

    def _plot_rect(self) -> QRectF:
        if self._compact:
            return QRectF(2, 2, max(1.0, self.width() - 4), max(1.0, self.height() - 4))
        return QRectF(
            MARGIN_LEFT,
            MARGIN_TOP,
            max(1.0, self.width() - MARGIN_LEFT - MARGIN_RIGHT),
            max(1.0, self.height() - MARGIN_TOP - MARGIN_BOTTOM),
        )

    def _to_pixel(self, temperature: float, percent: float) -> QPointF:
        rect = self._plot_rect()
        span = self._max_temp - self._min_temp
        x = rect.left() + (temperature - self._min_temp) / span * rect.width()
        y = rect.bottom() - percent / 100.0 * rect.height()
        return QPointF(x, y)

    def _to_value(self, pos: QPointF) -> tuple[float, float]:
        rect = self._plot_rect()
        span = self._max_temp - self._min_temp
        temperature = self._min_temp + (pos.x() - rect.left()) / rect.width() * span
        percent = (rect.bottom() - pos.y()) / rect.height() * 100.0
        return (
            max(self._min_temp, min(self._max_temp, round(temperature))),
            max(0.0, min(100.0, round(percent))),
        )

    def _point_at(self, pos: QPointF) -> int | None:
        for index, point in enumerate(self._points):
            if (self._to_pixel(point.temperature, point.percent) - pos).manhattanLength() < GRAB_RADIUS * 1.6:
                return index
        return None

    # ------------------------------------------------------------------
    # painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        palette = self.palette()
        text_colour = palette.color(palette.ColorRole.WindowText)
        grid_colour = QColor(text_colour)
        grid_colour.setAlpha(38)
        faint = QColor(text_colour)
        faint.setAlpha(150)

        rect = self._plot_rect()
        painter.fillRect(rect, palette.color(palette.ColorRole.Base))

        self._paint_grid(painter, rect, grid_colour, faint)
        if self._points:
            self._paint_curve(painter, rect)
            if self._editable:
                self._paint_handles(painter)
        else:
            painter.setPen(QPen(faint))
            painter.drawText(rect, Qt.AlignCenter, tr("Double-click to add a point"))
        self._paint_reading(painter, rect, text_colour)

        # drawRect fills with the current brush, and the tooltip tag above may
        # have left one set, so the border has to be drawn with no brush at all.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(grid_colour, 1))
        painter.drawRect(rect)

    def _paint_grid(self, painter: QPainter, rect: QRectF, grid: QColor, faint: QColor) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        painter.setFont(font)

        step = self._tick_step()
        first = int(self._min_temp // step) * int(step)
        for temperature in range(first, int(self._max_temp) + 1, int(step)):
            if temperature < self._min_temp:
                continue
            x = self._to_pixel(temperature, 0).x()
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            if temperature % (step * 2) == 0 and not self._compact:
                painter.setPen(QPen(faint))
                painter.drawText(
                    QRectF(x - 18, rect.bottom() + 4, 36, MARGIN_BOTTOM - 6),
                    Qt.AlignHCenter | Qt.AlignTop,
                    f"{temperature}°",
                )

        for percent in range(0, 101, 50 if self._compact else 20):
            y = self._to_pixel(0, percent).y()
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            if self._compact:
                continue
            painter.setPen(QPen(faint))
            painter.drawText(
                QRectF(0, y - 9, MARGIN_LEFT - 6, 18),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{percent}%",
            )

    def _tick_step(self) -> int:
        """A grid spacing that gives roughly ten lines across the range."""

        span = self._max_temp - self._min_temp
        for step in (5, 10, 20, 25, 50):
            if span / step <= 12:
                return step
        return 50

    def _curve_path(self) -> QPainterPath:
        path = QPainterPath()
        first = self._points[0]
        path.moveTo(self._to_pixel(self._min_temp, first.percent))
        for point in self._points:
            path.lineTo(self._to_pixel(point.temperature, point.percent))
        last = self._points[-1]
        path.lineTo(self._to_pixel(self._max_temp, last.percent))
        return path

    def _paint_curve(self, painter: QPainter, rect: QRectF) -> None:
        path = self._curve_path()

        filled = QPainterPath(path)
        filled.lineTo(QPointF(rect.right(), rect.bottom()))
        filled.lineTo(QPointF(rect.left(), rect.bottom()))
        filled.closeSubpath()

        gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
        top = QColor(self._accent)
        top.setAlpha(90)
        bottom = QColor(self._accent)
        bottom.setAlpha(12)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.fillPath(filled, QBrush(gradient))

        painter.setPen(QPen(self._accent, 2.2))
        painter.drawPath(path)

    def _paint_handles(self, painter: QPainter) -> None:
        base = self.palette().color(self.palette().ColorRole.Base)
        for index, point in enumerate(self._points):
            centre = self._to_pixel(point.temperature, point.percent)
            radius = 6.5 if index == self._hovered or index == self._dragging else 5.0
            painter.setPen(QPen(self._accent, 2))
            painter.setBrush(QBrush(base))
            painter.drawEllipse(centre, radius, radius)

        active = self._dragging if self._dragging is not None else self._hovered
        if active is not None and 0 <= active < len(self._points):
            point = self._points[active]
            centre = self._to_pixel(point.temperature, point.percent)
            label = f"{point.temperature:.0f}° → {point.percent:.0f}%"
            self._draw_tag(painter, centre + QPointF(10, -10), label)

    def _paint_reading(self, painter: QPainter, rect: QRectF, text_colour: QColor) -> None:
        if self._current_temp is None:
            return
        x = self._to_pixel(self._current_temp, 0).x()
        marker = QColor(text_colour)
        marker.setAlpha(120)
        pen = QPen(marker, 1.4, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        if self._current_percent is not None:
            centre = self._to_pixel(self._current_temp, self._current_percent)
            painter.setPen(QPen(marker, 1.4, Qt.DashLine))
            painter.drawLine(QPointF(rect.left(), centre.y()), QPointF(x, centre.y()))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(text_colour))
            painter.drawEllipse(centre, 4.0 if not self._compact else 3.0, 4.0 if not self._compact else 3.0)
            if self._compact:
                return
            self._draw_tag(
                painter,
                QPointF(min(x + 8, rect.right() - 90), rect.top() + 4),
                f"{self._current_temp:.0f}° → {self._current_percent:.0f}%",
            )

    def _draw_tag(self, painter: QPainter, position: QPointF, text: str) -> None:
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 12
        height = metrics.height() + 6
        box = QRectF(position.x(), position.y(), width, height)
        # Keep the tag inside the widget so it never gets clipped at the edges.
        if box.right() > self.width():
            box.moveRight(self.width() - 2)
        if box.top() < 0:
            box.moveTop(2)

        background = self.palette().color(self.palette().ColorRole.ToolTipBase)
        background.setAlpha(230)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QPen(self.palette().color(self.palette().ColorRole.ToolTipText)))
        painter.drawText(box, Qt.AlignCenter, text)

    # ------------------------------------------------------------------
    # interaction

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._editable:
            return
        position = event.position()
        index = self._point_at(position)
        if event.button() == Qt.LeftButton:
            if index is None:
                return
            self._dragging = index
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.RightButton and index is not None:
            # Two points are the minimum that still describes a curve.
            if len(self._points) > 2:
                del self._points[index]
                self._hovered = None
                self.pointsChanged.emit()
                self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._editable:
            return
        position = event.position()

        if self._dragging is None:
            hovered = self._point_at(position)
            if hovered != self._hovered:
                self._hovered = hovered
                self.setCursor(Qt.OpenHandCursor if hovered is not None else Qt.ArrowCursor)
                self.update()
            return

        temperature, percent = self._to_value(position)
        # Keep the points in temperature order, so dragging one past its
        # neighbour pushes against it instead of scrambling the curve.
        lower = (
            self._points[self._dragging - 1].temperature + 1
            if self._dragging > 0
            else self._min_temp
        )
        upper = (
            self._points[self._dragging + 1].temperature - 1
            if self._dragging < len(self._points) - 1
            else self._max_temp
        )
        self._points[self._dragging] = CurvePoint(
            max(lower, min(upper, temperature)), percent
        )
        self.pointsChanged.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging is not None:
            self._dragging = None
            self.setCursor(Qt.OpenHandCursor)
            self.pointsChanged.emit()
            self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if not self._editable or event.button() != Qt.LeftButton:
            return
        if self._point_at(event.position()) is not None:
            return
        temperature, percent = self._to_value(event.position())
        self._points.append(CurvePoint(temperature, percent))
        self._points.sort(key=lambda p: p.temperature)
        self.pointsChanged.emit()
        self.update()

    def leaveEvent(self, event) -> None:
        if self._hovered is not None:
            self._hovered = None
            self.update()

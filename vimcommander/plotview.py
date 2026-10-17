"""Quick view of a CSV file (zp) or an LTspice .raw file as a plot: every numeric
column in its own strip, over one x axis (a rising column such as time, else the
row number).

The mouse shows each column's value at that point. Drag across the plot to zoom
into that part, right-click to zoom back out, double-click to see it all. Each
strip gives min, max, mean and RMS of the part in view. Long columns are drawn
as one min-max bar per pixel. Clicking a column's name plots the others against
it: as lines if it rises, else as a scatter of points (an optimization's designs,
efficiency against frequency); clicking the x label at the bottom left goes back.
Reading runs in a thread; results carry a token, so a stale one is dropped.
"""

import bisect
import math
import threading
from array import array

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from . import graphview, tabular
from .tabular import si

TRACE_COLORS = ("folder", "code", "image", "video", "audio", "document", "archive", "design")
STRIP_MIN = 60  # pixels per column; with more columns than fit, the plot scrolls
SCATTER_POINTS = 40000  # points drawn per strip in a scatter (statistics use every row)


class _Loader(QObject):
    done = Signal(int, object, str)  # token, Columns or None, error


class PlotView(QWidget):
    """Plots one tabular.Columns. `not_numeric` is emitted when the file has no
    numbers to plot, so the caller shows it as a table."""

    not_numeric = Signal(str)  # path
    summary = Signal(str)  # one line for the quick view's title

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.data: tabular.Columns | None = None
        self.x: int | None = None  # the column on the x axis; None = the row number
        self.scatter = False  # that column doesn't rise: points, not lines
        self._labels: list[tuple[QRectF, int | None]] = []  # clickable names: strips, and the x label (None)
        self.message = ""
        self.token = 0
        self.span = (0.0, 1.0)  # the x range in view
        self.zooms: list[tuple[float, float]] = []  # earlier spans, for right-click
        self.mouse: float | None = None  # the mouse's x in pixels, over the plot
        self.drag: tuple[float, float] | None = None  # a range being selected, in pixels
        self._made_for = None  # (span, width) the traces were made for
        self._traces: list[list[list[tuple[float, float]]]] = []  # per strip: lines of (pixel x, value)
        self._stats: list[tuple[float, float, float, float] | None] = []  # per strip, of the part in view
        self._file = ""
        self._stop = threading.Event()
        self._loader = _Loader()
        self._loader.done.connect(self._loaded)

    # Loading

    def load(self, path: str, source: str | bytes, reader=None) -> None:
        """Read `source` (the file's path, or its bytes from a zip) in a thread with
        `reader` (tabular.read_columns, or ltspice.read_raw)."""
        reader = reader or tabular.read_columns
        self.stop()
        token, stop = self.token, self._stop
        self.message, self._file = "Reading the numbers…", path
        self.update()

        def work():
            try:
                result, error = reader(source, path, stop), ""
            except Exception as e:  # always answer, or the view would say "Reading…" for good
                result, error = None, str(getattr(e, "strerror", None) or e) or type(e).__name__
            self._loader.done.emit(token, result, error)

        threading.Thread(target=work, daemon=True).start()

    def stop(self) -> None:
        """Forget the plot (and stop reading a file)."""
        self.token += 1
        self._stop.set()
        self._stop = threading.Event()
        self.data, self._traces, self._stats, self._made_for = None, [], [], None
        self.zooms, self.drag = [], None
        self.setMinimumHeight(0)

    def _loaded(self, token: int, result, error: str) -> None:
        if token != self.token:
            return
        if result is None:
            if error:
                self.message = f"Cannot read this file: {error}"
                self.update()
            else:
                self.not_numeric.emit(self._file)
            return
        self.data, self.message = result, ""
        self.set_x(result.x)

    def set_x(self, column: int | None) -> None:
        """Plot the other columns against `column` (None: the row number)."""
        d = self.data
        self.x = column
        self.scatter = column is not None and (d.gaps[column] or not tabular._rising(d.values[column]))
        self.zooms, self.span, self._made_for = [], self._full(), None
        self.setMinimumHeight(int(len(self._strips()) * STRIP_MIN + self._axis_height()))
        x = "row number" if column is None else self._name(column) + ("  (scatter)" if self.scatter else "")
        part = "" if d.complete else "   first part"
        self.summary.emit(f"{d.rows} rows × {len(d.names)} columns   x: {x}{part}"
                          "   (drag = zoom, right-click = back, click a name = plot against it)")
        self.update()

    # Data helpers

    def _strips(self) -> list[int]:
        d = self.data
        return [k for k in range(len(d.names)) if k != self.x] if d else []

    def _name(self, k: int) -> str:
        d = self.data
        return d.names[k] + (f" [{d.units[k]}]" if d.units[k] else "")

    def _full(self) -> tuple[float, float]:
        d = self.data
        if self.x is None:
            return 0.0, float(d.rows - 1)
        xs = d.values[self.x]
        if not self.scatter:
            return xs[0], xs[-1]
        finite = [v for v in xs if v == v]
        lo, hi = (min(finite), max(finite)) if finite else (0.0, 1.0)
        return (lo, hi) if hi > lo else (lo - 1, hi + 1)

    def _index(self, x: float, after: bool = False) -> int:
        """The first sample at x or later (after: later than x)."""
        d = self.data
        if self.x is None:
            return min(d.rows, max(0, math.floor(x) + 1 if after else math.ceil(x)))
        return (bisect.bisect_right if after else bisect.bisect_left)(d.values[self.x], x)

    def _x_of(self, i: int) -> float:
        d = self.data
        return float(i) if self.x is None else d.values[self.x][i]

    def _nearest(self, x: float) -> int:
        i = min(self._index(x), self.data.rows - 1)
        if i > 0 and abs(self._x_of(i - 1) - x) <= abs(self._x_of(i) - x):
            i -= 1
        return i

    # Geometry: the strips share the plot area; the x axis is below them

    def _small_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        return font

    def _axis_height(self) -> float:
        return QFontMetricsF(self._small_font()).height() + 10

    def _area(self) -> QRectF:
        gutter = QFontMetricsF(self._small_font()).horizontalAdvance("-000.0m") + 12
        return QRectF(gutter, 0, max(10.0, self.width() - gutter - 12), self.height() - self._axis_height())

    def _plot_rect(self, strip: int, count: int) -> tuple[QRectF, QRectF]:
        """(the strip's label row, where its trace goes)."""
        area = self._area()
        height = area.height() / max(count, 1)
        top = area.top() + strip * height
        label = QFontMetricsF(self.font()).height() + 4
        return (QRectF(8, top + 2, self.width() - 16, label),
                QRectF(area.left(), top + label + 2, area.width(), max(4.0, height - label - 8)))

    def _px(self, x: float) -> float:
        area = self._area()
        x0, x1 = self.span
        return area.left() + (x - x0) / (x1 - x0) * area.width()

    def _x_at(self, px: float) -> float:
        area = self._area()
        x0, x1 = self.span
        return x0 + (px - area.left()) / area.width() * (x1 - x0)

    # Traces: for many samples per pixel, each pixel column gets a min-max bar

    def _make_traces(self) -> None:
        d, area = self.data, self._area()
        width = max(1, int(area.width()))
        if self._made_for == (self.span, width):
            return
        self._made_for = (self.span, width)
        if self.scatter:
            self._make_scatter(area)
            return
        x0, x1 = self.span
        step = (x1 - x0) / width
        bounds = [self._index(x0 + p * step) for p in range(width)] + [self._index(x1, after=True)]
        lo, hi = bounds[0], bounds[-1]
        dense = hi - lo > 2 * width
        self._traces, self._stats = [], []
        for k in self._strips():
            col, gaps = d.values[k], d.gaps[k]
            lines, line = [], []
            if dense:
                for p in range(width):
                    a, b = bounds[p], bounds[p + 1]
                    if a >= b:
                        continue  # no sample here: the line runs on to the next one
                    seg = col[a:b]
                    if gaps:
                        seg = [v for v in seg if v == v]
                        if not seg:  # only NaNs: a gap in the line
                            if line:
                                lines.append(line)
                                line = []
                            continue
                    px = area.left() + p + 0.5
                    line += [(px, max(seg)), (px, min(seg))]
            else:  # few samples: a line through each, and one beyond each edge
                scale = area.width() / (x1 - x0)
                for i in range(max(lo - 1, 0), min(hi + 1, d.rows)):
                    v = col[i]
                    if v != v:
                        if line:
                            lines.append(line)
                            line = []
                        continue
                    line.append((area.left() + (self._x_of(i) - x0) * scale, v))
            if line:
                lines.append(line)
            self._traces.append(lines)
            self._stats.append(tabular.stats(col, lo, hi, gaps))

    def _make_scatter(self, area: QRectF) -> None:
        """Points (x, value) of the rows whose x is in view: at most SCATTER_POINTS
        drawn, every one counted in the statistics."""
        d, (x0, x1) = self.data, self.span
        xs = d.values[self.x]
        rows = [i for i, v in enumerate(xs) if x0 <= v <= x1]  # NaN is never in range
        shown = rows[::max(1, len(rows) // SCATTER_POINTS)]
        scale = area.width() / (x1 - x0)
        pixels = [area.left() + (xs[i] - x0) * scale for i in shown]
        self._traces, self._stats = [], []
        for k in self._strips():
            col = d.values[k]
            self._traces.append([[(px, col[i]) for px, i in zip(pixels, shown) if col[i] == col[i]]])
            values = array("d", [col[i] for i in rows])
            self._stats.append(tabular.stats(values, 0, len(values), d.gaps[k]))

    # Painting

    def paintEvent(self, event):
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        text = pal.color(QPalette.ColorRole.Text)
        dim = pal.color(QPalette.ColorRole.PlaceholderText)
        if not self.data:
            p.setPen(dim)
            p.drawText(self.rect().adjusted(10, 10, -10, -10), Qt.AlignmentFlag.AlignTop, self.message)
            return
        self._make_traces()
        area, small = self._area(), self._small_font()
        font, bold = QFont(self.font()), QFont(self.font())
        bold.setBold(True)
        metrics, small_metrics = QFontMetricsF(font), QFontMetricsF(small)
        faint = QColor(dim)
        faint.setAlpha(40)

        # x grid and tick labels
        values, digits = tabular.ticks(*self.span, max(2, int(area.width() / 110)))
        p.setFont(small)
        axis_y = area.bottom() + 4
        for v in values:
            x = self._px(v)
            p.setPen(QPen(faint, 1))
            p.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            label = si(v, digits)
            w = small_metrics.horizontalAdvance(label)
            p.setPen(dim)
            p.drawText(QRectF(min(max(x - w / 2, 2), self.width() - w - 2), axis_y, w + 1,
                              small_metrics.height()), Qt.AlignmentFlag.AlignLeft, label)

        cursor = self._nearest(self._x_at(self.mouse)) if self.mouse is not None and not self.scatter else None
        strips = self._strips()
        self._labels = []
        for s, k in enumerate(strips):
            color = QColor(graphview.COLORS.get(TRACE_COLORS[s % len(TRACE_COLORS)], "#7aa2f7"))
            label_rect, rect = self._plot_rect(s, len(strips))
            stats = self._stats[s] if s < len(self._stats) else None
            # name, the value under the mouse, and the statistics of what is in view
            name = self._name(k)
            p.setFont(bold)
            p.setPen(color)
            p.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            used = QFontMetricsF(bold).horizontalAdvance(name) + 12
            self._labels.append((QRectF(label_rect.left(), label_rect.top(), used - 6, label_rect.height()), k))
            p.setFont(font)
            if cursor is not None:
                value = f"= {si(self.data.values[k][cursor])}"
                p.setPen(text)
                p.drawText(label_rect.adjusted(used, 0, 0, 0),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)
                used += metrics.horizontalAdvance(value) + 12
            if stats:
                summary = "   ".join(f"{n} {si(v)}" for n, v in zip(("min", "max", "mean", "rms"), stats))
                if metrics.horizontalAdvance(summary) < label_rect.width() - used:
                    p.setPen(dim)
                    p.drawText(label_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, summary)
            p.setPen(QPen(faint, 1))
            p.drawLine(QPointF(0, rect.bottom() + 4), QPointF(self.width(), rect.bottom() + 4))
            if not stats:
                p.setPen(dim)
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "no numbers in this part")
                continue
            low, high = stats[0], stats[1]
            pad = (high - low) * 0.06 or abs(high) * 0.1 or 1.0
            y0, y1 = low - pad, high + pad

            def y_of(v: float) -> float:
                return rect.bottom() - (v - y0) / (y1 - y0) * rect.height()

            # the value range at the left, a zero line if it is in range
            p.setFont(small)
            p.setPen(dim)
            gutter = QRectF(0, 0, area.left() - 6, small_metrics.height())
            p.drawText(gutter.translated(0, y_of(high) - small_metrics.height() / 2),
                       Qt.AlignmentFlag.AlignRight, si(high))
            if high != low:
                p.drawText(gutter.translated(0, y_of(low) - small_metrics.height() / 2),
                           Qt.AlignmentFlag.AlignRight, si(low))
            if y0 < 0 < y1:
                p.setPen(QPen(dim, 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(rect.left(), y_of(0)), QPointF(rect.right(), y_of(0)))
            p.setClipRect(rect.adjusted(-1, -2, 1, 2))
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(color, 2.5 if self.scatter else 1.2))
            for line in self._traces[s] if s < len(self._traces) else []:
                points = QPolygonF([QPointF(x, y_of(v)) for x, v in line])
                if self.scatter:
                    p.drawPoints(points)
                else:
                    p.drawPolyline(points)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setClipping(False)

        if self.drag:
            a, b = sorted(self.drag)
            band = QColor(pal.color(QPalette.ColorRole.Highlight))
            band.setAlpha(60)
            p.fillRect(QRectF(a, area.top(), b - a, area.height()), band)
        # the x axis' column, at the bottom left: click it to go back to the file's own x
        x_name = "x: " + ("row" if self.x is None else self.data.names[self.x])
        x_rect = QRectF(2, axis_y, area.left() - 6, small_metrics.height())
        p.setFont(small)
        p.setPen(text if self.x != self.data.x else dim)
        p.drawText(x_rect, Qt.AlignmentFlag.AlignLeft,
                   small_metrics.elidedText(x_name, Qt.TextElideMode.ElideRight, x_rect.width()))
        self._labels.append((x_rect, None))
        if self.mouse is not None and (cursor is not None or self.scatter):
            line = QColor(text)
            line.setAlpha(120)
            p.setPen(QPen(line, 1))
            p.drawLine(QPointF(self.mouse, area.top()), QPointF(self.mouse, area.bottom()))
            x_value = self._x_at(self.mouse) if self.scatter else self._x_of(cursor)
            label = si(x_value, max(digits + 1, 4))
            w = small_metrics.horizontalAdvance(label) + 10
            box = QRectF(min(max(self.mouse - w / 2, 0), self.width() - w), axis_y - 2, w,
                         small_metrics.height() + 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(pal.color(QPalette.ColorRole.Base))
            p.drawRoundedRect(box, 3, 3)
            p.setPen(text)
            p.setFont(small)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)

    # Mouse: hover for values, drag to zoom, right-click back, double-click all

    def resizeEvent(self, event):
        self._made_for = None
        super().resizeEvent(event)

    def mouseMoveEvent(self, event):
        x, area = event.position().x(), self._area()
        self.mouse = x if self.data and area.left() <= x <= area.right() else None
        if self.drag:
            self.drag = (self.drag[0], min(max(x, area.left()), area.right()))
        self.update()

    def leaveEvent(self, event):
        self.mouse = None
        self.update()

    def mousePressEvent(self, event):
        if not self.data:
            return
        x, area = event.position().x(), self._area()
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, column in self._labels:  # a column's name: plot the others against it
                if rect.contains(event.position()):
                    self.set_x(self.data.x if column is None else column)
                    return
        if event.button() == Qt.MouseButton.LeftButton and area.left() <= x <= area.right():
            self.drag = (x, x)
        elif event.button() == Qt.MouseButton.RightButton:
            self.zoom_out()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.drag:
            return
        a, b = sorted(self.drag)
        self.drag = None
        if b - a >= 4:
            self.zoom_to(self._x_at(a), self._x_at(b))
        self.update()

    def mouseDoubleClickEvent(self, event):
        if self.data and event.button() == Qt.MouseButton.LeftButton:
            self.zooms, self.span = [], self._full()
            self.update()

    def zoom_to(self, x0: float, x1: float) -> None:
        if not self.data:
            return
        if self.scatter:
            if sum(1 for v in self.data.values[self.x] if x0 <= v <= x1) < 2:
                return
        elif self._index(x1, after=True) - self._index(x0) < 2:
            return  # fewer than two samples there: nothing to draw a line through
        self.zooms.append(self.span)
        self.span = (x0, x1)
        self.update()

    def zoom_out(self) -> None:
        if self.zooms:
            self.span = self.zooms.pop()
            self.update()

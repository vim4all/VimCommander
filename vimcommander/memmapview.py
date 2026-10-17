"""Quick view of a firmware memory map (.map / .elf, parsed by memmap.py): a
usage bar per memory region, and a treemap of what fills the chosen one.

Click a bar to pick its region, click a box to zoom into it (section, then
object file, then functions), right-click or the path line to go back up.
Parsing runs in a thread; results carry a token, so a stale one is dropped.
"""

import threading

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPalette, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from . import graph, graphview, memmap

KIND_COLORS = {  # section kinds -> graph colors, so the theme applies
    "code": "code", "rodata": "document", "data": "archive", "bss": "image",
    "stack": "other", "vectors": "root", "other": "program",
}
LEGEND = (("code", "code"), ("rodata", "constants"), ("data", "data"), ("bss", "zeroed"),
          ("stack", "heap / stack"))


def usage_color(fraction: float | None) -> QColor:
    if fraction is None:
        return QColor(graphview.COLORS["folder"])
    return QColor(graphview.COLORS["code" if fraction < 0.7 else "image" if fraction < 0.9 else "video"])


class _Loader(QObject):
    done = Signal(int, object, str)  # token, MemMap or None, error


class MemMapView(QWidget):
    """Shows one MemMap. `not_a_map` is emitted when the file turns out to be
    something else (a .map that isn't a linker map), so the caller shows text."""

    not_a_map = Signal(str)  # path
    summary = Signal(str)  # one line for the quick view's title

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.map: memmap.MemMap | None = None
        self.region = 0
        self.path: list[memmap.Node] = []  # zoomed into: region tree, then children
        self.token = 0
        self.message = ""
        self._bars: list[tuple[int, QRectF]] = []
        self._boxes: list[tuple[memmap.Node, QRectF, memmap.Node]] = []  # node, rect, its top-level box
        self._crumb = QRectF()
        self.hover: memmap.Node | None = None
        self._loader = _Loader()
        self._loader.done.connect(self._loaded)

    # Loading

    def load(self, path: str) -> None:
        self.token += 1
        token = self.token
        self.map, self.message, self.path, self.hover = None, "Reading the memory map…", [], None
        self.update()

        def work():
            try:
                result, error = memmap.parse(path), ""
            except (OSError, ValueError) as e:
                result, error = None, str(getattr(e, "strerror", None) or e)
            self._loader.done.emit(token, result, error)

        threading.Thread(target=work, daemon=True).start()
        self._file = path

    def stop(self) -> None:
        self.token += 1

    def _loaded(self, token: int, result, error: str) -> None:
        if token != self.token:
            return
        if result is None:
            if error:
                self.message = f"Cannot read this file: {error}"
                self.update()
            else:
                self.not_a_map.emit(self._file)
            return
        self.map, self.message = result, ""
        ram = [i for i, r in enumerate(result.regions) if r.kind == "ram" and r.used]
        flash = [i for i, r in enumerate(result.regions) if r.kind == "flash" and r.used]
        self.region = (flash or ram or [0])[0]
        self.path = [result.regions[self.region].tree] if result.regions else []
        parts = []
        for r in result.regions:
            if r.used or r.kind in ("flash", "ram"):
                pct = f" {r.fraction * 100:.0f}%" if r.fraction is not None else ""
                parts.append(f"{r.name} {memmap.human(r.used)}{pct}")
        from_map = "" if result.path == self._file else f" (from {result.path.replace(chr(92), '/').rsplit('/', 1)[-1]})"
        self.summary.emit(f"{result.source}{from_map}   " + "   ".join(parts))
        self.update()

    # Painting

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        text = pal.color(QPalette.ColorRole.Text)
        dim = pal.color(QPalette.ColorRole.PlaceholderText)
        font = QFont(self.font())
        metrics = QFontMetricsF(font)
        if not self.map:
            p.setPen(dim)
            p.drawText(self.rect().adjusted(10, 10, -10, -10), Qt.AlignmentFlag.AlignTop, self.message)
            return
        line = metrics.height()
        y = 8.0
        self._bars = []
        label_w = min(self.width() * 0.25, max(metrics.horizontalAdvance(r.name) for r in self.map.regions) + 14)
        for i, r in enumerate(self.map.regions):
            row = QRectF(8, y, self.width() - 16, line + 8)
            self._bars.append((i, row))
            bold = QFont(font)
            bold.setBold(i == self.region)
            p.setFont(bold)
            p.setPen(text if i == self.region else dim)
            p.drawText(QRectF(row.left(), row.top(), label_w, row.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, r.name)
            p.setFont(font)
            pct = f"   {r.fraction * 100:.1f}%" if r.fraction is not None else ""
            right = f"{memmap.human(r.used)}" + (f" of {memmap.human(r.length)}" if r.length else "") + pct
            right_w = metrics.horizontalAdvance(right) + 12
            bar = QRectF(row.left() + label_w, row.top() + 3, max(20.0, row.width() - label_w - right_w), row.height() - 6)
            track = QColor(dim)
            track.setAlpha(45)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(bar, 4, 4)
            fill = QRectF(bar)
            fraction = r.fraction if r.fraction is not None else (1.0 if r.used else 0.0)
            fill.setWidth(bar.width() * min(1.0, fraction))
            if fill.width() > 0:
                p.setBrush(usage_color(r.fraction))
                p.drawRoundedRect(fill, 4, 4)
            if i == self.region:
                p.setPen(QPen(text, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(bar.adjusted(-1, -1, 1, 1), 5, 5)
            p.setPen(text if i == self.region else dim)
            p.drawText(QRectF(bar.right() + 8, row.top(), right_w, row.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, right)
            y += row.height() + 2

        # Path line and legend
        y += 4
        names = [self.map.regions[self.region].name] + [n.name for n in self.path[1:]]
        crumb = "  ›  ".join(names) + ("     (right-click: back)" if len(self.path) > 1 else "     (click a box to zoom in)")
        self._crumb = QRectF(8, y, self.width() - 16, line)
        p.setPen(dim)
        p.drawText(self._crumb, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   metrics.elidedText(crumb, Qt.TextElideMode.ElideMiddle, self._crumb.width()))
        y += line + 2
        x = 8.0
        small = QFont(font)
        small.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        sm = QFontMetricsF(small)
        p.setFont(small)
        for kind, label in LEGEND:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(graphview.COLORS[KIND_COLORS[kind]]))
            p.drawRoundedRect(QRectF(x, y + 3, 10, sm.height() - 6), 2, 2)
            p.setPen(dim)
            p.drawText(QPointF(x + 14, y + sm.ascent()), label)
            x += 14 + sm.horizontalAdvance(label) + 14
        y += sm.height() + 6

        # Treemap of the current node: its children with a header strip, and theirs inside
        area = QRectF(8, y, self.width() - 16, self.height() - y - 8)
        self._boxes = []
        node = self.path[-1] if self.path else None
        if node is None or not node.children or area.height() < 20:
            p.setPen(dim)
            p.drawText(area, Qt.AlignmentFlag.AlignTop, "Nothing uses this region")
            return
        border = QPen(pal.color(QPalette.ColorRole.Base))
        border.setWidthF(1.5)
        dark = QColor("#11111b")
        header = sm.height() + 2
        top = node.children
        for k, bx, by, bw, bh in graph.squarify([(k, c.size) for k, c in enumerate(top)],
                                                area.x(), area.y(), area.width(), area.height()):
            child = top[k]
            rect = QRectF(bx, by, bw, bh)
            self._boxes.append((child, rect, child))
            color = self._color(child, node)
            p.setPen(border)
            p.setBrush(color)
            p.drawRect(rect)
            inner = rect.adjusted(2, header, -2, -2)
            if child.children and inner.width() > 24 and inner.height() > 14:
                lighter = color.lighter(118)
                for j, ix, iy, iw, ih in graph.squarify([(j, c.size) for j, c in enumerate(child.children)],
                                                        inner.x(), inner.y(), inner.width(), inner.height()):
                    sub = child.children[j]
                    r2 = QRectF(ix, iy, iw, ih)
                    self._boxes.append((sub, r2, child))
                    c2 = QColor(lighter)
                    if self.hover is not None and self.hover is not sub:
                        c2.setAlpha(200)
                    p.setPen(border)
                    p.setBrush(c2)
                    p.drawRect(r2)
                    if iw > 34 and ih > sm.height() + 2:
                        p.setPen(dark)
                        lines = [sub.name] + ([memmap.human(sub.size)] if ih > 2 * sm.height() + 4 else [])
                        for n, t in enumerate(lines):
                            p.drawText(QPointF(ix + 3, iy + 2 + sm.ascent() + n * sm.height()),
                                       sm.elidedText(t, Qt.TextElideMode.ElideRight, iw - 6))
            if bw > 30 and bh > sm.height():
                p.setPen(dark)
                p.drawText(QPointF(bx + 4, by + 1 + sm.ascent()),
                           sm.elidedText(f"{child.name}  {memmap.human(child.size)}", Qt.TextElideMode.ElideRight, bw - 8))
        for n, rect, _ in self._boxes:
            if n is self.hover:
                p.setPen(QPen(text, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect.adjusted(1, 1, -1, -1))

    def _color(self, child: memmap.Node, parent: memmap.Node) -> QColor:
        kind = child.kind or parent.kind or "other"
        return QColor(graphview.COLORS[KIND_COLORS.get(kind, "program")])

    # Mouse

    def _box_at(self, pos: QPointF) -> tuple[memmap.Node, memmap.Node] | None:
        for node, rect, top in reversed(self._boxes):  # inner boxes were added after their parent
            if rect.contains(pos):
                return node, top
        return None

    def mouseMoveEvent(self, event):
        pos = event.position()
        hit = self._box_at(pos)
        node = hit[0] if hit else None
        if node is not self.hover:
            self.hover = node
            self.update()
        if node and self.map:
            region = self.map.regions[self.region]
            share = f"{node.size / region.used * 100:.1f}% of what {region.name} uses" if region.used else ""
            of = f", {node.size / region.length * 100:.2f}% of {region.name}" if region.length else ""
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"<b>{node.name}</b><br>{node.size:,} bytes ({memmap.human(node.size)})<br>{share}{of}", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def mousePressEvent(self, event):
        if not self.map:
            return
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton or (
                event.button() == Qt.MouseButton.LeftButton and self._crumb.contains(pos)):
            self.back()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        for i, rect in self._bars:
            if rect.contains(pos):
                self.region = i
                self.path = [self.map.regions[i].tree]
                self.hover = None
                self.update()
                return
        if hit := self._box_at(pos):
            node, top = hit
            target = top if top.children else None
            if target is not None:
                self.path.append(target)
                if node is not top and node.children:
                    self.path.append(node)
                self.hover = None
                self.update()

    def back(self) -> None:
        if len(self.path) > 1:
            self.path.pop()
            self.hover = None
            self.update()

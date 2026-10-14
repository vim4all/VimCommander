"""The graph widget: draws a graph.Graph, Obsidian style.

Wheel zooms around the mouse, dragging pans, hovering highlights a node and its
neighbours, clicking opens the node in the active pane, double-clicking opens a
note or file group with its app. The toolbar sets the depth, what node size
means, and the mode: tree, sunburst, treemap, timeline (when each subfolder's
files changed), types (what each subfolder is made of), notes or code.
Scanning runs in a thread. The sunburst, treemap, timeline and types always
fit the view; they keep their hit areas in self._rects / self._arcs.
"""

import math
import os
import threading
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QToolButton, QToolTip,
                               QVBoxLayout, QWidget)

from . import graph as g


def human_size(n: int) -> str:
    from .pane import human_size as size  # pane imports this module (via preview)
    return size(n)

COLORS = {  # node colors by kind of content
    "root": "#a882ff", "folder": "#7aa2f7", "image": "#e0af68", "video": "#f7768e",
    "audio": "#bb9af7", "code": "#9ece6a", "document": "#7dcfff", "archive": "#ff9e64",
    "program": "#c0caf5", "build": "#b4a48c", "design": "#2ac3de", "other": "#8a8fa3",
    # code mode: by language
    "python": "#4b8bbe", "js": "#e8d44d", "ts": "#3178c6", "c": "#a8b9cc", "cpp": "#f34b7d",
    "go": "#00add8", "rust": "#dea584",
}
FORCE_MODES = ("notes", "code")  # laid out by the force simulation
SUMMARY_MODES = ("timeline", "types")  # one row per subfolder, from graph.scan_folders()
FIXED_MODES = ("sunburst", "treemap") + SUMMARY_MODES  # fit the view: no zoom or panning
MODES = ("tree", "sunburst", "treemap", "timeline", "types", "notes", "code")
NEXT_MODE = {m: MODES[(i + 1) % len(MODES)] for i, m in enumerate(MODES)}
TYPE_ORDER = ("code", "design", "document", "image", "video", "audio", "archive", "program", "build",
              "other")

# Shared by every graph view; set from the config's "graph" section.
OPTIONS = {"enabled": True, "depth": 2, "size_by": "size", "mode": "tree", "max_nodes": 400}


class _Scanner(QObject):
    """Builds a graph in a thread: first the structure, then (tree mode) sizes."""

    ready = Signal(int, object)  # token, Graph
    updated = Signal(int, object)  # token, Graph with more sizes filled in

    def __init__(self):
        super().__init__()
        self.stop = threading.Event()

    def run(self, token: int, path: str, options: dict, show_hidden: bool, known: dict) -> None:
        if options["mode"] in FORCE_MODES:
            if options["mode"] == "notes":
                graph = g.scan_notes(path, show_hidden, self.stop, min(options["max_nodes"], 300))
            else:
                graph = g.scan_code(path, show_hidden, self.stop, min(max(options["max_nodes"], 300), 800))
            if not self.stop.is_set():
                self.ready.emit(token, graph)
            return
        if options["mode"] in SUMMARY_MODES:
            graph = g.scan_folders(path, show_hidden, self.stop, budget=8.0,
                                   max_folders=options["max_nodes"])
            graph.mode = options["mode"]
            if not self.stop.is_set():
                self.ready.emit(token, graph)
            return
        graph = g.scan_tree(path, options["depth"], show_hidden, options["size_by"], self.stop,
                            options["max_nodes"])
        if options["mode"] in FIXED_MODES:
            graph.mode = options["mode"]
        else:
            g.layout_radial(graph)
        if self.stop.is_set():
            return
        self.ready.emit(token, graph)
        if not graph.nodes[0].complete:
            g.fill_sizes(graph, self.stop, budget=6.0, known=known)
            if not self.stop.is_set():
                self.updated.emit(token, graph)


class GraphView(QWidget):
    open_requested = Signal(object, bool)  # Node, activate (double-click)
    options_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(120, 120)
        self.graph: g.Graph | None = None
        self.path = ""
        self.show_hidden = False
        self.known_sizes: dict = {}
        self.scale, self.offset = 1.0, QPointF(0, 0)
        self.hover = -1
        self.neighbours: dict[int, set[int]] = {}
        self._drag_from: QPointF | None = None
        self._dragged = False
        self._token = 0
        self._scanner: _Scanner | None = None
        self._grow = 1.0  # 0 → 1 while a new tree unfolds from the middle
        self._force: g.ForceLayout | None = None
        self._steps = 0
        self._arcs: list[tuple] = []  # sunburst slices, for hit-testing
        self._rects: list[tuple[int, QRectF]] = []  # treemap boxes and timeline / types rows
        self._timer = QTimer(self, interval=16)
        self._timer.timeout.connect(self._animate)
        self._debounce = QTimer(self, singleShot=True, interval=150)
        self._debounce.timeout.connect(self._scan)
        self.status = ""

        # Toolbar
        self.depth_buttons = QButtonGroup(self)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 0)
        bar.setSpacing(2)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: bold;")
        bar.addWidget(self.title, 1)
        for d in (1, 2, 3):
            b = self._button(str(d), f"Show {d} level(s) below the folder")
            b.setCheckable(True)
            self.depth_buttons.addButton(b, d)
            bar.addWidget(b)
        self.depth_buttons.idClicked.connect(lambda d: self.set_option("depth", d))
        self.size_button = self._button("", "Node size: total size or number of files")
        self.size_button.clicked.connect(lambda: self.set_option(
            "size_by", "count" if OPTIONS["size_by"] == "size" else "size"))
        self.mode_button = self._button("", "Tree of folders, sunburst or treemap of disk usage, "
                                             "timeline of changes, file types per folder, Markdown "
                                             "notes and their links, or source files and their imports")
        self.mode_button.clicked.connect(lambda: self.set_option(
            "mode", NEXT_MODE.get(OPTIONS["mode"], "tree")))
        fit = self.fit_button = self._button("Fit", "Zoom to fit")
        fit.clicked.connect(self.fit)
        for w in (self.size_button, self.mode_button, fit):
            bar.addWidget(w)
        # Every mode shows the same buttons (unused ones disabled) at a fixed
        # width, so a button never moves when the mode or its text changes.
        self._fix_width(self.size_button, ("Size", "Files"))
        self._fix_width(self.mode_button, [m.capitalize() for m in NEXT_MODE])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addStretch(1)
        self._sync_buttons()

    def _button(self, text: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    def _fix_width(self, button: QToolButton, texts) -> None:
        width = 0
        for text in texts:
            button.setText(text)
            width = max(width, button.sizeHint().width())
        button.setFixedWidth(width)

    def _sync_buttons(self) -> None:
        button = self.depth_buttons.button(OPTIONS["depth"])
        if button:
            button.setChecked(True)
        mode = OPTIONS["mode"]
        for b in self.depth_buttons.buttons():
            b.setEnabled(mode in ("tree", "sunburst", "treemap"))
        self.size_button.setText("Size" if OPTIONS["size_by"] == "size" else "Files")
        self.size_button.setEnabled(mode in ("tree", "sunburst", "treemap", "types"))
        self.mode_button.setText(mode.capitalize())
        self.fit_button.setEnabled(mode not in FIXED_MODES)

    def set_option(self, key: str, value) -> None:
        OPTIONS[key] = value
        self._sync_buttons()
        self.options_changed.emit()
        self.reload()

    # Loading

    def show_folder(self, path: str, show_hidden: bool = False, known_sizes: dict | None = None) -> None:
        if path == self.path and show_hidden == self.show_hidden and self.graph is not None:
            return
        self.path, self.show_hidden = path, show_hidden
        self.known_sizes = dict(known_sizes or {})
        self.title.setText(os.path.basename(path.rstrip("/\\")) or path)
        self.reload()

    def reload(self) -> None:
        self._sync_buttons()
        if self._scanner:
            self._scanner.stop.set()
        self.status = "Scanning…"
        self.update()
        self._debounce.start()  # wait until the cursor stops moving

    def _scan(self) -> None:
        if not self.path or not self.isVisible():
            return
        self._token += 1
        scanner = self._scanner = _Scanner()
        scanner.ready.connect(self._ready)
        scanner.updated.connect(self._updated)
        threading.Thread(target=scanner.run, daemon=True, args=(
            self._token, self.path, dict(OPTIONS), self.show_hidden, self.known_sizes)).start()

    def showEvent(self, event):
        super().showEvent(event)
        if self.path and self.graph is None:
            self._debounce.start()

    def stop(self) -> None:
        if self._scanner:
            self._scanner.stop.set()
        self._timer.stop()

    def _ready(self, token: int, graph: g.Graph) -> None:
        if token != self._token:
            return
        self.graph = graph
        self.hover = -1
        self.neighbours = {i: set() for i in range(len(graph.nodes))}
        for a, b in graph.edges:
            self.neighbours[a].add(b)
            self.neighbours[b].add(a)
        self._summary()
        if graph.mode in FORCE_MODES:
            self._force = g.ForceLayout(graph)
            self._steps = 0
            self._grow = 1.0
        elif graph.mode in ("treemap",) + SUMMARY_MODES:
            self._force = None
            self._grow = 1.0
        else:
            self._force = None
            self._grow = 0.0
        self._arcs = []
        self._rects = []
        self.fit()
        self._timer.start()

    def _updated(self, token: int, graph: g.Graph) -> None:
        if token == self._token:
            self._summary()
            self.update()

    def _summary(self) -> None:
        graph = self.graph
        if graph is None:
            return
        if graph.mode == "notes":
            n = len(graph.nodes)
            self.status = (f"{n} notes, {len(graph.edges)} links" if n else
                           "No Markdown notes here. Click Notes to go on to the code graph")
        elif graph.mode == "code":
            n = len(graph.nodes)
            hubs = sorted((x for x in graph.nodes if x.count), key=lambda x: -x.count)[:3]
            self.status = (f"{n} source files, {len(graph.edges)} imports" if n else
                           "No source files here (Python, JS/TS, C/C++, Go, Rust). "
                           "Click Code to go back to the tree")
            if hubs:
                self.status += "; most imported: " + ", ".join(f"{x.name} ({x.count})" for x in hubs)
        elif graph.mode in SUMMARY_MODES:
            root = graph.nodes[0]
            size = human_size(root.size) + ("" if root.complete else "+")
            self.status = f"{len(graph.nodes) - 1} folders, {root.count:,} files, {size}"
            if graph.mode == "timeline":
                if root.first:
                    self.status += (f", changed {time.strftime('%Y-%m', time.localtime(root.first))}"
                                    f" … {time.strftime('%Y-%m', time.localtime(root.last))}")
                self.status += "   (darker = more files changed that month)"
            else:
                self.status += "   (bars by " + ("size)" if OPTIONS["size_by"] == "size" else "number of files)")
        else:
            root = graph.nodes[0]
            folders = sum(1 for x in graph.nodes if x.kind == "folder")
            size = human_size(root.size) + ("" if root.complete else "+")
            self.status = f"{folders} folders shown, {root.count:,} files, {size}"
            if graph.mode in ("sunburst", "treemap"):
                self.status += "   (" + ("slices" if graph.mode == "sunburst" else "boxes") + ": share of the parent by " + (
                    "size)" if OPTIONS["size_by"] == "size" else "number of files)")
        if graph.truncated:
            self.status += "   (some items grouped or left out)"

    def _animate(self) -> None:
        if self._force is not None:
            moved = self._force.step()
            self._steps += 1
            if self._steps in (30, 120):
                self.fit()
            if moved < 0.05 or self._steps > 600:
                self._timer.stop()
                self.fit()
        else:
            self._grow = min(1.0, self._grow + 0.08)
            if self._grow >= 1.0:
                self._timer.stop()
        self.update()

    # View

    def fit(self) -> None:
        if not self.graph or not self.graph.nodes:
            return
        xs = [n.x for n in self.graph.nodes]
        ys = [n.y for n in self.graph.nodes]
        w = max(max(xs) - min(xs), 1) + 160
        h = max(max(ys) - min(ys), 1) + 160
        area = self._area()
        self.scale = max(0.05, min(area.width() / w, area.height() / h, 2.5))
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        self.offset = QPointF(area.center().x() - cx * self.scale, area.center().y() - cy * self.scale)
        self.update()

    def _area(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0, 30, 0, -22)

    def _screen(self, node: g.Node) -> QPointF:
        k = self._grow if self.graph and self.graph.mode == "tree" else 1.0
        k = 1 - (1 - k) ** 3  # ease out
        return QPointF(node.x * k * self.scale + self.offset.x(), node.y * k * self.scale + self.offset.y())

    def _radius(self, node: g.Node) -> float:
        if node.kind == "note":
            r = 3.5 + 2.2 * math.sqrt(node.links)  # like Obsidian: more links, bigger dot
        elif node.kind == "code":
            r = 3.5 + 2.6 * math.sqrt(node.count)  # imported by many: a hub
        elif OPTIONS["size_by"] == "size":
            r = 2.5 + 1.15 * math.log10(1 + node.size)  # 1 MB ≈ 9, 1 GB ≈ 13
        else:
            r = 3 + 2.2 * math.log10(1 + node.count)  # 1000 files ≈ 9.6
        if node.kind == "root":
            r = max(r, 9)
        return r * max(0.6, min(self.scale, 2.0) ** 0.5)

    def _node_at(self, pos: QPointF) -> int:
        if not self.graph:
            return -1
        if self.graph.mode == "sunburst":
            return self._arc_at(pos)
        if self.graph.mode in FIXED_MODES:
            for i, rect in reversed(self._rects):  # the deepest box first
                if rect.contains(pos):
                    return i
            return -1
        best, best_d = -1, 1e9
        for i, n in enumerate(self.graph.nodes):
            p = self._screen(n)
            d = math.hypot(p.x() - pos.x(), p.y() - pos.y())
            if d < max(self._radius(n) + 4, 8) and d < best_d:
                best, best_d = i, d
        return best

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        text = pal.color(QPalette.ColorRole.Text)
        graph = self.graph
        if graph is not None and graph.nodes and graph.mode == "sunburst":
            self._paint_sunburst(p, graph, text)
        elif graph is not None and graph.nodes and graph.mode == "treemap":
            self._paint_treemap(p, graph, text)
        elif graph is not None and graph.nodes and graph.mode == "timeline":
            self._paint_timeline(p, graph, text)
        elif graph is not None and graph.nodes and graph.mode == "types":
            self._paint_types(p, graph, text)
        elif graph is not None and graph.nodes:
            active = self.hover
            near = self.neighbours.get(active, set()) | {active} if active >= 0 else None
            edge = QColor(text)
            edge.setAlpha(55)
            dim_edge = QColor(text)
            dim_edge.setAlpha(18)
            accent = QColor(COLORS["root"])
            arrows = len(graph.edges) <= 300 and self.scale >= 0.5  # code mode: a imports b
            for a, b in graph.edges:
                lit = near is not None and a in near and b in near and active in (a, b)
                pen = QPen(accent if lit else (dim_edge if near is not None else edge))
                pen.setWidthF(1.6 if lit else 1.0)
                p.setPen(pen)
                start, end = self._screen(graph.nodes[a]), self._screen(graph.nodes[b])
                p.drawLine(start, end)
                if graph.mode == "code" and (lit or (near is None and arrows)):
                    self._arrow(p, start, end, self._radius(graph.nodes[b]), pen.color())
            font = QFont(self.font())
            font.setPointSizeF(max(7.0, font.pointSizeF() * 0.9))
            p.setFont(font)
            metrics = QFontMetricsF(font)
            show_all_labels = self.scale >= 0.9 or len(graph.nodes) <= 40
            for i, n in enumerate(graph.nodes):
                pos = self._screen(n)
                r = self._radius(n)
                color = QColor(COLORS.get("root" if n.kind == "root" else n.category, COLORS["other"]))
                if near is not None and i not in near:
                    color.setAlpha(60)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                if n.kind == "files":  # file groups: rounded squares, folders: circles
                    p.drawRoundedRect(QRectF(pos.x() - r, pos.y() - r, 2 * r, 2 * r), r * 0.35, r * 0.35)
                elif n.kind == "more":
                    p.drawEllipse(pos, r * 0.7, r * 0.7)
                else:
                    p.drawEllipse(pos, r, r)
                if i == active:
                    ring = QPen(text)
                    ring.setWidthF(1.5)
                    p.setPen(ring)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(pos, r + 3, r + 3)
            # Labels after all nodes, biggest node first; a label that would overlap
            # one already drawn is skipped, except for the hovered node and its neighbours.
            drawn: list[QRectF] = []
            order = sorted(range(len(graph.nodes)), key=lambda i: (
                i != active, not (near and i in near), graph.nodes[i].kind != "root",
                -self._radius(graph.nodes[i])))
            for i in order:
                n = graph.nodes[i]
                must = i == active or (near is not None and i in near) or n.kind == "root"
                if not must and (near is not None or not (show_all_labels or n.depth <= 1)):
                    continue
                pos, r = self._screen(n), self._radius(n)
                label = n.name if len(n.name) <= 28 else n.name[:26] + "…"
                w = metrics.horizontalAdvance(label)
                box = QRectF(pos.x() - w / 2, pos.y() + r + 2, w, metrics.height())
                if not must and any(box.intersects(d) for d in drawn):
                    continue
                drawn.append(box.adjusted(-3, -1, 3, 1))
                p.setPen(text)
                p.drawText(QPointF(box.left(), box.top() + metrics.ascent()), label)
        p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
        p.drawText(QRectF(self.rect()).adjusted(8, 0, -8, -4),
                   Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                   self.status + ("   click = open" if self.graph and self.graph.mode in FIXED_MODES
                                  else "   wheel = zoom, drag = move, click = open"))
        p.end()

    @staticmethod
    def _arrow(p: QPainter, start: QPointF, end: QPointF, radius: float, color: QColor) -> None:
        """A small arrowhead where the line from `start` meets the circle around `end`."""
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy)
        if length < radius + 8:
            return
        ux, uy = dx / length, dy / length
        tip = QPointF(end.x() - ux * (radius + 1), end.y() - uy * (radius + 1))
        size = 6.0
        back = QPointF(tip.x() - ux * size, tip.y() - uy * size)
        path = QPainterPath(tip)
        path.lineTo(back.x() - uy * size * 0.5, back.y() + ux * size * 0.5)
        path.lineTo(back.x() + uy * size * 0.5, back.y() - ux * size * 0.5)
        path.closeSubpath()
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)
        p.restore()

    # Sunburst: rings by depth, each slice as wide as its share of the parent

    def _sunburst_geometry(self) -> tuple[QPointF, float, float]:
        area = self._area()
        outer = max(40.0, min(area.width(), area.height()) / 2 - 8)
        inner = outer * 0.17
        depth = max((n.depth for n in self.graph.nodes), default=1) or 1
        return area.center(), inner, (outer - inner) / depth

    def _layout_arcs(self) -> None:
        """self._arcs: (node, inner radius, outer radius, start, end) with angles in
        degrees clockwise from 12 o'clock."""
        nodes = self.graph.nodes
        _, inner, ring = self._sunburst_geometry()
        by_size = OPTIONS["size_by"] == "size"
        arcs = []

        def place(i: int, a0: float, a1: float) -> None:
            kids = nodes[i].children
            weights = [max(nodes[k].size if by_size else nodes[k].count, 0) for k in kids]
            total = sum(weights)
            if total <= 0:
                weights, total = [1] * len(kids), len(kids)
            a = a0
            for k, w in zip(kids, weights):
                span = (a1 - a0) * w / total
                r0 = inner + (nodes[k].depth - 1) * ring
                arcs.append((k, r0, r0 + ring - 1.5, a, a + span))
                place(k, a, a + span)
                a += span

        ease = 1 - (1 - self._grow) ** 3
        place(0, 0.0, 360.0 * ease)
        self._arcs = arcs

    def _arc_at(self, pos: QPointF) -> int:
        center, inner, _ = self._sunburst_geometry()
        dx, dy = pos.x() - center.x(), pos.y() - center.y()
        r = math.hypot(dx, dy)
        if r < inner:
            return 0
        angle = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
        for i, r0, r1, a0, a1 in self._arcs:
            if r0 <= r < r1 and a0 <= angle < a1:
                return i
        return -1

    def _slice_color(self, node: g.Node, mid_angle: float) -> QColor:
        if node.kind == "folder":  # folders: hue by angle, lighter further out
            return QColor.fromHsvF(mid_angle / 360, max(0.25, 0.65 - 0.12 * node.depth), 0.85)
        if node.kind == "more":
            return QColor(COLORS["other"])
        return QColor(COLORS.get(node.category, COLORS["other"]))

    def _paint_sunburst(self, p: QPainter, graph: g.Graph, text: QColor) -> None:
        self._layout_arcs()
        center, inner, ring = self._sunburst_geometry()
        border = QPen(self.palette().color(QPalette.ColorRole.Base))
        border.setWidthF(1.0)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        p.setFont(font)
        metrics = QFontMetricsF(font)
        for i, r0, r1, a0, a1 in self._arcs:
            if a1 - a0 < 0.25:
                continue  # too thin to see
            node = graph.nodes[i]
            color = self._slice_color(node, (a0 + a1) / 2)
            if self.hover >= 0 and i != self.hover:
                color.setAlpha(150)
            outer_rect = QRectF(center.x() - r1, center.y() - r1, 2 * r1, 2 * r1)
            inner_rect = QRectF(center.x() - r0, center.y() - r0, 2 * r0, 2 * r0)
            path = QPainterPath()
            path.arcMoveTo(outer_rect, 90 - a0)
            path.arcTo(outer_rect, 90 - a0, -(a1 - a0))
            path.arcTo(inner_rect, 90 - a1, a1 - a0)
            path.closeSubpath()
            p.setPen(border)
            p.setBrush(color)
            p.drawPath(path)
            if i == self.hover:
                outline = QPen(text)
                outline.setWidthF(2)
                p.setPen(outline)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
            # A label where it fits along the middle of the slice
            mid_r, mid_a = (r0 + r1) / 2, math.radians((a0 + a1) / 2)
            room = math.radians(a1 - a0) * mid_r
            label = node.name if len(node.name) <= 22 else node.name[:20] + "…"
            if room > metrics.horizontalAdvance(label) + 6 and r1 - r0 > metrics.height():
                x = center.x() + mid_r * math.sin(mid_a)
                y = center.y() - mid_r * math.cos(mid_a)
                p.setPen(QColor("#111111"))
                w = metrics.horizontalAdvance(label)
                p.drawText(QPointF(x - w / 2, y + metrics.ascent() / 2 - 1), label)
        # The middle: the folder itself
        root = graph.nodes[0]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(COLORS["root"]) if self.hover != 0 else QColor(COLORS["root"]).lighter(115))
        p.drawEllipse(center, inner - 2, inner - 2)
        p.setPen(QColor("#ffffff"))
        size = human_size(root.size) + ("" if root.complete else "+")
        for line, dy in ((root.name if len(root.name) <= 16 else root.name[:15] + "…", -metrics.height() / 2),
                         (size if OPTIONS["size_by"] == "size" else f"{root.count:,} files",
                          metrics.height() / 2)):
            w = metrics.horizontalAdvance(line)
            p.drawText(QPointF(center.x() - w / 2, center.y() + dy + metrics.ascent() / 2 - 2), line)

    # Treemap: boxes as big as their size, nested like the folders

    def _hue(self, graph: g.Graph, i: int) -> float:
        """Folders under the same top-level folder share a hue."""
        nodes = graph.nodes
        while nodes[i].depth > 1 and nodes[i].parent > 0:
            i = nodes[i].parent
        rank = nodes[0].children.index(i) if i in nodes[0].children else 0
        return (rank * 0.618034) % 1.0

    def _box_color(self, graph: g.Graph, i: int) -> QColor:
        node = graph.nodes[i]
        if node.kind == "folder":
            return QColor.fromHsvF(self._hue(graph, i), max(0.18, 0.5 - 0.1 * (node.depth - 1)),
                                   min(0.95, 0.78 + 0.05 * node.depth))
        if node.kind == "more":
            return QColor(COLORS["other"])
        return QColor(COLORS.get(node.category, COLORS["other"]))

    def _paint_treemap(self, p: QPainter, graph: g.Graph, text: QColor) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        p.setFont(font)
        metrics = QFontMetricsF(font)
        area = self._area().adjusted(6, 0, -6, 0)
        boxes = g.layout_treemap(graph, area.x(), area.y(), area.width(), area.height(),
                                 OPTIONS["size_by"] == "size", header=metrics.height() + 2)
        self._rects = [(i, QRectF(x, y, w, h)) for i, x, y, w, h in boxes]
        border = QPen(self.palette().color(QPalette.ColorRole.Base))
        border.setWidthF(1.0)
        dark = QColor("#111111")
        for i, rect in self._rects:
            if rect.width() < 1 or rect.height() < 1:
                continue
            node = graph.nodes[i]
            color = self._box_color(graph, i)
            if self.hover >= 0 and i != self.hover and node.kind != "folder":
                color.setAlpha(170)
            p.setPen(border)
            p.setBrush(color)
            p.drawRect(rect)
            if node.kind == "folder" and node.children:  # name in the header strip
                line = f"{node.name}  {human_size(node.size) if OPTIONS['size_by'] == 'size' else node.count}"
                room = rect.width() - 6
                if room > 24 and rect.height() > metrics.height():
                    p.setPen(dark)
                    p.drawText(QPointF(rect.left() + 3, rect.top() + 1 + metrics.ascent()),
                               metrics.elidedText(line, Qt.TextElideMode.ElideRight, room))
            elif rect.width() > 30 and rect.height() > metrics.height() + 2:
                p.setPen(dark)
                lines = [node.name]
                if rect.height() > 2 * metrics.height() + 4:
                    lines.append(human_size(node.size) if OPTIONS["size_by"] == "size" else f"{node.count:,}")
                for k, line in enumerate(lines):
                    p.drawText(QPointF(rect.left() + 3, rect.top() + 2 + metrics.ascent() + k * metrics.height()),
                               metrics.elidedText(line, Qt.TextElideMode.ElideRight, rect.width() - 6))
        for i, rect in self._rects:
            if i == self.hover:
                outline = QPen(text)
                outline.setWidthF(2)
                p.setPen(outline)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect.adjusted(1, 1, -1, -1))

    # Timeline and types: one row per subfolder

    def _row_layout(self, graph: g.Graph, order: list[int], metrics: QFontMetricsF, top_extra: float,
                    bottom_extra: float) -> tuple[list[tuple[int, QRectF]], float, int]:
        """Rows for `order` (root first) that fit: ([(index, row rect)], label width, hidden rows)."""
        area = self._area().adjusted(6, top_extra, -6, -bottom_extra)
        height = max(metrics.height() + 4, min(22.0, area.height() / max(len(order), 1)))
        fit = max(1, int(area.height() // height))
        hidden = max(0, len(order) - fit)
        if hidden:
            fit -= 1  # room for the "N more" line
            hidden += 1
        shown = order[:fit]
        widest = max((metrics.horizontalAdvance(graph.nodes[i].name) for i in shown), default=40)
        label = min(area.width() * 0.32, widest + 12)
        rows = [(i, QRectF(area.left(), area.top() + k * height, area.width(), height))
                for k, i in enumerate(shown)]
        return rows, label, hidden

    def _row_label(self, p: QPainter, graph: g.Graph, i: int, row: QRectF, label: float,
                   metrics: QFontMetricsF, text: QColor) -> None:
        if i == self.hover:
            hl = QColor(text)
            hl.setAlpha(28)
            p.fillRect(row, hl)
        node = graph.nodes[i]
        font = p.font()
        if i == 0:
            bold = QFont(font)
            bold.setBold(True)
            p.setFont(bold)
        p.setPen(text)
        p.drawText(QRectF(row.left() + 2, row.top(), label - 8, row.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   metrics.elidedText("All" if i == 0 else node.name, Qt.TextElideMode.ElideRight, label - 8))
        p.setFont(font)

    def _paint_more(self, p: QPainter, rows, hidden: int, metrics: QFontMetricsF) -> None:
        if hidden and rows:
            last = rows[-1][1]
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(QPointF(last.left() + 2, last.bottom() + metrics.ascent() + 2),
                       f"+{hidden} more folders (a bigger pane shows more)")

    def _paint_timeline(self, p: QPainter, graph: g.Graph, text: QColor) -> None:
        nodes = graph.nodes
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        p.setFont(font)
        metrics = QFontMetricsF(font)
        kids = sorted(nodes[0].children, key=lambda k: (-nodes[k].last, nodes[k].name.lower()))
        rows, label, hidden = self._row_layout(graph, [0] + kids, metrics, metrics.height() + 6, 0)
        self._rects = rows
        dated = [nodes[i] for i, _ in rows if nodes[i].first]
        if not rows or not dated:
            p.setPen(text)
            p.drawText(self._area(), Qt.AlignmentFlag.AlignCenter, "No files with dates here")
            return
        # From the oldest change to the newest (not to today: old trees would be mostly empty)
        m0 = g._month(min(n.first for n in dated))
        m1 = g._month(max(n.last for n in dated))
        span = max(m1 - m0 + 1, 12)
        m0 = m1 - span + 1
        left = rows[0][1].left() + label
        width = rows[0][1].right() - left - 4

        def x(month: float) -> float:
            return left + (month - m0) / span * width

        # Year lines and labels
        years = span / 12
        step = 1 if years <= 12 else 2 if years <= 24 else 5
        faint = QColor(text)
        faint.setAlpha(30)
        axis_y = rows[0][1].top() - 4
        p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        first_year = (m0 + 11) // 12
        for year in range(first_year, m1 // 12 + 1):
            if year % step:
                continue
            xx = x(year * 12)
            p.setPen(faint)
            p.drawLine(QPointF(xx, axis_y + 2), QPointF(xx, rows[-1][1].bottom()))
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(QPointF(xx + 2, axis_y), str(year))
        # Darkness by changes per month; the All row has its own scale
        busiest = max((c for i, _ in rows if i for c in nodes[i].months.values()), default=1)
        busiest_all = max(nodes[0].months.values(), default=1)
        base = QColor(COLORS["folder"])
        for i, row in rows:
            node = nodes[i]
            self._row_label(p, graph, i, row, label, metrics, text)
            if not node.first:
                continue
            color = QColor(COLORS["root"]) if i == 0 else base
            band = QRectF(x(g._month(node.first)), row.top() + row.height() * 0.3,
                          max(2.0, x(g._month(node.last) + 1) - x(g._month(node.first))), row.height() * 0.4)
            span_color = QColor(color)
            span_color.setAlpha(45)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(span_color)
            p.drawRect(band)
            scale = math.log1p(busiest_all if i == 0 else busiest)
            for month, count in node.months.items():
                if month < m0:
                    continue
                cell = QColor(color)
                cell.setAlpha(int(110 + 145 * math.log1p(count) / scale) if scale else 255)
                x0 = x(month)
                p.fillRect(QRectF(x0, row.top() + 3, max(1.5, x(month + 1) - x0), row.height() - 6), cell)
        self._paint_more(p, rows, hidden, metrics)

    def _paint_types(self, p: QPainter, graph: g.Graph, text: QColor) -> None:
        nodes = graph.nodes
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        p.setFont(font)
        metrics = QFontMetricsF(font)
        k = 0 if OPTIONS["size_by"] == "size" else 1  # index in mix values: bytes or files

        def amount(n: g.Node) -> int:
            return n.size if k == 0 else n.count

        kids = sorted(nodes[0].children, key=lambda c: -amount(nodes[c]))
        legend_h = metrics.height() + 8
        rows, label, hidden = self._row_layout(graph, [0] + kids, metrics, 4, legend_h)
        self._rects = rows
        if not rows:
            return
        value_w = metrics.horizontalAdvance("999.9 MB ") + 4
        biggest = max((amount(nodes[i]) for i, _ in rows[1:]), default=0) or 1
        dark = QColor("#111111")
        for i, row in rows:
            node = nodes[i]
            self._row_label(p, graph, i, row, label, metrics, text)
            left = row.left() + label
            full = row.right() - left - value_w
            length = full if i == 0 else full * amount(node) / biggest
            total = sum(v[k] for v in node.mix.values()) or 1
            xx = left
            bar = QRectF(left, row.top() + 3, length, row.height() - 6)
            for cat in TYPE_ORDER:
                v = node.mix.get(cat)
                if not v or not v[k]:
                    continue
                w = length * v[k] / total
                seg = QRectF(xx, bar.top(), w, bar.height())
                p.fillRect(seg, QColor(COLORS.get(cat, COLORS["other"])))
                pct = f"{100 * v[k] / total:.0f}%"
                if w > metrics.horizontalAdvance(pct) + 6 and bar.height() >= metrics.height() - 2:
                    p.setPen(dark)
                    p.drawText(seg, Qt.AlignmentFlag.AlignCenter, pct)
                xx += w
            p.setPen(text)
            value = (human_size(node.size) + ("" if node.complete else "+")) if k == 0 else f"{node.count:,}"
            p.drawText(QRectF(left + length + 4, row.top(), value_w, row.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, value)
        self._paint_more(p, rows, hidden, metrics)
        # Legend: the kinds that occur
        present = [c for c in TYPE_ORDER if nodes[0].mix.get(c, [0, 0])[k]]
        lx = rows[0][1].left() + 2
        ly = self._area().bottom() - legend_h + 4
        for cat in present:
            w = metrics.horizontalAdvance(cat)
            if lx + w + 16 > self.width():
                break
            p.fillRect(QRectF(lx, ly + 2, 9, 9), QColor(COLORS.get(cat, COLORS["other"])))
            p.setPen(text)
            p.drawText(QPointF(lx + 12, ly + metrics.ascent()), cat)
            lx += w + 24

    # Mouse

    def wheelEvent(self, event):
        if self.graph and self.graph.mode in FIXED_MODES:
            return  # these always fit the view
        factor = 1.15 ** (event.angleDelta().y() / 120)
        pos = event.position()
        new = max(0.05, min(self.scale * factor, 8.0))
        factor = new / self.scale
        self.offset = QPointF(pos.x() - (pos.x() - self.offset.x()) * factor,
                              pos.y() - (pos.y() - self.offset.y()) * factor)
        self.scale = new
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.position()
            self._dragged = False

    def mouseMoveEvent(self, event):
        pos = event.position()
        if (self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton
                and not (self.graph and self.graph.mode in FIXED_MODES)):
            delta = pos - self._drag_from
            if self._dragged or abs(delta.x()) + abs(delta.y()) > 3:
                self._dragged = True
                self.offset += delta
                self._drag_from = pos
                self.update()
            return
        hover = self._node_at(pos)
        if hover != self.hover:
            self.hover = hover
            self.update()
            if hover >= 0 and self.graph:
                n = self.graph.nodes[hover]
                QToolTip.showText(event.globalPosition().toPoint(), self._tip(n), self)
            else:
                QToolTip.hideText()

    def _tip(self, n: g.Node) -> str:
        if n.kind == "note":
            return f"{n.path}\n{n.links} link(s)\nclick = show in pane, double-click = open"
        if n.kind == "code":
            return (f"{n.path}\nimported by {n.count}, imports {n.links - n.count}\n"
                    "click = show in pane, double-click = open")
        size = human_size(n.size) + ("" if n.complete else "+")
        if self.graph and self.graph.mode in SUMMARY_MODES:
            lines = [n.path if n.kind != "files" else n.path + " (files directly inside)",
                     f"{n.count:,} files, {size}"]
            if n.first:
                lines.append("changed " + time.strftime("%Y-%m-%d", time.localtime(n.first)) + " … "
                             + time.strftime("%Y-%m-%d", time.localtime(n.last)))
            total = sum(b for b, _ in n.mix.values()) or 1
            kinds = sorted(n.mix.items(), key=lambda kv: -kv[1][0])[:4]
            if kinds:
                lines.append(", ".join(f"{k} {100 * b / total:.0f}%" for k, (b, _) in kinds))
            lines.append("click = open in pane")
            return "\n".join(lines)
        what = {"files": f"{n.count} {n.ext or 'extensionless'} file(s)", "more": "more items"}.get(
            n.kind, f"{n.count:,} files")
        action = "click = show these files" if n.kind == "files" else "click = open in pane"
        return f"{n.path}\n{what}, {size}\n{action}"

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._dragged:
            node = self._node_at(event.position())
            if node >= 0 and self.graph:
                self.open_requested.emit(self.graph.nodes[node], False)
        self._drag_from = None

    def mouseDoubleClickEvent(self, event):
        node = self._node_at(event.position())
        if node >= 0 and self.graph:
            self.open_requested.emit(self.graph.nodes[node], True)

    def leaveEvent(self, event):
        if self.hover >= 0:
            self.hover = -1
            self.update()

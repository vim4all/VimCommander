"""Quick view of a 3D model (STEP, read by step.py; STL by stl.py): shaded faces
with their edges, drawn with QPainter back to front.

Drag to rotate (turntable: the model's z axis stays up), right- or middle-drag
(or Shift-drag) to pan, the wheel zooms at the mouse, double-click resets the
view. A click picks a point to measure from (snapped to a corner or to the
centre of a circle when one is near); a second click gives the distance. The
mouse shows the name of the part under it. `zv` in the file pane gives this view
the keys (`focus_keys()`): h j k l rotate, H J K L pan, + - zoom, 0-6 views,
w wireframe; Esc, q or Tab give them back (`leave`).

While the view moves only the biggest pieces are drawn, as many as fit in a
frame; the whole model is drawn once it stops. With numpy, the wireframe (w)
is wireframe.py's: every edge projected per frame at once, sub-pixel segments
merged; a model too big to shade while moving shows that wireframe until it
stops. Flat faces and outlines are drawn through a QTransform from polygons
made once in their plane (shapes_at, filled a time slice per frame). Reading
runs in a thread; results carry a token, so a stale one is dropped, and a stop
Event ends a reading nobody waits for.
"""

import gc
import math
import threading
import time
from bisect import bisect_right

from PySide6.QtCore import QByteArray, QDataStream, QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetricsF, QPainter, QPainterPath, QPalette, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget
import shiboken6

from . import step

try:  # numpy projects and culls every edge of a model per frame (wireframe.py)
    from . import wireframe
except ImportError:
    wireframe = None

FRAME_TIME = 0.04  # seconds a moving frame may take
STILL_TIME = 0.8  # seconds the still picture may take; smaller pieces are left out
LIGHT = (0.35, 0.45, 0.82)  # in view coordinates: from the upper right, toward the viewer
HOME = (math.radians(-35), math.radians(-60))  # yaw, pitch
VIEWS = {  # key: (yaw, pitch)
    "1": (0.0, -math.pi / 2), "2": (math.pi, -math.pi / 2),  # front, back
    "3": (-math.pi / 2, -math.pi / 2), "4": (math.pi / 2, -math.pi / 2),  # right, left
    "5": (0.0, 0.0), "6": (0.0, -math.pi),  # top, bottom
}
VIEW_NAMES = {"0": "", "1": "front", "2": "back", "3": "right", "4": "left", "5": "top", "6": "bottom"}
STEP_ANGLE = math.radians(15)  # per h j k l
SNAP = 12  # pixels from a corner or a circle's centre that snap to it
EDGE_PIXELS = 4.0  # edges and outlines smaller than this on screen are left out
FILL_PIXELS = 0.6  # filled pieces smaller than this are left out
THIN_PIXELS = 1.5  # an outline's segments shorter than this on screen are merged (see shapes_at)
THIN_LEVELS = 10  # thinning levels: tolerance doubling from the model's size / 8000
KEYS_HINT = "h j k l rotate · H J K L pan · + - zoom · 0-6 views · w wireframe · click = measure · Esc = back"


class _Loader(QObject):
    done = Signal(int, object, object, str)  # token, step.Model or None, its items, error


def thin(loop: list, tol: float) -> list:
    """`loop` without the points closer than `tol` to the last one kept (the
    first and last always stay): a circle seen small needs few points."""
    if len(loop) <= 4:
        return loop
    last = loop[0]
    out = [last]
    append = out.append
    for q in loop[1:-1]:
        if math.dist(q, last) >= tol:
            append(q)
            last = q
    append(loop[-1])
    return out


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else None


def plane_axes(normal):
    """Two unit vectors spanning the plane with this normal."""
    n = _unit(normal)
    if n is None:
        return None
    helper = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = _unit(_cross(n, helper))
    return e1, _cross(n, e1)


def flat(loops, origin, e1, e2) -> list:
    """The loops as 2D polygons in the plane (origin, e1, e2); drawn through a QTransform."""
    ox, oy, oz = origin
    a, b, c = e1
    d, e, f = e2
    return [QPolygonF([QPointF(a * (x - ox) + b * (y - oy) + c * (z - oz), d * (x - ox) + e * (y - oy) + f * (z - oz))
                       for x, y, z in lp]) for lp in loops]


def planar_runs(loop: list, eps: float) -> list:
    """`loop` split into runs that each lie in one plane (a circle, an arc, a line…),
    as (origin, e1, e2, points); neighbouring runs share their end point."""
    runs = []
    n = len(loop)
    i = 0
    while i < n - 1:
        o = loop[i]
        d1 = None  # the run's first direction
        normal = None
        j = i + 1
        while j < n:
            q = _sub(loop[j], o)
            if d1 is None:
                if _dot(q, q) > eps * eps:
                    d1 = q
            elif normal is None:
                c = _cross(d1, q)
                if math.sqrt(_dot(c, c)) > eps * math.sqrt(_dot(d1, d1)):  # off the line so far
                    normal = _unit(c)
            elif abs(_dot(q, normal)) > eps:
                break
            j += 1
        if normal is None:  # a straight run (or one point): any plane through it
            d = _unit(d1) if d1 else (1.0, 0.0, 0.0)
            normal = _cross(d, (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0))
        axes = plane_axes(normal) or ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        runs.append((o, axes[0], axes[1], loop[i:j]))
        if j >= n:
            break
        i = j - 1
    return runs


def shapes_at(it, level: int, base: float):
    """An item's drawing at a thinning level, made on first use and cached in the
    item (only the few thousand pieces big enough to draw ever need one):
    ([(origin, e1, e2, [QPolygonF…])…], None) to draw through a QTransform — a
    plane face is one shape (its outline and holes), an outline is split into
    planar runs (circles, arcs, lines) — or (None, loops) for a face that isn't flat."""
    cache = it[8]
    got = cache.get(level)
    if got is not None:
        return got
    loops, kind, normal = it[0], it[5], it[3]
    if level:
        loops = [thin(lp, base * 2 ** (level - 1)) for lp in loops]
    eps = base * 0.05
    got = None, loops
    if kind == 0:
        n = _unit(normal) if normal else None
        axes = plane_axes(normal) if n else None
        o = loops[0][0]
        if n and axes and all(abs(_dot(_sub(q, o), n)) <= eps for lp in loops for q in lp):
            got = [(o, axes[0], axes[1], flat(loops, o, *axes))], None
    else:
        got = [(o, e1, e2, flat([run], o, e1, e2))
               for lp in loops for o, e1, e2, run in planar_runs(lp, eps)], None
    cache[level] = got
    return got


def thin_base(model: step.Model) -> float:
    lo, hi = model.bounds or ((0, 0, 0), (1, 1, 1))
    return (math.dist(lo, hi) or 1.0) / 8000


def prepare(model: step.Model) -> list:
    """The pieces as drawing items, biggest first (in the reading thread: a big
    model takes seconds)."""
    items = []
    part_of = model.part_of
    for i, p in enumerate(model.pieces):
        pts = [q for lp in p.loops for q in lp]
        n = len(pts)
        xs, ys, zs = [q[0] for q in pts], [q[1] for q in pts], [q[2] for q in pts]
        c = (sum(xs) / n, sum(ys) / n, sum(zs) / n)
        size = math.dist((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))
        kind = 0 if p.edges else 1 if p.fill else 2  # plane with edges, cell, outline
        part = part_of[i] if i < len(part_of) else 0
        # Cells are small quads, drawn point by point; faces and outlines get a
        # cache of flat, simplified drawings (shapes_at).
        cache = {} if kind != 1 and not model.mesh else None
        items.append((p.loops, c, size, p.normal, p.color, kind, p.marks, part, cache))
    items.sort(key=lambda it: -it[2])
    return items


def points_polygon(points) -> QPolygonF:
    """An (n, 2) numpy array as a QPolygonF, copied in one go into its memory
    (QPainter.drawPoints takes it as is; building QPointFs would take seconds)."""
    import numpy as np
    poly = QPolygonF()
    poly.resize(len(points))
    memory = shiboken6.VoidPtr(poly.data(), len(points) * 16, True)
    np.frombuffer(memory, np.float64).reshape(-1, 2)[:] = points
    return poly


def line_color(rgb, light: bool) -> QColor:
    """An edge in a piece's color: dark on a light background (or over a face), light on a dark one."""
    if light:
        return QColor.fromRgbF(rgb[0] * 0.35, rgb[1] * 0.35, rgb[2] * 0.35)
    return QColor.fromRgbF(0.45 + rgb[0] * 0.5, 0.45 + rgb[1] * 0.5, 0.45 + rgb[2] * 0.5)


class StepView(QWidget):
    summary = Signal(str)  # one line for the quick view's title
    not_a_model = Signal(str)  # path: the reader didn't know it (a Windows .stl trust list), show it as data
    leave = Signal()  # Esc / q / Tab with the keys here: back to the file pane

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # only zv gives it the keys, not a click
        self.setMouseTracking(True)
        self.model: step.Model | None = None
        self.items: list = []  # (loops, centre, size, normal, rgb, kind, marks, part, drawing cache), biggest first
        self.edge_items: list = []  # the items a wireframe draws (no surface cells), biggest first
        self._thin_base = 1.0  # model units of the finest thinned copy (thin_base)
        self.wireframe = None  # wireframe.Wireframe of the model's edges (with numpy)
        self.wire_cutoff = EDGE_PIXELS  # moving frames: pieces smaller (px) leave their edges out
        self._neg_sizes: list[float] = []
        self.message = ""
        self.token = 0
        self._stop = threading.Event()
        self.yaw, self.pitch = HOME
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self.wire = False
        self.points: list[tuple[tuple[float, float, float], str]] = []  # measured: (point, "corner" / …)
        self.hover_label = ""
        self.snap: tuple[tuple[float, float, float], str] | None = None  # where a click would land
        self.drag: tuple[QPointF, str] | None = None  # last mouse position, "rotate" / "pan"
        self._press: QPointF | None = None
        self._dragged = False
        self.moving = False
        self.per_item = 4e-5  # seconds one piece takes to draw, measured
        self._drawn: list = []  # (polygons, item, screen x, y, reach, QTransform or None) in drawing order
        self._settle = QTimer(self, singleShot=True, interval=180)
        self._settle.timeout.connect(self._still)
        self._hover_at: QPointF | None = None
        self._hover = QTimer(self, singleShot=True, interval=100)
        self._hover.timeout.connect(self._update_hover)
        self._loader = _Loader()
        self._loader.done.connect(self._loaded)

    # Loading

    def load(self, path: str, source: str | bytes, reader=None) -> None:
        """Read `source` (the file's path, or its bytes from a zip) in a thread with
        `reader` (step.parse, or stl.parse), which takes bytes and a stop Event and
        raises step.NotAModel for another kind of file."""
        reader = reader or step.parse
        self.stop()
        token, stop = self.token, self._stop
        self.message, self._file = "Reading the model…", path
        self.update()

        def work():
            items = None
            try:
                data = source
                if isinstance(data, str):
                    with open(data, "rb") as f:
                        data = f.read()
                try:
                    result = reader(data, stop)
                except step.NotAModel:
                    result, error = None, ""  # another kind of file
                else:
                    error = "" if result.pieces else "no faces in this file"
                    if result.pieces:
                        drawn = prepare(result)
                        wire = wireframe.Wireframe(drawn, result.mesh) if wireframe else None
                        items = drawn, wire
            except step.Stopped:
                return
            except Exception as e:  # always answer, or the view would say "Reading…" for good
                result, error = None, str(getattr(e, "strerror", None) or e) or type(e).__name__
            self._loader.done.emit(token, result, items, error)

        threading.Thread(target=work, daemon=True).start()

    def stop(self) -> None:
        """Forget the model (and stop reading a file)."""
        self.token += 1
        self._stop.set()
        self._stop = threading.Event()
        self.model, self.items, self.edge_items, self._drawn = None, [], [], []
        self.wireframe, self._neg_sizes = None, []
        self.points, self.snap, self.hover_label = [], None, ""
        if self.hasFocus():
            self.leave.emit()

    def _loaded(self, token: int, result, items, error: str) -> None:
        if token != self.token:
            return
        if result is None and not error:
            self.not_a_model.emit(self._file)
            return
        if error:
            self.message = f"Cannot show this model: {error}"
            self.update()
            return
        self.model, self.message = result, ""
        self.items, self.wireframe = items
        # A big model is millions of small objects: a full garbage collection would
        # walk them all (over a second, at random moments). They hold no cycles.
        gc.freeze()
        self.edge_items = self.items if result.mesh else [it for it in self.items if it[5] != 1]
        self._neg_sizes = [-it[2] for it in self.items]  # rising: bisect counts pieces above a size
        self._thin_base = thin_base(result)
        self.home()
        lo, hi = result.bounds
        dims = " × ".join(f"{hi[k] - lo[k]:.4g}" for k in range(3)) + (f" {result.unit}" if result.unit else "")
        parts = f"{result.parts} parts, " if result.parts > 1 else ""
        more = "   (too big: part of it)" if result.partial else ""
        source = f"   {result.source}" if result.source else ""
        counts = (f"{result.solids} solids, {result.faces} faces" if result.solids
                  else f"{result.faces} triangles")
        self.summary.emit(f"{parts}{counts}   {dims}{source}{more}"
                          "   (drag = rotate, wheel = zoom, click = measure, zv = keys)")

    def home(self) -> None:
        self.yaw, self.pitch = HOME
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self.update()

    def focus_keys(self) -> bool:
        """zv: take the keys (if there is a model to move)."""
        if not self.model or not self.isVisible():
            return False
        self.setFocus()
        self.update()
        return True

    # The camera: view = R · (p - centre); screen = middle + pan + scale · (x, -y)

    def _frame(self):
        """Rows of the view rotation: screen right, screen up, toward the viewer."""
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        # Rx(pitch) · Rz(yaw)
        return ((cy, -sy, 0.0), (cp * sy, cp * cy, -sp), (sp * sy, sp * cy, cp))

    def _scale(self) -> float:
        lo, hi = self.model.bounds
        diagonal = math.dist(lo, hi) or 1.0
        return 0.92 * min(self.width(), self.height()) / diagonal * self.zoom

    def _centre(self):
        lo, hi = self.model.bounds
        return ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)

    def _projector(self):
        """A function giving (screen x, screen y, depth toward the viewer) of a model point."""
        (a0, a1, a2), (b0, b1, b2), (d0, d1, d2) = self._frame()
        s, (cx, cy, cz) = self._scale(), self._centre()
        mx, my = self.width() / 2 + self.pan.x(), self.height() / 2 + self.pan.y()

        def project(p):
            x, y, z = p[0] - cx, p[1] - cy, p[2] - cz
            return (mx + s * (a0 * x + a1 * y + a2 * z), my - s * (b0 * x + b1 * y + b2 * z),
                    d0 * x + d1 * y + d2 * z)
        return project

    def _project(self, p):
        return self._projector()(p)

    # Painting

    def paintEvent(self, event):
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        if not self.model:
            p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.rect().adjusted(10, 10, -10, -10), Qt.AlignmentFlag.AlignTop, self.message)
            return
        if not self.moving:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ra, rb, rd = self._frame()
        (a0, a1, a2), (b0, b1, b2), (d0, d1, d2) = ra, rb, rd
        s = self._scale()
        c = self._centre()
        ox = self.width() / 2 + self.pan.x() - s * (a0 * c[0] + a1 * c[1] + a2 * c[2])
        oy = self.height() / 2 + self.pan.y() + s * (b0 * c[0] + b1 * c[1] + b2 * c[2])
        a0, a1, a2, b0, b1, b2 = s * a0, s * a1, s * a2, -s * b0, -s * b1, -s * b2
        allowed = FRAME_TIME if self.moving else STILL_TIME
        budget = allowed / max(self.per_item, 1e-7)
        w, h = self.width(), self.height()
        # Wireframe-only mode as requested.
        wire = True
        light = pal.color(QPalette.ColorRole.Base).lightnessF() >= 0.5
        # Wireframe: edges and outlines only (a mesh's edges are its triangles).
        source = self.edge_items
        smallest = EDGE_PIXELS
        # The simplified copy of each outline whose merged segments stay under THIN_PIXELS.
        base = self._thin_base
        level = min(THIN_LEVELS, max(0, int(math.floor(math.log2(max(THIN_PIXELS / (base * s), 1e-9)))) + 1))
        # Flat drawings are made in up to half of the frame's time, out of its budget;
        # a still picture repaints until all are made.
        filling = 0.0
        unfinished = False
        chosen = []  # with the numpy wireframe: only for picking (measuring snaps to them)
        for it in source:
            if len(chosen) >= budget:
                break
            size = it[2] * s
            if size < smallest:
                break  # sorted by size: the rest are smaller still
            if it[5] == 2 and size < EDGE_PIXELS:
                continue  # an outline a pixel or two long: lost in its face anyway
            x, y, z = it[1]
            sx, sy = ox + a0 * x + a1 * y + a2 * z, oy + b0 * x + b1 * y + b2 * z
            if sx < -size or sy < -size or sx > w + size or sy > h + size:
                continue
            chosen.append((d0 * x + d1 * y + d2 * z, it, sx, sy, size))
        
        # Always use fast wireframe for performance
        self.wire = True
        started = time.perf_counter()
        self._draw_wireframe(p, (a0, a1, a2), (b0, b1, b2), ox, oy, s, light,
                             self.wire_cutoff if self.moving else EDGE_PIXELS)
        if self.moving:  # keep moving frames near FRAME_TIME: leave out more small pieces, or fewer
            spent = time.perf_counter() - started
            if spent > 1.25 * FRAME_TIME:
                self.wire_cutoff = min(self.wire_cutoff * 1.4, 16.0)
            elif spent < 0.6 * FRAME_TIME:
                self.wire_cutoff = max(self.wire_cutoff / 1.4, EDGE_PIXELS)
        self._drawn = [([], it, sx, sy, reach, None) for _d, it, sx, sy, reach in chosen]  # for picking
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._axes(p, ra, rb)
        self._overlay(p)
        return

    def _draw_wireframe(self, p: QPainter, ax, ay, ox: float, oy: float, s: float, light: bool,
                        cutoff: float) -> None:
        """Every edge of pieces at least `cutoff` pixels big at once (wireframe.py):
        a path and a set of points per color."""
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rgb, lines, points in self.wireframe.frame(ax, ay, ox, oy, self.width(), self.height(),
                                                       cutoff / s):
            p.setPen(QPen(line_color(rgb, light), 0))
            if len(lines):
                path = QPainterPath()
                stream = QDataStream(QByteArray(wireframe.path_bytes(lines)))
                stream.setByteOrder(QDataStream.ByteOrder.LittleEndian)
                stream >> path
                p.drawPath(path)
            if len(points):
                p.drawPoints(points_polygon(points))

    def _axes(self, p: QPainter, ra, rb) -> None:
        """A small x / y / z cross in the bottom left corner."""
        base = QPointF(30, self.height() - 30)
        colors = ("#d04040", "#40a040", "#4070e0")
        for k, (name, color) in enumerate(zip("xyz", colors)):
            end = base + QPointF(ra[k] * 20, -rb[k] * 20)
            p.setPen(QPen(QColor(color), 2))
            p.drawLine(base, end)
            p.drawText(end + QPointF(ra[k] * 6 - 3, -rb[k] * 6 + 4), name)

    def _overlay(self, p: QPainter) -> None:
        """Measured points and their distance, the snap marker, the part name, the key hint."""
        pal = self.palette()
        accent = pal.color(QPalette.ColorRole.Highlight)
        text = pal.color(QPalette.ColorRole.Text)
        metrics = QFontMetricsF(self.font())
        line = metrics.height()
        screen = [QPointF(*self._project(q)[:2]) for q, _how in self.points]
        if len(screen) == 2:
            p.setPen(QPen(accent, 2, Qt.PenStyle.DashLine))
            p.drawLine(screen[0], screen[1])
        for pt, name in zip(screen, "AB"):
            p.setPen(QPen(accent, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(pt, 5, 5)
            p.drawText(pt + QPointF(8, -6), name)
        if self.snap and not self.drag:
            q, how = self.snap
            pt = QPointF(*self._project(q)[:2])
            p.setPen(QPen(accent, 1))
            if how == "centre":
                p.drawLine(pt - QPointF(7, 0), pt + QPointF(7, 0))
                p.drawLine(pt - QPointF(0, 7), pt + QPointF(0, 7))
            elif how == "corner":
                p.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))
            else:
                p.drawEllipse(pt, 3, 3)
        lines = []
        unit = self.model.unit if self.model else ""
        for (q, how), name in zip(self.points, "AB"):
            lines.append(f"{name}  {q[0]:.3f}, {q[1]:.3f}, {q[2]:.3f}" + ("" if how == "surface" else f"  ({how})"))
        if len(self.points) == 2:
            (a, _), (b, _) = self.points
            lines.append(f"distance  {math.dist(a, b):.3f} {unit}   "
                         f"Δx {b[0] - a[0]:.3f}  Δy {b[1] - a[1]:.3f}  Δz {b[2] - a[2]:.3f}")
        if lines:
            width = max(metrics.horizontalAdvance(t) for t in lines) + 12
            box = QRectF(6, 6, width, line * len(lines) + 8)
            back = QColor(pal.color(QPalette.ColorRole.Base))
            back.setAlpha(220)
            p.fillRect(box, back)
            p.setPen(text)
            for i, t in enumerate(lines):
                p.drawText(QPointF(12, 10 + metrics.ascent() + i * line), t)
        bottom = self.height() - 8
        if self.hasFocus():
            p.setPen(QPen(accent, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(1, 1, self.width() - 2, self.height() - 2))
            p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
            p.drawText(QPointF(70, bottom), metrics.elidedText(KEYS_HINT, Qt.TextElideMode.ElideRight,
                                                               self.width() - 80))
            bottom -= line
        if self.hover_label:
            p.setPen(text)
            p.drawText(QPointF(70, bottom), self.hover_label)

    # Picking: the piece under the mouse, the point on it, corners and centres near it

    def _piece_at(self, pos: QPointF):
        """The front-most drawn face under `pos`, or None."""
        fill = Qt.FillRule.OddEvenFill
        if not self._drawn:
            return None
        px, py = pos.x(), pos.y()
        for polys, it, sx, sy, reach, transform in reversed(self._drawn):
            if not polys:
                continue
            if abs(px - sx) > reach or abs(py - sy) > reach or it[5] == 2:
                continue  # every point of a piece is within its size of its centre

            if not polys:
                continue
            if transform is not None:  # a flat face's polygons are in its plane
                polys = [transform.map(poly) for poly in polys]
            if not polys or polys[0].isEmpty():
                continue
            try:
                if not polys[0].boundingRect().contains(pos):
                    continue
                inside = sum(1 for poly in polys if poly.containsPoint(pos, fill)) % 2
                if inside:
                    return it
            except Exception:
                continue
        return None

    def _surface_point(self, it, pos: QPointF):
        """The model point under `pos` on a face's plane."""
        ra, rb, rd = self._frame()
        s, c = self._scale(), self._centre()
        vx = (pos.x() - self.width() / 2 - self.pan.x()) / s
        vy = -(pos.y() - self.height() / 2 - self.pan.y()) / s
        base = tuple(c[k] + ra[k] * vx + rb[k] * vy for k in range(3))
        n, q0 = it[3], it[0][0][0]
        nd = n[0] * rd[0] + n[1] * rd[1] + n[2] * rd[2]
        if abs(nd) < 1e-6:  # seen edge-on
            return q0
        vz = (n[0] * (q0[0] - base[0]) + n[1] * (q0[1] - base[1]) + n[2] * (q0[2] - base[2])) / nd
        return tuple(base[k] + rd[k] * vz for k in range(3))

    def _pick(self, pos: QPointF):
        """(point, how) a click at `pos` measures from: a near circle centre or
        corner in front, else the point on the face; None off the model."""
        hit = self._piece_at(pos)
        surface = self._surface_point(hit, pos) if hit else None
        lo, hi = self.model.bounds
        tolerance = 0.004 * (math.dist(lo, hi) or 1.0)
        project = self._projector()
        depth = project(surface)[2] - tolerance if surface else -math.inf
        best, best_d = None, SNAP
        px, py = pos.x(), pos.y()
        mesh = self.model.mesh
        for _polys, it, sx, sy, reach, _transform in self._drawn:
            if abs(px - sx) > reach + SNAP or abs(py - sy) > reach + SNAP:
                continue
            candidates = [(q, "centre") for q in it[6]]
            if it[5] != 1 or mesh:  # a surface's cells have no real corners, a mesh's do
                candidates += [(q, "corner") for lp in it[0] for q in lp]
            for q, how in candidates:
                x, y, z = project(q)
                if z < depth:
                    continue  # behind the face under the mouse
                d = math.hypot(x - pos.x(), y - pos.y()) * (0.6 if how == "centre" else 1.0)
                if d < best_d:
                    best, best_d = (q, how), d
        if best:
            return best
        return (surface, "surface") if surface else None

    def _update_hover(self) -> None:
        if not self.model or self._hover_at is None or self.drag:
            return
        pos = self._hover_at
        it = self._piece_at(pos)
        label = ""
        if it and len(it) > 7 and self.model.labels and it[7] < len(self.model.labels):
            label = self.model.labels[it[7]]
        snap = self._pick(pos)
        if label != self.hover_label or snap != self.snap:
            self.hover_label, self.snap = label, snap
            self.update()

    # Mouse

    def mousePressEvent(self, event):
        if not self.model:
            return
        pan = (event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton)
               or event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.drag = (event.position(), "pan" if pan else "rotate")
        self._press, self._dragged = event.position(), False

    def mouseMoveEvent(self, event):
        if not self.drag:
            self._hover_at = event.position()
            self._hover.start()
            return
        last, how = self.drag
        if not self._dragged:
            moved = event.position() - self._press
            if abs(moved.x()) + abs(moved.y()) < 4:
                return  # still a click
            self._dragged, self.moving = True, True
        delta = event.position() - last
        self.drag = (event.position(), how)
        if how == "pan":
            self.pan += delta
        else:
            self.yaw += delta.x() * 0.01
            self.pitch = min(0.0, max(-math.pi, self.pitch + delta.y() * 0.01))
        self.update()

    def mouseReleaseEvent(self, event):
        clicked = self.drag and not self._dragged
        self.drag = None
        if clicked and self.model:
            if event.button() == Qt.MouseButton.LeftButton:
                self.measure(event.position())
            elif event.button() == Qt.MouseButton.RightButton:
                self.points = []
                self.update()
        self._settle.start()

    def mouseDoubleClickEvent(self, event):
        if self.model and event.button() == Qt.MouseButton.LeftButton:
            self.points = []
            self.home()

    def leaveEvent(self, event):
        self._hover_at = None
        if self.hover_label or self.snap:
            self.hover_label, self.snap = "", None
            self.update()

    def measure(self, pos: QPointF) -> None:
        """A click: the first point, or the second (then the distance shows)."""
        picked = self._pick(pos)
        if picked is None:
            return
        if len(self.points) >= 2:
            self.points = []
        self.points.append(picked)
        self.update()

    def wheelEvent(self, event):
        if not self.model:
            return
        self._zoom_by(1.0015 ** event.angleDelta().y(), event.position())

    def _zoom_by(self, factor: float, at: QPointF | None = None) -> None:
        new = min(200.0, max(0.05, self.zoom * factor))
        factor = new / self.zoom
        centre = QPointF(self.width() / 2, self.height() / 2)
        offset = (at or centre) - centre
        self.pan = offset - (offset - self.pan) * factor  # keep the point under the mouse where it is
        self.zoom = new
        self._moved()

    def _moved(self) -> None:
        self.moving = True
        self.snap = None
        self._settle.start()
        self.update()

    def _still(self) -> None:
        if self.drag:
            return
        self.moving = False
        self.update()

    # Keys (after zv)

    def event(self, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.leave.emit()  # not Qt's focus chain
            return True
        return super().event(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def keyPressEvent(self, event):
        if not self.model:
            self.leave.emit()
            return
        key, text = event.key(), event.text()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        arrows = {Qt.Key.Key_Left: "h", Qt.Key.Key_Down: "j", Qt.Key.Key_Up: "k", Qt.Key.Key_Right: "l"}
        if key in arrows:
            text = arrows[key].upper() if shift else arrows[key]
        pan = 0.1 * min(self.width(), self.height())
        if key == Qt.Key.Key_Escape:
            if self.points:
                self.points = []
                self.update()
            else:
                self.leave.emit()
        elif text == "q":
            self.leave.emit()
        elif text in ("h", "l"):
            self.yaw += STEP_ANGLE if text == "l" else -STEP_ANGLE
            self._moved()
        elif text in ("j", "k"):
            self.pitch = min(0.0, max(-math.pi, self.pitch + (STEP_ANGLE if text == "j" else -STEP_ANGLE)))
            self._moved()
        elif text in ("H", "J", "K", "L"):
            dx, dy = {"H": (-pan, 0), "L": (pan, 0), "K": (0, -pan), "J": (0, pan)}[text]
            self.pan += QPointF(dx, dy)
            self._moved()
        elif text in ("+", "="):
            self._zoom_by(1.25)
        elif text in ("-", "_"):
            self._zoom_by(0.8)
        elif text == "0":
            self.home()
        elif text in VIEWS:
            self.yaw, self.pitch = VIEWS[text]
            self.pan = QPointF(0, 0)
            self._moved()
        elif text == "w":
            self.wire = not self.wire
            self.update()
        elif text == "c":
            self.points = []
            self.update()

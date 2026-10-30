"""STEP (ISO 10303-21) 3D models for quick view: Altium, FreeCAD, KiCad,
SolidWorks exports (pure Python, no CAD kernel).

`read()` parses the file lazily (entities split by one regex, arguments parsed
when first used), walks the assembly (representation relationships with an
ITEM_DEFINED_TRANSFORMATION, MAPPED_ITEMs) and turns every B-rep face into
drawable pieces:

- plane faces become one polygon with holes;
- cylinders, cones, spheres and tori are cut into a grid in their own (u, v)
  coordinates, keeping the cells inside the face's boundary;
- a B-spline face bounded by its whole patch is drawn as that patch;
- every face's boundary is kept as lines, so other surfaces show as wireframe.

Edges are sampled from lines, circles, ellipses, B-splines and polylines.
Colours come from STYLED_ITEM / OVER_RIDING_STYLED_ITEM chains. A component's
pieces are made once and placed per instance.
"""

import math
import re
from dataclasses import dataclass, field

ANGLE_STEP = math.pi / 12  # per segment of a circle or a curved surface
MAX_PIECES = 400_000  # pieces kept; the rest of a huge assembly is left out
DEFAULT_COLOR = (0.72, 0.74, 0.78)
NAMED_COLORS = {
    "red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1), "yellow": (1, 1, 0),
    "magenta": (1, 0, 1), "cyan": (0, 1, 1), "black": (0.1, 0.1, 0.1), "white": (1, 1, 1),
}

_ENTITY = re.compile(r"#(\d+)\s*=\s*((?:'(?:[^']|'')*'|[^;'])*);")
_TOKEN = re.compile(r"""\s*(?:
    \#(\d+)                                     # 1 reference
  | '((?:[^']|'')*)'                            # 2 string
  | \.([A-Za-z0-9_]+)\.                         # 3 enumeration / boolean
  | ([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?) # 4 number
  | ([A-Za-z_][A-Za-z0-9_]*)\s*\(              # 5 keyword with arguments
  | (\()                                        # 6 list
  | (\))                                        # 7 end
  | ([$*])                                      # 8 unset / derived
  | (,)                                         # 9
)""", re.X)
_HEAD = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")


class Ref(int):
    """A #reference to another entity."""


class Stopped(Exception):
    """The stop Event was set while reading."""


class NotAModel(ValueError):
    """The file is something else with the same extension."""


@dataclass
class Piece:
    """One thing to draw. `loops` are closed polygons (several = with holes),
    or open polylines when `fill` is False."""
    loops: list[list[tuple[float, float, float]]]
    color: tuple[float, float, float]
    normal: tuple[float, float, float] | None = None  # None: an outline
    fill: bool = True
    edges: bool = False  # a filled plane face: its outline is a real edge
    marks: list[tuple[float, float, float]] = field(default_factory=list)  # centres of its circles and arcs


@dataclass
class Model:
    pieces: list[Piece] = field(default_factory=list)
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
    solids: int = 0
    faces: int = 0
    parts: int = 0
    partial: bool = False  # MAX_PIECES was reached
    mesh: bool = False  # triangles of a mesh (STL): their corners are the model's
    labels: list[str] = field(default_factory=lambda: [""])  # part names ("L2 › R1206"); 0 = none
    part_of: list[int] = field(default_factory=list)  # per piece: its index in labels
    unit: str = "mm"
    source: str = ""  # the program that wrote the file


def is_step(path: str) -> bool:
    return path.lower().endswith((".step", ".stp"))


def looks_like_step(head: bytes) -> bool:
    return head.lstrip().startswith(b"ISO-10303-21")


# Parsing


class StepFile:
    def __init__(self, text: str, stop=None):
        start = text.find("DATA;")
        if start < 0:
            raise ValueError("no DATA section")
        self.header = text[:start]
        self.raw: dict[int, str] = {}
        for n, m in enumerate(_ENTITY.finditer(text, start + 5)):
            self.raw[int(m.group(1))] = m.group(2)
            if stop and not n % 20000 and stop.is_set():
                raise Stopped
        self._parsed: dict[int, dict[str, list]] = {}
        self._types: dict[str, list[int]] | None = None

    def kinds(self, ident: int) -> list[str]:
        raw = self.raw.get(ident)
        if raw is None:
            return []
        m = _HEAD.match(raw)
        return [m.group(1).upper()] if m else list(self.get(ident))

    def kind(self, ident: int) -> str:
        k = self.kinds(ident)
        return k[0] if k else ""

    def by_type(self, name: str) -> list[int]:
        if self._types is None:
            self._types = {}
            for ident in self.raw:
                for k in self.kinds(ident):
                    self._types.setdefault(k, []).append(ident)
        return self._types.get(name, [])

    def get(self, ident: int) -> dict[str, list]:
        """{TYPE: arguments}; a complex entity (A() B() …) has several types."""
        found = self._parsed.get(ident)
        if found is None:
            raw = self.raw.get(ident)
            found = _parse_entity(raw) if raw is not None else {}
            self._parsed[ident] = found
        return found

    def args(self, ident: int, kind: str | None = None) -> list:
        e = self.get(ident)
        if kind is None:
            return next(iter(e.values()), [])
        return e.get(kind, [])


def _parse_entity(raw: str) -> dict[str, list]:
    tokens = _TOKEN.findall(raw)
    pos = 0

    def values() -> list:
        nonlocal pos
        out: list = []
        while pos < len(tokens):
            ref, string, enum, number, keyword, lst, end, unset, _comma = tokens[pos]
            pos += 1
            if ref:
                out.append(Ref(int(ref)))
            elif string or (not enum and not number and not keyword and not lst and not end
                            and not unset and not _comma):
                out.append(string.replace("''", "'"))
            elif enum:
                out.append(True if enum in ("T", "Y") else False if enum in ("F", "N") else enum)
            elif number:
                out.append(float(number))
            elif keyword:
                inner = values()  # a typed value: LENGTH_MEASURE(1.0) -> 1.0
                out.append(inner[0] if len(inner) == 1 else inner)
            elif lst:
                out.append(values())
            elif end:
                return out
            elif unset:
                out.append(None)
        return out

    stripped = raw.lstrip()
    if stripped.startswith("("):  # complex: ( A(...) B(...) )
        result: dict[str, list] = {}
        pos = 1
        while pos < len(tokens):
            keyword = tokens[pos][4]
            pos += 1
            if keyword:
                result[keyword.upper()] = values()
        return result
    m = _HEAD.match(raw)
    if not m:
        return {}
    pos = 1
    return {m.group(1).upper(): values()}


# Vectors and placements: a placement is (origin, x, y, z)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-15 else (0.0, 0.0, 1.0)


IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)  # 3x4, rows


def matrix(origin, x, y, z):
    """The matrix taking local coordinates of a placement to its parent's."""
    return (x[0], y[0], z[0], origin[0], x[1], y[1], z[1], origin[1], x[2], y[2], z[2], origin[2])


def mat_mul(a, b):
    out = []
    for r in range(3):
        a0, a1, a2, a3 = a[4 * r:4 * r + 4]
        for c in range(4):
            v = a0 * b[c] + a1 * b[4 + c] + a2 * b[8 + c]
            out.append(v + a3 if c == 3 else v)
    return tuple(out)


def mat_inv(m):
    """Inverse of a rigid placement (rotation + translation)."""
    r = (m[0], m[4], m[8], m[1], m[5], m[9], m[2], m[6], m[10])
    t = (m[3], m[7], m[11])
    return (r[0], r[1], r[2], -(r[0] * t[0] + r[1] * t[1] + r[2] * t[2]),
            r[3], r[4], r[5], -(r[3] * t[0] + r[4] * t[1] + r[5] * t[2]),
            r[6], r[7], r[8], -(r[6] * t[0] + r[7] * t[1] + r[8] * t[2]))


def apply(m, p):
    x, y, z = p
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11])


def rotate(m, v):
    x, y, z = v
    return (m[0] * x + m[1] * y + m[2] * z, m[4] * x + m[5] * y + m[6] * z, m[8] * x + m[9] * y + m[10] * z)


# B-splines


def _knot_vector(mults, knots):
    out = []
    for m, k in zip(mults, knots):
        out.extend([k] * int(m))
    return out


def _basis(degree: int, knots: list[float], t: float) -> tuple[int, list[float]]:
    """The knot span holding t and the degree + 1 nonzero basis values."""
    n = len(knots) - degree - 2  # last control point index
    if t >= knots[n + 1]:
        span = n
    elif t <= knots[degree]:
        span = degree
    else:
        lo, hi = degree, n + 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if t < knots[mid]:
                hi = mid
            else:
                lo = mid
        span = lo
    values = [1.0] + [0.0] * degree
    left = [0.0] * (degree + 1)
    right = [0.0] * (degree + 1)
    for j in range(1, degree + 1):
        left[j] = t - knots[span + 1 - j]
        right[j] = knots[span + j] - t
        saved = 0.0
        for r in range(j):
            den = right[r + 1] + left[j - r]
            temp = values[r] / den if den else 0.0
            values[r] = saved + right[r + 1] * temp
            saved = left[j - r] * temp
        values[j] = saved
    return span, values


class _Curve:
    def __init__(self, degree, points, knots, weights=None):
        self.degree = degree
        self.points = points
        self.knots = knots
        self.weights = weights

    def range(self):
        return self.knots[self.degree], self.knots[-self.degree - 1]

    def at(self, t):
        span, basis = _basis(self.degree, self.knots, t)
        x = y = z = w = 0.0
        for i, b in enumerate(basis):
            k = span - self.degree + i
            if k < 0 or k >= len(self.points):
                continue
            px, py, pz = self.points[k]
            wb = b * (self.weights[k] if self.weights else 1.0)
            x += px * wb
            y += py * wb
            z += pz * wb
            w += wb
        return (x / w, y / w, z / w) if w else self.points[0]


class _Surface:
    def __init__(self, du, dv, grid, uknots, vknots, weights=None):
        self.du, self.dv = du, dv
        self.grid, self.uknots, self.vknots, self.weights = grid, uknots, vknots, weights

    def range(self):
        return (self.uknots[self.du], self.uknots[-self.du - 1],
                self.vknots[self.dv], self.vknots[-self.dv - 1])

    def at(self, u, v):
        su, bu = _basis(self.du, self.uknots, u)
        sv, bv = _basis(self.dv, self.vknots, v)
        x = y = z = w = 0.0
        for i, a in enumerate(bu):
            row = su - self.du + i
            if not 0 <= row < len(self.grid):
                continue
            for j, b in enumerate(bv):
                col = sv - self.dv + j
                if not 0 <= col < len(self.grid[row]):
                    continue
                wt = a * b * (self.weights[row][col] if self.weights else 1.0)
                px, py, pz = self.grid[row][col]
                x += px * wt
                y += py * wt
                z += pz * wt
                w += wt
        return (x / w, y / w, z / w) if w else self.grid[0][0]


# Turning entities into geometry


class _Builder:
    def __init__(self, f: StepFile, stop=None):
        self.f = f
        self.stop = stop
        self.edge_cache: dict[int, list] = {}
        self.edge_centre: dict[int, tuple] = {}  # edges on a circle or ellipse: its centre
        self._centres: list[tuple] = []  # of the face being made
        self._edge_centre = None
        self.item_cache: dict[int, list[Piece]] = {}
        self.colors: dict[int, tuple[float, float, float]] = {}
        self.faces = 0
        self.solids = 0

    # Basics

    def point(self, ident):
        a = self.f.args(ident)
        c = a[1] if len(a) > 1 and isinstance(a[1], list) else [0.0, 0.0, 0.0]
        c = [float(v) for v in c] + [0.0] * (3 - len(c))
        return (c[0], c[1], c[2])

    def direction(self, ident, default=(0.0, 0.0, 1.0)):
        if not ident:
            return default
        a = self.f.args(ident)
        if len(a) > 1 and isinstance(a[1], list):
            c = [float(v) for v in a[1]] + [0.0] * (3 - len(a[1]))
            return _unit((c[0], c[1], c[2]))
        return default

    def placement(self, ident):
        """(origin, x, y, z) of an AXIS2_PLACEMENT_3D (or 2D)."""
        if not ident:
            return (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
        kind = self.f.kind(ident)
        a = self.f.args(ident)
        origin = self.point(a[1]) if len(a) > 1 and a[1] else (0.0, 0.0, 0.0)
        if kind == "AXIS2_PLACEMENT_2D":
            x = self.direction(a[2] if len(a) > 2 else None, (1.0, 0.0, 0.0))
            z = (0.0, 0.0, 1.0)
        else:
            z = self.direction(a[2] if len(a) > 2 else None, (0.0, 0.0, 1.0))
            x = self.direction(a[3] if len(a) > 3 else None, (1.0, 0.0, 0.0))
        x = _sub(x, tuple(_dot(x, z) * c for c in z))
        if _dot(x, x) < 1e-12:
            x = (1.0, 0.0, 0.0) if abs(z[0]) < 0.9 else (0.0, 1.0, 0.0)
            x = _sub(x, tuple(_dot(x, z) * c for c in z))
        x = _unit(x)
        return origin, x, _cross(z, x), z

    def placement_matrix(self, ident):
        kind = self.f.kind(ident)
        if kind == "CARTESIAN_TRANSFORMATION_OPERATOR_3D":
            a = self.f.args(ident)  # name, description, axis1, axis2, origin, scale, axis3
            x = self.direction(a[2], (1.0, 0.0, 0.0)) if len(a) > 2 else (1.0, 0.0, 0.0)
            y = self.direction(a[3], (0.0, 1.0, 0.0)) if len(a) > 3 else (0.0, 1.0, 0.0)
            o = self.point(a[4]) if len(a) > 4 and a[4] else (0.0, 0.0, 0.0)
            z = self.direction(a[6], _cross(x, y)) if len(a) > 6 else _cross(x, y)
            return matrix(o, x, y, z)
        return matrix(*self.placement(ident))

    # Curves

    def bspline_curve(self, ident):
        e = self.f.get(ident)
        if "B_SPLINE_CURVE_WITH_KNOTS" in e and "B_SPLINE_CURVE" not in e:
            a = e["B_SPLINE_CURVE_WITH_KNOTS"]  # name, degree, points, form, closed, self, mults, knots, spec
            degree, pts, mults, knots = a[1], a[2], a[6], a[7]
            weights = None
        else:
            b = e.get("B_SPLINE_CURVE", [])  # degree, points, form, closed, self
            k = e.get("B_SPLINE_CURVE_WITH_KNOTS", [])  # mults, knots, spec
            if not b or not k:
                return None
            degree, pts, mults, knots = b[0], b[1], k[0], k[1]
            weights = e.get("RATIONAL_B_SPLINE_CURVE", [None])[0]
        pts = [self.point(p) for p in pts]
        kv = _knot_vector(mults, knots)
        degree = int(degree)
        if len(kv) != len(pts) + degree + 1 or not pts:
            return None
        return _Curve(degree, pts, kv, [float(w) for w in weights] if weights else None)

    def edge_points(self, ident):
        """Points along an EDGE_CURVE from its start vertex to its end vertex."""
        cached = self.edge_cache.get(ident)
        if cached is not None:
            return cached
        a = self.f.args(ident)  # name, start, end, curve, same_sense
        self._edge_centre = None
        try:
            p1 = self.vertex(a[1])
            p2 = self.vertex(a[2])
            pts = self.curve_points(a[3], p1, p2, a[4] is not False)
            if self._edge_centre:
                self.edge_centre[ident] = self._edge_centre
        except (IndexError, TypeError, ValueError, ZeroDivisionError):
            pts = []
        if not pts:
            pts = [self.vertex(a[1]), self.vertex(a[2])] if len(a) > 2 else []
        self.edge_cache[ident] = pts
        return pts

    def vertex(self, ident):
        a = self.f.args(ident)
        return self.point(a[1]) if len(a) > 1 else (0.0, 0.0, 0.0)

    def curve_points(self, curve, p1, p2, sense):
        kinds = self.f.kinds(curve)
        kind = kinds[0] if kinds else ""
        a = self.f.args(curve)
        if kind in ("SURFACE_CURVE", "SEAM_CURVE", "INTERSECTION_CURVE", "BOUNDED_SURFACE_CURVE"):
            return self.curve_points(a[1], p1, p2, sense)
        if kind == "LINE":
            return [p1, p2]
        if kind in ("CIRCLE", "ELLIPSE"):
            origin, x, y, _z = self.placement(a[1])
            self._edge_centre = origin
            r1 = float(a[2])
            r2 = float(a[3]) if kind == "ELLIPSE" else r1

            def angle(p):
                d = _sub(p, origin)
                return math.atan2(_dot(d, y) / r2, _dot(d, x) / r1)

            t1, t2 = angle(p1), angle(p2)
            if sense:
                span = (t2 - t1) % (2 * math.pi)
            else:
                span = -((t1 - t2) % (2 * math.pi))
            if abs(span) < 1e-9:
                span = 2 * math.pi if sense else -2 * math.pi  # a whole circle
            n = max(2, math.ceil(abs(span) / ANGLE_STEP))
            out = []
            for i in range(n + 1):
                t = t1 + span * i / n
                c, s = r1 * math.cos(t), r2 * math.sin(t)
                out.append((origin[0] + c * x[0] + s * y[0], origin[1] + c * x[1] + s * y[1],
                            origin[2] + c * x[2] + s * y[2]))
            out[0], out[-1] = p1, p2
            return out
        if kind == "POLYLINE":
            pts = [self.point(p) for p in a[1]]
            return pts if sense else pts[::-1]
        if "B_SPLINE_CURVE" in kinds or kind == "B_SPLINE_CURVE_WITH_KNOTS" or kind == "BOUNDED_CURVE":
            c = self.bspline_curve(curve)
            if c is None:
                return [p1, p2]
            t0, t1 = c.range()
            n = max(8, min(64, 4 * len(c.points)))
            samples = [c.at(t0 + (t1 - t0) * i / n) for i in range(n + 1)]
            if not sense:
                samples.reverse()
            return _trim(samples, p1, p2)
        if kind == "TRIMMED_CURVE":
            return self.curve_points(a[1], p1, p2, sense)
        return [p1, p2]

    # Faces

    def loop_points(self, ident):
        kind = self.f.kind(ident)
        a = self.f.args(ident)
        if kind == "POLY_LOOP":
            return [self.point(p) for p in a[1]]
        if kind == "VERTEX_LOOP":
            return []
        out = []
        for oe in a[1] if len(a) > 1 else []:
            o = self.f.args(oe)  # name, *, *, edge, orientation
            if len(o) < 5:
                continue
            pts = self.edge_points(o[3])
            centre = self.edge_centre.get(o[3])
            if centre and centre not in self._centres:
                self._centres.append(centre)
            if o[4] is False:
                pts = pts[::-1]
            out.extend(pts[1:] if out else pts)
        if len(out) > 1 and _close(out[0], out[-1]):
            out.pop()
        return out

    def face_pieces(self, ident, color):
        a = self.f.args(ident)  # name, bounds, surface, same_sense
        if len(a) < 3:
            return []
        if self.stop and self.stop.is_set():
            raise Stopped
        self.faces += 1
        loops = []
        self._centres = []
        for bound in a[1]:
            b = self.f.args(bound)  # name, loop, orientation
            if len(b) > 1:
                pts = self.loop_points(b[1])
                if len(pts) > 1:
                    loops.append(pts)
        if not loops:
            return []
        surface = a[2]
        kinds = self.f.kinds(surface)
        kind = kinds[0] if kinds else ""
        out: list[Piece] = []
        try:
            if kind == "PLANE":
                _o, _x, _y, z = self.placement(self.f.args(surface)[1])
                return [Piece(loops, color, z, True, True, self._centres)]
            if kind in _ANALYTIC:
                out = self.analytic_cells(kind, surface, loops, color)
            elif "B_SPLINE_SURFACE" in kinds or kind == "B_SPLINE_SURFACE_WITH_KNOTS":
                out = self.bspline_cells(surface, loops, color)
        except (IndexError, TypeError, ValueError, ZeroDivisionError):
            out = []
        out.append(Piece([lp + lp[:1] for lp in loops], color, None, False, marks=self._centres))
        return out

    def analytic_cells(self, kind, surface, loops, color):
        a = self.f.args(surface)
        m = matrix(*self.placement(a[1]))
        inv = mat_inv(m)
        r = float(a[2]) if len(a) > 2 else 1.0
        extra = float(a[3]) if len(a) > 3 and a[3] is not None else 0.0
        to_uv, to_xyz = _ANALYTIC[kind](r, extra)
        uv_loops = []
        winds = False
        for lp in loops:
            uv = [to_uv(apply(inv, p)) for p in lp]
            uv = _unwrap(uv, 0, kind)
            if kind == "TOROIDAL_SURFACE":
                uv = _unwrap(uv, 1, kind)
            if uv and abs(uv[-1][0] - uv[0][0]) > math.pi:  # goes once around the axis
                winds = True
            uv_loops.append(uv)
        us = [p[0] for lp in uv_loops for p in lp]
        vs = [p[1] for lp in uv_loops for p in lp]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        if winds:
            u1 = u0 + 2 * math.pi
        if u1 - u0 < 1e-9 or v1 - v0 < 1e-12:
            return []
        nu = max(1, min(48, math.ceil((u1 - u0) / ANGLE_STEP)))
        if kind not in ("CYLINDRICAL_SURFACE", "CONICAL_SURFACE"):
            nv = max(1, min(24, math.ceil((v1 - v0) / ANGLE_STEP)))
        elif winds or (len(uv_loops) == 1 and _area(uv_loops[0]) > 0.98 * (u1 - u0) * (v1 - v0)):
            nv = 1  # straight along v, and the face is a band or a rectangle
        else:
            nv = min(8, max(2, len(uv_loops[0]) // 4))  # trimmed: some rows to follow the outline
        if winds:
            inside = lambda u, v: True  # noqa: E731 - a band around the axis
        else:
            inside = lambda u, v: _inside(uv_loops, u, v)  # noqa: E731
        return _grid_cells(lambda u, v: apply(m, to_xyz(u, v)), u0, u1, v0, v1, nu, nv, inside, color)

    def bspline_surface(self, ident):
        e = self.f.get(ident)
        if "B_SPLINE_SURFACE_WITH_KNOTS" in e and "B_SPLINE_SURFACE" not in e:
            a = e["B_SPLINE_SURFACE_WITH_KNOTS"]
            # name, du, dv, grid, form, uclosed, vclosed, self, umults, vmults, uknots, vknots, spec
            du, dv, grid, um, vm, uk, vk = a[1], a[2], a[3], a[8], a[9], a[10], a[11]
            weights = None
        else:
            b = e.get("B_SPLINE_SURFACE", [])  # du, dv, grid, form, uclosed, vclosed, self
            k = e.get("B_SPLINE_SURFACE_WITH_KNOTS", [])  # umults, vmults, uknots, vknots, spec
            if not b or not k:
                return None
            du, dv, grid, um, vm, uk, vk = b[0], b[1], b[2], k[0], k[1], k[2], k[3]
            weights = e.get("RATIONAL_B_SPLINE_SURFACE", [None])[0]
        grid = [[self.point(p) for p in row] for row in grid]
        du, dv = int(du), int(dv)
        ukv, vkv = _knot_vector(um, uk), _knot_vector(vm, vk)
        if not grid or len(ukv) != len(grid) + du + 1 or len(vkv) != len(grid[0]) + dv + 1:
            return None
        if weights:
            weights = [[float(w) for w in row] for row in weights]
        return _Surface(du, dv, grid, ukv, vkv, weights)

    def bspline_cells(self, surface, loops, color):
        """The whole patch, if the face's boundary covers it (a fillet, a rounded
        corner); a trimmed patch stays wireframe."""
        if len(loops) != 1:
            return []
        s = self.bspline_surface(surface)
        if s is None:
            return []
        u0, u1, v0, v1 = s.range()
        edge = [s.at(u0 + (u1 - u0) * i / 8, v) for i in range(9) for v in (v0, v1)]
        edge += [s.at(u, v0 + (v1 - v0) * i / 8) for i in range(9) for u in (u0, u1)]
        pts = loops[0]
        size = _diagonal(edge)
        if size <= 0 or _diagonal(pts) < 0.9 * size:
            return []
        # every boundary sample of the patch must lie near the face's outline
        tol = 0.05 * size
        step = max(1, len(pts) // 200)
        sample = pts[::step]
        for p in edge:
            if min(_dot(_sub(p, q), _sub(p, q)) for q in sample) > tol * tol * 4:
                return []
        nu = max(2, min(12, len(s.grid)))
        nv = max(2, min(12, len(s.grid[0])))
        return _grid_cells(s.at, u0, u1, v0, v1, nu, nv, lambda u, v: True, color)

    # Solids and assemblies

    def item_pieces(self, ident, color=None) -> list[Piece]:
        """Pieces of a solid / shell / face set, in its representation's coordinates."""
        cached = self.item_cache.get(ident)
        if cached is not None:
            return cached
        color = self.colors.get(ident, color)
        kind = self.f.kind(ident)
        a = self.f.args(ident)
        out: list[Piece] = []
        if kind in ("MANIFOLD_SOLID_BREP", "FACETED_BREP", "BREP_WITH_VOIDS"):
            self.solids += 1
            shells = [a[1]] + (list(a[2]) if kind == "BREP_WITH_VOIDS" and len(a) > 2 else [])
            for shell in shells:
                out.extend(self.shell_pieces(shell, self.colors.get(shell, color)))
        elif kind == "SHELL_BASED_SURFACE_MODEL":
            self.solids += 1
            for shell in a[1]:
                out.extend(self.shell_pieces(shell, self.colors.get(shell, color)))
        elif kind in ("CLOSED_SHELL", "OPEN_SHELL"):
            out = self.shell_pieces(ident, color)
        elif kind in ("ADVANCED_FACE", "FACE_SURFACE"):
            out = self.face_pieces(ident, color or DEFAULT_COLOR)
        self.item_cache[ident] = out
        return out

    def shell_pieces(self, shell, color):
        out = []
        for face in self.f.args(shell)[1] if len(self.f.args(shell)) > 1 else []:
            out.extend(self.face_pieces(face, self.colors.get(face, color) or DEFAULT_COLOR))
        return out

    def read_colors(self):
        for kind in ("STYLED_ITEM", "OVER_RIDING_STYLED_ITEM"):
            for ident in self.f.by_type(kind):
                a = self.f.args(ident)  # name, styles, item[, over_ridden]
                if len(a) < 3 or not a[2]:
                    continue
                color = self.find_color(a[1], 0)
                if color and (kind == "OVER_RIDING_STYLED_ITEM" or a[2] not in self.colors):
                    self.colors[a[2]] = color

    def find_color(self, value, depth):
        if depth > 12:
            return None
        if isinstance(value, list):
            for v in value:
                c = self.find_color(v, depth + 1)
                if c:
                    return c
            return None
        if not isinstance(value, Ref):
            return None
        kind = self.f.kind(value)
        a = self.f.args(value)
        if kind == "COLOUR_RGB" and len(a) >= 4:
            return (float(a[1]), float(a[2]), float(a[3]))
        if kind == "DRAUGHTING_PRE_DEFINED_COLOUR" and a:
            return NAMED_COLORS.get(str(a[0]).lower())
        if kind in ("CURVE_STYLE", "POINT_STYLE", "TEXT_STYLE"):
            return None  # an edge's colour, not the face's
        for v in a:
            if isinstance(v, (Ref, list)):
                c = self.find_color(v, depth + 1)
                if c:
                    return c
        return None


def _close(a, b, eps=1e-9):
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps and abs(a[2] - b[2]) < eps


def _diagonal(pts):
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return math.dist(lo, hi)


def _trim(samples, p1, p2):
    """The part of a sampled curve between the points nearest to p1 and p2."""
    def nearest(p):
        return min(range(len(samples)), key=lambda i: _dot(_sub(samples[i], p), _sub(samples[i], p)))
    i, j = nearest(p1), nearest(p2)
    if _close(p1, p2, 1e-7) and (i == j or {i, j} == {0, len(samples) - 1}):
        part = samples  # closed
    elif i <= j:
        part = samples[i:j + 1]
    else:
        part = samples[j:i + 1][::-1]
    if len(part) < 2:
        return [p1, p2]
    return [p1] + part[1:-1] + [p2]


def _area(poly):
    return abs(sum(poly[i - 1][0] * p[1] - p[0] * poly[i - 1][1] for i, p in enumerate(poly))) / 2


def _unwrap(uv, k, kind):
    """Make an angle coordinate continuous along a loop."""
    if not uv:
        return uv
    out = [uv[0]]
    prev = uv[0][k]
    for p in uv[1:]:
        a = p[k]
        a += 2 * math.pi * round((prev - a) / (2 * math.pi))
        prev = a
        out.append((a, p[1]) if k == 0 else (p[0], a))
    return out


def _inside(loops, u, v):
    """Even-odd test over all loops, also trying the point a turn around (u + 2 pi)."""
    for shift in (0.0, 2 * math.pi, -2 * math.pi):
        x = u + shift
        hits = False
        for lp in loops:
            n = len(lp)
            j = n - 1
            for i in range(n):
                ui, vi = lp[i]
                uj, vj = lp[j]
                if (vi > v) != (vj > v) and x < (uj - ui) * (v - vi) / (vj - vi) + ui:
                    hits = not hits
                j = i
        if hits:
            return True
    return False


def _grid_cells(at, u0, u1, v0, v1, nu, nv, inside, color):
    us = [u0 + (u1 - u0) * i / nu for i in range(nu + 1)]
    vs = [v0 + (v1 - v0) * j / nv for j in range(nv + 1)]
    pts = [[at(u, v) for v in vs] for u in us]
    out = []
    for i in range(nu):
        for j in range(nv):
            if not inside((us[i] + us[i + 1]) / 2, (vs[j] + vs[j + 1]) / 2):
                continue
            a, b, c, d = pts[i][j], pts[i + 1][j], pts[i + 1][j + 1], pts[i][j + 1]
            n = _cross(_sub(c, a), _sub(d, b))
            if _dot(n, n) < 1e-30:
                continue
            out.append(Piece([[a, b, c, d]], color, _unit(n)))
    return out


def _cylinder(r, _extra):
    return (lambda p: (math.atan2(p[1], p[0]), p[2]),
            lambda u, v: (r * math.cos(u), r * math.sin(u), v))


def _cone(r, semi_angle):
    t = math.tan(semi_angle)
    return (lambda p: (math.atan2(p[1], p[0]), p[2]),
            lambda u, v: ((r + v * t) * math.cos(u), (r + v * t) * math.sin(u), v))


def _sphere(r, _extra):
    return (lambda p: (math.atan2(p[1], p[0]), math.atan2(p[2], math.hypot(p[0], p[1]))),
            lambda u, v: (r * math.cos(v) * math.cos(u), r * math.cos(v) * math.sin(u), r * math.sin(v)))


def _torus(big, small):
    return (lambda p: (math.atan2(p[1], p[0]), math.atan2(p[2], math.hypot(p[0], p[1]) - big)),
            lambda u, v: ((big + small * math.cos(v)) * math.cos(u), (big + small * math.cos(v)) * math.sin(u),
                          small * math.sin(v)))


_ANALYTIC = {
    "CYLINDRICAL_SURFACE": _cylinder, "CONICAL_SURFACE": _cone,
    "SPHERICAL_SURFACE": _sphere, "TOROIDAL_SURFACE": _torus,
}
_SOLIDS = ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS", "FACETED_BREP", "SHELL_BASED_SURFACE_MODEL")


def _is_shape_rep(kinds):
    return any(k.endswith("SHAPE_REPRESENTATION") and k != "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION"
               for k in kinds)


def build(f: StepFile, stop=None) -> Model:
    b = _Builder(f, stop)
    b.read_colors()
    model = Model()
    m = re.search(r"FILE_NAME\s*\((.*?)\)\s*;", f.header, re.S)
    if m:
        strings = re.findall(r"'((?:[^']|'')*)'", m.group(1))
        model.source = next((s for s in reversed(strings[-3:]) if s and s.lower() not in ("unknown", "")), "")
    if re.search(r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.", "".join(f.raw.get(i, "") for i in f.by_type("LENGTH_UNIT")[:5])):
        model.unit = "mm"
    elif f.by_type("CONVERSION_BASED_UNIT") and any(
            "INCH" in f.raw[i].upper() for i in f.by_type("CONVERSION_BASED_UNIT")):
        model.unit = "inch"

    # Representations: groups of the same coordinate system, linked by placements
    reps = [i for i in f.raw if _is_shape_rep(f.kinds(i))]
    parent = {r: r for r in reps}

    def root(r):
        while parent.get(r, r) != r:
            parent[r] = parent.get(parent[r], parent[r])
            r = parent[r]
        return r

    links: list[tuple[int, int, tuple]] = []  # child rep, parent rep, matrix
    relationships = set(f.by_type("REPRESENTATION_RELATIONSHIP")) | set(f.by_type("SHAPE_REPRESENTATION_RELATIONSHIP"))
    for ident in relationships:
        e = f.get(ident)
        a = e.get("REPRESENTATION_RELATIONSHIP") or e.get("SHAPE_REPRESENTATION_RELATIONSHIP") or []
        if len(a) < 4 or not a[2] or not a[3]:
            continue
        child, par = a[2], a[3]
        transform = e.get("REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION")
        if transform and transform[0]:
            t = transform[0]
            if f.kind(t) == "ITEM_DEFINED_TRANSFORMATION":
                ta = f.args(t)  # name, description, item1, item2
                mx = mat_mul(b.placement_matrix(ta[3]), mat_inv(b.placement_matrix(ta[2])))
            else:
                mx = b.placement_matrix(t)
            links.append((child, par, mx))
        elif child in parent and par in parent:
            parent[root(child)] = root(par)
    groups: dict[int, list[int]] = {}
    for r in reps:
        groups.setdefault(root(r), []).append(r)
    children: dict[int, list[tuple[int, tuple]]] = {}
    items: dict[int, list[int]] = {}
    is_child = set()
    for child, par, mx in links:
        if child in parent and par in parent:
            children.setdefault(root(par), []).append((root(child), mx))
            is_child.add(root(child))
    for g, members in groups.items():
        for r in members:
            a = f.args(r)
            for item in a[1] if len(a) > 1 and isinstance(a[1], list) else []:
                kind = f.kind(item)
                if kind in _SOLIDS:
                    items.setdefault(g, []).append(item)
                elif kind == "MAPPED_ITEM":
                    ma = f.args(item)  # name, REPRESENTATION_MAP(origin, rep), target
                    rm = f.args(ma[1])
                    if len(rm) > 1 and rm[1] in parent:
                        mx = mat_mul(b.placement_matrix(ma[2]), mat_inv(b.placement_matrix(rm[0])))
                        children.setdefault(g, []).append((root(rm[1]), mx))
                        is_child.add(root(rm[1]))
    roots = [g for g in groups if g not in is_child] or list(groups)
    model.parts = sum(1 for g in groups if g in items)

    # Part names: a representation's product (Altium: the designator, then the model)
    product_of: dict[int, str] = {}
    for ident in f.by_type("SHAPE_DEFINITION_REPRESENTATION"):
        a = f.args(ident)  # PRODUCT_DEFINITION_SHAPE, representation
        try:
            definition = f.args(a[0])[2]  # PRODUCT_DEFINITION(id, description, formation, context)
            product = f.args(f.args(definition)[2])[2]  # PRODUCT_DEFINITION_FORMATION(id, description, product)
            pa = f.args(product)  # PRODUCT(id, name, …)
            name = str(pa[1] or pa[0])
        except (IndexError, TypeError):
            continue
        if a[1] in parent and name:
            product_of[root(a[1])] = name
    label_index: dict[str, int] = {"": 0}

    def add(pieces, mx, label):
        part = label_index.get(label)
        if part is None:
            part = label_index[label] = len(model.labels)
            model.labels.append(label)
        for p in pieces:
            if len(model.pieces) >= MAX_PIECES:
                model.partial = True
                return
            model.part_of.append(part)
            if mx is IDENTITY:
                model.pieces.append(p)
                continue
            model.pieces.append(Piece([[apply(mx, q) for q in lp] for lp in p.loops], p.color,
                                      rotate(mx, p.normal) if p.normal else None, p.fill, p.edges,
                                      [apply(mx, q) for q in p.marks]))

    placed = set()

    def walk(g, mx, depth, names):
        if depth > 32 or model.partial:
            return
        name = product_of.get(g, "")
        if depth and name and (not names or names[-1] != name):  # the top product (the file) is left out
            names = names + [name]
        for item in items.get(g, []):
            placed.add(item)
            add(b.item_pieces(item), mx, " › ".join(names[-3:]))
        for child, cm in children.get(g, []):
            walk(child, mat_mul(mx, cm), depth + 1, names)

    for g in roots:
        walk(g, IDENTITY, 0, [])
    if not placed:  # no representations found: every solid where it stands
        for kind in _SOLIDS:
            for item in f.by_type(kind):
                add(b.item_pieces(item), IDENTITY, "")
    model.solids = b.solids
    model.faces = b.faces
    if model.pieces:
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for p in model.pieces:
            for lp in p.loops:
                for q in lp:
                    for k in range(3):
                        if q[k] < lo[k]:
                            lo[k] = q[k]
                        if q[k] > hi[k]:
                            hi[k] = q[k]
        model.bounds = ((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]))
    return model


def read(path: str, stop=None) -> Model:
    with open(path, "rb") as fh:
        data = fh.read()
    return parse(data, stop)


def parse(data: bytes | str, stop=None) -> Model:
    """Raises NotAModel if it isn't a STEP file, ValueError if it is a broken one,
    Stopped when `stop` (an Event) is set."""
    text = data.decode("latin-1") if isinstance(data, bytes) else data
    if not text.lstrip().startswith("ISO-10303-21"):
        raise NotAModel("not a STEP file")
    return build(StepFile(text, stop), stop)

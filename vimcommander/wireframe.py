"""A 3D model's edges as numpy arrays, projected and culled a whole frame at a
time (Qt-free; stepview draws the result). Used when numpy is installed.

Every edge loop of the model (plane faces' outlines and holes, curved faces'
outlines, a mesh's triangles) becomes segments between shared vertices. A frame
projects all vertices at once and snaps them to whole pixels, so a segment
shorter than a pixel collapses to a point and is dropped, while a chain of them
(a small circle) still joins up through the snapped points. The edges of a
piece smaller than `min_size` on screen, and segments outside the view, are
dropped too: segments are sorted by their piece's size, biggest first, so
that cut is a slice. A frame comes back as one array of lines per color.

Arrays are columns (x, y, z apart): numpy gathers 1-D arrays several times
faster than rows of a 2-D one.
"""

import numpy as np


class Wireframe:
    def __init__(self, items: list, mesh: bool):
        """`items`: stepview.prepare()'s tuples (loops, centre, size, normal, rgb, kind, …).
        Surface cells (kind 1) are left out, except a mesh's triangles."""
        points: list = []
        starts, counts, closed, sizes, colors = [], [], [], [], []
        color_ids: dict = {}
        for it in items:
            loops, size, rgb, kind = it[0], it[2], it[4], it[5]
            if kind == 1 and not mesh:
                continue
            cid = color_ids.setdefault(rgb, len(color_ids))
            for lp in loops:
                if len(lp) < 2:
                    continue
                starts.append(len(points))
                counts.append(len(lp))
                closed.append(kind != 2 and lp[0] != lp[-1])
                sizes.append(size)
                colors.append(cid)
                points.extend(lp)
        self.colors = list(color_ids)  # id -> rgb
        verts = np.array(points, dtype=np.float32).reshape(-1, 3)
        self.xyz = np.ascontiguousarray(verts.T)  # 3 × n: x, y, z columns
        color_type = np.uint8 if len(self.colors) <= 256 else np.int32  # small ints sort by radix
        if not starts:
            self.a = self.b = np.zeros(0, np.int32)
            self.neg_size = np.zeros(0, np.float32)
            self.color = np.zeros(0, color_type)
            return
        starts_a = np.array(starts, np.int64)
        counts_a = np.array(counts, np.int64)
        # Open segments: start + k for k < count - 1, numbered without a Python loop.
        per_loop = counts_a - 1
        total = int(per_loop.sum())
        first = np.cumsum(per_loop) - per_loop  # each loop's first segment number
        a = np.arange(total, dtype=np.int64) + np.repeat(starts_a - first, per_loop)
        b = a + 1
        loop_of = np.repeat(np.arange(len(starts)), per_loop)
        # Closing segments of loops that don't repeat their first point.
        shut = np.flatnonzero(np.array(closed, bool))
        a = np.concatenate([a, starts_a[shut] + counts_a[shut] - 1])
        b = np.concatenate([b, starts_a[shut]])
        loop_of = np.concatenate([loop_of, shut])
        neg_size = -np.array(sizes, np.float32)[loop_of]
        order = np.argsort(neg_size, kind="stable")  # biggest pieces first
        self.a, self.b = a[order].astype(np.int32), b[order].astype(np.int32)
        self.neg_size = neg_size[order]  # rising, for searchsorted
        self.color = np.array(colors, color_type)[loop_of][order]

    def __len__(self) -> int:
        return len(self.a)

    def frame(self, ax, ay, ox: float, oy: float, width: int, height: int,
              min_size: float) -> list[tuple[tuple, np.ndarray, np.ndarray]]:
        """The visible edges as [(rgb, lines, points)] for screen x = ox + ax·p,
        y = oy + ay·p: `lines` an (n, 4) float64 array of x1 y1 x2 y2, `points`
        (m, 2) pixels, both at pixel centres. A segment that only steps to the next
        pixel is drawn as its two pixels (much faster than a line), and a segment
        two faces share is drawn once. `min_size` (model units): edges of smaller
        pieces are left out."""
        n = int(np.searchsorted(self.neg_size, -min_size, side="right"))  # pieces at least min_size
        if not n:
            return []
        x, y, z = self.xyz
        sx = x * np.float32(ax[0]) + y * np.float32(ax[1]) + z * np.float32(ax[2]) + np.float32(ox)
        sy = x * np.float32(ay[0]) + y * np.float32(ay[1]) + z * np.float32(ay[2]) + np.float32(oy)
        np.rint(sx, out=sx)  # whole pixels: sub-pixel segments collapse
        np.rint(sy, out=sy)
        a, b = self.a[:n], self.b[:n]
        xa, ya, xb, yb = sx.take(a), sy.take(a), sx.take(b), sy.take(b)
        visible = (xa != xb) | (ya != yb)
        visible &= (np.maximum(xa, xb) >= 0) & (np.minimum(xa, xb) <= width)
        visible &= (np.maximum(ya, yb) >= 0) & (np.minimum(ya, yb) <= height)
        shown = np.flatnonzero(visible)
        if not len(shown):
            return []
        xa, ya, xb, yb = xa.take(shown), ya.take(shown), xb.take(shown), yb.take(shown)
        # Once per segment: the same two pixels, in either direction, are one key.
        ka, kb = _pixel_key(xa, ya), _pixel_key(xb, yb)
        first = _first_of_each((np.minimum(ka, kb) << np.uint64(32)) | np.maximum(ka, kb))
        xa, ya, xb, yb = xa.take(first), ya.take(first), xb.take(first), yb.take(first)
        color = self.color[:n].take(shown.take(first))
        step = (np.abs(xa - xb) <= 1) & (np.abs(ya - yb) <= 1)  # to a neighbouring pixel
        out = []
        long_ = np.flatnonzero(~step)
        lines = np.empty((len(long_), 4), np.float64)
        lines[:, 0], lines[:, 1] = xa.take(long_), ya.take(long_)
        lines[:, 2], lines[:, 3] = xb.take(long_), yb.take(long_)
        lines += 0.5  # pixel centres: crisp, and antialiasing doesn't smear them over two pixels
        line_color = color.take(long_)
        short = np.flatnonzero(step)
        px = np.concatenate([xa.take(short), xb.take(short)])
        py = np.concatenate([ya.take(short), yb.take(short)])
        first = _first_of_each(_pixel_key(px, py))
        points = np.empty((len(first), 2), np.float64)
        points[:, 0], points[:, 1] = px.take(first), py.take(first)
        points += 0.5
        point_color = np.concatenate([color.take(short), color.take(short)]).take(first)
        lines, line_bounds = _by_color(lines, line_color, len(self.colors))
        points, point_bounds = _by_color(points, point_color, len(self.colors))
        for c in range(len(self.colors)):
            ls, ps = lines[line_bounds[c]:line_bounds[c + 1]], points[point_bounds[c]:point_bounds[c + 1]]
            if len(ls) or len(ps):
                out.append((self.colors[c], ls, ps))
        return out


def _pixel_key(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """A pixel as 32 bits of a uint64, so two make a segment's key. Coordinates
    more than 32 000 pixels off screen are clamped: they only tell pixels apart."""
    xi = (np.clip(x, -32768, 32767) + 32768).astype(np.uint64)
    yi = (np.clip(y, -32768, 32767) + 32768).astype(np.uint64)
    return (xi << np.uint64(16)) | yi


def _first_of_each(keys: np.ndarray) -> np.ndarray:
    """Indices of one of each distinct key (np.unique's return_index sorts stably: 3× slower)."""
    if not len(keys):
        return np.zeros(0, np.intp)
    order = np.argsort(keys)
    ordered = keys.take(order)
    keep = np.empty(len(keys), bool)
    keep[0] = True
    np.not_equal(ordered[1:], ordered[:-1], out=keep[1:])
    return order[keep]


def _by_color(rows: np.ndarray, color: np.ndarray, colors: int):
    """`rows` sorted by color (stable), and where each color starts."""
    order = np.argsort(color, kind="stable")
    color = color.take(order)
    return rows.take(order, axis=0), np.searchsorted(color, np.arange(colors + 1))


# QPainterPath's QDataStream format: element count, then per element its type
# (0 = moveTo, 1 = lineTo) and x, y as doubles, then cStart and the fill rule.
# Writing it with numpy and reading it with `stream >> path` builds a path of
# 100 000s of segments in milliseconds (QPainter.drawLines copies point by point).

def path_bytes(lines: np.ndarray) -> bytes:
    """`lines` (n × 4: x1 y1 x2 y2) as a serialized QPainterPath, little-endian."""
    n2 = 2 * len(lines)
    rec = np.zeros((n2, 20), np.uint8)
    rec[1::2, 0] = 1  # lineTo after each moveTo
    points = np.ascontiguousarray(lines, dtype="<f8").reshape(n2, 2)
    rec[:, 4:] = points.view(np.uint8).reshape(n2, 16)
    return np.array([n2], "<i4").tobytes() + rec.tobytes() + np.zeros(2, "<i4").tobytes()

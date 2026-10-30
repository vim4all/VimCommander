"""STL meshes (binary or ASCII) as a step.Model, for the 3D quick view (pure Python).

Every triangle is a filled piece. Sharp edges (faces meeting at more than
FEATURE_ANGLE, or a hole's border) become outline pieces, so a part's shape
reads as in CAD; meshes over FEATURE_LIMIT triangles skip that.
"""

import math
import re
import struct

from . import step

FEATURE_ANGLE = 30  # degrees between neighbouring triangles that make an edge
FEATURE_LIMIT = 300_000  # triangles; bigger meshes get no edges
COLOR = (0.70, 0.73, 0.78)

_FACET = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)\s+vertex\s+(\S+)\s+(\S+)\s+(\S+)\s+vertex\s+(\S+)\s+(\S+)\s+(\S+)")


def is_stl(path: str) -> bool:
    return path.lower().endswith(".stl")


def triangles(data: bytes, stop=None) -> list[tuple]:
    """(a, b, c) vertex triples. Raises step.NotAModel if it isn't an STL file."""
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * count == len(data):
            out = []
            for n, t in enumerate(struct.iter_unpack("<12fH", memoryview(data)[84:])):
                if stop and not n % 100000 and stop.is_set():
                    raise step.Stopped
                out.append(((t[3], t[4], t[5]), (t[6], t[7], t[8]), (t[9], t[10], t[11])))
            return out
    head = data[:512].lstrip().lower()
    if not head.startswith(b"solid") or (b"facet" not in data[:4096].lower() and len(data) > 4096):
        raise step.NotAModel("not an STL file")
    out = []
    try:
        for n, m in enumerate(_FACET.finditer(data)):
            if stop and not n % 100000 and stop.is_set():
                raise step.Stopped
            v = [float(x) for x in m.groups()]
            out.append(((v[0], v[1], v[2]), (v[3], v[4], v[5]), (v[6], v[7], v[8])))
    except ValueError:
        raise step.NotAModel("not an STL file") from None
    if not out and b"facet" not in data.lower():
        raise step.NotAModel("not an STL file")
    return out


def parse(data: bytes, stop=None) -> step.Model:
    tris = triangles(data, stop)
    model = step.Model(unit="", source="STL", mesh=True)
    normals = []
    for a, b, c in tris:
        n = step._cross(step._sub(b, a), step._sub(c, a))
        length = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
        normals.append((n[0] / length, n[1] / length, n[2] / length) if length > 1e-30 else None)
    kept = 0
    for (a, b, c), n in zip(tris, normals):
        if n is None:
            continue  # a sliver
        if kept >= step.MAX_PIECES:
            model.partial = True
            break
        model.pieces.append(step.Piece([[a, b, c]], COLOR, n))
        kept += 1
    model.faces = kept
    if len(tris) <= FEATURE_LIMIT and not model.partial:
        for a, b in feature_edges(tris, normals, stop):
            model.pieces.append(step.Piece([[a, b]], COLOR, None, False))
    model.part_of = [0] * len(model.pieces)
    if model.pieces:
        pts = [q for p in model.pieces for q in p.loops[0]]
        xs, ys, zs = [q[0] for q in pts], [q[1] for q in pts], [q[2] for q in pts]
        model.bounds = ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))
    return model


def feature_edges(tris, normals, stop=None):
    """Edges with one triangle (a border) or two at a sharp angle."""
    limit = math.cos(math.radians(FEATURE_ANGLE))
    seen: dict[tuple, int] = {}
    shared: dict[tuple, int] = {}
    points: dict[tuple, tuple] = {}
    for i, (tri, n) in enumerate(zip(tris, normals)):
        if n is None:
            continue
        if stop and not i % 100000 and stop.is_set():
            raise step.Stopped
        for j in range(3):
            p, q = tri[j], tri[(j + 1) % 3]
            kp = (round(p[0], 5), round(p[1], 5), round(p[2], 5))
            kq = (round(q[0], 5), round(q[1], 5), round(q[2], 5))
            key = (kp, kq) if kp < kq else (kq, kp)
            if key in seen:
                other = normals[seen.pop(key)]
                if n[0] * other[0] + n[1] * other[1] + n[2] * other[2] < limit:
                    shared[key] = 1
            elif key not in shared:
                seen[key] = i
                points[key] = (p, q)
            else:
                shared.pop(key)  # a third triangle: not a clean edge, leave it out
    for key in seen:  # one triangle only: a border
        yield points[key]
    for key in shared:
        yield points[key]

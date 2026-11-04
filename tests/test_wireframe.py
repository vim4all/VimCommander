import math
import unittest

try:
    import numpy as np
    from vimcommander import wireframe
except ImportError:  # numpy is optional
    np = wireframe = None

GREY, RED = (0.5, 0.5, 0.5), (1.0, 0.0, 0.0)


def item(loops, kind=0, rgb=GREY):
    """A stepview.prepare() tuple: (loops, centre, size, normal, rgb, kind, marks, part, cache)."""
    pts = [q for lp in loops for q in lp]
    lo = [min(q[k] for q in pts) for k in range(3)]
    hi = [max(q[k] for q in pts) for k in range(3)]
    return (loops, None, math.dist(lo, hi), (0, 0, 1), rgb, kind, [], 0, {})


def square(x, y, side=10.0):
    return [[(x, y, 0.0), (x + side, y, 0.0), (x + side, y + side, 0.0), (x, y + side, 0.0)]]


def frame(wf, width=100, height=100, min_size=0.0):
    """Model x, y = screen pixels."""
    return wf.frame((1, 0, 0), (0, 1, 0), 0.0, 0.0, width, height, min_size)


def totals(out):
    return sum(len(lines) for _, lines, _ in out), sum(len(points) for _, _, points in out)


@unittest.skipIf(np is None, "numpy is not installed")
class WireframeTest(unittest.TestCase):
    def test_square(self):
        out = frame(wireframe.Wireframe([item(square(10, 10))], mesh=False))
        self.assertEqual(totals(out), (4, 0))  # closed: four edges
        rgb, lines, _ = out[0]
        self.assertEqual(rgb, GREY)
        self.assertIn([10.5, 10.5, 20.5, 10.5], lines.tolist())  # at pixel centres

    def test_shared_edge_drawn_once(self):
        wf = wireframe.Wireframe([item(square(10, 10)), item(square(20, 10), rgb=RED)], mesh=False)
        self.assertEqual(totals(frame(wf)), (7, 0))

    def test_small_circle_becomes_pixels(self):
        circle = [[(50 + 3 * math.cos(t / 64 * 2 * math.pi), 50 + 3 * math.sin(t / 64 * 2 * math.pi), 0.0)
                   for t in range(64)]]
        lines, points = totals(frame(wireframe.Wireframe([item(circle, kind=2)], mesh=False)))
        self.assertEqual(lines, 0)  # every step goes to a neighbouring pixel
        self.assertTrue(8 <= points <= 30)  # the ring of pixels, not 64 segments

    def test_cutoffs(self):
        wf = wireframe.Wireframe([item(square(10, 10, side=1.0)), item(square(500, 10))], mesh=False)
        self.assertEqual(frame(wf, min_size=2.0), [])  # too small, and off screen
        self.assertEqual(totals(frame(wf, min_size=1.0)), (0, 4))  # the 1-pixel square's corners

    def test_cells_only_in_a_mesh(self):
        cell = [item(square(10, 10), kind=1)]
        self.assertEqual(len(wireframe.Wireframe(cell, mesh=False)), 0)
        self.assertEqual(len(wireframe.Wireframe(cell, mesh=True)), 4)

    def test_path_bytes_read_by_qt(self):
        from PySide6.QtCore import QByteArray, QDataStream
        from PySide6.QtGui import QPainterPath
        lines = np.array([[1.5, 2.5, 30.5, 40.5], [5.0, 6.0, 7.0, 8.0]])
        path = QPainterPath()
        stream = QDataStream(QByteArray(wireframe.path_bytes(lines)))
        stream.setByteOrder(QDataStream.ByteOrder.LittleEndian)
        stream >> path
        self.assertEqual(path.elementCount(), 4)
        e = [path.elementAt(i) for i in range(4)]
        self.assertEqual([(x.x, x.y) for x in e], [(1.5, 2.5), (30.5, 40.5), (5.0, 6.0), (7.0, 8.0)])
        self.assertTrue(e[0].isMoveTo() and e[1].isLineTo() and e[2].isMoveTo())


if __name__ == "__main__":
    unittest.main()

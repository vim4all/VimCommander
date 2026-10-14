import os
import tempfile
import time
import unittest

from vimcommander import graph


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TreeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        r = self.root = self.dir.name
        for i in range(3):
            write(os.path.join(r, f"photo{i}.jpg"), "12345")
        write(os.path.join(r, "notes.txt"))
        write(os.path.join(r, "src", "a.py"))
        write(os.path.join(r, "src", "deep", "more", "b.py", ), "123")
        write(os.path.join(r, ".hidden", "x.txt"))

    def tearDown(self):
        self.dir.cleanup()

    def test_groups_files_by_extension(self):
        g = graph.scan_tree(self.root, 1)
        names = {n.name for n in g.nodes}
        self.assertIn("3 × .jpg", names)
        self.assertIn("src", names)
        self.assertNotIn(".hidden", names)
        jpg = next(n for n in g.nodes if n.ext == ".jpg")
        self.assertEqual((jpg.kind, jpg.category, jpg.count, jpg.size), ("files", "image", 3, 15))

    def test_depth_and_sizes(self):
        g = graph.scan_tree(self.root, 2)
        deep = next(n for n in g.nodes if n.name == "deep")
        self.assertFalse(deep.complete)  # its subfolder isn't scanned yet
        self.assertTrue(graph.fill_sizes(g))
        self.assertEqual(deep.size, 3)
        self.assertEqual(g.nodes[0].size, 15 + 1 + 1 + 3)
        self.assertTrue(g.nodes[0].complete)

    def test_many_children_are_folded(self):
        for i in range(60):
            os.makedirs(os.path.join(self.root, f"dir{i:02}"))
        g = graph.scan_tree(self.root, 1)
        self.assertTrue(g.truncated)
        self.assertTrue(any(n.kind == "more" for n in g.nodes))
        self.assertLessEqual(len(g.nodes[0].children), graph.KEEP[1] + 1)

    def test_radial_layout(self):
        g = graph.scan_tree(self.root, 2)
        graph.layout_radial(g)
        self.assertEqual((g.nodes[0].x, g.nodes[0].y), (0.0, 0.0))
        self.assertTrue(all(abs(n.x) + abs(n.y) > 1 for n in g.nodes[1:]))


class NotesTest(unittest.TestCase):
    def test_links(self):
        wiki, md = graph.find_links("[[A]] [[B#h|alias]] [x](sub/My%20Note.md) [w](https://x.md)")
        self.assertEqual(wiki, ["A", "B"])
        self.assertEqual(md, ["sub/My Note.md"])

    def test_note_graph(self):
        with tempfile.TemporaryDirectory() as r:
            write(os.path.join(r, "Index.md"), "[[Python]] [[Missing]] [x](sub/Deep.md)")
            write(os.path.join(r, "Python.md"), "[[index]]")  # case-insensitive, same edge
            write(os.path.join(r, "sub", "Deep.md"), "alone")
            write(os.path.join(r, "Lonely.md"), "")
            g = graph.scan_notes(r)
            names = {n.name: n for n in g.nodes}
            self.assertEqual(len(g.edges), 2)
            self.assertEqual(names["Index"].links, 2)
            self.assertEqual(names["Lonely"].links, 0)
            layout = graph.ForceLayout(g)
            for _ in range(50):
                layout.step()
            self.assertTrue(all(abs(n.x) < 1e4 and abs(n.y) < 1e4 for n in g.nodes))



class CodeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        r = self.root = self.dir.name
        files = {
            "pkg/__init__.py": "",
            "pkg/core.py": "import os\nfrom . import util\n",
            "pkg/util.py": "from .core import thing\n",
            "pkg/cli.py": "from pkg import (\n    core,\n    util,\n)\nimport pkg.util\n",
            "web/app.ts": "import { a } from './lib'\nconst h = require('./lib/helper')\n",
            "web/lib/index.ts": "export const a = 1\n",
            "web/lib/helper.js": "module.exports = 1\n",
            "c/main.c": '#include "util.h"\n#include <stdio.h>\n',
            "c/util.h": "int f();\n",
            "go/cmd/main.go": 'package main\nimport (\n  "fmt"\n  "example.com/m/internal/text"\n)\n',
            "go/internal/text/text.go": "package text\n",
            "rs/src/main.rs": "mod parser;\nfn main() {}\n",
            "rs/src/parser.rs": "pub mod lexer;\n",
            "rs/src/parser/lexer.rs": "",
            "node_modules/dep/index.js": "require('./x')\n",
        }
        for rel, text in files.items():
            write(os.path.join(r, *rel.split("/")), text)

    def tearDown(self):
        self.dir.cleanup()

    def test_imports(self):
        g = graph.scan_code(self.root)
        rel = [os.path.relpath(n.path, self.root).replace(os.sep, "/") for n in g.nodes]
        edges = {(rel[a], rel[b]) for a, b in g.edges}
        expected = {
            ("pkg/core.py", "pkg/util.py"), ("pkg/util.py", "pkg/core.py"),
            ("pkg/cli.py", "pkg/core.py"), ("pkg/cli.py", "pkg/util.py"),
            ("web/app.ts", "web/lib/index.ts"), ("web/app.ts", "web/lib/helper.js"),
            ("c/main.c", "c/util.h"),
            ("go/cmd/main.go", "go/internal/text/text.go"),
            ("rs/src/main.rs", "rs/src/parser.rs"), ("rs/src/parser.rs", "rs/src/parser/lexer.rs"),
        }
        self.assertEqual(edges, expected)
        self.assertFalse(any(r.startswith("node_modules") for r in rel))
        core = g.nodes[rel.index("pkg/core.py")]
        self.assertEqual((core.count, core.links), (2, 3))  # imported by util and cli, imports util
        self.assertEqual(g.nodes[rel.index("pkg/__init__.py")].name, "pkg/__init__.py")


class TreemapTest(unittest.TestCase):
    def test_squarify_fills_area(self):
        boxes = graph.squarify([(1, 6), (2, 6), (3, 4), (4, 3), (5, 2), (6, 2), (7, 1)], 0, 0, 6, 4)
        self.assertEqual(sorted(k for k, *_ in boxes), [1, 2, 3, 4, 5, 6, 7])
        self.assertAlmostEqual(sum(w * h for _, _, _, w, h in boxes), 24.0)
        for _, x, y, w, h in boxes:
            self.assertTrue(x >= -1e-9 and y >= -1e-9 and x + w <= 6 + 1e-9 and y + h <= 4 + 1e-9)

    def test_squarify_skips_empty(self):
        self.assertEqual(graph.squarify([(1, 0)], 0, 0, 10, 10), [])
        self.assertEqual(len(graph.squarify([(1, 5), (2, 0)], 0, 0, 10, 10)), 1)

    def test_layout_nests_children(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "a", "b"))
            for name, size in (("a/x.txt", 3000), ("a/b/y.bin", 5000), ("z.jpg", 2000)):
                with open(os.path.join(root, name), "wb") as f:
                    f.write(b"0" * size)
            g = graph.scan_tree(root, 2)
            graph.fill_sizes(g)
            boxes = {i: (x, y, w, h) for i, x, y, w, h in graph.layout_treemap(g, 0, 0, 400, 300)}
            a = next(i for i, n in enumerate(g.nodes) if n.name == "a")
            ax, ay, aw, ah = boxes[a]
            for k in g.nodes[a].children:
                x, y, w, h = boxes[k]
                self.assertTrue(ax <= x and ay < y and x + w <= ax + aw + 1e-6 and y + h <= ay + ah + 1e-6)


class FolderSummaryTest(unittest.TestCase):
    def test_scan_folders(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "proj", "src"))
            os.makedirs(os.path.join(root, "photos"))
            files = {"proj/src/main.c": 100, "proj/fw.elf": 400, "photos/a.jpg": 50, "loose.pdf": 10}
            for name, size in files.items():
                with open(os.path.join(root, name), "wb") as f:
                    f.write(b"0" * size)
            os.utime(os.path.join(root, "proj/src/main.c"), (1500000000, 1500000000))  # 2017-07
            g = graph.scan_folders(root)
            by_name = {n.name: n for n in g.nodes}
            proj = by_name["proj"]
            self.assertEqual((proj.count, proj.size), (2, 500))
            self.assertEqual(proj.mix, {"code": [100, 1], "build": [400, 1]})
            self.assertEqual(time.localtime(proj.first).tm_year, 2017)
            self.assertIn(2017 * 12 + 6, proj.months)
            self.assertEqual(by_name["(files here)"].mix, {"document": [10, 1]})
            self.assertEqual(g.nodes[0].count, 4)
            self.assertEqual(g.nodes[0].size, 560)


if __name__ == "__main__":
    unittest.main()

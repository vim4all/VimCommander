import unittest

from vimcommander import jsontree


class LoadTest(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(jsontree.kind("a/B.JSON"), "json")
        self.assertEqual(jsontree.kind("map.geojson"), "json")
        self.assertEqual(jsontree.kind("log.jsonl"), "jsonl")
        self.assertEqual(jsontree.kind("x.ndjson"), "jsonl")
        self.assertEqual(jsontree.kind("ci.yml"), "yaml")
        self.assertIsNone(jsontree.kind("notes.txt"))
        self.assertIsNone(jsontree.kind("n.ipynb"))

    def test_json(self):
        self.assertEqual(jsontree.load(b'\xef\xbb\xbf{"a": [1, 2]}', "json"), {"a": [1, 2]})  # BOM
        with self.assertRaises(ValueError) as cm:
            jsontree.load(b'{\n  "a": 1\n  "b": 2\n}', "json")
        self.assertIn("line 3", str(cm.exception))

    def test_json_lines(self):
        self.assertEqual(jsontree.load(b'{"n": 1}\n\n{"n": 2}\n', "jsonl"), [{"n": 1}, {"n": 2}])
        with self.assertRaises(ValueError) as cm:
            jsontree.load(b'{"n": 1}\n{oops\n', "jsonl")
        self.assertTrue(str(cm.exception).startswith("line 2:"))

    @unittest.skipIf(jsontree.yaml is None, "PyYAML isn't installed")
    def test_yaml(self):
        self.assertEqual(jsontree.load(b"name: x\nlist:\n  - 1\n  - two\n", "yaml"),
                         {"name": "x", "list": [1, "two"]})
        self.assertEqual(jsontree.load(b"a: 1\n---\nb: 2\n", "yaml"), [{"a": 1}, {"b": 2}])  # two documents
        with self.assertRaises(ValueError) as cm:
            jsontree.load(b"a: [1, 2\nb: 3\n", "yaml")
        self.assertIn("line", str(cm.exception))
        # a date is a datetime.date for PyYAML: shown, not an error
        self.assertEqual(jsontree.scalar(jsontree.load(b"d: 2026-11-08\n", "yaml")["d"]), "2026-11-08")

    def test_no_yaml(self):
        saved, jsontree.yaml = jsontree.yaml, None
        try:
            with self.assertRaises(ValueError):
                jsontree.load(b"a: 1", "yaml")
        finally:
            jsontree.yaml = saved


class LabelTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(jsontree.container_label({"a": 1, "b": 2}), "{2}")
        self.assertEqual(jsontree.container_label([1, 2, 3]), "[3]")
        self.assertEqual(jsontree.scalar("hé"), '"hé"')
        self.assertEqual((jsontree.scalar(None), jsontree.scalar(True), jsontree.scalar(1.5)), ("null", "true", "1.5"))
        long = jsontree.scalar("x" * 1000)
        self.assertEqual(len(long), jsontree.SCALAR_CHARS + 1)
        self.assertTrue(long.endswith("…"))
        self.assertEqual([jsontree.kind_of(v) for v in (None, False, 3, "s", {}, [])],
                         ["null", "bool", "number", "string", "object", "array"])

    def test_summary(self):
        self.assertEqual(jsontree.summary({"a": 1}, "json"), "JSON object, 1 key")
        self.assertEqual(jsontree.summary([1, 2], "jsonl"), "JSON Lines, 2 lines")
        self.assertEqual(jsontree.summary([], "yaml"), "YAML, 0 items")
        self.assertEqual(jsontree.summary(5, "json"), "JSON number")

    def test_children_capped(self):
        rows = jsontree.children(list(range(jsontree.MAX_CHILDREN + 5)))
        self.assertEqual(len(rows), jsontree.MAX_CHILDREN + 1)
        self.assertEqual(rows[1], (1, 1))
        key, more = rows[-1]
        self.assertIsNone(key)
        self.assertEqual(more.count, 5)
        self.assertEqual(jsontree.children({"a": 1}), [("a", 1)])

    def test_path(self):
        self.assertEqual(jsontree.path([]), ".")
        self.assertEqual(jsontree.path(["items", 3, "name"]), ".items[3].name")
        self.assertEqual(jsontree.path(["a key", "x-y", 0]), '.["a key"]["x-y"][0]')
        self.assertEqual(jsontree.path([5, "id"]), ".[5].id")  # a top-level array
        self.assertEqual(jsontree.path([True]), '.["True"]')  # a YAML key that isn't a string


if __name__ == "__main__":
    unittest.main()

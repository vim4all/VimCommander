import os
import tempfile
import time
import unittest
from datetime import date

from vimcommander import actions, vim
from vimcommander.activity import files_on_day, scan_activity
from vimcommander.compare import compare_trees, copy_plan
from vimcommander.fuzzy import rank, score
from vimcommander.rename import bulk_text, parse_bulk


def write(path, text="x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mtime:
        os.utime(path, (mtime, mtime))


class FuzzyTest(unittest.TestCase):
    def test_order_and_ranking(self):
        self.assertIsNone(score("ba", "abc"))
        self.assertIsNotNone(score("ab", "xab_a"))
        items = ["src/vimcommander/app.py", "tests/test_app.py", "src/vimcommander/archive.py"]
        self.assertEqual(rank("app", items)[0][1], "src/vimcommander/app.py")
        self.assertEqual(rank("arch", items)[0][1], "src/vimcommander/archive.py")
        self.assertEqual(rank("app", items, bonus=lambda i: 1000 if "tests" in i else 0)[0][1],
                         "tests/test_app.py")


class DescriptionsTest(unittest.TestCase):
    def test_every_key_is_described(self):
        tables = (vim.NORMAL_KEYS, vim.VISUAL_KEYS, vim.NORMAL_ARG_KEYS)
        names = {v.partition(":")[0] for t in tables for v in t.values()}
        self.assertEqual(sorted(n for n in names if n not in actions.ACTIONS), [])

    def test_every_command_is_described(self):
        from vimcommander.app import MainWindow
        commands = {m[3:] for m in dir(MainWindow) if m.startswith("ex_")}
        missing = [c for c in commands if c not in actions.COMMANDS and c not in actions.ALIASES]
        self.assertEqual(sorted(missing), [])
        self.assertTrue(actions.needs_argument("cd"))
        self.assertFalse(actions.needs_argument("tabnew"))


class ResolveTest(unittest.TestCase):
    """: commands may be shortened as in vim."""

    def test_abbreviations(self):
        from vimcommander.app import MainWindow
        names = {m[3:] for m in dir(MainWindow) if m.startswith("ex_")}
        for typed, name in (("se", "set"), ("sh", "shell"), ("d", "delete"), ("dis", "display"), ("tabn", "tabnext"),
                            ("tabp", "tabprevious"), ("pdfe", "pdfextract"), ("bulk", "bulkrename"),
                            ("mes", "messages"), ("norm", "normal"), ("cope", "copen"), ("q", "q"), ("e", "e")):
            self.assertEqual(actions.resolve(typed, names), (name, []), typed)
        name, could_be = actions.resolve("tab", names)
        self.assertIsNone(name)
        self.assertIn("tabnew", could_be)
        self.assertEqual(actions.resolve("nosuch", names), (None, []))
        for full, short in actions.SHORTEST.items():  # every short form names a real command
            self.assertIn(short, full)
            self.assertIn(full, names)


class BulkTest(unittest.TestCase):
    def test_round_trip(self):
        text = bulk_text(["a", "b", "c"]).replace("\tb\n", "\tB\n").replace("3\tc\n", "")
        self.assertEqual(parse_bulk(text, 3), ({0: "a", 1: "B"}, {2}))

    def test_errors(self):
        with self.assertRaises(ValueError):
            parse_bulk("1\ta\n1\tb\n", 2)
        with self.assertRaises(ValueError):
            parse_bulk("9\ta\n", 2)


class CompareTest(unittest.TestCase):
    def test_trees(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = os.path.join(d, "L"), os.path.join(d, "R")
            write(os.path.join(left, "same.txt"), "s", 1_000_000)
            write(os.path.join(right, "same.txt"), "s", 1_000_000)
            write(os.path.join(left, "new.txt"), "new", 2_000_000)
            write(os.path.join(right, "new.txt"), "old", 1_000_000)
            write(os.path.join(left, "sub", "only.txt"))
            write(os.path.join(right, "sub", "theirs.txt"))
            diffs = compare_trees(left, right)
            got = {(x.rel.replace(os.sep, "/"), x.status, x.action) for x in diffs}
            self.assertEqual(got, {("new.txt", "left_newer", ">"), ("sub/only.txt", "left_only", ">"),
                                   ("sub/theirs.txt", "right_only", "<")})
            shallow = compare_trees(left, right, recursive=False)
            self.assertEqual([x.rel for x in shallow], ["new.txt"])
            plan = copy_plan(diffs, left, right)
            self.assertIn((os.path.join(right, "sub", "theirs.txt"), os.path.join(left, "sub", "theirs.txt")), plan)


class ActivityTest(unittest.TestCase):
    def test_days_and_newest(self):
        with tempfile.TemporaryDirectory() as d:
            old = time.mktime((2025, 3, 4, 12, 0, 0, 0, 0, -1))
            write(os.path.join(d, "a.txt"), mtime=old)
            write(os.path.join(d, "sub", "b.txt"), mtime=old + 60)
            write(os.path.join(d, "c.txt"))
            days, newest, seen, finished = scan_activity(d)
            self.assertTrue(finished)
            self.assertEqual(seen, 3)
            self.assertEqual(days[date(2025, 3, 4)], 2)
            self.assertEqual(os.path.basename(newest[0][1]), "c.txt")
            on_day = [os.path.basename(p) for _, p in files_on_day(d, date(2025, 3, 4))]
            self.assertEqual(on_day, ["b.txt", "a.txt"])


if __name__ == "__main__":
    unittest.main()

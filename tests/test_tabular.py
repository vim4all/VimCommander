import os
import tempfile
import time
import unittest

from vimcommander import results, tabular


class ParseTest(unittest.TestCase):
    def test_comma_with_header(self):
        t = tabular.parse("name,score\nann,3\nbob,5\n", "x.csv")
        self.assertEqual(t.header, ["name", "score"])
        self.assertEqual(t.rows, [["ann", "3"], ["bob", "5"]])
        self.assertTrue(t.complete)

    def test_semicolon_and_decimal_comma(self):
        t = tabular.parse("a;b\n1,5;x\n2,5;y\n", "x.csv")
        self.assertEqual(t.delimiter, ";")
        self.assertIn("min 1.5, max 2.5, mean 2", tabular.column_stats(t, 0))
        self.assertIn("2 distinct", tabular.column_stats(t, 1))

    def test_tsv_and_no_header(self):
        t = tabular.parse("1\t2\n3\t4\n", "x.tsv")
        self.assertEqual(t.header, ["1", "2"])
        self.assertEqual(len(t.rows), 2)

    def test_ragged_rows_and_empty_cells(self):
        t = tabular.parse("a,b\n1\n2,\n", "x.csv")
        self.assertEqual(len(t.header), 2)
        self.assertIn("2 empty", tabular.column_stats(t, 1))

    def test_truncated_drops_partial_line(self):
        t = tabular.parse("a,b\n1,2\n3,", "x.csv", truncated=True)
        self.assertEqual(t.rows, [["1", "2"]])
        self.assertFalse(t.complete)

    def test_bom(self):
        self.assertEqual(tabular.parse("﻿a,b\nx,y\n").header, ["a", "b"])


class ChangedFilesTest(unittest.TestCase):
    def test_newest_first_and_old_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            now = time.time()
            for name, age in (("old.txt", 30), ("new.txt", 0.1), ("mid.txt", 2)):
                path = os.path.join(root, name)
                open(path, "w").close()
                os.utime(path, (now - age * 86400,) * 2)
            found = [p for batch in results.changed_files(root, 7) for p, _ in batch]
            self.assertEqual([os.path.basename(p) for p in found], ["new.txt", "mid.txt"])


if __name__ == "__main__":
    unittest.main()

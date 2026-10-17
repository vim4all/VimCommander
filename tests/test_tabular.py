import math
import os
import tempfile
import threading
import time
import unittest
from array import array

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


class ColumnsTest(unittest.TestCase):
    """read_columns(): the numbers of a whole file, for the plot (zp)."""

    def read(self, text: str, name="x.csv"):
        return tabular.read_columns(text.encode("utf-8"), name)

    def test_header_and_time_axis(self):
        c = self.read("Time,Ips,Vout\n0,1,5\n1e-6,2,5.5\n2e-6,3,6\n")
        self.assertEqual(c.names, ["Time", "Ips", "Vout"])
        self.assertEqual((c.x, c.rows), (0, 3))
        self.assertEqual(list(c.values[1]), [1.0, 2.0, 3.0])
        self.assertTrue(c.complete)

    def test_scope_preamble_gives_names_and_units(self):
        # A Siglent scope export: settings in the first columns, data from column 4 on
        rows = ["Record Length,4,,Source,CH1,CH2", "Sample Interval,1e-9,,Second,Volt,Volt"]
        rows += [f"Vertical Scale,CH1:1.00,,{i * 1e-9},{i},{-i}" for i in range(3)]
        rows += [f",,,{i * 1e-9},{i},{-i}" for i in range(3, 6)]
        c = self.read("\n".join(rows) + "\n")
        self.assertEqual(list(zip(c.names, c.units)), [("Source", "Second"), ("CH1", "Volt"), ("CH2", "Volt")])
        self.assertEqual((c.x, c.rows), (0, 6))
        self.assertEqual(list(c.values[2]), [0.0, -1.0, -2.0, -3.0, -4.0, -5.0])

    def test_headerless_and_not_rising(self):
        c = self.read("0.0 ,5\n2.5 ,4\n2.4 ,3\n")  # a first column that goes down: not an x axis
        self.assertEqual(c.names, ["column 1", "column 2"])
        self.assertIsNone(c.x)

    def test_text_columns_left_out_and_gaps(self):
        c = self.read("f,eff,topology\n1,0.9,LCC\n2,,SS\n3,0.95,LCC\nnot,a,row\n")
        self.assertEqual(c.names, ["f", "eff"])
        self.assertEqual(c.rows, 3)  # the text line is skipped
        self.assertTrue(math.isnan(c.values[1][1]))
        self.assertEqual(c.gaps, [False, True])
        self.assertEqual(tabular.stats(c.values[1], 0, 3, True)[:3], (0.9, 0.95, 0.925))

    def test_semicolons_decimal_commas_and_junk_labels(self):
        c = self.read("Terminal log file;;\n#ЗНАЧ!;x;\n1,5;2,5;\n3,5;4,5;\n")
        self.assertEqual(c.names, ["column 1", "column 2"])  # a title and an Excel error aren't names
        self.assertEqual(list(c.values[0]), [1.5, 3.5])

    def test_nothing_to_plot(self):
        self.assertIsNone(self.read("name,city\nann,Riga\nbob,Kyiv\n"))
        self.assertIsNone(self.read("a,b\n1,2\n"))  # one row of numbers is no line

    def test_infinity_becomes_a_gap_and_stop(self):
        c = self.read("a,b\n1,inf\n2,3\n3,4\n")
        self.assertTrue(math.isnan(c.values[1][0]))
        stop = threading.Event()
        stop.set()
        big = "a,b\n" + "".join(f"{i},{i}\n" for i in range(70000))
        self.assertIsNone(tabular.read_columns(big.encode(), "x.csv", stop))

    def test_file_with_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "x.csv")
            with open(path, "wb") as f:
                f.write("\ufefft,v\r\n0,1\r\n1,2\r\n".encode("utf-8"))
            c = tabular.read_columns(path)
            self.assertEqual((c.names, c.x, list(c.values[1])), (["t", "v"], 0, [1.0, 2.0]))

    def test_stats(self):
        values = array("d", [3.0, -3.0, 3.0, -3.0])
        self.assertEqual(tabular.stats(values, 0, 4), (-3.0, 3.0, 0.0, 3.0))
        self.assertEqual(tabular.stats(values, 0, 1), (3.0, 3.0, 3.0, 3.0))
        self.assertIsNone(tabular.stats(values, 2, 2))


class NumberFormatTest(unittest.TestCase):
    def test_si(self):
        self.assertEqual([tabular.si(v) for v in (0, 0.25, 0.9986, 0.0012, 2.5e-6, 12288, -7.74)],
                         ["0", "0.25", "0.9986", "1.2m", "2.5µ", "12.29k", "-7.74"])
        self.assertEqual(tabular.si(999970), "1M")  # rounding moves it to the next prefix
        self.assertEqual(tabular.si(1e20), "1e+20")
        self.assertEqual(tabular.si(float("nan")), "–")

    def test_ticks(self):
        self.assertEqual(tabular.ticks(0, 0.00123, 5)[0], [0.0, 0.0005, 0.001])
        values, digits = tabular.ticks(1.00001, 1.00009, 4)
        self.assertEqual([tabular.si(v, digits) for v in values], ["1.00002", "1.00004", "1.00006", "1.00008"])
        self.assertEqual(tabular.ticks(1, 1, 5), ([], 4))


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

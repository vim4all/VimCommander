import math
import struct
import unittest

from vimcommander import ltspice


def raw(names, kinds, points, flags="real forward", body=b"", wide=True, plot="Transient Analysis", binary=True):
    """A .raw file as LTspice writes it: the header, then the samples."""
    lines = [f"Title: * test.asc", "Date: today", f"Plotname: {plot}", f"Flags: {flags}",
             f"No. Variables: {len(names)}", f"No. Points: {points}", "Offset: 0", "Command: LTspice", "Variables:"]
    lines += [f"\t{i}\t{n}\t{k}" for i, (n, k) in enumerate(zip(names, kinds))]
    lines.append("Binary:" if binary else "Values:")
    head = ("\n".join(lines) + "\n").encode("utf-16-le" if wide else "latin-1")
    return head + body


class RawTest(unittest.TestCase):
    def test_transient(self):
        body = b"".join(struct.pack("<dff", t, 2 * t, -t) for t in (0.0, -1e-6, 2e-6))  # -1e-6: a flagged step
        c = ltspice.read_raw(raw(["time", "V(out)", "I(L1)"], ["time", "voltage", "device_current"], 3, body=body))
        self.assertEqual(c.names, ["time", "V(out)", "I(L1)"])
        self.assertEqual(c.units, ["s", "V", "A"])
        self.assertEqual(c.x, 0)
        self.assertEqual(list(c.values[0]), [0.0, 1e-6, 2e-6])
        self.assertAlmostEqual(c.values[1][2], 4e-6)

    def test_double_fastaccess_and_ascii_header(self):
        body = struct.pack("<3d", 0, 1, 2) + struct.pack("<3d", 5, 6, 7)  # time column, then V(a)
        c = ltspice.read_raw(raw(["time", "V(a)"], ["time", "voltage"], 3, "real forward double fastaccess", body,
                                 wide=False))
        self.assertEqual(list(c.values[1]), [5.0, 6.0, 7.0])

    def test_ac_magnitude_in_db(self):
        body = b"".join(struct.pack("<4d", f, 0, 10.0, 0) for f in (1.0, 10.0))
        c = ltspice.read_raw(raw(["frequency", "V(out)"], ["frequency", "voltage"], 2, "complex forward log", body,
                                 plot="AC Analysis"))
        self.assertEqual(c.names, ["frequency", "|V(out)| dB"])
        self.assertEqual(list(c.values[0]), [1.0, 10.0])
        self.assertAlmostEqual(c.values[1][0], 20.0)

    def test_values_as_text(self):
        text = "0\t0.0\n\t5\n1\t1.0\n\t6\n"
        c = ltspice.read_raw(raw(["time", "V(a)"], ["time", "voltage"], 2, body=text.encode("utf-16-le"),
                                 binary=False))
        self.assertEqual(list(c.values[1]), [5.0, 6.0])

    def test_operating_point_and_others(self):
        data = raw(["V(a)", "I(R1)"], ["voltage", "device_current"], 1, "real", struct.pack("<df", 3.3, 0.5),
                   plot="Operating Point")
        self.assertIsNone(ltspice.read_raw(data))
        self.assertEqual(ltspice.operating_point(data), [("V(a)", 3.3, "V"), ("I(R1)", 0.5, "A")])
        self.assertIsNone(ltspice.read_raw(bytes(range(256)) * 40))  # a camera's .raw image
        self.assertIsNone(ltspice.operating_point(b"QOptions"))
        self.assertTrue(ltspice.is_raw("x.RAW"))

    def test_truncated_file(self):
        body = b"".join(struct.pack("<df", t, t) for t in range(10))[:-3]
        c = ltspice.read_raw(raw(["time", "V(a)"], ["time", "voltage"], 10, body=body))
        self.assertEqual(c.rows, 9)
        self.assertFalse(c.complete)
        self.assertFalse(math.isnan(c.values[1][8]))


if __name__ == "__main__":
    unittest.main()

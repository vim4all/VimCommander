import unittest

from vimcommander.pane import parse_color


class ColorTest(unittest.TestCase):
    def test_formats(self):
        c = parse_color("#e5393526")  # CSS order: rrggbbaa
        self.assertEqual((c.red(), c.green(), c.blue(), c.alpha()), (0xe5, 0x39, 0x35, 0x26))
        self.assertEqual(parse_color("#2f6fd0").alpha(), 255)
        self.assertEqual(parse_color("orange").name(), "#ffa500")
        self.assertIsNone(parse_color(""))

    def test_bad(self):
        with self.assertRaises(ValueError):
            parse_color("nope")


if __name__ == "__main__":
    unittest.main()

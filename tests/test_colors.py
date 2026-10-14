import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from vimcommander import theme
from vimcommander.config import DEFAULTS
from vimcommander.pane import COLORS, parse_color, set_colors


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


class ThemeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        theme.apply("")

    def test_defaults_replaced(self):
        options = dict(DEFAULTS["colors"], tagged="#00ff00")  # one changed by the user
        self.assertEqual(set_colors(options, theme.color_defaults("mocha")), [])
        self.assertEqual(COLORS["cursor"].name(), theme.MOCHA["blue"])
        self.assertEqual(COLORS["tagged"].name(), "#00ff00")

    def test_apply(self):
        from vimcommander import graphview
        before = graphview.COLORS["folder"]
        self.assertTrue(theme.apply("catppuccin-mocha"))
        self.assertEqual(QApplication.palette().base().color().name(), theme.MOCHA["base"])
        self.assertEqual(graphview.COLORS["folder"], theme.MOCHA["blue"])
        theme.apply("")
        self.assertEqual(graphview.COLORS["folder"], before)
        self.assertFalse(theme.apply("nope"))


if __name__ == "__main__":
    unittest.main()

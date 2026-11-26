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

    def test_flavours(self):
        from vimcommander import icons, termshell
        flavours = {"catppuccin-latte": theme.LATTE, "catppuccin-frappe": theme.FRAPPE,
                    "catppuccin-macchiato": theme.MACCHIATO, "catppuccin-mocha": theme.MOCHA}
        for name, palette in flavours.items():
            with self.subTest(name):
                self.assertEqual(set(palette), set(theme.MOCHA))  # the same 26 colour names
                self.assertTrue(theme.apply(name))
                self.assertEqual(QApplication.palette().base().color().name(), palette["base"])
                self.assertIs(icons.THEME, palette)
                self.assertEqual(theme.color_defaults(name)["cursor"], palette["blue"])
        # Latte's terminal: dark gray for black, as Catppuccin's own terminal themes do
        theme.apply("latte")
        self.assertEqual(termshell.ANSI[0], theme.LATTE["subtext1"])
        self.assertEqual(theme.color_defaults("latte")["cursor_text"], theme.LATTE["base"])

    def test_aliases(self):
        for alias in ("Frappé", " frappe ", "catppuccin-frappé"):
            with self.subTest(alias):
                self.assertTrue(theme.apply(alias))
                self.assertEqual(QApplication.palette().base().color().name(), theme.FRAPPE["base"])
                self.assertEqual(theme.color_defaults(alias)["cursor"], theme.FRAPPE["blue"])
        self.assertEqual(theme.color_defaults("Macchiato")["cursor"], theme.MACCHIATO["blue"])


if __name__ == "__main__":
    unittest.main()

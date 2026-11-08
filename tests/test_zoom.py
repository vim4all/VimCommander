import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QApplication

from vimcommander import app


def key(k, mods=Qt.KeyboardModifier.ControlModifier, text=""):
    return QKeyEvent(QEvent.Type.KeyPress, k, mods, text)


class ZoomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved = app.SYSTEM_FONT
        font = QFont()
        font.setPointSizeF(10)
        app.SYSTEM_FONT = font

    def tearDown(self):
        app.SYSTEM_FONT = self.saved

    def test_key_names(self):
        ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        self.assertEqual(app.key_name(key(Qt.Key.Key_Plus, ctrl_shift)), "<C-+>")
        self.assertEqual(app.key_name(key(Qt.Key.Key_Equal)), "<C-=>")
        self.assertEqual(app.key_name(key(Qt.Key.Key_Minus)), "<C-->")
        self.assertEqual(app.key_name(key(Qt.Key.Key_0)), "<C-0>")
        # without Ctrl they stay the plain keys (0 = first column, - = parent…)
        self.assertEqual(app.key_name(key(Qt.Key.Key_0, Qt.KeyboardModifier.NoModifier, "0")), "0")
        self.assertEqual(app.key_name(key(Qt.Key.Key_Minus, Qt.KeyboardModifier.NoModifier, "-")), "-")

    def test_window_font(self):
        self.assertEqual(app.ui_font({}).pointSizeF(), 10)  # the system size
        self.assertEqual(app.ui_font({"font": {"size": 12}}).pointSizeF(), 12)
        self.assertEqual(app.ui_font({"font": {"size": 12}, "zoom": 2}).pointSizeF(), 14)
        self.assertEqual(app.ui_font({"zoom": -3}).pointSizeF(), 7)
        self.assertEqual(app.ui_font({"zoom": 100}).pointSizeF(), app.ZOOM_LIMITS[1])
        self.assertEqual(app.ui_font({"zoom": "x"}).pointSizeF(), 10)  # a bad value: no zoom

    def test_monospace_font(self):
        font, _ = app.monospace_font({"size": 11}, 2)
        self.assertEqual(font.pointSizeF(), 13)
        font, _ = app.monospace_font({"size": 11})
        self.assertEqual(font.pointSizeF(), 11)


if __name__ == "__main__":
    unittest.main()

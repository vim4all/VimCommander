import unittest

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication

from vimcommander import icons
from vimcommander.bar import BarItem, icon_for


class IconsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def test_kinds(self):
        for name, is_dir, kind in (("main.py", False, "python"), ("LIB.H", False, "header"),
                                   ("Makefile", False, "make"), ("CMakeLists.txt", False, "cmake"),
                                   (".gitignore", False, "git"), ("x.unknown", False, "file"),
                                   ("noext", False, "file"), (".git", True, "folder_git"),
                                   ("Music", True, "music"), ("src", True, "folder"),
                                   ("model.STEP", False, "model"), ("a.tar.gz", False, "archive")):
            self.assertEqual(icons.kind_for(name, is_dir), kind, name)
        self.assertEqual(icons.program_kind(r"C:\Program Files\Code.exe"), "vscode")
        self.assertEqual(icons.program_kind("/usr/bin/nvim"), "neovim")
        self.assertEqual(icons.program_kind("/opt/thing"), "program")
        for kind in set(icons.EXTENSIONS.values()) | set(icons.NAMES.values()) | set(icons.FOLDERS.values()) \
                | set(icons.PROGRAM_KINDS.values()):
            self.assertIn(kind, icons.GLYPHS)
        for _, colour in icons.GLYPHS.values():
            self.assertIn(colour, icons.MOCHA)

    def test_paints_clean(self):
        # the pixmap is cleared around the glyph (Qt's default leaves noise there)
        image = icons.icon("folder").pixmap(QSize(32, 32)).toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        self.assertTrue(any(image.pixelColor(x, 16).alpha() for x in range(32)))

    def test_recolored(self):
        icon = icons.icon("python")
        red = icons.recolored(icon, QColor("#ff0000"))
        self.assertIsNot(red, icon)
        self.assertIs(icons.recolored(icon, QColor("#ff0000")), red)  # cached
        other = QIcon()
        self.assertIs(icons.recolored(other, QColor("#ff0000")), other)  # not ours: as it is

    def test_spec(self):
        self.assertIsNotNone(icons.parse_spec("U+F120"))
        self.assertIsNotNone(icons.parse_spec("f120 mauve"))
        self.assertIsNotNone(icons.parse_spec("★ #a6e3a1"))
        for bad in ("", "logo.png", "U+F120 notacolour", "a b c", "12"):
            self.assertIsNone(icons.parse_spec(bad), bad)

    def test_system_mode(self):
        style = self.qapp.style()
        item = BarItem("Home", "~")
        try:
            icons.OPTIONS["icons"] = "system"
            self.assertFalse(icons.enabled())
            self.assertFalse(icon_for(item, style).isNull())
        finally:
            icons.OPTIONS["icons"] = "auto"
        self.assertIs(icon_for(item, style), icons.icon("home"))


if __name__ == "__main__":
    unittest.main()

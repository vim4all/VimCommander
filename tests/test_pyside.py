import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from vimcommander.app import broken_pyside


class PySideVersionTest(unittest.TestCase):
    def test_broken_pyside(self):
        # PySide6 6.12 leaks references to None, fatal before Python 3.12 (None not immortal)
        self.assertTrue(broken_pyside((3, 10), "6.12.0"))
        self.assertTrue(broken_pyside((3, 11), "6.12.1"))
        self.assertFalse(broken_pyside((3, 12), "6.12.0"))
        self.assertFalse(broken_pyside((3, 10), "6.11.0"))
        self.assertFalse(broken_pyside((3, 10), "6.7.3"))
        self.assertFalse(broken_pyside((3, 13), "6.13.0"))


if __name__ == "__main__":
    unittest.main()

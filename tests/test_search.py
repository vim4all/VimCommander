import unittest

from vimcommander.app import pattern_regex


class PatternTest(unittest.TestCase):
    def test_regex(self):
        self.assertTrue(pattern_regex(r"\d+\.txt$").search("a12.txt"))
        self.assertFalse(pattern_regex(r"txt$").search("a.txt.bak"))

    def test_shell_pattern(self):
        self.assertTrue(pattern_regex("*.jpg").search("photo.JPG"))
        self.assertFalse(pattern_regex("*.jpg").search("photo.jpg.txt"))

    def test_smartcase(self):
        self.assertTrue(pattern_regex("readme").search("README.md"))
        self.assertFalse(pattern_regex("Readme").search("README.md"))
        self.assertTrue(pattern_regex("Readme", smartcase=False).search("README.md"))

    def test_invalid_regex_is_literal(self):
        self.assertTrue(pattern_regex("a(b").search("xa(b"))


if __name__ == "__main__":
    unittest.main()

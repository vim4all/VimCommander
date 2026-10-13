import unittest

from vimcommander.zoxide import matches


class MatchesTest(unittest.TestCase):
    def test_keywords_in_order(self):
        self.assertTrue(matches("/home/me/projects/vimcommander", ["proj", "vim"]))
        self.assertFalse(matches("/home/me/projects/vimcommander", ["vim", "proj"]))

    def test_last_keyword_in_last_component(self):
        self.assertFalse(matches("/home/me/photos/2024", ["pho"]))
        self.assertTrue(matches("/home/me/photos/2024", ["pho", "24"]))

    def test_case_and_empty(self):
        self.assertTrue(matches(r"C:\Users\Me\Downloads", ["down"]))
        self.assertTrue(matches("/anything", []))


if __name__ == "__main__":
    unittest.main()

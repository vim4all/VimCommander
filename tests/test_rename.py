import unittest

from vimcommander.rename import check_names, format_name, parse_substitute


class RenameTest(unittest.TestCase):
    def test_pattern(self):
        self.assertEqual(format_name("img_{n:3}{ext}", "Photo.JPG", 7), "img_007.JPG")
        self.assertEqual(format_name("{name}-old{ext}", "a.tar.gz", 1), "a.tar-old.gz")
        self.assertEqual(format_name("{name}{ext}", ".bashrc", 1), ".bashrc")
        self.assertEqual(format_name("{name}_{n}", "my.folder", 2, is_dir=True), "my.folder_2")

    def test_substitute(self):
        regex, repl, count = parse_substitute(r"s/(\d+)/[&]-\1 \&/g")
        self.assertEqual(regex.sub(repl, "a12b3", count=count), "a[12]-12 &b[3]-3 &")
        regex, repl, count = parse_substitute("s#A#x#i")
        self.assertEqual(regex.sub(repl, "aAa", count=count), "xAa")
        with self.assertRaises(ValueError):
            parse_substitute("s/a/b/q")

    def test_check_names(self):
        self.assertIsNone(check_names([("a", "b"), ("b", "a")]))
        self.assertIn("Two items", check_names([("a", "x"), ("b", "X")]))
        self.assertIn("Invalid", check_names([("a", "x/y")]))


if __name__ == "__main__":
    unittest.main()

import unittest

from vimcommander.rename import CASES, check_names, format_name, increment, parse_substitute


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

    def test_increment(self):
        self.assertEqual(increment("img_0099.jpg", 1), "img_0100.jpg")
        self.assertEqual(increment("report_2024_v10.pdf", -1), "report_2024_v9.pdf")  # the last number
        self.assertEqual(increment("take 007.wav", 5), "take 012.wav")
        self.assertEqual(increment("0100", -1), "0099")
        self.assertEqual(increment("file-1", -3), "file-0")  # "-" is a separator: stops at 0
        self.assertEqual(increment("x2.mp3", 1), "x3.mp3")  # not the extension's digit
        self.assertEqual(increment("build.v2", 1, is_dir=True), "build.v3")
        self.assertIsNone(increment("song.mp3", 1))
        self.assertIsNone(increment("notes", 1))

    def test_cases(self):
        self.assertEqual([CASES[c]("Read Me.Txt") for c in ("upper", "lower", "toggle_case")],
                         ["READ ME.TXT", "read me.txt", "rEAD mE.tXT"])

    def test_check_names(self):
        self.assertIsNone(check_names([("a", "b"), ("b", "a")]))
        self.assertIn("Two items", check_names([("a", "x"), ("b", "X")]))
        self.assertIn("Invalid", check_names([("a", "x/y")]))


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
import zipfile

from vimcommander import archive


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.zip = os.path.join(self.dir.name, "a.zip")
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("top.txt", "hello")
            z.writestr("dir/in.txt", "inner")
            z.writestr("dir/empty/", "")
            z.writestr("../evil.txt", "no")

    def tearDown(self):
        self.dir.cleanup()

    def test_locate(self):
        self.assertEqual(archive.locate(self.zip), (self.zip, ""))
        self.assertEqual(archive.locate(os.path.join(self.zip, "dir", "in.txt")), (self.zip, "dir/in.txt"))
        self.assertIsNone(archive.locate(self.dir.name))
        self.assertIsNone(archive.inside(self.zip))
        self.assertIsNotNone(archive.inside(os.path.join(self.zip, "top.txt")))

    def test_list(self):
        self.assertEqual([(n, d) for n, d, _, _ in archive.list_dir(self.zip, "")],
                         [("dir", True), ("top.txt", False)])  # unsafe ../evil.txt left out
        self.assertEqual([n for n, *_ in archive.list_dir(self.zip, "dir")], ["empty", "in.txt"])
        with self.assertRaises(OSError):
            archive.list_dir(self.zip, "nope")

    def test_extract_folder_and_file(self):
        out = os.path.join(self.dir.name, "out")
        archive.extract(os.path.join(self.zip, "dir"), out)
        self.assertEqual(sorted(os.listdir(out)), ["empty", "in.txt"])
        archive.extract(os.path.join(self.zip, "top.txt"), out + ".txt")
        with open(out + ".txt") as f:
            self.assertEqual(f.read(), "hello")
        self.assertEqual(archive.read(os.path.join(self.zip, "dir", "in.txt"), 3), b"inn")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.dir.name), "evil.txt")))


if __name__ == "__main__":
    unittest.main()

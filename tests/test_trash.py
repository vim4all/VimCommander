import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vimcommander import config, trash  # noqa: E402


def write(path: str, text: str = "x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class FreedesktopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_parse_trashinfo(self):
        path, when = trash.parse_trashinfo(
            "[Trash Info]\nPath=/home/me/My%20Docs/r%C3%A9sum%C3%A9.txt\nDeletionDate=2026-10-24T13:05:00\n")
        self.assertEqual(path, "/home/me/My Docs/résumé.txt")
        self.assertEqual(when, time.mktime((2026, 10, 24, 13, 5, 0, 0, 0, -1)))
        self.assertEqual(trash.parse_trashinfo("[Other]\nPath=/x\n"), ("", 0.0))

    def test_read_trash_dir(self):
        home = os.path.join(self.tmp, "Trash")
        write(os.path.join(home, "files", "a.txt"))
        os.makedirs(os.path.join(home, "files", "pics"))
        write(os.path.join(home, "info", "a.txt.trashinfo"),
              "[Trash Info]\nPath=/data/a.txt\nDeletionDate=2026-10-01T10:00:00\n")
        write(os.path.join(home, "info", "pics.trashinfo"),
              "[Trash Info]\nPath=/data/pics\nDeletionDate=2026-10-02T10:00:00\n")
        write(os.path.join(home, "info", "gone.trashinfo"), "[Trash Info]\nPath=/data/gone\n")
        found = sorted(trash.read_trash_dir(home), key=lambda i: i.name)
        data = os.path.normpath("/data")  # \data on Windows
        self.assertEqual([(i.name, i.folder, i.is_dir) for i in found],
                         [("a.txt", data, False), ("pics", data, True)])
        self.assertIsNone(found[1].size())
        self.assertEqual(found[0].size(), 1)

    def test_drive_trash_relative_paths(self):
        top = os.path.join(self.tmp, "drive")
        for folder in (os.path.join(top, ".Trash-1000"), os.path.join(top, ".Trash", "1000")):
            write(os.path.join(folder, "files", "b.txt"))
            write(os.path.join(folder, "info", "b.txt.trashinfo"), "[Trash Info]\nPath=work/b.txt\n")
            item, = trash.read_trash_dir(folder)
            self.assertEqual(item.original, os.path.join(top, "work", "b.txt"))

    def test_trash_dirs(self):
        env = {"XDG_DATA_HOME": os.path.join(self.tmp, "data")}
        uid = os.getuid() if hasattr(os, "getuid") else 0
        os.makedirs(os.path.join(self.tmp, "data", "Trash", "info"))
        os.makedirs(os.path.join(self.tmp, "usb", f".Trash-{uid}", "info"))
        dirs = trash.trash_dirs(env, [os.path.join(self.tmp, "usb"), os.path.join(self.tmp, "none")])
        self.assertEqual(dirs, [os.path.join(self.tmp, "data", "Trash"),
                                os.path.join(self.tmp, "usb", f".Trash-{uid}")])

    def test_restore_and_purge(self):
        home = os.path.join(self.tmp, "Trash")
        write(os.path.join(home, "files", "a.txt"), "hello")
        write(os.path.join(home, "info", "a.txt.trashinfo"), "[Trash Info]\nPath=/x/a.txt\n")
        dest = os.path.join(self.tmp, "back", "a.txt")
        trash.restore(os.path.join(home, "files", "a.txt"), dest)
        with open(dest, encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello")
        self.assertEqual(os.listdir(os.path.join(home, "info")), [])
        with self.assertRaises(OSError):  # no longer there
            trash.restore(os.path.join(home, "files", "a.txt"), dest + "2")

        write(os.path.join(home, "files", "d", "f.txt"))
        write(os.path.join(home, "info", "d.trashinfo"), "[Trash Info]\nPath=/x/d\n")
        write(os.path.join(home, "files", "c.txt"))
        with self.assertRaises(OSError):  # the place is taken
            trash.restore(os.path.join(home, "files", "c.txt"), dest)
        trash.purge(os.path.join(home, "files", "d"))
        self.assertEqual(os.listdir(os.path.join(home, "files")), ["c.txt"])
        self.assertEqual(os.listdir(os.path.join(home, "info")), [])


class RecycleBinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_index_versions(self):
        when = 1792900000.0
        self.assertEqual(trash.parse_index(trash.make_index(r"D:\Pr\notes.md", when)), (r"D:\Pr\notes.md", when))
        import struct
        raw = r"C:\old.txt".encode("utf-16-le").ljust(520, b"\0")
        v1 = struct.pack("<qqq", 1, 10, int(when * 1e7) + trash.FILETIME_EPOCH) + raw
        self.assertEqual(trash.parse_index(v1), (r"C:\old.txt", when))
        with self.assertRaises(ValueError):
            trash.parse_index(b"\x03" + b"\0" * 30)

    def test_read_recycle_bin(self):
        write(os.path.join(self.tmp, "$RAB12CD.txt"), "data")
        with open(os.path.join(self.tmp, "$IAB12CD.txt"), "wb") as f:
            f.write(trash.make_index(r"D:\work\report.txt", 1792900000.0))
        with open(os.path.join(self.tmp, "$IGONE.txt"), "wb") as f:  # its $R file is gone
            f.write(trash.make_index(r"D:\gone.txt", 1.0))
        item, = trash.read_recycle_bin(self.tmp)
        self.assertEqual(item.original, r"D:\work\report.txt")
        self.assertEqual(item.path, os.path.join(self.tmp, "$RAB12CD.txt"))
        self.assertEqual(trash.info_path(item.path), os.path.join(self.tmp, "$IAB12CD.txt"))
        trash.purge(item.path)
        self.assertEqual(os.listdir(self.tmp), ["$IGONE.txt"])


class UndoFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        old = os.environ.get("VIMCOMMANDER_CONFIG")
        os.environ["VIMCOMMANDER_CONFIG"] = os.path.join(self.tmp, "config.json")
        self.addCleanup(lambda: os.environ.__setitem__("VIMCOMMANDER_CONFIG", old) if old is not None
                        else os.environ.pop("VIMCOMMANDER_CONFIG", None))

    def test_round_trip(self):
        self.assertEqual(config.load_undo(), ([], []))
        undo = [("trash of 1 item(s)", [("trashed", "/a/b.txt", "/t/files/b.txt")], 100.0),
                ("rename of x", [("renamed", "/a/x", "/a/y"), ("created", "/a/z")], 200.0)]
        redo = [("copy of 1 item(s)", [("moved", "/a/1", "/b/1")], 300.0)]
        config.save_undo(undo, redo)
        self.assertEqual(config.undo_path(), os.path.join(self.tmp, "undo.json"))
        self.assertEqual(config.load_undo(), (undo, redo))

    def test_bad_entries_dropped(self):
        with open(config.undo_path(), "w", encoding="utf-8") as f:
            f.write('{"undo": [{"what": "ok", "records": [["created", "/a"], ["created"], ["deleted", "/b"],'
                    ' ["moved", "/a", 3]]}, {"what": "empty", "records": []}, "junk"], "redo": 5}')
        self.assertEqual(config.load_undo(), ([("ok", [("created", "/a")], 0.0)], []))
        with open(config.undo_path(), "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertEqual(config.load_undo(), ([], []))


class QtTrashTest(unittest.TestCase):
    """The real trash (Qt's moveToTrash), in a temporary XDG_DATA_HOME."""

    def setUp(self):
        if sys.platform != "linux":
            self.skipTest("freedesktop.org trash only")
        from PySide6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, "data")
        os.makedirs(os.environ["XDG_DATA_HOME"])
        self.addCleanup(lambda: os.environ.__setitem__("XDG_DATA_HOME", old) if old is not None
                        else os.environ.pop("XDG_DATA_HOME", None))

    def test_trash_restore_undo(self):
        from vimcommander.fileops import Job
        path = write(os.path.join(self.tmp, "work", "notes.txt"), "keep me")
        job = Job("trash", [path])
        job._run()
        if os.path.exists(path):
            self.skipTest("this system can't trash files here")
        item, = trash.read_trash_dir(trash.home_trash(os.environ))
        self.assertEqual(item.original, path)

        job = Job("restore", [(item.path, item.original)])
        job._run()
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(job.undo, [("created", path)])
        self.assertEqual(trash.read_trash_dir(trash.home_trash(os.environ)), [])

        job = Job("undo", job.undo)  # u after restoring: back to the trash
        job._run()
        self.assertFalse(os.path.exists(path))
        item, = trash.read_trash_dir(trash.home_trash(os.environ))
        Job("purge", [item.path])._run()
        self.assertEqual(trash.read_trash_dir(trash.home_trash(os.environ)), [])


if __name__ == "__main__":
    unittest.main()

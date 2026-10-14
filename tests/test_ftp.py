"""FTP folders: ftpfs against a local pyftpdlib server (skipped without it)."""

import os
import stat
import tempfile
import threading
import time
import unittest
from datetime import datetime

from vimcommander import ftpfs, remote

try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer
except ImportError:  # a test-only package (requirements-build.txt)
    ThreadedFTPServer = None


class ParseListTest(unittest.TestCase):
    NOW = datetime(2026, 10, 14, 12, 0)

    def test_unix(self):
        a = ftpfs.parse_list_line("-rw-r--r--   1 bob  staff   1234 Mar  5 09:30 my file.txt", self.NOW)
        self.assertEqual((a.filename, a.st_size), ("my file.txt", 1234))
        self.assertFalse(stat.S_ISDIR(a.st_mode))
        self.assertEqual(datetime.fromtimestamp(a.st_mtime), datetime(2026, 3, 5, 9, 30))

    def test_unix_folder_old_year_and_link(self):
        a = ftpfs.parse_list_line("drwxr-xr-x 2 0 0 4096 Dec 31 2019 old", self.NOW)
        self.assertTrue(stat.S_ISDIR(a.st_mode))
        self.assertEqual(datetime.fromtimestamp(a.st_mtime).year, 2019)
        self.assertEqual(ftpfs.parse_list_line("lrwxrwxrwx 1 0 0 7 Jan 1 2020 l -> target", self.NOW).filename, "l")

    def test_future_date_is_last_year(self):
        a = ftpfs.parse_list_line("-rw-r--r-- 1 u g 1 Dec 20 10:00 x", self.NOW)
        self.assertEqual(datetime.fromtimestamp(a.st_mtime).year, 2025)

    def test_windows(self):
        a = ftpfs.parse_list_line("10-14-26  03:05PM       <DIR>          Some Folder")
        self.assertTrue(stat.S_ISDIR(a.st_mode))
        self.assertEqual(a.filename, "Some Folder")
        b = ftpfs.parse_list_line("01-02-2024  09:00AM               42 a b.txt")
        self.assertEqual((b.filename, b.st_size), ("a b.txt", 42))
        self.assertEqual(datetime.fromtimestamp(b.st_mtime), datetime(2024, 1, 2, 9, 0))

    def test_junk(self):
        self.assertIsNone(ftpfs.parse_list_line("total 12"))

    def test_urls(self):
        loc = remote.parse("ftp://bob@example.com:2121/pub/x")
        self.assertEqual((loc.scheme, loc.user, loc.port, loc.path), ("ftp", "bob", 2121, "/pub/x"))
        self.assertTrue(loc.is_ftp)
        self.assertEqual(loc.url("/a"), "ftp://bob@example.com:2121/a")
        self.assertEqual(remote.join("ftps://h/a", "b"), "ftps://h/a/b")
        self.assertNotEqual(loc.key, remote.parse("sftp://bob@example.com:2121/").key)
        self.assertEqual(remote.parse("sftp://h/x").url(), "sftp://h/x")


@unittest.skipIf(ThreadedFTPServer is None, "pyftpdlib is not installed")
class FtpServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = cls.tmp.name
        authorizer = DummyAuthorizer()
        authorizer.add_user("bob", "pw", cls.root, perm="elradfmwMT")
        authorizer.add_anonymous(cls.root)
        handler = type("Handler", (FTPHandler,), {"authorizer": authorizer, "banner": "test"})
        import logging
        from pyftpdlib.log import config_logging
        config_logging(level=logging.ERROR)
        cls.server = ThreadedFTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.address[1]
        threading.Thread(target=cls.server.serve_forever, kwargs={"timeout": 0.2}, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.close_all()
        remote.REMOTE.close_all()
        cls.tmp.cleanup()

    def client(self) -> ftpfs.FtpClient:
        return ftpfs.FtpClient("127.0.0.1", self.port, "bob", "pw")

    def test_list_stat_and_files(self):
        os.makedirs(os.path.join(self.root, "dir"), exist_ok=True)
        with open(os.path.join(self.root, "dir", "a.txt"), "wb") as f:
            f.write(b"hello")
        c = self.client()
        try:
            names = {a.filename: a for a in c.listdir_attr("/dir")}
            self.assertEqual(names["a.txt"].st_size, 5)
            self.assertTrue(stat.S_ISDIR(c.stat("/dir").st_mode))
            self.assertEqual(c.stat("/dir/a.txt").st_size, 5)
            with self.assertRaises(FileNotFoundError):
                c.stat("/dir/none")
            with c.open("/dir/b.bin", "wb") as f:
                f.write(b"\0\1" * 1000)
            with c.open("/dir/b.bin", "rb") as f:
                self.assertEqual(f.read(), b"\0\1" * 1000)
            with c.open("/dir/b.bin", "rb") as f:  # reading only the start, as the preview does
                self.assertEqual(f.read(3), b"\0\1\0")
            self.assertEqual(c.normalize("."), "/")  # the connection still works
            c.utime("/dir/b.bin", (0, 1_000_000_000))
            self.assertEqual(int(c.stat("/dir/b.bin").st_mtime), 1_000_000_000)
            c.rename("/dir/b.bin", "/dir/c.bin")
            c.mkdir("/dir/sub")
            c.rmdir("/dir/sub")
            c.remove("/dir/c.bin")
            self.assertEqual(sorted(c.listdir("/dir")), ["a.txt"])
        finally:
            c.close()

    def test_list_fallback_without_mlsd(self):
        c = self.client()
        try:
            c.mlsd = False
            os.makedirs(os.path.join(self.root, "f2"), exist_ok=True)
            open(os.path.join(self.root, "f2", "x y.txt"), "w").close()
            self.assertEqual([a.filename for a in c.listdir_attr("/f2")], ["x y.txt"])
            self.assertFalse(stat.S_ISDIR(c.stat("/f2/x y.txt").st_mode))
        finally:
            c.close()

    def test_copy_tree_and_remote_listing(self):
        src = tempfile.mkdtemp(dir=self.root)
        os.makedirs(os.path.join(src, "deep"))
        with open(os.path.join(src, "deep", "f.txt"), "w") as f:
            f.write("data")
        os.utime(os.path.join(src, "deep", "f.txt"), (1_500_000_000, 1_500_000_000))
        old_ask = remote.Remote.ask_password
        remote.Remote.ask_password = staticmethod(lambda prompt: "pw")
        try:
            url = f"ftp://bob@127.0.0.1:{self.port}/~"
            home = remote.REMOTE.resolve(url)
            self.assertEqual(remote.parse(home).path, "/")
            conn = remote.REMOTE.connection(remote.parse(home))
            channel = conn.open_sftp()
            try:
                remote.copy_tree(remote.Endpoint(None), src, remote.Endpoint(channel), "/copied",
                                 lambda n: None, lambda: None)
            finally:
                channel.close()
            items = remote.REMOTE.list_dir(remote.join(home, "copied"))
            self.assertEqual([(i[0], i[1]) for i in items], [("deep", True)])
            copied = os.path.join(self.root, "copied", "deep", "f.txt")
            with open(copied) as f:
                self.assertEqual(f.read(), "data")
            self.assertEqual(int(os.stat(copied).st_mtime), 1_500_000_000)
            self.assertEqual(remote.REMOTE.read_head(remote.join(home, "copied/deep/f.txt"), 2), b"da")
        finally:
            remote.Remote.ask_password = old_ask

    def test_anonymous_and_wrong_password(self):
        user, password = remote.ftp_login("127.0.0.1", "")
        c = ftpfs.FtpClient("127.0.0.1", self.port, user, password)
        c.close()
        with self.assertRaises(PermissionError):
            ftpfs.FtpClient("127.0.0.1", self.port, "bob", "wrong")


if __name__ == "__main__":
    unittest.main()

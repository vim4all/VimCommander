import os
import tempfile
import time
import unittest

from vimcommander import remote

try:
    from tests.sshserver import TestServer
except ImportError:
    from sshserver import TestServer


class UrlTest(unittest.TestCase):
    def test_parse(self):
        loc = remote.parse("sftp://me@box:2222/srv/www/")
        self.assertEqual((loc.user, loc.host, loc.port, loc.path), ("me", "box", 2222, "/srv/www"))
        self.assertEqual(loc.key, "me@box:2222")
        self.assertEqual(remote.parse("sftp://box").path, "/")
        self.assertEqual(remote.parse("sftp://box/a\\b").path, "/a/b")  # Windows os.path.join
        self.assertIsNone(remote.parse("/home/x"))
        self.assertEqual(remote.from_spec("me@box:/etc"), "sftp://me@box/etc")
        self.assertEqual(remote.from_spec("box"), "sftp://box/~")
        self.assertEqual(remote.from_spec("box:src"), "sftp://box/~/src")
        self.assertEqual(remote.from_spec("box:2222"), "sftp://box:2222/~")
        self.assertEqual(remote.join("sftp://box/a", "b c"), "sftp://box/a/b c")
        self.assertEqual(remote.dirname("sftp://box/a/b"), "sftp://box/a")
        self.assertEqual(remote.dirname("sftp://box/"), "sftp://box/")
        self.assertEqual(remote.basename("sftp://box/a/b.txt"), "b.txt")


class SftpTest(unittest.TestCase):
    """Against a local test SSH server (tests/sshserver.py)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.dir.name, "home")
        os.makedirs(os.path.join(self.home, "docs"))
        with open(os.path.join(self.home, "docs", "a.txt"), "w") as f:
            f.write("remote text")
        self.server = TestServer(self.home)
        self.old = remote.Remote.ask_password, remote.Remote.ask_host
        remote.Remote.ask_password = staticmethod(lambda prompt: "pw")
        remote.Remote.ask_host = staticmethod(lambda host, kind, fp: True)
        remote.REMOTE.known_hosts = os.path.join(self.dir.name, "known_hosts")
        self.base = f"sftp://tester@127.0.0.1:{self.server.port}"

    def tearDown(self):
        remote.REMOTE.close_all()
        remote.Remote.ask_password, remote.Remote.ask_host = self.old
        self.server.close()
        self.dir.cleanup()

    def url(self, local: str) -> str:
        return self.base + local.replace("\\", "/") if not local.startswith("/") else self.base + local

    def test_list_read_and_change(self):
        home = remote.REMOTE.resolve(self.base + "/~")
        self.assertEqual(remote.parse(home).path.lstrip("/"), self.home.replace("\\", "/").lstrip("/"))
        self.assertIn(("docs", True), [(n, d) for n, d, _, _ in remote.REMOTE.list_dir(home)])
        a = remote.join(home, "docs/a.txt")
        self.assertEqual(remote.REMOTE.read_head(a, 6), b"remote")
        remote.REMOTE.mkdir(remote.join(home, "x/y"), parents=True)
        remote.REMOTE.touch(remote.join(home, "x/y/new.txt"))
        remote.REMOTE.rename(remote.join(home, "x/y/new.txt"), remote.join(home, "x/y/old.txt"))
        self.assertTrue(os.path.exists(os.path.join(self.home, "x", "y", "old.txt")))
        self.assertTrue(os.path.exists(remote.REMOTE.known_hosts))  # the host key was saved

    def test_copy_both_ways(self):
        conn = remote.REMOTE.connection(remote.parse(self.base + "/"))
        sftp = conn.open_sftp()
        local = remote.Endpoint(None)
        far = remote.Endpoint(sftp)
        src = os.path.join(self.dir.name, "up")
        os.makedirs(os.path.join(src, "sub"))
        with open(os.path.join(src, "sub", "f.bin"), "wb") as f:
            f.write(os.urandom(300_000))
        os.utime(os.path.join(src, "sub", "f.bin"), (1_600_000_000, 1_600_000_000))
        sizes = []
        dest = (self.home + "/up").replace("\\", "/")
        dest = dest if dest.startswith("/") else "/" + dest  # a Windows server: /D:/...
        remote.copy_tree(local, src, far, dest, sizes.append, lambda: None)
        self.assertEqual(sum(sizes), 300_000)
        copied = os.path.join(self.home, "up", "sub", "f.bin")
        with open(copied, "rb") as a, open(os.path.join(src, "sub", "f.bin"), "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertEqual(int(os.path.getmtime(copied)), 1_600_000_000)
        back = os.path.join(self.dir.name, "down")
        remote.copy_tree(far, dest, local, back, lambda n: None, lambda: None)
        self.assertTrue(os.path.exists(os.path.join(back, "sub", "f.bin")))
        self.assertEqual(remote.walk_size(sftp, dest), (300_000, 1))
        remote.remove_tree(sftp, dest)  # what Del does on a server
        self.assertFalse(os.path.exists(os.path.join(self.home, "up")))
        sftp.close()

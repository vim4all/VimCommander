import io
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from vimcommander import share, terminal


def fetch(url, data=None, method="GET"):
    return urllib.request.urlopen(urllib.request.Request(url, data=data, method=method), timeout=5).read()


class ShareTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        r = self.root = self.dir.name
        os.makedirs(os.path.join(r, "folder", "sub"))
        with open(os.path.join(r, "a.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(r, "folder", "sub", "b.txt"), "w") as f:
            f.write("inner")
        self.inbox = os.path.join(r, "inbox")
        os.mkdir(self.inbox)
        self.events = []
        self.server = share.ShareServer([os.path.join(r, "a.txt"), os.path.join(r, "folder")], self.inbox,
                                        on_event=self.events.append)
        self.url = self.server.url("127.0.0.1")

    def tearDown(self):
        self.server.stop()
        self.dir.cleanup()

    def test_download_and_upload(self):
        page = fetch(self.url).decode()
        self.assertIn("a.txt", page)
        self.assertIn("folder", page)
        self.assertEqual(fetch(self.url + "f/0/a.txt"), b"hello")
        z = zipfile.ZipFile(io.BytesIO(fetch(self.url + "z/1/folder.zip")))
        self.assertEqual(z.read("folder/sub/b.txt"), b"inner")
        name = urllib.parse.quote("../фото:1.jpg", safe="")  # like the page's encodeURIComponent
        fetch(self.url + "u/" + name, b"x" * 5000, "PUT")
        fetch(self.url + "u/" + name, b"y", "PUT")
        self.assertEqual(sorted(os.listdir(self.inbox)), ["фото_1 (1).jpg", "фото_1.jpg"])
        self.assertTrue(any(e.startswith("Received") for e in self.events))

    def test_token_and_bounds(self):
        bad = self.url.replace(self.server.token, "nope")
        for url in (bad, self.url + "f/9/x", self.url + "f/../../etc"):
            with self.assertRaises(urllib.error.HTTPError):
                fetch(url)

    def test_names(self):
        self.assertEqual(share.safe_name('C:\\x\\a<b>.txt'), "a_b_.txt")
        self.assertEqual(share.safe_name(".."), "upload")


class TerminalTest(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(terminal.shell_kind("C:/Windows/System32/cmd.exe"), "cmd")
        self.assertEqual(terminal.shell_kind("pwsh"), "powershell")
        self.assertEqual(terminal.shell_kind("/bin/zsh"), "posix")
        self.assertEqual(terminal.cd_command("posix", "/a/it's"), "cd -- '/a/it'\\''s'")
        self.assertEqual(terminal.cd_command("powershell", "D:/it's"), "Set-Location -LiteralPath 'D:/it''s'")
        self.assertIn("${PWD}", terminal.mark_command("posix"))
        self.assertEqual(terminal.clean("\x1b[31mred\x1b[0m\r\nprogress 10%\rprogress 99%\n"),
                         "red\nprogress 99%\n")

import os
import tempfile
import unittest
from unittest import mock

from vimcommander import remote, termshell, wsl
from vimcommander.terminal import cd_command, mark_command, shell_kind

try:
    import pyte  # noqa: F401
    from vimcommander.termview import TermScreen
except ImportError:
    TermScreen = None


class WslPathTest(unittest.TestCase):
    def test_to_linux(self):
        self.assertEqual(wsl.to_linux(r"D:\Pr\Ws"), "/mnt/d/Pr/Ws")
        self.assertEqual(wsl.to_linux("C:\\"), "/mnt/c")
        self.assertEqual(wsl.to_linux(r"\\wsl.localhost\Ubuntu\home\me"), "/home/me")
        self.assertEqual(wsl.to_linux(r"\\wsl$\Ubuntu\home\me", "Ubuntu"), "/home/me")
        self.assertIsNone(wsl.to_linux(r"\\wsl.localhost\Debian\home", "Ubuntu"))
        self.assertIsNone(wsl.to_linux(r"\\server\share\x"))

    def test_to_windows(self):
        self.assertEqual(wsl.to_windows("/mnt/d/Pr/Ws", "Ubuntu"), r"D:\Pr\Ws")
        self.assertEqual(wsl.to_windows("/mnt/c", ""), "C:\\")
        self.assertEqual(wsl.to_windows("/home/me", "Ubuntu"), r"\\wsl.localhost\Ubuntu\home\me")
        self.assertIsNone(wsl.to_windows("/home/me", ""))

    def test_specs_and_list(self):
        self.assertTrue(wsl.is_wsl("wsl") and wsl.is_wsl("wsl:Ubuntu") and wsl.is_wsl("wsl.exe -d Debian"))
        self.assertFalse(wsl.is_wsl("pwsh"))
        self.assertEqual((wsl.distro_of("wsl:Ubuntu"), wsl.distro_of("wsl.exe -d Debian"), wsl.distro_of("wsl")),
                         ("Ubuntu", "Debian", ""))
        self.assertEqual(wsl.parse_list("Ubuntu\r\ndocker-desktop\r\nDebian\r\n".encode("utf-16-le")),
                         ["Ubuntu", "Debian"])


class ShellTest(unittest.TestCase):
    def test_commands(self):
        sh = termshell.shell_command("wsl:Ubuntu", r"D:\Pr")
        self.assertEqual((sh.kind, sh.distro), ("wsl", "Ubuntu"))
        self.assertEqual(sh.argv[:3], ["wsl.exe", "-d", "Ubuntu"])
        self.assertIn("PROMPT_COMMAND/u", sh.env["WSLENV"])
        self.assertEqual(termshell.shell_command("pwsh", "/").kind, "powershell")
        self.assertIn("-NoExit", termshell.shell_command("pwsh", "/").argv)
        self.assertEqual(termshell.shell_command("cmd", "/").kind, "cmd")
        bash = termshell.shell_command("/bin/bash", "/")
        self.assertEqual(bash.kind, "posix")
        self.assertIn("]7;file://", bash.env["PROMPT_COMMAND"])

    def test_osc7(self):
        text = "x\x1b]7;file://Ubuntu/mnt/d/Pr%20x\x07$ "
        m = termshell.OSC7.search(text)
        sh = termshell.Shell(["wsl.exe"], "wsl")
        self.assertEqual(termshell.osc7_folder(m.group(1), sh), ("D:\\Pr x", "Ubuntu"))
        sh = termshell.Shell(["bash"], "posix")
        with mock.patch.object(termshell, "WINDOWS", False):
            self.assertEqual(termshell.osc7_folder("file://host/home/me", sh), ("/home/me", "host"))
        with mock.patch.object(termshell, "WINDOWS", True):
            sh = termshell.Shell(["pwsh"], "powershell")
            self.assertEqual(termshell.osc7_folder("file://localhost/D:/Pr/Ws", sh)[0], r"D:\Pr\Ws")
            self.assertEqual(termshell.osc7_folder("file://localhost/D:\\Pr", sh)[0], r"D:\Pr")

    def test_cd_lines(self):
        self.assertEqual(termshell.cd_line(termshell.Shell([], "wsl", "Ubuntu"), r"D:\it's"), " cd -- '/mnt/d/it'\"'\"'s'\r")
        self.assertIsNone(termshell.cd_line(termshell.Shell([], "wsl", "Ubuntu"), r"\\nas\share"))
        self.assertEqual(termshell.cd_line(termshell.Shell([], "powershell"), r"D:\it's"),
                         " Set-Location -LiteralPath 'D:\\it''s'\r")
        self.assertEqual(termshell.cd_line(termshell.Shell([], "cmd"), r"D:\x y"), ' cd /d "D:\\x y"\r')

    def test_mount(self):
        loc = remote.Location("me", "box.lan", 2222, "/srv/data")
        line = termshell.mount_line(loc)
        self.assertIn("sshfs -p 2222 -o reconnect me@box.lan:/srv/data ~/mnt/box.lan", line)
        self.assertTrue(line.startswith("mkdir -p ~/mnt/box.lan") and line.endswith("cd ~/mnt/box.lan\r"))
        self.assertIn("fusermount -u ~/mnt/box.lan", termshell.umount_line(loc))

    def test_line_panel_wsl(self):
        self.assertEqual(shell_kind("wsl:Ubuntu"), "wsl")
        self.assertEqual(cd_command("wsl", r"D:\Pr"), "cd -- '/mnt/d/Pr'")
        self.assertIn("${WSL_DISTRO_NAME}|${PWD}", mark_command("wsl"))


@unittest.skipIf(TermScreen is None, "needs pyte")
class ScreenTest(unittest.TestCase):
    def screen(self):
        sent = []
        s = TermScreen(20, 5, sent.append)
        return s, pyte.Stream(s), sent

    def text(self, s, y):
        return "".join(s.buffer[y][x].data for x in range(s.columns)).rstrip()

    def test_alternate_screen(self):
        s, stream, _ = self.screen()
        stream.feed("shell line\r\n$ ")
        stream.feed("\x1b[?1049h")
        self.assertTrue(s.in_alt)
        self.assertEqual((self.text(s, 0), s.cursor.y), ("", 0))  # cleared, cursor home
        stream.feed("vim text")
        stream.feed("\x1b[?1049l")
        self.assertFalse(s.in_alt)
        self.assertEqual((self.text(s, 0), self.text(s, 1), s.cursor.x), ("shell line", "$", 2))

    def test_history_and_private_sgr(self):
        s, stream, sent = self.screen()
        for i in range(12):
            stream.feed(f"line {i}\r\n")
        self.assertEqual(len(s.history.top), 8)
        stream.feed("\x1b[>4;2m\x1b[31mred")  # vim's modifyOtherKeys must not break the colors
        self.assertEqual(s.buffer[s.cursor.y][0].fg, "red")
        stream.feed("\x1b[6n")  # where is the cursor? -> answered to the program
        self.assertTrue(sent and sent[-1].startswith("\x1b["))



@unittest.skipIf(os.name == "nt", "runs sh")
class QuickExitTest(unittest.TestCase):
    """A shell that ends right after starting keeps its words on screen until a key."""

    def test_error_stays_until_a_key(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QEvent, QEventLoop, Qt, QTimer
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QApplication
        from vimcommander.termview import TermView
        app = QApplication.instance() or QApplication([])
        term = TermView("sh -c 'echo no such distro; exit 3'", tempfile.gettempdir())
        term.resize(500, 300)
        exited = []
        term.exited.connect(lambda: exited.append(True))
        term.start()
        loop = QEventLoop()
        timer = QTimer(interval=20)
        timer.timeout.connect(lambda: term.ended and loop.quit())
        timer.start()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        timer.stop()
        screen = "\n".join("".join(term.vt.buffer[y][x].data for x in range(term.vt.columns))
                           for y in range(term.vt.lines))
        self.assertTrue(term.ended)
        self.assertIn("no such distro", screen)
        self.assertIn("exit code 3; press a key to close", screen)
        self.assertEqual(exited, [])
        app.sendEvent(term, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "))
        self.assertEqual(exited, [True])


if __name__ == "__main__":
    unittest.main()

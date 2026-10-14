"""The terminal panel (Ctrl-` or :term): a shell docked under the panes that
follows the active pane, and the pane follows it.

It is a shell reading commands line by line (PowerShell on Windows, bash or
your $SHELL elsewhere), not a full terminal emulator: commands and their output
work, programs that draw a full-screen interface (vim, htop) don't. After each
command the shell reports its folder with a marker line, which tells the panel
that the command is done and where the shell is now.
"""

import base64
import os
import re
import shutil
import sys

import shlex
import threading

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QDockWidget, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QVBoxLayout, QWidget)

from . import remote

WINDOWS = sys.platform == "win32"
MARK = "__VC_DONE__"
CMD_PROMPT = "__VC_PROMPT__"
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-Z\\-_]")
MARK_LINE = re.compile(re.escape(MARK) + r"(.*?)__\s*$")


def default_shell() -> str:
    if WINDOWS:
        return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
    shell = os.environ.get("SHELL", "")
    return shell if os.path.basename(shell) in ("bash", "zsh", "sh", "dash", "ksh") else (
        shutil.which("bash") or "/bin/sh")


def shell_kind(program: str) -> str:
    name = os.path.basename(program).lower()
    if name.startswith("cmd"):
        return "cmd"
    if name.startswith(("powershell", "pwsh")):
        return "powershell"
    return "posix"


def start_args(kind: str) -> list[str]:
    if kind == "cmd":
        return ["/Q", "/K", "chcp 65001>nul"]
    if kind == "powershell":
        return ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-"]
    return []  # posix shells read commands from stdin when it isn't a terminal


def setup_lines(kind: str) -> list[str]:
    if kind == "powershell":
        return ["[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8"]
    return []


def cd_command(kind: str, path: str) -> str:
    if kind == "cmd":
        return f'cd /d "{path}"'
    if kind == "powershell":
        return "Set-Location -LiteralPath '" + path.replace("'", "''") + "'"
    return "cd -- '" + path.replace("'", "'\\''") + "'"


def mark_command(kind: str) -> str:
    if kind == "cmd":
        return f"echo {MARK}%CD%__"
    if kind == "powershell":
        return f'Write-Output "{MARK}$((Get-Location).Path)__"'
    return f'echo "{MARK}${{PWD}}__"'  # braces: $PWD__ would be a variable named PWD__


def clean(text: str) -> str:
    """Output without color codes, carriage returns or cmd's prompts."""
    text = ANSI.sub("", text).replace("\r\n", "\n").replace(CMD_PROMPT, "")
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))


class _Pipe(QObject):
    """Output of a remote shell, from its reader thread to the GUI thread."""
    data = Signal(bytes)
    closed = Signal()


class TerminalInput(QLineEdit):
    history_step = Signal(int)
    interrupt = Signal()
    leave = Signal()
    clear_screen = Signal()

    def keyPressEvent(self, e):
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if e.key() == Qt.Key.Key_Up:
            self.history_step.emit(-1)
        elif e.key() == Qt.Key.Key_Down:
            self.history_step.emit(1)
        elif e.key() == Qt.Key.Key_Escape:
            self.leave.emit()
        elif ctrl and e.key() == Qt.Key.Key_C and not self.hasSelectedText():
            self.interrupt.emit()
        elif ctrl and e.key() == Qt.Key.Key_L:
            self.clear_screen.emit()
        else:
            super().keyPressEvent(e)


class Terminal(QDockWidget):
    cwd_changed = Signal(str)  # the shell is in another folder now (after cd)
    leave = Signal()  # Esc: back to the pane

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Terminal", parent)
        self.setObjectName("terminal")
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                         | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        self.output.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.prompt = QLabel()
        self.input = TerminalInput()
        self.input.returnPressed.connect(self._enter)
        self.input.history_step.connect(self._history)
        self.input.interrupt.connect(self.interrupt)
        self.input.leave.connect(self.leave.emit)
        self.input.clear_screen.connect(self.output.clear)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.prompt)
        row.addWidget(self.input, 1)
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        layout.addWidget(self.output, 1)
        layout.addLayout(row)
        self.setWidget(box)
        self.setFocusProxy(self.input)
        self.program = ""
        self.kind = "posix"
        self.cwd = ""
        self.busy = False  # a command is running (its marker hasn't come back)
        self.process: QProcess | None = None
        self.channel = None  # a paramiko channel when the shell runs on an SSH server
        self.location: remote.Location | None = None  # that server
        self.history: list[str] = []
        self._hist_i = 0
        self._partial = ""

    def set_font(self, font: QFont) -> None:
        for w in (self.output, self.input, self.prompt):
            w.setFont(font)

    # The shell process

    def start(self, program: str, cwd: str) -> None:
        self.stop()
        self.program = program or self.program or default_shell()
        if remote.is_remote(cwd):
            self._start_remote(cwd)
            return
        self.location = None
        self.kind = shell_kind(self.program)
        self.cwd = cwd
        p = self.process = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.setWorkingDirectory(cwd)
        env = QProcessEnvironment.systemEnvironment()
        for key, value in (("PYTHONUNBUFFERED", "1"), ("PYTHONIOENCODING", "utf-8"), ("TERM", "dumb"),
                           ("NO_COLOR", "1"), ("PROMPT", CMD_PROMPT), ("GIT_PAGER", "cat"), ("PAGER", "cat")):
            env.insert(key, value)
        p.setProcessEnvironment(env)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._finished)
        p.errorOccurred.connect(self._error)
        p.start(self.program, start_args(self.kind))
        if not p.waitForStarted(5000):  # lines written before the start would be lost
            return
        for line in setup_lines(self.kind):
            self._write(line)
        self._write(mark_command(self.kind))
        self.busy = True
        self._show_prompt()
        self._print(f"{os.path.basename(self.program)} in {cwd}   (Esc = back to the pane, "
                    "Ctrl-C = stop the running command, Ctrl-L = clear)\n", dim=True)

    def _start_remote(self, url: str) -> None:
        """A shell on the SSH server of `url`, in that folder, on the pane's connection."""
        loc = remote.parse(url)
        self.kind, self.cwd, self.location = "posix", url, loc
        if loc.is_ftp:
            self._print("FTP has no shell: the terminal works in SSH folders (:ssh) and on this computer.\n",
                        dim=True)
            return
        try:
            conn = remote.REMOTE.connection(loc)
            channel = conn.client.get_transport().open_session()
            channel.set_combine_stderr(True)
            channel.exec_command(f"cd {shlex.quote(loc.path)} 2>/dev/null; "
                                 "TERM=dumb PAGER=cat GIT_PAGER=cat exec ${SHELL:-/bin/sh} -s")
        except Exception as e:
            self._print(f"Could not start a shell on {loc.host}: {e}\n", dim=True)
            return
        self.channel = channel
        pipe = self._pipe = _Pipe()
        pipe.data.connect(self._data)
        pipe.closed.connect(self._finished)

        def reader():
            while True:
                try:
                    data = channel.recv(65536)
                except Exception:
                    data = b""
                if not data:
                    break
                pipe.data.emit(data)
            pipe.closed.emit()
        threading.Thread(target=reader, daemon=True).start()
        self._write(mark_command(self.kind))
        self.busy = True
        self._show_prompt()
        self._print(f"shell on {loc.key} in {loc.path}   (Esc = back to the pane, "
                    "Ctrl-C = stop the running command, Ctrl-L = clear)\n", dim=True)

    def stop(self) -> None:
        if self.channel is not None:
            channel, self.channel = self.channel, None
            self._pipe.closed.disconnect()
            try:
                channel.close()
            except Exception:
                pass
        if self.process:
            p, self.process = self.process, None
            p.finished.disconnect()
            p.kill()
            p.waitForFinished(2000)
        self.busy = False

    @property
    def running(self) -> bool:
        if self.channel is not None:
            return not self.channel.closed
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def _write(self, line: str, raw: bool = False) -> None:
        """Send a line to the shell. PowerShell reads its input in the old console code
        page, so a command with other characters goes in base64 and runs from there."""
        if self.channel is not None:
            try:
                self.channel.sendall((line + "\n").encode("utf-8"))
            except Exception:
                pass
            return
        if not self.process:
            return
        if self.kind == "powershell" and not raw and not line.isascii():
            encoded = base64.b64encode(line.encode("utf-8")).decode()
            line = ("Invoke-Expression ([Text.Encoding]::UTF8.GetString("
                    f"[Convert]::FromBase64String('{encoded}')))")
        self.process.write((line + "\n").encode("utf-8"))

    def _finished(self, *args) -> None:
        self.process = None
        self.channel = None
        self.busy = False
        self._print("\n[the shell ended; Enter starts a new one]\n", dim=True)
        self._show_prompt()

    def _error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._print(f"Could not start {self.program}: set programs.shell in the config\n", dim=True)
            self.process = None
            self.busy = False

    # Commands

    def run(self, line: str) -> None:
        if not self.running:
            self.start(self.program, self.cwd or os.path.expanduser("~"))
        if self.busy:  # the running command reads input, e.g. a y/n question
            self._print(line + "\n")
            self._write(line, raw=True)
            return
        self._print(f"{self._prompt_text()}{line}\n", bold=True)
        self._write(line)
        self._write(mark_command(self.kind))
        self.busy = True
        self._show_prompt()

    def follow(self, path: str) -> None:
        """The pane opened `path`: cd there too, unless a command is running. Between
        this computer and an SSH server (or two servers), the shell is swapped."""
        if not self.running or self.busy or not path or os.path.normcase(path) == os.path.normcase(self.cwd):
            return
        loc = remote.parse(path)
        if (loc.key if loc else None) != (self.location.key if self.location else None):
            self.start(self.program, path)
            return
        self.cwd = path
        self._write(cd_command(self.kind, loc.path if loc else path))
        self._write(mark_command(self.kind))
        self.busy = True
        self._show_prompt()

    def interrupt(self) -> None:
        """Ctrl-C: stop the running command (on Windows by restarting the shell)."""
        if not self.running or not self.busy:
            return
        if self.channel is not None:  # no signals over a plain channel: start a new shell
            self.start(self.program, self.cwd)
            self._print("^C\n", dim=True)
            return
        pid = self.process.processId()
        if WINDOWS:
            import subprocess
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            self.start(self.program, self.cwd)
        else:
            import subprocess
            subprocess.run(["pkill", "-INT", "-P", str(pid)], capture_output=True)
        self._print("^C\n", dim=True)

    # Output

    def _read(self) -> None:
        if self.process:
            self._data(self.process.readAllStandardOutput().data())

    def _data(self, data: bytes) -> None:
        text = self._partial + data.decode("utf-8", errors="replace")
        parts = text.split("\n")
        self._partial = parts.pop()  # an unfinished line waits for the rest…
        pieces = [p + "\n" for p in parts]
        if self._partial and not self._partial.startswith("_") and not self._partial.endswith("_"):
            pieces.append(self._partial)  # …unless it could be a marker: show "Continue? [y/n]" now
            self._partial = ""
        out = []
        for piece in pieces:
            line = clean(piece)
            if m := MARK_LINE.search(line):
                self._done(m.group(1).strip())
            elif not (self.kind == "cmd" and line == "\n"):  # cmd's empty line before each prompt
                out.append(line)
        if out:
            self._print("".join(out))

    def _done(self, cwd: str) -> None:
        self.busy = False
        if self.location and cwd:
            cwd = self.location.url(cwd)
        if cwd and os.path.normcase(cwd) != os.path.normcase(self.cwd):
            self.cwd = cwd
            self.cwd_changed.emit(cwd)
        elif cwd:
            self.cwd = cwd
        self._show_prompt()

    def _print(self, text: str, bold: bool = False, dim: bool = False) -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        if dim:
            fmt.setForeground(QColor(128, 128, 128))
        cursor.insertText(text, fmt)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def _prompt_text(self) -> str:
        if self.location:
            loc = remote.parse(self.cwd) or self.location
            return f"{loc.key}:{loc.path}$ "
        sign = "PS " if self.kind == "powershell" else ""
        home = os.path.expanduser("~")
        cwd = self.cwd
        if cwd == home or cwd.startswith(home + os.sep):
            cwd = "~" + cwd[len(home):]
        return f"{sign}{cwd}> " if not WINDOWS or self.kind != "posix" else f"{cwd}$ "

    def _show_prompt(self) -> None:
        self.prompt.setText("running…  (Ctrl-C stops)" if self.busy else self._prompt_text())

    # Input

    def _enter(self) -> None:
        line = self.input.text()
        self.input.clear()
        if line.strip() and not self.busy:
            if line in self.history:
                self.history.remove(line)
            self.history.append(line)
            del self.history[:-200]
        self._hist_i = len(self.history)
        if line.strip() in ("clear", "cls") and not self.busy:
            self.output.clear()
            return
        self.run(line)

    def _history(self, step: int) -> None:
        if not self.history:
            return
        self._hist_i = max(0, min(self._hist_i + step, len(self.history)))
        self.input.setText(self.history[self._hist_i] if self._hist_i < len(self.history) else "")

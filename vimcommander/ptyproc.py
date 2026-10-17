"""A program running in a pseudo-terminal, no Qt: what the terminal pane runs.

On Linux and macOS it is the standard pty module; on Windows it is ConPTY,
through pywinpty. Both give the same small interface: read() blocks until there
is output (text; None once the program has ended), write(), resize().
"""

import codecs
import os
import sys

WINDOWS = sys.platform == "win32"


class PtyProcess:
    def __init__(self, argv: list[str], cwd: str, env: dict[str, str], rows: int, cols: int):
        self.argv = argv
        if WINDOWS:
            try:
                from winpty import PtyProcess as WinPty
            except ImportError:
                raise OSError("the terminal pane needs pywinpty (pip install pywinpty)") from None
            self._win = WinPty.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
            self.pid = self._win.pid
            return
        import pty
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._status: int | None = None  # waitpid's status, once the program has ended
        pid, fd = pty.fork()
        if pid == 0:  # the child: become the program
            try:
                os.chdir(cwd)
            except OSError:
                pass
            try:
                os.execvpe(argv[0], argv, env)
            except OSError as e:
                os.write(2, f"Could not start {argv[0]}: {e.strerror}\r\n".encode())
            os._exit(127)
        self.pid, self.fd = pid, fd
        self.resize(rows, cols)

    def read(self) -> str | None:
        """Output so far ("" if only part of a character came), None once it ended."""
        if WINDOWS:
            try:
                text = self._win.read(65536)
            except EOFError:
                return None
            return text if text or self._win.isalive() else None
        try:
            data = os.read(self.fd, 65536)
        except OSError:  # EIO: the program ended and closed the terminal
            data = b""
        if not data:
            return None
        return self._decoder.decode(data)

    def write(self, text: str) -> None:
        try:
            if WINDOWS:
                self._win.write(text)
            else:
                data = text.encode("utf-8")
                while data:
                    n = os.write(self.fd, data)
                    data = data[n:]
        except (OSError, EOFError):
            pass

    def resize(self, rows: int, cols: int) -> None:
        rows, cols = max(rows, 2), max(cols, 2)
        try:
            if WINDOWS:
                self._win.setwinsize(rows, cols)
            else:
                import fcntl
                import struct
                import termios
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except (OSError, EOFError):
            pass

    @property
    def alive(self) -> bool:
        if WINDOWS:
            return bool(self._win.isalive())
        if self._status is not None:
            return False
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return False
        if pid == 0:
            return True
        self._status = status
        return False

    @property
    def exit_code(self) -> int | None:
        """The program's exit code once it has ended, None if not known (yet)."""
        if WINDOWS:
            try:
                return self._win.exitstatus
            except Exception:
                return None
        if self._status is None and self.alive:
            return None
        return os.waitstatus_to_exitcode(self._status) if self._status is not None else None

    def close(self) -> None:
        """End the program (if it still runs) and free the terminal."""
        if WINDOWS:
            try:
                self._win.terminate(force=True)
            except Exception:
                pass
            return
        import signal
        try:
            os.kill(self.pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            pass

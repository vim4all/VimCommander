"""zoxide integration: record visited folders and jump to them by keywords.

Uses the zoxide command (https://github.com/ajeetdsouza/zoxide), so the
database is shared with your shell's `z`. Pure Python, no Qt.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading

# No console window flashing up for each call in the windowed Windows build.
_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def find(configured: str = "") -> str | None:
    """The zoxide program: the configured path or name, else `zoxide` on PATH."""
    configured = os.path.expanduser(configured or "zoxide")
    return configured if os.path.isfile(configured) else shutil.which(configured)


def matches(path: str, keywords: list[str]) -> bool:
    """zoxide's rule: the keywords appear in order (ignoring case), and the
    last one is in the last part of the path."""
    if not keywords:
        return True
    text = path.casefold()
    pos = 0
    for word in keywords:
        found = text.find(word.casefold(), pos)
        if found < 0:
            return False
        pos = found + len(word)
    last = os.path.basename(path.rstrip("/\\")).casefold()
    return keywords[-1].casefold() in last


class Zoxide:
    def __init__(self, program: str):
        self.program = program
        self._adds: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._add_worker, daemon=True).start()

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([self.program, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=10,
                              creationflags=_FLAGS)

    def add(self, path: str) -> None:
        """Record a visit, in the background (like the shell hook does)."""
        self._adds.put(path)

    def _add_worker(self) -> None:
        while True:
            path = self._adds.get()
            try:
                self._run("add", "--", path)
            except (OSError, subprocess.SubprocessError):
                pass

    def query(self, keywords: list[str], exclude: str = "") -> str | None:
        """The best match for the keywords, or None. Raises OSError if zoxide fails to run."""
        args = ["query"] + (["--exclude", exclude] if exclude else []) + ["--", *keywords]
        try:
            result = self._run(*args)
        except subprocess.SubprocessError as e:
            raise OSError(str(e)) from e
        path = result.stdout.strip()
        return path if result.returncode == 0 and path else None

    def list(self) -> list[tuple[float, str]]:
        """All folders in the database as (score, path), best first."""
        try:
            result = self._run("query", "--list", "--score")
        except subprocess.SubprocessError as e:
            raise OSError(str(e)) from e
        out = []
        for line in result.stdout.splitlines():
            score, _, path = line.strip().partition(" ")
            try:
                out.append((float(score), path.strip()))
            except ValueError:
                continue
        return out

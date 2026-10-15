"""Result lists: searches whose results fill a pane (see pane.Results).

`find_paths()` is the :find search, pure Python. `Search` runs any such search
(a function that yields batches of paths) in a thread and reports the batches
on the GUI thread.
"""

import fnmatch
import os
import threading
import time
from collections.abc import Callable, Iterator

from PySide6.QtCore import QObject, Signal

from .graph import SKIP_DIRS


def find_paths(root: str, pattern: str, show_hidden: bool = False,
               stop: threading.Event | None = None, limit: int = 5000) -> Iterator[list[str]]:
    """Batches of files and folders below `root` whose name matches `pattern`
    (a shell pattern like *.log, or else any part of the name), ignoring case."""
    pattern = pattern.casefold()
    if not any(c in pattern for c in "*?["):
        pattern = f"*{pattern}*"
    batch, count = [], 0
    for folder, dirs, files in os.walk(root):
        if stop and stop.is_set():
            return
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and (show_hidden or not d.startswith(".")))
        for name in dirs + sorted(files):
            if (show_hidden or not name.startswith(".")) and fnmatch.fnmatchcase(name.casefold(), pattern):
                batch.append(os.path.join(folder, name))
                count += 1
                if count >= limit:
                    yield batch
                    return
        if len(batch) >= 100:
            yield batch
            batch = []
    if batch:
        yield batch


def branch_paths(root: str, show_hidden: bool = False,
                 stop: threading.Event | None = None, limit: int = 50000) -> Iterator[list[str]]:
    """Batches of every file below `root` (Total Commander's branch view)."""
    batch, count = [], 0
    for folder, dirs, files in os.walk(root):
        if stop and stop.is_set():
            return
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and (show_hidden or not d.startswith(".")))
        for name in sorted(files):
            if show_hidden or not name.startswith("."):
                batch.append(os.path.join(folder, name))
                count += 1
                if count >= limit:
                    yield batch
                    return
        if len(batch) >= 200:
            yield batch
            batch = []
    if batch:
        yield batch


def changed_files(root: str, days: float, show_hidden: bool = False,
                  stop: threading.Event | None = None, limit: int = 5000) -> Iterator[list]:
    """(path, note) for the files below `root` changed in the last `days` days, newest first
    (yielded once, at the end, so the order is right)."""
    since = time.time() - days * 86400
    found = []
    for folder, dirs, files in os.walk(root):
        if stop and stop.is_set():
            return
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (show_hidden or not d.startswith("."))]
        for name in files:
            if not show_hidden and name.startswith("."):
                continue
            path = os.path.join(folder, name)
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                continue
            if mtime >= since:
                found.append((mtime, path))
    found.sort(reverse=True)
    yield [(path, ago(mtime)) for mtime, path in found[:limit]]


def ago(mtime: float) -> str:
    """'5 min ago', '3 h ago', '2 days ago'."""
    s = max(0, time.time() - mtime)
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} days ago"


class Search(QObject):
    """Runs `producer(stop)` (which yields lists of paths) in a thread."""

    found = Signal(list)
    done = Signal(bool)  # True if it ran to the end, False if stopped

    def __init__(self, producer: Callable[[threading.Event], Iterator[list[str]]]):
        super().__init__()
        self.producer = producer
        self.stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            for batch in self.producer(self.stop):
                if self.stop.is_set():
                    break
                if batch:
                    self.found.emit(list(batch))
        except OSError:
            pass
        self.done.emit(not self.stop.is_set())

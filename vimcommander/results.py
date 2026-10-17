"""Result lists: searches whose results fill a pane (see pane.Results).

`find_paths()` is the :find search, pure Python. `Search` runs any such search
(a function that yields batches of paths) in a thread and reports the batches
on the GUI thread.
"""

import fnmatch
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from .graph import SKIP_DIRS


SIZE_UNITS = {"": 1, "b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
AGE_UNITS = {"h": 3600, "d": 86400, "w": 7 * 86400, "y": 365 * 86400}
_FILTER = re.compile(r"^(?:size([<>])(\d+(?:\.\d+)?)([bkmgt]?)|(newer|older):(\d+(?:\.\d+)?)([hdwy])|type:([fd]))$",
                     re.IGNORECASE)


@dataclass
class FindFilter:
    """:find's conditions besides the name: size, age, files or folders only."""
    min_size: int | None = None
    max_size: int | None = None
    newer: float | None = None  # changed after this time
    older: float | None = None  # changed before this time
    kind: str = ""  # "f" files, "d" folders, "" both

    def matches(self, path: str, is_dir: bool) -> bool:
        if self.kind and (self.kind == "d") != is_dir:
            return False
        if (self.min_size is not None or self.max_size is not None) and is_dir:
            return False  # a folder's size isn't known without adding it up
        if self.min_size is None and self.max_size is None and self.newer is None and self.older is None:
            return True
        try:
            st = os.stat(path)
        except OSError:
            return False
        return ((self.min_size is None or st.st_size > self.min_size)
                and (self.max_size is None or st.st_size < self.max_size)
                and (self.newer is None or st.st_mtime > self.newer)
                and (self.older is None or st.st_mtime < self.older))


def parse_find(text: str, now: float | None = None) -> tuple[str, FindFilter | None]:
    """':find *.log size>1M newer:7d' -> ("*.log", its filter). The words size>N / size<N
    (k, m, g units), newer:N / older:N (h, d, w, y) and type:f / type:d are conditions;
    the rest is the name pattern. ValueError for a condition that doesn't parse."""
    now = time.time() if now is None else now
    words, found = [], FindFilter()
    for word in text.split():
        m = _FILTER.match(word)
        if not m:
            if re.match(r"^(size[<>=]|newer:|older:|type:)", word, re.IGNORECASE):
                raise ValueError(f"not a condition: {word}   (size>1M, size<500k, newer:7d, older:2w, type:f, type:d)")
            words.append(word)
            continue
        if m.group(1):
            size = int(float(m.group(2)) * SIZE_UNITS[m.group(3).lower()])
            if m.group(1) == ">":
                found.min_size = size
            else:
                found.max_size = size
        elif m.group(4):
            when = now - float(m.group(5)) * AGE_UNITS[m.group(6).lower()]
            if m.group(4).lower() == "newer":
                found.newer = when
            else:
                found.older = when
        else:
            found.kind = m.group(7).lower()
    return " ".join(words), (found if found != FindFilter() else None)


def find_paths(root: str, pattern: str, show_hidden: bool = False,
               stop: threading.Event | None = None, limit: int = 5000,
               conditions: FindFilter | None = None) -> Iterator[list[str]]:
    """Batches of files and folders below `root` whose name matches `pattern`
    (a shell pattern like *.log, or else any part of the name; "" = any), ignoring
    case, and that meet `conditions`."""
    pattern = pattern.casefold()
    if not any(c in pattern for c in "*?["):
        pattern = f"*{pattern}*"
    batch, count = [], 0
    for folder, dirs, files in os.walk(root):
        if stop and stop.is_set():
            return
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and (show_hidden or not d.startswith(".")))
        for k, name in enumerate(dirs + sorted(files)):
            if (show_hidden or not name.startswith(".")) and fnmatch.fnmatchcase(name.casefold(), pattern):
                path = os.path.join(folder, name)
                if conditions and not conditions.matches(path, k < len(dirs)):
                    continue
                batch.append(path)
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

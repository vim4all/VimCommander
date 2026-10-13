"""Result lists: searches whose results fill a pane (see pane.Results).

`find_paths()` is the :find search, pure Python. `Search` runs any such search
(a function that yields batches of paths) in a thread and reports the batches
on the GUI thread.
"""

import fnmatch
import os
import threading
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

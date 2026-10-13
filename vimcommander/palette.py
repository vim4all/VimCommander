"""Fuzzy pickers: the command palette (Ctrl-Shift-p) and the file finder (Ctrl-p).

Type to filter, Up/Down (or Ctrl-j/k, Ctrl-n/p) to move, Enter to choose,
Shift-Enter for the alternative (open the file), Ctrl-t for a new tab, Ctrl-Enter
for all shown files as a result list (file finder), Esc to close.
"""

import math
import os
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QSplitter, QVBoxLayout, QWidget)

from . import fuzzy
from .graph import SKIP_DIRS
from .sizing import fit_window


class FuzzyDialog(QDialog):
    """A filter line over a list. Subclasses provide the items and what they show."""

    LIMIT = 300
    LIST_ALL = False  # Ctrl-Enter chooses all shown items ("all")

    def __init__(self, title: str, parent: QWidget, font: QFont | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.items: list[str] = []
        self.shown: list[str] = []
        self.chosen: str | None = None
        self.how = "enter"  # "enter", "alt" (Shift-Enter) or "tab" (Ctrl-t)
        self.query = QLineEdit()
        self.query.textChanged.connect(lambda _: self._timer.start())
        self.query.installEventFilter(self)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _: self._choose("enter"))
        self.list.currentRowChanged.connect(self._current_changed)
        if font is not None:
            self.list.setFont(font)
        self.status = QLabel()
        self._timer = QTimer(self, singleShot=True, interval=40)
        self._timer.timeout.connect(self.refresh)
        self.body = QVBoxLayout(self)
        self.body.addWidget(self.query)

    def eventFilter(self, obj, event):
        if obj is self.query and event.type() == event.Type.KeyPress:
            key, mods = event.key(), event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            steps = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 10, Qt.Key.Key_PageUp: -10}
            step = steps.get(key) or (ctrl and {Qt.Key.Key_J: 1, Qt.Key.Key_N: 1,
                                                 Qt.Key.Key_K: -1, Qt.Key.Key_P: -1}.get(key))
            if step:
                row = max(0, min(self.list.currentRow() + step, self.list.count() - 1))
                self.list.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if ctrl and self.LIST_ALL:
                    self._choose("all")
                else:
                    self._choose("alt" if mods & Qt.KeyboardModifier.ShiftModifier else "enter")
                return True
            if ctrl and key == Qt.Key.Key_T:
                self._choose("tab")
                return True
        return super().eventFilter(obj, event)

    def set_items(self, items: list[str]) -> None:
        self.items = items
        self.refresh()

    def bonus(self, item: str) -> int:
        return 0

    def display(self, item: str) -> str:
        return item

    def refresh(self) -> None:
        results = fuzzy.rank(self.query.text().strip(), self.items, self.LIMIT, self.bonus)
        self.list.clear()
        self.shown = [item for _, item in results]
        for item in self.shown:
            self.list.addItem(QListWidgetItem(self.display(item)))
        if self.shown:
            self.list.setCurrentRow(0)
        self.update_status()

    def update_status(self) -> None:
        self.status.setText(f"{len(self.shown)} of {len(self.items)}")

    def _current_changed(self, row: int) -> None:
        pass

    def _choose(self, how: str) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.shown):
            self.chosen, self.how = self.shown[row], how
            self.accept()


class PaletteDialog(FuzzyDialog):
    """Every action and ':' command, with its keys. `entries` maps the shown
    text to what to run."""

    def __init__(self, entries: dict[str, tuple], parent: QWidget, font: QFont | None = None):
        super().__init__("Command palette", parent, font)
        self.entries = entries
        fit_window(self, 760, 520)
        self.query.setPlaceholderText("Type to find a command…   (Enter runs it)")
        self.body.addWidget(self.list, 1)
        self.body.addWidget(self.status)
        self.set_items(list(entries))

    def update_status(self) -> None:
        self.status.setText(f"{len(self.shown)} of {len(self.items)} commands.   "
                            "Enter = run, Esc = close")


class _Indexer(QObject):
    found = Signal(list)
    done = Signal(int)

    def __init__(self, root: str, show_hidden: bool, limit: int):
        super().__init__()
        self.root, self.show_hidden, self.limit = root, show_hidden, limit
        self.stop = threading.Event()

    def run(self) -> None:
        batch, count = [], 0
        for folder, dirs, files in os.walk(self.root):
            if self.stop.is_set():
                break
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                             and (self.show_hidden or not d.startswith(".")))
            rel = os.path.relpath(folder, self.root)
            prefix = "" if rel == "." else rel + os.sep
            for d in dirs:
                batch.append(prefix + d + os.sep)
            for f in files:
                if self.show_hidden or not f.startswith("."):
                    batch.append(prefix + f)
            count += len(dirs) + len(files)
            if len(batch) >= 2000:
                self.found.emit(batch)
                batch = []
            if count >= self.limit:
                break
        if batch:
            self.found.emit(batch)
        self.done.emit(count)


class FileFinder(FuzzyDialog):
    """Ctrl-p: every file and folder below `root`, fuzzy-filtered, ranked with
    zoxide's frecency of their folder, with a quick view of the selected one."""

    LIMIT = 200
    LIST_ALL = True

    def __init__(self, root: str, show_hidden: bool, frecency: dict[str, float], parent: QWidget,
                 font: QFont | None = None):
        super().__init__(f"Find files in {root}", parent, font)
        from .preview import QuickView  # preview imports pane, which is fine here
        self.root = root
        self.frecency = {os.path.normcase(k): v for k, v in frecency.items()}
        fit_window(self, 1100, 640)
        self.query.setPlaceholderText("Type part of a name or path…   "
                                      "Enter = go there, Shift-Enter = open, Ctrl-t = new tab, "
                                      "Ctrl-Enter = all as a list")
        self.preview = QuickView()
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(self.preview)
        split.setSizes([600, 500])
        self.body.addWidget(split, 1)
        self.body.addWidget(self.status)
        self.indexing = True
        self.indexer = _Indexer(root, show_hidden, 300_000)
        self.indexer.found.connect(self._add)
        self.indexer.done.connect(self._done)
        self._preview_timer = QTimer(self, singleShot=True, interval=120)
        self._preview_timer.timeout.connect(self._show_preview)
        threading.Thread(target=self.indexer.run, daemon=True).start()

    def _add(self, batch: list[str]) -> None:
        self.items.extend(batch)
        self._timer.start()

    def _done(self, count: int) -> None:
        self.indexing = False
        self.update_status()

    def bonus(self, item: str) -> int:
        folder = os.path.dirname(os.path.join(self.root, item.rstrip("/\\")))
        score = self.frecency.get(os.path.normcase(folder))
        return int(min(40, 8 * math.log1p(score))) if score else 0

    def update_status(self) -> None:
        state = "Indexing… " if self.indexing else ""
        self.status.setText(f"{state}{len(self.shown)} shown of {len(self.items):,} items")

    def _current_changed(self, row: int) -> None:
        self._preview_timer.start()

    def _show_preview(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.shown):
            item = self.shown[row]
            path = os.path.join(self.root, item.rstrip("/\\"))
            self.preview.show_path(path, item.endswith(("/", "\\")))

    def done(self, result: int) -> None:
        self.indexer.stop.set()
        self.preview.clear()
        super().done(result)

    def chosen_path(self) -> str | None:
        return os.path.join(self.root, self.chosen.rstrip("/\\")) if self.chosen else None

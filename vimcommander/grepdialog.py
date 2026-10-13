""":grep and g/: search inside files as you type.

Files with matches and their matching lines on the left, the lines around the
selected match on the right. Enter opens the file in the editor at that line
(programs.editor_line), Shift-Enter goes to the file in the pane, Ctrl-Enter
shows every matching file as a result list in the pane.
"""

import os
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSplitter,
                               QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import grep
from .sizing import fit_window

CONTEXT = 40  # lines shown around the match in the preview
MATCH_COLOR = QColor(255, 196, 0, 110)
LINE_COLOR = QColor(47, 111, 208, 50)


class _Worker(QObject):
    found = Signal(int, list)  # token, [FileMatches]
    done = Signal(int, object)  # token, Stats

    def __init__(self):
        super().__init__()
        self.stop = threading.Event()

    def run(self, token, root, regex, globs, show_hidden) -> None:
        stats = grep.Stats()
        batch = []
        for fm in grep.grep(root, regex, globs, show_hidden, self.stop, stats):
            batch.append(fm)
            if len(batch) >= 20:
                self.found.emit(token, batch)
                batch = []
        if batch and not self.stop.is_set():
            self.found.emit(token, batch)
        if not self.stop.is_set():
            self.done.emit(token, stats)


class GrepDialog(QDialog):
    def __init__(self, root: str, pattern: str, show_hidden: bool, case: tuple[bool, bool],
                 parent: QWidget, font: QFont | None = None, globs: str = ""):
        super().__init__(parent)
        self.root, self.show_hidden, self.case = root, show_hidden, case
        self.action: str | None = None  # "edit" | "goto" | "list"
        self.chosen: tuple[str, int] | None = None  # (path, line)
        self.files: list[str] = []
        self.setWindowTitle(f"Search in files - {root}")
        fit_window(self, 1200, 720)
        self.query = QLineEdit(pattern)
        self.query.setPlaceholderText("Text or regex to find (smartcase)")
        self.globs = QLineEdit(globs)
        self.globs.setPlaceholderText("files, e.g. *.py *.md !test_*")
        self.globs.setMaximumWidth(320)
        for edit in (self.query, self.globs):
            edit.textChanged.connect(lambda _: self._debounce.start())
            edit.installEventFilter(self)
        top = QHBoxLayout()
        top.addWidget(self.query, 1)
        top.addWidget(self.globs)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(lambda *_: self._preview_timer.start())
        self.tree.itemActivated.connect(lambda *_: self._choose("edit"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if font is not None:
            self.tree.setFont(font)
            self.preview.setFont(font)
        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(self.preview)
        split.setSizes([560, 640])
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(split, 1)
        layout.addWidget(self.status)
        self._token = 0
        self._worker: _Worker | None = None
        self._debounce = QTimer(self, singleShot=True, interval=300)
        self._debounce.timeout.connect(self.search)
        self._preview_timer = QTimer(self, singleShot=True, interval=60)
        self._preview_timer.timeout.connect(self._show_preview)
        self._counts = [0, 0]  # matches, files
        self._preview_path = ""
        self._preview_lines: list[str] = []
        if pattern:
            self.search()
        else:
            self._idle()

    def _idle(self) -> None:
        self.status.setText("Type to search.   Enter = edit at the line, Shift-Enter = go to the file, "
                            "Ctrl-Enter = all files as a list, Esc = close")

    # Searching

    def search(self) -> None:
        if self._worker:
            self._worker.stop.set()
            self._worker = None
        self._token += 1
        self.tree.clear()
        self.files = []
        self._counts = [0, 0]
        self.preview.clear()
        text = self.query.text()
        if not text:
            self._idle()
            return
        regex = grep.compile_pattern(text, *self.case)
        self.status.setText("Searching…")
        worker = self._worker = _Worker()
        worker.found.connect(self._found)
        worker.done.connect(self._done)
        threading.Thread(target=worker.run, daemon=True, args=(
            self._token, self.root, regex, self.globs.text(), self.show_hidden)).start()

    def _found(self, token: int, batch: list) -> None:
        if token != self._token:
            return
        for fm in batch:
            self.files.append(fm.path)
            rel = os.path.relpath(fm.path, self.root)
            item = QTreeWidgetItem([f"{rel}   ({len(fm.matches)})"])
            item.setData(0, Qt.ItemDataRole.UserRole, (fm.path, fm.matches[0].line))
            bold = item.font(0)
            bold.setBold(True)
            item.setFont(0, bold)
            for m in fm.matches:
                child = QTreeWidgetItem([f"{m.line:>5}: {m.text.strip()}"])
                child.setData(0, Qt.ItemDataRole.UserRole, (fm.path, m.line))
                item.addChild(child)
            self.tree.addTopLevelItem(item)
            item.setExpanded(self.tree.topLevelItemCount() <= 50)
            self._counts[0] += len(fm.matches)
            self._counts[1] += 1
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            first = self.tree.topLevelItem(0)
            self.tree.setCurrentItem(first.child(0) or first)
        self.status.setText(f"Searching… {self._counts[0]} matches in {self._counts[1]} files")

    def _done(self, token: int, stats: grep.Stats) -> None:
        if token != self._token:
            return
        more = "" if stats.finished else " (stopped at the limit)"
        self.status.setText(f"{stats.matches} matches in {len(self.files)} of {stats.files} files{more}.   "
                            "Enter = edit at the line, Shift-Enter = go to the file, "
                            "Ctrl-Enter = all files as a list")

    # Preview

    def _show_preview(self) -> None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not data:
            return
        path, line = data
        if path != self._preview_path:
            self._preview_path = path
            try:
                with open(path, "rb") as f:
                    self._preview_lines = f.read(grep.MAX_SIZE).decode("utf-8", errors="replace").splitlines()
            except OSError:
                self._preview_lines = []
        lines = self._preview_lines
        first = max(0, line - 1 - CONTEXT)
        shown = lines[first:line + CONTEXT]
        self.preview.setPlainText("\n".join(f"{first + i + 1:>5}  {t}" for i, t in enumerate(shown)))
        # Highlight the line and the matches in it
        regex = grep.compile_pattern(self.query.text(), *self.case)
        selections = []
        doc = self.preview.document()
        block = doc.findBlockByNumber(line - 1 - first)
        if block.isValid():
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(LINE_COLOR)
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            sel.cursor = QTextCursor(block)
            selections.append(sel)
        offset = 7  # the line number column
        for b in range(len(shown)):
            text = shown[b]
            block_b = doc.findBlockByNumber(b)
            for m in regex.finditer(text):
                if m.end() == m.start():
                    continue
                sel = QTextEdit.ExtraSelection()
                fmt = QTextCharFormat()
                fmt.setBackground(MATCH_COLOR)
                sel.format = fmt
                cursor = QTextCursor(block_b)
                cursor.setPosition(block_b.position() + offset + m.start())
                cursor.setPosition(block_b.position() + offset + m.end(), QTextCursor.MoveMode.KeepAnchor)
                sel.cursor = cursor
                selections.append(sel)
        self.preview.setExtraSelections(selections)
        if block.isValid():
            cursor = QTextCursor(block)
            self.preview.setTextCursor(cursor)
            self.preview.centerCursor()

    # Keys and choosing

    def eventFilter(self, obj, event):
        if obj in (self.query, self.globs) and event.type() == event.Type.KeyPress:
            key, mods = event.key(), event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 15,
                    Qt.Key.Key_PageUp: -15}.get(key)
            if ctrl:
                step = {Qt.Key.Key_J: 1, Qt.Key.Key_N: 1, Qt.Key.Key_K: -1, Qt.Key.Key_P: -1}.get(key, step)
            if step:
                self._move(step)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if ctrl:
                    self._choose("list")
                elif mods & Qt.KeyboardModifier.ShiftModifier:
                    self._choose("goto")
                else:
                    self._choose("edit")
                return True
        return super().eventFilter(obj, event)

    def _move(self, step: int) -> None:
        for _ in range(abs(step)):
            item = self.tree.currentItem()
            if item is None:
                following = self.tree.topLevelItem(0)
            else:
                following = self.tree.itemBelow(item) if step > 0 else self.tree.itemAbove(item)
            if following is None:
                break
            self.tree.setCurrentItem(following)

    def _choose(self, action: str) -> None:
        if action == "list":
            if self.files:
                self.action = "list"
                self.accept()
            return
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if data:
            self.action, self.chosen = action, tuple(data)
            self.accept()

    def done(self, result: int) -> None:
        if self._worker:
            self._worker.stop.set()
        super().done(result)

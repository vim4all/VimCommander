"""The side-by-side diff window (:diffthis, gd, double-click in :synchronize).

Both sides scroll together. Changed lines are yellow with the changed part
darker, lines only on the left red, only on the right green; the other side
shows an empty filler row. ]c / [c (or n / N) jump to the next / previous
change, j / k / Ctrl-d / Ctrl-u / gg / G scroll, q or Esc closes.
"""

import filecmp
import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy,
                               QSplitter, QTextEdit, QVBoxLayout, QWidget)

from . import textdiff
from .sizing import fit_window

SIZE_LIMIT = 8 * 1024 * 1024
LINE_COLORS = {"change": QColor(255, 196, 0, 55), "delete": QColor(229, 57, 53, 60),
               "insert": QColor(46, 160, 67, 60), "filler": QColor(128, 128, 128, 40)}
SPAN_COLORS = {"left": QColor(229, 57, 53, 120), "right": QColor(46, 160, 67, 130)}
CURRENT_COLOR = QColor(47, 111, 208, 70)


def read_lines(path: str) -> list[str] | None:
    """The file's lines, or None if it is binary or too big."""
    with open(path, "rb") as f:
        data = f.read(SIZE_LIMIT + 1)
    if len(data) > SIZE_LIMIT or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace").splitlines()


def _describe(path: str) -> str:
    from .pane import human_size  # pane → preview → … would cycle at module level
    try:
        st = os.stat(path)
    except OSError:
        return path
    when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    home = os.path.expanduser("~")
    if path.startswith(home + os.sep):
        path = "~" + path[len(home):]
    return f"{path}   ({human_size(st.st_size)}, {when})"


class _Side(QPlainTextEdit):
    def __init__(self, font: QFont | None):
        super().__init__()
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                     | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        if font is not None:
            self.setFont(font)


class DiffDialog(QDialog):
    def __init__(self, left: str, right: str, parent: QWidget, font: QFont | None = None,
                 titles: tuple[str, str] | None = None):
        super().__init__(parent)
        self.paths = (left, right)
        self.setWindowTitle(f"Diff - {os.path.basename(left)}  ↔  {os.path.basename(right)}")
        fit_window(self, 1300, 800)
        self.rows: list[textdiff.Row] = []
        self.starts: list[int] = []
        self.current = -1  # index into self.starts
        self._pending = ""  # ']' or '[' or 'g' typed, waiting for the next key
        names = titles or (_describe(left), _describe(right))
        heads = []
        for name in names:
            label = QLabel(name)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet("padding: 3px 6px; font-weight: bold;")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # long paths don't widen the window
            label.setToolTip(name)
            heads.append(label)
        self.sides = (_Side(font), _Side(font))
        split = QSplitter()
        for head, side in zip(heads, self.sides):
            box = QWidget()
            col = QVBoxLayout(box)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(0)
            col.addWidget(head)
            col.addWidget(side, 1)
            split.addWidget(box)
            side.installEventFilter(self)
        self._syncing = False
        for a, b in (self.sides, self.sides[::-1]):
            a.verticalScrollBar().valueChanged.connect(lambda v, b=b: self._sync(b.verticalScrollBar(), v))
            a.horizontalScrollBar().valueChanged.connect(lambda v, b=b: self._sync(b.horizontalScrollBar(), v))
        self.ignore_space = QCheckBox("Ignore white space")
        self.ignore_space.toggled.connect(lambda _: self.compare())
        self.status = QLabel()
        self.status.setWordWrap(True)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.ignore_space)
        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addLayout(bottom)
        self.compare()
        self.sides[0].setFocus()

    def _sync(self, bar, value: int) -> None:
        if not self._syncing:
            self._syncing = True
            bar.setValue(value)
            self._syncing = False

    # Comparing and drawing

    def compare(self) -> None:
        try:
            a, b = read_lines(self.paths[0]), read_lines(self.paths[1])
        except OSError as e:
            self.status.setText(f"Can't read: {e.strerror or e}")
            return
        if a is None or b is None:
            same = filecmp.cmp(*self.paths, shallow=False)
            for side in self.sides:
                side.setPlainText("")
            self.status.setText("Binary or very big files: they are " + ("the same." if same else "different."))
            return
        self.rows = textdiff.align(a, b, self.ignore_space.isChecked())
        self.starts = textdiff.hunks(self.rows)
        self.current = -1
        width = len(str(max(len(a), len(b), 1)))
        for side, (no_attr, text_attr) in zip(self.sides, (("left_no", "left"), ("right_no", "right"))):
            lines = []
            for row in self.rows:
                no = getattr(row, no_attr)
                lines.append(f"{no:>{width}}  {getattr(row, text_attr)}" if no is not None else "")
            side.setPlainText("\n".join(lines))
        self.offset = width + 2
        self._paint()
        changed, deleted, inserted = textdiff.summary(self.rows)
        if not self.starts:
            what = "The files are the same" + (" (ignoring white space)" if self.ignore_space.isChecked()
                                                and a != b else "")
        else:
            what = (f"{len(self.starts)} change(s): {changed} line(s) changed, {deleted} only on the left, "
                    f"{inserted} only on the right")
        self._status = what + ".   ]c / [c or n / N = next / previous change, q = close"
        self.status.setText(self._status)
        if self.starts:
            self.goto_change(0)

    def _paint(self) -> None:
        for s, side in enumerate(self.sides):
            doc = side.document()
            selections = []
            for i, row in enumerate(self.rows):
                if row.kind == "same":
                    continue
                filler = (row.left_no if s == 0 else row.right_no) is None
                kind = "filler" if filler else row.kind
                if i in self._current_rows():
                    color = CURRENT_COLOR
                else:
                    color = LINE_COLORS[kind]
                block = doc.findBlockByNumber(i)
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(color)
                sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                sel.cursor = QTextCursor(block)
                selections.append(sel)
                if row.kind == "change":
                    spans = textdiff.changed_spans(row.left, row.right)[s]
                    for a, b in spans:
                        span = QTextEdit.ExtraSelection()
                        fmt = QTextCharFormat()
                        fmt.setBackground(SPAN_COLORS["left" if s == 0 else "right"])
                        span.format = fmt
                        cursor = QTextCursor(block)
                        cursor.setPosition(block.position() + self.offset + a)
                        cursor.setPosition(block.position() + self.offset + b, QTextCursor.MoveMode.KeepAnchor)
                        span.cursor = cursor
                        selections.append(span)
            side.setExtraSelections(selections)

    def _current_rows(self) -> range:
        if not 0 <= self.current < len(self.starts):
            return range(0)
        start = self.starts[self.current]
        end = start
        while end < len(self.rows) and self.rows[end].kind != "same":
            end += 1
        return range(start, end)

    # Moving

    def goto_change(self, index: int) -> None:
        if not self.starts:
            return
        self.current = max(0, min(index, len(self.starts) - 1))
        self._paint()
        row = self.starts[self.current]
        bar = self.sides[0].verticalScrollBar()
        bar.setValue(max(0, row - 6))
        self.status.setText(f"{self._status}   [change {self.current + 1} of {len(self.starts)}]")

    def _step_change(self, step: int) -> None:
        if not self.starts:
            return
        if self.current < 0:
            self.goto_change(0 if step > 0 else len(self.starts) - 1)
        else:
            self.goto_change((self.current + step) % len(self.starts))

    def _scroll(self, lines: int) -> None:
        bar = self.sides[0].verticalScrollBar()
        bar.setValue(bar.value() + lines)

    def eventFilter(self, obj, event):
        if obj in self.sides and event.type() == event.Type.KeyPress:
            return self._key(event)
        return super().eventFilter(obj, event)

    def _key(self, event) -> bool:
        text, key = event.text(), event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        page = max(1, self.sides[0].verticalScrollBar().pageStep())
        pending, self._pending = self._pending, ""
        if pending in ("]", "[") and text == "c":
            self._step_change(1 if pending == "]" else -1)
        elif pending == "g" and text == "g":
            self.sides[0].verticalScrollBar().setValue(0)
        elif text in ("]", "[", "g") and not ctrl:
            self._pending = text
        elif ctrl and key == Qt.Key.Key_D:
            self._scroll(page // 2)
        elif ctrl and key == Qt.Key.Key_U:
            self._scroll(-(page // 2))
        elif ctrl and key == Qt.Key.Key_F:
            self._scroll(page)
        elif ctrl and key == Qt.Key.Key_B:
            self._scroll(-page)
        elif text == "n":
            self._step_change(1)
        elif text == "N":
            self._step_change(-1)
        elif text == "j" or key == Qt.Key.Key_Down:
            self._scroll(1)
        elif text == "k" or key == Qt.Key.Key_Up:
            self._scroll(-1)
        elif text == "G":
            bar = self.sides[0].verticalScrollBar()
            bar.setValue(bar.maximum())
        elif text == "q" or key == Qt.Key.Key_Escape:
            self.reject()
        elif key == Qt.Key.Key_Tab:
            other = self.sides[1] if self.sides[0].hasFocus() else self.sides[0]
            other.setFocus()
        else:
            return False  # e.g. Ctrl-c copies the selected text
        return True

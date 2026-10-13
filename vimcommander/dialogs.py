"""Dialogs: file operation progress, name clash question, zoxide folder picker."""

import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QProgressBar, QVBoxLayout, QWidget)

from . import archive
from .fileops import Job
from .pane import human_size
from .zoxide import matches
from .sizing import fit_window


class ProgressDialog(QDialog):
    """Shows a running job's progress, with a Cancel button. Appears only if
    the job takes longer than a moment."""

    def __init__(self, job: Job, parent: QWidget):
        super().__init__(parent)
        self.job = job
        self.setWindowTitle(job.description.rstrip("…"))
        self.setMinimumWidth(460)
        self.label = QLabel(job.description)
        self.current = QLabel()
        self.current.setTextFormat(Qt.TextFormat.PlainText)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.amount = QLabel()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for w in (self.label, self.current, self.bar, self.amount, buttons):
            layout.addWidget(w)
        job.progress.connect(self.on_progress)
        QTimer.singleShot(400, self._show_if_running)
        self.running = True

    def _show_if_running(self) -> None:
        if self.running:
            self.show()

    def on_progress(self, done: int, total: int, items: int, items_total: int, name: str) -> None:
        self.current.setText(name)
        if total:
            self.bar.setValue(int(1000 * min(done, total) / total))
            self.amount.setText(f"{human_size(done)} of {human_size(total)}   "
                                f"(item {min(items + 1, items_total)} of {items_total})")
        else:
            self.bar.setValue(int(1000 * items / max(items_total, 1)))
            self.amount.setText(f"item {min(items + 1, items_total)} of {items_total}")

    def reject(self) -> None:
        self.job.cancel()
        self.label.setText("Cancelling…")

    def finish(self) -> None:
        self.running = False
        self.done(0)
        self.deleteLater()


def _describe(path: str) -> str:
    if archive.inside(path):
        return "in an archive"
    try:
        st = os.stat(path)
    except OSError:
        return ""
    when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    if os.path.isdir(path):
        return f"folder, modified {when}"
    return f"{human_size(st.st_size)}, modified {when}"


def ask_conflict(parent: QWidget, src: str, dest: str) -> tuple[str, bool]:
    """Ask what to do when `dest` already exists. Returns (choice, apply to all)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Name already exists")
    name = os.path.basename(dest)
    folder = os.path.isdir(dest)
    box.setText(f"<b>{name}</b> already exists in {os.path.dirname(dest)}.")
    box.setInformativeText(f"Existing: {_describe(dest)}\nNew: {_describe(src)}"
                           + ("\n\nOverwrite merges the folders." if folder else ""))
    buttons = {
        box.addButton("Overwrite", QMessageBox.ButtonRole.DestructiveRole): "overwrite",
        box.addButton("Keep both", QMessageBox.ButtonRole.AcceptRole): "rename",
        box.addButton("Skip", QMessageBox.ButtonRole.RejectRole): "skip",
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole): "cancel",
    }
    apply_all = QCheckBox("Do this for all clashes in this operation")
    box.setCheckBox(apply_all)
    box.setDefaultButton(next(b for b, c in buttons.items() if c == "rename"))
    box.setEscapeButton(next(b for b, c in buttons.items() if c == "cancel"))
    box.exec()
    return buttons.get(box.clickedButton(), "cancel"), apply_all.isChecked()


class ZoxideDialog(QDialog):
    """`:zi` / `gz`: pick a folder from zoxide's database, filtering as you type.

    Up/Down (or Ctrl-j/k, Ctrl-n/p) move, Enter jumps, Shift-Enter opens a new tab.
    """

    def __init__(self, entries: list[tuple[float, str]], text: str, parent: QWidget):
        super().__init__(parent)
        self.entries = entries
        self.chosen: str | None = None
        self.new_tab = False
        self.setWindowTitle("Jump to folder (zoxide)")
        fit_window(self, 720, 480)
        self.filter = QLineEdit(text)
        self.filter.setPlaceholderText("keywords, e.g.  proj vim")
        self.filter.textChanged.connect(self._refresh)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _: self._choose(False))
        self.status = QLabel()
        layout = QVBoxLayout(self)
        layout.addWidget(self.filter)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.status)
        self.home = os.path.expanduser("~")
        self._refresh()

    def _shown(self, path: str) -> str:
        if path == self.home or path.startswith(self.home + os.sep):
            return "~" + path[len(self.home):]
        return path

    def _refresh(self) -> None:
        keywords = self.filter.text().split()
        self.paths = [p for _, p in self.entries if matches(p, keywords)]
        self.list.clear()
        self.list.addItems([self._shown(p) for p in self.paths[:2000]])
        self.list.setCurrentRow(0)
        self.status.setText(f"{len(self.paths)} of {len(self.entries)} folders.   "
                            "Enter = jump, Shift+Enter = new tab, Esc = close")

    def _choose(self, new_tab: bool) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.paths):
            self.chosen, self.new_tab = self.paths[row], new_tab
            self.accept()

    def keyPressEvent(self, e):
        ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
        step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 10,
                Qt.Key.Key_PageUp: -10}.get(e.key())
        if ctrl:
            step = {Qt.Key.Key_J: 1, Qt.Key.Key_N: 1, Qt.Key.Key_K: -1, Qt.Key.Key_P: -1}.get(e.key(), step)
        if step:
            self.list.setCurrentRow(max(0, min(self.list.currentRow() + step, self.list.count() - 1)))
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._choose(bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        else:
            super().keyPressEvent(e)

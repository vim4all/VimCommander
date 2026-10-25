"""Dialogs: file operation progress, name clash question, pack / unpack, zoxide folder picker, trash."""

import os
from datetime import datetime

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QDialogButtonBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QProgressBar, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import archive, trash
from .fileops import Job
from .pane import human_size
from .zoxide import matches
from .sizing import fit_window


class ProgressDialog(QDialog):
    """Shows a running job's progress, with Pause, Background and Cancel buttons.
    Appears only if the job takes longer than a moment."""

    background = Signal()  # sent to the jobs panel

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
        self.pause_button = buttons.addButton("Pause", QDialogButtonBox.ButtonRole.ActionRole)
        self.pause_button.clicked.connect(self.toggle_pause)
        hide = buttons.addButton("Background", QDialogButtonBox.ButtonRole.ActionRole)
        hide.setToolTip("Hide this window; the job goes on in the jobs panel (:jobs)")
        hide.clicked.connect(self.to_background)
        layout = QVBoxLayout(self)
        for w in (self.label, self.current, self.bar, self.amount, buttons):
            layout.addWidget(w)
        job.progress.connect(self.on_progress)
        QTimer.singleShot(400, self._show_if_running)
        self.running = True

    def _show_if_running(self) -> None:
        if self.running:
            self.show()

    def toggle_pause(self) -> None:
        self.job.resume() if self.job.paused else self.job.pause()
        self.pause_button.setText("Resume" if self.job.paused else "Pause")
        self.label.setText(("Paused: " if self.job.paused else "") + self.job.description)

    def to_background(self) -> None:
        self.running = False  # don't show it again
        self.hide()
        self.background.emit()

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


class TrashDialog(QDialog):
    """`:trash`: what is in the system trash, newest first, filtered as you type.

    Enter = put back where it was, Shift+Enter = into `here` (the active pane's
    folder), Shift+Del = delete for good, Ctrl+A = select all. `action` and
    `chosen` say what to do once the dialog is accepted.
    """

    def __init__(self, items: list[trash.Item], text: str, here: str, parent: QWidget):
        super().__init__(parent)
        self.items = items
        self.here = here
        self.action = ""  # "restore", "restore_here" or "purge"
        self.chosen: list[trash.Item] = []
        self.setWindowTitle("Trash")
        fit_window(self, 900, 520)
        self.filter = QLineEdit(text)
        self.filter.setPlaceholderText("filter by name or folder")
        self.filter.textChanged.connect(self._refresh)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Was in", "Deleted", "Size"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.itemActivated.connect(lambda *_: self._choose("restore"))
        self.status = QLabel()
        self.status.setWordWrap(True)
        buttons = QHBoxLayout()
        for label, action in (("Restore", "restore"), ("Restore to this folder", "restore_here"),
                              ("Delete for good", "purge")):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _=False, a=action: self._choose(a))
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.filter)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        self.filter.installEventFilter(self)  # its own Shift+Del (cut) and Ctrl+A would win
        self.tree.installEventFilter(self)
        self._refresh()
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 360)

    def _refresh(self) -> None:
        words = self.filter.text().casefold().split()
        self.shown = [i for i in self.items
                      if all(w in (i.original or i.path).casefold() for w in words)]
        self.tree.clear()
        for item in self.shown[:5000]:
            size = item.size()
            when = datetime.fromtimestamp(item.deleted).strftime("%Y-%m-%d %H:%M") if item.deleted else ""
            row = QTreeWidgetItem([item.name + ("/" if item.is_dir else ""), item.folder or "?", when,
                                   "" if size is None else human_size(size)])
            row.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.setToolTip(1, item.original or item.path)
            self.tree.addTopLevelItem(row)
        if self.shown:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.status.setText(f"{len(self.shown)} of {len(self.items)} items.   Enter = restore, "
                            f"Shift+Enter = restore to {self.here}, Shift+Del = delete for good, "
                            "Ctrl+A = all, Esc = close")

    def selected(self) -> list[trash.Item]:
        rows = sorted(self.tree.indexOfTopLevelItem(i) for i in self.tree.selectedItems())
        return [self.shown[r] for r in rows if 0 <= r < len(self.shown)]

    def _choose(self, action: str) -> None:
        if not (chosen := self.selected()):
            return
        if action == "purge":
            answer = QMessageBox.warning(
                self, "Delete for good", f"Delete {len(chosen)} item(s) from the trash for good?\n\n"
                "This can't be undone.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.action, self.chosen = action, chosen
        self.accept()

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.KeyPress:
            ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
            shift = e.modifiers() & Qt.KeyboardModifier.ShiftModifier
            if (e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or (ctrl and e.key() == Qt.Key.Key_A)
                    or (shift and e.key() == Qt.Key.Key_Delete)):
                self.keyPressEvent(e)
                return True
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e):
        mods = e.modifiers()
        ctrl = mods & Qt.KeyboardModifier.ControlModifier
        shift = mods & Qt.KeyboardModifier.ShiftModifier
        step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 10,
                Qt.Key.Key_PageUp: -10}.get(e.key())
        if ctrl:
            step = {Qt.Key.Key_J: 1, Qt.Key.Key_N: 1, Qt.Key.Key_K: -1, Qt.Key.Key_P: -1}.get(e.key(), step)
        if step and self.tree.topLevelItemCount():
            row = self.tree.indexOfTopLevelItem(self.tree.currentItem())
            row = max(0, min(row + step, self.tree.topLevelItemCount() - 1))
            flag = (QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
                    if shift else QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows)
            self.tree.setCurrentItem(self.tree.topLevelItem(row), 0, flag)
        elif ctrl and e.key() == Qt.Key.Key_A:
            self.tree.selectAll()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._choose("restore_here" if shift else "restore")
        elif e.key() == Qt.Key.Key_Delete and shift:
            self._choose("purge")
        else:
            super().keyPressEvent(e)


LEVELS = [("Normal", 6), ("Maximum (slow)", 9), ("Fast", 1), ("Store (no compression)", 0)]


class PackDialog(QDialog):
    """Total Commander's Alt+F5: where to pack the files, and how."""

    def __init__(self, parent: QWidget, count: int, dest: str):
        super().__init__(parent)
        self.setWindowTitle("Pack files")
        form = QFormLayout(self)
        form.addRow(QLabel(f"Pack {count} item(s) into:"))
        self.path = QLineEdit(dest)
        self.path.setMinimumWidth(480)
        stem = os.path.splitext(os.path.basename(dest))[0]
        start = len(dest) - len(os.path.basename(dest))
        self.path.setSelection(start, len(stem))  # typing replaces just the name
        form.addRow("Archive:", self.path)
        self.level = QComboBox()
        for label, _ in LEVELS:
            self.level.addItem(label)
        form.addRow("Compression:", self.level)
        self.move = QCheckBox("Move to archive (put the originals in the trash)")
        self.separate = QCheckBox("Create a separate archive for each item")
        form.addRow(self.move)
        form.addRow(self.separate)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def options(self) -> dict:
        return {"dest": self.path.text().strip(), "level": LEVELS[self.level.currentIndex()][1],
                "move": self.move.isChecked(), "separate": self.separate.isChecked()}


class UnpackDialog(QDialog):
    """Total Commander's Alt+F9: where to unpack the archives."""

    def __init__(self, parent: QWidget, names: list[str], dest: str):
        super().__init__(parent)
        self.setWindowTitle("Unpack files")
        form = QFormLayout(self)
        shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        label = QLabel(f"Unpack {shown} to:")
        label.setWordWrap(True)
        form.addRow(label)
        self.path = QLineEdit(dest)
        self.path.setMinimumWidth(480)
        form.addRow("Folder:", self.path)
        self.subfolder = QCheckBox("Unpack each archive into its own subfolder")
        self.subfolder.setChecked(len(names) > 1)
        form.addRow(self.subfolder)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def options(self) -> dict:
        return {"dest": self.path.text().strip(), "subfolder": self.subfolder.isChecked()}

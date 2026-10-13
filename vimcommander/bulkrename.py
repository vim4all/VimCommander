""":bulkrename — edit many names as text, like vidir / vimv.

The names are shown as numbered lines. Edit them here, or press "Open in
editor" to edit the same text in your editor (programs.editor): every time you
save there, the text here is reloaded. Apply renames everything at once; a
deleted line moves that item to the trash.
"""

import os
import tempfile

from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from .rename import bulk_text, parse_bulk
from .sizing import fit_window


class BulkRenameDialog(QDialog):
    def __init__(self, names: list[str], open_in_editor, parent: QWidget, font: QFont | None = None):
        """`open_in_editor(path) -> bool` starts the user's editor on a file."""
        super().__init__(parent)
        self.names = names
        self.renames: dict[int, str] = {}
        self.deleted: set[int] = set()
        self.setWindowTitle(f"Rename {len(names)} item(s)")
        fit_window(self, 760, 560)
        self.text = QPlainTextEdit(bulk_text(names))
        if font is not None:
            self.text.setFont(font)
        self.info = QLabel("Edit the names, then Apply. Delete a line to move that item to the trash.")
        self.info.setWordWrap(True)
        buttons = QDialogButtonBox()
        self.apply_button = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        editor = QPushButton("Open in editor")
        editor.setToolTip("Edit in programs.editor; each save there updates this text")
        buttons.addButton(editor, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        editor.clicked.connect(lambda: self._open_editor(open_in_editor))
        layout = QVBoxLayout(self)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.info)
        layout.addWidget(buttons)
        self.file = ""
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(lambda _: self._reload_timer.start())
        self._reload_timer = QTimer(self, singleShot=True, interval=200)
        self._reload_timer.timeout.connect(self._reload)

    def _open_editor(self, open_in_editor) -> None:
        if not self.file:
            fd, self.file = tempfile.mkstemp(prefix="vimcommander-rename-", suffix=".txt")
            os.close(fd)
        with open(self.file, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.text.toPlainText())
        if self.file not in self.watcher.files():
            self.watcher.addPath(self.file)
        if open_in_editor(self.file):
            self.info.setText(f"Editing {self.file} in your editor. Save it there; this text "
                              "follows each save. Then press Apply.")
        else:
            self.info.setText("Could not start the editor: set programs.editor in the config file.")

    def _reload(self) -> None:
        if self.file and os.path.exists(self.file):
            if self.file not in self.watcher.files():  # editors that replace the file
                self.watcher.addPath(self.file)
            with open(self.file, encoding="utf-8", errors="replace") as f:
                self.text.setPlainText(f.read())
            self.info.setText("Reloaded from the editor. Press Apply when you're done.")

    def apply(self) -> None:
        try:
            names, deleted = parse_bulk(self.text.toPlainText(), len(self.names))
        except ValueError as e:
            self.info.setText(f"<b>Can't apply:</b> {e}")
            return
        self.renames = {i: n for i, n in names.items() if n != self.names[i]}
        self.deleted = deleted
        self.accept()

    def done(self, result: int) -> None:
        if self.file:
            self.watcher.removePaths(self.watcher.files())
            try:
                os.remove(self.file)
            except OSError:
                pass
        super().done(result)

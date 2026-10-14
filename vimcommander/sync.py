""":synchronize — compare the two panes' folder trees and copy what differs.

Like Total Commander's "Synchronize dirs": each difference gets a direction
(→ copy left to right, ← right to left, nothing = skip), proposed from which
side is newer or missing. Click the arrow or press > < = on selected rows to
change it, then Synchronize. Double-click a file on both sides to diff it.
"""

import os
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .compare import Diff, compare_trees, copy_plan, delete_plan, join, mirror
from .sizing import fit_window

ACTION_COLORS = {">": QColor("#2f6fd0"), "<": QColor("#2e7d32"), "": QColor("#808080"),
                 "x": QColor("#e53935")}
ARROWS = {">": "→", "<": "←", "": "", "x": "✗ delete"}
STATUS = {"left_only": "only left", "right_only": "only right", "left_newer": "left newer",
          "right_newer": "right newer", "different": "differ, same time"}


def _side(info, is_dir) -> str:
    if info is None:
        return ""
    size, mtime = info
    from .pane import human_size  # pane → preview → ... imports would cycle at module level
    when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    return f"{'<DIR>' if is_dir else human_size(size)}   {when}"


class _Comparer(QObject):
    done = Signal(int, list)

    def __init__(self):
        super().__init__()
        self.stop = threading.Event()

    def run(self, token, left, right, recursive, content, show_hidden):
        diffs = compare_trees(left, right, recursive, content, show_hidden, self.stop)
        if not self.stop.is_set():
            self.done.emit(token, diffs)


class SyncDialog(QDialog):
    diff_requested = Signal(str, str)  # double-click: the main window shows the two files
    def __init__(self, left: str, right: str, show_hidden: bool, parent: QWidget, mirror: bool = False):
        super().__init__(parent)
        self.left, self.right, self.show_hidden = left, right, show_hidden
        self.mirror = mirror
        self.diffs: list[Diff] = []
        self.plan: list[tuple[str, str]] = []
        self.deletes: list[str] = []
        self.setWindowTitle("Mirror: make the right side like the left" if mirror else "Synchronize folders")
        fit_window(self, 1000, 600)
        names = ("From (stays as it is)", "To (becomes a copy)") if mirror else ("Left", "Right")
        home = os.path.expanduser("~")
        short = lambda p: "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p
        header = QLabel(f"<b>{names[0]}:</b> {short(left)}<br><b>{names[1]}:</b> {short(right)}")
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.setWordWrap(True)
        self.recursive = QCheckBox("Subfolders")
        self.recursive.setChecked(True)
        self.content = QCheckBox("Compare contents of same-size files (slower)")
        self.show_left = QCheckBox("Show →")
        self.show_right = QCheckBox("Show ←")
        self.show_skip = QCheckBox("Show skipped")
        for box in (self.show_left, self.show_right, self.show_skip):
            box.setChecked(True)
            box.toggled.connect(self._fill)
        compare = QPushButton("Compare")
        compare.clicked.connect(self.compare)
        options = QHBoxLayout()
        for w in (self.recursive, self.content, compare):
            options.addWidget(w)
        options.addStretch(1)
        for w in (self.show_left, self.show_right, self.show_skip):
            options.addWidget(w)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Name", "Left", "", "Right", "Why"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(self._diff)
        self.tree.installEventFilter(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        buttons = QDialogButtonBox()
        self.sync_button = buttons.addButton("Mirror" if mirror else "Synchronize",
                                             QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addLayout(options)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status)
        layout.addWidget(buttons)
        self._token = 0
        self._comparer: _Comparer | None = None
        self.compare()

    def compare(self) -> None:
        if self._comparer:
            self._comparer.stop.set()
        self._token += 1
        self.status.setText("Comparing…")
        self.tree.clear()
        self._comparer = _Comparer()
        self._comparer.done.connect(self._compared)
        threading.Thread(target=self._comparer.run, daemon=True, args=(
            self._token, self.left, self.right, self.recursive.isChecked(),
            self.content.isChecked(), self.show_hidden)).start()

    def _compared(self, token: int, diffs: list) -> None:
        if token == self._token:
            self.diffs = diffs
            if self.mirror:
                mirror(diffs)
            self._fill()

    def _fill(self) -> None:
        self.tree.clear()
        shown = {">": self.show_left.isChecked(), "<": self.show_right.isChecked(),
                 "": self.show_skip.isChecked(), "x": True}
        for i, d in enumerate(self.diffs):
            if not shown[d.action]:
                continue
            name = d.rel + (os.sep if d.is_dir else "")
            item = QTreeWidgetItem([name, _side(d.left, d.is_dir), ARROWS[d.action],
                                    _side(d.right, d.is_dir), STATUS.get(d.status, "")])
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
            self._color(item, d)
            self.tree.addTopLevelItem(item)
        for col in range(5):
            self.tree.resizeColumnToContents(col)
        self._summary()

    def _color(self, item: QTreeWidgetItem, d: Diff) -> None:
        color = ACTION_COLORS[d.action]
        for col in range(5):
            item.setForeground(col, QBrush(color))

    def _summary(self) -> None:
        right = sum(1 for d in self.diffs if d.action == ">")
        left = sum(1 for d in self.diffs if d.action == "<")
        skip = sum(1 for d in self.diffs if d.action == "")
        delete = sum(1 for d in self.diffs if d.action == "x")
        if not self.diffs:
            self.status.setText("The folders are the same.")
        else:
            gone = f"{delete} to delete, " if delete else ""
            self.status.setText(f"{len(self.diffs)} difference(s): {right} to copy →, {left} to copy ←, {gone}"
                                f"{skip} skipped.   Click the arrow (or select rows and press > < = x) "
                                "to change what happens; double-click a file to see what changed.")
        self.sync_button.setEnabled(bool(right or left or delete))

    def _set_action(self, items, action: str) -> None:
        for item in items:
            d = self.diffs[item.data(0, Qt.ItemDataRole.UserRole)]
            d.action = action
            item.setText(2, ARROWS[action])
            self._color(item, d)
        self._summary()

    def _clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 2:
            d = self.diffs[item.data(0, Qt.ItemDataRole.UserRole)]
            one_side = d.left is None or d.right is None
            following = {">": "<", "<": "x" if one_side else "", "x": "", "": ">"}[d.action]
            self._set_action([item], following)

    def _diff(self, item: QTreeWidgetItem, column: int) -> None:
        """Double-click a file that is on both sides: show what changed."""
        d = self.diffs[item.data(0, Qt.ItemDataRole.UserRole)]
        if column != 2 and not d.is_dir and d.left and d.right:
            self.diff_requested.emit(join(self.left, d.rel), join(self.right, d.rel))

    def eventFilter(self, obj, event):
        if obj is self.tree and event.type() == event.Type.KeyPress:
            action = {">": ">", "<": "<", "=": "", " ": "", "x": "x"}.get(event.text())
            if action is not None:
                items = self.tree.selectedItems()
                if action == "x":  # only items that one side lacks can be deleted
                    items = [i for i in items if (lambda d: d.left is None or d.right is None)(
                        self.diffs[i.data(0, Qt.ItemDataRole.UserRole)])]
                self._set_action(items, action)
                return True
        return super().eventFilter(obj, event)

    def _accept(self) -> None:
        self.plan = copy_plan(self.diffs, self.left, self.right)
        self.deletes = delete_plan(self.diffs, self.left, self.right)
        if self.plan or self.deletes:
            self.accept()

    def done(self, result: int) -> None:
        if self._comparer:
            self._comparer.stop.set()
        super().done(result)

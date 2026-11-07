"""The quick view's tree of a JSON / JSON Lines / YAML document (jsontree.py
reads it). Rows are made when their parent first opens, so a big file shows at
once. A click on an arrow opens a level; `zv` in the file pane gives the tree
the keys (`focus_keys()`): j k move, h l close / open (or go to the parent /
first child), o toggles, O opens everything below, M closes all, g G top /
bottom, y copies the row's path (`.items[3].name`), Y its value as JSON;
Esc, q or Tab give them back (`leave`).
"""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QAbstractItemView, QApplication, QHeaderView, QTreeWidget, QTreeWidgetItem

from . import jsontree

OPEN_ALL_LIMIT = 5000  # rows O makes at most, so it can't freeze on a huge file


class _Row(QTreeWidgetItem):
    def __init__(self, parent, key, value, keys: list):
        super().__init__(parent)
        self.value, self.keys, self.filled = value, keys, False
        if isinstance(value, jsontree.More):
            self.setText(0, f"… {value.count} more")
            self.setForeground(0, QApplication.palette().color(QPalette.ColorRole.PlaceholderText))
            return
        self.setText(0, str(key))
        if jsontree.is_container(value):
            self.setText(1, jsontree.container_label(value))
            self.setForeground(1, QApplication.palette().color(QPalette.ColorRole.PlaceholderText))
            if value:
                self.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        else:
            self.setText(1, jsontree.scalar(value))
            self.setToolTip(1, jsontree.kind_of(value))

    def fill(self) -> int:
        """Make the child rows (once); returns how many were made."""
        if self.filled or not jsontree.is_container(self.value):
            return 0
        self.filled = True
        pairs = jsontree.children(self.value)
        for key, child in pairs:
            _Row(self, key, child, self.keys + [key])
        return len(pairs)


class JsonView(QTreeWidget):
    leave = Signal()  # Esc / q / Tab with the keys here: back to the file pane
    message = Signal(str)  # for the status line (what y / Y copied)

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # only zv gives it the keys, not a click
        self.setColumnCount(2)
        self.setHeaderLabels(["Key", "Value"])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.header().setStretchLastSection(True)
        self.setUniformRowHeights(True)  # much faster with thousands of rows
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.itemExpanded.connect(self._expanded)
        self.value = None

    def _expanded(self, item: "_Row") -> None:
        """Make the level's rows, and widen the key column to fit them (up to 60% of the view)."""
        item.fill()
        depth = 1
        parent = item.parent()
        while parent:
            depth, parent = depth + 1, parent.parent()
        metrics = self.fontMetrics()
        keys = (item.child(i).text(0) for i in range(min(item.childCount(), 200)))
        need = self.indentation() * (depth + 1) + max(map(metrics.horizontalAdvance, keys), default=0) + 16
        if need > self.columnWidth(0):
            self.setColumnWidth(0, min(need, max(120, int(self.viewport().width() * 0.6))))

    def show_value(self, value) -> None:
        """Show a document: its top level, a single top object or array opened."""
        self.clear()
        self.value = value
        if jsontree.is_container(value):
            for key, child in jsontree.children(value):
                _Row(self, key, child, [key])
        else:
            _Row(self, "(value)", value, [])
        if self.topLevelItemCount() == 1:
            self.topLevelItem(0).setExpanded(True)
        self.resizeColumnToContents(0)
        self.setColumnWidth(0, min(self.columnWidth(0) + 12, max(120, self.width() // 2)))

    def focus_keys(self) -> bool:
        """zv: take the keys (if there is a tree to move in)."""
        if not self.topLevelItemCount() or not self.isVisible():
            return False
        if self.currentItem() is None:
            self.setCurrentItem(self.topLevelItem(0))
        self.setFocus()
        return True

    def open_all(self, item: QTreeWidgetItem | None) -> None:
        """Open every level below `item` (all of them for None), up to OPEN_ALL_LIMIT rows."""
        made = 0
        todo = [item] if item else [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while todo and made < OPEN_ALL_LIMIT:
            row = todo.pop()
            made += row.fill()
            if row.childCount():
                row.setExpanded(True)
                todo.extend(row.child(i) for i in range(row.childCount()))
        if todo:
            self.message.emit(f"Opened the first {OPEN_ALL_LIMIT} rows")

    def _copy(self, text: str, what: str) -> None:
        QApplication.clipboard().setText(text)
        self.message.emit(f"Copied {what}")

    # Keys (after zv)

    def event(self, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.leave.emit()  # not Qt's focus chain
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key, text = event.key(), event.text()
        item = self.currentItem()
        if key == Qt.Key.Key_Escape or text == "q":
            self.leave.emit()
        elif text == "j":
            self.setCurrentItem(self.itemBelow(item) or item)
        elif text == "k":
            self.setCurrentItem(self.itemAbove(item) or item)
        elif text == "h" and item:
            if item.isExpanded():
                item.setExpanded(False)
            elif item.parent():
                self.setCurrentItem(item.parent())
        elif text == "l" and item:
            if item.childIndicatorPolicy() == QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator \
                    and not item.isExpanded():
                item.setExpanded(True)
            elif item.isExpanded() and item.childCount():
                self.setCurrentItem(item.child(0))
        elif text in ("o", " ") or key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if item:
                item.setExpanded(not item.isExpanded())
        elif text == "O":
            self.open_all(item)
        elif text == "M":
            self.collapseAll()
            while item and item.parent():
                item = item.parent()
            if item:
                self.setCurrentItem(item)
        elif text == "g":
            self.setCurrentItem(self.topLevelItem(0))
        elif text == "G":
            last = self.topLevelItem(self.topLevelItemCount() - 1)
            while last and last.isExpanded() and last.childCount():
                last = last.child(last.childCount() - 1)
            self.setCurrentItem(last)
        elif text == "y" and isinstance(item, _Row):
            self._copy(jsontree.path(item.keys), "the path " + jsontree.path(item.keys))
        elif text == "Y" and isinstance(item, _Row) and not isinstance(item.value, jsontree.More):
            self._copy(jsontree.dump(item.value), "the value as JSON")
        else:
            super().keyPressEvent(event)  # arrows, Home, End, Page keys
        if self.currentItem() is not None:
            self.scrollToItem(self.currentItem())

"""The shelf: a side panel to collect files from different folders, then act on
all of them at once (copy or move here, zip, show as a list, drag out).

zs puts the tagged items (or the cursor's) on it, zS shows / hides it, :shelf
does the rest. It is kept between runs. In the list: j / k move, Enter goes to
the file, d or Del takes items off the shelf (the files stay), Esc goes back
to the pane. Files can be dragged onto it and from it into a pane or another app.
"""

import os

from PySide6.QtCore import QFileInfo, QItemSelectionModel, QMimeData, QSize, Qt, QUrl, Signal
from PySide6.QtWidgets import (QAbstractItemView, QDockWidget, QFileIconProvider, QListWidget,
                               QListWidgetItem, QMenu, QWidget)


class ShelfList(QListWidget):
    files_dropped = Signal(list)
    key_command = Signal(str)  # "goto", "remove", "back"

    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setIconSize(QSize(16, 16))
        self.setUniformItemSizes(True)

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, items):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(i.data(Qt.ItemDataRole.UserRole)) for i in items])
        return mime

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction

    def dragEnterEvent(self, event):
        if event.source() is self:
            event.ignore()  # no reordering by drag
        elif event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.LinkAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.source() is not self and event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.LinkAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.setDropAction(Qt.DropAction.LinkAction)  # the files stay where they are
            event.accept()
            self.files_dropped.emit(paths)

    def keyPressEvent(self, event):
        text, key = event.text(), event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        step = {"j": 1, "k": -1}.get(text) if not ctrl else None
        if step:
            row = max(0, min(self.currentRow() + step, self.count() - 1))
            extend = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            self.setCurrentRow(row, QItemSelectionModel.SelectionFlag.SelectCurrent if extend
                               else QItemSelectionModel.SelectionFlag.ClearAndSelect)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or text == "l":
            self.key_command.emit("goto")
        elif key == Qt.Key.Key_Delete or text in ("d", "x"):
            self.key_command.emit("remove")
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Tab) or text in ("q", "h"):
            self.key_command.emit("back")
        elif text == "G":
            self.setCurrentRow(self.count() - 1)
        elif text == "g":
            self.setCurrentRow(0)
        else:
            super().keyPressEvent(event)


class Shelf(QDockWidget):
    changed = Signal()  # items added or removed: save them
    goto = Signal(str)  # show this file in the active pane
    back = Signal()  # focus back to the pane
    command = Signal(str)  # a :shelf subcommand from the context menu

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Shelf", parent)
        self.setObjectName("shelf")
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                         | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.list = ShelfList()
        self.list.files_dropped.connect(self.add)
        self.list.key_command.connect(self._key_command)
        self.list.itemActivated.connect(lambda _: self._key_command("goto"))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setWidget(self.list)
        self.setMinimumWidth(180)
        self._icons = QFileIconProvider()
        self._paths: list[str] = []
        self._update_title()

    @property
    def paths(self) -> list[str]:
        return list(self._paths)

    def selected_paths(self) -> list[str]:
        """The selected items, or all of them when none are selected."""
        chosen = [i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()]
        return chosen or self.paths

    def set_paths(self, paths: list[str]) -> None:
        self._paths = []
        self.list.clear()
        self._append([p for p in paths if isinstance(p, str)])

    def add(self, paths: list[str]) -> int:
        """Put files on the shelf (each only once). Returns how many were new."""
        n = self._append(paths)
        if n:
            self.changed.emit()
        return n

    def _append(self, paths: list[str]) -> int:
        have = {os.path.normcase(p) for p in self._paths}
        added = 0
        for path in paths:
            path = os.path.abspath(path)
            if os.path.normcase(path) in have:
                continue
            have.add(os.path.normcase(path))
            self._paths.append(path)
            self.list.addItem(self._item(path))
            added += 1
        self._update_title()
        return added

    def _item(self, path: str) -> QListWidgetItem:
        name = os.path.basename(path.rstrip("/\\")) or path
        folder = os.path.basename(os.path.dirname(path)) or os.path.dirname(path)
        item = QListWidgetItem(f"{name}   ({folder})")  # the full path is the tooltip
        item.setToolTip(path)
        item.setData(Qt.ItemDataRole.UserRole, path)
        info = QFileInfo(path)
        icon = (self._icons.icon(QFileIconProvider.IconType.Folder) if os.path.isdir(path)
                else self._icons.icon(info))
        item.setIcon(icon)
        return item

    def remove(self, paths: list[str]) -> None:
        gone = {os.path.normcase(p) for p in paths}
        keep = [p for p in self._paths if os.path.normcase(p) not in gone]
        if len(keep) != len(self._paths):
            row = self.list.currentRow()
            self.set_paths(keep)
            self.list.setCurrentRow(min(row, self.list.count() - 1))
            self.changed.emit()

    def clear(self) -> None:
        if self._paths:
            self.set_paths([])
            self.changed.emit()

    def renamed(self, pairs: list[tuple[str, str]]) -> None:
        """Follow renamed and moved files (old path, new path)."""
        new = {os.path.normcase(a): b for a, b in pairs}
        if any(os.path.normcase(p) in new for p in self._paths):
            self.set_paths([new.get(os.path.normcase(p), p) for p in self._paths])
            self.changed.emit()

    def prune(self) -> None:
        """Drop files that no longer exist (trashed, deleted)."""
        self.remove([p for p in self._paths if not os.path.lexists(p)])

    def _update_title(self) -> None:
        self.setWindowTitle(f"Shelf ({len(self._paths)})" if self._paths else "Shelf - zs adds files")

    def _key_command(self, what: str) -> None:
        if what == "goto":
            item = self.list.currentItem()
            if item:
                self.goto.emit(item.data(Qt.ItemDataRole.UserRole))
        elif what == "remove":
            self.remove([i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()]
                        or ([self.list.currentItem().data(Qt.ItemDataRole.UserRole)]
                            if self.list.currentItem() else []))
        elif what == "back":
            self.back.emit()

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        some = bool(self._paths)
        which = "selected" if self.list.selectedItems() else "all"
        for label, cmd in ((f"Copy {which} into the pane's folder", "copy"),
                           (f"Move {which} into the pane's folder", "move"),
                           (f"Zip {which} into the pane's folder…", "zip"),
                           (f"Show {which} as a list in the pane", "list")):
            menu.addAction(label, lambda cmd=cmd: self.command.emit(cmd)).setEnabled(some)
        menu.addSeparator()
        menu.addAction("Go to the file\tEnter", lambda: self._key_command("goto")).setEnabled(some)
        menu.addAction("Take off the shelf\tDel", lambda: self._key_command("remove")).setEnabled(some)
        menu.addAction("Clear the shelf", self.clear).setEnabled(some)
        menu.exec(self.list.viewport().mapToGlobal(pos))

"""One file panel: tabs, drive button, path header, directory model and table view.

A pane lists a real folder or a folder inside a zip file (see archive.py), and
can show a quick view of the other pane's file instead of its list.
"""

import fnmatch
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime

from PySide6.QtCore import (QAbstractTableModel, QEvent, QFileInfo, QRect, QFileSystemWatcher, QItemSelection,
                            QItemSelectionModel, QMimeData, QModelIndex, QPoint, QSize, QStorageInfo, Qt,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileIconProvider, QHBoxLayout,
                               QHeaderView, QLabel, QListView, QListWidget, QListWidgetItem, QMenu, QSizePolicy,
                               QSplitter,
                               QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QTableView, QTabBar, QToolButton, QVBoxLayout, QWidget)

from . import archive, remote
from .grid import GridDelegate, ThumbLoader
from .preview import QuickView

_THUMBS: ThumbLoader | None = None  # thumbnails for both panes' grids, made with the first pane


def thumbs() -> ThumbLoader:
    global _THUMBS
    if _THUMBS is None:
        _THUMBS = ThumbLoader()
    return _THUMBS

FILE_ATTRIBUTE_HIDDEN = 0x2
WINDOWS = sys.platform == "win32"

# Folder sizes worked out with gs / gS, shared by both panes: path -> bytes.
DIR_SIZES: dict[str, int] = {}

# Colors from the config's "colors" section, resolved by set_colors(). None = not set.
COLORS: dict[str, QColor | None] = {}


def parse_color(text: str) -> QColor | None:
    """'#rrggbb', '#rrggbbaa' (CSS order) or a color name. None if empty; raises ValueError if bad."""
    text = str(text or "").strip()
    if not text:
        return None
    if re.fullmatch(r"#[0-9a-fA-F]{8}", text):
        text = "#" + text[7:9] + text[1:7]  # Qt wants #aarrggbb
    color = QColor(text)
    if not color.isValid():
        raise ValueError(text)
    return color


def set_colors(options: dict) -> list[str]:
    """Resolve the config's colors, with theme defaults. Returns the names of bad ones."""
    palette = QApplication.palette()
    # The accent color (Qt 6.6+) is the user's system accent; the Windows 11
    # theme's Highlight is a dim grey that hardly shows in dark mode.
    accent_role = getattr(QPalette.ColorRole, "Accent", QPalette.ColorRole.Highlight)
    highlight = palette.color(accent_role)
    highlight_text = QColor("#000000" if highlight.lightnessF() > 0.6 else "#ffffff")
    inactive = QColor(highlight)
    inactive.setAlpha(70)
    defaults = {
        "cursor": highlight, "cursor_text": highlight_text, "cursor_inactive": inactive,
        "tagged": QColor("#e53935"), "tagged_background": QColor(0xe5, 0x39, 0x35, 0x26),
        "search": QColor(0xff, 0xc4, 0x00, 0x5a), "hidden": QColor(128, 128, 128),
        "folder": None, "header_active": highlight, "header_active_text": highlight_text,
        "git_modified": QColor("#d98e04"), "git_new": QColor("#2ea043"),
        "git_conflict": QColor("#ff4f8b"), "git_ignored": QColor(128, 128, 128),
        "linked_missing": QColor("#a056e0"),
    }
    bad = []
    for key, default in defaults.items():
        try:
            COLORS[key] = parse_color(options.get(key, "")) or default
        except ValueError:
            COLORS[key] = default
            bad.append(f"{key}: {options.get(key)}")
    return bad


def css(color: QColor) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


@dataclass
class Results:
    """A result list shown in a pane instead of a folder: files from anywhere,
    from :find, :grep, the activity calendar, the file finder or the shelf."""
    title: str
    root: str  # the folder the search ran in; names are shown relative to it
    paths: list[str]
    notes: dict[str, str] = field(default_factory=dict)  # path -> shown after the name
    groups: dict[str, int] = field(default_factory=dict)  # path -> duplicate set number

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Results | None":
        try:
            return cls(str(d["title"]), str(d["root"]), [str(p) for p in d["paths"]],
                       dict(d.get("notes") or {}), {k: int(v) for k, v in (d.get("groups") or {}).items()})
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float
    hidden: bool = False
    shown: str = ""  # in a result list: the path relative to where the search ran


    @property
    def is_parent(self) -> bool:
        return self.name == ".."


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    size = float(n)
    for unit in ("K", "M", "G", "T"):
        size /= 1024
        if size < 1024 or unit == "T":
            return f"{size:.1f} {unit}"
    return ""


SORT_KEYS = {
    "name": lambda e: (e.shown or e.name).casefold(),
    "ext": lambda e: os.path.splitext(e.name)[1].casefold(),
    "size": lambda e: e.size,
    "time": lambda e: e.mtime,
}


class DirModel(QAbstractTableModel):
    COLUMNS = ("Name", "Size", "Modified")

    def __init__(self):
        super().__init__()
        self.entries: list[Entry] = []
        self.show_hidden = False
        self.sort_by = "name"
        self.sort_reverse = False
        self.in_archive = False
        self.remote = False  # listing an sftp:// folder
        self.date_format = "%Y-%m-%d %H:%M"
        self.filter: re.Pattern | None = None  # '=' filter: only matching names are listed
        self.filter_text = ""  # the filter as typed
        self.highlight: re.Pattern | None = None  # search matches to highlight (hlsearch)
        self.number = self.relative = False  # line numbers in the vertical header
        self.cursor_row = 0
        self.git: dict[str, str] = {}  # name -> "modified" | "new" | "conflict" | "ignored"; "*" = all
        self.unique: set[str] = set()  # linked panes: normcased names the other pane lacks
        self._icons = QFileIconProvider()
        self._icon_cache = {}
        self._bold = QFont()
        self._bold.setBold(True)

    def load(self, path: str) -> None:
        """List a directory, or a folder in an archive. Raises OSError if it cannot be read."""
        entries = []
        loc = None if remote.is_remote(path) else archive.locate(path)
        if remote.is_remote(path):
            for name, is_dir, size, mtime in remote.REMOTE.list_dir(path):
                hidden = name.startswith(".")
                if not hidden or self.show_hidden:
                    entries.append(Entry(name, remote.join(path, name), is_dir,
                                         DIR_SIZES.get(remote.join(path, name), 0) if is_dir else size,
                                         mtime, hidden))
        elif loc:
            for name, is_dir, size, mtime in archive.list_dir(*loc):
                hidden = name.startswith(".")
                if not hidden or self.show_hidden:
                    entries.append(Entry(name, os.path.join(path, name), is_dir, size, mtime, hidden))
        else:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        st = e.stat()
                    except OSError:
                        st = e.stat(follow_symlinks=False)
                    is_dir = e.is_dir()
                    hidden = e.name.startswith(".") or bool(
                        getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_HIDDEN)
                    if hidden and not self.show_hidden:
                        continue
                    size = DIR_SIZES.get(e.path, 0) if is_dir else st.st_size
                    entries.append(Entry(e.name, e.path, is_dir, size, st.st_mtime, hidden))
        self.in_archive = loc is not None
        self.remote = remote.is_remote(path)
        if self.filter:
            entries = [e for e in entries if self.filter.search(e.name)]
        entries.sort(key=SORT_KEYS["name"])
        if self.sort_by != "name" or self.sort_reverse:
            entries.sort(key=SORT_KEYS[self.sort_by], reverse=self.sort_reverse)
        entries.sort(key=lambda en: not en.is_dir)  # stable: folders first, order kept
        parent = remote.dirname(path)
        if parent != path:
            entries.insert(0, Entry("..", parent, True, 0, 0))
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def load_results(self, results: Results) -> None:
        """List the files of a result list that still exist ('..' leaves the list)."""
        entries = []
        for path in results.paths:
            try:
                st = os.stat(path)
            except OSError:
                continue
            is_dir = os.path.isdir(path)
            name = os.path.basename(path.rstrip("/\\")) or path
            try:
                shown = os.path.relpath(path, results.root)
            except ValueError:  # another drive on Windows
                shown = path
            if shown.startswith(".."):
                shown = path
            if note := results.notes.get(path):
                shown += f"   [{note}]"
            entries.append(Entry(name, path, is_dir, DIR_SIZES.get(path, 0) if is_dir else st.st_size,
                                 st.st_mtime, False, shown))
        self.in_archive = self.remote = False
        if self.filter:
            entries = [e for e in entries if self.filter.search(e.name)]
        if self.sort_by != "name" or self.sort_reverse:
            entries.sort(key=SORT_KEYS[self.sort_by], reverse=self.sort_reverse)
        entries.insert(0, Entry("..", results.root, True, 0, 0))
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def set_font(self, font: QFont) -> None:
        self._bold = QFont(font)
        self._bold.setBold(True)

    def row_of(self, name: str) -> int:
        for i, e in enumerate(self.entries):
            if e.name == name:
                return i
        return -1

    # Qt model interface

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.COLUMNS[section]
        # Line numbers like vim: absolute (row 1 = '..', as G counts), relative to
        # the cursor, or both (the cursor's row shows its absolute number).
        if self.relative and not (self.number and section == self.cursor_row):
            return str(abs(section - self.cursor_row))
        return str(section + 1)

    def refresh_numbers(self) -> None:
        if self.entries and (self.number or self.relative):
            self.headerDataChanged.emit(Qt.Orientation.Vertical, 0, len(self.entries) - 1)

    def refresh_colors(self) -> None:
        if self.entries:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.entries) - 1, self.columnCount() - 1),
                                  [Qt.ItemDataRole.ForegroundRole])

    def set_highlight(self, regex: re.Pattern | None) -> None:
        self.highlight = regex
        if self.entries:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.entries) - 1, 0),
                                  [Qt.ItemDataRole.BackgroundRole])

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        e = self.entries[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return e.shown or e.name
            if col == 1:
                if e.is_dir:
                    return human_size(DIR_SIZES[e.path]) if e.path in DIR_SIZES else "<DIR>"
                return human_size(e.size)
            if col == 2 and not e.is_parent:
                return datetime.fromtimestamp(e.mtime).strftime(self.date_format)
        elif role == Qt.ItemDataRole.DecorationRole and col == 0:
            return self._icon(e)
        elif role == Qt.ItemDataRole.TextAlignmentRole and col == 1:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        elif role == Qt.ItemDataRole.FontRole and e.is_dir:
            return self._bold
        elif role == Qt.ItemDataRole.ForegroundRole and not e.is_parent and (
                kind := self.git.get(e.name) or self.git.get("*")):
            return COLORS.get("git_" + kind)
        elif (role == Qt.ItemDataRole.ForegroundRole and self.unique and not e.is_parent
              and os.path.normcase(e.name) in self.unique):
            return COLORS.get("linked_missing")
        elif role == Qt.ItemDataRole.ToolTipRole and col == 0 and not e.is_parent and (
                kind := self.git.get(e.name) or self.git.get("*")):
            return f"git: {kind}"
        elif role == Qt.ItemDataRole.ForegroundRole and e.hidden:
            return COLORS.get("hidden") or QColor(128, 128, 128)
        elif role == Qt.ItemDataRole.ForegroundRole and e.is_dir and COLORS.get("folder"):
            return COLORS["folder"]
        elif (role == Qt.ItemDataRole.BackgroundRole and col == 0 and self.highlight
              and not e.is_parent and self.highlight.search(e.name)):
            return COLORS.get("search") or QColor(255, 196, 0, 90)
        return None

    def _icon(self, e: Entry):
        if e.is_dir:
            key = "<dir>"
        else:
            key = os.path.splitext(e.name)[1].lower()
            if key in (".exe", ".lnk", ".ico", ".desktop") and not self.remote:
                key = e.path  # these carry their own icons
        if key not in self._icon_cache:
            if e.is_dir:
                self._icon_cache[key] = self._icons.icon(QFileIconProvider.IconType.Folder)
            else:
                self._icon_cache[key] = self._icons.icon(QFileInfo(e.name if self.remote else e.path))
        return self._icon_cache[key]

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        e = self.entries[index.row()]
        if self.in_archive:
            return f
        if not e.is_parent and not self.remote:  # other apps can't read sftp:// files
            f |= Qt.ItemFlag.ItemIsDragEnabled
        if e.is_dir:
            f |= Qt.ItemFlag.ItemIsDropEnabled
        return f

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes})
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.entries[r].path) for r in rows
                      if not self.entries[r].is_parent])
        return mime

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction


def child_name(parent: str, path: str) -> str | None:
    """First component of `path` below `parent`, or None if it is not below it."""
    try:
        rel = os.path.relpath(path, parent)
    except ValueError:  # different drives on Windows
        return None
    if rel == "." or rel.startswith(".."):
        return None
    return rel.split(os.sep)[0]


class CursorDelegate(QStyledItemDelegate):
    """Draws the vim cursor row and tagged (selected) rows in the configured colors.

    Tagged rows: colored bold text, a tint and a stripe on the left. The cursor:
    a bar in the active pane, a faint bar in the other. A tagged row under the
    cursor keeps its stripe, so both stay visible.
    """

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # No focus frame and no hover highlight: only the cursor and tags are drawn.
        option.state &= ~(QStyle.StateFlag.State_HasFocus | QStyle.StateFlag.State_MouseOver)

    def paint(self, painter, option, index):
        view = self.parent()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        tagged = bool(opt.state & QStyle.StateFlag.State_Selected)
        opt.state &= ~QStyle.StateFlag.State_Selected  # we draw the tag colors ourselves
        background = text = None
        if tagged:
            background, text = COLORS.get("tagged_background"), COLORS.get("tagged")
            opt.font.setBold(True)
        if index.row() == view.currentIndex().row():
            if view.active:
                background, text = COLORS.get("cursor"), COLORS.get("cursor_text")
            else:
                background = COLORS.get("cursor_inactive")
        if tagged:
            # The view already painted the style's selection color under the row
            # (PE_PanelItemViewRow); cover it so only our colors show.
            painter.fillRect(option.rect, opt.palette.base())
        if background is not None:
            painter.fillRect(option.rect, background)
        if text is not None:
            opt.palette.setColor(QPalette.ColorRole.Text, text)
            opt.palette.setColor(QPalette.ColorRole.HighlightedText, text)
        view.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, view)
        if tagged and index.column() == 0 and COLORS.get("tagged") is not None:
            r = option.rect
            painter.fillRect(QRect(r.left(), r.top(), 4, r.height()), COLORS["tagged"])


class ViewBehavior:
    """What the list (FileView) and the thumbnail grid (GridView) share: mouse
    clicks that only move the cursor, dragging, the right-click signal, and drops.
    Mixed into a QAbstractItemView subclass that has `pane`, `files_dropped` and
    `context_menu`."""

    # Mouse: a plain click only moves the cursor. Ctrl+click toggles a tag and
    # Shift+click tags a range, as in Explorer. Tags are the view's selection.

    TAG_MODIFIERS = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    MOUSE_EVENTS = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                    QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove)

    def selectionCommand(self, index, event=None):
        if (event is not None and event.type() in self.MOUSE_EVENTS
                and not event.modifiers() & self.TAG_MODIFIERS):
            return QItemSelectionModel.SelectionFlag.NoUpdate
        return super().selectionCommand(index, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.setFocus()  # only make the pane active; the menu decides what is targeted
            event.accept()
            return
        pos = event.position().toPoint()
        self._press = pos if (event.button() == Qt.MouseButton.LeftButton
                              and self.indexAt(pos).isValid()
                              and not event.modifiers() & self.TAG_MODIFIERS) else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Start dragging ourselves: Qt only drags tagged rows, and a click no longer tags.
        press = self._press
        if (press is not None and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - press).manhattanLength()
                >= QApplication.startDragDistance()):
            self._press = None
            self.startDrag(self.model().supportedDragActions())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)

    def startDrag(self, actions):
        """Drag the tagged items if the cursor is on one of them, else the cursor's item."""
        model = self.model()
        current = self.currentIndex().row()
        rows = {i.row() for i in self.selectionModel().selectedRows()}
        if current not in rows:
            rows = {current}
        indexes = [model.index(r, 0) for r in sorted(rows) if r >= 0
                   and model.flags(model.index(r, 0)) & Qt.ItemFlag.ItemIsDragEnabled]
        if not indexes:
            return
        drag = QDrag(self)
        drag.setMimeData(model.mimeData(indexes))
        drag.exec(actions, Qt.DropAction.CopyAction)

    def contextMenuEvent(self, event):
        event.accept()
        if event.reason() != event.Reason.Mouse:
            return  # the Menu key and Shift-F10 arrive as keys (see vim.py)
        row = self.rowAt(event.pos().y())
        self.context_menu.emit(row, event.globalPos())

    # Drag and drop. Plain drop copies, Shift+drop moves.

    def _drop_action(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return Qt.DropAction.MoveAction
        return Qt.DropAction.CopyAction

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(self._drop_action(event))
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(self._drop_action(event))
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if self.pane.archive:
            self.pane.message.emit("Archives are read-only")
            event.ignore()
            return
        index = self.indexAt(event.position().toPoint())
        target = None if self.pane.results else self.pane.path
        if index.isValid():
            entry = self.pane.model.entries[index.row()]
            if entry.is_dir and not entry.is_parent and entry.path not in paths:
                target = entry.path
            elif entry.is_parent and not self.pane.results:
                target = entry.path
        if target is None:  # a result list isn't a folder
            self.pane.message.emit("Drop on a folder: a result list isn't a folder")
            event.ignore()
            return
        paths = [p for p in paths if os.path.normcase(os.path.dirname(p)) != os.path.normcase(target)]
        if not paths:
            event.ignore()
            return
        action = self._drop_action(event)
        event.setDropAction(action)
        event.accept()
        self.files_dropped.emit(paths, target, action == Qt.DropAction.MoveAction)


class FileView(ViewBehavior, QTableView):
    files_dropped = Signal(list, str, bool)  # paths, target dir, move?
    context_menu = Signal(int, QPoint)  # row (-1 = empty space), global position
    cursor_moved = Signal()

    def __init__(self, pane: "Pane"):
        super().__init__()
        self.pane = pane
        self.active = False
        self._press: QPoint | None = None  # where a plain left-click started, for dragging
        self.setItemDelegate(CursorDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setTabKeyNavigation(False)
        self.setShowGrid(False)
        self.setWordWrap(False)
        # The vertical header is the line-number gutter (:set number / relativenumber).
        gutter = self.verticalHeader()
        gutter.hide()
        gutter.setSectionsClickable(False)
        gutter.setDefaultAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        gutter.setDefaultSectionSize(self.fontMetrics().height() + 6)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def set_list_font(self, font: QFont) -> None:
        self.setFont(font)
        self.verticalHeader().setFont(font)
        self.verticalHeader().setDefaultSectionSize(QFontMetrics(font).height() + 6)

    def set_numbers(self, number: bool, relative: bool) -> None:
        model = self.model()
        model.number, model.relative = number, relative
        self.verticalHeader().setVisible(number or relative)
        model.cursor_row = self.currentIndex().row()
        model.refresh_numbers()

    def setup_header(self):
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setHighlightSections(False)

    def currentChanged(self, current, previous):
        super().currentChanged(current, previous)
        model = self.model()
        model.cursor_row = current.row()
        if model.relative:
            model.refresh_numbers()
        self.viewport().update()
        self.cursor_moved.emit()


class GridView(ViewBehavior, QListView):
    """The thumbnail grid: the same model and selection as the pane's list."""

    files_dropped = Signal(list, str, bool)
    context_menu = Signal(int, QPoint)
    zoom = Signal(int)  # Ctrl+wheel: +1 / -1

    def __init__(self, pane: "Pane", loader: ThumbLoader):
        super().__init__()
        self.pane = pane
        self.active = False
        self._press: QPoint | None = None
        self.thumb_size = 128
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(GridDelegate(self, loader))
        loader.ready.connect(self._thumbs_ready)
        self._repaint = QTimer(self, singleShot=True, interval=60)
        self._repaint.timeout.connect(self.viewport().update)

    def set_thumb_size(self, size: int) -> None:
        self.thumb_size = size
        text = QFontMetrics(self.font()).height()
        self.setGridSize(QSize(size + 22, size + text + 18))
        self.verticalScrollBar().setSingleStep(max(20, size // 4))
        self.viewport().update()

    def setFont(self, font: QFont) -> None:
        super().setFont(font)
        self.set_thumb_size(self.thumb_size)

    def columns(self) -> int:
        return max(1, self.viewport().width() // max(1, self.gridSize().width()))

    def visible_rows(self) -> tuple[int, int]:
        """First and last item rows that are (partly) on screen."""
        model = self.model()
        first = self.indexAt(QPoint(self.gridSize().width() // 2, 4)).row()
        last = -1
        vp = self.viewport()
        for y in (vp.height() - 4, vp.height() - self.gridSize().height() // 2):
            for x in range(vp.width() - 4, 0, -max(1, self.gridSize().width() // 2)):
                last = self.indexAt(QPoint(x, y)).row()
                if last >= 0:
                    break
            if last >= 0:
                break
        n = model.rowCount() if model else 0
        return max(first, 0), (last if last >= 0 else n - 1)

    def _thumbs_ready(self) -> None:
        if self.isVisible() and not self._repaint.isActive():
            self._repaint.start()

    def currentChanged(self, current, previous):
        super().currentChanged(current, previous)
        self.viewport().update()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


SKIP_MOUNTS = ("/proc", "/sys", "/dev", "/run", "/snap", "/boot", "/var/lib", "/tmp")
SKIP_FILESYSTEMS = {b"tmpfs", b"devtmpfs", b"squashfs", b"overlay", b"proc", b"sysfs",
                    b"cgroup2", b"autofs", b"tracefs", b"debugfs", b"fusectl", b"9p"}


def volumes() -> list[QStorageInfo]:
    """Drives on Windows; mounted disks on Linux and macOS, without system mounts."""
    out = []
    for v in QStorageInfo.mountedVolumes():
        if not v.isValid() or not v.isReady():
            continue
        root = v.rootPath()
        if not WINDOWS and root != "/" and (
                v.fileSystemType().data() in SKIP_FILESYSTEMS
                or (root.startswith(SKIP_MOUNTS) and not root.startswith("/run/media"))
                or root.startswith("/System/Volumes")):
            continue
        out.append(v)
    return sorted(out, key=lambda v: v.rootPath().casefold())


def volume_label(v: QStorageInfo) -> str:
    name = v.displayName() if v.displayName() != v.rootPath() else ""
    space = f"({human_size(v.bytesAvailable())} free of {human_size(v.bytesTotal())})"
    root = os.path.normpath(v.rootPath()) if WINDOWS else v.rootPath()
    return "   ".join(part for part in (root, name, space) if part)


class ColumnList(QListWidget):
    """A plain list of a folder's items, for the Miller columns."""

    LIMIT = 1000

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._icons = QFileIconProvider()
        self._folder_icon = self._icons.icon(QFileIconProvider.IconType.Folder)
        self._file_icon = self._icons.icon(QFileIconProvider.IconType.File)
        self.folder: str | None = None
        self.setMinimumWidth(60)

    def show_folder(self, folder: str, select: str | None, show_hidden: bool) -> None:
        if folder == self.folder and select is None:
            return
        self.folder = folder
        self.clear()
        if not folder:
            return
        try:
            if remote.is_remote(folder):
                items = [(n, d) for n, d, _, _ in remote.REMOTE.list_dir(folder)]
            else:
                loc = archive.locate(folder)
                items = ([(n, d) for n, d, _, _ in archive.list_dir(*loc)] if loc else
                         [(e.name, e.is_dir()) for e in os.scandir(folder)])
        except OSError as e:
            self.addItem(f"({e.strerror or e})")
            return
        items = [(n, d) for n, d in items if show_hidden or not n.startswith(".")]
        items.sort(key=lambda x: (not x[1], x[0].casefold()))
        bold = QFont(self.font())
        bold.setBold(True)
        for name, is_dir in items[:self.LIMIT]:
            item = QListWidgetItem(self._folder_icon if is_dir else self._file_icon, name)
            if is_dir:
                item.setFont(bold)
            self.addItem(item)
            if name == select:
                self.setCurrentItem(item)
        if len(items) > self.LIMIT:
            self.addItem(f"… {len(items) - self.LIMIT} more")
        if not items:
            self.addItem("(empty)")
        current = self.currentItem()
        if current:
            self.scrollToItem(current, QAbstractItemView.ScrollHint.PositionAtCenter)


class Pane(QWidget):
    message = Signal(str)
    folder_changed = Signal(str)  # a different folder was opened (for zoxide)
    listed = Signal()  # the list was (re)loaded: a folder, a reload or a result list

    def __init__(self, path: str, settings: dict | None = None):
        super().__init__()
        self.path = ""
        self.back_stack: list[str] = []
        self.forward_stack: list[str] = []
        self.model = DirModel()
        self.history_limit = 100
        self.archive: tuple[str, str] | None = None  # (zip file, inner path) when in an archive
        self.free = ""  # free space on this folder's drive, for the status bar
        self.prev_path = ""  # the folder before this one (Ctrl-^, :cd -)
        self.last_jump: tuple[str, str] | None = None  # (folder, name) before the last jump ('')
        self.results: Results | None = None  # a result list shown instead of the folder
        self.last_results: Results | None = None  # for :results
        self.branch = ""  # git branch of this folder, shown in the header
        self.linked = False  # :linkpanes is on (shown in the header)
        self.columns = False  # Miller columns: parent | list | preview
        self.remote: remote.Location | None = None  # set while showing an sftp:// folder
        if settings:
            self.configure(settings)

        # Tabs: the current tab's state lives in the pane, the others in self.tabs.
        self.tabs: list[dict] = [{}]
        self.tab = 0
        self.tabbar = QTabBar()
        self.tabbar.setDocumentMode(True)
        self.tabbar.setTabsClosable(True)
        self.tabbar.setMovable(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabbar.addTab("")
        self.tabbar.currentChanged.connect(self.switch_tab)
        self.tabbar.tabCloseRequested.connect(self.close_tab)
        self.tabbar.tabMoved.connect(self._tab_moved)
        self.tabbar.hide()

        self.drive_button = QToolButton()
        self.drive_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.drive_button.setAutoRaise(True)
        self.drive_button.setToolTip("Change drive (Alt-F1 / Alt-F2)")
        self.drive_button.clicked.connect(self.show_drive_menu)
        self.header = QLabel()
        self.header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # A long path must not make the pane wider than the divider allows.
        self.header.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.header.setMinimumWidth(40)
        self.header_box = QWidget()
        header_row = QHBoxLayout(self.header_box)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(0)
        header_row.addWidget(self.drive_button)
        header_row.addWidget(self.header, 1)

        self.view = FileView(self)
        self.view.setModel(self.model)
        self.view.setup_header()
        self.view.doubleClicked.connect(lambda _: self.window().run_action("open"))
        self.grid = GridView(self, thumbs())
        self.grid.setModel(self.model)
        self.grid.setSelectionModel(self.view.selectionModel())  # one cursor and one set of tags
        self.grid.doubleClicked.connect(lambda _: self.window().run_action("open"))
        self.grid_mode = False
        self.preview = QuickView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.preview)

        # Miller columns (:set columns): the parent folder on the left, the list,
        # and what is under the cursor on the right.
        self.parent_column = ColumnList()
        self.child_column = ColumnList()
        self.column_preview = QuickView()
        self.column_preview.title.hide()
        self.right_column = QStackedWidget()
        self.right_column.addWidget(self.child_column)
        self.right_column.addWidget(self.column_preview)
        self.columns_split = QSplitter()
        self.columns_split.setChildrenCollapsible(False)
        self.columns_split.addWidget(self.parent_column)
        self.columns_split.addWidget(self.stack)
        self.columns_split.addWidget(self.right_column)
        self.columns_split.setStretchFactor(0, 1)
        self.columns_split.setStretchFactor(1, 3)
        self.columns_split.setStretchFactor(2, 2)
        self.parent_column.hide()
        self.right_column.hide()
        self._column_timer = QTimer(self, singleShot=True, interval=80)
        self._column_timer.timeout.connect(self.update_columns)
        self.view.cursor_moved.connect(lambda: self.columns and self._column_timer.start())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabbar)
        layout.addWidget(self.header_box)
        layout.addWidget(self.columns_split, 1)

        self.watcher = QFileSystemWatcher(self)
        self._reload_timer = QTimer(self, singleShot=True, interval=200)
        self._reload_timer.timeout.connect(self.reload)
        self.watcher.directoryChanged.connect(lambda _: self._reload_timer.start())

        self.set_active(False)
        if not self.cd(path):
            self.cd(os.path.expanduser("~"))

    def configure(self, settings: dict) -> None:
        """Apply the pane options from the settings file. Call reload() afterwards."""
        sort = str(settings.get("sort", "name"))
        if sort.lstrip("-") in SORT_KEYS:
            self.model.sort_by, self.model.sort_reverse = sort.lstrip("-"), sort.startswith("-")
        self.model.show_hidden = bool(settings.get("show_hidden", False))
        self.model.date_format = str(settings.get("date_format") or "%Y-%m-%d %H:%M")
        self.history_limit = max(int(settings.get("history_limit", 100)), 1)

    # Navigation

    def cd(self, path: str, select: str | None = None, record: bool = True) -> bool:
        if remote.is_remote(path):
            try:
                path = remote.REMOTE.resolve(path)  # sftp://host/~ -> the home folder there
            except OSError as e:
                self.message.emit(f"Cannot open {path}: {e.strerror or e}")
                return False
            if remote.parse(path) is None:
                self.message.emit(f"Not a remote address: {path}")
                return False
        else:
            path = os.path.abspath(os.path.expanduser(path))
        if (path != self.path or self.results) and self.path:
            self.model.filter, self.model.filter_text = None, ""  # a filter belongs to its folder
        leaving = self.results
        try:
            self.model.load(path)
        except OSError as e:
            if leaving:
                self.model.load_results(leaving)
            self.message.emit(f"Cannot open {path}: {e.strerror or e}")
            return False
        self.results = None
        self.remote = remote.parse(path)
        if path != self.path:
            self.model.git = {}
            if self.grid_mode:
                thumbs().forget_queue()
        self.archive = None if self.remote else archive.locate(path)
        watch = self.archive[0] if self.archive else path
        if self.watcher.directories() or self.watcher.files():
            self.watcher.removePaths(self.watcher.directories() + self.watcher.files())
        if not self.remote:  # remote folders reload with Ctrl-l and after file operations
            self.watcher.addPath(watch)
        if record and self.path and path != self.path:
            self.back_stack = self.back_stack[-self.history_limit:] + [self.path]
            self.forward_stack.clear()
        changed = path != self.path
        if changed and self.path:
            self.prev_path = self.path
        self.path = path
        if changed:
            self.folder_changed.emit(path)
        self.update_drive()
        self.update_header()
        row = self.model.row_of(select) if select else -1
        self.set_cursor(row if row >= 0 else 0)
        if self.columns:
            self.update_columns()
        self.listed.emit()
        return True

    def show_results(self, results: Results, select: str | None = None) -> None:
        """Show a result list (an empty one fills in later with refresh_results())."""
        if not self.results:
            self.model.filter, self.model.filter_text = None, ""
        self.results = self.last_results = results
        self.archive = self.remote = None
        self.path = results.root
        self.model.git, self.branch = {}, ""
        self.model.load_results(results)
        if self.watcher.directories() or self.watcher.files():
            self.watcher.removePaths(self.watcher.directories() + self.watcher.files())
        self.update_drive()
        self.update_header()
        rows = [i for i, e in enumerate(self.model.entries) if e.path == select] if select else []
        self.set_cursor(rows[0] if rows else min(1, len(self.model.entries) - 1))
        if self.columns:
            self.update_columns()
        self.listed.emit()

    def refresh_results(self) -> None:
        """Reload the result list: new paths appear, removed files drop out;
        the cursor and the tags stay on the same files."""
        if not self.results:
            return
        current = self.current_entry()
        at_start = current is None or current.is_parent or (self.row == 1 and len(self.model.entries) <= 2)
        tagged = {e.path for e in self.selected_entries()}
        self.model.load_results(self.results)
        self.update_header()
        self.listed.emit()
        rows = {e.path: i for i, e in enumerate(self.model.entries) if not e.is_parent}
        if at_start:
            self.set_cursor(min(1, len(self.model.entries) - 1))
        else:
            self.set_cursor(rows.get(current.path, self.row))
        sel = self.view.selectionModel()
        for path in tagged:
            if path in rows:
                sel.select(self.model.index(rows[path], 0), QItemSelectionModel.SelectionFlag.Select
                           | QItemSelectionModel.SelectionFlag.Rows)

    def results_renamed(self, pairs: list[tuple[str, str]]) -> None:
        """Keep a result list's paths in step with renames (old path, new path)."""
        if self.results:
            new = {os.path.normcase(a): b for a, b in pairs}
            self.results.paths = [new.get(os.path.normcase(p), p) for p in self.results.paths]

    def leave_results(self) -> None:
        """Back to the folder the search ran in, with the cursor on the file it was on."""
        if self.results:
            entry = self.current_entry()
            name = child_name(self.results.root, entry.path) if entry and not entry.is_parent else None
            self.cd(self.results.root, select=name, record=False)

    def update_header(self) -> None:
        if self.results:
            n = len(self.model.entries) - 1
            extra = f"   [filter: {self.model.filter_text}]" if self.model.filter else ""
            self.header.setText(f"[{self.results.title}]   {n} item(s) in {self._shown_path()}{extra}")
            self.tabbar.setTabText(self.tab, self.results.title[:24])
            self.tabbar.setTabToolTip(self.tab, self.results.title)
            return
        sort = ""
        if self.model.sort_by != "name" or self.model.sort_reverse:
            sort = f"   [sort: {'-' if self.model.sort_reverse else ''}{self.model.sort_by}]"
        if self.model.filter:
            sort += f"   [filter: {self.model.filter_text}]"
        if self.branch:
            sort = f"   [git: {self.branch}]" + sort
        if self.linked:
            sort += "   [linked]"
        self.header.setText(self._shown_path() + sort)
        label = (remote.basename(self.path) or self.remote.host) if self.remote else \
            os.path.basename(self.path.rstrip("/\\")) or self.path
        self.tabbar.setTabText(self.tab, label if len(label) <= 24 else label[:22] + "…")
        self.tabbar.setTabToolTip(self.tab, self.path)

    def set_git(self, kinds: dict[str, str], branch: str) -> None:
        self.model.git = kinds
        self.branch = branch
        self.model.refresh_colors()
        self.update_header()

    def _shown_path(self) -> str:
        if self.remote:
            return f"{self.remote.key}:{self.remote.path}"
        home = os.path.expanduser("~")
        if child_name(home, self.path) is not None or self.path == home:
            return "~" + self.path[len(home):]
        return self.path

    def update_drive(self) -> None:
        if self.remote:
            self.drive_button.setText("ssh " + self.remote.host)
            self.free = ""
            return
        info = QStorageInfo(self.archive[0] if self.archive else self.path)
        root = info.rootPath() if info.isValid() else ""
        self.drive_button.setText(root.rstrip("/\\") if WINDOWS else (root or "/"))
        self.free = f"{human_size(info.bytesAvailable())} free" if info.isValid() else ""

    def show_drive_menu(self) -> None:
        menu = QMenu(self)
        current = QStorageInfo(self.path).rootPath()
        for v in volumes():
            act = menu.addAction(volume_label(v), lambda root=v.rootPath(): self._go_drive(root))
            act.setCheckable(True)
            act.setChecked(v.rootPath() == current)
        home = os.path.expanduser("~")
        menu.addSeparator()
        menu.addAction(f"Home   {home}", lambda: self._go_drive(home))
        menu.exec(self.drive_button.mapToGlobal(self.drive_button.rect().bottomLeft()))

    def _go_drive(self, root: str) -> None:
        self.cd(root)
        self.view.setFocus()

    # Tabs

    def _save_tab(self) -> None:
        entry = self.current_entry()
        self.tabs[self.tab] = {
            "path": self.path, "back": list(self.back_stack), "forward": list(self.forward_stack),
            "sort": (self.model.sort_by, self.model.sort_reverse),
            "select": entry.name if entry else None,
            "prev": self.prev_path, "jump": self.last_jump,
            "results": self.results, "select_path": entry.path if entry else None,
        }

    def _load_tab(self, index: int) -> None:
        self.tab = index
        state = self.tabs[index]
        self.back_stack, self.forward_stack = list(state["back"]), list(state["forward"])
        self.model.sort_by, self.model.sort_reverse = state["sort"]
        if state.get("results"):
            self.show_results(state["results"], select=state.get("select_path"))
        elif not self.cd(state["path"], select=state.get("select"), record=False):
            self.cd(os.path.expanduser("~"), record=False)
        self.prev_path, self.last_jump = state.get("prev", ""), state.get("jump")
        self.tabbar.blockSignals(True)
        self.tabbar.setCurrentIndex(index)
        self.tabbar.blockSignals(False)

    def switch_tab(self, index: int) -> None:
        if index == self.tab or not 0 <= index < len(self.tabs):
            return
        self._save_tab()
        self._load_tab(index)

    def new_tab(self, path: str | None = None) -> None:
        self._save_tab()
        state = dict(self.tabs[self.tab], back=[], forward=[], select=None, results=None)
        if path:
            state["path"] = path
        index = self.tab + 1
        self.tabs.insert(index, state)
        self.tabbar.blockSignals(True)
        self.tabbar.insertTab(index, "")
        self.tabbar.blockSignals(False)
        self.tabbar.setVisible(True)
        self._load_tab(index)

    def close_tab(self, index: int | None = None) -> bool:
        index = self.tab if index is None else index
        if len(self.tabs) == 1:
            return False
        if index != self.tab:
            self._save_tab()
        del self.tabs[index]
        self.tabbar.blockSignals(True)
        self.tabbar.removeTab(index)
        self.tabbar.blockSignals(False)
        if index == self.tab:
            self._load_tab(min(index, len(self.tabs) - 1))
        elif index < self.tab:
            self.tab -= 1
        self.tabbar.setVisible(len(self.tabs) > 1)
        return True

    def _tab_moved(self, old: int, new: int) -> None:
        self.tabs.insert(new, self.tabs.pop(old))
        self.tab = self.tabbar.currentIndex()

    def move_tab(self, index: int) -> None:
        if index != self.tab:
            self.tabbar.moveTab(self.tab, index)  # tabMoved keeps self.tabs in step

    def tab_paths(self) -> list[str]:
        self._save_tab()
        return [t["path"] for t in self.tabs]

    def set_tabs(self, paths: list[str], index: int) -> None:
        """Open saved tabs; the first one replaces the current folder."""
        paths = [p for p in paths if isinstance(p, str) and os.path.isdir(p)]
        if not paths:
            return
        self.cd(paths[0], record=False)
        for p in paths[1:]:
            self.new_tab(p)
        self.switch_tab(max(0, min(index, len(self.tabs) - 1)))

    def set_font(self, font: QFont, text_font: QFont) -> None:
        """Font for the file list and path header; `text_font` for quick view text."""
        self.view.set_list_font(font)
        self.grid.setFont(font)
        self.model.set_font(font)
        self.header.setFont(font)
        self.drive_button.setFont(font)
        self.preview.text.setFont(text_font)
        self.column_preview.text.setFont(text_font)
        for column in (self.parent_column, self.child_column):
            column.setFont(font)
            column.folder = None  # refill with the new font

    # Miller columns

    def set_columns(self, on: bool) -> None:
        self.columns = on
        self.parent_column.setVisible(on)
        self.right_column.setVisible(on)
        if on:
            self.columns_split.setSizes([180, 520, 360])
            self.update_columns()
        else:
            self.column_preview.clear()

    def update_columns(self) -> None:
        if not self.columns:
            return
        hidden = self.model.show_hidden
        if self.results:
            self.parent_column.show_folder(self.results.root, None, hidden)
        else:
            parent = remote.dirname(self.path)
            self.parent_column.show_folder(parent if parent != self.path else "",
                                           remote.basename(self.path), hidden)
        entry = self.current_entry()
        if entry is None or entry.is_parent:
            self.child_column.show_folder("", None, hidden)
            self.right_column.setCurrentWidget(self.child_column)
        elif entry.is_dir:
            self.child_column.show_folder(entry.path, None, hidden)
            self.right_column.setCurrentWidget(self.child_column)
        else:
            self.right_column.setCurrentWidget(self.column_preview)
            self.column_preview.show_path(entry.path, False)

    # Sessions

    def session_state(self) -> dict:
        """The tabs of this pane, for :mksession."""
        self._save_tab()
        return {"tabs": [{"path": t["path"], "select": t.get("select"),
                          "results": t["results"].to_dict() if t.get("results") else None}
                         for t in self.tabs], "index": self.tab}

    def load_session(self, state: dict) -> None:
        tabs = [t for t in state.get("tabs") or [] if isinstance(t, dict) and isinstance(t.get("path"), str)]
        if not tabs:
            return
        self.tabbar.blockSignals(True)
        while self.tabbar.count() > 1:
            self.tabbar.removeTab(self.tabbar.count() - 1)
        self.tabbar.setCurrentIndex(0)
        self.tabbar.blockSignals(False)
        self.tabs, self.tab = [{}], 0
        self.back_stack, self.forward_stack = [], []
        for i, t in enumerate(tabs):
            results = Results.from_dict(t["results"]) if t.get("results") else None
            if i:
                self.new_tab(t["path"] if os.path.isdir(t["path"]) else None)
            if results:
                self.show_results(results)
            elif not self.cd(t["path"], select=t.get("select"), record=False):
                self.cd(os.path.expanduser("~"), record=False)
        self.tabbar.setVisible(len(self.tabs) > 1)
        index = state.get("index", 0)
        self.switch_tab(max(0, min(index if isinstance(index, int) else 0, len(self.tabs) - 1)))

    # Quick view

    @property
    def list_view(self) -> QAbstractItemView:
        """The list or the thumbnail grid, whichever is shown."""
        return self.grid if self.grid_mode else self.view

    def set_grid(self, on: bool) -> None:
        had_focus = self.view.hasFocus() or self.grid.hasFocus()
        self.grid_mode = on
        if self.stack.currentWidget() is not self.preview:
            self.stack.setCurrentWidget(self.list_view)
        # pane.view.setFocus() (used everywhere) reaches the grid while it is shown
        self.view.setFocusProxy(self.grid if on else None)
        if had_focus:
            self.list_view.setFocus()
        self.list_view.scrollTo(self.model.index(self.row, 0))
        thumbs().forget_queue()

    def row_step(self) -> int:
        """Rows that j / k move: a whole line of tiles in the grid."""
        return self.grid.columns() if self.grid_mode else 1

    def visible_range(self) -> tuple[int, int]:
        """First and last rows on screen."""
        if self.grid_mode:
            return self.grid.visible_rows()
        view = self.view
        top = max(view.rowAt(0), 0)
        bottom = view.rowAt(view.viewport().height() - 1)
        return top, bottom if bottom >= 0 else len(self.model.entries) - 1

    def show_preview(self, on: bool) -> None:
        self.stack.setCurrentWidget(self.preview if on else self.list_view)
        if not on:
            self.preview.clear()

    def _history_step(self, from_stack: list[str], to_stack: list[str]) -> bool:
        while from_stack:
            target = from_stack.pop()
            left = self.path
            select = remote.basename(left) if remote.is_remote(target) and remote.dirname(left) == target \
                else child_name(target, left)
            if self.cd(target, select=select, record=False):
                to_stack.append(left)
                return True
        return False

    def go_back(self) -> bool:
        return self._history_step(self.back_stack, self.forward_stack)

    def go_forward(self) -> bool:
        return self._history_step(self.forward_stack, self.back_stack)

    def set_filter(self, regex: re.Pattern | None, text: str = "") -> None:
        self.model.filter, self.model.filter_text = regex, text if regex else ""
        self.reload()

    def set_sort(self, key: str, reverse: bool) -> None:
        self.model.sort_by, self.model.sort_reverse = key, reverse
        self.reload()

    def reload(self) -> None:
        if self.results:
            self.refresh_results()
            return
        current = self.current_entry()
        tagged = {e.name for e in self.selected_entries()}
        if not self._exists(self.path):  # directory was removed under us
            parent = self.path
            while not self._exists(parent) and remote.dirname(parent) != parent:
                parent = remote.dirname(parent)
            self.cd(parent)
            return
        self.cd(self.path, current.name if current else None, record=False)
        sel = self.view.selectionModel()
        for name in tagged:
            row = self.model.row_of(name)
            if row >= 0:
                sel.select(self.model.index(row, 0), QItemSelectionModel.SelectionFlag.Select
                           | QItemSelectionModel.SelectionFlag.Rows)

    @staticmethod
    def _exists(path: str) -> bool:
        if remote.is_remote(path):
            try:
                return remote.REMOTE.is_dir(path)
            except OSError:
                return False
        return os.path.isdir(path) or archive.locate(path) is not None

    # Cursor and selection

    @property
    def row(self) -> int:
        return max(self.view.currentIndex().row(), 0)

    def set_cursor(self, row: int) -> None:
        if not self.model.entries:
            return
        row = max(0, min(row, len(self.model.entries) - 1))
        index = self.model.index(row, 0)
        self.view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.list_view.scrollTo(index)

    def visible_rows(self) -> int:
        if self.grid_mode:
            g = self.grid
            return max(1, g.viewport().height() // max(1, g.gridSize().height())) * g.columns()
        return max(1, self.view.viewport().height() // self.view.verticalHeader().defaultSectionSize())

    def current_entry(self) -> Entry | None:
        if not self.model.entries:
            return None
        return self.model.entries[self.row]

    def selected_entries(self) -> list[Entry]:
        rows = sorted({i.row() for i in self.view.selectionModel().selectedRows()})
        return [self.model.entries[r] for r in rows if not self.model.entries[r].is_parent]

    def targets(self, count: int = 1) -> list[Entry]:
        """Tagged entries, or `count` entries starting at the cursor."""
        selected = self.selected_entries()
        if selected:
            return selected
        return [e for e in self.model.entries[self.row:self.row + count] if not e.is_parent]

    def toggle_tag(self) -> None:
        self.view.selectionModel().select(
            self.model.index(self.row, 0),
            QItemSelectionModel.SelectionFlag.Toggle | QItemSelectionModel.SelectionFlag.Rows)

    def select_range(self, a: int, b: int) -> None:
        a, b = sorted((a, b))
        last_col = self.model.columnCount() - 1
        self.view.selectionModel().select(
            QItemSelection(self.model.index(a, 0), self.model.index(b, last_col)),
            QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def tag_matching(self, pattern: str, select: bool = True) -> int:
        """Tag (or untag) entries whose name matches a shell pattern like *.txt."""
        pattern = pattern.casefold()
        flag = QItemSelectionModel.SelectionFlag.Select if select else QItemSelectionModel.SelectionFlag.Deselect
        count = 0
        for row, e in enumerate(self.model.entries):
            if not e.is_parent and fnmatch.fnmatchcase(e.name.casefold(), pattern):
                self.view.selectionModel().select(self.model.index(row, 0),
                                                  flag | QItemSelectionModel.SelectionFlag.Rows)
                count += 1
        return count

    def tag_names(self, names: set[str]) -> int:
        """Tag exactly the items with these names (case as the file system sees it)."""
        self.clear_tags()
        wanted = {os.path.normcase(n) for n in names}
        sel = self.view.selectionModel()
        count = 0
        for row, e in enumerate(self.model.entries):
            if not e.is_parent and os.path.normcase(e.name) in wanted:
                sel.select(self.model.index(row, 0), QItemSelectionModel.SelectionFlag.Select
                           | QItemSelectionModel.SelectionFlag.Rows)
                count += 1
        return count

    def invert_tags(self) -> None:
        last_col = self.model.columnCount() - 1
        selection = QItemSelection()
        for row, e in enumerate(self.model.entries):
            if not e.is_parent:
                selection.select(self.model.index(row, 0), self.model.index(row, last_col))
        self.view.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.Toggle)

    def scroll_cursor(self, hint: QAbstractItemView.ScrollHint) -> None:
        self.list_view.scrollTo(self.model.index(self.row, 0), hint)

    def clear_tags(self) -> None:
        self.view.selectionModel().clearSelection()

    def set_active(self, active: bool) -> None:
        self.view.active = self.grid.active = active
        self.grid.viewport().update()
        if active:
            background = COLORS.get("header_active")
            text = COLORS.get("header_active_text")
            self.header.setStyleSheet(
                f"background: {css(background) if background else 'palette(highlight)'};"
                f"color: {css(text) if text else 'palette(highlighted-text)'};"
                "padding: 3px 6px; font-weight: bold;")
        else:
            self.header.setStyleSheet("padding: 3px 6px;")
        self.view.viewport().update()

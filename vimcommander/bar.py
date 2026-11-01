"""Launch bar: icon buttons for favourite folders and programs, like Total Commander's button bar.

Clicking a folder opens it in the active pane; clicking a program runs it.
Dropping files on a program opens them with it, dropping on a folder copies
them there (Shift moves), and dropping on empty space adds new buttons.

Program arguments may contain placeholders:
  %d current folder   %o other pane's folder   %f file under cursor   %s selected files
"""

import os
import shlex
import shutil
import sys
from functools import cache
from dataclasses import asdict, dataclass, fields

from PySide6.QtCore import QFileInfo, QProcess, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFileIconProvider,
                               QFormLayout, QHBoxLayout, QLineEdit, QMenu, QPushButton, QStyle,
                               QToolBar, QToolButton, QWidget)

REMOTE_SCHEMES = ("sftp://", "ftp://", "ftps://")  # remote.SCHEMES (bar.py stays free of the app's modules)

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
PROGRAM_EXTS = {e.lower() for e in os.environ.get("PATHEXT", ".EXE;.BAT;.CMD;.COM").split(";")}


@dataclass
class BarItem:
    name: str
    path: str
    args: str = ""
    icon: str = ""  # optional custom icon file

    @classmethod
    def from_dict(cls, d: dict) -> "BarItem":
        known = {f.name for f in fields(cls)}
        return cls(**{k: str(v) for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def full_path(self) -> str:
        return self.path if self.path.startswith(REMOTE_SCHEMES) else os.path.expanduser(self.path)

    @property
    def is_folder(self) -> bool:
        if self.path.startswith(REMOTE_SCHEMES):  # a remote folder (:ssh, :ftp)
            return True
        return os.path.isdir(self.full_path) and not (MAC and self.path.endswith(".app"))


def item_for_path(path: str) -> BarItem:
    if path.startswith(REMOTE_SCHEMES):
        host = path.split("://", 1)[1].split("/", 1)[0].split("@")[-1]
        return BarItem(f"{host}:{os.path.basename(path.rstrip('/')) or '/'}", path)
    name = os.path.basename(path.rstrip("/\\")) or path
    if not os.path.isdir(path):
        name = os.path.splitext(name)[0]
    return BarItem(name, path)


def default_terminal() -> str:
    """A terminal command for this platform, with %d for the folder, or ""."""
    if WINDOWS:
        return "wt.exe -d %d" if shutil.which("wt.exe") else "cmd.exe"
    if MAC:
        return "/System/Applications/Utilities/Terminal.app %d"
    # $TERMINAL is the usual way to name a preferred terminal (i3, sway, many scripts)
    preferred = os.environ.get("TERMINAL", "").strip()
    if preferred:
        tokens = split_command(preferred)
        if tokens and shutil.which(os.path.expanduser(tokens[0])):
            return preferred
    for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
                 "kitty", "alacritty", "xterm"):
        if shutil.which(term):
            return term
    return ""


def default_items() -> list[BarItem]:
    home = os.path.expanduser("~")
    items = [BarItem("Home", home)]
    for sub in ("Desktop", "Documents", "Downloads"):
        if os.path.isdir(os.path.join(home, sub)):
            items.append(BarItem(sub, os.path.join(home, sub)))
    items.append(BarItem("Root", os.environ.get("SystemDrive", "C:") + "\\" if WINDOWS else "/"))
    if terminal := split_command(default_terminal()):
        items.append(BarItem("Terminal", terminal[0], " ".join(terminal[1:])))
    return items


def _resolve_program(path: str) -> str | None:
    """Full path of a runnable program, or None if `path` is a document."""
    resolved = path if os.path.isfile(path) else shutil.which(path)
    if not resolved:
        return None
    if WINDOWS:
        return resolved if os.path.splitext(resolved)[1].lower() in PROGRAM_EXTS else None
    return resolved if os.access(resolved, os.X_OK) else None


FOLDER_THEME_ICONS = {
    "~": "user-home", "Desktop": "user-desktop", "Documents": "folder-documents",
    "Downloads": "folder-download", "Music": "folder-music", "Pictures": "folder-pictures",
    "Videos": "folder-videos", "/": "drive-harddisk",
}


@cache
def _provider() -> QFileIconProvider:
    return QFileIconProvider()


def icon_for(item: BarItem, style: QStyle) -> QIcon:
    if item.icon and os.path.isfile(os.path.expanduser(item.icon)):
        return QIcon(os.path.expanduser(item.icon))
    path = item.full_path
    if item.is_folder:
        home = os.path.expanduser("~")
        key = "~" if path == home else "/" if path == "/" else (
            os.path.basename(path) if os.path.dirname(path) == home else None)
        if key and not WINDOWS and not MAC:
            themed = QIcon.fromTheme(FOLDER_THEME_ICONS.get(key, ""))
            if not themed.isNull():
                return themed
        return _provider().icon(QFileInfo(path))
    program = _resolve_program(path)
    if program and not WINDOWS and not MAC:
        themed = QIcon.fromTheme(os.path.splitext(os.path.basename(program))[0])
        if not themed.isNull():
            return themed
    target = program or path
    if os.path.exists(target):
        return _provider().icon(QFileInfo(target))
    return style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)


def split_command(command: str | list) -> list[str]:
    """Split "prog arg1 arg2" into tokens. A list is taken as already split.

    On Windows, backslashes stay as they are and quotes around a token
    ("C:\\Program Files\\x.exe") are removed.
    """
    if isinstance(command, list):
        return [str(t) for t in command]
    if not command.strip():
        return []
    if not WINDOWS:
        return shlex.split(command)
    return [t[1:-1] if len(t) > 1 and t[0] == t[-1] == '"' else t
            for t in shlex.split(command, posix=False)]


def expand_args(template: str | list, ctx: dict, files: list[str]) -> list[str]:
    tokens = split_command(template)
    out = []
    for t in tokens:
        if t == "%s":
            out += files
        else:
            out.append(t.replace("%d", ctx["cwd"]).replace("%o", ctx["other"])
                        .replace("%f", ctx["current"]).replace("%l", str(ctx.get("line", ""))))
    if files and "%s" not in tokens and ctx.get("dropped"):
        out += files
    return out


def launch(item: BarItem, ctx: dict, files: list[str]) -> bool:
    """Run a program item (or open a document). Returns False on failure."""
    return start_program(item.full_path, expand_args(item.args, ctx, files), ctx["cwd"])


def run_command(command: str | list, ctx: dict, files: list[str] | None = None) -> bool:
    """Run a configured command line such as "code %f" or ["C:/x y/app.exe", "%s"].

    `files` are appended when the command has no %f or %s placeholder.
    """
    tokens = split_command(command)
    if not tokens:
        return False
    ctx = {**ctx, "dropped": bool(files) and not any("%f" in t for t in tokens)}
    return start_program(os.path.expanduser(tokens[0]),
                         expand_args(tokens[1:], ctx, files or []), ctx["cwd"])


def start_program(path: str, args: list[str], cwd: str) -> bool:
    if MAC and path.endswith(".app"):
        return _start("open", ["-a", path, *args], cwd)
    program = _resolve_program(path)
    if program and _start(program, args, cwd):
        return True
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _start(program: str, args: list[str], cwd: str) -> bool:
    result = QProcess.startDetached(program, args, cwd)
    return result[0] if isinstance(result, tuple) else bool(result)


class EditDialog(QDialog):
    def __init__(self, item: BarItem, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Bar button")
        self.setMinimumWidth(520)
        self.name = QLineEdit(item.name)
        self.path = QLineEdit(item.path)
        self.args = QLineEdit(item.args)
        self.args.setPlaceholderText("%d folder, %o other pane, %f file, %s selected files")
        self.icon = QLineEdit(item.icon)
        self.icon.setPlaceholderText("optional .png / .ico / .svg")

        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Path or command", self._with_buttons(self.path, [
            ("File…", self._browse_file), ("Folder…", self._browse_folder)]))
        form.addRow("Arguments", self.args)
        form.addRow("Icon", self._with_buttons(self.icon, [("…", self._browse_icon)]))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _with_buttons(self, edit: QLineEdit, buttons) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        for label, slot in buttons:
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        return box

    def _set_path(self, path: str) -> None:
        if path:
            self.path.setText(path)
            if not self.name.text():
                self.name.setText(item_for_path(path).name)

    def _browse_file(self):
        self._set_path(QFileDialog.getOpenFileName(self, "Choose program or file")[0])

    def _browse_folder(self):
        self._set_path(QFileDialog.getExistingDirectory(self, "Choose folder"))

    def _browse_icon(self):
        path = QFileDialog.getOpenFileName(self, "Choose icon", "",
                                           "Images (*.png *.ico *.svg *.jpg *.xpm)")[0]
        if path:
            self.icon.setText(path)

    def result_item(self) -> BarItem:
        path = self.path.text().strip()
        name = self.name.text().strip() or item_for_path(path).name
        return BarItem(name, path, self.args.text().strip(), self.icon.text().strip())


class LaunchBar(QToolBar):
    folder_requested = Signal(str)
    files_dropped = Signal(list, str, bool)  # paths, target folder, move?
    message = Signal(str)
    changed = Signal()

    def __init__(self, items: list[BarItem], show_labels: bool = True):
        super().__init__("Launch bar")
        self.items = items
        self.show_labels = show_labels
        self.context_provider = lambda: {"cwd": os.getcwd(), "other": os.getcwd(),
                                         "current": "", "selected": []}
        self.setObjectName("launchbar")
        self.setMovable(False)
        self.setIconSize(QSize(20, 20))
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.on_context_menu)
        self.rebuild()

    def rebuild(self) -> None:
        self.clear()
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if self.show_labels
                                else Qt.ToolButtonStyle.ToolButtonIconOnly)
        for i, item in enumerate(self.items):
            act = QAction(icon_for(item, self.style()), item.name, self)
            tip = f"{item.name}\n{item.path} {item.args}".rstrip()
            if i < 9:
                tip += f"\nShortcut: g{i + 1}"
            act.setToolTip(tip)
            act.setData(i)
            act.triggered.connect(lambda _=False, i=i: self.activate(i))
            self.addAction(act)
            self.widgetForAction(act).setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if not self.items:
            hint = self.addAction("Drop folders or programs here")
            hint.setEnabled(False)

    # Running buttons

    def activate(self, index: int, files: list[str] | None = None) -> None:
        if not 0 <= index < len(self.items):
            self.message.emit(f"No bar button {index + 1}")
            return
        item = self.items[index]
        if item.is_folder:
            if files:
                self.files_dropped.emit(files, item.full_path, False)
            else:
                self.folder_requested.emit(item.full_path)
            return
        ctx = self.context_provider()
        ctx["dropped"] = files is not None
        if launch(item, ctx, files if files is not None else ctx["selected"]):
            self.message.emit(f"Started {item.name}")
        else:
            self.message.emit(f"Could not start {item.name} ({item.path})")

    # Editing

    def add_item(self, item: BarItem, index: int | None = None) -> None:
        self.items.insert(len(self.items) if index is None else index, item)
        self.rebuild()
        self.changed.emit()

    def edit_item(self, index: int | None) -> None:
        item = self.items[index] if index is not None else BarItem("", "")
        dialog = EditDialog(item, self.window())
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_item().path:
            return
        if index is None:
            self.items.append(dialog.result_item())
        else:
            self.items[index] = dialog.result_item()
        self.rebuild()
        self.changed.emit()

    def remove_item(self, index: int) -> None:
        del self.items[index]
        self.rebuild()
        self.changed.emit()

    def move_item(self, index: int, delta: int) -> None:
        new = index + delta
        if 0 <= new < len(self.items):
            self.items[index], self.items[new] = self.items[new], self.items[index]
            self.rebuild()
            self.changed.emit()

    def index_at(self, pos) -> int | None:
        w = self.childAt(pos)
        if isinstance(w, QToolButton) and w.defaultAction():
            data = w.defaultAction().data()
            if isinstance(data, int):
                return data
        return None

    def on_context_menu(self, pos) -> None:
        index = self.index_at(pos)
        menu = QMenu(self)
        if index is not None:
            item = self.items[index]
            menu.addAction("Open" if item.is_folder else "Run", lambda: self.activate(index))
            menu.addAction("Edit…", lambda: self.edit_item(index))
            menu.addAction("Remove", lambda: self.remove_item(index))
            menu.addAction("Move left", lambda: self.move_item(index, -1)).setEnabled(index > 0)
            menu.addAction("Move right", lambda: self.move_item(index, 1)).setEnabled(
                index < len(self.items) - 1)
            menu.addSeparator()
        menu.addAction("Add current folder",
                       lambda: self.add_item(item_for_path(self.context_provider()["cwd"])))
        menu.addAction("Add button…", lambda: self.edit_item(None))
        labels = menu.addAction("Show labels")
        labels.setCheckable(True)
        labels.setChecked(self.show_labels)
        labels.toggled.connect(self.set_show_labels)
        menu.exec(self.mapToGlobal(pos))

    def set_show_labels(self, on: bool) -> None:
        self.show_labels = on
        self.rebuild()
        self.changed.emit()

    # Drag and drop

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if not paths:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        index = self.index_at(event.position().toPoint())
        if index is None:
            for p in paths:
                self.add_item(item_for_path(p))
        elif self.items[index].is_folder:
            move = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.files_dropped.emit(paths, self.items[index].full_path, move)
        else:
            self.activate(index, paths)

"""Main window: two panes, vim key dispatch, command line and status bar."""

import fnmatch
import os
import posixpath
import re
import shlex
import stat
import subprocess
import sys
import time
import traceback
import zipfile
from collections import deque
from datetime import datetime

from PySide6.QtCore import QEvent, QFile, QFileSystemWatcher, QObject, QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout, QInputDialog, QLabel, QSizePolicy,
                               QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QSplitter, QVBoxLayout,
                               QWidget)

import tempfile

from . import archive, cleanup, clipboard, config, gitstatus, graphview, pdftools, preview, remote, rules
from .actions import ACTIONS, ALIASES, COMMANDS, needs_argument, resolve
from .activity import ActivityDialog
from .bulkrename import BulkRenameDialog
from .compare import compare_trees
from .diffview import DiffDialog
from .grepdialog import GrepDialog
from .palette import FileFinder, PaletteDialog
from .shelf import Shelf
from .terminal import Terminal
from .sync import SyncDialog
from .whichkey import WhichKey, pretty
from .bar import BarItem, LaunchBar, default_items, default_terminal, item_for_path, run_command
from . import music, termshell, theme, trash, wsl, zoxide
from .dialogs import PackDialog, ProgressDialog, TrashDialog, UnpackDialog, ZoxideDialog, ask_conflict
from .jobs import QUEUED_OPS, JobsPanel
from .fileops import HASHES, Job, SizeJob, unique_dest
from .pane import DIR_SIZES, NATURAL, SORT_KEYS, Entry, Pane, Results, child_name, human_size, set_colors
from .results import Search, branch_paths, changed_files, find_paths, parse_find
from .rename import CASES, check_names, format_name, increment, parse_substitute
from .vim import NORMAL, VISUAL, Action, KeyEngine, parse_langmap, split_keys, user_bindings
from .rulesdialog import RulesDialog, group_steps
from .sizing import fit_window

WINDOWS = sys.platform == "win32"
if WINDOWS:
    from . import winshell

SPECIAL_KEYS = {
    Qt.Key.Key_Return: "<CR>", Qt.Key.Key_Enter: "<CR>",
    Qt.Key.Key_Escape: "<Esc>", Qt.Key.Key_Tab: "<Tab>", Qt.Key.Key_Backtab: "<S-Tab>",
    Qt.Key.Key_Backspace: "<BS>", Qt.Key.Key_Delete: "<Del>", Qt.Key.Key_Space: "<Space>",
    Qt.Key.Key_Up: "<Up>", Qt.Key.Key_Down: "<Down>",
    Qt.Key.Key_Left: "<Left>", Qt.Key.Key_Right: "<Right>",
    Qt.Key.Key_Home: "<Home>", Qt.Key.Key_End: "<End>",
    Qt.Key.Key_PageUp: "<PgUp>", Qt.Key.Key_PageDown: "<PgDown>", Qt.Key.Key_Menu: "<Menu>",
    Qt.Key.Key_MediaPlay: "<MediaPlay>", Qt.Key.Key_MediaPause: "<MediaPlay>",
    Qt.Key.Key_MediaTogglePlayPause: "<MediaPlay>", Qt.Key.Key_MediaNext: "<MediaNext>",
    Qt.Key.Key_MediaPrevious: "<MediaPrev>", Qt.Key.Key_MediaStop: "<MediaStop>",
    **{getattr(Qt.Key, f"Key_F{i}"): f"<F{i}>" for i in range(1, 13)},
}
SPECIAL_KEYS = {int(k.value): v for k, v in SPECIAL_KEYS.items()}
# Special keys that get modifier prefixes: <S-Del>, <A-F1>, <C-F5>, <S-F10>.
MODIFIABLE_KEYS = {int(Qt.Key.Key_Delete.value),
                   *(int(getattr(Qt.Key, f"Key_F{i}").value) for i in range(1, 13))}

# Tried in order when the config doesn't name a monospace font.
MONOSPACE_FONTS = ("Cascadia Mono", "Consolas", "JetBrains Mono", "SF Mono", "Menlo",
                   "DejaVu Sans Mono", "Ubuntu Mono", "Liberation Mono", "Noto Sans Mono")


def monospace_font(options: dict) -> tuple[QFont, str]:
    """The font for the file lists from the "monospace" options, and a warning or ""."""
    base = QApplication.font()
    if not options.get("enabled", True):
        return QFont(base), ""
    families = set(QFontDatabase.families())
    wanted = str(options.get("family") or "")
    family = wanted if wanted in families else next((f for f in MONOSPACE_FONTS if f in families), "")
    font = QFont(base)
    if family:
        font.setFamily(family)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSizeF(base.pointSizeF())
    font.setStyleHint(QFont.StyleHint.Monospace)
    size = options.get("size")
    if isinstance(size, (int, float)) and size > 0:
        font.setPointSizeF(size)
    warning = f"Font not installed: {wanted}, using {font.family()}" if wanted and wanted != family else ""
    return font, warning


HISTORY_SIZE = 100
RECENT_SIZE = 200  # files kept in :recent

# Actions that '.' repeats (vim's "changes"); ':' commands are listed in REPEATABLE_EX.
REPEATABLE = {"paste", "cut", "cut_motion", "trash", "delete", "copy_other", "move_other",
              "increment", "decrement", *CASES, *(name + "_motion" for name in CASES)}
REPEATABLE_EX = {"rename", "mkdir", "touch", "s"}

# :set options: name -> (config key, short names)
BOOL_OPTIONS = {
    "number": ("number", ("nu",)), "relativenumber": ("relativenumber", ("rnu",)),
    "ignorecase": ("ignorecase", ("ic",)), "smartcase": ("smartcase", ("scs",)),
    "hlsearch": ("hlsearch", ("hls",)), "wrapscan": ("wrapscan", ("ws",)),
    "hidden": ("show_hidden", ()),
    "columns": ("columns", ()), "git": ("git", ()), "natural": ("natural_sort", ()),
}
# :set options of this session's views (not saved): name -> short names
RUNTIME_OPTIONS = {"hex": (), "plot": (), "grid": (), "quickview": ("qv",), "linkpanes": ("lp",), "bar": ()}
# :set name=value options: name -> (config key, short names, allowed values)
STRING_OPTIONS = {
    "clipboard": ("clipboard", ("cb",), ("", "unnamed", "unnamedplus")),
}

# Actions that are jumps: '' goes back to where the cursor was before the last one.
JUMPS = {"first", "last", "percent", "search_next", "search_prev", "search_like",
         "search_like_back", "goto_mark", "group_next", "group_prev", "screen_top",
         "screen_middle", "screen_bottom"}
JUMP_COMMANDS = {"cd", "z", "zi", "find", "tabnew"}
MACRO_LIMIT = 20000  # keys played by one @, so a macro that calls itself stops
# Windows, in the console-less build: a console program started by :! would flash a window
NO_CONSOLE = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" and sys.stdout is None else 0


def pattern_regex(text: str, ignorecase: bool = True, smartcase: bool = True) -> re.Pattern:
    """A search / filter pattern as a regex. A pattern with * or ? and no other
    regex syntax is a shell pattern (*.jpg); an invalid regex is taken literally.
    With smartcase, a capital letter makes the match case-sensitive."""
    ignore = ignorecase and not (smartcase and any(c.isupper() for c in text))
    flags = re.IGNORECASE if ignore else 0
    if any(c in text for c in "*?") and not any(c in text for c in "\\()|^$+{["):
        return re.compile(fnmatch.translate(text), flags)
    try:
        return re.compile(text, flags)
    except re.error:
        return re.compile(re.escape(text), flags)


# :g/pattern/cmd, :g!/pattern/cmd and :v/pattern/cmd (any non-word character as the delimiter)
GLOBAL = re.compile(r"^(g(?:lobal)?!?|v(?:global)?)([^\w\s\\\"|])(.*)$")
# A vim range before a : command: %, or one or two addresses (3, ., $, 'a, '<, +2, .-1).
_ADDRESS = r"(?:\d+|\.|\$|'[a-z<>])?(?:[+-]\d*)*"
RANGE = re.compile(rf"(%|(?=[\d.$'+-]){_ADDRESS})(?:,({_ADDRESS}))?(?=[a-zA-Z!&]|$)")
# What :g runs for vim's line commands: d = trash, d! = delete, y = yank (into a register with y x)
GLOBAL_ACTIONS = {"d": "trash", "delete": "trash", "d!": "delete", "delete!": "delete",
                  "y": "yank", "yank": "yank"}


def split_global(rest: str, delimiter: str) -> tuple[str, str]:
    """'pat/cmd' -> ('pat', 'cmd'); a backslash escapes the delimiter, the closing one may be left out."""
    pattern, i = [], 0
    while i < len(rest):
        c = rest[i]
        if c == "\\" and i + 1 < len(rest) and rest[i + 1] == delimiter:
            pattern.append(delimiter)
            i += 2
            continue
        if c == delimiter:
            return "".join(pattern), rest[i + 1:].strip()
        pattern.append(c)
        i += 1
    return "".join(pattern), ""


# Commands whose argument is completed as a path by Tab on the ':' line.
PATH_COMMANDS = {"cd", "mkdir", "touch", "rename", "tabnew", "find", "bar", "play"}
# Commands that read the folder with local file functions: not for sftp:// folders.
LOCAL_ONLY = "this works on folders of this computer, not on SSH folders"

HELP = """\
<b>Movement</b>: j/k, gg/G, 5j, 12G, Ctrl-d/u half page, Ctrl-f/b page, H/M/L screen top/middle/bottom
&nbsp;&nbsp;fx / Fx = next/previous name starting with x, ; and , repeat it, zz/zt/zb scroll
<b>Navigate</b>: h, - or Backspace = parent folder, l or Enter = open, gh or ~ = home
&nbsp;&nbsp;Ctrl-o / Ctrl-i = back / forward in folder history, Ctrl-l = reload
&nbsp;&nbsp;Tab, Ctrl-w w = other pane, Ctrl-w h / Ctrl-w l = left / right pane
&nbsp;&nbsp;Ctrl-w o = only this pane (again: both), Ctrl-w x = swap the panes' folders and tabs
&nbsp;&nbsp;Ctrl-w c = close this pane, Ctrl-w v / Ctrl-w s = both panes, the other one here, Ctrl-w p = other pane
&nbsp;&nbsp;:only, :close, :vsplit [PATH] = the same as Ctrl-w o, Ctrl-w c, Ctrl-w v; Ctrl-w r = swap too
&nbsp;&nbsp;Ctrl-w '=' = equal widths, Ctrl-w | = widest, Ctrl-w &lt; / Ctrl-w &gt; = narrower / wider (3 Ctrl-w &gt; = 15%)
&nbsp;&nbsp;Alt-F1 / Alt-F2 = drive menu for the left / right pane (or click the drive button)
<b>Tabs</b>: Ctrl-t = new tab, gt / gT = next / previous tab (3gt = tab 3), :tabnew [PATH], :tabclose
<b>Marks</b>: ma = set mark a on this file, 'a = jump to it (kept between runs), :marks lists them
<b>Bar</b>: g1..g9 = bar button 1..9, :bar add [PATH], :bar to show/hide, right-click the bar to edit
&nbsp;&nbsp;drop files on a program button to open them with it, on empty space to add buttons
<b>Select</b>: t or Space = tag, T = invert tags, + or :select *.txt, :unselect PATTERN, Esc = clear
&nbsp;&nbsp;mouse: click = move the cursor, Ctrl+click = tag one item, Shift+click = tag a range
&nbsp;&nbsp;v = visual mode (y/d/x act on the range, c = rename, U/u/~ = case, o = other end), gv = reselect last range
<b>Files</b>: yy = copy, dd = cut, p = paste here (3yy yanks 3 items)
&nbsp;&nbsp;yf / yd / yn = copy file path / folder path / file name to the clipboard
&nbsp;&nbsp;F5 = copy to other pane, F6 = move to other pane, Del/F8 = move to trash
&nbsp;&nbsp;Alt-F5 = pack into a zip in the other pane, Alt-F9 = unpack zips into the other pane
&nbsp;&nbsp;gb = branch view (all files below this folder), :hash [sha256|md5|…] = checksum file, :hash check = verify it
&nbsp;&nbsp;Shift-Del or DD = delete permanently, u = undo the last copy / move / rename / trash; ga / gA = gs / gS
&nbsp;&nbsp;cw/F2 = rename, cW = rename without extension, F7 = new folder, za = show/hide hidden files (zo / zm = show / hide)
&nbsp;&nbsp;I / A = rename with the cursor at the start / end, cc = type a new name, C = a new name, keeping the extension
&nbsp;&nbsp;gUU / guu / g~~ = name to UPPER / lower / switched case; with a motion too: gUj, gu3j, g~G
&nbsp;&nbsp;Ctrl-a / Ctrl-x = add / subtract 1 (5 Ctrl-a: 5) to the last number in the name: img_0099 → img_0100
&nbsp;&nbsp;g Ctrl-a / g Ctrl-x = tagged or visual items get 1, 2, 3… (vim's g Ctrl-a); . repeats
&nbsp;&nbsp;F3 = view, F4 = edit (programs set in the config file), S = terminal here
&nbsp;&nbsp;Ctrl-q = quick view in the other pane (a folder shows as a graph), gs = folder size, gS = all folder sizes here
&nbsp;&nbsp;gG or :graph = graph window; :graph 1|2|3, size|count, tree|sunburst|treemap|timeline|types|notes|code, on|off
&nbsp;&nbsp;(notes = Markdown links, code = imports between source files; big dots are imported by many)
&nbsp;&nbsp;right-click, Menu key or Shift-F10 = context menu (on Windows the Explorer menu;
&nbsp;&nbsp;Shift+right-click adds extended items, Ctrl+right-click shows VimCommander's own menu)
&nbsp;&nbsp;zip files open like folders (read-only but for deleting: copy out with F5 or yy / p)
<b>Search</b>: /regex or ?regex (backwards), n/N = next/previous, * / # = next/previous with the same extension
&nbsp;&nbsp;matches are highlighted (:noh clears), smartcase; *.jpg works as a shell pattern
&nbsp;&nbsp;=pattern = show only matching names (= and Enter on an empty line clears), :filter PATTERN
<b>Vim</b>: yj, d3j, yG, ygg (operator + motion), Y = yy, . = repeat the last change, @: = repeat the last : command
&nbsp;&nbsp;u / Ctrl-r = undo / redo, kept between runs (:undolist), :trash = restore from the trash, "ayy / "ap = named register, "+yy / "+p = system clipboard, :reg
&nbsp;&nbsp;qa … q = record macro a, @a = play it, @@ = play the last one again, 5@a = five times
&nbsp;&nbsp;{ / } = previous / next group (folders, each extension), 50% = halfway, Ctrl-e / Ctrl-y = scroll
&nbsp;&nbsp;'' = back to before the last jump, Ctrl-^ or :cd - = previous folder, Ctrl-g = file details
&nbsp;&nbsp;gx = open with the system app, :e FILE = edit, ZZ / ZQ / :q = close tab or quit, :qa = quit
&nbsp;&nbsp;:tabs, :tabmove N, :tabn / :tabp / :tabfirst / :tablast / :tabonly, :set clipboard=unnamedplus (y / p use the system clipboard)
&nbsp;&nbsp;:d / :d! = trash / delete, :y / :pu = yank / put, :normal KEYS, & = repeat the last :s, :pwd, :messages
&nbsp;&nbsp;ranges: :'&lt;,'&gt;CMD (: in visual mode), :%CMD, :3,8CMD, :.,.+4CMD (4: types it), :'a = the rows, tagged, then CMD
&nbsp;&nbsp;:nnoremap ,d :delete&lt;CR&gt;, :nmap, :vmap, :map, :unmap, :mapclear; :let mapleader = "," for &lt;leader&gt;
&nbsp;&nbsp;:source FILE = run its : commands; the vimcommanderrc next to the config runs at start (like a vimrc)
&nbsp;&nbsp;Ctrl-c or Ctrl-[ = Esc, here and on the command line; q: / q/ = command / search history window (Enter runs, e edits)
&nbsp;&nbsp;:command NAME REPLACEMENT = a : command for the session, :help TOPIC = only the help lines about TOPIC
&nbsp;&nbsp;:!CMD: % = the file's name, %:p full path, %:h folder, %:t name, %:r without extension, %:e extension
<b>Tools</b>: Ctrl-Shift-p = command palette, Ctrl-p = find files (fuzzy, zoxide-ranked), gr = activity calendar
&nbsp;&nbsp;:bulkrename = rename in an editor, :compare = tag differences, :synchronize = sync the panes' folders
&nbsp;&nbsp;:graph sunburst / treemap = disk usage, :graph timeline = when folders changed, :graph types = what they hold; after g, z, y, ", m, ', q, @, Ctrl-w the next keys are listed
&nbsp;&nbsp;g/ or :grep TEXT = search inside files (Enter = edit at the line, Ctrl-Enter = files as a list)
&nbsp;&nbsp;Quick view renders Markdown (click a [[link]] to go to the note), CSV tables and PDFs; :obsidian = open in Obsidian
&nbsp;&nbsp;Jupyter notebooks (cells, outputs, plots) and STM32CubeMX .ioc projects (pins, clocks, peripherals) too
&nbsp;&nbsp;zp = CSV files as a plot / as a table; on the plot: drag = zoom in, right-click = back, double-click = all
&nbsp;&nbsp;on the plot, click a column's name = plot the others against it (a scatter if it doesn't rise); LTspice .raw files always plot
&nbsp;&nbsp;gd or :diffthis = two files side by side (tagged, or both cursors): ]c / [c = next / previous change
<b>Result lists</b>: :find, :grep, Ctrl-p + Ctrl-Enter and gr fill the pane with files from many folders;
&nbsp;&nbsp;tag, yy, F5, Del, cw work as usual; h = back to the folder, gf = go to the file, :results = show again
&nbsp;&nbsp;]q / [q or :cn / :cp = the next / previous item, opened in its own folder; 3]q = three items on
&nbsp;&nbsp;:copen / :cclose = show / leave the list, :clist = its items, :colder / :cnewer = earlier / later lists
&nbsp;&nbsp;:cdo !COMMAND %f = run it for each item (tagged, or all) in its folder; :cdo CMD = a : command for each
<b>Shelf</b>: zs = put on the shelf, zS = show / go to it, :shelf copy | move | zip | list | clear, :zip NAME
<b>Recent</b>: :recent = files you opened, newest first; :recent changed 3 = files changed here in 3 days
<b>Space</b>: :reclaim = caches, build output (also STM32 / Atmel / Keil / Ansys) and big old files,
&nbsp;&nbsp;:dupes = duplicate files (:dupes tag = all but the newest)
<b>PDF</b>: :pdfmerge NAME = join the tagged PDFs, in the pane's order, into NAME.pdf
&nbsp;&nbsp;:pdfextract 2-5 = those pages of the PDF (or each tagged one) as a new PDF; 1,4,7- or 9-7 work too
<b>Git</b>: changed / new / ignored files are colored, the branch is in the header; :git changed, :git diff (or gd)
<b>More</b>: :linkpanes = browse two mirror trees together, :set columns = Miller columns, :rules = folder rules,
&nbsp;&nbsp;:mksession NAME / :session NAME = save / open both panes' tabs and the shelf
<b>Grid</b>: zi = thumbnails / list (j k = a row, w b = one tile, Ctrl+wheel = size), :grid SIZE
<b>Terminal</b>: Ctrl-` = terminal panel; it follows the pane and the pane follows its cd; Esc = back
&nbsp;&nbsp;Ctrl-w t or :vterm [wsl|wsl:NAME|pwsh|cmd|bash] = a real terminal in the other pane (vim, htop work)
&nbsp;&nbsp;in it: Ctrl-w h/l/w = back to the files, Shift-PgUp = scroll back, Ctrl-Shift-C / V = copy / paste, exit = close
&nbsp;&nbsp;:mount / :umount = mount this SSH folder with sshfs in the terminal pane (WSL on Windows)
<b>Music</b>: gm = play the tagged items, the folder or .m3u under the cursor, or from the file under the cursor
&nbsp;&nbsp;gM = pause / resume, ]m / [m = next / previous track, media keys too; a slim player bar shows at the bottom
&nbsp;&nbsp;Ctrl-w m = the playlist in the other pane (Enter play, Space pause, n N, ← → seek, { } speed, d remove, q close)
&nbsp;&nbsp;:play off = stop, :play shuffle | repeat | volume 50 | speed 1.5 | seek +10 | save NAME (an .m3u8 here)
<b>Memory map</b>: Ctrl-q on a linker .map (GNU ld, Keil) or .elf = Flash / RAM bars and a treemap of sections, objects, functions
<b>3D model</b>: Ctrl-q on a .step / .stp / .stl file = the shaded model; drag = rotate, right-drag = pan, wheel = zoom, double-click = reset
&nbsp;&nbsp;click = measure from a corner, circle centre or face (a second click = the distance), right-click = clear; the mouse names the part
&nbsp;&nbsp;zv = keys to the model: h j k l rotate, H J K L pan, + - zoom, 0 = home, 1-6 = front back right left top bottom, w = wireframe, c = clear, Esc = back
<b>Share</b>: :share = QR code for a phone to download the tagged files and send files back; :share stop
<b>SSH</b>: :ssh user@host[:path] = a server's folder in this pane (F5 / F6 copy across, F4 edits and uploads
&nbsp;&nbsp;on save, Ctrl-` = a shell there), :mirror = make the other pane a copy of this one, :ssh close
<b>FTP</b>: :ftp [user@]host[/path] (no user = anonymous, ftps://host = TLS), same keys as SSH, no shell
<b>Jobs</b>: copies and moves wait in a queue; :jobs = the panel (pause, cancel, run next), :jobs pause / resume
&nbsp;&nbsp;:set nu / rnu / ic / scs / hls / ws / hidden / natural (no… to turn off, …! to toggle, …? to show)
&nbsp;&nbsp;:set hex / plot / grid / qv / lp / bar = views for this session (quick view, linked panes)
&nbsp;&nbsp;command line: Up/Down history, Ctrl-w word, Ctrl-u line, Ctrl-r Ctrl-f file name, Ctrl-r Ctrl-p path
&nbsp;&nbsp;Ctrl-r a = the paths in register a (Ctrl-r " the last yank, Ctrl-r : the last command, Ctrl-r / the last search)
<b>Drag and drop</b>: drop = copy, Shift+drop = move. Works with other apps too.
<b>Commands</b>: :q &nbsp; :cd PATH &nbsp; :mkdir NAME &nbsp; :touch NAME &nbsp; :rename NAME &nbsp; (Tab completes)
&nbsp;&nbsp;:se = :set, :tabn = :tabnext, :bulk = :bulkrename (any unique start, as in vim); :rename! / :pdfextract! = replace an existing file
&nbsp;&nbsp;:rename img_{n:3}{ext} renames all tagged items ({name} {ext} {n}) &nbsp; :s/old/new/gi
&nbsp;&nbsp;:g/PATTERN/CMD = tag matching names, then CMD on them (d = trash, d! = delete, y = yank, normal KEYS,
&nbsp;&nbsp;any : command); :g!/… or :v/… = the names that don't match; :g/PATTERN/ = only tag them
&nbsp;&nbsp;:hex = quick view in hex for every file (binary files always show in hex)
&nbsp;&nbsp;:find PATTERN (search below this folder; add size>1M, newer:7d, older:2w, type:f / type:d) &nbsp; :drives &nbsp; :undo
&nbsp;&nbsp;:z KEYWORDS = jump with zoxide (Tab completes) &nbsp; :zi or gz = pick from zoxide's list
&nbsp;&nbsp;:sort name|ext|size|time (-size = reverse) &nbsp; :sync (other pane goes here)
&nbsp;&nbsp;:!CMD (run in this folder; %d %o %f %s are filled in) &nbsp; :42 (go to line) &nbsp; :help
&nbsp;&nbsp;:shell (terminal here) &nbsp; :config (edit the config file; it reloads when saved)
"""


def key_name(event: QKeyEvent) -> str | None:
    """Turn a key event into vim notation, or None for bare modifier presses."""
    key = event.key()
    mods = event.modifiers()
    ctrl = mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
    if key in SPECIAL_KEYS:
        name = SPECIAL_KEYS[key]
        if key in MODIFIABLE_KEYS:
            prefix = (("C-" if ctrl else "") + ("A-" if mods & Qt.KeyboardModifier.AltModifier else "")
                      + ("S-" if mods & Qt.KeyboardModifier.ShiftModifier else ""))
            name = f"<{prefix}{name[1:-1]}>" if prefix else name
        return name
    if ctrl and int(Qt.Key.Key_A.value) <= key <= int(Qt.Key.Key_Z.value):
        shift = "S-" if mods & Qt.KeyboardModifier.ShiftModifier else ""
        return f"<C-{shift}{chr(key).lower()}>"
    if ctrl and 0x80 <= key <= 0xFFFF and chr(key).isalpha():  # another layout: the engine's langmap maps it
        shift = "S-" if mods & Qt.KeyboardModifier.ShiftModifier else ""
        return f"<C-{shift}{chr(key).lower()}>"
    if ctrl and key in (int(Qt.Key.Key_AsciiCircum.value), int(Qt.Key.Key_6.value)):
        return "<C-^>"
    if ctrl and key in (int(Qt.Key.Key_QuoteLeft.value), int(Qt.Key.Key_Dead_Grave.value)):
        return "<C-`>"
    if ctrl and key == int(Qt.Key.Key_BracketLeft.value):  # vim's other Esc
        return "<Esc>"
    text = event.text()
    return text if len(text) == 1 and text.isprintable() else None


class CommandLine(QLineEdit):
    """The ':', '/', '?' and '=' prompt. Esc or losing focus cancels.

    Vim's command-line keys: Up/Down or Ctrl-p/Ctrl-n recall history, Ctrl-w
    deletes a word, Ctrl-u everything before the cursor, Ctrl-b / Ctrl-e go to
    the start / end, Ctrl-r Ctrl-f (or Ctrl-w) inserts the file name under the
    cursor, Ctrl-r Ctrl-p its full path, Ctrl-r Ctrl-d the current folder, Ctrl-r x
    the paths in register x (Ctrl-r : the last command, Ctrl-r / the last search).
    Ctrl-c and Ctrl-[ cancel, like Esc.
    """

    cancelled = Signal()
    complete = Signal(bool)  # Tab (False) or Shift-Tab (True)
    history = Signal(int)  # -1 = older, 1 = newer
    insert_request = Signal(str)  # "name", "path", "folder" or "reg:x"

    def __init__(self):
        super().__init__()
        self._ctrl_r = False

    def event(self, e):
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.complete.emit(e.key() == Qt.Key.Key_Backtab)
            return True  # keep focus here
        return super().event(e)

    def keyPressEvent(self, e):
        key = e.key()
        ctrl = bool(e.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                     | Qt.KeyboardModifier.MetaModifier))
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return  # a bare modifier press doesn't end Ctrl-r
        if self._ctrl_r:
            self._ctrl_r = False
            kinds = {Qt.Key.Key_F: "name", Qt.Key.Key_W: "name", Qt.Key.Key_P: "path", Qt.Key.Key_D: "folder"}
            if ctrl and key in kinds:
                self.insert_request.emit(kinds[key])
            elif len(e.text()) == 1 and e.text().isprintable():
                self.insert_request.emit("reg:" + e.text())
            return
        if (key == Qt.Key.Key_Escape or (key == Qt.Key.Key_Backspace and not self.text())
                or (ctrl and key in (Qt.Key.Key_C, Qt.Key.Key_BracketLeft))):
            self.cancelled.emit()
        elif key == Qt.Key.Key_Up or (ctrl and key == Qt.Key.Key_P):
            self.history.emit(-1)
        elif key == Qt.Key.Key_Down or (ctrl and key == Qt.Key.Key_N):
            self.history.emit(1)
        elif ctrl and key == Qt.Key.Key_W:
            before = self.text()[:self.cursorPosition()]
            m = re.search(r"(\w+|[^\w\s]+)\s*$", before)
            if m:
                self.setSelection(m.start(), len(before) - m.start())
                self.del_()
        elif ctrl and key == Qt.Key.Key_U:
            self.setSelection(0, self.cursorPosition())
            self.del_()
        elif ctrl and key == Qt.Key.Key_B:
            self.home(False)
        elif ctrl and key == Qt.Key.Key_E:
            self.end(False)
        elif ctrl and key == Qt.Key.Key_R:
            self._ctrl_r = True
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self.isVisible():
            self.cancelled.emit()


class MacroRecorder(QObject):
    """While a macro is recorded (qa), sees every key press in the app, so keys
    typed on the command line and in dialogs are recorded too. A plain q in a
    pane, with nothing pending, stops the recording."""

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window = window

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.KeyPress or obj is not QApplication.focusWidget():
            return False  # each key once: only where it is delivered first
        w = self.window
        if w._playback or w._play_timer.isActive():
            return False  # keys a macro plays aren't recorded again
        engine = w.engine
        if (event.text() == "q" and any(obj is p.view for p in w.panes)
                and not (engine.pending or engine.count or engine.awaiting or engine.register)):
            w.stop_recording()
            return True
        if event.key() not in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            w._recorded.append([event.key(), event.modifiers().value, event.text()])
        return False


class RatioSplitter(QSplitter):
    """The two panes: keeps the share of the left pane when the window is resized,
    and reports a new share when the handle is dragged."""

    ratio_changed = Signal(float)

    def __init__(self, ratio: float = 0.5):
        super().__init__()
        self.ratio = ratio
        self.splitterMoved.connect(self._moved)

    def _moved(self, pos: int, index: int) -> None:
        sizes = self.sizes()
        if sum(sizes) > 0:
            self.ratio = sizes[0] / sum(sizes)
            self.ratio_changed.emit(self.ratio)

    def set_ratio(self, ratio: float) -> None:
        self.ratio = min(0.9, max(0.1, ratio))
        self.apply_ratio()

    def apply_ratio(self) -> None:
        total = sum(self.sizes()) or self.width()
        if total > 0 and self.count() == 2:
            left = round(total * self.ratio)
            self.setSizes([left, total - left])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_ratio()

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_ratio()


class MainWindow(QMainWindow):
    def __init__(self, left: str, right: str, settings: dict | None = None,
                 restore_tabs: bool = False):
        super().__init__()
        self.setWindowTitle("VimCommander")

        self.settings = config.with_defaults(settings or {})
        self.engine = KeyEngine()
        # :map / :noremap: mode -> {keys: binding}; "" unbinds (<Nop>). Kept for the session.
        self.maps: dict[str, dict[str, str]] = {NORMAL: {}, VISUAL: {}}
        self.base_bindings = (self.engine.bindings, self.engine.arg_bindings)  # without the maps
        self.leader = "\\"  # <leader>, set with :let mapleader
        self._map_timer = QTimer(self, singleShot=True)  # 'timeoutlen': a map waiting for more keys
        self._map_timer.timeout.connect(self.flush_keys)
        self._key_depth = 0  # maps running maps
        self._typed_command = ""  # the : command running, by its full name
        self.panes = [Pane(left, self.settings), Pane(right, self.settings)]
        self.active = 0
        # Registers: name -> ("copy" | "cut", paths). '"' is the unnamed one, which
        # always holds the last yank or cut; "+ and "* are the system clipboard.
        self.registers: dict[str, tuple[str, list[str]]] = {  # named ones last between runs, like viminfo
            k: (v[0], [str(p) for p in v[1]]) for k, v in (self.settings.get("registers") or {}).items()
            if len(k) == 1 and k.isalnum() and isinstance(v, list) and len(v) == 2
            and v[0] in ("copy", "cut") and isinstance(v[1], list)}
        self.user_commands: dict[str, str] = {}  # :command, for this session
        self.visual_anchor = 0
        self.last_search = ""
        self.search_origin = 0
        self.jobs: list[Job] = []  # running and queued
        self.job_queue: list[Job] = []  # waiting for the running transfer to finish
        self.job_dialogs: dict[int, ProgressDialog] = {}  # id(job) -> its dialog
        self.marks: dict[str, list[str]] = {}
        self.last_visual: tuple[int, int] | None = None
        self.last_find: tuple[str, int] | None = None
        # (description, undo records, time); kept in undo.json while "persistent_undo" is on
        self.undo_stack: list[tuple[str, list[tuple], float]] = []
        self.redo_stack: list[tuple[str, list[tuple], float]] = []
        if self.settings.get("persistent_undo", True):
            self.undo_stack, self.redo_stack = config.load_undo()
        # Macros: register -> recorded key presses as [key, modifiers, text].
        self.macros: dict[str, list[list]] = {
            k: v for k, v in (self.settings.get("macros") or {}).items() if isinstance(v, list)}
        self.recording: str | None = None
        self._recorded: list[list] = []
        self._recorder = MacroRecorder(self)
        self.last_macro: str | None = None
        self._playback: deque = deque()
        self._played = 0
        self._play_timer = QTimer(self, singleShot=True, interval=0)
        self._play_timer.timeout.connect(self._play_step)
        self.size_jobs: list[SizeJob] = []
        self.quickview = False
        self._completion: dict | None = None
        self._completing = False  # True while the app itself changes the command line
        self._history_state: dict | None = None
        history = self.settings.get("history")
        self.history: dict[str, list[str]] = {k: [h for h in (history or {}).get(k, [])
                                                  if isinstance(h, str)][-HISTORY_SIZE:]
                                              for k in (":", "/", "=")} if isinstance(history, dict) \
            else {":": [], "/": [], "=": []}
        self.recent: list[str] = [p for p in self.settings.get("recent") or [] if isinstance(p, str)][:RECENT_SIZE]
        self.search_dir = 1  # 1 after '/', -1 after '?'
        self.hl_active = False  # search matches highlighted (until :noh)
        self.filter_origin = ""
        self.last_change: tuple | None = None  # what '.' repeats
        self.last_ex = ""  # what '@:' repeats
        self.last_substitute = ""  # what & repeats
        self.bang = False  # the running : command was typed with ! (force)
        self.messages: deque[tuple[float, str]] = deque(maxlen=200)  # for :messages
        self._repeating = False
        self.zoxide: zoxide.Zoxide | None = None
        self.graph_windows: list[QWidget] = []
        self.searches: dict[int, Search] = {}  # running result-list searches, by id(pane)
        self.cdo_run: Search | None = None  # :cdo !COMMAND running over a result list
        self._grep_globs = ""  # the file patterns of the last :grep
        self.git_program: str | None = None
        self.git_repos: dict[int, tuple[str, dict]] = {}  # id(pane) -> (repo, files) of its last status
        self._git_runs: dict[int, Search] = {}
        self.link: tuple[str, str] | None = None  # :linkpanes: the two folders it started from
        self._linking = False
        self.whichkey = WhichKey(self)
        self._whichkey_timer = QTimer(self, singleShot=True)
        self._whichkey_timer.timeout.connect(self.show_whichkey)

        self.shelf = Shelf(self)
        self.shelf.set_paths(self.settings.get("shelf") or [])
        self.shelf.changed.connect(self.save_settings)
        self.shelf.goto.connect(self.goto_path)
        self.shelf.back.connect(lambda: self.pane.view.setFocus())
        self.shelf.command.connect(lambda cmd: self.ex_shelf(cmd))
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.shelf)
        self.shelf.setVisible(bool(self.settings.get("shelf_visible", False)))

        self.jobs_panel = JobsPanel(self)
        self.jobs_panel.cancel.connect(self.cancel_job)
        self.jobs_panel.move_first.connect(self.job_first)
        self.jobs_panel.paused.connect(lambda job: self._start_queued())
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.jobs_panel)
        self.jobs_panel.hide()

        self.terminal = Terminal(self)
        self.terminal.cwd_changed.connect(self.on_terminal_cd)
        self.terminal.leave.connect(lambda: self.pane.view.setFocus())
        self.terminal.visibilityChanged.connect(self.on_terminal_shown)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.terminal)
        self.terminal.hide()
        self.player = None  # player.Player, made by the first gm / :play (_player())
        self.player_bar = None
        self.playlist_view = None
        self._player_save_timer = QTimer(self, singleShot=True, interval=800)  # volume drags save once
        self._player_save_timer.timeout.connect(self._save_player)
        self.share_dialog = None  # a sharedialog.ShareDialog while sharing
        remote.Remote.ask_password = staticmethod(self.ask_password)
        remote.Remote.ask_host = staticmethod(self.ask_host_key)
        self.remote_edits: dict[str, str] = {}  # local copy being edited -> its sftp:// URL
        self.edit_watcher = QFileSystemWatcher(self)
        self.edit_watcher.fileChanged.connect(self.on_remote_edit_saved)

        self.bar = LaunchBar([])
        self.bar.context_provider = self.bar_context
        self.bar.folder_requested.connect(self.on_bar_folder)
        self.bar.files_dropped.connect(self.on_drop)
        self.bar.message.connect(self.show_message)
        self.bar.changed.connect(self.save_settings)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.bar)

        splitter = self.splitter = RatioSplitter(self._pane_ratio(self.settings))
        self._ratio_timer = QTimer(self, singleShot=True, interval=600)  # save once dragging stops
        self._ratio_timer.timeout.connect(self.save_settings)
        splitter.ratio_changed.connect(lambda _: self._ratio_timer.start())
        for i, pane in enumerate(self.panes):
            splitter.addWidget(pane)
            pane.view.installEventFilter(self)
            pane.grid.installEventFilter(self)
            pane.grid.files_dropped.connect(self.on_drop)
            pane.grid.context_menu.connect(lambda row, pos, i=i: self.show_context_menu(i, row, pos))
            pane.grid.zoom.connect(self.zoom_grid)
            pane.message.connect(self.show_message)
            pane.view.files_dropped.connect(self.on_drop)
            pane.view.context_menu.connect(
                lambda row, pos, i=i: self.show_context_menu(i, row, pos))
            pane.view.cursor_moved.connect(self.update_quickview)
            pane.folder_changed.connect(self.zoxide_add)
            pane.folder_changed.connect(lambda path, i=i: self.follow_link(i, path))
            pane.folder_changed.connect(lambda path, i=i: i == self.active and self.terminal.follow(path))
            pane.folder_changed.connect(lambda path, i=i: self.follow_vterm(i, path))
            pane.other_folder = lambda i=i: self.panes[1 - i].path
            pane.view.cursor_moved.connect(lambda i=i: self.follow_link_cursor(i))
            pane.listed.connect(lambda pane=pane: self.on_listed(pane))
            timer = QTimer(self, singleShot=True, interval=250)
            timer.timeout.connect(lambda pane=pane: self.run_git(pane))
            pane.git_timer = timer
            pane.preview.graph.open_requested.connect(self.on_graph_open)
            for view in (pane.preview, pane.column_preview):
                view.model3d.leave.connect(lambda: self.pane.view.setFocus())
            pane.preview.graph.options_changed.connect(self.save_graph_options)
            pane.preview.markdown.open_requested.connect(self.on_note_link)

        self.mode_label = QLabel()
        self.prompt_label = QLabel()
        self.cmdline = CommandLine()
        self.cmdline.setFrame(False)
        self.cmdline.returnPressed.connect(self.on_cmdline_enter)
        self.cmdline.textChanged.connect(self.on_cmdline_changed)
        self.cmdline.cancelled.connect(self.on_cmdline_cancel)
        self.cmdline.complete.connect(self.complete_cmdline)
        self.cmdline.history.connect(self.history_step)
        self.cmdline.insert_request.connect(self.cmdline_insert)
        self.message_label = QLabel()
        self.info_label = QLabel()
        # A long message must not widen the window: it is cut off (full text in the tooltip).
        self.message_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.message_label.setMinimumWidth(1)

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 6, 2)
        bar.addWidget(self.mode_label)
        bar.addWidget(self.prompt_label)
        bar.addWidget(self.cmdline, 1)
        bar.addWidget(self.message_label, 1)
        bar.addWidget(self.info_label)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)
        layout.addLayout(bar)
        self.setCentralWidget(central)

        self.config_watcher = QFileSystemWatcher(self)
        self._config_timer = QTimer(self, singleShot=True, interval=300)
        self._config_timer.timeout.connect(self.reload_config)
        self.config_watcher.fileChanged.connect(lambda _: self._config_timer.start())
        self._watch_config()

        self.hide_cmdline()
        self.apply_config()
        if os.path.isfile(self.rc_path()):
            QTimer.singleShot(0, lambda: self.ex_source(""))  # once the panes are set up
        grid = self.settings.get("grid")
        for pane, on in zip(self.panes, grid if isinstance(grid, list) else []):
            if on is True:
                pane.set_grid(True)
        for pane in self.panes:
            self.zoxide_add(pane.path)
        if restore_tabs:
            tabs, index = self.settings.get("tabs") or [], self.settings.get("tab_index") or []
            for i, pane in enumerate(self.panes):
                if i < len(tabs) and isinstance(tabs[i], list):
                    pane.set_tabs(tabs[i], index[i] if i < len(index) and isinstance(index[i], int)
                                  else 0)
        self.activate(0)

    @property
    def pane(self) -> Pane:
        return self.panes[self.active]

    @property
    def other(self) -> Pane:
        return self.panes[1 - self.active]

    def activate(self, index: int) -> None:
        if self.panes[index].isHidden():  # Ctrl-w o hid it
            self.show_both_panes()
        if self.panes[index].shows_terminal:  # that pane is a terminal (:vterm): go into it
            self.leave_visual()
            self.panes[index].term.setFocus()
            self.update_status()
            return
        if self.panes[index].shows_playlist:  # the music player's playlist (Ctrl-w m): go into it
            self.leave_visual()
            self.panes[index].playlist.setFocus()
            self.update_status()
            return
        self.active = index
        for i, pane in enumerate(self.panes):
            pane.set_active(i == index)
        if hasattr(self, "terminal") and not self.pane.results and not self.pane.archive:
            self.terminal.follow(self.pane.path)
        self.pane.view.setFocus()
        self.update_status()

    # Key handling

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.FocusIn:
            for i, pane in enumerate(self.panes):
                if obj in (pane.view, pane.grid) and i != self.active:
                    self.leave_visual()
                    self.activate(i)
        elif event.type() == QEvent.Type.KeyPress:
            key = key_name(event)
            if key is None:
                return False
            self.message_label.clear()
            self.whichkey.hide()
            self._whichkey_timer.stop()  # the action may open a dialog and wait in it
            self._map_timer.stop()
            self.feed_key(key)
            engine = self.engine
            if engine.pending in engine.bindings.get(engine.mode, {}):  # a map like ",x" next to ","
                self._map_timer.start(int(self.settings.get("timeoutlen", 1000)))
            delay = self.settings.get("which_key_delay", 500)
            if (engine.pending or engine.awaiting or engine._register_next) and delay:
                self._whichkey_timer.start(int(delay))
            else:
                self._whichkey_timer.stop()
            self.update_status()
            return True
        return super().eventFilter(obj, event)

    def feed_key(self, key: str) -> None:
        """One key into the engine; runs the action it completes, then the key that
        ended a waiting map, if any."""
        while key:
            action = self.engine.feed(key)
            key, self.engine.leftover = self.engine.leftover, None
            if action:
                self.run_action(action.name, action.count, action.arg, action.register)

    def flush_keys(self) -> None:
        """'timeoutlen' passed: the keys typed so far are a whole binding, run it."""
        self.whichkey.hide()
        self._whichkey_timer.stop()
        if action := self.engine.flush():
            self.run_action(action.name, action.count, action.arg, action.register)
        self.update_status()

    def run_action(self, name: str, count: int | None = None, arg: str | None = None,
                   register: str | None = None) -> None:
        if name in REPEATABLE and not self._repeating:
            self.last_change = ("action", name, count, arg, register)
        if name in JUMPS and not (name == "goto_mark" and arg in ("'", "`")):
            self.remember_jump()
        getattr(self, f"cmd_{name}")(Action(name, count, arg, register))
        if self.engine.mode == VISUAL:
            self.pane.select_range(self.visual_anchor, self.pane.row)
        self.update_status()

    # Motions

    def move(self, delta: int) -> None:
        self.pane.set_cursor(self.pane.row + delta)

    def cmd_down(self, a): self.move(a.n() * self.pane.row_step())
    def cmd_up(self, a): self.move(-a.n() * self.pane.row_step())
    def cmd_item_next(self, a): self.move(a.n())
    def cmd_item_prev(self, a): self.move(-a.n())
    def cmd_half_down(self, a): self.move(self.pane.visible_rows() // 2)
    def cmd_half_up(self, a): self.move(-(self.pane.visible_rows() // 2))
    def cmd_page_down(self, a): self.move(self.pane.visible_rows() * a.n())
    def cmd_page_up(self, a): self.move(-self.pane.visible_rows() * a.n())
    def cmd_first(self, a): self.pane.set_cursor(a.n() - 1)
    def cmd_last(self, a): self.pane.set_cursor(a.n(len(self.pane.model.entries)) - 1)

    def _screen_row(self, fraction: float) -> int:
        top, bottom = self.pane.visible_range()
        return top + int((bottom - top) * fraction)

    def cmd_screen_top(self, a): self.pane.set_cursor(self._screen_row(0))
    def cmd_screen_middle(self, a): self.pane.set_cursor(self._screen_row(0.5))
    def cmd_screen_bottom(self, a): self.pane.set_cursor(self._screen_row(1))

    def cmd_scroll_center(self, a): self.pane.scroll_cursor(QAbstractItemView.ScrollHint.PositionAtCenter)
    def cmd_scroll_top(self, a): self.pane.scroll_cursor(QAbstractItemView.ScrollHint.PositionAtTop)
    def cmd_scroll_bottom(self, a): self.pane.scroll_cursor(QAbstractItemView.ScrollHint.PositionAtBottom)

    def _find_char(self, char: str, step: int, times: int) -> None:
        entries = self.pane.model.entries
        char = char.casefold()
        row = self.pane.row
        for _ in range(times):
            for i in range(1, len(entries) + 1):
                r = (row + i * step) % len(entries)
                if not entries[r].is_parent and entries[r].name.casefold().startswith(char):
                    row = r
                    break
            else:
                self.show_message(f"No name starting with {char}")
                return
        self.pane.set_cursor(row)

    def cmd_find_char(self, a):
        self.last_find = (a.arg, 1)
        self._find_char(a.arg, 1, a.n())

    def cmd_find_char_back(self, a):
        self.last_find = (a.arg, -1)
        self._find_char(a.arg, -1, a.n())

    def cmd_repeat_find(self, a):
        if self.last_find:
            self._find_char(self.last_find[0], self.last_find[1], a.n())

    def cmd_repeat_find_back(self, a):
        if self.last_find:
            self._find_char(self.last_find[0], -self.last_find[1], a.n())

    # Navigation

    def cmd_parent(self, a):
        if self.pane.results:
            self.pane.leave_results()
            return
        old = remote.basename(self.pane.path)
        parent = remote.dirname(self.pane.path)
        if parent != self.pane.path:
            self.pane.cd(parent, select=old)

    def cmd_open(self, a):
        entry = self.pane.current_entry()
        if entry is None:
            return
        if entry.is_parent:
            self.cmd_parent(a)
        elif entry.is_dir:
            self.pane.cd(entry.path)
        elif (path := self._local_copy(entry.path)) is None:
            return
        elif program := self.open_with(entry.name):
            self.add_recent(entry.path)
            self._run(program, [path], entry.name)
        elif archive.is_archive(path):
            self.pane.cd(path)
        else:
            self.add_recent(entry.path)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                self.show_message(f"No application to open {entry.name}")

    def _local_copy(self, path: str, edit: bool = False) -> str | None:
        """`path`, or for a file inside an archive or on an SSH server a temporary copy.
        `edit`: a remote file's copy is uploaded again each time it is saved."""
        if remote.is_remote(path):
            folder = tempfile.mkdtemp(prefix="vimcommander-ssh-")
            local = os.path.join(folder, remote.basename(path))
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                remote.REMOTE.download(path, local)
            except OSError as e:
                self.show_message(f"Cannot download {remote.basename(path)}: {e.strerror or e}")
                return None
            finally:
                QApplication.restoreOverrideCursor()
            if edit:
                self.remote_edits[local] = path
                self.edit_watcher.addPath(local)
                self.show_message(f"Editing a copy of {remote.basename(path)}: each save uploads it")
            return local
        if not archive.inside(path):
            return path
        try:
            return archive.extract_to_temp(path)
        except OSError as e:
            self.show_message(f"Cannot extract {os.path.basename(path)}: {e.strerror or e}")
            return None

    def open_with(self, name: str) -> str | list | None:
        """The program configured for this file's extension in programs.open_with."""
        table = self.settings["programs"].get("open_with") or {}
        ext = os.path.splitext(name)[1].lower()
        for key, program in table.items():
            if ext and "." + key.lower().lstrip("*.") == ext:
                return program
        return None

    def _run(self, program: str | list, files: list[str], what: str) -> None:
        if run_command(program, self.bar_context(), files):
            self.show_message(f"Opened {what}")
        else:
            self.show_message(f"Could not start {program}")

    def _open_files(self, program_key: str, count: int) -> None:
        entries = [e for e in self.pane.targets(count) if not e.is_dir]
        for e in entries:
            self.add_recent(e.path)
        files = [self._local_copy(e.path, edit=program_key == "editor") for e in entries]
        files = [f for f in files if f]
        if not files:
            self.show_message("No file to open")
            return
        what = os.path.basename(files[0]) if len(files) == 1 else f"{len(files)} files"
        if program := self.settings["programs"].get(program_key):
            self._run(program, files, what)
        else:
            for path in files:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.leave_visual()

    def cmd_view(self, a): self._open_files("viewer", a.n())
    def cmd_edit(self, a): self._open_files("editor", a.n())

    def cmd_shell(self, a):
        if self.pane.remote:
            self.cmd_terminal(a)  # a shell on the server, in the terminal panel
            return
        terminal = self.settings["programs"].get("terminal") or default_terminal()
        if not terminal or not run_command(terminal, self.bar_context()):
            self.show_message(f"Could not start a terminal ({terminal or 'none found'}); "
                              "set programs.terminal in the config file")

    def cmd_ex(self, a): self.execute(a.arg or "")

    def cmd_home(self, a): self.pane.cd(os.path.expanduser("~"))

    def cmd_back(self, a):
        if not self.pane.go_back():
            self.show_message("Start of history")

    def cmd_forward(self, a):
        if not self.pane.go_forward():
            self.show_message("End of history")

    def cmd_reload(self, a):
        for pane in self.panes:
            pane.reload()

    def cmd_switch_pane(self, a):
        self.leave_visual()
        self.activate(1 - self.active)

    def cmd_left_pane(self, a):
        self.leave_visual()
        self.activate(0)

    def cmd_right_pane(self, a):
        self.leave_visual()
        self.activate(1)

    # Pane layout (Ctrl-w)

    def show_both_panes(self) -> None:
        if any(pane.isHidden() for pane in self.panes):
            for pane in self.panes:
                pane.show()
            self.splitter.apply_ratio()

    def _set_ratio(self, ratio: float) -> None:
        self.show_both_panes()
        self.splitter.set_ratio(ratio)
        self._ratio_timer.start()  # saved like a dragged divider

    def cmd_only_pane(self, a):
        """Ctrl-w o: only this pane, like vim's :only; again, Ctrl-w = or going to the
        other pane shows both."""
        other = self.other
        if other.isHidden():
            self.show_both_panes()
            return
        self.leave_visual()
        other.hide()
        self.show_message("Only this pane   (Ctrl-w o again = both)")

    def cmd_close_pane(self, a):
        """Ctrl-w c / Ctrl-w q: close this pane, so only the other one shows."""
        if self.other.shows_terminal:
            self.show_message("The other pane is a terminal   (:vterm off closes it)")
            return
        if self.other.shows_playlist:
            self.show_message("The other pane shows the playlist   (Ctrl-w m or q in it closes it)")
            return
        self.leave_visual()
        self.activate(1 - self.active)
        self.cmd_only_pane(a)

    def cmd_split_pane(self, a):
        """Ctrl-w s / Ctrl-w v: both panes, the other one in this folder, like vim's split."""
        self.show_both_panes()
        if not self.other.covered:
            self.other.cd(self.pane.path)

    def ex_only(self, arg):
        if not self.other.isHidden():
            self.cmd_only_pane(None)

    def ex_vsplit(self, arg):
        """:vsplit [PATH]: both panes, the other one in this folder (or PATH)."""
        self.cmd_split_pane(None)
        if arg and not self.other.covered:
            self.other.cd(self._arg_path(arg))
    ex_split = ex_vsplit

    def ex_close(self, arg): self.cmd_close_pane(None)

    def cmd_equal_panes(self, a): self._set_ratio(0.5)
    def cmd_maximize_pane(self, a): self._set_ratio(0.9 if self.active == 0 else 0.1)

    def cmd_resize_pane(self, a):
        step = 0.05 * a.n() * int(a.arg)
        self._set_ratio(self.splitter.ratio + (step if self.active == 0 else -step))

    def cmd_swap_panes(self, a):
        """Ctrl-w x: the panes trade their folders, tabs and history; the cursor goes
        along to the other side."""
        if any(pane.covered for pane in self.panes):
            self.show_message("Ctrl-w x doesn't swap a terminal pane or the playlist")
            return
        self.leave_visual()
        self.show_both_panes()
        states = [pane.tab_state() for pane in self.panes]
        self._linking = True  # the panes stay where they are linked, only swapped
        try:
            for pane, state in zip(self.panes, reversed(states)):
                pane.set_tab_state(*state)
        finally:
            self._linking = False
        if self.link:
            self.link = (self.link[1], self.link[0])
        self.activate(1 - self.active)
        self.update_quickview()

    # Marks

    def cmd_set_mark(self, a):
        entry = self.pane.current_entry()
        folder = os.path.dirname(entry.path) if self.pane.results and entry and not entry.is_parent \
            else self.pane.path
        self.marks[a.arg] = [folder, entry.name if entry and not entry.is_parent else ""]
        self.show_message(f"Mark '{a.arg}' set")
        self.save_settings()

    def remember_jump(self) -> None:
        entry = self.pane.current_entry()
        self.pane.last_jump = (self.pane.path, entry.name if entry else "")

    def cmd_goto_mark(self, a):
        if a.arg in ("'", "`"):  # '' : back to where the last jump started
            target = self.pane.last_jump
            if not target:
                self.show_message("No previous jump")
                return
            self.remember_jump()
            folder, name = target
            if folder == self.pane.path:
                self.pane.set_cursor(max(self.pane.model.row_of(name), 0))
            else:
                self.pane.cd(folder, select=name or None)
            return
        if a.arg not in self.marks:
            self.show_message(f"Mark not set: {a.arg}")
            return
        folder, name = self.marks[a.arg]
        self.pane.cd(folder, select=name or None)

    # Launch bar

    def cmd_bar(self, a): self.bar.activate(int(a.arg) - 1)

    def bar_context(self) -> dict:
        entry = self.pane.current_entry()
        return {
            "cwd": os.path.expanduser("~") if self.pane.remote else self.pane.path,  # programs start here
            "other": self.other.path,
            "current": entry.path if entry and not entry.is_parent else "",
            "selected": [e.path for e in self.pane.targets()],
        }

    def on_bar_folder(self, path: str) -> None:
        self.pane.cd(path)
        self.pane.view.setFocus()
        self.update_status()

    def cmd_toggle_hidden(self, a): self.set_hidden(not self.pane.model.show_hidden)
    def cmd_show_hidden(self, a): self.set_hidden(a.arg == "on")

    def set_hidden(self, shown: bool) -> None:
        for pane in self.panes:
            if pane.model.show_hidden != shown:
                pane.model.show_hidden = shown
                pane.reload()
        self.show_message("Hidden files " + ("shown" if shown else "hidden"))

    # Selection

    def cmd_tag(self, a): self.pane.toggle_tag()
    def cmd_invert_tags(self, a): self.pane.invert_tags()
    def cmd_select_prompt(self, a): self.show_cmdline(":", "select *")

    def cmd_visual(self, a):
        self.engine.mode = VISUAL
        self.visual_anchor = self.pane.row

    def cmd_reselect(self, a):
        if self.last_visual:
            self.engine.mode = VISUAL
            self.visual_anchor = self.last_visual[0]
            self.pane.set_cursor(self.last_visual[1])

    def cmd_swap_anchor(self, a):
        row = self.pane.row
        self.pane.set_cursor(self.visual_anchor)
        self.visual_anchor = row

    def leave_visual(self) -> None:
        if self.engine.mode == VISUAL:
            self.last_visual = (self.visual_anchor, self.pane.row)
            self.engine.mode = NORMAL
            self.pane.clear_tags()
        self.engine.reset()

    def cmd_escape(self, a):
        self.leave_visual()
        self.pane.clear_tags()
        if search := self.searches.get(id(self.pane)):
            search.stop.set()
        if self.cdo_run:
            self.cdo_run.stop.set()

    # File operations

    def _take_targets(self, count: int) -> list[str]:
        paths = [e.path for e in self.pane.targets(count)]
        self.leave_visual()
        self.pane.clear_tags()
        return paths

    @property
    def register(self) -> tuple[str, list[str]] | None:
        """The unnamed register: what a plain p pastes."""
        if self._register_name(None) == "+":
            return clipboard.get_files()
        return self.registers.get('"')

    def _register_name(self, name: str | None) -> str:
        """None (no "x typed) means the unnamed register, or the clipboard with
        :set clipboard=unnamedplus. '*' is the same as '+'."""
        if name is None or name == '"':
            return "+" if self.settings.get("clipboard") in ("unnamed", "unnamedplus") else '"'
        return "+" if name == "*" else name

    def store(self, name: str | None, kind: str, paths: list[str]) -> None:
        """Yank or cut into a register, as vim does: "A appends to "a, and the
        unnamed register always gets a copy."""
        reg = self._register_name(name)
        if reg.isupper():
            reg = reg.lower()
            old = self.registers.get(reg)
            paths = (old[1] if old else []) + [p for p in paths if not old or p not in old[1]]
        if reg == "+":
            clipboard.put_files(kind, paths)
        else:
            self.registers[reg] = (kind, paths)
        self.registers['"'] = (kind, paths)
        if reg.isalnum():
            self.save_settings()
        where = "the clipboard" if reg == "+" else f'register "{reg}' if reg != '"' else ""
        verb = "Yanked" if kind == "copy" else "Cut"
        self.show_message(f"{verb} {len(paths)} item(s){' to ' + where if where else ''} - p to paste")

    def cmd_yank(self, a):
        if paths := self._take_targets(a.n()):
            self.store(a.register, "copy", paths)

    def cmd_cut(self, a):
        if self._read_only():
            return
        if paths := self._take_targets(a.n()):
            self.store(a.register, "cut", paths)

    def _to_clipboard(self, text: str, what: str) -> None:
        QApplication.clipboard().setText(text)
        self.show_message(f"Copied {what} to clipboard")

    def cmd_yank_path(self, a):
        if paths := self._take_targets(a.n()):
            self._to_clipboard("\n".join(paths), "path" if len(paths) == 1 else f"{len(paths)} paths")

    def cmd_yank_name(self, a):
        if paths := self._take_targets(a.n()):
            self._to_clipboard("\n".join(os.path.basename(p) for p in paths), "name")

    def cmd_yank_dir(self, a): self._to_clipboard(self.pane.path, "folder path")

    def cmd_paste(self, a):
        reg = self._register_name(a.register)
        content = clipboard.get_files() if reg == "+" else self.registers.get(reg.lower())
        if not content:
            self.show_message("No files on the clipboard" if reg == "+" else
                              "Nothing yanked" if reg == '"' else f'Register "{reg} is empty')
            return
        if self._read_only(into=True):
            return
        kind, paths = content
        self.start_job("copy" if kind == "copy" else "move", paths, self.pane.path)
        if kind == "cut":  # cut files can only be pasted once
            if reg == "+":
                clipboard.clear()
            for name, value in list(self.registers.items()):
                if value[1] == paths:
                    del self.registers[name]

    def ex_registers(self, arg):
        lines = []
        names = ['"'] + sorted(k for k in self.registers if k != '"') + ["+"]
        for name in names:
            content = clipboard.get_files() if name == "+" else self.registers.get(name)
            if content:
                kind, paths = content
                shown = ", ".join(os.path.basename(p) for p in paths[:4])
                more = f" … ({len(paths)} items)" if len(paths) > 4 else ""
                lines.append(f'<b>"{name}</b> &nbsp; {kind}: {shown}{more}')
        for name, keys in sorted(self.macros.items()):
            typed = "".join(k[2] if k[2] and k[2].isprintable() else "·" for k in keys)
            lines.append(f"<b>@{name}</b> &nbsp; macro, {len(keys)} keys: <code>{typed[:60]}</code>")
        if lines:
            QMessageBox.information(self, "Registers", "<br>".join(lines))
        else:
            self.show_message("All registers are empty")
    ex_reg = ex_di = ex_display = ex_registers

    def cmd_copy_other(self, a):
        if self._read_only(self.other, into=True):
            return
        if paths := self._take_targets(a.n()):
            self.start_job("copy", paths, self.other.path)

    def cmd_move_other(self, a):
        if self._read_only() or self._read_only(self.other, into=True):
            return
        if paths := self._take_targets(a.n()):
            self.start_job("move", paths, self.other.path)

    def _read_only(self, pane: Pane | None = None, into: bool = False) -> bool:
        """True (with a message) if the pane shows an archive, which can't be changed,
        or (`into`: files would be put there) a result list, which isn't a folder."""
        pane = pane or self.pane
        if pane.archive:
            self.show_message("Archives are read-only (except for deleting): copy files out with F5 or yy / p")
            return True
        if into and pane.results:
            self.show_message("A result list isn't a folder: h goes back to the folder, gf to the file's folder")
            return True
        return False

    def cmd_trash(self, a):
        entries = self.pane.targets(a.n())
        if not entries:
            return
        if self.pane.remote or self.pane.archive:  # no trash on the server or in a zip
            self.cmd_delete(a)
            return
        what = entries[0].name if len(entries) == 1 else f"{len(entries)} items"
        if (not self.settings.get("confirm_trash", True)
                or QMessageBox.question(self, "Move to trash", f"Move {what} to trash?")
                == QMessageBox.StandardButton.Yes):
            self.start_job("trash", self._take_targets(a.n()))

    def cmd_delete(self, a):
        entries = self.pane.targets(a.n())
        if not entries:
            return
        what = entries[0].name if len(entries) == 1 else f"{len(entries)} items"
        where = (f" on {self.pane.remote.host}" if self.pane.remote else
                 f" from {os.path.basename(self.pane.archive[0])}" if self.pane.archive else "")
        why = (" (a server has no trash)." if self.pane.remote else
               " (a zip has no trash)." if self.pane.archive else ".")
        answer = QMessageBox.warning(
            self, "Delete permanently", f"Permanently delete {what}{where}?\n\nThis can't be undone" + why,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.start_job("delete", self._take_targets(a.n()))

    def cmd_undo(self, a): self._undo_redo("undo", a.n())

    def _undo_redo(self, op: str, count: int) -> None:
        """u / Ctrl-r: reverse the last `count` changes (or undos) in one job."""
        stack = self.undo_stack if op == "undo" else self.redo_stack
        if not stack:
            self.show_message(f"Nothing to {op}")
            return
        entries = [stack.pop() for _ in range(min(count, len(stack)))]
        records = [r for _, recs, _ in entries for r in reversed(recs)]
        job = self.start_job(op, records)
        job.what = entries[0][0] if len(entries) == 1 else f"{len(entries)} changes"

    def cmd_rename(self, a):
        entry = self.pane.current_entry()
        if self.pane.selected_entries():
            self.show_cmdline(":", "rename {name}{ext}")
            self.cmdline.setSelection(len("rename "), len("{name}"))
        elif entry and not entry.is_parent:
            self.show_cmdline(":", f"rename {entry.name}")

    def cmd_rename_stem(self, a):
        entry = self.pane.current_entry()
        if entry and not entry.is_parent:
            self.cmd_rename(a)
            stem = entry.name if entry.is_dir else os.path.splitext(entry.name)[0]
            self.cmdline.setSelection(len("rename "), len(stem))

    def cmd_rename_at(self, a):
        """Vim's insert and change keys on the name: I / A = rename with the cursor at
        the start / end, cc = type a new name, C = a new name before the extension."""
        entry = self.pane.current_entry()
        if self.pane.selected_entries():
            self.cmd_rename(a)
            return
        if not entry or entry.is_parent:
            return
        ext = "" if entry.is_dir else os.path.splitext(entry.name)[1]
        text = {"clear": "", "stem": ext}.get(a.arg, entry.name)
        self.show_cmdline(":", "rename " + text)
        if a.arg in ("start", "stem"):
            self.cmdline.setCursorPosition(len("rename "))

    def cmd_upper(self, a): self.change_case(a.name, self.pane.targets(a.n()))
    cmd_lower = cmd_toggle_case = cmd_upper

    def cmd_upper_motion(self, a):
        """gU / gu / g~ + a motion: the case of the names from the cursor to where it lands."""
        paths = set(self._motion_paths(a))
        self.change_case(a.name.removesuffix("_motion"),
                         [e for e in self.pane.model.entries if e.path in paths])
    cmd_lower_motion = cmd_toggle_case_motion = cmd_upper_motion

    def change_case(self, how: str, entries: list[Entry]) -> None:
        """gU / gu / g~ (U / u / ~ in visual mode): the names in upper / lower / switched case."""
        if self._read_only():
            return
        self.leave_visual()
        self.rename_following([(e, CASES[how](e.name)) for e in entries if not e.is_parent])

    def cmd_increment(self, a): self._add_to_numbers(a.n(), a.arg == "g")
    def cmd_decrement(self, a): self._add_to_numbers(-a.n(), a.arg == "g")

    def _add_to_numbers(self, delta: int, steps: bool) -> None:
        """Ctrl-a / Ctrl-x: add `delta` to the last number in the names of the tagged
        items (or the one under the cursor). With g, like vim's g Ctrl-a, the first
        name with a number gets delta, the second 2 × delta, and so on."""
        if self._read_only():
            return
        entries = self.pane.targets()
        self.leave_visual()
        pairs = []
        for e in entries:
            if (new := increment(e.name, delta * (len(pairs) + 1 if steps else 1), e.is_dir)) is not None:
                pairs.append((e, new))
        if not pairs:
            self.show_message("No number in the name")
            return
        self.rename_following(pairs)

    def rename_following(self, pairs: list[tuple[Entry, str]]) -> None:
        """rename_many(), and the cursor stays on the renamed item, which may have moved."""
        cur = self.pane.current_entry()
        follow = next((remote.join(remote.dirname(e.path), new) for e, new in pairs
                       if cur and e.path == cur.path), None)
        self.rename_many(pairs)
        if follow:
            row = next((i for i, e in enumerate(self.pane.model.entries) if e.path == follow), None)
            if row is not None:
                self.pane.set_cursor(row)

    def cmd_mkdir(self, a): self.show_cmdline(":", "mkdir ")
    def cmd_new_file(self, a): self.show_cmdline(":", "touch ")

    def on_drop(self, paths: list[str], target: str, move: bool) -> None:
        if archive.locate(target):
            self.show_message("Archives are read-only")
            return
        self.start_job("move" if move else "copy", paths, target)

    def start_job(self, op: str, paths: list, target: str | None = None) -> Job:
        """Run a file operation in the background. A copy, move, sync or zip started
        while another one runs waits in the queue (unless "queue_jobs" is off)."""
        return self._queue_job(Job(op, paths, target))

    def _queue_job(self, job: Job) -> Job:
        op = job.op
        job.conflict.connect(lambda src, dest, job=job: job.answer(*ask_conflict(self, src, dest)))
        job.finished.connect(lambda msg, errors, job=job: self.on_job_done(job, msg, errors))
        self.jobs.append(job)
        self.jobs_panel.add(job)
        if op in QUEUED_OPS and self.settings.get("queue_jobs", True) and self._transfer_running():
            self.job_queue.append(job)
            self.jobs_panel.show_auto()
            self.show_message(f"Queued: {job.description}   (:jobs shows the queue)")
            return job
        self._run_job(job, dialog=True)
        return job

    def _transfer_running(self) -> bool:
        return any(j.started and j.op in QUEUED_OPS for j in self.jobs)

    def _run_job(self, job: Job, dialog: bool) -> None:
        if dialog:  # jobs started from the queue show in the panel only
            box = self.job_dialogs[id(job)] = ProgressDialog(job, self)
            box.background.connect(self.jobs_panel.show)
        self.show_message(job.description)
        job.start()
        self.jobs_panel.refresh(job)

    def _start_queued(self) -> None:
        """Start the next waiting job (skipping paused ones) when no transfer runs."""
        if self._transfer_running():
            return
        for job in self.job_queue:
            if not job.paused:
                self.job_queue.remove(job)
                self._run_job(job, dialog=False)
                return

    def cancel_job(self, job: Job) -> None:
        if job in self.job_queue:  # never started: just drop it
            self.job_queue.remove(job)
            self.jobs.remove(job)
            self.jobs_panel.remove(job)
            self.show_message(f"Removed from the queue: {job.description}")
            return
        job.cancel()

    def job_first(self, job: Job) -> None:
        if job in self.job_queue:
            self.job_queue.remove(job)
            self.job_queue.insert(0, job)
            self.jobs_panel.reorder([j for j in self.jobs if j.started] + self.job_queue)

    def ex_hex(self, arg):
        """:hex shows every file in quick view as hex (binary files always are); again = off."""
        preview.HEX["on"] = {"on": True, "off": False}.get(arg.strip(), not preview.HEX["on"])
        for pane in self.panes:
            for view in (pane.preview, pane.column_preview):
                view.current = None  # show it again
        self.update_quickview()
        if self.pane.columns:
            self.pane.update_columns()
        self.show_message("Hex view " + ("on: every file in hex" if preview.HEX["on"] else
                                         "off (binary files still show in hex)"))

    def cmd_model_keys(self, a):
        """zv: the 3D model in quick view takes the keys (h j k l rotate…); Esc gives them back."""
        for view in (self.other.preview, self.pane.column_preview):
            if view.isVisible() and view.stack.currentWidget() is view.model3d and view.model3d.focus_keys():
                return
        self.show_message("No 3D model in quick view (Ctrl-q on a .step or .stl file)")

    def cmd_plot(self, a):
        """zp: quick view shows CSV files as a plot of their numeric columns; again = as a table."""
        preview.PLOT["on"] = not preview.PLOT["on"]
        for pane in self.panes:
            for view in (pane.preview, pane.column_preview):
                view.current = None  # show it again
        if preview.PLOT["on"] and not self.quickview:
            self.cmd_quickview(a)
        else:
            self.update_quickview()
        if self.pane.columns:
            self.pane.update_columns()
        self.show_message("CSV files show as plots: drag = zoom, right-click = back (zp = table)"
                          if preview.PLOT["on"] else "CSV files show as tables (zp = plot)")

    def ex_jobs(self, arg):
        """:jobs shows / hides the jobs panel; :jobs pause / resume / cancel act on all jobs."""
        arg = arg.strip()
        if arg in ("pause", "resume"):
            for job in self.jobs:
                job.pause() if arg == "pause" else job.resume()
                self.jobs_panel.refresh(job)
            if arg == "resume":
                self._start_queued()
            self.show_message(f"{len(self.jobs)} job(s) {'paused' if arg == 'pause' else 'resumed'}")
        elif arg == "cancel":
            for job in list(self.jobs):
                self.cancel_job(job)
        elif arg:
            self.show_message("Usage: :jobs [pause|resume|cancel]")
        else:
            self.jobs_panel.auto_shown = False
            self.jobs_panel.setVisible(not self.jobs_panel.isVisible())

    def on_job_done(self, job: Job, message: str, errors: list[str]) -> None:
        self.jobs.remove(job)
        self.jobs_panel.remove(job)
        if dialog := self.job_dialogs.pop(id(job), None):
            dialog.finish()
        self._start_queued()
        if job.op in ("undo", "redo"):
            other = self.redo_stack if job.op == "undo" else self.undo_stack
            if job.undo:
                other.append((job.what, job.undo, time.time()))
            self._save_undo()
        elif job.undo:
            self._record(f"{job.op} of {len(job.undo)} item(s)", job.undo)
        if moved := [(r[1], r[2]) for r in job.undo if r[0] in ("moved", "renamed")]:
            self._renamed_in_lists(moved)  # moved files stay in result lists and on the shelf
        self.shelf.prune()
        for pane in self.panes:
            pane.reload()
        if errors:
            QMessageBox.warning(self, "Some operations failed", "\n".join(errors[:20]))
        if job.op == "verify" and not job.cancelled:
            if job.bad:
                QMessageBox.warning(self, "Checksums", f"{len(job.bad)} file(s) failed:\n\n" + "\n".join(job.bad[:30]))
                message = f"Checksums: {len(job.bad)} file(s) differ or are missing"
            elif not errors:
                message = "Checksums: all files OK"
        if job.op in ("undo", "redo") and not job.cancelled:
            hint = "   (Ctrl-r = redo)" if job.op == "undo" else "   (u = undo)"
            message = f"{'Undid' if job.op == 'undo' else 'Redid'} {job.what}{hint}"
        elif job.undo:
            message += "   (u = undo)"
        self.show_message(message)

    # Folder sizes

    def cmd_dir_size(self, a):
        entries = [e for e in self.pane.targets(a.n()) if e.is_dir]
        if not entries:
            self.show_message("No folder under the cursor")
            return
        self._add_up([e.path for e in entries])

    def cmd_all_dir_sizes(self, a):
        self._add_up([e.path for e in self.pane.model.entries if e.is_dir and not e.is_parent])

    def _add_up(self, paths: list[str]) -> None:
        if self._read_only() or not paths:
            return
        job = SizeJob(paths)
        job.finished.connect(lambda sizes, job=job: self._sizes_done(job, sizes))
        self.size_jobs.append(job)
        self.show_message(f"Adding up {len(paths)} folder(s)…")
        job.start()

    def _sizes_done(self, job: SizeJob, sizes: dict) -> None:
        self.size_jobs.remove(job)
        DIR_SIZES.update(sizes)
        for pane in self.panes:
            pane.reload()
        self.show_message(f"{len(sizes)} folder(s): {human_size(sum(sizes.values()))}")

    # Quick view

    def cmd_quickview(self, a):
        self.quickview = not self.quickview
        if not self.quickview:
            for pane in self.panes:
                pane.show_preview(False)
        self.update_quickview()

    def update_quickview(self) -> None:
        if not self.quickview:
            return
        self.show_both_panes()
        self.pane.show_preview(False)
        self.other.show_preview(True)
        entry = self.pane.current_entry()
        self.other.preview.show_hidden = self.pane.model.show_hidden
        if entry is None or entry.is_parent:  # '..': show the current folder itself
            self.other.preview.show_path(self.pane.path, True)
        else:
            self.other.preview.show_path(entry.path, entry.is_dir)

    # Which-key, palette and descriptions

    def describe(self, value: str) -> str:
        """A binding value ("down", "bar:3", "ex:cd ~") in words."""
        name, _, arg = value.partition(":")
        if name == "ex":
            return f":{arg}"
        if name == "bar" and arg.isdigit():
            items = self.bar.items
            n = int(arg)
            return f"bar button {n}" + (f": {items[n - 1].name}" if n <= len(items) else "")
        if name == "drives":
            return "drive menu, " + ("left" if arg == "1" else "right") + " pane"
        return ACTIONS.get(name, name.replace("_", " "))

    def whichkey_entries(self) -> tuple[str, list[tuple[str, str]]]:
        engine = self.engine
        typed = engine.pending_display
        if engine._register_next:
            entries = [('"', "unnamed register"), ("+", "system clipboard"), ("*", "system clipboard"),
                       ("a-z", "named register (A-Z adds to it)")]
            for name, (kind, paths) in sorted(self.registers.items()):
                if name != '"':
                    entries.append((name, f"{kind}: {len(paths)} item(s)"))
            return f'{typed} — register for the next yank, cut or paste', entries
        if engine.awaiting:
            what = engine.awaiting
            if what == "set_mark":
                return f"{typed} — set a mark", [("a-z", "mark this file")] + [
                    (k, f"(replace) {os.path.join(*v) if v[1] else v[0]}") for k, v in sorted(self.marks.items())]
            if what == "goto_mark":
                return f"{typed} — go to a mark", [("'", "back to before the last jump")] + [
                    (k, os.path.join(*v) if v[1] else v[0]) for k, v in sorted(self.marks.items())]
            if what in ("find_char", "find_char_back"):
                return f"{typed} — a character", [("x", ACTIONS[what].replace("…", " x"))]
            if what == "record":
                return f"{typed} — record a macro (q stops)", [("a-z 0-9", "register to record into")] + [
                    (k, f"(replace) {len(v)} keys") for k, v in sorted(self.macros.items())]
            if what == "at":
                entries = [(":", "repeat the last : command"), ("@", "play the last macro again")]
                return f"{typed} — play a macro", entries + [
                    (k, f"{len(v)} keys") for k, v in sorted(self.macros.items())]
            return typed, []
        table = engine.bindings.get(engine.mode, {})
        prefix = engine.pending
        entries, motion_ops = [], False
        for key, value in sorted(table.items()):
            if key.startswith(prefix) and key != prefix:
                if "_motion:" in value:
                    motion_ops = True
                    continue
                entries.append((key[len(prefix):], self.describe(value)))
        for key, value in sorted(engine.arg_bindings.get(engine.mode, {}).items()):
            if key.startswith(prefix) and key != prefix:
                entries.append((key[len(prefix):] + "…", ACTIONS.get(value, value)))
        if motion_ops:
            verb = "yank" if prefix.startswith("y") else "cut"
            entries.append(("motion", f"{verb} to where it goes: j k gg G H M L {{ }} Ctrl-d…"))
        return typed, entries

    def show_whichkey(self) -> None:
        engine = self.engine
        if (not (engine.pending or engine.awaiting or engine._register_next)
                or QApplication.activeModalWidget() is not None or not self.isActiveWindow()):
            return  # nothing typed yet, or a dialog or another window is in front
        title, entries = self.whichkey_entries()
        if entries:
            self.whichkey.show_entries(title, entries, self.info_label)

    def palette_entries(self) -> dict[str, tuple]:
        keys_for: dict[str, list[str]] = {}
        for key, value in self.engine.bindings[NORMAL].items():
            if "_motion:" not in value:
                keys_for.setdefault(value, []).append(key)
        entries: dict[str, tuple] = {}
        for value, keys in keys_for.items():
            name, _, arg = value.partition(":")
            if name != "ex" and not hasattr(self, "cmd_" + name):
                continue
            shown = ", ".join(pretty(k) for k in sorted(keys, key=len)[:3])
            label = f"{self.describe(value)[:46]:<48}{shown}"
            entries[label] = ("ex", arg) if name == "ex" else ("action", name, arg or None)
        for name, (usage, desc) in COMMANDS.items():
            if hasattr(self, "ex_" + name):
                entries[f":{usage[:45]:<47}{desc}"] = ("command", name)
        for name, line in (self.settings.get("commands") or {}).items():
            entries[f":{name:<47}your command: {line}"] = ("command", name)
        return entries

    def cmd_palette(self, a):
        dialog = PaletteDialog(self.palette_entries(), self, self.cmdline.font())
        if dialog.exec() and dialog.chosen:
            entry = dialog.entries[dialog.chosen]
            if entry[0] == "action":
                self.run_action(entry[1], None, entry[2])
            elif entry[0] == "ex":
                self.execute(entry[1])
            elif needs_argument(entry[1]):
                self.show_cmdline(":", entry[1] + " ")
            else:
                self.execute(entry[1])
        self.update_status()

    def ex_palette(self, arg): self.cmd_palette(None)

    # Fuzzy file finder

    def cmd_files(self, a):
        if self._local_only("Find files"):
            return
        if self.pane.archive:
            self.show_message("The file finder doesn't look inside archives")
            return
        frecency = {}
        if self.zoxide:
            try:
                frecency = {path: score for score, path in self.zoxide.list()}
            except OSError:
                pass
        dialog = FileFinder(self.pane.path, self.pane.model.show_hidden, frecency, self,
                            self.cmdline.font())
        if not (dialog.exec() and dialog.chosen):
            return
        path = dialog.chosen_path()
        is_dir = dialog.chosen.endswith(("/", "\\"))
        self.remember_jump()
        if dialog.how == "all":
            self.show_result_list(f"files {dialog.query.text().strip()}", dialog.root,
                                  [os.path.join(dialog.root, item.rstrip("/\\")) for item in dialog.shown])
        elif dialog.how == "tab":
            self.pane.new_tab(path if is_dir else os.path.dirname(path))
            if not is_dir:
                self.pane.set_cursor(self.pane.model.row_of(os.path.basename(path)))
        elif is_dir:
            self.pane.cd(path)
        else:
            self.pane.cd(os.path.dirname(path), select=os.path.basename(path))
            if dialog.how == "alt":
                self.run_action("open")
        self.update_status()

    def ex_files(self, arg): self.cmd_files(None)

    # Bulk rename

    def ex_bulkrename(self, arg):
        if self._read_only():
            return
        entries = self.pane.selected_entries() or [e for e in self.pane.model.entries if not e.is_parent]
        if not entries:
            return

        def open_in_editor(path: str) -> bool:
            editor = self.settings["programs"].get("editor")
            if editor:
                return run_command(editor, self.bar_context(), [path])
            return QDesktopServices.openUrl(QUrl.fromLocalFile(path))

        dialog = BulkRenameDialog([e.name for e in entries], open_in_editor, self, self.cmdline.font())
        if not dialog.exec():
            return
        if dialog.renames:
            self.rename_many([(entries[i], name) for i, name in dialog.renames.items()])
        if dialog.deleted:
            gone = [entries[i] for i in sorted(dialog.deleted)]
            what = gone[0].name if len(gone) == 1 else f"{len(gone)} items"
            if QMessageBox.question(self, "Move to trash",
                                    f"You deleted the lines of {what}. Move them to the trash?") \
                    == QMessageBox.StandardButton.Yes:
                self.start_job("trash", [e.path for e in gone])
    ex_br = ex_bulkrename

    # Compare and synchronize

    def ex_compare(self, arg):
        """Tag what differs: in each pane, the items that are newer there or only there."""
        left, right = self.panes
        if left.archive or right.archive or left.results or right.results:
            self.show_message("Can't compare inside archives or result lists")
            return
        diffs = compare_trees(left.path, right.path, recursive=False,
                              show_hidden=left.model.show_hidden)
        n_left = left.tag_names({d.rel for d in diffs if d.status in ("left_only", "left_newer", "different")})
        n_right = right.tag_names({d.rel for d in diffs if d.status in ("right_only", "right_newer", "different")})
        if not diffs:
            self.show_message("The two folders have the same items (by name, size and date)")
        else:
            self.show_message(f"Tagged {n_left} item(s) newer or only on the left, {n_right} on the right. "
                              "F5 copies them across; :synchronize compares subfolders too")
    ex_diff = ex_compare

    def ex_synchronize(self, arg):
        left, right = self.panes
        if left.archive or right.archive or left.results or right.results:
            self.show_message("Can't synchronize archives or result lists")
            return
        dialog = SyncDialog(left.path, right.path, left.model.show_hidden, self)
        self._run_sync(dialog)

    def ex_mirror(self, arg):
        """:mirror makes the other pane's folder a copy of this one's: it copies what is new
        or changed here and deletes what only the other side has (after a preview)."""
        if any(p.archive or p.results for p in self.panes):
            self.show_message("Can't mirror archives or result lists")
            return
        dialog = SyncDialog(self.pane.path, self.other.path, self.pane.model.show_hidden, self, mirror=True)
        self._run_sync(dialog)

    def _run_sync(self, dialog: SyncDialog) -> None:
        dialog.diff_requested.connect(self.diff_files)
        if not dialog.exec():
            return
        if dialog.plan:
            self.start_job("sync", dialog.plan)
        if dialog.deletes:
            local = [p for p in dialog.deletes if not remote.is_remote(p)]
            far = [p for p in dialog.deletes if remote.is_remote(p)]
            if local:
                self.start_job("trash", local)  # on this computer they go to the trash (u brings them back)
            if far:
                self.start_job("delete", far)

    # Shelf and zip

    def cmd_shelf_add(self, a):
        """zs: put the tagged items (or the cursor's) on the shelf."""
        if self._local_only("The shelf"):
            return
        paths = [p for p in self._take_targets(a.n()) if not archive.inside(p)]
        if not paths:
            self.show_message("Nothing to put on the shelf (files inside archives can't go on it)")
            return
        added = self.shelf.add(paths)
        self.shelf.show()
        self.show_message(f"{added} item(s) put on the shelf, {len(self.shelf.paths)} on it   "
                          "(:shelf copy / move / zip / list, zS shows it)")

    def cmd_shelf(self, a):
        """zS: show the shelf and go to it, or hide it."""
        if self.shelf.isVisible() and self.shelf.list.hasFocus():
            self.shelf.hide()
            self.pane.view.setFocus()
            self.save_settings()
        else:
            self.shelf.show()
            self.shelf.list.setFocus()
            if self.shelf.list.currentRow() < 0 and self.shelf.list.count():
                self.shelf.list.setCurrentRow(0)
            self.save_settings()

    def ex_shelf(self, arg):
        """:shelf [add | copy | move | zip [NAME] | list | clear]"""
        sub, _, rest = arg.partition(" ")
        if sub == "":
            self.cmd_shelf(None)
            return
        if sub == "add":
            self.cmd_shelf_add(Action("shelf_add", None))
            return
        if sub == "clear":
            self.shelf.clear()
            self.show_message("The shelf is empty")
            return
        paths = self.shelf.selected_paths()
        if not paths:
            self.show_message("The shelf is empty: zs puts files on it")
            return
        if sub in ("copy", "move"):
            if not self._read_only(into=True):
                self.start_job(sub, paths, self.pane.path)
        elif sub == "zip":
            self.ex_zip(rest.strip(), paths)
        elif sub == "list":
            folders = [os.path.dirname(p) for p in paths]
            try:
                root = os.path.commonpath(folders)
            except ValueError:  # several drives
                root = self.pane.path
            self.remember_jump()
            self.show_result_list("shelf", root, paths)
        else:
            self.show_message("Usage: :shelf [add | copy | move | zip [NAME] | list | clear]")

    def ex_zip(self, arg, paths: list[str] | None = None):
        """:zip [NAME] packs the tagged items (or the cursor's) into NAME.zip in this folder."""
        if self._read_only(into=True):
            return
        if paths is None:
            paths = self._take_targets(1)
        if not paths:
            self.show_message("Nothing to zip")
            return
        name = arg or (os.path.splitext(os.path.basename(paths[0]))[0] if len(paths) == 1
                       else os.path.basename(self.pane.path.rstrip("/\\")) or "archive")
        if not name.lower().endswith(".zip"):
            name += ".zip"
        dest = os.path.join(self.pane.path, name) if arg else unique_dest(self.pane.path, name)
        if os.path.lexists(dest):
            self.show_message(f"Already exists: {name}")
            return
        self.start_job("zip", paths, dest)

    def ex_pdfmerge(self, arg):
        """:pdfmerge [NAME] joins the tagged PDFs, in the pane's order, into NAME.pdf here."""
        if self._read_only(into=True) or self._local_only(":pdfmerge"):
            return
        entries = self.pane.selected_entries()
        pdfs = [e.path for e in entries if not e.is_dir and pdftools.is_pdf(e.name)]
        if len(pdfs) < 2 or len(pdfs) != len(entries):
            self.show_message("Tag two or more PDFs to merge (only PDFs); they are joined in the pane's order")
            return
        name = arg.strip() or os.path.splitext(os.path.basename(pdfs[0]))[0] + " merged"
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        dest = os.path.join(self.pane.path, name) if arg.strip() else unique_dest(self.pane.path, name)
        if os.path.lexists(dest):
            self.show_message(f"Already exists: {name}")
            return
        self._take_targets(1)
        self.start_job("pdfmerge", pdfs, dest)

    def ex_pdfextract(self, arg):
        """:pdfextract PAGES [NAME] copies pages (3, 2-5, 1,4,7-, 9-7) of the PDF under the
        cursor, or of each tagged PDF, into a new PDF next to it: NAME.pdf or "<name> pPAGES.pdf"
        (so it works in a result list too)."""
        spec, _, name = arg.strip().partition(" ")
        if not spec:
            self.show_message("Usage: :pdfextract PAGES [NAME], e.g. :pdfextract 2-5 or :pdfextract 1,3,7-")
            return
        if self._read_only() or self._local_only(":pdfextract"):
            return
        entries = self.pane.targets(1)
        pdfs = [e.path for e in entries if not e.is_dir and pdftools.is_pdf(e.name)]
        if not pdfs or len(pdfs) != len(entries):
            self.show_message("Put the cursor on a PDF, or tag PDFs (only PDFs)")
            return
        name = name.strip()
        if name and len(pdfs) > 1:
            self.show_message("A NAME works for one PDF; with several, each gets \"<name> pPAGES.pdf\"")
            return
        if name:
            name += "" if name.lower().endswith(".pdf") else ".pdf"
            dest = os.path.join(os.path.dirname(pdfs[0]), name)
            if os.path.lexists(dest) and not self.bang:
                self.show_message(f"Already exists: {name}   (:pdfextract! replaces it)")
                return
            pairs = [(pdfs[0], dest)]
        else:
            label = re.sub(r'[\\/:*?"<>|]', "_", spec.replace(" ", ""))
            pairs = [(p, unique_dest(os.path.dirname(p), f"{os.path.splitext(os.path.basename(p))[0]} p{label}.pdf"))
                     for p in pdfs]
        self._take_targets(1)
        job = Job("pdfextract", pairs)
        job.pages, job.overwrite = spec, self.bang
        self._queue_job(job)

    def cmd_pack(self, a):
        """Alt+F5: pack the tagged items into a zip in the other pane, with Total Commander's options."""
        if self._read_only(self.other, into=True) or self._remote_pane("Pack", self.other):
            return
        paths = self.pane_paths_for_pack(a.n())
        if not paths:
            return
        name = (os.path.splitext(os.path.basename(paths[0]))[0] if len(paths) == 1
                else os.path.basename(self.pane.path.rstrip("/\\")) or "archive") + ".zip"
        box = PackDialog(self, len(paths), unique_dest(self.other.path, name))
        if box.exec() != QDialog.DialogCode.Accepted:
            self.pane.view.setFocus()
            return
        opts = box.options()
        dest = os.path.abspath(os.path.expanduser(opts["dest"]))
        if not dest.lower().endswith(".zip"):
            dest += ".zip"
        if opts["separate"] and len(paths) > 1:
            folder = os.path.dirname(dest)
            plan = [([p], unique_dest(folder, os.path.splitext(os.path.basename(p.rstrip("/\\")))[0] + ".zip"))
                    for p in paths]
        else:
            if os.path.lexists(dest):
                self.show_message(f"Already exists: {dest}")
                return
            plan = [(paths, dest)]
        if not os.path.isdir(os.path.dirname(dest)):
            self.show_message(f"No such folder: {os.path.dirname(dest)}")
            return
        for sources, target in plan:
            job = Job("zip", sources, target)
            job.zip_level, job.zip_move = opts["level"], opts["move"]
            self._queue_job(job)
        self.pane.view.setFocus()

    def pane_paths_for_pack(self, count: int) -> list[str]:
        if self._remote_pane("Pack", self.pane):
            return []
        paths = self._take_targets(count)
        if not paths:
            self.show_message("Nothing to pack")
        elif any(archive.inside(p) for p in paths):
            self.show_message("Copy files out of the archive first")
            return []
        return paths

    def cmd_unpack(self, a):
        """Alt+F9: unpack the tagged zips (or the one under the cursor) into the other pane."""
        if self._read_only(self.other, into=True) or self._remote_pane("Unpack", self.pane) \
                or self._remote_pane("Unpack", self.other):
            return
        zips = [p for p in self._take_targets(a.n()) if archive.is_archive(p)]
        if not zips:
            self.show_message("No zip file under the cursor or tagged")
            return
        box = UnpackDialog(self, [os.path.basename(z) for z in zips], self.other.path)
        if box.exec() != QDialog.DialogCode.Accepted:
            self.pane.view.setFocus()
            return
        opts = box.options()
        dest = os.path.abspath(os.path.expanduser(opts["dest"]))
        for z in zips:
            try:
                names = [n for n, *_ in archive.list_dir(z, "")]
            except (OSError, zipfile.BadZipFile) as e:
                self.show_message(f"{os.path.basename(z)}: {e}")
                continue
            target, made = dest, []
            if opts["subfolder"]:
                target = os.path.join(dest, os.path.splitext(os.path.basename(z))[0])
            if not os.path.isdir(target):
                try:
                    os.makedirs(target)
                except OSError as e:
                    self.show_message(f"Can't create {target}: {e.strerror or e}")
                    return
                made = [("created", target)]
            job = Job("copy", [os.path.join(z, n) for n in names], target)
            job.undo.extend(made)  # undo also removes the folder made for it
            self._queue_job(job)
        self.pane.view.setFocus()

    def cmd_branch(self, a):
        """gb: Total Commander's branch view, every file below this folder in one list."""
        self.ex_branch("")

    def ex_branch(self, arg):
        if self._local_only(":branch"):
            return
        if self.pane.archive or self.pane.results:
            self.show_message("Run :branch in a folder")
            return
        root, hidden = self.pane.path, self.pane.model.show_hidden
        self.remember_jump()
        name = os.path.basename(root.rstrip("/\\")) or root
        self.start_results(f"branch {name}", root,
                           lambda stop: branch_paths(root, hidden, stop))

    def ex_hash(self, arg):
        """:hash [md5|sha1|sha256|sha512] writes a checksum file for the tagged items;
        :hash check (or :hash on a checksum file) verifies one."""
        if self._local_only(":hash") or self._read_only(into=True):
            return
        word = arg.strip().lower()
        entries = [e for e in self.pane.targets(1) if not e.is_parent]
        if word in ("check", "verify") or (not word and len(entries) == 1 and
                                           os.path.splitext(entries[0].name)[1][1:].lower() in HASHES):
            sums = [p for p in self._take_targets(1) if os.path.splitext(p)[1][1:].lower() in HASHES]
            if not sums:
                self.show_message(f"Put the cursor on a checksum file ({', '.join('.' + h for h in HASHES)})")
                return
            self.start_job("verify", sums)
            return
        algorithm = word or "sha256"
        if algorithm not in HASHES:
            self.show_message(f"Usage: :hash [{' | '.join(HASHES)} | check]")
            return
        paths = self._take_targets(1)
        if not paths:
            self.show_message("Nothing to hash")
            return
        stem = (os.path.basename(paths[0].rstrip("/\\")) if len(paths) == 1
                else os.path.basename(self.pane.path.rstrip("/\\")) or "checksums")
        self.start_job("hash", paths, unique_dest(self.pane.path, f"{stem}.{algorithm}"))

    def goto_path(self, path: str) -> None:
        """Show a file in the active pane, with the cursor on it."""
        self.remember_jump()
        self.pane.cd(os.path.dirname(path), select=os.path.basename(path))
        self.pane.view.setFocus()
        self.update_status()

    # SSH

    def ask_password(self, prompt: str) -> str | None:
        QApplication.restoreOverrideCursor()
        text, ok = QInputDialog.getText(self, "SSH", prompt, QLineEdit.EchoMode.Password)
        return text if ok else None

    def ask_host_key(self, host: str, kind: str, fingerprint: str) -> bool:
        QApplication.restoreOverrideCursor()
        return QMessageBox.question(
            self, "Unknown SSH host",
            f"VimCommander hasn't connected to <b>{host}</b> before.<br><br>Its {kind} key fingerprint is"
            f"<br><code>{fingerprint}</code><br><br>Trust it and connect? (It is saved in "
            "~/.ssh/known_hosts, like ssh does.)") == QMessageBox.StandardButton.Yes

    def ex_ssh(self, arg):
        """:ssh [user@]host[:path] opens a folder on an SSH server; :ssh close disconnects."""
        arg = arg.strip()
        if arg == "close":
            remote.REMOTE.close_all()
            for pane in self.panes:
                if pane.remote:
                    pane.cd(os.path.expanduser("~"))
            self.show_message("SSH and FTP connections closed")
            return
        if not arg:
            hosts = remote.ssh_hosts()
            self.show_message("Usage: :ssh [user@]host[:path]" + (
                f"   hosts in ~/.ssh/config: {', '.join(hosts)}" if hosts else ""))
            return
        url = remote.from_spec(arg)
        if remote.parse(url) is None:
            self.show_message(f"Not an SSH address: {arg}")
            return
        self.remember_jump()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok = self.pane.cd(url)
        finally:
            QApplication.restoreOverrideCursor()
        if ok:
            self.show_message(f"Connected to {self.pane.remote.host}. F5 / F6 copy and move across, "
                              "F4 edits (saving uploads), Ctrl-` = a shell there, :mirror = sync")

    def ex_ftp(self, arg):
        """:ftp [user@]host[:port][/path] opens a folder on an FTP server (ftps://… for FTPS);
        :ftp close disconnects."""
        arg = arg.strip()
        if arg == "close":
            self.ex_ssh("close")
            return
        if not arg:
            self.show_message("Usage: :ftp [user@]host[:port][/path]   (no user = anonymous; "
                              "ftps://host for FTP over TLS; passwords from ~/.netrc or asked)")
            return
        if not remote.is_remote(arg):
            target, _, path = arg.partition("/")
            arg = f"ftp://{target}/{path}" if path else f"ftp://{target}/~"
        loc = remote.parse(arg)
        if loc is None or not loc.is_ftp:
            self.show_message(f"Not an FTP address: {arg}")
            return
        self.remember_jump()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok = self.pane.cd(arg)
        finally:
            QApplication.restoreOverrideCursor()
        if ok:
            self.show_message(f"Connected to {loc.host} (FTP). F5 / F6 copy and move across, "
                              "F4 edits (saving uploads)")

    def on_remote_edit_saved(self, local: str) -> None:
        """An edited copy of a remote file was saved: upload it (once the editor is done writing)."""
        url = self.remote_edits.get(local)
        if not url:
            return
        if local not in self.edit_watcher.files() and os.path.exists(local):
            self.edit_watcher.addPath(local)  # editors that save by replacing the file
        timers = getattr(self, "_upload_timers", {})
        self._upload_timers = timers
        if local not in timers:
            timer = timers[local] = QTimer(self, singleShot=True, interval=500)
            timer.timeout.connect(lambda local=local: self._upload_edit(local))
        timers[local].start()

    def _upload_edit(self, local: str) -> None:
        url = self.remote_edits.get(local)
        if not url or not os.path.exists(local):
            return
        try:
            remote.REMOTE.upload(local, url)
        except OSError as e:
            QMessageBox.warning(self, "Upload failed", f"{remote.basename(url)} was not uploaded: "
                                f"{e.strerror or e}\n\nYour changes are in {local}")
            return
        self.show_message(f"Uploaded {remote.basename(url)} to {remote.parse(url).host}")
        for pane in self.panes:
            if pane.remote and remote.dirname(url) == pane.path:
                pane.reload()

    def _local_only(self, what: str) -> bool:
        """True (with a message) when the pane shows an SSH folder."""
        if self.pane.remote:
            self.show_message(f"{what}: {LOCAL_ONLY}")
            return True
        return False

    def _remote_pane(self, what: str, pane: Pane) -> bool:
        if pane.remote:
            self.show_message(f"{what}: {LOCAL_ONLY}")
            return True
        return False

    def diff_files(self, a: str, b: str) -> None:
        files = [self._local_copy(p) for p in (a, b)]
        if None not in files:
            DiffDialog(files[0], files[1], self, self.cmdline.font(),
                       titles=(a, b) if any(map(remote.is_remote, (a, b))) else None).exec()

    # Space: :reclaim and :dupes

    def ex_reclaim(self, arg):
        """:reclaim [SIZE] [DAYS]: rebuildable caches and big files unchanged for DAYS, biggest first."""
        if self._local_only(":reclaim"):
            return
        if self.pane.archive or self.pane.results:
            self.show_message("Run :reclaim in a folder")
            return
        words = arg.split()
        try:
            big = cleanup.parse_size(words[0]) if words else 100 * 1024 ** 2
            days = int(words[1]) if len(words) > 1 else 180
        except ValueError:
            self.show_message("Usage: :reclaim [SIZE] [DAYS], e.g. :reclaim 200M 365")
            return
        root, hidden = self.pane.path, True
        self.remember_jump()
        self.start_results("reclaim", root, lambda stop: cleanup.reclaim(root, hidden, stop, big, days))

    def ex_dupes(self, arg):
        """:dupes [MINSIZE] finds duplicate files; :dupes tag [old] tags all but the newest (oldest) of each set."""
        if self._local_only(":dupes"):
            return
        word = arg.split()[0] if arg.split() else ""
        if word == "tag":
            results = self.pane.results
            if not results or not results.groups:
                self.show_message("First :dupes, then :dupes tag in its result list")
                return
            extra = set(cleanup.keep_one(results.groups, newest="old" not in arg.split()[1:]))
            self.pane.clear_tags()
            sel = self.pane.view.selectionModel()
            from PySide6.QtCore import QItemSelectionModel
            for row, e in enumerate(self.pane.model.entries):
                if e.path in extra:
                    sel.select(self.pane.model.index(row, 0), QItemSelectionModel.SelectionFlag.Select
                               | QItemSelectionModel.SelectionFlag.Rows)
            keep = "oldest" if "old" in arg.split()[1:] else "newest"
            self.show_message(f"Tagged {len(extra)} copies; the {keep} of each set is left. Del trashes them")
            self.update_status()
            return
        if self.pane.archive or self.pane.results:
            self.show_message("Run :dupes in a folder")
            return
        try:
            min_size = cleanup.parse_size(word) if word else 1
        except ValueError:
            self.show_message("Usage: :dupes [MINSIZE] (e.g. :dupes 1M), or :dupes tag [old] in its list")
            return
        root, hidden = self.pane.path, self.pane.model.show_hidden
        self.remember_jump()
        self.start_results("duplicates", root, lambda stop: cleanup.dupes(root, hidden, stop, min_size))

    # Git

    def on_listed(self, pane: Pane) -> None:
        if self.git_program and not pane.archive and not pane.results and not pane.remote:
            known = self.git_repos.get(id(pane))
            if known and pane.branch and child_name(known[0], pane.path) is None and \
                    os.path.normcase(known[0]) != os.path.normcase(pane.path):
                pane.set_git({}, "")  # left the repository: no stale branch while git runs
            pane.git_timer.start()
        if self.link:
            self.mark_unique()

    def run_git(self, pane: Pane) -> None:
        if not self.git_program or pane.archive or pane.results or pane.remote:
            return
        if old := self._git_runs.pop(id(pane), None):
            old.stop.set()
        folder, git = pane.path, self.git_program
        run = Search(lambda stop: iter([[(folder, gitstatus.status(git, folder))]]))

        def done(batch):
            path, result = batch[0]
            if pane.path != path or pane.results:
                return
            if result is None:
                self.git_repos.pop(id(pane), None)
                pane.set_git({}, "")
            else:
                repo, branch, files = result
                self.git_repos[id(pane)] = (repo, files)
                pane.set_git(gitstatus.folder_kinds(repo, files, path), branch)
        run.found.connect(done)
        self._git_runs[id(pane)] = run
        run.start()

    def _git_status(self) -> tuple[str, str, dict] | None:
        if not self.git_program:
            self.show_message("git not found: install it, or set programs.git (and :set git)")
            return None
        result = gitstatus.status(self.git_program, self.pane.path)
        if result is None:
            self.show_message("Not in a git repository")
        return result

    def ex_git(self, arg):
        """:git = branch and counts; :git changed = changed files as a list; :git diff = file vs HEAD."""
        sub = arg.strip()
        if sub in ("", "status"):
            if result := self._git_status():
                repo, branch, files = result
                counts = {}
                for kind in files.values():
                    counts[kind] = counts.get(kind, 0) + 1
                parts = [f"{counts[k]} {k}" for k in ("modified", "new", "conflict") if counts.get(k)]
                self.show_message(f"{repo}   on {branch or '?'}   " + (", ".join(parts) or "clean"))
        elif sub == "changed":
            if result := self._git_status():
                repo, branch, files = result
                paths = gitstatus.changed_paths(repo, files)
                if not paths:
                    self.show_message("Nothing changed")
                    return
                self.remember_jump()
                kinds = {p: files[k] for k in files for p in [os.path.join(repo, *k.rstrip("/").split("/"))]}
                self.pane.show_results(Results(f"git changed ({branch})", repo, paths, kinds))
                self.update_status()
        elif sub == "diff":
            self.git_diff()
        else:
            self.show_message("Usage: :git [changed | diff]")

    def git_diff(self) -> bool:
        """The file under the cursor next to its last committed version."""
        entry = self.pane.current_entry()
        if entry is None or entry.is_parent or entry.is_dir:
            self.show_message("No file under the cursor")
            return False
        if not self.git_program or (repo := gitstatus.find_repo(entry.path)) is None:
            self.show_message("Not in a git repository")
            return False
        old = gitstatus.head_version(self.git_program, repo, entry.path)
        if old is None:
            self.show_message(f"{entry.name} isn't in the last commit")
            return False
        folder = tempfile.mkdtemp(prefix="vimcommander-git-")
        copy = os.path.join(folder, entry.name)
        with open(copy, "wb") as f:
            f.write(old)
        rel = os.path.relpath(entry.path, repo).replace(os.sep, "/")
        DiffDialog(copy, entry.path, self, self.cmdline.font(),
                   titles=(f"HEAD:{rel}   (last commit)", f"{entry.path}   (now)")).exec()
        return True

    # Linked panes

    def ex_linkpanes(self, arg):
        """:linkpanes: both panes go into the same subfolders, and what one side lacks is marked."""
        if self._local_only(":linkpanes"):
            return
        on = self.link is None if arg not in ("on", "off") else arg == "on"
        if on:
            if any(p.archive or p.results for p in self.panes):
                self.show_message("Link two folders, not archives or result lists")
                return
            self.link = (self.panes[0].path, self.panes[1].path)
            self.show_message("Panes linked: they go into the same subfolders; purple = only on this side. "
                              ":linkpanes again to unlink")
        else:
            self.link = None
            self.show_message("Panes unlinked")
        for pane in self.panes:
            pane.linked = on
            pane.update_header()
        self.mark_unique()
    ex_link = ex_linkpanes

    def follow_link(self, index: int, path: str) -> None:
        """The active pane opened a folder: open the same place on the other side."""
        if not self.link or self._linking or index != self.active:
            return
        try:
            rel = os.path.relpath(path, self.link[index])
        except ValueError:
            return
        target = os.path.normpath(os.path.join(self.link[1 - index], rel))
        other = self.panes[1 - index]
        if os.path.isdir(target):
            self._linking = True
            other.cd(target)
            self._linking = False
        else:
            self.show_message(f"Not on the other side: {rel}")
        self.mark_unique()

    def follow_link_cursor(self, index: int) -> None:
        if not self.link or self._linking or index != self.active:
            return
        entry = self.panes[index].current_entry()
        other = self.panes[1 - index]
        if entry and not entry.is_parent and (row := other.model.row_of(entry.name)) >= 0:
            self._linking = True
            other.set_cursor(row)
            self._linking = False

    def mark_unique(self) -> None:
        """With linked panes, color the items that only one side has."""
        names = [{os.path.normcase(e.name) for e in p.model.entries if not e.is_parent} for p in self.panes]
        for i, pane in enumerate(self.panes):
            pane.model.unique = names[i] - names[1 - i] if self.link else set()
            pane.model.refresh_colors()

    # Folder rules

    def ex_rules(self, arg):
        """:rules shows what the folder rules in the config would do, then does what you keep."""
        rule_list, errors = rules.parse_rules(self.settings.get("rules"))
        if errors:
            self.show_message("Rules: " + "; ".join(errors))
            if not rule_list:
                return
        if not rule_list:
            self.show_message('No rules yet: add them to "rules" in the config (:config); see the README')
            return
        steps = rules.plan(rule_list)
        if not steps:
            self.show_message(f"{len(rule_list)} rule(s): nothing to do now")
            return
        dialog = RulesDialog(steps, self)
        if not dialog.exec() or not dialog.chosen:
            return
        for action, to, paths in group_steps(dialog.chosen):
            if action == "trash":
                self.start_job("trash", paths)
            else:
                try:
                    os.makedirs(to, exist_ok=True)
                except OSError as e:
                    self.show_message(f"Can't make {to}: {e.strerror or e}")
                    continue
                self.start_job(action, paths, to)

    # Sessions

    def ex_mksession(self, arg):
        """:mksession NAME saves both panes' tabs (result lists too) and the shelf."""
        name = arg.strip()
        if not name:
            self.show_message("Usage: :mksession NAME")
            return
        sessions = dict(self.settings.get("sessions") or {})
        sessions[name] = {"panes": [p.session_state() for p in self.panes], "active": self.active,
                          "shelf": self.shelf.paths, "link": list(self.link) if self.link else None}
        self.save_settings({"sessions": sessions})
        self.show_message(f"Session {name} saved   (:session {name} opens it)")
    ex_mks = ex_mksession

    def ex_session(self, arg):
        """:session NAME opens a saved session; :session alone lists them."""
        sessions = self.settings.get("sessions") or {}
        name = arg.strip()
        if not name:
            self.show_message("Sessions: " + (", ".join(sorted(sessions)) or "none yet (:mksession NAME)"))
            return
        state = sessions.get(name)
        if not isinstance(state, dict):
            self.show_message(f"No session {name}")
            return
        self.leave_visual()
        if self.link:
            self.ex_linkpanes("off")
        for pane, pane_state in zip(self.panes, state.get("panes") or []):
            if isinstance(pane_state, dict):
                pane.load_session(pane_state)
        if isinstance(state.get("shelf"), list):
            self.shelf.set_paths([p for p in state["shelf"] if isinstance(p, str) and os.path.lexists(p)])
        self.activate(state.get("active") if state.get("active") in (0, 1) else 0)
        link = state.get("link")
        if isinstance(link, list) and len(link) == 2 and all(p.path == f for p, f in zip(self.panes, link)):
            self.ex_linkpanes("on")
        self.save_settings()
        self.show_message(f"Session {name}")

    def ex_delsession(self, arg):
        sessions = dict(self.settings.get("sessions") or {})
        if sessions.pop(arg.strip(), None) is None:
            self.show_message(f"No session {arg.strip()}")
            return
        self.save_settings({"sessions": sessions})
        self.show_message(f"Session {arg.strip()} deleted")

    # Thumbnail grid

    @staticmethod
    def _thumb_size(settings: dict) -> int:
        try:
            return min(320, max(48, int(settings.get("thumbnail_size", 128))))
        except (TypeError, ValueError):
            return 128

    def cmd_grid(self, a):
        """zi: thumbnails / list for this pane."""
        self.ex_grid("")

    def ex_grid(self, arg):
        """:grid toggles; :grid on|off; :grid SIZE sets the thumbnail size (48-320)."""
        pane = self.pane
        if arg.isdigit():
            self.set_thumb_size(int(arg))
            if not pane.grid_mode:
                pane.set_grid(True)
        elif arg in ("on", "off", ""):
            pane.set_grid(not pane.grid_mode if not arg else arg == "on")
        else:
            self.show_message("Usage: :grid [on|off|SIZE]")
            return
        self.show_message("Thumbnails: w / b = next / previous, j / k = a row down / up, Ctrl+wheel = size"
                          if pane.grid_mode else "List view")
        self.save_settings()

    def set_thumb_size(self, size: int) -> None:
        size = min(320, max(48, size))
        for pane in self.panes:
            pane.grid.set_thumb_size(size)
            pane.list_view.scrollTo(pane.model.index(pane.row, 0))
        self._ratio_timer.start()  # saves the settings soon

    def zoom_grid(self, step: int) -> None:
        size = self.panes[0].grid.thumb_size
        self.set_thumb_size(int(size * (1.15 if step > 0 else 1 / 1.15)))

    # Terminal

    def cmd_terminal(self, a):
        """Ctrl-`: show the terminal and go to it; from the terminal (or again) hide it."""
        t = self.terminal
        if t.isVisible() and t.input.hasFocus():
            t.hide()
            self.pane.view.setFocus()
        else:
            t.show()
            t.input.setFocus()

    def ex_terminal(self, arg):
        """:terminal [COMMAND] shows the terminal; with a command, runs it there."""
        self.terminal.show()
        self.terminal.input.setFocus()
        if arg:
            self.terminal.run(arg)
    ex_term = ex_terminal

    # Terminal pane (:vterm)

    def cmd_vterm(self, a):
        """Ctrl-w t: a terminal in the other pane (or back into it), in this pane's folder."""
        self.ex_vterm("")

    def ex_vterm(self, arg):
        """:vterm [SHELL] opens a terminal in the other pane: SHELL is wsl, wsl:NAME,
        pwsh, cmd, bash or a command line; :vterm off closes it."""
        arg = arg.strip()
        target = self.other
        self.show_both_panes()
        if arg == "off":
            if target.term:
                target.close_terminal()
                self.show_message("Terminal closed")
            self.pane.view.setFocus()
            return
        if target.term:
            if not target.shows_terminal:  # the quick view covered it
                self.quickview = False
                target.show_preview(False)
            target.term.setFocus()
            return
        try:
            from .termview import TermView
        except ImportError:
            self.show_message("The terminal pane needs pyte: pip install pyte"
                              + (" pywinpty" if WINDOWS else ""))
            return
        pane = self.pane
        folder = pane.path
        if pane.remote or pane.archive:
            folder = os.path.dirname(pane.archive[0]) if pane.archive else os.path.expanduser("~")
        s = self.settings
        spec = arg or (s.get("vterm") or {}).get("shell") or s["programs"].get("shell", "")
        if self.quickview:
            self.quickview = False
            for p in self.panes:
                p.show_preview(False)
        term = TermView(spec, folder, target)
        term.set_font(getattr(self, "text_font", term.term_font))
        term.follow = bool((s.get("vterm") or {}).get("follow", True))
        term.cwd_changed.connect(lambda path, t=target: self.on_vterm_cd(t, path))
        term.leave.connect(lambda: self.pane.view.setFocus())
        term.exited.connect(lambda t=target: self.on_vterm_exit(t))
        target.show_terminal(term)
        if not target.remote and not target.archive and not target.results:
            target.cd(folder, record=False)
        term.start()
        term.setFocus()
        self.show_message(f"{term.shell.name} in the {'left' if target is self.panes[0] else 'right'} pane   "
                          "(Ctrl-w h/l/w = back to the files, exit = close)")

    def follow_vterm(self, i: int, path: str) -> None:
        """Pane i went to `path`: a terminal in the other pane cds there too."""
        other = self.panes[1 - i]
        pane = self.panes[i]
        if other.term and not pane.remote and not pane.archive and not pane.shows_terminal:
            other.term.cd(path)

    def on_vterm_cd(self, target: Pane, path: str) -> None:
        """A cd in the terminal pane: its own (hidden) list goes there, so F5 copies
        into the shell's folder; with follow on, the file pane goes there too."""
        if not os.path.isdir(path):
            return
        target.cd(path, record=False)
        files = self.panes[1 - self.panes.index(target)]
        if target.term and target.term.follow and not files.remote and \
                os.path.normcase(files.path) != os.path.normcase(path):
            files.cd(path)

    def on_vterm_exit(self, target: Pane) -> None:
        had_focus = target.term is not None and target.term.hasFocus()
        target.close_terminal()
        self.show_message("The terminal ended")
        if had_focus or self.pane is target:
            self.pane.view.setFocus()

    def ex_mount(self, arg):
        """:mount mounts this SSH folder with sshfs in the terminal pane (WSL on Windows)."""
        self._sshfs(mount=True)

    def ex_umount(self, arg):
        """:umount unmounts it again."""
        self._sshfs(mount=False)

    def _sshfs(self, mount: bool) -> None:
        loc = remote.parse(self.pane.path)
        if loc is None or loc.is_ftp:
            self.show_message(":mount works in an SSH folder (:ssh): it mounts it with sshfs "
                              "in a Linux terminal pane (WSL on Windows)")
            return
        if self.other.term is None:
            self.ex_vterm("wsl" if WINDOWS else "")
        term = self.other.term
        if term is None:
            return
        if term.shell.kind not in ("wsl", "posix"):
            self.show_message("sshfs needs a Linux shell: close this terminal (:vterm off), then :vterm wsl")
            return
        term.send_line(termshell.mount_line(loc) if mount else termshell.umount_line(loc))
        term.setFocus()  # sshfs may ask for the password there
        if mount:
            self.show_message(f"Mounting {loc.key} at {termshell.mount_point(loc)} "
                              "(needs sshfs in Linux: sudo apt install sshfs)")

    def on_terminal_shown(self, visible: bool) -> None:
        if visible and not self.terminal.running:
            folder = self.pane.path if not (self.pane.archive or self.pane.results) else os.path.expanduser("~")
            self.terminal.start(self.settings["programs"].get("shell", ""), folder)

    def on_terminal_cd(self, path: str) -> None:
        """A cd in the terminal: the active pane goes there too."""
        pane = self.pane
        if os.path.normcase(path) != os.path.normcase(pane.path) and (
                remote.is_remote(path) or os.path.isdir(path)):
            pane.cd(path)
            self.update_status()

    # Music player

    def _player(self):
        """The player, made on first use; None (with a message) without QtMultimedia."""
        if self.player is None:
            try:
                from .player import Player, PlayerBar, PlaylistView
            except ImportError:
                self.show_message("The music player needs QtMultimedia: pip install PySide6-Addons")
                return None
            self.player = Player(self, self.settings.get("player") or {})
            self.player.message.connect(self.show_message)
            self.player.options_changed.connect(self._player_save_timer.start)
            self.player_bar = PlayerBar(self.player, self)
            self.player_bar.playlist_requested.connect(lambda: self.cmd_playlist(None))
            self.player_bar.close_requested.connect(lambda: self.ex_play("off"))
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.player_bar)
            self.playlist_view = PlaylistView(self.player)
            self.playlist_view.back.connect(self._leave_playlist)
            self.playlist_view.close_requested.connect(self.close_playlist)
            self.playlist_view.goto.connect(self._playlist_goto)
            self.playlist_view.command_line.connect(lambda: self.show_cmdline(":"))
        return self.player

    def _play_targets(self) -> tuple[list[str], int] | None:
        """What gm plays: (tracks, the one to start with), or None."""
        pane = self.pane
        tagged = pane.selected_entries()
        entry = pane.current_entry()
        if tagged:
            return music.collect([e.path for e in tagged]), 0
        if pane.results:  # the list's audio files, from the cursor's on
            tracks = [e.path for e in pane.model.entries if not e.is_dir and music.is_audio(e.path)]
            start = tracks.index(entry.path) if entry and entry.path in tracks else 0
            return tracks, start
        if entry is None or entry.is_parent:
            return music.collect([pane.path]), 0
        if music.is_audio(entry.path):  # its folder, from this file on
            tracks = music.folder_tracks(os.path.dirname(entry.path))
            return tracks, tracks.index(entry.path) if entry.path in tracks else 0
        return music.collect([entry.path]), 0

    def cmd_play(self, a):
        """gm: play the tagged items, the folder or .m3u under the cursor, or the
        audio file under the cursor with the rest of its folder."""
        if self._local_only("The player") or self._read_only_archive("The player"):
            return
        found = self._play_targets()
        tracks, start = found if found else ([], 0)
        if not tracks:
            self.show_message("No music here (mp3, flac, ogg, opus, m4a, wav… or an .m3u)")
            return
        self.leave_visual()
        self.pane.clear_tags()
        self._start_playing(tracks, start)

    def _start_playing(self, tracks: list[str], start: int = 0) -> None:
        player = self._player()
        if player is None:
            return
        player.load(tracks, start)
        self.player_bar.show()
        self.show_message(f"Playing {len(tracks)} track(s)   (gM = pause, ]m / [m = next / previous, "
                          "Ctrl-w m = the playlist, :play off = stop)")

    def _read_only_archive(self, what: str) -> bool:
        if self.pane.archive:
            self.show_message(f"{what}: unpack the files first (Alt-F9)")
            return True
        return False

    def _with_player(self) -> "object | None":
        """The player if something is loaded, else a hint and None."""
        if self.player is None or not self.player.tracks:
            self.show_message("Nothing is playing: gm plays the folder or the files under the cursor")
            return None
        return self.player

    def cmd_play_pause(self, a):
        """gM: pause / resume."""
        if player := self._with_player():
            player.toggle()
            self.player_bar.show()

    def cmd_play_next(self, a):
        """]m: the next track (3]m = three on)."""
        if player := self._with_player():
            player.step(a.n())

    def cmd_play_prev(self, a):
        """[m: the start of this track, or the previous one."""
        if player := self._with_player():
            if a.n() > 1:
                player.step(-a.n())
            else:
                player.previous()

    def cmd_play_stop(self, a):
        if self.player is not None:
            self.player.stop()

    def cmd_playlist(self, a):
        """Ctrl-w m: the playlist in the other pane (or back into it); again from it = close."""
        if self._with_player() is None:
            return
        view = self.playlist_view
        for pane in self.panes:
            if pane.playlist is view:
                if pane.shows_playlist:
                    if view.hasFocus() or view.list.hasFocus():
                        self.close_playlist()
                    else:
                        view.setFocus()
                    return
                pane.close_playlist()  # covered by the quick view or a terminal: move it here
        target = self.other
        if target.shows_terminal:
            self.show_message("The other pane is a terminal   (:vterm off closes it)")
            return
        self.show_both_panes()
        if self.quickview:
            self.quickview = False
            for p in self.panes:
                p.show_preview(False)
        target.show_playlist(view)
        view.setFocus()

    def close_playlist(self) -> None:
        for pane in self.panes:
            if pane.playlist is not None:
                had_focus = pane.playlist.list.hasFocus()
                pane.close_playlist()
                if had_focus:
                    self.pane.view.setFocus()

    def _leave_playlist(self) -> None:
        self.pane.view.setFocus()
        self.update_status()

    def _playlist_goto(self, path: str) -> None:
        self._leave_playlist()
        self.goto_path(path)

    def _save_player(self) -> None:
        if self.player is not None:
            self.save_settings({"player": self.player.options()})

    def ex_play(self, arg):
        """:play [PATH | pause | next [N] | prev | stop | off | list | shuffle [on|off] |
        repeat [all|one|off] | volume N | speed RATE | seek [+|-]SECONDS | save NAME]"""
        word, _, rest = arg.strip().partition(" ")
        rest = rest.strip()
        if not word:
            self.cmd_play(Action("play", None))
            return
        if word not in ("pause", "next", "prev", "previous", "stop", "off", "list", "shuffle",
                        "repeat", "volume", "seek", "speed", "save"):
            path = self._arg_path(arg.strip())
            tracks = music.collect([path]) if os.path.exists(path) else []
            if not tracks:
                self.show_message(f"No music in {path}")
            else:
                self._start_playing(tracks)
            return
        if word == "off":
            if self.player is not None:
                self.player.clear()
                self.close_playlist()
                self.player_bar.hide()
            return
        settings = word in ("shuffle", "repeat", "volume", "speed")  # these work before playing too
        player = self._player() if settings else self._with_player()
        if player is None:
            return
        if word == "pause":
            player.toggle()
        elif word == "next":
            player.step(int(rest) if rest.isdigit() else 1)
        elif word in ("prev", "previous"):
            player.previous()
        elif word == "stop":
            player.stop()
        elif word == "list":
            self.cmd_playlist(None)
        elif word == "shuffle":
            player.set_shuffle(rest == "on" if rest in ("on", "off") else not player.shuffle)
            self.show_message(f"Shuffle {'on' if player.shuffle else 'off'}")
        elif word == "repeat":
            if rest:
                player.set_repeat(rest)
            else:
                player.cycle_repeat()
            self.show_message(f"Repeat {player.repeat}")
        elif word == "volume":
            m = re.fullmatch(r"([+-]?)(\d+)", rest)
            if not m:
                self.show_message(f"Volume {player.volume}   (:play volume 0-100, +10, -10)")
                return
            n = int(m.group(2))
            player.set_volume(player.volume + n if m.group(1) == "+" else
                              player.volume - n if m.group(1) == "-" else n)
            self.show_message(f"Volume {player.volume}")
        elif word == "speed":
            if rest in ("+", "-"):
                player.faster(1 if rest == "+" else -1)
            elif rest:
                rate = music.parse_speed(rest, player.speed)
                if rate is None:
                    self.show_message("Usage: :play speed 1.5, 150%, +0.25, -0.25, + or - (0.25-4)")
                    return
                player.set_speed(rate)
            self.show_message(f"Speed {music.format_speed(player.speed)}")
        elif word == "seek":
            m = re.fullmatch(r"([+-]?)(\d+(?:\.\d+)?)", rest)
            if not m:
                self.show_message("Usage: :play seek 90, +10 or -10 (seconds)")
                return
            ms = int(float(m.group(2)) * 1000)
            player.seek(player.position + ms if m.group(1) == "+" else
                        player.position - ms if m.group(1) == "-" else ms)
        elif word == "save":
            if self._local_only("Saving a playlist") or self._read_only(self.pane, into=True):
                return
            if not rest:
                self.show_message("Usage: :play save NAME   (writes NAME.m3u8 here)")
                return
            name = rest if music.is_playlist(rest) else rest + ".m3u8"
            path = os.path.join(self.pane.path, name)
            try:
                music.write_m3u(path, player.tracks)
            except OSError as e:
                self.show_message(f"Could not save the playlist: {e}")
                return
            self.pane.cd(self.pane.path, select=os.path.basename(path), record=False)
            self.show_message(f"{len(player.tracks)} track(s) saved in {name}")

    # Share

    def ex_share(self, arg):
        """:share sends the tagged items (or the cursor's) to a phone by QR code;
        the phone can send files back into this folder. :share stop ends it."""
        if self._local_only(":share"):
            return
        if self.share_dialog is not None:
            self.share_dialog.close()
            self.share_dialog = None
            if arg == "stop":
                self.show_message("Sharing stopped")
                return
        if arg == "stop":
            self.show_message("Nothing is shared")
            return
        options = self.settings.get("share") or {}
        entry = self.pane.current_entry()
        items = [] if (entry is None or entry.is_parent) and not self.pane.selected_entries() \
            else [p for p in self._take_targets(1) if not archive.inside(p)]
        upload = None
        if options.get("upload", True) and not self.pane.archive and not self.pane.results:
            upload = self.pane.path
        if not items and not upload:
            self.show_message("Nothing to share")
            return
        try:
            port = int(options.get("port", 0))
            minutes = max(1, int(options.get("minutes", 15)))
            from .sharedialog import ShareDialog  # loads http.server: not at startup
            dialog = ShareDialog(items, upload, port, minutes, self)
        except (OSError, ValueError) as e:
            self.show_message(f"Can't share: {e}")
            return
        dialog.destroyed.connect(lambda _=None: setattr(self, "share_dialog", None))
        dialog.received.connect(lambda: [pane.reload() for pane in self.panes])
        self.share_dialog = dialog
        dialog.show()

    # Side-by-side diff

    def cmd_diff(self, a): self.ex_diffthis("")

    def ex_diffthis(self, arg):
        """:diffthis [FILE] (gd): two tagged files, or the files under the cursors of
        both panes, or the cursor's file and FILE, side by side."""
        tagged = [e for e in self.pane.selected_entries() if not e.is_dir]
        if arg:
            entry = self.pane.current_entry()
            if entry is None or entry.is_parent or entry.is_dir:
                self.show_message("No file under the cursor")
                return
            pair = [entry.path, self._arg_path(arg)]
        elif len(tagged) == 2:
            pair = [tagged[0].path, tagged[1].path]
        else:
            ends = [p.current_entry() for p in self.panes]
            if any(e is None or e.is_parent or e.is_dir for e in ends):
                entry = self.pane.current_entry()
                if (entry and not entry.is_parent and not entry.is_dir
                        and self.pane.model.git.get(entry.name) == "modified"):
                    self.git_diff()  # a changed file in git: compare with the last commit
                    return
                self.show_message("Put the cursors on a file in each pane, or tag two files")
                return
            pair = [e.path for e in ends]
        files = [self._local_copy(p) for p in pair]
        if None in files:
            return
        if not all(os.path.isfile(f) for f in files):
            self.show_message(f"Not a file: {next(f for f in files if not os.path.isfile(f))}")
            return
        self.leave_visual()
        DiffDialog(files[0], files[1], self, self.cmdline.font()).exec()

    # Activity

    def cmd_activity(self, a):
        if self._local_only("Activity"):
            return
        if self.pane.archive:
            self.show_message("No activity view inside archives")
            return
        dialog = ActivityDialog(self.pane.path, self.pane.model.show_hidden, self)
        if dialog.exec() and dialog.result_list:
            self.remember_jump()
            self.show_result_list(dialog.result_list[0], self.pane.path, dialog.result_list[1])
        elif dialog.result() and dialog.chosen:
            self.remember_jump()
            self.pane.cd(os.path.dirname(dialog.chosen), select=os.path.basename(dialog.chosen))
            self.update_status()

    def ex_activity(self, arg): self.cmd_activity(None)

    # Graph

    def on_graph_open(self, node, activate: bool) -> None:
        """A click on a graph node: open it in the active pane. A file group opens
        its folder filtered to that extension; a double-clicked note opens."""
        pane = self.pane
        if node.kind in ("note", "code"):
            pane.cd(os.path.dirname(node.path), select=os.path.basename(node.path))
            if activate:
                self.run_action("open")
        elif node.kind == "files":
            pane.cd(node.path)
            if node.ext:
                self.set_filter("*" + node.ext)
                self._filter_message()
        else:
            pane.cd(node.path)
        pane.view.setFocus()
        self.update_status()

    def on_note_link(self, path: str) -> None:
        """A click on a link in a previewed note: go to that file in the active pane."""
        self.pane.cd(os.path.dirname(path), select=os.path.basename(path))
        self.pane.view.setFocus()
        self.update_status()

    def ex_obsidian(self, arg):
        """:obsidian: open the note (or folder) under the cursor in Obsidian."""
        if self._local_only("Obsidian"):
            return
        entry = self.pane.current_entry()
        path = entry.path if entry and not entry.is_parent else self.pane.path
        if archive.locate(path):
            self.show_message("Obsidian can't open files inside an archive")
            return
        url = QUrl("obsidian://open")
        url.setQuery("path=" + QUrl.toPercentEncoding(os.path.abspath(path)).data().decode())
        if not QDesktopServices.openUrl(url):
            self.show_message("Cannot open Obsidian (is it installed?)")

    def save_graph_options(self) -> None:
        self.save_settings({"graph": dict(graphview.OPTIONS)})
        for pane in self.panes:
            if pane.preview.graph.isVisible():
                pane.preview.graph.reload()

    def cmd_graph_window(self, a):
        if not self._local_only("The graph"):
            self.ex_graph("")

    def ex_graph(self, arg):
        """:graph opens a graph window; :graph 1|2|3, size|count, tree|sunburst|treemap|timeline|types|notes|code, on|off set options."""
        if arg:
            words = arg.split()
            for word in words:
                if word in ("1", "2", "3"):
                    graphview.OPTIONS["depth"] = int(word)
                elif word in ("size", "count", "files"):
                    graphview.OPTIONS["size_by"] = "count" if word == "files" else word
                elif word in graphview.MODES:
                    graphview.OPTIONS["mode"] = word
                elif word in ("on", "off"):
                    graphview.OPTIONS["enabled"] = word == "on"
                else:
                    self.show_message("Usage: :graph [1|2|3] [size|count] [tree|sunburst|treemap|timeline|types|notes|code] [on|off]")
                    return
            self.save_graph_options()
            for pane in self.panes:
                pane.preview.current = None  # show again with the new options
            self.update_quickview()
            self.show_message("Graph: " + ", ".join(f"{k}={v}" for k, v in graphview.OPTIONS.items()))
            return
        entry = self.pane.current_entry()
        folder = entry.path if entry and entry.is_dir and not entry.is_parent else self.pane.path
        if archive.locate(folder):
            self.show_message("No graph inside archives")
            return
        window = QWidget(self, Qt.WindowType.Window)
        window.setWindowTitle(f"Graph - {folder}")
        fit_window(window, 1000, 720)
        view = graphview.GraphView(window)
        layout = QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)
        view.open_requested.connect(self.on_graph_open)
        view.options_changed.connect(self.save_graph_options)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(lambda _=None, w=window: self.graph_windows.remove(w)
                                 if w in self.graph_windows else None)
        self.graph_windows.append(window)
        window.show()
        view.show_folder(folder, self.pane.model.show_hidden, DIR_SIZES)

    # Tabs and drives

    def cmd_new_tab(self, a): self.pane.new_tab()

    def cmd_open_in_tab(self, a):
        entry = self.pane.current_entry()
        self.pane.new_tab(entry.path if entry and entry.is_dir else None)

    def cmd_next_tab(self, a):
        pane = self.pane
        pane.switch_tab(a.count - 1 if a.count else (pane.tab + 1) % len(pane.tabs))

    def cmd_prev_tab(self, a): self.pane.switch_tab((self.pane.tab - a.n()) % len(self.pane.tabs))

    def ex_tabnew(self, arg): self.pane.new_tab(self._arg_path(arg) if arg else None)

    def ex_tabclose(self, arg):
        if not self.pane.close_tab():
            self.show_message("This is the only tab")
    ex_tabc = ex_tabclose

    def cmd_drives(self, a):
        index = int(a.arg) - 1 if a.arg else self.active
        self.panes[index].show_drive_menu()

    def ex_drives(self, arg): self.cmd_drives(Action("drives", None))

    # Context menu

    def cmd_context_menu(self, a):
        self.show_context_menu(self.active, self.pane.row, None)

    def _cursor_point(self) -> QPoint:
        view = self.pane.list_view
        rect = view.visualRect(self.pane.model.index(self.pane.row, 0))
        return view.viewport().mapToGlobal(rect.bottomLeft() + QPoint(24, 0))

    def show_context_menu(self, index: int, row: int, pos: QPoint | None) -> None:
        """Right-click (pos = mouse position) or the menu key (pos = None)."""
        if index != self.active:
            self.leave_visual()
            self.activate(index)
        pane = self.pane
        entries = pane.model.entries
        background = not 0 <= row < len(entries) or entries[row].is_parent
        if not background:
            if entries[row].path not in {e.path for e in pane.selected_entries()}:
                self.leave_visual()
                pane.clear_tags()  # like Explorer: the clicked item, not the tagged ones
            pane.set_cursor(row)
        self.update_status()
        mods = QApplication.keyboardModifiers()
        if WINDOWS and not pane.archive and not pane.remote and not mods & Qt.KeyboardModifier.ControlModifier:
            if self._shell_menu(background, pos is not None,
                                bool(mods & Qt.KeyboardModifier.ShiftModifier)):
                return
        self._own_menu(background).exec(pos or self._cursor_point())

    def _menu_items(self, background: bool) -> list[tuple[str, str, bool] | None]:
        """(label, action, enabled) for the context menu; None = separator."""
        ro = self.pane.archive is not None
        entry = self.pane.current_entry()
        if background:
            return [
                ("Paste\tp", "paste", bool(self.register) and not ro),
                ("New folder\tF7", "mkdir", not ro), ("New file…", "new_file", not ro), None,
                ("Copy folder path\tyd", "yank_dir", True),
                ("Add up all folder sizes\tgS", "all_dir_sizes", not ro),
                ("Show / hide hidden files\tza", "toggle_hidden", True),
                ("New tab\tCtrl+T", "new_tab", True), ("Reload\tCtrl+L", "reload", True), None,
                ("Terminal here\tS", "shell", True),
            ]
        is_dir = bool(entry and entry.is_dir)
        items = [("Open\tEnter", "open", True)]
        items += [("Open in new tab", "open_in_tab", True)] if is_dir else [
            ("View\tF3", "view", True), ("Edit\tF4", "edit", True)]
        items += [
            ("Quick view\tCtrl+Q", "quickview", True), None,
            ("Copy\tyy", "yank", True), ("Cut\tdd", "cut", not ro),
            ("Paste\tp", "paste", bool(self.register) and not ro),
            ("Copy to other pane\tF5", "copy_other", True),
            ("Move to other pane\tF6", "move_other", not ro), None,
            ("Rename\tcw", "rename", not ro), ("Move to trash\tDel", "trash", not ro),
            ("Delete permanently\tShift+Del", "delete", not ro), None,
            ("Copy path\tyf", "yank_path", True), ("Copy name\tyn", "yank_name", True),
            ("Put on the shelf\tzs", "shelf_add", not ro),
        ]
        if is_dir:
            items.append(("Add up folder size\tgs", "dir_size", not ro))
        if entry and (is_dir or music.is_audio(entry.path) or music.is_playlist(entry.path)) \
                and not ro and not self.pane.remote:
            items.append(("Play\tgm", "play", True))
        return items + [None, ("Terminal here\tS", "shell", True)]

    def _own_menu(self, background: bool) -> QMenu:
        menu = QMenu(self)
        for item in self._menu_items(background):
            if item is None:
                menu.addSeparator()
            else:
                label, action, enabled = item
                menu.addAction(label, lambda action=action: self.run_action(action)).setEnabled(enabled)
        return menu

    def _shell_menu(self, background: bool, from_mouse: bool, extended: bool) -> bool:
        """Show the Windows Explorer menu. Returns False if the shell had none."""
        pane = self.pane
        paths = [pane.path] if background else [e.path for e in pane.targets()]
        if not paths:
            return False
        extras = [("Terminal here\tS", "shell"), ("Copy folder path\tyd", "yank_dir"),
                  ("Add up all folder sizes\tgS", "all_dir_sizes")] if background else [
            ("Edit with configured editor\tF4", "edit"), ("Quick view\tCtrl+Q", "quickview"),
            ("Copy path\tyf", "yank_path"), ("Add up folder size\tgs", "dir_size"),
            ("Put on the shelf\tzs", "shelf_add"), ("Terminal here\tS", "shell")]
        hwnd = int(self.winId())
        native_pos = None
        if not from_mouse:
            p = self.mapFromGlobal(self._cursor_point())
            ratio = self.devicePixelRatioF()
            native_pos = winshell.client_to_screen(hwnd, int(p.x() * ratio), int(p.y() * ratio))
        try:
            result = winshell.show_menu(hwnd, paths, background, native_pos, extended,
                                        [label for label, _ in extras])
        except OSError as e:
            self.show_message(f"Explorer menu not available ({e})")
            return False
        if result:
            kind, value = result
            if kind == "extra":
                self.run_action(extras[value][1])
            elif value == "rename":
                self.run_action("rename")
            elif value == "delete":
                self.run_action("trash")
        return True

    def nativeEvent(self, event_type, message):
        if WINDOWS and winshell.menu_active():
            handled, result = winshell.handle_message(int(message))
            if handled:
                return True, result
        return super().nativeEvent(event_type, message)

    # Search

    def cmd_search(self, a):
        self.search_origin = self.pane.row
        self.show_cmdline("/", "")

    def cmd_search_back(self, a):
        self.search_origin = self.pane.row
        self.show_cmdline("?", "")

    def search_regex(self, text: str) -> re.Pattern:
        return pattern_regex(text, bool(self.settings.get("ignorecase", True)),
                             bool(self.settings.get("smartcase", True)))

    def find(self, text: str, start: int, step: int, wrap: bool = True) -> int:
        """Row of the first name matching `text` from `start` in direction `step`, or -1."""
        entries = self.pane.model.entries
        regex = self.search_regex(text)
        for i in range(len(entries)):
            row = start + i * step
            if not 0 <= row < len(entries):
                if not wrap:
                    return -1
                row %= len(entries)
            if not entries[row].is_parent and regex.search(entries[row].name):
                return row
        return -1

    def _search_step(self, step: int, count: int = 1) -> None:
        if not self.last_search:
            self.show_message("No previous search")
            return
        wrap = bool(self.settings.get("wrapscan", True))
        row = self.pane.row
        for _ in range(count):
            found = self.find(self.last_search, row + step, step, wrap)
            if found < 0:
                edge = "BOTTOM" if step > 0 else "TOP"
                self.show_message(f"Search hit {edge} without match for: {self.last_search}"
                                  if not wrap else f"Pattern not found: {self.last_search}")
                return
            if (found < row) if step > 0 else (found > row):
                self.show_message("Search hit BOTTOM, continuing at TOP" if step > 0
                                  else "Search hit TOP, continuing at BOTTOM")
            row = found
        self.pane.set_cursor(row)
        self.set_highlight(True)

    def cmd_search_next(self, a): self._search_step(self.search_dir, a.n())
    def cmd_search_prev(self, a): self._search_step(-self.search_dir, a.n())

    def _search_like(self, step: int, count: int) -> None:
        """* and #: the next / previous name with the same extension (or, for a
        name without one, starting with the same word)."""
        entry = self.pane.current_entry()
        if entry is None or entry.is_parent:
            return
        ext = os.path.splitext(entry.name)[1] if not entry.is_dir else ""
        if ext:
            self.last_search = r"(?i)" + re.escape(ext) + "$"
        else:
            word = re.match(r"[^\W_]+", entry.name)
            self.last_search = r"(?i)^" + re.escape(word.group() if word else entry.name)
        self.search_dir = step
        self._search_step(step, count)

    def cmd_search_like(self, a): self._search_like(1, a.n())
    def cmd_search_like_back(self, a): self._search_like(-1, a.n())

    def set_highlight(self, active: bool, text: str | None = None) -> None:
        """Highlight names matching the search in both panes (vim's hlsearch)."""
        self.hl_active = active
        text = self.last_search if text is None else text
        regex = (self.search_regex(text) if active and text and self.settings.get("hlsearch", True)
                 else None)
        for pane in self.panes:
            pane.model.set_highlight(regex)

    def ex_set(self, arg):
        """:set nu, :set nonu, :set nu!, :set invnu, :set nu?, several at once."""
        runtime = self.runtime_options()
        if not arg:
            self.show_message("  ".join(
                [("" if self.settings.get(key) else "no") + name for name, (key, _) in BOOL_OPTIONS.items()]
                + [f"{name}={self.settings.get(key, '')}" for name, (key, _, _) in STRING_OPTIONS.items()]
                + [("" if on else "no") + name for name, (on, _) in runtime.items()]))
            return
        changes, shown, live = {}, [], {}
        names = {alias: name for name, (_, short) in BOOL_OPTIONS.items() for alias in (name, *short)}
        names.update({alias: name for name, short in RUNTIME_OPTIONS.items() for alias in (name, *short)})
        strings = {alias: name for name, (_, short, _) in STRING_OPTIONS.items()
                   for alias in (name, *short)}
        for word in arg.split():
            option, eq, value = word.partition("=")
            if option.rstrip("?") in strings:
                name = strings[option.rstrip("?")]
                key, _, allowed = STRING_OPTIONS[name]
                if eq:
                    if value not in allowed:
                        self.show_message(f"{name} can be: " + ", ".join(a or '""' for a in allowed))
                        return
                    changes[key] = value
                else:
                    shown.append(f"{name}={changes.get(key, self.settings.get(key, ''))}")
                continue
            query, toggle = word.endswith("?"), word.endswith("!")
            word = word.rstrip("?!")
            value = True
            if word.startswith("inv") and word[3:] in names:
                word, toggle = word[3:], True
            elif word.startswith("no") and word[2:] in names:
                word, value = word[2:], False
            if word not in names:
                self.show_message(f"Unknown option: {word}")
                return
            name = names[word]
            if name in runtime:
                current = live.get(name, runtime[name][0])
                if query:
                    shown.append(("" if current else "no") + name)
                else:
                    live[name] = (not current) if toggle else value
                continue
            key = BOOL_OPTIONS[name][0]
            current = bool(changes.get(key, self.settings.get(key)))
            if query:
                shown.append(("" if current else "no") + name)
                continue
            changes[key] = (not current) if toggle else value
        if changes:
            self.settings.update(changes)
            self.apply_config()
            self.save_settings(changes)
        for name, on in live.items():
            if on != runtime[name][0]:
                runtime[name][1](on)
        if shown:
            self.show_message("  ".join(shown))

    def ex_nohlsearch(self, arg): self.set_highlight(False)
    ex_noh = ex_nohlsearch

    # Filter: = shows only matching names

    def cmd_filter(self, a):
        self.filter_origin = self.pane.model.filter_text
        self.show_cmdline("=", self.filter_origin)

    def set_filter(self, text: str) -> None:
        self.pane.set_filter(self.search_regex(text) if text else None, text)

    def ex_filter(self, arg):
        self.set_filter(arg)
        self._filter_message()

    def _filter_message(self) -> None:
        text = self.pane.model.filter_text
        shown = sum(1 for e in self.pane.model.entries if not e.is_parent)
        self.show_message(f"Filter {text}: {shown} item(s). = and Enter on an empty line clears it"
                          if text else "Filter cleared")

    # Repeat

    def cmd_repeat(self, a):
        if not self.last_change:
            self.show_message("Nothing to repeat")
            return
        self._repeating = True
        try:
            if self.last_change[0] == "action":
                _, name, count, arg, register = self.last_change
                self.run_action(name, a.count if a.count is not None else count, arg, register)
            else:
                self.execute(self.last_change[1])
        finally:
            self._repeating = False

    def cmd_at(self, a):
        if a.arg == ":":
            if self.last_ex:
                for _ in range(a.n()):
                    self.execute(self.last_ex)
            else:
                self.show_message("No previous command line")
        else:
            self.play_macro(a.arg or "", a.n())

    # Operators: y / d + motion

    def _motion_paths(self, a) -> list[str]:
        """Run the motion in a.arg and return the rows from the cursor to where it landed."""
        start = self.pane.row
        getattr(self, "cmd_" + a.arg)(Action(a.arg, a.count))
        lo, hi = sorted((start, self.pane.row))
        self.pane.set_cursor(lo)
        return [e.path for e in self.pane.model.entries[lo:hi + 1] if not e.is_parent]

    def cmd_yank_motion(self, a):
        if paths := self._motion_paths(a):
            self.store(a.register, "copy", paths)

    def cmd_cut_motion(self, a):
        if self._read_only():
            return
        if paths := self._motion_paths(a):
            self.store(a.register, "cut", paths)

    # Movement: groups, percent, scrolling, alternate folder

    @staticmethod
    def _group(e: Entry) -> tuple:
        if e.is_parent:
            return ("..",)
        return ("folder",) if e.is_dir else ("file", os.path.splitext(e.name)[1].lower())

    def cmd_group_next(self, a):
        """}: the first item of the next group (folders, then each extension)."""
        entries, row = self.pane.model.entries, self.pane.row
        for _ in range(a.n()):
            group = self._group(entries[row])
            while row < len(entries) - 1 and self._group(entries[row]) == group:
                row += 1
        self.pane.set_cursor(row)

    def cmd_group_prev(self, a):
        """{: the first item of this group, or of the previous one if already there."""
        entries, row = self.pane.model.entries, self.pane.row
        for _ in range(a.n()):
            if row > 0 and self._group(entries[row - 1]) != self._group(entries[row]):
                row -= 1
            group = self._group(entries[row])
            while row > 0 and self._group(entries[row - 1]) == group:
                row -= 1
        self.pane.set_cursor(row)

    def cmd_percent(self, a):
        if a.count is None:
            self.show_message("Give a count: 50% goes halfway down")
            return
        total = len(self.pane.model.entries)
        self.pane.set_cursor((min(a.count, 100) * total + 99) // 100 - 1)

    def _scroll_lines(self, lines: int) -> None:
        """Ctrl-e / Ctrl-y: scroll without moving the cursor, unless it would leave the view."""
        view = self.pane.list_view
        bar = view.verticalScrollBar()
        step = self.pane.grid.gridSize().height() if self.pane.grid_mode else 1
        bar.setValue(bar.value() + lines * step)
        top, bottom = self.pane.visible_range()
        if not self.pane.grid_mode and bottom < len(self.pane.model.entries) - 1:
            bottom -= 1
        row = self.pane.row
        if row < top or row > bottom:
            index = self.pane.model.index(min(max(row, top), max(bottom, top)), 0)
            view.selectionModel().setCurrentIndex(index, view.selectionModel().SelectionFlag.NoUpdate)

    def cmd_scroll_line_down(self, a): self._scroll_lines(a.n())
    def cmd_scroll_line_up(self, a): self._scroll_lines(-a.n())

    def cmd_alternate(self, a):
        """Ctrl-^ and :cd - : the previous folder of this pane."""
        previous = self.pane.prev_path
        if not previous:
            self.show_message("No previous folder")
            return
        self.pane.cd(previous, select=child_name(previous, self.pane.path))

    # Undo / redo

    def cmd_redo(self, a): self._undo_redo("redo", a.n())

    def ex_redo(self, arg): self._undo_redo("redo", 1)

    def ex_undolist(self, arg):
        def when(t: float) -> str:
            if not t:
                return ""
            day = datetime.fromtimestamp(t)
            return day.strftime("   (%H:%M)" if day.date() == datetime.now().date() else "   (%Y-%m-%d %H:%M)")
        lines = [f"undo {i}: {what}{when(t)}" for i, (what, _, t) in enumerate(reversed(self.undo_stack), 1)]
        lines += [f"redo {i}: {what}{when(t)}" for i, (what, _, t) in enumerate(reversed(self.redo_stack), 1)]
        if lines:
            QMessageBox.information(self, "Undo list", "\n".join(lines))
        else:
            self.show_message("Nothing to undo or redo")

    def ex_trash(self, arg):
        """:trash [WORDS]: what is in the system trash, to put back or delete for good."""
        found = trash.items()
        if not found:
            self.show_message("The trash is empty")
            return
        here = self.pane.path
        dialog = TrashDialog(found, arg, here, self)
        if not dialog.exec():
            return
        chosen = dialog.chosen
        if dialog.action == "purge":
            self.start_job("purge", [i.path for i in chosen])
            return
        if dialog.action == "restore_here":
            if self._local_only("Restoring from the trash") or self._read_only(into=True):
                return
            pairs, taken = [], set()
            for item in chosen:  # never over an existing file, nor two items onto one name
                stem, ext = (item.name, "") if item.is_dir else os.path.splitext(item.name)
                dest, n = os.path.join(here, item.name), 0
                while os.path.lexists(dest) or dest in taken:
                    n += 1
                    dest = os.path.join(here, f"{stem} ({n}){ext}")
                taken.add(dest)
                pairs.append((item.path, dest))
        else:
            if unknown := [i for i in chosen if not i.original]:
                self.show_message(f"The trash doesn't say where {unknown[0].name} was: "
                                  "Shift+Enter in :trash restores into the pane's folder")
                return
            pairs = [(i.path, i.original) for i in chosen]
        job = self.start_job("restore", pairs)
        if len(pairs) == 1:
            job.finished.connect(lambda *_, dest=pairs[0][1]: self._show_restored(dest))

    def _show_restored(self, path: str) -> None:
        if os.path.lexists(path) and not self.pane.remote:
            self.pane.cd(os.path.dirname(path), select=os.path.basename(path))

    # Macros

    def cmd_record(self, a):
        reg = a.arg or ""
        if reg in (":", "/", "?"):  # q: / q/: the command-line window
            self.history_window(":" if reg == ":" else "/")
            return
        if not (reg.isalnum() and reg.isascii()):
            self.show_message("Record into a letter or digit: qa … q")
            return
        self.recording = reg
        self._recorded = []
        QApplication.instance().installEventFilter(self._recorder)
        self.show_message("")

    def stop_recording(self) -> None:
        reg = self.recording
        if reg is None:
            return
        QApplication.instance().removeEventFilter(self._recorder)
        self.recording = None
        name = reg.lower()
        self.macros[name] = (self.macros.get(name, []) if reg.isupper() else []) + self._recorded
        self.show_message(f"Recorded @{name}: {len(self.macros[name])} keys")
        self.update_status()
        self.save_settings()

    def play_macro(self, reg: str, count: int) -> None:
        if reg == "@":
            if not self.last_macro:
                self.show_message("No previous macro")
                return
            reg = self.last_macro
        keys = self.macros.get(reg.lower())
        if not keys:
            self.show_message(f"Macro @{reg} is empty: record it with q{reg}")
            return
        self.last_macro = reg.lower()
        if not self._playback:
            self._played = 0
        self._playback.extend(keys * count)
        self._play_timer.start()

    def _play_step(self) -> None:
        """Send the next recorded key to whatever has focus. The timer is restarted
        before sending, so keys keep flowing into a dialog the macro opened."""
        if not self._playback:
            return
        self._played += 1
        if self._played > MACRO_LIMIT:
            self._playback.clear()
            self.show_message("Macro stopped: too many keys (does it call itself?)")
            return
        key, mods, text = self._playback.popleft()
        if self._playback:
            self._play_timer.start()
        target = QApplication.focusWidget() or self.pane.view
        modifiers = Qt.KeyboardModifier(mods)
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(target, QKeyEvent(kind, key, modifiers, text))

    # Small commands

    def cmd_quit(self, a): self.ex_q("")

    def cmd_info(self, a):
        """Ctrl-g: details of the item under the cursor."""
        entry = self.pane.current_entry()
        if entry is None:
            return
        n, total = self.pane.row + 1, len(self.pane.model.entries)
        if entry.is_parent:
            self.show_message(f"{self.pane.path}   {n}/{total}")
            return
        parts = [entry.path]
        if entry.is_dir:
            parts.append(human_size(DIR_SIZES[entry.path]) if entry.path in DIR_SIZES else "folder")
        else:
            parts.append(f"{human_size(entry.size)} ({entry.size:,} bytes)")
        if entry.mtime:
            parts.append(datetime.fromtimestamp(entry.mtime).strftime("modified %Y-%m-%d %H:%M:%S"))
        if not self.pane.archive:
            try:
                st = os.lstat(entry.path)
                parts.append(stat.filemode(st.st_mode))
                if stat.S_ISLNK(st.st_mode):
                    parts.append("→ " + os.readlink(entry.path))
            except OSError:
                pass
        parts.append(f"{n}/{total}")
        self.show_message("   ".join(parts))

    def cmd_open_system(self, a):
        """gx: open with the system's default app (a folder opens in the system file manager)."""
        entry = self.pane.current_entry()
        path = self.pane.path if entry is None or entry.is_parent else self._local_copy(entry.path)
        if entry is not None and not entry.is_dir:
            self.add_recent(entry.path)
        if path and not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.show_message(f"No application to open {os.path.basename(path)}")

    def ex_edit(self, arg):
        """:e FILE opens it in the editor; :e alone the file under the cursor."""
        if arg:
            path = self._arg_path(arg)
        else:
            entry = self.pane.current_entry()
            if entry is None or entry.is_parent or entry.is_dir:
                self.show_message("No file under the cursor")
                return
            self.add_recent(entry.path)
            path = self._local_copy(entry.path, edit=True)
            if path is None:
                return
        self.add_recent(path)
        if editor := self.settings["programs"].get("editor"):
            self._run(editor, [path], os.path.basename(path))
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.show_message(f"Can't open {path}: set programs.editor in the config file")
    ex_e = ex_edit

    def ex_tabs(self, arg):
        lines = []
        for side, pane in zip(("Left", "Right"), self.panes):
            lines.append(f"<b>{side} pane</b>")
            for i, path in enumerate(pane.tab_paths()):
                mark = "&gt;" if i == pane.tab else "&nbsp;&nbsp;"
                lines.append(f"&nbsp;{mark} {i + 1} &nbsp; {path}")
        QMessageBox.information(self, "Tabs", "<br>".join(lines))

    def ex_tabmove(self, arg):
        """:tabmove N puts the tab after tab N (0 = first); no N = last; +N / -N relative."""
        pane = self.pane
        last = len(pane.tabs) - 1
        try:
            if not arg:
                target = last
            elif arg[0] in "+-":
                target = pane.tab + int(arg)
            else:
                target = int(arg)
        except ValueError:
            self.show_message("Usage: :tabmove [N | +N | -N]")
            return
        pane.move_tab(max(0, min(target, last)))

    # Command line

    def cmd_command(self, a):
        """: in visual mode starts with the range '<,'> and 3: with .,.+2, as in vim."""
        if self.engine.mode == VISUAL:
            self.show_cmdline(":", "'<,'>")
        else:
            self.show_cmdline(":", f".,.+{a.count - 1}" if a.count and a.count > 1 else "")

    def cmd_help(self, a, topic: str = ""):
        from .helpview import HelpDialog
        HelpDialog(HELP, self, topic).exec()

    def show_cmdline(self, prompt: str, text: str) -> None:
        self.whichkey.hide()
        self.prompt_label.setText(prompt)
        self.message_label.hide()
        self.prompt_label.show()
        self.cmdline.show()
        self.cmdline.setText(text)
        self.cmdline.setFocus()

    def hide_cmdline(self) -> None:
        # Focus first: hiding the focused line would hand the focus to the next
        # widget (the left pane), which makes that pane the active one.
        self.pane.view.setFocus()
        self.cmdline.hide()
        self.prompt_label.hide()
        self.message_label.show()

    def complete_cmdline(self, backwards: bool) -> None:
        """Tab / Shift-Tab on the ':' line: cycle through command names or paths."""
        if self.prompt_label.text() != ":":
            return
        state = self._completion
        if state is None:
            matches, base = self.completions(self.cmdline.text())
            if not matches:
                QApplication.beep()
                return
            state = self._completion = {"base": base, "matches": matches, "i": -1}
        state["i"] = (state["i"] + (-1 if backwards else 1)) % len(state["matches"])
        self._completing = True
        self.cmdline.setText(state["base"] + state["matches"][state["i"]])
        self._completing = False

    def completions(self, text: str) -> tuple[list[str], str]:
        """(candidates, the text before them) for the ':' line."""
        if " " not in text:
            names = {m[3:] for m in dir(self) if m.startswith("ex_")}
            names |= set(self.settings.get("commands") or {})
            return sorted(n for n in names if n.startswith(text)), ""
        cmd, _, arg = text.partition(" ")
        if cmd == "ssh":
            return [h for h in remote.ssh_hosts() if h.startswith(arg)], "ssh "
        if cmd in ("session", "delsession", "mksession"):
            names = sorted(self.settings.get("sessions") or {})
            return [n for n in names if n.startswith(arg)], cmd + " "
        if cmd == "z" and self.zoxide:
            try:
                return [p for _, p in self.zoxide.list() if zoxide.matches(p, arg.split())], "z "
            except OSError:
                return [], ""
        if cmd not in PATH_COMMANDS:
            return [], ""
        base = cmd + " "
        if cmd == "bar":
            if not arg.startswith("add "):
                return [], ""
            base, arg = "bar add ", arg[4:]
        cut = max(arg.rfind("/"), arg.rfind("\\")) + 1
        folder_part, prefix = arg[:cut], arg[cut:].casefold()
        folder = self._arg_path(folder_part) if folder_part else self.pane.path
        try:
            loc = archive.locate(folder)
            items = ([(n, d) for n, d, _, _ in archive.list_dir(*loc)] if loc else
                     [(e.name, e.is_dir()) for e in os.scandir(folder)])
        except OSError:
            return [], ""
        dirs_only = cmd in ("cd", "tabnew")
        sep = "/" if "/" in folder_part or not WINDOWS else "\\"
        matches = sorted((name + sep if is_dir else name for name, is_dir in items
                          if name.casefold().startswith(prefix) and (is_dir or not dirs_only)
                          and (prefix.startswith(".") or not name.startswith("."))),
                         key=lambda n: (not n.endswith(sep), n.casefold()))
        return matches, base + folder_part

    def on_cmdline_changed(self, text: str) -> None:
        if not self._completing:
            self._completion = None
            self._history_state = None
        if not self.cmdline.isVisible():
            return
        prompt = self.prompt_label.text()
        if prompt in ("/", "?"):  # incremental search
            step = 1 if prompt == "/" else -1
            row = self.find(text, self.search_origin + step, step) if text else -1
            self.pane.set_cursor(row if row >= 0 else self.search_origin)
            self.set_highlight(bool(text), text)
        elif prompt == "=":  # live filter
            self.set_filter(text)

    def on_cmdline_cancel(self) -> None:
        prompt = self.prompt_label.text()
        if prompt in ("/", "?"):
            self.pane.set_cursor(self.search_origin)
            self.set_highlight(self.hl_active and bool(self.last_search))
        elif prompt == "=":
            self.set_filter(self.filter_origin or "")
        self.hide_cmdline()

    def on_cmdline_enter(self) -> None:
        prompt, text = self.prompt_label.text(), self.cmdline.text().strip()
        self.hide_cmdline()
        if text:
            items = self.history["/" if prompt == "?" else prompt]
            if text in items:
                items.remove(text)
            items.append(text)
            del items[:-HISTORY_SIZE]
        if prompt in ("/", "?"):
            self.search_dir = 1 if prompt == "/" else -1
            entries = self.pane.model.entries
            if 0 <= self.search_origin < len(entries):
                self.pane.last_jump = (self.pane.path, entries[self.search_origin].name)
            if text:
                self.last_search = text
            elif self.last_search:  # an empty search repeats the last one, as in vim
                self._search_step(self.search_dir)
            if self.last_search and self.find(self.last_search, 0, 1) < 0:
                self.show_message(f"Pattern not found: {self.last_search}")
            self.set_highlight(bool(self.last_search))
        elif prompt == "=":
            self.set_filter(text)
            self._filter_message()
        elif text:
            self.last_ex = text
            self.execute(text)
        self.update_status()

    def history_window(self, prompt: str) -> None:
        """q: / q/: the history in a list, newest at the bottom. j / k move, Enter runs
        the line, e (or i) puts it on the command line to edit first, Esc / q closes."""
        items = self.history[prompt]
        if not items:
            self.show_message("No history yet")
            return
        box = QDialog(self)
        box.setWindowTitle("Command history" if prompt == ":" else "Search history")
        view = QListWidget()
        view.setFont(getattr(self, "text_font", view.font()))
        view.addItems([prompt + item for item in items])
        view.setCurrentRow(len(items) - 1)
        chosen: list[tuple[str, bool]] = []

        def key_press(e: QKeyEvent, base=view.keyPressEvent):
            text, row = e.text(), view.currentRow()
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or text in ("e", "i"):
                chosen.append((items[row], text not in ("e", "i")))
                box.accept()
            elif text in ("j", "k"):
                view.setCurrentRow(max(0, min(len(items) - 1, row + (1 if text == "j" else -1))))
            elif text == "G":
                view.setCurrentRow(len(items) - 1)
            elif text == "g":
                view.setCurrentRow(0)
            elif text == "q":
                box.reject()
            else:
                base(e)

        view.keyPressEvent = key_press
        view.itemDoubleClicked.connect(lambda _: (chosen.append((items[view.currentRow()], True)), box.accept()))
        QVBoxLayout(box).addWidget(view)
        fit_window(box, 800, 400)
        box.exec()
        self.pane.view.setFocus()
        if not chosen:
            return
        text, run = chosen[0]
        if not run:
            self.show_cmdline(prompt, text)
        elif prompt == ":":
            self.last_ex = text
            self.execute(text)
        else:
            self.show_cmdline("/", text)
            self.search_origin = self.pane.row
            self.on_cmdline_enter()

    def history_step(self, step: int) -> None:
        """Up / Down on the command line: older / newer entries starting with what was typed."""
        prompt = self.prompt_label.text()
        items = self.history["/" if prompt == "?" else prompt]
        state = self._history_state
        if state is None:
            state = self._history_state = {"prefix": self.cmdline.text(), "i": len(items)}
        i = state["i"]
        while True:
            i += step
            if i < 0:
                QApplication.beep()
                return
            if i >= len(items):
                state["i"], text = len(items), state["prefix"]
                break
            if items[i].startswith(state["prefix"]):
                state["i"], text = i, items[i]
                break
        self._completing = True
        self.cmdline.setText(text)
        self._completing = False

    def register_text(self, name: str) -> str:
        """Register x as text for Ctrl-r x on the command line: its paths, quoted
        if needed; ':' is the last command, '/' the last search."""
        if name == ":":
            return self.last_ex
        if name == "/":
            return self.last_search or ""
        reg = self._register_name(name)
        held = clipboard.get_files() if reg == "+" else self.registers.get(reg.lower())
        quote = subprocess.list2cmdline if WINDOWS else shlex.join
        return quote(held[1]) if held else ""

    def cmdline_insert(self, kind: str) -> None:
        entry = self.pane.current_entry()
        if kind.startswith("reg:"):
            self.cmdline.insert(self.register_text(kind[4:]))
        elif kind == "folder":
            self.cmdline.insert(self.pane.path)
        elif entry and not entry.is_parent:
            self.cmdline.insert(entry.name if kind == "name" else entry.path)

    def command_names(self) -> set[str]:
        """Every : command: the ex_ methods and the config's own commands."""
        return ({m[3:] for m in dir(type(self)) if m.startswith("ex_")}
                | set(self.settings.get("commands") or {}) | set(self.user_commands))

    def execute(self, line: str, depth: int = 0) -> None:
        line = line.strip().lstrip(":")
        if (m := RANGE.match(line)) and (m.group(2) or not line.isdigit()):
            self.range_command(m.group(1), m.group(2), line[m.end():].strip(), depth)
            return
        if line in ("&", "&&"):  # repeat the last :s
            self.repeat_substitute()
            return
        if depth == 0 and not self._repeating:
            word = line.partition(" ")[0].rstrip("!")
            name = "s" if re.match(r"s[^\w\s]", line) else (resolve(word, self.command_names())[0] or word)
            if name in REPEATABLE_EX:
                self.last_change = ("ex", line)
            if name in JUMP_COMMANDS or line.isdigit():
                self.remember_jump()
        if line.startswith("!"):
            self.shell_command(line[1:])
            return
        if line.isdigit():
            self.pane.set_cursor(int(line) - 1)
            return
        if re.match(r"s[^\w\s]", line):
            self.substitute(line)
            return
        if m := GLOBAL.match(line):
            self.global_command(m.group(1), m.group(2), m.group(3), depth)
            return
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
        name, could_be = resolve(cmd.rstrip("!"), self.command_names())
        could_be = [n for n in could_be if ALIASES.get(n) not in could_be]  # :tabclose, not also :tabc
        if name is None:
            self.show_message(f"Ambiguous: :{cmd} could be " + ", ".join(":" + n for n in could_be[:8])
                              if could_be else f"Not a command: {cmd}")
            return
        user = {**(self.settings.get("commands") or {}), **self.user_commands}
        if name in user and not hasattr(self, f"ex_{name}"):
            if depth < 10:
                self.execute(f"{user[name]} {arg}", depth + 1)
            return
        self.bang = cmd.endswith("!")
        self._typed_command = name  # :nmap and :map share a method
        try:
            getattr(self, f"ex_{name}")(arg)
        except OSError as e:
            self.show_message(f"{name}: {e.strerror or e}")
        finally:
            self.bang = False

    def range_address(self, text: str) -> int:
        """A row from a vim address: N (line N), . (the cursor), $ (the last), 'a (mark
        a, if it is in this folder), '< / '> (the visual range), each with +N / -N."""
        m = re.match(r"(\d+|\.|\$|'.)?(.*)", text)
        base, offsets = m.group(1), m.group(2)
        pane = self.pane
        if base is None or base == ".":
            row = pane.row
        elif base == "$":
            row = len(pane.model.entries) - 1
        elif base.isdigit():
            row = int(base) - 1
        elif base in ("'<", "'>"):
            ends = ((self.visual_anchor, pane.row) if self.engine.mode == VISUAL
                    else self.last_visual or (pane.row, pane.row))
            row = (min if base == "'<" else max)(ends)
        else:
            folder, name = self.marks.get(base[1], ("", ""))
            row = pane.model.row_of(name) if folder == pane.path else -1
            if row < 0:
                raise ValueError(f"Mark not in this folder: {base[1]}")
        for sign, n in re.findall(r"([+-])(\d*)", offsets):
            row += int(n or 1) * (1 if sign == "+" else -1)
        return row

    def range_command(self, first: str, second: str | None, cmd: str, depth: int) -> None:
        """:'<,'>CMD, :%CMD, :.,.+4CMD, :3,8CMD: tag the rows of the range, then run
        CMD on them (without CMD the rows stay tagged)."""
        try:
            if first == "%":
                lo, hi = 0, len(self.pane.model.entries) - 1
            else:
                lo, hi = sorted((self.range_address(first), self.range_address(second or first)))
        except ValueError as e:
            self.show_message(str(e))
            return
        entries = self.pane.model.entries
        if second is None and first != "%" and not cmd:  # :$, :'a, :.+3 go there
            self.pane.set_cursor(max(0, min(lo, len(entries) - 1)))
            return
        rows = [r for r in range(max(lo, 0), min(hi, len(entries) - 1) + 1) if not entries[r].is_parent]
        if self.engine.mode == VISUAL:  # the range replaces the visual selection
            self.last_visual = (self.visual_anchor, self.pane.row)
            self.engine.mode = NORMAL
        if not rows:
            self.show_message("The range has no items")
            return
        self.pane.tag_rows(rows)
        if cmd:
            self.execute(cmd, depth)
        else:
            self.show_message(f"Tagged {len(rows)} item(s)")
        self.update_status()

    def global_command(self, name: str, delimiter: str, rest: str, depth: int = 0) -> None:
        """:g/pattern/cmd tags the items whose names match (:g! and :v the others), then
        runs cmd on them: a : command, d (trash), d! (delete), y (yank) or normal KEYS.
        Without cmd it only tags them; an empty pattern is the last search."""
        invert = name.startswith("v") or name.endswith("!")
        text, cmd = split_global(rest, delimiter)
        text = text or self.last_search
        if not text:
            self.show_message("No pattern: :g/pattern/command")
            return
        regex = self.search_regex(text)
        pane = self.pane
        rows = [row for row, e in enumerate(pane.model.entries)
                if not e.is_parent and bool(regex.search(e.shown or e.name)) != invert]
        if not rows:
            self.show_message(f"No {'other ' if invert else ''}items match: {text}")
            return
        self.leave_visual()
        pane.tag_rows(rows)
        if not cmd:
            self.show_message(f"Tagged {len(rows)} item(s)")
            return
        word, _, arg = cmd.partition(" ")
        if cmd in GLOBAL_ACTIONS or (word in ("y", "yank") and len(arg.strip()) == 1):
            register = arg.strip() if word in ("y", "yank") and arg.strip() else None
            self.run_action(GLOBAL_ACTIONS[word if register else cmd], None, None, register)
        elif word in ("normal", "norm"):
            self.run_keys(arg)
        elif depth < 10:
            self.execute(cmd, depth + 1)

    @staticmethod
    def fill_shell(line: str, ctx: dict) -> str:
        """%d %o %f %s in a shell command line, quoted, from a bar_context() dict (%% = %).
        A % on its own is vim's: the file's name, with modifiers :p (full path), :h (its
        folder), :t (the name), :r (without the extension) and :e (the extension)."""
        quote = subprocess.list2cmdline if sys.platform == "win32" else shlex.join
        values = {"d": quote([ctx["cwd"]]), "o": quote([ctx["other"]]),
                  "f": quote([ctx["current"]]) if ctx["current"] else "",
                  "s": quote(ctx["selected"]), "%": "%"}

        def vim_name(mods: str) -> str:
            current = ctx["current"]
            if not current:
                return ""
            name = os.path.basename(current)  # relative to the folder the command runs in
            for mod in mods[1::2]:
                full = os.path.join(os.path.dirname(current), name)
                name = {"p": full, "h": os.path.dirname(full), "t": os.path.basename(name),
                        "r": os.path.splitext(name)[0], "e": os.path.splitext(name)[1][1:]}[mod]
            return quote([name])

        return re.sub(r"%([dofs%])|%((?::[phtre])*)",
                      lambda m: values[m.group(1)] if m.group(1) else vim_name(m.group(2)), line)

    def shell_command(self, line: str) -> None:
        """Run a shell command line in this folder, with %d %o %f %s filled in (%% = %)."""
        subprocess.Popen(self.fill_shell(line, self.bar_context()), shell=True, cwd=self.pane.path,
                         creationflags=NO_CONSOLE)

    def _arg_path(self, arg: str) -> str:
        if len(arg) > 1 and arg[0] == arg[-1] and arg[0] in "'\"":
            arg = shlex.split(arg)[0]
        if remote.is_remote(arg):
            return arg
        loc = self.pane.remote
        if loc and not re.match(r"^[A-Za-z]:[\\/]", arg):  # in an SSH folder: paths there (C:\x stays local)
            if arg == "~" or arg.startswith("~/"):
                return loc.url("/~" + arg[1:])
            return loc.url(posixpath.normpath(posixpath.join(loc.path, arg.replace("\\", "/"))))
        return os.path.join(self.pane.path, os.path.expanduser(arg))

    def ex_q(self, arg):
        """:q closes the current tab if the pane has several, else quits (:qa always quits)."""
        if not self.pane.close_tab():
            self.close()
    ex_quit = ex_wq = ex_x = ex_q

    def ex_qa(self, arg): self.close()
    ex_qall = ex_quitall = ex_wqa = ex_xa = ex_qa

    def ex_help(self, arg):
        """:help [TOPIC]: the key reference, filtered to the lines that mention TOPIC."""
        self.cmd_help(None, arg)

    def ex_cd(self, arg):
        if arg == "-":
            self.cmd_alternate(None)
        else:
            self.pane.cd(self._arg_path(arg) if arg else os.path.expanduser("~"))

    def ex_sync(self, arg): self.other.cd(self.pane.path)

    def ex_shell(self, arg): self.cmd_shell(None)

    def ex_config(self, arg):
        path = config.config_path()
        if not os.path.exists(path):
            self.save_settings()
        if editor := self.settings["programs"].get("editor"):
            self._run(editor, [path], "the config file")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.show_message(f"Config file: {path}")

    def ex_sort(self, arg):
        """:sort size, :sort -size or :sort! size (reversed)."""
        key = arg.lstrip("-") or "name"
        if key not in SORT_KEYS:
            self.show_message(f"Sort by one of: {', '.join(SORT_KEYS)} (:sort! or a - in front to reverse)")
            return
        self.pane.set_sort(key, arg.startswith("-") or self.bang)

    def ex_select(self, arg):
        n = self.pane.tag_matching(arg or "*")
        self.show_message(f"Tagged {n} item(s)")

    def ex_unselect(self, arg):
        self.pane.tag_matching(arg or "*", select=False)

    def ex_marks(self, arg):
        if not self.marks:
            self.show_message("No marks set (use m + letter)")
            return
        lines = [f"<b>{k}</b> &nbsp; {os.path.join(d, n)}" for k, (d, n) in sorted(self.marks.items())]
        QMessageBox.information(self, "Marks", "<br>".join(lines))

    def ex_delmarks(self, arg):
        for k in arg.replace(" ", "") or list(self.marks):
            self.marks.pop(k, None)
        self.save_settings()

    def ex_bar(self, arg):
        sub, _, rest = arg.partition(" ")
        if sub == "add":
            path = self._arg_path(rest.strip()) if rest.strip() else self.pane.path
            self.bar.add_item(item_for_path(path if remote.is_remote(path) else os.path.normpath(path)))
            self.show_message(f"Added {path} to the bar")
        elif sub == "":
            self.bar.setVisible(not self.bar.isVisible())
            self.save_settings()
        else:
            self.show_message("Usage: :bar  or  :bar add [PATH]")

    def _record(self, what: str, records: list[tuple]) -> None:
        """A new change: it can be undone, and (as in vim) ends what could be redone."""
        if records:
            self.undo_stack = self.undo_stack[-49:] + [(what, records, time.time())]
            self.redo_stack.clear()
            self._save_undo()

    def _save_undo(self) -> None:
        if self.settings.get("persistent_undo", True):
            try:
                config.save_undo(self.undo_stack, self.redo_stack)
            except OSError as e:
                self.show_message(f"Could not save the undo history: {e.strerror or e}")

    @staticmethod
    def _exists(path: str) -> bool:
        return remote.REMOTE.stat(path) is not None if remote.is_remote(path) else os.path.lexists(path)

    @staticmethod
    def _rename(old: str, new: str) -> None:
        remote.REMOTE.rename(old, new) if remote.is_remote(old) else os.rename(old, new)

    def ex_mkdir(self, arg):
        if arg and self.pane.remote and not self._read_only(into=True):
            remote.REMOTE.mkdir(self._arg_path(arg), parents=True)
            self.pane.cd(self.pane.path, select=arg.replace("\\", "/").split("/")[0])
        elif arg and not self._read_only(into=True):
            top = arg.replace("\\", "/").split("/")[0]
            existed = os.path.lexists(self._arg_path(top))
            os.makedirs(self._arg_path(arg))
            if not existed:
                self._record(f"mkdir {top}", [("created", self._arg_path(top))])
            self.pane.cd(self.pane.path, select=top)

    def ex_touch(self, arg):
        if arg and self.pane.remote and not self._read_only(into=True):
            remote.REMOTE.touch(self._arg_path(arg))
            self.pane.cd(self.pane.path, select=arg)
        elif arg and not self._read_only(into=True):
            path = self._arg_path(arg)
            existed = os.path.lexists(path)
            open(path, "a").close()
            if not existed:
                self._record(f"touch {arg}", [("created", path)])
            self.pane.cd(self.pane.path, select=arg)

    def ex_rename(self, arg):
        """:rename NAME for the item under the cursor, or a {name}{ext}{n} pattern
        for every tagged item."""
        entry = self.pane.current_entry()
        if not arg or self._read_only():
            return
        tagged = self.pane.selected_entries()
        if tagged or "{" in arg:
            entries = tagged or ([entry] if entry and not entry.is_parent else [])
            self.rename_many([(e, format_name(arg, e.name, i + 1, e.is_dir))
                              for i, e in enumerate(entries)])
            return
        if not entry or entry.is_parent or arg == entry.name:
            return
        dest = remote.join(remote.dirname(entry.path), arg)
        if self._exists(dest) and os.path.normcase(dest) != os.path.normcase(entry.path):
            if not self.bang:
                self.show_message(f"Already exists: {arg}   (:rename! {arg} replaces it, moving it to the trash)")
                return
            if remote.is_remote(dest) or not QFile(dest).moveToTrash():
                self.show_message(f"Could not move the old {arg} to the trash")
                return
        self._rename(entry.path, dest)
        if not remote.is_remote(dest):  # undo works on this computer only
            self._record(f"rename of {entry.name}", [("renamed", entry.path, dest)])
        if self.pane.results:
            self._renamed_in_lists([(entry.path, dest)])
            self.pane.set_cursor(next((i for i, e in enumerate(self.pane.model.entries) if e.path == dest),
                                      self.pane.row))
        else:
            self.pane.cd(self.pane.path, select=arg)

    def substitute(self, line: str) -> None:
        """:s/regex/replacement/[gi] on the names of the tagged items (or the cursor's)."""
        if self._read_only():
            return
        self.last_substitute = line
        try:
            regex, repl, count = parse_substitute(line)
        except (ValueError, re.error) as e:
            self.show_message(str(e))
            return
        entry = self.pane.current_entry()
        entries = self.pane.selected_entries() or ([entry] if entry and not entry.is_parent else [])
        try:
            self.rename_many([(e, regex.sub(repl, e.name, count=count)) for e in entries])
        except (re.error, IndexError) as e:
            self.show_message(f"Bad replacement: {e}")

    def rename_many(self, pairs: list[tuple[Entry, str]]) -> None:
        pairs = [(e, new) for e, new in pairs if new != e.name]
        if not pairs:
            self.show_message("No names change")
            return
        # Items keep their folder (in a result list they can come from many).
        by_folder: dict[str, list[tuple[str, str]]] = {}
        for e, new in pairs:
            by_folder.setdefault(os.path.normcase(remote.dirname(e.path)), []).append((e.name, new))
        if error := next(filter(None, (check_names(group) for group in by_folder.values())), None):
            self.show_message(error)
            return
        renamed = {os.path.normcase(e.path) for e, _ in pairs}
        dests = [remote.join(remote.dirname(e.path), new) for e, new in pairs]
        if clash := next((d for d in dests if self._exists(d)
                          and os.path.normcase(d) not in renamed), None):
            self.show_message(f"Already exists: {remote.basename(clash)}")
            return
        # Two steps through temporary names, so swaps and case-only changes work.
        steps = [(e.path, e.path + ".vcrename~", dest) for (e, _), dest in zip(pairs, dests)]
        done = []
        try:
            for old, tmp, _ in steps:
                self._rename(old, tmp)
            for old, tmp, new in steps:
                self._rename(tmp, new)
                done.append(("renamed", old, new))
        except OSError as e:
            self.show_message(f"Rename stopped: {e.strerror or e}")
        self._record(f"rename of {len(done)} item(s)", [r for r in done if not remote.is_remote(r[1])])
        self.pane.clear_tags()
        self._renamed_in_lists([(old, new) for _, old, new in done])
        self.pane.reload()
        if done and len(done) == len(steps):
            self.show_message(f"Renamed {len(done)} item(s)" + ("" if self.pane.remote else "   (u = undo)"))

    def _renamed_in_lists(self, pairs: list[tuple[str, str]]) -> None:
        """Renamed files stay in the result lists and on the shelf under their new names."""
        for pane in self.panes:
            pane.results_renamed(pairs)
            for tab in pane.tabs:
                if tab.get("results") and tab["results"] is not pane.results:
                    old = {os.path.normcase(a): b for a, b in pairs}
                    tab["results"].paths = [old.get(os.path.normcase(p), p) for p in tab["results"].paths]
            pane.refresh_results()
        self.shelf.renamed(pairs)
        if self.player is not None:
            for old, new in pairs:
                self.player.rename(old, new)

    # zoxide

    def zoxide_add(self, path: str) -> None:
        if self.zoxide and not remote.is_remote(path) and not archive.locate(path):
            self.zoxide.add(path)

    def _need_zoxide(self) -> bool:
        if self.zoxide is None:
            self.show_message("zoxide not found: install it (Windows: winget install "
                              "ajeetdsouza.zoxide) or set programs.zoxide in the config file")
        return self.zoxide is not None

    def ex_z(self, arg):
        """:z KEYWORDS jumps like zoxide's z; :z with no keywords goes home."""
        if not arg:
            self.pane.cd(os.path.expanduser("~"))
        elif os.path.isdir(path := self._arg_path(arg)):
            self.pane.cd(path)
        elif self._need_zoxide():
            if found := self.zoxide.query(arg.split(), exclude=self.pane.path):
                self.pane.cd(found)
            else:
                self.show_message(f"zoxide: no match for {arg}")

    def ex_zi(self, arg):
        if not self._need_zoxide():
            return
        entries = self.zoxide.list()
        if not entries:
            self.show_message("zoxide's database is empty: visit some folders first")
            return
        dialog = ZoxideDialog(entries, arg, self)
        if dialog.exec() and dialog.chosen:
            if dialog.new_tab:
                self.pane.new_tab(dialog.chosen)
            else:
                self.pane.cd(dialog.chosen)

    def cmd_zoxide_pick(self, a): self.ex_zi("")

    def ex_undo(self, arg): self.cmd_undo(Action("undo", None))

    def ex_find(self, arg):
        if self._local_only(":find"):
            return
        if not arg:
            self.show_message("Usage: :find PATTERN [size>1M] [size<500k] [newer:7d] [older:2w] [type:f|d]")
            return
        if self.pane.archive:
            self.show_message("Can't search inside archives")
            return
        try:
            pattern, conditions = parse_find(arg)
        except ValueError as e:
            self.show_message(str(e))
            return
        root, hidden = self.pane.path, self.pane.model.show_hidden
        self.start_results(f"find {arg}", root,
                           lambda stop: find_paths(root, pattern, hidden, stop, limit=5000, conditions=conditions))

    # Search in files

    def cmd_grep(self, a): self.ex_grep("")

    def ex_grep(self, arg):
        """:grep [PATTERN] (g/) searches inside the files below this folder."""
        if self._local_only(":grep"):
            return
        if self.pane.archive:
            self.show_message("Can't search inside archives")
            return
        root = self.pane.path
        case = (bool(self.settings.get("ignorecase", True)), bool(self.settings.get("smartcase", True)))
        dialog = GrepDialog(root, arg, self.pane.model.show_hidden, case, self, self.cmdline.font(),
                            self._grep_globs)
        accepted = dialog.exec()
        self._grep_globs = dialog.globs.text()
        if dialog.query.text():
            items = self.history[":"]
            line = f"grep {dialog.query.text()}"
            if line in items:
                items.remove(line)
            items.append(line)
        if not accepted:
            return
        self.remember_jump()
        if dialog.action == "list":
            self.show_result_list(f"grep {dialog.query.text()}", root, dialog.files)
            return
        path, line = dialog.chosen
        if dialog.action == "goto":
            self.pane.cd(os.path.dirname(path), select=os.path.basename(path))
        else:
            self.edit_at(path, line)
        self.update_status()

    def edit_at(self, path: str, line: int) -> None:
        """Open a file in the editor at a line (programs.editor_line), else just the file."""
        self.add_recent(path)
        programs = self.settings["programs"]
        ctx = {**self.bar_context(), "current": path, "line": line}
        if program := programs.get("editor_line"):
            if not run_command(program, ctx, [path]):
                self.show_message(f"Could not start {program}")
            return
        if editor := programs.get("editor"):
            self._run(editor, [path], f"{os.path.basename(path)}   "
                      "(set programs.editor_line, e.g. code -g %f:%l, to open at the line)")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.show_message(f"Can't open {path}: set programs.editor in the config file")

    # Result lists

    def start_results(self, title: str, root: str, producer) -> None:
        """Show a result list in the active pane and fill it from `producer(stop)`
        (a function yielding batches of paths) in a thread."""
        pane = self.pane
        if old := self.searches.pop(id(pane), None):
            old.stop.set()
        results = Results(title, root, [])
        pane.show_results(results)
        search = Search(producer)
        timer = QTimer(self, singleShot=True, interval=250)  # refresh at most 4 times a second

        def refresh():
            if pane.results is results:
                pane.refresh_results()
                if search is self.searches.get(id(pane)):
                    self.show_message(f"Searching… {len(results.paths)} found   (Esc stops)")
                self.update_status()

        def found(batch):
            for item in batch:  # a path, or (path, note) or (path, note, duplicate set)
                if isinstance(item, str):
                    results.paths.append(item)
                else:
                    results.paths.append(item[0])
                    results.notes[item[0]] = item[1]
                    if len(item) > 2:
                        results.groups[item[0]] = item[2]
            if not timer.isActive():
                timer.start()

        def done(finished):
            timer.stop()
            refresh()
            if self.searches.get(id(pane)) is search:
                del self.searches[id(pane)]
                if pane.results is results:
                    self.show_message(f"{len(results.paths)} found" + ("" if finished else " (stopped)")
                                      + "   h = back to the folder, gf = go to the file")
            timer.deleteLater()

        timer.timeout.connect(refresh)
        search.found.connect(found)
        search.done.connect(done)
        self.searches[id(pane)] = search
        self.show_message("Searching…")
        search.start()

    def add_recent(self, path: str) -> None:
        """Remember a file opened from the app, for :recent (local files only)."""
        if remote.is_remote(path) or archive.inside(path) or not os.path.isfile(path):
            return
        path = os.path.abspath(path)
        key = os.path.normcase(path)
        self.recent = [path] + [p for p in self.recent if os.path.normcase(p) != key][:RECENT_SIZE - 1]

    def ex_recent(self, arg):
        """:recent lists the files you opened, newest first; :recent DAYS the files changed
        below this folder in the last DAYS days (default 7 with :recent changed)."""
        words = arg.split()
        if not words:
            paths = [p for p in self.recent if os.path.exists(p)]
            if not paths:
                self.show_message("No files opened yet (Enter, :e, F3 / F4 on a file adds it)")
                return
            self.remember_jump()
            self.show_result_list("recent files", self.pane.path, paths)  # .. / h return here
            return
        if words[0] == "clear":
            self.recent = []
            self.save_settings()
            self.show_message("Recent files cleared")
            return
        if words[0] == "changed":
            words = words[1:] or ["7"]
        try:
            days = float(words[0])
        except ValueError:
            self.show_message("Usage: :recent, :recent [changed] DAYS, :recent clear")
            return
        if self._local_only(":recent") or self.pane.archive:
            return
        root, hidden = self.pane.path, self.pane.model.show_hidden
        self.remember_jump()
        self.start_results(f"changed in {words[0]} days", root,
                           lambda stop: changed_files(root, days, hidden, stop))

    def show_result_list(self, title: str, root: str, paths: list[str], pane: Pane | None = None) -> None:
        pane = pane or self.pane
        if old := self.searches.pop(id(pane), None):
            old.stop.set()
        pane.show_results(Results(title, root, list(paths)))
        self.show_message(f"{len(pane.model.entries) - 1} item(s)   h = back to the folder, gf = go to the file")
        self.update_status()

    # ]q / [q walk a result list in place; :cdo runs a command for each of its items

    def _result_list(self) -> Results | None:
        """The active pane's result list: the one it shows, or else the last one."""
        results = self.pane.results or self.pane.last_results
        if results is None:
            self.show_message("No result list yet: :find, :grep, :recent, gb, Ctrl-p then Ctrl-Enter…")
        return results

    def cmd_qf_next(self, a): self.qf_step(a.n())

    def cmd_qf_prev(self, a): self.qf_step(-a.n())

    def ex_cnext(self, arg): self.qf_step(int(arg) if arg.strip().isdigit() else 1)
    ex_cn = ex_cnext

    def ex_cprevious(self, arg): self.qf_step(-(int(arg) if arg.strip().isdigit() else 1))
    ex_cp = ex_cN = ex_cprev = ex_cprevious

    def qf_step(self, step: int) -> None:
        """]q / [q: open the folder of the next / previous item of the result list, with
        the cursor on it (like gf), so the list can be gone through where the files
        are. In the list itself, it goes on from the item under the cursor."""
        pane = self.pane
        results = self._result_list()
        if results is None:
            return
        if pane.results is results and (entry := pane.current_entry()):
            if not entry.is_parent and entry.path in results.paths:
                results.index = results.paths.index(entry.path)
        i = results.index
        while True:
            i += step
            if not 0 <= i < len(results.paths):
                QApplication.beep()
                self.show_message(f"No more items ({len(results.paths)} in [{results.title}])")
                return
            if os.path.lexists(results.paths[i]):
                break
            step = 1 if step > 0 else -1  # skip removed files one at a time
        results.index = i
        path = results.paths[i]
        self.remember_jump()
        if self.pane.cd(os.path.dirname(path), select=os.path.basename(path)):
            self.show_message(f"({i + 1} of {len(results.paths)})  {path}")

    def ex_cdo(self, arg):
        """:cdo CMD runs CMD once for each item of the result list (the tagged ones, or all):
        :cdo !COMMAND in the item's folder with %f = the item, or :cdo COMMAND (a : command)
        with the cursor on the item."""
        cmd = arg.strip()
        if not cmd:
            self.show_message("Usage: :cdo !COMMAND (%f = the item, %d = its folder) or :cdo COMMAND (a : command)")
            return
        pane = self.pane
        results = self._result_list()
        if results is None:
            return
        if pane.results is results:
            paths = [e.path for e in pane.selected_entries() if not e.is_parent]
            paths = paths or [e.path for e in pane.model.entries if not e.is_parent]
        else:
            paths = [p for p in results.paths if os.path.lexists(p)]
        if not paths:
            self.show_message(f"[{results.title}] is empty")
        elif cmd.startswith("!"):
            self._cdo_shell(cmd[1:].strip(), paths)
        else:
            self._cdo_command(cmd, paths, results)

    def _cdo_command(self, cmd: str, paths: list[str], results: Results) -> None:
        """Run a : command with the cursor on each item in turn (in the list, untagged)."""
        name = cmd.partition(" ")[0].rstrip("!")
        if name == "cdo" or not (re.match(r"s[^\w\s]", cmd) or GLOBAL.match(cmd) or hasattr(self, f"ex_{name}")
                                 or name in (self.settings.get("commands") or {})):
            self.show_message(f"Not a command for :cdo: {name}")
            return
        pane = self.pane
        if pane.results is not results:
            pane.show_results(results)
        self.leave_visual()
        pane.clear_tags()
        done, rows, listed = 0, {}, None
        for path in paths:
            if pane.results is not results:
                break  # the command left the list
            if pane.model.entries is not listed:  # the list was reloaded (a rename, a delete…)
                listed = pane.model.entries
                rows = {e.path: i for i, e in enumerate(listed)}
            if path in rows:
                pane.set_cursor(rows[path])
                self.execute(cmd, depth=1)
                done += 1
        self.show_message(f":{cmd} ran for {done} item(s)")

    def _cdo_shell(self, line: str, paths: list[str]) -> None:
        """Run a shell command for each path, one after another, in a thread (Esc stops)."""
        if not line:
            self.show_message("Usage: :cdo !COMMAND, e.g. :cdo !dos2unix %f")
            return
        if self.cdo_run:
            self.show_message("A :cdo is still running (Esc stops it)")
            return
        other = self.other.path
        jobs = [(path, os.path.dirname(path) or ".") for path in paths]
        commands = [self.fill_shell(line, {"cwd": folder, "other": other, "current": path, "selected": [path]})
                    for path, folder in jobs]

        def run(stop):
            for (path, folder), command in zip(jobs, commands):
                if stop.is_set():
                    return
                try:
                    done = subprocess.run(command, shell=True, cwd=folder, capture_output=True,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    output = (done.stderr or done.stdout).decode(errors="replace").strip()
                    yield [(path, done.returncode, output)]
                except (OSError, ValueError, subprocess.SubprocessError) as e:
                    yield [(path, -1, str(e))]

        search, ran, failed = Search(run), [0], []

        def found(batch):
            for path, code, output in batch:
                ran[0] += 1
                if code:
                    failed.append((path, code, output))
            self.show_message(f":cdo {ran[0]} of {len(paths)}…   (Esc stops)")

        def done(finished):
            self.cdo_run = None
            search.deleteLater()
            for pane in self.panes:  # the commands may have made or changed files
                pane.reload()
            text = f":cdo ran for {ran[0]} item(s)" + ("" if finished else " (stopped)")
            if failed:
                path, code, output = failed[0]
                first = output.splitlines()[0][:200] if output else ""
                text += (f", {len(failed)} failed: {os.path.basename(path)} (exit {code})"
                         + (f": {first}" if first else ""))
            self.show_message(text)

        search.found.connect(found)
        search.done.connect(done)
        self.cdo_run = search
        self.show_message(f":cdo running on {len(paths)} item(s)…   (Esc stops)")
        search.start()

    def ex_copen(self, arg): self.ex_results(arg)

    def ex_cclose(self, arg):
        if self.pane.results:
            self.pane.leave_results()

    def ex_clist(self, arg):
        """:clist: the result list's items, numbered; > marks the one ]q went to last."""
        results = self._result_list()
        if results is None:
            return
        lines = [f"{'>' if i == results.index else ' '}{i + 1:4}  {p}" for i, p in enumerate(results.paths[:500])]
        if len(results.paths) > 500:
            lines.append(f"… {len(results.paths) - 500} more")
        self.show_text(f"[{results.title}]", "\n".join(lines) or "(empty)")

    def ex_colder(self, arg): self._result_history(-(int(arg) if arg.isdigit() else 1))

    def ex_cnewer(self, arg): self._result_history(int(arg) if arg.isdigit() else 1)

    def _result_history(self, step: int) -> None:
        """:colder / :cnewer: the pane keeps its last result lists, like vim's quickfix stack."""
        pane = self.pane
        lists = pane.result_history
        current = pane.results or pane.last_results
        i = next((k for k, r in enumerate(lists) if r is current), len(lists) - 1) + step
        if not lists or not 0 <= i < len(lists):
            self.show_message(f"No {'older' if step < 0 else 'newer'} result list ({len(lists)} kept)")
            return
        self.remember_jump()
        pane.show_results(lists[i])
        self.show_message(f"Result list {i + 1} of {len(lists)}: [{lists[i].title}]")

    def ex_delete(self, arg):
        """:delete trashes the tagged items (or the cursor's), :delete! deletes them for good."""
        self.run_action("delete" if self.bang else "trash", None, None, None)

    def ex_yank(self, arg): self.run_action("yank", None, None, arg[:1] or None)

    def ex_put(self, arg): self.run_action("paste", None, None, arg[:1] or None)

    def ex_normal(self, arg):
        """:normal KEYS runs keys in the pane as if typed (<C-d>, <CR> style for special
        keys), through the maps; :normal! uses the built-in keys only."""
        if not arg:
            self.show_message("Usage: :normal KEYS, e.g. :normal 3jyy")
            return
        self.run_keys(arg, remap=not self.bang)

    def run_keys(self, keys: str, remap: bool = True) -> None:
        """Keys as if typed: through the maps, or (remap=False) the built-in and config
        keys only. After :, /, ? or = they go to the command line until <CR>; a
        prompt left open waits for the user."""
        if self._key_depth >= 20:
            self.show_message("Maps nested too deeply (a map that runs itself?)")
            return
        engine = self.engine
        saved = engine.bindings, engine.arg_bindings
        if not remap:
            engine.bindings, engine.arg_bindings = self.base_bindings
        self._key_depth += 1
        try:
            engine.reset()
            for key in split_keys(keys, self.leader):
                if self.cmdline.isVisible():
                    if key == "<CR>":
                        self.on_cmdline_enter()
                    elif key in ("<Esc>", "<C-c>"):
                        self.on_cmdline_cancel()
                    elif key == "<BS>":
                        self.cmdline.backspace()
                    elif key == "<Space>":
                        self.cmdline.insert(" ")
                    elif len(key) == 1:
                        self.cmdline.insert(key)
                    continue
                self.feed_key(key)
            if action := engine.flush():
                self.run_action(action.name, action.count, action.arg, action.register)
            engine.reset()
        finally:
            self._key_depth -= 1
            if not remap:
                engine.bindings, engine.arg_bindings = saved

    def cmd_keys(self, a): self.run_keys((str(a.count) if a.count else "") + (a.arg or ""), remap=False)
    def cmd_remap(self, a): self.run_keys((str(a.count) if a.count else "") + (a.arg or ""))

    # :map

    def _map_modes(self) -> tuple[str, ...]:
        """The modes of the running :map, :nmap, :vnoremap… (x = v: there is one visual mode)."""
        name = self._typed_command
        if name[:1] in "nvx" and name[1:] in ("map", "noremap", "unmap"):
            return (NORMAL,) if name[0] == "n" else (VISUAL,)
        return (NORMAL, VISUAL)

    def _map_command(self, arg: str, remap: bool) -> None:
        modes = self._map_modes()
        lhs, _, rhs = arg.strip().partition(" ")
        while lhs.lower() in ("<silent>", "<nowait>", "<unique>", "<buffer>"):  # vim's flags, ignored
            lhs, _, rhs = rhs.strip().partition(" ")
        rhs = rhs.strip()
        if not lhs or not rhs:
            self.list_maps(modes, "".join(split_keys(lhs, self.leader)) if lhs else "")
            return
        keys = "".join(split_keys(lhs, self.leader))
        rhs = rhs.replace("<leader>", self.leader).replace("<Leader>", self.leader)
        if rhs.lower() == "<nop>":
            value = ""
        elif m := re.fullmatch(r":([^<]*)<cr>", rhs, re.IGNORECASE):  # a : command: run it directly
            value = "ex:" + m.group(1)
        else:
            value = ("remap:" if remap else "keys:") + rhs
        for mode in modes:
            self.maps[mode][keys] = value
        self.apply_keys()

    def ex_map(self, arg): self._map_command(arg, True)
    ex_nmap = ex_vmap = ex_xmap = ex_map

    def ex_noremap(self, arg): self._map_command(arg, False)
    ex_nnoremap = ex_vnoremap = ex_xnoremap = ex_noremap

    def ex_unmap(self, arg):
        modes = self._map_modes()
        keys = "".join(split_keys(arg.strip(), self.leader))
        found = [mode for mode in modes if self.maps[mode].pop(keys, None) is not None]
        if not found:
            self.show_message(f"No such mapping: {arg.strip()}")
        self.apply_keys()
    ex_nunmap = ex_vunmap = ex_xunmap = ex_unmap

    def ex_mapclear(self, arg):
        for maps in self.maps.values():
            maps.clear()
        self.apply_keys()
        self.show_message("Maps cleared")

    def list_maps(self, modes: tuple[str, ...], start: str = "") -> None:
        rows = []
        for mode in modes:
            for keys, value in sorted(self.maps[mode].items()):
                if keys.startswith(start):
                    kind, _, what = value.partition(":")
                    shown = {"ex": f":{what}<CR>", "keys": f"* {what}", "remap": what}.get(kind, "<Nop>")
                    rows.append(f"{mode[0].lower()}  {keys:<12} {shown}")
        if not rows:
            self.show_message("No mapping found")
        elif len(rows) == 1:
            self.show_message(rows[0])
        else:
            self.show_text("Maps", "\n".join(rows))

    def ex_command(self, arg):
        """:command NAME REPLACEMENT adds a : command for this session (like the config's
        "commands"; arguments are added after it); :command lists them, :delcommand NAME
        removes one. Vim's -nargs and other flags are skipped."""
        words = arg.split()
        while words and words[0].startswith("-"):
            words.pop(0)
        if not words:
            user = {**(self.settings.get("commands") or {}), **self.user_commands}
            if user:
                self.show_text("Commands", "\n".join(f":{k:<14} {v}" for k, v in sorted(user.items())))
            else:
                self.show_message("No user commands")
            return
        name, replacement = words[0], " ".join(words[1:])
        if not replacement:
            self.show_message(f":{name} = {self.user_commands.get(name, '(not defined)')}")
        elif hasattr(self, f"ex_{name}"):
            self.show_message(f":{name} is a built-in command")
        else:
            self.user_commands[name] = replacement

    def ex_delcommand(self, arg):
        if self.user_commands.pop(arg.strip(), None) is None:
            self.show_message(f"No such user command: {arg.strip()}")

    def ex_let(self, arg):
        """:let mapleader = ","   (the only variable there is)"""
        m = re.fullmatch(r"(?:g:)?mapleader\s*=\s*(['\"])(.*)\1", arg.strip())
        if not m:
            self.show_message('Only :let mapleader = "X" is supported')
            return
        value = m.group(2).replace("\\<", "<") if m.group(1) == '"' else m.group(2)  # "\<Space>"
        if m.group(1) == '"':
            value = value.replace("\\\\", "\\")  # "\\" is one backslash
        keys = split_keys(value)
        if len(keys) != 1:
            self.show_message("mapleader must be one key")
            return
        self.leader = "<Space>" if keys[0] == " " else keys[0]

    def rc_path(self) -> str:
        """The vimcommanderrc run at start: the config's "vimrc", relative to the config file's folder."""
        name = os.path.expanduser(str(self.settings.get("vimrc") or "vimcommanderrc"))
        return os.path.join(os.path.dirname(config.config_path()), name)

    def ex_source(self, arg):
        """:source FILE runs its lines as : commands ("-comments and empty lines skipped;
        a line starting with \\ continues the one before, as in vim)."""
        path = self._arg_path(arg) if arg else self.rc_path()
        try:
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
        except OSError as e:
            self.show_message(f"Can't read {path}: {e.strerror or e}")
            return
        lines: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("\\") and lines:
                lines[-1] += line[1:]
            elif line and not line.startswith('"'):
                lines.append(line)
        for line in lines:
            self.execute(line, depth=1)

    def ex_pwd(self, arg): self.show_message(self.pane.path)

    def ex_messages(self, arg):
        if not self.messages:
            self.show_message("No messages yet")
            return
        self.show_text("Messages", "\n".join(f"{time.strftime('%H:%M:%S', time.localtime(t))}  {m}"
                                             for t, m in self.messages))

    def show_text(self, title: str, text: str) -> None:
        """A list (:messages, :clist) in a window of its own, newest at the bottom."""
        box = QDialog(self)
        box.setWindowTitle(title)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(getattr(self, "text_font", view.font()))
        layout = QVBoxLayout(box)
        layout.addWidget(view)
        fit_window(box, 900, 500)
        view.moveCursor(QTextCursor.MoveOperation.End)
        box.exec()
        self.pane.view.setFocus()

    def cmd_repeat_substitute(self, a): self.repeat_substitute()

    def repeat_substitute(self) -> None:
        """&: the last :s again, on the tagged items or the cursor's."""
        if not self.last_substitute:
            self.show_message("No :s yet")
        else:
            self.substitute(self.last_substitute)

    # Tabs

    def ex_tabnext(self, arg):
        """:tabnext = the next tab, :tabnext N = tab N."""
        pane = self.pane
        pane.switch_tab(int(arg) - 1 if arg.isdigit() else (pane.tab + 1) % len(pane.tabs))

    def ex_tabprevious(self, arg):
        pane = self.pane
        pane.switch_tab((pane.tab - (int(arg) if arg.isdigit() else 1)) % len(pane.tabs))

    def ex_tabfirst(self, arg): self.pane.switch_tab(0)

    def ex_tablast(self, arg): self.pane.switch_tab(len(self.pane.tabs) - 1)

    def ex_tabonly(self, arg):
        pane = self.pane
        for i in reversed(range(len(pane.tabs))):
            if i != pane.tab:
                pane.close_tab(i)

    # :set options that last for this session

    def runtime_options(self) -> dict:
        """name -> (current value, function setting it): views, not saved settings."""
        return {
            "hex": (preview.HEX["on"], lambda on: self.ex_hex("on" if on else "off")),
            "plot": (preview.PLOT["on"], lambda on: on != preview.PLOT["on"] and self.cmd_plot(None)),
            "grid": (self.pane.grid_mode, lambda on: self.ex_grid("on" if on else "off")),
            "quickview": (self.quickview, lambda on: on != self.quickview and self.cmd_quickview(None)),
            "linkpanes": (self.link is not None, lambda on: self.ex_linkpanes("on" if on else "off")),
            "bar": (self.bar.isVisible(), lambda on: on != self.bar.isVisible() and self.ex_bar("")),
        }

    def ex_results(self, arg):
        """:results shows the last result list of this pane again."""
        last = self.pane.last_results
        if last is None:
            self.show_message("No result list yet: :find, :grep, Ctrl-p then Ctrl-Enter, gr, :shelf list")
        elif self.pane.results is not last:
            self.remember_jump()
            self.pane.show_results(last)
    ex_res = ex_results

    def cmd_goto_file(self, a):
        """gf: open the folder of the file under the cursor, on that file (useful in result lists)."""
        entry = self.pane.current_entry()
        if entry is None or entry.is_parent:
            return
        if (results := self.pane.results) and entry.path in results.paths:
            results.index = results.paths.index(entry.path)  # ]q goes on from here
        folder = os.path.dirname(entry.path)
        if self.pane.results is None and os.path.normcase(folder) == os.path.normcase(self.pane.path):
            self.show_message("Already in its folder")
            return
        self.remember_jump()
        self.pane.cd(folder, select=entry.name)

    # Settings

    def apply_config(self) -> None:
        """Apply self.settings: keys, pane options, launch bar, marks."""
        s = self.settings
        try:
            bad = self.apply_keys()
            try:
                self.engine.langmap = parse_langmap(str(s.get("langmap") or ""))
            except ValueError as e:
                self.engine.langmap = parse_langmap("cyrillic")
                bad.append(str(e))
            self.engine.reset()
            self.git_program = gitstatus.find_git(s["programs"].get("git", "")) if s.get("git", True) else None
            program = zoxide.find(s["programs"].get("zoxide", ""))
            if program != (self.zoxide.program if self.zoxide else None):
                self.zoxide = zoxide.Zoxide(program) if program else None
            if not theme.apply(s.get("theme") or ""):
                bad.append(f"theme: {s.get('theme')}")
            bad_colors = set_colors(s.get("colors") or {}, theme.color_defaults(s.get("theme") or ""))
            options = s.get("graph") or {}
            graphview.OPTIONS.update({
                "enabled": bool(options.get("enabled", True)),
                "depth": min(3, max(1, int(options.get("depth", 2)))),
                "size_by": "count" if options.get("size_by") == "count" else "size",
                "mode": options.get("mode") if options.get("mode") in graphview.MODES else "tree",
                "max_nodes": min(2000, max(50, int(options.get("max_nodes", 400)))),
            })
            font, font_warning = monospace_font(s.get("monospace") or {})
            text_font = font if (s.get("monospace") or {}).get("enabled", True) else \
                monospace_font({})[0]  # quick view text stays monospace
            NATURAL["on"] = bool(s.get("natural_sort", True))
            for pane in self.panes:
                pane.configure(s)
                pane.set_font(font, text_font)
                pane.reload()
                pane.view.set_numbers(bool(s.get("number")), bool(s.get("relativenumber")))
                pane.grid.set_thumb_size(self._thumb_size(s))
                if pane.columns != bool(s.get("columns")):
                    pane.set_columns(bool(s.get("columns")))
                    QTimer.singleShot(0, self.splitter.apply_ratio)  # minimum widths changed
                if not self.git_program:
                    pane.set_git({}, "")
            for i, pane in enumerate(self.panes):
                pane.set_active(i == self.active)
            self.set_highlight(self.hl_active)
            for w in (self.mode_label, self.prompt_label, self.cmdline, self.message_label,
                      self.info_label):
                w.setFont(font)
            self.terminal.set_font(text_font)
            self.text_font = text_font
            for pane in self.panes:
                if pane.term:
                    pane.term.set_font(text_font)
                    pane.term.follow = bool((s.get("vterm") or {}).get("follow", True))
            if "bar" in s:
                items = [BarItem.from_dict(d) for d in s["bar"] if isinstance(d, dict)]
            else:
                items = default_items()
            if items != self.bar.items or s.get("bar_labels", True) != self.bar.show_labels:
                self.bar.items, self.bar.show_labels = items, bool(s.get("bar_labels", True))
                self.bar.rebuild()
            self.bar.setVisible(bool(s.get("bar_visible", True)))
            if hasattr(self, "splitter") and abs(self._pane_ratio(s) - self.splitter.ratio) > 0.001:
                self.splitter.set_ratio(self._pane_ratio(s))  # edited by hand
            self.marks = {k: v for k, v in (s.get("marks") or {}).items()
                          if isinstance(v, list) and len(v) == 2}
        except (AttributeError, TypeError, ValueError) as e:
            self.show_message(f"Bad value in the config file: {e}")
            return
        if bad:
            self.show_message("Unknown action in the config file: " + ", ".join(bad))
        elif bad_colors:
            self.show_message("Bad color in the config file (use #rrggbb): " + ", ".join(bad_colors))
        elif font_warning:
            self.show_message(font_warning)

    def apply_keys(self) -> list[str]:
        """The engine's bindings: the defaults, the config's "keys", then the :maps.
        Returns the config's bindings to unknown actions (left out)."""
        bindings, arg_bindings = user_bindings(self.settings.get("keys") or {})
        bad = []
        for table in bindings.values():
            for key, value in list(table.items()):
                if not hasattr(self, "cmd_" + value.partition(":")[0]):
                    bad.append(f"{key}: {value}")
                    del table[key]
        self.base_bindings = ({m: dict(t) for m, t in bindings.items()}, arg_bindings)
        for mode, maps in self.maps.items():
            for keys, value in maps.items():
                bindings[mode].pop(keys, None)
                if value:
                    bindings[mode][keys] = value
                    arg_bindings[mode] = {k: v for k, v in arg_bindings[mode].items() if k != keys}
        self.engine.bindings, self.engine.arg_bindings = bindings, arg_bindings
        self.engine.reset()
        return bad

    @staticmethod
    def _pane_ratio(settings: dict) -> float:
        try:
            return min(0.9, max(0.1, float(settings.get("pane_ratio", 0.5))))
        except (TypeError, ValueError):
            return 0.5

    def reload_config(self) -> None:
        self._watch_config()
        try:
            data = config.load()
        except config.ConfigError as e:
            self.show_message(f"Config not reloaded: {e}")
            return
        settings = config.with_defaults(data)
        if settings != self.settings:
            self.settings = settings
            self.apply_config()
            if not self.message_label.text():
                self.show_message("Config reloaded")

    def _watch_config(self) -> None:
        path = config.config_path()
        if os.path.exists(path) and path not in self.config_watcher.files():
            self.config_watcher.addPath(path)

    def save_settings(self, options: dict | None = None) -> None:
        """Write the app's state (and changed `options`) into the config file,
        keeping everything else in it."""
        try:
            data = config.with_defaults(config.load())  # pick up edits made meanwhile
        except config.ConfigError as e:
            self.show_message(f"Settings not saved, fix the config file first: {e}")
            return
        data.update({
            "bar": [item.to_dict() for item in self.bar.items],
            "bar_labels": self.bar.show_labels,
            "bar_visible": not self.bar.isHidden(),
            "marks": self.marks,
            "macros": self.macros,
            "registers": {k: list(v) for k, v in self.registers.items() if k.isalnum()},
            "panes": [pane.path for pane in self.panes],
            "tabs": [pane.tab_paths() for pane in self.panes],
            "tab_index": [pane.tab for pane in self.panes],
            "history": self.history,
            "recent": self.recent,
            "pane_ratio": round(self.splitter.ratio, 4),
            "grid": [pane.grid_mode for pane in self.panes],
            "thumbnail_size": self.panes[0].grid.thumb_size,
            "shelf": self.shelf.paths,
            "shelf_visible": not self.shelf.isHidden(),
            **(options or {}),
        })
        try:
            config.save(data)
        except OSError as e:
            self.show_message(f"Could not save settings: {e}")
            return
        self.settings = data
        self._watch_config()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.whichkey.hide()
            self._whichkey_timer.stop()
        super().changeEvent(event)

    def closeEvent(self, event):
        self.save_settings()
        self.terminal.stop()
        if self.player is not None:
            self.player.release()
        for pane in self.panes:
            pane.close_terminal()
        remote.REMOTE.close_all()
        super().closeEvent(event)

    # Status

    def show_message(self, text: str) -> None:
        # :messages keeps them, but not progress ("Searching… 12 found") or repeats
        if text and not text.endswith("…") and "(Esc stops)" not in text and (
                not self.messages or self.messages[-1][1] != text):
            self.messages.append((time.time(), text))
        self.message_label.setText(text)
        self.message_label.setToolTip(text if len(text) > 60 else "")

    def update_status(self) -> None:
        mode = f"-- {self.engine.mode} --" if self.engine.mode == VISUAL else ""
        if self.recording:
            mode = (mode + "  " if mode else "") + f"recording @{self.recording.lower()}"
        self.mode_label.setText(mode)
        pane = self.pane
        total = sum(1 for e in pane.model.entries if not e.is_parent)
        tagged = len(pane.selected_entries())
        parts = []
        if self.engine.pending_display:
            parts.append(self.engine.pending_display)
        if self.register:
            parts.append(f"[{self.register[0]}: {len(self.register[1])}]")
        if tagged:
            parts.append(f"{tagged} tagged")
        parts.append(f"{pane.row + 1}/{len(pane.model.entries)}" if total else "empty")
        if pane.free:
            parts.append(pane.free)
        self.info_label.setText("   ".join(parts))
        self.update_quickview()


def show_exception(kind, value, tb) -> None:
    """Without a console (packaged build) errors would vanish; show them instead."""
    text = "".join(traceback.format_exception(kind, value, tb))
    QMessageBox.critical(QApplication.activeWindow(), "VimCommander error", text[-3000:])


def main() -> None:
    if sys.stderr is None:
        sys.excepthook = show_exception
    app = QApplication(sys.argv)
    app.setApplicationName("VimCommander")
    from . import appicon
    app.setWindowIcon(appicon.icon())
    error = None
    try:
        settings = config.with_defaults(config.load())
    except config.ConfigError as e:
        settings, error = config.with_defaults({}), str(e)

    font = app.font()
    if family := settings["font"].get("family"):
        font.setFamily(family)
    if (size := settings["font"].get("size")) and isinstance(size, (int, float)) and size > 0:
        font.setPointSizeF(size)
    app.setFont(font)

    saved = [p for p in settings.get("panes", []) if isinstance(p, str) and os.path.isdir(p)]
    if not settings.get("restore_panes", True):
        saved = []
    args = sys.argv[1:]
    left = args[0] if args else saved[0] if saved else os.getcwd()
    right = args[1] if len(args) > 1 else saved[1] if len(saved) > 1 else os.path.expanduser("~")
    window = MainWindow(left, right, settings, restore_tabs=bool(saved) and not args)
    win = settings["window"]
    try:
        window.resize(int(win.get("width", 1100)), int(win.get("height", 700)))
    except (TypeError, ValueError):
        pass
    if win.get("maximized"):
        window.showMaximized()
    else:
        window.show()
    if error:
        QMessageBox.warning(window, "Config file not loaded",
                            f"{error}\n\nUsing default settings. Fix the file and it reloads "
                            "automatically; nothing is saved until then.")
    else:
        window.save_settings()  # writes any options missing from the file, so it lists them all
    code = app.exec()
    clipboard.release_at_exit()
    sys.exit(code)

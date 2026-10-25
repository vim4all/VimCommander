"""Settings stored as one JSON file that you can edit by hand.

The file holds user options (window, font, programs, keys, commands) and
state the app saves itself (launch bar, marks, last folders). It is found at,
in order: $VIMCOMMANDER_CONFIG; config.json next to the executable of a
packaged build (portable mode); config.json in Qt's AppConfigLocation.
"""

import copy
import json
import os
import sys

from PySide6.QtCore import QStandardPaths

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"

if WINDOWS:
    DEFAULT_EDITOR = "notepad.exe"
elif MAC:
    DEFAULT_EDITOR = "open -t"
else:
    DEFAULT_EDITOR = ""  # empty = the file's default application

# Every option with its default. Nested dicts are merged one level deep, so a
# file that sets only {"window": {"width": 1400}} keeps the default height.
DEFAULTS = {
    "window": {"width": 1100, "height": 700, "maximized": False},
    "font": {"family": "", "size": 0},  # empty / 0 = system default
    # File lists, paths, command line and quick view. Empty family = best installed one.
    "monospace": {"enabled": True, "family": "", "size": 0},
    # "" = the system look, "catppuccin-mocha" = Catppuccin Mocha (the whole window and
    # the defaults of "colors", which still win where you change them)
    "theme": "",
    # Colors: "#rrggbb", "#rrggbbaa" (aa = opacity, 00-ff) or a name like "red";
    # empty = the system theme's color.
    "colors": {
        "cursor": "#2f6fd0",           # cursor row in the active pane
        "cursor_text": "#ffffff",
        "cursor_inactive": "#2f6fd04d",  # cursor row in the other pane
        "tagged": "#e53935",           # tagged items: text and the stripe on the left
        "tagged_background": "#e5393526",
        "search": "#ffc4005a",         # names matching the search (hlsearch)
        "hidden": "#808080",           # hidden files' text
        "folder": "",                  # folders' text
        "header_active": "#2f6fd0",    # path header of the active pane
        "header_active_text": "#ffffff",
        "git_modified": "#d98e04",     # git: changed files (and folders with changes inside)
        "git_new": "#2ea043",          # git: new, not yet committed
        "git_conflict": "#ff4f8b",     # git: merge conflicts
        "git_ignored": "#808080",      # git: ignored by .gitignore
        "linked_missing": "#a056e0",   # :linkpanes: items the other pane lacks
        "line_number": "",             # line numbers (:set number / relativenumber); "" = theme / system
        "line_number_current": "",     # the cursor row's number
    },
    # Graph in the quick view of a folder: depth 1-3, node size by "size" or "count",
    # mode "tree" (folders), "sunburst" / "treemap" (disk usage), "timeline" (when each
    # subfolder changed), "types" (kinds of files per subfolder), "notes" (Markdown files
    # and their links) or "code" (source files and their imports).
    "graph": {"enabled": True, "depth": 2, "size_by": "size", "mode": "tree", "max_nodes": 400},
    "show_hidden": False,
    "natural_sort": True,  # numbers in names in counting order: file2 before file10 (:set nonatural)
    "git": True,  # color files by their git status and show the branch (needs git)
    "columns": False,  # Miller columns: parent folder | list | preview (:set columns)
    "thumbnail_size": 128,  # thumbnail grid (zi), 48-320; Ctrl+wheel changes it
    # :share: port 0 = any free one (set one to allow it once in a firewall), minutes until
    # it stops, and whether the phone may send files into the pane's folder
    "share": {"port": 0, "minutes": 15, "upload": True},
    # The music player (gm): volume 0-100, shuffle, repeat "all" | "one" | "off", speed 0.25-4
    # (pitch kept); it saves them itself
    "player": {"volume": 70, "shuffle": False, "repeat": "all", "speed": 1.0},
    # Folder rules for :rules, e.g. {"folder": "~/Downloads", "match": "*.pdf",
    # "older_than_days": 7, "action": "move", "to": "~/Documents/PDF"}; see the README.
    "rules": [],
    # vim options, also set with :set
    "number": False, "relativenumber": False,
    "ignorecase": True, "smartcase": True, "hlsearch": True, "wrapscan": True,
    "clipboard": "",  # "unnamedplus": plain y / p use the system clipboard
    # vim's 'langmap': keys typed in another keyboard layout act as their US-layout keys
    # ("cyrillic" = Ukrainian / Russian, so о = j and пп = gg); "" = off; or vim's format
    # ("FROM;TO" or pairs, comma-separated), e.g. "cyrillic,õ[ü]" for the Estonian keys
    "langmap": "cyrillic",
    # The terminal pane (:vterm, Ctrl-w t): shell "" = programs.shell, else the system's
    # ("wsl", "wsl:Ubuntu", "pwsh", "cmd", "bash" or a command line); follow = the shell
    # and the file pane cd along with each other
    "vterm": {"shell": "", "follow": True},
    "which_key_delay": 500,  # ms after a prefix key (g, z, ", Ctrl-w…) before the hints show; 0 = off
    "timeoutlen": 1000,  # ms a key waits when a :map starts with it (",x" next to ","), as in vim
    "vimrc": "vimcommanderrc",  # : commands run at start (:map, :set, :let mapleader); next to the config
    "sort": "name",  # name | ext | size | time, "-size" = reverse
    "date_format": "%Y-%m-%d %H:%M",
    "confirm_trash": True,
    "persistent_undo": True,  # keep what u / Ctrl-r can undo and redo between runs (undo.json)
    "queue_jobs": True,  # copies, moves, syncs and zips run one after another (:jobs shows the queue)
    "restore_panes": True,
    "pane_ratio": 0.5,  # the left pane's share of the width (0.1-0.9); dragging the divider saves it
    "history_limit": 100,
    "programs": {
        "editor": DEFAULT_EDITOR,  # F4
        # :grep opens a match with this, %f = file, %l = line; empty = the editor, at the top.
        # e.g. "code -g %f:%l", "notepad++ -n%l %f", "gvim +%l %f", "subl %f:%l"
        "editor_line": "",
        "viewer": "",              # F3
        "terminal": "",            # S and :shell; empty = detect one
        "open_with": {},           # {".txt": "code %f"} for l / Enter
        "zoxide": "",              # :z, :zi, gz; empty = zoxide on PATH
        "git": "",                 # git status colors, :git; empty = git on PATH
        "shell": "",               # terminal panel (Ctrl-`); empty = PowerShell on Windows, $SHELL elsewhere
                                   # (on an :ssh folder the terminal runs your shell on the server)
    },
    "commands": {},  # {"code": "!code %d"} adds :code
    "keys": {"normal": {}, "visual": {}},  # {"<F4>": "edit", "gD": ":cd ~/Downloads"}
}


class ConfigError(Exception):
    pass


def app_dir() -> str | None:
    """Folder of the executable in a packaged (PyInstaller) build, else None."""
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None


def config_path() -> str:
    if path := os.environ.get("VIMCOMMANDER_CONFIG"):
        return path
    if (folder := app_dir()) and (os.path.exists(os.path.join(folder, "config.json"))
                                  or os.access(folder, os.W_OK)):
        return os.path.join(folder, "config.json")
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    return os.path.join(base, "config.json")


def with_defaults(data: dict) -> dict:
    """`data` with every missing option filled in, defaults first in the file."""
    merged = copy.deepcopy(DEFAULTS)
    for key, value in data.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def load() -> dict:
    """The settings file's contents ({} if it doesn't exist).

    Raises ConfigError if the file exists but isn't valid JSON, so a typo
    never makes the app overwrite the file with defaults.
    """
    path = config_path()
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise ConfigError(f"{path}: {e}") from e
    except OSError as e:
        raise ConfigError(f"{path}: {e.strerror or e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the file must contain a JSON object {{...}}")
    return data


UNDO_KINDS = {"created": 2, "moved": 3, "renamed": 3, "trashed": 3}  # record kind: its length


def undo_path() -> str:
    """undo.json, next to the settings file."""
    return os.path.join(os.path.dirname(config_path()) or ".", "undo.json")


def _undo_entries(raw) -> list[tuple[str, list[tuple], float]]:
    out = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("what"), str):
            continue
        records = [tuple(r) for r in e.get("records") or []
                   if isinstance(r, list) and r and UNDO_KINDS.get(r[0]) == len(r)
                   and all(isinstance(x, str) for x in r)]
        if records:
            when = e.get("time")
            out.append((e["what"], records, float(when) if isinstance(when, (int, float)) else 0.0))
    return out


def load_undo() -> tuple[list, list]:
    """(undo, redo) entries saved by save_undo(): (description, records, time);
    both empty if the file is missing or broken."""
    try:
        with open(undo_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    return _undo_entries(data.get("undo")), _undo_entries(data.get("redo"))


def save_undo(undo: list, redo: list) -> None:
    def entries(stack):
        return [{"what": what, "time": when, "records": [list(r) for r in records]}
                for what, records, when in stack]
    path = undo_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"undo": entries(undo), "redo": entries(redo)}, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def save(data: dict) -> None:
    path = config_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)

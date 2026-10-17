"""Color themes: the config's "theme" option.

A theme sets the whole app's Qt palette (with the Fusion style, which follows
the palette on every platform), a flat style sheet (no bevels or frames: line
numbers, headers, launch bar, tabs and scroll bars blend into the panes) and
the defaults of the config's "colors", the graph, diff, grep, sync and activity
colors. "" keeps the system look, with only the line-number gutter flattened.
"""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# https://catppuccin.com/palette, Mocha
MOCHA = {
    "rosewater": "#f5e0dc", "flamingo": "#f2cdcd", "pink": "#f5c2e7", "mauve": "#cba6f7",
    "red": "#f38ba8", "maroon": "#eba0ac", "peach": "#fab387", "yellow": "#f9e2af",
    "green": "#a6e3a1", "teal": "#94e2d5", "sky": "#89dceb", "sapphire": "#74c7ec",
    "blue": "#89b4fa", "lavender": "#b4befe", "text": "#cdd6f4", "subtext1": "#bac2de",
    "subtext0": "#a6adc8", "overlay2": "#9399b2", "overlay1": "#7f849c", "overlay0": "#6c7086",
    "surface2": "#585b70", "surface1": "#45475a", "surface0": "#313244", "base": "#1e1e2e",
    "mantle": "#181825", "crust": "#11111b",
}


# The system look keeps its native widgets; only the line numbers lose the
# raised header-cell look, so the gutter reads as part of the list.
SYSTEM_SHEET = """
QHeaderView::section:vertical { background: palette(base); border: none; padding: 0 6px 0 4px; }
QHeaderView:vertical { background: palette(base); border: none; }
QTableCornerButton::section { background: palette(base); border: none; }
"""


def _sheet(m: dict) -> str:
    """A flat, quiet style sheet from a Catppuccin-like palette."""
    return f"""
QMainWindow, QDialog {{ background: {m["mantle"]}; }}
QToolTip {{ background: {m["surface0"]}; color: {m["text"]}; border: 1px solid {m["surface1"]};
           border-radius: 4px; padding: 3px 6px; }}

QTableView, QListView, QTreeView, QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit, QTextEdit,
QTextBrowser, QScrollArea {{ border: none; background: {m["base"]}; }}
QTableView {{ gridline-color: {m["surface0"]}; }}
QHeaderView {{ background: {m["base"]}; border: none; }}
QHeaderView::section {{ background: {m["base"]}; color: {m["overlay1"]}; border: none; padding: 3px 6px; }}
QHeaderView::section:horizontal {{ border-bottom: 1px solid {m["surface0"]}; }}
QHeaderView::section:horizontal:hover {{ color: {m["text"]}; }}
QHeaderView::section:vertical {{ padding: 0 6px 0 4px; }}
QHeaderView::up-arrow, QHeaderView::down-arrow {{ width: 0; height: 0; }}
QTableCornerButton::section {{ background: {m["base"]}; border: none; }}

QToolBar {{ background: {m["mantle"]}; border: none; spacing: 2px; padding: 3px 6px; }}
QToolBar::separator {{ background: {m["surface0"]}; width: 1px; margin: 4px 4px; }}
QToolBar QToolButton {{ background: transparent; color: {m["subtext1"]}; border: none;
                       border-radius: 6px; padding: 3px 8px; }}
QToolBar QToolButton:hover {{ background: {m["surface0"]}; color: {m["text"]}; }}
QToolBar QToolButton:pressed, QToolBar QToolButton:checked {{ background: {m["surface1"]}; }}

QTabBar {{ background: {m["mantle"]}; }}
QTabBar::tab {{ background: transparent; color: {m["overlay1"]}; border: none;
               border-bottom: 2px solid transparent; padding: 3px 12px; }}
QTabBar::tab:selected {{ color: {m["text"]}; border-bottom-color: {m["mauve"]}; }}
QTabBar::tab:hover {{ color: {m["text"]}; }}

QStatusBar {{ background: {m["mantle"]}; color: {m["subtext0"]}; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: {m["mantle"]}; }}
QDockWidget {{ color: {m["subtext1"]}; }}
QDockWidget::title {{ background: {m["mantle"]}; padding: 4px 6px; }}

QMenu {{ background: {m["mantle"]}; color: {m["text"]}; border: 1px solid {m["surface0"]};
        border-radius: 6px; padding: 4px; }}
QMenu::item {{ padding: 4px 18px 4px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background: {m["surface0"]}; }}
QMenu::item:disabled {{ color: {m["overlay0"]}; }}
QMenu::separator {{ height: 1px; background: {m["surface0"]}; margin: 4px 6px; }}

QLineEdit, QSpinBox, QComboBox {{ background: {m["base"]}; color: {m["text"]}; border: 1px solid {m["surface0"]};
                                 border-radius: 5px; padding: 2px 6px;
                                 selection-background-color: {m["surface2"]}; selection-color: {m["text"]}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {m["lavender"]}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {m["mantle"]}; border: 1px solid {m["surface0"]};
                              selection-background-color: {m["surface0"]}; }}
QPushButton {{ background: {m["surface0"]}; color: {m["text"]}; border: 1px solid transparent;
              border-radius: 6px; padding: 4px 14px; }}
QPushButton:hover {{ background: {m["surface1"]}; }}
QPushButton:pressed, QPushButton:checked {{ background: {m["surface2"]}; }}
QPushButton:default {{ border-color: {m["lavender"]}; }}
QPushButton:disabled {{ color: {m["overlay0"]}; background: {m["mantle"]}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {m["surface1"]}; border-radius: 3px; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 28px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; }}
QScrollBar::handle:hover {{ background: {m["surface2"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QProgressBar {{ background: {m["surface0"]}; border: none; border-radius: 4px; text-align: center;
               color: {m["text"]}; }}
QProgressBar::chunk {{ background: {m["blue"]}; border-radius: 4px; }}
"""


def _mocha() -> dict:
    m = MOCHA
    return {
        "sheet": _sheet(m),
        "palette": {
            "Window": m["mantle"], "WindowText": m["text"], "Base": m["base"],
            "AlternateBase": m["mantle"], "ToolTipBase": m["surface0"], "ToolTipText": m["text"],
            "PlaceholderText": m["overlay1"], "Text": m["text"], "Button": m["surface0"],
            "ButtonText": m["text"], "BrightText": m["red"], "Light": m["surface2"],
            "Midlight": m["surface1"], "Mid": m["surface0"], "Dark": m["crust"],
            "Shadow": m["crust"], "Highlight": m["blue"], "HighlightedText": m["base"],
            "Accent": m["blue"], "Link": m["blue"], "LinkVisited": m["mauve"],
        },
        "disabled": {"WindowText": m["overlay0"], "Text": m["overlay0"],
                     "ButtonText": m["overlay0"], "Highlight": m["surface1"]},
        "colors": {  # the config's "colors" section
            "cursor": m["blue"], "cursor_text": m["crust"], "cursor_inactive": m["blue"] + "40",
            "tagged": m["red"], "tagged_background": m["red"] + "26",
            "search": m["yellow"] + "55", "hidden": m["overlay0"], "folder": m["blue"],
            "header_active": m["surface0"], "header_active_text": m["blue"],
            "git_modified": m["peach"], "git_new": m["green"], "git_conflict": m["pink"],
            "git_ignored": m["overlay0"], "linked_missing": m["mauve"],
            "line_number": m["surface2"], "line_number_current": m["lavender"],
        },
        "graph": {
            "root": m["mauve"], "folder": m["blue"], "image": m["yellow"], "video": m["red"],
            "audio": m["pink"], "code": m["green"], "document": m["sky"], "archive": m["peach"],
            "program": m["subtext1"], "other": m["overlay1"],
        },
        "diff": {"change": (m["yellow"], 50), "delete": (m["red"], 55), "insert": (m["green"], 55),
                 "filler": (m["overlay0"], 40), "left": (m["red"], 110), "right": (m["green"], 110),
                 "current": (m["blue"], 70)},
        "grep": {"match": (m["yellow"], 100), "line": (m["blue"], 45)},
        "sync": {">": m["blue"], "<": m["green"], "": m["overlay1"], "x": m["red"]},
        "activity": m["green"],
        "terminal": [m["surface1"], m["red"], m["green"], m["yellow"], m["blue"], m["pink"], m["teal"],
                     m["subtext1"], m["surface2"], m["red"], m["green"], m["yellow"], m["blue"], m["pink"],
                     m["teal"], m["subtext0"]],
    }


THEMES = {"catppuccin-mocha": _mocha}
ALIASES = {"mocha": "catppuccin-mocha", "catppuccin": "catppuccin-mocha"}

_system: dict = {}  # the look before any theme: style name, palette, module colors
_current: str | None = None  # None: nothing applied yet


def app() -> QApplication:
    instance = QApplication.instance()
    assert isinstance(instance, QApplication)
    return instance


def color_defaults(name: str) -> dict[str, str]:
    """The theme's defaults for the config's "colors" ({} for the system look)."""
    name = ALIASES.get(name, name)
    return THEMES[name]()["colors"] if name in THEMES else {}


def _rgba(spec) -> QColor:
    color, alpha = spec
    c = QColor(color)
    c.setAlpha(alpha)
    return c


def apply(name: str) -> bool:
    """Switch the app to theme `name` ("" = system). False if there is no such theme."""
    global _current
    from . import activity, diffview, graphview, grepdialog, sync, termshell  # they import Qt widgets
    name = ALIASES.get(str(name or "").strip().lower(), str(name or "").strip().lower())
    if name and name not in THEMES:
        return False
    if name == _current:
        return True
    if not _system:
        _system.update({
            "style": QApplication.style().name(), "palette": QPalette(QApplication.palette()),
            "sheet": app().styleSheet(),
            "graph": dict(graphview.COLORS), "diff": (dict(diffview.LINE_COLORS),
                                                      dict(diffview.SPAN_COLORS), diffview.CURRENT_COLOR),
            "grep": (grepdialog.MATCH_COLOR, grepdialog.LINE_COLOR),
            "sync": dict(sync.ACTION_COLORS), "activity": activity.BASE_COLOR,
        })
    if not name:
        QApplication.setStyle(_system["style"])
        QApplication.setPalette(_system["palette"])
        app().setStyleSheet(_system["sheet"] + SYSTEM_SHEET)
        graphview.COLORS.update(_system["graph"])
        lines, spans, current = _system["diff"]
        diffview.LINE_COLORS.update(lines)
        diffview.SPAN_COLORS.update(spans)
        diffview.CURRENT_COLOR = current
        grepdialog.MATCH_COLOR, grepdialog.LINE_COLOR = _system["grep"]
        sync.ACTION_COLORS.update(_system["sync"])
        activity.BASE_COLOR = _system["activity"]
        termshell.ANSI[:] = termshell.DEFAULT_ANSI
    else:
        t = THEMES[name]()
        QApplication.setStyle("Fusion")
        palette = QPalette()
        for role, color in t["palette"].items():
            if hasattr(QPalette.ColorRole, role):  # Accent is Qt 6.6+
                palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
        for role, color in t["disabled"].items():
            palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor(color))
        QApplication.setPalette(palette)
        app().setStyleSheet(_system["sheet"] + t["sheet"])
        graphview.COLORS.update(t["graph"])
        d = t["diff"]
        diffview.LINE_COLORS.update({k: _rgba(d[k]) for k in ("change", "delete", "insert", "filler")})
        diffview.SPAN_COLORS.update({k: _rgba(d[k]) for k in ("left", "right")})
        diffview.CURRENT_COLOR = _rgba(d["current"])
        grepdialog.MATCH_COLOR, grepdialog.LINE_COLOR = _rgba(t["grep"]["match"]), _rgba(t["grep"]["line"])
        sync.ACTION_COLORS.update({k: QColor(v) for k, v in t["sync"].items()})
        activity.BASE_COLOR = QColor(t["activity"])
        termshell.ANSI[:] = t["terminal"]
    _current = name
    return True

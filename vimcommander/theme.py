"""Color themes: the config's "theme" option.

A theme sets the whole app's Qt palette (with the Fusion style, which follows
the palette on every platform) and the defaults of the config's "colors", the
graph, diff, grep, sync and activity colors. "" keeps the system look.
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


def _mocha() -> dict:
    m = MOCHA
    return {
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
            "header_active": m["blue"], "header_active_text": m["crust"],
            "git_modified": m["peach"], "git_new": m["green"], "git_conflict": m["pink"],
            "git_ignored": m["overlay0"], "linked_missing": m["mauve"],
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
    }


THEMES = {"catppuccin-mocha": _mocha}
ALIASES = {"mocha": "catppuccin-mocha", "catppuccin": "catppuccin-mocha"}

_system: dict = {}  # the look before any theme: style name, palette, module colors
_current = ""


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
    from . import activity, diffview, graphview, grepdialog, sync  # they import Qt widgets
    name = ALIASES.get(str(name or "").strip().lower(), str(name or "").strip().lower())
    if name and name not in THEMES:
        return False
    if name == _current:
        return True
    if not _system:
        _system.update({
            "style": QApplication.style().name(), "palette": QPalette(QApplication.palette()),
            "graph": dict(graphview.COLORS), "diff": (dict(diffview.LINE_COLORS),
                                                      dict(diffview.SPAN_COLORS), diffview.CURRENT_COLOR),
            "grep": (grepdialog.MATCH_COLOR, grepdialog.LINE_COLOR),
            "sync": dict(sync.ACTION_COLORS), "activity": activity.BASE_COLOR,
        })
    if not name:
        QApplication.setStyle(_system["style"])
        QApplication.setPalette(_system["palette"])
        graphview.COLORS.update(_system["graph"])
        lines, spans, current = _system["diff"]
        diffview.LINE_COLORS.update(lines)
        diffview.SPAN_COLORS.update(spans)
        diffview.CURRENT_COLOR = current
        grepdialog.MATCH_COLOR, grepdialog.LINE_COLOR = _system["grep"]
        sync.ACTION_COLORS.update(_system["sync"])
        activity.BASE_COLOR = _system["activity"]
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
        graphview.COLORS.update(t["graph"])
        d = t["diff"]
        diffview.LINE_COLORS.update({k: _rgba(d[k]) for k in ("change", "delete", "insert", "filler")})
        diffview.SPAN_COLORS.update({k: _rgba(d[k]) for k in ("left", "right")})
        diffview.CURRENT_COLOR = _rgba(d["current"])
        grepdialog.MATCH_COLOR, grepdialog.LINE_COLOR = _rgba(t["grep"]["match"]), _rgba(t["grep"]["line"])
        sync.ACTION_COLORS.update({k: QColor(v) for k, v in t["sync"].items()})
        activity.BASE_COLOR = QColor(t["activity"])
    _current = name
    return True

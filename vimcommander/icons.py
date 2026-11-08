"""File, folder and launch-bar icons in the Nerd Font style, coloured from the
Catppuccin palette (the config's "icons" option; "system" keeps the OS icons).

Each kind of file is a glyph and a palette colour name (`GLYPHS`), picked by
name (`NAMES`, `FOLDERS`) or extension (`EXTENSIONS`). The glyphs come from an
installed Nerd Font (`nerd_family()`, or the config's "icon_font"); without one,
`_shape()` draws a plain folder or page in the same colour. `GlyphEngine`
paints at any size and looks the colour up while painting, so the icons follow
a theme change without being made again.
"""

import os
from functools import cache

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QGuiApplication, QIcon,
                           QIconEngine, QPainter, QPainterPath, QPalette, QPixmap)

from .theme import LATTE, MOCHA

OPTIONS = {"icons": "auto", "font": ""}  # the config's "icons" and "icon_font" (app.apply_config)
THEME: dict | None = None  # the theme's palette (theme.apply), else Mocha / Latte by the background

# kind -> (Nerd Font code point, Catppuccin colour name)
GLYPHS = {
    "folder": (0xF07B, "blue"), "parent": (0xF07C, "overlay2"), "folder_git": (0xE5FB, "peach"),
    "folder_github": (0xE5FD, "lavender"), "folder_npm": (0xE5FA, "red"), "folder_config": (0xE5FC, "blue"),
    "home": (0xF015, "blue"), "desktop": (0xF108, "blue"), "downloads": (0xF019, "teal"),
    "documents": (0xF02D, "blue"), "music": (0xF001, "pink"), "pictures": (0xF03E, "yellow"),
    "videos": (0xF03D, "red"), "drive": (0xF0A0, "subtext0"),
    "server": (0xF233, "teal"), "trash": (0xF1F8, "overlay2"),
    "file": (0xF016, "overlay2"), "text": (0xF0F6, "subtext0"), "markdown": (0xF48A, "blue"),
    "pdf": (0xF1C1, "red"), "image": (0xF1C5, "yellow"), "audio": (0xF1C7, "pink"),
    "video": (0xF1C8, "red"), "word": (0xF1C2, "blue"), "excel": (0xF1C3, "green"),
    "powerpoint": (0xF1C4, "peach"), "table": (0xF0CE, "green"), "archive": (0xF410, "peach"),
    "python": (0xE606, "yellow"), "javascript": (0xE60C, "yellow"), "typescript": (0xE628, "blue"),
    "react": (0xE7BA, "sky"), "vue": (0xE6A0, "green"), "json": (0xE60B, "yellow"),
    "config": (0xE615, "overlay2"), "html": (0xE736, "peach"), "css": (0xE749, "blue"),
    "xml": (0xF05C0, "peach"), "c": (0xE61E, "blue"), "cpp": (0xE61D, "sapphire"),
    "header": (0xF0FD, "mauve"), "csharp": (0xF031B, "green"), "rust": (0xE7A8, "peach"),
    "go": (0xE627, "sapphire"), "shell": (0xE795, "green"), "lua": (0xE620, "blue"),
    "java": (0xE738, "red"), "kotlin": (0xE634, "mauve"), "swift": (0xE755, "peach"),
    "ruby": (0xE739, "red"), "php": (0xE73D, "lavender"), "haskell": (0xE777, "mauve"),
    "elixir": (0xE62D, "mauve"), "dart": (0xE798, "sky"), "vim": (0xE62B, "green"),
    "git": (0xE702, "peach"), "lock": (0xF023, "overlay1"), "key": (0xF023, "yellow"),
    "docker": (0xF308, "sapphire"), "make": (0xE779, "peach"), "cmake": (0xE794, "blue"),
    "database": (0xE706, "sky"), "program": (0xF2D0, "lavender"), "binary": (0xF471, "overlay2"),
    "link": (0xF481, "sky"), "model": (0xF1B2, "lavender"), "chip": (0xF2DB, "teal"),
    "memmap": (0xF0E8, "teal"), "notebook": (0xE678, "peach"), "plot": (0xF1FE, "green"),
    "license": (0xE60A, "yellow"), "mail": (0xF0E0, "sky"), "code": (0xF1C9, "green"),
    # programs in the launch bar
    "terminal": (0xF120, "green"), "vscode": (0xE70C, "blue"), "neovim": (0xE6AE, "green"),
    "firefox": (0xF269, "peach"), "chrome": (0xF268, "yellow"), "browser": (0xF0AC, "sky"),
    "explorer": (0xF07C, "blue"), "github": (0xF09B, "lavender"), "settings": (0xF013, "overlay2"),
}

_EXT = {
    "text": "txt log nfo rst org", "markdown": "md markdown mdx", "pdf": "pdf",
    "image": "png jpg jpeg gif bmp webp tif tiff ico svg heic avif psd xcf raw",
    "audio": "mp3 flac ogg opus wav m4a aac wma aiff", "video": "mp4 mkv avi mov webm wmv m4v mpg mpeg flv",
    "word": "doc docx odt rtf", "excel": "xls xlsx xlsm ods", "powerpoint": "ppt pptx odp",
    "table": "csv tsv", "archive": "zip 7z rar tar gz tgz bz2 xz zst lz4 cab iso jar whl deb rpm apk",
    "python": "py pyw pyi pyx", "javascript": "js mjs cjs", "typescript": "ts mts cts",
    "react": "jsx tsx", "vue": "vue", "json": "json jsonc json5", "config": "yaml yml toml ini cfg conf env props",
    "html": "html htm xhtml", "css": "css scss sass less", "xml": "xml xsd xsl ui qrc",
    "c": "c", "cpp": "cpp cc cxx c++ ino", "header": "h hpp hh hxx", "csharp": "cs csx",
    "rust": "rs", "go": "go", "shell": "sh bash zsh fish ps1 psm1 bat cmd", "lua": "lua",
    "java": "java", "kotlin": "kt kts", "swift": "swift", "ruby": "rb", "php": "php",
    "haskell": "hs", "elixir": "ex exs", "dart": "dart", "vim": "vim", "lock": "lock",
    "key": "pem key crt cer p12 pfx pub gpg asc", "cmake": "cmake", "make": "mk mak",
    "database": "db sqlite sqlite3 sql mdb", "program": "exe msi app appimage com",
    "binary": "bin hex elf dll so dylib o obj a lib pyc axf out", "link": "lnk url desktop",
    "model": "step stp stl 3mf iges igs ply glb gltf fbx", "chip": "ioc", "memmap": "map",
    "notebook": "ipynb", "plot": "raw", "mail": "eml msg mbox", "code": "asm s ld sv v vhd vhdl m r jl scala",
}
EXTENSIONS = {"." + ext: kind for kind, exts in _EXT.items() for ext in exts.split()}

NAMES = {  # whole names, lower case
    "makefile": "make", "gnumakefile": "make", "cmakelists.txt": "cmake", "dockerfile": "docker",
    "docker-compose.yml": "docker", "compose.yaml": "docker", ".gitignore": "git",
    ".gitattributes": "git", ".gitmodules": "git", ".gitconfig": "git", "license": "license",
    "license.txt": "license", "license.md": "license", "copying": "license", ".vimrc": "vim",
    "vimcommanderrc": "vim", "readme": "markdown", ".env": "config", ".editorconfig": "config",
    ".bashrc": "shell", ".zshrc": "shell", ".profile": "shell",
}
FOLDERS = {
    ".git": "folder_git", ".github": "folder_github", "node_modules": "folder_npm",
    ".config": "folder_config", ".vscode": "folder_config", ".idea": "folder_config",
    "desktop": "desktop", "downloads": "downloads", "documents": "documents", "music": "music",
    "pictures": "pictures", "videos": "videos", "$recycle.bin": "trash", ".trash": "trash",
}
PROGRAMS = {  # launch-bar programs, by file name without the extension
    "terminal": "wt windowsterminal powershell pwsh cmd bash zsh fish wsl konsole gnome-terminal alacritty "
                "wezterm wezterm-gui kitty xterm foot",
    "vscode": "code code-insiders codium", "neovim": "nvim nvim-qt neovide", "vim": "vim gvim",
    "firefox": "firefox", "chrome": "chrome chromium google-chrome", "browser": "msedge brave opera vivaldi",
    "explorer": "explorer nautilus dolphin thunar nemo pcmanfm finder", "git": "git gitk lazygit tig",
    "github": "githubdesktop", "text": "notepad notepad++ gedit kate mousepad",
    "settings": "control systemsettings gnome-control-center", "python": "python python3 pythonw",
}
PROGRAM_KINDS = {name: kind for kind, names in PROGRAMS.items() for name in names.split()}


def enabled() -> bool:
    return OPTIONS["icons"] != "system"


def kind_for(name: str, is_dir: bool = False) -> str:
    """The kind of icon for a file or folder name."""
    low = name.lower()
    if is_dir:
        return FOLDERS.get(low, "folder")
    if low in NAMES:
        return NAMES[low]
    return EXTENSIONS.get(os.path.splitext(low)[1], "file")


def program_kind(path: str) -> str:
    """The kind of icon for a program in the launch bar (by its name)."""
    name = os.path.splitext(os.path.basename(path.replace("\\", "/")))[0].lower()
    return PROGRAM_KINDS.get(name) or ("program" if name else "file")


def palette() -> dict:
    if THEME:
        return THEME
    app = QGuiApplication.instance()
    dark = app is not None and QGuiApplication.palette().color(QPalette.ColorRole.Base).lightnessF() < 0.5
    return MOCHA if dark else LATTE


def color(name: str) -> QColor:
    """A palette colour name, or a colour Qt knows ("#rrggbb", "red")."""
    return QColor(palette().get(name) or name)


@cache
def _families() -> tuple[str, ...]:
    return tuple(QFontDatabase.families())


@cache
def _has_glyphs(family: str) -> bool:
    metrics = QFontMetrics(QFont(family))
    return metrics.inFontUcs4(GLYPHS["folder"][0]) and metrics.inFontUcs4(GLYPHS["python"][0])


def nerd_family() -> str:
    """The font the glyphs come from: "icon_font", else an installed Nerd Font ("" if none)."""
    if OPTIONS["font"]:
        return OPTIONS["font"] if _has_glyphs(OPTIONS["font"]) else ""
    return _nerd_family()


@cache
def _nerd_family() -> str:
    names = [f for f in _families() if "nerd font" in f.lower() or f.endswith((" NF", " NFM", " NFP"))]
    # the symbols-only font first, then the proportional variants (bigger glyphs)
    names.sort(key=lambda f: ("symbols" not in f.lower(), "mono" in f.lower() or f.endswith(" NFM"), f))
    return next((f for f in names if _has_glyphs(f)), "")


_PATHS: dict = {}  # (family, text) -> (path, bounds) at a 100 px size


def _glyph_path(family: str, text: str):
    key = (family, text)
    if key not in _PATHS:
        font = QFont(family)
        font.setPixelSize(100)
        path = QPainterPath()
        path.addText(0, 0, font, text)
        _PATHS[key] = (path, path.boundingRect())
    return _PATHS[key]


def _shape(folder: bool) -> QPainterPath:
    """A plain folder or page (16 units square), for when there is no Nerd Font."""
    path = QPainterPath()
    if folder:
        path.moveTo(1, 3.5)
        path.lineTo(6, 3.5)
        path.lineTo(7.5, 5)
        path.lineTo(15, 5)
        path.lineTo(15, 13.5)
        path.lineTo(1, 13.5)
        path.closeSubpath()
    else:
        path.moveTo(3, 1)
        path.lineTo(10, 1)
        path.lineTo(13.5, 4.5)
        path.lineTo(13.5, 15)
        path.lineTo(3, 15)
        path.closeSubpath()
    return path


class GlyphEngine(QIconEngine):
    """Paints one glyph (or the fallback shape), fitted to the rectangle."""

    def __init__(self, text: str, color_name: str, folder: bool = False):
        super().__init__()
        self.text, self.color_name, self.folder = text, color_name, folder

    def clone(self):
        return GlyphEngine(self.text, self.color_name, self.folder)

    def pixmap(self, size, mode, state):
        # Qt's default leaves the new pixmap uncleared: buttons showed noise around the glyph
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRect(QPoint(0, 0), size), mode, state)
        painter.end()
        return pixmap

    def scaledPixmap(self, size, mode, state, scale):
        pixmap = self.pixmap(size * scale, mode, state)
        pixmap.setDevicePixelRatio(scale)
        return pixmap

    def paint(self, painter: QPainter, rect, mode, state):
        c = color(self.color_name)
        if mode == QIcon.Mode.Disabled:
            c.setAlphaF(0.4)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        box = QRectF(rect)
        family = nerd_family()
        if family:
            path, bounds = _glyph_path(family, self.text)
            fit = 0.9 * box.height()  # glyphs fill a square: wide ones by width, tall ones by height
            scale = fit / max(bounds.width(), bounds.height(), 1)
            painter.translate(box.center() - bounds.center() * scale)
            painter.scale(scale, scale)
            painter.drawPath(path)
        else:
            side = min(box.width(), box.height())
            painter.translate(box.center() - QPointF(side / 2, side / 2))
            painter.scale(side / 16, side / 16)
            painter.drawPath(_shape(self.folder))
        painter.restore()


_ICONS: dict = {}


def icon(kind: str) -> QIcon:
    """The icon of a kind (`GLYPHS`); unknown kinds are a plain file."""
    if kind not in _ICONS:
        code, colour = GLYPHS.get(kind, GLYPHS["file"])
        folder = kind.startswith("folder") or kind in FOLDERS.values() or kind in ("parent", "home")
        _ICONS[kind] = QIcon(GlyphEngine(chr(code), colour, folder))
        _GLYPH_OF[_ICONS[kind].cacheKey()] = (chr(code), folder)
    return _ICONS[kind]


_GLYPH_OF: dict = {}  # QIcon.cacheKey() -> (text, folder), for recolored()
_RECOLORED: dict = {}


def recolored(icon_: QIcon, c: QColor) -> QIcon:
    """One of our icons in another colour (the cursor row's text colour: a blue
    icon would vanish on a blue cursor); any other icon as it is."""
    glyph = _GLYPH_OF.get(icon_.cacheKey())
    if glyph is None:
        return icon_
    key = (glyph, c.rgba())
    if key not in _RECOLORED:
        _RECOLORED[key] = QIcon(GlyphEngine(glyph[0], c.name(QColor.NameFormat.HexArgb), glyph[1]))
    return _RECOLORED[key]


def for_name(name: str, is_dir: bool = False) -> QIcon:
    return icon(kind_for(name, is_dir))


def parse_spec(spec: str) -> QIcon | None:
    """A launch-bar button's own glyph: a character or a code point ("U+F120",
    "f120"), then optionally a colour ("U+F120 mauve", " #a6e3a1"). None if
    `spec` isn't one."""
    parts = spec.split()
    if not parts or len(parts) > 2:
        return None
    text = parts[0]
    if len(text) > 1:
        hexa = text[2:] if text[:2].lower() in ("u+", "0x") else text
        try:
            code = int(hexa, 16)
        except ValueError:
            return None
        if not 0x20 < code <= 0x10FFFF or len(hexa) < 4:
            return None
        text = chr(code)
    colour = parts[1] if len(parts) > 1 else "text"
    if colour not in MOCHA and not QColor.isValidColorName(colour):
        return None
    return QIcon(GlyphEngine(text, colour))

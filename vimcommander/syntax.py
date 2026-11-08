"""Syntax colouring for the quick view's text, with Pygments (optional; Qt-free).

`lexer_for()` picks a lexer by the file name (and its first line, for a
shebang); `spans()` lexes the text once and returns, per line, the
(start, length, category) runs to colour, in UTF-16 units as Qt counts them.
Categories are a few broad kinds (keyword, string, comment…) so every theme
needs only a short palette: `palette()` gives the one for the theme in use
(`THEME`, set by theme.apply) or a light / dark default.
"""

import importlib.util
import threading

CATEGORIES = ("keyword", "type", "function", "string", "number", "comment", "preproc",
              "constant", "decorator", "tag", "attribute", "heading", "error")

# Pygments is imported on first use (in the quick view's thread): it and its
# lexers take a second or two to import, which startup shouldn't pay.
_pyg = None  # the loaded names, see _load()
_LOCK = threading.Lock()
_CACHE: dict = {}  # token type -> category


def _load():
    global _pyg
    with _LOCK:
        if _pyg is None:
            from pygments.lexers import get_lexer_for_filename, guess_lexer
            from pygments.lexers.special import TextLexer
            from pygments.token import Comment, Error, Generic, Keyword, Name, Number, Operator, String
            from pygments.util import ClassNotFound
            # token types, most specific first, to a category (none: not coloured)
            rules = [
                (Comment.Preproc, "preproc"), (Comment.PreprocFile, "string"), (Comment, "comment"),
                (Keyword.Type, "type"), (Keyword.Constant, "constant"), (Keyword, "keyword"),
                (Name.Builtin.Pseudo, "constant"), (Name.Builtin, "type"), (Name.Class, "type"),
                (Name.Function, "function"), (Name.Decorator, "decorator"), (Name.Constant, "constant"),
                (Name.Tag, "tag"), (Name.Attribute, "attribute"), (Name.Label, "preproc"),
                (Name.Exception, "type"),
                (String.Doc, "comment"), (String, "string"), (Number, "number"),
                (Operator.Word, "keyword"), (Generic.Heading, "heading"), (Generic.Subheading, "heading"),
                (Generic.Deleted, "error"), (Generic.Inserted, "string"), (Error, "error"),
            ]
            _pyg = (get_lexer_for_filename, guess_lexer, TextLexer, ClassNotFound, rules)
    return _pyg


LIGHT = {"keyword": "#8839ef", "type": "#1e66f5", "function": "#1a7f8e", "string": "#40a02b",
         "number": "#d0631e", "comment": "#8c8fa1", "preproc": "#d20f39", "constant": "#d0631e",
         "decorator": "#df8e1d", "tag": "#1e66f5", "attribute": "#df8e1d", "heading": "#8839ef",
         "error": "#d20f39"}
DARK = {"keyword": "#c678dd", "type": "#61afef", "function": "#56b6c2", "string": "#98c379",
        "number": "#d19a66", "comment": "#7f848e", "preproc": "#e06c75", "constant": "#d19a66",
        "decorator": "#e5c07b", "tag": "#e06c75", "attribute": "#d19a66", "heading": "#61afef",
        "error": "#e06c75"}
THEME: dict | None = None  # the theme's own palette (theme.apply), else LIGHT / DARK by the background
ITALIC = {"comment"}
BOLD = {"heading"}


def available() -> bool:
    """Pygments is installed (checked without importing it)."""
    return importlib.util.find_spec("pygments") is not None


def palette(dark: bool) -> dict:
    return THEME or (DARK if dark else LIGHT)


def lexer_for(path: str, head: str = ""):
    """A Pygments lexer for this file, or None (no Pygments, plain text, unknown kind).
    The first call imports Pygments, and each language's lexer its module: slow, so
    the quick view calls it in a thread."""
    if not available():
        return None
    get_lexer_for_filename, guess_lexer, TextLexer, ClassNotFound, _rules = _load()
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    try:
        lexer = get_lexer_for_filename(name, head, stripnl=False, ensurenl=False)
    except ClassNotFound:
        if not head.startswith("#!"):
            return None
        try:  # a script without an extension: its shebang
            lexer = guess_lexer(head.split("\n", 1)[0], stripnl=False, ensurenl=False)
        except ClassNotFound:
            return None
    except Exception:  # a lexer that fails on this head
        return None
    return None if isinstance(lexer, TextLexer) else lexer


def category(tokentype) -> str | None:
    """The category of a Pygments token type (cached: there are few types)."""
    got = _CACHE.get(tokentype, False)
    if got is not False:
        return got
    found = None
    for kind, name in _load()[4]:
        if tokentype in kind:
            found = name
            break
    _CACHE[tokentype] = found
    return found


def _utf16(text: str) -> list[int] | None:
    """Qt's UTF-16 offset of each character, or None when they are the same
    (no character outside the Basic Multilingual Plane, the usual case)."""
    if text.isascii() or all(ord(c) <= 0xFFFF for c in text):
        return None
    offsets, n = [], 0
    for c in text:
        offsets.append(n)
        n += 2 if ord(c) > 0xFFFF else 1
    offsets.append(n)
    return offsets


def spans(text: str, lexer, stop=None) -> list[list[tuple[int, int, str]]]:
    """Per line of `text` (split at "\\n", as the text is shown): the coloured runs
    (start, length, category). `stop` (an Event) ends it early: None is returned."""
    lines = text.split("\n")
    out: list[list] = [[] for _ in lines]
    line, col = 0, 0
    for n, (_index, tokentype, value) in enumerate(lexer.get_tokens_unprocessed(text)):
        if stop is not None and n % 4096 == 0 and stop.is_set():
            return None
        cat = category(tokentype)
        parts = value.split("\n")
        for i, part in enumerate(parts):
            if i:
                line, col = line + 1, 0
            if part and cat and line < len(out):
                runs = out[line]
                if runs and runs[-1][2] == cat and runs[-1][0] + runs[-1][1] == col:
                    runs[-1] = (runs[-1][0], runs[-1][1] + len(part), cat)  # one run for "s" (3 tokens)
                else:
                    runs.append((col, len(part), cat))
            col += len(part)
    for i, runs in enumerate(out):  # positions in UTF-16 units for Qt
        if runs and (offsets := _utf16(lines[i])) is not None:
            out[i] = [(offsets[s], offsets[s + n] - offsets[s], c) for s, n, c in runs]
    return out

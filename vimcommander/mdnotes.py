"""Markdown notes for the quick view (Qt-free).

mdrender turns [[note|alias]] into a link with the "wikilink:" scheme (_link())
and ![[image.png]] into an image. resolve() finds the file a link points to: relative to the note, then from the vault root (the nearest folder
with .obsidian), then by name anywhere in the vault, like Obsidian does.
"""

import os
import re
import urllib.parse

from .graph import SKIP_DIRS

EXTENSIONS = (".md", ".markdown")
EMBED = re.compile(r"!\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
WIKI = re.compile(r"\[\[([^\]|\n]+?)(?:\|([^\]\n]*))?\]\]")
INDEX_LIMIT = 50000

_indexes: dict[str, dict[str, str]] = {}  # vault root -> {lower-case name: path}


def is_markdown(path: str) -> bool:
    return path.lower().endswith(EXTENSIONS)


def _link(target: str) -> str:
    return "wikilink:" + urllib.parse.quote(target.strip())


def vault_root(path: str) -> str:
    """The nearest folder above path holding .obsidian, else path's own folder."""
    folder = os.path.dirname(os.path.abspath(path))
    probe = folder
    while True:
        if os.path.isdir(os.path.join(probe, ".obsidian")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return folder
        probe = parent


def _index(root: str) -> dict[str, str]:
    if root in _indexes:
        return _indexes[root]
    names: dict[str, str] = {}
    count = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(files):
            names.setdefault(f.lower(), os.path.join(folder, f))
            count += 1
        if count > INDEX_LIMIT:
            break
    _indexes[root] = names
    return names


def forget(root: str | None = None) -> None:
    """Drop the cached name index (of one vault, or all)."""
    if root is None:
        _indexes.clear()
    else:
        _indexes.pop(root, None)


def resolve(target: str, note: str) -> str | None:
    """The existing file a link in note points to, or None. Accepts "name",
    "folder/name", "name.md", "image.png", with or without "#heading"."""
    target = urllib.parse.unquote(target).split("#")[0].split("^")[0].strip()
    if not target:
        return None
    candidates = [target] if os.path.splitext(target)[1] else [target + ".md", target]
    root = vault_root(note)
    for base in (os.path.dirname(os.path.abspath(note)), root):
        for name in candidates:
            path = os.path.normpath(os.path.join(base, name))
            if os.path.isfile(path):
                return path
    index = _index(root)
    for name in candidates:
        if path := index.get(os.path.basename(name).lower()):
            if os.path.isfile(path):
                return path
    forget(root)  # maybe the note is new: look again next time
    return None

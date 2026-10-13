""":grep: search inside the text files below a folder. Pure Python, no Qt.

Binary files (a NUL byte near the start), very big files and the folders in
graph.SKIP_DIRS (.git, node_modules, …) are skipped. File patterns are shell
patterns separated by spaces; a leading ! excludes ("*.py *.md !test_*").
"""

import fnmatch
import os
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

from .graph import SKIP_DIRS

MAX_SIZE = 20 * 1024 * 1024
LINE_LIMIT = 400  # longer lines are cut, around the first match


@dataclass
class Match:
    line: int  # 1-based
    text: str
    spans: list[tuple[int, int]]  # where the pattern matched in `text`


@dataclass
class FileMatches:
    path: str
    matches: list[Match] = field(default_factory=list)


@dataclass
class Stats:
    files: int = 0  # files searched
    matches: int = 0
    finished: bool = False


def compile_pattern(text: str, ignorecase: bool = True, smartcase: bool = True) -> re.Pattern:
    """A regex, or the literal text if it isn't a valid one; vim's smartcase rule."""
    flags = re.IGNORECASE if ignorecase and not (smartcase and any(c.isupper() for c in text)) else 0
    try:
        return re.compile(text, flags)
    except re.error:
        return re.compile(re.escape(text), flags)


def parse_globs(text: str) -> tuple[list[str], list[str]]:
    """'*.py *.md !test_*' -> (['*.py', '*.md'], ['test_*']), lowercased."""
    include, exclude = [], []
    for word in text.replace(",", " ").split():
        word = word.casefold()
        if word.startswith("!"):
            if word[1:]:
                exclude.append(word[1:])
        else:
            include.append(word)
    return include, exclude


def _wanted(name: str, include: list[str], exclude: list[str]) -> bool:
    name = name.casefold()
    if any(fnmatch.fnmatchcase(name, p) for p in exclude):
        return False
    return not include or any(fnmatch.fnmatchcase(name, p) for p in include)


def _cut(text: str, spans: list[tuple[int, int]]) -> tuple[str, list[tuple[int, int]]]:
    """Cut a long line to LINE_LIMIT characters around its first match."""
    if len(text) <= LINE_LIMIT:
        return text, spans
    start = max(0, spans[0][0] - LINE_LIMIT // 4) if spans else 0
    end = start + LINE_LIMIT
    shown = text[start:end]
    spans = [(max(a - start, 0), min(b - start, len(shown))) for a, b in spans if b > start and a < end]
    return ("…" if start else "") + shown + "…", [(a + (1 if start else 0), b + (1 if start else 0))
                                                     for a, b in spans]


def search_file(path: str, regex: re.Pattern, per_file: int = 500) -> list[Match]:
    """Matching lines of one text file ([] for binary or unreadable files)."""
    try:
        if os.path.getsize(path) > MAX_SIZE:
            return []
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    if b"\0" in data[:8192]:
        return []
    found = []
    for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines()):
        spans = [m.span() for m in regex.finditer(line) if m.end() > m.start()]
        if spans:
            text, spans = _cut(line, spans)
            found.append(Match(i + 1, text, spans))
            if len(found) >= per_file:
                break
    return found


def grep(root: str, regex: re.Pattern, globs: str = "", show_hidden: bool = False,
         stop: threading.Event | None = None, stats: Stats | None = None,
         max_matches: int = 10_000) -> Iterator[FileMatches]:
    """Each file below `root` with matching lines, in folder order."""
    include, exclude = parse_globs(globs)
    stats = stats if stats is not None else Stats()
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and (show_hidden or not d.startswith("."))
                         and not any(fnmatch.fnmatchcase(d.casefold(), p) for p in exclude))
        for name in sorted(files):
            if stop and stop.is_set():
                return
            if (not show_hidden and name.startswith(".")) or not _wanted(name, include, exclude):
                continue
            path = os.path.join(folder, name)
            stats.files += 1
            matches = search_file(path, regex)
            if matches:
                stats.matches += len(matches)
                yield FileMatches(path, matches)
                if stats.matches >= max_matches:
                    return
    stats.finished = True

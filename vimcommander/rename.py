"""New names for renaming several items at once. Pure Python, no Qt.

Patterns use {name} (name without extension), {ext} (extension with its dot)
and {n} (1, 2, 3...; {n:3} pads to 001). Substitutions are vim-style
s/regex/replacement/flags with flags g (every match) and i (ignore case).
increment() is vim's Ctrl-a / Ctrl-x for the number in a name, CASES its
gU / gu / g~.
"""

import re

FIELD = re.compile(r"\{(name|ext|n)(?::(\d+))?\}")
NUMBER = re.compile(r"[0-9]+")


def split_name(name: str, is_dir: bool = False) -> tuple[str, str]:
    if is_dir:
        return name, ""
    dot = name.rfind(".")
    return (name[:dot], name[dot:]) if dot > 0 else (name, "")


def format_name(pattern: str, name: str, n: int, is_dir: bool = False) -> str:
    stem, ext = split_name(name, is_dir)

    def field(m: re.Match) -> str:
        if m.group(1) == "n":
            return str(n).zfill(int(m.group(2) or 0))
        return stem if m.group(1) == "name" else ext

    return FIELD.sub(field, pattern)


def increment(name: str, delta: int, is_dir: bool = False) -> str | None:
    """Add `delta` to the last number in the name (not in the extension), like
    vim's Ctrl-a: img_0099.jpg + 1 = img_0100.jpg, v10 - 1 = v9. Leading zeros keep
    the width; a "-" before the number is a separator, not a sign, so it stops at 0.
    None if the name has no number."""
    stem, ext = split_name(name, is_dir)
    found = list(NUMBER.finditer(stem))
    if not found:
        return None
    m = found[-1]
    digits = m.group()
    value = str(max(0, int(digits) + delta))
    if len(digits) > 1 and digits[0] == "0":
        value = value.zfill(len(digits))
    return stem[:m.start()] + value + stem[m.end():] + ext


CASES = {"upper": str.upper, "lower": str.lower, "toggle_case": str.swapcase}


def parse_substitute(line: str) -> tuple[re.Pattern, str, int]:
    """Parse "s/old/new/g" into (regex, replacement, count). Raises ValueError."""
    if len(line) < 2 or line[0] != "s" or line[1].isalnum() or line[1] in " \\":
        raise ValueError("Usage: :s/PATTERN/REPLACEMENT/[gi]")
    delim = line[1]
    parts, current, i = [], "", 2
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line) and line[i + 1] == delim:
            current += delim
            i += 2
            continue
        if c == delim:
            parts.append(current)
            current = ""
        else:
            current += c
        i += 1
    parts.append(current)
    if len(parts) < 2:
        raise ValueError("Usage: :s/PATTERN/REPLACEMENT/[gi]")
    pattern, repl, flags = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
    if set(flags) - set("gi"):
        raise ValueError(f"Unknown flag in {flags!r}: use g and/or i")
    regex = re.compile(pattern, re.IGNORECASE if "i" in flags else 0)
    # vim's & is the whole match, \& a literal &; \1 already works in Python
    repl = re.sub(r"\\?&", lambda m: "&" if len(m.group()) == 2 else r"\g<0>", repl)
    return regex, repl, 0 if "g" in flags else 1


def check_names(pairs: list[tuple[str, str]]) -> str | None:
    """An error message if the new names are invalid or clash, else None."""
    new = [b for _, b in pairs]
    for name in new:
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            return f"Invalid name: {name!r}"
    seen = set()
    for name in new:
        if name.casefold() in seen:
            return f"Two items would be named {name}"
        seen.add(name.casefold())
    return None


# Bulk rename: names as numbered lines to edit in a text editor (like vidir).

BULK_HEADER = """\
# Edit the names after the numbers, then save and press Apply.
# Delete a line to move that item to the trash. Lines starting with # are ignored.
# Keep the number and the tab before each name; don't change the numbers.
"""


def bulk_text(names: list[str]) -> str:
    width = len(str(len(names)))
    return BULK_HEADER + "".join(f"{i:0{width}d}\t{name}\n" for i, name in enumerate(names, 1))


def parse_bulk(text: str, count: int) -> tuple[dict[int, str], set[int]]:
    """({index: new name}, indexes whose line was deleted), indexes counting from 0.
    Raises ValueError for a line it can't read or a repeated number."""
    names: dict[int, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"\s*(\d+)\t(.*)$", line) or re.match(r"\s*(\d+) +(.*)$", line)
        if not m:
            raise ValueError(f"Line {n} has no number: {line!r}")
        index = int(m.group(1)) - 1
        if not 0 <= index < count:
            raise ValueError(f"Line {n}: there is no item number {m.group(1)}")
        if index in names:
            raise ValueError(f"Line {n}: number {m.group(1)} appears twice")
        names[index] = m.group(2).rstrip("\r")
    return names, set(range(count)) - set(names)

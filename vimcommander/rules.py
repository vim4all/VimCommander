"""Folder rules, like Hazel: "in Downloads, PDFs older than a week go to
Documents/PDF". :rules works out what the rules would do and shows it first.
Pure Python, no Qt.

A rule in the config's "rules" list:
    {"name": "old PDFs",            optional, shown in the preview
     "folder": "~/Downloads",       where to look
     "match": "*.pdf *.djvu",       shell patterns, space separated (or a list)
     "older_than_days": 7,          optional: last changed at least this long ago
     "larger_than": "10M",          optional
     "subfolders": false,           optional: look in subfolders too
     "action": "move",              "move", "copy" or "trash"
     "to": "~/Documents/PDF"}       for move / copy; made if missing
A file is handled by the first rule that matches it.
"""

import fnmatch
import os
import time
from dataclasses import dataclass

from .cleanup import parse_size
from .graph import SKIP_DIRS

ACTIONS = ("move", "copy", "trash")


@dataclass
class Rule:
    name: str
    folder: str
    patterns: list[str]
    action: str
    to: str = ""
    older_than_days: float = 0
    larger_than: int = 0
    subfolders: bool = False


@dataclass
class Step:
    rule: str
    path: str
    action: str
    to: str  # destination folder ("" for trash)


def parse_rules(items) -> tuple[list[Rule], list[str]]:
    """Rules from the config, and an error message for each bad one."""
    rules, errors = [], []
    if not isinstance(items, list):
        return [], ["rules must be a list"] if items else []
    for i, d in enumerate(items, 1):
        if not isinstance(d, dict):
            errors.append(f"rule {i}: not an object")
            continue
        name = str(d.get("name") or f"rule {i}")
        folder = os.path.abspath(os.path.expanduser(str(d.get("folder") or "")))
        match = d.get("match") or "*"
        patterns = [str(p).casefold() for p in (match if isinstance(match, list) else str(match).split())]
        action = str(d.get("action") or "")
        to = os.path.abspath(os.path.expanduser(str(d.get("to")))) if d.get("to") else ""
        if not d.get("folder"):
            errors.append(f"{name}: no folder")
        elif action not in ACTIONS:
            errors.append(f"{name}: action must be one of {', '.join(ACTIONS)}")
        elif action != "trash" and not to:
            errors.append(f"{name}: no \"to\" folder")
        else:
            try:
                larger = parse_size(str(d["larger_than"])) if d.get("larger_than") else 0
                older = float(d.get("older_than_days") or 0)
            except (TypeError, ValueError):
                errors.append(f"{name}: bad older_than_days or larger_than")
                continue
            rules.append(Rule(name, folder, patterns, action, to, older, larger, bool(d.get("subfolders"))))
    return rules, errors


def _files(rule: Rule):
    if rule.subfolders:
        for folder, dirs, files in os.walk(rule.folder):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                       and os.path.normcase(os.path.join(folder, d)) != os.path.normcase(rule.to)]
            for f in files:
                yield os.path.join(folder, f)
    else:
        try:
            with os.scandir(rule.folder) as it:
                for e in it:
                    if e.is_file():
                        yield e.path
        except OSError:
            return


def plan(rules: list[Rule], now: float | None = None) -> list[Step]:
    """What the rules would do now; each file only by the first rule that matches it."""
    now = time.time() if now is None else now
    taken: set[str] = set()
    steps = []
    for rule in rules:
        for path in _files(rule):
            key = os.path.normcase(path)
            name = os.path.basename(path)
            if key in taken or name.startswith(".") or not any(
                    fnmatch.fnmatchcase(name.casefold(), p) for p in rule.patterns):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            if rule.older_than_days and now - st.st_mtime < rule.older_than_days * 86400:
                continue
            if rule.larger_than and st.st_size <= rule.larger_than:
                continue
            if rule.to and os.path.normcase(os.path.dirname(path)) == os.path.normcase(rule.to):
                continue  # already there
            taken.add(key)
            steps.append(Step(rule.name, path, rule.action, rule.to))
    return steps

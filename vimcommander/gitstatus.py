"""Git awareness: the status of the files in a folder, from `git status`. Pure
Python, no Qt; the main window runs it in a thread.

Kinds: "modified", "new" (untracked or added), "conflict", "ignored". A folder
gets the strongest kind of anything inside it (conflict > modified > new).
"""

import os
import shutil
import subprocess
import sys

_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
RANK = {"conflict": 3, "modified": 2, "new": 1}


def find_git(configured: str = "") -> str | None:
    """The git program: the configured one, or git on PATH."""
    if configured:
        return configured if os.path.exists(configured) else shutil.which(configured)
    return shutil.which("git")


def find_repo(path: str) -> str | None:
    """The top folder of the git work tree that `path` is in, or None."""
    path = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def kind_of(xy: str) -> str:
    if xy == "??":
        return "new"
    if xy == "!!":
        return "ignored"
    if "U" in xy or xy in ("AA", "DD"):
        return "conflict"
    if xy[0] == "A":
        return "new"
    return "modified"


def parse_status(output: str) -> tuple[str, dict[str, str]]:
    """(branch, {path relative to the repo with /: kind}) from
    `git status --porcelain=v1 -b -z`. Folders end with /."""
    branch = ""
    files: dict[str, str] = {}
    parts = output.split("\0")
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if not item:
            continue
        if item.startswith("## "):
            branch = parse_branch(item[3:])
            continue
        xy, path = item[:2], item[3:]
        if xy[0] in "RC":  # renamed / copied: the old path follows
            i += 1
        files[path] = kind_of(xy)
    return branch, files


def parse_branch(header: str) -> str:
    """'main...origin/main [ahead 2, behind 1]' -> 'main ↑2 ↓1'."""
    if header.startswith("No commits yet on "):
        return header[len("No commits yet on "):]
    if header.startswith("HEAD (no branch)"):
        return "detached"
    name, _, rest = header.partition("...")
    if not rest:
        name, _, rest = header.partition(" ")
    marks = []
    if "[" in rest:
        for part in rest[rest.index("[") + 1:].rstrip("]").split(","):
            word, _, n = part.strip().partition(" ")
            if word == "ahead":
                marks.append(f"↑{n}")
            elif word == "behind":
                marks.append(f"↓{n}")
    return " ".join([name.strip()] + marks)


def status(git: str, folder: str, timeout: float = 15.0) -> tuple[str, str, dict[str, str]] | None:
    """(repo, branch, files) for the repo `folder` is in, or None if it isn't in one."""
    repo = find_repo(folder)
    if repo is None:
        return None
    try:
        result = subprocess.run(
            [git, "-C", repo, "status", "--porcelain=v1", "-b", "-z", "--untracked-files=normal",
             "--ignored=matching"],
            capture_output=True, timeout=timeout, creationflags=_FLAGS)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    branch, files = parse_status(result.stdout.decode("utf-8", errors="replace"))
    return repo, branch, files


def folder_kinds(repo: str, files: dict[str, str], folder: str) -> dict[str, str]:
    """The kind of each item directly in `folder` (by name), from the repo's status."""
    try:
        here = os.path.relpath(os.path.abspath(folder), repo).replace(os.sep, "/")
    except ValueError:
        return {}
    here = "" if here == "." else here + "/"
    kinds: dict[str, str] = {}
    for path, kind in files.items():
        if kind == "ignored" and path.endswith("/") and here.startswith(path):
            return {"*": "ignored"}  # the whole folder is ignored
        if not path.startswith(here) or path == here:
            continue
        rest = path[len(here):]
        name, _, deeper = rest.partition("/")
        if kind == "ignored":
            if not deeper:  # the item itself is ignored, not just something inside it
                kinds[name] = "ignored"
            continue
        old = kinds.get(name)
        if old is None or old == "ignored" or RANK[kind] > RANK.get(old, 0):
            kinds[name] = kind
    return kinds


def changed_paths(repo: str, files: dict[str, str]) -> list[str]:
    """Full paths of everything modified, new or in conflict."""
    return [os.path.join(repo, *p.rstrip("/").split("/")) for p, k in sorted(files.items())
            if k != "ignored"]


def head_version(git: str, repo: str, path: str) -> bytes | None:
    """The file's content in the last commit, or None if it isn't there."""
    rel = os.path.relpath(path, repo).replace(os.sep, "/")
    try:
        result = subprocess.run([git, "-C", repo, "show", f"HEAD:{rel}"], capture_output=True,
                                timeout=15, creationflags=_FLAGS)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None

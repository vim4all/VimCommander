"""Comparing two folder trees, for :compare and :synchronize. Pure Python, no Qt.

Files count as the same when size and modification time match (within 2
seconds, for FAT drives); optionally, same-size files with different times are
compared byte by byte.
"""

import filecmp
import os
import posixpath
import threading
from dataclasses import dataclass

from . import remote
from .graph import SKIP_DIRS

TIME_SLACK = 2.0


@dataclass
class Diff:
    rel: str  # path relative to both roots
    status: str  # "left_only" | "right_only" | "left_newer" | "right_newer" | "different" | "same"
    is_dir: bool
    left: tuple[int, float] | None  # (size, mtime) on the left, None if missing
    right: tuple[int, float] | None
    action: str = ""  # ">" copy left to right, "<" right to left, "x" delete it, "" nothing


def join(root: str, rel: str) -> str:
    """A path below a local folder or an sftp:// URL."""
    if loc := remote.parse(root):
        return loc.url(posixpath.join(loc.path, rel.replace(os.sep, "/"))) if rel else root
    return os.path.join(root, rel)


def _entries(folder: str, show_hidden: bool) -> dict[str, tuple[str, bool, int, float]]:
    """normcase(name) -> (name, is_dir, size, mtime)."""
    if remote.is_remote(folder):
        try:
            return {name: (name, d, 0 if d else size, mtime) for name, d, size, mtime
                    in remote.REMOTE.list_dir(folder) if show_hidden or not name.startswith(".")}
        except OSError:
            return {}
    out = {}
    try:
        with os.scandir(folder) as it:
            for e in it:
                if not show_hidden and e.name.startswith("."):
                    continue
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                out[os.path.normcase(e.name)] = (e.name, is_dir, 0 if is_dir else st.st_size, st.st_mtime)
    except OSError:
        pass
    return out


def compare_trees(left: str, right: str, recursive: bool = True, content: bool = False,
                  show_hidden: bool = False, stop: threading.Event | None = None) -> list[Diff]:
    diffs: list[Diff] = []
    stack = [""]
    while stack:
        if stop and stop.is_set():
            break
        rel = stack.pop()
        a = _entries(join(left, rel), show_hidden)
        b = _entries(join(right, rel), show_hidden)
        if remote.is_remote(left) != remote.is_remote(right):  # match names by case on both sides
            a = {k.lower() if os.name == "nt" else k: v for k, v in a.items()}
            b = {k.lower() if os.name == "nt" else k: v for k, v in b.items()}
        for key in sorted(set(a) | set(b)):
            ea, eb = a.get(key), b.get(key)
            name = (ea or eb)[0]
            path = os.path.join(rel, name) if rel else name
            if name in SKIP_DIRS:
                continue
            if ea and not eb:
                diffs.append(Diff(path, "left_only", ea[1], ea[2:], None, ">"))
            elif eb and not ea:
                diffs.append(Diff(path, "right_only", eb[1], None, eb[2:], "<"))
            elif ea[1] and eb[1]:
                if recursive:
                    stack.append(path)
            elif ea[1] != eb[1]:
                diffs.append(Diff(path, "different", False, ea[2:], eb[2:], ""))
            else:
                diffs.append(_compare_files(left, right, path, ea, eb, content))
    diffs = [d for d in diffs if d.status != "same"]
    diffs.sort(key=lambda d: d.rel.casefold())
    return diffs


def _compare_files(left, right, path, ea, eb, content) -> Diff:
    (size_a, time_a), (size_b, time_b) = ea[2:], eb[2:]
    same_time = abs(time_a - time_b) <= TIME_SLACK
    if size_a == size_b and same_time:
        return Diff(path, "same", False, ea[2:], eb[2:])
    if size_a == size_b and content and not remote.is_remote(left) and not remote.is_remote(right):
        try:
            if filecmp.cmp(os.path.join(left, path), os.path.join(right, path), shallow=False):
                return Diff(path, "same", False, ea[2:], eb[2:])
        except OSError:
            pass
    if same_time:  # different size, same time: no way to tell which is newer
        return Diff(path, "different", False, ea[2:], eb[2:], "")
    if time_a > time_b:
        return Diff(path, "left_newer", False, ea[2:], eb[2:], ">")
    return Diff(path, "right_newer", False, ea[2:], eb[2:], "<")


def copy_plan(diffs: list[Diff], left: str, right: str) -> list[tuple[str, str]]:
    """(source, destination) pairs for the diffs' actions."""
    plan = []
    for d in diffs:
        if d.action == ">":
            plan.append((join(left, d.rel), join(right, d.rel)))
        elif d.action == "<":
            plan.append((join(right, d.rel), join(left, d.rel)))
    return plan


def delete_plan(diffs: list[Diff], left: str, right: str) -> list[str]:
    """The items to delete (action "x"): an item that only one side has, on that side."""
    return [join(left if d.left else right, d.rel) for d in diffs if d.action == "x"]


def mirror(diffs: list[Diff]) -> None:
    """Directions that make the right side a copy of the left: copy what is new or
    changed on the left, delete what only the right has."""
    for d in diffs:
        d.action = "x" if d.status == "right_only" else ">"

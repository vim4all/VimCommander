"""Finding space to free: :reclaim (caches, big old files) and :dupes (duplicate
files). Pure Python, no Qt. Both yield batches of (path, note) for a result list.
"""

import hashlib
import os
import threading
import time
from collections.abc import Iterator

from .graph import SKIP_DIRS, folder_size

# Folders that tools rebuild by themselves, so deleting them is safe (it only costs a rebuild).
CACHE_DIRS = {
    "node_modules": "npm packages", "__pycache__": "Python cache", ".venv": "Python virtualenv",
    "venv": "Python virtualenv", ".tox": "tox", ".pytest_cache": "pytest cache",
    ".mypy_cache": "mypy cache", ".ruff_cache": "ruff cache", ".gradle": "Gradle cache",
    ".next": "Next.js build", ".nuxt": "Nuxt build", ".parcel-cache": "Parcel cache",
    ".turbo": "Turborepo cache", "target": "Rust/Maven build", "build": "build output",
    "dist": "build output", "obj": ".NET build",
    ".dart_tool": "Dart cache", "Pods": "CocoaPods", "DerivedData": "Xcode build",
    ".angular": "Angular cache", ".svelte-kit": "SvelteKit build", "coverage": "test coverage",
}
# "build" or "dist" alone could be anything; they count only next to a project file.
NEEDS_PROJECT = {"build", "dist", "target", "obj", "coverage"}
PROJECT_FILES = {"package.json", "setup.py", "pyproject.toml", "Cargo.toml", "pom.xml",
                 "build.gradle", "build.gradle.kts", "CMakeLists.txt", "Makefile", "go.mod"}
PROJECT_EXTS = (".csproj", ".sln", ".fsproj", ".vbproj")

UNITS = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}


def parse_size(text: str) -> int:
    """'100M' -> 104857600; '1.5g', '20k', '4096' work too. Raises ValueError."""
    text = text.strip().lower().rstrip("b")
    if text and text[-1] in UNITS:
        return int(float(text[:-1]) * UNITS[text[-1]])
    return int(float(text))


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _is_project(names: list[str]) -> bool:
    return any(n in PROJECT_FILES or n.endswith(PROJECT_EXTS) for n in names)


def reclaim(root: str, show_hidden: bool = True, stop: threading.Event | None = None,
            big: int = 100 * 1024 ** 2, old_days: int = 180,
            budget: float = 60.0) -> Iterator[list[tuple[str, str]]]:
    """Rebuildable cache folders and big files not changed for `old_days`, biggest first."""
    found: list[tuple[int, str, str]] = []
    cutoff = time.time() - old_days * 86400
    deadline = time.monotonic() + budget
    for folder, dirs, files in os.walk(root):
        if (stop and stop.is_set()) or time.monotonic() > deadline:
            break
        project = _is_project(files)
        keep = []
        for d in dirs:
            path = os.path.join(folder, d)
            what = CACHE_DIRS.get(d)
            if what and (d not in NEEDS_PROJECT or project) and not os.path.islink(path):
                size, _, _ = folder_size(path, stop)
                if size:
                    found.append((size, path, f"{what}, {_human(size)}"))
            elif d not in SKIP_DIRS and (show_hidden or not d.startswith(".")):
                keep.append(d)
        dirs[:] = keep
        for f in files:
            path = os.path.join(folder, f)
            try:
                st = os.stat(path, follow_symlinks=False)
            except OSError:
                continue
            if st.st_size >= big and st.st_mtime < cutoff:
                days = int((time.time() - st.st_mtime) // 86400)
                found.append((st.st_size, path, f"big, unchanged for {days} days, {_human(st.st_size)}"))
    found.sort(reverse=True)
    yield [(path, note) for _, path, note in found]


def _hash(path: str, limit: int | None = None) -> bytes | None:
    h = hashlib.blake2b(digest_size=20)
    try:
        with open(path, "rb") as f:
            if limit:
                h.update(f.read(limit))
            else:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
    except OSError:
        return None
    return h.digest()


def duplicates(root: str, show_hidden: bool = False, stop: threading.Event | None = None,
               min_size: int = 1) -> list[list[str]]:
    """Sets of files with the same content (same size, then same hash of the first
    64 KB, then of everything), the sets that waste the most space first."""
    by_size: dict[int, list[str]] = {}
    for folder, dirs, files in os.walk(root):
        if stop and stop.is_set():
            return []
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (show_hidden or not d.startswith("."))]
        for f in files:
            if not show_hidden and f.startswith("."):
                continue
            path = os.path.join(folder, f)
            try:
                st = os.stat(path, follow_symlinks=False)
            except OSError:
                continue
            if st.st_size >= min_size and os.path.isfile(path) and not os.path.islink(path):
                by_size.setdefault(st.st_size, []).append(path)
    sets: list[tuple[int, list[str]]] = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        if stop and stop.is_set():
            return []
        candidates = [g for g in _group(paths, lambda p: _hash(p, 64 * 1024)) if len(g) > 1]
        if size > 64 * 1024:  # the same start: compare everything
            candidates = [g2 for g in candidates for g2 in _group(g, _hash) if len(g2) > 1]
        for g in candidates:
            sets.append((size * (len(g) - 1), sorted(g)))
    sets.sort(key=lambda s: -s[0])
    return [g for _, g in sets]


def _group(paths: list[str], key) -> list[list[str]]:
    groups: dict[bytes, list[str]] = {}
    for p in paths:
        if (k := key(p)) is not None:
            groups.setdefault(k, []).append(p)
    return list(groups.values())


def dupes(root: str, show_hidden: bool = False, stop: threading.Event | None = None,
          min_size: int = 1) -> Iterator[list[tuple[str, str, int]]]:
    """duplicates() as (path, note, set number) for a result list."""
    batch = []
    for n, group in enumerate(duplicates(root, show_hidden, stop, min_size), 1):
        try:
            size = os.path.getsize(group[0])
        except OSError:
            continue
        for p in group:
            batch.append((p, f"set {n}: {len(group)} copies of {_human(size)}", n))
    yield batch


def keep_one(groups: dict[str, int], newest: bool = True) -> list[str]:
    """The paths to tag in duplicate sets: all but the newest (or oldest) of each set."""
    sets: dict[int, list[str]] = {}
    for path, n in groups.items():
        sets.setdefault(n, []).append(path)
    extra = []
    for paths in sets.values():
        def mtime(p: str) -> float:
            try:
                return os.path.getmtime(p)
            except OSError:
                return 0.0
        keep = (max if newest else min)(paths, key=mtime)
        extra += [p for p in paths if p != keep]
    return extra

"""What is in the system trash, putting items back and deleting them for good:
the freedesktop.org trash (Linux), the Windows Recycle Bin ($I / $R files) and
the macOS ~/.Trash (no record of where items came from). Pure Python, no Qt.
"""

import os
import shutil
import stat
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote


@dataclass
class Item:
    path: str  # where it is in the trash
    original: str  # where it was before ("" when the trash doesn't say)
    deleted: float  # when it went to the trash (0 when unknown)

    @property
    def name(self) -> str:
        return os.path.basename(self.original or self.path)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.original) if self.original else ""

    @property
    def is_dir(self) -> bool:
        return os.path.isdir(self.path) and not os.path.islink(self.path)

    def size(self) -> int | None:
        """The file's size, or None for a folder (adding those up can take long)."""
        try:
            st = os.lstat(self.path)
        except OSError:
            return None
        return None if stat.S_ISDIR(st.st_mode) else st.st_size


def info_path(trash_path: str) -> str:
    """The file that records where a trashed item came from."""
    folder, name = os.path.split(trash_path)
    if name.startswith("$R"):  # Windows Recycle Bin keeps the details in $I...
        return os.path.join(folder, "$I" + name[2:])
    return os.path.join(os.path.dirname(folder), "info", name + ".trashinfo")  # freedesktop.org


def restore(trash_path: str, original: str) -> None:
    """Move an item from the trash back to `original` and drop its record."""
    if not trash_path or not os.path.lexists(trash_path):
        raise OSError(f"'{os.path.basename(original)}' is no longer in the trash")
    if os.path.lexists(original):
        raise OSError(f"'{os.path.basename(original)}' exists again")
    os.makedirs(os.path.dirname(original), exist_ok=True)
    shutil.move(trash_path, original)
    _remove(info_path(trash_path))


def purge(trash_path: str) -> None:
    """Delete an item in the trash for good, with its record."""
    if os.path.isdir(trash_path) and not os.path.islink(trash_path):
        shutil.rmtree(trash_path)
    elif os.path.lexists(trash_path):
        os.remove(trash_path)
    _remove(info_path(trash_path))


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def items(environ=None, platform: str = sys.platform) -> list[Item]:
    """Everything in the current user's trash, the most recently trashed first."""
    environ = os.environ if environ is None else environ
    if platform == "win32":
        found = [i for b in recycle_bins() for i in read_recycle_bin(b)]
    elif platform == "darwin":
        found = read_mac_trash(os.path.join(os.path.expanduser("~"), ".Trash"))
    else:
        found = [i for d in trash_dirs(environ) for i in read_trash_dir(d)]
    return sorted(found, key=lambda i: -i.deleted)


# freedesktop.org (Linux, BSD)

def home_trash(environ) -> str:
    data = environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "Trash")


def mount_points(mounts: str = "/proc/mounts") -> list[str]:
    try:
        with open(mounts, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    # spaces in mount points are written as \040
    return [line.split()[1].replace("\\040", " ") for line in lines if len(line.split()) > 1]


def trash_dirs(environ, mounts: list[str] | None = None) -> list[str]:
    """The home trash and the trash folders at the top of other drives
    ($top/.Trash/$uid and $top/.Trash-$uid), those that exist."""
    uid = os.getuid() if hasattr(os, "getuid") else 0
    dirs = [home_trash(environ)]
    for top in mount_points() if mounts is None else mounts:
        dirs += [os.path.join(top, ".Trash", str(uid)), os.path.join(top, f".Trash-{uid}")]
    out: list[str] = []
    for d in dirs:
        if d not in out and os.path.isdir(os.path.join(d, "info")):
            out.append(d)
    return out


def parse_trashinfo(text: str) -> tuple[str, float]:
    """(Path, DeletionDate as a timestamp) from a .trashinfo file."""
    path, when = "", 0.0
    in_section = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_section = line == "[Trash Info]"
        elif in_section and line.startswith("Path="):
            path = unquote(line[5:])
        elif in_section and line.startswith("DeletionDate="):
            try:
                when = datetime.fromisoformat(line[13:]).timestamp()
            except ValueError:
                pass
    return path, when


def read_trash_dir(trash_dir: str) -> list[Item]:
    """The items of one freedesktop.org trash folder (files/ + info/)."""
    info_dir = os.path.join(trash_dir, "info")
    # a relative Path= is relative to the drive's top: the folder holding .Trash-$uid or .Trash
    top = os.path.dirname(trash_dir)
    if os.path.basename(top) == ".Trash":
        top = os.path.dirname(top)
    out = []
    try:
        names = os.listdir(info_dir)
    except OSError:
        return []
    for info in names:
        if not info.endswith(".trashinfo"):
            continue
        path = os.path.join(trash_dir, "files", info[:-len(".trashinfo")])
        if not os.path.lexists(path):
            continue
        try:
            with open(os.path.join(info_dir, info), encoding="utf-8", errors="replace") as f:
                original, when = parse_trashinfo(f.read())
        except OSError:
            continue
        if original and not os.path.isabs(original):
            original = os.path.join(top, original)
        out.append(Item(path, os.path.normpath(original) if original else "", when))
    return out


# Windows Recycle Bin

FILETIME_EPOCH = 116444736000000000  # 1601-01-01 to 1970-01-01 in 100 ns steps


def parse_index(data: bytes) -> tuple[str, float]:
    """(original path, deletion time) from a $I file: version 1 (Vista to 8.1) has
    the path in a fixed 520-byte field, version 2 (Windows 10+) a length before it."""
    if len(data) < 24:
        raise ValueError("too short")
    version, _, filetime = struct.unpack_from("<qqq", data)
    if version == 1:
        raw = data[24:24 + 520]
    elif version == 2:
        (chars,) = struct.unpack_from("<i", data, 24)
        raw = data[28:28 + 2 * chars]
    else:
        raise ValueError(f"unknown version {version}")
    path = raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]
    when = (filetime - FILETIME_EPOCH) / 1e7 if filetime > FILETIME_EPOCH else 0.0
    return path, when


def make_index(path: str, when: float, size: int = 0) -> bytes:
    """A version 2 $I file (for tests)."""
    raw = (path + "\0").encode("utf-16-le")
    filetime = int(when * 1e7) + FILETIME_EPOCH
    return struct.pack("<qqqi", 2, size, filetime, len(raw) // 2) + raw


def recycle_bins() -> list[str]:
    """The current user's Recycle Bin folder on every drive (the folders of
    other users can't be read, so they are left out)."""
    out = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\$Recycle.Bin"
        try:
            sids = os.listdir(root)
        except OSError:
            continue
        for sid in sids:
            folder = os.path.join(root, sid)
            try:
                os.listdir(folder)
            except OSError:
                continue
            out.append(folder)
    return out


def read_recycle_bin(folder: str) -> list[Item]:
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not name.startswith("$I"):
            continue
        path = os.path.join(folder, "$R" + name[2:])
        if not os.path.lexists(path):
            continue
        try:
            with open(os.path.join(folder, name), "rb") as f:
                original, when = parse_index(f.read())
        except (OSError, ValueError, struct.error):
            continue
        out.append(Item(path, original, when))
    return out


# macOS

def read_mac_trash(folder: str) -> list[Item]:
    """~/.Trash: where items came from is in Finder's .DS_Store, so it's unknown here."""
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.name != ".DS_Store":
                    try:
                        when = e.stat(follow_symlinks=False).st_ctime
                    except OSError:
                        when = time.time()
                    out.append(Item(e.path, "", when))
    except OSError:
        return []
    return out

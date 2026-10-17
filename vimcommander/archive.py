"""Read-only browsing of zip files as if they were folders.

A path such as C:\\x\\photos.zip\\2024\\a.jpg points inside photos.zip. Such
virtual paths can be listed, read and extracted. The only change allowed is
deleting members (remove()), which rewrites the zip.
"""

import os
import shutil
import tempfile
import time
import zipfile

EXTENSIONS = (".zip", ".jar", ".whl")


def is_archive(path: str) -> bool:
    return path.lower().endswith(EXTENSIONS) and os.path.isfile(path)


def locate(path: str) -> tuple[str, str] | None:
    """(archive file, inner path with '/' separators) if `path` is an archive or
    inside one, else None. The inner path of the archive itself is ""."""
    p = os.path.abspath(path)
    inner: list[str] = []
    while True:
        if p.lower().endswith(EXTENSIONS) and os.path.isfile(p):
            return p, "/".join(reversed(inner))
        if os.path.isdir(p) or os.path.isfile(p):
            return None  # a real folder or file: nothing below it is virtual
        parent, name = os.path.split(p)
        if parent == p or not name:
            return None
        inner.append(name)
        p = parent


def inside(path: str) -> tuple[str, str] | None:
    """Like locate(), but only for paths strictly inside an archive (not the zip file itself)."""
    loc = locate(path)
    return loc if loc and loc[1] else None


def _clean(name: str) -> str | None:
    """A member name with '/' separators, or None if it is unsafe (absolute, '..')."""
    name = name.replace("\\", "/")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or ".." in parts or ":" in parts[0] or name.startswith("/"):
        return None
    return "/".join(parts) + ("/" if name.endswith("/") else "")


def _mtime(info: zipfile.ZipInfo) -> float:
    try:
        return time.mktime(info.date_time + (0, 0, -1))
    except (OverflowError, ValueError):
        return 0.0


def list_dir(archive: str, inner: str) -> list[tuple[str, bool, int, float]]:
    """(name, is_dir, size, mtime) of the items directly inside `inner`.

    Raises OSError if `inner` is not a folder in the archive."""
    prefix = inner.strip("/") + "/" if inner.strip("/") else ""
    items: dict[str, tuple[str, bool, int, float]] = {}
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                name = _clean(info.filename)
                if name is None or not name.startswith(prefix) or name == prefix:
                    continue
                head, sep, tail = name[len(prefix):].partition("/")
                if sep:  # a folder, or something inside one
                    mtime = _mtime(info) if not tail else items.get(head, ("", True, 0, 0.0))[3]
                    items[head] = (head, True, 0, mtime)
                elif head not in items:
                    items[head] = (head, False, info.file_size, _mtime(info))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as e:
        raise OSError(f"{os.path.basename(archive)}: {e}") from e
    if prefix and not items and not _has_dir(archive, prefix):
        raise FileNotFoundError(f"{inner} is not a folder in {os.path.basename(archive)}")
    return sorted(items.values())


def _has_dir(archive: str, prefix: str) -> bool:
    with zipfile.ZipFile(archive) as zf:
        return any((_clean(n) or "").startswith(prefix) for n in zf.namelist())


def members(archive: str, inner: str) -> list[tuple[zipfile.ZipInfo, str]]:
    """The file and folder entries at or below `inner`, each with its path
    relative to `inner` ("" for `inner` itself when it is a file)."""
    inner = inner.strip("/")
    out = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = _clean(info.filename)
            if name is None:
                continue
            bare = name.rstrip("/")
            if bare == inner:  # the file itself, or the folder's own entry
                out.append((info, ""))
            elif not inner or bare.startswith(inner + "/"):
                rel = bare[len(inner) + 1:] if inner else bare
                out.append((info, rel + ("/" if name.endswith("/") else "")))
    return out


def size_of(path: str) -> int:
    loc = locate(path)
    if not loc:
        return 0
    return sum(info.file_size for info, rel in members(*loc) if not rel.endswith("/"))


def read(path: str, limit: int) -> bytes:
    """Up to `limit` bytes of a file inside an archive."""
    archive, inner = locate(path) or ("", "")
    with zipfile.ZipFile(archive) as zf, zf.open(inner) as f:
        return f.read(limit)


def extract(path: str, dest: str, on_chunk=None) -> None:
    """Extract the file or folder at virtual `path` to `dest`.

    `on_chunk(nbytes)` is called as data is written; it may raise to stop."""
    archive, inner = locate(path) or ("", "")
    entries = members(archive, inner)
    if not entries:
        raise FileNotFoundError(f"{os.path.basename(path)} not found in {os.path.basename(archive)}")
    single_file = len(entries) == 1 and entries[0][1] == "" and not entries[0][0].is_dir()
    if not single_file:
        os.makedirs(dest, exist_ok=True)  # so an empty folder is still created
    with zipfile.ZipFile(archive) as zf:
        for info, rel in entries:
            target = os.path.join(dest, *rel.rstrip("/").split("/")) if rel else dest
            if rel.endswith("/") or (not rel and info.is_dir()):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
                    if on_chunk:
                        on_chunk(len(chunk))
            mtime = _mtime(info)
            if mtime:
                os.utime(target, (mtime, mtime))


def extract_to_temp(path: str) -> str:
    """Extract a file from an archive into a new temporary folder (for opening it)."""
    folder = tempfile.mkdtemp(prefix="vimcommander-")
    dest = os.path.join(folder, os.path.basename(path))
    try:
        extract(path, dest)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return dest


def remove(archive: str, inners: list[str]) -> None:
    """Delete the files or folders at `inners` (and everything below them) from
    the zip, by writing a new zip next to it and replacing the old one."""
    targets = [i.strip("/") for i in inners if i.strip("/")]
    if not targets:
        return

    def under(name: str | None, target: str) -> bool:
        bare = (name or "").rstrip("/")
        return bare == target or bare.startswith(target + "/")

    def gone(name: str | None) -> bool:
        return any(under(name, t) for t in targets)

    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        for t in targets:
            if not any(under(_clean(i.filename), t) for i in infos):
                raise FileNotFoundError(f"{t} not found in {os.path.basename(archive)}")
        fd, tmp = tempfile.mkstemp(suffix=".vctmp", dir=os.path.dirname(archive))
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w") as out:
                out.comment = zf.comment
                for info in infos:
                    if gone(_clean(info.filename)):
                        continue
                    with zf.open(info) as src, out.open(info, "w", force_zip64=info.file_size > 0x7FFFFFFF) as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
        except BaseException:
            os.remove(tmp)
            raise
    try:
        shutil.copymode(archive, tmp)
    except OSError:
        pass
    os.replace(tmp, archive)

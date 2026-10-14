"""File operations: copy, move, trash, delete, undo. Run off the GUI thread via Job.

A Job reports progress, can be cancelled, asks the GUI what to do when a name
already exists (the `conflict` signal, answered with answer()), and records
what it did in `undo` so the operation can be reversed.
"""

import errno
import os
import posixpath
import shutil
import stat
import threading
import time
import zipfile

from PySide6.QtCore import QFile, QObject, Signal

from . import archive, remote

CHUNK = 1024 * 1024


class Cancelled(Exception):
    pass


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _inside(path: str, folder: str) -> bool:
    path = os.path.normcase(os.path.abspath(path))
    folder = os.path.normcase(os.path.abspath(folder))
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives on Windows
        return False


def unique_dest(target_dir: str, name: str) -> str:
    """Destination path in target_dir that does not exist yet: 'a.txt', 'a (1).txt', ..."""
    dest = os.path.join(target_dir, name)
    if not os.path.lexists(dest):
        return dest
    stem, ext = os.path.splitext(name)
    if os.path.isdir(dest):
        stem, ext = name, ""
    i = 1
    while os.path.lexists(dest := os.path.join(target_dir, f"{stem} ({i}){ext}")):
        i += 1
    return dest


def tree_size(path: str, stop: threading.Event | None = None) -> tuple[int, int]:
    """(bytes, files) of a file or folder tree, not following links."""
    if archive.inside(path):
        return archive.size_of(path), 1
    try:
        st = os.lstat(path)
    except OSError:
        return 0, 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size, 1
    total, files = 0, 0
    stack = [path]
    while stack:
        if stop and stop.is_set():
            break
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return total, files


def _make_writable(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)  # read-only files can't be deleted on Windows
    func(path)


def delete_path(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, onerror=_make_writable)
    else:
        try:
            os.remove(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)


def move_to_trash(path: str) -> str:
    """Move to the system trash. Returns the item's path in the trash, or ""."""
    f = QFile(path)  # the instance form reports where the item went, via fileName()
    if not f.moveToTrash():
        raise OSError(f"could not move '{os.path.basename(path)}' to trash")
    return f.fileName() if f.fileName() != path else ""


def restore_from_trash(trash_path: str, original: str) -> None:
    if not trash_path or not os.path.lexists(trash_path):
        raise OSError(f"'{os.path.basename(original)}' is no longer in the trash")
    if os.path.lexists(original):
        raise OSError(f"'{os.path.basename(original)}' exists again")
    os.makedirs(os.path.dirname(original), exist_ok=True)
    shutil.move(trash_path, original)
    folder, name = os.path.split(trash_path)
    if name.startswith("$R"):  # Windows Recycle Bin keeps the details in $I...
        info = os.path.join(folder, "$I" + name[2:])
    else:  # freedesktop.org trash: files/NAME + info/NAME.trashinfo
        info = os.path.join(os.path.dirname(folder), "info", name + ".trashinfo")
    try:
        os.remove(info)
    except OSError:
        pass


def _cross_device(e: OSError) -> bool:
    return e.errno == errno.EXDEV or getattr(e, "winerror", None) == 17


OPERATIONS = {  # op: (verb while running, verb when done)
    "copy": ("Copying", "Copied"),
    "move": ("Moving", "Moved"),
    "trash": ("Trashing", "Trashed"),
    "delete": ("Deleting", "Deleted"),
    "undo": ("Undoing", "Undid"),
    "redo": ("Redoing", "Redid"),
    "sync": ("Synchronizing", "Synchronized"),
    "zip": ("Zipping", "Zipped"),
}


class Job(QObject):
    """Runs one operation over a list of paths in a background thread.

    For "undo", `sources` is a list of undo records instead of paths.
    """

    progress = Signal(object, object, int, int, str)  # bytes done, bytes total, items done, items total, name
    conflict = Signal(str, str)  # source, existing destination
    finished = Signal(str, list)  # summary message, error messages

    def __init__(self, op: str, sources: list, target_dir: str | None = None):
        super().__init__()
        self.op, self.sources, self.target_dir = op, sources, target_dir or ""
        self.undo: list[tuple] = []  # ("created", path) | ("moved", src, dest) | ("trashed", path, where)
        self.cancelled = False
        self.started = False
        self._stop = threading.Event()
        self._running = threading.Event()  # cleared while paused
        self._running.set()
        self._answered = threading.Event()
        self._answer = ""
        self._policy = ""  # an answer given with "apply to all"
        self.bytes_done = self.bytes_total = 0
        self.items_done = 0
        self._name = ""
        self._last_emit = 0.0
        self._zip_names: set[str] = set()  # names already in the zip being made
        self._channels: dict[str, object] = {}  # remote host key -> this job's SFTP channel

    @property
    def description(self) -> str:
        verb = OPERATIONS[self.op][0]
        where = f" to {self.target_dir}" if self.target_dir else ""
        return f"{verb} {len(self.sources)} item(s){where}…"

    def start(self) -> None:
        self.started = True
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancelled = True
        self._stop.set()
        self._answered.set()
        self._running.set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def pause(self) -> None:
        """Stop between two chunks until resume() (the files stay open)."""
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def answer(self, choice: str, apply_all: bool = False) -> None:
        """Reply to `conflict`: "overwrite", "skip", "rename" or "cancel"."""
        self._answer = choice
        if apply_all:
            self._policy = choice
        self._answered.set()

    # Thread side

    def _check(self) -> None:
        while not self._running.wait(0.2):  # paused
            if self._stop.is_set():
                break
        if self._stop.is_set():
            raise Cancelled

    def _advance(self, nbytes: int = 0, force: bool = False) -> None:
        self._check()
        self.bytes_done += nbytes
        now = time.monotonic()
        if force or now - self._last_emit > 0.1:
            self._last_emit = now
            self.progress.emit(self.bytes_done, self.bytes_total, self.items_done,
                               len(self.sources), self._name)

    def _run(self) -> None:
        errors: list[str] = []
        ok = 0
        try:
            if self.op in ("copy", "move"):
                self.bytes_total = sum(self._size_to_copy(s) for s in self.sources)
            elif self.op == "sync":
                self.bytes_total = sum(self._tree_size(src) for src, _ in self.sources)
            elif self.op == "zip":
                self.bytes_total = sum(tree_size(src, self._stop)[0] for src in self.sources)
            work = getattr(self, "_" + self.op)
            for src in self.sources:
                self._name = remote.basename(src) if isinstance(src, str) else remote.basename(src[1])
                self._advance(force=True)
                try:
                    if work(src) is not False:
                        ok += 1
                except Cancelled:
                    raise
                except (OSError, shutil.Error) as e:
                    errors.append(f"{self._name}: {e.strerror or e}" if isinstance(e, OSError)
                                  else f"{self._name}: {e}")
                self.items_done += 1
        except Cancelled:
            if self.op == "zip" and self.undo:  # no half-made zip
                try:
                    os.remove(self.target_dir)
                except OSError:
                    pass
                self.undo.clear()
            self.finished.emit(f"Cancelled after {ok} item(s)", errors)
            return
        finally:
            for channel in self._channels.values():
                try:
                    channel.close()
                except Exception:
                    pass
        self.finished.emit(f"{OPERATIONS[self.op][1]} {ok} item(s)", errors)

    # Remote locations (sftp:// URLs)

    def _channel(self, loc: "remote.Location", role: str = ""):
        key = loc.key + role
        if key not in self._channels:
            self._channels[key] = remote.REMOTE.connection(loc).open_sftp()
        return self._channels[key]

    def _endpoint(self, path: str, dest: bool = False) -> tuple["remote.Endpoint", str]:
        """(file system, path in it) for a local path or a remote URL. FTP can't read
        and write on one connection at once, so the destination gets its own."""
        loc = remote.parse(path)
        if loc is None:
            return remote.Endpoint(None), path
        return remote.Endpoint(self._channel(loc, " dest" if dest and loc.is_ftp else "")), loc.path

    def _tree_size(self, path: str) -> int:
        loc = remote.parse(path)
        if loc is None:
            return tree_size(path, self._stop)[0]
        return remote.walk_size(self._channel(loc), loc.path, self._stop)[0]

    def _transfer(self, src: str, move: bool):
        """Copy or move between a local folder and a remote one (or two remote ones)."""
        if archive.inside(src):
            raise OSError("copy it out of the archive first")
        src_fs, src_path = self._endpoint(src)
        dst_fs, dst_dir = self._endpoint(self.target_dir, dest=True)
        name = remote.basename(src.rstrip("/\\"))
        dest = dst_fs.join(dst_dir, name)
        src_loc, dst_loc = remote.parse(src), remote.parse(self.target_dir)
        if src_loc and dst_loc and src_loc.key == dst_loc.key and \
                posixpath.dirname(src_loc.path) == dst_loc.path:
            return False  # already there
        if dst_fs.exists(dest):
            choice = self._policy
            if not choice:
                self._answered.clear()
                self.conflict.emit(src, remote.join(self.target_dir, name))
                self._answered.wait()
                self._check()
                choice = self._answer
            if choice == "cancel":
                self.cancel()
                raise Cancelled
            if choice == "skip":
                return False
            if choice == "rename":
                stem, ext = os.path.splitext(name)
                i = 1
                while dst_fs.exists(dest):
                    dest = dst_fs.join(dst_dir, f"{stem} ({i}){ext}")
                    i += 1
        if move and src_loc and dst_loc and src_loc.key == dst_loc.key:
            dst_fs.replace(src_path, dest)  # a rename on the same server
        else:
            remote.copy_tree(src_fs, src_path, dst_fs, dest, lambda n: self._advance(n), self._check)
            if move:
                if src_loc:
                    remote.remove_tree(src_fs.sftp, src_path, self._check)
                else:
                    delete_path(src_path)
        if dst_loc is None:  # a new local item can be undone (moved to the trash)
            self.undo.append(("created", dest))

    def _size_to_copy(self, src: str) -> int:
        if remote.is_remote(src) or remote.is_remote(self.target_dir):
            if self.op == "move" and remote.parse(src) and remote.parse(self.target_dir) and \
                    remote.parse(src).key == remote.parse(self.target_dir).key:
                return 0  # a rename on the server
            return self._tree_size(src)
        if self.op == "move" and not archive.inside(src):
            try:
                if os.lstat(src).st_dev == os.stat(self.target_dir).st_dev:
                    return 0  # a rename, nothing to copy
            except OSError:
                return 0
        return tree_size(src, self._stop)[0]

    def _destination(self, src: str) -> tuple[str, bool] | None:
        """(destination, overwrite?) for `src`, asking on a name clash. None = skip."""
        name = os.path.basename(src)
        dest = os.path.join(self.target_dir, name)
        if not os.path.lexists(dest):
            return dest, False
        if self.op == "copy" and _same(dest, src):
            return unique_dest(self.target_dir, name), False  # a copy in the same folder
        choice = self._policy
        if not choice:
            self._answered.clear()
            self.conflict.emit(src, dest)
            self._answered.wait()
            self._check()
            choice = self._answer
        if choice == "cancel":
            self.cancel()
            raise Cancelled
        if choice == "skip":
            return None
        if choice == "rename":
            return unique_dest(self.target_dir, name), False
        src_is_dir = _archive_is_dir(src) if archive.inside(src) else os.path.isdir(src)
        if os.path.isdir(dest) != src_is_dir:
            raise OSError("can't replace a folder with a file or the other way round")
        return dest, True

    # Copy

    def _copy(self, src: str):
        if remote.is_remote(src) or remote.is_remote(self.target_dir):
            return self._transfer(src, move=False)
        if os.path.isdir(src) and _inside(self.target_dir, src):
            raise OSError(f"cannot copy '{os.path.basename(src)}' into itself")
        target = self._destination(src)
        if target is None:
            return False
        dest, overwrite = target
        if archive.inside(src):
            archive.extract(src, dest, lambda n: self._advance(n))
        else:
            self._copy_tree(src, dest)
        if not overwrite:
            self.undo.append(("created", dest))

    def _copy_tree(self, src: str, dest: str) -> None:
        self._check()
        if os.path.islink(src):
            if os.path.lexists(dest):
                delete_path(dest)
            os.symlink(os.readlink(src), dest, target_is_directory=os.path.isdir(src))
        elif os.path.isdir(src):
            os.makedirs(dest, exist_ok=True)
            with os.scandir(src) as it:
                for e in it:
                    self._copy_tree(e.path, os.path.join(dest, e.name))
            try:
                shutil.copystat(src, dest)
            except OSError:
                pass
        else:
            self._copy_file(src, dest)

    def _copy_file(self, src: str, dest: str) -> None:
        """Copy through a temporary file, so a cancelled copy never leaves half a file."""
        tmp = dest + ".vctmp"
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                while chunk := fin.read(CHUNK):
                    fout.write(chunk)
                    self._advance(len(chunk))
            shutil.copystat(src, tmp)
            if os.path.lexists(dest) and not os.access(dest, os.W_OK):
                os.chmod(dest, stat.S_IWRITE)
            os.replace(tmp, dest)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # Move

    def _move(self, src: str):
        if remote.is_remote(src) or remote.is_remote(self.target_dir):
            return self._transfer(src, move=True)
        if archive.inside(src):
            raise OSError("archives are read-only")
        if _same(os.path.dirname(os.path.abspath(src)), self.target_dir):
            return False  # already there
        if os.path.isdir(src) and _inside(self.target_dir, src):
            raise OSError(f"cannot move '{os.path.basename(src)}' into itself")
        target = self._destination(src)
        if target is None:
            return False
        dest, overwrite = target
        if overwrite and os.path.isdir(dest) and not os.path.islink(src):
            self._copy_tree(src, dest)  # merge the folders
            delete_path(src)
            return
        try:
            os.replace(src, dest)
        except OSError as e:
            if not _cross_device(e):
                raise
            self._copy_tree(src, dest)
            delete_path(src)
        if not overwrite:
            self.undo.append(("moved", src, dest))

    # Synchronize: sources are (source, destination) pairs; newer files replace older ones

    def _sync(self, pair: tuple[str, str]):
        src, dest = pair
        if remote.is_remote(src) or remote.is_remote(dest):
            src_fs, src_path = self._endpoint(src)
            dst_fs, dst_path = self._endpoint(dest, dest=True)
            existed = dst_fs.exists(dst_path)
            dst_fs.mkdirs(posixpath.dirname(dst_path) if dst_fs.sftp else os.path.dirname(dst_path))
            remote.copy_tree(src_fs, src_path, dst_fs, dst_path, lambda n: self._advance(n), self._check)
            if not existed and dst_fs.sftp is None:
                self.undo.append(("created", dst_path))
            return
        existed = os.path.lexists(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isdir(src) and not os.path.islink(src):
            self._copy_tree(src, dest)
        else:
            if existed and os.path.isdir(dest):
                raise OSError("a folder has the same name on the other side")
            self._copy_file(src, dest)
        if not existed:
            self.undo.append(("created", dest))

    # Zip: target_dir is the zip file to make; each source goes in under its own name

    def _zip(self, src: str):
        if remote.is_remote(src):
            raise OSError("can't zip remote files: copy them here first")
        if archive.inside(src):
            raise OSError("can't zip what is inside an archive: copy it out first")
        if not self.undo:
            self.undo.append(("created", self.target_dir))
        base = os.path.basename(src.rstrip("/\\")) or "item"
        stem, ext = os.path.splitext(base)
        name, i = base, 1
        while name.casefold() in self._zip_names:
            name, i = f"{stem} ({i}){ext}", i + 1
        self._zip_names.add(name.casefold())
        with zipfile.ZipFile(self.target_dir, "a", zipfile.ZIP_DEFLATED) as z:
            if not os.path.isdir(src):
                z.write(src, name)
                self._advance(os.path.getsize(src))
                return
            for folder, dirs, files in os.walk(src):
                rel = os.path.relpath(folder, src)
                inner = name if rel == "." else os.path.join(name, rel)
                if not dirs and not files:
                    z.write(folder, inner)  # keep empty folders
                for f in files:
                    self._check()
                    path = os.path.join(folder, f)
                    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.target_dir)):
                        continue  # the zip itself, when zipping its own folder
                    z.write(path, os.path.join(inner, f))
                    self._advance(os.path.getsize(path))

    # Trash and delete

    def _trash(self, src: str):
        if remote.is_remote(src):
            raise OSError("a remote folder has no trash")
        where = move_to_trash(src)
        self.undo.append(("trashed", src, where))

    def _delete(self, src: str):
        if loc := remote.parse(src):
            remote.remove_tree(self._channel(loc), loc.path, self._check)
            return
        delete_path(src)

    # Undo

    def _redo(self, record: tuple):
        self._undo(record)

    def _undo(self, record: tuple):
        """Reverse one record, and add the record that reverses this step to
        self.undo, so an undo can be redone (and a redo undone again)."""
        kind = record[0]
        if kind == "created":
            where = move_to_trash(record[1])  # the trash, not a delete, in case it changed since
            self.undo.append(("trashed", record[1], where))
        elif kind in ("moved", "renamed"):
            src, dest = record[1], record[2]
            if os.path.lexists(src) and not _same(src, dest):
                raise OSError(f"'{os.path.basename(src)}' exists again")
            os.makedirs(os.path.dirname(src), exist_ok=True)
            shutil.move(dest, src)
            self.undo.append((kind, dest, src))
        elif kind == "trashed":
            restore_from_trash(record[2], record[1])
            self.undo.append(("created", record[1]))


def _archive_is_dir(path: str) -> bool:
    loc = archive.inside(path)
    return bool(loc) and any(rel for _, rel in archive.members(*loc))


class SizeJob(QObject):
    """Adds up folder sizes in a background thread."""

    finished = Signal(dict)  # path -> bytes

    def __init__(self, paths: list[str]):
        super().__init__()
        self.paths = paths
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        sizes = {}
        for p in self.paths:
            if loc := remote.parse(p):
                try:
                    sftp = remote.REMOTE.connection(loc).open_sftp()
                    sizes[p] = remote.walk_size(sftp, loc.path, self._stop)[0]
                    sftp.close()
                except OSError:
                    pass
            else:
                sizes[p] = tree_size(p, self._stop)[0]
        self.finished.emit(sizes)

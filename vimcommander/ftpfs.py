"""FTP and FTPS folders (Qt-free): an ftplib client that looks like paramiko's
SFTPClient, so remote.py, the copy jobs and compare.py use it unchanged.

Only the calls VimCommander makes are there: listdir_attr / listdir / stat /
lstat / open / mkdir / rmdir / remove / rename / posix_rename / utime /
normalize / get / put / close. ftplib errors become OSError (IOError), like
paramiko's. Listing uses MLSD where the server has it, else parses LIST
(Unix and Windows / IIS formats).
"""

import calendar
import errno
import ftplib
import posixpath
import re
import stat
import time
from datetime import datetime

DIR_MODE = stat.S_IFDIR | 0o755
FILE_MODE = stat.S_IFREG | 0o644
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
UNIX_LINE = re.compile(
    r"^([\-dlbcps])[\w\-+.@]{9,10}\s+\d+\s+\S+(?:\s+\S+)?\s+(\d+)\s+"
    r"(\w{3})\s+(\d{1,2})\s+(\d{1,2}:\d{2}|\d{4})\s(.+)$")
DOS_LINE = re.compile(r"^(\d{2})-(\d{2})-(\d{2,4})\s+(\d{1,2}):(\d{2})\s*([AP]M)?\s+(<DIR>|\d+)\s+(.+)$", re.I)


class Attr:
    """Like paramiko's SFTPAttributes, for the fields VimCommander reads."""

    def __init__(self, filename: str, is_dir: bool, size: int = 0, mtime: float = 0.0):
        self.filename = filename
        self.st_mode = DIR_MODE if is_dir else FILE_MODE
        self.st_size = 0 if is_dir else size
        self.st_mtime = mtime


def _os_error(e: Exception) -> OSError:
    text = str(e).strip()
    if text.startswith("550"):
        return FileNotFoundError(errno.ENOENT, text[4:] or "not found")
    if text.startswith("530"):
        return PermissionError(errno.EACCES, "login refused: " + text[4:])
    return OSError(errno.EIO, text or type(e).__name__)


def _guard(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ftplib.all_errors as e:
            if isinstance(e, OSError):  # socket errors already are
                raise
            raise _os_error(e) from None
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


def mlsd_time(value: str) -> float:
    """MLSD 'modify' (UTC, YYYYMMDDHHMMSS[.sss]) -> timestamp."""
    try:
        return float(calendar.timegm(time.strptime(value[:14], "%Y%m%d%H%M%S")))
    except (ValueError, OverflowError):
        return 0.0


def parse_list_line(line: str, now: datetime | None = None) -> Attr | None:
    """One line of a LIST reply (Unix ls -l or Windows / IIS style), or None."""
    m = UNIX_LINE.match(line)
    if m:
        kind, size, month, day, when, name = m.groups()
        if kind == "l":
            name = name.split(" -> ")[0]  # a link: treated as a file, unless listing it works
        now = now or datetime.now()
        mon = MONTHS.get(month.lower(), 1)
        try:
            if ":" in when:  # this year (or last year if the date is in the future)
                hour, minute = map(int, when.split(":"))
                dt = datetime(now.year, mon, int(day), hour, minute)
                if dt > now.replace(microsecond=0) and (dt - now).days > 1:
                    dt = dt.replace(year=now.year - 1)
            else:
                dt = datetime(int(when), mon, int(day))
            mtime = dt.timestamp()
        except (ValueError, OverflowError):
            mtime = 0.0
        return Attr(name, kind == "d", int(size), mtime)
    m = DOS_LINE.match(line)
    if m:
        month, day, year, hour, minute, ampm, size, name = m.groups()
        year = int(year) + (2000 if len(year) == 2 and int(year) < 70 else 1900 if len(year) == 2 else 0)
        hour = int(hour) % 12 + (12 if (ampm or "").upper() == "PM" else 0) if ampm else int(hour)
        try:
            mtime = datetime(year, int(month), int(day), hour, int(minute)).timestamp()
        except (ValueError, OverflowError):
            mtime = 0.0
        is_dir = size.upper() == "<DIR>"
        return Attr(name, is_dir, 0 if is_dir else int(size), mtime)
    return None


class _Transfer:
    """A data connection of RETR / STOR / APPE, used as a file."""

    def __init__(self, ftp: ftplib.FTP, sock, writing: bool):
        self.ftp, self.sock, self.writing = ftp, sock, writing
        self.done = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @_guard
    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunks = []
            while chunk := self.sock.recv(65536):
                chunks.append(chunk)
            self.done = True
            return b"".join(chunks)
        out = bytearray()
        while len(out) < size:
            chunk = self.sock.recv(size - len(out))
            if not chunk:
                self.done = True
                break
            out += chunk
        return bytes(out)

    @_guard
    def write(self, data: bytes) -> int:
        self.sock.sendall(data)
        return len(data)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            self.ftp.voidresp()  # 226 when complete; 426 / 451 when a read stopped early
        except ftplib.all_errors as e:
            if self.writing or self.done:
                raise _os_error(e) from None


class FtpClient:
    """One FTP (or FTPS) control connection."""

    def __init__(self, host: str, port: int, user: str, password: str, tls: bool = False,
                 timeout: float = 20):
        self.tls = tls
        try:
            ftp = ftplib.FTP_TLS(timeout=timeout) if tls else ftplib.FTP(timeout=timeout)
            ftp.encoding = "utf-8"
            ftp.connect(host, port or 21)
            ftp.login(user or "anonymous", password)
            if isinstance(ftp, ftplib.FTP_TLS):
                ftp.prot_p()  # encrypt the data connections too
            try:
                ftp.sendcmd("OPTS UTF8 ON")
            except ftplib.all_errors:
                pass
            ftp.voidcmd("TYPE I")
        except ftplib.error_perm as e:
            raise _os_error(e) from None
        except ftplib.all_errors as e:
            raise OSError(errno.EIO, f"can't connect to {host}: {e}") from None
        self.ftp = ftp
        self.mlsd = True  # until the server says it has no MLSD
        self.last_use = time.monotonic()

    def _touch(self) -> None:
        self.last_use = time.monotonic()

    @_guard
    def noop(self) -> None:
        self.ftp.voidcmd("NOOP")
        self._touch()

    def normalize(self, path: str) -> str:
        if path in (".", ""):
            try:
                return self.ftp.pwd() or "/"
            except ftplib.all_errors as e:
                raise _os_error(e) from None
        return posixpath.normpath(path)

    @_guard
    def listdir_attr(self, path: str) -> list[Attr]:
        self._touch()
        if self.mlsd:
            try:
                out = []
                for name, facts in self.ftp.mlsd(path, ["type", "size", "modify"]):
                    kind = facts.get("type", "file").lower()
                    if kind in ("cdir", "pdir") or name in (".", ".."):
                        continue
                    out.append(Attr(name, kind == "dir", int(facts.get("size", 0) or 0),
                                    mlsd_time(facts.get("modify", ""))))
                return out
            except ftplib.error_perm as e:
                if not str(e).startswith(("500", "501", "502", "504")):
                    raise
                self.mlsd = False
        lines: list[str] = []
        self.ftp.retrlines(f"LIST {path}" if path else "LIST", lines.append)
        out = []
        for line in lines:
            attr = parse_list_line(line)
            if attr and attr.filename not in (".", ".."):
                out.append(attr)
        return out

    def listdir(self, path: str) -> list[str]:
        return [a.filename for a in self.listdir_attr(path)]

    @_guard
    def stat(self, path: str) -> Attr:
        self._touch()
        path = posixpath.normpath(path) if path else "/"
        if path in ("/", "//"):
            return Attr("/", True)
        if self.mlsd:
            try:
                reply = self.ftp.sendcmd(f"MLST {path}")
                for line in reply.splitlines()[1:]:
                    if line.startswith(" ") and ";" in line:
                        facts_text = line.strip().rsplit(" ", 1)[0] if " " in line.strip() else line.strip()
                        facts = dict(f.split("=", 1) for f in facts_text.split(";") if "=" in f)
                        kind = facts.get("type", facts.get("Type", "file")).lower()
                        size = facts.get("size", facts.get("Size", "0"))
                        modify = facts.get("modify", facts.get("Modify", ""))
                        return Attr(posixpath.basename(path), kind in ("dir", "cdir", "pdir"),
                                    int(size or 0), mlsd_time(modify))
            except ftplib.error_perm as e:
                if str(e).startswith("550"):
                    raise
                if not str(e).startswith(("500", "501", "502", "504")):
                    raise
        # No MLST: look the name up in its folder's listing.
        folder, name = posixpath.split(path)
        for attr in self.listdir_attr(folder or "/"):
            if attr.filename == name:
                if not stat.S_ISDIR(attr.st_mode):  # a link to a folder lists as a file
                    try:
                        current = self.ftp.pwd()
                        self.ftp.cwd(path)
                        self.ftp.cwd(current)
                        return Attr(name, True, 0, attr.st_mtime)
                    except ftplib.error_perm:
                        pass
                return attr
        raise FileNotFoundError(errno.ENOENT, f"{path} not found")

    lstat = stat

    @_guard
    def open(self, path: str, mode: str = "rb") -> _Transfer:
        self._touch()
        self.ftp.voidcmd("TYPE I")
        if "r" in mode:
            return _Transfer(self.ftp, self.ftp.transfercmd(f"RETR {path}"), False)
        command = "APPE" if "a" in mode else "STOR"
        return _Transfer(self.ftp, self.ftp.transfercmd(f"{command} {path}"), True)

    @_guard
    def mkdir(self, path: str, mode: int = 0o777) -> None:
        self._touch()
        self.ftp.mkd(path)

    @_guard
    def rmdir(self, path: str) -> None:
        self._touch()
        self.ftp.rmd(path)

    @_guard
    def remove(self, path: str) -> None:
        self._touch()
        self.ftp.delete(path)

    @_guard
    def rename(self, old: str, new: str) -> None:
        self._touch()
        self.ftp.rename(old, new)

    posix_rename = rename

    @_guard
    def utime(self, path: str, times: tuple[float, float]) -> None:
        """MFMT, where the server has it (else the upload time stays)."""
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(times[1]))
        try:
            self.ftp.sendcmd(f"MFMT {stamp} {path}")
        except ftplib.error_perm:
            pass

    @_guard
    def get(self, path: str, local: str) -> None:
        self._touch()
        with open(local, "wb") as f:
            self.ftp.retrbinary(f"RETR {path}", f.write)

    @_guard
    def put(self, local: str, path: str) -> None:
        self._touch()
        with open(local, "rb") as f:
            self.ftp.storbinary(f"STOR {path}", f)

    def close(self) -> None:
        try:
            self.ftp.quit()
        except ftplib.all_errors:
            try:
                self.ftp.close()
            except ftplib.all_errors:
                pass

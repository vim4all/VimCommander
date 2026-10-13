"""Remote folders over SSH (SFTP), for :ssh. Pure Python (paramiko), no Qt.

A remote location is written as a URL: sftp://[user@]host[:port]/path, where
host can be an alias from ~/.ssh/config. Connections are made on first use
and shared: listing uses one SFTP channel (with a lock), each file job and the
remote terminal open their own channels on the same connection.

Logging in tries the SSH agent and your keys first (as ssh does), then asks
for a password through `Remote.ask_password`. An unknown host key is shown to
`Remote.ask_host` and saved to ~/.ssh/known_hosts when accepted.
"""

import os
import posixpath
import re
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass

URL = re.compile(r"^sftp://(?:([^@/]+)@)?([^/:]+)(?::(\d+))?(/.*)?$")
CHUNK = 256 * 1024


@dataclass(frozen=True)
class Location:
    user: str
    host: str  # as typed: an alias from ~/.ssh/config or a host name
    port: int  # 0 = from the ssh config, else 22
    path: str  # absolute remote path, with /

    @property
    def key(self) -> str:
        """Identifies the connection: [user@]host[:port]."""
        return (f"{self.user}@" if self.user else "") + self.host + (f":{self.port}" if self.port else "")

    def url(self, path: str | None = None) -> str:
        return f"sftp://{self.key}{path if path is not None else self.path}"


def parse(path: str) -> Location | None:
    """The Location of an sftp:// URL, or None for a local path."""
    if not isinstance(path, str) or not path.startswith("sftp://"):
        return None
    m = URL.match(path.replace("\\", "/"))
    if not m:
        return None
    user, host, port, rest = m.groups()
    rest = posixpath.normpath(rest or "/")
    if rest.startswith("//"):
        rest = rest[1:]
    return Location(user or "", host, int(port or 0), rest)


def is_remote(path: str) -> bool:
    return isinstance(path, str) and path.startswith("sftp://")


def from_spec(spec: str) -> str:
    """'user@host:/path', 'host:path', 'host' or an sftp:// URL -> URL (path may be relative: '~')."""
    if spec.startswith("sftp://"):
        return spec
    target, sep, path = spec.partition(":")
    if sep and path.isdigit():  # host:2222
        return f"sftp://{target}:{path}/~"
    path = path or "~"
    if not path.startswith("/"):
        path = "/~/" + path if path != "~" else "/~"
    return f"sftp://{target}{path}"


def join(base: str, name: str) -> str:
    loc = parse(base)
    return loc.url(posixpath.join(loc.path, name)) if loc else os.path.join(base, name)


def dirname(path: str) -> str:
    loc = parse(path)
    return loc.url(posixpath.dirname(loc.path)) if loc else os.path.dirname(path)


def basename(path: str) -> str:
    loc = parse(path)
    return posixpath.basename(loc.path) if loc else os.path.basename(path)


def ssh_hosts() -> list[str]:
    """Host aliases from ~/.ssh/config (no wildcards)."""
    path = os.path.expanduser("~/.ssh/config")
    hosts = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                words = line.strip().split()
                if len(words) > 1 and words[0].lower() == "host":
                    hosts += [h for h in words[1:] if not any(c in h for c in "*?!")]
    except OSError:
        pass
    return hosts


class HostKeyRejected(Exception):
    pass


class _AskPolicy:
    """paramiko MissingHostKeyPolicy: ask before trusting a new host."""

    def __init__(self, ask: Callable[[str, str, str], bool], known_hosts: str):
        self.ask, self.known_hosts = ask, known_hosts

    def missing_host_key(self, client, hostname, key):
        import hashlib
        import base64
        digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if not self.ask(hostname, key.get_name(), f"SHA256:{digest}"):
            raise HostKeyRejected(f"host key of {hostname} not trusted")
        client.get_host_keys().add(hostname, key.get_name(), key)
        try:
            os.makedirs(os.path.dirname(self.known_hosts), exist_ok=True)
            with open(self.known_hosts, "a", encoding="utf-8") as f:
                f.write(f"{hostname} {key.get_name()} {key.get_base64()}\n")
        except OSError:
            pass


class Connection:
    """One SSH connection: a paramiko client and the SFTP channel used for listing."""

    def __init__(self, client, label: str):
        self.client = client
        self.label = label
        self.sftp = client.open_sftp()
        self.lock = threading.Lock()
        self.home = self.sftp.normalize(".")

    @property
    def alive(self) -> bool:
        t = self.client.get_transport()
        return t is not None and t.is_active()

    def open_sftp(self):
        """A new SFTP channel, for a job running in another thread."""
        return self.client.open_sftp()

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass


class Remote:
    """All open connections, by Location.key."""

    ask_password: Callable[[str], str | None] = staticmethod(lambda prompt: None)
    ask_host: Callable[[str, str, str], bool] = staticmethod(lambda host, kind, fingerprint: False)

    def __init__(self):
        self.connections: dict[str, Connection] = {}
        self._lock = threading.Lock()
        self.known_hosts = os.path.expanduser("~/.ssh/known_hosts")

    def connection(self, loc: Location) -> Connection:
        with self._lock:
            conn = self.connections.get(loc.key)
            if conn and conn.alive:
                return conn
            conn = self._connect(loc)
            self.connections[loc.key] = conn
            return conn

    def _connect(self, loc: Location) -> Connection:
        import paramiko
        cfg = {}
        config_path = os.path.expanduser("~/.ssh/config")
        if os.path.exists(config_path):
            try:
                cfg = paramiko.SSHConfig.from_path(config_path).lookup(loc.host)
            except Exception:
                cfg = {}
        hostname = cfg.get("hostname", loc.host)
        port = loc.port or int(cfg.get("port", 22))
        user = loc.user or cfg.get("user") or os.environ.get("USER") or os.environ.get("USERNAME") or ""
        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except (OSError, paramiko.SSHException):
            pass
        try:
            client.load_host_keys(self.known_hosts) if os.path.exists(self.known_hosts) else None
        except (OSError, paramiko.SSHException):
            pass
        client.set_missing_host_key_policy(_AskPolicy(Remote.ask_host, self.known_hosts))
        keys = [os.path.expanduser(k) for k in cfg.get("identityfile", [])]
        sock = paramiko.ProxyCommand(cfg["proxycommand"]) if cfg.get("proxycommand") else None
        options = dict(hostname=hostname, port=port, username=user, timeout=15, banner_timeout=15,
                       auth_timeout=20, key_filename=[k for k in keys if os.path.exists(k)] or None,
                       sock=sock)
        try:
            client.connect(**options, allow_agent=True, look_for_keys=True)
        except paramiko.PasswordRequiredException:  # an encrypted key: ask for its passphrase
            phrase = Remote.ask_password(f"Passphrase for your SSH key ({user}@{hostname})")
            if phrase is None:
                raise OSError("cancelled")
            client.connect(**options, passphrase=phrase, allow_agent=True, look_for_keys=True)
        except (paramiko.AuthenticationException, paramiko.SSHException) as e:
            # No key was accepted, or there was none to try (no keys, no agent:
            # "No authentication methods available"): ask for the password.
            if not isinstance(e, paramiko.AuthenticationException) and "authentication methods" not in str(e):
                raise OSError(f"can't connect to {hostname}: {e}") from None
            password = Remote.ask_password(f"Password for {user}@{hostname}")
            if password is None:
                raise OSError("cancelled")
            try:
                client.connect(**options, password=password, allow_agent=False, look_for_keys=False)
            except paramiko.AuthenticationException:
                raise OSError("wrong password, or the server doesn't accept passwords") from None
        except HostKeyRejected as e:
            raise OSError(str(e)) from None
        except (paramiko.SSHException, OSError, EOFError) as e:
            raise OSError(f"can't connect to {hostname}: {e}") from None
        return Connection(client, f"{user}@{hostname}")

    def close_all(self) -> None:
        with self._lock:
            for conn in self.connections.values():
                conn.close()
            self.connections.clear()

    # Listing, on the shared channel

    def resolve(self, path: str) -> str:
        """The URL with '~' turned into the remote home folder."""
        loc = parse(path)
        if not loc or not (loc.path == "/~" or loc.path.startswith("/~/")):
            return path
        conn = self.connection(loc)
        return loc.url(posixpath.normpath(conn.home + loc.path[2:]))

    def list_dir(self, path: str) -> list[tuple[str, bool, int, float]]:
        """(name, is_dir, size, mtime) of a remote folder. Raises OSError."""
        loc = parse(path)
        conn = self.connection(loc)
        with conn.lock:
            try:
                items = conn.sftp.listdir_attr(loc.path)
            except IOError as e:
                raise OSError(e.errno, e.strerror or str(e)) from None
            out = []
            for a in items:
                is_dir = stat.S_ISDIR(a.st_mode or 0)
                if stat.S_ISLNK(a.st_mode or 0):  # follow links to see if they are folders
                    try:
                        is_dir = stat.S_ISDIR(conn.sftp.stat(posixpath.join(loc.path, a.filename)).st_mode)
                    except IOError:
                        pass
                out.append((a.filename, is_dir, a.st_size or 0, float(a.st_mtime or 0)))
        return out

    def stat(self, path: str):
        """paramiko SFTPAttributes, or None if it doesn't exist."""
        loc = parse(path)
        conn = self.connection(loc)
        with conn.lock:
            try:
                return conn.sftp.stat(loc.path)
            except IOError:
                return None

    def is_dir(self, path: str) -> bool:
        a = self.stat(path)
        return a is not None and stat.S_ISDIR(a.st_mode or 0)

    def read_head(self, path: str, limit: int) -> bytes:
        loc = parse(path)
        conn = self.connection(loc)
        with conn.lock:
            with conn.sftp.open(loc.path, "rb") as f:
                return f.read(limit)

    # Changes (small ones, on the shared channel)

    def mkdir(self, path: str, parents: bool = False) -> None:
        loc = parse(path)
        conn = self.connection(loc)
        with conn.lock:
            _mkdirs(conn.sftp, loc.path) if parents else conn.sftp.mkdir(loc.path)

    def touch(self, path: str) -> None:
        loc = parse(path)
        conn = self.connection(loc)
        with conn.lock:
            conn.sftp.open(loc.path, "ab").close()

    def rename(self, old: str, new: str) -> None:
        a, b = parse(old), parse(new)
        conn = self.connection(a)
        with conn.lock:
            try:
                conn.sftp.posix_rename(a.path, b.path)
            except IOError:
                conn.sftp.rename(a.path, b.path)

    def download(self, path: str, local: str) -> None:
        loc = parse(path)
        conn = self.connection(loc)
        with conn.lock:
            conn.sftp.get(loc.path, local)

    def upload(self, local: str, path: str) -> None:
        loc = parse(path)
        conn = self.connection(loc)
        with conn.lock:
            conn.sftp.put(local, loc.path)


REMOTE = Remote()


# Tree operations on a job's own SFTP channel (called from job threads)

def _mkdirs(sftp, path: str) -> None:
    parts, current = [p for p in path.split("/") if p], ""
    for p in parts:
        current += "/" + p
        try:
            sftp.stat(current)
        except IOError:
            sftp.mkdir(current)


def walk_size(sftp, path: str, stop: threading.Event | None = None) -> tuple[int, int]:
    """(bytes, files) of a remote file or folder tree."""
    try:
        a = sftp.stat(path)
    except IOError:
        return 0, 0
    if not stat.S_ISDIR(a.st_mode or 0):
        return a.st_size or 0, 1
    total = files = 0
    stack = [path]
    while stack:
        if stop and stop.is_set():
            break
        folder = stack.pop()
        try:
            for e in sftp.listdir_attr(folder):
                if stat.S_ISDIR(e.st_mode or 0):
                    stack.append(posixpath.join(folder, e.filename))
                else:
                    total += e.st_size or 0
                    files += 1
        except IOError:
            pass
    return total, files


def remove_tree(sftp, path: str, check: Callable[[], None] = lambda: None) -> None:
    a = sftp.lstat(path)
    if stat.S_ISDIR(a.st_mode or 0):
        for e in sftp.listdir_attr(path):
            check()
            remove_tree(sftp, posixpath.join(path, e.filename), check)
        sftp.rmdir(path)
    else:
        sftp.remove(path)


class Endpoint:
    """A file system for a copy: local, or remote through a job's SFTP channel."""

    def __init__(self, sftp=None):
        self.sftp = sftp

    def exists(self, p: str) -> bool:
        if self.sftp is None:
            return os.path.lexists(p)
        try:
            self.sftp.lstat(p)
            return True
        except IOError:
            return False

    def is_dir(self, p: str) -> bool:
        if self.sftp is None:
            return os.path.isdir(p)
        try:
            return stat.S_ISDIR(self.sftp.stat(p).st_mode or 0)
        except IOError:
            return False

    def listdir(self, p: str) -> list[str]:
        return os.listdir(p) if self.sftp is None else self.sftp.listdir(p)

    def join(self, a: str, b: str) -> str:
        return os.path.join(a, b) if self.sftp is None else posixpath.join(a, b)

    def mkdirs(self, p: str) -> None:
        if self.sftp is None:
            os.makedirs(p, exist_ok=True)
        else:
            _mkdirs(self.sftp, p)

    def open(self, p: str, mode: str):
        return open(p, mode) if self.sftp is None else self.sftp.open(p, mode)

    def replace(self, a: str, b: str) -> None:
        if self.sftp is None:
            os.replace(a, b)
            return
        try:
            self.sftp.posix_rename(a, b)
        except IOError:
            if self.exists(b):
                self.sftp.remove(b)
            self.sftp.rename(a, b)

    def remove(self, p: str) -> None:
        if self.sftp is None:
            os.remove(p)
        else:
            self.sftp.remove(p)

    def mtime(self, p: str) -> float | None:
        try:
            return os.stat(p).st_mtime if self.sftp is None else float(self.sftp.stat(p).st_mtime)
        except (OSError, IOError, TypeError):
            return None

    def set_mtime(self, p: str, mtime: float) -> None:
        try:
            os.utime(p, (mtime, mtime)) if self.sftp is None else self.sftp.utime(p, (mtime, mtime))
        except (OSError, IOError):
            pass


def copy_tree(src_fs: Endpoint, src: str, dst_fs: Endpoint, dst: str,
              progress: Callable[[int], None], check: Callable[[], None]) -> None:
    """Copy a file or folder between two endpoints (local or remote), through a
    temporary file, keeping modification times. `progress(n)` gets byte counts,
    `check()` raises to cancel."""
    check()
    if src_fs.is_dir(src):
        dst_fs.mkdirs(dst)
        for name in src_fs.listdir(src):
            copy_tree(src_fs, src_fs.join(src, name), dst_fs, dst_fs.join(dst, name), progress, check)
        return
    tmp = dst + ".vctmp"
    try:
        with src_fs.open(src, "rb") as fin, dst_fs.open(tmp, "wb") as fout:
            if hasattr(fin, "prefetch"):
                fin.prefetch()  # paramiko: read ahead, much faster
            while chunk := fin.read(CHUNK):
                check()
                fout.write(chunk)
                progress(len(chunk))
        dst_fs.replace(tmp, dst)
    except BaseException:
        try:
            dst_fs.remove(tmp)
        except (OSError, IOError):
            pass
        raise
    if (mtime := src_fs.mtime(src)) is not None:
        dst_fs.set_mtime(dst, mtime)


def entries(sftp, path: str, show_hidden: bool) -> dict[str, tuple[str, bool, int, float]]:
    """For compare.py: normcase(name) -> (name, is_dir, size, mtime) of a remote folder."""
    out = {}
    try:
        for a in sftp.listdir_attr(path):
            if not show_hidden and a.filename.startswith("."):
                continue
            is_dir = stat.S_ISDIR(a.st_mode or 0)
            out[a.filename] = (a.filename, is_dir, 0 if is_dir else a.st_size or 0, float(a.st_mtime or 0))
    except IOError:
        pass
    return out

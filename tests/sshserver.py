"""A small SSH server for the tests: password login (any user, password "pw"),
SFTP on this computer's real file system (home = the given folder), and
commands run with the local shell (for the remote terminal). Not for real use."""

import os
import re
import socket
import subprocess
import sys
import threading

import paramiko
from paramiko import SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface
from paramiko.sftp import SFTP_OK, SFTP_FAILURE

PASSWORD = "pw"


class _Handle(SFTPHandle):
    def stat(self):
        return SFTPAttributes.from_stat(os.fstat(self.readfile.fileno() if hasattr(self, "readfile")
                                                 else self.writefile.fileno()))

    def chattr(self, attr):
        return SFTP_OK


def _p(path: str) -> str:
    """A path from the client to this computer's form: '/D:/x' -> 'D:/x' (as Windows OpenSSH does)."""
    return path[1:] if re.match(r"^/[A-Za-z]:", path) else path


def _error(e: OSError):
    return SFTPServer.convert_errno(e.errno)


class _SFTP(SFTPServerInterface):
    home = "/"

    def canonicalize(self, path):
        if path in (".", ""):
            out = self.home
        else:
            path = _p(path)
            out = os.path.normpath(path if os.path.isabs(path) else os.path.join(self.home, path)).replace("\\", "/")
        return out if out.startswith("/") else "/" + out

    def list_folder(self, path):
        path = _p(path)
        try:
            out = []
            for name in os.listdir(path):
                attr = SFTPAttributes.from_stat(os.lstat(os.path.join(path, name)))
                attr.filename = name
                out.append(attr)
            return out
        except OSError as e:
            return _error(e)

    def stat(self, path):
        path = _p(path)
        try:
            return SFTPAttributes.from_stat(os.stat(path))
        except OSError as e:
            return _error(e)

    def lstat(self, path):
        path = _p(path)
        try:
            return SFTPAttributes.from_stat(os.lstat(path))
        except OSError as e:
            return _error(e)

    def open(self, path, flags, attr):
        path = _p(path)
        try:
            binary = getattr(os, "O_BINARY", 0)
            fd = os.open(path, flags | binary, 0o666)
        except OSError as e:
            return _error(e)
        if flags & os.O_WRONLY:
            mode = "ab" if flags & os.O_APPEND else "wb"
        elif flags & os.O_RDWR:
            mode = "a+b" if flags & os.O_APPEND else "r+b"
        else:
            mode = "rb"
        f = os.fdopen(fd, mode)
        handle = _Handle(flags)
        handle.filename = path
        handle.readfile = f
        handle.writefile = f
        return handle

    def remove(self, path):
        path = _p(path)
        try:
            os.remove(path)
        except OSError as e:
            return _error(e)
        return SFTP_OK

    def rename(self, old, new):
        old, new = _p(old), _p(new)
        if os.path.exists(new):
            return SFTP_FAILURE
        try:
            os.rename(old, new)
        except OSError as e:
            return _error(e)
        return SFTP_OK

    def posix_rename(self, old, new):
        old, new = _p(old), _p(new)
        try:
            os.replace(old, new)
        except OSError as e:
            return _error(e)
        return SFTP_OK

    def mkdir(self, path, attr):
        path = _p(path)
        try:
            os.mkdir(path)
        except OSError as e:
            return _error(e)
        return SFTP_OK

    def rmdir(self, path):
        path = _p(path)
        try:
            os.rmdir(path)
        except OSError as e:
            return _error(e)
        return SFTP_OK

    def chattr(self, path, attr):
        path = _p(path)
        try:
            if attr.st_mtime is not None:
                os.utime(path, (attr.st_atime or attr.st_mtime, attr.st_mtime))
        except OSError as e:
            return _error(e)
        return SFTP_OK


class _Server(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if password == PASSWORD else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        threading.Thread(target=_run_command, args=(channel, command.decode()), daemon=True).start()
        return True


def _run_command(channel, command: str) -> None:
    shell = ["bash", "-c", command] if sys.platform != "win32" else ["bash", "-c", command]
    try:
        p = subprocess.Popen(shell, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env={**os.environ, "SHELL": "/bin/bash"})
    except OSError as e:
        channel.sendall(f"cannot run: {e}\n".encode())
        channel.close()
        return

    def pump_in():
        while True:
            try:
                data = channel.recv(4096)
            except Exception:
                data = b""
            if not data:
                break
            p.stdin.write(data)
            p.stdin.flush()
        try:
            p.stdin.close()
        except OSError:
            pass
    threading.Thread(target=pump_in, daemon=True).start()
    while chunk := p.stdout.read1(4096):
        try:
            channel.sendall(chunk)
        except Exception:
            break
    p.wait()
    try:
        channel.send_exit_status(p.returncode)
        channel.close()
    except Exception:
        pass


class TestServer:
    """with TestServer(home) as port: ... — an SSH server on 127.0.0.1:port."""

    _key = None

    def __init__(self, home: str):
        self.home = home
        if TestServer._key is None:
            TestServer._key = paramiko.RSAKey.generate(2048)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.transports = []
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        home = self.home

        class Stub(_SFTP):
            pass
        Stub.home = home.replace("\\", "/")
        while not self._stop:
            try:
                client, _ = self.sock.accept()
            except OSError:
                break
            t = paramiko.Transport(client)
            t.add_server_key(TestServer._key)
            t.set_subsystem_handler("sftp", SFTPServer, Stub)
            t.start_server(server=_Server())
            self.transports.append(t)

    def close(self) -> None:
        self._stop = True
        self.sock.close()
        for t in self.transports:
            t.close()

    def __enter__(self):
        return self.port

    def __exit__(self, *exc):
        self.close()

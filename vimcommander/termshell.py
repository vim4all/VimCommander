"""Which shell the terminal pane runs, and how it and the pane follow each other. No Qt.

The shell reports its folder after each command with the OSC 7 escape sequence
(ESC ] 7 ; file://host/path BEL), as many terminals use: bash through
PROMPT_COMMAND, PowerShell and cmd through their prompt. The pane turns that
into a folder (in WSL: /mnt/d/x -> D:\\x); going the other way, cd_line() is
the command that takes the shell to the pane's folder.
"""

import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from . import wsl

WINDOWS = sys.platform == "win32"
OSC7 = re.compile(r"\x1b\]7;([^\x07\x1b]*)(?:\x07|\x1b\\)")
# bash: report the folder before each prompt; in WSL the "host" is the distro's name
BASH_HOOK = r'printf "\033]7;file://%s%s\033\\" "${WSL_DISTRO_NAME:-$HOSTNAME}" "$PWD"'
PS_HOOK = ("$__vcPrompt = $function:prompt; function prompt { $e = [char]27; "
           "\"$e]7;file://localhost/$($executionContext.SessionState.Path.CurrentLocation.ProviderPath"
           " -replace '\\\\', '/')$e\\\" + (& $__vcPrompt) }")
CMD_PROMPT = "$e]7;file://localhost/$p$e\\$p$g"
# The terminal pane's 16 ANSI colors, black..white then the bright ones (a theme replaces them)
DEFAULT_ANSI = ["#1e1e1e", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
                "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#ffffff"]
ANSI: list[str] = list(DEFAULT_ANSI)


@dataclass
class Shell:
    argv: list[str]
    kind: str  # "wsl" | "powershell" | "cmd" | "posix"
    distro: str = ""  # WSL: the distro ("" = the default one, known after the first prompt)
    env: dict[str, str] = field(default_factory=dict)  # added to the environment

    @property
    def name(self) -> str:
        if self.kind == "wsl":
            return f"WSL {self.distro}".strip()
        return os.path.basename(self.argv[0]) if self.argv else "shell"


def default_spec() -> str:
    if WINDOWS:
        return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
    return os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"


def shell_command(spec: str, cwd: str) -> Shell:
    """The shell for `spec`: "" (the system's), "wsl", "wsl:Ubuntu", "pwsh",
    "cmd", "bash" or any command line, started in `cwd`."""
    spec = (spec or "").strip() or default_spec()
    env = {"TERM": "xterm-256color", "COLORTERM": "truecolor"}
    if wsl.is_wsl(spec):
        distro = wsl.distro_of(spec)
        start = cwd if WINDOWS and cwd and not cwd.startswith(("\\\\", "//")) else "~"
        if cwd.lower().startswith(("\\\\wsl.localhost\\", "\\\\wsl$\\")):
            start = wsl.to_linux(cwd, distro) or "~"
        argv = ["wsl.exe"] + (["-d", distro] if distro else []) + ["--cd", start]
        if spec.split()[0].lower() not in ("wsl", "wsl.exe") and not spec.lower().startswith("wsl:"):
            argv = shlex.split(spec, posix=False) + ["--cd", start]
        env["PROMPT_COMMAND"] = BASH_HOOK
        env["WSLENV"] = ":".join(p for p in (os.environ.get("WSLENV", ""), "PROMPT_COMMAND/u") if p)
        return Shell(argv, "wsl", distro, env)
    argv = shlex.split(spec, posix=not WINDOWS)
    name = os.path.basename(argv[0]).lower() if argv else ""
    if name.startswith(("pwsh", "powershell")):
        if len(argv) == 1:
            argv += ["-NoLogo", "-NoExit", "-Command", PS_HOOK]
        return Shell(argv, "powershell", env=env)
    if name.startswith("cmd"):
        if len(argv) == 1:
            argv += ["/k", f"prompt {CMD_PROMPT}"]
        return Shell(argv, "cmd", env=env)
    old = os.environ.get("PROMPT_COMMAND", "")
    env["PROMPT_COMMAND"] = BASH_HOOK + (f"; {old}" if old else "")
    return Shell(argv, "posix", env=env)


def osc7_folder(url: str, shell: Shell) -> tuple[str | None, str]:
    """(folder for the pane, host) from an OSC 7 URL; the folder is None when
    this computer can't open it."""
    parts = urlparse(url)
    host, path = parts.netloc, unquote(parts.path)
    if shell.kind == "wsl":
        return wsl.to_windows(path, shell.distro or host), host
    if WINDOWS:
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return path.replace("/", "\\"), host
    return path or None, host


def cd_line(shell: Shell, folder: str) -> str | None:
    """The line that takes the shell to `folder` (a leading space keeps it out of
    bash's history), or None if the shell can't reach it."""
    if shell.kind == "wsl":
        path = wsl.to_linux(folder, shell.distro)
        return None if path is None else " cd -- " + shlex.quote(path) + "\r"
    if shell.kind == "powershell":
        return " Set-Location -LiteralPath '" + folder.replace("'", "''") + "'\r"
    if shell.kind == "cmd":
        return f' cd /d "{folder}"\r'
    return " cd -- " + shlex.quote(folder) + "\r"


def mount_point(loc) -> str:
    return "~/mnt/" + re.sub(r"[^A-Za-z0-9._-]", "_", loc.host)


def mount_line(loc) -> str:
    """sshfs mounts the SSH folder `loc` (a remote.Location) at ~/mnt/HOST, then cds into it."""
    target = (f"{loc.user}@" if loc.user else "") + f"{loc.host}:{loc.path}"
    port = f"-p {loc.port} " if loc.port else ""
    point = mount_point(loc)
    return (f"mkdir -p {point} && sshfs {port}-o reconnect {shlex.quote(target)} {point}"
            f" && cd {point}\r")


def umount_line(loc) -> str:
    point = mount_point(loc)
    return f"cd ~ && (fusermount -u {point} 2>/dev/null || umount {point})\r"

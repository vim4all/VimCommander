"""Windows Subsystem for Linux, no Qt: paths both ways and the installed distros.

Windows drives are mounted in WSL (D:\\Pr is /mnt/d/Pr), and WSL's own files
are a network folder for Windows (/home/me is \\\\wsl.localhost\\Ubuntu\\home\\me),
so a pane and a WSL shell can follow each other.
"""

import re
import subprocess
import sys

WINDOWS = sys.platform == "win32"
_DRIVE = re.compile(r"^([A-Za-z]):(?:[\\/](.*))?$")
_MNT = re.compile(r"^/mnt/([a-zA-Z])(?:/(.*))?$")
_UNC = re.compile(r"^[\\/]{2}(?:wsl\.localhost|wsl\$)[\\/]([^\\/]+)(?:[\\/](.*))?$", re.IGNORECASE)


def is_wsl(spec: str) -> bool:
    """'wsl', 'wsl:Ubuntu' or a wsl.exe command line."""
    first = (spec or "").strip().split(" ", 1)[0].lower()
    return first in ("wsl", "wsl.exe") or first.startswith("wsl:") or first.endswith(("\\wsl.exe", "/wsl.exe"))


def distro_of(spec: str) -> str:
    """'wsl:Ubuntu' or 'wsl.exe -d Ubuntu' -> 'Ubuntu'; '' = the default distro."""
    spec = (spec or "").strip()
    if spec.lower().startswith("wsl:"):
        return spec[4:].strip()
    parts = spec.split()
    for i, p in enumerate(parts[:-1]):
        if p in ("-d", "--distribution"):
            return parts[i + 1]
    return ""


def to_linux(path: str, distro: str = "") -> str | None:
    """A Windows path as WSL sees it: D:\\Pr\\Ws -> /mnt/d/Pr/Ws,
    \\\\wsl.localhost\\Ubuntu\\home\\me -> /home/me. None if WSL can't reach it
    (another network share, or another distro's files)."""
    if m := _DRIVE.match(path):
        rest = (m.group(2) or "").replace("\\", "/").strip("/")
        return f"/mnt/{m.group(1).lower()}" + (f"/{rest}" if rest else "")
    if m := _UNC.match(path):
        if distro and m.group(1).lower() != distro.lower():
            return None
        return "/" + (m.group(2) or "").replace("\\", "/").strip("/")
    if path.startswith("/"):
        return path
    return None


def to_windows(path: str, distro: str) -> str | None:
    """A WSL path as a Windows path: /mnt/d/Pr -> D:\\Pr, /home/me ->
    \\\\wsl.localhost\\DISTRO\\home\\me (None without a distro name)."""
    if m := _MNT.match(path):
        rest = (m.group(2) or "").replace("/", "\\")
        return f"{m.group(1).upper()}:\\{rest}"
    if path.startswith("/") and distro:
        return f"\\\\wsl.localhost\\{distro}" + path.replace("/", "\\").rstrip("\\")
    return None


_distros: list[str] | None = None


def distros(timeout: float = 4.0) -> list[str]:
    """Installed distros, the default first ([] when WSL isn't there); asked once."""
    global _distros
    if _distros is None:
        _distros = _list(timeout)
    return _distros


def _list(timeout: float) -> list[str]:
    if not WINDOWS:
        return []
    try:
        out = subprocess.run(["wsl.exe", "--list", "--quiet"], capture_output=True, timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_list(out)


def parse_list(out: bytes) -> list[str]:
    """`wsl --list --quiet` writes UTF-16 (with NULs between the letters)."""
    text = out.decode("utf-16-le", errors="ignore") if b"\0" in out else out.decode(errors="ignore")
    return [line.strip().lstrip("\ufeff") for line in text.splitlines()
            if line.strip() and "docker-desktop" not in line.lower()]


def home(distro: str) -> str:
    """The distro's home folders (\\\\wsl.localhost\\Ubuntu\\home) as a Windows path."""
    return f"\\\\wsl.localhost\\{distro}\\home"

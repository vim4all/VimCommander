"""Playlists for the music player: which files are audio, a folder's tracks in
album order, .m3u files, times as m:ss and the shuffle order. Pure Python, no Qt.
"""

import os
import random
import re

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac", ".wav", ".wma",
              ".aif", ".aiff", ".ape", ".wv", ".mka", ".mpc", ".alac"}
PLAYLIST_EXTS = {".m3u", ".m3u8"}
MAX_TRACKS = 10000  # a folder tree with more stops there

_DIGITS = re.compile(r"(\d+)")


def _key(text: str) -> tuple:
    """Track 2 before track 10, as in pane.natural_key()."""
    parts: list = _DIGITS.split(text.casefold())
    parts[1::2] = [int(p) for p in parts[1::2]]
    return tuple(parts), text


def audio_backend(environ: dict, platform: str, exists=os.path.exists) -> str | None:
    """The QT_AUDIO_BACKEND to set before QtMultimedia starts, or None. Qt 6.10+
    uses PipeWire on Linux, and libpipewire aborts the process when it finds no
    client.conf (WSL, minimal installs); PulseAudio (WSLg has a server) works there."""
    if not platform.startswith("linux") or environ.get("QT_AUDIO_BACKEND"):
        return None
    home = environ.get("XDG_CONFIG_HOME") or os.path.join(environ.get("HOME", "~"), ".config")
    folders = [environ.get("PIPEWIRE_CONFIG_DIR", ""), os.path.join(home, "pipewire"),
               "/etc/pipewire", "/usr/share/pipewire", "/usr/local/share/pipewire"]
    if any(f and exists(os.path.join(f, "client.conf")) for f in folders):
        return None
    return "pulseaudio"


def is_audio(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in AUDIO_EXTS


def is_playlist(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in PLAYLIST_EXTS


def folder_tracks(folder: str) -> list[str]:
    """The audio files directly in `folder`, in name order."""
    try:
        with os.scandir(folder) as it:
            names = [e.name for e in it if e.is_file() and is_audio(e.name)]
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names, key=_key)]


def tree_tracks(folder: str, limit: int = MAX_TRACKS) -> list[str]:
    """The audio files in `folder` and below: each folder's own files first, then
    its subfolders (CD1, CD2…) in name order, hidden ones left out."""
    out: list[str] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted((d for d in dirs if not d.startswith(".")), key=_key)
        out += [os.path.join(root, f) for f in sorted(files, key=_key) if is_audio(f)]
        if len(out) >= limit:
            return out[:limit]
    return out


def read_m3u(path: str) -> list[str]:
    """The existing audio files an .m3u / .m3u8 lists (relative to its folder)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    base = os.path.dirname(path)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "://" in line:
            continue
        track = os.path.normpath(os.path.join(base, line.replace("\\", os.sep)))
        if is_audio(track) and os.path.isfile(track):
            out.append(track)
    return out


def collect(paths: list[str], limit: int = MAX_TRACKS) -> list[str]:
    """The tracks of the chosen items: folders with everything below them,
    playlists with what they list, audio files themselves; each once."""
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if os.path.isdir(path):
            found = tree_tracks(path, limit)
        elif is_playlist(path):
            found = read_m3u(path)
        else:
            found = [path] if is_audio(path) else []
        for track in found:
            if track not in seen:
                seen.add(track)
                out.append(track)
        if len(out) >= limit:
            return out[:limit]
    return out


def write_m3u(path: str, tracks: list[str]) -> None:
    """Save the tracks as an .m3u8, relative to its folder where they can be."""
    base = os.path.dirname(os.path.abspath(path))
    lines = ["#EXTM3U"]
    for track in tracks:
        try:
            lines.append(os.path.relpath(track, base))
        except ValueError:  # another drive on Windows
            lines.append(track)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def format_time(ms: int) -> str:
    """1:05, 12:00, 1:02:03."""
    s = max(0, int(ms) // 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


MIN_SPEED, MAX_SPEED = 0.25, 4.0


def clamp_speed(rate) -> float:
    """A playback rate within MIN_SPEED..MAX_SPEED (1.0 for anything that isn't a number)."""
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return 1.0
    if rate != rate:  # NaN
        return 1.0
    return round(max(MIN_SPEED, min(MAX_SPEED, rate)), 2)


def step_speed(rate: float, steps: int, speeds: tuple) -> float:
    """The speed `steps` places up (or down) in `speeds` from `rate`, which may lie between them."""
    if steps > 0:
        faster = [s for s in speeds if s > rate + 1e-9]
        return faster[min(steps, len(faster)) - 1] if faster else rate
    slower = [s for s in speeds if s < rate - 1e-9]
    return slower[max(len(slower) + steps, 0)] if slower else rate


def parse_speed(text: str, rate: float) -> float | None:
    """:play speed's argument: 1.5, 1.5x, 150%, +0.25 / -0.25, or + / - (None if it isn't one)."""
    m = re.fullmatch(r"([+-]?)(\d*\.?\d+)\s*(x|×|%)?", text.strip().lower())
    if not m:
        return None
    value = float(m.group(2)) / (100 if m.group(3) == "%" else 1)
    return clamp_speed(rate + value if m.group(1) == "+" else rate - value if m.group(1) == "-" else value)


def format_speed(rate: float) -> str:
    """1×, 1.25×, 0.5×."""
    return f"{rate:g}×"


def title(path: str) -> str:
    """The file name without its extension."""
    return os.path.splitext(os.path.basename(path))[0]


def display_names(tracks: list[str]) -> list[str]:
    """What the playlist shows: the paths below the folder they share
    ("CD1/01 Intro"), without extensions."""
    if not tracks:
        return []
    try:
        root = os.path.commonpath([os.path.dirname(t) for t in tracks])
    except ValueError:  # different drives
        root = ""
    names = []
    for t in tracks:
        rel = os.path.relpath(t, root) if root else t
        names.append(os.path.splitext(rel)[0].replace(os.sep, "/"))
    return names


def shuffled(count: int, first: int | None = None, rng: random.Random | None = None) -> list[int]:
    """A random order of the track numbers 0..count-1, `first` at its start."""
    order = list(range(count))
    (rng or random).shuffle(order)
    if first is not None and 0 <= first < count:
        order.remove(first)
        order.insert(0, first)
    return order

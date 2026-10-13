"""Files on the system clipboard, for the "+ and "* registers.

Writes file URLs plus the markers file managers use to tell copy from cut
(Windows Explorer, KDE, GNOME), so files yanked here paste in Explorer and the
other way round.
"""

import os
import struct
import sys

from PySide6.QtCore import QByteArray, QMimeData, QUrl
from PySide6.QtWidgets import QApplication

WINDOWS_EFFECT = "application/x-qt-windows-mime;value=\"Preferred DropEffect\""
EFFECT_COPY, EFFECT_MOVE = 5, 2  # DROPEFFECT_COPY | DROPEFFECT_LINK, DROPEFFECT_MOVE
GNOME = "x-special/gnome-copied-files"
KDE_CUT = "application/x-kde-cutselection"
OURS = "application/x-vimcommander"  # marks clipboard data this app put there


def put_files(kind: str, paths: list[str]) -> None:
    """Put files on the clipboard; `kind` is "copy" or "cut"."""
    cut = kind == "cut"
    urls = [QUrl.fromLocalFile(p) for p in paths]
    mime = QMimeData()
    mime.setUrls(urls)
    mime.setText("\n".join(paths))
    mime.setData(WINDOWS_EFFECT, QByteArray(struct.pack("<I", EFFECT_MOVE if cut else EFFECT_COPY)))
    mime.setData(GNOME, QByteArray(("cut" if cut else "copy").encode() + b"".join(
        b"\n" + u.toEncoded().data() for u in urls)))
    mime.setData(KDE_CUT, QByteArray(b"1" if cut else b"0"))
    mime.setData(OURS, QByteArray(b"1"))
    QApplication.clipboard().setMimeData(mime)


def get_files() -> tuple[str, list[str]] | None:
    """("copy" | "cut", paths) for files on the clipboard, or None.

    Also accepts plain text with one existing path per line."""
    mime = QApplication.clipboard().mimeData()
    if mime is None:
        return None
    paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
    if not paths and mime.hasText():
        lines = [line.strip().strip('"') for line in mime.text().splitlines() if line.strip()]
        if lines and all(os.path.exists(line) for line in lines):
            paths = lines
    if not paths:
        return None
    return ("cut" if _is_cut(mime) else "copy"), paths


def _is_cut(mime: QMimeData) -> bool:
    for fmt in mime.formats():
        if "Preferred DropEffect" in fmt:
            data = bytes(mime.data(fmt).data())
            if len(data) >= 4 and struct.unpack("<I", data[:4])[0] & EFFECT_MOVE \
                    and not struct.unpack("<I", data[:4])[0] & 1:
                return True
    if mime.hasFormat(GNOME):
        return bytes(mime.data(GNOME).data()).startswith(b"cut")
    if mime.hasFormat(KDE_CUT):
        return bytes(mime.data(KDE_CUT).data()).strip() == b"1"
    return False


def clear() -> None:
    QApplication.clipboard().clear()


def release_at_exit() -> None:
    """Call before the app quits. On Linux and macOS, PySide crashes at exit
    when the clipboard still holds data we put there (seen with Qt 6.12), and
    the data goes away with the app anyway; on Windows, Qt hands it to the
    system so Explorer can still paste it, so it is kept."""
    board = QApplication.clipboard()
    mime = board.mimeData()
    if sys.platform != "win32" and mime is not None and mime.hasFormat(OURS):
        board.clear()

"""The app icon, drawn with Qt: used for the window and by build.py for the exe."""

import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def image(size: int = 256) -> QImage:
    """Draw the icon at `size` pixels (needs a QGuiApplication for the text)."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    s = size / 256
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.scale(s, s)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2e7d32"))
    p.drawRoundedRect(8, 8, 240, 240, 40, 40)
    p.setBrush(QColor("#ffffff"))
    p.drawRect(124, 40, 8, 176)  # the two panes
    font = QFont()
    font.setBold(True)
    font.setPixelSize(150)
    p.setFont(font)
    p.setPen(QColor("#ffffff"))
    p.drawText(0, 0, 256, 256, Qt.AlignmentFlag.AlignCenter, "V")
    p.end()
    return img


def icon() -> QIcon:
    result = QIcon()
    for size in SIZES:
        result.addPixmap(QPixmap.fromImage(image(size)))
    return result


def _png(img: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buffer, "PNG")
    buffer.close()
    return bytes(data.data())


def save_ico(path: str) -> None:
    """A Windows .ico with every size Explorer asks for (PNG entries, Vista+)."""
    pngs = [_png(image(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries = b""
    for size, png in zip(SIZES, pngs):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    with open(path, "wb") as f:
        f.write(header + entries + b"".join(pngs))

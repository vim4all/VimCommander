"""The app icon, drawn with Qt: used for the window and by build.py for the exe."""

import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


GREEN = "#43a047"  # Vim's green
DARK = "#1b2420"
ROW = "#9fb3a8"


def image(size: int = 256) -> QImage:
    """Draw the icon at `size` pixels: a dark two-pane window with a thick green frame,
    file rows and a cursor row, and Vim's green diamond with a V. Small sizes drop
    the details, so the frame and panes stay crisp (needs a QGuiApplication for the text)."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    s = size / 256
    small = size <= 32
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.scale(s, s)
    p.setPen(Qt.PenStyle.NoPen)
    # The window: green frame (seen on light and dark backgrounds), dark inside.
    p.setBrush(QColor(GREEN))
    p.drawRoundedRect(QRectF(4, 4, 248, 248), 44, 44)
    border = 30 if small else 20
    p.setBrush(QColor(DARK))
    p.drawRoundedRect(QRectF(4 + border, 4 + border, 248 - 2 * border, 248 - 2 * border), 26, 26)
    # The divider between the panes.
    p.setBrush(QColor(GREEN))
    p.drawRect(QRectF(118, 24, 20, 208) if small else QRectF(122, 24, 12, 208))
    if small:
        p.setBrush(QColor(GREEN).lighter(130))
        p.drawRect(QRectF(46, 108, 60, 40))  # the cursor
        p.end()
        return img
    # File rows (fewer and thicker when small); the cursor row in the left pane.
    p.setBrush(QColor(ROW))
    rows, h = ((54, 82, 110, 138, 166, 194), 12) if size > 64 else ((56, 100, 144, 188), 18)
    cursor = 2 if size > 64 else 1
    for i, y in enumerate(rows):
        if i != cursor:
            p.drawRoundedRect(QRectF(42, y, 60 if i % 3 else 70, h), h / 2, h / 2)
        if y < 105:  # the diamond covers the right pane's lower half
            p.drawRoundedRect(QRectF(148, y, 66 if i % 2 else 50, h), h / 2, h / 2)
    p.setBrush(QColor(GREEN))
    p.drawRoundedRect(QRectF(34, rows[cursor] - 6, 84, h + 12), 6, 6)
    # Vim's diamond with a V, bottom right, inside the window.
    cx, cy, r = 178, 176, 46
    diamond = QPolygonF([QPointF(cx, cy - r), QPointF(cx + r, cy), QPointF(cx, cy + r), QPointF(cx - r, cy)])
    p.setPen(QPen(QColor(DARK), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QColor(GREEN))
    p.drawPolygon(diamond)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(56)
    p.setFont(font)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, "V")
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

"""The :share window: a QR code to scan with a phone, the link, and a log of what
was sent and received. Sharing stops when the window closes or the time runs out."""

import os

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QVBoxLayout, QWidget)

from .share import ShareServer, lan_addresses
from .sizing import fit_window


class QrCode(QWidget):
    """Draws a QR code (from segno) as black squares on white, as big as fits."""

    def __init__(self):
        super().__init__()
        self.matrix: list[list[bool]] = []
        self.setMinimumSize(220, 220)

    def set_text(self, text: str) -> None:
        import segno  # only needed here
        qr = segno.make(text, error="m")
        self.matrix = [[bool(v) for v in row] for row in qr.matrix]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        n = len(self.matrix)
        quiet = 4  # the white margin QR readers need, in modules
        side = min(self.width(), self.height())
        cell = side / (n + 2 * quiet) if n else 0
        x0 = (self.width() - side) / 2
        y0 = (self.height() - side) / 2
        p.fillRect(QRectF(x0, y0, side, side), QColor("white"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("black"))
        for r, row in enumerate(self.matrix):
            for c, dark in enumerate(row):
                if dark:
                    p.drawRect(QRectF(x0 + (c + quiet) * cell, y0 + (r + quiet) * cell, cell + 0.5, cell + 0.5))
        p.end()


class _Events(QObject):
    happened = Signal(str)  # from the server's threads to the window


class ShareDialog(QDialog):
    received = Signal()  # a file was uploaded

    def __init__(self, items: list[str], upload_dir: str | None, port: int, minutes: int,
                 parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("Share on the local network")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        fit_window(self, 560, 720)
        self.events = _Events()
        self.events.happened.connect(self._log)
        self.server: ShareServer | None = ShareServer(items, upload_dir, port, self.events.happened.emit)
        self.left = minutes * 60
        what = []
        if items:
            names = ", ".join(os.path.basename(p.rstrip("/\\")) for p in items[:3])
            what.append(f"<b>Sharing</b> {names}" + (f" and {len(items) - 3} more" if len(items) > 3 else ""))
        if upload_dir:
            what.append(f"<b>Uploads go to</b> {upload_dir}")
        info = QLabel("<br>".join(what))
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.qr = QrCode()
        self.address = QComboBox()
        self.address.addItems(lan_addresses())
        self.address.setToolTip("This computer's address on the network (a 100.x one is Tailscale, "
                                "reachable from your devices anywhere). If the phone can't open the "
                                "link, try another one")
        self.address.currentTextChanged.connect(self._show_url)
        self.link = QLabel()
        self.link.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.link.setWordWrap(True)
        copy = QPushButton("Copy link")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._url()))
        row = QHBoxLayout()
        row.addWidget(self.address)
        row.addWidget(copy)
        row.addStretch(1)
        self.log = QListWidget()
        self.status = QLabel()
        stop = QPushButton("Stop sharing")
        stop.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(stop)
        hint = QLabel("Scan the code with the phone's camera (same Wi-Fi, or pick the "
                      "100.x Tailscale address). Only someone with this link "
                      "can see the files. On Windows, allow VimCommander through the firewall if asked.")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.qr, 3)
        layout.addLayout(row)
        layout.addWidget(self.link)
        layout.addWidget(hint)
        layout.addWidget(self.log, 1)
        layout.addLayout(bottom)
        self._show_url()
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._tick()

    def _url(self) -> str:
        return self.server.url(self.address.currentText()) if self.server else ""

    def _show_url(self) -> None:
        self.qr.set_text(self._url())
        self.link.setText(self._url())

    def _log(self, text: str) -> None:
        self.log.addItem(text)
        self.log.scrollToBottom()
        if text.startswith("Received"):
            self.received.emit()

    def _tick(self) -> None:
        if self.left <= 0:
            self.close()
            return
        m, s = divmod(self.left, 60)
        self.status.setText(f"Stops in {m}:{s:02d}")
        self.left -= 1

    def reject(self) -> None:
        self.close()  # Esc: stop sharing like the close button

    def closeEvent(self, event):
        self.timer.stop()
        if self.server:
            self.server.stop()
            self.server = None
        super().closeEvent(event)

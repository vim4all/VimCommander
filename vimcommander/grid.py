"""The thumbnail grid (zi or :grid): a pane's items as big tiles, images as
thumbnails, folders as a picture of the first image inside them.

ThumbLoader reads scaled-down images in background threads (QImageReader scales
while decoding, so a big JPEG loads fast) and keeps the newest ones in memory.
GridDelegate draws a tile with the same cursor and tag colors as the list.
"""

import os
import threading
from collections import OrderedDict, deque

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QImageReader, QPalette, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

_EXTS: set[str] = set()
CACHE = 3000  # thumbnails kept in memory
WORKERS = 3


def is_image(name: str) -> bool:
    if not _EXTS:  # asked once Qt runs, so the image plugins are loaded
        _EXTS.update(bytes(f.data()).decode().lower() for f in QImageReader.supportedImageFormats())
        _EXTS.discard("pdf")
    return os.path.splitext(name)[1].lower().lstrip(".") in _EXTS


def first_image(folder: str, limit: int = 400) -> str | None:
    """The first image (by name) directly in a folder, to stand for it."""
    try:
        with os.scandir(folder) as it:
            names = []
            for i, e in enumerate(it):
                if i >= limit:
                    break
                if not e.name.startswith(".") and is_image(e.name):
                    names.append(e.name)
    except OSError:
        return None
    return os.path.join(folder, min(names, key=str.casefold)) if names else None


def load_thumbnail(path: str, size: int) -> QImage:
    """`path` scaled to fit size × size, turned by its EXIF orientation; null if unreadable."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    full = reader.size()
    if full.isValid() and (full.width() > size or full.height() > size):
        reader.setScaledSize(full.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if not image.isNull() and (image.width() > size or image.height() > size):  # rotated, or no size known
        image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


class ThumbLoader(QObject):
    """Thumbnails by (path, size, mtime), loaded on request in background threads."""

    ready = Signal()  # some thumbnails arrived: repaint

    def __init__(self):
        super().__init__()
        self._cache: OrderedDict[tuple, QImage | None] = OrderedDict()
        self._queue: deque[tuple] = deque()
        self._queued: set[tuple] = set()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        for _ in range(WORKERS):
            threading.Thread(target=self._work, daemon=True).start()

    def get(self, path: str, is_dir: bool, mtime: float, size: int) -> QImage | None:
        """The thumbnail if it's ready (None if it isn't, or there is none); asks for it if needed."""
        key = (path, size, mtime, is_dir)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            if key not in self._queued:
                self._queued.add(key)
                self._queue.append(key)  # newest first: what is on screen now
                self._wake.notify()
        return None

    def forget_queue(self) -> None:
        """Drop the waiting requests (the pane shows something else now)."""
        with self._lock:
            self._queue.clear()
            self._queued.clear()

    def _work(self) -> None:
        while True:
            with self._lock:
                while not self._queue:
                    self._wake.wait()
                key = self._queue.pop()
            path, size, _, is_dir = key
            source = first_image(path) if is_dir else path
            image = load_thumbnail(source, size) if source else None
            if image is not None and image.isNull():
                image = None
            with self._lock:
                self._queued.discard(key)
                self._cache[key] = image
                while len(self._cache) > CACHE:
                    self._cache.popitem(last=False)
            self.ready.emit()


class GridDelegate(QStyledItemDelegate):
    """A tile: the thumbnail (or a big icon) and the name below it."""

    def __init__(self, view, loader: ThumbLoader):
        super().__init__(view)
        self.loader = loader

    def sizeHint(self, option, index):
        return self.parent().gridSize()

    def paint(self, painter, option, index):
        from .pane import COLORS  # pane imports this module
        view = self.parent()
        model = index.model()
        entry = model.entries[index.row()]
        rect = option.rect.adjusted(3, 3, -3, -3)
        size = view.thumb_size
        tagged = bool(option.state & QStyle.StateFlag.State_Selected)
        cursor = index.row() == view.currentIndex().row()
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        background = None
        if cursor:
            background = COLORS.get("cursor") if view.active else COLORS.get("cursor_inactive")
        elif tagged:
            background = COLORS.get("tagged_background")
        if background is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(QRectF(rect), 6, 6)
        if tagged and COLORS.get("tagged") is not None:
            pen = QPen(COLORS["tagged"])
            pen.setWidthF(2.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 6, 6)
        # The picture
        box = QRect(rect.left() + (rect.width() - size) // 2, rect.top() + 4, size, size)
        image = None
        if not entry.is_parent and not model.in_archive and not model.remote and (
                entry.is_dir or is_image(entry.name)):
            image = self.loader.get(entry.path, entry.is_dir, entry.mtime, size)
        if image is not None:
            target = QRect(0, 0, image.width(), image.height())
            target.moveCenter(box.center())
            if entry.is_dir:  # a folder's picture sits on a folder icon
                icon = model.data(index, Qt.ItemDataRole.DecorationRole)
                if icon is not None:
                    icon.paint(painter, box)
                inner = target.size().scaled(QSize(int(size * 0.62), int(size * 0.62)),
                                             Qt.AspectRatioMode.KeepAspectRatio)
                target = QRect(0, 0, inner.width(), inner.height())
                target.moveCenter(box.center() + QPoint(0, size // 12))
            painter.drawImage(target, image)
        else:
            icon = model.data(index, Qt.ItemDataRole.DecorationRole)
            if icon is not None:
                side = int(size * 0.6)
                icon_box = QRect(0, 0, side, side)
                icon_box.moveCenter(box.center())
                icon.paint(painter, icon_box)
        # The name
        font = QFont(option.font)
        color = option.palette.color(QPalette.ColorRole.Text)
        fg = model.data(index, Qt.ItemDataRole.ForegroundRole)
        if fg is not None:
            color = QColor(fg)
        if tagged:
            font.setBold(True)
            color = COLORS.get("tagged") or color
        if cursor and view.active:
            color = COLORS.get("cursor_text") or color
        if entry.is_dir:
            font.setBold(True)
        painter.setFont(font)
        painter.setPen(color)
        metrics = QFontMetrics(font)
        text_rect = QRect(rect.left() + 4, box.bottom() + 4, rect.width() - 8, rect.bottom() - box.bottom() - 4)
        name = entry.shown or entry.name
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, text_rect.width()))
        painter.restore()

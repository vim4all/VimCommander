"""Quick view (Ctrl-q): shows the file under the cursor in the other pane.

Text files show their first part, images are scaled to fit, folders and other
files show a short summary.
"""

import os
from datetime import datetime

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QFontDatabase, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QStackedWidget, QVBoxLayout, QWidget

from . import archive, graphview, remote

TEXT_LIMIT = 256 * 1024
IMAGE_LIMIT = 64 * 1024 * 1024


def _size(n: int) -> str:
    from .pane import human_size  # pane imports this module
    return human_size(n)


class ImageLabel(QLabel):
    """Shows a pixmap scaled to fit, rescaling when resized."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)
        self.pixmap_full = QPixmap()

    def set_image(self, image: QImage) -> None:
        self.pixmap_full = QPixmap.fromImage(image)
        self._rescale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self.pixmap_full.isNull():
            self.clear()
            return
        size = self.pixmap_full.size().boundedTo(self.size() - QSize(8, 8))
        self.setPixmap(self.pixmap_full.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))


class QuickView(QWidget):
    def __init__(self):
        super().__init__()
        self.current: str | None = None
        self.title = QLabel()
        self.title.setStyleSheet("padding: 3px 6px; font-weight: bold;")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.image = ImageLabel()
        self.info = QLabel()
        self.info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.info.setWordWrap(True)
        self.info.setStyleSheet("padding: 8px;")
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.graph = graphview.GraphView()
        self.show_hidden = False  # set by the main window, for the graph's scan
        self.stack = QStackedWidget()
        for w in (self.text, self.image, self.info, self.graph):
            self.stack.addWidget(w)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title)
        layout.addWidget(self.stack, 1)

    def clear(self) -> None:
        self.current = None
        self.text.clear()
        self.image.set_image(QImage())
        self.graph.stop()

    def show_path(self, path: str | None, is_dir: bool = False) -> None:
        if path == self.current:
            return
        self.current = path
        if not path:
            self.title.setText("")
            self._info("Nothing to show")
            return
        self.title.setText(os.path.basename(path) or path)
        self.title.show()
        if remote.is_remote(path):
            self.graph.stop()
            try:
                self._remote(path, is_dir)
            except OSError as e:
                self._info(f"Cannot read this file: {e.strerror or e}")
            return
        in_archive = archive.inside(path) is not None
        if not (is_dir and graphview.OPTIONS["enabled"] and not archive.locate(path)):
            self.graph.stop()
        try:
            if is_dir and graphview.OPTIONS["enabled"] and not archive.locate(path):
                from .pane import DIR_SIZES  # pane imports this module
                self.title.hide()  # the graph has its own title row
                self.stack.setCurrentWidget(self.graph)
                self.graph.show_folder(path, self.show_hidden, DIR_SIZES)
            elif is_dir:
                self._folder(path, in_archive)
            elif not self._try_image(path, in_archive):
                self._text_or_info(path, in_archive)
        except OSError as e:
            self._info(f"Cannot read this file: {e.strerror or e}")

    def scroll_pages(self, pages: int) -> None:
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.value() + pages * bar.pageStep())

    # Kinds of preview

    def _info(self, html: str) -> None:
        self.info.setText(html)
        self.stack.setCurrentWidget(self.info)

    def _remote(self, path: str, is_dir: bool) -> None:
        """A file on an SSH server: its first part only (images up to IMAGE_LIMIT)."""
        if is_dir:
            items = remote.REMOTE.list_dir(path)
            folders = sum(1 for i in items if i[1])
            self._info(f"<b>Remote folder</b><br>{remote.parse(path).key}:{remote.parse(path).path}<br>"
                       f"{folders} folder(s), {len(items) - folders} file(s)")
            return
        ext = os.path.splitext(path)[1].lower().lstrip(".").encode()
        if ext in {f.data() for f in QImageReader.supportedImageFormats()}:
            image = QImage.fromData(QByteArray(remote.REMOTE.read_head(path, IMAGE_LIMIT)))
            if not image.isNull():
                self.title.setText(f"{os.path.basename(path)}   {image.width()} × {image.height()}")
                self.image.set_image(image)
                self.stack.setCurrentWidget(self.image)
                return
        attrs = remote.REMOTE.stat(path)
        size = attrs.st_size if attrs else 0
        data = remote.REMOTE.read_head(path, TEXT_LIMIT)
        if b"\0" in data[:8192]:
            self._info(f"<b>Binary file</b><br>{_size(size)}")
            return
        text = data.decode("utf-8", errors="replace")
        if size > TEXT_LIMIT:
            text += f"\n\n… (first {_size(TEXT_LIMIT)} of {_size(size)} shown)"
        self.text.setPlainText(text)
        self.stack.setCurrentWidget(self.text)

    def _folder(self, path: str, in_archive: bool) -> None:
        if in_archive:
            items = archive.list_dir(*archive.locate(path))
            folders = sum(1 for i in items if i[1])
            files = [i for i in items if not i[1]]
            total = sum(i[2] for i in files)
        else:
            folders, files, total = 0, [], 0
            with os.scandir(path) as it:
                for i, e in enumerate(it):
                    if i >= 20000:
                        break
                    try:
                        if e.is_dir():
                            folders += 1
                        else:
                            files.append(e)
                            total += e.stat().st_size
                    except OSError:
                        pass
        self._info(f"<b>Folder</b><br>{folders} folder(s), {len(files)} file(s)<br>"
                   f"{_size(total)} in the files directly inside (gs adds up subfolders)")

    def _try_image(self, path: str, in_archive: bool) -> bool:
        ext = os.path.splitext(path)[1].lower().lstrip(".").encode()
        if ext not in {f.data() for f in QImageReader.supportedImageFormats()}:
            return False
        if in_archive:
            image = QImage.fromData(QByteArray(archive.read(path, IMAGE_LIMIT)))
        else:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            image = reader.read()
        if image.isNull():
            return False
        self.title.setText(f"{os.path.basename(path)}   {image.width()} × {image.height()}")
        self.image.set_image(image)
        self.stack.setCurrentWidget(self.image)
        return True

    def _text_or_info(self, path: str, in_archive: bool) -> None:
        if in_archive:
            data = archive.read(path, TEXT_LIMIT)
            size, mtime = len(data), None
        else:
            st = os.stat(path)
            size, mtime = st.st_size, st.st_mtime
            with open(path, "rb") as f:
                data = f.read(TEXT_LIMIT)
        if b"\0" in data[:8192]:
            when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else ""
            hexdump = "<br>".join(
                f"{i:08x}&nbsp; " + " ".join(f"{b:02x}" for b in data[i:i + 16])
                for i in range(0, min(len(data), 256), 16))
            self._info(f"<b>Binary file</b><br>{_size(size)}<br>{when}"
                       f"<br><br><code>{hexdump}</code>")
            return
        text = data.decode("utf-8", errors="replace")
        if size > TEXT_LIMIT:
            text += f"\n\n… (first {_size(TEXT_LIMIT)} of {_size(size)} shown)"
        self.text.setPlainText(text)
        self.stack.setCurrentWidget(self.text)

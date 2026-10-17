"""Quick view (Ctrl-q): shows the file under the cursor in the other pane.

Text files show their first part, images are scaled to fit, folders and other
files show a short summary. Markdown is rendered (Obsidian links work), CSV / TSV files show as a table
(or a plot, zp), PDFs show their first pages, rendered by PDFium (pypdfium2) on a worker thread.
Jupyter notebooks show their cells and outputs, STM32CubeMX projects their pins and clocks.
"""

import html
import os
import queue
import threading
from datetime import datetime

from PySide6.QtCore import QByteArray, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QDesktopServices, QFontDatabase, QImage, QImageReader, QPalette, QPixmap, QTextCursor,
                           QTextDocument, QTextFormat)
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QLabel, QPlainTextEdit, QScrollArea, QSizePolicy,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from . import archive, cubemx, graphview, ltspice, mdnotes, memmap, notebook, pdftools, remote, tabular
from .memmapview import MemMapView
from .plotview import PlotView

TEXT_LIMIT = 256 * 1024
IMAGE_LIMIT = 64 * 1024 * 1024
PDF_LIMIT = 256 * 1024 * 1024  # bigger PDFs inside a zip are not previewed
PDF_PAGES = 30  # pages rendered in the preview
HEX_LIMIT = 64 * 1024  # bytes shown in the hex view
HEX = {"on": False}  # :hex: every file in hex, not only binary ones
PLOT = {"on": False}  # zp: CSV files as a plot instead of a table
ARCHIVE_PLOT_LIMIT = 256 * 1024 * 1024  # a CSV inside a zip is read up to this for a plot
NOTEBOOK_LIMIT = 64 * 1024 * 1024  # bigger notebooks show as text


def hexdump(data: bytes, offset: int = 0) -> str:
    """Offset, 16 bytes in hex (in two groups of 8) and as text, per line, like hexdump -C."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk[:8]) + "  " + " ".join(f"{b:02x}" for b in chunk[8:])
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset + i:08x}  {hex_part:<48}  |{text}|")
    return "\n".join(lines)


def _size(n: int) -> str:
    from .pane import human_size  # pane imports this module
    return human_size(n)


class TitleLabel(QLabel):
    """One line that never widens its pane: a long text is shortened in the
    middle ("long_na…me.pdf   12 pages"), the full text is in the tooltip."""

    def __init__(self):
        super().__init__()
        self.full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(1)

    def setText(self, text: str) -> None:
        self.full = text
        self.setToolTip(text if text else "")
        self._elide()

    def text(self) -> str:
        return self.full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        m = self.contentsMargins()
        width = self.width() - m.left() - m.right() - 12  # the style sheet's padding
        super().setText(self.fontMetrics().elidedText(self.full, Qt.TextElideMode.ElideMiddle, max(width, 10)))


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


class PdfRenderer(QObject):
    """Renders PDF pages on one worker thread (PDFium is not thread-safe, so
    every call into it happens there). Only the latest request is worked on;
    pages carry its token so stale ones are dropped."""

    page_ready = Signal(int, int, int, QImage)  # token, page index, page count, image
    failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.token = 0
        self.requests: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def request(self, source, width: int) -> int:
        """source: a file path or the PDF's bytes. Returns the request's token."""
        self.token += 1
        self.requests.put((self.token, source, width))
        return self.token

    def cancel(self) -> None:
        self.token += 1

    def _run(self) -> None:
        pdfium = loaded = None
        while True:
            token, source, width = self.requests.get()
            if token != self.token:
                continue
            if not loaded:  # on the first PDF: importing it at startup slowed the window's first paint
                loaded = True
                try:
                    import pypdfium2 as pdfium
                except ImportError:
                    pdfium = None
            if pdfium is None:
                self.failed.emit(token, "PDF preview needs pypdfium2 (pip install pypdfium2)")
                continue
            try:
                with pdftools.LOCK:
                    pdf = pdfium.PdfDocument(source)
            except Exception as e:  # PdfiumError: damaged, encrypted…
                self.failed.emit(token, f"Cannot read this PDF: {e}")
                continue
            try:
                with pdftools.LOCK:
                    count = len(pdf)
                for i in range(min(count, PDF_PAGES)):
                    if token != self.token:
                        break
                    with pdftools.LOCK:  # per page, so a :pdfmerge can run in between
                        page = pdf[i]
                        scale = width / max(page.get_width(), 1)
                        bitmap = page.render(scale=scale, rev_byteorder=True)
                        fmt = (QImage.Format.Format_RGBA8888 if bitmap.n_channels == 4
                               else QImage.Format.Format_RGB888)
                        image = QImage(bytes(bitmap.buffer), bitmap.width, bitmap.height,
                                       bitmap.stride, fmt).copy()
                    self.page_ready.emit(token, i, count, image)
            except Exception as e:
                self.failed.emit(token, f"Cannot render this PDF: {e}")
            finally:
                with pdftools.LOCK:
                    pdf.close()


_renderer: PdfRenderer | None = None


def renderer() -> PdfRenderer:
    global _renderer
    if _renderer is None:
        _renderer = PdfRenderer()
    return _renderer


class PdfView(QScrollArea):
    """The first PDF_PAGES pages of a PDF, one under the other, fit to width."""

    status = Signal(str)  # "12 pages" for the title

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: palette(dark); }")
        self.pages = QWidget()
        self.layout_ = QVBoxLayout(self.pages)
        self.layout_.setContentsMargins(6, 6, 6, 6)
        self.layout_.setSpacing(6)
        self.layout_.addStretch(1)
        self.setWidget(self.pages)
        self.source = None
        self.token = 0
        self.width_rendered = 0
        self.resize_timer = QTimer(self, singleShot=True, interval=200)
        self.resize_timer.timeout.connect(self._rerender)
        renderer().page_ready.connect(self._page)
        renderer().failed.connect(self._failed)

    def _clear_pages(self) -> None:
        while self.layout_.count() > 1:
            self.layout_.takeAt(0).widget().deleteLater()

    def _target_width(self) -> int:
        return max(100, self.viewport().width() - 12)

    def show_pdf(self, source) -> None:
        self.source = source
        self._clear_pages()
        self.verticalScrollBar().setValue(0)
        self._render()

    def stop(self) -> None:
        if self.source is not None:
            self.source = None
            renderer().cancel()
            self._clear_pages()

    def _render(self) -> None:
        self.width_rendered = self._target_width()
        dpr = self.devicePixelRatioF()
        self.token = renderer().request(self.source, int(self.width_rendered * dpr))

    def _rerender(self) -> None:
        if self.source is not None and abs(self._target_width() - self.width_rendered) > 8:
            scroll = self.verticalScrollBar()
            fraction = scroll.value() / max(scroll.maximum(), 1)
            self._clear_pages()
            self._render()
            QTimer.singleShot(0, lambda: scroll.setValue(int(fraction * scroll.maximum())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.source is not None:
            self.resize_timer.start()

    def _page(self, token: int, index: int, count: int, image: QImage) -> None:
        if token != self.token:
            return
        image.setDevicePixelRatio(self.devicePixelRatioF())
        label = QLabel()
        label.setPixmap(QPixmap.fromImage(image))
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.layout_.insertWidget(self.layout_.count() - 1, label)
        if index == 0:
            more = f", first {PDF_PAGES} shown" if count > PDF_PAGES else ""
            self.status.emit(f"{count} page{'s' if count != 1 else ''}{more}")

    def _failed(self, token: int, message: str) -> None:
        if token == self.token:
            self._clear_pages()
            label = QLabel(message)
            label.setWordWrap(True)
            self.layout_.insertWidget(0, label)


class MarkdownView(QTextBrowser):
    """A rendered note. Wikilinks, relative links and images resolve like in
    Obsidian; a click on a note emits open_requested, web links open outside."""

    open_requested = Signal(str)  # a local file path

    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(10)
        self.note: str | None = None  # local path, None for remote or zip notes
        self.images: list[bytes] = []  # a notebook's images, nbimage:N in its text
        self.anchorClicked.connect(self._clicked)

    def show_note(self, text: str, note: str | None) -> None:
        self.note, self.images = note, []
        base = QUrl.fromLocalFile(os.path.dirname(note) + "/") if note else QUrl()
        self.document().setBaseUrl(base)
        self.document().setMarkdown(mdnotes.prepare(text))
        self.verticalScrollBar().setValue(0)

    def show_notebook(self, nb: notebook.Notebook, path: str | None) -> None:
        """A Jupyter notebook: code cells shaded, outputs indented below them."""
        self.note, self.images = path, nb.images
        self.document().setBaseUrl(QUrl.fromLocalFile(os.path.dirname(path) + "/") if path else QUrl())
        self.document().setMarkdown(nb.markdown)
        shade = self.palette().color(QPalette.ColorRole.AlternateBase)
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        block = self.document().begin()
        while block.isValid():
            fmt = block.blockFormat()
            if fmt.hasProperty(QTextFormat.Property.BlockCodeFence):
                if fmt.stringProperty(QTextFormat.Property.BlockCodeLanguage) == "output":
                    fmt.setLeftMargin(fmt.leftMargin() + 18)
                else:
                    fmt.setBackground(shade)
                cursor.setPosition(block.position())
                cursor.setBlockFormat(fmt)
            block = block.next()
        cursor.endEditBlock()
        self.moveCursor(QTextCursor.MoveOperation.Start)  # else the view follows the edits to the end
        self.verticalScrollBar().setValue(0)

    def show_html(self, text: str) -> None:
        self.note, self.images = None, []
        self.document().setBaseUrl(QUrl())
        self.setHtml(text)
        self.verticalScrollBar().setValue(0)

    def _target(self, url: QUrl) -> str | None:
        if not self.note:
            return None
        if url.scheme() == "wikilink":
            return mdnotes.resolve(url.path(), self.note)
        if url.isLocalFile():
            return url.toLocalFile() if os.path.exists(url.toLocalFile()) else None
        if url.isRelative():
            return mdnotes.resolve(url.path(), self.note) if url.path() else None
        return None

    def loadResource(self, kind, url):
        if kind == QTextDocument.ResourceType.ImageResource and url.scheme() == "nbimage":
            try:
                image = QImage.fromData(QByteArray(self.images[int(url.path())]))
            except (ValueError, IndexError):
                return None
            limit = max(100, self.viewport().width() - 30)
            if image.width() > limit:
                image = image.scaledToWidth(limit, Qt.TransformationMode.SmoothTransformation)
            return image if not image.isNull() else None
        if kind == QTextDocument.ResourceType.ImageResource:
            path = self._target(url) if self.note else None
            if path:
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                size = reader.size()
                limit = max(100, self.viewport().width() - 30)
                if size.isValid() and size.width() > limit:
                    reader.setScaledSize(size.scaled(limit, 10 ** 6, Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
                if not image.isNull():
                    return image
            return None
        return super().loadResource(kind, url)

    def _clicked(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto", "obsidian"):
            QDesktopServices.openUrl(url)
        elif not url.path() and url.fragment():
            self.scrollToAnchor(url.fragment())
        elif path := self._target(url):
            self.open_requested.emit(path)


class CsvView(QTableWidget):
    """A CSV file as a table; hovering a column header shows the column's summary."""

    def __init__(self):
        super().__init__()
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 4)

    def show_table(self, table: tabular.Table) -> None:
        self.clear()
        self.setRowCount(len(table.rows))
        self.setColumnCount(len(table.header))
        self.setHorizontalHeaderLabels(table.header)
        for c, name in enumerate(table.header):
            self.horizontalHeaderItem(c).setToolTip(f"{name}: {tabular.column_stats(table, c)}")
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row):
                item = QTableWidgetItem(cell)
                if tabular._number(cell) is not None:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(r, c, item)
        self.resizeColumnsToContents()
        for c in range(self.columnCount()):
            self.setColumnWidth(c, min(self.columnWidth(c), 300))


class QuickView(QWidget):
    def __init__(self):
        super().__init__()
        self.current: str | None = None
        self.title = TitleLabel()
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
        self.markdown = MarkdownView()
        self.pdf = PdfView()
        self.csv = CsvView()
        self.memmap = MemMapView()  # firmware .map / .elf
        self.memmap.summary.connect(lambda text: self.title.setText(
            f"{os.path.basename(self.current or '')}   {text}"))
        self.memmap.not_a_map.connect(self._not_a_map)
        self.plot = PlotView()  # CSV as a plot (zp); scrolls when there are many columns
        self.plot.summary.connect(lambda text: self.title.setText(
            f"{os.path.basename(self.current or '')}   {text}"))
        self.plot.not_numeric.connect(self._not_numeric)
        self.plot_area = QScrollArea()
        self.plot_area.setWidget(self.plot)
        self.plot_area.setWidgetResizable(True)
        self.plot_area.setFrameShape(QFrame.Shape.NoFrame)
        self.plot_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pdf.status.connect(lambda text: self.title.setText(
            f"{os.path.basename(self.current or '')}   {text}"))
        self.show_hidden = False  # set by the main window, for the graph's scan
        self.stack = QStackedWidget()
        for w in (self.text, self.image, self.info, self.graph, self.markdown, self.pdf, self.csv, self.memmap,
                  self.plot_area):
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
        self.pdf.stop()
        self.memmap.stop()
        self.plot.stop()
        self.markdown.clear()
        self.csv.clear()

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
        if not path.lower().endswith(".pdf") or is_dir:
            self.pdf.stop()
        self.memmap.stop()
        self.plot.stop()
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
            elif HEX["on"]:
                self._text_or_info(path, in_archive)
            elif path.lower().endswith(".pdf"):
                self._pdf(path, in_archive)
            elif memmap.is_candidate(path) and not in_archive:
                self.stack.setCurrentWidget(self.memmap)
                self.memmap.load(path)  # not a linker map / ELF after all: _not_a_map shows text
            elif ltspice.is_raw(path):
                self._raw(path, in_archive)  # not LTspice after all: _not_numeric shows hex
            elif PLOT["on"] and tabular.is_table(path):
                self._plot(path, in_archive)  # no numbers in it after all: _not_numeric shows the table
            elif notebook.is_notebook(path):
                self._notebook(path, in_archive)
            elif cubemx.is_ioc(path):
                self._ioc(path, in_archive)
            elif not self._try_image(path, in_archive):
                self._text_or_info(path, in_archive)
        except OSError as e:
            self._info(f"Cannot read this file: {e.strerror or e}")

    def scroll_pages(self, pages: int) -> None:
        widget = self.stack.currentWidget()
        bar = (widget if widget in (self.text, self.markdown, self.pdf, self.csv, self.plot_area)
               else self.text).verticalScrollBar()
        bar.setValue(bar.value() + pages * bar.pageStep())

    # Kinds of preview

    def _not_a_map(self, path: str) -> None:
        if path == self.current:
            try:
                self._text_or_info(path, False)
            except OSError as e:
                self._info(f"Cannot read this file: {e.strerror or e}")

    def _plot(self, path: str, in_archive: bool) -> None:
        source = archive.read(path, ARCHIVE_PLOT_LIMIT) if in_archive else path
        self.stack.setCurrentWidget(self.plot_area)
        self.plot.load(path, source)

    def _raw(self, path: str, in_archive: bool) -> None:
        """An LTspice .raw file: its traces as a plot (an operating point as a table)."""
        source = archive.read(path, ARCHIVE_PLOT_LIMIT) if in_archive else path
        self.stack.setCurrentWidget(self.plot_area)
        self.plot.load(path, source, ltspice.read_raw)

    def _operating_point(self, path: str) -> bool:
        """An LTspice .op result (one point): its values as a table."""
        in_archive = archive.inside(path) is not None
        if in_archive:
            data = archive.read(path, TEXT_LIMIT)
        else:
            with open(path, "rb") as f:
                data = f.read(TEXT_LIMIT)
        values = ltspice.operating_point(data)
        if not values:
            return False
        rows = "".join(f"<tr><td><b>{html.escape(name)}</b></td><td align='right'>{tabular.si(v)}</td>"
                       f"<td>{unit}</td></tr>" for name, v, unit in values)
        self.title.setText(f"{os.path.basename(path)}   operating point, {len(values)} values")
        self.markdown.show_html(f"<table cellspacing='0' cellpadding='3'>{rows}</table>")
        self.stack.setCurrentWidget(self.markdown)
        return True

    def _not_numeric(self, path: str) -> None:
        if path == self.current and ltspice.is_raw(path):
            try:
                if not self._operating_point(path):
                    self._text_or_info(path, archive.inside(path) is not None)  # another program's .raw
            except OSError as e:
                self._info(f"Cannot read this file: {e.strerror or e}")
            return
        if path == self.current:
            try:
                self._text_or_info(path, archive.inside(path) is not None)
            except OSError as e:
                self._info(f"Cannot read this file: {e.strerror or e}")
                return
            self.title.setText(self.title.text() + "   (no numbers to plot)")

    def _notebook(self, path: str, in_archive: bool) -> None:
        """A Jupyter notebook's cells and outputs; text if it is too big or not one."""
        if in_archive:
            data = archive.read(path, NOTEBOOK_LIMIT)
        elif os.path.getsize(path) <= NOTEBOOK_LIMIT:
            with open(path, "rb") as f:
                data = f.read()
        else:
            data = b""
        try:
            nb = notebook.convert(data)
        except ValueError:
            self._text_or_info(path, in_archive)
            return
        self.title.setText(f"{os.path.basename(path)}   {nb.summary}")
        self.markdown.show_notebook(nb, None if in_archive else path)
        self.stack.setCurrentWidget(self.markdown)

    def _ioc(self, path: str, in_archive: bool) -> None:
        """An STM32CubeMX project: chip, clocks, pins, peripherals."""
        if in_archive:
            data = archive.read(path, TEXT_LIMIT * 4)
        else:
            with open(path, "rb") as f:
                data = f.read(TEXT_LIMIT * 4)
        project = cubemx.parse(data.decode("utf-8", errors="replace"))
        if project is None:
            self._text_or_info(path, in_archive)
            return
        self.title.setText(f"{os.path.basename(path)}   {cubemx.summary(project)}")
        self.markdown.show_html(cubemx.to_html(project))
        self.stack.setCurrentWidget(self.markdown)

    def _info(self, html: str) -> None:
        self.info.setText(html)
        self.stack.setCurrentWidget(self.info)

    def _remote(self, path: str, is_dir: bool) -> None:
        """A file on an SSH server: its first part only (images up to IMAGE_LIMIT)."""
        if is_dir:
            items = remote.REMOTE.list_dir(path)
            folders = sum(1 for i in items if i[1])
            self._info(f"<b>Remote folder</b><br>{remote.parse(path).url()}<br>"
                       f"{folders} folder(s), {len(items) - folders} file(s)")
            return
        ext = os.path.splitext(path)[1].lower().lstrip(".").encode()
        if not HEX["on"] and ext in {f.data() for f in QImageReader.supportedImageFormats()}:
            image = QImage.fromData(QByteArray(remote.REMOTE.read_head(path, IMAGE_LIMIT)))
            if not image.isNull():
                self.title.setText(f"{os.path.basename(path)}   {image.width()} × {image.height()}")
                self.image.set_image(image)
                self.stack.setCurrentWidget(self.image)
                return
        attrs = remote.REMOTE.stat(path)
        size = attrs.st_size if attrs else 0
        data = remote.REMOTE.read_head(path, TEXT_LIMIT)
        if HEX["on"] or b"\0" in data[:8192]:
            self._hex(path, data, size, None)
            return
        self._show_text(data.decode("utf-8", errors="replace"), size, path, None)

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
        if HEX["on"] or b"\0" in data[:8192]:
            self._hex(path, data, size, mtime)
            return
        self._show_text(data.decode("utf-8", errors="replace"), size, path,
                        None if in_archive else path)

    def _hex(self, path: str, data: bytes, size: int, mtime: float | None) -> None:
        """A hex dump of the file's start (:hex shows text files this way too)."""
        data = data[:HEX_LIMIT]
        when = "   " + datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else ""
        shown = f"   first {_size(len(data))} in hex" if size > len(data) else "   hex"
        self.title.setText(f"{os.path.basename(path)}   {_size(size)}{when}{shown}")
        self.text.setPlainText(hexdump(data))
        self.stack.setCurrentWidget(self.text)

    def _show_text(self, text: str, size: int, path: str, local: str | None) -> None:
        """Plain text, or a rendered note for Markdown (links only work for local files)."""
        if tabular.is_table(path):
            self._csv(text, size, path)
            return
        if size > TEXT_LIMIT:
            text += f"\n\n… (first {_size(TEXT_LIMIT)} of {_size(size)} shown)"
        if mdnotes.is_markdown(path):
            self.markdown.show_note(text, local)
            self.stack.setCurrentWidget(self.markdown)
        else:
            self.text.setPlainText(text)
            self.stack.setCurrentWidget(self.text)

    def _csv(self, text: str, size: int, path: str) -> None:
        table = tabular.parse(text, path, truncated=size > TEXT_LIMIT)
        if not table.header:
            self.text.setPlainText(text)
            self.stack.setCurrentWidget(self.text)
            return
        sep = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "bar"}.get(table.delimiter, table.delimiter)
        shown = "" if table.complete else "   first part"
        self.title.setText(f"{os.path.basename(path)}   {len(table.rows)} rows × {len(table.header)} columns"
                           f"   {sep}-separated   {_size(size)}{shown}   (hover a column for its summary)")
        self.csv.show_table(table)
        self.stack.setCurrentWidget(self.csv)

    def _pdf(self, path: str, in_archive: bool) -> None:
        if in_archive:
            data = archive.read(path, PDF_LIMIT)
            if len(data) >= PDF_LIMIT:
                self._info("<b>PDF</b><br>Too big to preview inside an archive")
                return
            source = data
        else:
            source = path
        self.stack.setCurrentWidget(self.pdf)
        self.pdf.show_pdf(source)

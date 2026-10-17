"""PDF work with PDFium (pypdfium2), no Qt: :pdfmerge joins PDFs into one,
:pdfextract copies some of a PDF's pages into a new one.

PDFium is not thread-safe, and the quick view renders pages on its own thread,
so every call into it holds LOCK.
"""

import os
import re
import tempfile
import threading

LOCK = threading.RLock()
_RANGE = re.compile(r"^(\d*)\s*-\s*(\d*|\$)$")


def is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def parse_pages(spec: str, count: int) -> list[int]:
    """Zero-based page indexes for a page list like "3", "2-5", "1,4,7-", "-3",
    "5-$" or "9-7" (backwards), in the order written. ValueError if it is
    malformed or names a page the document doesn't have."""
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if part.isdigit() or part == "$":
            first = last = count if part == "$" else int(part)
        elif m := _RANGE.match(part):
            first = int(m.group(1)) if m.group(1) else 1
            last = count if m.group(2) in ("", "$") else int(m.group(2))
        else:
            raise ValueError(f"not a page or a range of pages: {part}")
        for n in (first, last):
            if not 1 <= n <= count:
                raise ValueError(f"there is no page {n} (the PDF has {count})")
        step = 1 if last >= first else -1
        pages.extend(range(first - 1, last - 1 + step, step))
    if not pages:
        raise ValueError("no pages given, e.g. 3, 2-5 or 1,4,7-")
    return pages


def _save(pdf, dest: str) -> None:
    """Write `pdf` through a temporary file, so a failed save leaves nothing
    half-written at `dest`."""
    fd, tmp = tempfile.mkstemp(suffix=".vctmp", dir=os.path.dirname(dest) or ".")
    try:
        with os.fdopen(fd, "wb") as f, LOCK:
            pdf.save(f)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _open(pdfium, path: str):
    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as e:
        raise OSError(f"can't read this PDF ({e})") from None


def extract(src: str, spec: str, dest: str) -> int:
    """Save the pages `spec` (see parse_pages) of `src` as the new PDF `dest`.
    Returns the number of pages; OSError if it can't (also for a bad `spec`)."""
    import pypdfium2 as pdfium  # here, not at import: it is slow to load
    with LOCK:
        doc = _open(pdfium, src)
        new = None
        try:
            try:
                pages = parse_pages(spec, len(doc))
            except ValueError as e:
                raise OSError(str(e)) from None
            new = pdfium.PdfDocument.new()
            try:
                new.import_pages(doc, pages=pages)
            except pdfium.PdfiumError as e:
                raise OSError(f"can't copy its pages ({e})") from None
            try:
                _save(new, dest)
            except pdfium.PdfiumError as e:
                raise OSError(f"can't save {os.path.basename(dest)} ({e})") from None
        finally:
            if new is not None:
                new.close()
            doc.close()
    return len(pages)


class Merger:
    """Collects the pages of several PDFs, in order, and saves them as one."""

    def __init__(self):
        import pypdfium2 as pdfium  # here, not at import: it is slow to load
        self._pdfium = pdfium
        with LOCK:
            self.pdf = pdfium.PdfDocument.new()
        self.pages = 0

    def add(self, path: str) -> int:
        """Append all pages of `path`. Returns its page count; OSError if it
        can't be read (damaged, encrypted…)."""
        with LOCK:
            src = _open(self._pdfium, path)
            try:
                count = len(src)
                self.pdf.import_pages(src, index=self.pages)
            except self._pdfium.PdfiumError as e:
                raise OSError(f"can't copy its pages ({e})") from None
            finally:
                src.close()
        self.pages += count
        return count

    def save(self, dest: str) -> None:
        _save(self.pdf, dest)

    def close(self) -> None:
        with LOCK:
            self.pdf.close()

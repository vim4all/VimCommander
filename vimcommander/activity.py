""":activity (gr) — when did files below this folder change?

A GitHub-style calendar of the last year (one square per day, darker = more
files changed) and the most recently changed files, grouped by day. Click a
day to list only its files; Enter or double-click on a file jumps to it.
"""

import heapq
import os
import threading
import time
from datetime import date, datetime, timedelta

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPalette, QShortcut
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QToolTip, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .graph import SKIP_DIRS
from .sizing import fit_window

RECENT = 3000  # files kept for the list
WEEKS = 53


def scan_activity(root: str, show_hidden: bool = False, stop: threading.Event | None = None,
                  limit: int = 400_000, budget: float = 20.0):
    """({date: files changed that day}, newest files as [(mtime, path)], files seen, finished)."""
    days: dict[date, int] = {}
    newest: list[tuple[float, str]] = []  # a min-heap of the RECENT newest
    seen = 0
    deadline = time.monotonic() + budget
    for folder, dirs, files in os.walk(root):
        if (stop and stop.is_set()) or time.monotonic() > deadline or seen >= limit:
            return days, sorted(newest, reverse=True), seen, False
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (show_hidden or not d.startswith("."))]
        for f in files:
            if not show_hidden and f.startswith("."):
                continue
            path = os.path.join(folder, f)
            try:
                mtime = os.stat(path, follow_symlinks=False).st_mtime
            except OSError:
                continue
            seen += 1
            day = date.fromtimestamp(mtime)
            days[day] = days.get(day, 0) + 1
            if len(newest) < RECENT:
                heapq.heappush(newest, (mtime, path))
            elif mtime > newest[0][0]:
                heapq.heapreplace(newest, (mtime, path))
    return days, sorted(newest, reverse=True), seen, True


def files_on_day(root: str, day: date, show_hidden: bool = False,
                 stop: threading.Event | None = None, limit: int = 5000) -> list[tuple[float, str]]:
    """Files below `root` last changed on `day`, newest first."""
    lo = datetime.combine(day, datetime.min.time()).timestamp()
    hi = lo + 86400
    found = []
    for folder, dirs, files in os.walk(root):
        if stop and stop.is_set():
            break
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (show_hidden or not d.startswith("."))]
        for f in files:
            if not show_hidden and f.startswith("."):
                continue
            path = os.path.join(folder, f)
            try:
                mtime = os.stat(path, follow_symlinks=False).st_mtime
            except OSError:
                continue
            if lo <= mtime < hi:
                found.append((mtime, path))
                if len(found) >= limit:
                    return sorted(found, reverse=True)
    return sorted(found, reverse=True)


def day_label(day: date, today: date) -> str:
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return day.strftime("%a %Y-%m-%d")


class Heatmap(QWidget):
    """53 weeks × 7 days of squares, like GitHub's contribution graph."""

    day_clicked = Signal(object)  # date, or None to show all

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.days: dict[date, int] = {}
        self.selected: date | None = None
        self.today = date.today()
        self.setMinimumHeight(150)

    def set_days(self, days: dict[date, int]) -> None:
        self.days = days
        counts = sorted(c for d, c in days.items() if d > self.today - timedelta(weeks=WEEKS))
        # Color levels from quartiles, so one busy day doesn't wash out the rest.
        self.levels = [counts[int(len(counts) * q)] for q in (0.25, 0.5, 0.75)] if counts else [1, 2, 3]
        self.update()

    def _start(self) -> date:
        # The first column starts on a Monday, 52 weeks before this week's Monday.
        return self.today - timedelta(days=self.today.weekday()) - timedelta(weeks=WEEKS - 1)

    def _geometry(self) -> tuple[float, float, float]:
        cell = min((self.width() - 40) / WEEKS, (self.height() - 30) / 7)
        return cell, 32.0, 20.0  # cell size, left margin, top margin

    def _day_at(self, pos: QPointF) -> date | None:
        cell, left, top = self._geometry()
        col, row = int((pos.x() - left) // cell), int((pos.y() - top) // cell)
        if 0 <= col < WEEKS and 0 <= row < 7:
            day = self._start() + timedelta(weeks=col, days=row)
            return day if day <= self.today else None
        return None

    def _color(self, count: int) -> QColor:
        base = QColor("#2ea043")
        if count == 0:
            c = QColor(self.palette().color(QPalette.ColorRole.Text))
            c.setAlpha(25)
            return c
        level = sum(1 for threshold in self.levels if count > threshold)
        base.setAlpha((70, 120, 185, 255)[level])
        return base

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Base))
        cell, left, top = self._geometry()
        text = self.palette().color(QPalette.ColorRole.Text)
        start = self._start()
        p.setPen(text)
        for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            p.drawText(QRectF(0, top + row * cell, left - 4, cell),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, name)
        last_month = None
        for col in range(WEEKS):
            monday = start + timedelta(weeks=col)
            if monday.month != last_month:
                last_month = monday.month
                p.setPen(text)
                p.drawText(QPointF(left + col * cell, top - 6), monday.strftime("%b"))
            for row in range(7):
                day = monday + timedelta(days=row)
                if day > self.today:
                    continue
                r = QRectF(left + col * cell + 1, top + row * cell + 1, cell - 2, cell - 2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self._color(self.days.get(day, 0)))
                p.drawRoundedRect(r, 2, 2)
                if day == self.selected:
                    p.setPen(text)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 2, 2)
        p.end()

    def mouseMoveEvent(self, event):
        day = self._day_at(event.position())
        if day:
            n = self.days.get(day, 0)
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"{n} file(s) changed on {day.strftime('%a %Y-%m-%d')}", self)
        else:
            QToolTip.hideText()

    def mousePressEvent(self, event):
        day = self._day_at(event.position())
        self.selected = None if day == self.selected else day
        self.update()
        self.day_clicked.emit(self.selected)


class _Scanner(QObject):
    done = Signal(object, object, int, bool)
    day_done = Signal(object, list)

    def __init__(self):
        super().__init__()
        self.stop = threading.Event()

    def run(self, root: str, show_hidden: bool) -> None:
        days, newest, seen, finished = scan_activity(root, show_hidden, self.stop)
        if not self.stop.is_set():
            self.done.emit(days, newest, seen, finished)

    def run_day(self, root: str, day: date, show_hidden: bool) -> None:
        files = files_on_day(root, day, show_hidden, self.stop)
        if not self.stop.is_set():
            self.day_done.emit(day, files)


class ActivityDialog(QDialog):
    def __init__(self, root: str, show_hidden: bool, parent: QWidget):
        super().__init__(parent)
        self.root = root
        self.chosen: str | None = None
        self.result_list: tuple[str, list[str]] | None = None  # (title, paths) for a result pane
        self.listed: list[str] = []
        self.listed_day: date | None = None
        self.newest: list[tuple[float, str]] = []
        self.setWindowTitle(f"Activity - {root}")
        fit_window(self, 1000, 680)
        self.heatmap = Heatmap()
        self.heatmap.day_clicked.connect(self._fill)
        self.summary = QLabel("Scanning…")
        self.summary.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Time"])
        self.tree.itemActivated.connect(self._choose)
        as_list = QPushButton("Show in the pane")
        as_list.setToolTip("Show the listed files as a result list in the pane (Ctrl-Enter), "
                           "where you can tag, copy, move or trash them")
        as_list.clicked.connect(self._as_list)
        for key in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(key), self, self._as_list)
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(as_list)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heatmap)
        layout.addLayout(bottom)
        layout.addWidget(self.tree, 1)
        self.scanner = _Scanner()
        self.scanner.done.connect(self._done)
        self.scanner.day_done.connect(self._day_done)
        self.show_hidden = show_hidden
        self.day_files: dict[date, list] = {}
        self.days: dict[date, int] = {}
        threading.Thread(target=self.scanner.run, args=(root, show_hidden), daemon=True).start()

    def _done(self, days, newest, seen: int, finished: bool) -> None:
        self.days, self.newest = days, newest
        self.heatmap.set_days(days)
        year = sum(c for d, c in days.items() if d > date.today() - timedelta(weeks=WEEKS))
        busiest = max(days.items(), key=lambda kv: kv[1]) if days else None
        text = f"{seen:,} files, {year:,} changed in the last year"
        if busiest:
            text += f"; busiest day {busiest[0]:%Y-%m-%d} ({busiest[1]} files)"
        if not finished:
            text += "   (stopped early: very big folder)"
        self.summary.setText(text + ".   Click a day to see its files; Enter jumps to a file.")
        self._fill(None)

    def _fill(self, day: date | None) -> None:
        self.tree.clear()
        today = date.today()
        groups: dict[date, QTreeWidgetItem] = {}
        files = self.newest if day is None else self._files_on(day)
        self.listed, self.listed_day = [p for _, p in files], day
        if day is not None and len(files) < self.days.get(day, 0) and day not in self.day_files:
            self.summary.setText(f"Looking for the files changed on {day:%Y-%m-%d}…")
            threading.Thread(target=self.scanner.run_day, daemon=True,
                             args=(self.root, day, self.show_hidden)).start()
        for mtime, path in files:
            d = date.fromtimestamp(mtime)
            if d not in groups:
                groups[d] = QTreeWidgetItem([day_label(d, today), ""])
                self.tree.addTopLevelItem(groups[d])
            item = QTreeWidgetItem([os.path.relpath(path, self.root),
                                    datetime.fromtimestamp(mtime).strftime("%H:%M")])
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            groups[d].addChild(item)
        for i, group in enumerate(groups.values()):
            group.setText(0, f"{group.text(0)}   ({group.childCount()})")
            group.setExpanded(i < 5 or day is not None)
        self.tree.resizeColumnToContents(0)

    def _files_on(self, day: date) -> list[tuple[float, str]]:
        if day in self.day_files:
            return self.day_files[day]
        return [(m, p) for m, p in self.newest if date.fromtimestamp(m) == day]

    def _day_done(self, day: date, files: list) -> None:
        self.day_files[day] = files
        if self.heatmap.selected == day:
            self.summary.setText(f"{len(files)} file(s) changed on {day:%Y-%m-%d}.")
            self._fill(day)

    def _as_list(self) -> None:
        if self.listed:
            day = self.listed_day
            self.result_list = (f"changed {day:%Y-%m-%d}" if day else "recently changed", self.listed)
            self.accept()

    def _choose(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.chosen = path
            self.accept()

    def done(self, result: int) -> None:
        self.scanner.stop.set()
        super().done(result)

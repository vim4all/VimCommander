"""The jobs panel (:jobs): running and queued file operations.

Big copies, moves, synchronizations and zips run one after another (the
config's "queue_jobs"): a job started while another one runs waits in the
queue. The panel lists them all with their progress; each row can be paused,
resumed, cancelled or (while it waits) moved to the front.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QDockWidget, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from .fileops import Job
from .pane import human_size

QUEUED_OPS = ("copy", "move", "sync", "zip")  # long transfers; trash, delete and undo run at once


class JobRow(QWidget):
    def __init__(self, job: Job, panel: "JobsPanel"):
        super().__init__()
        self.job = job
        self.label = QLabel(job.description)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail = QLabel()
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(8)
        self.pause_button = self._button("Pause", "Pause / resume this job",
                                         lambda: panel.toggle_pause(self.job))
        self.first_button = self._button("Next", "Run this job next",
                                         lambda: panel.move_first.emit(self.job))
        self.cancel_button = self._button("Cancel", "Cancel this job", lambda: panel.cancel.emit(self.job))
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.label, 1)
        for b in (self.first_button, self.pause_button, self.cancel_button):
            top.addWidget(b)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        layout.addLayout(top)
        layout.addWidget(self.bar)
        layout.addWidget(self.detail)
        job.progress.connect(self.on_progress)
        self.update_state()

    def _button(self, text: str, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(slot)
        return b

    def update_state(self) -> None:
        job = self.job
        self.first_button.setVisible(not job.started)
        self.pause_button.setText("Resume" if job.paused else "Pause")
        if not job.started:
            self.detail.setText("Waiting (paused)" if job.paused else "Waiting")
        else:
            self.detail.setText(("Paused   " if job.paused else "") + self.detail.text().removeprefix("Paused   "))

    def on_progress(self, done: int, total: int, items: int, items_total: int, name: str) -> None:
        if total:
            self.bar.setValue(int(1000 * min(done, total) / total))
            amount = f"{human_size(done)} of {human_size(total)}"
        else:
            self.bar.setValue(int(1000 * items / max(items_total, 1)))
            amount = f"item {min(items + 1, items_total)} of {items_total}"
        self.detail.setText(("Paused   " if self.job.paused else "") + f"{amount}   {name}")


class JobsPanel(QDockWidget):
    cancel = Signal(object)  # Job
    move_first = Signal(object)  # Job
    paused = Signal(object)  # Job: paused or resumed

    def __init__(self, parent=None):
        super().__init__("Jobs", parent)
        self.setObjectName("jobs")
        self.rows: dict[int, JobRow] = {}
        self.auto_shown = False  # shown by a queued job, not by :jobs: hides itself when all are done
        box = QWidget()
        self.list = QVBoxLayout(box)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(0)
        self.empty = QLabel("No file operations running")
        self.empty.setStyleSheet("padding: 8px; color: gray;")
        self.list.addWidget(self.empty)
        self.list.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(box)
        self.setWidget(scroll)
        self._timer = QTimer(self, interval=500)  # a job paused from its dialog shows it here too
        self._timer.timeout.connect(lambda: [row.update_state() for row in self.rows.values()])
        self._timer.start()

    def add(self, job: Job) -> None:
        row = JobRow(job, self)
        self.rows[id(job)] = row
        self.list.insertWidget(self.list.count() - 1, row)
        self.empty.hide()

    def remove(self, job: Job) -> None:
        row = self.rows.pop(id(job), None)
        if row:
            row.deleteLater()
        self.empty.setVisible(not self.rows)
        if not self.rows and self.auto_shown:
            self.auto_shown = False
            self.hide()

    def reorder(self, jobs: list[Job]) -> None:
        """Show the rows in this order (running first, then the queue)."""
        for i, job in enumerate(jobs):
            row = self.rows.get(id(job))
            if row:
                self.list.removeWidget(row)
                self.list.insertWidget(i + 1, row)  # after the "no jobs" label

    def refresh(self, job: Job) -> None:
        if row := self.rows.get(id(job)):
            row.update_state()

    def toggle_pause(self, job: Job) -> None:
        job.resume() if job.paused else job.pause()
        self.refresh(job)
        self.paused.emit(job)

    def show_auto(self) -> None:
        if not self.isVisible():
            self.auto_shown = True
            self.show()

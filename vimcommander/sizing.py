"""Sizing for the big tool windows (diff, grep, sync, activity…): never larger
than the screen they open on, centred there, and resizable with maximize and
minimize buttons like a normal window."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QWidget


def fit_window(window: QWidget, width: int, height: int) -> None:
    """Resize to width × height, shrunk to fit the screen of the parent window."""
    if isinstance(window, QDialog):
        window.setWindowFlags(window.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
                              | Qt.WindowType.WindowMinimizeButtonHint
                              | Qt.WindowType.WindowCloseButtonHint)
        window.setSizeGripEnabled(True)
    parent = window.parentWidget()
    screen = (parent.window().screen() if parent else None) or QGuiApplication.primaryScreen()
    if screen is None:
        window.resize(width, height)
        return
    area = screen.availableGeometry()
    w = min(width, int(area.width() * 0.94))
    h = min(height, int(area.height() * 0.90))
    window.resize(w, h)
    window.move(area.x() + (area.width() - w) // 2, area.y() + (area.height() - h) // 2)

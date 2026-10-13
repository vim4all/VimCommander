"""The which-key popup: after a prefix key (g, z, y, ", Ctrl-w, m, '…) and a
short pause, a panel above the status bar lists what can come next."""

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


def pretty(key: str) -> str:
    """'<C-d>' -> 'Ctrl-d', '<S-Del>' -> 'Shift-Del', '<A-F1>' -> 'Alt-F1'."""
    return (key.replace("<C-S-", "Ctrl-Shift-").replace("<C-", "Ctrl-").replace("<S-", "Shift-")
            .replace("<A-", "Alt-").replace("<CR>", "Enter").replace("<BS>", "Backspace")
            .replace("<", "").replace(">", "") if key not in ("<", ">") else key)


class WhichKey(QFrame):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.ToolTipBase)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setForegroundRole(QPalette.ColorRole.ToolTipText)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self.label)
        self.hide()

    def show_entries(self, title: str, entries: list[tuple[str, str]], anchor: QWidget) -> None:
        """Show `entries` (key, description) in columns, just above `anchor`'s top edge."""
        parent = self.parentWidget()
        width = parent.width() - 16
        cell = 260
        columns = max(1, width // cell)
        rows = (len(entries) + columns - 1) // columns
        cells = []
        for r in range(rows):
            row = []
            for c in range(columns):
                i = c * rows + r
                if i < len(entries):
                    key, desc = entries[i]
                    row.append(f"<td style='padding-right:6px'><b>{html.escape(pretty(key))}</b></td>"
                               f"<td style='padding-right:22px'>{html.escape(desc)}</td>")
            cells.append("<tr>" + "".join(row) + "</tr>")
        self.label.setText(f"<b>{html.escape(title)}</b><table>{''.join(cells)}</table>")
        self.adjustSize()
        self.resize(width, self.sizeHint().height())
        bottom = anchor.mapTo(parent, anchor.rect().topLeft()).y()
        self.move(8, max(0, bottom - self.height() - 4))
        self.raise_()
        self.show()

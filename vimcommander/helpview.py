"""The F1 / :help window: app.HELP as sections with headings, scrollable and
fitted to the screen, with a filter box (type to keep only the matching lines).
j / k / Ctrl-d / Ctrl-u / gg / G scroll, / focuses the filter, Esc or q closes."""

import html
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QLineEdit, QTextBrowser, QVBoxLayout

from .sizing import fit_window

SECTION = re.compile(r"^<b>(.+?)</b>:?\s*(.*)$")


def sections(text: str) -> list[tuple[str, list[str]]]:
    """[(title, lines)] from HELP: "<b>Title</b>: line" starts a section,
    "&nbsp;&nbsp;line" continues it."""
    out: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.replace("&nbsp;", " ").strip()
        if not line:
            continue
        m = SECTION.match(line)
        if m:
            out.append((m.group(1), [m.group(2)] if m.group(2) else []))
        elif out:
            out[-1][1].append(line)
        else:
            out.append(("", [line]))
    return out


def _line_html(line: str) -> str:
    """One help line: the keys before each ' = ' in bold monospace."""
    plain = re.sub(r"<[^>]+>", "", html.unescape(line))
    parts = re.split(r"(,\s+|;\s+|\s{2,})", plain)
    out = []
    for part in parts:
        key, sep, rest = part.partition(" = ")
        if sep and 0 < len(key) <= 40:
            lead = key[:len(key) - len(key.lstrip("("))]  # "(notes = …": the bracket isn't the key
            out.append(f"{lead}<span class='k'>{html.escape(key[len(lead):])}</span> = {html.escape(rest)}")
        else:
            out.append(html.escape(part))
    return "".join(out)


def render(text: str, query: str = "") -> str:
    query = query.casefold().strip()
    blocks = []
    for title, lines in sections(text):
        if query and query not in title.casefold():
            lines = [l for l in lines if query in re.sub(r"<[^>]+>", "", l).casefold()]
            if not lines:
                continue
        items = "".join(f"<li>{_line_html(l)}</li>" for l in lines)
        blocks.append(f"<h3>{html.escape(title)}</h3><ul>{items}</ul>")
    if not blocks:
        return f"<p>Nothing matches <b>{html.escape(query)}</b>.</p>"
    return ("<style>h3 { margin: 14px 0 4px 0; } ul { margin: 0 0 0 -16px; } li { margin: 2px 0; }"
            ".k { font-family: monospace; font-weight: bold; }</style>" + "".join(blocks))


class HelpDialog(QDialog):
    def __init__(self, text: str, parent=None, topic: str = ""):
        super().__init__(parent)
        self.setWindowTitle("VimCommander keys and commands")
        self.text = text
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter: type a key, command or word (Esc = back to the text)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.update_text)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.filter)
        layout.addWidget(self.view, 1)
        self.filter.setText(topic)  # :help TOPIC
        self.update_text()
        fit_window(self, 900, 900)
        self.view.setFocus()
        self._g = False

    def update_text(self) -> None:
        self.view.setHtml(render(self.text, self.filter.text()))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.filter.hasFocus():
            if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down):
                self.view.setFocus()
                return
            return super().keyPressEvent(event)
        bar = self.view.verticalScrollBar()
        key, text, ctrl = event.key(), event.text(), event.modifiers() & Qt.KeyboardModifier.ControlModifier
        g, self._g = self._g, False
        if text == "j":
            bar.setValue(bar.value() + bar.singleStep() * 3)
        elif text == "k":
            bar.setValue(bar.value() - bar.singleStep() * 3)
        elif ctrl and key == Qt.Key.Key_D:
            bar.setValue(bar.value() + bar.pageStep() // 2)
        elif ctrl and key == Qt.Key.Key_U:
            bar.setValue(bar.value() - bar.pageStep() // 2)
        elif ctrl and key == Qt.Key.Key_F:
            bar.setValue(bar.value() + bar.pageStep())
        elif ctrl and key == Qt.Key.Key_B:
            bar.setValue(bar.value() - bar.pageStep())
        elif text == "G":
            bar.setValue(bar.maximum())
        elif text == "g":
            if g:
                bar.setValue(0)
            else:
                self._g = True
        elif text == "/":
            self.filter.setFocus()
            self.filter.selectAll()
        elif text == "q" or key == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)

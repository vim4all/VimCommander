"""The terminal pane (:vterm, Ctrl-w t): a real terminal in one of the two panes.

A shell (PowerShell, cmd, bash, WSL…) runs in a pseudo-terminal (ptyproc.py);
pyte keeps the screen, and this widget draws it and sends the keys, so
full-screen programs (vim, htop, less, mc) work. The shell and the pane follow
each other (termshell.py).

Keys all go to the program, except Ctrl-w followed by h / j / k / l / w / p or
Ctrl-w, which go back to the file list (Ctrl-w . sends a Ctrl-w). Shift-PgUp /
PgDn and the mouse wheel scroll back; drag to select, Ctrl-Shift-C copies,
Ctrl-Shift-V / Shift-Insert pastes, right-click copies the selection or pastes.
"""

import copy
import os
import threading
import time

import pyte
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QClipboard, QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import QWidget

from . import termshell
from .ptyproc import PtyProcess
from .vim import LANGMAPS

ALT_SCREENS = (47, 1047, 1049)
QUICK_EXIT = 3.0  # seconds: a shell that ends this soon most likely couldn't start
APP_CURSOR = 1 << 5  # DECCKM, as pyte stores private modes
BRACKETED_PASTE = 2004 << 5
LEAVE_KEYS = {Qt.Key.Key_H, Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_L, Qt.Key.Key_W, Qt.Key.Key_P}
NAMES = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"]
SCROLLBACK = 5000


class TermScreen(pyte.HistoryScreen):
    """pyte's screen with the alternate screen (vim, less, htop draw there and
    the shell's text comes back when they quit) and answers to the program's
    questions (cursor position, device attributes)."""

    def __init__(self, columns: int, lines: int, respond):
        super().__init__(columns, lines, history=SCROLLBACK, ratio=0.5)
        self._respond = respond
        self._saved: tuple[dict, pyte.screens.Cursor] | None = None

    @property
    def in_alt(self) -> bool:
        return self._saved is not None

    def write_process_input(self, data: str) -> None:
        self._respond(data)

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private") and any(m in ALT_SCREENS for m in modes):
            if self._saved is None:
                self._saved = ({y: dict(line) for y, line in self.buffer.items()}, copy.copy(self.cursor))
                self.erase_in_display(2)
                self.cursor_position()  # less draws a short file from where the cursor is: the top
            modes = tuple(m for m in modes if m not in ALT_SCREENS)
            if not modes:
                return
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get("private") and any(m in ALT_SCREENS for m in modes):
            if self._saved is not None:
                lines, cursor = self._saved
                self._saved = None
                self.buffer.clear()
                for y, line in lines.items():
                    if y < self.lines:
                        self.buffer[y].update(line)
                self.cursor = cursor
                self.cursor.y = min(self.cursor.y, self.lines - 1)
                self.cursor.x = min(self.cursor.x, self.columns - 1)
                self.dirty.update(range(self.lines))
            modes = tuple(m for m in modes if m not in ALT_SCREENS)
            if not modes:
                return
        super().reset_mode(*modes, **kwargs)

    def select_graphic_rendition(self, *attrs, **kwargs):
        if kwargs.get("private"):  # CSI > 4;2 m (xterm's modifyOtherKeys, sent by vim): not colors
            return
        super().select_graphic_rendition(*attrs)

    def index(self):
        if self._saved is not None:  # the alternate screen keeps no history
            pyte.Screen.index(self)
        else:
            super().index()


class _Pipe(QObject):
    data = Signal(str)
    closed = Signal()


def color(name: str, default: QColor, bold: bool = False) -> QColor:
    if name == "default":
        return default
    ansi = termshell.ANSI
    if name.startswith("bright") and name[6:] in NAMES:
        return QColor(ansi[8 + NAMES.index(name[6:])])
    if name in NAMES:
        return QColor(ansi[NAMES.index(name) + (8 if bold else 0)])
    if len(name) == 6:
        return QColor("#" + name)
    return default


def key_text(event, app_cursor: bool) -> str | None:
    """What a key sends to the program, in xterm's encoding."""
    key, mods, text = event.key(), event.modifiers(), event.text()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    alt = bool(mods & Qt.KeyboardModifier.AltModifier)
    shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    mod = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)
    K = Qt.Key
    letters = {K.Key_Up: "A", K.Key_Down: "B", K.Key_Right: "C", K.Key_Left: "D", K.Key_Home: "H", K.Key_End: "F"}
    if key in letters:
        if mod > 1:
            return f"\x1b[1;{mod}{letters[key]}"
        return ("\x1bO" if app_cursor else "\x1b[") + letters[key]
    tilde = {K.Key_Insert: 2, K.Key_Delete: 3, K.Key_PageUp: 5, K.Key_PageDown: 6, K.Key_F5: 15, K.Key_F6: 17,
             K.Key_F7: 18, K.Key_F8: 19, K.Key_F9: 20, K.Key_F10: 21, K.Key_F11: 23, K.Key_F12: 24}
    if key in tilde:
        return f"\x1b[{tilde[key]};{mod}~" if mod > 1 else f"\x1b[{tilde[key]}~"
    pf = {K.Key_F1: "P", K.Key_F2: "Q", K.Key_F3: "R", K.Key_F4: "S"}
    if key in pf:
        return f"\x1b[1;{mod}{pf[key]}" if mod > 1 else f"\x1bO{pf[key]}"
    if key in (K.Key_Return, K.Key_Enter):
        return "\x1b\r" if alt else "\r"
    if key == K.Key_Backspace:
        return "\x08" if ctrl else ("\x1b\x7f" if alt else "\x7f")
    if key == K.Key_Tab:
        return "\t"
    if key == K.Key_Backtab:
        return "\x1b[Z"
    if key == K.Key_Escape:
        return "\x1b"
    if ctrl and alt and text and text.isprintable():  # AltGr on Windows: a character like @ or €
        return text
    if ctrl:
        if K.Key_A <= key <= K.Key_Z:
            return chr(key - K.Key_A + 1)
        if 0x80 <= key <= 0xFFFF and chr(key).isalpha():  # a Cyrillic layout: the letter's US key
            latin = LANGMAPS["cyrillic"].get(chr(key).lower(), "")
            if latin.isascii() and latin.isalpha():
                return chr(ord(latin.lower()) - 96)
        specials = {K.Key_Space: "\x00", K.Key_At: "\x00", K.Key_BracketLeft: "\x1b", K.Key_Backslash: "\x1c",
                    K.Key_BracketRight: "\x1d", K.Key_AsciiCircum: "\x1e", K.Key_6: "\x1e",
                    K.Key_Underscore: "\x1f", K.Key_Minus: "\x1f", K.Key_Slash: "\x1f", K.Key_Question: "\x7f"}
        if key in specials:
            return specials[key]
    if text:
        return ("\x1b" + text) if alt else text
    return None


class TermView(QWidget):
    cwd_changed = Signal(str)  # the shell's folder, as a path this computer opens
    leave = Signal()  # Ctrl-w h/j/k/l/w/p: back to the file list
    exited = Signal()
    title_changed = Signal(str)

    def __init__(self, spec: str, cwd: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(False)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.shell = termshell.shell_command(spec, cwd)
        self.cwd = cwd
        self.follow = True  # cd along with the file pane
        self.at_prompt = False  # the shell showed its prompt and nothing was typed since
        self.offset = 0  # lines scrolled back
        self.proc: PtyProcess | None = None
        self.started = 0.0  # time.monotonic() when the program started
        self.ended = False  # it ended at once: its last words stay until a key is pressed
        self.title = ""
        self._ctrl_w = False
        self._sel: tuple[tuple[int, int], tuple[int, int]] | None = None  # (line, col) in history + screen
        self._tail = ""  # the end of the last output, for an OSC 7 cut in two
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._set_metrics(font)
        self.vt = TermScreen(80, 24, self._send)
        self.stream = pyte.Stream(self.vt)
        self._repaint = QTimer(self, singleShot=True, interval=12)
        self._repaint.timeout.connect(self.update)
        self._blink = True

    # Process

    def start(self) -> None:
        cols, rows = self._grid_size()
        self.vt.resize(rows, cols)
        env = dict(os.environ)
        env.update(self.shell.env)
        self.started = time.monotonic()
        try:
            self.proc = PtyProcess(self.shell.argv, self.cwd if os.path.isdir(self.cwd) else os.path.expanduser("~"),
                                   env, rows, cols)
        except OSError as e:
            self.stream.feed(f"\x1b[31mCould not start {' '.join(self.shell.argv)}: {e.strerror or e}\x1b[0m\r\n"
                             "\x1b[33m[press a key to close]\x1b[0m")
            self.ended = True
            self.update()
            return
        pipe = self._pipe = _Pipe()
        pipe.data.connect(self._output)
        pipe.closed.connect(self._closed)
        proc = self.proc

        def reader():
            while True:
                text = proc.read()
                if text is None:
                    break
                if text:
                    pipe.data.emit(text)
            pipe.closed.emit()
        threading.Thread(target=reader, daemon=True).start()
        # ConPTY keeps its output open after the shell exits, so the reader never
        # sees the end: notice the exit by asking now and then.
        self._alive_timer = QTimer(self, interval=300)
        self._alive_timer.timeout.connect(self._check_alive)
        self._alive_timer.start()

    def _check_alive(self) -> None:
        if self.proc is not None and not self.proc.alive:
            self._ended()

    def _ended(self) -> None:
        """The program ended: close, unless that was right after it started (most
        likely it couldn't: a missing distro, a bad command line). Then what it
        said stays on screen, with its exit code, until a key is pressed."""
        code = self.proc.exit_code if self.proc else None
        self._alive_timer.stop()
        self.stop()
        if time.monotonic() - self.started > QUICK_EXIT:
            self.exited.emit()
            return
        self.ended = True
        status = f" with exit code {code}" if code else ""
        self.stream.feed(f"\r\n\x1b[33m[{self.shell.name} ended{status}; press a key to close]\x1b[0m")
        self.offset = 0
        self.update()

    def stop(self) -> None:
        if self.proc:
            proc, self.proc = self.proc, None
            try:
                self._pipe.closed.disconnect()
            except (RuntimeError, TypeError):
                pass
            proc.close()

    @property
    def running(self) -> bool:
        return self.proc is not None

    def _send(self, text: str) -> None:
        if self.proc:
            self.proc.write(text)

    def _closed(self) -> None:
        if self.proc is not None:  # else already noticed by _check_alive
            self._ended()

    def _output(self, text: str) -> None:
        scan = self._tail + text
        for m in termshell.OSC7.finditer(scan):
            folder, host = termshell.osc7_folder(m.group(1), self.shell)
            if self.shell.kind == "wsl" and not self.shell.distro and host:
                self.shell.distro = host  # the default distro's name, from its prompt
            self.at_prompt = True
            if folder and os.path.normcase(folder) != os.path.normcase(self.cwd):
                self.cwd = folder
                self.cwd_changed.emit(folder)
        cut = scan.rfind("\x1b]")
        self._tail = scan[cut:][-512:] if cut >= 0 and not termshell.OSC7.search(scan[cut:]) else ""
        try:
            self.stream.feed(text)
        except Exception:  # a sequence pyte can't handle: drop it, keep the parser going
            self.stream = pyte.Stream(self.vt)
        if self.vt.title != self.title:
            self.title = self.vt.title
            self.title_changed.emit(self.title)
        if not self._repaint.isActive():
            self._repaint.start()

    def cd(self, folder: str) -> None:
        """The file pane went to `folder`: follow it, if the shell waits at its prompt."""
        if not (self.follow and self.proc and self.at_prompt) or not folder:
            return
        if os.path.normcase(folder) == os.path.normcase(self.cwd):
            return
        line = termshell.cd_line(self.shell, folder)
        if line:
            self.at_prompt = False
            self.cwd = folder
            self._send(line)

    def send_line(self, line: str) -> None:
        """Type a command line, as if the user did (e.g. :mount's sshfs)."""
        self.at_prompt = False
        self._send(line)

    # Geometry

    def set_font(self, font: QFont) -> None:
        self._set_metrics(font)
        self._fit()

    def _set_metrics(self, font: QFont) -> None:
        self.term_font = QFont(font)
        self.term_font.setStyleHint(QFont.StyleHint.Monospace)
        self.term_font.setKerning(False)
        self.bold_font = QFont(self.term_font)
        self.bold_font.setBold(True)
        fm = QFontMetricsF(self.term_font)
        self.cw = max(fm.horizontalAdvance("M"), 1.0)
        self.ch = max(fm.height(), 1.0)
        self.ascent = fm.ascent()
        self.setFont(self.term_font)

    def _grid_size(self) -> tuple[int, int]:
        return max(int((self.width() - 8) // self.cw), 10), max(int((self.height() - 6) // self.ch), 3)

    def _fit(self) -> None:
        cols, rows = self._grid_size()
        if (cols, rows) != (self.vt.columns, self.vt.lines):
            self.vt.resize(rows, cols)
            if self.proc:
                self.proc.resize(rows, cols)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    # Drawing

    def _line(self, index: int):
        """Line `index` of history + screen (history first)."""
        top = self.vt.history.top
        if index < len(top):
            return top[index]
        return self.vt.buffer[index - len(top)]

    def _first_line(self) -> int:
        return len(self.vt.history.top) - self.offset

    def paintEvent(self, event):
        p = QPainter(self)
        pal = self.palette()
        bg0 = pal.color(QPalette.ColorRole.Base)
        fg0 = pal.color(QPalette.ColorRole.Text)
        p.fillRect(self.rect(), bg0)
        vt = self.vt
        first = self._first_line()
        sel = self._normalized_selection()
        select_bg = pal.color(QPalette.ColorRole.Highlight)
        select_fg = pal.color(QPalette.ColorRole.HighlightedText)
        default = vt.default_char
        for row in range(vt.lines):
            index = first + row
            line = self._line(index)
            y = 3 + row * self.ch
            col = 0
            while col < vt.columns:
                char = line.get(col, default)
                start = col
                selected = self._selected(sel, index, col)
                key = (char.fg, char.bg, char.bold, char.reverse, char.underscore, char.italics,
                       char.strikethrough, selected)
                text = [char.data or " "]
                col += 1
                while col < vt.columns:
                    c = line.get(col, default)
                    s = self._selected(sel, index, col)
                    if (c.fg, c.bg, c.bold, c.reverse, c.underscore, c.italics, c.strikethrough, s) != key:
                        break
                    text.append(c.data)
                    col += 1
                fg = color(char.fg, fg0, char.bold)
                bg = color(char.bg, bg0)
                if char.reverse:
                    fg, bg = (bg0 if char.bg == "default" else bg), (fg0 if char.fg == "default" else fg)
                if selected:
                    fg, bg = select_fg, select_bg
                x = 4 + start * self.cw
                width = (col - start) * self.cw
                if bg != bg0:
                    p.fillRect(QRectF(x, y, width, self.ch), bg)
                run = "".join(text)
                if run.strip():
                    font = self.bold_font if char.bold else self.term_font
                    if char.italics or char.underscore or char.strikethrough:
                        font = QFont(font)
                        font.setItalic(char.italics)
                        font.setUnderline(char.underscore)
                        font.setStrikeOut(char.strikethrough)
                    p.setFont(font)
                    p.setPen(fg)
                    if run.isascii():
                        p.drawText(QPointF(x, y + self.ascent), run)
                    else:  # other scripts and wide characters: one cell each, so columns stay put
                        k = 0
                        for ch in text:
                            if ch:
                                p.drawText(QPointF(x + k * self.cw, y + self.ascent), ch)
                            k += 1
        # the cursor
        if self.offset == 0 and not vt.cursor.hidden:
            cx, cy = vt.cursor.x, vt.cursor.y
            rect = QRectF(4 + cx * self.cw, 3 + cy * self.ch, self.cw, self.ch)
            cursor = QColor(fg0)
            if self.hasFocus():
                cursor.setAlpha(200)
                p.fillRect(rect, cursor)
                char = vt.buffer[cy].get(cx, default)
                if char.data.strip():
                    p.setFont(self.bold_font if char.bold else self.term_font)
                    p.setPen(bg0)
                    p.drawText(QPointF(rect.x(), rect.y() + self.ascent), char.data)
            else:
                p.setPen(cursor)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        if self.offset:
            hint = f" ↑ {self.offset} lines back (Shift-PgDn / any key returns) "
            p.setFont(self.term_font)
            w = QFontMetricsF(self.term_font).horizontalAdvance(hint)
            box = QRectF(self.width() - w - 8, 2, w + 4, self.ch)
            p.fillRect(box, select_bg)
            p.setPen(select_fg)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, hint)

    # Selection

    def _cell(self, pos: QPointF) -> tuple[int, int]:
        col = int(max(0.0, pos.x() - 4) // self.cw)
        row = int(max(0.0, pos.y() - 3) // self.ch)
        row = min(row, self.vt.lines - 1)
        return self._first_line() + row, min(col, self.vt.columns - 1)

    def _normalized_selection(self):
        if not self._sel:
            return None
        a, b = sorted(self._sel)
        return None if a == b else (a, b)

    @staticmethod
    def _selected(sel, index: int, col: int) -> bool:
        if not sel:
            return False
        (l1, c1), (l2, c2) = sel
        return (l1, c1) <= (index, col) < (l2, c2)

    def selected_text(self) -> str:
        sel = self._normalized_selection()
        if not sel:
            return ""
        (l1, c1), (l2, c2) = sel
        out = []
        for index in range(l1, l2 + 1):
            line = self._line(index)
            start = c1 if index == l1 else 0
            end = c2 if index == l2 else self.vt.columns
            out.append("".join(line.get(c, self.vt.default_char).data for c in range(start, end)).rstrip())
        return "\n".join(out)

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() == Qt.MouseButton.LeftButton:
            cell = self._cell(event.position())
            self._sel = (cell, cell)
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            if self._normalized_selection():
                self.copy()
            else:
                self.paste()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.paste(QGuiApplication.clipboard().text(QClipboard.Mode.Selection) or None)

    def mouseMoveEvent(self, event):
        if self._sel and event.buttons() & Qt.MouseButton.LeftButton:
            self._sel = (self._sel[0], self._cell(event.position()))
            self.update()

    def mouseDoubleClickEvent(self, event):
        index, col = self._cell(event.position())
        line = self._line(index)
        text = "".join(line.get(c, self.vt.default_char).data or " " for c in range(self.vt.columns))
        start = end = col
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        while end < len(text) and not text[end].isspace():
            end += 1
        self._sel = ((index, start), (index, end))
        self.update()

    def copy(self) -> None:
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)
        self._sel = None
        self.update()

    def paste(self, text: str | None = None) -> None:
        text = text if text is not None else QGuiApplication.clipboard().text()
        if not text or not self.proc:
            return
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        if BRACKETED_PASTE in self.vt.mode:
            text = f"\x1b[200~{text}\x1b[201~"
        self.at_prompt = False
        self.offset = 0
        self._send(text)

    # Scrolling

    def scroll_back(self, lines: int) -> None:
        self.offset = max(0, min(self.offset + lines, len(self.vt.history.top)))
        self.update()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        if not steps:
            return
        if self.vt.in_alt:  # less, vim, man: the wheel moves through the text, as arrow keys
            key = "\x1bOA" if steps > 0 else "\x1bOB"
            if APP_CURSOR not in self.vt.mode:
                key = key.replace("O", "[")
            self._send(key * int(abs(steps) * 3))
            return
        self.scroll_back(int(steps * 3))

    # Keys

    def event(self, e):
        # Tab and Shift-Tab would move the focus; here they belong to the shell
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(e)
            return True
        if e.type() == QEvent.Type.ShortcutOverride:  # no app shortcut takes a key from the program
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, event):
        key, mods = event.key(), event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            return
        if self.ended:  # the program is gone: any key closes (Ctrl-Shift-C still copies its words)
            if ctrl and shift and key == Qt.Key.Key_C:
                self.copy()
            else:
                self.exited.emit()
            return
        if self._ctrl_w:
            self._ctrl_w = False
            if key in LEAVE_KEYS:
                self.leave.emit()
                return
            if key == Qt.Key.Key_Period:
                self._type("\x17")
                return
            if key == Qt.Key.Key_Escape:
                return
            self._type("\x17")  # not one of ours: the program gets both keys
        elif ctrl and not shift and key == Qt.Key.Key_W:
            self._ctrl_w = True
            return
        if ctrl and shift and key == Qt.Key.Key_C:
            self.copy()
            return
        if (ctrl and shift and key == Qt.Key.Key_V) or (shift and key == Qt.Key.Key_Insert):
            self.paste()
            return
        if shift and key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown) and not self.vt.in_alt:
            page = max(self.vt.lines - 2, 1)
            self.scroll_back(page if key == Qt.Key.Key_PageUp else -page)
            return
        text = key_text(event, APP_CURSOR in self.vt.mode)
        if text:
            self._type(text)

    def _type(self, text: str) -> None:
        self.at_prompt = False
        self._sel = None
        if self.offset:
            self.offset = 0
            self.update()
        self._send(text)

    def inputMethodEvent(self, event):
        if event.commitString():
            self._type(event.commitString())
        event.accept()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._ctrl_w = False
        self.update()

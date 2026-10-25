"""The music player: plays a folder (or the tagged files, or an .m3u) with
QtMultimedia, shown as a one-row bar at the bottom of the window; Ctrl-w m
shows its playlist in the other pane.

Player owns the QMediaPlayer (made on the first track, so starting the app
doesn't load the FFmpeg backend), the track list and the play order. PlayerBar
and PlaylistView only show it and send it commands; neither takes the focus
from the panes, except the playlist while you are in it.

QtMultimedia is in PySide6-Addons; app.py imports this module only when the
player is first used and says what to install if it's missing.
"""

import os
import sys

from . import music

if backend := music.audio_backend(dict(os.environ), sys.platform):  # before QtMultimedia starts
    os.environ["QT_AUDIO_BACKEND"] = backend

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QFont, QPainter
from PySide6.QtMultimedia import QAudio, QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QDockWidget, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QSizePolicy, QSlider, QStyle, QStyleOptionSlider,
                               QToolButton, QVBoxLayout, QWidget)

REPEAT_MODES = ("all", "one", "off")
SPEEDS = (0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)  # what faster / slower step through
SEEK_STEP = 5000  # ms that Left / Right seek in the playlist


class Player(QObject):
    track_changed = Signal()  # another track, or the list changed
    playing_changed = Signal(bool)
    position_changed = Signal(int, int)  # position, duration (ms)
    options_changed = Signal()  # volume, shuffle or repeat: save them
    message = Signal(str)

    def __init__(self, parent: QObject | None = None, options: dict | None = None):
        super().__init__(parent)
        options = options or {}
        self.tracks: list[str] = []
        self.order: list[int] = []  # track numbers in play order
        self.pos = -1  # where in `order` we are
        self.shuffle = bool(options.get("shuffle", False))
        repeat = options.get("repeat")
        self.repeat: str = repeat if repeat in REPEAT_MODES else "all"
        self._volume = max(0, min(100, int(options.get("volume", 70) or 0)))
        self._speed = music.clamp_speed(options.get("speed", 1.0))
        self._media: QMediaPlayer | None = None
        self._output: QAudioOutput | None = None
        self._errors = 0  # tracks that failed in a row: stop when all did

    # The QMediaPlayer, made when first needed

    def media(self) -> QMediaPlayer:
        if self._media is None:
            self._output = QAudioOutput(self)
            self._media = QMediaPlayer(self)
            self._media.setAudioOutput(self._output)
            self._apply_volume()
            self._apply_speed()
            self._media.mediaStatusChanged.connect(self._on_status)
            self._media.playbackStateChanged.connect(
                lambda s: self.playing_changed.emit(s == QMediaPlayer.PlaybackState.PlayingState))
            self._media.positionChanged.connect(lambda p: self.position_changed.emit(p, self.duration))
            self._media.durationChanged.connect(lambda d: self.position_changed.emit(self.position, d))
            self._media.errorOccurred.connect(self._on_error)
        return self._media

    def release(self) -> None:
        """Stop and let go of the audio device (at exit)."""
        if self._media is not None:
            self._media.stop()
            self._media.setSource(QUrl())

    # State

    @property
    def index(self) -> int:
        return self.order[self.pos] if 0 <= self.pos < len(self.order) else -1

    @property
    def current(self) -> str | None:
        return self.tracks[self.index] if self.index >= 0 else None

    @property
    def playing(self) -> bool:
        return self._media is not None and \
            self._media.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def position(self) -> int:
        return self._media.position() if self._media is not None else 0

    @property
    def duration(self) -> int:
        return self._media.duration() if self._media is not None else 0

    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        if value != self._volume:
            self._volume = value
            self._apply_volume()
            self.options_changed.emit()

    def _apply_volume(self) -> None:
        if self._output is not None:  # the slider is perceptual, the output linear
            self._output.setVolume(QAudio.convertVolume(
                self._volume / 100, QAudio.VolumeScale.LogarithmicVolumeScale,
                QAudio.VolumeScale.LinearVolumeScale))

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, rate: float) -> None:
        rate = music.clamp_speed(rate)
        if rate != self._speed:
            self._speed = rate
            self._apply_speed()
            self.options_changed.emit()

    def faster(self, steps: int = 1) -> None:
        """The next speed up (steps > 0) or down in SPEEDS."""
        self.set_speed(music.step_speed(self._speed, steps, SPEEDS))

    def _apply_speed(self) -> None:
        if self._media is not None:
            self._media.setPlaybackRate(self._speed)
            if hasattr(self._media, "setPitchCompensation"):  # Qt 6.10+: voices keep their pitch
                self._media.setPitchCompensation(True)

    def options(self) -> dict:
        return {"volume": self._volume, "shuffle": self.shuffle, "repeat": self.repeat, "speed": self._speed}

    # Commands

    def load(self, tracks: list[str], start: int = 0) -> None:
        """Play `tracks`, from track number `start`."""
        self.tracks = list(tracks)
        start = max(0, min(start, len(self.tracks) - 1))
        self._make_order(start)
        self._errors = 0
        self._play_current()

    def add(self, tracks: list[str]) -> int:
        """Append the tracks not in the list yet; returns how many. Starts playing
        if nothing was loaded."""
        have = set(self.tracks)
        new = [t for t in dict.fromkeys(tracks) if t not in have]
        if not new:
            return 0
        if not self.tracks:
            self.load(new)
            return len(new)
        first = len(self.tracks)
        self.tracks += new
        self.order += list(range(first, len(self.tracks)))
        self.track_changed.emit()
        return len(new)

    def _make_order(self, first: int) -> None:
        n = len(self.tracks)
        self.order = music.shuffled(n, first) if self.shuffle else list(range(n))
        self.pos = self.order.index(first) if n else -1

    def play_index(self, i: int) -> None:
        if 0 <= i < len(self.tracks):
            self.pos = self.order.index(i)
            self._errors = 0
            self._play_current()

    def _play_current(self) -> None:
        media = self.media()
        path = self.current
        if path is None:
            media.stop()
            media.setSource(QUrl())
        else:
            media.setSource(QUrl.fromLocalFile(path))
            media.play()
        self.track_changed.emit()

    def toggle(self) -> None:
        """Pause / resume; with nothing loaded, nothing happens."""
        if self.current is None:
            return
        media = self.media()
        if self.playing:
            media.pause()
        else:
            media.play()

    def stop(self) -> None:
        if self._media is not None:
            self._media.stop()

    def step(self, n: int = 1) -> None:
        """n tracks on (or back, n < 0), wrapping around with repeat on."""
        if not self.order:
            return
        pos = self.pos + n
        if 0 <= pos < len(self.order):
            self.pos = pos
        elif self.repeat == "off":
            self.stop()
            return
        else:
            if self.shuffle and pos >= len(self.order):  # a new round, in a new order
                self.order = music.shuffled(len(self.tracks))
            self.pos = pos % len(self.order)
        self._play_current()

    def previous(self) -> None:
        """Back to the start of this track, or the one before when near its start."""
        if self.position > 3000:
            self.seek(0)
        else:
            self.step(-1)

    def seek(self, ms: int) -> None:
        if self._media is not None and self.current is not None:
            self._media.setPosition(max(0, min(int(ms), self.duration or int(ms))))

    def seek_by(self, ms: int) -> None:
        self.seek(self.position + ms)

    def set_shuffle(self, on: bool) -> None:
        self.shuffle = on
        i = self.index
        if self.tracks:
            if on:
                self._make_order(i)
            else:
                self.order = list(range(len(self.tracks)))
                self.pos = i
        self.options_changed.emit()

    def cycle_repeat(self) -> str:
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)]
        self.options_changed.emit()
        return self.repeat

    def set_repeat(self, mode: str) -> None:
        if mode in REPEAT_MODES:
            self.repeat = mode
            self.options_changed.emit()

    def remove(self, i: int) -> None:
        """Take track i off the list (the file stays); the one playing goes on to the next."""
        if not 0 <= i < len(self.tracks):
            return
        was_current = i == self.index
        playing = self.playing
        current = self.index
        del self.tracks[i]
        self.order = [j - (j > i) for j in self.order if j != i]
        if not self.tracks:
            self.pos = -1
            self._play_current()
            return
        if was_current:
            self.pos = min(self.pos, len(self.order) - 1)
            if playing:
                self._play_current()
            else:
                self.media().setSource(QUrl.fromLocalFile(self.current or ""))
                self.track_changed.emit()
        else:
            self.pos = self.order.index(current - (current > i))
            self.track_changed.emit()

    def clear(self) -> None:
        self.tracks, self.order, self.pos = [], [], -1
        if self._media is not None:
            self._media.stop()
            self._media.setSource(QUrl())
        self.track_changed.emit()

    def rename(self, old: str, new: str) -> None:
        """A track was renamed or moved: keep it in the list (not the one playing)."""
        if old in self.tracks and old != self.current:
            self.tracks[self.tracks.index(old)] = new
            self.track_changed.emit()

    # Events

    def _on_status(self, status) -> None:
        S = QMediaPlayer.MediaStatus
        if status == S.EndOfMedia:
            if self.repeat == "one":
                self._play_current()
            elif self.pos == len(self.order) - 1 and self.repeat == "off":
                self.track_changed.emit()  # finished: stays on the last track
            else:
                self.step(1)
        elif status in (S.BufferedMedia, S.LoadedMedia):
            self._errors = 0

    def _on_error(self, error, text: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.message.emit(f"Can't play {music.title(self.current or '')}: {text}")
        self._errors += 1
        if self._errors < len(self.tracks):
            self.step(1)


class SeekSlider(QSlider):
    """A slider that jumps to where it is clicked (not a page at a time)."""
    jumped = Signal(int)

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                                 QStyle.SubControl.SC_SliderHandle, self)
            x = int(event.position().x())
            if not handle.contains(event.position().toPoint()):
                value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                                       x - handle.width() // 2,
                                                       self.width() - handle.width())
                self.setValue(value)
                self.jumped.emit(value)
        super().mousePressEvent(event)


class ElidedLabel(QLabel):
    """One line of text cut off with … when it doesn't fit; clicking it emits clicked."""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self._text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_text(self, text: str) -> None:
        self._text = text
        self.setToolTip(text)
        self.update()

    def sizeHint(self):
        return QSize(220, self.fontMetrics().height())

    def minimumSizeHint(self):
        return QSize(40, self.fontMetrics().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


REPEAT_TEXT = {"all": ("↻", "Repeat the list"), "one": ("↻1", "Repeat this track"),
               "off": ("→", "No repeat: stop after the last track")}


class PlayerBar(QDockWidget):
    """The player as one row at the bottom: ⏮ ⏯ ⏭  title  ━━●━━  time  volume  ⇄ ↻ ≡ ✕."""
    playlist_requested = Signal()
    close_requested = Signal()

    def __init__(self, player: Player, parent: QWidget | None = None):
        super().__init__("Player", parent)
        self.setObjectName("player")
        self.player = player
        self.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.setTitleBarWidget(QWidget())  # no title bar: as small as it gets
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)
        style = self.style()

        def button(icon=None, text="", tip="", slot=None, checkable=False) -> QToolButton:
            b = QToolButton()
            if icon is not None:
                b.setIcon(style.standardIcon(icon))
            else:
                b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if slot:
                b.clicked.connect(slot)
            return b

        SP = QStyle.StandardPixmap
        self.prev_button = button(SP.SP_MediaSkipBackward, tip="Previous track   [m", slot=player.previous)
        self.play_button = button(SP.SP_MediaPlay, tip="Play / pause   gM", slot=player.toggle)
        self.next_button = button(SP.SP_MediaSkipForward, tip="Next track   ]m", slot=lambda: player.step(1))
        self.title = ElidedLabel()
        self.title.clicked.connect(self.playlist_requested)
        self.seek = SeekSlider()
        self.seek.setMinimumWidth(80)
        self.seek.jumped.connect(player.seek)
        self.seek.sliderReleased.connect(lambda: player.seek(self.seek.value()))
        self.seek.sliderMoved.connect(lambda v: self._show_time(v, self.player.duration))
        self.time = QLabel("0:00 / 0:00")
        self.volume = SeekSlider()
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(70)
        self.volume.setValue(player.volume)
        self.volume.valueChanged.connect(player.set_volume)
        self.volume.setToolTip("Volume")
        self.shuffle_button = button(text="⇄", tip="Shuffle", checkable=True,
                                     slot=lambda on: player.set_shuffle(on))
        self.shuffle_button.setChecked(player.shuffle)
        self.repeat_button = button(text="↻", slot=self._cycle_repeat)
        self.speed_button = button(text="1×", tip="Speed (click = choose, wheel = faster / slower)")
        self.speed_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        speed_menu = QMenu(self.speed_button)
        for rate in SPEEDS:
            speed_menu.addAction(music.format_speed(rate), lambda r=rate: player.set_speed(r))
        self.speed_button.setMenu(speed_menu)
        self.speed_button.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self.speed_button.installEventFilter(self)
        self.list_button = button(text="≡", tip="Playlist in the other pane   Ctrl-w m",
                                  slot=self.playlist_requested)
        self.close_button = button(SP.SP_TitleBarCloseButton, tip="Stop and close the player   :play off",
                                   slot=self.close_requested)
        for w in (self.prev_button, self.play_button, self.next_button):
            row.addWidget(w)
        row.addWidget(self.title, 3)
        row.addWidget(self.seek, 4)
        for w in (self.time, self.volume, self.speed_button, self.shuffle_button, self.repeat_button, self.list_button,
                  self.close_button):
            row.addWidget(w)
        self.setWidget(box)
        self._update_repeat()
        self._update_speed()

        player.track_changed.connect(self.update_track)
        player.playing_changed.connect(self._update_playing)
        player.position_changed.connect(self._update_position)
        player.options_changed.connect(self._update_options)
        self.update_track()

    def update_track(self) -> None:
        p = self.player
        if p.current is None:
            self.title.set_text("")
        else:
            self.title.set_text(f"{p.index + 1}/{len(p.tracks)}  {music.title(p.current)}")
            self.title.setToolTip(f"{p.current}\nclick = the playlist (Ctrl-w m)")

    def _update_playing(self, playing: bool) -> None:
        self.play_button.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay))

    def _update_position(self, position: int, duration: int) -> None:
        if not self.seek.isSliderDown():
            self.seek.setRange(0, max(0, duration))
            self.seek.setValue(position)
            self._show_time(position, duration)

    def _show_time(self, position: int, duration: int) -> None:
        self.time.setText(f"{music.format_time(position)} / {music.format_time(duration)}")

    def _update_options(self) -> None:
        p = self.player
        self.shuffle_button.setChecked(p.shuffle)
        if self.volume.value() != p.volume:
            self.volume.blockSignals(True)
            self.volume.setValue(p.volume)
            self.volume.blockSignals(False)
        self._update_repeat()
        self._update_speed()

    def _update_speed(self) -> None:
        rate = self.player.speed
        self.speed_button.setText(music.format_speed(rate))
        font = self.speed_button.font()
        font.setBold(rate != 1.0)  # stands out when not normal
        self.speed_button.setFont(font)

    def eventFilter(self, obj, event):
        if obj is self.speed_button and event.type() == QEvent.Type.Wheel:
            self.player.faster(1 if event.angleDelta().y() > 0 else -1)
            return True
        return super().eventFilter(obj, event)

    def _cycle_repeat(self) -> None:
        self.player.cycle_repeat()

    def _update_repeat(self) -> None:
        text, tip = REPEAT_TEXT[self.player.repeat]
        self.repeat_button.setText(text)
        self.repeat_button.setToolTip(tip + "   (click = next mode)")


class PlaylistList(QListWidget):
    """The tracks, with vim keys; everything it can't do itself goes out as key_command."""
    key_command = Signal(str)  # "play", "toggle", "next", "prev", "remove", "back", "close", "seek+", "seek-"
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setUniformItemSizes(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._pending = ""  # g of gg, Ctrl-w of Ctrl-w h

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.LinkAction)
            event.accept()
        else:
            event.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.setDropAction(Qt.DropAction.LinkAction)  # the files stay where they are
            event.accept()
            self.files_dropped.emit(paths)

    def move_to(self, row: int) -> None:
        if self.count():
            self.setCurrentRow(max(0, min(row, self.count() - 1)))

    def keyPressEvent(self, event):
        text, key = event.text(), event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        pending, self._pending = self._pending, ""
        K = Qt.Key
        if pending == "<C-w>":
            self.key_command.emit("back")  # Ctrl-w h / l / w: back to the files
            return
        if ctrl and key == K.Key_W:
            self._pending = "<C-w>"
            return
        page = max(1, self.viewport().height() // max(1, self.sizeHintForRow(0)))
        if text == "j" or key == K.Key_Down:
            self.move_to(self.currentRow() + 1)
        elif text == "k" or key == K.Key_Up:
            self.move_to(self.currentRow() - 1)
        elif ctrl and key == K.Key_D:
            self.move_to(self.currentRow() + page // 2)
        elif ctrl and key == K.Key_U:
            self.move_to(self.currentRow() - page // 2)
        elif text == "g":
            if pending == "g":
                self.move_to(0)
            else:
                self._pending = "g"
        elif text == "G" or key == K.Key_End:
            self.move_to(self.count() - 1)
        elif key == K.Key_Home:
            self.move_to(0)
        elif key in (K.Key_Return, K.Key_Enter) or text == "l":
            self.key_command.emit("play")
        elif key in (K.Key_Space, K.Key_MediaPlay, K.Key_MediaPause, K.Key_MediaTogglePlayPause):
            self.key_command.emit("toggle")
        elif text in ("n", "]") or key == K.Key_MediaNext:
            self.key_command.emit("next")
        elif text in ("N", "p", "[") or key == K.Key_MediaPrevious:
            self.key_command.emit("prev")
        elif key == K.Key_Right or text == ">":
            self.key_command.emit("seek+")
        elif key == K.Key_Left or text == "<":
            self.key_command.emit("seek-")
        elif key == K.Key_Delete or text in ("d", "x"):
            self.key_command.emit("remove")
        elif text == "q":
            self.key_command.emit("close")
        elif key in (K.Key_Escape, K.Key_Tab, K.Key_Backtab) or text == "h":
            self.key_command.emit("back")
        elif text in ("+", "="):
            self.key_command.emit("louder")
        elif text == "-":
            self.key_command.emit("quieter")
        elif text == "}":
            self.key_command.emit("faster")
        elif text == "{":
            self.key_command.emit("slower")
        elif text == "0":
            self.key_command.emit("speed1")
        elif text == "s":
            self.key_command.emit("shuffle")
        elif text == "r":
            self.key_command.emit("repeat")
        elif text == "o" or text == ":":
            self.key_command.emit(text)  # o = show the file in the pane, : = command line
        else:
            super().keyPressEvent(event)


class PlaylistView(QWidget):
    """The player's tracks in a pane (Ctrl-w m): a title line and the list."""
    back = Signal()  # focus back to the files
    close_requested = Signal()
    goto = Signal(str)  # show this file in the file pane
    command_line = Signal()

    def __init__(self, player: Player, parent: QWidget | None = None):
        super().__init__(parent)
        self.player = player
        self.header = QLabel()
        self.header.setContentsMargins(6, 3, 6, 3)
        self.list = PlaylistList()
        self.list.key_command.connect(self._key_command)
        self.list.files_dropped.connect(lambda paths: self.player.add(music.collect(paths)))
        self.list.itemActivated.connect(lambda item: self.player.play_index(self.list.row(item)))
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(self.header)
        box.addWidget(self.list)
        self.setFocusProxy(self.list)
        self._shown: list[str] = []  # the tracks the items were made from
        self._marked = -1
        player.track_changed.connect(self.refresh)
        player.playing_changed.connect(lambda _: self._update_header())
        player.options_changed.connect(self._update_header)
        self.refresh()

    def refresh(self) -> None:
        p = self.player
        if p.tracks != self._shown:
            row = self.list.currentRow()
            self.list.clear()
            for n, name in enumerate(music.display_names(p.tracks), 1):
                item = QListWidgetItem(f"  {n:>3}  {name}")  # "▶ " goes over the first two
                item.setToolTip(p.tracks[n - 1])
                self.list.addItem(item)
            self._shown = list(p.tracks)
            self._marked = -1
            self.list.move_to(row if row >= 0 else p.index)
        self._mark(p.index)
        self._update_header()

    def _mark(self, index: int) -> None:
        if self._marked == index:
            return
        for row, bold in ((self._marked, False), (index, True)):
            item = self.list.item(row) if row >= 0 else None
            if item is not None:
                font = QFont(self.list.font())
                font.setBold(bold)
                item.setFont(font)
                text = item.text()
                item.setText(("▶ " if bold else "  ") + text[2:])
        self._marked = index
        if index >= 0 and not self.list.hasFocus():
            self.list.move_to(index)
            self.list.scrollToItem(self.list.item(index), QAbstractItemView.ScrollHint.PositionAtCenter)

    def _update_header(self) -> None:
        p = self.player
        state = "playing" if p.playing else "paused" if p.current else "stopped"
        extras = (["shuffle"] if p.shuffle else []) + [f"repeat {p.repeat}"] + \
            ([f"speed {music.format_speed(p.speed)}"] if p.speed != 1.0 else [])
        self.header.setText(f"<b>Playlist</b> — {len(p.tracks)} track(s), {state}, {', '.join(extras)}"
                            "   <span style='color:gray'>Enter play · Space pause · n/N next/prev · "
                            "←/→ seek · { } speed · d remove · q close</span>")

    def _key_command(self, cmd: str) -> None:
        p, row = self.player, self.list.currentRow()
        if cmd == "play":
            p.play_index(row)
        elif cmd == "toggle":
            p.toggle()
        elif cmd == "next":
            p.step(1)
        elif cmd == "prev":
            p.previous()
        elif cmd == "seek+":
            p.seek_by(SEEK_STEP)
        elif cmd == "seek-":
            p.seek_by(-SEEK_STEP)
        elif cmd == "remove":
            p.remove(row)
            self.list.move_to(row)
        elif cmd == "louder":
            p.set_volume(p.volume + 5)
        elif cmd == "quieter":
            p.set_volume(p.volume - 5)
        elif cmd == "faster":
            p.faster(1)
        elif cmd == "slower":
            p.faster(-1)
        elif cmd == "speed1":
            p.set_speed(1.0)
        elif cmd == "shuffle":
            p.set_shuffle(not p.shuffle)
        elif cmd == "repeat":
            p.cycle_repeat()
        elif cmd == "o" and 0 <= row < len(p.tracks):
            self.goto.emit(p.tracks[row])
        elif cmd == ":":
            self.command_line.emit()
        elif cmd == "close":
            self.close_requested.emit()
        elif cmd == "back":
            self.back.emit()

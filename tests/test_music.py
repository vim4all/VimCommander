import os
import random
import tempfile
import unittest

from vimcommander import music


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


class PlaylistTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "Album")
        for name in ("10 ten.mp3", "2 two.FLAC", "1 one.ogg", "cover.jpg", "CD2/1 bonus.opus",
                     ".hidden/x.mp3", "CD10/a.mp3"):
            touch(os.path.join(self.root, name))

    def tearDown(self):
        self.tmp.cleanup()

    def rel(self, tracks):
        return [os.path.relpath(t, self.root).replace(os.sep, "/") for t in tracks]

    def test_folder_and_tree(self):
        self.assertEqual(self.rel(music.folder_tracks(self.root)), ["1 one.ogg", "2 two.FLAC", "10 ten.mp3"])
        self.assertEqual(self.rel(music.tree_tracks(self.root)),
                         ["1 one.ogg", "2 two.FLAC", "10 ten.mp3", "CD2/1 bonus.opus", "CD10/a.mp3"])
        self.assertEqual(len(music.tree_tracks(self.root, limit=2)), 2)
        self.assertEqual(music.folder_tracks(os.path.join(self.root, "nothing")), [])

    def test_m3u_and_collect(self):
        m3u = os.path.join(self.root, "mix.m3u8")
        with open(m3u, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n#EXTINF:1,x\nCD2/1 bonus.opus\nmissing.mp3\nhttp://radio/x.mp3\n"
                    "cover.jpg\n2 two.FLAC\n")
        self.assertEqual(self.rel(music.read_m3u(m3u)), ["CD2/1 bonus.opus", "2 two.FLAC"])
        one = os.path.join(self.root, "1 one.ogg")
        tracks = music.collect([one, m3u, os.path.join(self.root, "CD2"), os.path.join(self.root, "cover.jpg")])
        self.assertEqual(self.rel(tracks), ["1 one.ogg", "CD2/1 bonus.opus", "2 two.FLAC"])  # each once

    def test_write_m3u_round_trip(self):
        tracks = music.tree_tracks(self.root)
        out = os.path.join(self.tmp.name, "list.m3u8")
        music.write_m3u(out, tracks)
        with open(out, encoding="utf-8") as f:
            self.assertIn("Album/CD2/1 bonus.opus".replace("/", os.sep), f.read())
        self.assertEqual(music.read_m3u(out), tracks)

    def test_names_and_times(self):
        tracks = music.tree_tracks(self.root)
        self.assertEqual(music.display_names(tracks)[:4], ["1 one", "2 two", "10 ten", "CD2/1 bonus"])
        self.assertEqual(music.display_names([]), [])
        self.assertEqual(music.title("/x/03 Song.mp3"), "03 Song")
        self.assertEqual([music.format_time(ms) for ms in (0, 65_400, 720_000, 3_723_000, -5)],
                         ["0:00", "1:05", "12:00", "1:02:03", "0:00"])
        self.assertTrue(music.is_audio("a.M4A") and music.is_playlist("b.M3U") and not music.is_audio("c.txt"))

    def test_shuffled(self):
        order = music.shuffled(10, 7, random.Random(1))
        self.assertEqual(order[0], 7)
        self.assertEqual(sorted(order), list(range(10)))
        self.assertEqual(music.shuffled(0), [])

    def test_speed(self):
        speeds = (0.5, 1.0, 1.5, 2.0)
        self.assertEqual([music.clamp_speed(x) for x in (9, 0.1, "x", None, float("nan"), 1.234)],
                         [4.0, 0.25, 1.0, 1.0, 1.0, 1.23])
        self.assertEqual(music.step_speed(1.0, 1, speeds), 1.5)
        self.assertEqual(music.step_speed(1.0, -1, speeds), 0.5)
        self.assertEqual(music.step_speed(1.2, 1, speeds), 1.5)  # from between two steps
        self.assertEqual(music.step_speed(1.2, -1, speeds), 1.0)
        self.assertEqual(music.step_speed(2.0, 1, speeds), 2.0)  # the ends stay
        self.assertEqual(music.step_speed(0.5, -3, speeds), 0.5)
        self.assertEqual(music.step_speed(0.5, 9, speeds), 2.0)
        for text, rate in (("1.5", 1.5), ("1.5x", 1.5), ("150%", 1.5), ("+0.25", 1.25), ("-.5", 0.5),
                           ("10", 4.0)):
            self.assertEqual(music.parse_speed(text, 1.0), rate, text)
        self.assertIsNone(music.parse_speed("fast", 1.0))
        self.assertEqual((music.format_speed(1.0), music.format_speed(1.25)), ("1×", "1.25×"))

    def test_audio_backend(self):
        def exists(found):
            return lambda path: path in found
        self.assertIsNone(music.audio_backend({}, "win32", exists(set())))
        self.assertIsNone(music.audio_backend({"QT_AUDIO_BACKEND": "pipewire"}, "linux", exists(set())))
        self.assertEqual(music.audio_backend({"HOME": "/h"}, "linux", exists(set())), "pulseaudio")
        conf = os.path.join("/usr/share/pipewire", "client.conf")
        self.assertIsNone(music.audio_backend({"HOME": "/h"}, "linux", exists({conf})))


class _Media:
    """Stands in for QMediaPlayer: records what it was told."""
    def __init__(self):
        self.source, self.pos, self.calls = None, 0, []

    def setSource(self, url): self.source = url.toLocalFile()
    def play(self): self.calls.append("play")
    def pause(self): self.calls.append("pause")
    def stop(self): self.calls.append("stop")
    def position(self): return self.pos
    def setPosition(self, ms): self.pos = ms
    def setPlaybackRate(self, rate): self.rate = rate
    def setPitchCompensation(self, on): self.pitch = on
    def duration(self): return 200_000

    def playbackState(self):
        from PySide6.QtMultimedia import QMediaPlayer
        return QMediaPlayer.PlaybackState.PlayingState


class PlayerTest(unittest.TestCase):
    """Play order, repeat, shuffle and removing tracks, without an audio device."""

    def setUp(self):
        try:
            from vimcommander.player import Player
        except ImportError:
            self.skipTest("QtMultimedia (PySide6-Addons) not installed")
        self.player = Player(options={"repeat": "all"})
        self.media = _Media()
        self.player._media = self.media
        self.tracks = [f"/m/{i}.mp3" for i in range(4)]

    def test_steps_and_repeat(self):
        p = self.player
        p.load(self.tracks, 2)
        self.assertEqual(self.media.source, "/m/2.mp3")
        p.step(1)
        p.step(1)  # wraps around with repeat all
        self.assertEqual(p.current, "/m/0.mp3")
        p.step(-1)
        self.assertEqual(p.current, "/m/3.mp3")
        p.set_repeat("off")
        p.step(1)  # the end: stops, stays on the last track
        self.assertEqual((p.current, self.media.calls[-1]), ("/m/3.mp3", "stop"))
        self.media.pos = 10_000
        p.previous()  # past 3 s: back to the start of the track
        self.assertEqual((p.current, self.media.pos), ("/m/3.mp3", 0))
        p.previous()
        self.assertEqual(p.current, "/m/2.mp3")
        self.assertEqual(p.cycle_repeat(), "all")

    def test_shuffle_keeps_current(self):
        p = self.player
        p.load(self.tracks, 1)
        p.set_shuffle(True)
        self.assertEqual((p.current, sorted(p.order)), ("/m/1.mp3", [0, 1, 2, 3]))
        seen = {p.current}
        for _ in range(3):
            p.step(1)
            seen.add(p.current)
        self.assertEqual(len(seen), 4)  # every track once per round
        p.set_shuffle(False)
        self.assertEqual(p.order, [0, 1, 2, 3])

    def test_remove_add_rename(self):
        p = self.player
        p.load(self.tracks, 2)
        p.remove(0)  # before the current one
        self.assertEqual((p.current, p.index), ("/m/2.mp3", 1))
        p.remove(1)  # the current one: goes on to the next
        self.assertEqual(p.current, "/m/3.mp3")
        self.assertEqual(p.add(["/m/3.mp3", "/m/9.mp3", "/m/9.mp3"]), 1)
        self.assertEqual(p.tracks, ["/m/1.mp3", "/m/3.mp3", "/m/9.mp3"])
        p.rename("/m/9.mp3", "/m/nine.mp3")
        self.assertEqual(p.tracks[-1], "/m/nine.mp3")
        p.clear()
        self.assertIsNone(p.current)

    def test_volume_is_clamped(self):
        self.player.set_volume(150)
        self.assertEqual(self.player.options(), {"volume": 100, "shuffle": False, "repeat": "all", "speed": 1.0})

    def test_speed(self):
        p = self.player
        p.faster(1)
        self.assertEqual((p.speed, self.media.rate, self.media.pitch), (1.1, 1.1, True))
        p.faster(-2)
        self.assertEqual(p.speed, 0.9)
        p.set_speed(7)
        self.assertEqual((p.speed, p.options()["speed"]), (4.0, 4.0))


if __name__ == "__main__":
    unittest.main()

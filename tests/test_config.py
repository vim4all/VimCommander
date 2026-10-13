import json
import os
import tempfile
import unittest

from vimcommander import config
from vimcommander.bar import WINDOWS, expand_args, split_command
from vimcommander.vim import NORMAL, VISUAL, KeyEngine, user_bindings


class UserBindingsTest(unittest.TestCase):
    def engine(self, user):
        bindings, arg_bindings = user_bindings(user)
        return KeyEngine(bindings, arg_bindings)

    def test_add_and_override(self):
        engine = self.engine({"normal": {"Q": "edit", "j": "up"}})
        self.assertEqual(engine.feed("Q").name, "edit")
        self.assertEqual(engine.feed("j").name, "up")

    def test_colon_value_runs_a_command(self):
        engine = self.engine({"normal": {"gd": ":cd ~/Downloads"}})
        engine.feed("g")
        action = engine.feed("d")
        self.assertEqual((action.name, action.arg), ("ex", "cd ~/Downloads"))

    def test_empty_value_unbinds(self):
        bindings, arg_bindings = user_bindings({"normal": {"dd": "", "m": ""}, "visual": {"y": ""}})
        self.assertNotIn("dd", bindings[NORMAL])
        self.assertNotIn("m", arg_bindings[NORMAL])
        self.assertNotIn("y", bindings[VISUAL])

    def test_defaults_untouched(self):
        user_bindings({"normal": {"j": "up"}})
        self.assertEqual(KeyEngine().feed("j").name, "down")


class CommandTest(unittest.TestCase):
    ctx = {"cwd": "/here", "other": "/there", "current": "/here/a.txt", "selected": []}

    def test_list_is_taken_as_split(self):
        self.assertEqual(split_command(["C:/x y/app.exe", "%f"]), ["C:/x y/app.exe", "%f"])

    @unittest.skipIf(WINDOWS, "POSIX quoting")
    def test_quoted_program(self):
        self.assertEqual(split_command('"/opt/my app/run" -n %f'), ["/opt/my app/run", "-n", "%f"])

    def test_placeholders(self):
        self.assertEqual(expand_args("%d %o %f %s", self.ctx, ["/x", "/y"]),
                         ["/here", "/there", "/here/a.txt", "/x", "/y"])


class ConfigFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "config.json")
        os.environ["VIMCOMMANDER_CONFIG"] = self.path

    def tearDown(self):
        del os.environ["VIMCOMMANDER_CONFIG"]
        self.dir.cleanup()

    def test_missing_file_is_empty(self):
        self.assertEqual(config.load(), {})

    def test_bad_json_raises(self):
        with open(self.path, "w") as f:
            f.write('{"show_hidden": true,}')
        with self.assertRaises(config.ConfigError):
            config.load()

    def test_defaults_merge_one_level(self):
        merged = config.with_defaults({"window": {"width": 1400}, "marks": {"a": ["/", ""]}})
        self.assertEqual(merged["window"]["width"], 1400)
        self.assertEqual(merged["window"]["height"], config.DEFAULTS["window"]["height"])
        self.assertEqual(merged["marks"], {"a": ["/", ""]})
        self.assertEqual(config.DEFAULTS["window"]["width"], 1100)  # defaults not mutated

    def test_save_round_trip(self):
        config.save({"a": "ü"})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": "ü"})


if __name__ == "__main__":
    unittest.main()

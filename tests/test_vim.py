import unittest

from vimcommander.vim import NORMAL, NORMAL_KEYS, VISUAL, KeyEngine, parse_langmap, split_keys


def feed(engine, *keys):
    actions = [engine.feed(k) for k in keys]
    return [a for a in actions if a]


class KeyEngineTest(unittest.TestCase):
    def test_single_key(self):
        [a] = feed(KeyEngine(), "j")
        self.assertEqual((a.name, a.count), ("down", None))

    def test_count(self):
        [a] = feed(KeyEngine(), "1", "2", "j")
        self.assertEqual((a.name, a.count), ("down", 12))

    def test_zero_alone_is_not_a_count(self):
        engine = KeyEngine()
        self.assertEqual(feed(engine, "0", "j")[0].count, None)

    def test_multi_key_sequence_waits(self):
        engine = KeyEngine()
        self.assertIsNone(engine.feed("g"))
        self.assertEqual(engine.pending_display, "g")
        self.assertEqual(engine.feed("g").name, "first")
        self.assertEqual(engine.pending_display, "")

    def test_count_with_sequence(self):
        [a] = feed(KeyEngine(), "3", "y", "y")
        self.assertEqual((a.name, a.count), ("yank", 3))

    def test_unknown_sequence_resets(self):
        engine = KeyEngine()
        self.assertEqual(feed(engine, "5", "g", "q"), [])
        self.assertEqual(engine.pending_display, "")
        self.assertEqual(feed(engine, "j")[0].count, None)

    def test_special_keys(self):
        self.assertEqual(feed(KeyEngine(), "<C-d>")[0].name, "half_down")
        self.assertEqual(feed(KeyEngine(), "<CR>")[0].name, "open")

    def test_visual_mode_uses_its_own_table(self):
        engine = KeyEngine()
        engine.mode = VISUAL
        self.assertEqual(feed(engine, "d")[0].name, "cut")
        engine.mode = NORMAL
        self.assertEqual(feed(engine, "d"), [])
        self.assertEqual(engine.pending_display, "d")

    def test_argument_key(self):
        engine = KeyEngine()
        self.assertIsNone(engine.feed("m"))
        self.assertEqual(engine.pending_display, "m")
        a = engine.feed("a")
        self.assertEqual((a.name, a.arg), ("set_mark", "a"))

    def test_argument_key_with_count(self):
        [a] = feed(KeyEngine(), "3", "f", "x")
        self.assertEqual((a.name, a.count, a.arg), ("find_char", 3, "x"))

    def test_escape_cancels_argument(self):
        engine = KeyEngine()
        self.assertEqual(feed(engine, "'", "<Esc>"), [])
        self.assertEqual(feed(engine, "j")[0].name, "down")

    def test_fixed_argument_in_binding(self):
        [a] = feed(KeyEngine(), "g", "3")
        self.assertEqual((a.name, a.arg), ("bar", "3"))

    def test_ctrl_w_sequence(self):
        [a] = feed(KeyEngine(), "<C-w>", "l")
        self.assertEqual(a.name, "right_pane")

    def test_pane_layout_keys(self):
        for keys, name in ((("<C-w>", "o"), "only_pane"), (("<C-w>", "x"), "swap_panes"),
                           (("<C-w>", "="), "equal_panes"), (("<C-w>", "|"), "maximize_pane")):
            self.assertEqual(feed(KeyEngine(), *keys)[0].name, name)
        [a] = feed(KeyEngine(), "3", "<C-w>", ">")
        self.assertEqual((a.name, a.count, a.arg), ("resize_pane", 3, "1"))
        self.assertEqual(feed(KeyEngine(), "<C-w>", "<")[0].arg, "-1")
        self.assertEqual(feed(KeyEngine(), "<C-w>", "<C-w>")[0].name, "switch_pane")

    def test_increment_keys(self):
        [a] = feed(KeyEngine(), "5", "<C-a>")
        self.assertEqual((a.name, a.count, a.arg), ("increment", 5, None))
        [a] = feed(KeyEngine(), "g", "<C-x>")
        self.assertEqual((a.name, a.arg), ("decrement", "g"))
        engine = KeyEngine()
        engine.mode = VISUAL
        self.assertEqual(feed(engine, "g", "<C-a>")[0].arg, "g")

    def test_case_operators(self):
        [a] = feed(KeyEngine(), "g", "U", "U")
        self.assertEqual(a.name, "upper")
        [a] = feed(KeyEngine(), "2", "g", "u", "3", "j")
        self.assertEqual((a.name, a.arg, a.count), ("lower_motion", "down", 6))
        self.assertEqual(feed(KeyEngine(), "g", "~", "G")[0].name, "toggle_case_motion")
        engine = KeyEngine()
        engine.mode = VISUAL
        self.assertEqual([a.name for a in feed(engine, "U", "u", "~", "x")],
                         ["upper", "lower", "toggle_case", "cut"])

    def test_split_keys(self):
        self.assertEqual(split_keys("<leader>d<cr>", ","), [",", "d", "<CR>"])
        self.assertEqual(split_keys("<c-W>o<C-S-P><s-f5><M-x>"), ["<C-w>", "o", "<C-S-p>", "<S-F5>", "<A-x>"])
        self.assertEqual(split_keys("<lt><Bar><Space>a"), ["<", "|", "<Space>", "a"])

    def test_map_waits_for_longer_one(self):
        bindings = {NORMAL: {**NORMAL_KEYS, ",x": "keys:G"}, VISUAL: {}}
        engine = KeyEngine(bindings)
        self.assertEqual(feed(engine, ","), [])  # "," alone is a binding too: wait
        self.assertEqual(engine.flush().name, "repeat_find_back")  # the timeout
        self.assertEqual(feed(engine, ",", "x")[0].arg, "G")
        [a] = feed(engine, "2", ",", "j")  # "," then j: the j is left over
        self.assertEqual((a.name, a.count, engine.leftover), ("repeat_find_back", 2, "j"))
        self.assertEqual(KeyEngine().flush(), None)
        engine = KeyEngine()
        self.assertEqual(feed(KeyEngine(), "<C-w>", "<")[0].name, "resize_pane")  # <C-w><C-w> starts the same

    def test_shared_prefix(self):
        engine = KeyEngine()
        self.assertEqual(feed(engine, "y", "f")[0].name, "yank_path")
        self.assertEqual(feed(engine, "y", "y")[0].name, "yank")


    def test_operator_motion(self):
        [a] = feed(KeyEngine(), "y", "j")
        self.assertEqual((a.name, a.count, a.arg), ("yank_motion", None, "down"))
        [a] = feed(KeyEngine(), "d", "g", "g")
        self.assertEqual((a.name, a.arg), ("cut_motion", "first"))

    def test_counts_multiply(self):
        engine = KeyEngine()
        self.assertIsNone(engine.feed("2"))
        self.assertIsNone(engine.feed("d"))
        self.assertIsNone(engine.feed("3"))
        self.assertEqual(engine.pending_display, "2d3")
        self.assertEqual(engine.feed("j").count, 6)

    def test_doubled_operator_and_old_sequences(self):
        self.assertEqual(feed(KeyEngine(), "y", "3", "y")[0].count, 3)
        self.assertEqual(feed(KeyEngine(), "y", "n")[0].name, "yank_name")
        self.assertEqual(feed(KeyEngine(), "y", "f")[0].name, "yank_path")

    def test_repeat_keys(self):
        self.assertEqual(feed(KeyEngine(), ".")[0].name, "repeat")
        [a] = feed(KeyEngine(), "@", ":")
        self.assertEqual((a.name, a.arg), ("at", ":"))


    def test_register_prefix(self):
        [a] = feed(KeyEngine(), '"', "a", "y", "y")
        self.assertEqual((a.name, a.register), ("yank", "a"))
        [a] = feed(KeyEngine(), "3", '"', "+", "y", "j")
        self.assertEqual((a.name, a.count, a.arg, a.register), ("yank_motion", 3, "down", "+"))
        engine = KeyEngine()
        feed(engine, '"', "A", "2")
        self.assertEqual(engine.pending_display, '2"A')
        self.assertEqual(feed(engine, "d", "d")[0].register, "A")
        self.assertIsNone(feed(engine, "p")[0].register)  # the prefix is used once

    def test_new_keys(self):
        self.assertEqual(feed(KeyEngine(), "<C-r>")[0].name, "redo")
        self.assertEqual(feed(KeyEngine(), "Z", "Z")[0].name, "quit")
        [a] = feed(KeyEngine(), "q", "a")
        self.assertEqual((a.name, a.arg), ("record", "a"))
        [a] = feed(KeyEngine(), "5", "0", "%")
        self.assertEqual((a.name, a.count), ("percent", 50))
        self.assertEqual(feed(KeyEngine(), "d", "}")[0].arg, "group_next")


if __name__ == "__main__":
    unittest.main()


class LangmapTest(unittest.TestCase):
    def engine(self, spec="cyrillic"):
        e = KeyEngine()
        e.langmap = parse_langmap(spec)
        return e

    def test_cyrillic_keys_act_as_latin(self):
        e = self.engine()
        [a] = feed(e, "3", "о")
        self.assertEqual((a.name, a.count), ("down", 3))
        [a] = feed(e, "п", "п")
        self.assertEqual(a.name, "first")
        [a] = feed(e, "<C-в>")
        self.assertEqual(a.name, "half_down")

    def test_marks_translate_but_find_char_does_not(self):
        e = self.engine()
        [a] = feed(e, "ь", "ф")  # m a
        self.assertEqual((a.name, a.arg), ("set_mark", "a"))
        [a] = feed(e, "а", "с")  # f, then a Cyrillic с to find
        self.assertEqual((a.name, a.arg), ("find_char", "с"))

    def test_latin_untouched_and_off(self):
        [a] = feed(self.engine(), ".")
        self.assertEqual(a.name, "repeat")
        self.assertEqual(feed(self.engine(""), "о"), [])

    def test_vim_format(self):
        self.assertEqual(parse_langmap("õ[ü]"), {"õ": "[", "ü": "]"})
        self.assertEqual(parse_langmap("аб;f\\;"), {"а": "f", "б": ";"})
        self.assertEqual(parse_langmap("cyrillic,ж:")["ж"], ":")
        for bad in ("аб;f", "ab;cd", "абв"):
            with self.assertRaises(ValueError):
                parse_langmap(bad)

import threading
import unittest

from vimcommander import syntax


@unittest.skipUnless(syntax.available(), "Pygments isn't installed")
class SyntaxTest(unittest.TestCase):
    def runs(self, name, text):
        return syntax.spans(text, syntax.lexer_for(name, text[:256]))

    def test_lexer_choice(self):
        for name, head in (("main.c", ""), ("a.py", ""), ("CMakeLists.txt", ""), ("Makefile", ""),
                           ("x.rs", ""), ("run", "#!/bin/bash\necho hi")):
            self.assertIsNotNone(syntax.lexer_for(name, head), name)
        for name, head in (("notes.txt", ""), ("data.unknownext", ""), ("README", "hello")):
            self.assertIsNone(syntax.lexer_for(name, head), name)

    def test_c(self):
        runs = self.runs("main.c", '#include <stdio.h>\n/* two\n   lines */\nint main(void) { return 0x1F; }\n')
        self.assertEqual(len(runs), 5)  # the last line is empty
        self.assertEqual(runs[0][0][2], "preproc")
        self.assertEqual(runs[1], [(0, 6, "comment")])  # a comment over two lines: both coloured
        self.assertEqual(runs[2], [(0, 11, "comment")])
        line = 'int main(void) { return 0x1F; }'
        cats = {line[s:s + n]: c for s, n, c in runs[3]}
        self.assertEqual((cats["int"], cats["main"], cats["return"], cats["0x1F"]),
                         ("type", "function", "keyword", "number"))

    def test_python(self):
        runs = self.runs("a.py", '@cache\ndef f(x):\n    """Doc."""\n    return "s" + 1  # note\n')
        line3 = '    return "s" + 1  # note'
        cats = {line3[s:s + n]: c for s, n, c in runs[3]}
        self.assertEqual((cats["return"], cats['"s"'], cats["1"], cats["# note"]),
                         ("keyword", "string", "number", "comment"))
        self.assertEqual(runs[0][0][2], "decorator")
        self.assertEqual({c for _, _, c in runs[2]}, {"comment"})  # a docstring reads as a comment

    def test_utf16_positions(self):
        # Qt counts characters outside the BMP (emoji) as two; the runs after one move by one
        runs = self.runs("a.py", 'x = "😀"  # c\n')
        line = 'x = "😀"  # c'
        comment = [r for r in runs[0] if r[2] == "comment"][0]
        self.assertEqual(comment[0], line.index("#") + 1)
        string = [r for r in runs[0] if r[2] == "string"]
        self.assertEqual(sum(n for _, n, _ in string), 4)  # "😀" = 3 characters, 4 UTF-16 units

    def test_stop(self):
        stop = threading.Event()
        stop.set()
        self.assertIsNone(syntax.spans("x = 1\n" * 10, syntax.lexer_for("a.py"), stop))

    def test_palettes(self):
        for colors in (syntax.LIGHT, syntax.DARK):
            self.assertEqual(set(colors), set(syntax.CATEGORIES))
        saved, syntax.THEME = syntax.THEME, {"keyword": "#000000"}
        try:
            self.assertEqual(syntax.palette(True), {"keyword": "#000000"})
        finally:
            syntax.THEME = saved
        self.assertIs(syntax.palette(False), syntax.LIGHT)


if __name__ == "__main__":
    unittest.main()

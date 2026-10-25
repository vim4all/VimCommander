import os
import tempfile
import unittest

from vimcommander import mdrender


def body(text: str, **kw) -> str:
    return mdrender.render(text, **kw).html


class BlockTest(unittest.TestCase):
    def test_front_matter_title_block(self):
        page = mdrender.render('---\ntitle: "Spec"\nversion: "1.2"\ndate: 2026-06-28\ntags: [a, b]\n---\nText')
        self.assertEqual(page.title, "Spec")
        self.assertIn(">Spec</h1>", page.html)
        self.assertIn("Version 1.2 · 2026-06-28", page.html)
        self.assertIn(">a, b</td>", page.html)

    def test_headings_get_anchors(self):
        out = body("# Hello, World\n## Part {#custom}\nText\n---")
        self.assertIn('<a name="hello-world"></a>', out)
        self.assertIn('<a name="custom"></a>', out)
        self.assertIn(">Part</h2>", out)  # setext "---" under a paragraph is a heading too
        self.assertIn(">Text</h2>", out)

    def test_md2doc_callouts(self):
        out = body("> **CRITICAL — never commoned.** Pins live.\n\n> plain quote")
        self.assertIn('bgcolor="#fdecec" style="border-left:4px solid #c62828"', out)
        self.assertIn('<b style="color:#c62828">CRITICAL — never commoned.</b>', out)
        self.assertIn('bgcolor="#f2f6fb"', out)  # a plain quote keeps the navy style

    def test_alert_callouts(self):
        out = body("> [!WARNING]\n> Hot.")
        self.assertIn("#ef6c00", out)
        self.assertIn(">Warning</b>", out)
        self.assertNotIn("[!WARNING]", out)

    def test_table_shading_and_alignment(self):
        out = body("| A | B |\n|---|--:|\n| 1<!--bg:best--> | <span class=\"bg-bad\">2</span> |\n"
                   "| `a|b` | <!--bg:#BBDEFB-->x |\n| y | z |")
        self.assertIn('bgcolor="#c8e6c9"', out)
        self.assertIn('bgcolor="#ffcdd2"', out)
        self.assertIn('bgcolor="#bbdefb"', out)
        self.assertIn('align="right"', out)
        self.assertIn("a|b</code>", out)  # a pipe in code doesn't split the cell
        self.assertNotIn("bg:", out)
        self.assertEqual(out.count('bgcolor="#eef2f7"'), 1)  # the second body row is striped, x shaded

    def test_lists(self):
        out = body("1. one\n2. two\n   - a\n   - b\n3. three\n\n- [x] done\n- [ ] todo")
        self.assertEqual(out.count("<li>"), 7)
        self.assertIn("<ol><li>one</li><li>two\n<ul><li>a</li><li>b</li></ul></li><li>three</li></ol>", out)
        self.assertIn("☑ done", out)
        self.assertIn("☐ todo", out)

    def test_code_is_escaped_and_untouched(self):
        out = body("```python\nif a < b and [[x]]:\n    **y**\n```")
        self.assertIn("if a &lt; b and [[x]]:\n    **y**</pre>", out)

    def test_footnotes(self):
        out = body("Claim.[^n]\n\n[^n]: Source.")
        self.assertIn('<sup><a href="#fn-n">1</a></sup>', out)
        self.assertIn('<a name="fn-n"></a>Source.', out)

    def test_html_comments_hidden(self):
        self.assertNotIn("secret", body("<!-- secret\nmore -->\nText"))


class InlineTest(unittest.TestCase):
    def test_emphasis(self):
        out = body("**b** *i* ~~s~~ ==m== H~2~O x^2^ snake_case_name")
        for part in ("<b>b</b>", "<i>i</i>", "<s>s</s>", ">m</span>", "H<sub>2</sub>O", "x<sup>2</sup>",
                     "snake_case_name"):
            self.assertIn(part, out)

    def test_links(self):
        out = body("[a](other.md) [[Note|alias]] <https://x.org> www.y.com [r][1]\n\n[1]: https://r.org")
        self.assertIn('<a href="other.md">a</a>', out)
        self.assertIn('<a href="wikilink:Note">alias</a>', out)
        self.assertIn('<a href="https://x.org">https://x.org</a>', out)
        self.assertIn('<a href="https://www.y.com">www.y.com</a>', out)
        self.assertIn('<a href="https://r.org">r</a>', out)

    def test_math(self):
        self.assertIn("V<sub>out</sub> = R<sub>2</sub>/(R<sub>1</sub>+R<sub>2</sub>) · 3\u2009Ω",
                      body(r"$V_{out} = \frac{R_2}{R_1+R_2} \cdot 3\,\Omega$"))
        self.assertNotIn("<i>", body("costs $5 and $10"))  # dollars, not math

    def test_escapes(self):
        out = body(r"\*not\* <b>bold</b> a < b")
        self.assertIn("*not*", out)
        self.assertIn("<b>bold</b>", out)
        self.assertIn("a &lt; b", out)


class DiagramTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_djb2_matches_lua(self):
        self.assertEqual(mdrender.djb2(""), "00001505")
        self.assertEqual(mdrender.djb2("a"), "0002b606")

    def test_md2doc_plot_found_and_checked(self):
        code = "import matplotlib.pyplot as plt\nplt.plot([1])"
        os.makedirs(os.path.join(self.tmp.name, "plots"))
        png = os.path.join(self.tmp.name, "plots", "ripple.png")
        with open(png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\x05\0\0\0\x02\x58")
        self.assertEqual(mdrender.md2doc_image("matplotlib", "ripple", code, self.tmp.name), (png, False))
        with open(os.path.join(self.tmp.name, "plots", "ripple.hash"), "w") as f:
            f.write(mdrender.djb2(code))
        self.assertEqual(mdrender.md2doc_image("matplotlib", "ripple", code, self.tmp.name), (png, True))
        self.assertEqual(mdrender.png_size(png), (1280, 600))

        def finder(kind, ident, text):
            return mdrender.md2doc_image(kind, ident, text, self.tmp.name)[0], ""
        page = mdrender.render('```{.matplotlib #ripple caption="Ripple **now**."}\n' + code + "\n```",
                               diagram=finder, max_width=500)
        self.assertIn('width="500"', page.html)  # 1280 / 2, capped
        self.assertIn("Figure 1: Ripple <b>now</b>.", page.html)
        self.assertEqual(page.missing, [])

    def test_missing_diagram_shows_code(self):
        page = mdrender.render("```mermaid\nflowchart LR\n  A --> B\n```")
        self.assertEqual(page.missing, [("mermaid", "", "flowchart LR\n  A --> B")])
        self.assertIn("A --&gt; B</pre>", page.html)

    def test_dark_style(self):
        st = mdrender.style(True, "#1e1e2e", "#cdd6f4")
        _, tint = st.callout("CRITICAL")
        self.assertNotEqual(tint, "#fdecec")
        self.assertIn("color:#cdd6f4", body("x", st=st))


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest

from vimcommander import mdnotes


class PrepareTest(unittest.TestCase):
    def test_wikilinks(self):
        out = mdnotes.prepare("See [[Some note]] and [[folder/x#Head|the x]].")
        self.assertIn("[Some note](wikilink:Some%20note)", out)
        self.assertIn("[the x](wikilink:folder/x%23Head)", out)

    def test_embed(self):
        self.assertEqual(mdnotes.prepare("![[pic.png|300]]"), "![pic.png](wikilink:pic.png)")

    def test_code_blocks_untouched(self):
        text = "```\n[[not a link]]\n```\n[[link]]"
        out = mdnotes.prepare(text).splitlines()
        self.assertEqual(out[1], "[[not a link]]")
        self.assertEqual(out[3], "[link](wikilink:link)")

    def test_front_matter(self):
        out = mdnotes.prepare("---\ntags: [a]\n---\n# Title").splitlines()
        self.assertEqual(out[:3], ["```yaml", "tags: [a]", "```"])
        self.assertEqual(out[-1], "# Title")


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, ".obsidian"))
        os.makedirs(os.path.join(root, "10_A", "deep"))
        os.makedirs(os.path.join(root, "20_B"))
        self.note = os.path.join(root, "10_A", "note.md")
        self.other = os.path.join(root, "20_B", "Other Note.md")
        self.deep = os.path.join(root, "10_A", "deep", "img.png")
        for path in (self.note, self.other, self.deep):
            open(path, "w").close()
        mdnotes.forget()

    def tearDown(self):
        self.tmp.cleanup()

    def test_vault_root(self):
        self.assertEqual(mdnotes.vault_root(self.note), self.tmp.name)

    def test_by_name_anywhere(self):
        self.assertEqual(mdnotes.resolve("other note", self.note), self.other)
        self.assertEqual(mdnotes.resolve("Other%20Note#Heading", self.note), self.other)
        self.assertEqual(mdnotes.resolve("img.png", self.note), self.deep)

    def test_path_from_root_and_relative(self):
        self.assertEqual(mdnotes.resolve("20_B/Other Note", self.note), self.other)
        self.assertEqual(mdnotes.resolve("deep/img.png", self.note), self.deep)

    def test_missing(self):
        self.assertIsNone(mdnotes.resolve("nope", self.note))


if __name__ == "__main__":
    unittest.main()

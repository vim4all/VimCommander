import os
import tempfile
import unittest
import zipfile

from vimcommander import grep, textdiff
from vimcommander.fileops import Job
from vimcommander.results import find_paths


def write(path, data="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


class FindTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        r = self.root = self.dir.name
        write(os.path.join(r, "a", "notes.txt"), "hello world\nTODO: fix\n")
        write(os.path.join(r, "a", "b", "Deep.TXT"), "another todo here\n")
        write(os.path.join(r, "c", "readme.md"), "x " * 500 + "TODO" + " y" * 500 + "\n")
        write(os.path.join(r, "c", "image.bin"), b"\0\0TODO\0")
        write(os.path.join(r, ".git", "config.txt"), "TODO")
        write(os.path.join(r, ".hidden.txt"), "TODO")

    def tearDown(self):
        self.dir.cleanup()

    def rel(self, paths):
        return sorted(os.path.relpath(p, self.root).replace(os.sep, "/") for p in paths)

    def test_find_paths(self):
        found = [p for batch in find_paths(self.root, "*.txt") for p in batch]
        self.assertEqual(self.rel(found), ["a/b/Deep.TXT", "a/notes.txt"])
        found = [p for batch in find_paths(self.root, "read") for p in batch]
        self.assertEqual(self.rel(found), ["c/readme.md"])
        found = [p for batch in find_paths(self.root, "*.txt", show_hidden=True) for p in batch]
        self.assertEqual(self.rel(found), [".hidden.txt", "a/b/Deep.TXT", "a/notes.txt"])  # not .git

    def test_grep(self):
        regex = grep.compile_pattern("todo")  # smartcase: lower case ignores case
        hits = {os.path.relpath(fm.path, self.root).replace(os.sep, "/"): fm.matches
                for fm in grep.grep(self.root, regex)}
        self.assertEqual(sorted(hits), ["a/b/Deep.TXT", "a/notes.txt", "c/readme.md"])
        m = hits["a/notes.txt"][0]
        self.assertEqual((m.line, m.text[slice(*m.spans[0])]), (2, "TODO"))
        long = hits["c/readme.md"][0]  # a long line is cut around the match
        self.assertLessEqual(len(long.text), grep.LINE_LIMIT + 2)
        self.assertEqual(long.text[slice(*long.spans[0])], "TODO")
        self.assertEqual(len(list(grep.grep(self.root, grep.compile_pattern("TODO")))), 2)  # case
        only_md = list(grep.grep(self.root, regex, "*.md"))
        self.assertEqual(len(only_md), 1)
        not_md = list(grep.grep(self.root, regex, "!*.md"))
        self.assertEqual(len(not_md), 2)
        self.assertEqual(grep.parse_globs("*.py, !test_* *.MD"), (["*.py", "*.md"], ["test_*"]))
        self.assertEqual(grep.compile_pattern("a(").pattern, r"a\(")  # bad regex: literal

    def test_zip_job(self):
        dest = os.path.join(self.root, "out.zip")
        job = Job("zip", [os.path.join(self.root, "a"), os.path.join(self.root, "c", "readme.md")], dest)
        for src in job.sources:
            job._zip(src)
        with zipfile.ZipFile(dest) as z:
            self.assertEqual(sorted(z.namelist()), ["a/b/Deep.TXT", "a/notes.txt", "readme.md"])
        self.assertEqual(job.undo, [("created", dest)])

    def test_hash_and_verify(self):
        from vimcommander.fileops import parse_checksums
        sums = os.path.join(self.root, "a.sha256")
        job = Job("hash", [os.path.join(self.root, "a")], sums)
        job._run()
        names = [n for _, n in parse_checksums(sums)]
        self.assertEqual(names, ["a/b/Deep.TXT", "a/notes.txt"])
        job = Job("verify", [sums])
        job._run()
        self.assertEqual(job.bad, [])
        with open(os.path.join(self.root, "a", "notes.txt"), "a") as f:
            f.write("changed")
        os.remove(os.path.join(self.root, "a", "b", "Deep.TXT"))
        job = Job("verify", [sums])
        job._run()
        self.assertEqual(job.bad, ["a/b/Deep.TXT: missing", "a/notes.txt: differs"])

    def test_branch_paths(self):
        from vimcommander.results import branch_paths
        found = [p for batch in branch_paths(self.root) for p in batch]
        self.assertTrue(found and all(os.path.isfile(p) for p in found))
        self.assertIn(os.path.join(self.root, "a", "b", "Deep.TXT"), found)

    def test_zip_job_stored(self):
        dest = os.path.join(self.root, "stored.zip")
        job = Job("zip", [os.path.join(self.root, "a")], dest)
        job.zip_level = 0
        job._zip(job.sources[0])
        with zipfile.ZipFile(dest) as z:
            self.assertEqual({i.compress_type for i in z.infolist()}, {zipfile.ZIP_STORED})


class DiffTest(unittest.TestCase):
    def test_align(self):
        a = ["one", "two", "three", "four", "gone"]
        b = ["one", "2", "three", "four", "five", "six"]
        rows = textdiff.align(a, b)
        self.assertEqual([(r.kind, r.left_no, r.right_no) for r in rows], [
            ("same", 1, 1), ("change", 2, 2), ("same", 3, 3), ("same", 4, 4),
            ("change", 5, 5), ("insert", None, 6)])
        self.assertEqual(textdiff.hunks(rows), [1, 4])
        self.assertEqual(textdiff.summary(rows), (2, 0, 1))
        rows = textdiff.align(["a  b", "x"], ["a b", "x", "y"], ignore_space=True)
        self.assertEqual([r.kind for r in rows], ["same", "same", "insert"])
        rows = textdiff.align(["a", "b", "c"], ["a", "c"])
        self.assertEqual([(r.kind, r.left_no, r.right_no) for r in rows],
                         [("same", 1, 1), ("delete", 2, None), ("same", 3, 2)])

    def test_changed_spans(self):
        left, right = textdiff.changed_spans("value = 10", "value = 12")
        self.assertEqual((left, right), ([(9, 10)], [(9, 10)]))
        self.assertEqual(textdiff.changed_spans("abc", "xyz"), ([], []))

import os
import shutil
import subprocess
import tempfile
import time
import unittest

from vimcommander import cleanup, gitstatus, rules


def write(path, data="x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    if mtime:
        os.utime(path, (mtime, mtime))


class SpaceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name

    def tearDown(self):
        self.dir.cleanup()

    def p(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def test_parse_size(self):
        self.assertEqual(cleanup.parse_size("100M"), 100 * 1024 ** 2)
        self.assertEqual(cleanup.parse_size("1.5k"), 1536)
        self.assertEqual(cleanup.parse_size("2GB"), 2 * 1024 ** 3)
        self.assertEqual(cleanup.parse_size("4096"), 4096)
        with self.assertRaises(ValueError):
            cleanup.parse_size("lots")

    def test_reclaim(self):
        old = time.time() - 400 * 86400
        write(self.p("app/package.json"), "{}")
        write(self.p("app/node_modules/x/index.js"), "y" * 5000)
        write(self.p("app/build/out.js"), "z" * 100)
        write(self.p("photos/build/keep.jpg"), "k")  # 'build' without a project file: not a cache
        write(self.p("lib/__pycache__/m.pyc"), "c" * 10)
        write(self.p("big/old.iso"), b"\0" * 3000, mtime=old)
        write(self.p("big/new.iso"), b"\0" * 3000)
        found = [item for batch in cleanup.reclaim(self.root, big=2000) for item in batch]
        rel = [os.path.relpath(p, self.root).replace(os.sep, "/") for p, _ in found]
        self.assertEqual(rel, ["app/node_modules", "big/old.iso", "app/build", "lib/__pycache__"])
        self.assertIn("npm packages", found[0][1])

    def test_duplicates(self):
        write(self.p("a/one.txt"), "same content")
        write(self.p("b/two.txt"), "same content")
        write(self.p("c/three.txt"), "same content")
        write(self.p("a/other.txt"), "different!!!")  # same size, other content
        big = os.urandom(200_000)
        write(self.p("big1.bin"), big)
        write(self.p("big2.bin"), big)
        write(self.p("big3.bin"), big[:-1] + bytes([big[-1] ^ 1]))  # same start, different end
        sets = cleanup.duplicates(self.root)
        rel = [[os.path.relpath(p, self.root).replace(os.sep, "/") for p in g] for g in sets]
        self.assertEqual(rel, [["big1.bin", "big2.bin"], ["a/one.txt", "b/two.txt", "c/three.txt"]])
        rows = [r for batch in cleanup.dupes(self.root) for r in batch]
        groups = {p: n for p, _, n in rows}
        os.utime(self.p("b/two.txt"), (time.time() + 100, time.time() + 100))
        extra = cleanup.keep_one(groups)
        self.assertNotIn(self.p("b/two.txt"), extra)
        self.assertEqual(len(extra), 3)  # one of the 2 big files, two of the 3 texts

    def test_rules(self):
        old = time.time() - 10 * 86400
        dl = self.p("Downloads")
        write(os.path.join(dl, "a.pdf"), "x", mtime=old)
        write(os.path.join(dl, "b.PDF"), "x")  # too new
        write(os.path.join(dl, "c.tmp"), "x")
        write(os.path.join(dl, "sub", "d.pdf"), "x", mtime=old)
        items = [
            {"name": "pdf", "folder": dl, "match": "*.pdf", "older_than_days": 7, "action": "move",
             "to": self.p("Docs/PDF")},
            {"folder": dl, "match": ["*.tmp", "*.pdf"], "action": "trash"},
            {"folder": dl, "action": "explode"},
            {"folder": dl, "action": "copy"},
        ]
        parsed, errors = rules.parse_rules(items)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(len(errors), 2)
        steps = rules.plan(parsed)
        got = sorted((os.path.basename(s.path), s.action, s.rule) for s in steps)
        self.assertEqual(got, [("a.pdf", "move", "pdf"), ("b.PDF", "trash", "rule 2"),
                               ("c.tmp", "trash", "rule 2")])


class GitTest(unittest.TestCase):
    def test_parse(self):
        out = ("## main...origin/main [ahead 2, behind 1]\0 M src/app.py\0?? new.txt\0"
               "R  src/b.py\0src/a.py\0!! build/\0UU conflict.txt\0A  added.py\0")
        branch, files = gitstatus.parse_status(out)
        self.assertEqual(branch, "main ↑2 ↓1")
        self.assertEqual(files, {"src/app.py": "modified", "new.txt": "new", "src/b.py": "modified",
                                 "build/": "ignored", "conflict.txt": "conflict", "added.py": "new"})
        self.assertEqual(gitstatus.parse_branch("No commits yet on dev"), "dev")
        repo = os.path.abspath("repo")
        kinds = gitstatus.folder_kinds(repo, files, repo)
        self.assertEqual(kinds, {"src": "modified", "new.txt": "new", "build": "ignored",
                                 "conflict.txt": "conflict", "added.py": "new"})
        self.assertEqual(gitstatus.folder_kinds(repo, files, os.path.join(repo, "src")),
                         {"app.py": "modified", "b.py": "modified"})
        self.assertEqual(gitstatus.folder_kinds(repo, files, os.path.join(repo, "build", "x")),
                         {"*": "ignored"})

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_real_repo(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                subprocess.run(["git", "-C", root, *args], check=True, capture_output=True)
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            write(os.path.join(root, "a.txt"), "one\n")
            write(os.path.join(root, ".gitignore"), "*.log\n")
            git("add", ".")
            git("commit", "-q", "-m", "first")
            write(os.path.join(root, "a.txt"), "two\n")
            write(os.path.join(root, "b.txt"), "new\n")
            write(os.path.join(root, "x.log"), "log\n")
            repo, branch, files = gitstatus.status(shutil.which("git"), root)
            self.assertEqual(branch, "main")
            self.assertEqual(gitstatus.folder_kinds(repo, files, root),
                             {"a.txt": "modified", "b.txt": "new", "x.log": "ignored"})
            self.assertEqual(gitstatus.head_version(shutil.which("git"), repo, os.path.join(root, "a.txt")),
                             b"one\n")

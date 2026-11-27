import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None


def requirement_lines() -> list[str]:
    """requirements.txt without comments, as pyproject.toml writes them."""
    lines = []
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if line:
                lines.append(re.sub(r"\s*;\s*", "; ", line))
    return lines


@unittest.skipIf(tomllib is None, "tomllib needs Python 3.11")
class PackagingTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            self.project = tomllib.load(f)["project"]

    def test_dependencies_match_requirements(self):
        self.assertEqual(self.project["dependencies"], requirement_lines())

    def test_entry_point(self):
        from vimcommander import app
        self.assertEqual(self.project["gui-scripts"]["vimcommander"], "vimcommander.app:main")
        self.assertTrue(callable(app.main))


class VersionTest(unittest.TestCase):
    def test_version(self):
        import vimcommander
        self.assertRegex(vimcommander.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()

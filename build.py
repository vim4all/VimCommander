r"""Build a standalone VimCommander (no console window) into release/VimCommander/.

Use the project's virtual environment, not the system Python:

    Windows exe, from WSL:   .venv-win/Scripts/python.exe build.py
    Windows exe, from cmd:   .venv-win\Scripts\python build.py
    Linux / macOS:           .venv/bin/pip install -r requirements-build.txt
                             .venv/bin/python build.py

(.venv-win is created with: py -m venv .venv-win, then
 .venv-win\Scripts\pip install -r requirements-build.txt)

PyInstaller builds for the platform it runs on: run it with a Windows Python
to get VimCommander.exe, with a Linux or macOS Python for those systems.
A config.json is put next to the executable, so the build is portable: its
settings live in that folder. An existing config.json there is kept.
"""

import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "VimCommander"
RELEASE = os.path.join(ROOT, "release")
OUT = os.path.join(RELEASE, NAME)
WORK = os.path.join(ROOT, "build")


def make_icon(path: str) -> bool:
    """Draw a simple app icon with Qt. Returns False if the format isn't supported."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QGuiApplication.instance() or QGuiApplication([])  # text drawing needs one
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2e7d32"))
    p.drawRoundedRect(8, 8, 240, 240, 40, 40)
    p.setBrush(QColor("#ffffff"))
    p.drawRect(124, 40, 8, 176)  # the two panes
    font = QFont()
    font.setBold(True)
    font.setPixelSize(150)
    p.setFont(font)
    p.setPen(QColor("#ffffff"))
    p.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "V")
    p.end()
    return image.save(path)


def main() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(f"PyInstaller is missing: {sys.executable} -m pip install -r requirements-build.txt"
                 "\n(run build.py with the project's venv Python, not the system one)")

    sys.path.insert(0, ROOT)

    exe = os.path.join(OUT, NAME + (".exe" if sys.platform == "win32" else ""))
    if sys.platform == "win32" and os.path.exists(exe):
        try:
            open(exe, "r+b").close()  # fails while the program is running
        except PermissionError:
            sys.exit(f"{NAME} is running from {OUT}. Close it and build again.")

    # PyInstaller deletes the output folder first, so keep the settings safe
    # outside it and put them back even if the build fails.
    kept = None
    config_file = os.path.join(OUT, "config.json")
    os.makedirs(WORK, exist_ok=True)
    backup = os.path.join(WORK, "config.backup.json")
    if os.path.exists(config_file):
        shutil.copy2(config_file, backup)
        with open(config_file, encoding="utf-8") as f:
            kept = f.read()
    try:
        build(kept is not None)
    finally:
        if kept is not None:
            os.makedirs(OUT, exist_ok=True)
            with open(config_file, "w", encoding="utf-8") as f:
                f.write(kept)
    print(f"\nBuilt {OUT}")


def build(have_config: bool) -> None:
    from vimcommander import config

    config_file = os.path.join(OUT, "config.json")

    os.makedirs(WORK, exist_ok=True)
    ext = ".ico" if sys.platform == "win32" else ".icns" if sys.platform == "darwin" else ".png"
    icon = os.path.join(WORK, "icon" + ext)
    icon_args = ["--icon", icon] if make_icon(icon) and ext != ".png" else []

    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--windowed",  # no console window
        "--name", NAME,
        "--distpath", RELEASE,
        "--workpath", os.path.join(WORK, "pyinstaller"),
        "--specpath", WORK,
        *icon_args,
        os.path.join(ROOT, "main.py"),
    ], check=True)

    if not have_config:
        # The launch bar isn't included: it is filled in for the machine on first run.
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config.with_defaults({}), f, indent=2)
            f.write("\n")
    shutil.copy(os.path.join(ROOT, "README.md"), os.path.join(OUT, "README.md"))


if __name__ == "__main__":
    main()

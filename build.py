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
settings live in that folder, with undo.json and the vimcommanderrc. Rebuilding
keeps every file there that the build doesn't make (copies stay in build/kept/).
If config.json is gone but build/kept/ has one, the build stops rather than
start from defaults; --fresh-config builds anyway.
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
KEPT = os.path.join(WORK, "kept")  # copies of the user's files in OUT, from the last build
BUILT = {NAME, NAME + ".exe", "_internal", "README.md", "LICENSE.txt"}  # what a build puts in OUT


def make_icon(path: str) -> bool:
    """Draw the app icon with Qt. Returns False if the format isn't supported."""
    from PySide6.QtGui import QGuiApplication

    from vimcommander import appicon

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QGuiApplication.instance() or QGuiApplication([])  # text drawing needs one
    if path.endswith(".ico"):
        # Qt writes a single 256 px BMP entry, which Explorer shows as blank.
        appicon.save_ico(path)
        return True
    return appicon.image().save(path)


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

    # PyInstaller deletes the output folder first, so the user's files in it
    # (settings, undo history, vimcommanderrc…) are copied to build/kept/ and put
    # back even if the build fails.
    names = user_files()
    if ("config.json" not in names and os.path.exists(os.path.join(KEPT, "config.json"))
            and "--fresh-config" not in sys.argv):
        sys.exit(f"{os.path.join(OUT, 'config.json')} is missing, but the last build kept one in {KEPT}.\n"
                 f"Copy it back (with the other files there) and build again, or run with --fresh-config\n"
                 f"to start from the default settings.")
    keep_user_files(names)
    try:
        build("config.json" in names)
    finally:
        restore_user_files()
    print(f"\nBuilt {OUT}" + (f"\nKept {', '.join(sorted(names))} (copies in {KEPT})" if names else ""))


def user_files() -> list[str]:
    """What is in the output folder that a build doesn't make: config.json,
    undo.json, vimcommanderrc and anything else the user put there."""
    if not os.path.isdir(OUT):
        return []
    return [name for name in os.listdir(OUT) if name not in BUILT]


def keep_user_files(names: list[str]) -> None:
    """Copy them to KEPT, replacing the previous build's copies only once all are copied."""
    fresh = KEPT + ".new"
    shutil.rmtree(fresh, ignore_errors=True)
    os.makedirs(fresh)
    for name in names:
        src = os.path.join(OUT, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(fresh, name))
        else:
            shutil.copy2(src, os.path.join(fresh, name))
    shutil.rmtree(KEPT, ignore_errors=True)
    os.replace(fresh, KEPT)


def restore_user_files() -> None:
    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(KEPT):
        src, dest = os.path.join(KEPT, name), os.path.join(OUT, name)
        if os.path.isdir(src):
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)


def winpty_programs() -> list[str]:
    """PyInstaller arguments adding the programs pywinpty starts from its own folder:
    OpenConsole.exe hosts the terminal pane's console (with the conpty.dll next to
    it, the shell ends at once without it), winpty-agent.exe the fallback for old
    Windows. PyInstaller collects a package's DLLs, but not its .exe files."""
    if sys.platform != "win32":
        return []
    try:
        import winpty
    except ImportError:
        return []
    folder = os.path.dirname(winpty.__file__)
    args = []
    for name in ("OpenConsole.exe", "winpty-agent.exe"):
        if os.path.exists(os.path.join(folder, name)):
            args += ["--add-binary", os.path.join(folder, name) + os.pathsep + "winpty"]
    return args


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
        *winpty_programs(),
        os.path.join(ROOT, "main.py"),
    ], check=True)

    if not have_config:
        # The launch bar isn't included: it is filled in for the machine on first run.
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config.with_defaults({}), f, indent=2)
            f.write("\n")
    shutil.copy(os.path.join(ROOT, "README.md"), os.path.join(OUT, "README.md"))
    shutil.copy(os.path.join(ROOT, "LICENSE"), os.path.join(OUT, "LICENSE.txt"))


if __name__ == "__main__":
    main()

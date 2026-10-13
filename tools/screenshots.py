"""Renders the README screenshots into docs/ from a made-up demo home folder.

    .venv/bin/python tools/screenshots.py

Linux (or WSL) with git installed; it runs headless (QT_QPA_PLATFORM=offscreen)
and builds the demo folder in build/demo, with HOME pointing there.
"""
import math
import os
import random
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
DEMO = os.path.join(ROOT, "build", "demo")
HOME = os.path.join(DEMO, "home")
shutil.rmtree(DEMO, ignore_errors=True)
os.makedirs(os.path.join(DEMO, "xdg"))
os.environ.update({"HOME": HOME, "QT_QPA_PLATFORM": "offscreen", "XDG_DATA_HOME": os.path.join(DEMO, "xdg"),
                   "VIMCOMMANDER_CONFIG": os.path.join(DEMO, "config.json"), "_ZO_DATA_DIR": os.path.join(DEMO, "zo"),
                   "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
                   "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "demo@example.com"})
sys.path.insert(0, ROOT)
random.seed(7)
NOW = time.time()


def write(rel, text="", size=0, days_ago=None):
    p = os.path.join(HOME, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
        if size > len(text):
            f.write("x" * (size - len(text)))
    if days_ago is not None:
        t = NOW - days_ago * 86400 - random.randint(0, 80000)
        os.utime(p, (t, t))
    return p


# A small website project in git
site = "projects/website"
write(f"{site}/package.json", '{\n  "name": "website",\n  "scripts": {"dev": "vite"}\n}\n')
write(f"{site}/README.md", "# Website\n\nTODO: deploy script\n")
write(f"{site}/index.html", "<!doctype html>\n<script type=module src=src/main.ts></script>\n")
write(f"{site}/src/main.ts", "import { render } from './render'\nimport { router } from './router'\n// TODO: lazy routes\nrender(router())\n")
write(f"{site}/src/render.ts", "import { h } from './dom'\nexport const render = (x) => h(x)\n")
write(f"{site}/src/router.ts", "import { pages } from './pages'\nimport { h } from './dom'\nexport const router = () => pages\n")
write(f"{site}/src/dom.ts", "export const h = (x) => x // TODO: diffing\n")
write(f"{site}/src/pages/index.ts", "import { about } from './about'\nimport { blog } from './blog'\nexport const pages = [about, blog]\n")
write(f"{site}/src/pages/about.ts", "import { h } from '../dom'\nexport const about = h('about')\n")
write(f"{site}/src/pages/blog.ts", "import { h } from '../dom'\nexport const blog = h('blog')\n")
write(f"{site}/styles/main.css", "body {\n  margin: 0;\n  font-family: system-ui;\n}\n", size=4000)
write(f"{site}/styles/theme.css", ":root { --accent: #2f6fd0; }\n", size=2500)
for i in range(6):
    write(f"{site}/public/img/hero-{i}.jpg", "", size=180_000 + i * 40_000)
write(f"{site}/node_modules/vite/index.js", "", size=900_000)
write(f"{site}/node_modules/typescript/lib.js", "", size=1_400_000)
write(f"{site}/.gitignore", "node_modules/\ndist/\n")
git = ["git", "-C", os.path.join(HOME, site)]
subprocess.run(git + ["init", "-q", "-b", "main"], check=True)
subprocess.run(git + ["add", "-A"], check=True)
subprocess.run(git + ["commit", "-q", "-m", "first version"], check=True)
# a backup of the website, slightly behind, for mirror
shutil.copytree(os.path.join(HOME, site), os.path.join(HOME, "backup", "website"),
                ignore=shutil.ignore_patterns(".git", "node_modules", "analytics.ts"))
write("backup/website/old-notes.txt", "remove me")
write(f"{site}/src/main.ts", "import { render } from './render'\nimport { router } from './router'\n"
      "import { analytics } from './analytics'\n// TODO: lazy routes\nrender(router())\nanalytics()\n")
write(f"{site}/src/analytics.ts", "export const analytics = () => {}\n")
write(f"{site}/styles/main.css", "body {\n  margin: 0;\n  font-family: Inter, system-ui;\n  color: #222;\n}\n", size=4200)

# Photos (generated pictures), documents, notes, music, a backup copy
from PySide6.QtCore import QEventLoop, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import QApplication

app = QApplication([])
app.setStyle("Fusion")
font = QFont("Inter", 10)
app.setFont(font)


def picture(path, seed):
    rnd = random.Random(seed)
    img = QImage(1200, 800, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    sky = QLinearGradient(0, 0, 0, 800)
    hue = rnd.random()
    sky.setColorAt(0, QColor.fromHsvF(hue, 0.55, 0.95))
    sky.setColorAt(1, QColor.fromHsvF((hue + 0.12) % 1, 0.35, 1.0))
    p.fillRect(img.rect(), sky)
    sun = QRadialGradient(QPointF(rnd.randint(200, 1000), rnd.randint(120, 300)), 140)
    sun.setColorAt(0, QColor(255, 250, 220))
    sun.setColorAt(1, QColor(255, 250, 220, 0))
    p.setBrush(sun)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(0, 0, 1200, 800))
    for layer in range(3):
        color = QColor.fromHsvF((hue + 0.45 + layer * 0.05) % 1, 0.45, 0.55 - layer * 0.12)
        p.setBrush(color)
        pts = [QPointF(0, 800)]
        base = 430 + layer * 110
        for x in range(0, 1260, 60):
            pts.append(QPointF(x, base - 120 * math.sin(x / (180 + layer * 60) + seed) - rnd.randint(0, 40)))
        pts.append(QPointF(1200, 800))
        p.drawPolygon(pts)
    p.end()
    img.save(path, quality=85)


os.makedirs(os.path.join(HOME, "Photos", "2025 Carpathians"), exist_ok=True)
for i in range(14):
    picture(os.path.join(HOME, "Photos", "2025 Carpathians", f"IMG_{2041 + i}.jpg"), i)
for name, seed in (("Lviv", 30), ("Odesa", 31), ("Kyiv", 32)):
    os.makedirs(os.path.join(HOME, "Photos", name), exist_ok=True)
    for i in range(3):
        picture(os.path.join(HOME, "Photos", name, f"photo_{i + 1}.jpg"), seed + i * 5)
for i in range(30):
    write(f"Documents/reports/report-{2024 + i // 12}-{i % 12 + 1:02d}.pdf", "%PDF", size=60_000 + i * 9000,
          days_ago=random.randint(0, 360))
write("Documents/thesis/thesis.tex", "\\documentclass{article}\n", size=120_000, days_ago=3)
write("Documents/thesis/figures/fig1.png", "", size=300_000, days_ago=40)
write("Documents/budget.xlsx", "", size=48_000, days_ago=1)
for name, links in (("Index", ["Projects", "Reading", "Ideas"]), ("Projects", ["Website", "Thesis", "Index"]),
                    ("Website", ["Ideas"]), ("Thesis", ["Reading"]), ("Reading", ["Books 2025"]),
                    ("Ideas", ["Website", "Thesis"]), ("Books 2025", []), ("Journal", ["Index"])):
    write(f"Notes/{name}.md", f"# {name}\n" + "".join(f"- [[{l}]]\n" for l in links), days_ago=random.randint(0, 60))
for i in range(12):
    write(f"Music/Album {i // 4 + 1}/track{i % 4 + 1:02d}.flac", "", size=700_000 + i * 50_000,
          days_ago=random.randint(100, 300))
write("Downloads/setup.msi", "", size=2_500_000, days_ago=20)
write("Downloads/paper.pdf", "%PDF", size=400_000, days_ago=2)
# activity over the year
for i in range(220):
    write(f"Documents/log/entry-{i:03d}.txt", "x", days_ago=int(abs(random.gauss(60, 70))) % 365)
# a copy of VimCommander's own source, for the code graph
shutil.copytree(os.path.join(ROOT, "vimcommander"), os.path.join(HOME, "projects", "vimcommander", "vimcommander"),
                ignore=shutil.ignore_patterns("__pycache__"))
shutil.copy(os.path.join(ROOT, "main.py"), os.path.join(HOME, "projects", "vimcommander"))

from vimcommander import app as A, config, graphview


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(cond, ms=15000):
    end = time.time() + ms / 1000
    while time.time() < end and not cond():
        wait(30)


def save(widget, name):
    widget.grab().save(os.path.join(DOCS, name))
    print("saved", name)


settings = config.with_defaults({"which_key_delay": 0, "monospace": {"family": "DejaVu Sans Mono", "size": 10},
                                 "graph": {"depth": 2}})
H = lambda rel: os.path.join(HOME, *rel.split("/"))
w = A.MainWindow(H(site), H("Photos"), settings)
w.resize(1400, 820)
w.show()
wait(300)
left, right = w.panes

# 1. Main window: git colors, tags, quick view graph of Photos? -> keep: two panes + tags
left.set_cursor(left.model.row_of("src"))
wait_for(lambda: left.branch)
for name in ("index.html", "README.md"):
    left.set_cursor(left.model.row_of(name))
    left.toggle_tag()
left.set_cursor(left.model.row_of("src"))
right.cd(H("projects"))
right.set_cursor(right.model.row_of("website"))
w.activate(0)
w.update_status()
wait(400)
save(w, "screenshot.png")

# 2. Quick view graph (tree) of the home folder
right.cd(HOME)
w.activate(0)
left.cd(HOME)
left.set_cursor(0)
graphview.OPTIONS.update(mode="tree", depth=2)
w.cmd_quickview(None)
wait(200)
wait_for(lambda: right.preview.graph.graph is not None)
wait(2500)
right.preview.graph.fit()
wait(100)
save(w, "graph-tree.png")
w.cmd_quickview(None)


def graph_window(path, mode, name, size=(1000, 720), depth=2):
    graphview.OPTIONS.update(mode=mode, depth=depth)
    view = graphview.GraphView()
    view.resize(*size)
    view.show()
    view.show_folder(path, False, {})
    wait_for(lambda: view.graph is not None)
    wait(3500)
    if mode != "sunburst":
        view.fit()
    wait(150)
    save(view, name)
    view.close()


graph_window(HOME, "sunburst", "graph-sunburst.png", depth=3)
graph_window(H("projects/vimcommander"), "code", "graph-code.png", (1100, 760))
graph_window(H("Notes"), "notes", "graph-notes.png", (800, 560))
graphview.OPTIONS.update(mode="tree", depth=2)

# 3. Thumbnail grid with a quick view of a photo
left.cd(H("Photos/2025 Carpathians"))
w.activate(0)
left.set_grid(True)
left.set_cursor(left.model.row_of("IMG_2046.jpg"))
for n in ("IMG_2043.jpg", "IMG_2044.jpg"):
    left.set_cursor(left.model.row_of(n))
    left.toggle_tag()
left.set_cursor(left.model.row_of("IMG_2046.jpg"))
w.cmd_quickview(None)
wait(2500)
save(w, "grid.png")
w.cmd_quickview(None)
left.clear_tags()
left.set_grid(False)

# 4. Git + terminal + Miller columns
left.cd(H(site + "/src"))
right.cd(H(site))
w.execute("set columns")
right.set_cursor(right.model.row_of("src"))
w.activate(0)
left.set_cursor(left.model.row_of("main.ts"))
w.execute("terminal")
wait_for(lambda: w.terminal.running and not w.terminal.busy)
w.terminal.run("git status --short")
wait_for(lambda: not w.terminal.busy)
w.terminal.run("ls ..")
wait_for(lambda: not w.terminal.busy)
wait_for(lambda: left.branch)
wait(600)
save(w, "columns-terminal.png")
w.terminal.hide()
w.execute("set nocolumns")

# 5. Dialogs: grep, diff, activity, mirror, palette
from vimcommander.grepdialog import GrepDialog
from vimcommander.diffview import DiffDialog
from vimcommander.activity import ActivityDialog
from vimcommander.sync import SyncDialog

g = GrepDialog(H(site), "TODO", False, (True, True), w, w.cmdline.font(), "!node_modules")
g.resize(1100, 600)
g.show()
wait_for(lambda: "matches in" in g.status.text() and "Searching" not in g.status.text())
wait(300)
save(g, "grep.png")
g.close()

d = DiffDialog(H("backup/website/src/main.ts"), H(site + "/src/main.ts"), w, w.cmdline.font())
d.resize(1100, 420)
d.show()
wait(300)
save(d, "diff.png")
d.close()

a = ActivityDialog(H("Documents"), False, w)
a.resize(1000, 620)
a.show()
wait_for(lambda: a.heatmap.days)
wait(300)
save(a, "activity.png")
a.close()

m = SyncDialog(H(site), H("backup/website"), False, w, mirror=True)
m.resize(1000, 460)
m.show()
wait_for(lambda: m.diffs)
wait(300)
save(m, "mirror.png")
m.close()

# 6. Result list from :dupes-like search: :find
left.cd(H("Documents"))
w.activate(0)
w.execute("find report-2025")
wait_for(lambda: not w.searches)
for row in (2, 4, 5):
    left.set_cursor(row)
    left.toggle_tag()
left.set_cursor(3)
right.cd(H("Documents"))
w.shelf.add([H("Downloads/paper.pdf"), H("Documents/budget.xlsx"), H("Notes/Ideas.md")])
w.shelf.show()
w.update_status()
wait(400)
save(w, "results-shelf.png")
w.shelf.hide()
w.shelf.clear()
w.terminal.stop()
w.close()
print("done")

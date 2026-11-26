"""Renders the README screenshots into docs/ from a made-up demo home folder.

    .venv/bin/python tools/screenshots.py

Linux (or WSL) with git installed; it runs headless (QT_QPA_PLATFORM=offscreen)
and builds the demo folder in the temp folder (vimcommander-demo), with HOME pointing there.
"""
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
# outside the checkout, so its git repo doesn't show around the demo folders
DEMO = os.path.join(tempfile.gettempdir(), "vimcommander-demo")
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

# an STM32 firmware project: code, a linker map, a scope capture, a 3D part, a README
FW = "projects/charger-fw"
write(f"{FW}/Core/Src/main.c", """/* Wireless charger controller: STM32F411, 100 kHz full-bridge inverter */
#include "main.h"
#include "control.h"
#include <math.h>

#define F_SW_HZ        100000u   /* switching frequency */
#define V_BAT_MAX_MV   4200u
#define ADC_TO_MV(x)   ((x) * 3300u / 4095u)

static volatile uint16_t adc_buf[4];
static pi_t current_loop = { .kp = 0.08f, .ki = 1200.0f, .limit = 0.45f };

static void set_duty(float d)
{
    uint32_t arr = __HAL_TIM_GET_AUTORELOAD(&htim1);
    __HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_1, (uint32_t)(d * arr));
}

void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc)
{
    float i_rx = ADC_TO_MV(adc_buf[1]) * 0.002f;      /* 2 mA per mV */
    float duty = pi_step(&current_loop, I_REF_A - i_rx, 1.0f / F_SW_HZ);
    set_duty(duty);
}

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    MX_GPIO_Init();
    MX_ADC1_Init();
    MX_TIM1_Init();

    HAL_ADC_Start_DMA(&hadc1, (uint32_t *)adc_buf, 4);
    HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1);

    while (1) {
        if (ADC_TO_MV(adc_buf[2]) >= V_BAT_MAX_MV) {
            set_duty(0.0f);                             /* charged: stop */
            HAL_GPIO_WritePin(LED_GPIO_Port, LED_Pin, GPIO_PIN_SET);
        }
        HAL_Delay(10);
    }
}
""", days_ago=1)
write(f"{FW}/Core/Src/control.c", "#include \"control.h\"\n", size=2400, days_ago=2)
write(f"{FW}/Core/Inc/main.h", "#pragma once\n", size=1800, days_ago=5)
write(f"{FW}/Core/Inc/control.h", "#pragma once\n", size=600, days_ago=5)


def _map():
    objs = [  # (section, name, object, size, region)
        (".text", "main", "Core/Src/main.o", 0x5a4, "FLASH"),
        (".text", "pi_step", "Core/Src/control.o", 0x1e8, "FLASH"),
        (".text", "HAL_TIM_PWM_Start", "Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_tim.o", 0x1c40, "FLASH"),
        (".text", "HAL_ADC_Start_DMA", "Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_adc.o", 0x1380, "FLASH"),
        (".text", "HAL_RCC_OscConfig", "Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_rcc.o", 0xd70, "FLASH"),
        (".text", "HAL_DMA_Start_IT", "Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_dma.o", 0x9c0, "FLASH"),
        (".text", "HAL_GPIO_Init", "Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_gpio.o", 0x4d0, "FLASH"),
        (".text", "HAL_UART_Transmit", "Drivers/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal_uart.o", 0x1720, "FLASH"),
        (".text", "sinf", "d:/gcc/lib\\libm.a(lib_a-sf_sin.o)", 0x6b0, "FLASH"),
        (".text", "_printf_float", "d:/gcc/lib\\libc_nano.a(lib_a-nano-vfprintf_float.o)", 0x2f8c, "FLASH"),
        (".text", "memcpy", "d:/gcc/lib\\libc_nano.a(lib_a-memcpy.o)", 0xfe, "FLASH"),
        (".rodata", "sine_table", "Core/Src/control.o", 0x1000, "FLASH"),
        (".rodata", "AHBPrescTable", "Drivers/CMSIS/system_stm32f4xx.o", 0x10, "FLASH"),
        (".data", "current_loop", "Core/Src/main.o", 0x14, "RAM"),
        (".data", "SystemCoreClock", "Drivers/CMSIS/system_stm32f4xx.o", 0x4, "RAM"),
        (".bss", "adc_buf", "Core/Src/main.o", 0x8, "RAM"),
        (".bss", "log_ring", "Core/Src/control.o", 0x2000, "RAM"),
        (".bss", "htim1", "Core/Src/main.o", 0x48, "RAM"),
        (".bss", "huart2", "Core/Src/main.o", 0x88, "RAM"),
    ]
    out = ["Archive member included to satisfy reference by file (symbol)", "", "Memory Configuration", "",
           "Name             Origin             Length             Attributes",
           "RAM              0x0000000020000000 0x0000000000020000 xrw",
           "FLASH            0x0000000008000000 0x0000000000080000 xr",
           "*default*        0x0000000000000000 0xffffffffffffffff", "", "Linker script and memory map", "",
           ".isr_vector     0x0000000008000000      0x198",
           " .isr_vector    0x0000000008000000      0x198 Core/Startup/startup_stm32f411retx.o", ""]
    addr = {"FLASH": 0x08000198, "RAM": 0x20000000}
    for sec in (".text", ".rodata", ".data", ".bss"):
        items = [o for o in objs if o[0] == sec]
        region = items[0][4]
        start, total = addr[region], sum(o[3] for o in items)
        load = f" load address 0x{addr['FLASH']:016x}" if sec == ".data" else ""
        out.append(f"{sec:<15} 0x{start:016x} {total:#10x}{load}")
        a = start
        for _, name, obj, size, _ in items:
            out += [f" {sec}.{name}", f"                0x{a:016x} {size:#10x} {obj}",
                    f"                0x{a:016x}                {name}"]
            a += size
        out.append("")
        addr[region] = start + total
        if sec == ".data":
            addr["FLASH"] += total
    out += ["._user_heap_stack", f"                0x{addr['RAM']:016x}     0x1600",
            f" *fill*         0x{addr['RAM']:016x}     0x1600 ", ""]
    return "\n".join(out) + "\n"


write(f"{FW}/Debug/charger-fw.map", _map(), days_ago=1)
write(f"{FW}/Debug/charger-fw.elf", "", size=412_000, days_ago=1)
write(f"{FW}/Debug/charger-fw.bin", "", size=38_400, days_ago=1)

rows = ["Time,Inverter,Coil current,Vout", "s,V,A,V"]
for i in range(2400):
    t = i * 2e-7
    v = 24.0 if (t * 1e5) % 1 < 0.5 else -24.0
    rows.append(f"{t:.7f},{v:.2f},{3.1 * math.sin(2 * math.pi * 1e5 * t - 0.6):.4f},"
                f"{4.2 * (1 - math.exp(-t / 1.2e-4)) + 0.05 * math.sin(2 * math.pi * 2e5 * t):.4f}")
write(f"{FW}/measurements/scope_100kHz.csv", "\n".join(rows) + "\n", days_ago=3)


def _gear(teeth=24, r_out=20.0, r_root=17.0, r_hole=5.0, h=6.0):
    """An ASCII STL spur gear with a bore: its outline and the hole as rings of points."""
    n = teeth * 4
    outer, hole = [], []
    for k in range(n):
        a = 2 * math.pi * k / n
        r = r_out if k % 4 in (1, 2) else r_root
        outer.append((r * math.cos(a), r * math.sin(a)))
        hole.append((r_hole * math.cos(a), r_hole * math.sin(a)))
    tris = []
    for k in range(n):
        o0, o1, i0, i1 = outer[k], outer[(k + 1) % n], hole[k], hole[(k + 1) % n]
        for z, flip in ((h, False), (0.0, True)):  # top and bottom rings
            for t in (((*o0, z), (*o1, z), (*i1, z)), ((*o0, z), (*i1, z), (*i0, z))):
                tris.append(t[::-1] if flip else t)
        for p, q, out in ((o0, o1, True), (i0, i1, False)):  # outer flank and bore wall
            t1 = ((*p, 0.0), (*q, 0.0), (*q, h))
            t2 = ((*p, 0.0), (*q, h), (*p, h))
            tris += [t1, t2] if out else [t1[::-1], t2[::-1]]
    lines = ["solid gear"]
    for t in tris:
        lines += ["  facet normal 0 0 0", "    outer loop"]
        lines += [f"      vertex {x:.4f} {y:.4f} {z:.4f}" for x, y, z in t]
        lines += ["    endloop", "  endfacet"]
    return "\n".join(lines + ["endsolid gear"]) + "\n"


write(f"{FW}/mechanics/coil_gear.stl", _gear(), days_ago=6)
write(f"{FW}/.gitignore", "Debug/*.elf\nDebug/*.bin\n")
write(f"{FW}/README.md", """# Charger firmware

Firmware for a **100 kHz wireless charger** (STM32F411): a full-bridge inverter,
a PI current loop on the receiver side and CC/CV charging of a Li-ion cell.

> [!note]
> Build with STM32CubeIDE 1.16 or `make`. Flash over ST-Link: `make flash`.

## Status

| Block | State | Notes |
|---|---|---|
| Inverter PWM | done | TIM1, dead time 120 ns |
| Current loop | done | PI, 100 kHz ADC trigger |
| CV phase | testing | taper at 4.2 V |
| Foreign object detection | todo | Q-factor method |

## Next

- [x] Soft start of the inverter
- [x] Over-temperature shutdown
- [ ] Foreign object detection
- [ ] Log over UART at 1 kHz

```c
float duty = pi_step(&current_loop, I_REF_A - i_rx, 1.0f / F_SW_HZ);
```
""", days_ago=0)
fw_git = ["git", "-C", os.path.join(HOME, FW)]
subprocess.run(fw_git + ["init", "-q", "-b", "main"], check=True)
subprocess.run(fw_git + ["add", "-A"], check=True)
subprocess.run(fw_git + ["commit", "-q", "-m", "first version"], check=True)
with open(os.path.join(HOME, FW, "Core/Src/main.c"), "a") as f:
    f.write("/* TODO: foreign object detection */\n")
write(f"{FW}/measurements/scope_150kHz.csv", "Time,Vout\n0,0\n", size=64_000, days_ago=0)

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
    if hasattr(widget, "show_message"):
        widget.show_message("")
        widget.update_status()
        wait(50)
    widget.grab().save(os.path.join(DOCS, name))
    print("saved", name)


settings = config.with_defaults({"theme": "catppuccin-mocha", "which_key_delay": 0, "monospace": {"family": "DejaVu Sans Mono", "size": 10},
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

# 7. Quick view of the newer previews, in the firmware project
from vimcommander.palette import PaletteDialog


def quick(folder, name, shot, wait_ms=1500, plot=False):
    left.cd(H(folder))
    w.activate(0)
    left.set_cursor(left.model.row_of(name))
    right.cd(H(FW))
    w.cmd_quickview(None)
    if plot:
        w.cmd_plot(None)
    wait(wait_ms)
    save(w, shot)
    if plot:
        w.cmd_plot(None)
    w.cmd_quickview(None)


quick(FW + "/Core/Src", "main.c", "code.png")
quick(FW + "/Debug", "charger-fw.map", "memmap.png")
quick(FW + "/measurements", "scope_100kHz.csv", "plot.png", plot=True)
quick(FW + "/mechanics", "coil_gear.stl", "model.png", 3000)
quick(FW, "README.md", "markdown.png")

# 8. Which-key after g, and the command palette
left.cd(H(FW))
right.cd(H(site))
w.activate(0)
left.set_cursor(left.model.row_of("Core"))
w.feed_key("g")
title, entries = w.whichkey_entries()
w.whichkey.show_entries(title, entries, w.info_label)
wait(400)
save(w, "whichkey.png")
w.engine.reset()
w.whichkey.hide()

p = PaletteDialog(w.palette_entries(), w, w.cmdline.font())
p.resize(760, 440)
p.show()
p.query.setText("sync")
wait(400)
save(p, "palette.png")
p.close()
w.terminal.stop()
w.close()
print("done")

"""Folder, note and code graphs for the quick view. Pure Python, no Qt.

Tree mode: a folder in the middle, its subfolders around it 1-3 levels deep,
and each folder's files grouped by extension ("142 × .jpg"), laid out as a
radial tree. Notes mode: the Markdown files below a folder and the
[[wikilinks]] / [text](note.md) links between them, laid out by a small force
simulation, like Obsidian's graph view. Code mode: the source files below a
folder and the imports between them (Python, JS/TS, C/C++, Go, Rust), laid out
the same way, so the files many others import stand out as hubs.

Treemap mode uses the tree scan, laid out by layout_treemap(). Timeline and
types modes use scan_folders(): one node per subfolder with the first and last
change of its files, changes per month, and bytes / files per category.
"""

import math
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

CATEGORIES = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff",
              ".heic", ".ico", ".raw", ".cr2", ".nef", ".psd"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v"},
    "audio": {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus", ".wma"},
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".h", ".cpp", ".hpp", ".cs", ".java",
             ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat", ".lua", ".kt", ".swift",
             ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".sql", ".ipynb",
             ".s", ".asm", ".ld", ".ioc", ".mk", ".cmake", ".ino", ".m", ".v", ".vhd", ".sv"},
    "document": {".md", ".txt", ".pdf", ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx",
                 ".ods", ".csv", ".ppt", ".pptx", ".odp", ".epub", ".tex"},
    "archive": {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso", ".jar", ".whl",
                ".deb", ".rpm", ".msi", ".dmg"},
    "program": {".exe", ".dll", ".so", ".dylib", ".app", ".appimage", ".bin"},
    "build": {".o", ".obj", ".elf", ".axf", ".hex", ".map", ".lst", ".d", ".su", ".a", ".lib",
              ".pdb", ".ilk", ".pyc", ".class", ".srec"},
    "design": {".aedt", ".aedtz", ".kicad_pcb", ".kicad_sch", ".kicad_pro", ".sch", ".brd",
               ".pcbdoc", ".schdoc", ".prjpcb", ".dsn", ".step", ".stp", ".stl", ".iges", ".dwg",
               ".dxf", ".f3d", ".sldprt", ".sldasm", ".slx", ".mdl", ".psimsch", ".asc", ".graphml"},
}
_BY_EXT = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}
SKIP_DIRS = {".git", ".svn", ".hg", "node_modules", "__pycache__", ".obsidian", ".venv",
             ".trash", "$RECYCLE.BIN", "System Volume Information"}

# How many children a node keeps at each level before the rest are folded
# into one "N more" node, so big folders stay readable.
KEEP = {1: 36, 2: 12, 3: 6}


def category(ext: str) -> str:
    return _BY_EXT.get(ext.lower(), "other")


@dataclass
class Node:
    name: str
    path: str
    kind: str  # "root" | "folder" | "files" | "more" | "note" | "code"
    category: str = "folder"
    ext: str = ""  # for "files" nodes: the extension they share
    size: int = 0  # bytes in everything this node stands for
    count: int = 0  # files in it
    depth: int = 0
    parent: int = -1
    children: list[int] = field(default_factory=list)
    complete: bool = True  # False while the size of a leaf folder's subtree is unknown
    links: int = 0  # notes mode: number of links
    first: float = 0.0  # timeline: oldest and newest file change (mtime)
    last: float = 0.0
    months: dict[int, int] = field(default_factory=dict)  # timeline: year * 12 + month - 1 -> changes
    mix: dict[str, list[int]] = field(default_factory=dict)  # types: category -> [bytes, files]
    x: float = 0.0
    y: float = 0.0


@dataclass
class Graph:
    nodes: list[Node]
    edges: list[tuple[int, int]]
    mode: str  # "tree" | "sunburst" | "treemap" | "timeline" | "types" | "notes" | "code"
    root: str
    truncated: bool = False  # some items were left out


# Tree mode

def _visible(name: str, show_hidden: bool) -> bool:
    return show_hidden or not name.startswith(".")


def _list(path: str, show_hidden: bool) -> tuple[list[tuple[str, str]], list[tuple[str, int]]]:
    """(subfolders as (name, path), files as (name, size)) of a folder."""
    folders, files = [], []
    try:
        with os.scandir(path) as it:
            for e in it:
                if not _visible(e.name, show_hidden):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        folders.append((e.name, e.path))
                    else:
                        files.append((e.name, e.stat(follow_symlinks=False).st_size))
                except OSError:
                    pass
    except OSError:
        pass
    return folders, files


def scan_tree(root: str, depth: int, show_hidden: bool = False, size_by: str = "size",
              stop: threading.Event | None = None, max_nodes: int = 400) -> Graph:
    """Folders below `root` to `depth` levels, with files grouped by extension.

    Leaf folders (at the last level) get their item count, but their size only
    covers the files directly inside (complete=False); fill_sizes() adds the rest.
    """
    nodes = [Node(os.path.basename(root.rstrip("/\\")) or root, root, "root")]
    truncated = False
    level = [0]
    for d in range(1, depth + 1):
        next_level = []
        for parent in level:
            if stop and stop.is_set():
                break
            folders, files = _list(nodes[parent].path, show_hidden)
            groups: dict[str, list[int]] = {}
            for name, size in files:
                ext = os.path.splitext(name)[1].lower() if "." in name[1:] else ""
                g = groups.setdefault(ext, [0, 0])
                g[0] += 1
                g[1] += size
            children = []
            for ext, (count, size) in groups.items():
                label = f"{count} × {ext or 'no extension'}"
                children.append(Node(label, nodes[parent].path, "files", category(ext), ext,
                                     size, count, d, parent))
            for name, path in folders:
                if name in SKIP_DIRS:
                    continue
                sub_folders, sub_files = _list(path, show_hidden) if d == depth else ([], [])
                node = Node(name, path, "folder", "folder", "", 0, 0, d, parent)
                if d == depth:  # a leaf: count what's directly inside
                    node.count = len(sub_files) + len(sub_folders)
                    node.size = sum(s for _, s in sub_files)
                    node.complete = not sub_folders
                children.append(node)
            metric = (lambda n: n.size) if size_by == "size" else (lambda n: max(n.count, 1))
            children.sort(key=metric, reverse=True)
            keep = KEEP.get(d, 6)
            if len(children) > keep + 1:
                rest = children[keep:]
                children = children[:keep]
                children.append(Node(f"+{len(rest)} more", nodes[parent].path, "more", "other",
                                     "", sum(n.size for n in rest), sum(n.count for n in rest),
                                     d, parent, complete=all(n.complete for n in rest)))
                truncated = True
            for child in children:
                if len(nodes) >= max_nodes:
                    truncated = True
                    break
                nodes.append(child)
                index = len(nodes) - 1
                nodes[parent].children.append(index)
                if child.kind == "folder" and d < depth:
                    next_level.append(index)
        level = next_level
    _aggregate(nodes)
    edges = [(n.parent, i) for i, n in enumerate(nodes) if n.parent >= 0]
    return Graph(nodes, edges, "tree", root, truncated)


def _aggregate(nodes: list[Node]) -> None:
    """Folder sizes and counts from their children (children come after parents)."""
    for node in reversed(nodes):
        if node.children:
            node.size = sum(nodes[c].size for c in node.children)
            node.count = sum(nodes[c].count for c in node.children)
            node.complete = all(nodes[c].complete for c in node.children)


def folder_size(path: str, stop: threading.Event | None = None, deadline: float = 0) -> tuple[int, int, bool]:
    """(bytes, files, finished) of a whole folder tree; stops at `deadline` (time.monotonic)."""
    total = files = 0
    stack = [path]
    while stack:
        if (stop and stop.is_set()) or (deadline and time.monotonic() > deadline):
            return total, files, False
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return total, files, True


def fill_sizes(graph: Graph, stop: threading.Event | None = None, budget: float = 4.0,
               known: dict[str, int] | None = None) -> bool:
    """Add up the full size of leaf folders, within `budget` seconds. True if all done."""
    deadline = time.monotonic() + budget
    all_done = True
    for node in graph.nodes:
        if node.kind != "folder" or node.children or node.complete:
            continue
        if known and node.path in known:
            node.size, node.complete = known[node.path], True
            continue
        size, files, done = folder_size(node.path, stop, deadline)
        if done:
            node.size, node.count, node.complete = size, max(node.count, files), True
        else:
            all_done = False
            break
    _aggregate(graph.nodes)
    return all_done


# Treemap

def squarify(items: list[tuple[int, float]], x: float, y: float, w: float, h: float
             ) -> list[tuple[int, float, float, float, float]]:
    """Squarified treemap (Bruls et al.): (key, weight) items, biggest first, to
    (key, x, y, w, h) rectangles filling the area, as square as they can be."""
    items = [(k, wt) for k, wt in items if wt > 0]
    total = sum(wt for _, wt in items)
    out: list[tuple[int, float, float, float, float]] = []
    if total <= 0 or w <= 0 or h <= 0:
        return out
    scale = w * h / total
    areas = [(k, wt * scale) for k, wt in items]

    def worst(row, side):
        s = sum(a for _, a in row)
        return max(side * side * row[0][1] / (s * s), s * s / (side * side * row[-1][1]))

    while areas:
        side = min(w, h)
        row, i = [areas[0]], 1
        while i < len(areas) and worst(row + [areas[i]], side) <= worst(row, side):
            row.append(areas[i])
            i += 1
        areas = areas[i:]
        s = sum(a for _, a in row)
        if w >= h:  # a column at the left
            cw, yy = s / h, y
            for k, a in row:
                out.append((k, x, yy, cw, a / cw))
                yy += a / cw
            x, w = x + cw, w - cw
        else:  # a row at the top
            rh, xx = s / w, x
            for k, a in row:
                out.append((k, xx, y, a / rh, rh))
                xx += a / rh
            y, h = y + rh, h - rh
    return out


def layout_treemap(graph: Graph, x: float, y: float, w: float, h: float, by_size: bool = True,
                   header: float = 15.0, pad: float = 2.0) -> list[tuple[int, float, float, float, float]]:
    """Every node below the root as (index, x, y, w, h), parents before their
    children. A folder keeps a `header` strip at its top for its name."""
    nodes = graph.nodes
    out: list[tuple[int, float, float, float, float]] = []

    def weight(n: Node) -> float:
        return max(n.size if by_size else n.count, 0)

    def place(i: int, x: float, y: float, w: float, h: float) -> None:
        kids = sorted(nodes[i].children, key=lambda k: -weight(nodes[k]))
        for k, kx, ky, kw, kh in squarify([(k, weight(nodes[k])) for k in kids], x, y, w, h):
            out.append((k, kx, ky, kw, kh))
            if nodes[k].children and kw > 4 * pad + 12 and kh > header + 3 * pad + 8:
                place(k, kx + pad, ky + header, kw - 2 * pad, kh - header - pad)

    place(0, x, y, w, h)
    return out


# Timeline and types: a summary of each subfolder

def _month(t: float) -> int:
    d = time.localtime(t)
    return d.tm_year * 12 + d.tm_mon - 1


def _add_file(node: Node, name: str, st: os.stat_result, now: float) -> None:
    node.count += 1
    node.size += st.st_size
    ext = os.path.splitext(name)[1] if "." in name[1:] else ""
    mix = node.mix.setdefault(category(ext), [0, 0])
    mix[0] += st.st_size
    mix[1] += 1
    t = st.st_mtime
    if 315532800 < t <= now + 86400:  # 1980..now: ignore broken dates
        node.first = min(node.first, t) if node.first else t
        node.last = max(node.last, t)
        m = _month(t)
        node.months[m] = node.months.get(m, 0) + 1


def scan_folders(root: str, show_hidden: bool = False, stop: threading.Event | None = None,
                 budget: float = 8.0, max_folders: int = 400) -> Graph:
    """The root (totals) and one node per subfolder (its whole subtree), plus a
    "files here" node for the files directly in root. A subtree not finished
    within `budget` seconds is marked complete=False."""
    now = time.time()
    deadline = time.monotonic() + budget
    nodes = [Node(os.path.basename(root.rstrip("/\\")) or root, root, "root")]
    here = Node("(files here)", root, "files", "other", depth=1, parent=0)
    folders, truncated = [], False
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError:
        entries = []
    for e in entries:
        if not _visible(e.name, show_hidden):
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                if e.name in SKIP_DIRS:
                    continue
                if len(folders) >= max_folders:
                    truncated = True
                    continue
                folders.append(Node(e.name, e.path, "folder", depth=1, parent=0))
            else:
                _add_file(here, e.name, e.stat(follow_symlinks=False), now)
        except OSError:
            pass
    for node in folders:
        stack = [node.path]
        while stack:
            if (stop and stop.is_set()) or time.monotonic() > deadline:
                node.complete = False
                truncated = True
                break
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        if not _visible(e.name, show_hidden):
                            continue
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if e.name not in SKIP_DIRS:
                                    stack.append(e.path)
                            else:
                                _add_file(node, e.name, e.stat(follow_symlinks=False), now)
                        except OSError:
                            pass
            except OSError:
                pass
        if stop and stop.is_set():
            break
    rootnode = nodes[0]
    for n in folders + ([here] if here.count else []):
        n.parent = 0
        rootnode.children.append(len(nodes))
        nodes.append(n)
        rootnode.size += n.size
        rootnode.count += n.count
        rootnode.complete = rootnode.complete and n.complete
        for cat, (b, c) in n.mix.items():
            mix = rootnode.mix.setdefault(cat, [0, 0])
            mix[0] += b
            mix[1] += c
        for m, c in n.months.items():
            rootnode.months[m] = rootnode.months.get(m, 0) + c
        if n.first:
            rootnode.first = min(rootnode.first, n.first) if rootnode.first else n.first
            rootnode.last = max(rootnode.last, n.last)
    return Graph(nodes, [(0, i) for i in range(1, len(nodes))], "timeline", root, truncated)


def layout_radial(graph: Graph, ring: float = 120.0) -> None:
    """Root in the middle, each level on a ring; a subtree gets an angle in
    proportion to its number of leaves, so the picture is stable and readable."""
    nodes = graph.nodes
    weight = [1.0] * len(nodes)
    for i in reversed(range(len(nodes))):
        if nodes[i].children:
            weight[i] = sum(weight[c] for c in nodes[i].children)
    # Rings grow when a level has many nodes, so they don't overlap.
    per_level: dict[int, int] = {}
    for n in nodes:
        per_level[n.depth] = per_level.get(n.depth, 0) + 1
    radius = {0: 0.0}
    for d in sorted(k for k in per_level if k > 0):
        radius[d] = max(radius[d - 1] + ring, per_level[d] * 26 / (2 * math.pi))

    def place(i: int, start: float, span: float) -> None:
        node = nodes[i]
        angle = start + span / 2
        node.x, node.y = radius[node.depth] * math.cos(angle), radius[node.depth] * math.sin(angle)
        a = start
        for c in node.children:
            part = span * weight[c] / weight[i]
            place(c, a, part)
            a += part

    place(0, -math.pi / 2, 2 * math.pi)


# Notes mode

WIKILINK = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
MDLINK = re.compile(r"\]\(<?([^)>#\s]+?\.md)>?(?:#[^)]*)?\)", re.IGNORECASE)


def find_links(text: str) -> tuple[list[str], list[str]]:
    """(wikilink targets, relative .md link targets) in a note."""
    wiki = [m.group(1).strip() for m in WIKILINK.finditer(text)]
    md = [urllib.parse.unquote(m.group(1)) for m in MDLINK.finditer(text)
          if "://" not in m.group(1)]
    return wiki, md


def count_notes(root: str, limit: int = 3, show_hidden: bool = False) -> int:
    """How many .md files are below `root` (stops counting at `limit`)."""
    found = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and _visible(d, show_hidden)]
        found += sum(1 for f in files if f.lower().endswith(".md"))
        if found >= limit:
            break
    return found


def scan_notes(root: str, show_hidden: bool = False, stop: threading.Event | None = None,
               max_notes: int = 300) -> Graph:
    paths = []
    truncated = False
    for folder, dirs, files in os.walk(root):
        if stop and stop.is_set():
            break
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and _visible(d, show_hidden))
        for f in sorted(files):
            if f.lower().endswith(".md") and _visible(f, show_hidden):
                paths.append(os.path.join(folder, f))
        if len(paths) >= max_notes:
            paths, truncated = paths[:max_notes], True
            break
    nodes = [Node(os.path.splitext(os.path.basename(p))[0], p, "note", "document") for p in paths]
    by_stem: dict[str, int] = {}
    by_path: dict[str, int] = {}
    for i, p in enumerate(paths):
        by_stem.setdefault(os.path.splitext(os.path.basename(p))[0].casefold(), i)
        by_path[os.path.normcase(os.path.abspath(p))] = i
    edges: set[tuple[int, int]] = set()
    for i, p in enumerate(paths):
        if stop and stop.is_set():
            break
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read(200_000)
        except OSError:
            continue
        wiki, md = find_links(text)
        targets = [by_stem.get(os.path.basename(w).casefold()) for w in wiki]
        targets += [by_path.get(os.path.normcase(os.path.abspath(os.path.join(os.path.dirname(p), m))))
                    for m in md]
        for t in targets:
            if t is not None and t != i:
                edges.add((min(i, t), max(i, t)))
    for a, b in edges:
        nodes[a].links += 1
        nodes[b].links += 1
    return Graph(nodes, sorted(edges), "notes", root, truncated)


class ForceLayout:
    """A small force simulation for the notes graph: nodes push each other apart
    (only near neighbours, through a grid, so it stays fast), links pull, and a
    weak pull to the middle keeps it together. Call step() until it settles."""

    REPULSION, SPRING, LENGTH, GRAVITY, DAMPING, CELL = 1800.0, 0.04, 70.0, 0.012, 0.82, 140.0

    def __init__(self, graph: Graph):
        self.graph = graph
        self.vx = [0.0] * len(graph.nodes)
        self.vy = [0.0] * len(graph.nodes)
        n = max(len(graph.nodes), 1)
        for i, node in enumerate(graph.nodes):  # a stable start: a spiral, by name
            angle = i * 2.399963  # golden angle
            r = 18 * math.sqrt(i + 1) * (1 + 40 / n)
            node.x, node.y = r * math.cos(angle), r * math.sin(angle)

    def step(self) -> float:
        """One step; returns how far the nodes moved on average."""
        nodes = self.graph.nodes
        fx = [0.0] * len(nodes)
        fy = [0.0] * len(nodes)
        grid: dict[tuple[int, int], list[int]] = {}
        for i, n in enumerate(nodes):
            grid.setdefault((int(n.x // self.CELL), int(n.y // self.CELL)), []).append(i)
        for (cx, cy), members in grid.items():
            near = [j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in grid.get((cx + dx, cy + dy), ())]
            for i in members:
                xi, yi = nodes[i].x, nodes[i].y
                for j in near:
                    if j <= i:
                        continue
                    dx, dy = xi - nodes[j].x, yi - nodes[j].y
                    d2 = dx * dx + dy * dy + 0.01
                    if d2 > self.CELL * self.CELL:
                        continue
                    f = self.REPULSION / d2
                    d = math.sqrt(d2)
                    fx[i] += f * dx / d
                    fy[i] += f * dy / d
                    fx[j] -= f * dx / d
                    fy[j] -= f * dy / d
        for a, b in self.graph.edges:
            dx, dy = nodes[b].x - nodes[a].x, nodes[b].y - nodes[a].y
            d = math.sqrt(dx * dx + dy * dy) + 0.01
            f = self.SPRING * (d - self.LENGTH)
            fx[a] += f * dx / d
            fy[a] += f * dy / d
            fx[b] -= f * dx / d
            fy[b] -= f * dy / d
        moved = 0.0
        for i, n in enumerate(nodes):
            fx[i] -= self.GRAVITY * n.x
            fy[i] -= self.GRAVITY * n.y
            self.vx[i] = (self.vx[i] + fx[i]) * self.DAMPING
            self.vy[i] = (self.vy[i] + fy[i]) * self.DAMPING
            speed = math.hypot(self.vx[i], self.vy[i])
            if speed > 30:  # no wild jumps
                self.vx[i] *= 30 / speed
                self.vy[i] *= 30 / speed
            n.x += self.vx[i]
            n.y += self.vy[i]
            moved += abs(self.vx[i]) + abs(self.vy[i])
        return moved / max(len(nodes), 1)


# Code mode: source files and the imports between them

LANGUAGES = {
    ".py": "python", ".pyw": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".hh": "cpp", ".hxx": "cpp",
    ".go": "go", ".rs": "rust",
}
CODE_SKIP_DIRS = SKIP_DIRS | {"build", "dist", "target", "vendor", "venv", "env", "site-packages",
                              ".venv-win", ".tox", ".mypy_cache", ".pytest_cache", "out"}
# Names that say little on their own; they're shown with their folder.
GENERIC_NAMES = {"__init__.py", "__main__.py", "index.js", "index.ts", "index.tsx", "index.jsx",
                 "mod.rs", "lib.rs", "main.rs", "main.go", "main.c", "main.cpp"}
JS_SUFFIXES = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".d.ts",
               "/index.ts", "/index.tsx", "/index.js", "/index.jsx")

PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w. \t,]+)", re.MULTILINE)
PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]*(?:\(([^)]*)\)|([\w \t,*]+))",
                     re.MULTILINE)
JS_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)['"](\.{1,2}/[^'"]*)['"]""")
C_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.MULTILINE)
GO_IMPORT = re.compile(r'\bimport\s*(?:\(([^)]*)\)|[\w.]*\s*"([^"]+)")', re.DOTALL)
GO_PATH = re.compile(r'"([^"]+)"')
RUST_MOD = re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?mod[ \t]+(\w+)[ \t]*;", re.MULTILINE)


def code_files(root: str, show_hidden: bool = False, stop: threading.Event | None = None,
               limit: int = 600) -> tuple[list[str], bool]:
    """Source files below `root` (sorted per folder) and whether some were left out."""
    paths = []
    for folder, dirs, files in os.walk(root):
        if stop and stop.is_set():
            break
        dirs[:] = sorted(d for d in dirs if d not in CODE_SKIP_DIRS and _visible(d, show_hidden)
                         and not d.endswith(".egg-info"))
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in LANGUAGES and _visible(f, show_hidden):
                paths.append(os.path.join(folder, f))
                if len(paths) >= limit:
                    return paths, True
    return paths, False


class _CodeIndex:
    """Finds the file an import refers to."""

    def __init__(self, root: str, paths: list[str]):
        self.root = root
        self.by_path = {os.path.normcase(os.path.abspath(p)): i for i, p in enumerate(paths)}
        self.modules: dict[str, list[int]] = {}  # Python dotted names (and their tails) -> files
        self.headers: dict[str, list[int]] = {}  # C/C++ file names -> files
        self.go_dirs: dict[str, list[int]] = {}  # folder relative to root, with / -> Go files
        for i, p in enumerate(paths):
            rel = os.path.relpath(p, root)
            stem, ext = os.path.splitext(rel)
            lang = LANGUAGES[ext.lower()]
            if lang == "python":
                parts = stem.split(os.sep)
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                for k in range(len(parts)):
                    self.modules.setdefault(".".join(parts[k:]), []).append(i)
            elif lang in ("c", "cpp"):
                self.headers.setdefault(os.path.basename(p).casefold(), []).append(i)
            elif lang == "go" and not p.endswith("_test.go"):
                self.go_dirs.setdefault(os.path.dirname(rel).replace(os.sep, "/"), []).append(i)
        self.paths = paths

    def file(self, path: str) -> int | None:
        return self.by_path.get(os.path.normcase(os.path.abspath(path)))

    def _closest(self, candidates: list[int], importer: str) -> int | None:
        """Of several files with the same name, the one nearest the importer."""
        if not candidates:
            return None
        here = os.path.dirname(importer)

        def shared(i: int) -> int:
            try:
                return len(os.path.commonpath([here, os.path.dirname(self.paths[i])]))
            except ValueError:
                return 0
        return max(candidates, key=shared)

    def python(self, importer: str, dots: str, module: str, names: list[str]) -> list[int]:
        """`from x import a, b` imports submodules x.a and x.b if they exist, else module x."""
        if dots:  # relative: from . import x, from ..pkg import y
            base = os.path.dirname(importer)
            for _ in range(len(dots) - 1):
                base = os.path.dirname(base)
            folder = os.path.join(base, *module.split(".")) if module else base

            def find(target: str) -> int | None:
                for candidate in (target + ".py", os.path.join(target, "__init__.py")):
                    if (i := self.file(candidate)) is not None:
                        return i
                return None
            subs = [i for n in names if (i := find(os.path.join(folder, n))) is not None]
            main = find(folder) if not subs and module else None
            return subs or ([main] if main is not None else [])
        subs = [i for n in names
                if (i := self._closest(self.modules.get(f"{module}.{n}", []), importer)) is not None]
        main = self._closest(self.modules.get(module, []), importer) if not subs else None
        return subs or ([main] if main is not None else [])

    def js(self, importer: str, spec: str) -> int | None:
        base = os.path.normpath(os.path.join(os.path.dirname(importer), spec))
        for suffix in JS_SUFFIXES:
            if (i := self.file(base + suffix.replace("/", os.sep))) is not None:
                return i
        if base.endswith(".js"):  # TypeScript's "./x.js" means ./x.ts
            for ext in (".ts", ".tsx"):
                if (i := self.file(base[:-3] + ext)) is not None:
                    return i
        return None

    def include(self, importer: str, name: str) -> int | None:
        if (i := self.file(os.path.join(os.path.dirname(importer), name))) is not None:
            return i
        return self._closest(self.headers.get(os.path.basename(name).casefold(), []), importer)

    def go(self, spec: str) -> list[int]:
        """The Go files of the folder that ends with the longest tail of the import path
        (example.com/m/internal/text -> …/internal/text). At least two parts must match,
        so standard packages like "fmt" or "encoding/json" don't match a folder by chance."""
        parts = spec.split("/")
        for k in range(len(parts) - 1):
            tail = "/".join(parts[k:])
            folders = sorted(d for d in self.go_dirs if d == tail or d.endswith("/" + tail))
            if folders:
                return self.go_dirs[folders[0]][:8]
        return []

    def rust(self, importer: str, name: str) -> int | None:
        folder, file = os.path.split(importer)
        if file not in ("mod.rs", "lib.rs", "main.rs"):
            folder = os.path.join(folder, os.path.splitext(file)[0])
        for candidate in (os.path.join(folder, name + ".rs"), os.path.join(folder, name, "mod.rs")):
            if (i := self.file(candidate)) is not None:
                return i
        return None


def find_imports(path: str, text: str, index: _CodeIndex) -> set[int]:
    """Files (indexes into the scanned list) that the source file `path` imports."""
    lang = LANGUAGES.get(os.path.splitext(path)[1].lower())
    found: set[int | None] = set()
    if lang == "python":
        for m in PY_IMPORT.finditer(text):
            for part in m.group(1).split(","):
                module = part.split()[0] if part.split() else ""
                if module:
                    found.update(index.python(path, "", module, []))
        for m in PY_FROM.finditer(text):
            names = [n.split()[0] for n in (m.group(3) or m.group(4)).replace("\n", ",").split(",")
                     if n.split() and n.split()[0] != "*"]
            found.update(index.python(path, m.group(1), m.group(2), names))
    elif lang in ("js", "ts"):
        found.update(index.js(path, m.group(1)) for m in JS_IMPORT.finditer(text))
    elif lang in ("c", "cpp"):
        found.update(index.include(path, m.group(1)) for m in C_INCLUDE.finditer(text))
    elif lang == "go":
        for m in GO_IMPORT.finditer(text):
            specs = GO_PATH.findall(m.group(1)) if m.group(1) is not None else [m.group(2)]
            for spec in specs:
                found.update(index.go(spec))
    elif lang == "rust":
        found.update(index.rust(path, m.group(1)) for m in RUST_MOD.finditer(text))
    found.discard(None)
    found.discard(index.file(path))
    return found  # type: ignore[return-value]


def scan_code(root: str, show_hidden: bool = False, stop: threading.Event | None = None,
              max_files: int = 600) -> Graph:
    """Source files below `root` as nodes, an edge from each file to each file it
    imports. Node.count = how many files import it (hubs are big), Node.links = all edges."""
    paths, truncated = code_files(root, show_hidden, stop, max_files)
    index = _CodeIndex(root, paths)
    names = [os.path.basename(p) for p in paths]
    repeated = {n for n in names if names.count(n) > 1} if len(names) < 3000 else set()
    nodes = []
    for p, name in zip(paths, names):
        if name in GENERIC_NAMES or name in repeated:
            name = os.path.basename(os.path.dirname(p)) + "/" + name
        nodes.append(Node(name, p, "code", LANGUAGES[os.path.splitext(p)[1].lower()]))
    edges: set[tuple[int, int]] = set()
    for i, p in enumerate(paths):
        if stop and stop.is_set():
            break
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read(300_000)
        except OSError:
            continue
        for t in find_imports(p, text, index):
            edges.add((i, t))
    for a, b in edges:
        nodes[b].count += 1
        nodes[a].links += 1
        nodes[b].links += 1
    return Graph(nodes, sorted(edges), "code", root, truncated)

"""Markdown to the HTML subset QTextBrowser shows, for quick view (Qt-free).

Qt's own Markdown import knows CommonMark and GitHub tables, nothing more. This
renders what notes and md2doc documents (pandoc Markdown) use on top of that:

- YAML front matter as a title block (title, version, date) plus a property table;
- headings with rules and anchors (pandoc and GitHub ids, `{#id}`);
- callouts: a blockquote opening with CRITICAL, WARNING, DECISION, RULE, VERIFY,
  NOTE or TIP (md2doc), or with `[!note]` (GitHub alerts, Obsidian callouts);
- pipe tables with a shaded header, striped rows and cell shading from
  `<!--bg:best-->` or `<span class="bg-warn">` (md2doc's cellcolor filter);
- fenced code with a left bar; ```mermaid and ```{.matplotlib #id caption="…"}
  blocks as figures when diagram() finds an image for them;
- footnotes, task lists, `$math$` (simple TeX as text), ==highlight==, ~~strike~~,
  H~2~O and x^2^, pandoc image attributes, `\\newpage`, Obsidian [[wikilinks]].

render() returns a Page; Page.missing lists the diagrams that had no image, so
the view can render them (render_mermaid) and show the note again.
"""

import hashlib
import html
import json
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import mdnotes

# md2doc's palette (callout-filter.lua, _md2doc_styles.py): keyword -> (bar, tint)
CALLOUTS = {
    "CRITICAL": ("#c62828", "#fdecec"),
    "WARNING": ("#ef6c00", "#fff3e0"),
    "DECISION": ("#6a1b9a", "#f6eefa"),
    "RULE": ("#00695c", "#e6f2f0"),
    "VERIFY": ("#1565c0", "#e8f1fb"),
    "NOTE": ("#455a64", "#eff2f4"),
    "TIP": ("#455a64", "#eff2f4"),
}
QUOTE = ("#002f6c", "#f2f6fb")  # a plain blockquote
# GitHub alerts and Obsidian callout types -> md2doc keyword
ALERTS = {
    "note": "VERIFY", "info": "VERIFY", "todo": "VERIFY", "abstract": "NOTE", "summary": "NOTE",
    "tldr": "NOTE", "tip": "RULE", "hint": "RULE", "success": "RULE", "check": "RULE", "done": "RULE",
    "important": "DECISION", "question": "DECISION", "help": "DECISION", "faq": "DECISION",
    "example": "DECISION", "warning": "WARNING", "caution": "CRITICAL", "attention": "WARNING",
    "danger": "CRITICAL", "error": "CRITICAL", "bug": "CRITICAL", "failure": "CRITICAL",
    "fail": "CRITICAL", "missing": "CRITICAL", "quote": "NOTE", "cite": "NOTE",
}
CELL_COLORS = {"best": "#c8e6c9", "good": "#c8e6c9", "warn": "#ffe0b2", "bad": "#ffcdd2",
               "info": "#bbdefb", "note": "#eceff1"}

# The mermaid theme md2doc ships (mermaid-config.json), used when we render
MERMAID_CONFIG = {
    "theme": "base",
    "themeVariables": {
        "fontFamily": "Arial, Helvetica, sans-serif", "fontSize": "16px",
        "primaryColor": "#eef2f7", "primaryTextColor": "#002f6c", "primaryBorderColor": "#002f6c",
        "secondaryColor": "#f6eefa", "tertiaryColor": "#ffffff", "lineColor": "#41668c",
        "textColor": "#002f6c", "clusterBkg": "#fbfcfe", "clusterBorder": "#9fb3c8",
        "edgeLabelBackground": "#ffffff", "actorBkg": "#eef2f7", "actorBorder": "#002f6c",
        "actorTextColor": "#002f6c", "signalColor": "#41668c", "signalTextColor": "#002f6c",
        "labelBoxBkgColor": "#eef2f7", "labelBoxBorderColor": "#002f6c", "noteBkgColor": "#fff3e0",
        "noteBorderColor": "#ef6c00", "noteTextColor": "#002f6c",
    },
    "flowchart": {"curve": "basis", "nodeSpacing": 40, "rankSpacing": 50, "padding": 10, "useMaxWidth": False},
    "sequence": {"useMaxWidth": False, "wrap": True, "width": 190},
}
MERMAID_SCALE = 2
DIAGRAM_KINDS = ("mermaid", "matplotlib")

FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`]*?)[ \t]*$")
ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
LIST_ITEM = re.compile(r"^( {0,3})([-*+]|\d{1,9}[.)])([ \t]+|$)(.*)$")
BLOCKQUOTE = re.compile(r"^ {0,3}> ?(.*)$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
TABLE_DELIM = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
FOOTNOTE_DEF = re.compile(r"^ {0,3}\[\^([^\]\s]+)\]:[ \t]*(.*)$")
REF_DEF = re.compile(r"^ {0,3}\[([^\]^][^\]]*)\]:[ \t]*<?([^\s>]+)>?(?:[ \t]+[\"'(].*[\"')])?[ \t]*$")
HTML_BLOCK = re.compile(r"^ {0,3}<(!--|/?(?:div|details|summary|table|center|figure|section|pre|dl|hr|br|p|img)\b)",
                        re.IGNORECASE)
PAGE_BREAK = re.compile(r"^ {0,3}\\(?:newpage|pagebreak|clearpage)[ \t]*$")
ATTRS = re.compile(r"\{([^{}]*)\}[ \t]*$")
ALERT = re.compile(r"^\[!([A-Za-z-]+)\][+-]?[ \t]*(.*)$")
TASK = re.compile(r"^\[([ xX])\][ \t]+")
CELL_COMMENT = re.compile(r"<!--\s*bg:([#\w-]+)\s*-->")
CELL_SPAN = re.compile(r"<span\s+class=\"[^\"]*?\bbg-([#\w-]+)[^\"]*\"\s*>(.*?)</span>", re.DOTALL)

INLINE = re.compile(r"""
    (?P<esc>\\[!-/:-@\[-`{-~])
  | (?P<code>(?P<ticks>`+)(?P<codetext>.+?)(?<!`)(?P=ticks)(?!`))
  | (?P<math>\$(?![\s$])(?P<mathtext>(?:[^$\\]|\\.)+?)(?<![\s\\])\$(?!\d))
  | (?P<embed>!\[\[[^\]\n]+\]\])
  | (?P<wiki>\[\[[^\]\n]+\]\])
  | (?P<fnref>\[\^(?P<fnid>[^\]\s]+)\])
  | (?P<img>!\[(?P<alt>(?:[^\[\]]|\[[^\]]*\])*)\]\(\s*(?P<src><[^>]*>|(?:[^()\s]|\([^()\s]*\))*)
        (?:\s+(?:"[^"]*"|'[^']*'))?\s*\)(?:\{[^{}]*\})?)
  | (?P<link>\[(?P<text>(?:[^\[\]]|\[[^\]]*\])*)\]\(\s*(?P<href><[^>]*>|(?:[^()\s]|\([^()\s]*\))*)
        (?:\s+(?:"[^"]*"|'[^']*'))?\s*\)(?:\{[^{}]*\})?)
  | (?P<ref>\[(?P<reftext>(?:[^\[\]]|\[[^\]]*\])+)\](?:\[(?P<refid>[^\]]*)\])?)
  | (?P<auto><(?P<autourl>(?:https?|ftp|mailto):[^>\s]+)>)
  | (?P<comment><!--.*?-->)
  | (?P<tag></?[A-Za-z][\w-]*(?:\s[^<>]*)?/?>)
  | (?P<url>(?<![\w/"'=(])(?:https?://|www\.)[^\s<]*[^\s<.,:;"')\]!?*_])
""", re.VERBOSE | re.DOTALL)

EMPHASIS = [
    (re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*"), r"<b><i>\1</i></b>"),
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), r"<b>\1</b>"),
    (re.compile(r"(?<![\w])__(?=\S)(.+?)(?<=\S)__(?!\w)"), r"<b>\1</b>"),
    (re.compile(r"(?<!\*)\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?!\*)"), r"<i>\1</i>"),
    (re.compile(r"(?<![\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)"), r"<i>\1</i>"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), r"<s>\1</s>"),
    (re.compile(r"(?<!~)~(?=[^\s~])([^\s~]+)~(?!~)"), r"<sub>\1</sub>"),
    (re.compile(r"\^(?=[^\s^])([^\s^]+)\^"), r"<sup>\1</sup>"),
]
HIGHLIGHT = re.compile(r"==(?=\S)(.+?)(?<=\S)==")
SLOT = re.compile("\x00(\\d+)\x00")

TEX_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "le": "≤", "leq": "≤",
    "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠", "approx": "≈", "sim": "∼", "equiv": "≡",
    "propto": "∝", "infty": "∞", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏",
    "int": "∫", "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒",
    "leftrightarrow": "↔", "degree": "°", "circ": "°", "ldots": "…", "cdots": "⋯", "dots": "…",
    "in": "∈", "notin": "∉", "subset": "⊂", "cup": "∪", "cap": "∩", "forall": "∀", "exists": "∃",
    "angle": "∠", "parallel": "∥", "perp": "⊥", "ll": "≪", "gg": "≫", "quad": "\u2003",
    "qquad": "\u2003\u2003", ",": "\u2009", ";": " ", "!": "", " ": " ", "%": "%", "$": "$",
    "{": "{", "}": "}", "_": "_", "#": "#", "&": "&",
}
TEX_TEXT = re.compile(r"\\(?:text|mathrm|textrm|mathit|mathbf|textbf|operatorname|mbox)\s*\{([^{}]*)\}")
TEX_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
TEX_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
TEX_SCRIPT = re.compile(r"([_^])(?:\{([^{}]*)\}|(\\?[A-Za-z0-9]))")
TEX_COMMAND = re.compile(r"\\([A-Za-z]+|.)")


@dataclass
class Style:
    """Colors of a rendered page. style() picks them for a light or dark background."""
    text: str = "#002f6c"
    h1: str = "#002f6c"
    h3: str = "#1565c0"
    h4: str = "#0277bd"
    faint: str = "#5b7190"
    rule: str = "#002f6c"
    soft_rule: str = "#b8c6d9"
    code_bg: str = "#eef2f7"
    code_bar: str = "#002f6c"
    head_bg: str = "#002f6c"
    head_text: str = "#ffffff"
    stripe: str = "#eef2f7"
    border: str = "#9fb3c8"
    mark: str = "#fff59d"
    dark: bool = False
    base: str = "#ffffff"

    def callout(self, kind: str | None) -> tuple[str, str]:
        bar, tint = CALLOUTS.get(kind, QUOTE) if kind else QUOTE
        if self.dark:
            return _blend(bar, "#ffffff", 0.35), _blend(bar, self.base, 0.18)
        return bar, tint

    def cell(self, color: str) -> str:
        return _blend(color, self.base, 0.45) if self.dark else color


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _blend(a: str, b: str, amount: float) -> str:
    """amount of color a over color b."""
    return "#" + "".join(f"{round(x * amount + y * (1 - amount)):02x}" for x, y in zip(_rgb(a), _rgb(b)))


def style(dark: bool = False, base: str = "#ffffff", text: str = "#000000") -> Style:
    if not dark:
        return Style(base=base)
    return Style(text=text, h1="#8ab4f8", h3="#64b5f6", h4="#4fc3f7", faint=_blend(text, base, 0.6),
                 rule="#8ab4f8", soft_rule=_blend("#8ab4f8", base, 0.35), code_bg=_blend(text, base, 0.08),
                 code_bar="#8ab4f8", head_bg="#1e3a5f", head_text="#e8eef7", stripe=_blend(text, base, 0.06),
                 border=_blend(text, base, 0.25), mark="#6b5d00", dark=True, base=base)


@dataclass
class Page:
    html: str
    missing: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, id, code) without an image
    title: str = ""


# A diagram block -> (image path or None, a note shown under it or instead of it)
Finder = Callable[[str, str, str], tuple[str | None, str]]


def _no_diagrams(kind: str, ident: str, code: str) -> tuple[str | None, str]:
    return None, ""


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def parse_attrs(text: str) -> tuple[str, list[str], dict[str, str]]:
    """Pandoc attributes `#id .class key="value"` -> (id, classes, keys)."""
    ident, classes, keys = "", [], {}
    for m in re.finditer(r"""([\w-]+)=("(?:[^"\\]|\\.)*"|'[^']*'|\S+)|#([\w:.-]+)|\.([\w-]+)""", text):
        if m.group(1):
            value = m.group(2)
            if value[:1] in "\"'":
                value = value[1:-1].replace('\\"', '"')
            keys[m.group(1)] = value
        elif m.group(3):
            ident = m.group(3)
        else:
            classes.append(m.group(4))
    return ident, classes, keys


def _fence_info(info: str) -> tuple[str, str, dict[str, str]]:
    """A fence's info string -> (language, id, attributes)."""
    info = info.strip()
    if info.startswith("{") and info.endswith("}"):
        ident, classes, keys = parse_attrs(info[1:-1])
        return (classes[0].lower() if classes else ""), ident, keys
    word = info.split()[0] if info else ""
    rest = ATTRS.search(info)
    ident, _, keys = parse_attrs(rest.group(1)) if rest else ("", [], {})
    return word.strip("{}.").lower(), ident, keys


def slug(text: str) -> str:
    """Pandoc's heading id: lower case, punctuation dropped, spaces to dashes,
    nothing before the first letter."""
    s = re.sub(r"[^\w\s.-]", "", text.lower(), flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"^[^a-z\u00c0-\uffff]+", "", s)
    return s or "section"


def github_slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    return re.sub(r"\s", "-", s.strip())


def tex(text: str) -> str:
    """Simple TeX math as HTML text: Greek letters and symbols, sub- and
    superscripts, fractions, \\text{}. Anything else stays as written."""
    s = text
    for _ in range(3):  # nested braces, inside out
        s = TEX_TEXT.sub(r"\1", s)
        s = TEX_FRAC.sub(lambda m: f"{_group(m.group(1))}/{_group(m.group(2))}", s)
        s = TEX_SQRT.sub(r"√(\1)", s)
    s = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)(?=[()\[\]|.]|\\[{}])\.?", "", s)
    s = _esc(s)
    out = []

    def script(m):
        body = m.group(2) if m.group(2) is not None else m.group(3)
        tag = "sub" if m.group(1) == "_" else "sup"
        out.append(f"<{tag}>{TEX_COMMAND.sub(_tex_symbol, body)}</{tag}>")
        return f"\x01{len(out) - 1}\x01"
    s = TEX_SCRIPT.sub(script, s)
    s = TEX_COMMAND.sub(_tex_symbol, s)
    s = s.replace("{", "").replace("}", "")
    return re.sub("\x01(\\d+)\x01", lambda m: out[int(m.group(1))], s)


def _group(term: str) -> str:
    """A fraction's numerator or denominator, in parentheses when it is a sum."""
    return f"({term})" if re.search(r"[-+ ]|\\cdot|\\times", term.strip()) else term


def _tex_symbol(m: re.Match) -> str:
    name = m.group(1)
    return TEX_SYMBOLS.get(name, m.group(0) if len(name) > 1 else name)


def front_matter(lines: list[str]) -> tuple[list[tuple[str, str]], int]:
    """YAML front matter as (key, value) pairs (lists joined by commas) and the
    number of lines it takes; ([], 0) without one."""
    if not lines or lines[0].strip() != "---":
        return [], 0
    for end in range(1, min(len(lines), 300)):
        if lines[end].strip() in ("---", "..."):
            break
    else:
        return [], 0
    pairs: list[tuple[str, str]] = []
    for line in lines[1:end]:
        if m := re.match(r"^([\w][\w .-]*?)\s*:\s*(.*)$", line):
            value = m.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                value = ", ".join(v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip())
            pairs.append((m.group(1), value.strip("'\"") if value not in ("|", ">") else ""))
        elif (m := re.match(r"^\s+-\s+(.*)$", line)) and pairs:
            key, value = pairs[-1]
            pairs[-1] = (key, f"{value}, {m.group(1).strip()}" if value else m.group(1).strip())
        elif line.startswith((" ", "\t")) and pairs and line.strip():
            key, value = pairs[-1]
            pairs[-1] = (key, f"{value} {line.strip()}".strip())
    return pairs, end + 1


def _split_row(line: str) -> list[str]:
    """A pipe table row -> cells; `\\|` and pipes in code spans don't split."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i, ticks = [], [], 0, 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if c == "`":
            n = len(s[i:]) - len(s[i:].lstrip("`"))
            ticks = 0 if ticks == n else (n if not ticks else ticks)
            cur.append(s[i:i + n])
            i += n
            continue
        if c == "|" and not ticks:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _cell_color(text: str, st: Style) -> tuple[str, str | None]:
    """A table cell's text without its shading tag, and the shade."""
    color = None

    def resolve(name: str) -> str | None:
        name = name.lower()
        if re.fullmatch(r"#[0-9a-f]{6}", name):
            return name
        return CELL_COLORS.get(name)

    def comment(m):
        nonlocal color
        if c := resolve(m.group(1)):
            color = c
            return ""
        return m.group(0)

    def span(m):
        nonlocal color
        if c := resolve(m.group(1)):
            color = c
            return m.group(2)
        return m.group(0)
    text = CELL_SPAN.sub(span, CELL_COMMENT.sub(comment, text))
    return text.strip(), (st.cell(color) if color else None)


def djb2(text: str) -> str:
    """The hash md2doc's Lua filters name their images by."""
    h = 5381
    for b in text.encode("utf-8"):
        h = (h * 33 + b) % 0x100000000
    return f"{h:08x}"


def md2doc_image(kind: str, ident: str, code: str, folder: str) -> tuple[str | None, bool]:
    """The PNG md2doc rendered for a diagram block next to the note
    (diagrams/ for mermaid, plots/ for matplotlib), and whether it is current."""
    code = code.expandtabs(4)
    stem = ident if re.fullmatch(r"[\w-]+", ident or "") else djb2(code)
    path = os.path.join(folder, "diagrams" if kind == "mermaid" else "plots", stem + ".png")
    if not os.path.isfile(path):
        return None, False
    if kind == "mermaid":
        return path, True  # its .hash also covers md2doc's config, which we can't see
    try:
        with open(os.path.join(os.path.dirname(path), stem + ".hash"), encoding="utf-8") as f:
            return path, f.read().strip() == djb2(code)
    except OSError:
        return path, False


def mermaid_key(code: str) -> str:
    return hashlib.sha1((json.dumps(MERMAID_CONFIG, sort_keys=True) + "\0" + code).encode()).hexdigest()[:20]


def render_mermaid(code: str, cache: str, mmdc: str, timeout: float = 90) -> tuple[str | None, str]:
    """Renders a mermaid diagram with mermaid-cli into the cache folder:
    (the PNG path, "") or (None, the error)."""
    os.makedirs(cache, exist_ok=True)
    key = mermaid_key(code)
    out = os.path.join(cache, key + ".png")
    if os.path.isfile(out):
        return out, ""
    config = os.path.join(cache, "config.json")
    source = os.path.join(cache, key + ".mmd")
    tmp = os.path.join(cache, key + ".tmp.png")
    try:
        with open(config, "w", encoding="utf-8") as f:
            json.dump(MERMAID_CONFIG, f)
        with open(source, "w", encoding="utf-8") as f:
            f.write(code)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        run = subprocess.run([mmdc, "-q", "-i", source, "-o", tmp, "-c", config, "-s", str(MERMAID_SCALE),
                              "-b", "white"], capture_output=True, text=True, errors="replace",
                             timeout=timeout, creationflags=flags)
        if run.returncode != 0 or not os.path.isfile(tmp):
            lines = [l for l in (run.stderr or run.stdout or "").splitlines() if l.strip()]
            error = next((l.strip() for l in lines if "Error" in l), lines[0].strip() if lines else "failed")
            return None, error[:200]
        os.replace(tmp, out)
        return out, ""
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)[:200]
    finally:
        for p in (source, tmp):
            try:
                os.remove(p)
            except OSError:
                pass


def png_size(path: str) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def _length_px(value: str) -> int | None:
    """A pandoc width ("4in", "300px", "8cm") in pixels at 96 dpi."""
    m = re.fullmatch(r"\s*([\d.]+)\s*(in|cm|mm|px|pt)?\s*", value or "")
    if not m:
        return None
    n = float(m.group(1))
    return round(n * {"in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4, "pt": 96 / 72}.get(m.group(2) or "px", 1))


class _Renderer:
    def __init__(self, st: Style, diagram: Finder, max_width: int):
        self.st = st
        self.diagram = diagram
        self.max_width = max_width
        self.refs: dict[str, str] = {}
        self.footnotes: dict[str, list[str]] = {}
        self.fn_order: list[str] = []
        self.figures = 0
        self.missing: list[tuple[str, str, str]] = []
        self.ids: set[str] = set()

    # -- blocks --

    def collect_defs(self, lines: list[str]) -> list[str]:
        """Takes out footnote and link reference definitions (outside code)."""
        out, fence, i = [], None, 0
        while i < len(lines):
            line = lines[i]
            m = FENCE.match(line)
            if fence:
                if m and m.group(2)[0] == fence[0] and len(m.group(2)) >= len(fence) and not m.group(3):
                    fence = None
            elif m:
                fence = m.group(2)
            elif m := FOOTNOTE_DEF.match(line):
                body = [m.group(2)]
                i += 1
                while i < len(lines) and (lines[i].startswith(("    ", "\t")) or
                                          (not lines[i].strip() and i + 1 < len(lines)
                                           and lines[i + 1].startswith(("    ", "\t")))):
                    body.append(lines[i][4:] if lines[i].startswith("    ") else lines[i].lstrip("\t"))
                    i += 1
                self.footnotes.setdefault(m.group(1), body)
                continue
            elif (m := REF_DEF.match(line)) and not line.lstrip().startswith("[!"):
                self.refs.setdefault(m.group(1).strip().lower(), m.group(2))
                i += 1
                continue
            out.append(line)
            i += 1
        return out

    def starts_block(self, line: str) -> bool:
        return bool(ATX.match(line) or FENCE.match(line) or HR.match(line) or BLOCKQUOTE.match(line)
                    or HTML_BLOCK.match(line) or PAGE_BREAK.match(line)
                    or ((m := LIST_ITEM.match(line)) and m.group(4).strip()
                        and (m.group(2)[0] in "-*+" or m.group(2)[:-1] == "1")))

    def blocks(self, lines: list[str], tight: bool = False) -> str:
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            if m := FENCE.match(line):
                i = self.fenced(lines, i, m, out)
                continue
            if m := ATX.match(line):
                out.append(self.heading(len(m.group(1)), m.group(2) or ""))
                i += 1
                continue
            if PAGE_BREAK.match(line):
                out.append(self.rule(dashed=True))
                i += 1
                continue
            if HR.match(line):
                out.append(self.rule())
                i += 1
                continue
            if line.strip().startswith("$$"):
                i = self.display_math(lines, i, out)
                continue
            if BLOCKQUOTE.match(line):
                i = self.blockquote(lines, i, out)
                continue
            if LIST_ITEM.match(line) and not (HR.match(line)):
                i = self.list_block(lines, i, out)
                continue
            if HTML_BLOCK.match(line):
                i = self.html_block(lines, i, out)
                continue
            if line.startswith(("    ", "\t")):
                code = []
                while i < n and (lines[i].startswith(("    ", "\t")) or not lines[i].strip()):
                    code.append(lines[i][4:] if lines[i].startswith("    ") else lines[i][1:])
                    i += 1
                while code and not code[-1].strip():
                    code.pop()
                out.append(self.code_block("\n".join(code)))
                continue
            if "|" in line and i + 1 < n and "|" in lines[i + 1] and TABLE_DELIM.match(lines[i + 1]):
                i = self.table(lines, i, out)
                continue
            # a paragraph, or a setext heading
            para = [line]
            i += 1
            while i < n and lines[i].strip():
                if m := SETEXT.match(lines[i]):
                    out.append(self.heading(1 if m.group(1)[0] == "=" else 2, " ".join(p.strip() for p in para)))
                    para = []
                    i += 1
                    break
                if self.starts_block(lines[i]) or ("|" in lines[i] and i + 1 < n
                                                   and TABLE_DELIM.match(lines[i + 1]) and "|" in lines[i + 1]):
                    break
                para.append(lines[i])
                i += 1
            if para:
                out.append(self.paragraph(para, tight))
        return "\n".join(out)

    def paragraph(self, lines: list[str], tight: bool = False) -> str:
        text = "\n".join(l.strip() if k else l.lstrip() for k, l in enumerate(lines))
        text = re.sub(r"[ \t]+$", "", text) if not text.endswith("\\") else text
        figure = re.fullmatch(r"!\[((?:[^\[\]]|\[[^\]]*\])+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)(\{[^{}]*\})?", text)
        if figure:
            width = None
            if figure.group(3):
                width = _length_px(parse_attrs(figure.group(3)[1:-1])[2].get("width", ""))
            return self.figure(self.link_target(figure.group(2)), figure.group(1), width, "")
        body = self.inline(text)
        if tight:
            return body
        return f"<p>{body}</p>"

    def heading(self, level: int, text: str) -> str:
        text = text.strip()
        ident = ""
        if m := ATTRS.search(text):
            ident = parse_attrs(m.group(1))[0]
            text = text[:m.start()].rstrip()
        plain = re.sub(r"[*_`~]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", text)
        names = [ident or slug(plain), github_slug(plain)]
        anchors = ""
        for name in dict.fromkeys(names):
            unique, k = name, 1
            while unique in self.ids:
                unique, k = f"{name}-{k}", k + 1
            self.ids.add(unique)
            anchors += f'<a name="{_attr(unique)}"></a>'
        st = self.st
        color = st.h1 if level <= 2 else st.h3 if level == 3 else st.h4
        body = f'<h{level} style="color:{color}">{anchors}{self.inline(text)}</h{level}>'
        if level > 2:
            return body
        rule = f"2px solid {st.rule}" if level == 1 else f"1px solid {st.soft_rule}"
        return (f'<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse; '
                f'margin-top:{18 if level == 1 else 14}px; margin-bottom:6px"><tr>'
                f'<td style="border-bottom:{rule}; padding-bottom:2px">{body}</td></tr></table>')

    def rule(self, dashed: bool = False) -> str:
        style = "dashed" if dashed else "solid"
        return (f'<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse; '
                f'margin-top:10px; margin-bottom:10px"><tr><td style="border-top:1px {style} '
                f'{self.st.soft_rule}"></td></tr></table>')

    def fenced(self, lines: list[str], i: int, m: re.Match, out: list[str]) -> int:
        indent, fence = len(m.group(1)), m.group(2)
        lang, ident, keys = _fence_info(m.group(3))
        code = []
        i += 1
        while i < len(lines):
            end = FENCE.match(lines[i])
            if end and end.group(2)[0] == fence[0] and len(end.group(2)) >= len(fence) and not end.group(3):
                i += 1
                break
            line = lines[i]
            strip = min(indent, len(line) - len(line.lstrip(" ")))
            code.append(line[strip:])
            i += 1
        text = "\n".join(code)
        if lang in DIAGRAM_KINDS:
            out.append(self.diagram_block(lang, ident, keys, text))
        else:
            out.append(self.code_block(text, lang))
        return i

    def code_block(self, code: str, lang: str = "", note: str = "") -> str:
        st = self.st
        label = (f'<p style="margin:0 0 4px 0; color:{st.faint}; font-size:small">{_esc(note or lang)}</p>'
                 if note or lang in DIAGRAM_KINDS else "")
        return (f'<table width="100%" cellspacing="0" cellpadding="6" style="border-collapse:collapse; '
                f'margin-top:4px; margin-bottom:8px"><tr><td bgcolor="{st.code_bg}" '
                f'style="border-left:3px solid {st.code_bar}">{label}<pre style="margin:0">{_esc(code)}</pre>'
                f'</td></tr></table>')

    def diagram_block(self, kind: str, ident: str, keys: dict[str, str], code: str) -> str:
        path, note = self.diagram(kind, ident, code)
        caption = keys.get("caption", "")
        if not path:
            self.missing.append((kind, ident, code))
            return self.code_block(code, kind, note or f"{kind} (not rendered)")
        width = _length_px(keys.get("width", ""))
        if width is None and (size := png_size(path)):
            width = size[0] // MERMAID_SCALE
        return self.figure(Path(path).as_uri(), caption, width, note, inline_caption=True)

    def figure(self, src: str, caption: str, width: int | None, note: str, inline_caption: bool = False) -> str:
        width = min(width, self.max_width) if width else None
        size = f' width="{width}"' if width else ""
        out = f'<p align="center" style="margin-top:8px; margin-bottom:2px"><img src="{_attr(src)}"{size}></p>'
        text = ""
        if caption:
            self.figures += 1
            text = f"Figure {self.figures}: {self.inline(caption)}"
        if note:
            text += f'{"  " if text else ""}<span style="color:{self.st.faint}">({_esc(note)})</span>'
        if text:
            out += f'<p align="center" style="margin-top:2px; margin-bottom:10px"><i>{text}</i></p>'
        return out

    def display_math(self, lines: list[str], i: int, out: list[str]) -> int:
        first = lines[i].strip()[2:]
        body = []
        if first.rstrip().endswith("$$"):
            body.append(first.rstrip()[:-2])
            i += 1
        else:
            body.append(first)
            i += 1
            while i < len(lines):
                line = lines[i].rstrip()
                i += 1
                if line.endswith("$$"):
                    body.append(line[:-2])
                    break
                body.append(line)
        text = " ".join(b.strip() for b in body if b.strip())
        out.append(f'<p align="center"><i>{tex(text)}</i></p>')
        return i

    def blockquote(self, lines: list[str], i: int, out: list[str]) -> int:
        inner = []
        while i < len(lines):
            line = lines[i]
            if m := BLOCKQUOTE.match(line):
                inner.append(m.group(1))
            elif line.strip() and inner and inner[-1].strip() and not self.starts_block(line):
                inner.append(line)  # lazy continuation
            else:
                break
            i += 1
        kind, title = None, ""
        first = next((k for k, l in enumerate(inner) if l.strip()), None)
        if first is not None:
            if m := ALERT.match(inner[first].strip()):
                name = m.group(1).lower()
                kind = ALERTS.get(name, "NOTE")
                title = m.group(2).strip() or name.capitalize()
                inner = inner[:first] + inner[first + 1:]
            elif (m := re.match(r"\s*[*_]*([A-Z]+)", inner[first])) and m.group(1) in CALLOUTS:
                kind = m.group(1)
        bar, tint = self.st.callout(kind)
        body = self.blocks(inner)
        if title:
            body = f'<p style="margin-bottom:4px"><b style="color:{bar}">{self.inline(title)}</b></p>' + body
        elif kind:  # md2doc tints the leading bold label in the callout's color
            body = re.sub(r"^<p><b>", f'<p><b style="color:{bar}">', body, count=1)
        out.append(f'<table width="100%" cellspacing="0" cellpadding="8" style="border-collapse:collapse; '
                   f'margin-top:4px; margin-bottom:10px"><tr><td bgcolor="{tint}" '
                   f'style="border-left:4px solid {bar}">{body}</td></tr></table>')
        return i

    def list_block(self, lines: list[str], i: int, out: list[str]) -> int:
        first = LIST_ITEM.match(lines[i])
        assert first
        ordered = first.group(2)[0] not in "-*+"
        start = int(first.group(2)[:-1]) if ordered else 1
        base = len(first.group(1))

        def sibling(line: str) -> bool:
            m = LIST_ITEM.match(line)
            return bool(m and len(m.group(1)) <= base + 3 and not HR.match(line)
                        and m.group(2)[-1] == first.group(2)[-1] and (m.group(2)[0] not in "-*+") == ordered)
        items: list[list[str]] = []
        loose = False
        n = len(lines)
        while i < n and sibling(lines[i]):
            m = LIST_ITEM.match(lines[i])
            assert m
            spaces = len(m.group(3))
            content = len(m.group(1)) + len(m.group(2)) + (spaces if 0 < spaces <= 4 else 1)
            item = [m.group(4) if spaces <= 4 else " " * (spaces - 1) + m.group(4)]
            i += 1
            while i < n:
                line = lines[i]
                if not line.strip():
                    nxt = next((k for k in range(i + 1, n) if lines[k].strip()), n)
                    if nxt < n and len(lines[nxt]) - len(lines[nxt].lstrip(" ")) >= content:
                        item.append("")  # the item goes on after a blank line
                        loose = True
                        i += 1
                        continue
                    if nxt < n and sibling(lines[nxt]):
                        loose = True
                        i = nxt
                    break
                ind = len(line) - len(line.lstrip(" "))
                if ind < content and sibling(line):
                    break
                if ind >= content:
                    item.append(line[content:])
                elif ind > base and LIST_ITEM.match(line):
                    item.append(line[ind:])  # a nested list indented less than the text
                elif not self.starts_block(line) and item[-1].strip():
                    item.append(line.strip())  # lazy continuation
                else:
                    break
                i += 1
            items.append(item)
        tasks = all(TASK.match(item[0]) for item in items) if items else False
        tag = "ol" if ordered else "ul"
        attrs = f' start="{start}"' if ordered and start != 1 else ""
        if tasks:
            attrs += ' style="list-style-type:none"'
        parts = []
        for item in items:
            box = ""
            if t := TASK.match(item[0]):
                box = "☑ " if t.group(1).lower() == "x" else "☐ "
                item[0] = item[0][t.end():]
            body = self.blocks(item, tight=not loose)
            if box:
                body = body.replace("<p>", f"<p>{box}", 1) if body.startswith("<p>") else box + body
            parts.append(f"<li>{body}</li>")
        out.append(f"<{tag}{attrs}>{''.join(parts)}</{tag}>")
        return i

    def html_block(self, lines: list[str], i: int, out: list[str]) -> int:
        chunk = []
        if lines[i].lstrip().startswith("<!--"):
            while i < len(lines):
                chunk.append(lines[i])
                i += 1
                if "-->" in chunk[-1]:
                    break
            return i  # comments show nothing
        while i < len(lines) and lines[i].strip():
            chunk.append(lines[i])
            i += 1
        out.append(re.sub(r"<!--.*?-->", "", "\n".join(chunk), flags=re.DOTALL))
        return i

    def table(self, lines: list[str], i: int, out: list[str]) -> int:
        st = self.st
        head = _split_row(lines[i])
        aligns = []
        for spec in _split_row(lines[i + 1]):
            spec = spec.strip()
            aligns.append("center" if spec.startswith(":") and spec.endswith(":") else
                          "right" if spec.endswith(":") else "left")
        i += 2
        rows = []
        while i < len(lines) and lines[i].strip() and ("|" in lines[i]) and not self.starts_block(lines[i]):
            rows.append(_split_row(lines[i]))
            i += 1
        cols = len(head)
        border = f"1px solid {st.border}"

        def cell(tag: str, text: str, c: int, bg: str | None, color: str | None) -> str:
            text, shade = _cell_color(text, st)
            bg = shade or bg
            align = aligns[c] if c < len(aligns) else "left"
            style = f"border:{border}; padding:4px 6px"
            if color and not shade:
                style += f"; color:{color}"
            body = self.inline(text) or "&nbsp;"
            if tag == "th":
                body = f"<b>{body}</b>"
            shade_attr = f' bgcolor="{bg}"' if bg else ""
            return f'<td align="{align}" valign="top"{shade_attr} style="{style}">{body}</td>'
        html_rows = ["<tr>" + "".join(cell("th", head[c], c, st.head_bg, st.head_text) for c in range(cols)) + "</tr>"]
        for r, row in enumerate(rows):
            row = (row + [""] * cols)[:cols]
            bg = st.stripe if r % 2 else None
            html_rows.append("<tr>" + "".join(cell("td", row[c], c, bg, None) for c in range(cols)) + "</tr>")
        out.append(f'<table cellspacing="0" cellpadding="4" style="border-collapse:collapse; margin-top:6px; '
                   f'margin-bottom:10px">{"".join(html_rows)}</table>')
        return i

    # -- inline --

    def link_target(self, url: str) -> str:
        url = url.strip()
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]
        return url.replace(" ", "%20")

    def inline(self, text: str) -> str:
        slots: list[str] = []

        def keep(s: str) -> str:
            slots.append(s)
            return f"\x00{len(slots) - 1}\x00"
        text = re.sub(r"(?: {2,}|\\)\n", "\x02", text).replace("\n", " ")
        st = self.st
        parts = []
        pos = 0
        for m in INLINE.finditer(text):
            parts.append(_esc(text[pos:m.start()]))
            pos = m.end()
            kind = next(k for k in ("esc", "code", "math", "embed", "wiki", "fnref", "img", "link", "ref", "auto",
                                    "comment", "tag", "url") if m.group(k) is not None)
            if kind == "esc":
                parts.append(keep(_esc(m.group(0)[1])))
            elif kind == "code":
                body = m.group("codetext")
                if len(body) > 1 and body[0] == " " and body[-1] == " " and body.strip():
                    body = body[1:-1]
                parts.append(keep(f'<code style="background-color:{st.code_bg}">'
                                  f'{_esc(body).replace(" ", "&nbsp;")}</code>'))
            elif kind == "math":
                parts.append(keep(f"<i>{tex(m.group('mathtext'))}</i>"))
            elif kind == "embed":
                inner = m.group(0)[3:-2]
                target = inner.split("|")[0].split("#")[0].strip()
                parts.append(keep(f'<img src="{_attr(mdnotes._link(target))}" alt="{_attr(target)}">'))
            elif kind == "wiki":
                w = mdnotes.WIKI.match(m.group(0))
                target = w.group(1).strip()
                label = (w.group(2) or target.split("#")[0] or target).strip()
                parts.append(keep(f'<a href="{_attr(mdnotes._link(target))}">{_esc(label)}</a>'))
            elif kind == "fnref":
                ident = m.group("fnid")
                if ident in self.footnotes:
                    if ident not in self.fn_order:
                        self.fn_order.append(ident)
                    k = self.fn_order.index(ident) + 1
                    parts.append(keep(f'<sup><a href="#fn-{_attr(ident)}">{k}</a></sup>'))
                else:
                    parts.append(keep(_esc(m.group(0))))
            elif kind == "img":
                src = self.link_target(m.group("src"))
                parts.append(keep(f'<img src="{_attr(src)}" alt="{_attr(m.group("alt"))}">'))
            elif kind == "link":
                href = self.link_target(m.group("href"))
                parts.append(keep(f'<a href="{_attr(href)}">{self.inline(m.group("text"))}</a>'))
            elif kind == "ref":
                ref = (m.group("refid") or m.group("reftext")).strip().lower()
                if ref in self.refs:
                    parts.append(keep(f'<a href="{_attr(self.refs[ref])}">{self.inline(m.group("reftext"))}</a>'))
                else:  # plain brackets: keep them, render what is inside
                    parts.append(keep("[" + self.inline(m.group("reftext")) + "]" +
                                      (f"[{_esc(m.group('refid'))}]" if m.group("refid") is not None else "")))
            elif kind == "auto":
                url = m.group("autourl")
                shown = url[7:] if url.startswith("mailto:") else url
                parts.append(keep(f'<a href="{_attr(url)}">{_esc(shown)}</a>'))
            elif kind == "url":
                url = m.group(0)
                href = url if "://" in url else "https://" + url
                parts.append(keep(f'<a href="{_attr(href)}">{_esc(url)}</a>'))
            elif kind == "tag":
                tag = m.group(0)
                if span := re.match(r"<span\s+class=\"[^\"]*?\bbg-([#\w-]+)[^\"]*\"\s*>", tag):
                    name = span.group(1).lower()
                    color = name if re.fullmatch(r"#[0-9a-f]{6}", name) else CELL_COLORS.get(name)
                    tag = f'<span style="background-color:{st.cell(color)}">' if color else "<span>"
                parts.append(keep(tag))
            # comments show nothing
        parts.append(_esc(text[pos:]))
        s = "".join(parts)
        for pattern, repl in EMPHASIS:
            s = pattern.sub(repl, s)
        s = HIGHLIGHT.sub(f'<span style="background-color:{st.mark}">\\1</span>', s)
        s = SLOT.sub(lambda m: slots[int(m.group(1))], s)
        return s.replace("\x02", "<br>")

    def footnote_list(self) -> str:
        if not self.fn_order:
            return ""
        items = []
        k = 0
        while k < len(self.fn_order):  # a footnote may refer to another one
            ident = self.fn_order[k]
            body = self.blocks(self.footnotes[ident], tight=True)
            items.append(f'<li><a name="fn-{_attr(ident)}"></a>{body}</li>')
            k += 1
        return self.rule() + f'<ol style="font-size:small">{"".join(items)}</ol>'


def _front_block(pairs: list[tuple[str, str]], r: _Renderer) -> tuple[str, str]:
    """The title block made from front matter, and the title."""
    st = r.st
    values = {k.lower(): v for k, v in pairs}
    title = values.get("title", "")
    out = []
    if title:
        out.append(r.heading(1, title))
    meta = []
    if values.get("version"):
        meta.append(f"Version {_esc(values['version'])}")
    for key in ("author", "date"):
        if values.get(key):
            meta.append(_esc(values[key]))
    if meta:
        out.append(f'<p style="color:{st.faint}">{" · ".join(meta)}</p>')
    rest = [(k, v) for k, v in pairs if k.lower() not in ("title", "version", "author", "date") and v]
    if rest:
        rows = "".join(f'<tr><td style="color:{st.faint}; padding:1px 12px 1px 0">{_esc(k)}</td>'
                       f'<td style="padding:1px 0">{r.inline(v)}</td></tr>' for k, v in rest)
        out.append(f'<table cellspacing="0" cellpadding="0" style="margin-bottom:10px; font-size:small">{rows}</table>')
    return "\n".join(out), title


def render(text: str, st: Style | None = None, diagram: Finder | None = None, max_width: int = 800) -> Page:
    st = st or style()
    r = _Renderer(st, diagram or _no_diagrams, max_width)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    pairs, skip = front_matter(lines)
    head, title = _front_block(pairs, r) if pairs else ("", "")
    lines = r.collect_defs(lines[skip:])
    body = r.blocks(lines)
    css = (f"body {{ color:{st.text} }} p {{ margin-top:0px; margin-bottom:8px }} "
           f"h1 {{ font-size:x-large; margin:0px }} h2 {{ font-size:large; margin:0px }} "
           f"h3 {{ font-size:medium; margin-top:12px; margin-bottom:4px }} "
           f"h4, h5, h6 {{ font-size:medium; font-weight:normal; margin-top:10px; margin-bottom:4px }} "
           f"li {{ margin-bottom:2px }} ul, ol {{ margin-top:0px; margin-bottom:8px }}")
    page = f"<html><head><style>{css}</style></head><body>{head}{body}{r.footnote_list()}</body></html>"
    return Page(page, r.missing, title)

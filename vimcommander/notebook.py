"""Jupyter notebooks (.ipynb) for the quick view, no Qt: the cells as one Markdown
text (code in fenced blocks, each output below its cell in a block marked
"output") and the images the notebook shows, which the text refers to as
nbimage:N.
"""

import base64
import binascii
import json
import re
from dataclasses import dataclass

IMAGE_LIMIT = 40  # images shown; later ones are only mentioned
TEXT_LIMIT = 60_000  # characters of cells shown: a longer text takes Qt long to lay out
OUTPUT_LINES = 40  # lines shown of each text output
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")  # colors in tracebacks
_ATTACHMENT = re.compile(r"\(attachment:([^)\s]+)\)")  # an image pasted into a Markdown cell
_NO_ALT = re.compile(r"!\[\]\(")  # ![](x): Qt leaves out an image with no alt text


@dataclass
class Notebook:
    markdown: str
    images: list[bytes]  # PNG / JPEG data; the text shows images[N] as nbimage:N
    summary: str  # kernel and counts, for the quick view's title


def is_notebook(path: str) -> bool:
    return path.lower().endswith(".ipynb")


def _text(value) -> str:
    """Notebook text is a string or a list of lines."""
    return "".join(str(v) for v in value) if isinstance(value, list) else str(value or "")


def _fence(text: str, info: str) -> str:
    """A fenced block that backticks inside it can't end."""
    longest = max((len(m) for m in re.findall(r"`{3,}", text)), default=2)
    ticks = "`" * (longest + 1)
    return f"{ticks}{info}\n{text.rstrip()}\n{ticks}"


def _clip(text: str) -> str:
    lines = _ANSI.sub("", text).rstrip().split("\n")
    if len(lines) > OUTPUT_LINES:
        lines = lines[:OUTPUT_LINES] + [f"… {len(lines) - OUTPUT_LINES} more lines"]
    return "\n".join(lines)


def convert(data: bytes) -> Notebook:
    """The notebook in `data` (the .ipynb file); ValueError if it isn't one."""
    try:
        nb = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not a notebook: {e}") from None
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise ValueError("not a notebook (or an old version 3 one)")
    meta = nb.get("metadata") or {}
    kernel = meta.get("kernelspec") or {}
    language = kernel.get("language") or (meta.get("language_info") or {}).get("name") or ""
    images: list[bytes] = []
    skipped = 0

    def add(encoded) -> str | None:
        """Keep an image (base64 text); its nbimage: URL, or None."""
        nonlocal skipped
        if len(images) >= IMAGE_LIMIT:
            skipped += 1
            return None
        try:
            images.append(base64.b64decode(_text(encoded)))
        except (binascii.Error, ValueError):
            return None
        return f"nbimage:{len(images) - 1}"

    def image(bundle: dict) -> str | None:
        encoded = bundle.get("image/png") or bundle.get("image/jpeg")
        if not encoded:
            return None
        url = add(encoded)
        return f"![image]({url})" if url else "*(image)*"  # Qt drops images without alt text

    def attachment(files: dict, match: re.Match) -> str:
        """(attachment:name.png) in a Markdown cell -> (nbimage:N)."""
        bundle = files.get(match.group(1)) or {}
        encoded = bundle.get("image/png") or bundle.get("image/jpeg") or bundle.get("image/gif")
        url = add(encoded) if encoded else None
        return f"({url})" if url else match.group(0)

    cells = [c for c in nb["cells"] if isinstance(c, dict)]
    kinds = [str(c.get("cell_type")) for c in cells]
    parts, cut, after_text = [], 0, True
    for n, cell in enumerate(cells):
        if sum(map(len, parts)) > TEXT_LIMIT:
            cut = n
            parts.append(f"*… {len(cells) - n} more cells: open the notebook to see them*")
            break
        kind, source = kinds[n], _text(cell.get("source"))
        if kind == "markdown":
            files = cell.get("attachments") or {}
            source = _ATTACHMENT.sub(lambda m: attachment(files, m), source)
            parts.append(_NO_ALT.sub("![image](", source))
            after_text = True
            continue
        if source.strip():
            if not after_text:
                parts.append("&nbsp;")  # an empty line between two code cells
            parts.append(_fence(source, language if kind == "code" else "output"))
        after_text = False
        for out in cell.get("outputs") or []:
            bundle = out.get("data") or {}  # mime type -> content
            what = out.get("output_type")
            if what == "stream":
                parts.append(_fence(_clip(_text(out.get("text"))), "output"))
            elif what in ("execute_result", "display_data"):
                if shown := image(bundle):
                    parts.append(shown)
                elif "text/markdown" in bundle:
                    parts.append(_text(bundle["text/markdown"]))
                elif "text/plain" in bundle:
                    parts.append(_fence(_clip(_text(bundle["text/plain"])), "output"))
            elif what == "error":
                trace = "\n".join(_text(t) for t in out.get("traceback") or [])
                parts.append(_fence(_clip(trace or f"{out.get('ename')}: {out.get('evalue')}"), "output"))
    shown = len(images) + skipped
    summary = (f"{kernel.get('display_name') or language or 'notebook'}   {len(cells)} cells "
               f"({kinds.count('code')} code, {kinds.count('markdown')} Markdown)"
               + (f", the first {cut} shown" if cut else "")
               + (f"   {shown} image(s)" if shown else "")
               + (f", the first {IMAGE_LIMIT} shown" if skipped else ""))
    return Notebook("\n\n".join(parts), images, summary)

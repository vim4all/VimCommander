"""JSON, JSON Lines and YAML documents for the quick view's tree (Qt-free).

`load()` parses a file's bytes into plain Python values (YAML needs PyYAML; a
file with several YAML documents, or JSON Lines, becomes a list of them).
`children()` lists a container's entries for one tree level, capped at
`MAX_CHILDREN` so a huge array stays quick to open; `scalar()` and
`container_label()` give the text of a row and `path()` the jq-style path of
an entry (`.items[3].name`, `.a["x-y"]`), which the tree copies with `y`.
"""

import json
import re

try:
    import yaml
except ImportError:  # YAML files then show as text
    yaml = None

JSON_EXTS = (".json", ".geojson", ".jsonl", ".ndjson")
YAML_EXTS = (".yaml", ".yml")
MAX_CHILDREN = 2000  # entries shown per object or array; the rest are counted in one row
SCALAR_CHARS = 300  # a longer string is cut in its row (y copies the path, Y the whole value)
_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class More:
    """The row standing for the entries past MAX_CHILDREN."""

    def __init__(self, count: int):
        self.count = count


def kind(path: str) -> str | None:
    """"json", "jsonl" or "yaml" by the file name, else None."""
    low = path.lower()
    if low.endswith((".jsonl", ".ndjson")):
        return "jsonl"
    if low.endswith(JSON_EXTS):
        return "json"
    if low.endswith(YAML_EXTS):
        return "yaml"
    return None


def load(data: bytes, how: str):
    """The document in `data`; ValueError (with a short reason) if it doesn't parse."""
    text = data.decode("utf-8-sig", errors="replace")
    if how == "jsonl":
        rows = []
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError as e:
                    raise ValueError(f"line {n}: {e}") from None
        return rows
    if how == "yaml":
        if yaml is None:
            raise ValueError("PyYAML isn't installed")
        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            where = f"line {mark.line + 1}: " if mark else ""
            raise ValueError(f"{where}{getattr(e, 'problem', None) or e}") from None
        return docs[0] if len(docs) == 1 else docs
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"line {e.lineno}: {e.msg}") from None


def is_container(value) -> bool:
    return isinstance(value, (dict, list))


def children(value) -> list:
    """(key, child) pairs of an object or array, at most MAX_CHILDREN, then
    (None, More(rest)) if there are more."""
    pairs = value.items() if isinstance(value, dict) else enumerate(value)
    out = []
    for i, pair in enumerate(pairs):
        if i == MAX_CHILDREN:
            out.append((None, More(len(value) - MAX_CHILDREN)))
            break
        out.append(pair)
    return out


def container_label(value) -> str:
    """`{3}` for an object of 3 keys, `[12]` for an array of 12 items."""
    return f"{{{len(value)}}}" if isinstance(value, dict) else f"[{len(value)}]"


def scalar(value) -> str:
    """A leaf's text as JSON writes it (strings quoted), cut at SCALAR_CHARS."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):  # YAML dates, sets, binary…
        text = str(value)
    return text if len(text) <= SCALAR_CHARS else text[:SCALAR_CHARS] + "…"


def kind_of(value) -> str:
    """The type name shown in the third column."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def path(keys: list) -> str:
    """jq's path to an entry: keys from the top, ints for array items
    (`.items[3].name`, `.[0]`, `.["a key"]`, `.a["x-y"]`)."""
    out = []
    for key in keys:
        if isinstance(key, str) and _PLAIN_KEY.match(key):
            out.append(f".{key}")
            continue
        index = key if type(key) is int else json.dumps(str(key), ensure_ascii=False)
        out.append(f"{'' if out else '.'}[{index}]")
    return "".join(out) or "."


def dump(value) -> str:
    """The whole value as indented JSON (Y copies it)."""
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def summary(value, how: str) -> str:
    """One line for the title: `JSON object, 12 keys`."""
    name = {"json": "JSON", "jsonl": "JSON Lines", "yaml": "YAML"}[how]
    if isinstance(value, dict):
        return f"{name} object, {len(value)} key{'s' * (len(value) != 1)}"
    if isinstance(value, list):
        noun = "line" if how == "jsonl" else "item"
        return f"{name}, {len(value)} {noun}{'s' * (len(value) != 1)}"
    return f"{name} {kind_of(value)}"

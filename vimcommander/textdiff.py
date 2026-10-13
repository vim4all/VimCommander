"""Side-by-side diff of two texts, for :diffthis (gd). Pure Python, no Qt.

align() lines up the two files row by row, the way vimdiff shows them: equal
lines side by side, changed lines next to each other, and an empty filler row
opposite a line that only one side has.
"""

import difflib
from dataclasses import dataclass


@dataclass
class Row:
    kind: str  # "same" | "change" | "delete" (left only) | "insert" (right only)
    left_no: int | None  # 1-based line numbers; None on a filler row
    left: str
    right_no: int | None
    right: str


def _key(line: str, ignore_space: bool) -> str:
    return " ".join(line.split()) if ignore_space else line


def align(a: list[str], b: list[str], ignore_space: bool = False) -> list[Row]:
    ka, kb = [_key(x, ignore_space) for x in a], [_key(x, ignore_space) for x in b]
    rows: list[Row] = []
    matcher = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            rows += [Row("same", i + 1, a[i], j + 1, b[j]) for i, j in zip(range(i1, i2), range(j1, j2))]
            continue
        n = max(i2 - i1, j2 - j1)
        for k in range(n):
            i, j = i1 + k, j1 + k
            has_a, has_b = i < i2, j < j2
            kind = "change" if has_a and has_b else "delete" if has_a else "insert"
            rows.append(Row(kind, i + 1 if has_a else None, a[i] if has_a else "",
                            j + 1 if has_b else None, b[j] if has_b else ""))
    return rows


def hunks(rows: list[Row]) -> list[int]:
    """The row where each run of differences starts."""
    starts = []
    for i, row in enumerate(rows):
        if row.kind != "same" and (i == 0 or rows[i - 1].kind == "same"):
            starts.append(i)
    return starts


def changed_spans(a: str, b: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """The parts of two changed lines that differ: (spans in a, spans in b).
    Empty when the lines have little in common (the whole line is then marked)."""
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if matcher.ratio() < 0.3:
        return [], []
    left, right = [], []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "equal":
            if i2 > i1:
                left.append((i1, i2))
            if j2 > j1:
                right.append((j1, j2))
    return left, right


def summary(rows: list[Row]) -> tuple[int, int, int]:
    """(changed, deleted, inserted) line counts."""
    return (sum(r.kind == "change" for r in rows), sum(r.kind == "delete" for r in rows),
            sum(r.kind == "insert" for r in rows))

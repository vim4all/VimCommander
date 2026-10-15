"""CSV / TSV parsing for the quick view: the delimiter is guessed, and each
column gets a short summary (numbers: min, max, mean; text: distinct values).
Pure Python, no Qt.
"""

import csv
import io
import math
from dataclasses import dataclass

EXTENSIONS = (".csv", ".tsv", ".tab", ".psv")
ROW_LIMIT = 5000  # rows parsed for the preview


def is_table(path: str) -> bool:
    return path.lower().endswith(EXTENSIONS)


def _number(text: str) -> float | None:
    text = text.strip().replace(" ", "").replace(" ", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        try:  # a decimal comma, as in many European exports
            value = float(text.replace(",", "."))
        except ValueError:
            return None
    return value if math.isfinite(value) else None


@dataclass
class Table:
    header: list[str]
    rows: list[list[str]]
    delimiter: str
    complete: bool  # False if only the first ROW_LIMIT rows (or the first part of the file) were read


def guess_delimiter(sample: str, path: str = "") -> str:
    if path.lower().endswith((".tsv", ".tab")):
        return "\t"
    try:
        return csv.Sniffer().sniff(sample[:64 * 1024], delimiters=",;\t|").delimiter
    except csv.Error:
        lines = sample.splitlines()[:20]
        return max(",;\t|", key=lambda d: sum(line.count(d) for line in lines))


def parse(text: str, path: str = "", truncated: bool = False) -> Table:
    """The table in `text` (the first part of the file if `truncated`)."""
    text = text.lstrip("﻿")
    delimiter = guess_delimiter(text, path)
    if truncated:  # drop the last line, it may be cut in half
        text = text[:text.rfind("\n") + 1] if "\n" in text else text
    rows: list[list[str]] = []
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    complete = not truncated
    try:
        for row in reader:
            if len(rows) > ROW_LIMIT:
                complete = False
                break
            if row:
                rows.append(row)
    except csv.Error:
        complete = False
    if not rows:
        return Table([], [], delimiter, complete)
    header, rows = rows[0], rows[1:]
    if not _has_header(header, rows):
        rows.insert(0, header)
        header = [str(i + 1) for i in range(len(header))]
    width = max([len(header)] + [len(r) for r in rows])
    header += [str(i + 1) for i in range(len(header), width)]
    return Table(header, rows, delimiter, complete)


def _has_header(first: list[str], rows: list[list[str]]) -> bool:
    """A first row is a header unless it looks like data: a number where the column has numbers."""
    for i, cell in enumerate(first):
        if _number(cell) is not None and any(i < len(r) and _number(r[i]) is not None for r in rows[:20]):
            return False
    return True


def column_stats(table: Table, column: int) -> str:
    """One line describing a column."""
    values = [r[column] if column < len(r) else "" for r in table.rows]
    empty = sum(1 for v in values if not v.strip())
    numbers = [n for v in values if (n := _number(v)) is not None]
    filled = len(values) - empty
    parts = []
    if numbers and len(numbers) >= filled * 0.9:
        mean = sum(numbers) / len(numbers)
        parts.append(f"numbers: min {_fmt(min(numbers))}, max {_fmt(max(numbers))}, mean {_fmt(mean)}")
        if len(numbers) < filled:
            parts.append(f"{filled - len(numbers)} not numbers")
    else:
        distinct = len(set(values) - {""})
        parts.append(f"text: {distinct} distinct value(s)")
    if empty:
        parts.append(f"{empty} empty")
    return ", ".join(parts)


def _fmt(x: float) -> str:
    return str(int(x)) if x == int(x) and abs(x) < 1e15 else f"{x:.6g}"

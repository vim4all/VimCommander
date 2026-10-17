"""CSV / TSV parsing for the quick view: the delimiter is guessed, and each
column gets a short summary (numbers: min, max, mean; text: distinct values).
read_columns() reads a whole file's numeric columns for the plot (zp).
Pure Python, no Qt.
"""

import csv
import io
import itertools
import math
import operator
import re
from array import array
from dataclasses import dataclass

EXTENSIONS = (".csv", ".tsv", ".tab", ".psv")
ROW_LIMIT = 5000  # rows parsed for the preview
PLOT_CELLS = 16_000_000  # numbers read for a plot (8 bytes each)
HEAD_ROWS = 200  # rows that decide which columns are numeric and what they are called
NAN = float("nan")
_TIME = re.compile(r"time|^t\b|^x\b|^sec|second", re.IGNORECASE)  # an x axis column's name or unit


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


# The plot (zp): every numeric column of the whole file

@dataclass
class Columns:
    """A table's numeric columns: values[i] is the column names[i], all of the same
    length (NaN where a row has no number in that column)."""
    names: list[str]
    units: list[str]
    values: list[array]
    x: int | None  # the column that is the x axis (rising, like time); None = the row number
    gaps: list[bool]  # the column has NaNs
    complete: bool  # False if only the first PLOT_CELLS numbers were read

    @property
    def rows(self) -> int:
        return len(self.values[0]) if self.values else 0


def read_columns(source: str | bytes, path: str = "", stop=None) -> Columns | None:
    """The numeric columns of a CSV file (a path, or the file's bytes), None if it
    has fewer than two rows of numbers (or `stop`, a threading.Event, is set).
    Columns are those holding numbers in at least half of the first rows; rows
    above the first numbers (a header, or a scope's settings) give each column
    its name and unit."""
    with io.BytesIO(source) if isinstance(source, bytes) else open(source, "rb") as raw:
        sample = raw.read(64 * 1024).decode("utf-8", errors="replace").lstrip("\ufeff")
        delimiter = guess_delimiter(sample, path or (source if isinstance(source, str) else ""))
        raw.seek(0)
        reader = csv.reader(_lines(raw), delimiter=delimiter)
        try:
            head = list(itertools.islice(reader, HEAD_ROWS))
        except csv.Error:
            return None
        counts = [0] * max((len(r) for r in head), default=0)
        with_numbers = 0  # rows holding any number: a header or text lines don't count
        for row in head:
            found = [i for i, cell in enumerate(row) if _number(cell) is not None]
            with_numbers += bool(found)
            for i in found:
                counts[i] += 1
        live = [i for i, c in enumerate(counts) if c >= 2 and 2 * c >= with_numbers]
        if not live:
            return None
        start = next((k for k, row in enumerate(head)
                      if 2 * sum(1 for i in live if i < len(row) and _number(row[i]) is not None) >= len(live)), 0)
        # Names come from the first row above the numbers that labels most columns
        # (a title line or a scope's settings don't), units from the row right after it
        labelled = [k for k, row in enumerate(head[:start])
                    if 2 * sum(1 for i in live if _label(row, i)) > len(live)]
        name_row = head[labelled[0]] if labelled else []
        unit_row = head[labelled[0] + 1] if len(labelled) > 1 and labelled[1] == labelled[0] + 1 else []
        names = [_label(name_row, i) or f"column {i + 1}" for i in live]
        units = [_label(unit_row, i) for i in live]
        n = len(live)
        pick = operator.itemgetter(*live) if n > 1 else (lambda row, i=live[0]: (row[i],))
        flat, count, complete = array("d"), 0, True
        try:
            for row in itertools.chain(head[start:], reader):
                try:
                    flat.extend(map(float, pick(row)))  # the fast way, for plain numbers
                except (ValueError, IndexError):
                    del flat[count * n:]  # what the failed row added
                    numbers = [_number(row[i]) if i < len(row) else None for i in live]
                    if all(v is None for v in numbers):
                        continue  # a blank or text line
                    flat.extend(NAN if v is None else v for v in numbers)
                count += 1
                if count * n >= PLOT_CELLS:
                    complete = False
                    break
                if not count & 0xFFFF and stop is not None and stop.is_set():
                    return None
        except csv.Error:
            complete = False
    if count < 2:
        return None
    values = [flat[i::n] for i in range(n)]
    del flat
    gaps = []
    for col in values:
        if any(map(math.isinf, col)):  # "inf", 1e999: no place on a plot
            for k in [k for k, v in enumerate(col) if math.isinf(v)]:
                col[k] = NAN
        gaps.append(any(map(math.isnan, col)))
    x = _x_column(names, units, values, gaps) if n > 1 else None  # one column: plot it over the rows
    return Columns(names, units, values, x, gaps, complete)


def _lines(raw, block: int = 8 << 20):
    """The lines of a binary file, decoded in big blocks: twice as fast for the csv
    module as a text stream."""
    def blocks():
        rest, first = b"", True
        while data := raw.read(block):
            data = rest + data
            cut = data.rfind(b"\n") + 1  # a newline is never inside a UTF-8 character
            rest = data[cut:]
            if cut:
                text = data[:cut].decode("utf-8", errors="replace")
                yield (text.lstrip("\ufeff") if first else text).split("\n")
                first = False
        if rest:
            yield rest.decode("utf-8", errors="replace").lstrip("\ufeff" if first else "").split("\n")
    return itertools.chain.from_iterable(blocks())


_EXCEL_ERROR = re.compile(r"^#(N/A|[\w/]+[!?])$")  # #VALUE!, #DIV/0!, #N/A, #ЗНАЧ!…


def _label(row: list[str], i: int) -> str:
    """The cell as a column's name or unit: text that isn't a number, an Excel
    error or garbled (a legacy code page read as UTF-8); "" otherwise."""
    cell = row[i].strip() if i < len(row) else ""
    if not cell or _number(cell) is not None or "�" in cell or _EXCEL_ERROR.match(cell):
        return ""
    return cell


def _rising(col: array) -> bool:
    return col[0] < col[-1] and all(map(operator.le, col, itertools.islice(col, 1, None)))


def _x_column(names: list[str], units: list[str], values: list[array], gaps: list[bool]) -> int | None:
    """A rising column named like time, else a rising first column, else None (the row number)."""
    for k, (name, unit) in enumerate(zip(names, units)):
        if not gaps[k] and _TIME.search(f"{name} {unit}".strip()) and _rising(values[k]):
            return k
    return 0 if not gaps[0] and _rising(values[0]) else None


def stats(col: array, lo: int, hi: int, gaps: bool = False) -> tuple[float, float, float, float] | None:
    """(min, max, mean, RMS) of col[lo:hi], NaNs left out; None if nothing is there."""
    seg = col[lo:hi]
    if gaps:
        seg = array("d", [v for v in seg if v == v])
    if not seg:
        return None
    squares = math.sumprod(seg, seg) if hasattr(math, "sumprod") else math.fsum(v * v for v in seg)
    return min(seg), max(seg), math.fsum(seg) / len(seg), math.sqrt(squares / len(seg))


_PREFIXES = {-15: "f", -12: "p", -9: "n", -6: "µ", -3: "m", 0: "", 3: "k", 6: "M", 9: "G", 12: "T"}


def si(value: float, digits: int = 4) -> str:
    """A number for a plot, with `digits` significant digits and an SI prefix when it
    is very small or big (0.0012 -> 1.2m, 2.5e-06 -> 2.5µ, 12288 -> 12.29k, 0.25 -> 0.25)."""
    if not math.isfinite(value):
        return "–" if value != value else str(value)
    size = abs(value)
    if size == 0:
        return "0"
    if not 1e-15 <= size < 1e15:
        return f"{value:.{digits}g}"
    exp = 0 if 0.01 <= size < 10000 else math.floor(math.log10(size) / 3) * 3
    for _ in range(2):
        scaled = value / 10.0 ** exp
        # never fewer digits than the whole part has (500, not 5e+02); one more if rounding adds one
        places = max(digits, math.floor(math.log10(abs(scaled))) + 1)
        text = f"{scaled:.{places}g}"
        if "e" in text:
            text = f"{scaled:.{places + 1}g}"
        if exp and exp < 12 and abs(float(text)) >= 1000:  # 999.97k rounds to 1000k: 1M
            exp += 3
            continue
        break
    return text + _PREFIXES[exp]


def ticks(lo: float, hi: float, count: int) -> tuple[list[float], int]:
    """About `count` round values between lo and hi (1, 2 or 5 × 10^n apart), and the
    significant digits si() needs to tell them apart."""
    if not hi > lo or count < 1:
        return [], 4
    raw = (hi - lo) / count
    magnitude = 10.0 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw * 0.999)
    first = math.ceil(lo / step) * step
    values = []
    while len(values) < 100 and (v := first + len(values) * step) <= hi + step * 1e-6:
        values.append(0.0 if abs(v) < step * 1e-6 else v)
    top = max(abs(lo), abs(hi))
    digits = math.floor(math.log10(top)) - math.floor(math.log10(step)) + 1 if top > 0 else 1
    return values, max(1, min(12, digits))

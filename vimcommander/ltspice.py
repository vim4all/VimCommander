"""LTspice waveform files (.raw), no Qt: the header and the traces, as the
tabular.Columns the plot view draws.

A .raw file is a text header (UTF-16 in LTspice XVII and later, else ASCII),
then "Binary:" and the samples, or "Values:" and the samples as text.
Transient results store time as a double (its sign is a flag: the value is
abs(time)) and every other trace as a float32, unless the flags say "double";
"fastaccess" stores trace after trace instead of point after point; AC results
("complex") store every value as two doubles, shown here as magnitude in dB.
Other programs use .raw for images and such: those aren't LTspice files.
"""

import math
import re
import struct
from array import array
from dataclasses import dataclass

from .tabular import Columns, NAN, _rising

LIMIT = 1 << 30  # bytes read
UNITS = {"time": "s", "frequency": "Hz", "voltage": "V", "device_current": "A", "current": "A",
         "subckt_current": "A"}


@dataclass
class Header:
    plot: str  # "Transient Analysis", "AC Analysis", "Operating Point"…
    flags: set[str]
    names: list[str]
    kinds: list[str]  # voltage, device_current, time…
    points: int
    data: int  # where the samples start
    binary: bool


def is_raw(path: str) -> bool:
    return path.lower().endswith(".raw")


def read_header(data: bytes) -> Header | None:
    """The header, None if this isn't an LTspice (SPICE) raw file."""
    wide = data[1:2] == b"\0"
    encoding = "utf-16-le" if wide else "latin-1"
    width = 2 if wide else 1
    for marker, binary in (("Binary:\n", True), ("Values:\n", False)):
        at = data.find(marker.encode(encoding))
        if at >= 0:
            break
    else:
        return None
    text = data[:at].decode(encoding, errors="replace")
    if not text.startswith("Title:") or "Variables:" not in text:
        return None
    fields = dict(re.findall(r"^([A-Z][\w. ]*?):[ \t]*(.*)$", text, re.MULTILINE))
    try:
        count, points = int(fields["No. Variables"]), int(fields["No. Points"])
    except (KeyError, ValueError):
        return None
    names, kinds = [], []
    for line in text.split("Variables:", 1)[1].splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            names.append(parts[1])
            kinds.append(parts[2] if len(parts) > 2 else "")
    if len(names) != count or count < 1:
        return None
    return Header(fields.get("Plotname", ""), set(fields.get("Flags", "").lower().split()), names, kinds,
                  points, at + len(marker) * width, binary)


def read_raw(source: str | bytes, path: str = "", stop=None) -> Columns | None:
    """The traces of an LTspice .raw file (a path or its bytes), None if it isn't one
    or holds fewer than two points (an operating point: see operating_point()).
    `path` and `stop` are unused: the arguments match tabular.read_columns()."""
    if isinstance(source, bytes):
        data = source
    else:
        with open(source, "rb") as f:
            data = f.read(LIMIT)
    head = read_header(data)
    if head is None or head.points < 2:
        return None
    values = _samples(head, data)
    if values is None:
        return None
    units = [UNITS.get(k, "") for k in head.kinds]
    names = list(head.names)
    if "complex" in head.flags:
        names = [names[0]] + [f"|{n}| dB" for n in names[1:]]
        units = [units[0]] + ["dB"] * (len(names) - 1)
    gaps = [any(map(math.isnan, col)) for col in values]
    x = 0 if not gaps[0] and _rising(values[0]) else None  # a stepped run starts over: row numbers
    return Columns(names, units, values, x, gaps, len(values[0]) == head.points)


def _samples(head: Header, data: bytes) -> list[array] | None:
    n, points, body = len(head.names), head.points, data[head.data:]
    if not head.binary:
        return _text_samples(head, body.decode("utf-16-le" if data[1:2] == b"\0" else "latin-1", errors="replace"))
    if "complex" in head.flags:  # every value: real, imaginary (doubles)
        flat = array("d")
        flat.frombytes(body[:len(body) // 16 * 16])
        points = min(points, len(flat) // (2 * n))
        if "fastaccess" in head.flags:
            pairs = [(flat[2 * i * points:2 * (i + 1) * points:2], flat[2 * i * points + 1:2 * (i + 1) * points:2])
                     for i in range(n)]
        else:
            pairs = [(flat[2 * i::2 * n][:points], flat[2 * i + 1::2 * n][:points]) for i in range(n)]
        return [pairs[0][0]] + [array("d", (20 * math.log10(math.hypot(r, j)) if r or j else NAN
                                            for r, j in zip(real, imag))) for real, imag in pairs[1:]]
    double = "double" in head.flags
    size = 8 if double else 4
    record = 8 + (n - 1) * size
    points = min(points, len(body) // record)
    if "fastaccess" in head.flags:  # the time column, then each trace in turn
        x = array("d")
        x.frombytes(body[:8 * points])
        cols, at = [x], 8 * points
        for _ in range(n - 1):
            col = array("d" if double else "f")
            col.frombytes(body[at:at + size * points])
            cols.append(col if double else array("d", col))
            at += size * points
    else:
        flat = array("d")
        fmt = struct.Struct("<d" + ("d" if double else "f") * (n - 1))
        for row in fmt.iter_unpack(body[:record * points]):
            flat.extend(row)
        cols = [flat[i::n] for i in range(n)]
    cols[0] = array("d", map(abs, cols[0]))  # a negative time only flags a step change
    return cols


def _text_samples(head: Header, text: str) -> list[array] | None:
    """The "Values:" form: a point number, then one value per line (re,im for AC)."""
    n = len(head.names)
    numbers = []
    for line in text.split("\n"):
        parts = line.split()
        if not parts:
            continue
        value = parts[-1].split(",")[0]  # AC: "re,im": the real part
        try:
            numbers.append(float(value))
        except ValueError:
            return None
    points = len(numbers) // n
    if points < 2:
        return None
    return [array("d", numbers[i:points * n:n]) for i in range(n)]


def operating_point(data: bytes) -> list[tuple[str, float, str]] | None:
    """(name, value, unit) of a one-point .raw file (.op), None if it isn't one."""
    head = read_header(data)
    if head is None or head.points != 1:
        return None
    body, n = data[head.data:], len(head.names)
    try:
        if not head.binary:
            text = body.decode("utf-16-le" if data[1:2] == b"\0" else "latin-1", errors="replace")
            values = [float(line.split()[-1].split(",")[0]) for line in text.splitlines() if line.split()][:n]
        elif "double" in head.flags or "complex" in head.flags:
            values = list(struct.unpack_from(f"<{n}d", body))
        else:
            values = list(struct.unpack_from("<d" + "f" * (n - 1), body))
    except (struct.error, ValueError, IndexError):
        return None
    return [(name, value, UNITS.get(kind, "")) for name, value, kind in zip(head.names, values, head.kinds)]

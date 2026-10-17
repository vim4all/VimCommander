"""Firmware memory maps, no Qt: what fills the Flash and RAM of a microcontroller.

Reads three kinds of files into the same shape (regions, each with a tree of
what uses it):

- GNU ld .map files (arm-none-eabi-gcc: STM32CubeIDE, Atmel Studio, ESP-IDF,
  avr-gcc...): the "Memory Configuration" table gives the regions; each output
  section (.text, .data...) goes to the region holding its address, and .data
  also to the region of its load address (its initial values sit in Flash).
  Below each section: object files, below them their input sections
  (.text.HAL_Init, i.e. functions with -ffunction-sections).
- Keil MDK .map files: load / execution regions and the "Image component sizes"
  table (Code, RO, RW, ZI per object file).
- ELF images (.elf, .axf): allocated sections, Flash = sections with contents,
  RAM = writable ones, and the symbol table's functions and variables. A .map
  next to it (same name) is used instead, as it knows the object files.
"""

import os
import re
import struct
from dataclasses import dataclass, field

LIMIT = 96 * 1024 * 1024  # bigger files aren't parsed
EXTENSIONS = (".map", ".elf", ".axf", ".out")


@dataclass
class Node:
    name: str
    size: int = 0
    kind: str = ""  # top-level sections: "code" | "rodata" | "data" | "bss" | "stack" | "vectors" | "other"
    children: list["Node"] = field(default_factory=list)

    def child(self, name: str, kind: str = "") -> "Node":
        for c in self.children:
            if c.name == name:
                return c
        c = Node(name, 0, kind)
        self.children.append(c)
        return c

    def finish(self) -> int:
        """Add up sizes from the leaves and sort children, biggest first."""
        if self.children:
            self.size = sum(c.finish() for c in self.children)
            self.children.sort(key=lambda c: (-c.size, c.name))
        return self.size


@dataclass
class Region:
    name: str
    origin: int
    length: int  # 0 = unknown (ELF files don't say)
    kind: str  # "flash" | "ram" | "other"
    tree: Node = field(default_factory=lambda: Node(""))

    @property
    def used(self) -> int:
        return self.tree.size

    @property
    def fraction(self) -> float | None:
        return self.used / self.length if self.length else None


@dataclass
class MemMap:
    source: str  # "GNU ld map" | "Keil map" | "ELF"
    regions: list[Region]
    path: str = ""


def is_candidate(path: str) -> bool:
    return path.lower().endswith(EXTENSIONS)


def section_kind(name: str) -> str:
    n = name.lower()
    if "heap" in n or "stack" in n:
        return "stack"
    if "vector" in n or n.startswith(".isr"):
        return "vectors"
    if "bss" in n or "noinit" in n or n.endswith("zi") or "zi data" in n or "common" in n:
        return "bss"
    if "rodata" in n or n.startswith(".ro") or "ro data" in n or "const" in n:
        return "rodata"
    if "data" in n or "rw" in n:
        return "data"
    if "text" in n or "code" in n or "init" in n or "fini" in n or ".arm" in n or "flash" in n:
        return "code"
    return "other"


def _region_kind(name: str, attrs: str = "") -> str:
    n = name.lower()
    if "ram" in n or n in ("data", "dram", "sram") or "dram" in n or "iram" in n:
        return "ram"
    if "flash" in n or "rom" in n or n == "text" or "irom" in n or "drom" in n:
        return "flash"
    a = attrs.lower().lstrip("!")
    if "w" in a:
        return "ram"
    if "x" in a or "r" in a:
        return "flash"
    return "other"


def object_name(path: str) -> str:
    """'d:/x/libc_nano.a(lib_a-strlen.o)' -> 'libc_nano.a(lib_a-strlen.o)', 'Core/Src/main.o' -> 'main.o'."""
    path = path.strip()
    if path.endswith(")") and "(" in path:
        lib, _, member = path.partition("(")
        return re.split(r"[\\/]", lib)[-1] + "(" + member
    return re.split(r"[\\/]", path)[-1] or path


def parse(path: str) -> MemMap | None:
    """The memory map in `path`, or None if it isn't one. Raises OSError."""
    lower = path.lower()
    if lower.endswith((".elf", ".axf", ".out")):
        stem = os.path.splitext(path)[0]
        for candidate in (stem + ".map", stem + ".MAP"):
            if os.path.isfile(candidate):
                result = parse(candidate)
                if result:
                    return result
        with open(path, "rb") as f:
            data = f.read(LIMIT)
        result = parse_elf(data)
        if result:
            result.path = path
        return result
    if os.path.getsize(path) > LIMIT:
        return None
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", errors="replace")
    result = parse_gnu(text) if "Linker script and memory map" in text else (
        parse_keil(text) if "Image component sizes" in text else None)
    if result:
        result.path = path
    return result


# GNU ld

_HEX = r"0x([0-9a-fA-F]+)"
_OUT = re.compile(rf"^(\.\S*|[A-Za-z_]\S*)\s+{_HEX}\s+{_HEX}(?:\s+load address\s+{_HEX})?\s*$")
_OUT_NAME = re.compile(r"^(\.\S+)\s*$")
_OUT_CONT = re.compile(rf"^\s+{_HEX}\s+{_HEX}(?:\s+load address\s+{_HEX})?\s*$")
_IN = re.compile(rf"^ (\S+)\s+{_HEX}\s+{_HEX}\s*(.*)$")
_IN_NAME = re.compile(r"^ (\S+)\s*$")
_IN_CONT = re.compile(rf"^\s+{_HEX}\s+{_HEX}\s+(\S.*)$")
_REGION = re.compile(rf"^(\S+)\s+{_HEX}\s+{_HEX}(?:\s+(\S+))?\s*$")
_NOT_LOADED = (".debug", ".comment", ".ARM.attributes", ".stab", ".note", ".xt.", ".riscv.attributes")


def parse_gnu(text: str) -> MemMap | None:
    lines = text.splitlines()
    regions: list[Region] = []
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("Memory Configuration"):
            for row in lines[i + 1:]:
                if row.startswith("Linker script and memory map"):
                    break
                m = _REGION.match(row)
                if m and m.group(1) not in ("Name", "*default*"):
                    regions.append(Region(m.group(1), int(m.group(2), 16), int(m.group(3), 16),
                                          _region_kind(m.group(1), m.group(4) or "")))
        if line.startswith("Linker script and memory map"):
            start = i + 1
            break
    if not start:
        return None

    def region_of(addr: int) -> Region | None:
        for r in regions:
            if r.origin <= addr < r.origin + r.length:
                return r
        return None

    sections: list[tuple[str, int, int, int | None, list[tuple[str, str, int]]]] = []
    current: list[tuple[str, str, int]] | None = None
    pending_out: str | None = None
    pending_in: str | None = None
    for line in lines[start:]:
        if not line.strip():
            continue
        if pending_out is not None:
            m = _OUT_CONT.match(line)
            name, pending_out = pending_out, None
            if m:
                current = []
                lma = int(m.group(3), 16) if m.group(3) else None
                sections.append((name, int(m.group(1), 16), int(m.group(2), 16), lma, current))
                continue
        if not line[0].isspace():
            current = None
            pending_in = None
            if m := _OUT.match(line):
                current = []
                lma = int(m.group(4), 16) if m.group(4) else None
                sections.append((m.group(1), int(m.group(2), 16), int(m.group(3), 16), lma, current))
            elif m := _OUT_NAME.match(line):
                pending_out = m.group(1)
            continue
        if current is None:
            continue
        if pending_in is not None:
            m = _IN_CONT.match(line)
            name, pending_in = pending_in, None
            if m:
                current.append((name, m.group(3).strip(), int(m.group(2), 16)))
                continue
        if m := _IN.match(line):
            name, size, rest = m.group(1), int(m.group(3), 16), m.group(4).strip()
            if name == "*fill*":
                current.append(("*fill*", "", size))
            elif not name.startswith("*(") and rest and not rest.startswith("0x"):
                current.append((name, rest, size))
        elif m := _IN_NAME.match(line):
            if not m.group(1).startswith(("*", "KEEP", "SORT", "EXCLUDE", "PROVIDE")):
                pending_in = m.group(1)

    if not regions:  # no MEMORY in the linker script: one region for everything loaded
        regions.append(Region("memory", 0, 0, "other"))
    used = False
    for name, addr, size, lma, items in sections:
        if size <= 0 or name.startswith(_NOT_LOADED) or name == "/DISCARD/":
            continue
        home = region_of(addr) if regions[0].length else regions[0]
        if home is None:
            continue
        used = True
        kind = section_kind(name)
        _add_section(home.tree, name, kind, size, items)
        load = region_of(lma) if lma is not None and regions[0].length else None
        if load is not None and load is not home:
            _add_section(load.tree, f"{name} (initial values)", kind, size, items)
    if not used:
        return None
    for r in regions:
        r.tree.finish()
    return MemMap("GNU ld map", [r for r in regions if r.used or r.kind in ("flash", "ram")])


def _add_section(tree: Node, name: str, kind: str, size: int, items: list[tuple[str, str, int]]) -> None:
    section = tree.child(name, kind)
    counted = 0
    for in_name, obj, in_size in items:
        if in_size <= 0:
            continue
        counted += in_size
        if in_name == "*fill*":
            what = "(reserved: heap / stack)" if kind == "stack" else "(padding)"
            section.child(what, kind).child(what, kind).size += in_size
            continue
        leaf = in_name
        for prefix in (name + ".", ".text.", ".rodata.", ".data.", ".bss.", ".literal."):
            if leaf.startswith(prefix) and len(leaf) > len(prefix):
                leaf = leaf[len(prefix):]
                break
        section.child(object_name(obj), kind).child(leaf, kind).size += in_size
    if size > counted:  # linker-made space: heap and stack reservations, alignment
        what = "(reserved: heap / stack)" if kind == "stack" else "(other)"
        section.child(what, kind).child(what, kind).size += size - counted


# Keil MDK (armlink)

_KEIL_REGION = re.compile(r"(Load|Execution) Region (\S+) \(Base: 0x([0-9a-fA-F]+), Size: 0x([0-9a-fA-F]+),"
                          r" Max: 0x([0-9a-fA-F]+)")
_KEIL_ROW = re.compile(r"^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S.*?)\s*$")


def parse_keil(text: str) -> MemMap | None:
    loads: list[tuple[str, int, int, int]] = []
    execs: list[tuple[str, int, int, int]] = []
    for m in _KEIL_REGION.finditer(text):
        row = (m.group(2), int(m.group(3), 16), int(m.group(4), 16), int(m.group(5), 16))
        (loads if m.group(1) == "Load" else execs).append(row)
    rows: list[tuple[int, int, int, int, str]] = []
    table = ""
    for line in text[text.find("Image component sizes"):].splitlines():
        if "Object Name" in line or "Library Member Name" in line:
            table = "use"
        elif "Library Name" in line:
            table = "skip"
        elif "Grand Totals" in line:
            break
        elif table == "use" and (m := _KEIL_ROW.match(line)):
            name = m.group(7)
            if "Totals" not in name and not name.startswith("(incl."):
                code, ro, rw, zi = (int(m.group(k)) for k in (1, 3, 4, 5))
                rows.append((code, ro, rw, zi, name))
    if not rows:
        return None
    flash = Region(loads[0][0] if loads else "ROM", loads[0][1] if loads else 0,
                   loads[0][3] if loads else 0, "flash")
    ram_regions = [e for e in execs if not any(lb <= e[1] < lb + lmax for _, lb, _, lmax in loads)]
    ram = Region(ram_regions[0][0] if ram_regions else "RAM", ram_regions[0][1] if ram_regions else 0,
                 ram_regions[0][3] if ram_regions else 0, "ram")
    for code, ro, rw, zi, name in rows:
        for tree, part, kind, size in ((flash.tree, "Code", "code", code), (flash.tree, "RO data", "rodata", ro),
                                       (flash.tree, "RW data (initial values)", "data", rw),
                                       (ram.tree, "RW data", "data", rw), (ram.tree, "ZI data", "bss", zi)):
            if size:
                tree.child(part, kind).child(name, kind).size += size
    flash.tree.finish()
    ram.tree.finish()
    return MemMap("Keil map", [flash, ram])


# ELF

SHF_WRITE, SHF_ALLOC = 0x1, 0x2
SHT_NOBITS, SHT_SYMTAB = 8, 2


def parse_elf(data: bytes) -> MemMap | None:
    if data[:4] != b"\x7fELF" or len(data) < 52:
        return None
    is64, end = data[4] == 2, "<" if data[5] == 1 else ">"
    try:
        if is64:
            shoff, = struct.unpack_from(end + "Q", data, 0x28)
            shentsize, shnum, shstrndx = struct.unpack_from(end + "HHH", data, 0x3A)
        else:
            shoff, = struct.unpack_from(end + "I", data, 0x20)
            shentsize, shnum, shstrndx = struct.unpack_from(end + "HHH", data, 0x2E)
        sections = []
        for i in range(shnum):
            off = shoff + i * shentsize
            if is64:
                name, kind, flags, addr, offset, size, link = struct.unpack_from(end + "IIQQQQI", data, off)
            else:
                name, kind, flags, addr, offset, size, link = struct.unpack_from(end + "IIIIIII", data, off)
            sections.append((name, kind, flags, addr, offset, size, link))
        names_at = sections[shstrndx][4]
    except (struct.error, IndexError):
        return None

    def cstr(offset: int) -> str:
        stop = data.find(b"\0", offset)
        return data[offset:stop if stop >= 0 else len(data)].decode("utf-8", errors="replace")

    named = [(cstr(names_at + s[0]), *s[1:]) for s in sections]
    symbols: dict[int, list[tuple[str, int]]] = {}
    for name, kind, flags, addr, offset, size, link in named:
        if kind != SHT_SYMTAB or link >= len(named):
            continue
        strtab = named[link][4]
        entsize = 24 if is64 else 16
        for off in range(offset, min(offset + size, len(data)) - entsize + 1, entsize):
            if is64:
                st_name, info, _, shndx, _, st_size = struct.unpack_from(end + "IBBHQQ", data, off)
            else:
                st_name, _, st_size, info, _, shndx = struct.unpack_from(end + "IIIBBH", data, off)
            if st_size and (info & 0xF) in (1, 2) and 0 < shndx < len(named):  # OBJECT, FUNC
                symbols.setdefault(shndx, []).append((cstr(strtab + st_name), st_size))
    flash, ram = Region("Flash", 0, 0, "flash"), Region("RAM", 0, 0, "ram")
    # Program headers tell RAM from Flash: a segment copied at start-up (load
    # address != run address, like .data) or without file contents (.bss) is RAM;
    # one that runs where it is loaded (.text, and .init_array though writable) is Flash.
    segments = []
    try:
        if is64:
            phoff, = struct.unpack_from(end + "Q", data, 0x20)
            phentsize, phnum = struct.unpack_from(end + "HH", data, 0x36)
        else:
            phoff, = struct.unpack_from(end + "I", data, 0x1C)
            phentsize, phnum = struct.unpack_from(end + "HH", data, 0x2A)
        for i in range(phnum):
            off = phoff + i * phentsize
            if is64:
                p_type, _, _, vaddr, paddr, filesz, memsz = struct.unpack_from(end + "IIQQQQQ", data, off)
            else:
                p_type, _, vaddr, paddr, filesz, memsz = struct.unpack_from(end + "IIIIII", data, off)
            if p_type == 1:  # PT_LOAD
                segments.append((vaddr, memsz, paddr != vaddr, filesz == 0))
    except struct.error:
        segments = []
    # Without a segment copied at start-up it isn't a firmware image (a PC program):
    # then writable sections are the RAM.
    firmware = any(copied for _, _, copied, _ in segments)

    def runs_in_ram(addr: int, flags: int) -> bool:
        if firmware:
            for vaddr, memsz, copied, empty in segments:
                if vaddr <= addr < vaddr + max(memsz, 1):
                    return copied or empty
        return bool(flags & SHF_WRITE)
    for index, (name, kind, flags, addr, offset, size, link) in enumerate(named):
        if not flags & SHF_ALLOC or size <= 0:
            continue
        skind = section_kind(name)
        in_ram = runs_in_ram(addr, flags)
        targets = []
        if kind != SHT_NOBITS:
            targets.append((flash, f"{name} (initial values)" if in_ram else name))
        if in_ram:
            targets.append((ram, name))
        for region, label in targets:
            section = region.tree.child(label, skind)
            counted = 0
            for sym, sym_size in sorted(symbols.get(index, []), key=lambda s: -s[1]):
                if counted + sym_size > size:
                    continue  # aliases (two names for one function) would count twice
                section.child(sym, skind).size += sym_size
                counted += sym_size
            if size > counted:
                section.child("(no symbol)", skind).size += size - counted
    flash.tree.finish()
    ram.tree.finish()
    regions = [r for r in (flash, ram) if r.used]
    return MemMap("ELF", regions) if regions else None


def human(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    size = float(n)
    for unit in ("K", "M", "G"):
        size /= 1024
        if size < 1024 or unit == "G":
            return f"{size:.1f} {unit}"
    return ""

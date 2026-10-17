import os
import shutil
import subprocess
import tempfile
import unittest

from vimcommander import memmap

GNU = """\
Archive member included to satisfy reference by file (symbol)

Memory Configuration

Name             Origin             Length             Attributes
RAM              0x0000000020000000 0x0000000000005000 xrw
FLASH            0x0000000008000000 0x0000000000010000 xr
*default*        0x0000000000000000 0xffffffffffffffff

Linker script and memory map

LOAD Core/Src/main.o
.isr_vector     0x0000000008000000      0x100
 .isr_vector    0x0000000008000000      0x100 Core/Startup/startup.o
                0x0000000008000000                g_pfnVectors

.text           0x0000000008000100      0x300
 *(.text*)
 .text.main     0x0000000008000100      0x200 Core/Src/main.o
                0x0000000008000100                main
 .text.a_function_with_a_very_long_name
                0x0000000008000300       0xfe d:/gcc/lib\\libc_nano.a(lib_a-memcpy.o)
 *fill*         0x00000000080003fe        0x2 

.data           0x0000000020000000       0x10 load address 0x0000000008000400
 .data.counter  0x0000000020000000       0x10 Core/Src/main.o

.bss            0x0000000020000010       0x40
 COMMON         0x0000000020000010       0x40 Core/Src/main.o

._user_heap_stack
                0x0000000020000050      0x600
 *fill*         0x0000000020000050      0x600 

.debug_info     0x0000000000000000     0x9999
 .debug_info    0x0000000000000000     0x9999 Core/Src/main.o
"""

KEIL = """\
    Load Region LR_IROM1 (Base: 0x08000000, Size: 0x00000cd8, Max: 0x00100000, ABSOLUTE)
    Execution Region ER_IROM1 (Base: 0x08000000, Size: 0x00000cd0, Max: 0x00100000, ABSOLUTE)
    Execution Region RW_IRAM1 (Base: 0x20000000, Size: 0x00000408, Max: 0x00020000, ABSOLUTE)

Image component sizes

      Code (inc. data)   RO Data    RW Data    ZI Data      Debug   Object Name

       468         36          0          0          0       2452   main.o
        36          8        392          0       1024        816   startup_stm32f407xx.o
       142         22          0          4          0       4063   stm32f4xx_hal.o

    ----------------------------------------------------------------------
       646         66        392          4       1024       7331   Object Totals

      Code (inc. data)   RO Data    RW Data    ZI Data      Debug   Library Member Name

        30          0          0          0          0          0   handlers.o

      Code (inc. data)   RO Data    RW Data    ZI Data      Debug   Library Name

        30          0          0          0          0          0   mc_w.l

      Code (inc. data)   RO Data    RW Data    ZI Data      Debug

       676         66        392          4       1024       7331   Grand Totals
"""


def child(node, *names):
    for name in names:
        node = next(c for c in node.children if c.name == name)
    return node


class MemMapTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_gnu(self):
        m = memmap.parse(self.write("fw.map", GNU))
        self.assertEqual(m.source, "GNU ld map")
        ram, flash = m.regions
        self.assertEqual((ram.name, ram.kind, ram.length), ("RAM", "ram", 0x5000))
        self.assertEqual(flash.used, 0x100 + 0x300 + 0x10)  # .data's initial values count in Flash
        self.assertEqual(ram.used, 0x10 + 0x40 + 0x600)
        self.assertEqual(child(flash.tree, ".text", "main.o", "main").size, 0x200)
        # a long input section name wraps onto the next line; a library member keeps its archive
        self.assertEqual(child(flash.tree, ".text", "libc_nano.a(lib_a-memcpy.o)",
                               "a_function_with_a_very_long_name").size, 0xfe)
        self.assertEqual(child(flash.tree, ".data (initial values)").size, 0x10)
        self.assertEqual(child(ram.tree, "._user_heap_stack").kind, "stack")
        self.assertEqual(child(ram.tree, "._user_heap_stack", "(reserved: heap / stack)").size, 0x600)
        self.assertNotIn(".debug_info", [c.name for c in flash.tree.children + ram.tree.children])

    def test_keil(self):
        m = memmap.parse(self.write("fw.map", KEIL))
        flash, ram = m.regions
        self.assertEqual((flash.name, flash.length, ram.name, ram.length),
                         ("LR_IROM1", 0x100000, "RW_IRAM1", 0x20000))
        self.assertEqual(child(flash.tree, "Code").size, 468 + 36 + 142 + 30)  # library members too
        self.assertEqual(child(flash.tree, "RO data", "startup_stm32f407xx.o").size, 392)
        self.assertEqual(child(ram.tree, "ZI data", "startup_stm32f407xx.o").size, 1024)
        self.assertEqual(child(ram.tree, "RW data").size, 4)

    def test_not_a_map(self):
        self.assertIsNone(memmap.parse(self.write("app.js.map", '{"version":3,"sources":[]}')))
        self.assertIsNone(memmap.parse(self.write("level.map", "Linker script and memory map\n\nnothing\n")))
        self.assertIsNone(memmap.parse(self.write("x.out", "plain text output")))

    @unittest.skipUnless(shutil.which("gcc"), "needs gcc")
    def test_elf(self):
        src = self.write("t.c", "int table[64] = {1};\nint zeros[32];\n"
                                "int compute(int x) { return table[x] + zeros[x]; }\n"
                                "int main(void) { return compute(3); }\n")
        exe = os.path.join(self.dir.name, "t.elf")
        subprocess.run(["gcc", "-O0", "-o", exe, src], check=True, capture_output=True)
        with open(exe, "rb") as f:
            if f.read(4) != b"\x7fELF":
                self.skipTest("this gcc doesn't make ELF files (MinGW)")
        m = memmap.parse(exe)
        self.assertEqual(m.source, "ELF")
        flash = next(r for r in m.regions if r.kind == "flash")
        ram = next(r for r in m.regions if r.kind == "ram")
        self.assertGreater(child(flash.tree, ".text", "compute").size, 0)
        self.assertEqual(child(ram.tree, ".data", "table").size, 256)
        self.assertEqual(child(ram.tree, ".bss", "zeros").size, 128)
        self.assertEqual(child(flash.tree, ".data (initial values)", "table").size, 256)

    def test_elf_prefers_map_next_to_it(self):
        self.write("fw.map", GNU)
        with open(os.path.join(self.dir.name, "fw.elf"), "wb") as f:
            f.write(b"\x7fELF" + b"\0" * 60)
        self.assertEqual(memmap.parse(os.path.join(self.dir.name, "fw.elf")).source, "GNU ld map")

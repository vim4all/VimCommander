import base64
import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QEventLoop, QIODevice, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from vimcommander import cubemx, notebook, pane, preview

IOC = r"""#MicroXplorer Configuration settings - do not modify
Mcu.CPN=STM32F103C8T6
Mcu.Family=STM32F1
Mcu.Package=LQFP48
Mcu.IP0=NVIC
Mcu.IP1=TIM2
Mcu.IP2=USART2
Mcu.IPNb=3
Mcu.Pin0=PC13-TAMPER-RTC
Mcu.Pin1=PA10
Mcu.Pin2=PA0-WKUP
Mcu.Pin3=VP_TIM2_VS_ClockSourceINT
Mcu.Pin4=PA2
Mcu.PinsNb=5
PA0-WKUP.Signal=S_TIM2_CH1_ETR
PA2.Mode=Asynchronous
PA2.Signal=USART2_TX
PA10.GPIO_PuPd=GPIO_PULLUP
PA10.Signal=GPIO_Input
PC13-TAMPER-RTC.GPIO_Label=LED_PIN
PC13-TAMPER-RTC.PinState=GPIO_PIN_SET
PC13-TAMPER-RTC.Signal=GPIO_Output
SH.S_TIM2_CH1_ETR.0=TIM2_CH1,PWM Generation1 CH1
TIM2.IPParameters=Prescaler,Pulse-PWM Generation1 CH1
TIM2.Prescaler=24-1
TIM2.Pulse-PWM\ Generation1\ CH1=4500
USART2.IPParameters=BaudRate
USART2.BaudRate=921600
NVIC.USART2_IRQn=true\:1\:0\:true\:false\:true\:true\:true\:true
NVIC.HardFault_IRQn=true\:0\:0\:false\:false\:true\:false\:false\:false
NVIC.TIM2_IRQn=false\:0\:0\:false\:false\:true\:true\:true\:true
RCC.SYSCLKFreq_VALUE=72000000
RCC.APB1Freq_Value=36000000
RCC.SYSCLKSource=RCC_SYSCLKSOURCE_PLLCLK
RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE
ProjectManager.TargetToolchain=STM32CubeIDE
"""


def png_base64() -> str:
    image = QImage(4, 3, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return base64.b64encode(bytes(buffer.data())).decode()


def notebook_json(png: str) -> bytes:
    return json.dumps({
        "nbformat": 4, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python"}},
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n", "![plot](attachment:a.png)"],
             "attachments": {"a.png": {"image/png": png}}},
            {"cell_type": "code", "source": "x = 1\nprint(x)", "outputs": [
                {"output_type": "stream", "name": "stdout", "text": ["1\n"]},
                {"output_type": "display_data", "data": {"image/png": png, "text/plain": "<Figure>"}},
                {"output_type": "error", "ename": "E", "evalue": "v",
                 "traceback": ["\x1b[31mTraceback\x1b[0m", "ZeroDivisionError: x"]}]},
            {"cell_type": "code", "source": "```\nweird", "outputs": [
                {"output_type": "execute_result", "data": {"text/plain": ["line\n"] * 50}}]},
        ]}).encode()


class NaturalSortTest(unittest.TestCase):
    def tearDown(self):
        pane.NATURAL["on"] = True

    def test_numbers_in_names(self):
        names = ["test10.csv", "Test2.csv", "test1.csv", "a01", "a1", "a1b", "a1b2", "x", "ß2"]
        self.assertEqual(sorted(names, key=pane.natural_key),  # ß sorts as ss
                         ["a01", "a1", "a1b", "a1b2", "ß2", "test1.csv", "Test2.csv", "test10.csv", "x"])
        pane.NATURAL["on"] = False
        self.assertEqual(sorted(["f10", "f9"], key=pane.name_key), ["f10", "f9"])

    def test_pane_list(self):
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as root:
            for name in ("run10.csv", "run9.csv", "run1.csv", "dir2", "dir10"):
                path = os.path.join(root, name)
                os.mkdir(path) if name.startswith("dir") else open(path, "w").close()
            model = pane.DirModel()
            model.load(root)
            self.assertEqual([e.name for e in model.entries],
                             ["..", "dir2", "dir10", "run1.csv", "run9.csv", "run10.csv"])
            pane.NATURAL["on"] = False
            model.load(root)
            self.assertEqual([e.name for e in model.entries][3:], ["run1.csv", "run10.csv", "run9.csv"])


class NotebookTest(unittest.TestCase):
    def test_convert(self):
        nb = notebook.convert(notebook_json(png_base64()))
        self.assertEqual(len(nb.images), 2)  # the attachment and the plot
        self.assertIn("![plot](nbimage:0)", nb.markdown)
        self.assertIn("![image](nbimage:1)", nb.markdown)  # Qt leaves out images without alt text
        self.assertIn("```\n\n&nbsp;\n\n````python", nb.markdown)  # a gap between two code cells
        self.assertIn("```python\nx = 1\nprint(x)\n```", nb.markdown)
        self.assertIn("```output\n1\n```", nb.markdown)
        self.assertIn("Traceback\nZeroDivisionError: x", nb.markdown)  # colors gone
        self.assertIn("````python\n```\nweird\n````", nb.markdown)  # its own backticks can't end it
        self.assertIn(f"… 10 more lines", nb.markdown)
        self.assertEqual(nb.summary, "Python 3   3 cells (2 code, 1 Markdown)   2 image(s)")

    def test_not_a_notebook(self):
        for data in (b"{}", b"not json", b'{"worksheets": []}'):
            with self.assertRaises(ValueError):
                notebook.convert(data)


class CubeMXTest(unittest.TestCase):
    def test_parse(self):
        p = cubemx.parse(IOC)
        self.assertEqual(p.mcu, "STM32F103C8T6")
        self.assertEqual(p.details[:3], ["LQFP48", "STM32F1", "STM32CubeIDE"])
        self.assertEqual(p.clocks, [("SYSCLK", 72000000), ("APB1", 36000000)])
        self.assertEqual(p.clock_source, "PLLCLK from HSE")
        self.assertEqual([(x.name, x.signal, x.label, x.mode) for x in p.pins], [
            ("PA0-WKUP", "TIM2_CH1", "", ""), ("PA2", "USART2_TX", "", "Asynchronous"),
            ("PA10", "GPIO_Input", "", "pull-up"), ("PC13-TAMPER-RTC", "GPIO_Output", "LED_PIN", "starts high")])
        self.assertEqual(p.peripherals, [("TIM2", [("Prescaler", "24-1"), ("Pulse-PWM Generation1 CH1", "4500")]),
                                         ("USART2", [("BaudRate", "921600")])])
        self.assertEqual(p.interrupts, ["USART2 (1)"])  # not the core's, not a disabled one
        self.assertIn("<b>PC13-TAMPER-RTC</b>", cubemx.to_html(p))
        self.assertEqual(cubemx.summary(p), "STM32F103C8T6   LQFP48   SYSCLK 72 MHz   4 pins used")
        self.assertEqual(cubemx.hz(32768), "32.768 kHz")

    def test_not_a_project(self):
        self.assertIsNone(cubemx.parse("a=b\n"))


class QuickViewTest(unittest.TestCase):
    """The quick view picks the right widget for notebooks, .ioc files and (zp) CSV plots."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.view = preview.QuickView()
        self.view.resize(600, 400)
        self.view.show()
        self.addCleanup(self.view.deleteLater)
        self.addCleanup(preview.PLOT.update, {"on": False})

    def write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def wait(self, ready, ms=5000):
        loop = QEventLoop()
        timer = QTimer(interval=20)
        timer.timeout.connect(lambda: ready() and loop.quit())
        timer.start()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        timer.stop()

    def test_notebook(self):
        self.view.show_path(self.write("n.ipynb", notebook_json(png_base64())))
        self.assertIs(self.view.stack.currentWidget(), self.view.markdown)
        self.assertEqual(len(self.view.markdown.images), 2)
        self.assertIn("3 cells", self.view.title.text())
        self.assertIn("print(x)", self.view.markdown.toPlainText())
        self.assertEqual(self.view.markdown.document().toHtml().count("<img"), 2)
        self.view.show_path(self.write("bad.ipynb", b"{oops"))
        self.assertIs(self.view.stack.currentWidget(), self.view.text)  # shown as text instead

    def test_ioc(self):
        self.view.show_path(self.write("p.ioc", IOC.encode()))
        self.assertIs(self.view.stack.currentWidget(), self.view.markdown)
        self.assertIn("LED_PIN", self.view.markdown.toPlainText())
        self.assertIn("SYSCLK 72 MHz", self.view.title.text())

    def test_csv_plot_and_table(self):
        csv = self.write("w.csv", b"Time,I\n" + b"".join(f"{i},{i % 7}\n".encode() for i in range(5000)))
        self.view.show_path(csv)
        self.assertIs(self.view.stack.currentWidget(), self.view.csv)  # a table unless zp
        preview.PLOT["on"] = True
        self.view.show_path(None)
        self.view.show_path(csv)
        self.assertIs(self.view.stack.currentWidget(), self.view.plot_area)
        self.wait(lambda: self.view.plot.data is not None)
        plot = self.view.plot
        self.assertEqual((plot.data.rows, plot.data.x), (5000, 0))
        self.assertIn("5000 rows", self.view.title.text())
        plot.grab()  # paints: makes the min-max bars of 5000 rows
        self.assertEqual(len(plot._traces), 1)
        self.assertEqual(plot._stats[0][:2], (0.0, 6.0))
        plot.zoom_to(100.0, 110.0)
        plot.grab()
        self.assertEqual(plot._stats[0][1], 6.0)
        self.assertLess(len(plot._traces[0][0]), 20)  # few samples: one point each
        plot.zoom_out()
        self.assertEqual(plot.span, (0.0, 4999.0))
        text = self.write("t.csv", b"name,city\nann,Riga\nbob,Kyiv\n")
        self.view.show_path(text)
        self.wait(lambda: self.view.stack.currentWidget() is self.view.csv)
        self.assertIs(self.view.stack.currentWidget(), self.view.csv)  # nothing to plot: the table
        self.assertIn("no numbers to plot", self.view.title.text())


if __name__ == "__main__":
    unittest.main()

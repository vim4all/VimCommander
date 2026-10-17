"""STM32CubeMX projects (.ioc) for the quick view, no Qt: the microcontroller,
clocks, used pins, configured peripherals, DMA and interrupts, as HTML.

An .ioc file is a Java properties file: "Key=value" lines, with "\\:" and
"\\ " escapes. Peripheral settings changed from their defaults are listed in
"IP.IPParameters", so those are the ones shown.
"""

import html
import re
from dataclasses import dataclass, field

# Interrupts of the Cortex core itself, which every project has
CORE_IRQS = {"NonMaskableInt_IRQn", "HardFault_IRQn", "MemoryManagement_IRQn", "BusFault_IRQn",
             "UsageFault_IRQn", "SVCall_IRQn", "DebugMonitor_IRQn", "PendSV_IRQn", "SysTick_IRQn"}
HIDDEN_IPS = {"NVIC", "RCC", "SYS", "GPIO", "DMA"}  # shown elsewhere, or always there
CLOCKS = (("SYSCLK", "RCC.SYSCLKFreq_VALUE"), ("HCLK", "RCC.HCLKFreq_Value"),
          ("APB1", "RCC.APB1Freq_Value"), ("APB2", "RCC.APB2Freq_Value"), ("APB3", "RCC.APB3Freq_Value"))
_PIN = re.compile(r"^P([A-Z])(\d+)")


@dataclass
class Pin:
    name: str  # PA9, PC13-TAMPER-RTC
    signal: str  # USART1_TX, GPIO_Output
    label: str  # the user's name for it
    mode: str  # Asynchronous, I2C, pull-up…


@dataclass
class Project:
    mcu: str
    details: list[str]  # package, family, toolchain, firmware package…
    clocks: list[tuple[str, int]]  # ("SYSCLK", 72000000)
    clock_source: str
    pins: list[Pin] = field(default_factory=list)
    peripherals: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    dma: list[str] = field(default_factory=list)
    interrupts: list[str] = field(default_factory=list)


def is_ioc(path: str) -> bool:
    return path.lower().endswith(".ioc")


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def properties(text: str) -> dict[str, str]:
    """Key -> value of a Java properties file (one line each, as CubeMX writes them)."""
    props = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#!":
            continue
        m = re.match(r"((?:\\.|[^\\=])*)=(.*)$", line)
        if m:
            props[_unescape(m.group(1)).strip()] = _unescape(m.group(2)).strip()
    return props


def _numbered(props: dict[str, str], prefix: str, count_key: str) -> list[str]:
    """Mcu.Pin0, Mcu.Pin1, … up to Mcu.PinsNb."""
    try:
        count = int(props.get(count_key, "0"))
    except ValueError:
        count = 0
    return [props[f"{prefix}{i}"] for i in range(count) if f"{prefix}{i}" in props]


def _pin_order(pin: Pin) -> tuple:
    m = _PIN.match(pin.name)
    return (0, m.group(1), int(m.group(2))) if m else (1, pin.name, 0)


def parse(text: str) -> Project | None:
    """The project in an .ioc file's text, None if it isn't one."""
    props = properties(text)
    mcu = props.get("Mcu.CPN") or props.get("Mcu.UserName") or props.get("Mcu.Name")
    if not mcu:
        return None
    details = [v for v in (props.get("Mcu.Package"), props.get("Mcu.Family"),
                           props.get("ProjectManager.TargetToolchain"),
                           props.get("ProjectManager.FirmwarePackage")) if v]
    if props.get("MxCube.Version"):
        details.append(f"CubeMX {props['MxCube.Version']}")
    if props.get("ProjectManager.HeapSize") or props.get("ProjectManager.StackSize"):
        details.append(f"heap {props.get('ProjectManager.HeapSize', '?')}, "
                       f"stack {props.get('ProjectManager.StackSize', '?')}")
    clocks = []
    for name, key in CLOCKS:
        try:
            clocks.append((name, int(float(props[key]))))
        except (KeyError, ValueError):
            pass
    source = props.get("RCC.SYSCLKSource", "").replace("RCC_SYSCLKSOURCE_", "")
    if source.startswith("PLL") and (pll := props.get("RCC.PLLSourceVirtual") or props.get("RCC.PLLSource")):
        source += " from " + pll.replace("RCC_PLLSOURCE_", "")
    project = Project(mcu, details, clocks, source)

    for name in _numbered(props, "Mcu.Pin", "Mcu.PinsNb"):
        if name.startswith("VP_"):  # a virtual pin: a peripheral's internal clock and such
            continue
        signal = props.get(f"{name}.Signal", "")
        if signal.startswith("S_"):  # shared: SH.S_TIM2_CH1_ETR.0=TIM2_CH1,PWM Generation1 CH1
            signal = props.get(f"SH.{signal}.0", signal).split(",")[0]
        mode = [props.get(f"{name}.Mode", "")]
        mode.append({"GPIO_PULLUP": "pull-up", "GPIO_PULLDOWN": "pull-down"}.get(props.get(f"{name}.GPIO_PuPd", ""), ""))
        if props.get(f"{name}.PinState") == "GPIO_PIN_SET":
            mode.append("starts high")
        project.pins.append(Pin(name, signal, props.get(f"{name}.GPIO_Label", ""), ", ".join(m for m in mode if m)))
    project.pins.sort(key=_pin_order)

    for ip in _numbered(props, "Mcu.IP", "Mcu.IPNb"):
        if ip in HIDDEN_IPS:
            continue
        names = [p for p in props.get(f"{ip}.IPParameters", "").split(",") if p]
        project.peripherals.append((ip, [(p, props[f"{ip}.{p}"]) for p in names if f"{ip}.{p}" in props]))
    for request in _numbered(props, "Dma.Request", "Dma.RequestsNb"):
        base = f"Dma.{request}.0."
        how = [props.get(base + "Instance", ""), props.get(base + "Mode", "").replace("DMA_", "").lower()]
        project.dma.append(request + (f" → {', '.join(h for h in how if h)}" if any(how) else ""))
    for key, value in sorted(props.items()):
        if key.startswith("NVIC.") and key.endswith("_IRQn") and key[5:] not in CORE_IRQS:
            fields = value.split(":")
            if fields[0] == "true":
                priority = f" ({fields[1]})" if len(fields) > 1 and fields[1].isdigit() else ""
                project.interrupts.append(key[5:-5] + priority)
    return project


def hz(value: int) -> str:
    for unit, size in (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3)):
        if value >= size:
            return f"{value / size:g} {unit}"
    return f"{value} Hz"


def summary(project: Project) -> str:
    """One line for the quick view's title."""
    parts = [project.mcu] + project.details[:1]
    if project.clocks:
        parts.append(f"{project.clocks[0][0]} {hz(project.clocks[0][1])}")
    parts.append(f"{len(project.pins)} pins used")
    return "   ".join(parts)


def to_html(project: Project) -> str:
    """The project as an HTML page: details, clocks, a pin table, peripherals."""
    e = html.escape
    out = [f"<h3>{e(project.mcu)}</h3>", f"<p>{e('  ·  '.join(project.details))}</p>"]
    if project.clocks:
        clocks = "  ·  ".join(f"{name} {hz(v)}" for name, v in project.clocks)
        source = f" (source: {e(project.clock_source)})" if project.clock_source else ""
        out.append(f"<p><b>Clocks</b>  {e(clocks)}{source}</p>")
    if project.pins:
        out.append('<table cellspacing="0" cellpadding="3"><tr><th align="left">Pin</th>'
                   '<th align="left">Signal</th><th align="left">Label</th><th align="left">Mode</th></tr>')
        out += [f"<tr><td><b>{e(p.name)}</b></td><td>{e(p.signal)}</td><td>{e(p.label)}</td>"
                f"<td>{e(p.mode)}</td></tr>" for p in project.pins]
        out.append("</table>")
    if project.peripherals:
        out.append('<p><b>Peripherals</b></p><table cellspacing="0" cellpadding="3">')
        out += [f"<tr><td valign=\"top\"><b>{e(ip)}</b></td>"
                f"<td>{e(', '.join(f'{k} = {v}' for k, v in settings)) or '(defaults)'}</td></tr>"
                for ip, settings in project.peripherals]
        out.append("</table>")
    if project.dma:
        out.append(f"<p><b>DMA</b>  {e('  ·  '.join(project.dma))}</p>")
    if project.interrupts:
        out.append(f"<p><b>Interrupts</b> (priority)  {e('  ·  '.join(project.interrupts))}</p>")
    return "\n".join(out)

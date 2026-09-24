"""Netlist export: circuit graph -> SPICE / KiCad / plain-text / CSV.

The :class:`~schemaccess.model.CircuitGraph` already holds everything a
netlist needs -- every component with its type and value, and every net
with the pins that landed on it -- so this module is a pure formatter.  It
adds no analysis of its own and never touches the filesystem.

Four formats are produced:

``spice``
    An ngspice/LTspice deck (``.cir``).  Ground becomes node ``0``, every
    other net keeps its name.  Devices SPICE cannot infer from a schematic
    alone (controlled sources without control nodes, switches, ICs) are
    emitted as comments rather than as guesses, and every one of them is
    named in :attr:`NetlistResult.notes`.

``kicad``
    KiCad's own S-expression netlist (``.net``), the format
    ``Tools > Generate Netlist`` writes, so the result imports into the
    same tools a KiCad export would.

``text``
    A plain-text component/net table meant to be *read* -- by a person or
    by a screen reader -- rather than parsed.

``csv``
    One row per pin (net, reference, pin number, pin name, pin type), for
    a spreadsheet.

Output is deterministic: identical graphs always produce identical text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .model import (CircuitGraph, Component, ComponentType, Net, NetKind,
                    PinConnection)

__all__ = ["FORMATS", "FORMAT_NAMES", "EXTENSIONS", "NetlistResult",
           "generate", "spice", "kicad", "text", "csv"]

#: Format keys accepted by :func:`generate`, in presentation order.
FORMATS: Tuple[str, ...] = ("spice", "kicad", "text", "csv")

#: Filename suffix for each format, appended to the schematic's stem.
EXTENSIONS: Dict[str, str] = {
    "spice": ".cir",
    "kicad": ".net",
    "text": "_netlist.txt",
    "csv": "_netlist.csv",
}

#: Human-readable format names, for menus and file pickers.
FORMAT_NAMES: Dict[str, str] = {
    "spice": "SPICE deck (ngspice / LTspice)",
    "kicad": "KiCad netlist",
    "text": "Readable net table",
    "csv": "Spreadsheet (CSV)",
}


@dataclass
class NetlistResult:
    """A generated netlist plus anything the caller should be told about."""
    text: str = ""
    #: Components that could not be expressed in the target format and
    #: were emitted as comments instead.  Never silent.
    notes: List[str] = field(default_factory=list)

    def __str__(self) -> str:      # so callers can treat it as the text
        return self.text


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _pin_sort_key(number: str) -> Tuple[int, int, str]:
    if number.isdigit():
        return (0, int(number), number)
    return (1, 0, number)


def _pins_in_order(comp: Component) -> List[PinConnection]:
    return [comp.pins[n] for n in sorted(comp.pins, key=_pin_sort_key)]


def _pin_named(pins: Sequence[PinConnection],
               names: Tuple[str, ...]) -> Optional[PinConnection]:
    """First pin whose *name* is one of *names* (case-insensitive)."""
    for pin in pins:
        if pin.name.strip().lower() in names:
            return pin
    return None


def _sim_pin_roles(comp: Component) -> Dict[str, str]:
    """KiCad's ``Sim.Pins`` property as pin number -> role.

    Simulation symbols often leave their pins unnamed and record polarity
    only here, e.g. ``"1=+ 2=-"``.
    """
    roles: Dict[str, str] = {}
    for token in comp.properties.get("Sim.Pins", "").split():
        num, sep, role = token.partition("=")
        if sep:
            roles[num.strip()] = role.strip().lower()
    return roles


def _source_terminals(comp: Component) -> Tuple[PinConnection, PinConnection]:
    """Return (positive, negative) pins of a source.

    KiCad names them ``+``/``-`` when it names them at all; simulation
    symbols record the same thing in ``Sim.Pins``; and every stock KiCad
    source (VDC, VSIN, Battery, ...) puts ``+`` on pin 1 when the pins
    carry no names at all.
    """
    pins = _pins_in_order(comp)
    roles = _sim_pin_roles(comp)
    plus = _pin_named(pins, ("+", "p", "plus", "n+", "in+")) or next(
        (p for p in pins if roles.get(p.number) == "+"), None)
    minus = _pin_named(pins, ("-", "n", "minus", "n-", "in-")) or next(
        (p for p in pins if roles.get(p.number) == "-"), None)
    if plus is None:
        plus = next((p for p in pins if p.number == "1"), pins[0])
    if minus is None or minus is plus:
        minus = next((p for p in pins if p is not plus), pins[-1])
    return (plus, minus)


def _diode_terminals(comp: Component) -> Tuple[PinConnection, PinConnection]:
    """Return (anode, cathode).  KiCad's diode pin 1 is the cathode."""
    pins = _pins_in_order(comp)
    anode = _pin_named(pins, ("a", "anode"))
    cathode = _pin_named(pins, ("k", "c", "cathode"))
    if anode is not None and cathode is not None:
        return (anode, cathode)
    return (pins[1], pins[0])


def _by_pin_names(comp: Component,
                  wanted: Sequence[Tuple[str, ...]]
                  ) -> Optional[List[PinConnection]]:
    """Resolve pins by name, e.g. ``(("c",), ("b",), ("e",))``.

    Returns ``None`` unless every group matched a *distinct* pin, so a
    caller can fall back to pin-number order instead of mis-wiring a
    device.
    """
    pins = _pins_in_order(comp)
    found: List[PinConnection] = []
    for names in wanted:
        pin = _pin_named([p for p in pins if p not in found], names)
        if pin is None:
            return None
        found.append(pin)
    return found


# ---------------------------------------------------------------------------
# SPICE
# ---------------------------------------------------------------------------

# SPICE is case-insensitive and reads 'm' as milli, so mega must be spelled
# 'Meg'.  Everything else is the usual single letter.
_SPICE_PREFIX: Dict[str, str] = {
    "t": "T", "g": "G", "meg": "Meg", "k": "k",
    "u": "u", "µ": "u", "μ": "u", "n": "n", "p": "p", "f": "f",
}

# Unit words/letters that may trail a value; longest first so 'ohm' wins
# over 'H' and 'farad' over 'A'.
_UNIT_TOKENS = ("ohms", "ohm", "farads", "farad", "henries", "henry",
                "volts", "volt", "amperes", "ampere", "amps", "amp",
                "seconds", "second", "hertz", "hz",
                "Ω", "Ω", "F", "H", "V", "A", "R")

_NUMBER_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(.*)$")
# RKM / "R notation": 4k7 = 4.7k, 1R5 = 1.5 ohm, 2M2 = 2.2 mega.
_RKM_RE = re.compile(r"^(\d+)([A-Za-zµμΩ])(\d+)$")

_SPICE_NAME_RE = re.compile(r"[^A-Za-z0-9_]")


def _spice_prefix(token: str) -> Optional[str]:
    """SPICE suffix for a metric-prefix token; ``''`` for none."""
    token = token.strip()
    if token == "":
        return ""
    if token.lower() == "meg":
        return "Meg"
    if len(token) == 1:
        if token == "m":
            return "m"          # milli
        if token == "M":
            return "Meg"        # KiCad users write 'M' for mega
        return _SPICE_PREFIX.get(token.lower())
    return None


def _spice_value(value: str, ctype: ComponentType) -> Optional[str]:
    """Convert a KiCad value string to a SPICE magnitude.

    ``'4.7k'`` -> ``'4.7k'``, ``'22nF'`` -> ``'22n'``, ``'100 Ohm'`` ->
    ``'100'``, ``'1MEG'`` -> ``'1Meg'``, ``'4k7'`` -> ``'4.7k'``,
    ``'10 mH'`` -> ``'10m'``.  Returns ``None`` when the string carries no
    usable number, so the caller can fall back to a comment instead of
    writing a deck that will not run.
    """
    text_value = (value or "").strip().replace(",", ".")
    if not text_value or not any(ch.isdigit() for ch in text_value):
        return None

    rkm = _RKM_RE.match(text_value)
    if rkm is not None:
        whole, letter, fraction = rkm.groups()
        if letter in ("R", "r", "Ω", "Ω"):
            return f"{whole}.{fraction}"
        prefix = _spice_prefix(letter)
        if prefix is None:
            return None
        return f"{whole}.{fraction}{prefix}"

    match = _NUMBER_RE.match(text_value)
    if match is None:
        return None
    number, rest = match.group(1), match.group(2).strip()
    if number.startswith("."):
        number = "0" + number

    for token in _UNIT_TOKENS:
        if rest.lower().endswith(token.lower()):
            rest = rest[:len(rest) - len(token)].strip()
            break
    prefix = _spice_prefix(rest)
    if prefix is None:
        return None
    return number + prefix


def _spice_node(net: Optional[Net], fallback: str) -> str:
    """Node name for *net*; ground is always ``0``."""
    if net is None:
        return fallback
    if net.kind == NetKind.GROUND:
        return "0"
    name = _SPICE_NAME_RE.sub(
        "_", net.name.replace("+", "P").replace("-", "M"))
    name = name.strip("_") or fallback
    if name[0].isdigit():
        name = "N" + name
    return name


def _spice_nodes(graph: CircuitGraph) -> Dict[int, str]:
    names: Dict[int, str] = {}
    used = {"0", "gnd"}  # ngspice is case-blind and reads gnd as node 0
    for net in graph.nets:
        name = _spice_node(net, f"NET{net.net_id + 1}")
        if name != "0":
            base, suffix = name, 2
            while name.lower() in used:
                name = f"{base}_{suffix}"
                suffix += 1
            used.add(name.lower())
        names[net.net_id] = name
    return names


def _spice_ref(letter: str, ref: str) -> str:
    """A SPICE device name that starts with the right letter.

    SPICE picks the device type from the first character, so ``R1`` may
    stay ``R1`` but an op amp called ``U3`` has to become ``XU3``.
    """
    clean = _SPICE_NAME_RE.sub("_", ref) or "1"
    if clean[0].upper() == letter.upper():
        return clean
    return letter + clean


def _model_name(comp: Component, default: str) -> str:
    """Use the part number as the model name when the value looks like one.

    A schematic saying ``2N2222`` gets ``.model 2N2222 NPN``, which the
    professor can replace with a real vendor model without touching any
    device line.
    """
    raw = (comp.value or "").strip()
    part_number = (raw and " " not in raw
                   and any(ch.isalpha() for ch in raw)
                   and any(ch.isdigit() for ch in raw))
    if part_number:
        return _SPICE_NAME_RE.sub("_", raw)
    return default


_IDEAL_OPAMP = [
    "* Ideal op amp: infinite-gain VCVS.  Replace with a vendor subcircuit",
    "* for a realistic simulation (finite gain, slew rate, supply rails).",
    ".subckt SCHEMACCESS_OPAMP inp inn out",
    "Egain out 0 inp inn 1e6",
    ".ends SCHEMACCESS_OPAMP",
]


def spice(graph: CircuitGraph, *, title: str = "") -> NetlistResult:
    """Return an ngspice/LTspice deck for *graph*."""
    result = NetlistResult()
    node_names = _spice_nodes(graph)

    def node(pin: PinConnection, comp: Component) -> str:
        if pin.net_id in node_names:
            return node_names[pin.net_id]
        return ("NC_" + _SPICE_NAME_RE.sub("_", comp.ref)
                + "_" + _SPICE_NAME_RE.sub("_", pin.number))

    if graph.components and not any(net.kind == NetKind.GROUND and net.pins
                                    for net in graph.nets):
        result.notes.append(
            "The schematic has no ground, and SPICE needs one (node 0) to "
            "simulate; add a GND symbol to the reference net.")

    header = title
    if not header and graph.document is not None and graph.document.source_path:
        header = graph.document.source_path.replace("\\", "/").rsplit("/", 1)[-1]
    header = header or "circuit"

    # The first line of a SPICE deck is its title, never a statement.
    lines: List[str] = [
        f"* {header} - netlist generated by SchemAccess",
        f"* {len(graph.components)} components, {len(graph.nets)} nets",
        "",
    ]

    models: List[str] = []
    need_opamp = False

    def add_model(name: str, body: str) -> None:
        entry = f".model {name} {body}"
        if entry not in models:
            models.append(entry)

    def unsupported(comp: Component, why: str,
                    template: Sequence[str] = ()) -> None:
        """Comment a device out rather than guess at it.

        The pin-to-node map is always written, and where SPICE has a device
        that only needs numbers the schematic does not carry, a ready-made
        line is written with the nodes already filled in: uncomment it,
        put the numbers in, and it runs.
        """
        pin_map = "  ".join(f"{p.number}={node(p, comp)}"
                            for p in _pins_in_order(comp))
        lines.append(f"* {comp.ref} ({comp.ctype.value}) {why}")
        lines.append(f"*   pins: {pin_map}")
        for line in template:
            lines.append(f"* {line}")
        result.notes.append(f"{comp.ref} ({comp.ctype.value}) {why}.")

    def value_of(comp: Component, default: str, unit_note: str) -> str:
        converted = _spice_value(comp.value, comp.ctype)
        if converted is not None:
            return converted
        if comp.value.strip():
            result.notes.append(
                f"{comp.ref}: could not read the value "
                f"'{comp.value.strip()}' as a number; wrote {default}"
                f"{unit_note}.")
        else:
            result.notes.append(
                f"{comp.ref}: no value in the schematic; wrote {default}"
                f"{unit_note}.")
        return default

    for comp in graph.sorted_components():
        pins = _pins_in_order(comp)
        ctype = comp.ctype

        if len(pins) < 2:
            unsupported(comp, "has fewer than two pins; left out of the deck")
            continue

        if ctype == ComponentType.RESISTOR:
            a, b = pins[0], pins[1]
            lines.append(f"{_spice_ref('R', comp.ref)} {node(a, comp)} "
                         f"{node(b, comp)} {value_of(comp, '1k', ' ohms')}")
        elif ctype == ComponentType.POTENTIOMETER and len(pins) >= 3:
            total = value_of(comp, "10k", " ohms")
            end_a, wiper, end_b = pins[0], pins[1], pins[2]
            named = _by_pin_names(comp, (("1",), ("w", "wiper"), ("3",)))
            if named is not None:
                end_a, wiper, end_b = named
            base = _SPICE_NAME_RE.sub("_", comp.ref)
            lines.append(f"R{base}A {node(end_a, comp)} {node(wiper, comp)} "
                         f"{{0.5*{total}}}")
            lines.append(f"R{base}B {node(wiper, comp)} {node(end_b, comp)} "
                         f"{{0.5*{total}}}")
            result.notes.append(
                f"{comp.ref}: a potentiometer became two resistors split "
                f"50/50; edit the two halves to move the wiper.")
        elif ctype in (ComponentType.CAPACITOR,
                       ComponentType.CAPACITOR_POLARIZED):
            a, b = pins[0], pins[1]
            lines.append(f"{_spice_ref('C', comp.ref)} {node(a, comp)} "
                         f"{node(b, comp)} {value_of(comp, '1u', ' farads')}")
        elif ctype == ComponentType.INDUCTOR:
            a, b = pins[0], pins[1]
            lines.append(f"{_spice_ref('L', comp.ref)} {node(a, comp)} "
                         f"{node(b, comp)} {value_of(comp, '1m', ' henries')}")
        elif ctype in (ComponentType.DIODE, ComponentType.LED,
                       ComponentType.ZENER):
            anode, cathode = _diode_terminals(comp)
            default = {ComponentType.DIODE: "SCHEMACCESS_D",
                       ComponentType.LED: "SCHEMACCESS_LED",
                       ComponentType.ZENER: "SCHEMACCESS_ZENER"}[ctype]
            model = _model_name(comp, default)
            body = {ComponentType.DIODE: "D",
                    ComponentType.LED: "D(N=2 IS=1e-20)",
                    ComponentType.ZENER: "D(BV=5.1)"}[ctype]
            add_model(model, body)
            lines.append(f"{_spice_ref('D', comp.ref)} {node(anode, comp)} "
                         f"{node(cathode, comp)} {model}")
        elif ctype in (ComponentType.VOLTAGE_SOURCE, ComponentType.BATTERY):
            plus, minus = _source_terminals(comp)
            lines.append(f"{_spice_ref('V', comp.ref)} {node(plus, comp)} "
                         f"{node(minus, comp)} DC "
                         f"{value_of(comp, '1', ' volts')}")
        elif ctype == ComponentType.AC_SOURCE:
            plus, minus = _source_terminals(comp)
            amplitude = value_of(comp, "1", " volts")
            lines.append(f"{_spice_ref('V', comp.ref)} {node(plus, comp)} "
                         f"{node(minus, comp)} DC 0 AC {amplitude}")
            result.notes.append(
                f"{comp.ref}: written as an AC source of {amplitude} for "
                f"a .ac sweep; add SIN(...) for a transient run.")
        elif ctype == ComponentType.CURRENT_SOURCE:
            plus, minus = _source_terminals(comp)
            # SPICE drives current through the source from the first node
            # to the second, i.e. out of the '+' terminal into the circuit.
            lines.append(f"{_spice_ref('I', comp.ref)} {node(plus, comp)} "
                         f"{node(minus, comp)} DC "
                         f"{value_of(comp, '1m', ' amps')}")
        elif ctype in (ComponentType.TRANSISTOR_NPN,
                       ComponentType.TRANSISTOR_PNP) and len(pins) >= 3:
            named = _by_pin_names(comp, (("c", "collector"), ("b", "base"),
                                         ("e", "emitter")))
            c, b, e = named if named is not None else (pins[0], pins[1],
                                                       pins[2])
            npn = ctype == ComponentType.TRANSISTOR_NPN
            model = _model_name(comp, "SCHEMACCESS_NPN" if npn
                                else "SCHEMACCESS_PNP")
            add_model(model, "NPN" if npn else "PNP")
            lines.append(f"{_spice_ref('Q', comp.ref)} {node(c, comp)} "
                         f"{node(b, comp)} {node(e, comp)} {model}")
        elif ctype in (ComponentType.NMOS, ComponentType.PMOS) \
                and len(pins) >= 3:
            named = _by_pin_names(comp, (("d", "drain"), ("g", "gate"),
                                         ("s", "source")))
            d, g, s = named if named is not None else (pins[0], pins[1],
                                                       pins[2])
            nmos = ctype == ComponentType.NMOS
            model = _model_name(comp, "SCHEMACCESS_NMOS" if nmos
                                else "SCHEMACCESS_PMOS")
            add_model(model, "NMOS" if nmos else "PMOS")
            # Bulk is tied to source: the schematic symbol has no bulk pin.
            lines.append(f"{_spice_ref('M', comp.ref)} {node(d, comp)} "
                         f"{node(g, comp)} {node(s, comp)} {node(s, comp)} "
                         f"{model}")
            result.notes.append(
                f"{comp.ref}: the MOSFET bulk is tied to its source "
                f"(the schematic symbol has no bulk pin).")
        elif ctype in (ComponentType.NJFET, ComponentType.PJFET) \
                and len(pins) >= 3:
            named = _by_pin_names(comp, (("d", "drain"), ("g", "gate"),
                                         ("s", "source")))
            d, g, s = named if named is not None else (pins[0], pins[1],
                                                       pins[2])
            njf = ctype == ComponentType.NJFET
            model = _model_name(comp, "SCHEMACCESS_NJF" if njf
                                else "SCHEMACCESS_PJF")
            add_model(model, "NJF" if njf else "PJF")
            lines.append(f"{_spice_ref('J', comp.ref)} {node(d, comp)} "
                         f"{node(g, comp)} {node(s, comp)} {model}")
        elif ctype == ComponentType.OPAMP:
            roles = _sim_pin_roles(comp)
            plus = _pin_named(pins, ("+", "in+", "inp", "vin+", "ninv")) \
                or next((p for p in pins
                         if roles.get(p.number) in ("+", "in+")), None)
            minus = _pin_named(pins, ("-", "in-", "inn", "vin-", "inv")) \
                or next((p for p in pins
                         if roles.get(p.number) in ("-", "in-")), None)
            out = _pin_named(pins, ("out", "output", "vout", "o")) \
                or next((p for p in pins
                         if roles.get(p.number) == "out"), None) \
                or next((p for p in pins if p.etype == "output"), None)
            if plus is None or minus is None or out is None:
                unsupported(comp, "has no recognisable +/-/out pins")
                continue
            need_opamp = True
            lines.append(f"{_spice_ref('X', comp.ref)} {node(plus, comp)} "
                         f"{node(minus, comp)} {node(out, comp)} "
                         f"SCHEMACCESS_OPAMP")
            result.notes.append(
                f"{comp.ref}: written against an ideal op-amp subcircuit "
                f"included at the end of the deck.")
        elif ctype == ComponentType.FUSE:
            a, b = pins[0], pins[1]
            lines.append(f"{_spice_ref('R', comp.ref)} {node(a, comp)} "
                         f"{node(b, comp)} 1m")
            result.notes.append(
                f"{comp.ref}: an intact fuse became a 1 milliohm resistor.")
        elif ctype in (ComponentType.SWITCH, ComponentType.PUSHBUTTON):
            a, b = pins[0], pins[1]
            base = _SPICE_NAME_RE.sub("_", comp.ref)
            unsupported(
                comp,
                "needs a control node SPICE cannot infer",
                [f"S{base} {node(a, comp)} {node(b, comp)} "
                 f"CTRLP CTRLM {base}_MODEL",
                 f".model {base}_MODEL SW(Ron=1m Roff=1G Vt=0.5)",
                 "  (or simply short/open the two nodes above)"])
        elif ctype in (ComponentType.CONTROLLED_VOLTAGE_SOURCE,
                       ComponentType.CONTROLLED_CURRENT_SOURCE):
            plus, minus = _source_terminals(comp)
            base = _SPICE_NAME_RE.sub("_", comp.ref)
            letter = ("E" if ctype == ComponentType.CONTROLLED_VOLTAGE_SOURCE
                      else "G")
            unsupported(
                comp,
                "is a controlled source; the schematic does not record "
                "which nodes control it",
                [f"{letter}{base} {node(plus, comp)} {node(minus, comp)} "
                 f"CTRLP CTRLM GAIN"])
        elif ctype == ComponentType.TRANSFORMER and len(pins) >= 4:
            named = _by_pin_names(comp, (("aa", "p1", "1"), ("ab", "p2", "2"),
                                         ("sa", "s1", "3"), ("sb", "s2", "4")))
            pa, pb, sa, sb = named if named is not None else pins[:4]
            base = _SPICE_NAME_RE.sub("_", comp.ref)
            unsupported(
                comp,
                "needs two inductances and a coupling coefficient the "
                "schematic does not carry",
                [f"L{base}P {node(pa, comp)} {node(pb, comp)} 1     "
                 f"; primary",
                 f"L{base}S {node(sa, comp)} {node(sb, comp)} 1     "
                 f"; secondary (ratio = sqrt(Ls/Lp))",
                 f"K{base} L{base}P L{base}S 0.99"])
        else:
            unsupported(comp, "has no SPICE device equivalent")

    if models:
        lines.append("")
        lines.append("* Device models - replace with vendor models as needed")
        lines.extend(sorted(models))
    if need_opamp:
        lines.append("")
        lines.extend(_IDEAL_OPAMP)

    lines.append("")
    lines.append("* Add an analysis, e.g.  .op   .tran 1u 5m   "
                 ".ac dec 20 1 1Meg")
    lines.append(".end")
    lines.append("")
    result.text = "\n".join(lines)
    return result


# ---------------------------------------------------------------------------
# KiCad netlist
# ---------------------------------------------------------------------------

def _quote(value) -> str:
    """Quote a string the way KiCad writes S-expression atoms."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ref_key(ref: str) -> Tuple[str, int, str]:
    prefix = "".join(ch for ch in ref if not ch.isdigit())
    digits = "".join(ch for ch in ref if ch.isdigit())
    return (prefix, int(digits) if digits else 0, ref)


def kicad(graph: CircuitGraph, *, source: str = "") -> NetlistResult:
    """Return a KiCad-format netlist (the ``.net`` S-expression).

    This is the same shape KiCad's own ``Tools > Generate Netlist`` writes,
    so the file imports into the tools that already read KiCad netlists.
    No date or tool-version stamp is written, which keeps the output
    byte-identical for identical inputs.
    """
    result = NetlistResult()
    doc = graph.document
    # The file's NAME, never its path.  An absolute path would differ
    # between the desktop and the browser (breaking the promise that both
    # produce the same file) and would publish the author's directory
    # layout inside a netlist they hand to someone else.
    src = source or (doc.source_path if doc is not None else "") or "unknown"
    src = src.replace("\\", "/").rsplit("/", 1)[-1] or "unknown"

    lines: List[str] = [
        '(export (version "E")',
        "  (design",
        f"    (source {_quote(src)})",
        '    (tool "SchemAccess"))',
        "  (components",
    ]
    for comp in sorted(graph.components.values(),
                       key=lambda c: _ref_key(c.ref)):
        lib, sep, part = comp.lib_id.partition(":")
        if not sep:
            lib, part = "", lib
        lines.append(f"    (comp (ref {_quote(comp.ref)})")
        lines.append(f"      (value {_quote(comp.value or '~')})")
        footprint = comp.properties.get("Footprint", "")
        if footprint:
            lines.append(f"      (footprint {_quote(footprint)})")
        lines.append(f"      (libsource (lib {_quote(lib)}) "
                     f"(part {_quote(part)}) "
                     f"(description {_quote(comp.ctype.value)})))")
    lines.append("  )")
    lines.append("  (nets")

    code = 0
    for net in graph.nets:
        if not net.pins:
            continue
        code += 1
        lines.append(f"    (net (code {_quote(code)}) "
                     f"(name {_quote(net.name)})")
        for ref, number in net.pins:
            comp = graph.components.get(ref)
            pin = comp.pins.get(number) if comp is not None else None
            pin_name = pin.name if pin is not None else ""
            pin_type = pin.etype if pin is not None else "passive"
            entry = f"      (node (ref {_quote(ref)}) (pin {_quote(number)})"
            if pin_name and pin_name != "~":
                entry += f" (pinfunction {_quote(pin_name)})"
            entry += f" (pintype {_quote(pin_type)}))"
            lines.append(entry)
        lines.append("    )")
    lines.append("  )")
    lines.append(")")
    lines.append("")
    result.text = "\n".join(lines)
    return result


# ---------------------------------------------------------------------------
# Readable net table
# ---------------------------------------------------------------------------

_KIND_WORD = {
    NetKind.GROUND: "ground",
    NetKind.POWER: "power rail",
    NetKind.NAMED: "labelled",
    NetKind.ANONYMOUS: "unnamed",
}


def text(graph: CircuitGraph) -> NetlistResult:
    """Return a plain-text component and net table, meant to be read.

    Laid out as prose-with-indentation rather than as a grid of separators,
    so a screen reader announces something useful line by line.
    """
    result = NetlistResult()
    doc = graph.document
    name = ""
    if doc is not None and doc.source_path:
        name = doc.source_path.replace("\\", "/").rsplit("/", 1)[-1]

    lines: List[str] = []
    lines.append(f"Netlist for {name}" if name else "Netlist")
    connected = [net for net in graph.nets if net.pins]
    lines.append(f"{len(graph.components)} components, {len(connected)} "
                 f"connected nets.")
    lines.append("")

    lines.append("Components")
    if not graph.components:
        lines.append("  (none)")
    for comp in sorted(graph.components.values(),
                       key=lambda c: _ref_key(c.ref)):
        value = comp.value.strip()
        head = f"  {comp.ref}: {comp.ctype.value}"
        if value and value not in ("~", "?"):
            head += f", {value}"
        lines.append(head + ".")
        for pin in _pins_in_order(comp):
            net = (graph.nets[pin.net_id]
                   if 0 <= pin.net_id < len(graph.nets) else None)
            where = net.name if net is not None else "nothing"
            label = f"pin {pin.number}"
            if pin.name and pin.name != "~":
                label += f" ({pin.name})"
            lines.append(f"    {label} connects to {where}.")
    lines.append("")

    lines.append("Nets")
    if not connected:
        lines.append("  (none)")
    for net in connected:
        members = []
        for ref, number in net.pins:
            comp = graph.components.get(ref)
            pin = comp.pins.get(number) if comp is not None else None
            if pin is not None and pin.name and pin.name != "~":
                members.append(f"{ref} pin {number} ({pin.name})")
            else:
                members.append(f"{ref} pin {number}")
        count = len(net.pins)
        lines.append(f"  {net.name} ({_KIND_WORD[net.kind]}), {count} "
                     f"{'pin' if count == 1 else 'pins'}: "
                     + ", ".join(members) + ".")
    lines.append("")
    result.text = "\n".join(lines)
    return result


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _csv_cell(value) -> str:
    cell = str(value)
    if any(ch in cell for ch in ',"\n\r'):
        return '"' + cell.replace('"', '""') + '"'
    return cell


def csv(graph: CircuitGraph) -> NetlistResult:
    """Return one CSV row per connected pin."""
    result = NetlistResult()
    rows = [["net", "net_kind", "reference", "component", "value",
             "pin", "pin_name", "pin_type"]]
    for net in graph.nets:
        for ref, number in net.pins:
            comp = graph.components.get(ref)
            pin = comp.pins.get(number) if comp is not None else None
            rows.append([
                net.name,
                _KIND_WORD[net.kind],
                ref,
                comp.ctype.value if comp is not None else "",
                comp.value if comp is not None else "",
                number,
                (pin.name if pin is not None and pin.name != "~" else ""),
                (pin.etype if pin is not None else ""),
            ])
    result.text = "\r\n".join(",".join(_csv_cell(c) for c in row)
                              for row in rows) + "\r\n"
    return result


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def generate(graph: CircuitGraph, fmt: str = "spice",
             **kwargs) -> NetlistResult:
    """Generate the netlist named by *fmt* (see :data:`FORMATS`)."""
    if fmt == "spice":
        return spice(graph, **kwargs)
    if fmt == "kicad":
        return kicad(graph, **kwargs)
    if fmt == "text":
        return text(graph)
    if fmt == "csv":
        return csv(graph)
    raise ValueError(f"Unknown netlist format '{fmt}'; "
                     f"expected one of {', '.join(FORMATS)}")

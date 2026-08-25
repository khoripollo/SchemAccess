"""CircuiTikZ generator: KiCad circuit graph -> compilable LaTeX.

Converts a :class:`~schemaccess.model.CircuitGraph` (plus the geometry of
its source :class:`~schemaccess.model.SchematicDocument`) into a complete
standalone LaTeX document using the ``circuitikz`` package.

Design goals:

* **Layout preservation** -- KiCad schematic coordinates (millimetres,
  Y axis down) are mapped linearly onto TikZ coordinates (Y axis up) so
  the rendered drawing matches the original schematic's layout.
* **Determinism** -- identical inputs always produce identical output:
  wires and labels are emitted in document order, components sorted by
  reference, and no timestamps or unordered iterations are used.
* **Robustness** -- unknown or odd components degrade gracefully to a
  labelled rectangle whose pins still land exactly on their true
  positions, so the surrounding wiring stays correct.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Set, Tuple

from .model import (CircuitGraph, Component, ComponentType, Net, NetKind,
                    PinConnection, Point, SchematicDocument)

__all__ = ["generate", "generate_body", "SCALE"]

# Millimetres -> TikZ units.  Chosen so KiCad's grid lands on circuitikz's
# own natural symbol proportions: a symbol pin 2.54 mm off centre maps to
# 0.49 units, exactly where circuitikz puts an op amp's input anchor.  Every
# symbol - bipoles and the op amp alike - therefore draws at its natural
# circuitikz size, the way a hand-written figure looks, and op-amp leads
# land on their pins without any scaling of the shape.  (A standard 7.62 mm
# two-pin element becomes 1.47 units, just over circuitikz's 1.4 default
# bipole length, so components keep a little lead and never collide.)
SCALE = 0.49 / 2.54

# Stub length (TikZ units) between a pin and the generic rectangle body.
_STUB = 0.3
# Minimum width/height (TikZ units) of a generic rectangle body.
_MIN_BODY = 1.0

# Flattened hierarchical sheets are spread 10000 mm apart by the parser so
# their coordinates never collide.  Rendering that verbatim overflows TeX's
# maximum dimension, so horizontal gaps wider than _GAP_THRESHOLD mm (which
# never occur inside one sheet) are compressed to _GAP_MARGIN mm.
_GAP_THRESHOLD = 1000.0
_GAP_MARGIN = 50.0

# ---------------------------------------------------------------------------
# circuitikz bipole keys for two-terminal components (verified by compiling
# against circuitikz 1.6+ and inspecting the rendered output).
# ---------------------------------------------------------------------------
_BIPOLE_KEYS: Dict[ComponentType, str] = {
    ComponentType.RESISTOR: "R",
    ComponentType.POTENTIOMETER: "pR",
    ComponentType.CAPACITOR: "C",
    ComponentType.CAPACITOR_POLARIZED: "cC",
    ComponentType.INDUCTOR: "L",
    ComponentType.DIODE: "D",
    ComponentType.LED: "leD",
    ComponentType.ZENER: "zD",
    ComponentType.VOLTAGE_SOURCE: "V",
    ComponentType.CURRENT_SOURCE: "I",
    ComponentType.BATTERY: "battery1",
    ComponentType.AC_SOURCE: "sV",
    ComponentType.SWITCH: "nos",
    ComponentType.PUSHBUTTON: "nopb",
    ComponentType.FUSE: "fuse",
    ComponentType.CRYSTAL: "generic",
    # KiCad draws behavioural/dependent sources as a diamond, matching
    # circuitikz's controlled-source shape.
    ComponentType.CONTROLLED_SOURCE: "cvsource",
}

_DIODE_TYPES = (ComponentType.DIODE, ComponentType.LED, ComponentType.ZENER)

# Measured geometry of circuitikz's `op amp` node at scale 1 (probed with
# \pgfgetlastxy against circuitikz 1.7): the '+'/'-' input anchors sit at
# (-1.190, +/-0.490) and the out anchor at (+1.190, 0) TikZ units.
# Stretching the node with xscale/yscale so these anchors land exactly on
# the KiCad pin positions makes every lead a zero-length straight join,
# just like the original schematic.
_OPAMP_INPUT_HALF = 0.490
_OPAMP_ANCHOR_X = 1.190
# The '.up' supply anchor sits on the triangle's upper edge; together with
# the apex (the '.out' anchor) it defines that edge, which lets supply
# leads be drawn as straight vertical lines down to the body - the way
# KiCad draws them - instead of slashing across the symbol.
_OPAMP_UP_ANCHOR = (-0.083, 0.539)

#: Size of the drawn op amp.  ``None`` means "scale uniformly so the input
#: anchors coincide with the KiCad input pins", which keeps the leads
#: perfectly straight; with :data:`SCALE` above this works out at ~1.0 (the
#: natural circuitikz size) for standard symbols.  Set a float to force a
#: fixed size instead.
OPAMP_SCALE: Optional[float] = None


def _opamp_edge_y(local_x: float) -> float:
    """Height of the op amp's upper edge at *local_x* (node coordinates)."""
    ax, ay = _OPAMP_UP_ANCHOR
    slope = (0.0 - ay) / (_OPAMP_ANCHOR_X - ax)
    return max(ay + slope * (local_x - ax), 0.0)

# Measured circuitikz geometry at natural size (probed with \pgfgetlastxy
# against circuitikz 1.7).  Per style: the control anchor (B/G), the two
# channel anchors, the control anchor's y offset from the node centre, and
# the y offset of the *first* channel anchor.  Note that the p-type shapes
# put their first channel terminal at the BOTTOM, and that a JFET's gate
# sits off the centre line - both of which have to be compensated for when
# placing the node, or the leads come out with a step in them.
_TRANSISTOR_STYLES: Dict[
        ComponentType, Tuple[str, str, str, str, float, float]] = {
    ComponentType.TRANSISTOR_NPN: ("npn", "B", "C", "E", 0.0, 0.77),
    ComponentType.TRANSISTOR_PNP: ("pnp", "B", "C", "E", 0.0, -0.77),
    ComponentType.NMOS: ("nmos", "G", "D", "S", 0.0, 0.77),
    ComponentType.PMOS: ("pmos", "G", "D", "S", 0.0, -0.77),
    ComponentType.NJFET: ("njfet", "G", "D", "S", -0.2695, 0.77),
    ComponentType.PJFET: ("pjfet", "G", "D", "S", 0.2695, -0.77),
}

_TR_CHANNEL_Y = 0.77
_XFMR_ANCHOR = 1.0495

# ---------------------------------------------------------------------------
# JFET geometry, taken from KiCad's own Transistor_FET symbols.
#
# circuitikz draws a JFET's gate a third of the way down the channel, and
# its keys cannot move it without collapsing the channel, so JFETs are drawn
# directly instead.  All values are millimetres in KiCad library coordinates
# (Y up), measured relative to the drain/source pin column, so the symbol can
# be anchored on the real pins at any size or orientation.
# ---------------------------------------------------------------------------
_JFET_BAR_X = -2.286        # channel bar, left of the D/S column
_JFET_BAR_HALF = 1.905      # half the bar's height
_JFET_CONN_Y = 1.397        # where the D/S leads meet the bar
_JFET_STUB_Y = 2.54         # where the D/S leads leave the pin column
_JFET_GATE_END = -5.08      # outer end of the gate lead (on the centre line)
_JFET_ARROW_TIP = -2.54     # gate arrow, tip toward the channel
_JFET_ARROW_BACK = -3.556
_JFET_ARROW_HALF = 0.381
#: Height of the drawn body, used to keep the label clear of it.  KiCad
#: encircles its JFET symbols; circuitikz's transistors are drawn bare, so
#: the circle is left out to match the rest of the output.
_JFET_BODY_HALF = 2.54

_GATE_STYLES: Dict[ComponentType, str] = {
    ComponentType.AND_GATE: "and port",
    ComponentType.OR_GATE: "or port",
    ComponentType.NOT_GATE: "not port",
    ComponentType.NAND_GATE: "nand port",
    ComponentType.NOR_GATE: "nor port",
    ComponentType.XOR_GATE: "xor port",
    ComponentType.XNOR_GATE: "xnor port",
    ComponentType.BUFFER: "buffer port",
}

_GROUND_NAMES = {"gnd", "gnda", "gndd", "gndref", "gndpwr", "agnd", "dgnd",
                 "earth", "0", "gnds", "vss"}
_NEGATIVE_RAIL_HINTS = ("VEE", "VSS", "V-", "-V")

# ---------------------------------------------------------------------------
# LaTeX escaping
# ---------------------------------------------------------------------------

_CHAR_MAP: Dict[str, str] = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "µ": r"$\mu$",       # micro sign
    "μ": r"$\mu$",       # Greek mu
    "Ω": r"$\Omega$",    # Greek Omega
    "Ω": r"$\Omega$",    # Ohm sign
    "°": r"$^{\circ}$",  # degree sign
    # Symbols that turn up in component values: phasors (10<90), tolerances,
    # ratios, exponents and the usual Greek.
    "∠": r"$\angle$",
    "±": r"$\pm$",
    "∓": r"$\mp$",
    "×": r"$\times$",
    "·": r"$\cdot$",
    "÷": r"$\div$",
    "≈": r"$\approx$",
    "≤": r"$\leq$",
    "≥": r"$\geq$",
    "≠": r"$\neq$",
    "∞": r"$\infty$",
    "²": r"$^{2}$",
    "³": r"$^{3}$",
    "Δ": r"$\Delta$",
    "δ": r"$\delta$",
    "π": r"$\pi$",
    "ω": r"$\omega$",
    "θ": r"$\theta$",
    "φ": r"$\varphi$",
    "λ": r"$\lambda$",
    "α": r"$\alpha$",
    "β": r"$\beta$",
    "–": "--",       # en dash
    "—": "---",      # em dash
    "‘": "`",
    "’": "'",
    "“": "``",
    "”": "''",
    "\n": " ",
    "\r": " ",
    "\t": " ",
}


def _escape(text: str) -> str:
    """Escape *text* so it is safe inside LaTeX node/label content."""
    out: List[str] = []
    for ch in text:
        if ch in _CHAR_MAP:
            out.append(_CHAR_MAP[ch])
        elif ord(ch) < 128:
            out.append(ch)
        # Other non-ASCII characters are dropped: pdflatex may not have a
        # glyph mapping for them and a missing character beats a crash.
        # _unmapped() reports them so the loss is never silent.
    return "".join(out)


def _unmapped(text: str) -> List[str]:
    """Characters of *text* that :func:`_escape` would silently drop."""
    return sorted({ch for ch in text
                   if ch not in _CHAR_MAP and ord(ch) >= 128})


_BARE_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_MICRO_VALUE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*[uµμ]([A-Za-z]*)$")


def _is_placeholder_value(value: str, lib_id: str,
                          ctype: ComponentType) -> bool:
    """True when the KiCad value is just a placeholder (R, C, ~, ?, empty)
    and should not be printed.  For generic two-terminal devices a value
    that merely repeats the library symbol name (LED, D_Zener, VDC...) is
    also a placeholder; for ICs the part name is meaningful and kept."""
    v = value.strip()
    if v in ("", "~", "?"):
        return True
    if "${" in v:
        return True  # unresolved KiCad text variable, e.g. ${SIM.PARAMS}
    if v.upper() in ("R", "C", "L", "D"):
        return True
    lib_name = lib_id.split(":", 1)[-1] if lib_id else ""
    return bool(lib_name) and v.lower() == lib_name.lower() \
        and ctype in _BIPOLE_KEYS


def _format_value(comp: Component) -> str:
    """Return the LaTeX annotation text for a component value ('' to omit).

    Returns '' when the Value field's "Show" checkbox is off in KiCad, so
    the drawing shows exactly the fields the schematic shows.
    """
    if not comp.shows("Value"):
        return ""
    raw = comp.value.strip()
    if _is_placeholder_value(raw, comp.lib_id, comp.ctype):
        return ""
    if comp.ctype in (ComponentType.RESISTOR, ComponentType.POTENTIOMETER) \
            and _BARE_NUMBER_RE.match(raw):
        return raw + r"~$\Omega$"
    micro = _MICRO_VALUE_RE.match(raw)
    if micro:
        return micro.group(1) + r"\,$\mu$" + _escape(micro.group(2))
    return _escape(raw)


_NODE_NAME_RE = re.compile(r"[^A-Za-z0-9]")


def _node_name(ref: str) -> str:
    """A TikZ-safe node name derived from a reference designator."""
    return "n" + (_NODE_NAME_RE.sub("", ref) or "x")


def _ref_sort_key(ref: str) -> Tuple[str, int, str]:
    """Natural sort key for reference designators (R1 < R2 < R10)."""
    prefix = "".join(ch for ch in ref if not ch.isdigit())
    digits = "".join(ch for ch in ref if ch.isdigit())
    return (prefix, int(digits) if digits else 0, ref)


def _pin_sort_key(number: str) -> Tuple[int, int, str]:
    if number.isdigit():
        return (0, int(number), number)
    return (1, 0, number)


# ---------------------------------------------------------------------------
# Coordinate transform
# ---------------------------------------------------------------------------

class _Transform:
    """Maps KiCad millimetre coordinates (Y down) to TikZ units (Y up)."""

    def __init__(self, graph: CircuitGraph) -> None:
        points: List[Point] = []
        doc = graph.document
        if doc is not None:
            for wire in doc.wires:
                points.extend(wire.points)
            for inst in doc.symbols:
                lib = doc.lib_symbol_for(inst)
                if lib is None:
                    continue
                for pin in lib.pins_for_unit(inst.unit):
                    points.append(inst.pin_position(pin))
        if not points:  # fall back to the electrical graph's pin positions
            for ref in sorted(graph.components, key=_ref_sort_key):
                comp = graph.components[ref]
                for number in sorted(comp.pins, key=_pin_sort_key):
                    points.append(comp.pins[number].position)
        # Compress huge horizontal gaps (flattened sheet islands) so the
        # drawing stays within TeX's maximum dimension.  For ordinary
        # single-sheet schematics no gap exceeds the threshold and the
        # mapping is exactly linear.
        self._breaks: List[Tuple[float, float]] = []  # (from_x, shift)
        self.compressed_gaps = 0
        shift = 0.0
        prev: Optional[float] = None
        for x in sorted({round(p[0], 4) for p in points}):
            if prev is not None and x - prev > _GAP_THRESHOLD:
                shift += (x - prev) - _GAP_MARGIN
                self._breaks.append((x, shift))
                self.compressed_gaps += 1
            prev = x
        if points:
            self.min_x = min(self._map_x(p[0]) for p in points)
            self.max_y = max(p[1] for p in points)
        else:
            self.min_x = 0.0
            self.max_y = 0.0

    def _map_x(self, x: float) -> float:
        shift = 0.0
        for break_x, break_shift in self._breaks:
            if x >= break_x - 1e-6:
                shift = break_shift
            else:
                break
        return x - shift

    def point(self, p: Point) -> Tuple[float, float]:
        tx = round((self._map_x(p[0]) - self.min_x) * SCALE, 3)
        ty = round((self.max_y - p[1]) * SCALE, 3)
        return (tx, ty)

    def coord(self, p: Point) -> str:
        tx, ty = self.point(p)
        return f"({_fmt(tx)},{_fmt(ty)})"


def _fmt(v: float) -> str:
    """Deterministic short float formatting: 2.0 -> '2', 1.575 -> '1.575'."""
    v = round(v, 3)
    if v == 0:
        v = 0.0  # normalise -0.0
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s or "0"


def _xy(x: float, y: float) -> str:
    return f"({_fmt(x)},{_fmt(y)})"


# ---------------------------------------------------------------------------
# Pin ordering / polarity for two-terminal elements
# ---------------------------------------------------------------------------

def _pin_named(pins: List[PinConnection],
               names: Tuple[str, ...]) -> Optional[PinConnection]:
    for pin in pins:
        if pin.name.strip().lower() in names:
            return pin
    return None


def _sim_pin_roles(comp: Component) -> Dict[str, str]:
    """Parse KiCad's ``Sim.Pins`` property into pin number -> role.

    Simulation symbols (Simulation_SPICE:VDC, OPAMP...) often have unnamed
    pins and carry polarity only here, e.g. ``"1=+ 2=-"`` or
    ``"1=in+ 2=in- 3=vcc 4=vee 5=out"``.  Roles are lower-cased.
    """
    roles: Dict[str, str] = {}
    for token in comp.properties.get("Sim.Pins", "").split():
        num, sep, role = token.partition("=")
        if sep:
            roles[num.strip()] = role.strip().lower()
    return roles


def _bipole_pin_order(comp: Component) -> Tuple[PinConnection, PinConnection]:
    """Return (first, second) pins so the circuitikz bipole polarity is right.

    Verified conventions (circuitikz 1.7, rendered and inspected):

    * ``to[D]`` (and leD/zD) conducts from the first coordinate to the
      second: anode first, cathode second.  KiCad diode pin 1 is the
      cathode (name ``K``), pin 2 the anode (name ``A``).
    * ``to[V]``/``to[sV]``/``to[battery1]`` place the **plus** terminal
      (the ``+`` sign / long battery bar) at the **second** coordinate,
      so the pin named ``+`` goes second.
    * ``to[I]`` points its current arrow toward the second coordinate.
      KiCad/SPICE draw the internal arrow from ``+`` to ``-``, so the
      pin named ``+`` goes first for current sources.
    * A polarized capacitor ``to[cC]`` has its straight (positive) plate
      at the first coordinate; KiCad pin 1 is positive, so number order
      already matches.
    """
    pins = [comp.pins[k] for k in sorted(comp.pins, key=_pin_sort_key)]
    a, b = pins[0], pins[1]
    if comp.ctype in _DIODE_TYPES:
        anode = _pin_named(pins, ("a", "anode"))
        cathode = _pin_named(pins, ("k", "c", "cathode"))
        if anode is not None and cathode is not None:
            return (anode, cathode)
        return (b, a)  # KiCad convention: pin 1 = K, pin 2 = A
    if comp.ctype.is_source:
        roles = _sim_pin_roles(comp)
        plus = _pin_named(pins, ("+", "p", "plus", "n+", "in+")) or next(
            (p for p in pins if roles.get(p.number) == "+"), None)
        minus = _pin_named(pins, ("-", "n", "minus", "n-", "in-")) or next(
            (p for p in pins if roles.get(p.number) == "-"), None)
        if plus is None:
            # KiCad sources (VDC, VSIN, Battery...) put '+' on pin 1 even
            # when the pins are unnamed and Sim.Pins is absent.
            plus = next((p for p in pins if p.number == "1"), a)
        if minus is None or minus is plus:
            minus = next((p for p in pins if p is not plus), b)
        if comp.ctype == ComponentType.CURRENT_SOURCE:
            return (plus, minus)
        return (minus, plus)
    return (a, b)


# ---------------------------------------------------------------------------
# Section emitters
# ---------------------------------------------------------------------------

def _emit_wires(doc: SchematicDocument, tr: _Transform) -> List[str]:
    lines: List[str] = []
    for wire in doc.wires:
        if len(wire.points) < 2:
            continue
        path = " -- ".join(tr.coord(p) for p in wire.points)
        lines.append(f"\\draw {path};")
    return lines


def _emit_junctions(doc: SchematicDocument, tr: _Transform) -> List[str]:
    return [f"\\draw {tr.coord((j.x, j.y))} node[circ]{{}};"
            for j in doc.junctions]


def _bipole_options(comp: Component, key: str) -> str:
    opts = [key]
    if comp.shows("Reference"):
        opts.append(f"l={{{_escape(comp.ref)}}}")
    value = _format_value(comp)
    if value:
        opts.append(f"a={{{value}}}")
    return ", ".join(opts)


def _emit_two_terminal(comp: Component, tr: _Transform) -> List[str]:
    key = _BIPOLE_KEYS[comp.ctype]
    if comp.ctype == ComponentType.POTENTIOMETER and len(comp.pins) == 3:
        return _emit_potentiometer(comp, tr)
    first, second = _bipole_pin_order(comp)
    return [f"\\draw {tr.coord(first.position)} "
            f"to[{_bipole_options(comp, key)}] {tr.coord(second.position)};"]


def _emit_potentiometer(comp: Component, tr: _Transform) -> List[str]:
    """A 3-pin potentiometer: track between pins 1 and 3, wiper to pin 2."""
    numbers = sorted(comp.pins, key=_pin_sort_key)
    end_a = comp.pins[numbers[0]]
    wiper = comp.pins[numbers[1]]
    end_b = comp.pins[numbers[2]]
    name = _node_name(comp.ref)
    lines = [f"\\draw {tr.coord(end_a.position)} "
             f"to[{_bipole_options(comp, 'pR')}, name={name}] "
             f"{tr.coord(end_b.position)};",
             f"\\draw ({name}.wiper) -- {tr.coord(wiper.position)};"]
    return lines


def _classify_gate_pins(comp: Component) -> Tuple[List[PinConnection],
                                                  List[PinConnection],
                                                  List[PinConnection]]:
    """Split gate pins into (inputs, outputs, other) using electrical type,
    with a positional fallback when the symbol lacks type data."""
    pins = [comp.pins[k] for k in sorted(comp.pins, key=_pin_sort_key)]
    inputs = [p for p in pins if p.etype == "input"]
    outputs = [p for p in pins if p.etype == "output"]
    used = set(id(p) for p in inputs) | set(id(p) for p in outputs)
    other = [p for p in pins if id(p) not in used]
    if not inputs and not outputs:
        signal = [p for p in pins if not p.etype.startswith("power")]
        if len(signal) >= 2:
            inputs, outputs = signal[:-1], [signal[-1]]
            used = set(id(p) for p in inputs) | set(id(p) for p in outputs)
            other = [p for p in pins if id(p) not in used]
    return inputs, outputs, other


def _emit_gate(comp: Component, tr: _Transform, warnings: List[str],
               dangling: Set[int]) -> List[str]:
    style = _GATE_STYLES[comp.ctype]
    inputs, outputs, other = _classify_gate_pins(comp)
    max_inputs = 1 if comp.ctype in (ComponentType.NOT_GATE,
                                     ComponentType.BUFFER) else 2
    if len(outputs) != 1 or not 1 <= len(inputs) <= max_inputs:
        warnings.append(
            f"{comp.ref}: gate pin pattern not recognised; drawing a box.")
        return _emit_generic_box(comp, tr)
    name = _node_name(comp.ref)
    cx, cy = tr.point(comp.position)
    lines = [f"\\node[{style}] ({name}) at {_xy(cx, cy)} {{}};"]
    for idx, pin in enumerate(inputs, start=1):
        lines.append(f"\\draw ({name}.in {idx}) -- {tr.coord(pin.position)};")
    lines.append(f"\\draw ({name}.out) -- {tr.coord(outputs[0].position)};")
    for pin in other:  # power pins etc.: keep the connection point honest
        if pin.net_id < 0 or pin.net_id in dangling:
            continue  # floating optional pin: no lead
        lines.append(f"\\draw {tr.coord(pin.position)} -- ({name}.center);")
    lines.extend(_label_node(comp, cx, cy + 0.45))
    return lines


def _emit_opamp(comp: Component, tr: _Transform, warnings: List[str],
                dangling: Set[int]) -> List[str]:
    name = _node_name(comp.ref)
    cx, cy = tr.point(comp.position)
    roles = _sim_pin_roles(comp)
    matched: Dict[str, str] = {}
    for number in sorted(comp.pins, key=_pin_sort_key):
        pin = comp.pins[number]
        pname = pin.name.strip().lower()
        role = roles.get(number, "")
        if (pname in ("-", "in-", "inn") or role in ("-", "in-")) \
                and "-" not in matched.values():
            matched[number] = "-"
        elif (pname in ("+", "in+", "inp") or role in ("+", "in+")) \
                and "+" not in matched.values():
            matched[number] = "+"
        elif pname in ("v+", "vcc", "vdd") or role in ("vcc", "vdd", "v+"):
            matched[number] = "up"
        elif pname in ("v-", "vee", "vss", "gnd") \
                or role in ("vee", "vss", "v-"):
            matched[number] = "down"
        elif pin.etype == "output" or role == "out" \
                or pname in ("out", "output", "~", ""):
            if "out" not in matched.values():
                matched[number] = "out"

    # KiCad symbols may put the non-inverting input on top (e.g.
    # Simulation_SPICE:OPAMP), or the symbol may be rotated/mirrored;
    # circuitikz's default op amp has '-' on top.  Compare the true pin
    # heights and flip the node so the anchor-to-pin leads never cross.
    node_style = "op amp"
    plus_no = next((n for n, a in matched.items() if a == "+"), None)
    minus_no = next((n for n, a in matched.items() if a == "-"), None)
    out_no = next((n for n, a in matched.items() if a == "out"), None)
    plus_y = minus_y = None
    if plus_no is not None and minus_no is not None:
        plus_y = tr.point(comp.pins[plus_no].position)[1]
        minus_y = tr.point(comp.pins[minus_no].position)[1]
        if plus_y > minus_y:
            node_style = "op amp, noinv input up"
    # Supply anchors likewise follow the actual pin geometry.
    for number, anchor in list(matched.items()):
        if anchor in ("up", "down"):
            pin_y = tr.point(comp.pins[number].position)[1]
            matched[number] = "up" if pin_y >= cy else "down"

    # circuitikz's own 'op amp' shape, scaled UNIFORMLY (never stretched,
    # so the triangle keeps its proper proportions) by just enough that
    # its input anchors sit at the KiCad input-pin heights.  The leads
    # into the inputs and the output are then straight horizontal lines,
    # exactly as in a hand-written circuitikz figure.
    mirrored = False
    if out_no is not None and plus_no is not None and minus_no is not None:
        out_x = tr.point(comp.pins[out_no].position)[0]
        in_x = (tr.point(comp.pins[plus_no].position)[0]
                + tr.point(comp.pins[minus_no].position)[0]) / 2.0
        mirrored = out_x < in_x

    scale = OPAMP_SCALE
    node_y = cy
    if plus_y is not None and minus_y is not None:
        if scale is None:  # match the KiCad input-pin spacing exactly
            wanted = abs(plus_y - minus_y) / 2.0
            scale = (min(max(wanted / _OPAMP_INPUT_HALF, 0.5), 2.5)
                     if wanted > 0.05 else 1.0)
        node_y = (plus_y + minus_y) / 2.0
    if scale is None:
        scale = 1.0
    if abs(scale - 1.0) > 1e-3 or mirrored:
        node_style += (f", xscale={_fmt(-scale if mirrored else scale)}"
                       f", yscale={_fmt(scale)}")

    def anchor_y(anchor: str) -> float:
        """Absolute y of an input/output anchor after placement."""
        if anchor == "+":
            return node_y + _OPAMP_INPUT_HALF * scale * (
                1.0 if "noinv input up" in node_style else -1.0)
        if anchor == "-":
            return node_y + _OPAMP_INPUT_HALF * scale * (
                -1.0 if "noinv input up" in node_style else 1.0)
        return node_y  # 'out' sits on the centre line

    def supply_lead(pin: PinConnection, anchor: str) -> str:
        """Lead from a supply pin to the body, vertical where possible -
        the way KiCad draws V+/V- pin leads."""
        px, py = tr.point(pin.position)
        local_x = (-(px - cx) if mirrored else (px - cx)) / scale
        edge = _opamp_edge_y(local_x) * scale
        edge_y = node_y + edge if anchor == "up" else node_y - edge
        inside = abs(local_x) < _OPAMP_ANCHOR_X
        if inside and ((anchor == "up" and py > edge_y)
                       or (anchor == "down" and py < edge_y)):
            return f"\\draw {_xy(px, py)} -- {_xy(px, edge_y)};"
        # Pin sits outside the body outline: route vertically, then across.
        return f"\\draw ({name}.{anchor}) |- {_xy(px, py)};"

    # Put the label clear of the body and of anything wired above it.
    label_y = max([node_y + 0.98 * scale]
                  + [tr.point(p.position)[1] for p in comp.pins.values()
                     if p.net_id >= 0 and p.net_id not in dangling])
    lines = [f"\\node[{node_style}] ({name}) at {_xy(cx, node_y)} {{}};"]
    lines.extend(_label_node(comp, cx, label_y + 0.25))
    for number in sorted(comp.pins, key=_pin_sort_key):
        pin = comp.pins[number]
        anchor = matched.get(number)
        unconnected = pin.net_id < 0 or pin.net_id in dangling
        if anchor is None:
            if unconnected:
                continue  # optional pin with nothing attached: no lead
            warnings.append(f"{comp.ref}: pin {number} ('{pin.name}') has "
                            f"no op-amp anchor; drawing a plain lead.")
            lines.append(
                f"\\draw {tr.coord(pin.position)} -- ({name}.center);")
        elif anchor in ("up", "down"):
            if unconnected:
                continue  # supply pin left floating in the schematic
            lines.append(supply_lead(pin, anchor))
        else:
            # Inputs and output: a straight horizontal lead when the anchor
            # already sits at the pin's height, otherwise step vertically
            # right at the symbol and then run across, so the bend reads as
            # part of the pin lead rather than a kink out in the wiring.
            py = tr.point(pin.position)[1]
            joiner = "--" if abs(anchor_y(anchor) - py) < 5e-3 else "|-"
            lines.append(
                f"\\draw ({name}.{anchor}) {joiner} {tr.coord(pin.position)};")
    return lines


def _emit_transistor(comp: Component, tr: _Transform,
                     warnings: List[str]) -> List[str]:
    """A circuitikz transistor node placed so its leads stay orthogonal.

    circuitikz puts the channel anchors (C/E, D/S) on the node's centre
    line and the control anchor (B/G) to its left; KiCad puts the channel
    pins on a common vertical and the control pin on the opposite side.
    Centring the node on the channel pins' x therefore makes the channel
    leads vertical and the control lead horizontal - no diagonals, and the
    symbol keeps circuitikz's natural size.
    """
    style, control, first, second, ctrl_dy, first_dy = \
        _TRANSISTOR_STYLES[comp.ctype]
    anchors = (control, first, second)
    name = _node_name(comp.ref)

    assigned: Dict[str, str] = {}
    remaining = dict(zip(anchors, anchors))
    for number in sorted(comp.pins, key=_pin_sort_key):
        letter = comp.pins[number].name.strip()[:1].upper()
        if letter in remaining:
            assigned[number] = remaining.pop(letter)

    by_anchor = {a: comp.pins[n] for n, a in assigned.items()}
    ctrl_pin = by_anchor.get(control)
    first_pin, second_pin = by_anchor.get(first), by_anchor.get(second)

    cx, cy = tr.point(comp.position)
    mirrored = False
    flipped = False
    if first_pin is not None and second_pin is not None:
        fx, fy = tr.point(first_pin.position)
        _sx, sy = tr.point(second_pin.position)
        # Centre on the channel pins' x so their leads run straight down.
        cx = fx
        # circuitikz's p-type shapes carry their first channel terminal at
        # the bottom; flip vertically when KiCad has it the other way up.
        flipped = (fy > sy) != (first_dy > 0)
        if ctrl_pin is not None:
            # Place vertically so the control anchor lands on its own pin -
            # this is what keeps a JFET's offset gate lead horizontal.
            cy = tr.point(ctrl_pin.position)[1] - (
                -ctrl_dy if flipped else ctrl_dy)
            mirrored = tr.point(ctrl_pin.position)[0] > cx
        else:
            cy = (fy + sy) / 2.0

    options = [style]
    if mirrored:
        options.append("xscale=-1")
    if flipped:
        options.append("yscale=-1")
    lines = [f"\\node[{', '.join(options)}] ({name}) at {_xy(cx, cy)} {{}};"]
    lines.extend(_label_node(comp, cx, cy + _TR_CHANNEL_Y + 0.15))

    for number in sorted(comp.pins, key=_pin_sort_key):
        pin = comp.pins[number]
        anchor = assigned.get(number)
        px, py = tr.point(pin.position)
        if anchor is None:
            warnings.append(f"{comp.ref}: pin {number} ('{pin.name}') has "
                            f"no {style} anchor; drawing a plain lead.")
            lines.append(f"\\draw {_xy(px, py)} -- ({name}.center);")
            continue
        if anchor == control:
            anchor_y = cy + (-ctrl_dy if flipped else ctrl_dy)
            joiner = "--" if abs(py - anchor_y) < 5e-3 else "|-"
        else:
            joiner = "--" if abs(px - cx) < 5e-3 else "-|"
        lines.append(f"\\draw ({name}.{anchor}) {joiner} {_xy(px, py)};")
    return lines


def _emit_jfet(comp: Component, tr: _Transform,
               warnings: List[str]) -> List[str]:
    """Draw a JFET the way KiCad does: a channel bar with the gate entering
    on its centre line, stepped drain/source leads and a body circle.

    circuitikz's own ``njfet``/``pjfet`` place the gate off the centre line
    and offer no way to centre it without flattening the channel, so the
    symbol is drawn from KiCad's geometry instead.  Every lead is straight.
    """
    by_name = {}
    for number in sorted(comp.pins, key=_pin_sort_key):
        letter = comp.pins[number].name.strip()[:1].upper()
        if letter in ("D", "G", "S") and letter not in by_name:
            by_name[letter] = comp.pins[number]
    if len(by_name) != 3:
        warnings.append(f"{comp.ref}: JFET pins not recognised; drawing a box.")
        return _emit_generic_box(comp, tr)

    dx, dy = tr.point(by_name["D"].position)
    sx, sy = tr.point(by_name["S"].position)
    gx, gy = tr.point(by_name["G"].position)
    chx = (dx + sx) / 2.0
    cy = (dy + sy) / 2.0
    mx = -1.0 if gx > chx else 1.0          # gate on the right: mirror
    # The drain is normally on top, but honour whatever KiCad has.
    upper, lower = ("D", "S") if dy >= sy else ("S", "D")

    def at(mm_x: float, mm_y: float) -> str:
        return _xy(chx + mx * mm_x * SCALE, cy + mm_y * SCALE)

    lines = [
        f"\\draw[line width=0.8pt] {at(_JFET_BAR_X, -_JFET_BAR_HALF)} -- "
        f"{at(_JFET_BAR_X, _JFET_BAR_HALF)};",
        # gate lead, straight along the centre line
        f"\\draw {at(_JFET_BAR_X, 0)} -- {at(_JFET_GATE_END, 0)};",
    ]
    # Gate arrow: points at the channel for an N-JFET, away for a P-JFET.
    tip, back = _JFET_ARROW_TIP, _JFET_ARROW_BACK
    if comp.ctype == ComponentType.PJFET:
        tip, back = back, tip
    lines.append(
        f"\\fill {at(tip, 0)} -- {at(back, _JFET_ARROW_HALF)} -- "
        f"{at(back, -_JFET_ARROW_HALF)} -- cycle;")
    # Stepped leads from the bar out to the pin column, then to the pins.
    for name, sign in ((upper, 1.0), (lower, -1.0)):
        pin_x, pin_y = tr.point(by_name[name].position)
        lines.append(
            f"\\draw {at(_JFET_BAR_X, sign * _JFET_CONN_Y)} -- "
            f"{at(0, sign * _JFET_CONN_Y)} -- {at(0, sign * _JFET_STUB_Y)} "
            f"-- {_xy(pin_x, pin_y)};")
    lines.append(f"\\draw {at(_JFET_GATE_END, 0)} -- {_xy(gx, gy)};")
    lines.extend(_label_node(comp, chx, cy + _JFET_BODY_HALF * SCALE + 0.1))
    return lines


def _emit_transformer(comp: Component, tr: _Transform) -> List[str]:
    """A circuitikz transformer, scaled so its winding taps line up with
    the KiCad pins and every lead runs straight across."""
    pts = {n: tr.point(comp.pins[n].position)
           for n in sorted(comp.pins, key=_pin_sort_key)}
    if len(pts) != 4:
        return _emit_generic_box(comp, tr)

    xs = sorted({round(p[0], 3) for p in pts.values()})
    ys = sorted({round(p[1], 3) for p in pts.values()})
    if len(xs) != 2 or len(ys) != 2:
        return _emit_generic_box(comp, tr)

    cx = (xs[0] + xs[1]) / 2.0
    cy = (ys[0] + ys[1]) / 2.0
    scale = min(max(((ys[1] - ys[0]) / 2.0) / _XFMR_ANCHOR, 0.4), 2.5)
    name = _node_name(comp.ref)
    lines = [f"\\node[transformer core, scale={_fmt(scale)}] ({name}) "
             f"at {_xy(cx, cy)} {{}};"]
    lines.extend(_label_node(comp, cx, cy + _XFMR_ANCHOR * scale + 0.15))

    # A1/A2 are the left (primary) taps, B1/B2 the right (secondary) ones;
    # 1 is the upper tap of each winding.  Assign by geometry so a rotated
    # or mirrored symbol still maps correctly.
    for number, (px, py) in pts.items():
        side = "A" if abs(px - xs[0]) < abs(px - xs[1]) else "B"
        index = "1" if py > cy else "2"
        lines.append(f"\\draw ({name}.{side}{index}) -- {_xy(px, py)};")
    return lines


def _emit_generic_box(comp: Component, tr: _Transform) -> List[str]:
    """Rectangle body with exact pin stubs, for ICs/connectors/unknowns."""
    numbers = sorted(comp.pins, key=_pin_sort_key)
    pts = {n: tr.point(comp.pins[n].position) for n in numbers}
    if not pts:
        cx, cy = tr.point(comp.position)
        return [f"\\draw {_xy(cx - 0.5, cy - 0.5)} rectangle "
                f"{_xy(cx + 0.5, cy + 0.5)};"] + _label_node(
                    comp, cx, cy + 0.5)
    bx0 = min(p[0] for p in pts.values())
    bx1 = max(p[0] for p in pts.values())
    by0 = min(p[1] for p in pts.values())
    by1 = max(p[1] for p in pts.values())
    eps = 1e-6
    sides: Dict[str, str] = {}
    for n in numbers:
        px, py = pts[n]
        if px <= bx0 + eps:
            sides[n] = "left"
        elif px >= bx1 - eps:
            sides[n] = "right"
        elif py <= by0 + eps:
            sides[n] = "bottom"
        else:
            sides[n] = "top"
    have = set(sides.values())
    x0 = bx0 + _STUB if "left" in have else bx0
    x1 = bx1 - _STUB if "right" in have else bx1
    y0 = by0 + _STUB if "bottom" in have else by0
    y1 = by1 - _STUB if "top" in have else by1
    if x1 - x0 < _MIN_BODY:
        if "left" in have and "right" not in have:
            x1 = x0 + _MIN_BODY
        elif "right" in have and "left" not in have:
            x0 = x1 - _MIN_BODY
        else:
            cx = (x0 + x1) / 2.0
            x0, x1 = cx - _MIN_BODY / 2.0, cx + _MIN_BODY / 2.0
    if y1 - y0 < _MIN_BODY:
        if "bottom" in have and "top" not in have:
            y1 = y0 + _MIN_BODY
        elif "top" in have and "bottom" not in have:
            y0 = y1 - _MIN_BODY
        else:
            cy = (y0 + y1) / 2.0
            y0, y1 = cy - _MIN_BODY / 2.0, cy + _MIN_BODY / 2.0
    lines = [f"\\draw {_xy(x0, y0)} rectangle {_xy(x1, y1)};"]
    lines.extend(_label_node(comp, (x0 + x1) / 2.0, y1))

    def clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    for n in numbers:
        px, py = pts[n]
        side = sides[n]
        if side == "left":
            sx, sy = x0, clamp(py, y0, y1)
            lx, ly, anchor = sx + 0.06, sy, "west"
        elif side == "right":
            sx, sy = x1, clamp(py, y0, y1)
            lx, ly, anchor = sx - 0.06, sy, "east"
        elif side == "bottom":
            sx, sy = clamp(px, x0, x1), y0
            lx, ly, anchor = sx, sy + 0.06, "south"
        else:
            sx, sy = clamp(px, x0, x1), y1
            lx, ly, anchor = sx, sy - 0.06, "north"
        lines.append(f"\\draw {_xy(px, py)} -- {_xy(sx, sy)};")
        lines.append(f"\\node[font=\\tiny, anchor={anchor}] at "
                     f"{_xy(lx, ly)} {{{_escape(n)}}};")
    return lines


def _box_label(comp: Component) -> str:
    """Label text for a multi-pin symbol ('' when KiCad hides both fields)."""
    parts = []
    if comp.shows("Reference"):
        parts.append(_escape(comp.ref))
    value = _format_value(comp)
    if value:
        parts.append(value)
    return " ".join(parts)


def _label_node(comp: Component, x: float, y: float) -> List[str]:
    """Caption node for a multi-pin symbol, or nothing at all when KiCad
    hides both its Reference and Value fields."""
    text = _box_label(comp)
    if not text:
        return []
    return [f"\\node[font=\\small, anchor=south] at {_xy(x, y)} {{{text}}};"]


def _emit_component(comp: Component, tr: _Transform, warnings: List[str],
                    dangling: Set[int]) -> List[str]:
    if comp.ctype == ComponentType.OPAMP and len(comp.pins) >= 3:
        return _emit_opamp(comp, tr, warnings, dangling)
    if comp.ctype in (ComponentType.NJFET, ComponentType.PJFET) \
            and len(comp.pins) >= 3:
        return _emit_jfet(comp, tr, warnings)
    if comp.ctype in _TRANSISTOR_STYLES and len(comp.pins) >= 3:
        return _emit_transistor(comp, tr, warnings)
    if comp.ctype == ComponentType.TRANSFORMER:
        return _emit_transformer(comp, tr)
    if comp.ctype in _GATE_STYLES and len(comp.pins) >= 2:
        return _emit_gate(comp, tr, warnings, dangling)
    return _emit_generic_box(comp, tr)


# ---------------------------------------------------------------------------
# Power symbols (ground / rail flags)
# ---------------------------------------------------------------------------

def _is_positive_rail(name: str) -> bool:
    upper = name.upper()
    if any(hint in upper for hint in _NEGATIVE_RAIL_HINTS):
        return False
    if upper.startswith("-"):
        return False
    return True


def _emit_power_symbols(doc: SchematicDocument, tr: _Transform,
                        net_at: Dict[Point, Net],
                        warnings: List[str]) -> List[str]:
    lines: List[str] = []
    for inst in doc.symbols:
        lib = doc.lib_symbol_for(inst)
        if lib is None:
            continue
        if not (lib.is_power or inst.reference.startswith("#PWR")):
            continue
        if inst.reference.startswith("#FLG"):
            continue  # ERC power flags have no graphic meaning
        name = inst.value or inst.lib_id.split(":", 1)[-1]
        # KiCad can hide a power symbol's Value field; then the rail is
        # drawn without its name, exactly as the schematic shows it.
        shown = "" if "Value" in inst.hidden_properties else _escape(name)
        for pin in lib.pins_for_unit(inst.unit):
            pos = inst.pin_position(pin)
            net = net_at.get(pos)
            is_ground = (net.kind == NetKind.GROUND if net is not None
                         else name.strip().lower() in _GROUND_NAMES)
            if is_ground:
                lines.append(f"\\draw {tr.coord(pos)} node[ground]{{}};")
            elif _is_positive_rail(name):
                lines.append(f"\\draw {tr.coord(pos)} node[vcc]{{{shown}}};")
            else:
                lines.append(f"\\draw {tr.coord(pos)} node[vee]{{{shown}}};")
    return lines


def _warn_unmapped_characters(graph: CircuitGraph, doc: SchematicDocument,
                              warnings: List[str]) -> None:
    """Report text that cannot be rendered, instead of quietly losing it.

    ``_escape`` drops non-ASCII characters it has no LaTeX form for, which
    would otherwise turn a value like ``10<90`` into ``1090`` with nothing
    to show for it.
    """
    sources: List[Tuple[str, str]] = []
    for ref in sorted(graph.components, key=_ref_sort_key):
        comp = graph.components[ref]
        sources.append((f"{ref}'s value", comp.value))
        sources.append((f"the reference {ref}", comp.ref))
    for label in doc.labels:
        sources.append((f"the label '{label.text}'", label.text))

    seen: Dict[str, str] = {}
    for where, text in sources:
        for ch in _unmapped(text):
            seen.setdefault(ch, where)
    for ch in sorted(seen):
        warnings.append(
            f"Character {ch!r} (U+{ord(ch):04X}) has no LaTeX equivalent "
            f"and was left out of the drawing (in {seen[ch]}).")


def _emit_polarity_dots(graph: CircuitGraph, tr: _Transform) -> List[str]:
    """Filled dots the KiCad symbols carry (winding phase, polarity).

    They are emitted from the shared graph rather than by each symbol
    emitter, so a dot survives whichever way the component is drawn -
    circuitikz bipole, node or fallback box.
    """
    lines: List[str] = []
    for ref in sorted(graph.components, key=_ref_sort_key):
        for position, radius in graph.components[ref].dots:
            lines.append(f"\\fill {tr.coord(position)} "
                         f"circle ({_fmt(max(radius * SCALE, 0.03))});")
    return lines


def _emit_labels(doc: SchematicDocument, tr: _Transform) -> List[str]:
    return [f"\\node[anchor=south west, font=\\small] at "
            f"{tr.coord((lbl.x, lbl.y))} {{{_escape(lbl.text)}}};"
            for lbl in doc.labels]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_body(graph: CircuitGraph, *, junction_dots: bool = True) -> str:
    """Return only the ``\\begin{circuitikz}...\\end{circuitikz}`` body.

    Set *junction_dots* to False to omit the filled dots KiCad draws where
    three or more wires meet.  Connectivity is unchanged either way, but
    the dots are what visually distinguish a connection from a crossing,
    so they are on by default.
    """
    doc = graph.document if graph.document is not None else SchematicDocument()
    tr = _Transform(graph)
    warnings: List[str] = []
    if tr.compressed_gaps:
        warnings.append(
            f"Compressed {tr.compressed_gaps} very wide horizontal gap(s) "
            f"(flattened sheets) so the drawing fits on one page.")

    net_at: Dict[Point, Net] = {}
    for net in graph.nets:
        for p in net.points:
            net_at[p] = net

    two_terminal: List[Component] = []
    multi_pin: List[Component] = []
    for ref in sorted(graph.components, key=_ref_sort_key):
        comp = graph.components[ref]
        if len(comp.pins) < 2:
            warnings.append(f"{comp.ref}: fewer than two connected pins; "
                            f"drawing a box.")
            multi_pin.append(comp)
        elif comp.ctype in _BIPOLE_KEYS and len(comp.pins) == 2:
            two_terminal.append(comp)
        elif comp.ctype == ComponentType.POTENTIOMETER and len(comp.pins) == 3:
            two_terminal.append(comp)
        else:
            multi_pin.append(comp)

    lines: List[str] = ["\\begin{circuitikz}[american]"]

    wire_lines = _emit_wires(doc, tr)
    if wire_lines:
        lines.append("% Wires")
        lines.extend(wire_lines)

    junction_lines = _emit_junctions(doc, tr) if junction_dots else []
    if junction_lines:
        lines.append("% Junctions")
        lines.extend(junction_lines)

    if two_terminal:
        lines.append("% Two-terminal components")
        for comp in two_terminal:
            lines.extend(_emit_two_terminal(comp, tr))

    if multi_pin:
        lines.append("% Multi-pin components")
        # Nets with fewer than two pins are dangling: optional pins (op-amp
        # supplies, gate power) on them get no lead drawn.
        dangling = {net.net_id for net in graph.nets if len(net.pins) < 2}
        for comp in multi_pin:
            lines.extend(_emit_component(comp, tr, warnings, dangling))

    _warn_unmapped_characters(graph, doc, warnings)

    dot_lines = _emit_polarity_dots(graph, tr)
    if dot_lines:
        lines.append("% Polarity dots")
        lines.extend(dot_lines)

    power_lines = _emit_power_symbols(doc, tr, net_at, warnings)
    if power_lines:
        lines.append("% Power symbols")
        lines.extend(power_lines)

    label_lines = _emit_labels(doc, tr)
    if label_lines:
        lines.append("% Net labels")
        lines.extend(label_lines)

    lines.append("\\end{circuitikz}")

    for message in warnings:
        if message not in graph.warnings:
            graph.warnings.append(message)
    return "\n".join(lines)


def generate(graph: CircuitGraph, *, junction_dots: bool = True) -> str:
    """Return a complete standalone LaTeX document (circuitikz) for *graph*.

    The document compiles with ``pdflatex`` without modification, preserves
    the schematic layout, labels and values, and is deterministic for
    identical inputs.  Pass ``junction_dots=False`` to omit the connection
    dots at multi-wire nodes.
    """
    doc = graph.document
    if doc is not None and doc.source_path:
        base = os.path.basename(doc.source_path)
    else:
        base = "schematic"
    n_comp = len(graph.components)
    n_nets = len(graph.nets)
    return "\n".join([
        f"% Generated by SchemAccess from {base}",
        f"% {n_comp} components, {n_nets} nets",
        r"\documentclass[border=4pt]{standalone}",
        r"\usepackage[RPvoltages]{circuitikz}",
        r"\begin{document}",
        generate_body(graph, junction_dots=junction_dots),
        r"\end{document}",
        "",
    ])

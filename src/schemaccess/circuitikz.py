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

import math
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from . import power
from .loops import find_meshes
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
    ComponentType.VARIABLE_RESISTOR: "vR",
    ComponentType.CAPACITOR: "C",
    ComponentType.CAPACITOR_POLARIZED: "cC",
    ComponentType.INDUCTOR: "L",
    ComponentType.DIODE: "D",
    ComponentType.LED: "leD",
    # 'zzD' is circuitikz's fullzzdiode - the Zener with the full Z-bar
    # cathode, rather than 'zD' which draws only a half bar.
    ComponentType.ZENER: "zzD",
    ComponentType.SCHOTTKY: "sD",
    ComponentType.VOLTAGE_SOURCE: "V",
    ComponentType.CURRENT_SOURCE: "I",
    ComponentType.BATTERY: "battery",
    ComponentType.AC_SOURCE: "sV",
    ComponentType.SWITCH: "nos",
    ComponentType.PUSHBUTTON: "nopb",
    ComponentType.FUSE: "fuse",
    ComponentType.CRYSTAL: "generic",
    # KiCad draws behavioural/dependent sources as a diamond, matching
    # circuitikz's controlled-source shapes.
    ComponentType.CONTROLLED_VOLTAGE_SOURCE: "cvsource",
    ComponentType.CONTROLLED_CURRENT_SOURCE: "cisource",
}

_DIODE_TYPES = (ComponentType.DIODE, ComponentType.LED, ComponentType.ZENER,
                ComponentType.SCHOTTKY)

# Measured geometry of circuitikz's `op amp` node at scale 1 (probed with
# \pgfgetlastxy against circuitikz 1.7): the '+'/'-' input anchors sit at
# (-1.190, +/-0.490) and the out anchor at (+1.190, 0) TikZ units.
# Stretching the node with xscale/yscale so these anchors land exactly on
# the KiCad pin positions makes every lead a zero-length straight join,
# just like the original schematic.
_OPAMP_INPUT_HALF = 0.490
_OPAMP_ANCHOR_X = 1.190
# The triangle is narrower than its anchors: the shape draws short lead
# stubs from the body out to '+', '-' and 'out', so the body itself spans
# only +/-0.833 and its apex sits there, not at _OPAMP_ANCHOR_X.  Measured
# off the shape's own drawn path (compiled, converted to SVG, read back
# against a reference rectangle).  Using the anchor x here instead makes
# every supply lead stop in the wrong place - short of the body on one
# side of the symbol, inside it on the other.
_OPAMP_BODY_X = 0.833
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
    slope = (0.0 - ay) / (_OPAMP_BODY_X - ax)
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
    ComponentType.UJT: ("nujt", "G", "D", "S", -0.2695, 0.77),
}

# Pin names that do not start with their anchor's letter.  A unijunction
# transistor's terminals are E, B1 and B2, which share no initial with
# circuitikz's G/D/S anchors - and worse, both bases start with 'B', so
# first-letter matching hands B2 the BJT base anchor and leaves B1 with
# nothing.  The anchors are positional: 'D' is the top terminal, so KiCad's
# B2 (drawn on top) goes there and the symbol keeps the sheet's layout.
_TRANSISTOR_PIN_ALIASES: Dict[ComponentType, Dict[str, str]] = {
    ComponentType.UJT: {"E": "G", "B2": "D", "B1": "S"},
}

# Measured circuitikz 'spdt' geometry at natural size (probed the same
# way as the transistors): the common terminal sits at (-0.595, 0) and
# the two throws at (+0.595, +/-0.315).  Scaling the node uniformly so
# the throws land on the KiCad pin heights keeps every lead straight,
# the same trick the op amp uses.
#: The plain 'spdt'.  Do NOT swap this for 'cute spdt up' or any other
#: cute variant: the thin lever is the house style for this project.
#: The cute shape would close the small gap circuitikz leaves between
#: the lever and the throw contact, but that is not worth changing the
#: symbol over - the gap is drawn into the plain shape's own path and
#: cannot be closed from outside.
_SPDT_STYLE = "spdt"
_SPDT_IN_X = 0.595
_SPDT_OUT_DY = 0.315

#: How circuitikz strokes a switch's motion arrow: the component line
#: width with a solid 'latexslim' head, not TikZ's hairline '->'.
_SWITCH_ARROW_STYLE = "-{Latex[length=3.6pt,width=3pt]}, line width=0.8pt"

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
    if comp.ctype in (ComponentType.RESISTOR, ComponentType.POTENTIOMETER,
                      ComponentType.VARIABLE_RESISTOR) \
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


#: Diamond-bodied equivalents, used when the KiCad symbol is drawn as a
#: diamond.  The shape follows the schematic's artwork; the component type
#: (and so the alt text) still follows what the part actually is.
_DIAMOND_KEYS: Dict[ComponentType, str] = {
    ComponentType.CURRENT_SOURCE: "cisource",
    ComponentType.VOLTAGE_SOURCE: "cvsource",
    ComponentType.AC_SOURCE: "cvsource",
}


#: How a switch may be drawn.  circuitikz's 'nos' is a plain normally-open
#: switch; 'cnos' and 'onos' add the arrow that says the contact is in the
#: act of closing or opening, which is what teaching material uses to show
#: a transient.  Keyed by the names the CLI and GUI accept.
#: circuitikz's plain switch family.  Do not swap these for the "cute"
#: variants: the thin lever is the house style for this project.
#:
#: 'cspst' and 'ospst' are the styles circuitikz itself calls "closing
#: switch" and "opening switch": lever, arrow, and a clean gap in the
#: lead.  Do not use 'cnos'/'onos' here - those are the *normal open*
#: switches, which draw an extra vertical contact tick beside the arrow
#: and run the lead straight through, so the symbol looks cramped and
#: carries a stray mark on its right.
SWITCH_STYLE_KEYS: Dict[str, str] = {
    "default": "nos",
    "closing": "cspst",
    "opening": "ospst",
}


def switch_key(style: str = "default") -> str:
    """The bipole every switch is drawn with.

    An unknown name falls back to the plain switch rather than failing
    the conversion.
    """
    return SWITCH_STYLE_KEYS.get(style, "nos")


def _bipole_key(comp: Component, switch_style: str = "default") -> str:
    """The circuitikz bipole for *comp*, honouring its drawn outline."""
    if comp.ctype == ComponentType.SWITCH:
        return switch_key(switch_style)
    if comp.body_shape == "diamond" and comp.ctype in _DIAMOND_KEYS:
        return _DIAMOND_KEYS[comp.ctype]
    return _BIPOLE_KEYS[comp.ctype]


def _emit_two_terminal(comp: Component, tr: _Transform,
                       switch_style: str = "default") -> List[str]:
    key = _bipole_key(comp, switch_style)
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
               dangling: Set[int],
               fallbacks: Optional[Set[str]] = None) -> List[str]:
    style = _GATE_STYLES[comp.ctype]
    inputs, outputs, other = _classify_gate_pins(comp)
    max_inputs = 1 if comp.ctype in (ComponentType.NOT_GATE,
                                     ComponentType.BUFFER) else 2
    if len(outputs) != 1 or not 1 <= len(inputs) <= max_inputs:
        warnings.append(
            f"{comp.ref}: gate pin pattern not recognised; drawing a box.")
        return _emit_generic_box(comp, tr, fallbacks)
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
                dangling: Set[int],
                unit: Optional[int] = None) -> List[str]:
    """Emit one op-amp body.

    *unit* selects one placed unit of a multi-unit part.  A dual op amp is
    a single component but two drawn amplifiers, so drawing it as one body
    puts a triangle on one half of the chip and loses the other entirely.
    """
    if unit is None:
        pins = comp.pins
        centre = comp.position
        name = _node_name(comp.ref)
    else:
        pins = {n: p for n, p in comp.pins.items() if p.unit == unit}
        centre = comp.unit_positions.get(unit, comp.position)
        name = _node_name(f"{comp.ref}u{unit}")
    cx, cy = tr.point(centre)
    roles = _sim_pin_roles(comp)
    matched: Dict[str, str] = {}
    for number in sorted(pins, key=_pin_sort_key):
        pin = pins[number]
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
        plus_y = tr.point(pins[plus_no].position)[1]
        minus_y = tr.point(pins[minus_no].position)[1]
        if plus_y > minus_y:
            node_style = "op amp, noinv input up"
    # Supply anchors likewise follow the actual pin geometry.
    for number, anchor in list(matched.items()):
        if anchor in ("up", "down"):
            pin_y = tr.point(pins[number].position)[1]
            matched[number] = "up" if pin_y >= cy else "down"

    # circuitikz's own 'op amp' shape, scaled UNIFORMLY (never stretched,
    # so the triangle keeps its proper proportions) by just enough that
    # its input anchors sit at the KiCad input-pin heights.  The leads
    # into the inputs and the output are then straight horizontal lines,
    # exactly as in a hand-written circuitikz figure.
    mirrored = False
    if out_no is not None and plus_no is not None and minus_no is not None:
        out_x = tr.point(pins[out_no].position)[0]
        in_x = (tr.point(pins[plus_no].position)[0]
                + tr.point(pins[minus_no].position)[0]) / 2.0
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
        inside = abs(local_x) < _OPAMP_BODY_X
        if inside and ((anchor == "up" and py > edge_y)
                       or (anchor == "down" and py < edge_y)):
            return f"\\draw {_xy(px, py)} -- {_xy(px, edge_y)};"
        # Pin sits outside the body outline: route vertically, then across.
        return f"\\draw ({name}.{anchor}) |- {_xy(px, py)};"

    # Put the label clear of the body and of anything wired above it.
    label_y = max([node_y + 0.98 * scale]
                  + [tr.point(p.position)[1] for p in pins.values()
                     if p.net_id >= 0 and p.net_id not in dangling])
    lines = [f"\\node[{node_style}] ({name}) at {_xy(cx, node_y)} {{}};"]
    lines.extend(_label_node(comp, cx, label_y + 0.25))
    for number in sorted(pins, key=_pin_sort_key):
        pin = pins[number]
        anchor = matched.get(number)
        unconnected = pin.net_id < 0 or pin.net_id in dangling
        if anchor is None:
            if unconnected:
                continue  # optional pin with nothing attached: no lead
            # circuitikz's op amp has no anchor for this pin (a CA3080's
            # BIAS, a THS4226's PD...).  Stop the lead on the body outline,
            # the way KiCad draws such a pin as a stub; running it to
            # .center slashes a line right across the triangle.
            warnings.append(f"{comp.ref}: pin {number} ('{pin.name}') has "
                            f"no op-amp anchor; drawn as a stub on the body.")
            side = "up" if tr.point(pin.position)[1] >= node_y else "down"
            lines.append(supply_lead(pin, side))
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


#: Pin names that are mechanical, not electrical: a mounting pin must not
#: be mistaken for a switch terminal.
_MECHANICAL_PIN_NAMES = {"MP", "MH", "SHIELD", "SH", "CASE"}


def _spdt_triple(points: List[Point], usable: List[int]
                 ) -> Optional[Tuple[int, int, int]]:
    """Find (pole, throw, throw) among *usable* pins, at any rotation.

    The throws share a coordinate on one axis and the pole sits on the
    far side, roughly level with their midpoint.  Testing both axes is
    what makes this work for a symbol KiCad placed rotated: the throws
    then share a row rather than a column.
    """
    for axis in (0, 1):
        groups: Dict[float, List[int]] = {}
        for index in usable:
            groups.setdefault(round(points[index][axis], 2), []).append(index)
        for key, members in groups.items():
            if len(members) != 2:
                continue
            first, second = members
            middle = (points[first][1 - axis] + points[second][1 - axis]) / 2.0
            for index in usable:
                if index in members:
                    continue
                if (abs(points[index][1 - axis] - middle) <= 0.3
                        and abs(points[index][axis] - key) > 0.3):
                    return index, first, second
    return None


def _emit_controlled_source(comp: Component, tr: _Transform,
                            warnings: List[str],
                            dangling: Set[int],
                            fallbacks: Optional[Set[str]] = None
                            ) -> List[str]:
    """A SPICE E/G source: the diamond, plus leads for its sense pins.

    KiCad's ESOURCE and GSOURCE carry four pins - the output pair N+/N-
    and the controlling pair C+/C-.  That extra pair is the only reason
    they never reached the two-terminal path and came out as boxes; the
    body is the same diamond a two-pin controlled source draws, so it is
    drawn across N+/N- and the sense pins get the stub KiCad draws.
    """
    outputs, controls = [], []
    for number in sorted(comp.pins, key=_pin_sort_key):
        pin = comp.pins[number]
        name = pin.name.strip().upper()
        (controls if name.startswith("C") else outputs).append(pin)
    if len(outputs) != 2:
        warnings.append(f"{comp.ref}: controlled source pins are not an "
                        f"N+/N- output pair; drawing a box.")
        return _emit_generic_box(comp, tr, fallbacks)

    plus = next((p for p in outputs if "+" in p.name), outputs[0])
    minus = next(p for p in outputs if p is not plus)
    # Same polarity convention as the two-terminal sources: a voltage
    # source wants its '+' second, a current source wants it first.
    first, second = ((plus, minus)
                     if comp.ctype == ComponentType.CONTROLLED_CURRENT_SOURCE
                     else (minus, plus))
    key = _bipole_key(comp)
    lines = [f"\\draw {tr.coord(first.position)} "
             f"to[{_bipole_options(comp, key)}] {tr.coord(second.position)};"]
    # The sense pins get a lead only when something is actually wired to
    # them.  On an unwired sheet their stubs are just two stray marks
    # floating beside the diamond.
    for pin in controls:
        if pin.net_id < 0 or pin.net_id in dangling:
            continue
        target = (tr.coord(pin.body_point) if pin.body_point is not None
                  else tr.coord(comp.position))
        lines.append(f"\\draw {tr.coord(pin.position)} -- {target};")
    return lines


def _library_up(comp: Component) -> Point:
    """The symbol's own +y axis, as a direction in drawing coordinates.

    Mirrors :meth:`SymbolInstance.lib_point`: rotate within the library
    frame, flip Y into schematic space, then apply the mirror - and the
    drawing flips Y once more.
    """
    angle = math.radians(comp.angle)
    ox, oy = -math.sin(angle), -math.cos(angle)
    if comp.mirror == "x":
        oy = -oy
    elif comp.mirror == "y":
        ox = -ox
    return (ox, -oy)


def _emit_spdt(comp: Component, tr: _Transform, warnings: List[str],
               fallbacks: Optional[Set[str]] = None,
               switch_style: str = "default") -> List[str]:
    """Draw a changeover switch as a circuitikz 'spdt'.

    KiCad draws an SPDT with the common pole alone on one side and the
    two throws facing it; circuitikz's spdt node has exactly that shape,
    with 'in' for the pole and 'out 1'/'out 2' for the throws.  The node
    is rotated to the pole-to-throws direction and scaled uniformly so
    the throw anchors land on the real pin positions, which keeps every
    lead straight whatever rotation the symbol was placed at.
    """
    numbers = sorted(comp.pins, key=_pin_sort_key)
    pins = [comp.pins[n] for n in numbers]
    points = [tr.point(p.position) for p in pins]
    usable = [i for i, p in enumerate(pins)
              if p.name.strip().upper() not in _MECHANICAL_PIN_NAMES]
    triple = _spdt_triple(points, usable)
    if triple is None:
        warnings.append(f"{comp.ref}: not a changeover (SPDT) layout; "
                        f"drawing a box.")
        return _emit_generic_box(comp, tr, fallbacks)

    pole_i, first_i, second_i = triple
    pole_x, pole_y = points[pole_i]
    mid_x = (points[first_i][0] + points[second_i][0]) / 2.0
    mid_y = (points[first_i][1] + points[second_i][1]) / 2.0
    # KiCad places on a grid, so the pole-to-throws direction is a right
    # angle; snapping removes any float drift before it reaches the TeX.
    angle = round(math.degrees(math.atan2(mid_y - pole_y, mid_x - pole_x))
                  / 90.0) * 90.0
    radians = math.radians(angle)
    along = (math.cos(radians), math.sin(radians))
    across = (-math.sin(radians), math.cos(radians))

    half = math.dist(points[first_i], points[second_i]) / 2.0
    scale = min(max(half / _SPDT_OUT_DY, 0.6), 2.0) if half > 0.05 else 1.0
    reach = _SPDT_IN_X * scale
    cx = pole_x + along[0] * reach
    cy = pole_y + along[1] * reach

    # Which throw the lever rests on is not a free choice: KiCad draws it
    # against the throw at positive y in the symbol's own coordinates -
    # true of every stock changeover (SW_SPDT_321 rests on pin 3,
    # SW_SPDT_312 on pin 3, SW_DPDT_x2 on pin 1, the TS3A analog switch
    # on pin 5).  circuitikz rests its lever on 'out 1', so that throw
    # has to be given that anchor or the drawing shows the switch thrown
    # the wrong way.
    lever = _library_up(comp)
    span = (points[first_i][0] - points[second_i][0],
            points[first_i][1] - points[second_i][1])
    out_one, out_two = ((first_i, second_i)
                        if span[0] * lever[0] + span[1] * lever[1] > 0
                        else (second_i, first_i))

    # 'out 1' must come to rest on the lever's own side of the symbol, or
    # its lead crosses over the other throw's.  A rotation alone cannot
    # do that for half the placements - it preserves handedness, so it
    # carries 'out 1' to the far side - and the flip is what fixes it.
    # Measured: [rotate=T, yscale=-1] mirrors before rotating, putting
    # 'out 1' at -perp instead of +perp.
    wanted = (points[out_one][0] - mid_x, points[out_one][1] - mid_y)
    flipped = wanted[0] * across[0] + wanted[1] * across[1] < 0

    # The node's own frame, reused to draw the motion arrow in the same
    # orientation as the lever.
    frame = []
    if abs(angle) > 1e-6:
        frame.append(f"rotate={_fmt(angle)}")
    if flipped:
        frame.append("yscale=-1")

    options = [_SPDT_STYLE]
    if abs(scale - 1.0) > 1e-3:
        options.append(f"scale={_fmt(scale)}")
    options.extend(frame)
    name = _node_name(comp.ref)
    draws_arrow = switch_style in ("closing", "opening")
    arrow_radius = 1.2 * _SPDT_IN_X * scale
    label_y = cy + _SPDT_OUT_DY * scale + 0.3
    if draws_arrow:
        # Keep the reference clear of the arc that is about to be drawn.
        label_y = max(label_y, cy + arrow_radius * 0.75 + 0.25)
    lines = [f"\\node[{', '.join(options)}] ({name}) at {_xy(cx, cy)} {{}};"]
    lines.extend(_label_node(comp, cx, label_y))

    for anchor, index in (("out 1", out_one), ("out 2", out_two)):
        lines.append(f"\\draw ({name}.{anchor}) -- "
                     f"{_xy(*points[index])};")

    # Control and mounting pins have no anchor on the shape; draw the
    # stub KiCad draws rather than a line across the symbol.
    for index, pin in enumerate(pins):
        if index in (pole_i, first_i, second_i):
            continue
        target = (tr.coord(pin.body_point) if pin.body_point is not None
                  else _xy(cx, cy))
        lines.append(f"\\draw {_xy(*points[index])} -- {target};")

    # The style option is a bipole key, which a node cannot take, so the
    # motion arrow is drawn by hand - matching how circuitikz draws it on
    # cspst/ospst, because an arrow that does not match SW2's looks like
    # a different kind of mark.  There it is an arc centred on the pole,
    # radius 1.2x the half-width, swept 90deg to -20deg for closing and
    # the reverse for opening, stroked at the component line width with a
    # solid head; drawing it in the node's own frame keeps it crossing
    # the lever whatever rotation the symbol was placed at.
    if draws_arrow:
        # The sweep is pulled in from circuitikz's 90/-20: its lever
        # rises at ~40deg where the spdt's rises at ~15deg, so the same
        # angles would leave the arrow sailing well past the lever.
        # 50 degrees of sweep, centred on the lever so the arc crosses it
        # rather than running alongside: the lever rises from the pole to
        # the closed throw, so its angle follows from the shape's own
        # proportions rather than being a fixed number.
        lever_angle = math.degrees(
            math.atan2(_SPDT_OUT_DY, 2.0 * _SPDT_IN_X))
        low, high = lever_angle - 25.0, lever_angle + 25.0
        start, end = ((high, low) if switch_style == "closing"
                      else (low, high))
        radius = arrow_radius
        scope = ", ".join([f"shift={{({name}.in)}}"] + frame)
        lines.append(
            f"\\draw[{_SWITCH_ARROW_STYLE}, {scope}] "
            f"({_fmt(start)}:{_fmt(radius)}) arc[start angle={_fmt(start)}, "
            f"end angle={_fmt(end)}, radius={_fmt(radius)}];")
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

    aliases = _TRANSISTOR_PIN_ALIASES.get(comp.ctype, {})
    assigned: Dict[str, str] = {}
    remaining = dict(zip(anchors, anchors))
    for number in sorted(comp.pins, key=_pin_sort_key):
        pin_name = comp.pins[number].name.strip().upper()
        key = aliases.get(pin_name, pin_name[:1])
        if key in remaining:
            assigned[number] = remaining.pop(key)

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
            # circuitikz has no terminal for this pin (a MOSFET's bulk, a
            # BJT's substrate).  Draw the stub KiCad draws - from the
            # connection point in to where the pin meets the body - and
            # stop there.  Running it to .center instead puts a line
            # straight across the symbol.
            warnings.append(f"{comp.ref}: pin {number} ('{pin.name}') has "
                            f"no {style} anchor; drawn as a stub.")
            stub = (tr.coord(pin.body_point) if pin.body_point is not None
                    else f"({name}.center)")
            lines.append(f"\\draw {_xy(px, py)} -- {stub};")
            continue
        if anchor == control:
            anchor_y = cy + (-ctrl_dy if flipped else ctrl_dy)
            joiner = "--" if abs(py - anchor_y) < 5e-3 else "|-"
        else:
            joiner = "--" if abs(px - cx) < 5e-3 else "-|"
        lines.append(f"\\draw ({name}.{anchor}) {joiner} {_xy(px, py)};")
    return lines


def _emit_jfet(comp: Component, tr: _Transform, warnings: List[str],
               fallbacks: Optional[Set[str]] = None) -> List[str]:
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
        return _emit_generic_box(comp, tr, fallbacks)

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


def _emit_transformer(comp: Component, tr: _Transform,
                      fallbacks: Optional[Set[str]] = None) -> List[str]:
    """A circuitikz transformer, scaled so its winding taps line up with
    the KiCad pins and every lead runs straight across."""
    pts = {n: tr.point(comp.pins[n].position)
           for n in sorted(comp.pins, key=_pin_sort_key)}
    if len(pts) != 4:
        return _emit_generic_box(comp, tr, fallbacks)

    xs = sorted({round(p[0], 3) for p in pts.values()})
    ys = sorted({round(p[1], 3) for p in pts.values()})
    if len(xs) != 2 or len(ys) != 2:
        return _emit_generic_box(comp, tr, fallbacks)

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


def _emit_generic_box(comp: Component, tr: _Transform,
                      fallbacks: Optional[Set[str]] = None) -> List[str]:
    """Rectangle body with exact pin stubs, for ICs/connectors/unknowns.

    Records the reference in *fallbacks* so callers can report which
    components had no dedicated symbol.
    """
    if fallbacks is not None:
        fallbacks.add(comp.ref)
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
                    dangling: Set[int],
                    fallbacks: Optional[Set[str]] = None,
                    switch_style: str = "default") -> List[str]:
    if comp.ctype == ComponentType.OPAMP and len(comp.pins) >= 3:
        units = sorted({p.unit for p in comp.pins.values()})
        if len(units) <= 1:
            return _emit_opamp(comp, tr, warnings, dangling)
        lines: List[str] = []
        for unit in units:
            lines.extend(_emit_opamp(comp, tr, warnings, dangling, unit))
        return lines
    if comp.ctype in (ComponentType.NJFET, ComponentType.PJFET) \
            and len(comp.pins) >= 3:
        return _emit_jfet(comp, tr, warnings, fallbacks)
    if comp.ctype in _TRANSISTOR_STYLES and len(comp.pins) >= 3:
        return _emit_transistor(comp, tr, warnings)
    if comp.ctype == ComponentType.TRANSFORMER:
        return _emit_transformer(comp, tr, fallbacks)
    if comp.ctype in _GATE_STYLES and len(comp.pins) >= 2:
        return _emit_gate(comp, tr, warnings, dangling, fallbacks)
    if comp.ctype in (ComponentType.SWITCH, ComponentType.PUSHBUTTON) \
            and 3 <= len(comp.pins) <= 5:
        return _emit_spdt(comp, tr, warnings, fallbacks, switch_style)
    if comp.ctype in (ComponentType.CONTROLLED_VOLTAGE_SOURCE,
                      ComponentType.CONTROLLED_CURRENT_SOURCE) \
            and len(comp.pins) == 4:
        return _emit_controlled_source(comp, tr, warnings, dangling,
                                       fallbacks)
    return _emit_generic_box(comp, tr, fallbacks)


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
        name = power.net_name(inst)
        # KiCad can hide a power symbol's Value field; then the rail is
        # drawn without its name, exactly as the schematic shows it.
        shown = "" if "Value" in inst.hidden_properties else _escape(name)
        for pin in lib.pins_for_unit(inst.unit):
            pos = inst.pin_position(pin)
            net = net_at.get(pos)
            is_ground = (net.kind == NetKind.GROUND if net is not None
                         else power.is_ground(inst, lib))
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
        comp = graph.components[ref]
        # A transistor's filled circles are body art, not polarity marks:
        # KiCad fills the dot where a MOSFET's bulk meets its source, and
        # circuitikz's own shape already draws whatever it needs there.
        if comp.ctype.is_transistor:
            continue
        for position, radius in comp.dots:
            lines.append(f"\\fill {tr.coord(position)} "
                         f"circle ({_fmt(max(radius * SCALE, 0.03))});")
    return lines


def _emit_texts(doc: SchematicDocument, tr: _Transform) -> List[str]:
    """Free graphic text the sheet carries ("a", "b", "t = 0").

    KiCad anchors its text at the left of the first line, so the node is
    anchored the same way rather than centred, or the annotation drifts
    off whatever it was placed beside.
    """
    lines = []
    for item in doc.texts:
        options = ["anchor=west", "font=\\small"]
        if abs(item.angle) > 1e-6:
            options.append(f"rotate={_fmt(item.angle)}")
        body = _escape(item.text).replace("\n", r"\\")
        lines.append(f"\\node[{', '.join(options)}] at "
                     f"{tr.coord((item.x, item.y))} {{{body}}};")
    return lines


def _emit_labels(doc: SchematicDocument, tr: _Transform) -> List[str]:
    return [f"\\node[anchor=south west, font=\\small] at "
            f"{tr.coord((lbl.x, lbl.y))} {{{_escape(lbl.text)}}};"
            for lbl in doc.labels]


_LOOP_COLOUR = "blue!70!black"
_LOOP_ARCS = {True: (120, -150), False: (60, 330)}


def _emit_loops(graph: CircuitGraph, tr: _Transform) -> List[str]:
    found = find_meshes(graph)
    lines: List[str] = []
    for mesh in found.meshes if found.ok else ():
        cx, cy = tr.point(mesh.centre)
        radius = round(mesh.radius * SCALE, 3)
        start, end = _LOOP_ARCS[mesh.clockwise]
        sx = cx + radius * math.cos(math.radians(start))
        sy = cy + radius * math.sin(math.radians(start))
        lines.append(
            f"\\draw[{_LOOP_COLOUR}, thick, -{{Latex[length=2.2mm, bend]}}] "
            f"{_xy(sx, sy)} arc[start angle={start}, "
            f"end angle={end}, radius={_fmt(radius)}];")
        lines.append(f"\\node[{_LOOP_COLOUR}] at {_xy(cx, cy)} "
                     f"{{$i_{{{mesh.index}}}$}};")
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_body(graph: CircuitGraph, *, junction_dots: bool = True,
                  fallbacks: Optional[Set[str]] = None,
                  loops: bool = False,
                  switch_style: str = "default") -> str:
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
            lines.extend(_emit_two_terminal(comp, tr, switch_style))

    if multi_pin:
        lines.append("% Multi-pin components")
        # Nets with fewer than two pins are dangling: optional pins (op-amp
        # supplies, gate power) on them get no lead drawn.
        dangling = {net.net_id for net in graph.nets if len(net.pins) < 2}
        for comp in multi_pin:
            lines.extend(
                _emit_component(comp, tr, warnings, dangling, fallbacks,
                                switch_style))

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

    text_lines = _emit_texts(doc, tr)
    if text_lines:
        lines.append("% Sheet text")
        lines.extend(text_lines)

    loop_lines = _emit_loops(graph, tr) if loops else []
    if loop_lines:
        lines.append("% Loop currents, each taken clockwise")
        lines.extend(loop_lines)

    lines.append("\\end{circuitikz}")

    for message in warnings:
        if message not in graph.warnings:
            graph.warnings.append(message)
    return "\n".join(lines)


def generate(graph: CircuitGraph, *, junction_dots: bool = True,
             fallbacks: Optional[Set[str]] = None,
             loops: bool = False,
             switch_style: str = "default") -> str:
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
        generate_body(graph, junction_dots=junction_dots,
                      fallbacks=fallbacks, loops=loops,
                      switch_style=switch_style),
        r"\end{document}",
        "",
    ])

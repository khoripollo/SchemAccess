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

# One standard 7.62 mm two-pin KiCad element becomes 2.0 TikZ units.
SCALE = 0.2625

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

_TRANSISTOR_STYLES: Dict[ComponentType, Tuple[str, Tuple[str, str, str]]] = {
    ComponentType.TRANSISTOR_NPN: ("npn", ("B", "C", "E")),
    ComponentType.TRANSISTOR_PNP: ("pnp", ("B", "C", "E")),
    ComponentType.NMOS: ("nmos", ("G", "D", "S")),
    ComponentType.PMOS: ("pmos", ("G", "D", "S")),
}

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
    return "".join(out)


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
    """Return the LaTeX annotation text for a component value ('' to omit)."""
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
        plus = _pin_named(pins, ("+", "p", "plus")) or next(
            (p for p in pins if roles.get(p.number) == "+"), None)
        minus = _pin_named(pins, ("-", "n", "minus")) or next(
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
    opts = [key, f"l={{{_escape(comp.ref)}}}"]
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
    lines.append(f"\\node[font=\\small, anchor=south] at "
                 f"{_xy(cx, cy + 0.45)} {{{_box_label(comp)}}};")
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

    if out_no is not None and plus_y is not None \
            and abs(plus_y - minus_y) > 0.1:
        # Pin the node's output anchor to the true output pin and stretch
        # it (independently in x and y) so the input anchors land exactly
        # on the KiCad pin positions: every lead becomes a zero-length
        # straight join, exactly like the original schematic.  A negative
        # xscale flips a mirrored (output-on-the-left) op amp correctly.
        ox, oy = tr.point(comp.pins[out_no].position)
        in_x = (tr.point(comp.pins[plus_no].position)[0]
                + tr.point(comp.pins[minus_no].position)[0]) / 2.0
        yscale = abs(plus_y - minus_y) / 2.0 / _OPAMP_INPUT_HALF
        yscale = min(max(yscale, 0.4), 4.0)
        xscale = (ox - in_x) / (2.0 * _OPAMP_ANCHOR_X)
        sign = 1.0 if xscale >= 0 else -1.0
        xscale = sign * min(max(abs(xscale), 0.4), 4.0)
        node_style += (f", anchor=out, xscale={_fmt(xscale)}"
                       f", yscale={_fmt(yscale)}")
        node_at = _xy(ox, oy)
    else:
        node_at = _xy(cx, cy)

    lines = [f"\\node[{node_style}] ({name}) at {node_at} {{}};",
             f"\\node[font=\\small, anchor=south] at ({name}.north) "
             f"{{{_box_label(comp)}}};"]
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
            # Right-angle route (vertical, then horizontal) so supply
            # leads never slash across the triangle.
            lines.append(
                f"\\draw ({name}.{anchor}) |- {tr.coord(pin.position)};")
        else:
            lines.append(
                f"\\draw ({name}.{anchor}) -- {tr.coord(pin.position)};")
    return lines


def _emit_transistor(comp: Component, tr: _Transform,
                     warnings: List[str]) -> List[str]:
    style, anchors = _TRANSISTOR_STYLES[comp.ctype]
    name = _node_name(comp.ref)
    cx, cy = tr.point(comp.position)
    lines = [f"\\node[{style}] ({name}) at {_xy(cx, cy)} {{}};",
             f"\\node[font=\\small, anchor=south] at {_xy(cx, cy + 0.7)} "
             f"{{{_box_label(comp)}}};"]
    remaining = dict(zip(anchors, anchors))
    assigned: Dict[str, str] = {}
    for number in sorted(comp.pins, key=_pin_sort_key):
        pin = comp.pins[number]
        letter = pin.name.strip()[:1].upper()
        if letter in remaining:
            assigned[number] = remaining.pop(letter)
    for number in sorted(comp.pins, key=_pin_sort_key):
        pin = comp.pins[number]
        anchor = assigned.get(number)
        if anchor is None:
            warnings.append(f"{comp.ref}: pin {number} ('{pin.name}') has "
                            f"no {style} anchor; drawing a plain lead.")
            lines.append(
                f"\\draw {tr.coord(pin.position)} -- ({name}.center);")
        else:
            lines.append(
                f"\\draw ({name}.{anchor}) -- {tr.coord(pin.position)};")
    return lines


def _emit_generic_box(comp: Component, tr: _Transform) -> List[str]:
    """Rectangle body with exact pin stubs, for ICs/connectors/unknowns."""
    numbers = sorted(comp.pins, key=_pin_sort_key)
    pts = {n: tr.point(comp.pins[n].position) for n in numbers}
    if not pts:
        cx, cy = tr.point(comp.position)
        return [f"\\draw {_xy(cx - 0.5, cy - 0.5)} rectangle "
                f"{_xy(cx + 0.5, cy + 0.5)};",
                f"\\node[font=\\small, anchor=south] at "
                f"{_xy(cx, cy + 0.5)} {{{_box_label(comp)}}};"]
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
    lines = [f"\\draw {_xy(x0, y0)} rectangle {_xy(x1, y1)};",
             f"\\node[font=\\small, anchor=south] at "
             f"{_xy((x0 + x1) / 2.0, y1)} {{{_box_label(comp)}}};"]

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
    label = _escape(comp.ref)
    value = _format_value(comp)
    if value:
        label += f" {value}"
    return label


def _emit_component(comp: Component, tr: _Transform, warnings: List[str],
                    dangling: Set[int]) -> List[str]:
    if comp.ctype == ComponentType.OPAMP and len(comp.pins) >= 3:
        return _emit_opamp(comp, tr, warnings, dangling)
    if comp.ctype in _TRANSISTOR_STYLES and len(comp.pins) >= 3:
        return _emit_transistor(comp, tr, warnings)
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
        for pin in lib.pins_for_unit(inst.unit):
            pos = inst.pin_position(pin)
            net = net_at.get(pos)
            is_ground = (net.kind == NetKind.GROUND if net is not None
                         else name.strip().lower() in _GROUND_NAMES)
            if is_ground:
                lines.append(f"\\draw {tr.coord(pos)} node[ground]{{}};")
            elif _is_positive_rail(name):
                lines.append(f"\\draw {tr.coord(pos)} "
                             f"node[vcc]{{{_escape(name)}}};")
            else:
                lines.append(f"\\draw {tr.coord(pos)} "
                             f"node[vee]{{{_escape(name)}}};")
    return lines


def _emit_labels(doc: SchematicDocument, tr: _Transform) -> List[str]:
    return [f"\\node[anchor=south west, font=\\small] at "
            f"{tr.coord((lbl.x, lbl.y))} {{{_escape(lbl.text)}}};"
            for lbl in doc.labels]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_body(graph: CircuitGraph) -> str:
    """Return only the ``\\begin{circuitikz}...\\end{circuitikz}`` body."""
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

    junction_lines = _emit_junctions(doc, tr)
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


def generate(graph: CircuitGraph) -> str:
    """Return a complete standalone LaTeX document (circuitikz) for *graph*.

    The document compiles with ``pdflatex`` without modification, preserves
    the schematic layout, labels and values, and is deterministic for
    identical inputs.
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
        generate_body(graph),
        r"\end{document}",
        "",
    ])

"""SVG preview: circuit graph -> a standalone, self-contained SVG drawing.

Why this exists
---------------
:mod:`schemaccess.circuitikz` produces the *authoritative* drawing, but it
needs a LaTeX toolchain to become a picture.  A browser has no LaTeX, and
a professor uploading a schematic wants to see, immediately, that the file
was read correctly before downloading anything.  This module draws the same
circuit directly as SVG so there is something to look at in under a second.

It is a **preview**, not a second renderer: it works from the same
:class:`~schemaccess.model.CircuitGraph` and the same schematic geometry,
so the topology, positions and labels always agree with the LaTeX output,
but the symbol artwork is drawn here rather than by circuitikz and will not
match the compiled PDF stroke for stroke.

Coordinates
-----------
SVG's Y axis points down, and so does KiCad's, so the drawing works in
millimetres directly: the ``viewBox`` is the schematic's bounding box in
millimetres and every length below is a real millimetre.  Nothing is
flipped, and the output is deterministic for identical inputs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import power
from .loops import find_meshes
from .model import (CircuitGraph, Component, ComponentType, LabelKind,
                    NetKind, PinConnection, Point, SchematicDocument,
                    SymbolInstance)

__all__ = ["generate", "draw", "Bounds", "PX_PER_MM"]

#: Rendered pixels per schematic millimetre at scale 1.  The SVG scales
#: freely afterwards; this only sets the default ``width``/``height``.
PX_PER_MM = 4.0

# Blank millimetres around the drawing.  It has to clear the widest
# label, not just the wires: a value set to the left of a vertical
# component reaches several millimetres past the geometry, and at
# 5 mm the leading digit was being clipped off the page.
_MARGIN = 9.0
_WIRE_W = 0.18           # wire stroke width, mm (KiCad draws 0.152)
_BODY_W = 0.22           # component outline stroke width, mm
_JUNCTION_R = 0.5        # junction dot radius, mm
_STUB = 1.5              # gap between a pin and a generic box, mm
_TEXT = 1.5              # reference/value text height, mm
_LABEL_TEXT = 1.3        # net-label text height, mm

# Flattened hierarchical sheets are spread 10000 mm apart by the parser.
# Drawing that verbatim would make one sheet a speck, so wide horizontal
# gaps are compressed exactly as the CircuiTikZ generator compresses them.
_GAP_THRESHOLD = 1000.0
_GAP_MARGIN = 50.0


# ---------------------------------------------------------------------------
# Small vector helpers
# ---------------------------------------------------------------------------

def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def _scale(a: Point, k: float) -> Point:
    return (a[0] * k, a[1] * k)


def _length(a: Point) -> float:
    return math.hypot(a[0], a[1])


def _unit(a: Point) -> Point:
    length = _length(a)
    if length < 1e-9:
        return (1.0, 0.0)
    return (a[0] / length, a[1] / length)


def _mid(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _angle_deg(u: Point) -> float:
    return math.degrees(math.atan2(u[1], u[0]))


def _f(value: float) -> str:
    """Deterministic short float formatting ('2.0' -> '2')."""
    value = round(value, 3)
    if value == 0:
        value = 0.0                     # normalise -0.0
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _pt(p: Point) -> str:
    return f"{_f(p[0])},{_f(p[1])}"


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _pin_sort_key(number: str) -> Tuple[int, int, str]:
    if number.isdigit():
        return (0, int(number), number)
    return (1, 0, number)


def _ref_key(ref: str) -> Tuple[str, int, str]:
    prefix = "".join(ch for ch in ref if not ch.isdigit())
    digits = "".join(ch for ch in ref if ch.isdigit())
    return (prefix, int(digits) if digits else 0, ref)


# ---------------------------------------------------------------------------
# Drawing surface
# ---------------------------------------------------------------------------

class _Canvas:
    """Collects SVG elements, grouped so CSS can style them by layer."""

    def __init__(self) -> None:
        self.wires: List[str] = []
        self.bodies: List[str] = []
        self.text: List[str] = []

    # -- primitives ------------------------------------------------------
    def line(self, a: Point, b: Point, cls: str = "sa-wire") -> None:
        target = self.wires if cls == "sa-wire" else self.bodies
        target.append(f'<line x1="{_f(a[0])}" y1="{_f(a[1])}" '
                      f'x2="{_f(b[0])}" y2="{_f(b[1])}" class="{cls}"/>')

    def polyline(self, points: Sequence[Point], cls: str = "sa-wire") -> None:
        if len(points) < 2:
            return
        target = self.wires if cls == "sa-wire" else self.bodies
        pts = " ".join(_pt(p) for p in points)
        target.append(f'<polyline points="{pts}" class="{cls}"/>')

    def circle(self, centre: Point, radius: float,
               cls: str = "sa-body") -> None:
        target = self.wires if cls == "sa-dot" else self.bodies
        target.append(f'<circle cx="{_f(centre[0])}" cy="{_f(centre[1])}" '
                      f'r="{_f(radius)}" class="{cls}"/>')

    def rect(self, x: float, y: float, w: float, h: float,
             cls: str = "sa-body", radius: float = 0.0) -> None:
        extra = f' rx="{_f(radius)}"' if radius else ""
        self.bodies.append(f'<rect x="{_f(x)}" y="{_f(y)}" '
                           f'width="{_f(w)}" height="{_f(h)}"{extra} '
                           f'class="{cls}"/>')

    def path(self, d: str, cls: str = "sa-body") -> None:
        self.bodies.append(f'<path d="{d}" class="{cls}"/>')

    def label(self, position: Point, content: str, *, size: float = _TEXT,
              anchor: str = "middle", cls: str = "sa-text",
              baseline: str = "middle") -> None:
        if not content:
            return
        self.text.append(
            f'<text x="{_f(position[0])}" y="{_f(position[1])}" '
            f'font-size="{_f(size)}" text-anchor="{anchor}" '
            f'dominant-baseline="{baseline}" class="{cls}">'
            f'{_esc(content)}</text>')

    # -- local frames ----------------------------------------------------
    def open_frame(self, origin: Point, direction: Point) -> None:
        """Start a group whose local +x axis points along *direction*."""
        angle = _angle_deg(direction)
        self.bodies.append(
            f'<g transform="translate({_pt(origin)}) '
            f'rotate({_f(angle)})">')

    def close_frame(self) -> None:
        self.bodies.append("</g>")


# ---------------------------------------------------------------------------
# Coordinate compression for flattened hierarchies
# ---------------------------------------------------------------------------

class _Squeeze:
    """Removes the huge horizontal gaps between flattened sheet islands."""

    def __init__(self, xs: Sequence[float]) -> None:
        self._breaks: List[Tuple[float, float]] = []
        shift = 0.0
        previous: Optional[float] = None
        for x in sorted({round(v, 4) for v in xs}):
            if previous is not None and x - previous > _GAP_THRESHOLD:
                shift += (x - previous) - _GAP_MARGIN
                self._breaks.append((x, shift))
            previous = x

    def __call__(self, p: Point) -> Point:
        if not self._breaks:
            return p
        shift = 0.0
        for break_x, break_shift in self._breaks:
            if p[0] >= break_x - 1e-6:
                shift = break_shift
            else:
                break
        return (p[0] - shift, p[1])


# ---------------------------------------------------------------------------
# Values shown on the drawing
# ---------------------------------------------------------------------------

def _display_value(comp: Component) -> str:
    """The value text to draw, or '' when KiCad would not draw one."""
    if not comp.shows("Value"):
        return ""
    raw = (comp.value or "").strip()
    if raw in ("", "~", "?") or "${" in raw:
        return ""
    if raw.upper() in ("R", "C", "L", "D"):
        return ""
    lib_name = comp.lib_id.split(":", 1)[-1] if comp.lib_id else ""
    if lib_name and raw.lower() == lib_name.lower() \
            and comp.ctype.is_two_terminal:
        return ""
    if comp.ctype in (ComponentType.RESISTOR, ComponentType.POTENTIOMETER) \
            and raw.replace(".", "", 1).isdigit():
        return raw + "Ω"           # bare number on a resistor is ohms
    return raw


def _display_ref(comp: Component) -> str:
    return comp.ref if comp.shows("Reference") else ""


# ---------------------------------------------------------------------------
# Two-terminal glyphs.  Every glyph is drawn in a local frame whose +x axis
# runs from the first terminal to the second and whose origin is the body
# centre, so the same code works at any rotation.
# ---------------------------------------------------------------------------

def _glyph_resistor(c: _Canvas, half: float) -> None:
    span = half * 1.5
    step = span / 6.0
    amplitude = min(1.1, half * 0.45)
    points: List[Point] = [(-half, 0.0), (-span / 2.0, 0.0)]
    for i in range(6):
        points.append((-span / 2.0 + (i + 0.5) * step,
                       amplitude if i % 2 == 0 else -amplitude))
    points.extend([(span / 2.0, 0.0), (half, 0.0)])
    c.polyline(points, "sa-body")


def _glyph_capacitor(c: _Canvas, half: float, polarized: bool = False) -> None:
    gap = min(0.5, half * 0.3)
    plate = 1.6
    c.line((-half, 0.0), (-gap, 0.0), "sa-body")
    c.line((gap, 0.0), (half, 0.0), "sa-body")
    c.line((-gap, -plate), (-gap, plate), "sa-body")
    if polarized:
        # Curved plate, bulging away from the positive (first) terminal.
        c.path(f"M {_f(gap)},{_f(-plate)} Q {_f(gap + 0.9)},0 "
               f"{_f(gap)},{_f(plate)}", "sa-body")
        c.line((-gap - 1.3, -plate - 0.5), (-gap - 0.3, -plate - 0.5),
               "sa-body")
        c.line((-gap - 0.8, -plate - 1.0), (-gap - 0.8, -plate), "sa-body")
    else:
        c.line((gap, -plate), (gap, plate), "sa-body")


def _glyph_inductor(c: _Canvas, half: float) -> None:
    span = min(half * 1.6, half * 2 - 0.4)
    radius = span / 8.0
    start = -span / 2.0
    parts = [f"M {_f(-half)},0 L {_f(start)},0"]
    for i in range(4):
        x0 = start + i * 2 * radius
        parts.append(f"A {_f(radius)},{_f(radius)} 0 0 1 "
                     f"{_f(x0 + 2 * radius)},0")
    parts.append(f"L {_f(half)},0")
    c.path(" ".join(parts), "sa-body")


def _glyph_diode(c: _Canvas, half: float, kind: str = "diode") -> None:
    body = min(1.2, half * 0.7)
    c.line((-half, 0.0), (-body, 0.0), "sa-body")
    c.line((body, 0.0), (half, 0.0), "sa-body")
    c.path(f"M {_f(-body)},{_f(-body)} L {_f(-body)},{_f(body)} "
           f"L {_f(body)},0 Z", "sa-body sa-filled")
    if kind == "zener":
        c.polyline([(body - 0.5, -body), (body, -body), (body, body),
                    (body + 0.5, body)], "sa-body")
    else:
        c.line((body, -body), (body, body), "sa-body")
    if kind == "led":
        for offset in (-0.5, 0.5):
            tail = (offset - 0.2, -body - 0.4)
            tip = (offset + 0.7, -body - 1.4)
            c.line(tail, tip, "sa-body")
            c.path(f"M {_pt(tip)} L {_f(tip[0] - 0.45)},{_f(tip[1] + 0.1)} "
                   f"L {_f(tip[0] - 0.1)},{_f(tip[1] + 0.45)} Z",
                   "sa-body sa-filled")


def _glyph_source(c: _Canvas, half: float, kind: str) -> None:
    """A round source.  Local +x runs from the '-' terminal to the '+'."""
    radius = min(1.7, half * 0.8)
    c.line((-half, 0.0), (-radius, 0.0), "sa-body")
    c.line((radius, 0.0), (half, 0.0), "sa-body")
    c.circle((0.0, 0.0), radius, "sa-body")
    if kind == "dc":
        c.line((radius * 0.35, -0.5), (radius * 0.35, 0.5), "sa-body")
        c.line((radius * 0.1, 0.0), (radius * 0.6, 0.0), "sa-body")
        c.line((-radius * 0.6, 0.0), (-radius * 0.1, 0.0), "sa-body")
    elif kind == "ac":
        r = radius * 0.62
        c.path(f"M {_f(-r)},0 Q {_f(-r / 2)},{_f(-r * 1.3)} 0,0 "
               f"Q {_f(r / 2)},{_f(r * 1.3)} {_f(r)},0", "sa-body")
    elif kind == "current":
        # The arrow shows conventional current leaving the '+' terminal.
        c.line((-radius * 0.6, 0.0), (radius * 0.45, 0.0), "sa-body")
        c.path(f"M {_f(radius * 0.7)},0 L {_f(radius * 0.2)},-0.45 "
               f"L {_f(radius * 0.2)},0.45 Z", "sa-body sa-filled")


def _glyph_battery(c: _Canvas, half: float) -> None:
    """Local +x runs from '-' to '+', so the long plate is on the right."""
    c.line((-half, 0.0), (-0.6, 0.0), "sa-body")
    c.line((0.6, 0.0), (half, 0.0), "sa-body")
    c.line((-0.6, -0.8), (-0.6, 0.8), "sa-body")     # short: negative
    c.line((0.6, -1.7), (0.6, 1.7), "sa-body")       # long: positive
    c.line((1.1, -2.1), (2.1, -2.1), "sa-body")
    c.line((1.6, -2.6), (1.6, -1.6), "sa-body")


def _glyph_switch(c: _Canvas, half: float, push: bool = False) -> None:
    contact = min(1.2, half * 0.7)
    c.line((-half, 0.0), (-contact, 0.0), "sa-body")
    c.line((contact, 0.0), (half, 0.0), "sa-body")
    c.circle((-contact, 0.0), 0.28, "sa-body")
    c.circle((contact, 0.0), 0.28, "sa-body")
    if push:
        c.line((-contact, -1.3), (contact, -1.3), "sa-body")
        c.line((0.0, -1.3), (0.0, -2.1), "sa-body")
    else:
        c.line((-contact, 0.0), (contact * 0.85, -1.5), "sa-body")


def _glyph_fuse(c: _Canvas, half: float) -> None:
    body = min(1.8, half * 0.8)
    c.line((-half, 0.0), (half, 0.0), "sa-body")
    c.rect(-body, -0.85, body * 2, 1.7, "sa-body")


def _glyph_crystal(c: _Canvas, half: float) -> None:
    c.line((-half, 0.0), (-1.0, 0.0), "sa-body")
    c.line((1.0, 0.0), (half, 0.0), "sa-body")
    c.line((-1.0, -1.4), (-1.0, 1.4), "sa-body")
    c.line((1.0, -1.4), (1.0, 1.4), "sa-body")
    c.rect(-0.5, -1.1, 1.0, 2.2, "sa-body")


def _glyph_controlled_source(c: _Canvas, half: float,
                             current: bool) -> None:
    """A diamond, the shape KiCad and circuitikz both use."""
    r = min(1.8, half * 0.85)
    c.line((-half, 0.0), (-r, 0.0), "sa-body")
    c.line((r, 0.0), (half, 0.0), "sa-body")
    c.path(f"M {_f(-r)},0 L 0,{_f(-r)} L {_f(r)},0 L 0,{_f(r)} Z", "sa-body")
    if current:
        c.line((-r * 0.5, 0.0), (r * 0.35, 0.0), "sa-body")
        c.path(f"M {_f(r * 0.6)},0 L {_f(r * 0.15)},-0.4 "
               f"L {_f(r * 0.15)},0.4 Z", "sa-body sa-filled")
    else:
        c.line((r * 0.4, -0.45), (r * 0.4, 0.45), "sa-body")
        c.line((r * 0.17, 0.0), (r * 0.63, 0.0), "sa-body")
        c.line((-r * 0.63, 0.0), (-r * 0.17, 0.0), "sa-body")


#: Body length (millimetres) drawn for each two-terminal component, before
#: it is clipped to fit between the pins.
_BODY_LENGTH: Dict[ComponentType, float] = {
    ComponentType.RESISTOR: 5.0,
    ComponentType.POTENTIOMETER: 5.0,
    ComponentType.CAPACITOR: 2.4,
    ComponentType.CAPACITOR_POLARIZED: 2.4,
    ComponentType.INDUCTOR: 5.6,
    ComponentType.DIODE: 3.0,
    ComponentType.LED: 3.0,
    ComponentType.ZENER: 3.0,
    ComponentType.VOLTAGE_SOURCE: 3.6,
    ComponentType.AC_SOURCE: 3.6,
    ComponentType.CURRENT_SOURCE: 3.6,
    ComponentType.CONTROLLED_VOLTAGE_SOURCE: 3.8,
    ComponentType.CONTROLLED_CURRENT_SOURCE: 3.8,
    ComponentType.BATTERY: 3.0,
    ComponentType.SWITCH: 3.0,
    ComponentType.PUSHBUTTON: 3.0,
    ComponentType.FUSE: 4.0,
    ComponentType.CRYSTAL: 3.0,
}


# ---------------------------------------------------------------------------
# Pin-role helpers (kept deliberately small; the netlist module owns the
# authoritative electrical version of these rules)
# ---------------------------------------------------------------------------

def _ordered_pins(comp: Component) -> List[PinConnection]:
    return [comp.pins[n] for n in sorted(comp.pins, key=_pin_sort_key)]


def _named(pins: Sequence[PinConnection],
           names: Tuple[str, ...]) -> Optional[PinConnection]:
    for pin in pins:
        if pin.name.strip().lower() in names:
            return pin
    return None


def _is_dangling(pin: PinConnection, dangling: Optional[set]) -> bool:
    """True when *pin* sits on no net, or on one nothing else touches.

    circuitikz leaves an optional pin like an op-amp supply unwired in
    that case rather than drawing a lead to nowhere.
    """
    if pin.net_id < 0:
        return True
    return bool(dangling) and pin.net_id in dangling


def _bipole_ends(comp: Component) -> Tuple[Point, Point]:
    """Return (start, end) so the glyph's local +x points the right way.

    For sources that means '-' to '+', for diodes anode to cathode; for
    everything symmetric it is simply pin order.
    """
    pins = _ordered_pins(comp)
    a, b = pins[0], pins[1]
    if comp.ctype in (ComponentType.DIODE, ComponentType.LED,
                      ComponentType.ZENER):
        anode = _named(pins, ("a", "anode"))
        cathode = _named(pins, ("k", "c", "cathode"))
        if anode is not None and cathode is not None:
            return (anode.position, cathode.position)
        return (b.position, a.position)   # KiCad pin 1 is the cathode
    if comp.ctype.is_source:
        plus = _named(pins, ("+", "p", "plus", "n+", "in+"))
        minus = _named(pins, ("-", "n", "minus", "n-", "in-"))
        if plus is None:
            plus = next((p for p in pins if p.number == "1"), a)
        if minus is None or minus is plus:
            minus = next((p for p in pins if p is not plus), b)
        if comp.ctype == ComponentType.CURRENT_SOURCE:
            # The arrow points out of '+', so draw from '+' toward '-'.
            return (plus.position, minus.position)
        return (minus.position, plus.position)
    return (a.position, b.position)


# ---------------------------------------------------------------------------
# Component drawing
# ---------------------------------------------------------------------------

def _draw_two_terminal(c: _Canvas, comp: Component,
                       squeeze: _Squeeze) -> None:
    start, end = _bipole_ends(comp)
    start, end = squeeze(start), squeeze(end)
    axis = _sub(end, start)
    span = _length(axis)
    if span < 0.2:
        return
    direction = _unit(axis)
    normal = (-direction[1], direction[0])
    centre = _mid(start, end)
    body = min(_BODY_LENGTH.get(comp.ctype, 4.0), span * 0.72)
    half = body / 2.0

    c.line(start, _add(centre, _scale(direction, -half)), "sa-lead")
    c.line(_add(centre, _scale(direction, half)), end, "sa-lead")

    c.open_frame(centre, direction)
    ctype = comp.ctype
    if ctype in (ComponentType.RESISTOR, ComponentType.POTENTIOMETER):
        _glyph_resistor(c, half)
    elif ctype == ComponentType.CAPACITOR:
        _glyph_capacitor(c, half)
    elif ctype == ComponentType.CAPACITOR_POLARIZED:
        _glyph_capacitor(c, half, polarized=True)
    elif ctype == ComponentType.INDUCTOR:
        _glyph_inductor(c, half)
    elif ctype == ComponentType.DIODE:
        _glyph_diode(c, half, "diode")
    elif ctype == ComponentType.LED:
        _glyph_diode(c, half, "led")
    elif ctype == ComponentType.ZENER:
        _glyph_diode(c, half, "zener")
    elif ctype == ComponentType.VOLTAGE_SOURCE:
        _glyph_source(c, half, "dc")
    elif ctype == ComponentType.AC_SOURCE:
        _glyph_source(c, half, "ac")
    elif ctype == ComponentType.CURRENT_SOURCE:
        _glyph_source(c, half, "current")
    elif ctype == ComponentType.BATTERY:
        _glyph_battery(c, half)
    elif ctype == ComponentType.SWITCH:
        _glyph_switch(c, half)
    elif ctype == ComponentType.PUSHBUTTON:
        _glyph_switch(c, half, push=True)
    elif ctype == ComponentType.FUSE:
        _glyph_fuse(c, half)
    elif ctype == ComponentType.CRYSTAL:
        _glyph_crystal(c, half)
    elif ctype == ComponentType.CONTROLLED_VOLTAGE_SOURCE:
        _glyph_controlled_source(c, half, current=False)
    elif ctype == ComponentType.CONTROLLED_CURRENT_SOURCE:
        _glyph_controlled_source(c, half, current=True)
    else:
        c.rect(-half, -1.2, half * 2, 2.4, "sa-body")
    c.close_frame()

    if comp.ctype == ComponentType.POTENTIOMETER and len(comp.pins) >= 3:
        _draw_wiper(c, comp, centre, normal, squeeze)

    _label_bipole(c, comp, centre, normal)


def _draw_wiper(c: _Canvas, comp: Component, centre: Point,
                normal: Point, squeeze: _Squeeze) -> None:
    """Arrow from a potentiometer's wiper pin to its body."""
    pins = _ordered_pins(comp)
    wiper = _named(pins, ("w", "wiper")) or pins[2]
    tail = squeeze(wiper.position)
    towards = _unit(_sub(centre, tail))
    tip = _add(centre, _scale(towards, -1.3))
    c.line(tail, tip, "sa-lead")
    left = _add(tip, _add(_scale(towards, -0.9), _scale(normal, 0.45)))
    right = _add(tip, _add(_scale(towards, -0.9), _scale(normal, -0.45)))
    c.path(f"M {_pt(tip)} L {_pt(left)} L {_pt(right)} Z",
           "sa-body sa-filled")


def _label_bipole(c: _Canvas, comp: Component, centre: Point,
                  normal: Point) -> None:
    """Reference and value on opposite sides of the body.

    For a horizontal component that means above and below; for a vertical
    one the text is set outward from either side, so it never lands on the
    symbol -- which is what KiCad does too.
    """
    ref = _display_ref(comp)
    value = _display_value(comp)
    if not ref and not value:
        return
    # LEDs throw light arrows out to one side and batteries carry a '+'
    # sign, so those two need the text set a little further out.
    reach = 3.9 if comp.ctype in (ComponentType.LED,
                                  ComponentType.BATTERY) else 2.7
    upright = abs(normal[0]) > abs(normal[1])
    if upright:
        side = normal if normal[0] > 0 else (-normal[0], -normal[1])
        first = (_add(centre, _scale(side, reach - 0.3)), "start")
        second = (_add(centre, _scale(side, -(reach - 0.3))), "end")
    else:
        side = normal if normal[1] < 0 else (-normal[0], -normal[1])
        first = (_add(centre, _scale(side, reach)), "middle")
        second = (_add(centre, _scale(side, -reach)), "middle")
    if ref and value:
        c.label(first[0], ref, anchor=first[1])
        c.label(second[0], value, anchor=second[1])
    else:
        c.label(first[0], ref or value, anchor=first[1])


# Circuitikz's ``op amp`` node, measured at its natural size and converted
# to millimetres (1 TikZ unit is 1/SCALE = 5.184 mm).  The symbol does NOT
# stretch onto the pins: circuitikz draws it this size whatever the pin
# spacing and runs short leads out to them.  Sizing the triangle from the
# pin separation instead is what made it collapse into a sliver whenever
# the inputs were close together and the output far away.
_OPAMP_BACK = 6.169          # back edge / apex, mm from the body centre
_OPAMP_HALF_HEIGHT = 5.224   # half height of the back edge, mm
_OPAMP_INPUT_OFFSET = 2.540  # input anchors either side of the axis, mm


def _units(comp: Component) -> Dict[int, List[PinConnection]]:
    """A component's pins grouped by the placed unit they belong to.

    A dual op amp is a single component -- one chip, one netlist line --
    but two drawn symbols.  Merging them into one body draws one amplifier
    and silently loses the other, so anything that draws a symbol has to
    work per unit.
    """
    groups: Dict[int, List[PinConnection]] = {}
    for pin in _ordered_pins(comp):
        groups.setdefault(pin.unit, []).append(pin)
    return groups


def _draw_opamp(c: _Canvas, comp: Component, squeeze: _Squeeze,
                dangling: Optional[set] = None,
                pins: Optional[List[PinConnection]] = None,
                unit: Optional[int] = None) -> bool:
    """Draw one op amp at circuitikz's natural size.  False if pins absent.

    The body is a fixed-size isoceles triangle placed between the input
    pins and the output, with leads joining its anchors to the real pin
    positions -- exactly what ``circuitikz._emit_opamp`` emits.  *pins*
    restricts the drawing to one unit of a multi-unit part.
    """
    pins = pins if pins is not None else _ordered_pins(comp)
    plus = _named(pins, ("+", "in+", "inp", "vin+", "ninv"))
    minus = _named(pins, ("-", "in-", "inn", "vin-", "inv"))
    out = _named(pins, ("out", "output", "vout", "o")) \
        or next((p for p in pins if p.etype == "output"), None)
    if plus is None or minus is None or out is None:
        return False

    p_plus = squeeze(plus.position)
    p_minus = squeeze(minus.position)
    p_out = squeeze(out.position)
    back_mid = _mid(p_plus, p_minus)
    span = _sub(p_out, back_mid)
    if _length(span) < 0.5 or _length(_sub(p_plus, p_minus)) < 0.5:
        return False
    axis = _unit(span)
    # The '+' side of the body has to stay on the '+' pin's side.
    normal = _unit(_sub(p_plus, p_minus))

    # The body sits on the symbol's own origin, exactly where circuitikz
    # puts its node -- not on the input pins.  Anchoring it to the pins
    # instead pushes the back edge over whatever is wired to them: the
    # corner then runs down the ground lead of an inverting amplifier and
    # reads as part of the wire.
    centre = squeeze(comp.unit_positions.get(unit, comp.position)
                     if unit is not None else comp.position)
    if _length(_sub(centre, back_mid)) < 0.5:
        centre = _add(back_mid, _scale(axis, _OPAMP_BACK))
    back_centre = _add(centre, _scale(axis, -_OPAMP_BACK))
    apex = _add(centre, _scale(axis, _OPAMP_BACK))
    corner_plus = _add(back_centre, _scale(normal, _OPAMP_HALF_HEIGHT))
    corner_minus = _add(back_centre, _scale(normal, -_OPAMP_HALF_HEIGHT))
    anchor_plus = _add(back_centre, _scale(normal, _OPAMP_INPUT_OFFSET))
    anchor_minus = _add(back_centre, _scale(normal, -_OPAMP_INPUT_OFFSET))

    c.line(p_plus, anchor_plus, "sa-lead")
    c.line(p_minus, anchor_minus, "sa-lead")
    c.line(apex, p_out, "sa-lead")
    c.path(f"M {_pt(corner_plus)} L {_pt(corner_minus)} L {_pt(apex)} Z",
           "sa-body")

    c.label(_add(anchor_plus, _scale(axis, 1.7)), "+", size=1.9)
    c.label(_add(anchor_minus, _scale(axis, 1.7)), "−", size=1.9)

    # A supply pin gets a stub only when it is actually wired to
    # something.  circuitikz skips the lead for a dangling optional pin,
    # and drawing one leaves a stray tick floating beside the symbol.
    for pin in pins:
        if pin in (plus, minus, out) or _is_dangling(pin, dangling):
            continue
        position = squeeze(pin.position)
        c.line(position, _add(position,
                              _scale(_unit(_sub(centre, position)), 1.2)),
               "sa-lead")

    label = " ".join(x for x in (_display_ref(comp), _display_value(comp))
                     if x)
    top = min(corner_plus[1], corner_minus[1], apex[1]) - 1.6
    c.label((centre[0], top), label)
    return True


# ---------------------------------------------------------------------------
# Transistors and transformers, at circuitikz's own size
#
# The anchor offsets below were measured out of circuitikz itself with
# \pgfpointanchor (1 TikZ unit = 1/SCALE = 5.1837 mm), not guessed:
#
#   npn/pnp   base  (-0.84, 0)        collector/emitter (0, +/-0.77)
#   n/pmos    gate  (-0.98, 0)        drain/source      (0, +/-0.77)
#   n/pjfet   gate  (-0.98, -/+0.27)
#   xfmr      windings at (+/-1.05, +/-1.05)
#
# Like the op amp these are FIXED-size symbols: circuitikz draws them at
# their natural size wherever the pins happen to be and runs leads out.
# ---------------------------------------------------------------------------

_MM_PER_UNIT = 2.54 / 0.49           # 5.1837 mm to the TikZ unit

_TR_CHANNEL = 0.77 * _MM_PER_UNIT    # channel anchors, either side
_TR_BASE_X = -0.84 * _MM_PER_UNIT    # bipolar base anchor
_TR_GATE_X = -0.98 * _MM_PER_UNIT    # field-effect gate anchor
_TR_JFET_DY = 0.2695 * _MM_PER_UNIT  # a JFET's gate is off the centre line

_TR_BAR_X = -0.42 * _MM_PER_UNIT     # the vertical bar the leads meet
_TR_BAR_HALF = 0.37 * _MM_PER_UNIT
_TR_ELBOW = 0.55 * _MM_PER_UNIT      # where a channel lead turns
_TR_FOOT = 0.22 * _MM_PER_UNIT       # where it leaves the bar
_TR_GATE_PLATE_X = -0.62 * _MM_PER_UNIT
_TR_PLATE_HALF = 0.42 * _MM_PER_UNIT

_XFMR_ANCHOR = 1.05 * _MM_PER_UNIT
_XFMR_COIL_X = 0.55 * _MM_PER_UNIT
_XFMR_COIL_HALF = 0.72 * _MM_PER_UNIT
_XFMR_CORE_X = 0.09 * _MM_PER_UNIT
_XFMR_CORE_HALF = 0.78 * _MM_PER_UNIT


def _arrow(c: _Canvas, tip: Point, direction: Point,
           size: float = 1.15) -> None:
    """A filled arrowhead at *tip*, pointing along *direction*."""
    back = _add(tip, _scale(direction, -size))
    side = (-direction[1] * size * 0.42, direction[0] * size * 0.42)
    c.path("M " + _pt(tip) + " L " + _pt(_add(back, side))
           + " L " + _pt(_sub(back, side)) + " Z", "sa-body sa-filled")


def _draw_transistor(c: _Canvas, comp: Component, squeeze: _Squeeze) -> bool:
    """Draw a transistor with circuitikz's body, at circuitikz's size."""
    pins = _ordered_pins(comp)
    bipolar = comp.ctype in (ComponentType.TRANSISTOR_NPN,
                             ComponentType.TRANSISTOR_PNP)
    jfet = comp.ctype in (ComponentType.NJFET, ComponentType.PJFET)
    if bipolar:
        control = _named(pins, ("b", "base"))
        first = _named(pins, ("c", "collector"))
        second = _named(pins, ("e", "emitter"))
    else:
        control = _named(pins, ("g", "gate"))
        first = _named(pins, ("d", "drain"))
        second = _named(pins, ("s", "source"))
    if control is None or first is None or second is None:
        return False

    p_ctrl = squeeze(control.position)
    p_first = squeeze(first.position)
    p_second = squeeze(second.position)
    if _length(_sub(p_first, p_second)) < 0.5:
        return False

    # Local frame: +y toward the first channel pin, +x away from the
    # control pin -- the layout circuitikz's own shapes use.
    centre = _mid(p_first, p_second)
    up = _unit(_sub(p_first, centre))
    right = (up[1], -up[0])
    if _dot(right, _unit(_sub(p_ctrl, centre))) > 0:
        right = (-right[0], -right[1])

    def place(lx: float, ly: float) -> Point:
        return _add(centre, _add(_scale(right, lx), _scale(up, ly)))

    ctrl_x = _TR_BASE_X if bipolar else _TR_GATE_X
    ctrl_y = 0.0
    if jfet:
        ctrl_y = (-_TR_JFET_DY if comp.ctype == ComponentType.NJFET
                  else _TR_JFET_DY)

    c.line(p_ctrl, place(ctrl_x, ctrl_y), "sa-lead")
    c.line(p_first, place(0.0, _TR_CHANNEL), "sa-lead")
    c.line(p_second, place(0.0, -_TR_CHANNEL), "sa-lead")

    if bipolar:
        c.line(place(ctrl_x, 0.0), place(_TR_BAR_X, 0.0), "sa-body")
        c.line(place(_TR_BAR_X, _TR_BAR_HALF),
               place(_TR_BAR_X, -_TR_BAR_HALF), "sa-body sa-thick")
        for sign in (1.0, -1.0):
            foot = place(_TR_BAR_X, sign * _TR_FOOT)
            elbow = place(0.0, sign * _TR_ELBOW)
            c.line(foot, elbow, "sa-body")
            c.line(elbow, place(0.0, sign * _TR_CHANNEL), "sa-body")
        # The arrow marks the emitter: pointing out of an NPN, into a PNP.
        npn = comp.ctype == ComponentType.TRANSISTOR_NPN
        emitter = -1.0 if npn else 1.0
        foot = place(_TR_BAR_X, emitter * _TR_FOOT)
        elbow = place(0.0, emitter * _TR_ELBOW)
        along = _unit(_sub(elbow, foot))
        _arrow(c, _mid(foot, elbow),
               along if npn else (-along[0], -along[1]))
    else:
        c.line(place(ctrl_x, ctrl_y), place(_TR_GATE_PLATE_X, ctrl_y),
               "sa-body")
        if jfet:
            # A JFET's gate is a junction, drawn as an arrow into the bar.
            _arrow(c, place(_TR_BAR_X, ctrl_y), (right[0], right[1]))
        else:
            c.line(place(_TR_GATE_PLATE_X, _TR_PLATE_HALF),
                   place(_TR_GATE_PLATE_X, -_TR_PLATE_HALF), "sa-body")
            if comp.ctype == ComponentType.PMOS:
                c.circle(place(_TR_GATE_PLATE_X - 0.9, ctrl_y), 0.55,
                         "sa-body sa-filled")
        c.line(place(_TR_BAR_X, _TR_PLATE_HALF),
               place(_TR_BAR_X, -_TR_PLATE_HALF), "sa-body sa-thick")
        for sign in (1.0, -1.0):
            c.line(place(_TR_BAR_X, sign * _TR_ELBOW),
                   place(0.0, sign * _TR_ELBOW), "sa-body")
            c.line(place(0.0, sign * _TR_ELBOW),
                   place(0.0, sign * _TR_CHANNEL), "sa-body")

    label = " ".join(x for x in (_display_ref(comp), _display_value(comp))
                     if x)
    c.label((centre[0], min(p_first[1], p_second[1]) - 1.6), label)
    return True


def _transformer_pins_by_position(pins: List[PinConnection]):
    """Order four unnamed transformer pins as (A1, A2, B1, B2).

    Some transformer symbols leave their pins unnamed, and taking them in
    pin-number order crosses the leads over the core.  The geometry is
    unambiguous though: the four pins fall into two pairs either side of
    the body, so split on the wider axis and order each pair on the other.
    """
    if len(pins) < 4:
        return None
    chosen = pins[:4]
    xs = [p.position[0] for p in chosen]
    ys = [p.position[1] for p in chosen]
    horizontal = (max(xs) - min(xs)) >= (max(ys) - min(ys))
    across = (lambda p: p.position[0]) if horizontal else (
        lambda p: p.position[1])
    along = (lambda p: p.position[1]) if horizontal else (
        lambda p: p.position[0])
    ordered = sorted(chosen, key=across)
    first, second = ordered[:2], ordered[2:]
    if len(first) != 2 or len(second) != 2:
        return None
    first.sort(key=along)
    second.sort(key=along)
    return [first[0], first[1], second[0], second[1]]


def _draw_transformer(c: _Canvas, comp: Component, squeeze: _Squeeze) -> bool:
    """Two coupled windings and a core, the way circuitikz draws them."""
    pins = _ordered_pins(comp)
    # Position, not pin names.  KiCad's AA/AB/SA/SB do not promise that AA
    # and SA are both the top of their winding, so ordering by name can
    # hand back a pair that is upside down and cross the leads over the
    # core.  Where the pins physically sit never lies.
    named = _transformer_pins_by_position(pins)
    if named is None:
        return False
    a1, a2, b1, b2 = (squeeze(pin.position) for pin in named)
    left_mid, right_mid = _mid(a1, a2), _mid(b1, b2)
    if _length(_sub(a1, a2)) < 0.5 or _length(_sub(left_mid, right_mid)) < 0.5:
        return False
    centre = _mid(left_mid, right_mid)
    up = _unit(_sub(a1, left_mid))
    right = _unit(_sub(right_mid, left_mid))

    def place(lx: float, ly: float) -> Point:
        return _add(centre, _add(_scale(right, lx), _scale(up, ly)))

    for anchor, side, top in ((a1, -1.0, 1.0), (a2, -1.0, -1.0),
                              (b1, 1.0, 1.0), (b2, 1.0, -1.0)):
        corner = place(side * _XFMR_COIL_X, top * _XFMR_ANCHOR)
        c.line(anchor, corner, "sa-lead")
        c.line(corner, place(side * _XFMR_COIL_X, top * _XFMR_COIL_HALF),
               "sa-lead")

    # Four half-turns a winding, bulging away from the core.
    span = 2.0 * _XFMR_COIL_HALF / 4.0
    radius = span / 2.0
    for side in (-1.0, 1.0):
        x = side * _XFMR_COIL_X
        sweep = 1 if side < 0 else 0
        for turn in range(4):
            start = place(x, _XFMR_COIL_HALF - turn * span)
            end = place(x, _XFMR_COIL_HALF - (turn + 1) * span)
            c.path("M " + _pt(start) + " A " + _f(radius) + "," + _f(radius)
                   + " 0 0 " + str(sweep) + " " + _pt(end), "sa-body")

    for side in (-1.0, 1.0):
        c.line(place(side * _XFMR_CORE_X, _XFMR_CORE_HALF),
               place(side * _XFMR_CORE_X, -_XFMR_CORE_HALF), "sa-body")

    label = " ".join(x for x in (_display_ref(comp), _display_value(comp))
                     if x)
    c.label(place(0.0, -_XFMR_ANCHOR - 2.4), label)
    return True


_GATE_MARK = {
    ComponentType.AND_GATE: "&",
    ComponentType.NAND_GATE: "&",
    ComponentType.OR_GATE: "≥1",
    ComponentType.NOR_GATE: "≥1",
    ComponentType.XOR_GATE: "=1",
    ComponentType.XNOR_GATE: "=1",
    ComponentType.NOT_GATE: "1",
    ComponentType.BUFFER: "1",
}
_INVERTING_GATES = (ComponentType.NAND_GATE, ComponentType.NOR_GATE,
                    ComponentType.XNOR_GATE, ComponentType.NOT_GATE)
_OR_FAMILY = (ComponentType.OR_GATE, ComponentType.NOR_GATE,
              ComponentType.XOR_GATE, ComponentType.XNOR_GATE)
_EXCLUSIVE_GATES = (ComponentType.XOR_GATE, ComponentType.XNOR_GATE)
_SINGLE_INPUT_GATES = (ComponentType.NOT_GATE, ComponentType.BUFFER)

#: Radius of the bubble on an inverting output, in millimetres.
_BUBBLE = 0.45


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _classify_gate_pins(comp: Component):
    """Split a gate's pins into (inputs, outputs, other).

    Mirrors ``circuitikz._classify_gate_pins`` so that a gate the LaTeX
    generator can draw is one this drawing can draw too, and a gate it
    falls back to a box for falls back here as well.
    """
    pins = _ordered_pins(comp)
    inputs = [p for p in pins if p.etype == "input"]
    outputs = [p for p in pins if p.etype == "output"]
    used = {id(p) for p in inputs} | {id(p) for p in outputs}
    other = [p for p in pins if id(p) not in used]
    if not inputs and not outputs:
        signal = [p for p in pins if not p.etype.startswith("power")]
        if len(signal) >= 2:
            inputs, outputs = signal[:-1], [signal[-1]]
            used = {id(p) for p in inputs} | {id(p) for p in outputs}
            other = [p for p in pins if id(p) not in used]
    return inputs, outputs, other


def _gate_outline(c: _Canvas, ctype: ComponentType, half_l: float,
                  half_h: float) -> None:
    """The distinctive body of a logic gate, in the gate's own frame.

    Local +x points from the inputs toward the output, so one set of path
    data serves every rotation and mirroring.
    """
    if ctype in _SINGLE_INPUT_GATES:
        c.path(f"M {_f(-half_l)},{_f(-half_h)} "
               f"L {_f(-half_l)},{_f(half_h)} "
               f"L {_f(half_l)},0 Z", "sa-body")
        return

    if ctype in _OR_FAMILY:
        # Two curved flanks meeting at a nose, over a concave back.
        c.path(f"M {_f(-half_l)},{_f(-half_h)} "
               f"Q {_f(half_l * 0.2)},{_f(-half_h)} {_f(half_l)},0 "
               f"Q {_f(half_l * 0.2)},{_f(half_h)} "
               f"{_f(-half_l)},{_f(half_h)} "
               f"Q {_f(-half_l * 0.45)},0 "
               f"{_f(-half_l)},{_f(-half_h)} Z", "sa-body")
        if ctype in _EXCLUSIVE_GATES:
            back = -half_l - 0.9
            c.path(f"M {_f(back)},{_f(-half_h)} "
                   f"Q {_f(back + half_l * 0.55)},0 "
                   f"{_f(back)},{_f(half_h)}", "sa-body")
        return

    # AND family: a rectangle closed by a semicircular nose.
    straight = half_l - half_h
    c.path(f"M {_f(-half_l)},{_f(-half_h)} "
           f"L {_f(straight)},{_f(-half_h)} "
           f"A {_f(half_h)},{_f(half_h)} 0 0 1 "
           f"{_f(straight)},{_f(half_h)} "
           f"L {_f(-half_l)},{_f(half_h)} Z", "sa-body")


def _draw_gate(c: _Canvas, comp: Component, squeeze: _Squeeze,
               dangling: Optional[set] = None) -> bool:
    """Draw a logic gate with its proper shape.  False to fall back to a box.

    KiCad draws logic as a numbered IC rectangle; circuitikz draws the
    distinctive AND/OR shapes, and this preview stands in for the LaTeX
    output, so it draws them too.
    """
    inputs, outputs, other = _classify_gate_pins(comp)
    limit = 1 if comp.ctype in _SINGLE_INPUT_GATES else 3
    if len(outputs) != 1 or not 1 <= len(inputs) <= limit:
        return False

    out_point = squeeze(outputs[0].position)
    in_points = [squeeze(pin.position) for pin in inputs]
    back = (sum(p[0] for p in in_points) / len(in_points),
            sum(p[1] for p in in_points) / len(in_points))
    reach = _length(_sub(out_point, back))
    if reach < 2.5:
        return False
    axis = _unit(_sub(out_point, back))
    normal = (-axis[1], axis[0])

    spread = max((abs(_dot(_sub(p, back), normal)) for p in in_points),
                 default=0.0)
    half_h = max(spread + 1.0, 2.2)
    bubble = _BUBBLE if comp.ctype in _INVERTING_GATES else 0.0

    # Leave a lead at each end; the nose stops short of the bubble.
    front = reach - 1.0 - 2.0 * bubble
    start = min(0.7, max(0.0, front - 2.2))
    half_l = max((front - start) / 2.0, half_h * 0.95)
    origin = _add(back, _scale(axis, start + half_l))

    for pin, point in zip(inputs, in_points):
        offset = _dot(_sub(point, back), normal)
        inset = 0.4 if comp.ctype in _OR_FAMILY else 0.0
        edge = _add(_add(origin, _scale(axis, -half_l + inset)),
                    _scale(normal, offset))
        c.line(point, edge, "sa-lead")

    nose = _add(origin, _scale(axis, half_l))
    if bubble:
        centre = _add(nose, _scale(axis, bubble))
        c.circle(centre, bubble, "sa-body")
        c.line(_add(centre, _scale(axis, bubble)), out_point, "sa-lead")
    else:
        c.line(nose, out_point, "sa-lead")

    c.open_frame(origin, axis)
    _gate_outline(c, comp.ctype, half_l, half_h)
    c.close_frame()

    for pin in other:                    # supply pins keep a short stub
        if _is_dangling(pin, dangling):
            continue
        point = squeeze(pin.position)
        c.line(point, _add(point, _scale(_unit(_sub(origin, point)), 1.2)),
               "sa-lead")

    label = " ".join(x for x in (_display_ref(comp), _display_value(comp))
                     if x)
    c.label((origin[0], origin[1] - half_h - 1.8), label)
    return True


def _draw_box(c: _Canvas, comp: Component, squeeze: _Squeeze) -> None:
    """A labelled rectangle with stubs landing on the true pin positions.

    This is the honest fallback: whatever the part is, its wiring stays
    exactly where the schematic put it.
    """
    points = [squeeze(pin.position) for pin in _ordered_pins(comp)]
    if not points:
        return
    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)
    # Inset from the pins, but never collapse to nothing.
    left = min_x + _STUB if max_x - min_x > 2 * _STUB + 2.0 else min_x - 1.0
    right = max_x - _STUB if max_x - min_x > 2 * _STUB + 2.0 else max_x + 1.0
    top = min_y + _STUB if max_y - min_y > 2 * _STUB + 2.0 else min_y - 1.0
    bottom = max_y - _STUB if max_y - min_y > 2 * _STUB + 2.0 else max_y + 1.0
    if right - left < 4.0:
        centre_x = (left + right) / 2.0
        left, right = centre_x - 2.0, centre_x + 2.0
    if bottom - top < 4.0:
        centre_y = (top + bottom) / 2.0
        top, bottom = centre_y - 2.0, centre_y + 2.0

    c.rect(left, top, right - left, bottom - top, "sa-body", radius=0.4)
    for point in points:
        clamped = (min(max(point[0], left), right),
                   min(max(point[1], top), bottom))
        if _length(_sub(clamped, point)) > 1e-6:
            c.line(point, clamped, "sa-lead")

    centre = ((left + right) / 2.0, (top + bottom) / 2.0)
    mark = _GATE_MARK.get(comp.ctype)
    if mark:
        c.label(_add(centre, (0.0, -0.4)), mark, size=2.0)
        if comp.ctype in _INVERTING_GATES:
            output = next((p for p in _ordered_pins(comp)
                           if p.etype == "output"), None)
            if output is not None:
                position = squeeze(output.position)
                towards = _unit(_sub(centre, position))
                c.circle(_add(_clamp_to_box(position, left, top, right,
                                            bottom),
                              _scale(towards, 0.45)), 0.45, "sa-body")
        c.label(_add(centre, (0.0, 1.6)), _display_ref(comp), size=1.3)
    else:
        label = _display_ref(comp)
        value = _display_value(comp)
        if label and value:
            c.label(_add(centre, (0.0, -0.8)), label)
            c.label(_add(centre, (0.0, 0.9)), value, size=1.3)
        else:
            c.label(centre, label or value)


def _clamp_to_box(point: Point, left: float, top: float,
                  right: float, bottom: float) -> Point:
    return (min(max(point[0], left), right),
            min(max(point[1], top), bottom))


def _draw_component(c: _Canvas, comp: Component, squeeze: _Squeeze,
                    fallbacks: Optional[List[str]] = None,
                    dangling: Optional[set] = None) -> None:
    ctype = comp.ctype
    if len(comp.pins) >= 2 and (ctype.is_two_terminal
                                or ctype == ComponentType.POTENTIOMETER):
        _draw_two_terminal(c, comp, squeeze)
        return
    if ctype == ComponentType.OPAMP:
        drawn = False
        for unit, unit_pins in sorted(_units(comp).items()):
            if _draw_opamp(c, comp, squeeze, dangling, unit_pins, unit):
                drawn = True
        if drawn:
            return
    if ctype.is_transistor and len(comp.pins) >= 3 \
            and _draw_transistor(c, comp, squeeze):
        return
    if ctype.is_gate and _draw_gate(c, comp, squeeze, dangling):
        return
    if (ctype == ComponentType.TRANSFORMER
            and _draw_transformer(c, comp, squeeze)):
        return
    _draw_box(c, comp, squeeze)
    if fallbacks is not None and not ctype.is_gate:
        fallbacks.append(comp.ref)


# ---------------------------------------------------------------------------
# Power symbols and net labels
# ---------------------------------------------------------------------------

def _draw_power_symbol(c: _Canvas, inst: SymbolInstance, pin_point: Point,
                       name: str, ground: bool, squeeze: _Squeeze) -> None:
    """Ground bars or a rail bar, pointing away from the wire."""
    anchor = squeeze(pin_point)
    origin = squeeze((inst.x, inst.y))
    direction = _sub(origin, anchor)
    if _length(direction) < 0.2:
        direction = (0.0, 1.0)               # KiCad's default: below the pin
    direction = _unit(direction)
    across = (-direction[1], direction[0])

    if ground:
        c.line(anchor, _add(anchor, _scale(direction, 1.3)), "sa-lead")
        for step, width in ((1.3, 1.5), (1.9, 0.95), (2.5, 0.4)):
            centre = _add(anchor, _scale(direction, step))
            c.line(_add(centre, _scale(across, width)),
                   _add(centre, _scale(across, -width)), "sa-body")
        return

    c.line(anchor, _add(anchor, _scale(direction, 1.6)), "sa-lead")
    bar = _add(anchor, _scale(direction, 1.6))
    c.line(_add(bar, _scale(across, 1.3)), _add(bar, _scale(across, -1.3)),
           "sa-body")
    c.label(_add(anchor, _scale(direction, 3.0)), name, size=_LABEL_TEXT,
            cls="sa-text sa-netname")


def _draw_net_label(c: _Canvas, text: str, position: Point, kind: LabelKind,
                    squeeze: _Squeeze) -> None:
    """Draw a net label as plain text, anchored where KiCad anchors it.

    No tag or flag outline: ``circuitikz._emit_labels`` writes a bare
    ``\node`` with the text in it, and this drawing exists to agree with
    that output.  KiCad's own editor draws a shaped tag, which is why the
    two look different side by side -- the LaTeX rendering is the one this
    preview is standing in for.
    """
    del kind                          # every label kind draws the same way
    point = squeeze(position)
    c.label(_add(point, (0.6, -0.9)), text, size=_LABEL_TEXT, anchor="start",
            cls="sa-text sa-netname")


_LOOP_TEXT = 1.8


def _draw_loops(c: _Canvas, graph: CircuitGraph, squeeze: _Squeeze) -> None:
    found = find_meshes(graph)
    for mesh in found.meshes if found.ok else ():
        cx, cy = squeeze(mesh.centre)
        r = mesh.radius
        first, last = (120, -150) if mesh.clockwise else (60, 330)

        def at(degrees: float) -> Point:
            angle = math.radians(degrees)
            return (cx + r * math.cos(angle), cy - r * math.sin(angle))

        start, end = at(first), at(last)
        sweep = 1 if mesh.clockwise else 0
        c.path(f"M {_f(start[0])} {_f(start[1])} A {_f(r)} {_f(r)} 0 1 "
               f"{sweep} {_f(end[0])} {_f(end[1])}", "sa-loop")
        angle = math.radians(last)
        turn = 1 if mesh.clockwise else -1
        heading = (turn * math.sin(angle), turn * math.cos(angle))
        side = (-heading[1], heading[0])
        tip = _add(end, _scale(heading, 0.5))
        back = _add(end, _scale(heading, -0.8))
        wing_a = _add(back, _scale(side, 0.55))
        wing_b = _add(back, _scale(side, -0.55))
        c.path(f"M {_f(tip[0])} {_f(tip[1])} L {_f(wing_a[0])} "
               f"{_f(wing_a[1])} L {_f(wing_b[0])} {_f(wing_b[1])} Z",
               "sa-loop sa-filled")
        c.label((cx, cy), mesh.name, size=_LOOP_TEXT, cls="sa-text sa-loop")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_STYLE = """
svg.schemaccess .sa-wire, svg.schemaccess .sa-lead,
svg.schemaccess .sa-body {
  stroke: currentColor; fill: none;
  stroke-linecap: round; stroke-linejoin: round;
}
svg.schemaccess .sa-wire, svg.schemaccess .sa-lead { stroke-width: %(wire)s; }
svg.schemaccess .sa-body { stroke-width: %(body)s; }
svg.schemaccess .sa-thick { stroke-width: %(thick)s; }
svg.schemaccess .sa-filled { fill: currentColor; }
svg.schemaccess .sa-ghost { stroke-width: %(body)s; opacity: 0.45; }
svg.schemaccess .sa-dot { fill: currentColor; stroke: none; }
svg.schemaccess .sa-text {
  fill: currentColor; stroke: none;
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
}
svg.schemaccess .sa-netname { opacity: 0.75; font-style: italic; }
svg.schemaccess .sa-loop {
  stroke: #1d4ed8; fill: none; stroke-width: %(body)s;
  stroke-linecap: round;
}
svg.schemaccess .sa-loop.sa-filled, svg.schemaccess .sa-text.sa-loop {
  fill: #1d4ed8; stroke: none; font-style: italic;
}
""".strip()

# Only a standalone file needs to pick its own ink colour; embedded in a
# page the drawing inherits ``currentColor``, so saying nothing here lets
# the host theme -- light or dark -- decide.
_STANDALONE_STYLE = """
svg.schemaccess { color: #111827; background: transparent; }
@media (prefers-color-scheme: dark) {
  svg.schemaccess { color: #e5e7eb; }
  svg.schemaccess .sa-loop { stroke: #93c5fd; }
  svg.schemaccess .sa-loop.sa-filled,
  svg.schemaccess .sa-text.sa-loop { fill: #93c5fd; }
}
""".strip()


@dataclass
class Bounds:
    """The drawing's extent in millimetres, plus what to call it."""
    min_x: float = 0.0
    min_y: float = 0.0
    width: float = 40.0
    height: float = 20.0
    title: str = "Circuit schematic"


def draw(graph: CircuitGraph, canvas, *,
         junction_dots: bool = True, loops: bool = False) -> Bounds:
    """Draw *graph* onto *canvas* and return the extent of the result.

    ``canvas`` is anything with the method set :class:`_Canvas` provides
    (``line``, ``polyline``, ``circle``, ``rect``, ``path``, ``label``,
    ``open_frame``, ``close_frame``).  Splitting this out is what lets a
    second back end -- :mod:`schemaccess.pdfwriter` -- produce a PDF from
    exactly the same drawing code, so the two can never drift apart.
    """
    doc = graph.document if graph.document is not None else SchematicDocument()

    raw_points: List[Point] = []
    for wire in doc.wires:
        raw_points.extend(wire.points)
    for inst in doc.symbols:
        lib = doc.lib_symbol_for(inst)
        if lib is None:
            continue
        for pin in lib.pins_for_unit(inst.unit):
            raw_points.append(inst.pin_position(pin))
    for comp in graph.components.values():
        for pin in comp.pins.values():
            raw_points.append(pin.position)
    for label in doc.labels:
        raw_points.append((label.x, label.y))

    squeeze = _Squeeze([p[0] for p in raw_points])

    for wire in doc.wires:
        canvas.polyline([squeeze(p) for p in wire.points], "sa-wire")
    if junction_dots:
        for junction in doc.junctions:
            canvas.circle(squeeze((junction.x, junction.y)), _JUNCTION_R,
                          "sa-dot")

    # Nets joining fewer than two pins are dangling; an optional pin on
    # one of them gets no lead, matching the CircuiTikZ output.
    dangling = {net.net_id for net in graph.nets if len(net.pins) < 2}
    for ref in sorted(graph.components, key=_ref_key):
        _draw_component(canvas, graph.components[ref], squeeze,
                        dangling=dangling)

    for inst in doc.symbols:
        lib = doc.lib_symbol_for(inst)
        if lib is None or not (lib.is_power or inst.reference.startswith("#")):
            continue
        if inst.reference.startswith("#FLG"):
            continue                     # a PWR_FLAG draws nothing useful
        name = power.net_name(inst)
        for pin in lib.pins_for_unit(inst.unit):
            _draw_power_symbol(canvas, inst, inst.pin_position(pin), name,
                               power.is_ground(inst, lib), squeeze)

    for label in doc.labels:
        _draw_net_label(canvas, label.text, (label.x, label.y), label.kind,
                        squeeze)

    if loops:
        _draw_loops(canvas, graph, squeeze)

    points = [squeeze(p) for p in raw_points]
    bounds = Bounds()
    if points:
        bounds.min_x = min(p[0] for p in points) - _MARGIN
        bounds.min_y = min(p[1] for p in points) - _MARGIN
        bounds.width = max(p[0] for p in points) - bounds.min_x + _MARGIN
        bounds.height = max(p[1] for p in points) - bounds.min_y + _MARGIN
    if doc.source_path:
        bounds.title = doc.source_path.replace("\\", "/").rsplit("/", 1)[-1]
    return bounds


def generate(graph: CircuitGraph, *, junction_dots: bool = True,
             standalone: bool = True, loops: bool = False) -> str:
    """Return an SVG drawing of *graph* as a string.

    With *standalone* true (the default) the result carries its own
    ``<style>`` block and an XML header, so it can be saved as a ``.svg``
    file and opened anywhere.  Either way the drawing inherits the page's
    text colour through ``currentColor``, so it reads correctly in a light
    or a dark theme.
    """
    canvas = _Canvas()
    bounds = draw(graph, canvas, junction_dots=junction_dots, loops=loops)
    min_x, min_y = bounds.min_x, bounds.min_y
    width, height, title = bounds.width, bounds.height, bounds.title

    style = _STYLE % {"wire": _f(_WIRE_W), "body": _f(_BODY_W),
                      "thick": _f(_BODY_W * 1.8)}
    if standalone:
        style = _STANDALONE_STYLE + "\n" + style
    parts: List[str] = []
    if standalone:
        parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" class="schemaccess" '
        f'viewBox="{_f(min_x)} {_f(min_y)} {_f(width)} {_f(height)}" '
        f'width="{_f(width * PX_PER_MM)}" '
        f'height="{_f(height * PX_PER_MM)}" role="img" '
        f'aria-label="{_esc(title)}">')
    parts.append(f"<title>{_esc(title)}</title>")
    parts.append(f"<style>{style}</style>")
    parts.append('<g class="sa-wires">')
    parts.extend(canvas.wires)
    parts.append("</g>")
    parts.append('<g class="sa-symbols">')
    parts.extend(canvas.bodies)
    parts.append("</g>")
    parts.append('<g class="sa-labels">')
    parts.extend(canvas.text)
    parts.append("</g>")
    parts.append("</svg>")
    parts.append("")
    return "\n".join(parts)

"""Lcapy netlist export: circuit graph -> a netlist ``lcapy.Circuit`` loads.

`Lcapy <https://lcapy.readthedocs.io>`_ analyses linear circuits
symbolically from a SPICE-like netlist.  This module writes that netlist
from the same :class:`~schemaccess.model.CircuitGraph` every other
exporter reads, so it agrees with them by construction.  Like them it is
a pure formatter: it never imports Lcapy and never touches the
filesystem, so SchemAccess gains no dependency by offering the format.

Lcapy's netlist looks like SPICE and differs from it in ways that matter,
each of which was checked against Lcapy itself rather than assumed:

*Ground is node 0.*  Every ground net becomes ``0`` and nothing else
does.  Without it Lcapy still loads the circuit but reports no node
voltages, so a schematic with no ground gets a note saying so.

*The other nodes are the description's.*  A labelled net keeps its label,
and an unlabelled one takes the number the alt text gives it - "node 2"
there is node ``2`` here - so a reader can go from the description
straight to ``cct[2].V``.

*A name is also a type.*  Lcapy reads a component's type off the longest
type name its name starts with: ``RV1`` is a potentiometer and ``VM1`` a
voltmeter whatever they were meant to be, and a battery called ``BT1`` is
no source at all.  So a reference is kept when it already reads as the
right type and is prefixed when it does not (``BT1`` -> ``VBT1``).

*Values are plain numbers.*  Lcapy accepts some SPICE suffixes and gets
others wrong (``22n`` picks up a rounding error, ``1Meg`` is a syntax
error), so ``4k7`` is written ``4700`` and ``22n`` ``2.2e-8``.

*A missing value is a symbol, not a guess.*  A netlist line with no value
makes Lcapy carry the component as a symbol of the same name, which is
what a symbolic tool is for.  The SPICE writer has to invent ``1k``; this
one does not.

*A current source points the other way.*  SPICE's ``I1 a b`` drives
current through the source from ``a`` to ``b``; Lcapy's drives it out of
``a`` into the circuit.  The two nodes are therefore written in the
opposite order to the SPICE deck's, and likewise for a voltage-controlled
current source.

*Nothing is dropped silently.*  A part Lcapy has no model for is written
as a comment with its pin-to-node map and named in the notes.  A part
Lcapy can draw but not analyse (a diode, a transistor) stays in the
netlist, and the notes say what it will stop.

For the analysis backend to come
--------------------------------
:class:`LcapyNetlist` carries more than text.  It records which net
became which node, which reference became which Lcapy name, which parts
were left out, which stop a linear analysis, and which symbols stand in
for numbers the schematic does not give - everything a caller needs to
decide whether the circuit can be solved and to translate the answers
back into the schematic's own terms, without parsing the text again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from .model import (CircuitGraph, Component, ComponentType, Net, NetKind,
                    PinConnection)
from .netlist import (NetlistResult, _by_pin_names, _diode_terminals,
                      _pin_named, _pins_in_order, _sim_pin_roles,
                      _source_terminals, _spice_value)

__all__ = ["LCAPY_TYPES", "LcapyNetlist", "generate"]

#: Every component type name Lcapy's parser knows (``lcapy/grammar.py``),
#: plus ``RL``, which its documentation gives for a reluctance.  The type
#: of a component is the *longest* of these its name starts with, so this
#: list is what decides whether a reference can be used as it stands.
#: ``tests/test_lcapy_netlist.py`` checks it against the installed Lcapy.
LCAPY_TYPES: Tuple[str, ...] = (
    "Cable", "CCCS", "CCVS", "MISC", "VCCS", "VCVS",
    "ADC", "ANT", "BAT", "CPE", "DAC", "REL",
    "AM", "BL", "FB", "FS", "GY", "MT", "MX", "NR", "RL", "RV", "SP", "SW",
    "TF", "TL", "TP", "TR", "TV", "VM", "XT",
    "A", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "O", "P",
    "Q", "R", "S", "U", "V", "W", "Y", "Z", "k", "m", "r",
)
_TYPES_LONGEST_FIRST = tuple(sorted(LCAPY_TYPES, key=lambda n: (-len(n), n)))

_NAME_RE = re.compile(r"[^A-Za-z0-9_]")
# A node may be called anything without a period; signs are kept so a
# rail stays '+5V'.  Spaces, brackets and commas delimit arguments, '='
# introduces a named one and ';' starts the drawing attributes.
_NODE_RE = re.compile(r"[^A-Za-z0-9_+\-]")

# What _spice_value hands back: a number and at most one SPICE suffix.
_MAGNITUDE_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)(T|G|Meg|k|m|u|n|p|f)?$")
_EXPONENT = {"": 0, "T": 12, "G": 9, "Meg": 6, "k": 3,
             "m": -3, "u": -6, "n": -9, "p": -12, "f": -15}

# '+5V', '-12V', '+3V3', '3.3V', '+5VA': a net named for the voltage it
# is held at.
_RAIL_RE = re.compile(r"^([+-]?)(\d+)(?:\.(\d+))?V(\d+)?[A-Za-z]?$")

# Simulation symbols whose name states a waveform Lcapy has no netlist
# form for.
_WAVEFORM_RE = re.compile(r"^[vi](pulse|exp|pwl|sffm|am|trnoise|trrandom)",
                          re.IGNORECASE)
_SINE_RE = re.compile(r"^[vi](sin|ac)", re.IGNORECASE)

_PLUS_NAMES = ("+", "in+", "inp", "vin+", "ninv")
_MINUS_NAMES = ("-", "in-", "inn", "vin-", "inv")
_OUT_NAMES = ("out", "output", "vout", "o")

_VOLTAGE_SOURCES = (ComponentType.VOLTAGE_SOURCE, ComponentType.BATTERY,
                    ComponentType.AC_SOURCE)


@dataclass
class LcapyNetlist(NetlistResult):
    """An Lcapy netlist, and what a caller needs to make use of it.

    ``text`` and ``notes`` are the same two things every other netlist
    writer returns.  The rest is the map between the schematic and the
    netlist, kept so that whatever solves the circuit later can report
    its answers in the schematic's terms.
    """
    #: Net id -> the Lcapy node it became.  Ground nets are ``'0'``.
    nodes: Dict[int, str] = field(default_factory=dict)
    #: Schematic reference -> the Lcapy component name(s) written for it.
    #: A dual op amp is one reference and two names.
    names: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    #: References left out of the circuit and written as comments.  The
    #: netlist is the schematic minus these.
    omitted: List[str] = field(default_factory=list)
    #: References Lcapy loads and draws but cannot analyse: diodes,
    #: transistors, crystals.
    drawn_only: List[str] = field(default_factory=list)
    #: Switches, which make the circuit time-varying; Lcapy wants
    #: ``replace_switches(t)`` or ``convert_IVP(t)`` first.
    switches: List[str] = field(default_factory=list)
    #: Symbol -> what it stands for, for every quantity the schematic
    #: does not give a number for.
    symbols: Dict[str, str] = field(default_factory=dict)
    #: Supply-rail node -> its voltage as written on the schematic
    #: (``None`` when the name gives none), for rails nothing drives.
    undriven_rails: Dict[str, Optional[str]] = field(default_factory=dict)
    #: Whether the schematic has a ground, i.e. whether node 0 exists.
    has_ground: bool = False
    #: The references of each separately wired circuit that has no path
    #: to node 0.  A sheet of worked examples holds several circuits, and
    #: Lcapy can solve only those that reach ground.
    ungrounded: List[Tuple[str, ...]] = field(default_factory=list)

    @property
    def analysable(self) -> bool:
        """True when Lcapy can run a linear analysis on the netlist as it
        stands: every circuit in it reaches a ground, and nothing in it
        is non-linear or switched."""
        return (bool(self.names) and self.has_ground
                and not self.ungrounded and not self.drawn_only
                and not self.switches)

    @property
    def complete(self) -> bool:
        """True when every component of the schematic is in the netlist."""
        return not self.omitted


# ---------------------------------------------------------------------------
# Names, nodes and numbers
# ---------------------------------------------------------------------------

def _parsed_type(name: str) -> str:
    """The component type Lcapy reads off *name*, ``''`` for none.

    This is Lcapy's own rule: the longest type name the name starts with.
    """
    for kind in _TYPES_LONGEST_FIRST:
        if name.startswith(kind):
            return kind
    return ""


def _lcapy_name(kind: str, ref: str, used: Optional[set] = None) -> str:
    """A component name Lcapy reads as *kind*, with *ref* still in it.

    ``R1`` stays ``R1``.  ``BT1`` is no type Lcapy knows, so as a voltage
    source it becomes ``VBT1``; a resistor called ``RV1`` would be read
    as a potentiometer, so it becomes ``RRV1``; and where even the prefix
    runs into another type (a source called ``M1`` -> ``VM1``, a
    voltmeter) an underscore keeps them apart: ``V_M1``.  No type name
    contains an underscore, so that last form always reads as *kind*.

    Names must also be unique - Lcapy quietly replaces an earlier
    component of the same name - so one already in *used* gets a
    numeric suffix.
    """
    clean = _NAME_RE.sub("_", ref).strip("_") or "1"
    name = f"{kind}_{clean}"
    for candidate in (clean, kind + clean):
        if _parsed_type(candidate) == kind:
            name = candidate
            break
    if used is not None:
        base, suffix = name, 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
    return name


def _plain(number: Decimal) -> str:
    """*number* written so Lcapy reads it exactly: ``4700``, ``2.2e-8``.

    Everyday magnitudes are written out in full, the way a person types
    them; very small and very large ones use an exponent.  Either way
    the digits are the schematic's own, never a binary float's.
    """
    if number == 0:
        return "0"
    number = number.normalize()
    exponent = number.adjusted()
    if -4 <= exponent < 9:
        return format(number, "f")
    digits = "".join(str(d) for d in number.as_tuple().digits)
    mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{'-' if number.is_signed() else ''}{mantissa}e{exponent}"


def _number(value: str, ctype: ComponentType) -> Optional[str]:
    """A KiCad value as a plain number, or ``None`` if it is not one.

    Reading the value is left to the SPICE writer's parser, which already
    knows ``4k7``, ``22nF``, ``100 Ohm`` and ``1MEG``; only its suffix is
    multiplied out.
    """
    match = _MAGNITUDE_RE.match(_spice_value(value, ctype) or "")
    if match is None:
        return None
    try:
        number = Decimal(match.group(1)).scaleb(
            _EXPONENT[match.group(2) or ""])
    except InvalidOperation:
        return None
    return _plain(number)


def _sim_params(comp: Component) -> Dict[str, str]:
    """KiCad's ``Sim.Params`` as a dictionary: ``'dc=5 f=1k'``.

    A simulation symbol can keep its numbers here and show only
    ``${SIM.PARAMS}`` as its value, so this is where a value has to be
    looked for when the Value field does not hold one.
    """
    params: Dict[str, str] = {}
    for token in comp.properties.get("Sim.Params", "").split():
        key, sep, value = token.partition("=")
        if sep:
            params[key.strip().lower()] = value.strip().strip('"')
    return params


def _node_names(graph: CircuitGraph) -> Dict[int, str]:
    """Net id -> Lcapy node name.

    The numbering is :func:`schemaccess.netbuilder.node_names`'s, so the
    description and the netlist agree: ground is ``0``, a supply rail or
    a labelled net keeps its name, an unlabelled net that joins two or
    more pins takes its node number, and a pin joined to nothing keeps
    KiCad's own net name.  Labels are settled first, because they are the
    author's and a number must give way to one.
    """
    names: Dict[int, str] = {}
    used = {"0"}
    numbered: List[Tuple[Net, int]] = []
    counter = 0

    def claim(net: Net, wanted: str) -> None:
        base = _NODE_RE.sub("_", wanted).strip("_")
        if not base or base == "0":
            base = f"N{net.net_id + 1}"
        name, suffix = base, 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        names[net.net_id] = name

    for net in graph.nets:
        if net.kind == NetKind.GROUND:
            names[net.net_id] = "0"
            continue
        is_node = net.kind != NetKind.POWER and len(net.pins) >= 2
        if is_node:
            counter += 1
        if net.kind in (NetKind.POWER, NetKind.NAMED):
            claim(net, net.name)
        elif is_node:
            numbered.append((net, counter))
        else:
            claim(net, net.name)
    for net, number in numbered:
        claim(net, str(number))
    return names


def _rail_voltage(net: Net) -> Tuple[bool, Optional[str]]:
    """Whether *net* is a supply rail, and the voltage its name states.

    A power symbol's net is a rail whatever it is called; a label is one
    only when it is written as a voltage (``+5V``, ``-12V``, ``+3V3``).
    """
    match = _RAIL_RE.match(net.name.strip())
    if match is None:
        return (net.kind == NetKind.POWER, None)
    if net.kind not in (NetKind.POWER, NetKind.NAMED):
        return (False, None)
    sign, whole, decimal, after = match.groups()
    fraction = decimal or after
    text = whole + (f".{fraction}" if fraction else "")
    return (True, _plain(Decimal(("-" if sign == "-" else "") + text)))


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _waveform(comp: Component) -> str:
    """The waveform a simulation source is marked with, ``''`` for DC.

    ``'SIN'`` for a sinusoid, otherwise KiCad's own name for the shape
    (``'PULSE'``, ``'EXP'``, ``'PWL'``...).  Read from ``Sim.Type`` first
    and from the symbol's name second, because a symbol placed before
    its simulation model was edited carries only the name.
    """
    kind = comp.properties.get("Sim.Type", "").strip().upper()
    part = comp.lib_id.rpartition(":")[2]
    if comp.ctype == ComponentType.AC_SOURCE or kind == "SIN" \
            or _SINE_RE.match(part):
        return "SIN"
    if kind and kind != "DC":
        return kind
    match = _WAVEFORM_RE.match(part)
    return match.group(1).upper() if match else ""


def _radians(degrees: str) -> Optional[str]:
    """A phase in degrees as an exact multiple of pi, for Lcapy."""
    try:
        turn = Fraction(degrees.strip().rstrip("°")) / 180
    except (ValueError, ZeroDivisionError):
        return None
    if turn == 0:
        return "0"
    top = {1: "pi", -1: "-pi"}.get(turn.numerator, f"{turn.numerator}*pi")
    return "{%s}" % (top if turn.denominator == 1
                     else f"{top}/{turn.denominator}")


def _sine_parts(comp: Component
                ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """``(amplitude, phase, angular frequency)`` of a sinusoidal source.

    Read from the Value field the way people write one - ``5V 1kHz``,
    ``10∠90`` - and from ``Sim.Params`` for whatever that leaves out.
    Each part is ``None`` when the schematic does not give it.
    """
    amplitude = phase = frequency = None
    for token in (comp.value or "").split():
        if "∠" in token:
            left, _sep, right = token.partition("∠")
            amplitude = amplitude or _number(left, comp.ctype)
            phase = phase or _radians(right)
        elif token.lower().endswith("hz"):
            frequency = frequency or _number(token[:-2], comp.ctype)
        else:
            amplitude = amplitude or _number(token, comp.ctype)
    params = _sim_params(comp)
    amplitude = amplitude or _number(params.get("ampl", ""), comp.ctype)
    frequency = frequency or _number(params.get("f", ""), comp.ctype)
    if phase is None and "ph" in params:
        phase = _radians(params["ph"])
    omega = "{2*pi*%s}" % frequency if frequency is not None else None
    return amplitude, phase, omega


# ---------------------------------------------------------------------------
# Multi-pin parts
# ---------------------------------------------------------------------------

def _opamp_units(comp: Component
                 ) -> List[Tuple[int, PinConnection, PinConnection,
                                 PinConnection]]:
    """``(unit, plus, minus, out)`` for each amplifier in *comp*.

    A dual op amp is one component with two amplifiers and a third unit
    that only carries the supply pins, so the pins are sorted by unit
    first; taking the first '+', '-' and output of the whole chip would
    wire up one amplifier and lose the other without a word.
    """
    roles = _sim_pin_roles(comp)
    by_unit: Dict[int, List[PinConnection]] = {}
    for pin in _pins_in_order(comp):
        by_unit.setdefault(pin.unit, []).append(pin)

    found = []
    for unit in sorted(by_unit):
        pins = by_unit[unit]
        plus = _pin_named(pins, _PLUS_NAMES) or next(
            (p for p in pins if roles.get(p.number) in ("+", "in+")), None)
        minus = _pin_named(pins, _MINUS_NAMES) or next(
            (p for p in pins if roles.get(p.number) in ("-", "in-")), None)
        out = _pin_named(pins, _OUT_NAMES) \
            or next((p for p in pins if roles.get(p.number) == "out"), None) \
            or next((p for p in pins if p.etype == "output"), None)
        if plus is not None and minus is not None and out is not None:
            found.append((unit, plus, minus, out))
    return found


def _dotted_first(comp: Component, first: PinConnection,
                  second: PinConnection
                  ) -> Tuple[PinConnection, PinConnection]:
    """The two ends of a winding, dotted end first.

    Lcapy puts a winding's dot on its positive node.  Where the symbol
    carries phase dots, the end nearer one is that node; where it does
    not, the winding's first pin is, which is the end KiCad letters 'A'.
    """
    def nearest(pin: PinConnection) -> float:
        return min(((pin.position[0] - x) ** 2 + (pin.position[1] - y) ** 2)
                   for (x, y), _radius in comp.dots)

    if comp.dots and nearest(second) < nearest(first):
        return second, first
    return first, second


def _control_pins(comp: Component) -> Optional[List[PinConnection]]:
    """``[out+, out-, control+, control-]`` of a voltage-controlled
    source, or ``None`` when the symbol has no control pins or is
    controlled by a current, which Lcapy names by its source instead."""
    lowered = comp.lib_id.lower()
    if "cccs" in lowered or "ccvs" in lowered:
        return None
    return _by_pin_names(comp, (("n+", "+", "out+", "p"),
                                ("n-", "-", "out-", "n"),
                                ("c+", "nc+", "in+", "cp"),
                                ("c-", "nc-", "in-", "cn")))


def _join(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _each(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _a_few(refs: Sequence[str], limit: int = 3) -> str:
    """Name a circuit by a few of its parts: 'V2, R7 and 2 more'."""
    refs = list(refs)
    if len(refs) > limit:
        return ", ".join(refs[:limit]) + f" and {len(refs) - limit} more"
    return _join(refs)


class _Islands:
    """Which nodes are joined to which, by the components between them."""

    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}

    def find(self, node: str) -> str:
        self._parent.setdefault(node, node)
        while self._parent[node] != node:
            self._parent[node] = self._parent[self._parent[node]]
            node = self._parent[node]
        return node

    def join(self, nodes: Sequence[str]) -> None:
        for other in nodes[1:]:
            self._parent[self.find(other)] = self.find(nodes[0])
        for node in nodes:
            self.find(node)


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------

def generate(graph: CircuitGraph, *, title: str = "") -> LcapyNetlist:
    """Return an Lcapy netlist for *graph*."""
    result = LcapyNetlist()
    result.nodes = _node_names(graph)
    result.has_ground = any(net.kind == NetKind.GROUND and net.pins
                            for net in graph.nets)
    used_names: set = set()
    live_nodes: set = set()
    islands = _Islands()
    anchor: Dict[str, str] = {}        # reference -> one node it sits on
    renamed: List[str] = []
    symbolic: List[str] = []
    body: List[str] = []

    def node(pin: PinConnection, comp: Component) -> str:
        if pin.net_id in result.nodes:
            return result.nodes[pin.net_id]
        return ("NC_" + _NAME_RE.sub("_", comp.ref)
                + "_" + _NAME_RE.sub("_", pin.number))

    def name_for(kind: str, comp: Component, unit: str = "") -> str:
        name = _lcapy_name(kind, comp.ref + unit, used_names)
        result.names[comp.ref] = result.names.get(comp.ref, ()) + (name,)
        if name != comp.ref and not unit:
            renamed.append(f"{comp.ref} is {name}")
        return name

    def emit(comp: Component, name: str, *parts, attributes: str = "",
             joins: Optional[Sequence[Sequence[int]]] = None) -> None:
        """Write one live line: the name, then pins and words in order.

        A pin becomes the node it sits on, and that node is remembered as
        one the circuit really uses.  *joins* says which of the line's
        nodes the component connects to one another, by position - all of
        them unless told otherwise, which is right for anything with two
        terminals and wrong for a transformer, whose windings are joined
        end to end but not to each other.
        """
        tokens = [name]
        nodes: List[str] = []
        for part in parts:
            if isinstance(part, PinConnection):
                part = node(part, comp)
                live_nodes.add(part)
                nodes.append(part)
            if part:
                tokens.append(part)
        for group in (joins if joins is not None else (range(len(nodes)),)):
            islands.join([nodes[i] for i in group])
        if nodes:
            anchor.setdefault(comp.ref, nodes[0])
        body.append(" ".join(tokens)
                    + (f"; {attributes}" if attributes else ""))

    def omit(comp: Component, why: str,
             template: Sequence[str] = ()) -> None:
        """Comment a part out rather than guess at it.

        The pin-to-node map is always written, and where Lcapy has a
        component that only needs something the schematic does not
        record, a ready-made line is written with the nodes filled in.
        """
        pin_map = "  ".join(f"{p.number}={node(p, comp)}"
                            for p in _pins_in_order(comp))
        body.append(f"# {comp.ref} ({comp.ctype.value}) {why}")
        body.append(f"#   pins: {pin_map}")
        body.extend(f"# {line}" for line in template)
        result.omitted.append(comp.ref)
        result.notes.append(
            f"{comp.ref} ({comp.ctype.value}) {why}; it is left out of "
            f"the Lcapy netlist as a comment.")

    def value_of(comp: Component, name: str, meaning: str,
                 param: str = "") -> str:
        """The component's value as a number, or nothing at all.

        Nothing is the point: a line with no value makes Lcapy carry the
        component as a symbol named after it.
        """
        number = _number(comp.value, comp.ctype)
        if number is None and param:
            number = _number(_sim_params(comp).get(param, ""), comp.ctype)
        if number is None:
            symbolic.append(comp.ref)
            result.symbols[name] = f"{meaning} of {comp.ref}"
            return ""
        return number

    def unrepresented(comp: Component, shown: Sequence[PinConnection]
                      ) -> List[str]:
        """Connected pins the Lcapy line has no place for, as phrases.

        A pin wired to nothing loses nothing by being left out, so only
        a pin that shares its net, or sits on a labelled one, is listed.
        """
        left = []
        for pin in _pins_in_order(comp):
            if pin in shown or pin.net_id not in result.nodes:
                continue
            net = graph.nets[pin.net_id]
            if net.kind == NetKind.ANONYMOUS and len(net.pins) < 2:
                continue
            label = f"pin {pin.number}"
            if pin.name and pin.name != "~":
                label += f" ({pin.name})"
            left.append(f"{label} on node {result.nodes[pin.net_id]}")
        return left

    def three_terminal(comp: Component, kind: str, wanted, keyword: str,
                       what: str) -> None:
        """A transistor: three nodes in Lcapy's order, then its polarity."""
        named = _by_pin_names(comp, wanted)
        chosen = named if named is not None else _pins_in_order(comp)[:3]
        emit(comp, name_for(kind, comp), *chosen, keyword)
        result.drawn_only.append(comp.ref)
        extra = unrepresented(comp, chosen)
        if extra:
            body.append(f"#   {comp.ref}: Lcapy's {what} has no place for "
                        f"{_join(extra)}")
            result.notes.append(
                f"{comp.ref}: Lcapy's {what} has three terminals, so "
                f"{_join(extra)} {_each(len(extra), 'is', 'are')} not in "
                f"the Lcapy netlist.")

    for comp in graph.sorted_components():
        pins = _pins_in_order(comp)
        ctype = comp.ctype

        if len(pins) < 2:
            omit(comp, "has fewer than two pins")
            continue

        if ctype == ComponentType.RESISTOR:
            name = name_for("R", comp)
            emit(comp, name, pins[0], pins[1],
                 value_of(comp, name, "resistance"))
        elif ctype == ComponentType.VARIABLE_RESISTOR or (
                ctype == ComponentType.POTENTIOMETER and len(pins) == 2):
            name = name_for("R", comp)
            emit(comp, name, pins[0], pins[1],
                 value_of(comp, name, "resistance"), attributes="variable")
        elif ctype == ComponentType.POTENTIOMETER:
            end_a, wiper, end_b = pins[0], pins[1], pins[2]
            named = _by_pin_names(comp, (("1",), ("w", "wiper"), ("3",)))
            if named is not None:
                end_a, wiper, end_b = named
            name = name_for("RV", comp)
            # Both of Lcapy's arguments default to the component's name,
            # which would make the resistance and the wiper position the
            # same symbol; so both are always written out.
            total = _number(comp.value, comp.ctype)
            if total is None:
                total = name
                symbolic.append(comp.ref)
                result.symbols[name] = f"total resistance of {comp.ref}"
            position = f"alpha_{name}"
            result.symbols[position] = (
                f"wiper position of {comp.ref}: the fraction of its track "
                f"between the wiper and pin {end_b.number}")
            emit(comp, name, end_a, end_b, wiper, total, position)
            result.notes.append(
                f"{comp.ref}: the schematic does not record where the "
                f"wiper sits, so the Lcapy netlist leaves it as the symbol "
                f"{position} (0 to 1: the fraction of the track between "
                f"the wiper and pin {end_b.number}).")
        elif ctype in (ComponentType.CAPACITOR,
                       ComponentType.CAPACITOR_POLARIZED):
            first, second = pins[0], pins[1]
            polar = ctype == ComponentType.CAPACITOR_POLARIZED
            if polar and _pin_named(pins, ("+", "p", "plus")) is second:
                first, second = second, first      # positive plate first
            name = name_for("C", comp)
            emit(comp, name, first, second,
                 value_of(comp, name, "capacitance"),
                 attributes="kind=polar" if polar else "")
        elif ctype == ComponentType.INDUCTOR:
            name = name_for("L", comp)
            emit(comp, name, pins[0], pins[1],
                 value_of(comp, name, "inductance"))
        elif ctype in (ComponentType.DIODE, ComponentType.LED,
                       ComponentType.ZENER, ComponentType.SCHOTTKY):
            anode, cathode = _diode_terminals(comp)
            kind = {ComponentType.LED: "kind=led",
                    ComponentType.ZENER: "kind=zener",
                    ComponentType.SCHOTTKY: "kind=schottky"}.get(ctype, "")
            emit(comp, name_for("D", comp), anode, cathode, attributes=kind)
            result.drawn_only.append(comp.ref)
        elif ctype in _VOLTAGE_SOURCES \
                or ctype == ComponentType.CURRENT_SOURCE:
            plus, minus = _source_terminals(comp)
            is_current = ctype == ComponentType.CURRENT_SOURCE
            name = name_for("I" if is_current else "V", comp)
            # A voltage source is written '+' first, as in SPICE.  A
            # current source is written the other way round: Lcapy drives
            # current out of the first node, and the current leaves this
            # source at its '-' terminal.
            first, second = (minus, plus) if is_current else (plus, minus)
            shape = "" if ctype == ComponentType.BATTERY else _waveform(comp)
            if shape == "SIN":
                amplitude, phase, omega = _sine_parts(comp)
                if amplitude is None:
                    symbolic.append(comp.ref)
                    result.symbols[name] = f"amplitude of {comp.ref}"
                # Lcapy's arguments are positional - amplitude, phase,
                # angular frequency - so a later one needs the earlier.
                args = ["ac"]
                if amplitude or phase or omega:
                    args.append(amplitude or name)
                if phase or omega:
                    args.append(phase or "0")
                if omega:
                    args.append(omega)
                else:
                    result.symbols["omega_0"] = (
                        "angular frequency of the AC sources")
                    result.notes.append(
                        f"{comp.ref}: the schematic gives no frequency, so "
                        f"in the Lcapy netlist it runs at Lcapy's default "
                        f"angular frequency, the symbol omega_0.")
                emit(comp, name, first, second, *args)
            elif shape:
                function = f"{name.lower()}(t)"
                result.symbols[function] = (
                    f"the {shape} waveform of {comp.ref}")
                emit(comp, name, first, second, "{%s}" % function)
                result.notes.append(
                    f"{comp.ref}: the schematic marks it as a {shape} "
                    f"source, a waveform Lcapy has no netlist form for; "
                    f"the Lcapy netlist writes it as the unspecified "
                    f"waveform {function}.")
            else:
                emit(comp, name, first, second, "dc",
                     value_of(comp, name,
                              "current" if is_current else "voltage",
                              param="dc"))
        elif ctype in (ComponentType.TRANSISTOR_NPN,
                       ComponentType.TRANSISTOR_PNP) and len(pins) >= 3:
            three_terminal(
                comp, "Q", (("c", "collector"), ("b", "base"),
                            ("e", "emitter")),
                "npn" if ctype == ComponentType.TRANSISTOR_NPN else "pnp",
                "transistor")
        elif ctype in (ComponentType.NMOS, ComponentType.PMOS) \
                and len(pins) >= 3:
            three_terminal(
                comp, "M", (("d", "drain"), ("g", "gate"), ("s", "source")),
                "nmos" if ctype == ComponentType.NMOS else "pmos", "MOSFET")
        elif ctype in (ComponentType.NJFET, ComponentType.PJFET) \
                and len(pins) >= 3:
            three_terminal(
                comp, "J", (("d", "drain"), ("g", "gate"), ("s", "source")),
                "njf" if ctype == ComponentType.NJFET else "pjf", "JFET")
        elif ctype == ComponentType.OPAMP:
            units = _opamp_units(comp)
            if not units:
                omit(comp, "has no recognisable +, - and output pins")
                continue
            shown: List[PinConnection] = []
            gains: List[str] = []
            for unit, plus, minus, out in units:
                letter = (chr(ord("A") + unit - 1)
                          if len(units) > 1 and 1 <= unit <= 26 else "")
                name = name_for("E", comp, letter)
                # 'out 0 opamp + -': the output is referred to ground,
                # and the gain is left off so that it stays a symbol.
                # The output is what the op amp ties to ground; its
                # inputs only sense, and join nothing.
                emit(comp, name, out, "0", "opamp", plus, minus,
                     joins=((0,), (1,), (2,)))
                islands.join([node(out, comp), "0"])
                live_nodes.add("0")
                result.symbols[name] = f"open-loop gain of {comp.ref}" + (
                    f", unit {letter}" if letter else "")
                shown.extend((plus, minus, out))
                gains.append(name)
            note = (f"{comp.ref}: written for Lcapy as "
                    + _each(len(units), "an ideal op amp",
                            f"{len(units)} ideal op amps")
                    + f" with symbolic open-loop gain {_join(gains)}, since "
                    f"the schematic gives none (substitute a number, or "
                    f"take the limit as it goes to infinity)")
            extra = unrepresented(comp, shown)
            if extra:
                body.append(f"#   {comp.ref}: outside Lcapy's op-amp "
                            f"model: {_join(extra)}")
                note += (f"; {_join(extra)} "
                         f"{_each(len(extra), 'is', 'are')} outside that "
                         f"model and not in the netlist")
            result.notes.append(note + ".")
        elif ctype == ComponentType.FUSE:
            emit(comp, name_for("W", comp), pins[0], pins[1])
            result.notes.append(
                f"{comp.ref}: an intact fuse is written in the Lcapy "
                f"netlist as a wire.")
        elif ctype in (ComponentType.SWITCH, ComponentType.PUSHBUTTON):
            if len(pins) != 2:
                omit(comp, f"has {len(pins)} pins, and the Lcapy switch "
                           f"written here joins two")
                continue
            emit(comp, name_for("SW", comp), pins[0], pins[1],
                 "push" if ctype == ComponentType.PUSHBUTTON else "no")
            result.switches.append(comp.ref)
        elif ctype in (ComponentType.CONTROLLED_VOLTAGE_SOURCE,
                       ComponentType.CONTROLLED_CURRENT_SOURCE):
            is_current = ctype == ComponentType.CONTROLLED_CURRENT_SOURCE
            kind = "G" if is_current else "E"
            control = _control_pins(comp)
            if control is None:
                plus, minus = _source_terminals(comp)
                first, second = (minus, plus) if is_current \
                    else (plus, minus)
                omit(comp, "is a controlled source, and the schematic does "
                           "not record what controls it",
                     [f"{_lcapy_name(kind, comp.ref)} {node(first, comp)} "
                      f"{node(second, comp)} CONTROL+ CONTROL- GAIN"])
                continue
            out_p, out_m, ctl_p, ctl_m = control
            name = name_for(kind, comp)
            # As with an independent one, a current source's two output
            # nodes go in the opposite order to SPICE's.
            first, second = (out_m, out_p) if is_current else (out_p, out_m)
            emit(comp, name, first, second, ctl_p, ctl_m,
                 value_of(comp, name, "gain", param="gain"),
                 joins=((0, 1), (2,), (3,)))
        elif ctype == ComponentType.TRANSFORMER and len(pins) == 4:
            named = _by_pin_names(comp, (("aa", "p1", "1"), ("ab", "p2", "2"),
                                         ("sa", "s1", "3"), ("sb", "s2", "4")))
            pa, pb, sa, sb = named if named is not None else pins
            pa, pb = _dotted_first(comp, pa, pb)
            sa, sb = _dotted_first(comp, sa, sb)
            name = name_for("TF", comp)
            # Secondary first, then primary.  With no turns given, Lcapy
            # names the ratio after the component.
            emit(comp, name, sa, sb, pa, pb, joins=((0, 1), (2, 3)))
            result.symbols[name] = (
                f"turns ratio of {comp.ref}, secondary over primary")
            result.notes.append(
                f"{comp.ref}: written for Lcapy as an ideal transformer; "
                f"the schematic gives no turns ratio, so it is the symbol "
                f"{name} (secondary turns over primary turns).")
        elif ctype == ComponentType.CRYSTAL:
            emit(comp, name_for("XT", comp), pins[0], pins[1])
            result.drawn_only.append(comp.ref)
        else:
            omit(comp, "has no Lcapy model")

    # ---- Circuits that never reach ground --------------------------------
    # A sheet of worked examples holds several circuits side by side.
    # Lcapy solves the whole netlist at once and stops if any part of it
    # has no path to node 0, so each separate circuit is checked here.
    grounded = islands.find("0") if "0" in live_nodes else None
    circuits: Dict[str, List[str]] = {}
    for ref in result.names:
        if ref in anchor:
            circuits.setdefault(islands.find(anchor[ref]), []).append(ref)
    floating = [refs for root, refs in circuits.items() if root != grounded]
    result.ungrounded = [tuple(refs) for refs in floating]

    # ---- What the caller should be told ---------------------------------
    if graph.components and not result.has_ground:
        result.notes.append(
            "The schematic has no ground, and Lcapy needs one (node 0) to "
            "report node voltages; add a GND symbol to the reference net"
            + (f" of each of its {len(circuits)} separate circuits."
               if len(circuits) > 1 else "."))
    elif floating:
        which = "; ".join(f"the one with {_a_few(refs)}" for refs in floating)
        result.notes.append(
            f"{len(floating)} of the {len(circuits)} separate circuits on "
            f"the sheet {_each(len(floating), 'has', 'have')} no ground "
            f"({which}); Lcapy can only solve a circuit that reaches node "
            f"0, so give {_each(len(floating), 'it', 'each')} a GND symbol.")
    if renamed:
        result.notes.append(
            "Lcapy reads a component's type from the first letters of its "
            f"name, so in the Lcapy netlist {_join(renamed)}.")
    if symbolic:
        refs = list(dict.fromkeys(symbolic))
        result.notes.append(
            f"{_join(refs)} {_each(len(refs), 'has', 'have')} no numeric "
            f"value in the schematic, so the Lcapy netlist leaves "
            f"{_each(len(refs), 'it as a symbol', 'each as a symbol')} "
            f"rather than inventing one.")
    if result.drawn_only:
        count = len(result.drawn_only)
        result.notes.append(
            f"Lcapy loads and draws {_join(result.drawn_only)} but has no "
            f"linear model for {_each(count, 'it', 'them')}, so it cannot "
            f"analyse a circuit that contains {_each(count, 'it', 'them')}.")
    if result.switches:
        count = len(result.switches)
        result.notes.append(
            f"{_join(result.switches)} "
            f"{_each(count, 'is a switch', 'are switches')}, which Lcapy "
            f"models as changing state at t = 0; call replace_switches(t) "
            f"or convert_IVP(t) on the circuit before analysing it.")

    # ---- Supply rails nothing drives --------------------------------------
    rails: List[str] = []
    taken = set(used_names)
    for net in graph.nets:
        is_rail, volts = _rail_voltage(net)
        rail = result.nodes.get(net.net_id, "")
        if not is_rail or rail not in live_nodes:
            continue
        if any(graph.components[ref].ctype in _VOLTAGE_SOURCES
               for ref, _pin in net.pins if ref in graph.components):
            continue
        result.undriven_rails[rail] = volts
        source = _lcapy_name(
            "V", rail.replace("+", "p").replace("-", "m"), taken)
        rails.append(f"# {source} {rail} 0 dc"
                     + (f" {volts}" if volts else ""))
    if rails:
        count = len(rails)
        result.notes.append(
            f"Nothing in the schematic drives the supply "
            f"{_each(count, 'rail', 'rails')} "
            f"{_join(list(result.undriven_rails))}, so Lcapy sees "
            f"{_each(count, 'that node', 'those nodes')} as undriven; a "
            f"ready-made source for {_each(count, 'it', 'each')} is in the "
            f"Lcapy netlist as a comment.")

    # ---- Assemble ---------------------------------------------------------
    header = title
    if not header and graph.document is not None \
            and graph.document.source_path:
        header = graph.document.source_path.replace(
            "\\", "/").rsplit("/", 1)[-1]
    header = header or "circuit"
    count = f"{len(graph.components)} components"
    if result.omitted:
        count += (f" ({len(result.omitted)} written as "
                  f"{_each(len(result.omitted), 'a comment', 'comments')})")

    lines = [
        f"# {header} - Lcapy netlist generated by SchemAccess",
        f"# {count}, {len(live_nodes)} nodes.",
        "# Ground is node 0; a numbered node is the node the description "
        "calls by",
        "# that number.",
        "",
    ]
    lines.extend(body)
    if rails:
        lines.append("")
        lines.append("# Supply rails.  Nothing in the schematic drives "
                     "these nodes; to analyse")
        lines.append("# the circuit, uncomment a source for each:")
        lines.extend(rails)
    lines.append("")
    lines.append("# To use: from lcapy import Circuit, then "
                 "cct = Circuit(<this file>).")
    lines.append("# cct[<node>].V is a node voltage and cct.<name>.i a "
                 "component's current.")
    lines.append("")
    result.text = "\n".join(lines)
    return result

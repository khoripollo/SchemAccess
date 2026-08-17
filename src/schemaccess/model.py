"""Shared data model for SchemAccess.

Two layers:

1. **Document layer** — a faithful, geometry-preserving representation of the
   ``.kicad_sch`` file (symbols, wires, junctions, labels...).  Produced by
   :mod:`schemaccess.kicad_parser`.  Coordinates are KiCad schematic
   coordinates: millimetres, **Y axis pointing down**.

2. **Circuit layer** — an electrical graph (components + nets) produced by
   :mod:`schemaccess.netbuilder` from the document layer.  Shared by the
   alt-text generator and the CircuiTikZ generator.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# Coordinates are snapped to this many decimal places (0.0001 mm) when used
# as dictionary keys, so float noise never splits a net.
COORD_DECIMALS = 4

Point = Tuple[float, float]


def snap(x: float, y: float) -> Point:
    """Round a coordinate pair for use as a connectivity key."""
    return (round(x + 0.0, COORD_DECIMALS), round(y + 0.0, COORD_DECIMALS))


# ---------------------------------------------------------------------------
# Document layer
# ---------------------------------------------------------------------------

@dataclass
class PinDef:
    """A pin definition inside a library symbol (lib coords, Y **up**)."""
    number: str
    name: str
    x: float
    y: float
    orientation: float  # degrees; direction the pin points toward the body
    length: float
    etype: str = "passive"   # electrical type: input/output/passive/power_in...
    unit: int = 1            # symbol unit the pin belongs to (0 = all units)
    hidden: bool = False     # KiCad '(hide yes)' on the pin


@dataclass
class LibSymbol:
    """An embedded library symbol definition from the ``lib_symbols`` block."""
    lib_id: str
    pins: List[PinDef] = field(default_factory=list)
    reference_prefix: str = "U"
    is_power: bool = False
    description: str = ""

    def pins_for_unit(self, unit: int) -> List[PinDef]:
        """Connectable pins of *unit*.

        Hidden ``no_connect`` pins (e.g. the unused pins 1/5/8 of an
        SOIC-8 op amp) are omitted: KiCad does not draw them and they
        carry no connectivity.  Hidden *power* pins are kept, because
        KiCad connects those implicitly by name.
        """
        return [p for p in self.pins
                if p.unit in (0, unit)
                and not (p.hidden and p.etype == "no_connect")]


@dataclass
class SymbolInstance:
    """A placed symbol in the schematic (schematic coords, Y **down**)."""
    uuid: str
    lib_id: str
    x: float
    y: float
    angle: float = 0.0
    mirror: str = ""          # '', 'x' (flip about X axis) or 'y'
    unit: int = 1
    reference: str = "?"
    value: str = ""
    footprint: str = ""
    properties: Dict[str, str] = field(default_factory=dict)
    dnp: bool = False
    on_sheet: str = ""        # sheet path name, '' for root

    def pin_position(self, pin: PinDef) -> Point:
        """Absolute schematic position of *pin*'s connection point.

        Library pin coordinates are Y-up; schematic coordinates are Y-down.
        The instance ``angle`` is a counter-clockwise rotation in library
        space.  Mirror ('x' or 'y') is applied after rotation, in schematic
        space, matching KiCad's transform composition.
        """
        a = math.radians(self.angle)
        rx = pin.x * math.cos(a) - pin.y * math.sin(a)
        ry = pin.x * math.sin(a) + pin.y * math.cos(a)
        ox, oy = rx, -ry          # library Y-up -> schematic Y-down
        if self.mirror == "x":
            oy = -oy
        elif self.mirror == "y":
            ox = -ox
        return snap(self.x + ox, self.y + oy)


@dataclass
class Wire:
    """A wire polyline segment (usually two points)."""
    points: List[Point]
    uuid: str = ""


@dataclass
class Junction:
    x: float
    y: float
    uuid: str = ""


class LabelKind(enum.Enum):
    LOCAL = "label"
    GLOBAL = "global_label"
    HIERARCHICAL = "hierarchical_label"


@dataclass
class Label:
    text: str
    x: float
    y: float
    kind: LabelKind = LabelKind.LOCAL
    on_sheet: str = ""


@dataclass
class NoConnect:
    x: float
    y: float


@dataclass
class SheetRef:
    """A hierarchical sheet reference placed on a schematic."""
    name: str
    filename: str
    x: float = 0.0
    y: float = 0.0
    pins: List[Tuple[str, Point]] = field(default_factory=list)  # (name, pos)


@dataclass
class SchematicDocument:
    """Everything extracted from one ``.kicad_sch`` file (plus flattened
    sub-sheets, when hierarchy is resolved)."""
    source_path: str = ""
    version: int = 0
    generator: str = ""
    lib_symbols: Dict[str, LibSymbol] = field(default_factory=dict)
    symbols: List[SymbolInstance] = field(default_factory=list)
    wires: List[Wire] = field(default_factory=list)
    junctions: List[Junction] = field(default_factory=list)
    labels: List[Label] = field(default_factory=list)
    no_connects: List[NoConnect] = field(default_factory=list)
    sheets: List[SheetRef] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def lib_symbol_for(self, inst: SymbolInstance) -> Optional[LibSymbol]:
        return self.lib_symbols.get(inst.lib_id)


# ---------------------------------------------------------------------------
# Circuit layer
# ---------------------------------------------------------------------------

class ComponentType(enum.Enum):
    RESISTOR = "resistor"
    CAPACITOR = "capacitor"
    CAPACITOR_POLARIZED = "polarized capacitor"
    INDUCTOR = "inductor"
    DIODE = "diode"
    LED = "LED"
    ZENER = "Zener diode"
    VOLTAGE_SOURCE = "voltage source"
    CURRENT_SOURCE = "current source"
    BATTERY = "battery"
    AC_SOURCE = "AC voltage source"
    TRANSISTOR_NPN = "NPN transistor"
    TRANSISTOR_PNP = "PNP transistor"
    NMOS = "N-channel MOSFET"
    PMOS = "P-channel MOSFET"
    OPAMP = "operational amplifier"
    SWITCH = "switch"
    PUSHBUTTON = "push button"
    FUSE = "fuse"
    POTENTIOMETER = "potentiometer"
    CRYSTAL = "crystal"
    TRANSFORMER = "transformer"
    AND_GATE = "AND gate"
    OR_GATE = "OR gate"
    NOT_GATE = "NOT gate (inverter)"
    NAND_GATE = "NAND gate"
    NOR_GATE = "NOR gate"
    XOR_GATE = "XOR gate"
    XNOR_GATE = "XNOR gate"
    BUFFER = "buffer"
    CONNECTOR = "connector"
    IC = "integrated circuit"
    POWER_FLAG = "power flag"
    UNKNOWN = "component"

    @property
    def is_two_terminal(self) -> bool:
        return self in _TWO_TERMINAL

    @property
    def is_source(self) -> bool:
        return self in (ComponentType.VOLTAGE_SOURCE, ComponentType.BATTERY,
                        ComponentType.AC_SOURCE, ComponentType.CURRENT_SOURCE)

    @property
    def is_gate(self) -> bool:
        return self in (ComponentType.AND_GATE, ComponentType.OR_GATE,
                        ComponentType.NOT_GATE, ComponentType.NAND_GATE,
                        ComponentType.NOR_GATE, ComponentType.XOR_GATE,
                        ComponentType.XNOR_GATE, ComponentType.BUFFER)


_TWO_TERMINAL = {
    ComponentType.RESISTOR, ComponentType.CAPACITOR,
    ComponentType.CAPACITOR_POLARIZED, ComponentType.INDUCTOR,
    ComponentType.DIODE, ComponentType.LED, ComponentType.ZENER,
    ComponentType.VOLTAGE_SOURCE, ComponentType.CURRENT_SOURCE,
    ComponentType.BATTERY, ComponentType.AC_SOURCE, ComponentType.SWITCH,
    ComponentType.PUSHBUTTON, ComponentType.FUSE, ComponentType.CRYSTAL,
}


class NetKind(enum.Enum):
    GROUND = "ground"
    POWER = "power"       # named supply rail, e.g. +5V, VCC
    NAMED = "named"       # user label
    ANONYMOUS = "anonymous"


@dataclass
class PinConnection:
    """One component pin and the net it landed on."""
    number: str
    name: str
    position: Point
    net_id: int = -1
    etype: str = "passive"


@dataclass
class Component:
    """An electrical component in the circuit graph."""
    ref: str                       # reference designator, e.g. "R1"
    ctype: ComponentType
    value: str
    lib_id: str
    position: Point
    angle: float = 0.0
    mirror: str = ""
    pins: Dict[str, PinConnection] = field(default_factory=dict)
    properties: Dict[str, str] = field(default_factory=dict)

    def net_ids(self) -> List[int]:
        seen: List[int] = []
        for pin in self.pins.values():
            if pin.net_id >= 0 and pin.net_id not in seen:
                seen.append(pin.net_id)
        return seen

    def pin_by_net(self, net_id: int) -> Optional[PinConnection]:
        for pin in self.pins.values():
            if pin.net_id == net_id:
                return pin
        return None


@dataclass
class Net:
    """An electrical net: a set of connected pins."""
    net_id: int
    name: str
    kind: NetKind = NetKind.ANONYMOUS
    pins: List[Tuple[str, str]] = field(default_factory=list)  # (ref, pin no.)
    points: List[Point] = field(default_factory=list)

    @property
    def is_ground(self) -> bool:
        return self.kind == NetKind.GROUND


@dataclass
class CircuitGraph:
    """The shared electrical representation of a schematic."""
    components: Dict[str, Component] = field(default_factory=dict)
    nets: List[Net] = field(default_factory=list)
    document: Optional[SchematicDocument] = None
    warnings: List[str] = field(default_factory=list)

    def net(self, net_id: int) -> Net:
        return self.nets[net_id]

    def components_on(self, net_id: int) -> List[Component]:
        out = []
        for ref, _pin in self.nets[net_id].pins:
            comp = self.components.get(ref)
            if comp is not None and comp not in out:
                out.append(comp)
        return out

    def ground_net(self) -> Optional[Net]:
        for net in self.nets:
            if net.kind == NetKind.GROUND:
                return net
        return None

    def sorted_components(self) -> List[Component]:
        """Components in deterministic reading order: sources first, then by
        reference prefix and number (R1 < R2 < R10)."""
        def key(comp: Component):
            prefix = "".join(ch for ch in comp.ref if not ch.isdigit())
            digits = "".join(ch for ch in comp.ref if ch.isdigit())
            num = int(digits) if digits else 0
            source_rank = 0 if comp.ctype.is_source else 1
            return (source_rank, prefix, num, comp.ref)
        return sorted(self.components.values(), key=key)


# ---------------------------------------------------------------------------
# Component classification
# ---------------------------------------------------------------------------

# lib_id name (after the colon), lower-cased, matched by prefix.
_LIB_NAME_MAP = [
    ("r_potentiometer", ComponentType.POTENTIOMETER),
    ("r_variable", ComponentType.POTENTIOMETER),
    ("r_pack", ComponentType.IC),
    ("r_", ComponentType.RESISTOR),
    ("r", ComponentType.RESISTOR),
    ("c_polarized", ComponentType.CAPACITOR_POLARIZED),
    ("cp", ComponentType.CAPACITOR_POLARIZED),
    ("c_", ComponentType.CAPACITOR),
    ("c", ComponentType.CAPACITOR),
    ("l_", ComponentType.INDUCTOR),
    ("l", ComponentType.INDUCTOR),
    ("led", ComponentType.LED),
    ("d_zener", ComponentType.ZENER),
    ("d_schottky", ComponentType.DIODE),
    ("d_", ComponentType.DIODE),
    ("d", ComponentType.DIODE),
    ("battery", ComponentType.BATTERY),
    ("vdc", ComponentType.VOLTAGE_SOURCE),
    ("vsource", ComponentType.VOLTAGE_SOURCE),
    ("vac", ComponentType.AC_SOURCE),
    ("vsin", ComponentType.AC_SOURCE),
    ("idc", ComponentType.CURRENT_SOURCE),
    ("isource", ComponentType.CURRENT_SOURCE),
    ("q_npn", ComponentType.TRANSISTOR_NPN),
    ("q_pnp", ComponentType.TRANSISTOR_PNP),
    ("q_nmos", ComponentType.NMOS),
    ("q_pmos", ComponentType.PMOS),
    ("bc547", ComponentType.TRANSISTOR_NPN),
    ("2n2222", ComponentType.TRANSISTOR_NPN),
    ("2n7002", ComponentType.NMOS),
    ("opamp", ComponentType.OPAMP),
    ("lm358", ComponentType.OPAMP),
    ("lm741", ComponentType.OPAMP),
    ("tl07", ComponentType.OPAMP),
    ("sw_push", ComponentType.PUSHBUTTON),
    ("sw_", ComponentType.SWITCH),
    ("sw", ComponentType.SWITCH),
    ("fuse", ComponentType.FUSE),
    ("crystal", ComponentType.CRYSTAL),
    ("transformer", ComponentType.TRANSFORMER),
    ("conn", ComponentType.CONNECTOR),
    ("and", ComponentType.AND_GATE),
    ("nand", ComponentType.NAND_GATE),
    ("nor", ComponentType.NOR_GATE),
    ("xnor", ComponentType.XNOR_GATE),
    ("xor", ComponentType.XOR_GATE),
    ("or", ComponentType.OR_GATE),
    ("not", ComponentType.NOT_GATE),
    ("inverter", ComponentType.NOT_GATE),
    ("buffer", ComponentType.BUFFER),
    ("74ls00", ComponentType.NAND_GATE),
    ("74ls02", ComponentType.NOR_GATE),
    ("74ls04", ComponentType.NOT_GATE),
    ("74ls08", ComponentType.AND_GATE),
    ("74ls32", ComponentType.OR_GATE),
    ("74ls86", ComponentType.XOR_GATE),
]

_PREFIX_MAP = {
    "R": ComponentType.RESISTOR,
    "C": ComponentType.CAPACITOR,
    "L": ComponentType.INDUCTOR,
    "D": ComponentType.DIODE,
    "V": ComponentType.VOLTAGE_SOURCE,
    "I": ComponentType.CURRENT_SOURCE,
    "BT": ComponentType.BATTERY,
    "Q": ComponentType.TRANSISTOR_NPN,
    "U": ComponentType.IC,
    "SW": ComponentType.SWITCH,
    "S": ComponentType.SWITCH,
    "F": ComponentType.FUSE,
    "J": ComponentType.CONNECTOR,
    "P": ComponentType.CONNECTOR,
    "Y": ComponentType.CRYSTAL,
    "T": ComponentType.TRANSFORMER,
    "RV": ComponentType.POTENTIOMETER,
}


# KiCad library (the part before the colon), lower-cased, matched by
# prefix.  Catches every part in a family regardless of part number, e.g.
# Amplifier_Operational:OP1177AR / :LM358 / :AD8629.
_LIB_CATEGORY_MAP = [
    ("amplifier_operational", ComponentType.OPAMP),
    ("amplifier_instrumentation", ComponentType.OPAMP),
    ("simulation_spice", None),   # handled by symbol name (VDC, OPAMP...)
]

# Pin-name signatures, used when neither the library nor the part name is
# recognised.  Works for custom and third-party symbols.
_OPAMP_PLUS_NAMES = {"+", "in+", "inp", "vin+", "ninv", "non-inverting"}
_OPAMP_MINUS_NAMES = {"-", "in-", "inn", "vin-", "inv", "inverting"}


def _looks_like_opamp(pin_names: Sequence[str]) -> bool:
    """True when the pin names show a differential-input amplifier."""
    names = {str(n).strip().lower() for n in pin_names}
    return bool(names & _OPAMP_PLUS_NAMES) and bool(names & _OPAMP_MINUS_NAMES)


def classify(lib_id: str, reference: str = "", value: str = "",
             pin_count: int = 0,
             pin_names: Sequence[str] = ()) -> ComponentType:
    """Best-effort classification of a symbol into a :class:`ComponentType`.

    Priority: library symbol name, then KiCad library category, then the
    pin-name signature (so unknown part numbers still resolve), then the
    reference-designator prefix.
    """
    name = lib_id.split(":", 1)[-1].lower() if lib_id else ""
    library = lib_id.split(":", 1)[0].lower() if ":" in lib_id else ""
    for prefix, ctype in _LIB_NAME_MAP:
        if name == prefix:
            return ctype
        # Prefixes ending in '_' (R_, C_...) and long prefixes (led, opamp,
        # nand...) also match as leading substrings: R_Small, LED_RGB, NAND2.
        if (prefix.endswith("_") or len(prefix) > 2) and name.startswith(prefix):
            return ctype

    for lib_prefix, ctype in _LIB_CATEGORY_MAP:
        if ctype is not None and library.startswith(lib_prefix):
            return ctype

    if _looks_like_opamp(pin_names):
        return ComponentType.OPAMP

    ref_prefix = "".join(ch for ch in reference if not ch.isdigit()).upper()
    if ref_prefix in _PREFIX_MAP:
        ctype = _PREFIX_MAP[ref_prefix]
        # A 'U' with 3 pins and 'opamp'-like value is probably an op-amp.
        if ctype == ComponentType.IC and "amp" in value.lower():
            return ComponentType.OPAMP
        return ctype
    return ComponentType.UNKNOWN

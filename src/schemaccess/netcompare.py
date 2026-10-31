"""Check a SchemAccess netlist against the one KiCad exports itself.

SchemAccess reads a ``.kicad_sch`` file with its own parser and builds its
own connectivity graph.  KiCad, given the same schematic, will write a
netlist from *its* internal model.  If the two agree, the parser and the
net builder read the drawing the way KiCad does; where they disagree, one
of them is wrong about the circuit.  That makes KiCad's export an
independent reference, and this module the comparison.

Two file formats are compared, each parsed from text so the comparison can
be driven from a test, from the CLI, or from a file on disk:

``.net``
    KiCad's S-expression netlist.  The authoritative one: it lists every
    component and every pin of every net, and KiCad always writes it in
    full.

``.cir``
    A SPICE deck.  Compared on a best-effort basis, because KiCad's SPICE
    writer gives up on any symbol it cannot map to an ngspice primitive
    and emits a bare ``<ref> __<ref>`` line instead of a device.  Those
    lines carry no nodes, so there is nothing to compare; they are
    reported as notes rather than counted against either side.

What is compared
----------------

*Names are not.*  KiCad calls a net ``Net-(R1-Pad1)`` or ``/VOUT`` and
SchemAccess calls the same net ``N1`` or ``VOUT``; a SPICE ground node is
``GND`` on one side and ``0`` on the other.  None of that is electrical.
So nets are compared by **the set of component pins they join**, and SPICE
node names are compared only through the mapping the device lines imply:
zip the two node lists of every device that appears on both sides, and the
topologies match exactly when that mapping is one-to-one.

What *is* compared is the circuit: which components exist, their values,
which pins share a net, which net is ground, and -- for a SPICE deck --
each device's node count, node order and value.

Everything the comparison finds is sorted into one of three buckets on a
:class:`Report`: ``differences`` (the circuits disagree; the report fails),
``notes`` (something could not be compared, and why) and ``checks`` (what
was verified and agreed).  A report never fails silently and never fails
without saying which pins or which device.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import (Dict, FrozenSet, Iterable, List, Optional, Sequence, Set,
                    Tuple)

from . import sexpr
from .power import is_ground_name

__all__ = ["KIND_KICAD", "KIND_SPICE", "NetlistFormatError",
           "Difference", "Report", "Part", "ParsedNet", "KicadNetlist",
           "SpiceDevice", "SpiceDeck", "values_agree", "is_placeholder",
           "parse_kicad_netlist", "parse_spice_deck", "kind_of_path",
           "netlist_source", "compare_kicad_netlists",
           "compare_spice_decks", "compare", "compare_files"]

#: A single pin of a single component: ``("R1", "2")``.
Pin = Tuple[str, str]
PinSet = FrozenSet[Pin]

#: Comparison kinds, matching the two file formats KiCad exports.
KIND_KICAD = "net"
KIND_SPICE = "cir"


class NetlistFormatError(ValueError):
    """Raised when a file does not hold the netlist it was taken for."""


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Difference:
    """One electrical disagreement between the two netlists."""
    #: Which check found it: components, values, pins, connectivity,
    #: ground, devices, topology.
    area: str
    message: str

    def __str__(self) -> str:
        return f"{self.area}: {self.message}"


@dataclass
class Report:
    """The result of one comparison: what agreed, what did not, what could
    not be compared."""
    subject: str = ""
    reference_label: str = "KiCad"
    generated_label: str = "SchemAccess"
    #: What was verified and agreed, one line per check.
    checks: List[str] = field(default_factory=list)
    #: Electrical disagreements.  Any one of these fails the report.
    differences: List[Difference] = field(default_factory=list)
    #: Things that could not be compared, each saying why.  Never fatal.
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the two netlists describe the same circuit."""
        return not self.differences

    @property
    def verdict(self) -> str:
        return "PASS" if self.ok else "FAIL"

    def add(self, area: str, message: str) -> None:
        self.differences.append(Difference(area, message))

    def format(self, *, indent: str = "  ") -> str:
        """Return the report as text, PASS/FAIL first."""
        head = f"{self.verdict}  {self.subject}" if self.subject \
            else self.verdict
        lines = [f"{head}  ({self.reference_label} reference vs "
                 f"{self.generated_label})"]
        if self.differences:
            lines.append(f"{indent}differences ({len(self.differences)}):")
            for diff in self.differences:
                lines.append(f"{indent * 2}{diff}")
        if self.notes:
            lines.append(f"{indent}not compared ({len(self.notes)}):")
            for note in self.notes:
                lines.append(f"{indent * 2}{note}")
        if self.checks:
            lines.append(f"{indent}checked:")
            for check in self.checks:
                lines.append(f"{indent * 2}{check}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format()


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

# Metric prefixes as a KiCad Value field writes them, which is also what
# SchemAccess writes into a SPICE deck: 'k' kilo, 'm' milli, 'M' and 'Meg'
# mega.  (Strict ngspice reads a lone 'M' as milli; KiCad users do not, and
# every value here came from a KiCad field.)  Values are compared as
# strings first, so a string both sides spell the same way never reaches
# this table.
_PREFIX_SCALE: Dict[str, float] = {
    "": 1.0, "t": 1e12, "g": 1e9, "k": 1e3, "m": 1e-3,
    "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
}

# Unit words that may trail a value, longest first so 'ohm' wins over 'H'.
_UNIT_WORDS: Tuple[str, ...] = (
    "ohms", "ohm", "farads", "farad", "henries", "henry", "volts", "volt",
    "amperes", "ampere", "amps", "amp", "seconds", "second", "hertz", "hz",
    "Ω", "Ω", "F", "H", "V", "A", "R", "s")

_NUMBER_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*)$")
# RKM / "R notation": 4k7 = 4.7k, 1R5 = 1.5 ohm.
_RKM_RE = re.compile(r"^(\d+)([A-Za-zµμΩ])(\d+)$")
_BLANK_VALUES = ("", "~", "?")
# A KiCad text variable the schematic never expanded, e.g. '${SIM.PARAMS}'.
_PLACEHOLDER_RE = re.compile(r"^\$\{[^}]*\}$")


def _scale_of(token: str) -> Optional[float]:
    """Multiplier for a metric-prefix token; ``1.0`` for none."""
    token = token.strip()
    if token == "":
        return 1.0
    if token.lower() == "meg":
        return 1e6
    if len(token) == 1:
        if token == "M":
            return 1e6
        return _PREFIX_SCALE.get(token.lower())
    return None


def _quantity(text: str) -> Optional[float]:
    """*text* as a number, or ``None`` when it is not a quantity.

    ``'10k'``, ``'22nF'``, ``'100 Ohm'``, ``'4k7'`` and ``'1Meg'`` all
    convert; ``'LED'``, ``'~'`` and ``'10 ∠ 90'`` do not.
    """
    raw = (text or "").strip().replace(",", ".")
    if not raw or not any(ch.isdigit() for ch in raw):
        return None

    rkm = _RKM_RE.match(raw)
    if rkm is not None:
        whole, letter, fraction = rkm.groups()
        number = float(f"{whole}.{fraction}")
        if letter in ("R", "r", "Ω", "Ω"):
            return number
        scale = _scale_of(letter)
        return None if scale is None else number * scale

    match = _NUMBER_RE.match(raw)
    if match is None:
        return None
    rest = match.group(2).strip()
    for word in _UNIT_WORDS:
        if rest.lower().endswith(word.lower()):
            rest = rest[:len(rest) - len(word)].strip()
            break
    scale = _scale_of(rest)
    return None if scale is None else float(match.group(1)) * scale


def _clean_value(value: str) -> str:
    """Collapse whitespace and treat KiCad's blank markers as empty."""
    text = " ".join((value or "").split())
    return "" if text in _BLANK_VALUES else text


def is_placeholder(value: str) -> bool:
    """True for a value that is only an unexpanded KiCad text variable.

    ``${SIM.PARAMS}`` is a reference to a variable, not a value: KiCad
    substitutes it on export and SchemAccess passes the field through, so
    the two sides read the same field and still disagree.
    """
    return _PLACEHOLDER_RE.match(_clean_value(value)) is not None


def _close(one: float, two: float) -> bool:
    """True when two quantities differ only by floating-point rounding.

    The tolerance is relative, never absolute: a picofarad is a thousand
    times smaller than any absolute floor worth writing, so an absolute
    one would quietly call 22 pF and 23 pF the same value.
    """
    if one == two:
        return True
    scale = max(abs(one), abs(two))
    return scale > 0.0 and abs(one - two) <= 1e-9 * scale


def values_agree(left: str, right: str) -> bool:
    """True when two value strings mean the same thing.

    Spelling is tried first, so a part number like ``1N914`` compares as
    itself rather than as a number; only strings that differ are read as
    quantities, which is where ``'22nF'`` meets ``'22n'``.
    """
    first, second = _clean_value(left), _clean_value(right)
    if first.casefold() == second.casefold():
        return True
    one, two = _quantity(first), _quantity(second)
    if one is None or two is None:
        return False
    return _close(one, two)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _ref_key(ref: str) -> Tuple[str, int, str]:
    """Sort R2 before R10, the way a schematic is read."""
    prefix = "".join(ch for ch in ref if not ch.isdigit())
    digits = "".join(ch for ch in ref if ch.isdigit())
    return (prefix, int(digits) if digits else 0, ref)


def _pin_key(pin: Pin) -> Tuple[str, int, str, str]:
    ref, number = pin
    prefix, index, _ = _ref_key(ref)
    return (prefix, index, number.rjust(8), ref)


def _pins_text(pins: Iterable[Pin]) -> str:
    """``{('R1','2'), ('R2','1')}`` -> ``'R1 pin 2, R2 pin 1'``."""
    ordered = sorted(pins, key=_pin_key)
    if not ordered:
        return "nothing"
    return ", ".join(f"{ref} pin {number}" for ref, number in ordered)


def _listing(names: Iterable[str], limit: int = 12) -> str:
    ordered = sorted(names, key=_ref_key)
    if len(ordered) > limit:
        shown = ordered[:limit]
        return ", ".join(shown) + f", ... (+{len(ordered) - limit} more)"
    return ", ".join(ordered)


def _is_power_ref(ref: str) -> bool:
    """True for a power-symbol reference.

    KiCad names power flags ``#PWR01`` and leaves them out of modern
    netlist exports; older versions list them.  SchemAccess never treats
    one as a component, so dropping them puts both sides on the same
    footing.
    """
    return ref.startswith("#")


# ---------------------------------------------------------------------------
# KiCad netlist (.net)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Part:
    """One component as a KiCad-format netlist records it."""
    ref: str
    value: str = ""
    lib: str = ""
    part: str = ""


@dataclass(frozen=True)
class ParsedNet:
    """One net, by name and by the component pins it joins."""
    name: str
    pins: PinSet


@dataclass
class KicadNetlist:
    """A parsed ``.net`` file."""
    parts: Dict[str, Part] = field(default_factory=dict)
    nets: List[ParsedNet] = field(default_factory=list)

    @property
    def pins(self) -> Set[Pin]:
        """Every pin the netlist places on a net."""
        return {pin for net in self.nets for pin in net.pins}

    def solitary_pins(self) -> Set[Pin]:
        """Pins that are the only pin on their net.

        Such a pin connects to nothing, so whether a tool bothers to list
        it changes no connectivity.  KiCad writes one net per dangling
        pin, named ``unconnected-(U1-NC-Pad1)``; SchemAccess leaves a
        hidden no-connect pin out entirely.
        """
        return {pin for net in self.nets if len(net.pins) == 1
                for pin in net.pins}

    def ground_pins(self) -> PinSet:
        """Pins on a net whose name marks it as ground."""
        return frozenset(pin for net in self.nets
                         if is_ground_name(net.name.lstrip("/"))
                         for pin in net.pins)


def _atom(node: Optional[list], name: str) -> str:
    """First atom of ``(name <atom> ...)`` inside *node*, or ``''``."""
    if node is None:
        return ""
    child = sexpr.child(node, name)
    if child is None or len(child) < 2 or isinstance(child[1], list):
        return ""
    return str(child[1])


def parse_kicad_netlist(text: str) -> KicadNetlist:
    """Parse a KiCad S-expression netlist.

    Power-symbol references are dropped, and so are nets left with no
    pins once they are gone: they are drawing annotations, not parts.
    """
    try:
        expr = sexpr.loads(text)
    except sexpr.SExprError as exc:
        raise NetlistFormatError(f"not a readable S-expression: {exc}") \
            from exc
    if sexpr.tag(expr) != "export":
        raise NetlistFormatError(
            "not a KiCad netlist: the file does not start with '(export'")

    result = KicadNetlist()
    components = sexpr.child(expr, "components")
    for comp in sexpr.children(components or [], "comp"):
        ref = _atom(comp, "ref")
        if not ref or _is_power_ref(ref):
            continue
        libsource = sexpr.child(comp, "libsource")
        result.parts[ref] = Part(ref, _atom(comp, "value"),
                                 _atom(libsource, "lib"),
                                 _atom(libsource, "part"))

    nets = sexpr.child(expr, "nets")
    for net in sexpr.children(nets or [], "net"):
        pins = {(ref, number)
                for ref, number in ((_atom(node, "ref"), _atom(node, "pin"))
                                    for node in sexpr.children(net, "node"))
                if ref and number and not _is_power_ref(ref)}
        if pins:
            result.nets.append(ParsedNet(_atom(net, "name"), frozenset(pins)))
    return result


# ---------------------------------------------------------------------------
# SPICE deck (.cir)
# ---------------------------------------------------------------------------

# How many tokens after the device name are nodes.  'X' is absent because a
# subcircuit call takes any number of nodes followed by the subcircuit name.
_SPICE_ARITY: Dict[str, int] = {
    "R": 2, "C": 2, "L": 2, "D": 2, "V": 2, "I": 2, "B": 2,
    "F": 2, "H": 2, "W": 2,
    "Q": 3, "J": 3, "Z": 3,
    "M": 4, "E": 4, "G": 4, "S": 4, "T": 4,
    "K": 0,
}

# "* SW1 (switch) needs a control node SPICE cannot infer" -- how
# SchemAccess records a component it refused to guess at.  The reference
# is what matters: it proves the omission was deliberate and reported.
_COMMENTED_REF_RE = re.compile(r"^\*\s*([A-Za-z][A-Za-z0-9_]*)\s*\(")
_MODEL_RE = re.compile(r"^\.model\s+(\S+)\s*(.*)$", re.IGNORECASE)

# KiCad has no name for a net that carries no label, no power symbol and
# no named pin, so its SPICE writer calls it '_NO_NET_' -- every one of
# them, which makes the token a placeholder rather than a node name: two
# unrelated nets share it, and no node mapping can be built through it.
# Its netlist writer drops those nets instead, which is why a pin can be
# missing from a '.net' export altogether.
_NO_NET = "_NO_NET_"


def _is_unnamed_node(node: str) -> bool:
    """True for KiCad's placeholder for a net it could not name."""
    return node.rsplit("/", 1)[-1].upper() == _NO_NET


@dataclass(frozen=True)
class SpiceDevice:
    """One device line, split into its nodes and the rest."""
    name: str
    nodes: Tuple[str, ...]
    args: Tuple[str, ...]

    @property
    def letter(self) -> str:
        return self.name[:1].upper()


@dataclass
class SpiceDeck:
    """A parsed SPICE deck."""
    devices: Dict[str, SpiceDevice] = field(default_factory=dict)
    #: Model name -> body, from ``.model`` lines.
    models: Dict[str, str] = field(default_factory=dict)
    #: Names the deck mentions on a line that is not a usable device,
    #: mapped to why it is not.  KiCad writes ``D1 __D1`` for every symbol
    #: it cannot turn into an ngspice primitive.
    incomplete: Dict[str, str] = field(default_factory=dict)
    #: References named in a comment, i.e. deliberately left out.
    commented: Set[str] = field(default_factory=set)

    def entry(self, name: str) -> Optional[SpiceDevice]:
        return self.devices.get(name)


def parse_spice_deck(text: str) -> SpiceDeck:
    """Parse a SPICE deck into devices, models and leftovers.

    Comments and dot-commands are not devices, and the body of a
    ``.subckt`` block is library code rather than part of the schematic,
    so it is skipped.
    """
    deck = SpiceDeck()
    inside_subckt = False
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("*"):
            match = _COMMENTED_REF_RE.match(stripped)
            if match is not None:
                deck.commented.add(match.group(1))
            continue
        lowered = stripped.lower()
        if lowered.startswith(".subckt"):
            inside_subckt = True
            continue
        if lowered.startswith(".ends"):
            inside_subckt = False
            continue
        if inside_subckt:
            continue
        if stripped.startswith("."):
            model = _MODEL_RE.match(stripped)
            if model is not None:
                deck.models[model.group(1)] = model.group(2).strip()
            continue
        if stripped.startswith("+"):          # continuation of the last line
            continue

        tokens = stripped.split()
        name, rest = tokens[0], tokens[1:]
        letter = name[:1].upper()
        if letter == "X":
            # X<name> node... subckt -- at least one node and the name.
            if len(rest) < 2:
                deck.incomplete[name] = (
                    "a subcircuit call needs at least one node and a "
                    "subcircuit name")
                continue
            deck.devices[name] = SpiceDevice(name, tuple(rest[:-1]),
                                             tuple(rest[-1:]))
            continue
        arity = _SPICE_ARITY.get(letter)
        if arity is None:
            deck.incomplete[name] = (
                f"'{letter}' is not a SPICE device letter")
            continue
        if len(rest) < arity:
            deck.incomplete[name] = (
                f"only {len(rest)} token(s) follow the name, and a "
                f"'{letter}' device needs {arity} node(s)")
            continue
        deck.devices[name] = SpiceDevice(name, tuple(rest[:arity]),
                                         tuple(rest[arity:]))
    return deck


# ---------------------------------------------------------------------------
# Comparison: KiCad netlists
# ---------------------------------------------------------------------------

def _normalised_part(name: str) -> str:
    """A library part name with KiCad's alias suffix removed.

    A symbol stored under ``lib_name`` comes back from KiCad's export as
    ``L_Polarized_1`` where the schematic says ``L_Polarized``.  The
    trailing index is bookkeeping, not a different part.
    """
    return re.sub(r"_\d+$", "", (name or "").strip()).casefold()


def _blocks(nets: Sequence[ParsedNet], keep: Set[Pin]) -> Set[PinSet]:
    """The partition *nets* induces on *keep*, empty blocks dropped."""
    out: Set[PinSet] = set()
    for net in nets:
        block = frozenset(pin for pin in net.pins if pin in keep)
        if block:
            out.add(block)
    return out


def _block_key(block: PinSet) -> Tuple[str, int, str, str]:
    """Order nets by their first pin, so reports read schematic order."""
    return _pin_key(min(block, key=_pin_key))


def _describe_block(block: PinSet, others: Set[PinSet],
                    reference_label: str, generated_label: str) -> str:
    """Say how *block* fails to appear among *others*."""
    overlapping = sorted((other for other in others if other & block),
                         key=_block_key)
    if not overlapping:
        return (f"{reference_label} joins {_pins_text(block)}, which "
                f"{generated_label} does not join at all")
    if len(overlapping) > 1:
        split = "; ".join(_pins_text(other & block) for other in overlapping)
        return (f"{reference_label} joins {_pins_text(block)} as one net, "
                f"but {generated_label} splits them across {len(overlapping)} "
                f"nets: {split}")
    other = overlapping[0]
    clauses = []
    if block - other:
        clauses.append(f"leaves out {_pins_text(block - other)}")
    if other - block:
        clauses.append(f"also joins {_pins_text(other - block)}")
    return (f"{reference_label} joins {_pins_text(block)}; "
            f"{generated_label} " + " and ".join(clauses or ["differs"]))


def compare_kicad_netlists(reference: str, generated: str, *,
                           subject: str = "",
                           reference_label: str = "KiCad",
                           generated_label: str = "SchemAccess") -> Report:
    """Compare two KiCad-format netlists electrically.

    *reference* is KiCad's own export and *generated* is SchemAccess's.
    Net names are never compared; nets are matched by the component pins
    they join.
    """
    report = Report(subject=subject, reference_label=reference_label,
                    generated_label=generated_label)
    left = parse_kicad_netlist(reference)
    right = parse_kicad_netlist(generated)

    # ---- components ------------------------------------------------------
    missing = set(left.parts) - set(right.parts)
    extra = set(right.parts) - set(left.parts)
    if missing:
        report.add("components",
                   f"{reference_label} has {len(missing)} component(s) "
                   f"{generated_label} does not: {_listing(missing)}")
    if extra:
        report.add("components",
                   f"{generated_label} has {len(extra)} component(s) "
                   f"{reference_label} does not: {_listing(extra)}")

    shared = sorted(set(left.parts) & set(right.parts), key=_ref_key)
    agreed = 0
    for ref in shared:
        one, two = left.parts[ref], right.parts[ref]
        agreed += 1
        if is_placeholder(one.value) or is_placeholder(two.value):
            report.notes.append(
                f"{ref}: its value is an unexpanded KiCad text variable, "
                f"'{_clean_value(one.value) or '(blank)'}' in "
                f"{reference_label} and "
                f"'{_clean_value(two.value) or '(blank)'}' in "
                f"{generated_label}, so there is no value to compare")
        elif not values_agree(one.value, two.value):
            agreed -= 1
            report.add("values",
                       f"{ref} is {_clean_value(one.value) or '(blank)'} in "
                       f"{reference_label} but "
                       f"{_clean_value(two.value) or '(blank)'} in "
                       f"{generated_label}")
        # An empty library name is KiCad recording an alias rather than a
        # different part, so only compare the part itself.
        if _normalised_part(one.part) != _normalised_part(two.part):
            report.notes.append(
                f"{ref} is library part '{one.part}' in {reference_label} "
                f"and '{two.part}' in {generated_label}")
    if agreed:
        report.checks.append(f"{agreed} component(s) with matching "
                             f"references and values")

    # ---- pins ------------------------------------------------------------
    # A pin the generated netlist has and the reference does not cannot be
    # checked: KiCad's netlist writer leaves out no-connect pins and every
    # net it could not name, so its silence is not a claim that the pin is
    # unconnected.  The other way round it is a claim, and a pin sharing a
    # net in the reference but missing here is wiring that was lost --
    # unless the pin was alone on its net, where it joined nothing anyway.
    left_pins, right_pins = left.pins, right.pins
    lost = left_pins - right_pins
    dangling = lost & left.solitary_pins()
    if lost - dangling:
        report.add("pins",
                   f"{reference_label} puts {len(lost - dangling)} pin(s) on "
                   f"a shared net that {generated_label} does not mention: "
                   f"{_pins_text(lost - dangling)}")
    if dangling:
        report.notes.append(
            f"{reference_label} lists {len(dangling)} pin(s) alone on a net "
            f"of their own that {generated_label} leaves out: "
            f"{_pins_text(dangling)}; a pin alone on a net connects to "
            f"nothing")
    gained = right_pins - left_pins
    if gained:
        report.notes.append(
            f"{generated_label} has {len(gained)} pin(s) on a net "
            f"{reference_label} does not mention at all: "
            f"{_pins_text(gained)}; KiCad's netlist leaves out no-connect "
            f"pins and any net it could not name, so these could not be "
            f"checked")

    # ---- connectivity ----------------------------------------------------
    # Only the pins both sides know about: a pin missing altogether is
    # already reported above, and counting it twice would bury the cause.
    common = left_pins & right_pins
    left_blocks = _blocks(left.nets, common)
    right_blocks = _blocks(right.nets, common)
    only_left = left_blocks - right_blocks
    only_right = right_blocks - left_blocks
    for block in sorted(only_left, key=_block_key):
        report.add("connectivity",
                   _describe_block(block, right_blocks, reference_label,
                                   generated_label))
    for block in sorted(only_right, key=_block_key):
        if any(other & block for other in only_left):
            continue       # already described from the reference's side
        report.add("connectivity",
                   _describe_block(block, left_blocks, generated_label,
                                   reference_label))
    if not only_left and not only_right:
        report.checks.append(f"{len(left_blocks)} net(s) joining the same "
                             f"pins on both sides")

    # ---- ground ----------------------------------------------------------
    # A netlist only says which net is ground through its name, so the
    # check needs the reference to have named one.  KiCad names the net
    # after the power symbol, and a symbol whose Value was emptied leaves
    # it anonymous -- which says nothing about where ground is, so there
    # is nothing to compare rather than a disagreement.
    left_ground, right_ground = left.ground_pins(), right.ground_pins()
    if not left_ground:
        report.notes.append(
            f"{reference_label} names no ground net, so ground was not "
            f"checked"
            + (f" ({generated_label} puts ground on "
               f"{_pins_text(right_ground)})" if right_ground else ""))
    elif left_ground != right_ground:
        report.add("ground",
                   f"ground reaches {_pins_text(left_ground)} in "
                   f"{reference_label} but {_pins_text(right_ground)} in "
                   f"{generated_label}")
    else:
        report.checks.append(f"ground on {_pins_text(left_ground)}")
    return report


# ---------------------------------------------------------------------------
# Comparison: SPICE decks
# ---------------------------------------------------------------------------

def _match_devices(
        left: SpiceDeck,
        right: SpiceDeck) -> List[Tuple[Optional[str], Optional[str]]]:
    """Pair reference device names with generated ones.

    SPICE picks a device's type from the first letter of its name, so a
    component SchemAccess cannot name directly gets one added: an op amp
    called ``U1`` becomes the subcircuit call ``XU1``.  Exact names are
    paired first so a genuine ``SW1`` is never mistaken for a prefixed
    ``W1``.
    """
    unpaired = set(right.devices)
    pairs: List[Tuple[Optional[str], Optional[str]]] = []
    names = sorted(set(left.devices) | set(left.incomplete), key=_ref_key)
    for name in names:
        if name in unpaired:
            unpaired.discard(name)
            pairs.append((name, name))
        else:
            pairs.append((name, None))
    for index, (name, partner) in enumerate(pairs):
        if partner is not None:
            continue
        prefixed = next((other for other in sorted(unpaired, key=_ref_key)
                         if other[1:] == name), None)
        if prefixed is not None:
            unpaired.discard(prefixed)
            pairs[index] = (name, prefixed)
    for name in sorted(unpaired, key=_ref_key):
        pairs.append((None, name))      # generated only
    return pairs


def _compare_args(report: Report, name: str, device: SpiceDevice,
                  other: SpiceDevice, reference_label: str,
                  generated_label: str) -> bool:
    """Compare what follows two matching device lines' nodes.

    A device whose argument is a single magnitude -- R, C, L, and the
    value of a plain source -- is compared as a number, which is what
    holds SchemAccess's unit conversion (``22nF`` -> ``22n``) to KiCad's.
    Anything longer is a simulation statement, and a lone word is a model
    name; the two tools write both differently by design, so those are
    compared as text and a difference is a note.  Nothing is lost by
    that: the Value field each one started from is compared exactly by
    :func:`compare_kicad_netlists`.

    Returns True when the arguments were compared and agreed.
    """
    left_text, right_text = " ".join(device.args), " ".join(other.args)
    if left_text.casefold() == right_text.casefold():
        return True

    if len(device.args) != 1 or len(other.args) != 1:
        report.notes.append(
            f"{name}: {reference_label} writes '{left_text}' where "
            f"{generated_label} writes '{right_text}'; these are simulation "
            f"statements rather than a single value, and the two tools "
            f"spell them differently")
        return False

    one, two = _quantity(device.args[0]), _quantity(other.args[0])
    if one is not None and two is not None:
        if not _close(one, two):
            report.add("values",
                       f"{name} is {left_text} in {reference_label} but "
                       f"{right_text} in {generated_label}")
            return False
        return True
    if one is None and two is not None:
        report.notes.append(
            f"{name}: {reference_label} wrote '{left_text}', which is not a "
            f"number, where {generated_label} wrote '{right_text}'; the "
            f"schematic carries no usable value here, so each tool "
            f"supplied its own")
    elif one is not None:
        report.add("values",
                   f"{name} is {left_text} in {reference_label} but "
                   f"'{right_text}' in {generated_label}, which is not a "
                   f"number")
    else:
        report.notes.append(
            f"{name}: {reference_label} writes '{left_text}' where "
            f"{generated_label} writes '{right_text}'; neither is a number, "
            f"so this names a device model rather than a value")
    return False


def compare_spice_decks(reference: str, generated: str, *,
                        subject: str = "",
                        reference_label: str = "KiCad",
                        generated_label: str = "SchemAccess") -> Report:
    """Compare two SPICE decks electrically.

    Node names differ between the two tools, so the topology is checked
    by the mapping the shared device lines imply: every device present on
    both sides contributes ``reference node -> generated node`` for each
    of its positions, and the decks are wired the same way exactly when
    that mapping stays one-to-one.  Ground is checked by requiring
    KiCad's ground node to map to SPICE node ``0``.
    """
    report = Report(subject=subject, reference_label=reference_label,
                    generated_label=generated_label)
    left = parse_spice_deck(reference)
    right = parse_spice_deck(generated)

    forward: Dict[str, str] = {}
    backward: Dict[str, str] = {}
    compared = 0
    unnamed = 0

    for ref_name, gen_name in _match_devices(left, right):
        if ref_name is None:
            report.add("devices",
                       f"{generated_label} has a device {gen_name} that "
                       f"{reference_label} does not")
            continue
        why = left.incomplete.get(ref_name)
        if why is not None:
            report.notes.append(
                f"{ref_name}: {reference_label} wrote no usable device line "
                f"({why}), so there is nothing to compare")
            continue
        device = left.devices[ref_name]
        if gen_name is None:
            if ref_name in right.commented:
                report.notes.append(
                    f"{ref_name}: {generated_label} left it out on purpose "
                    f"and says so in a comment in the deck")
            else:
                report.add("devices",
                           f"{reference_label} has a device {ref_name} on "
                           f"nodes {', '.join(device.nodes)} that "
                           f"{generated_label} does not, and the deck does "
                           f"not say why")
            continue

        other = right.devices[gen_name]
        # Two subcircuit calls to different subcircuits are calls into two
        # different models, whose terminals need not correspond at all --
        # KiCad's Simulation_SPICE op amp takes its supply rails, the
        # ideal one SchemAccess writes does not.
        if device.letter == other.letter == "X" and device.args \
                and other.args \
                and device.args[-1].casefold() != other.args[-1].casefold():
            report.notes.append(
                f"{ref_name}: {reference_label} calls subcircuit "
                f"'{device.args[-1]}' on {len(device.nodes)} node(s) and "
                f"{generated_label} calls '{other.args[-1]}' on "
                f"{len(other.nodes)}; different models, so their node "
                f"lists are not comparable")
            continue
        if len(device.nodes) != len(other.nodes):
            report.add("devices",
                       f"{ref_name} has {len(device.nodes)} node(s) in "
                       f"{reference_label} but {len(other.nodes)} in "
                       f"{generated_label} (as {gen_name})")
            continue

        conflict = False
        for position, (ref_node, gen_node) in enumerate(
                zip(device.nodes, other.nodes), start=1):
            if _is_unnamed_node(ref_node):
                unnamed += 1
                continue
            seen = forward.get(ref_node)
            if seen is not None and seen != gen_node:
                report.add("topology",
                           f"{ref_name} node {position}: {reference_label}'s "
                           f"'{ref_node}' maps to '{seen}' elsewhere in "
                           f"{generated_label} but to '{gen_node}' here, so "
                           f"the two decks are not wired the same way")
                conflict = True
                continue
            taken = backward.get(gen_node)
            if taken is not None and taken != ref_node:
                report.add("topology",
                           f"{ref_name} node {position}: {generated_label}'s "
                           f"'{gen_node}' stands for {reference_label}'s "
                           f"'{taken}' elsewhere but for '{ref_node}' here, "
                           f"so two separate nets have been merged")
                conflict = True
                continue
            forward[ref_node] = gen_node
            backward[gen_node] = ref_node
        if conflict:
            continue

        if _compare_args(report, ref_name, device, other, reference_label,
                         generated_label):
            compared += 1

    if compared:
        report.checks.append(f"{compared} device(s) with matching node "
                             f"counts, node order and values")
    if forward:
        report.checks.append(f"{len(forward)} node name(s) mapped one to one")
    if unnamed:
        report.notes.append(
            f"{unnamed} device terminal(s) sit on a net {reference_label} "
            f"could not name, where it writes '{_NO_NET}' for every such "
            f"net; those positions could not be matched")

    # ---- ground ----------------------------------------------------------
    grounds = sorted(node for node in forward
                     if node == "0" or is_ground_name(node.lstrip("/")))
    for node in grounds:
        if forward[node] != "0":
            report.add("ground",
                       f"{reference_label}'s ground node '{node}' became "
                       f"'{forward[node]}' in {generated_label}, not SPICE "
                       f"node 0")
    if grounds and not any(d.area == "ground" for d in report.differences):
        report.checks.append(
            f"ground node {', '.join(repr(g) for g in grounds)} written as "
            f"SPICE node 0")
    if not grounds:
        report.notes.append(
            f"{reference_label} named no ground node, so ground was not "
            f"checked")
    return report


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

_KINDS: Dict[str, str] = {
    ".net": KIND_KICAD,
    ".cir": KIND_SPICE,
    ".sp": KIND_SPICE,
    ".spice": KIND_SPICE,
    ".ckt": KIND_SPICE,
}


def kind_of_path(path: str) -> Optional[str]:
    """Which comparison a filename asks for, by extension."""
    return _KINDS.get(os.path.splitext(path)[1].lower())


def netlist_source(text: str) -> str:
    """The schematic a KiCad netlist says it came from, by file name.

    Lets a caller notice that the reference was exported from a different
    schematic -- comparing last week's export against today's conversion
    produces differences that say nothing about either tool.
    """
    try:
        expr = sexpr.loads(text)
    except sexpr.SExprError:
        return ""
    source = _atom(sexpr.child(expr, "design"), "source")
    return source.replace("\\", "/").rsplit("/", 1)[-1]


def compare(kind: str, reference: str, generated: str, **kwargs) -> Report:
    """Dispatch to the comparison named by *kind* (see :data:`KIND_KICAD`)."""
    if kind == KIND_KICAD:
        return compare_kicad_netlists(reference, generated, **kwargs)
    if kind == KIND_SPICE:
        return compare_spice_decks(reference, generated, **kwargs)
    raise ValueError(f"Unknown netlist comparison '{kind}'; expected "
                     f"'{KIND_KICAD}' or '{KIND_SPICE}'")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def compare_files(reference_path: str, generated_path: str, *,
                  kind: Optional[str] = None, **kwargs) -> Report:
    """Compare two netlist files, choosing the comparison by extension."""
    resolved = kind or kind_of_path(reference_path) \
        or kind_of_path(generated_path)
    if resolved is None:
        raise ValueError(
            f"Cannot tell what kind of netlist '{reference_path}' is; "
            f"expected a .net or .cir file")
    kwargs.setdefault("subject", os.path.basename(reference_path))
    return compare(resolved, _read(reference_path), _read(generated_path),
                   **kwargs)

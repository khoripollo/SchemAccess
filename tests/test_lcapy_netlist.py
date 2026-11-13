"""Lcapy netlist tests.

An Lcapy netlist is one somebody will *solve*, so, like the SPICE tests,
these check it electrically rather than textually - and where Lcapy is
installed they hand the result to Lcapy itself:

* every component line is re-read and its nodes compared against the
  circuit graph (LCAPY-1), and nothing disappears: a component is a line,
  or a comment plus a note (LCAPY-2);
* ground is node ``0`` and only ground is (LCAPY-3), and every other node
  carries the number the description gives it (LCAPY-4);
* every name reads, by Lcapy's own longest-prefix rule, as the type it
  was written to be (LCAPY-5);
* values are exact plain numbers, and a missing one is a symbol rather
  than an invented number (LCAPY-6, LCAPY-9);
* sources keep their polarity - which for a current source means the
  opposite node order to the SPICE deck (LCAPY-8);
* ``lcapy.Circuit`` loads every fixture's netlist and reads back the
  same components on the same nodes (LCAPY-20);
* Lcapy's *answers* for the DC resistive fixtures equal the ones worked
  out straight from the circuit graph by an independent nodal solver in
  exact arithmetic (LCAPY-22) - which is what proves the values, the
  wiring and the polarities all survived the trip together.

The tests that need Lcapy are skipped when it is not installed; the rest
run everywhere, because the exporter itself never imports Lcapy.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

from conftest import LCAPY_AVAILABLE, VALID_FIXTURES
from schemaccess import cli, lcapy_netlist, netlist
from schemaccess.lcapy_netlist import LcapyNetlist
from schemaccess.model import (CircuitGraph, Component, ComponentType, Net,
                               NetKind, PinConnection)
from schemaccess.netbuilder import node_names
from schemaccess.pipeline import PipelineOptions, run_pipeline

needs_lcapy = pytest.mark.skipif(not LCAPY_AVAILABLE,
                                 reason="Lcapy is not installed")

#: The user's own example circuit: an op amp driving a class-B output
#: stage from split rails.  It is not in the manifest, so it is named
#: here to keep it under test.
EXTRA_FIXTURES = ["class_b_amp.kicad_sch"]
ALL_FIXTURES = VALID_FIXTURES + [name for name in EXTRA_FIXTURES
                                 if name not in VALID_FIXTURES]

#: How many leading arguments of a line are nodes, by Lcapy type.
_NODE_COUNT = {"R": 2, "C": 2, "L": 2, "D": 2, "V": 2, "I": 2, "W": 2,
               "SW": 2, "XT": 2, "Q": 3, "M": 3, "J": 3, "RV": 3,
               "E": 4, "G": 4, "TF": 4}

#: The Lcapy type each kind of component is written as.
_EXPECTED_TYPE = {
    ComponentType.RESISTOR: "R", ComponentType.VARIABLE_RESISTOR: "R",
    ComponentType.CAPACITOR: "C", ComponentType.CAPACITOR_POLARIZED: "C",
    ComponentType.INDUCTOR: "L", ComponentType.DIODE: "D",
    ComponentType.LED: "D", ComponentType.ZENER: "D",
    ComponentType.SCHOTTKY: "D", ComponentType.VOLTAGE_SOURCE: "V",
    ComponentType.BATTERY: "V", ComponentType.AC_SOURCE: "V",
    ComponentType.CURRENT_SOURCE: "I", ComponentType.TRANSISTOR_NPN: "Q",
    ComponentType.TRANSISTOR_PNP: "Q", ComponentType.NMOS: "M",
    ComponentType.PMOS: "M", ComponentType.NJFET: "J",
    ComponentType.PJFET: "J", ComponentType.OPAMP: "E",
    ComponentType.FUSE: "W", ComponentType.SWITCH: "SW",
    ComponentType.PUSHBUTTON: "SW", ComponentType.TRANSFORMER: "TF",
    ComponentType.POTENTIOMETER: "RV", ComponentType.CRYSTAL: "XT",
    ComponentType.CONTROLLED_VOLTAGE_SOURCE: "E",
    ComponentType.CONTROLLED_CURRENT_SOURCE: "G",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lines(text: str) -> List[Tuple[str, List[str], str]]:
    """``(name, arguments, attributes)`` for every component line."""
    out = []
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        body, _sep, attributes = raw.partition(";")
        tokens = body.split()
        out.append((tokens[0], tokens[1:], attributes.strip()))
    return out


def _nodes_of(name: str, args: Sequence[str]) -> List[str]:
    """The node arguments of one line, whatever keyword sits among them."""
    kind = lcapy_netlist._parsed_type(name)
    if kind == "E" and len(args) > 2 and args[2] == "opamp":
        return [args[0], args[1], args[3], args[4]]
    return list(args[:_NODE_COUNT[kind]])


def _owner(result: LcapyNetlist) -> Dict[str, str]:
    """Lcapy component name -> the schematic reference it stands for."""
    return {name: ref for ref, names in result.names.items()
            for name in names}


def _pin_nodes(graph: CircuitGraph, result: LcapyNetlist,
               ref: str) -> set:
    """Every node the pins of *ref* sit on."""
    return {result.nodes[pin.net_id]
            for pin in graph.components[ref].pins.values()
            if pin.net_id in result.nodes}


def circuit(*parts, kinds: Optional[Dict[str, NetKind]] = None
            ) -> CircuitGraph:
    """A circuit graph built by hand.

    Each part is ``(ref, ctype, value, pins)`` or the same with a fifth
    item of extra :class:`Component` fields.  ``pins`` maps a pin number
    to the name of the net it is on, or to ``(net, pin name)``, or to
    ``(net, pin name, unit)``.  A net called ``GND`` is the ground; other
    kinds come from *kinds*.
    """
    graph = CircuitGraph()
    ids: Dict[str, int] = {}

    def net_id(name: str) -> int:
        if name not in ids:
            ids[name] = len(graph.nets)
            kind = NetKind.GROUND if name == "GND" else (
                (kinds or {}).get(name, NetKind.ANONYMOUS))
            graph.nets.append(Net(net_id=ids[name], name=name, kind=kind))
        return ids[name]

    for part in parts:
        ref, ctype, value, pins = part[:4]
        extra = part[4] if len(part) > 4 else {}
        comp = Component(ref=ref, ctype=ctype, value=value,
                         lib_id=extra.pop("lib_id", "Device:X"),
                         position=(0.0, 0.0), **extra)
        for number, where in pins.items():
            spec = (where,) if isinstance(where, str) else tuple(where)
            net = net_id(spec[0])
            comp.pins[number] = PinConnection(
                number=number, name=spec[1] if len(spec) > 1 else "~",
                position=spec[3] if len(spec) > 3 else (0.0, 0.0),
                net_id=net, unit=spec[2] if len(spec) > 2 else 0)
            graph.nets[net].pins.append((ref, number))
        graph.components[ref] = comp
    return graph


R, C, L = (ComponentType.RESISTOR, ComponentType.CAPACITOR,
           ComponentType.INDUCTOR)
V, I = ComponentType.VOLTAGE_SOURCE, ComponentType.CURRENT_SOURCE


# ---------------------------------------------------------------------------
# The netlist against the circuit graph
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_lcapy_nodes_match_the_graph(load, fixture):
    """LCAPY-1: every line is wired to the nets its component's pins are on.

    A two-terminal part must sit on exactly its two nodes; a part with
    more pins may use only nodes its pins really touch.  The one node a
    line may add is ``0``, the reference an ideal op amp's output is
    measured from.
    """
    graph = load(fixture)
    result = netlist.generate(graph, "lcapy")
    owner = _owner(result)

    for name, args, _attributes in _lines(result.text):
        assert name in owner, f"{fixture}: line '{name}' matches no component"
        ref = owner[name]
        nodes = _nodes_of(name, args)
        touched = _pin_nodes(graph, result, ref)
        stray = {n for n in nodes if not n.startswith("NC_")} - touched
        if graph.components[ref].ctype == ComponentType.OPAMP:
            stray.discard("0")
        assert not stray, (
            f"{fixture}: {name} is wired to {sorted(stray)}, which {ref} "
            f"does not touch")
        if len(graph.components[ref].pins) == 2:
            assert set(nodes) == touched, (
                f"{fixture}: {name} sits on {nodes}, but {ref}'s two pins "
                f"are on {sorted(touched)}")


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_lcapy_accounts_for_every_component(load, fixture):
    """LCAPY-2: nothing is dropped silently.

    A component becomes at least one line, or it is written as a comment
    *and* named in the notes *and* listed in ``omitted``.  There is no
    third outcome, and no component gets both.
    """
    graph = load(fixture)
    result = netlist.generate(graph, "lcapy")
    written = {name for name, _args, _attrs in _lines(result.text)}
    commented = {line.split()[1] for line in result.text.splitlines()
                 if line.startswith("# ") and len(line.split()) > 1
                 and line.split()[1] in graph.components}

    for ref in graph.components:
        names = result.names.get(ref, ())
        if names:
            assert set(names) <= written, f"{fixture}: {ref} has no line"
            assert ref not in result.omitted
        else:
            assert ref in result.omitted, (
                f"{fixture}: {ref} is unaccounted for")
            assert ref in commented, f"{fixture}: {ref} is not in a comment"
            assert any(note.startswith(f"{ref} ") for note in result.notes), (
                f"{fixture}: {ref} was left out without a note")
    assert written == set(_owner(result)), (
        f"{fixture}: the lines and the name map disagree")
    assert result.complete == (not result.omitted)


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_lcapy_ground_is_node_zero(load, fixture):
    """LCAPY-3: every ground net is node 0, and no other net is."""
    graph = load(fixture)
    result = netlist.generate(graph, "lcapy")
    for net in graph.nets:
        if net.kind == NetKind.GROUND:
            assert result.nodes[net.net_id] == "0"
        else:
            assert result.nodes[net.net_id] != "0", (
                f"{fixture}: net {net.name} was written as ground")

    grounded = any(net.kind == NetKind.GROUND and net.pins
                   for net in graph.nets)
    assert result.has_ground == grounded
    if graph.components and not grounded:
        assert any("no ground" in note for note in result.notes), (
            f"{fixture}: a circuit with no ground must say so")
        assert not result.analysable


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_lcapy_node_numbers_are_the_descriptions(load, fixture):
    """LCAPY-4: "node 2" in the alt text is node 2 in the netlist.

    That agreement is the point of the numbering: a reader goes from the
    description straight to ``cct[2].V``.  A labelled net keeps its label
    instead, and node names never repeat.
    """
    graph = load(fixture)
    result = netlist.generate(graph, "lcapy")
    spoken = node_names(graph)

    for net in graph.nets:
        said = spoken[net.net_id]
        name = result.nodes[net.net_id]
        if said == "ground":
            assert name == "0"
        elif said.startswith("node ") and "(" not in said:
            assert name == said[len("node "):], (
                f"{fixture}: the description's '{said}' is '{name}' here")
        elif net.kind in (NetKind.NAMED, NetKind.POWER):
            assert name == net.name or not net.name.isidentifier(), (
                f"{fixture}: label {net.name!r} became {name!r}")
    assert len(set(result.nodes.values()) - {"0"}) == sum(
        1 for net in graph.nets if net.kind != NetKind.GROUND), (
        f"{fixture}: two nets share a node name")


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_lcapy_names_read_as_their_type(load, fixture):
    """LCAPY-5: Lcapy will take each name for the type it was written as.

    Lcapy reads the type off the longest type name a name starts with, so
    this applies that same rule to every name the exporter chose - and
    checks no two components were given one name, since Lcapy would let
    the second quietly replace the first.
    """
    graph = load(fixture)
    result = netlist.generate(graph, "lcapy")
    seen = set()
    for ref, names in result.names.items():
        wanted = _EXPECTED_TYPE[graph.components[ref].ctype]
        for name in names:
            assert lcapy_netlist._parsed_type(name) == wanted, (
                f"{fixture}: {ref} was written as '{name}', which Lcapy "
                f"reads as a '{lcapy_netlist._parsed_type(name)}'")
            assert name not in seen, f"{fixture}: the name {name} is reused"
            seen.add(name)


# ---------------------------------------------------------------------------
# Names and numbers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,ref,expected", [
    ("R", "R1", "R1"),          # already reads as a resistor
    ("V", "BT1", "VBT1"),       # 'BT' is no Lcapy type at all
    ("E", "U1", "EU1"),         # 'U' would be a drawn-only chip
    ("R", "RV1", "RRV1"),       # 'RV1' alone would be a potentiometer
    ("R", "RL1", "RRL1"),       # ...and 'RL1' a reluctance
    ("C", "CPE1", "CCPE1"),     # a constant-phase element
    ("V", "VM1", "VVM1"),       # a voltmeter
    ("V", "M1", "V_M1"),        # 'VM1' again: the prefix alone is not enough
    ("W", "F1", "WF1"),         # a fuse written as a wire, not a CCCS
    ("M", "Q3", "MQ3"),         # KiCad calls MOSFETs Q too
    ("R", "r1", "Rr1"),         # lower-case 'r' is a mechanical damper
    ("R", "R1-A", "R1_A"),      # only letters, digits and underscores
    ("TF", "T1", "TFT1"),
])
def test_lcapy_name_reads_as_the_wanted_type(kind, ref, expected):
    """LCAPY-7: a reference is kept when it can be and fixed when not."""
    name = lcapy_netlist._lcapy_name(kind, ref)
    assert name == expected
    assert lcapy_netlist._parsed_type(name) == kind


def test_lcapy_names_never_collide():
    """Two references that clean up to one name must stay two names:
    Lcapy overwrites a component whose name it has already seen."""
    used: set = set()
    first = lcapy_netlist._lcapy_name("R", "R1-A", used)
    second = lcapy_netlist._lcapy_name("R", "R1_A", used)
    assert first != second
    assert {first, second} == used


@pytest.mark.parametrize("value,expected", [
    ("100", "100"),
    ("4.7k", "4700"),
    ("4k7", "4700"),
    ("100k", "100000"),
    ("2M2", "2200000"),
    ("1MEG", "1000000"),
    ("1G", "1e9"),
    ("1R5", "1.5"),
    ("10 mH", "0.01"),
    ("2mA", "0.002"),
    ("0.5m", "0.0005"),
    ("22nF", "2.2e-8"),
    ("0.1uf", "1e-7"),
    ("2.2uF", "2.2e-6"),
    ("470pF", "4.7e-10"),
    ("12,5k", "12500"),
    ("5V", "5"),
    ("-5", "-5"),
    ("0", "0"),
    ("R", None),
    ("", None),
    ("~", None),
    ("${SIM.PARAMS}", None),
    ("5V 1kHz", None),
])
def test_lcapy_value_conversion(value, expected):
    """LCAPY-6: a KiCad value becomes a plain number, or nothing.

    No SPICE suffix survives, because Lcapy reads ``22n`` with a rounding
    error and ``1Meg`` not at all; and no value that is not a number is
    turned into one.
    """
    assert lcapy_netlist._number(value, ComponentType.RESISTOR) == expected


@pytest.mark.parametrize("value", ["4.7k", "22nF", "470pF", "0.1uf", "1MEG",
                                   "2M2", "12,5k", "1R5"])
def test_lcapy_numbers_are_exact(value):
    """The digits written are the schematic's, not a binary float's."""
    written = lcapy_netlist._number(value, ComponentType.RESISTOR)
    magnitude = netlist._spice_value(value, ComponentType.RESISTOR)
    scale = {"T": 12, "G": 9, "Meg": 6, "k": 3, "m": -3, "u": -6,
             "n": -9, "p": -12, "f": -15}
    suffix = next((s for s in sorted(scale, key=len, reverse=True)
                   if magnitude.endswith(s)), "")
    exact = Fraction(magnitude[:len(magnitude) - len(suffix)]) \
        * Fraction(10) ** scale.get(suffix, 0)
    assert Fraction(written) == exact


def test_lcapy_missing_value_is_a_symbol_not_a_guess():
    """LCAPY-9: no value in, no value out.

    The SPICE writer has to invent 1k to produce a deck that runs.  An
    Lcapy line with no value is carried as a symbol of the same name,
    which is the honest answer - and the note says which parts it was.
    """
    graph = circuit(("V1", V, "5", {"1": "A", "2": "GND"}),
                    ("R1", R, "R", {"1": "A", "2": "B"}),
                    ("R2", R, "", {"1": "B", "2": "GND"}),
                    ("C1", C, "100n", {"1": "B", "2": "GND"}))
    result = netlist.generate(graph, "lcapy")
    lines = {name: args for name, args, _a in _lines(result.text)}
    assert lines["R1"] == ["1", "2"] and lines["R2"] == ["2", "0"]
    assert lines["C1"] == ["2", "0", "1e-7"]
    assert set(result.symbols) == {"R1", "R2"}
    assert any(note.startswith("R1 and R2 have no numeric value")
               for note in result.notes)


def test_lcapy_reads_a_value_kept_in_sim_params():
    """A KiCad simulation source can show ``${SIM.PARAMS}`` as its value
    and keep the number in ``Sim.Params``; that is still its value."""
    graph = circuit(
        ("V1", V, "${SIM.PARAMS}", {"1": "A", "2": "GND"},
         {"properties": {"Sim.Params": "dc=12"}}),
        ("R1", R, "1k", {"1": "A", "2": "GND"}))
    assert ("V1", ["1", "0", "dc", "12"], "") in _lines(
        netlist.generate(graph, "lcapy").text)


# ---------------------------------------------------------------------------
# Polarity
# ---------------------------------------------------------------------------

def test_lcapy_voltage_source_is_positive_first(load):
    """LCAPY-8a: a voltage source is written '+' first, as in SPICE."""
    graph = load("rc_divider.kicad_sch")
    result = netlist.generate(graph, "lcapy")
    plus, minus = netlist._source_terminals(graph.components["V1"])
    args = dict((n, a) for n, a, _ in _lines(result.text))["V1"]
    assert args[:2] == [result.nodes[plus.net_id], result.nodes[minus.net_id]]


def test_lcapy_current_source_is_the_reverse_of_spice(load):
    """LCAPY-8b: a current source's nodes are swapped against the deck.

    SPICE's ``I1 a b`` sends current through the source from a to b.
    Lcapy's sends it out of a into the circuit - the other direction - so
    the same source has to be written with its nodes the other way round.
    The fixture's own numbering is used so the two netlists can be laid
    side by side.
    """
    graph = load("loops_current_source.kicad_sch")
    deck = {tokens[0]: tokens for tokens in (
        line.split() for line in netlist.spice(graph).text.splitlines()
        if line and not line.startswith(("*", ".")))}
    result = netlist.generate(graph, "lcapy")
    args = dict((n, a) for n, a, _ in _lines(result.text))["I1"]

    plus, minus = netlist._source_terminals(graph.components["I1"])
    in_spice = [netlist._spice_node(graph.nets[pin.net_id], "?")
                for pin in (plus, minus)]
    assert deck["I1"][1:3] == in_spice                    # '+' then '-'
    assert args[:2] == [result.nodes[minus.net_id],       # '-' then '+'
                        result.nodes[plus.net_id]]


def test_lcapy_diode_is_anode_first(load):
    """LCAPY-8c: KiCad numbers a diode's cathode pin 1; Lcapy wants the
    anode first, and an LED keeps saying it is one."""
    graph = load("led_battery.kicad_sch")
    result = netlist.generate(graph, "lcapy")
    anode, cathode = netlist._diode_terminals(graph.components["D1"])
    name, args, attributes = next(
        line for line in _lines(result.text) if line[0] == "D1")
    assert args == [result.nodes[anode.net_id], result.nodes[cathode.net_id]]
    assert attributes == "kind=led"


def test_lcapy_polarized_capacitor_is_positive_plate_first():
    graph = circuit(
        ("C1", ComponentType.CAPACITOR_POLARIZED, "10u",
         {"1": ("GND", "-"), "2": ("A", "+")}),
        ("V1", V, "5", {"1": "A", "2": "GND"}))
    assert ("C1", ["1", "0", "1e-5"], "kind=polar") in _lines(
        netlist.generate(graph, "lcapy").text)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def test_lcapy_battery_becomes_a_source(load):
    """Lcapy can draw a BAT but does not treat it as a source, so a
    battery has to be written as the DC source it is."""
    result = netlist.generate(load("led_battery.kicad_sch"), "lcapy")
    assert result.names["BT1"] == ("VBT1",)
    assert ("VBT1", ["1", "2", "dc", "9"], "") in _lines(result.text)
    assert any("BT1 is VBT1" in note for note in result.notes)


@pytest.mark.parametrize("fixture,expected", [
    # '5V 1kHz': amplitude and frequency, the way people write one.
    ("rlc_series.kicad_sch", ["ac", "5", "0", "{2*pi*1000}"]),
    # '10∠90' with f=1k in Sim.Params: a phasor, and degrees become radians.
    ("mixed_symbols.kicad_sch", ["ac", "10", "{pi/2}", "{2*pi*1000}"]),
])
def test_lcapy_ac_source(load, fixture, expected):
    """An AC source carries its amplitude, phase and frequency."""
    result = netlist.generate(load(fixture), "lcapy")
    args = dict((n, a) for n, a, _ in _lines(result.text))["V1"]
    assert args[2:] == expected


def test_lcapy_ac_source_without_a_frequency_says_so():
    graph = circuit(("V1", ComponentType.AC_SOURCE, "3V",
                     {"1": "A", "2": "GND"}),
                    ("R1", R, "1k", {"1": "A", "2": "GND"}))
    result = netlist.generate(graph, "lcapy")
    assert ("V1", ["1", "0", "ac", "3"], "") in _lines(result.text)
    assert "omega_0" in result.symbols
    assert any("no frequency" in note for note in result.notes)


@pytest.mark.parametrize("degrees,expected", [
    ("0", "0"), ("90", "{pi/2}"), ("-90", "{-pi/2}"), ("180", "{pi}"),
    ("45", "{pi/4}"), ("120", "{2*pi/3}"), ("360", "{2*pi}"), ("x", None),
])
def test_lcapy_phase_is_an_exact_multiple_of_pi(degrees, expected):
    assert lcapy_netlist._radians(degrees) == expected


def test_lcapy_does_not_pass_a_pulse_off_as_dc():
    """A source the schematic marks as a pulse must not come out as DC.

    Lcapy has no pulse-train source, so the waveform is left unspecified
    and the note says why, rather than a wrong circuit being written
    without comment.
    """
    graph = circuit(
        ("V1", V, "5", {"1": "A", "2": "GND"},
         {"lib_id": "Simulation_SPICE:VPULSE",
          "properties": {"Sim.Type": "PULSE"}}),
        ("R1", R, "1k", {"1": "A", "2": "GND"}))
    result = netlist.generate(graph, "lcapy")
    assert ("V1", ["1", "0", "{v1(t)}"], "") in _lines(result.text)
    assert any("PULSE" in note for note in result.notes)


# ---------------------------------------------------------------------------
# Parts with more than two pins
# ---------------------------------------------------------------------------

def test_lcapy_opamp_is_ideal_with_a_symbolic_gain(load):
    """An op amp is 'out 0 opamp + -' with the gain left as a symbol."""
    graph = load("opamp_inverting.kicad_sch")
    result = netlist.generate(graph, "lcapy")
    name, args, _attributes = next(
        line for line in _lines(result.text) if line[0] == "EU1")
    assert args[1:3] == ["0", "opamp"] and len(args) == 5
    assert result.symbols["EU1"] == "open-loop gain of U1"
    assert result.analysable


def test_lcapy_reports_the_opamp_pins_it_cannot_carry(load):
    """Supply pins are outside Lcapy's op-amp model.  They are wired in
    the schematic, so leaving them out has to be said, with where they
    went - which here also shows the fixture has them on swapped rails."""
    result = netlist.generate(load("class_b_amp.kicad_sch"), "lcapy")
    note = next(n for n in result.notes if n.startswith("U1:"))
    assert "pin 3 (V+) on node -5V" in note
    assert "pin 4 (V-) on node +5V" in note
    assert "outside Lcapy's op-amp model" in result.text


def test_lcapy_dual_opamp_keeps_both_amplifiers():
    """One chip, two amplifiers, two lines.

    Taking the first '+', '-' and output of the whole component would
    write one amplifier and lose the other without a word.
    """
    graph = circuit(
        ("U1", ComponentType.OPAMP, "LM358", {
            "1": ("OUTA", "~", 1), "2": ("INA", "-", 1), "3": ("GND", "+", 1),
            "5": ("OUTA", "+", 2), "6": ("INB", "-", 2), "7": ("OUTB", "~", 2),
            "4": ("GND", "V-", 3), "8": ("VCC", "V+", 3)},
         {"lib_id": "Amplifier_Operational:LM358"}),
        ("R1", R, "1k", {"1": "INA", "2": "OUTA"}),
        ("R2", R, "1k", {"1": "INB", "2": "OUTB"}),
        kinds={"OUTA": NetKind.NAMED, "OUTB": NetKind.NAMED,
               "INA": NetKind.NAMED, "INB": NetKind.NAMED,
               "VCC": NetKind.POWER})
    # Pin 1 and pin 7 are the outputs; mark them so, as KiCad does.
    graph.components["U1"].pins["1"].etype = "output"
    graph.components["U1"].pins["7"].etype = "output"

    result = netlist.generate(graph, "lcapy")
    assert result.names["U1"] == ("EU1A", "EU1B")
    lines = {name: args for name, args, _a in _lines(result.text)}
    assert lines["EU1A"] == ["OUTA", "0", "opamp", "0", "INA"]
    assert lines["EU1B"] == ["OUTB", "0", "opamp", "OUTA", "INB"]
    assert any("2 ideal op amps" in note and "pin 8 (V+)" in note
               for note in result.notes)


def test_lcapy_transistors_stay_and_are_flagged(load):
    """LCAPY-11: a non-linear part is kept, in Lcapy's own pin order.

    Lcapy can load and draw a transistor but not analyse one, so the part
    stays in the netlist - the circuit is still the circuit - and the
    result says a linear analysis will not run.
    """
    graph = load("class_b_amp.kicad_sch")
    result = netlist.generate(graph, "lcapy")
    lines = {name: args for name, args, _a in _lines(result.text)}
    for ref, polarity in (("Q1", "npn"), ("Q2", "pnp")):
        pins = {pin.name: result.nodes[pin.net_id]
                for pin in graph.components[ref].pins.values()}
        assert lines[ref] == [pins["C"], pins["B"], pins["E"], polarity]
    assert result.drawn_only == ["Q1", "Q2"]
    assert not result.analysable and result.complete
    assert any("Q1 and Q2" in note and "cannot analyse" in note
               for note in result.notes)


def test_lcapy_mosfet_bulk_pin_is_reported():
    graph = circuit(
        ("M1", ComponentType.NMOS, "", {
            "1": ("D", "D"), "2": ("G", "G"), "3": ("GND", "S"),
            "4": ("BULK", "B")}),
        ("R1", R, "1k", {"1": "D", "2": "BULK"}),
        kinds={"D": NetKind.NAMED, "G": NetKind.NAMED,
               "BULK": NetKind.NAMED})
    result = netlist.generate(graph, "lcapy")
    assert ("M1", ["D", "G", "0", "nmos"], "") in _lines(result.text)
    assert any(note.startswith("M1:") and "pin 4 (B) on node BULK" in note
               for note in result.notes)


def test_lcapy_potentiometer_keeps_its_wiper_symbolic():
    """A pot's total is on the schematic; where its wiper sits is not.

    Lcapy's two arguments both default to the component's name, which
    would make the resistance and the position one symbol, so both are
    always written.
    """
    graph = circuit(("RV1", ComponentType.POTENTIOMETER, "10k",
                     {"1": "A", "2": "W", "3": "GND"}),
                    ("V1", V, "5", {"1": "A", "2": "GND"}),
                    ("R1", R, "1k", {"1": "W", "2": "GND"}))
    result = netlist.generate(graph, "lcapy")
    assert ("RV1", ["1", "0", "2", "10000", "alpha_RV1"], "") in _lines(
        result.text)
    assert "alpha_RV1" in result.symbols
    assert any(note.startswith("RV1:") and "wiper" in note
               for note in result.notes)


def test_lcapy_transformer_is_ideal_secondary_first(load):
    """Lcapy lists the secondary before the primary, and with no turns
    on the schematic the ratio is a symbol.

    TR1 is a real part (TEZ0.5-D-1) whose pins carry numbers and no
    names: 1 and 4 are its primary, 6 and 7 its secondary.
    """
    graph = load("mixed_symbols.kicad_sch")
    result = netlist.generate(graph, "lcapy")
    at = {pin.number: result.nodes[pin.net_id]
          for pin in graph.components["TR1"].pins.values()}
    args = dict((n, a) for n, a, _ in _lines(result.text))["TFTR1"]
    assert args == [at["6"], at["7"], at["1"], at["4"]]
    assert result.symbols["TFTR1"].startswith("turns ratio of TR1")
    assert any(note.startswith("TR1:") and "ideal transformer" in note
               for note in result.notes)


def test_lcapy_transformer_dot_sets_the_positive_node():
    """A winding's dotted end is its positive node, wherever its pin
    number puts it."""
    def transformer(dot_near_sb: bool) -> List[str]:
        graph = circuit(("T1", ComponentType.TRANSFORMER, "", {
            "1": ("PA", "AA", 0, (0.0, 0.0)),
            "2": ("PB", "AB", 0, (0.0, 10.0)),
            "3": ("SA", "SA", 0, (20.0, 0.0)),
            "4": ("SB", "SB", 0, (20.0, 10.0))}),
            kinds={n: NetKind.NAMED for n in ("PA", "PB", "SA", "SB")})
        graph.components["T1"].dots = [((1.0, 1.0), 0.3)] + (
            [((19.0, 9.0), 0.3)] if dot_near_sb else [((19.0, 1.0), 0.3)])
        return _lines(netlist.generate(graph, "lcapy").text)[0][1]

    assert transformer(dot_near_sb=False) == ["SA", "SB", "PA", "PB"]
    assert transformer(dot_near_sb=True) == ["SB", "SA", "PA", "PB"]


def test_lcapy_controlled_sources():
    """A controlled source with control pins is written in full - and a
    controlled *current* source, like an independent one, with its output
    nodes the other way round from SPICE.  One with no control pins
    cannot be, and says so."""
    kinds = {n: NetKind.NAMED for n in ("OP", "OM", "CP", "CM", "X")}
    four = {"1": ("OP", "N+"), "2": ("OM", "N-"),
            "3": ("CP", "C+"), "4": ("CM", "C-")}
    graph = circuit(
        ("E1", ComponentType.CONTROLLED_VOLTAGE_SOURCE, "2", dict(four),
         {"lib_id": "Simulation_SPICE:ESOURCE"}),
        ("G1", ComponentType.CONTROLLED_CURRENT_SOURCE, "1m", dict(four),
         {"lib_id": "Simulation_SPICE:GSOURCE"}),
        ("B1", ComponentType.CONTROLLED_VOLTAGE_SOURCE, "5",
         {"1": ("X", "+"), "2": ("GND", "-")},
         {"lib_id": "Simulation_SPICE:BSOURCE"}),
        kinds=kinds)
    result = netlist.generate(graph, "lcapy")
    lines = {name: args for name, args, _a in _lines(result.text)}
    assert lines["E1"] == ["OP", "OM", "CP", "CM", "2"]
    assert lines["G1"] == ["OM", "OP", "CP", "CM", "0.001"]
    assert result.omitted == ["B1"] and "B1" not in lines
    assert "# EB1 X 0 CONTROL+ CONTROL- GAIN" in result.text


def test_lcapy_everyday_two_terminal_parts():
    """A fuse is a wire, a switch a switch, a crystal a crystal."""
    graph = circuit(
        ("F1", ComponentType.FUSE, "1A", {"1": "A", "2": "B"}),
        ("SW1", ComponentType.SWITCH, "", {"1": "B", "2": "C"}),
        ("SW2", ComponentType.PUSHBUTTON, "", {"1": "C", "2": "D"}),
        ("Y1", ComponentType.CRYSTAL, "8MHz", {"1": "D", "2": "GND"}),
        ("D2", ComponentType.ZENER, "5V1", {"1": ("A", "K"),
                                            "2": ("GND", "A")}),
        ("R5", ComponentType.VARIABLE_RESISTOR, "1k", {"1": "A", "2": "D"}),
        ("V1", V, "5", {"1": "A", "2": "GND"}))
    result = netlist.generate(graph, "lcapy")
    lines = {name: (args, attrs) for name, args, attrs in _lines(result.text)}
    assert lines["WF1"] == (["1", "2"], "")
    assert lines["SW1"] == (["2", "3", "no"], "")
    assert lines["SW2"] == (["3", "4", "push"], "")
    assert lines["XTY1"] == (["4", "0"], "")
    assert lines["D2"] == (["0", "1"], "kind=zener")
    assert lines["R5"] == (["1", "4", "1000"], "variable")
    assert result.switches == ["SW1", "SW2"]
    assert set(result.drawn_only) == {"D2", "Y1"}
    assert any(note.startswith("F1:") and "wire" in note
               for note in result.notes)


def test_lcapy_part_with_no_model_becomes_a_comment(load):
    """A logic gate has no Lcapy counterpart: comment, pin map, note."""
    graph = load("logic_gates.kicad_sch")
    result = netlist.generate(graph, "lcapy")
    assert sorted(result.omitted) == sorted(graph.components)
    assert not _lines(result.text)
    for ref in graph.components:
        assert f"# {ref} (" in result.text
    assert "#   pins: " in result.text
    assert not result.complete


# ---------------------------------------------------------------------------
# Rails and nodes
# ---------------------------------------------------------------------------

def test_lcapy_undriven_rail_is_reported_with_a_ready_source(load):
    """LCAPY-12: a supply rail nothing drives is not driven quietly.

    The power symbol says a node is held at +5 V; nothing on the sheet
    holds it there.  Adding a source would be inventing a component, so
    one is offered as a comment and the note says why it matters.
    """
    result = netlist.generate(load("voltage_divider.kicad_sch"), "lcapy")
    assert result.undriven_rails == {"+5V": "5"}
    assert "# Vp5V +5V 0 dc 5" in result.text
    assert any("supply rail +5V" in note for note in result.notes)
    assert not any(name == "Vp5V" for name, _a, _b in _lines(result.text))


def test_lcapy_driven_rail_is_left_alone():
    graph = circuit(("V1", V, "5", {"1": "+5V", "2": "GND"}),
                    ("R1", R, "1k", {"1": "+5V", "2": "GND"}),
                    kinds={"+5V": NetKind.POWER})
    result = netlist.generate(graph, "lcapy")
    assert result.undriven_rails == {}
    assert "Supply rails" not in result.text


@pytest.mark.parametrize("name,kind,expected", [
    ("+5V", NetKind.POWER, (True, "5")),
    ("-12V", NetKind.POWER, (True, "-12")),
    ("+3V3", NetKind.POWER, (True, "3.3")),
    ("+3.3V", NetKind.NAMED, (True, "3.3")),
    ("VCC", NetKind.POWER, (True, None)),      # a rail, voltage unstated
    ("VCC", NetKind.NAMED, (False, None)),     # just a label
    ("VOUT", NetKind.NAMED, (False, None)),
    ("N3", NetKind.ANONYMOUS, (False, None)),
])
def test_lcapy_rail_voltage(name, kind, expected):
    net = Net(net_id=0, name=name, kind=kind)
    assert lcapy_netlist._rail_voltage(net) == expected


def test_lcapy_names_the_circuit_that_has_no_ground():
    """LCAPY-13: one ungrounded circuit must not go unreported.

    A page of worked examples holds several circuits.  Lcapy solves the
    netlist as a whole and stops if any part of it cannot reach node 0,
    so the result picks out the circuit that cannot - by its parts, since
    that is how a reader would find it on the page.
    """
    graph = circuit(("V1", V, "5", {"1": "A", "2": "GND"}),
                    ("R1", R, "1k", {"1": "A", "2": "GND"}),
                    ("V2", V, "9", {"1": "B", "2": "C"}),
                    ("R2", R, "1k", {"1": "B", "2": "D"}),
                    ("R3", R, "1k", {"1": "D", "2": "C"}))
    result = netlist.generate(graph, "lcapy")
    assert result.has_ground
    assert result.ungrounded == [("V2", "R2", "R3")]
    assert not result.analysable
    assert any(note.startswith("1 of the 2 separate circuits")
               and "the one with V2, R2 and R3" in note
               for note in result.notes)


def test_lcapy_one_grounded_circuit_is_analysable(load):
    result = netlist.generate(load("wheatstone.kicad_sch"), "lcapy")
    assert result.ungrounded == [] and result.analysable


def test_lcapy_transformer_does_not_ground_its_other_winding():
    """A transformer joins the two ends of each winding and nothing
    else, so a secondary circuit with no ground of its own is floating
    however well grounded the primary is."""
    pins = {"1": ("A", "AA"), "2": ("GND", "AB"),
            "3": ("S1", "SA"), "4": ("S2", "SB")}
    graph = circuit(("V1", V, "5", {"1": "A", "2": "GND"}),
                    ("T1", ComponentType.TRANSFORMER, "", pins),
                    ("R1", R, "1k", {"1": "S1", "2": "S2"}))
    result = netlist.generate(graph, "lcapy")
    assert [set(group) for group in result.ungrounded] == [{"T1", "R1"}]


def test_lcapy_node_names_are_safe_and_unique():
    """A label Lcapy could not read as one token is cleaned, a label that
    would pass for ground is not allowed to, and a number gives way to a
    label that got there first."""
    graph = circuit(
        ("R1", R, "1", {"1": "V out", "2": "0"}),
        ("R2", R, "1", {"1": "2", "2": "N9"}),
        ("R3", R, "1", {"1": "N9", "2": "U1.out"}),
        ("R4", R, "1", {"1": "U1.out", "2": "V out"}),
        ("R5", R, "1", {"1": "0", "2": "2"}),
        kinds={"V out": NetKind.NAMED, "0": NetKind.NAMED,
               "2": NetKind.NAMED, "U1.out": NetKind.NAMED})
    nodes = netlist.generate(graph, "lcapy").nodes
    names = {net.name: nodes[net.net_id] for net in graph.nets}
    assert names["V out"] == "V_out"
    assert names["U1.out"] == "U1_out"
    assert names["0"] != "0"                # only ground is node 0
    assert names["2"] == "2"                # the label keeps its name...
    assert names["N9"] != "2"               # ...and the number moves over
    assert len(set(names.values())) == len(names)


# ---------------------------------------------------------------------------
# The result as a whole
# ---------------------------------------------------------------------------

def test_lcapy_result_is_a_netlist_result_and_more(load):
    """The dispatcher returns the ordinary result type, with the maps a
    solver would need riding along."""
    graph = load("rc_divider.kicad_sch")
    result = netlist.generate(graph, "lcapy")
    assert isinstance(result, netlist.NetlistResult)
    assert isinstance(result, LcapyNetlist)
    assert str(result) == result.text
    assert result.names == {ref: (ref,) for ref in graph.components}
    assert set(result.nodes) == {net.net_id for net in graph.nets}
    assert result.analysable and result.complete
    assert result.notes == []


def test_lcapy_is_a_registered_format():
    assert "lcapy" in netlist.FORMATS
    assert netlist.EXTENSIONS["lcapy"] == "_lcapy.txt"
    assert "Lcapy" in netlist.FORMAT_NAMES["lcapy"]
    assert netlist.lcapy(CircuitGraph()).text.startswith("# circuit - Lcapy")


def test_lcapy_only_comments_and_components(load):
    """Every line is a comment, a blank, or a component Lcapy can parse:
    nothing of SPICE's dot-commands leaks in."""
    for fixture in ("rc_divider.kicad_sch", "mixed_symbols.kicad_sch"):
        for line in netlist.generate(load(fixture), "lcapy").text.splitlines():
            assert not line or line.startswith("#") or (
                lcapy_netlist._parsed_type(line.split()[0])), line
            assert not line.startswith((".", "*"))


def test_lcapy_type_table_is_longest_first():
    """The naming rule is only Lcapy's rule if a longer type wins."""
    assert lcapy_netlist._parsed_type("RV1") == "RV"
    assert lcapy_netlist._parsed_type("VM1") == "VM"
    assert lcapy_netlist._parsed_type("CCCS1") == "CCCS"
    assert lcapy_netlist._parsed_type("T1") == ""
    lengths = [len(n) for n in lcapy_netlist._TYPES_LONGEST_FIRST]
    assert lengths == sorted(lengths, reverse=True)


# ---------------------------------------------------------------------------
# Through the pipeline, the command line and the GUI
# ---------------------------------------------------------------------------

def test_lcapy_pipeline_writes_the_file(fixtures_dir, tmp_path):
    result = run_pipeline(PipelineOptions(
        input_path=str(fixtures_dir / "rc_divider.kicad_sch"),
        output_dir=str(tmp_path), netlist_formats=("lcapy",),
        generate_alt_text=False, generate_image=False))
    assert not result.errors
    path = tmp_path / "rc_divider_lcapy.txt"
    assert result.output_files["netlist_lcapy"] == str(path)
    assert path.read_text(encoding="utf-8") == result.netlists["lcapy"]
    assert "V1 1 0 dc 5" in result.netlists["lcapy"]


def test_lcapy_is_part_of_all(fixtures_dir, tmp_path):
    """'all' means all, the new format included."""
    result = run_pipeline(PipelineOptions(
        input_path=str(fixtures_dir / "rc_divider.kicad_sch"),
        output_dir=str(tmp_path), netlist_formats=("all",), dry_run=True))
    assert set(result.netlists) == set(netlist.FORMATS)


def test_lcapy_notes_reach_the_warnings(fixtures_dir, tmp_path):
    """What the netlist could not say is said where the user will see it."""
    result = run_pipeline(PipelineOptions(
        input_path=str(fixtures_dir / "voltage_divider.kicad_sch"),
        output_dir=str(tmp_path), netlist_formats=("lcapy",), dry_run=True))
    assert any("supply rail +5V" in warning for warning in result.warnings)


def test_lcapy_cli(fixtures_dir, tmp_path, capsys):
    code = cli.main([str(fixtures_dir / "rc_divider.kicad_sch"),
                     "-o", str(tmp_path), "--netlist", "lcapy",
                     "--no-image", "--no-alt-text"])
    out = capsys.readouterr().out
    assert code == 0
    assert (tmp_path / "rc_divider_lcapy.txt").is_file()
    assert "rc_divider_lcapy.txt" in out


# ---------------------------------------------------------------------------
# With Lcapy itself
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def lcapy_circuit():
    """``load(text) -> lcapy.Circuit``, with Lcapy's chatter silenced."""
    import warnings

    from lcapy import Circuit

    def load(text: str):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return Circuit(text)
    return load


def _exact(expression) -> Fraction:
    """An Lcapy constant as an exact fraction."""
    import sympy

    value = sympy.nsimplify(expression.sympy, rational=True)
    assert value.is_Rational, f"{expression} is not a constant"
    return Fraction(int(value.p), int(value.q))


@needs_lcapy
def test_lcapy_type_table_covers_the_installed_lcapy():
    """LCAPY-14: every type name this Lcapy knows is in the exporter's
    table.

    The table is what keeps a reference from being read as the wrong
    type.  If Lcapy grows a type the table lacks, a name starting with it
    would slip through, so the gap has to fail here rather than there.
    """
    import lcapy.mnacpts as components
    from lcapy import grammar
    from lcapy.parser import Parser

    known = set(Parser(components, grammar).ruledict)
    missing = known - set(lcapy_netlist.LCAPY_TYPES)
    assert not missing, (
        f"Lcapy has component types the exporter does not know about: "
        f"{sorted(missing)}; add them to lcapy_netlist.LCAPY_TYPES")


@needs_lcapy
@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_lcapy_loads_every_netlist(load, fixture, lcapy_circuit):
    """LCAPY-20: ``lcapy.Circuit`` accepts the netlist and reads it back.

    Loading is necessary and not sufficient - Lcapy will happily load a
    resistor it has taken for a potentiometer - so every component is
    then checked: it is there under the name written, Lcapy took it for
    the type intended, and it sits on the nodes written, in that order.
    """
    graph = load(fixture)
    result = netlist.generate(graph, "lcapy")
    if not _lines(result.text):
        pytest.skip("nothing in this circuit has an Lcapy model")
    cct = lcapy_circuit(result.text)

    # 'XX' is how Lcapy holds a comment line.
    read = {name: element for name, element in cct.elements.items()
            if element.type != "XX"}
    written = {name: _nodes_of(name, args)
               for name, args, _attrs in _lines(result.text)}
    assert set(read) == set(written), (
        f"{fixture}: Lcapy read back {sorted(set(read) ^ set(written))} "
        f"differently")
    owner = _owner(result)
    for name, nodes in written.items():
        wanted = _EXPECTED_TYPE[graph.components[owner[name]].ctype]
        assert read[name].type == wanted, (
            f"{fixture}: {name} was meant as '{wanted}' and Lcapy read "
            f"a '{read[name].type}'")
        assert list(read[name].node_names) == nodes, (
            f"{fixture}: {name} was written on {nodes} and Lcapy has it "
            f"on {list(read[name].node_names)}")


def _dc_currents(graph: CircuitGraph) -> Dict[str, Fraction]:
    """Each resistor's current, pin 1 to pin 2, solved from the graph.

    Nodal analysis in exact arithmetic, written against SPICE's
    conventions and sharing nothing with the exporter beyond the value
    reader: a voltage source holds its '+' pin above its '-' pin, and a
    current source carries current through itself from '+' to '-'.
    Every separately wired circuit gets its own reference, so one without
    a ground can still be solved for its currents.
    """
    scale = {"T": 12, "G": 9, "Meg": 6, "k": 3, "m": -3, "u": -6,
             "n": -9, "p": -12, "f": -15}

    def number(comp: Component) -> Fraction:
        text = netlist._spice_value(comp.value, comp.ctype)
        suffix = next((s for s in sorted(scale, key=len, reverse=True)
                       if text.endswith(s)), "")
        return Fraction(text[:len(text) - len(suffix)]) \
            * Fraction(10) ** scale.get(suffix, 0)

    parent: Dict[int, int] = {}

    def find(n: int) -> int:
        parent.setdefault(n, n)
        while parent[n] != n:
            n = parent[n]
        return n

    for comp in graph.components.values():
        a, b = (pin.net_id for pin in netlist._pins_in_order(comp))
        parent[find(a)] = find(b)
    islands: Dict[int, List[int]] = {}
    for n in sorted(parent):
        islands.setdefault(find(n), []).append(n)
    ground = {net.net_id for net in graph.nets if net.kind == NetKind.GROUND}
    reference = {next((n for n in members if n in ground), members[0])
                 for members in islands.values()}

    unknown = [n for n in sorted(parent) if n not in reference]
    sources = [c for c in graph.components.values()
               if c.ctype in (ComponentType.VOLTAGE_SOURCE,
                              ComponentType.BATTERY)]
    column = {n: i for i, n in enumerate(unknown)}
    size = len(unknown) + len(sources)
    matrix = [[Fraction(0)] * (size + 1) for _ in range(size)]

    def stamp(row_node: int, col_node: int, value: Fraction) -> None:
        if row_node in column and col_node in column:
            matrix[column[row_node]][column[col_node]] += value

    for comp in graph.components.values():
        pins = netlist._pins_in_order(comp)
        if comp.ctype == ComponentType.RESISTOR:
            a, b = pins[0].net_id, pins[1].net_id
            g = 1 / number(comp)
            stamp(a, a, g), stamp(b, b, g), stamp(a, b, -g), stamp(b, a, -g)
        elif comp.ctype == ComponentType.CURRENT_SOURCE:
            plus, minus = netlist._source_terminals(comp)
            # Out of the '+' node, through the source, into the '-' node.
            if plus.net_id in column:
                matrix[column[plus.net_id]][size] -= number(comp)
            if minus.net_id in column:
                matrix[column[minus.net_id]][size] += number(comp)
    for k, comp in enumerate(sources):
        plus, minus = netlist._source_terminals(comp)
        row = len(unknown) + k
        for pin, sign in ((plus, 1), (minus, -1)):
            if pin.net_id in column:
                matrix[row][column[pin.net_id]] += sign
                matrix[column[pin.net_id]][row] += sign
        matrix[row][size] = number(comp)

    for i in range(size):                       # Gauss-Jordan, exact
        pivot = next(r for r in range(i, size) if matrix[r][i] != 0)
        matrix[i], matrix[pivot] = matrix[pivot], matrix[i]
        matrix[i] = [x / matrix[i][i] for x in matrix[i]]
        for r in range(size):
            if r != i and matrix[r][i] != 0:
                factor = matrix[r][i]
                matrix[r] = [x - factor * y
                             for x, y in zip(matrix[r], matrix[i])]
    volts = {n: Fraction(0) for n in reference}
    volts.update({n: matrix[column[n]][size] for n in unknown})

    currents = {}
    for ref, comp in graph.components.items():
        if comp.ctype == ComponentType.RESISTOR:
            a, b = (pin.net_id for pin in netlist._pins_in_order(comp))
            currents[ref] = (volts[a] - volts[b]) / number(comp)
    return currents


#: Fixtures made only of resistors and DC sources with numbers on them:
#: the ones a hand calculation can settle exactly.
DC_FIXTURES = [
    "earth_blank_value.kicad_sch", "hier_parent.kicad_sch",
    "loops_two.kicad_sch", "loops_three.kicad_sch", "loops_grid.kicad_sch",
    "loops_seven.kicad_sch", "loops_two_sources.kicad_sch",
    "loops_middle_source.kicad_sch", "loops_current_source.kicad_sch",
    "loops_crossing.kicad_sch", "loops_scattered_ground.kicad_sch",
    "wheatstone.kicad_sch",
]


@needs_lcapy
@pytest.mark.slow
@pytest.mark.parametrize("fixture", DC_FIXTURES)
def test_lcapy_solves_to_the_same_currents(load, fixture, lcapy_circuit):
    """LCAPY-22: Lcapy's answer from the netlist is the circuit's answer.

    Each resistor's current is found twice: by Lcapy from the exported
    netlist, and by the nodal solver above straight from the circuit
    graph.  They can only agree, resistor for resistor and sign for sign,
    if the values, the connectivity and every source's polarity all came
    through - including the current source, whose nodes are written the
    other way round from SPICE precisely so that this holds.
    """
    graph = load(fixture)
    allowed = (ComponentType.RESISTOR, ComponentType.VOLTAGE_SOURCE,
               ComponentType.BATTERY, ComponentType.CURRENT_SOURCE)
    assert all(c.ctype in allowed for c in graph.components.values()), (
        f"{fixture} is no longer a DC resistive circuit; take it off the list")

    result = netlist.generate(graph, "lcapy")
    assert result.complete and not result.symbols
    cct = lcapy_circuit(result.text)
    expected = _dc_currents(graph)
    assert expected and any(expected.values()), "nothing flows: a dull test"

    for ref, current in expected.items():
        name = result.names[ref][0]
        assert _exact(cct[name].i) == current, (
            f"{fixture}: Lcapy has {cct[name].i} through {name}; the "
            f"circuit has {current}")


@needs_lcapy
@pytest.mark.slow
def test_lcapy_solves_the_reference_circuit(load, lcapy_circuit):
    """The assignment's own circuit, end to end: 5 V across 20 ohms in
    series with 100 ohms, so 25/6 V on node 2 once the capacitor has
    charged - on the node the description also calls node 2."""
    graph = load("rc_divider.kicad_sch")
    result = netlist.generate(graph, "lcapy")
    cct = lcapy_circuit(result.text)
    assert cct.is_dc
    assert _exact(cct[2].V.dc) == Fraction(25, 6)
    assert _exact(cct[1].V.dc) == 5
    assert node_names(graph)[graph.components["C1"].pins["1"].net_id] \
        == "node 2"


@needs_lcapy
@pytest.mark.slow
def test_lcapy_symbolic_gain_gives_the_textbook_answer(load, lcapy_circuit):
    """An inverting amplifier with R2/R1 = 10: the gain is -10 as the
    op amp's open-loop gain, left symbolic, goes to infinity."""
    import sympy

    result = netlist.generate(load("opamp_inverting.kicad_sch"), "lcapy")
    cct = lcapy_circuit(result.text + "\nVtest VIN 0 dc 1\n")
    out = cct["VOUT"].V.dc.sympy
    assert sympy.limit(out, sympy.Symbol("EU1", positive=True),
                       sympy.oo) == -10 or sympy.limit(
        out, list(out.free_symbols)[0], sympy.oo) == -10


@needs_lcapy
@pytest.mark.slow
def test_lcapy_ac_source_is_ac_at_its_frequency(load, lcapy_circuit):
    """'5V 1kHz' on the schematic is 5 cos(2000 pi t) to Lcapy."""
    import sympy
    from lcapy import t

    cct = lcapy_circuit(netlist.generate(load("rlc_series.kicad_sch"),
                                         "lcapy").text)
    assert cct.is_ac
    wanted = 5 * sympy.cos(2000 * sympy.pi * t.sympy)
    assert sympy.simplify(cct.V1.v.sympy - wanted) == 0


@needs_lcapy
@pytest.mark.slow
def test_lcapy_agrees_a_circuit_with_no_ground_cannot_be_solved(
        load, lcapy_circuit):
    """Two circuits on one sheet and no ground on either: the result
    says Lcapy will refuse, and Lcapy refuses."""
    result = netlist.generate(load("loops_two_circuits.kicad_sch"), "lcapy")
    assert len(result.ungrounded) == 2 and not result.analysable
    cct = lcapy_circuit(result.text)
    with pytest.raises(ValueError, match="no path to node 0"):
        cct.R7.i


@needs_lcapy
@pytest.mark.slow
def test_lcapy_offered_rail_source_is_valid(load, lcapy_circuit):
    """The source offered in a comment is a line that works: uncommented,
    the divider's midpoint sits at half the rail."""
    result = netlist.generate(load("voltage_divider.kicad_sch"), "lcapy")
    with_rail = "\n".join(
        line[2:] if line.startswith("# Vp5V ") else line
        for line in result.text.splitlines())
    assert with_rail != result.text
    assert _exact(lcapy_circuit(with_rail)["VOUT"].V.dc) == Fraction(5, 2)


@needs_lcapy
@pytest.mark.slow
def test_lcapy_agrees_about_what_it_cannot_analyse(load, lcapy_circuit):
    """``analysable`` is a promise about Lcapy, so ask Lcapy.

    A circuit flagged as holding a non-linear part must be one Lcapy
    refuses, and for that reason; if Lcapy ever learns to solve it, the
    flag is what has gone stale.
    """
    result = netlist.generate(load("led_battery.kicad_sch"), "lcapy")
    assert result.drawn_only == ["D1"] and not result.analysable
    cct = lcapy_circuit(result.text)
    with pytest.raises(NotImplementedError, match="non-linear"):
        cct.R1.i

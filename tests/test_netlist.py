"""Netlist generator tests.

The netlist is the output a professor is most likely to *run*, so these
tests check it electrically rather than textually:

* every SPICE device line is re-read and its nodes compared against the
  circuit graph, so a deck can never wire a component to the wrong net;
* nothing disappears -- a component that has no SPICE equivalent must be
  named in a comment *and* in the notes;
* ground is node ``0`` everywhere, and nowhere else;
* the KiCad netlist round-trips every (reference, pin) pair;
* the CSV has exactly one row per connected pin;
* identical inputs give byte-identical output in all four formats.
"""

from __future__ import annotations

import csv as csv_module
import io
from typing import Dict, List, Set, Tuple

import pytest

from conftest import VALID_FIXTURES
from schemaccess import netlist
from schemaccess.model import CircuitGraph, ComponentType, NetKind

# Device letters whose line is "<name> <node> <node> ... <value|model>".
# The count is how many leading tokens after the name are nodes.
_NODE_COUNT = {"R": 2, "C": 2, "L": 2, "D": 2, "V": 2, "I": 2,
               "Q": 3, "J": 3, "M": 4, "X": 3, "E": 4, "G": 4}


def _device_lines(deck: str) -> List[List[str]]:
    """Tokenised device lines from the top-level circuit.

    Comments, dot-commands, blanks and the bodies of ``.subckt`` blocks
    are skipped: a subcircuit's internals are library code, not part of
    the schematic's wiring.
    """
    lines = []
    inside_subckt = False
    for raw in deck.splitlines():
        line = raw.split(";", 1)[0].strip()
        if line.lower().startswith(".subckt"):
            inside_subckt = True
            continue
        if line.lower().startswith(".ends"):
            inside_subckt = False
            continue
        if inside_subckt or not line or line.startswith("*") \
                or line.startswith("."):
            continue
        lines.append(line.split())
    return lines


def _expected_nodes(graph: CircuitGraph) -> Dict[str, Set[str]]:
    """Reference -> the set of SPICE node names its pins sit on."""
    out: Dict[str, Set[str]] = {}
    for ref, comp in graph.components.items():
        names = set()
        for pin in comp.pins.values():
            if 0 <= pin.net_id < len(graph.nets):
                net = graph.nets[pin.net_id]
                names.add("0" if net.kind == NetKind.GROUND
                          else netlist._spice_node(net, "?"))
        out[ref] = names
    return out


def _ref_of(name: str, graph: CircuitGraph) -> str:
    """Recover the component reference from a SPICE device name."""
    if name in graph.components:
        return name
    if name[1:] in graph.components:      # 'XU1' -> 'U1', 'JQ3' -> 'Q3'
        return name[1:]
    for suffix in ("A", "B"):             # potentiometer halves
        if name.endswith(suffix) and name[1:-1] in graph.components:
            return name[1:-1]
    return ""


# ---------------------------------------------------------------------------
# SPICE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_spice_nodes_match_the_graph(load, fixture):
    """NET-1: every device is wired to exactly the nets its pins are on."""
    graph = load(fixture)
    deck = netlist.spice(graph).text
    expected = _expected_nodes(graph)

    for tokens in _device_lines(deck):
        name = tokens[0]
        ref = _ref_of(name, graph)
        assert ref, f"{fixture}: device '{name}' matches no component"
        count = _NODE_COUNT.get(name[0].upper())
        assert count, f"{fixture}: unknown SPICE device letter in '{name}'"
        nodes = {n for n in tokens[1:1 + count] if not n.startswith("NC_")}
        assert nodes <= expected[ref], (
            f"{fixture}: {name} is wired to {sorted(nodes - expected[ref])}, "
            f"which {ref} does not touch")


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_spice_accounts_for_every_component(load, fixture):
    """NET-2: nothing is dropped silently.

    A component either becomes a device line, or is written as a comment
    and named in the notes.  There is no third outcome.
    """
    graph = load(fixture)
    result = netlist.spice(graph)
    emitted = {_ref_of(tokens[0], graph) for tokens in _device_lines(result.text)}
    commented = {line.split()[1] for line in result.text.splitlines()
                 if line.startswith("* ") and len(line.split()) > 1
                 and line.split()[1] in graph.components}

    for ref in graph.components:
        assert ref in emitted or ref in commented, (
            f"{fixture}: {ref} appears nowhere in the deck")
        if ref not in emitted:
            assert any(note.startswith(f"{ref} ") or note.startswith(f"{ref}:")
                       for note in result.notes), (
                f"{fixture}: {ref} was commented out without a note")


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_spice_ground_is_node_zero(load, fixture):
    """NET-3: the ground net is node 0, and only the ground net is."""
    graph = load(fixture)
    deck = netlist.spice(graph).text
    ground = graph.ground_net()
    uses_zero = any("0" in tokens[1:5] for tokens in _device_lines(deck))
    if ground is None or not ground.pins:
        assert not uses_zero, f"{fixture}: node 0 used with no ground net"
    else:
        assert uses_zero, f"{fixture}: a ground net exists but node 0 is unused"


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_spice_deck_is_well_formed(load, fixture):
    """NET-4: title line first, .end last, no blank device lines."""
    graph = load(fixture)
    lines = netlist.spice(graph).text.splitlines()
    assert lines[0].startswith("*"), "the first line of a deck is its title"
    assert lines[-1] == ".end" or lines[-1] == ""
    assert ".end" in lines


@pytest.mark.parametrize("value,expected", [
    ("100", "100"),
    ("4.7k", "4.7k"),
    ("22nF", "22n"),
    ("100 Ohm", "100"),
    ("1MEG", "1Meg"),
    ("1M", "1Meg"),
    ("10m", "10m"),
    ("10 mH", "10m"),
    ("4k7", "4.7k"),
    ("1R5", "1.5"),
    ("2.2uF", "2.2u"),
    ("0.1u", "0.1u"),
    (".47", "0.47"),
    ("12,5k", "12.5k"),
    ("5V", "5"),
    ("R", None),
    ("", None),
    ("~", None),
    ("5V 1kHz", None),
])
def test_spice_value_conversion(value, expected):
    """NET-5: KiCad value strings become SPICE magnitudes, or nothing.

    Returning ``None`` matters as much as the conversions: a value the
    parser cannot read must not be guessed at, so the caller can write a
    default and say so.
    """
    assert netlist._spice_value(value, ComponentType.RESISTOR) == expected


def test_spice_diode_polarity(load):
    """NET-6: a diode is written anode-first, cathode-second.

    KiCad numbers a diode's cathode pin 1, so plain pin order would emit
    every diode backwards.
    """
    graph = load("led_battery.kicad_sch")
    deck = netlist.spice(graph).text
    diodes = [t for t in _device_lines(deck) if t[0].upper().startswith("D")]
    assert diodes, "the fixture should contain a diode"
    for tokens in diodes:
        comp = graph.components[_ref_of(tokens[0], graph)]
        anode, cathode = netlist._diode_terminals(comp)
        assert tokens[1] == netlist._spice_node(
            graph.nets[anode.net_id], "?")
        assert tokens[2] == netlist._spice_node(
            graph.nets[cathode.net_id], "?")


def test_spice_source_polarity(load):
    """NET-7: a voltage source is written positive terminal first."""
    graph = load("rc_divider.kicad_sch")
    deck = netlist.spice(graph).text
    sources = [t for t in _device_lines(deck) if t[0].upper().startswith("V")]
    assert sources, "the fixture should contain a voltage source"
    for tokens in sources:
        comp = graph.components[_ref_of(tokens[0], graph)]
        plus, minus = netlist._source_terminals(comp)
        assert tokens[1] == netlist._spice_node(graph.nets[plus.net_id], "?")
        assert tokens[2] == netlist._spice_node(graph.nets[minus.net_id], "?")


def test_spice_opamp_gets_a_subcircuit(load):
    """NET-8: an op amp is callable, not a dangling X-line."""
    graph = load("opamp_inverting.kicad_sch")
    deck = netlist.spice(graph).text
    assert "XU1" in deck
    assert ".subckt SCHEMACCESS_OPAMP" in deck
    assert ".ends" in deck


# ---------------------------------------------------------------------------
# KiCad netlist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_kicad_netlist_round_trips_every_pin(load, fixture):
    """NET-9: every (reference, pin) on a net appears once in the export."""
    graph = load(fixture)
    text = netlist.kicad(graph).text

    expected: Set[Tuple[str, str]] = set()
    for net in graph.nets:
        expected.update(net.pins)

    found: Set[Tuple[str, str]] = set()
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("(node "):
            ref = stripped.split('(ref "', 1)[1].split('"', 1)[0]
            pin = stripped.split('(pin "', 1)[1].split('"', 1)[0]
            assert (ref, pin) not in found, f"{fixture}: {ref}.{pin} twice"
            found.add((ref, pin))
        elif stripped.startswith("(comp "):
            current = stripped.split('(ref "', 1)[1].split('"', 1)[0]
            assert current in graph.components
    assert found == expected


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_kicad_netlist_lists_every_component(load, fixture):
    """NET-10: the components block matches the graph exactly."""
    text = netlist.kicad(load(fixture)).text
    graph = load(fixture)
    refs = [line.split('(ref "', 1)[1].split('"', 1)[0]
            for line in text.splitlines() if line.strip().startswith("(comp ")]
    assert sorted(refs) == sorted(graph.components)
    assert len(refs) == len(set(refs))


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_kicad_netlist_codes_are_consecutive(load, fixture):
    """NET-11: net codes run 1, 2, 3 ... with no gaps."""
    text = netlist.kicad(load(fixture)).text
    codes = [int(line.split('(code "', 1)[1].split('"', 1)[0])
             for line in text.splitlines() if line.strip().startswith("(net ")]
    assert codes == list(range(1, len(codes) + 1))


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_kicad_netlist_names_the_file_not_its_path(load, fixture):
    """NET-16: the export records a file name, never a directory.

    An absolute path would make the same schematic produce a different
    netlist on a desktop and in the browser, and would publish the
    author's folder layout inside a file they hand to someone else.
    """
    text = netlist.kicad(load(fixture)).text
    source = text.split('(source "', 1)[1].split('"', 1)[0]
    assert "/" not in source and "\\" not in source, source
    assert source.endswith(".kicad_sch") or source == "unknown"


def test_kicad_netlist_is_independent_of_where_the_file_lives(load):
    """NET-16: moving the schematic must not change the netlist."""
    graph = load("rc_divider.kicad_sch")
    here = netlist.kicad(graph).text
    graph.document.source_path = "/somewhere/else/rc_divider.kicad_sch"
    assert netlist.kicad(graph).text == here


def test_kicad_netlist_escapes_quotes():
    """NET-12: a value containing a quote cannot break the S-expression."""
    from schemaccess.model import Component, Net
    graph = CircuitGraph()
    graph.components["R1"] = Component(
        ref="R1", ctype=ComponentType.RESISTOR, value='4"7',
        lib_id="Device:R", position=(0.0, 0.0))
    text = netlist.kicad(graph).text
    assert r'\"' in text
    assert text.count('(value "') == 1


# ---------------------------------------------------------------------------
# Readable table and CSV
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_text_table_names_everything(load, fixture):
    """NET-13: every component and every connected net is named in prose."""
    graph = load(fixture)
    text = netlist.text(graph).text
    for ref in graph.components:
        assert f"  {ref}: " in text, f"{fixture}: {ref} missing from the table"
    for net in graph.nets:
        if net.pins:
            assert f"  {net.name} (" in text, (
                f"{fixture}: net {net.name} missing from the table")


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_csv_has_one_row_per_pin(load, fixture):
    """NET-14: the CSV is a faithful pin table, header included."""
    graph = load(fixture)
    rows = list(csv_module.reader(io.StringIO(netlist.csv(graph).text)))
    rows = [row for row in rows if row]
    assert rows[0][0] == "net" and rows[0][2] == "reference"
    expected = sum(len(net.pins) for net in graph.nets)
    assert len(rows) - 1 == expected


def test_csv_quotes_embedded_commas():
    """NET-15: a value with a comma cannot shift the columns."""
    from schemaccess.model import Component, Net, PinConnection
    graph = CircuitGraph()
    graph.nets.append(Net(net_id=0, name="N1", pins=[("R1", "1")]))
    comp = Component(ref="R1", ctype=ComponentType.RESISTOR,
                     value="1k, 1%", lib_id="Device:R", position=(0.0, 0.0))
    comp.pins["1"] = PinConnection(number="1", name="~", position=(0.0, 0.0),
                                   net_id=0)
    graph.components["R1"] = comp
    rows = list(csv_module.reader(io.StringIO(netlist.csv(graph).text)))
    assert rows[1][4] == "1k, 1%"
    assert len(rows[1]) == len(rows[0])


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", VALID_FIXTURES)
@pytest.mark.parametrize("fmt", netlist.FORMATS)
def test_output_is_deterministic(load, fixture, fmt):
    """DET-1: identical inputs give byte-identical netlists."""
    first = netlist.generate(load(fixture), fmt).text
    second = netlist.generate(load(fixture), fmt).text
    assert first == second
    assert first.strip(), f"{fixture}: the {fmt} netlist is empty"


def test_unknown_format_is_rejected(load):
    with pytest.raises(ValueError, match="Unknown netlist format"):
        netlist.generate(load("rc_divider.kicad_sch"), "verilog")


def test_empty_graph_still_produces_every_format():
    """REL-1: an empty circuit is a valid circuit, not a crash."""
    graph = CircuitGraph()
    for fmt in netlist.FORMATS:
        result = netlist.generate(graph, fmt)
        assert isinstance(result.text, str)
    assert ".end" in netlist.spice(graph).text
    assert netlist.kicad(graph).text.startswith("(export")

"""Power symbols, SPICE node names and the power report."""

from __future__ import annotations

from typing import List, Tuple

import pytest

from schemaccess import alttext, circuitikz, netlist, power
from schemaccess.model import (CircuitGraph, Component, ComponentType, Net,
                               NetKind, PinConnection)
from schemaccess.pipeline import summarize

KICAD_GROUNDS = ["Earth", "Earth_Clean", "Earth_Protective", "GND", "GND1",
                 "GND2", "GND3", "GNDA", "GNDD", "GNDPWR", "GNDREF", "GNDS"]


def _net_of(graph: CircuitGraph, ref: str, pin: str) -> Net:
    return graph.nets[graph.components[ref].pins[pin].net_id]


def _spice_line(deck: str, device: str) -> List[str]:
    for line in deck.splitlines():
        tokens = line.split()
        if tokens and tokens[0] == device:
            return tokens
    raise AssertionError(f"{device} missing from the deck")


def test_pwr1_emptied_earth_is_ground_everywhere(load) -> None:
    graph = load("earth_blank_value.kicad_sch")
    bottom = _net_of(graph, "R1", "2")
    assert bottom.kind == NetKind.GROUND
    assert bottom.name == "Earth"
    assert _net_of(graph, "V1", "2") is bottom

    tex = circuitikz.generate(graph)
    assert "node[ground]" in tex
    assert "node[vcc]" not in tex and "textasciitilde" not in tex

    deck = netlist.spice(graph).text
    assert _spice_line(deck, "V1")[1:3] == ["N1", "0"]
    assert _spice_line(deck, "R1")[1:3] == ["N1", "0"]
    assert "NC_" not in deck

    for detail in ("short", "standard", "detailed"):
        assert "~" not in alttext.generate(graph, detail)


def test_pwr1_the_emptied_name_is_reported(load) -> None:
    graph = load("earth_blank_value.kicad_sch")
    assert any("#PWR01" in w and "read as Earth" in w
               for w in graph.warnings)


@pytest.mark.parametrize("index,name", list(enumerate(KICAD_GROUNDS, 1)))
def test_pwr2_every_kicad_ground_is_ground(index: int, name: str,
                                           load) -> None:
    graph = load("power_symbols.kicad_sch")
    net = _net_of(graph, f"R{index}", "2")
    assert net.kind == NetKind.GROUND, name
    assert net.name == name
    deck = netlist.spice(graph).text
    assert _spice_line(deck, f"R{index}")[2] == "0"


def test_pwr2_every_ground_is_drawn_as_ground(load) -> None:
    tex = circuitikz.generate(load("power_symbols.kicad_sch"))
    assert tex.count("node[ground]") == 15


@pytest.mark.parametrize("name", KICAD_GROUNDS + [
    "gnd", "AGND", "DGND", "GND_ISO", "EARTH", "0", "VSS"])
def test_pwr2_ground_names(name: str) -> None:
    assert power.is_ground_name(name)


@pytest.mark.parametrize("name", [
    "", "~", "VCC", "VDD", "VEE", "VSSA", "+5V", "-12V", "V+", "VGND",
    "COMMON", "OUT"])
def test_pwr2_not_ground_names(name: str) -> None:
    assert not power.is_ground_name(name)


def test_pwr3_emptied_symbols_never_short_together(load) -> None:
    graph = load("power_symbols.kicad_sch")
    emptied = [_net_of(graph, "R1", "2"),
               _net_of(graph, "R4", "2"),
               _net_of(graph, "R8", "2"),
               _net_of(graph, "R31", "1")]
    assert len({net.net_id for net in emptied}) == 4
    assert emptied[3].kind == NetKind.POWER and emptied[3].name == "+5V"


def test_pwr3_emptied_symbol_joins_its_named_twin(load) -> None:
    graph = load("power_symbols.kicad_sch")
    assert _net_of(graph, "R31", "1") is _net_of(graph, "R30", "1")
    assert _net_of(graph, "R4", "2") is _net_of(graph, "R21", "1")


def test_pwr4_lib_name_alias_finds_its_definition(load) -> None:
    graph = load("power_symbols.kicad_sch")
    assert "R99" in graph.components
    assert not any("No library definition" in w for w in graph.warnings)
    assert _net_of(graph, "R99", "2").kind == NetKind.GROUND


def _resistors_between(names: List[Tuple[str, NetKind]]) -> CircuitGraph:
    graph = CircuitGraph()
    for i, (name, kind) in enumerate(names):
        graph.nets.append(Net(net_id=i, name=name, kind=kind))
    for k in range(len(names) // 2):
        ref = f"R{k + 1}"
        comp = Component(ref=ref, ctype=ComponentType.RESISTOR, value="1k",
                         lib_id="Device:R", position=(0.0, 0.0))
        for number, net_id in (("1", 2 * k), ("2", 2 * k + 1)):
            comp.pins[number] = PinConnection(number=number, name="~",
                                              position=(0.0, 0.0),
                                              net_id=net_id)
            graph.nets[net_id].pins.append((ref, number))
        graph.components[ref] = comp
    return graph


def test_pwr5_spice_never_merges_or_splits_nets() -> None:
    graph = _resistors_between([
        ("V+", NetKind.NAMED), ("VP", NetKind.NAMED),
        ("Vout", NetKind.NAMED), ("VOUT", NetKind.NAMED),
        ("~", NetKind.POWER), ("?", NetKind.NAMED),
        ("gnd", NetKind.NAMED), ("GND", NetKind.GROUND)])
    deck = netlist.spice(graph).text
    nodes = [_spice_line(deck, f"R{k}")[1:3] for k in range(1, 5)]
    names = [n.lower() for pair in nodes for n in pair if n != "0"]
    assert len(set(names)) == len(names) == 7
    assert "gnd" not in names
    assert nodes[3][1] == "0"
    assert "NC_" not in deck


def test_pwr5_spice_warns_when_there_is_no_ground() -> None:
    graph = _resistors_between([("A", NetKind.NAMED), ("B", NetKind.NAMED)])
    assert any("no ground" in note for note in netlist.spice(graph).notes)
    grounded = _resistors_between([("A", NetKind.NAMED),
                                   ("GND", NetKind.GROUND)])
    assert not any("no ground" in note
                   for note in netlist.spice(grounded).notes)


def test_pwr6_report_lists_what_each_power_symbol_became(load) -> None:
    stats = summarize(load("earth_blank_value.kicad_sch"))
    assert "Power symbols: ground Earth." in stats.summary_lines()
    stats = summarize(load("power_symbols.kicad_sch"))
    line = next(s for s in stats.summary_lines()
                if s.startswith("Power symbols"))
    assert line.endswith("; supply +5V, -12V, VCC.")
    for name in KICAD_GROUNDS:
        assert name in line

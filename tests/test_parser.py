"""FUN-1: parsing completeness and accuracy.

Per-fixture component/net counts and ground presence must match the
manifest; the rc_divider reference fixture is deep-checked for component
types, values and exact net membership.
"""

from __future__ import annotations

import pytest

from conftest import VALID_FIXTURES, load_graph
from schemaccess.model import ComponentType, NetKind


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_fun1_component_count_matches_manifest(name: str, load,
                                               manifest) -> None:
    graph = load(name)
    assert len(graph.components) == manifest[name]["components"], (
        f"{name}: expected {manifest[name]['components']} components, "
        f"got {sorted(graph.components)}")


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_fun1_net_count_matches_manifest(name: str, load, manifest) -> None:
    graph = load(name)
    assert len(graph.nets) == manifest[name]["nets"], (
        f"{name}: expected {manifest[name]['nets']} nets, got "
        f"{[(n.net_id, n.name, sorted(n.pins)) for n in graph.nets]}")


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_fun1_ground_presence_matches_manifest(name: str, load,
                                               manifest) -> None:
    graph = load(name)
    has_ground = graph.ground_net() is not None
    assert has_ground == manifest[name]["ground"], (
        f"{name}: ground presence mismatch")


def test_fun1_rc_divider_component_types(load) -> None:
    """rc_divider deep check: reference set and component types."""
    graph = load("rc_divider.kicad_sch")
    comps = graph.components
    assert set(comps) == {"V1", "R1", "R2", "C1"}
    assert comps["R1"].ctype is ComponentType.RESISTOR
    assert comps["R2"].ctype is ComponentType.RESISTOR
    assert comps["C1"].ctype is ComponentType.CAPACITOR
    assert comps["V1"].ctype is ComponentType.VOLTAGE_SOURCE


def test_fun1_rc_divider_component_values(load) -> None:
    """rc_divider deep check: raw KiCad value strings survive parsing."""
    graph = load("rc_divider.kicad_sch")
    comps = graph.components
    assert comps["R1"].value == "20"
    assert comps["R2"].value == "100"
    assert comps["C1"].value == "22nF"
    assert comps["V1"].value == "5V"


def test_fun1_rc_divider_net_membership(load) -> None:
    """rc_divider deep check: the three nets contain exactly these pins."""
    graph = load("rc_divider.kicad_sch")
    assert len(graph.nets) == 3
    memberships = {frozenset(net.pins) for net in graph.nets}
    expected = {
        frozenset({("V1", "2"), ("C1", "2"), ("R2", "2")}),   # GND
        frozenset({("V1", "1"), ("R1", "1")}),                # source node
        frozenset({("C1", "1"), ("R1", "2"), ("R2", "1")}),   # divider tap
    }
    assert memberships == expected

    ground = graph.ground_net()
    assert ground is not None
    assert ground.kind is NetKind.GROUND
    assert set(ground.pins) == {("V1", "2"), ("C1", "2"), ("R2", "2")}


# ---------------------------------------------------------------------------
# FUN-1 conversion report: counts the components in each KiCad file and
# shows how many made it through to the alt-text description.
#
#     python -m pytest tests/test_parser.py -k report -s
#
# The -s flag is what lets the table print (pytest captures stdout by
# default).  The test also fails if any component is missing from the
# description, so it is a real check and not just a printout.
# ---------------------------------------------------------------------------

def test_fun1_conversion_report(manifest) -> None:
    """Report, per schematic, what went in and what came out."""
    from schemaccess import alttext, circuitikz
    from schemaccess.pipeline import summarize

    problems = {}
    print()  # start the report on its own line

    for name in VALID_FIXTURES:
        graph = load_graph(name)
        fallbacks: set = set()
        tikz = circuitikz.generate(graph, fallbacks=fallbacks)
        text = alttext.generate(graph, "detailed")
        stats = summarize(graph, tikz, text, fallbacks)

        drew = "OK  " if stats.drawn == stats.components else "FAIL"
        told = "OK  " if stats.described == stats.components else "FAIL"
        print(f"{name}")
        print(f"    {stats.components} components in KiCad schematic, "
              f"{stats.nodes} nodes ({stats.nets} nets)")
        print(f"    {drew} {stats.drawn} converted to CircuiTikZ symbols")
        print(f"    {told} {stats.described} described in the alt text")
        if stats.fallbacks:
            print(f"         not converted: {', '.join(stats.fallbacks)}")
        if stats.undescribed:
            print(f"         not described: {', '.join(stats.undescribed)}")

        if stats.fallbacks or stats.undescribed:
            problems[name] = {
                "not converted": stats.fallbacks,
                "not described": stats.undescribed,
            }

    assert not problems, f"components that did not convert: {problems}"

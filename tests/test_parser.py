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
# FUN-1 wire-to-wire connectivity.  These two rules sit either side of a
# fine line and are easy to get backwards, so they are pinned directly on
# hand-built geometry rather than on a fixture file.
# ---------------------------------------------------------------------------

def _wire_only_doc(*segments) -> "SchematicDocument":
    """A document containing nothing but the given wire segments."""
    from schemaccess.model import SchematicDocument, Wire

    return SchematicDocument(
        wires=[Wire(points=[a, b]) for a, b in segments])


def test_fun1_wire_ending_on_another_wire_connects() -> None:
    """A T joint connects even with no junction dot stored in the file.

    KiCad draws the dot for you and does not always write one, so
    connectivity may not depend on the dot being present.
    """
    from schemaccess.netbuilder import build_graph

    doc = _wire_only_doc(
        ((0.0, 0.0), (10.0, 0.0)),     # horizontal rail
        ((5.0, 0.0), (5.0, 5.0)),      # stub ending on the rail's middle
    )
    assert len(build_graph(doc).nets) == 1


def test_fun1_wires_crossing_without_a_junction_stay_separate() -> None:
    """Two wires crossing mid-span are not connected without a junction."""
    from schemaccess.netbuilder import build_graph

    doc = _wire_only_doc(
        ((0.0, 5.0), (10.0, 5.0)),     # horizontal, crossing at (5, 5)
        ((5.0, 0.0), (5.0, 10.0)),     # vertical, neither end on the other
    )
    assert len(build_graph(doc).nets) == 2


def test_fun1_crossing_wires_connect_when_a_junction_is_present() -> None:
    """The same crossing becomes one net once a junction is placed on it."""
    from schemaccess.model import Junction
    from schemaccess.netbuilder import build_graph

    doc = _wire_only_doc(
        ((0.0, 5.0), (10.0, 5.0)),
        ((5.0, 0.0), (5.0, 10.0)),
    )
    doc.junctions.append(Junction(x=5.0, y=5.0))
    assert len(build_graph(doc).nets) == 1


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

#: PER-1 budget for translating a schematic, in milliseconds.  Only the
#: translation counts; running LaTeX is an external tool and is timed
#: separately by the renderer tests.
_CONVERT_BUDGET_MS = 5000.0


def test_fun1_conversion_report(fixtures_dir, manifest) -> None:
    """Report, per schematic, what went in, what came out, and how long."""
    import time

    from schemaccess import alttext, circuitikz, kicad_parser, netbuilder
    from schemaccess.pipeline import summarize

    problems = {}
    slow = {}
    print()  # start the report on its own line

    for name in VALID_FIXTURES:
        # Time the whole translation the way a user would experience it:
        # read the file, build the graph, write both outputs.
        started = time.perf_counter()
        doc = kicad_parser.parse_file(str(fixtures_dir / name))
        graph = netbuilder.build_graph(doc)
        read_ms = (time.perf_counter() - started) * 1000.0

        fallbacks: set = set()
        started = time.perf_counter()
        tikz = circuitikz.generate(graph, fallbacks=fallbacks)
        draw_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        text = alttext.generate(graph, "detailed")
        text_ms = (time.perf_counter() - started) * 1000.0

        stats = summarize(graph, tikz, text, fallbacks)
        total_ms = read_ms + draw_ms + text_ms

        drew = "OK  " if stats.drawn == stats.components else "FAIL"
        told = "OK  " if stats.described == stats.components else "FAIL"
        fast = "OK  " if total_ms <= _CONVERT_BUDGET_MS else "FAIL"
        print(f"{name}")
        print(f"    {stats.components} components in KiCad schematic, "
              f"{stats.nodes} nodes ({stats.nets} nets)")
        print(f"    {drew} {stats.drawn} converted to CircuiTikZ symbols")
        print(f"    {told} {stats.described} described in the alt text")
        print(f"    {fast} converted in {total_ms:.1f} ms "
              f"(read {read_ms:.1f}, drawing {draw_ms:.1f}, "
              f"description {text_ms:.1f})")
        if stats.fallbacks:
            print(f"         not converted: {', '.join(stats.fallbacks)}")
        if stats.undescribed:
            print(f"         not described: {', '.join(stats.undescribed)}")

        if stats.fallbacks or stats.undescribed:
            problems[name] = {
                "not converted": stats.fallbacks,
                "not described": stats.undescribed,
            }
        if total_ms > _CONVERT_BUDGET_MS:
            slow[name] = round(total_ms, 1)

    assert not problems, f"components that did not convert: {problems}"
    assert not slow, (
        f"schematics over the {_CONVERT_BUDGET_MS:.0f} ms budget: {slow}")

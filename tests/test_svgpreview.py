"""SVG preview tests.

The preview is the first thing a reader sees, so it has to be *true* --
same topology, same positions, same labels as the LaTeX output -- even
though the symbol artwork is drawn here rather than by circuitikz.

* SVG-1 - the output is well-formed XML with a viewBox that contains the
  whole schematic;
* SVG-2 - every wire in the document becomes a drawn polyline;
* SVG-3 - every visible reference designator is drawn;
* SVG-4 - every component leaves marks at its own pin positions, so the
  drawing cannot silently omit a part;
* SVG-5 - the standalone form carries its own colours; the embedded form
  inherits the page's, so it reads in a light or a dark theme;
* DET-1 - identical inputs give byte-identical SVG.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Tuple

import pytest

from conftest import VALID_FIXTURES
from schemaccess import svgpreview
from schemaccess.model import CircuitGraph, SchematicDocument

SVG_NS = "{http://www.w3.org/2000/svg}"

Point = Tuple[float, float]

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _root(svg: str) -> ET.Element:
    return ET.fromstring(svg.split("?>", 1)[-1] if svg.startswith("<?xml")
                         else svg)


def _viewbox(svg: str) -> Tuple[float, float, float, float]:
    values = [float(v) for v in _root(svg).get("viewBox").split()]
    assert len(values) == 4
    return tuple(values)               # type: ignore[return-value]


def _drawn_points(svg: str) -> List[Point]:
    """Every coordinate the drawing puts down, in schematic millimetres.

    A symbol's own artwork lives inside a ``<g transform=...>`` and is
    written in that frame's local coordinates, so those subtrees are
    skipped: only marks already in schematic space are collected.
    """
    points: List[Point] = []

    def walk(element: ET.Element) -> None:
        for child in element:
            tag = child.tag.replace(SVG_NS, "")
            if tag == "g" and child.get("transform"):
                continue                     # local symbol frame
            if tag == "line":
                points.append((float(child.get("x1")), float(child.get("y1"))))
                points.append((float(child.get("x2")), float(child.get("y2"))))
            elif tag == "polyline":
                numbers = [float(n)
                           for n in _NUMBER.findall(child.get("points"))]
                points.extend(zip(numbers[0::2], numbers[1::2]))
            elif tag == "circle":
                points.append((float(child.get("cx")),
                               float(child.get("cy"))))
            walk(child)

    walk(_root(svg))
    return points


def _symbol_frames(svg: str) -> List[Point]:
    """Origins of the transformed groups a symbol's artwork sits in."""
    origins: List[Point] = []
    for element in _root(svg).iter(f"{SVG_NS}g"):
        transform = element.get("transform") or ""
        if not transform.startswith("translate"):
            continue
        numbers = [float(n) for n in _NUMBER.findall(transform)]
        if len(numbers) >= 2:
            origins.append((numbers[0], numbers[1]))
    return origins


def _near(point: Point, targets: List[Point], tolerance: float = 0.9) -> bool:
    return any(abs(point[0] - x) <= tolerance and abs(point[1] - y) <= tolerance
               for x, y in targets)


# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_output_is_well_formed_svg(load, fixture):
    """SVG-1: the file parses, and declares a viewBox and a title."""
    svg = svgpreview.generate(load(fixture))
    root = _root(svg)
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("viewBox")
    assert root.get("role") == "img"
    assert root.get("aria-label")
    assert root.find(f"{SVG_NS}title") is not None


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_viewbox_contains_the_whole_drawing(load, fixture):
    """SVG-1: nothing is drawn outside the visible area."""
    graph = load(fixture)
    svg = svgpreview.generate(graph)
    min_x, min_y, width, height = _viewbox(svg)
    assert width > 0 and height > 0

    # Marks in schematic space, and the origin of every symbol's own
    # frame, all have to land inside the visible area.
    for x, y in _drawn_points(svg) + _symbol_frames(svg):
        assert min_x - 0.01 <= x <= min_x + width + 0.01, f"{fixture}: x={x}"
        assert min_y - 0.01 <= y <= min_y + height + 0.01, f"{fixture}: y={y}"


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_every_wire_is_drawn(load, fixture):
    """SVG-2: one polyline per wire in the document, none invented."""
    graph = load(fixture)
    svg = svgpreview.generate(graph)
    root = _root(svg)
    wires = root.find(f"{SVG_NS}g[@class='sa-wires']")
    assert wires is not None
    drawn = wires.findall(f"{SVG_NS}polyline")
    expected = [w for w in graph.document.wires if len(w.points) >= 2]
    assert len(drawn) == len(expected), fixture


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_junction_dots_can_be_turned_off(load, fixture):
    """SVG-2: the dots are optional, and only the dots change."""
    graph = load(fixture)
    with_dots = _root(svgpreview.generate(graph))
    without = _root(svgpreview.generate(load(fixture), junction_dots=False))

    def dots(root):
        return [e for e in root.iter()
                if e.get("class") == "sa-dot"]

    assert len(dots(with_dots)) == len(graph.document.junctions)
    assert dots(without) == []


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_visible_references_are_drawn(load, fixture):
    """SVG-3: a reader can name every part they can see."""
    graph = load(fixture)
    svg = svgpreview.generate(graph)
    texts = {(element.text or "").strip()
             for element in _root(svg).iter(f"{SVG_NS}text")}
    # A label may be drawn as "U1 LM358", so match on word boundaries.
    words = set()
    for text in texts:
        words.update(text.split())

    for ref, comp in graph.components.items():
        if not comp.shows("Reference"):
            continue
        assert ref in words, f"{fixture}: {ref} is not labelled"


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_every_component_reaches_its_pins(load, fixture):
    """SVG-4: each component draws something at each of its pins.

    This is what makes the preview trustworthy: whatever symbol was
    chosen, the drawing still lands on the real connection points, so a
    reader comparing it with KiCad sees the same circuit.
    """
    graph = load(fixture)
    xs = [pin.position[0] for comp in graph.components.values()
          for pin in comp.pins.values()]
    if xs and max(xs) - min(xs) > svgpreview._GAP_THRESHOLD:
        pytest.skip("flattened hierarchy: coordinates are deliberately "
                    "compressed, so pin positions do not map one to one")

    svg = svgpreview.generate(graph)
    points = _drawn_points(svg)
    # An optional pin on a net nothing else touches gets no lead, the way
    # circuitikz leaves a dangling op-amp supply unwired; only connected
    # pins have to be reached.
    dangling = {net.net_id for net in graph.nets if len(net.pins) < 2}

    for ref, comp in graph.components.items():
        if len(comp.pins) < 2:
            continue
        for number, pin in comp.pins.items():
            if pin.net_id < 0 or pin.net_id in dangling:
                continue
            assert _near(pin.position, points), (
                f"{fixture}: nothing is drawn at pin {number} of {ref} "
                f"{pin.position}")


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_net_labels_are_drawn(load, fixture):
    """SVG-3: local, global and hierarchical labels all appear."""
    graph = load(fixture)
    svg = svgpreview.generate(graph)
    texts = {(element.text or "").strip()
             for element in _root(svg).iter(f"{SVG_NS}text")}
    for label in graph.document.labels:
        assert label.text in texts, f"{fixture}: label {label.text} missing"


def test_standalone_carries_its_own_colours(load):
    """SVG-5: a saved file has ink; an embedded one inherits it."""
    graph = load("rc_divider.kicad_sch")
    standalone = svgpreview.generate(graph, standalone=True)
    embedded = svgpreview.generate(load("rc_divider.kicad_sch"),
                                   standalone=False)

    assert standalone.startswith("<?xml")
    assert "prefers-color-scheme" in standalone
    assert "color: #111827" in standalone

    assert not embedded.startswith("<?xml")
    assert "prefers-color-scheme" not in embedded
    # Both draw with currentColor, which is what lets the host theme win.
    assert "currentColor" in embedded


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_output_is_deterministic(load, fixture):
    """DET-1: identical inputs give byte-identical SVG."""
    first = svgpreview.generate(load(fixture))
    second = svgpreview.generate(load(fixture))
    assert first == second


def test_empty_graph_is_a_valid_drawing():
    """REL-1: an empty circuit produces an empty picture, not a crash."""
    svg = svgpreview.generate(CircuitGraph(document=SchematicDocument()))
    root = _root(svg)
    assert root.get("viewBox")
    assert _drawn_points(svg) == []


def test_hierarchical_sheets_are_compressed(load):
    """SVG-1: flattened sheet islands do not stretch the drawing to nothing.

    The parser spreads sub-sheets 10 000 mm apart so their coordinates
    cannot collide.  Rendering that verbatim would make each sheet a
    speck, so wide gaps are compressed exactly as the CircuiTikZ
    generator compresses them.
    """
    graph = load("hier_parent.kicad_sch")
    _min_x, _min_y, width, _height = _viewbox(svgpreview.generate(graph))
    assert width < 500, "a flattened hierarchy should not be 10 metres wide"

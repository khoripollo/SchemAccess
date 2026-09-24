"""PDF writer tests.

The PDF is written by hand rather than by a library, so these tests check
the two things that could go wrong: that the file is a structurally valid
PDF a reader will open, and that what it draws is the same geometry the
SVG draws.

* PDF-1 - header, single page, cross-reference table and trailer are all
  present and internally consistent;
* PDF-2 - the page is exactly the drawing's bounding box, in points;
* PDF-3 - every wire endpoint appears in the content stream, so the PDF
  cannot quietly lose part of the circuit;
* PDF-4 - the path operators are balanced: nothing is left unpainted and
  no fill is emitted without a path;
* PDF-5 - visible reference designators reach the page as text;
* DET-1 - identical inputs give byte-identical PDFs.
"""

from __future__ import annotations

import re
from typing import List, Set, Tuple

import pytest

from conftest import VALID_FIXTURES
from schemaccess import pdfwriter, svgpreview
from schemaccess.model import CircuitGraph, SchematicDocument

Point = Tuple[float, float]

_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _content(pdf: bytes) -> str:
    match = _STREAM.search(pdf)
    assert match, "the PDF has no content stream"
    return match.group(1).decode("latin-1")


def _media_box(pdf: bytes) -> Tuple[float, float]:
    match = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf)
    assert match, "the page has no MediaBox"
    return (float(match.group(1)), float(match.group(2)))


def _painted_points(content: str) -> List[Point]:
    """Coordinates of every moveto and lineto outside a rotated frame.

    Symbol artwork is drawn inside ``q ... cm ... Q`` blocks in its own
    local frame, exactly as the SVG puts it inside a transformed group,
    so those runs are skipped and only schematic-space marks collected.
    """
    points: List[Point] = []
    depth = 0
    for token in content.split("\n"):
        parts = token.split()
        if not parts:
            continue
        if parts[0] == "q":
            depth += 1
            continue
        if parts[0] == "Q":
            depth = max(0, depth - 1)
            continue
        if depth > 1:                       # inside a symbol's own frame
            continue
        numbers = [float(n) for n in _NUMBER.findall(token)]
        index = 0
        for word in parts:
            if word in ("m", "l") and index >= 2:
                points.append((numbers[index - 2], numbers[index - 1]))
            if _NUMBER.fullmatch(word):
                index += 1
    return points


def _near(point: Point, targets: List[Point], tolerance: float = 0.9) -> bool:
    return any(abs(point[0] - x) <= tolerance and abs(point[1] - y) <= tolerance
               for x, y in targets)


# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_structure_is_a_valid_pdf(load, fixture):
    """PDF-1: the parts a reader looks for are all present and agree."""
    pdf = pdfwriter.generate(load(fixture))
    assert pdf.startswith(b"%PDF-1.")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") == 1
    assert b"/Type /Catalog" in pdf
    assert b"xref" in pdf and b"trailer" in pdf

    # startxref must point at the cross-reference table.
    start = int(pdf.rsplit(b"startxref", 1)[1].split(b"%%EOF")[0].strip())
    assert pdf[start:start + 4] == b"xref"

    # Every object offset in the table must land on its own "N 0 obj".
    table = pdf[start:].split(b"\n")
    entries = [row for row in table if row.endswith(b" n ")
               or row.endswith(b" n")]
    assert entries, "no in-use objects in the xref table"
    for number, row in enumerate(entries, start=1):
        offset = int(row.split()[0])
        assert pdf[offset:].startswith(f"{number} 0 obj".encode())


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_stream_length_is_declared_correctly(load, fixture):
    """PDF-1: a wrong /Length is the classic way to break a reader."""
    pdf = pdfwriter.generate(load(fixture))
    declared = int(re.search(rb"/Length (\d+)", pdf).group(1))
    assert len(_content(pdf).encode("latin-1")) == declared


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_page_matches_the_drawing(load, fixture):
    """PDF-2: the page is the bounding box, converted to points."""
    graph = load(fixture)
    bounds = svgpreview.draw(graph, svgpreview._Canvas())
    width, height = _media_box(pdfwriter.generate(load(fixture)))
    assert width == pytest.approx(bounds.width * pdfwriter.PT_PER_MM, abs=0.05)
    assert height == pytest.approx(bounds.height * pdfwriter.PT_PER_MM,
                                   abs=0.05)


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_every_wire_reaches_the_page(load, fixture):
    """PDF-3: no part of the circuit is lost on the way to the PDF."""
    graph = load(fixture)
    points = _painted_points(_content(pdfwriter.generate(graph)))
    document = graph.document or SchematicDocument()
    squeeze = svgpreview._Squeeze(
        [p[0] for wire in document.wires for p in wire.points])
    for wire in document.wires:
        for corner in (wire.points[0], wire.points[-1]):
            assert _near(squeeze(corner), points), (
                f"{fixture}: wire endpoint {corner} is not painted")


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_paths_are_balanced(load, fixture):
    """PDF-4: every path is painted, and the q/Q stack returns to zero."""
    content = _content(pdfwriter.generate(load(fixture)))
    depth = 0
    lowest = 0
    for line in content.split("\n"):
        parts = line.split()
        if parts[:1] == ["q"]:
            depth += 1
        elif parts[:1] == ["Q"]:
            depth -= 1
            lowest = min(lowest, depth)
    assert depth == 0, f"{fixture}: unbalanced q/Q ({depth} left open)"
    assert lowest >= 0, f"{fixture}: a Q appeared before its q"

    for line in content.split("\n"):
        if " m " in f" {line} " and not line.startswith("%"):
            assert line.rstrip().rsplit(" ", 1)[-1] in ("S", "f", "s", "h"), (
                f"{fixture}: path is never painted: {line[:60]}")


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_references_are_drawn_as_text(load, fixture):
    """PDF-5: the labels a reader needs are really in the file."""
    graph = load(fixture)
    content = _content(pdfwriter.generate(graph))
    shown: Set[str] = set()
    for match in re.finditer(r"\(([^)]*)\) Tj", content):
        shown.update(match.group(1).split())
    for ref, comp in graph.components.items():
        if comp.shows("Reference"):
            assert ref in shown, f"{fixture}: {ref} is not labelled"


def test_omega_uses_the_symbol_font(load):
    """PDF-5: Helvetica has no Omega, so the ohm sign switches fonts.

    Writing it in Helvetica would silently print the wrong glyph, which
    is exactly the kind of quiet wrongness the rest of this project
    refuses to ship.
    """
    content = _content(pdfwriter.generate(load("wheatstone.kicad_sch")))
    graph = load("rc_divider.kicad_sch")
    ohms = _content(pdfwriter.generate(graph))
    assert "/F3 1 Tf (W) Tj" in ohms, "the ohm sign should be Symbol 'W'"
    assert "/F1" in content


@pytest.mark.parametrize("fixture", VALID_FIXTURES)
def test_output_is_deterministic(load, fixture):
    """DET-1: identical inputs give byte-identical PDFs."""
    assert pdfwriter.generate(load(fixture)) == pdfwriter.generate(
        load(fixture))


def test_empty_graph_is_a_valid_document():
    """REL-1: an empty circuit still produces a file a reader can open."""
    pdf = pdfwriter.generate(CircuitGraph(document=SchematicDocument()))
    assert pdf.startswith(b"%PDF-1.")
    assert pdf.rstrip().endswith(b"%%EOF")
    width, height = _media_box(pdf)
    assert width > 0 and height > 0


def test_arc_conversion_lands_on_its_endpoint():
    """The inductor is drawn with arcs; a bad conversion would leave a gap."""
    segments = pdfwriter._arc_to_cubics((0.0, 0.0), 2.0, 2.0, 0.0,
                                        False, True, (4.0, 0.0))
    assert segments, "a semicircle should produce at least one curve"
    end = segments[-1][-1]
    assert end[0] == pytest.approx(4.0, abs=1e-6)
    assert end[1] == pytest.approx(0.0, abs=1e-6)

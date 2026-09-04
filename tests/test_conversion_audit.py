"""Three-way conversion audit: KiCad input vs .tex drawing vs alt text.

Every other test file checks one stage in isolation.  This one takes a
schematic and tallies the same quantities in all three artefacts -
components, nodes, connections, values, wires and junctions - then
asserts the three tallies agree.  That is what makes it an audit rather
than three separate spot checks: a component that silently vanishes
between the parser and the drawing, or a pin that ends up on the wrong
node in the description, shows up as a disagreement even though each
stage on its own looks healthy.

Runs over the whole fixture corpus by default.  To audit your own
schematic as well::

    python -m pytest tests/test_conversion_audit.py -s ^
        --schematic "C:\\path\\to\\your.kicad_sch"

``--schematic`` is repeatable and needs no manifest entry: every check
here is derived from the file itself, never from a hand-written expected
count, so it works on a schematic the suite has never seen.  Add ``-s``
to see the per-schematic tally table printed by the report test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

from schemaccess import alttext, circuitikz, kicad_parser, netbuilder
from schemaccess.circuitikz import _Transform, _format_value
from schemaccess.model import CircuitGraph, NetKind
from schemaccess.netbuilder import node_names

#: "R1 (resistor): pin 1 (IN) to node 2; pin 2 to ground."
_ALT_COMPONENT = re.compile(r"^(?P<ref>\S+) \((?P<ctype>[^)]*)\): "
                            r"(?P<body>.*)\.$")
_ALT_PIN = re.compile(r"^pin (?P<num>\S+?)(?: \((?P<name>.*)\))? "
                      r"to (?P<node>.+)$")
_ALT_COUNTS = re.compile(r"There (?:is|are) (\d+) elements? "
                         r"and (\d+) nodes?")
#: A named circuitikz node: "\node[npn] (nQ1) at (1,2) {};"
_TEX_NODE = re.compile(r"\\node\[[^\]]*\]\s*\((n\w+)\)")

#: Sections of the .tex that draw components.  Pin coordinates are looked
#: for here rather than in the whole body, so a wire that merely happens
#: to end at a pin cannot be mistaken for the symbol being drawn.
_COMPONENT_SECTIONS = ("Two-terminal components", "Multi-pin components")


# ---------------------------------------------------------------------------
# Tallies
# ---------------------------------------------------------------------------

@dataclass
class _Input:
    """What the parser found in the .kicad_sch file."""
    components: int = 0
    refs: Set[str] = field(default_factory=set)
    nodes: int = 0
    nets: int = 0
    #: (ref, pin number) -> the node that pin sits on
    connections: Dict[Tuple[str, str], str] = field(default_factory=dict)
    #: ref -> value text KiCad shows, so the drawing must show it too
    tex_values: Dict[str, str] = field(default_factory=dict)
    #: ref -> spoken value; the description carries these even when the
    #: field is hidden in KiCad, because hiding is a drawing choice
    alt_values: Dict[str, str] = field(default_factory=dict)
    wire_polylines: int = 0
    wire_segments: int = 0
    junctions: int = 0
    #: ref -> .tex coordinates of the pins that must be drawn
    pin_coords: Dict[str, Set[str]] = field(default_factory=dict)
    #: ref -> every pin coordinate, connected or not
    all_pin_coords: Dict[str, Set[str]] = field(default_factory=dict)


@dataclass
class _Tex:
    """What actually reached the CircuiTikZ drawing."""
    wire_polylines: int = 0
    wire_segments: int = 0
    junction_dots: int = 0
    bipoles: int = 0
    named_nodes: Set[str] = field(default_factory=set)
    body: str = ""
    #: only the lines that draw components
    component_body: str = ""


@dataclass
class _Alt:
    """What actually reached the accessible description."""
    elements: int = 0
    nodes: int = 0
    connections: Dict[Tuple[str, str], str] = field(default_factory=dict)
    text: str = ""

    @property
    def refs(self) -> Set[str]:
        return {ref for ref, _ in self.connections}


@dataclass
class Audit:
    name: str
    graph: CircuitGraph
    source: _Input
    tex: _Tex
    alt: _Alt
    #: refs the drawing demonstrably drew
    drawn_refs: Set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _count_nodes(graph: CircuitGraph) -> int:
    """Nets a reader would call a node: shared by 2+ pins, or ground."""
    return sum(1 for net in graph.nets
               if len(net.pins) >= 2
               or (net.kind == NetKind.GROUND and net.pins))


def _read_input(graph: CircuitGraph) -> _Input:
    doc = graph.document
    names = node_names(graph)
    transform = _Transform(graph)
    tally = _Input(
        components=len(graph.components),
        refs=set(graph.components),
        nodes=_count_nodes(graph),
        nets=len(graph.nets),
    )
    for comp in graph.sorted_components():
        required: Set[str] = set()
        every: Set[str] = set()
        for number, pin in comp.pins.items():
            tally.connections[(comp.ref, number)] = (
                names.get(pin.net_id, "an unconnected point")
                if pin.net_id >= 0 else "an unconnected point")
            every.add(transform.coord(pin.position))
            # A pin sharing a net with another pin is a real connection
            # and must be drawn.  A lone optional pin (an unused op-amp
            # supply) deliberately gets no lead, so it is not required.
            if pin.net_id >= 0 and len(graph.nets[pin.net_id].pins) >= 2:
                required.add(transform.coord(pin.position))
        tally.pin_coords[comp.ref] = required
        tally.all_pin_coords[comp.ref] = every
        shown = _format_value(comp)
        if shown:
            tally.tex_values[comp.ref] = shown
        spoken = alttext.format_value(comp.value, comp.ctype)
        if spoken:
            tally.alt_values[comp.ref] = spoken

    if doc is not None:
        polylines = [w for w in doc.wires if len(w.points) >= 2]
        tally.wire_polylines = len(polylines)
        tally.wire_segments = sum(len(w.points) - 1 for w in polylines)
        tally.junctions = len(doc.junctions)
    return tally


def _sections(body: str) -> Dict[str, List[str]]:
    """Split a circuitikz body on its "% Section" comment headers."""
    out: Dict[str, List[str]] = {}
    current: List[str] = []
    for line in body.splitlines():
        if line.startswith("% "):
            current = out.setdefault(line[2:], [])
        else:
            current.append(line)
    return out


def _read_tex(body: str) -> _Tex:
    section = _sections(body)
    wires = section.get("Wires", [])
    return _Tex(
        wire_polylines=len([l for l in wires if l.startswith("\\draw")]),
        wire_segments=sum(l.count(" -- ") for l in wires),
        junction_dots=sum(l.count("node[circ]")
                          for l in section.get("Junctions", [])),
        bipoles=sum(1 for l in section.get("Two-terminal components", [])
                    if "to[" in l),
        named_nodes={m for l in section.get("Multi-pin components", [])
                     for m in _TEX_NODE.findall(l)},
        body=body,
        component_body="\n".join(line for key in _COMPONENT_SECTIONS
                                 for line in section.get(key, [])),
    )


def _read_alt(text: str) -> _Alt:
    tally = _Alt(text=text)
    counts = _ALT_COUNTS.search(text)
    if counts:
        tally.elements = int(counts.group(1))
        tally.nodes = int(counts.group(2))

    reading = False
    for line in text.splitlines():
        if line.startswith("Connections by component:"):
            reading = True
            continue
        if not reading:
            continue
        match = _ALT_COMPONENT.match(line)
        if not match:
            break                      # end of the listing
        body = match.group("body")
        if body == "no pins":
            continue
        for part in body.split("; "):
            pin = _ALT_PIN.match(part)
            if pin:
                tally.connections[(match.group("ref"), pin.group("num"))] = \
                    pin.group("node")
    return tally


def _drawn_refs(source: _Input, tex: _Tex) -> Set[str]:
    """Refs the drawing provably drew.

    Evidence, in order: every pin that had to be drawn appears at its
    computed coordinate inside a component-drawing command; or the
    component owns a named circuitikz node; or its printed label or one
    of its pin coordinates shows up there.  The fallbacks matter because
    hand-drawn symbols (the JFETs) own no named node, and a part whose
    pins are all dangling requires no leads at all.
    """
    drawn: Set[str] = set()
    for ref in source.refs:
        required = source.pin_coords.get(ref, set())
        if required and all(c in tex.component_body for c in required):
            drawn.add(ref)
        elif f"n{ref}" in tex.named_nodes:
            drawn.add(ref)
        elif f"l={{{ref}}}" in tex.component_body:
            drawn.add(ref)
        elif any(c in tex.component_body
                 for c in source.all_pin_coords.get(ref, set())):
            drawn.add(ref)
    return drawn


_CACHE: Dict[str, Audit] = {}


def audit(path: Path) -> Audit:
    """Convert *path* and tally all three artefacts (cached per path)."""
    key = str(path)
    if key not in _CACHE:
        doc = kicad_parser.parse_file(key)
        graph = netbuilder.build_graph(doc)
        body = circuitikz.generate_body(graph)
        text = alttext.generate(graph, "detailed")
        source = _read_input(graph)
        tex = _read_tex(body)
        _CACHE[key] = Audit(name=path.name, graph=graph, source=source,
                            tex=tex, alt=_read_alt(text),
                            drawn_refs=_drawn_refs(source, tex))
    return _CACHE[key]


def _in_order(value: str, text: str) -> bool:
    """True if *value*'s words appear in order within one sentence.

    The description weaves a value into its sentence - "10 Volt" and
    "at an angle of 90 degrees" end up either side of the component
    phrase - so an exact substring test would reject correct output.
    """
    pattern = ".*?".join(re.escape(word) for word in value.split())
    return any(re.search(pattern, line) for line in text.splitlines())


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

def test_audit_component_count_agrees(schematic: Path) -> None:
    """The same number of components in the file, the drawing and the text."""
    a = audit(schematic)
    assert len(a.drawn_refs) == a.source.components, (
        f"{a.name}: {a.source.components} components in the schematic but "
        f"{len(a.drawn_refs)} drawn; missing "
        f"{sorted(a.source.refs - a.drawn_refs)}")
    assert a.alt.elements == a.source.components, (
        f"{a.name}: {a.source.components} components in the schematic but "
        f"the description announces {a.alt.elements}")
    assert len(a.alt.refs) == a.source.components, (
        f"{a.name}: description details {len(a.alt.refs)} components, "
        f"expected {a.source.components}; missing "
        f"{sorted(a.source.refs - a.alt.refs)}")


def test_audit_every_component_is_described(schematic: Path) -> None:
    """Identity, not just headcount: the same refs on both sides."""
    a = audit(schematic)
    assert a.alt.refs == a.source.refs, (
        f"{a.name}: only in schematic {sorted(a.source.refs - a.alt.refs)}; "
        f"only in description {sorted(a.alt.refs - a.source.refs)}")


def test_audit_every_component_is_drawn(schematic: Path) -> None:
    """Each connected pin is drawn at the coordinate the parser computed."""
    a = audit(schematic)
    missing = {}
    for ref, coords in a.source.pin_coords.items():
        gone = sorted(c for c in coords if c not in a.tex.component_body)
        if gone:
            missing[ref] = gone
    assert not missing, (
        f"{a.name}: pins missing from the drawing at their schematic "
        f"coordinates: {missing}")


def test_audit_node_count_agrees(schematic: Path) -> None:
    """The node count the reader is told matches the connectivity graph."""
    a = audit(schematic)
    assert a.alt.nodes == a.source.nodes, (
        f"{a.name}: {a.source.nodes} nodes in the circuit graph but the "
        f"description announces {a.alt.nodes}")


def test_audit_connections_match_exactly(schematic: Path) -> None:
    """Every pin is described as sitting on the node it really sits on.

    The strongest check in the suite: it compares the whole pin-to-node
    mapping, so a mis-routed pin fails even when every count adds up.
    """
    a = audit(schematic)
    wrong = {key: (want, a.alt.connections.get(key))
             for key, want in a.source.connections.items()
             if a.alt.connections.get(key) != want}
    assert not wrong, (
        f"{a.name}: {len(wrong)} pin(s) described on the wrong node "
        f"(pin: expected, described): {dict(list(wrong.items())[:8])}")
    extra = set(a.alt.connections) - set(a.source.connections)
    assert not extra, (
        f"{a.name}: description invents pins that are not in the "
        f"schematic: {sorted(extra)}")


def test_audit_values_reach_both_outputs(schematic: Path) -> None:
    """No component value is dropped on the way to either output."""
    a = audit(schematic)
    missing_tex = sorted(ref for ref, value in a.source.tex_values.items()
                         if value not in a.tex.body)
    assert not missing_tex, (
        f"{a.name}: values missing from the drawing: "
        f"{ {r: a.source.tex_values[r] for r in missing_tex} }")
    missing_alt = sorted(ref for ref, value in a.source.alt_values.items()
                         if not _in_order(value, a.alt.text))
    assert not missing_alt, (
        f"{a.name}: values missing from the description: "
        f"{ {r: a.source.alt_values[r] for r in missing_alt} }")


def test_audit_wires_and_junctions_agree(schematic: Path) -> None:
    """The drawing reproduces every wire and junction, none invented."""
    a = audit(schematic)
    assert a.tex.wire_polylines == a.source.wire_polylines, (
        f"{a.name}: {a.source.wire_polylines} wires in the schematic, "
        f"{a.tex.wire_polylines} drawn")
    assert a.tex.wire_segments == a.source.wire_segments, (
        f"{a.name}: {a.source.wire_segments} wire segments in the "
        f"schematic, {a.tex.wire_segments} drawn")
    assert a.tex.junction_dots == a.source.junctions, (
        f"{a.name}: {a.source.junctions} junctions in the schematic, "
        f"{a.tex.junction_dots} dots drawn")


def test_audit_report(schematic: Path) -> None:
    """Print the side-by-side tally.  Run with -s to see it.

    Fails if any row disagrees, so it is a check and not only a printout.
    """
    a = audit(schematic)
    src, tex, alt = a.source, a.tex, a.alt
    rows = [
        ("components", src.components, len(a.drawn_refs), alt.elements),
        ("nodes", src.nodes, "-", alt.nodes),
        ("pin connections", len(src.connections), "-", len(alt.connections)),
        ("wires", src.wire_polylines, tex.wire_polylines, "-"),
        ("wire segments", src.wire_segments, tex.wire_segments, "-"),
        ("junctions", src.junctions, tex.junction_dots, "-"),
        ("values shown", len(src.tex_values),
         sum(1 for v in src.tex_values.values() if v in tex.body), "-"),
        ("values spoken", len(src.alt_values), "-",
         sum(1 for v in src.alt_values.values() if _in_order(v, alt.text))),
    ]
    print(f"\n{a.name}")
    print(f"    {'quantity':18} {'.kicad_sch':>11} {'.tex':>8} "
          f"{'alt text':>9}")
    mismatched = []
    for label, in_sch, in_tex, in_alt in rows:
        agree = len({v for v in (in_sch, in_tex, in_alt) if v != "-"}) == 1
        if not agree:
            mismatched.append(label)
        print(f"    {label:18} {in_sch:>11} {in_tex:>8} {in_alt:>9}"
              f"   {'OK' if agree else 'MISMATCH'}")
    assert not mismatched, f"{a.name}: tallies disagree on {mismatched}"

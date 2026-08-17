"""CircuiTikZ generator tests.

* FUN-2  - structural syntax validity for every fixture, plus real
  pdflatex compilation of every generated document (slow).
* FUN-3 / REL-2 - connectivity preservation: bipole endpoints extracted
  from the .tex reconnect exactly like the electrical graph.
* FUN-7  - ground symbols map one-to-one onto ``node[ground]``.
* COM-2  - reference designators and value annotations are preserved,
  none duplicated, none dropped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from conftest import LATEX_AVAILABLE, MANIFEST, VALID_FIXTURES
from schemaccess import circuitikz
from schemaccess.circuitikz import _format_value  # value oracle for COM-2
from schemaccess.model import CircuitGraph, SchematicDocument
from schemaccess.netbuilder import _GROUND_NAMES
from schemaccess.renderer import Renderer

Point = Tuple[float, float]

# ---------------------------------------------------------------------------
# .tex parsing helpers
# ---------------------------------------------------------------------------

_COORD_RE = re.compile(r"\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)")
_BIPOLE_RE = re.compile(
    r"^\\draw \((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\) "
    r"to\[([^\]]*)\] \((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\);$")
_LABEL_OPT_RE = re.compile(r"l=\{([^}]*)\}")
_POWER_NODE_RE = re.compile(
    r"^\\draw \((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\) "
    r"node\[(ground|vcc|vee)\]\{([^}]*)\};$")


def _pt(x: object, y: object) -> Point:
    return (round(float(x), 3), round(float(y), 3))


def _parse_tex(tex: str) -> Tuple[List[List[Point]],
                                  List[Tuple[str, str, Point, Point]],
                                  List[Tuple[str, str, Point]]]:
    """Extract wire polylines, bipoles and power markers from a document.

    Returns ``(wires, bipoles, markers)``: *wires* is a list of
    coordinate polylines, *bipoles* a list of
    ``(ref, options, start, end)`` and *markers* a list of
    ``(style, rail_text, point)`` for ground/vcc/vee flags.
    """
    wires: List[List[Point]] = []
    bipoles: List[Tuple[str, str, Point, Point]] = []
    markers: List[Tuple[str, str, Point]] = []
    for raw in tex.splitlines():
        line = raw.strip()
        if not line.startswith("\\draw"):
            continue
        match = _BIPOLE_RE.match(line)
        if match:
            options = match.group(3)
            label = _LABEL_OPT_RE.search(options)
            bipoles.append((label.group(1) if label else "", options,
                            _pt(match.group(1), match.group(2)),
                            _pt(match.group(4), match.group(5))))
            continue
        power = _POWER_NODE_RE.match(line)
        if power:
            markers.append((power.group(3), power.group(4),
                            _pt(power.group(1), power.group(2))))
            continue
        if "node[" in line or "to[" in line:
            continue
        coords = _COORD_RE.findall(line)
        # A pure wire polyline contains only numeric coordinates.
        if len(coords) >= 2 and line.count("(") == len(coords):
            wires.append([_pt(x, y) for x, y in coords])
    return wires, bipoles, markers


class _UnionFind:
    """Minimal union-find over coordinate tuples."""

    def __init__(self) -> None:
        self._parent: Dict[Point, Point] = {}

    def find(self, p: Point) -> Point:
        self._parent.setdefault(p, p)
        root = p
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[p] != root:
            self._parent[p], p = root, self._parent[p]
        return root

    def union(self, a: Point, b: Point) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


def _on_segment(p: Point, a: Point, b: Point, eps: float = 2e-3) -> bool:
    """True when *p* lies on segment a-b (endpoints included)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    if not (min(ax, bx) - eps <= px <= max(ax, bx) + eps
            and min(ay, by) - eps <= py <= max(ay, by) + eps):
        return False
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    return abs(cross) <= eps * max(abs(bx - ax) + abs(by - ay), 1.0)


def _ground_symbol_count(doc: SchematicDocument) -> int:
    """Number of ground power-symbol instances placed in *doc*."""
    count = 0
    for inst in doc.symbols:
        lib = doc.lib_symbols.get(inst.lib_id)
        if lib is None:
            continue
        if not (lib.is_power or inst.reference.startswith("#PWR")):
            continue
        if inst.reference.startswith("#FLG"):
            continue
        rail = (inst.value or inst.lib_id.split(":", 1)[-1]).strip().lower()
        if rail in _GROUND_NAMES:
            count += 1
    return count


# ---------------------------------------------------------------------------
# FUN-2: syntax validity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_fun2_syntax_validity(name: str, load) -> None:
    """The generated document is a structurally valid LaTeX file."""
    tex = circuitikz.generate(load(name))
    assert tex.count(r"\documentclass") == 1
    assert re.search(r"\\usepackage(\[[^\]]*\])?\{circuitikz\}", tex)
    assert tex.count(r"\begin{document}") == 1
    assert tex.count(r"\end{document}") == 1
    assert tex.count(r"\begin{circuitikz}") == 1
    assert tex.count(r"\end{circuitikz}") == 1
    assert (tex.index(r"\begin{document}")
            < tex.index(r"\begin{circuitikz}")
            < tex.index(r"\end{circuitikz}")
            < tex.index(r"\end{document}"))
    # Balanced braces/brackets (escaped braces removed pairwise first).
    stripped = tex.replace(r"\{", "").replace(r"\}", "")
    assert stripped.count("{") == stripped.count("}"), "unbalanced braces"
    assert tex.count("[") == tex.count("]"), "unbalanced brackets"


@pytest.mark.slow
@pytest.mark.skipif(not LATEX_AVAILABLE,
                    reason="pdflatex is not available on PATH")
@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_fun2_compiles_with_pdflatex(name: str, load, tmp_path) -> None:
    """Every generated .tex compiles with pdflatex (exit code 0)."""
    tex = circuitikz.generate(load(name))
    stem = Path(name).stem
    tex_path = tmp_path / f"{stem}.tex"
    tex_path.write_text(tex, encoding="utf-8")
    render = Renderer()
    pdf = render.render(str(tex_path), "pdf", str(tmp_path))
    assert Path(pdf).is_file()
    assert Path(pdf).stat().st_size > 0


# ---------------------------------------------------------------------------
# FUN-3 / REL-2: connectivity preservation
# ---------------------------------------------------------------------------

_FUN3_FIXTURES = ("rc_divider.kicad_sch", "voltage_divider.kicad_sch",
                  "rlc_series.kicad_sch")


def _map_pins_to_tex(graph: CircuitGraph,
                     bipoles) -> Dict[Tuple[str, str], Point]:
    """Match each two-terminal pin to its .tex endpoint coordinate.

    The coordinate transform preserves x-order and reverses y-order
    (KiCad y points down, TikZ y up), so within one component the pin
    with the smaller schematic x (or, at equal x, the larger schematic
    y) must be the endpoint with the smaller TikZ x (respectively the
    smaller TikZ y).
    """
    mapping: Dict[Tuple[str, str], Point] = {}
    for ref, _options, start, end in bipoles:
        comp = graph.components[ref]
        pins = sorted(comp.pins.values(),
                      key=lambda p: (p.position[0], -p.position[1]))
        ends = sorted((start, end))
        for pin, coord in zip(pins, ends):
            mapping[(ref, pin.number)] = coord
    return mapping


@pytest.mark.parametrize("name", _FUN3_FIXTURES)
def test_fun3_rel2_connectivity_preserved(name: str, load) -> None:
    """Pins sharing a net in the graph are connected in the .tex drawing."""
    graph = load(name)
    tex = circuitikz.generate(graph)
    wires, bipoles, markers = _parse_tex(tex)

    two_terminal = sorted(
        ref for ref, comp in graph.components.items()
        if comp.ctype.is_two_terminal and len(comp.pins) == 2)

    # No duplicates, none dropped: every two-terminal ref appears exactly
    # once as a bipole (diff in both directions via sorted list equality).
    refs_in_tex = sorted(ref for ref, *_ in bipoles)
    assert refs_in_tex == two_terminal

    pin_coord = _map_pins_to_tex(graph, bipoles)

    # Coordinate -> pins mapping: pins sharing an identical coordinate
    # must share a net (the drawing never merges distinct nets).
    net_of = {(ref, number): pin.net_id
              for ref in two_terminal
              for number, pin in graph.components[ref].pins.items()}
    by_coord: Dict[Point, List[Tuple[str, str]]] = {}
    for key, coord in pin_coord.items():
        by_coord.setdefault(coord, []).append(key)
    for coord, keys in sorted(by_coord.items()):
        nets_here = {net_of[k] for k in keys}
        assert len(nets_here) == 1, (
            f"{name}: pins {keys} share coordinate {coord} but belong to "
            f"different nets {nets_here}")

    # Union wires (and points lying on wire segments) into islands.
    uf = _UnionFind()
    segments: List[Tuple[Point, Point]] = []
    for poly in wires:
        for a, b in zip(poly, poly[1:]):
            uf.union(a, b)
            segments.append((a, b))
    points = set(pin_coord.values())
    for poly in wires:
        points.update(poly)
    for _style, _text, p in markers:
        points.add(p)
    for p in sorted(points):
        for a, b in segments:
            if _on_segment(p, a, b):
                uf.union(p, a)
    # Power markers connect logically: every node[ground] is one net, and
    # rail flags with the same style+name (e.g. two '+5V' vcc arrows) are
    # one net, exactly like the power symbols of the source schematic.
    by_rail: Dict[Tuple[str, str], List[Point]] = {}
    for style, text, p in markers:
        key = ("ground", "") if style == "ground" else (style, text)
        by_rail.setdefault(key, []).append(p)
    for key in sorted(by_rail):
        rail_points = by_rail[key]
        for p in rail_points[1:]:
            uf.union(rail_points[0], p)

    # Pins on the same net land in the same drawn island; pins on
    # different nets land in different islands.
    net_roots: Dict[int, Point] = {}
    for net in graph.nets:
        coords = [pin_coord[(ref, number)] for ref, number in net.pins
                  if (ref, number) in pin_coord]
        if not coords:
            continue
        roots = {uf.find(c) for c in coords}
        assert len(roots) == 1, (
            f"{name}: net {net.name} pins {net.pins} are not connected "
            f"in the .tex drawing (coords {coords})")
        net_roots[net.net_id] = roots.pop()
    assert len(set(net_roots.values())) == len(net_roots), (
        f"{name}: distinct nets were merged in the .tex drawing")


# ---------------------------------------------------------------------------
# FUN-7: ground mapping
# ---------------------------------------------------------------------------

def test_fun7_ground_mapping_rc_divider(load) -> None:
    """rc_divider has exactly 3 GND symbols and 3 node[ground] in .tex."""
    graph = load("rc_divider.kicad_sch")
    tex = circuitikz.generate(graph)
    assert _ground_symbol_count(graph.document) == 3
    assert tex.count("node[ground]") == 3


_GROUNDED_FIXTURES = [n for n in VALID_FIXTURES if MANIFEST[n]["ground"]]


@pytest.mark.parametrize("name", _GROUNDED_FIXTURES)
def test_fun7_ground_mapping_matches_symbol_count(name: str, load) -> None:
    """node[ground] count equals the GND power-symbol instance count."""
    graph = load(name)
    tex = circuitikz.generate(graph)
    n_ground_symbols = _ground_symbol_count(graph.document)
    assert n_ground_symbols >= 1
    assert tex.count("node[ground]") == n_ground_symbols


# ---------------------------------------------------------------------------
# COM-2: identifier and value preservation
# ---------------------------------------------------------------------------

def _ref_pattern(ref: str) -> re.Pattern:
    """Match *ref* as a standalone designator token (not nR1, not R10)."""
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(ref)}(?![A-Za-z0-9])")


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_com2_identifiers_and_values_preserved(name: str, load) -> None:
    """Every ref labels the drawing exactly once; no value is dropped."""
    graph = load(name)
    tex = circuitikz.generate(graph)
    body = tex[tex.index(r"\begin{circuitikz}"):]

    counts = {ref: len(_ref_pattern(ref).findall(body))
              for ref in graph.components}
    missing = sorted(ref for ref, n in counts.items() if n == 0)
    duplicated = sorted(ref for ref, n in counts.items() if n > 1)
    assert not missing, f"{name}: refs dropped from .tex: {missing}"
    assert not duplicated, f"{name}: refs duplicated in .tex: {duplicated}"

    for ref in sorted(graph.components):
        comp = graph.components[ref]
        expected = _format_value(comp)
        if expected:
            assert expected in body, (
                f"{name}: value annotation '{expected}' of {ref} "
                f"(raw '{comp.value}') missing from .tex")


# ---------------------------------------------------------------------------
# FUN-4/FUN-5 regression: KiCad 10 Simulation_SPICE symbols
# (sim_spice_opamp.kicad_sch: OPAMP has its '+' input on TOP; the VDC
# source has unnamed pins, polarity only in the Sim.Pins property).
# ---------------------------------------------------------------------------

def _sim_spice_tex() -> str:
    from conftest import load_graph
    return circuitikz.generate(load_graph("sim_spice_opamp.kicad_sch"))


def test_fun4_opamp_noninverting_input_up_matches_kicad():
    """The op amp node must flip so '+' is on top, like the KiCad symbol."""
    tex = _sim_spice_tex()
    assert "op amp, noinv input up" in tex


def test_fun4_source_polarity_from_sim_pins():
    """Unnamed-pin VDC: pin 1 ('+' per Sim.Pins) is the TOP pin, and the
    circuitikz V bipole puts '+' at the SECOND coordinate - so the draw
    direction must run bottom-to-top (second y > first y)."""
    tex = _sim_spice_tex()
    v_lines = [ln for ln in tex.splitlines() if "to[V," in ln]
    assert len(v_lines) == 1
    match = _BIPOLE_RE.match(v_lines[0])
    assert match is not None
    y_first, y_second = float(match.group(2)), float(match.group(5))
    assert y_second > y_first, (
        "V1 drawn with '+' at the wrong end: " + v_lines[0])


def test_fun5_unresolved_kicad_variables_suppressed():
    """${SIM.PARAMS} and friends must never appear in output."""
    tex = _sim_spice_tex()
    assert "SIM.PARAMS" not in tex


def test_fun5_no_leads_to_floating_supply_pins():
    """The op amp's V+/V- pins are unconnected in this schematic, so no
    anchor leads may be drawn for them (they slash across the symbol)."""
    tex = _sim_spice_tex()
    assert ".up)" not in tex
    assert ".down)" not in tex


# ---------------------------------------------------------------------------
# FUN-4 regression: op amps identified by library category / pin signature,
# not by a hard-coded part-number list (opamp_partnumber.kicad_sch uses
# Amplifier_Operational:OP1177AR and has three hidden no-connect pins).
# ---------------------------------------------------------------------------

def test_fun4_unlisted_opamp_part_number_classified():
    """An op amp whose part number is not in any lookup table must still be
    recognised - via its KiCad library category and its +/- pin names."""
    from conftest import load_graph
    from schemaccess.model import ComponentType

    graph = load_graph("opamp_partnumber.kicad_sch")
    assert graph.components["U1"].ctype is ComponentType.OPAMP


def test_fun4_hidden_no_connect_pins_dropped():
    """Hidden no-connect pins (SOIC-8 op amp pins 1/5/8) carry no
    connectivity and must not appear as pins or spurious nets."""
    from conftest import load_graph

    graph = load_graph("opamp_partnumber.kicad_sch")
    assert sorted(graph.components["U1"].pins) == ["2", "3", "4", "6", "7"]


def test_fun5_unlisted_opamp_drawn_as_op_amp_not_box():
    """It must render as a circuitikz op amp, never the rectangle fallback."""
    from conftest import load_graph

    tex = circuitikz.generate(load_graph("opamp_partnumber.kicad_sch"))
    assert "op amp" in tex
    assert "rectangle" not in tex


def test_fun6_opamp_supply_leads_are_vertical():
    """Connected V+/V- leads run straight down/up to the body edge (like
    KiCad's pin leads) instead of crossing the triangle."""
    from conftest import load_graph

    tex = circuitikz.generate(load_graph("opamp_partnumber.kicad_sch"))
    # Only the multi-pin section: everything else is ordinary wiring.
    all_lines = tex.splitlines()
    start = all_lines.index("% Multi-pin components") + 1
    section: List[str] = []
    for line in all_lines[start:]:
        if line.startswith("% "):
            break
        section.append(line)
    leads = [ln for ln in section if "nU1" not in ln and ln.startswith("\\draw")]
    assert len(leads) == 2, f"expected 2 supply leads, got {leads}"
    for line in leads:
        pts = _COORD_RE.findall(line)
        assert len(pts) == 2 and pts[0][0] == pts[1][0], (
            f"supply lead is not vertical: {line}")


_SCALE_RE = re.compile(r"xscale=(-?[\d.]+), yscale=([\d.]+)")


def test_fun4_opamp_uses_circuitikz_shape_undistorted():
    """The symbol is circuitikz's own 'op amp'.  Any scaling is UNIFORM -
    the triangle keeps its proportions and is never stretched in one axis
    the way matching the pin spacing exactly would require."""
    for fixture in ("sim_spice_opamp.kicad_sch", "opamp_inverting.kicad_sch",
                    "opamp_partnumber.kicad_sch"):
        from conftest import load_graph

        tex = circuitikz.generate(load_graph(fixture))
        line = next(ln for ln in tex.splitlines() if "op amp" in ln)
        assert "anchor=" not in line, f"{fixture}: {line}"
        match = _SCALE_RE.search(line)
        if match is None:
            continue  # emitted at natural size - nothing to check
        xs, ys = abs(float(match.group(1))), float(match.group(2))
        assert abs(xs - ys) < 1e-6, f"{fixture}: non-uniform scale {line}"
        assert 0.5 <= ys <= 2.5, f"{fixture}: extreme scale {line}"


def test_fun4_standard_kicad_opamp_needs_no_scaling():
    """SCALE maps KiCad's grid onto circuitikz's own proportions, so a
    standard symbol (inputs 2.54 mm off centre) draws at exactly natural
    size: no scale transform is emitted at all."""
    from conftest import load_graph

    for fixture in ("sim_spice_opamp.kicad_sch", "opamp_inverting.kicad_sch",
                    "opamp_partnumber.kicad_sch"):
        tex = circuitikz.generate(load_graph(fixture))
        line = next(ln for ln in tex.splitlines() if "op amp" in ln)
        assert "scale" not in line, f"{fixture} should be natural size: {line}"


def test_scale_matches_circuitikz_natural_geometry():
    """The mm->TikZ factor is pinned to circuitikz's op-amp input anchor
    offset; changing it would silently resize every drawing."""
    from schemaccess.circuitikz import _OPAMP_INPUT_HALF

    assert circuitikz.SCALE * 2.54 == pytest.approx(_OPAMP_INPUT_HALF)


def test_fun6_opamp_input_leads_are_straight():
    """After the uniform scaling the anchors sit at the KiCad pin heights,
    so input and output leads are plain straight segments (no jog)."""
    for fixture in ("sim_spice_opamp.kicad_sch", "opamp_inverting.kicad_sch",
                    "opamp_partnumber.kicad_sch"):
        from conftest import load_graph

        tex = circuitikz.generate(load_graph(fixture))
        signal = [ln for ln in tex.splitlines()
                  if ln.startswith("\\draw (nU1.")
                  and any(a in ln for a in (".+)", ".-)", ".out)"))]
        assert signal, f"{fixture}: no op-amp signal leads emitted"
        for line in signal:
            assert " -- " in line, f"{fixture}: lead needs a jog: {line}"


def test_fun6_opamp_straight_leads_are_truly_horizontal():
    """A '--' lead is only safe when the anchor really sits at the pin's
    height.  Recompute each anchor's y from the emitted node placement and
    check it against the lead's endpoint, so a diagonal cannot slip in."""
    from schemaccess.circuitikz import _OPAMP_INPUT_HALF

    for fixture in ("sim_spice_opamp.kicad_sch", "opamp_inverting.kicad_sch",
                    "opamp_partnumber.kicad_sch"):
        from conftest import load_graph

        tex = circuitikz.generate(load_graph(fixture))
        node_line = next(ln for ln in tex.splitlines() if "op amp" in ln)
        node_y = float(_COORD_RE.findall(node_line)[-1][1])
        scale_match = _SCALE_RE.search(node_line)
        scale = float(scale_match.group(2)) if scale_match else 1.0
        noinv_up = "noinv input up" in node_line
        offsets = {
            "+": _OPAMP_INPUT_HALF * scale * (1 if noinv_up else -1),
            "-": _OPAMP_INPUT_HALF * scale * (-1 if noinv_up else 1),
            "out": 0.0,
        }
        for line in tex.splitlines():
            if not line.startswith("\\draw (nU1.") or " -- " not in line:
                continue
            anchor = line.split("nU1.", 1)[1].split(")", 1)[0]
            if anchor not in offsets:
                continue
            end_y = float(_COORD_RE.findall(line)[-1][1])
            assert abs((node_y + offsets[anchor]) - end_y) < 0.01, (
                f"{fixture}: '{anchor}' lead is diagonal: {line}")


# ---------------------------------------------------------------------------
# Junction-dot option: dots are drawn by default and can be switched off
# without changing any wiring or component placement.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_junction_dots_present_by_default(name, load) -> None:
    """Every junction in the source appears as a node[circ] marker."""
    graph = load(name)
    tex = circuitikz.generate(graph)
    expected = len(graph.document.junctions) if graph.document else 0
    assert tex.count("node[circ]") == expected


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_junction_dots_can_be_disabled(name, load) -> None:
    """junction_dots=False removes every dot and nothing else."""
    graph = load(name)
    with_dots = circuitikz.generate(graph).splitlines()
    without = circuitikz.generate(graph, junction_dots=False).splitlines()
    assert "node[circ]" not in "\n".join(without)
    # The remaining document is identical line for line.
    kept = [ln for ln in with_dots
            if "node[circ]" not in ln and ln != "% Junctions"]
    assert kept == without


def test_junction_dot_option_flows_through_pipeline(tmp_path) -> None:
    """The pipeline option reaches the generated .tex file."""
    from conftest import FIXTURES_DIR
    from schemaccess.pipeline import PipelineOptions, run_pipeline

    source = str(FIXTURES_DIR / "rc_divider.kicad_sch")
    for dots in (True, False):
        out = tmp_path / f"dots_{dots}"
        result = run_pipeline(PipelineOptions(
            input_path=source, output_dir=str(out),
            generate_alt_text=False, generate_image=True,
            export_format="pdf", junction_dots=dots))
        assert ("node[circ]" in result.tikz_code) is dots

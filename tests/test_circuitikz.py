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
    """Match *ref* as a standalone designator token.

    Excludes ``nR1`` (TikZ node names), ``R10`` (longer designators) and
    ``nT1.B1`` (anchor references - a dot before the token means it is an
    anchor on some other node, not a printed label).
    """
    return re.compile(
        rf"(?<![A-Za-z0-9.]){re.escape(ref)}(?![A-Za-z0-9])")


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_com2_identifiers_and_values_preserved(name: str, load) -> None:
    """Every ref KiCad shows labels the drawing exactly once; no value is
    dropped.  Fields whose "Show" box is unchecked in KiCad are expected to
    be absent - that is field visibility, not data loss - and the alt text
    still carries them (see test_com2_hidden_refs_still_in_alt_text)."""
    graph = load(name)
    tex = circuitikz.generate(graph)
    body = tex[tex.index(r"\begin{circuitikz}"):]

    counts = {ref: len(_ref_pattern(ref).findall(body))
              for ref in graph.components}
    shown = {ref for ref, comp in graph.components.items()
             if comp.shows("Reference")}
    missing = sorted(ref for ref, n in counts.items()
                     if n == 0 and ref in shown)
    hidden_but_drawn = sorted(ref for ref, n in counts.items()
                              if n > 0 and ref not in shown)
    duplicated = sorted(ref for ref, n in counts.items() if n > 1)
    assert not missing, f"{name}: refs dropped from .tex: {missing}"
    assert not hidden_but_drawn, (
        f"{name}: refs drawn despite being hidden in KiCad: "
        f"{hidden_but_drawn}")
    assert not duplicated, f"{name}: refs duplicated in .tex: {duplicated}"

    for ref in sorted(graph.components):
        comp = graph.components[ref]
        expected = _format_value(comp)
        if expected:
            assert expected in body, (
                f"{name}: value annotation '{expected}' of {ref} "
                f"(raw '{comp.value}') missing from .tex")


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_com2_hidden_refs_still_in_alt_text(name: str, load) -> None:
    """Hiding a field is a drawing choice; the accessible description must
    still name every component, hidden fields included."""
    from schemaccess import alttext

    graph = load(name)
    text = alttext.generate(graph, "detailed")
    for ref in graph.components:
        assert _ref_pattern(ref).search(text), (
            f"{name}: {ref} missing from the alt text")


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


# ---------------------------------------------------------------------------
# Field visibility: KiCad's per-field "Show" checkbox is honoured in the
# drawing (hidden_fields.kicad_sch hides R1's Reference and R2's Value).
# ---------------------------------------------------------------------------

def test_fun5_hidden_reference_and_value_not_drawn():
    """A field whose Show box is unchecked in KiCad is not drawn."""
    from conftest import load_graph

    tex = circuitikz.generate(load_graph("hidden_fields.kicad_sch"))
    bipoles = [ln for ln in tex.splitlines() if "to[" in ln]
    assert len(bipoles) == 2, bipoles
    r1 = next(ln for ln in bipoles if "10k" in ln)
    r2 = next(ln for ln in bipoles if "R2" in ln)
    # R1: Reference hidden -> value only, no l= label.
    assert "l={R1}" not in r1 and "a={10k}" in r1, r1
    # R2: Value hidden -> reference only, no a= annotation.
    assert "l={R2}" in r2 and "22k" not in r2, r2


def test_fun5_hidden_fields_parsed_in_both_kicad_syntaxes():
    """KiCad 7+ writes '(hide yes)' on the property; KiCad 6 wrote it inside
    '(effects ...)'.  Both must be recognised."""
    from conftest import load_graph

    graph = load_graph("hidden_fields.kicad_sch")
    assert graph.components["R1"].hidden_properties == {"Reference"}
    assert graph.components["R2"].hidden_properties == {"Value"}
    assert graph.components["R1"].shows("Value")
    assert not graph.components["R1"].shows("Reference")


def test_fun5_multi_pin_label_omitted_when_both_fields_hidden():
    """With Reference and Value both hidden there is no caption node at all
    (rather than an empty one)."""
    from schemaccess import circuitikz as ctz
    from conftest import load_graph

    graph = load_graph("opamp_partnumber.kicad_sch")
    comp = graph.components["U1"]
    comp.hidden_properties = {"Reference", "Value"}
    tex = ctz.generate(graph)
    assert "op amp" in tex
    assert "anchor=south" not in tex, "empty caption node was still emitted"


def test_rel3_hidden_fields_do_not_change_alt_text():
    """Hiding a label affects the drawing only: the description still names
    every component, because a screen-reader user needs the identifier."""
    from schemaccess import alttext
    from conftest import load_graph

    text = alttext.generate(load_graph("hidden_fields.kicad_sch"), "standard")
    assert "R1" in text and "R2" in text


# ---------------------------------------------------------------------------
# FUN-4/FUN-5: transistors, transformers and controlled sources get their
# real circuitikz symbols (mixed_symbols.kicad_sch covers all of them).
# ---------------------------------------------------------------------------

def _mixed():
    from conftest import load_graph
    return load_graph("mixed_symbols.kicad_sch")


def test_fun4_transistor_families_classified():
    """NPN, PNP and JFET are told apart from the library name plus the
    symbol's description/keywords - not from the reference prefix, which
    is 'Q' for all of them."""
    from schemaccess.model import ComponentType

    comps = _mixed().components
    assert comps["Q1"].ctype is ComponentType.TRANSISTOR_NPN
    assert comps["Q2"].ctype is ComponentType.TRANSISTOR_PNP
    assert comps["Q3"].ctype is ComponentType.NJFET
    assert comps["T1"].ctype is ComponentType.TRANSFORMER
    assert comps["B1"].ctype is ComponentType.CONTROLLED_SOURCE


def test_fun5_real_symbols_not_generic_boxes():
    """Each of these draws as its circuitikz element, never the fallback
    rectangle, and no pin is left without an anchor."""
    graph = _mixed()
    tex = circuitikz.generate(graph)
    for key in ("npn", "pnp", "transformer core", "cvsource"):
        assert key in tex, f"{key} missing from generated .tex"
    # Exactly the components with no dedicated symbol may fall back to a
    # labelled rectangle - and none of the five above is among them.
    fallback = _expected_fallbacks(graph)
    assert not ({"Q1", "Q2", "Q3", "Q4", "T1", "B1"} & fallback), (
        f"a supported symbol fell back to a box: {sorted(fallback)}")
    assert tex.count("rectangle") == len(fallback), (
        f"expected {len(fallback)} fallback boxes for {sorted(fallback)}")
    assert not [w for w in graph.warnings if "no " in w and "anchor" in w], (
        f"unanchored pins: {graph.warnings}")


def _expected_fallbacks(graph) -> set:
    """Refs the generator legitimately draws as a labelled rectangle."""
    from schemaccess.model import ComponentType

    boxed = {ComponentType.UNKNOWN, ComponentType.IC,
             ComponentType.CONNECTOR, ComponentType.POWER_FLAG}
    out = set()
    for ref, comp in graph.components.items():
        if comp.ctype in boxed or len(comp.pins) < 2:
            out.add(ref)
        elif comp.ctype is ComponentType.TRANSFORMER and len(comp.pins) != 4:
            out.add(ref)   # multi-winding: no four-tap circuitikz shape
        elif (comp.ctype in _BIPOLE_ONLY and len(comp.pins) != 2
                and comp.ctype is not ComponentType.POTENTIOMETER):
            out.add(ref)
    return out


def _bipole_only():
    from schemaccess.circuitikz import _BIPOLE_KEYS

    return set(_BIPOLE_KEYS)


_BIPOLE_ONLY = _bipole_only()


def test_fun6_transistor_and_transformer_leads_orthogonal():
    """Every lead from these nodes is straight or right-angled."""
    tex = circuitikz.generate(_mixed())
    leads = [ln for ln in tex.splitlines()
             if ln.startswith("\\draw (n")
             and any(f"(n{r}." in ln for r in ("Q1", "Q2", "Q3", "T1"))]
    assert leads, "no transistor/transformer leads emitted"
    for line in leads:
        assert (" -- " in line or " -| " in line or " |- " in line), line


def test_fun4_transformer_taps_match_kicad_sides():
    """The transformer's four taps map to the correct winding and end:
    left/right by x, upper/lower by y."""
    graph = _mixed()
    tex = circuitikz.generate(graph)
    node = next(ln for ln in tex.splitlines() if "transformer core" in ln)
    cx, cy = (float(v) for v in _COORD_RE.findall(node)[-1])
    seen = {}
    for line in tex.splitlines():
        if not line.startswith("\\draw (nT1."):
            continue
        anchor = line.split("nT1.", 1)[1].split(")", 1)[0]
        x, y = (float(v) for v in _COORD_RE.findall(line)[-1])
        seen[anchor] = (x, y)
    assert set(seen) == {"A1", "A2", "B1", "B2"}, seen
    for anchor, (x, y) in seen.items():
        assert (x < cx) == anchor.startswith("A"), f"{anchor} on wrong side"
        assert (y > cy) == anchor.endswith("1"), f"{anchor} on wrong end"


def test_fun6_transistor_leads_are_straight():
    """Every bipolar transistor lead is a plain straight segment: the node
    is centred on the channel pins' x and placed vertically from the base
    pin, so channel leads run down and the base lead runs across.  (JFETs
    are drawn directly - see the JFET tests below.)"""
    tex = circuitikz.generate(_mixed())
    for ref in ("Q1", "Q2"):
        leads = [ln for ln in tex.splitlines()
                 if ln.startswith(f"\\draw (n{ref}.")]
        assert len(leads) == 3, f"{ref}: expected 3 leads, got {leads}"
        for line in leads:
            assert " -- " in line, f"{ref}: lead needs a jog: {line}"


def test_fun4_jfet_gate_enters_on_the_centre_line():
    """A JFET is drawn from KiCad's geometry rather than circuitikz's shape
    (whose gate sits a third of the way down the channel).  Its gate lead
    must be horizontal and on the drain/source midline."""
    graph = _mixed()
    tex = circuitikz.generate(graph)
    for ref in ("Q3", "Q4"):
        comp = graph.components[ref]
        pins = {p.name.strip()[:1].upper(): p for p in comp.pins.values()}
        gate = _tex_point(tex, pins["G"].position, graph)
        drain = _tex_point(tex, pins["D"].position, graph)
        source = _tex_point(tex, pins["S"].position, graph)
        midline = (drain[1] + source[1]) / 2.0
        assert abs(gate[1] - midline) < 1e-6, (
            f"{ref}: gate pin is not on the D/S midline")
        # The lead reaching that pin must be perfectly horizontal.
        target = f"-- {_xy_str(gate)};"
        lead = [ln for ln in tex.splitlines() if ln.endswith(target)]
        assert lead, f"{ref}: no lead drawn to the gate pin"
        pts = _COORD_RE.findall(lead[0])
        assert abs(float(pts[-2][1]) - gate[1]) < 1e-6, (
            f"{ref}: gate lead is not horizontal: {lead[0]}")


def test_fun5_jfet_drawn_with_kicad_body():
    """The JFET body is KiCad's picture minus the enclosing circle (which
    KiCad draws but circuitikz's transistors do not): a thick channel bar,
    a filled gate arrow and stepped drain/source leads."""
    tex = circuitikz.generate(_mixed())
    assert "line width=0.8pt" in tex, "no thick channel bar"
    assert "\\fill (" in tex, "no filled gate arrow"
    # No stroked circle anywhere: KiCad encircles its FETs, we do not.
    # (Filled circles are polarity dots, drawn with \fill, and are fine.)
    assert not re.search(r"\\draw[^\n]*\bcircle \(", tex), (
        "JFET should not be encircled")
    assert "njfet" not in tex and "pjfet" not in tex, (
        "circuitikz's off-centre JFET shape is still being used")


def _tex_point(tex: str, position, graph):
    """Transform a KiCad pin position the way the generator does."""
    from schemaccess.circuitikz import _Transform, _fmt

    point = _Transform(graph).point(position)
    return (float(_fmt(point[0])), float(_fmt(point[1])))


def _xy_str(point) -> str:
    from schemaccess.circuitikz import _fmt

    return f"({_fmt(point[0])},{_fmt(point[1])})"


def test_fun4_p_type_transistor_flipped_to_match_kicad():
    """circuitikz's p-type shapes carry the collector/drain at the bottom;
    KiCad's Q2 has it on top, so the node must be flipped vertically."""
    tex = circuitikz.generate(_mixed())
    pnp = next(ln for ln in tex.splitlines() if "pnp" in ln)
    assert "yscale=-1" in pnp, pnp
    npn = next(ln for ln in tex.splitlines() if "[npn" in ln)
    assert "yscale" not in npn, npn


# ---------------------------------------------------------------------------
# Polarity / winding-phase dots: KiCad draws these as filled circles in the
# symbol graphics (mixed_symbols.kicad_sch has a custom L_Polarized).
# ---------------------------------------------------------------------------

def test_fun5_polarity_dot_is_drawn():
    """A filled circle in the symbol art is a polarity dot and must appear
    in the drawing, positioned where KiCad puts it."""
    graph = _mixed()
    dotted = {ref for ref, comp in graph.components.items() if comp.dots}
    assert dotted, "no polarity dot was captured from the symbol graphics"

    tex = circuitikz.generate(graph)
    assert "% Polarity dots" in tex
    for ref in sorted(dotted):
        for position, _radius in graph.components[ref].dots:
            point = _tex_point(tex, position, graph)
            assert f"\\fill {_xy_str(point)} circle (" in tex, (
                f"{ref}: dot at {position} missing from the .tex")


def test_fun5_only_filled_circles_become_dots():
    """Outline-only circles are body art (a source's envelope, a JFET's
    circle) and must NOT be mistaken for polarity dots."""
    from schemaccess import kicad_parser
    from conftest import FIXTURES_DIR

    doc = kicad_parser.parse_file(
        str(FIXTURES_DIR / "mixed_symbols.kicad_sch"))
    # The behavioural source and the op amps have unfilled body circles.
    for lib_id, lib in doc.lib_symbols.items():
        for dot in lib.dots:
            assert dot.radius <= 1.0, (
                f"{lib_id}: body outline picked up as a dot (r={dot.radius})")


def test_fun4_dot_follows_symbol_rotation():
    """A dot is a point in library space, so it must be transformed by the
    instance's rotation and mirroring exactly like a pin is."""
    from schemaccess.model import SymbolInstance

    inst = SymbolInstance(uuid="u", lib_id="lib:L", x=100.0, y=100.0,
                          angle=90.0)
    # 90 degrees CCW in library space, then Y flipped for schematic space.
    assert inst.lib_point(0.0, 2.54) == (97.46, 100.0)
    inst.angle = 0.0
    inst.mirror = "y"
    assert inst.lib_point(1.27, 0.0) == (98.73, 100.0)

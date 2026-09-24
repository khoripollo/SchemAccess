"""Loop currents (--loops)."""

from __future__ import annotations

import importlib.util
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from conftest import FIXTURES_DIR, LATEX_AVAILABLE, VALID_FIXTURES
from schemaccess import (alttext, circuitikz, cli, loops, pdfwriter,
                         svgpreview)
from schemaccess.model import CircuitGraph, ComponentType
from schemaccess.pipeline import PipelineOptions, run_pipeline
from schemaccess.renderer import Renderer

Point = Tuple[float, float]

_WEB_TEX = Path(__file__).resolve().parents[1] / "web" / "latex" / "tex"


def _meshes(load, name: str) -> loops.MeshResult:
    return loops.find_meshes(load(name))


def _summary(found: loops.MeshResult):
    return [(m.name, m.sense, m.refs) for m in found.meshes]


def test_loop1_two_loop_network(load) -> None:
    found = _meshes(load, "loops_two.kicad_sch")
    assert found.ok, found.reason
    assert _summary(found) == [
        ("i1", "clockwise", ["V1", "R1", "R2"]),
        ("i2", "clockwise", ["R2", "R3", "R4"])]
    assert [(ref, a.name, b.name) for ref, a, b in found.shared()] == [
        ("R2", "i1", "i2")]
    assert not loops.same_way(*found.meshes)


def test_loop1_second_source_turns_its_loop_the_other_way(load) -> None:
    found = _meshes(load, "loops_two_sources.kicad_sch")
    assert _summary(found) == [
        ("i1", "clockwise", ["V1", "R1", "R2"]),
        ("i2", "counterclockwise", ["R2", "V2", "R3"])]
    assert loops.same_way(*found.meshes)


def test_loop1_source_in_the_shared_branch(load) -> None:
    found = _meshes(load, "loops_middle_source.kicad_sch")
    assert _summary(found) == [
        ("i1", "counterclockwise", ["R1", "V1", "R2"]),
        ("i2", "clockwise", ["V1", "R3", "R4"])]


def test_loop1_current_source_sets_the_direction(load) -> None:
    found = _meshes(load, "loops_current_source.kicad_sch")
    assert [(m.name, m.sense) for m in found.meshes] == [
        ("i1", "counterclockwise"), ("i2", "counterclockwise")]


def test_loop1_ladder_numbers_left_to_right(load) -> None:
    found = _meshes(load, "loops_three.kicad_sch")
    assert [m.refs for m in found.meshes] == [
        ["V1", "R1", "R2"], ["R2", "R3", "R4"], ["R4", "R5", "R6"]]
    xs = [m.centre[0] for m in found.meshes]
    assert xs == sorted(xs)


def test_loop1_grid_numbers_row_by_row(load) -> None:
    found = _meshes(load, "loops_grid.kicad_sch")
    assert _summary(found) == [
        ("i1", "clockwise", ["V1", "R5", "R2", "R7"]),
        ("i2", "clockwise", ["R2", "R6", "R4", "R8"]),
        ("i3", "clockwise", ["R1", "R7", "R3"]),
        ("i4", "clockwise", ["R3", "R8", "R9"])]
    top, bottom = found.meshes[:2], found.meshes[2:]
    assert max(m.centre[1] for m in top) < min(m.centre[1] for m in bottom)
    assert {r for r, _a, _b in found.shared()} == {"R2", "R3", "R7", "R8"}


@pytest.mark.parametrize("name,expected", [
    ("earth_blank_value.kicad_sch", [["V1", "R1"]]),
    ("led_battery.kicad_sch", [["BT1", "R1", "D1"]]),
    ("rlc_series.kicad_sch", [["V1", "R1", "L1", "C1"]]),
    ("rc_divider.kicad_sch", [["V1", "R1", "R2"], ["R2", "C1"]]),
    ("wheatstone.kicad_sch", [["V1", "R1", "R2"], ["R1", "R3", "R4", "R2"]]),
])
def test_loop1_existing_circuits(name: str, expected, load) -> None:
    found = _meshes(load, name)
    assert found.ok, found.reason
    assert [m.refs for m in found.meshes] == expected
    assert all(m.clockwise for m in found.meshes)


def _independent_loops(graph: CircuitGraph) -> int:
    parent: Dict[int, int] = {}

    def find(n: int) -> int:
        parent.setdefault(n, n)
        while parent[n] != n:
            n = parent[n]
        return n

    branches = 0
    for comp in graph.components.values():
        if len(comp.pins) != 2:
            continue
        a, b = (pin.net_id for pin in comp.pins.values())
        branches += 1
        parent[find(a)] = find(b)
    nodes = list(parent)
    return branches - len(nodes) + len({find(n) for n in nodes})


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_loop2_loops_found_match_the_circuit(name: str, load) -> None:
    graph = load(name)
    found = loops.find_meshes(graph)
    if found.ok:
        assert len(found.meshes) == _independent_loops(graph)
        assert len(found.meshes) <= loops.MAX_LOOPS
        numbers = [m.index for m in found.meshes]
        assert numbers == list(range(1, len(numbers) + 1))
    else:
        assert found.reason and not found.meshes


def test_loop2_deterministic(load) -> None:
    first = _meshes(load, "loops_grid.kicad_sch")
    again = _meshes(load, "loops_grid.kicad_sch")
    assert [(m.refs, m.centre, m.radius, m.clockwise)
            for m in first.meshes] == \
        [(m.refs, m.centre, m.radius, m.clockwise) for m in again.meshes]


@pytest.mark.parametrize("name,reason", [
    ("loops_seven.kicad_sch", "7 loops"),
    ("power_symbols.kicad_sch", "5 loops"),
    ("big_200.kicad_sch", "99 loops"),
    ("loops_crossing.kicad_sch", "crosses itself without a junction"),
    ("loops_scattered_ground.kicad_sch", "not in one row"),
    ("opamp_inverting.kicad_sch", "two-terminal parts"),
    ("mixed_symbols.kicad_sch", "two-terminal parts"),
    ("hier_parent.kicad_sch", "several sheets"),
    ("voltage_divider.kicad_sch", "no closed loop"),
    ("hidden_fields.kicad_sch", "no source"),
    ("loops_undecidable.kicad_sch", "V1 and V2 push the loop currents "
                                    "opposite ways"),
])
def test_loop3_refused_with_a_reason(name: str, reason: str, load) -> None:
    found = _meshes(load, name)
    assert not found.ok and not found.meshes
    assert reason in found.reason


def test_loop3_undecidable_names_what_blocks_the_calculation(load) -> None:
    assert "D1 is a diode" in _meshes(load,
                                      "loops_undecidable.kicad_sch").reason


def test_loop3_limit_is_four() -> None:
    assert loops.MAX_LOOPS == 4


def _distance(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy)
                     / max(dx * dx + dy * dy, 1e-12)))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _inside(p: Point, polygon: List[Point]) -> bool:
    inside, j = False, len(polygon) - 1
    for i in range(len(polygon)):
        (xi, yi), (xj, yj) = polygon[i], polygon[j]
        if (yi > p[1]) != (yj > p[1]) and \
                p[0] < (xj - xi) * (p[1] - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


_DRAWN = ["loops_two.kicad_sch", "loops_three.kicad_sch",
          "loops_grid.kicad_sch", "loops_two_sources.kicad_sch",
          "loops_middle_source.kicad_sch", "loops_current_source.kicad_sch",
          "earth_blank_value.kicad_sch", "rc_divider.kicad_sch",
          "wheatstone.kicad_sch", "led_battery.kicad_sch",
          "rlc_series.kicad_sch"]


@pytest.mark.parametrize("name", _DRAWN)
def test_loop4_arrows_sit_clear_inside_their_windows(name: str, load) -> None:
    graph = load(name)
    found = loops.find_meshes(graph)
    assert found.ok, found.reason
    segments = [seg for wire in graph.document.wires
                for seg in zip(wire.points, wire.points[1:])]
    segments += [tuple(pin.position for pin in comp.pins.values())
                 for comp in graph.components.values()]
    for mesh in found.meshes:
        assert _inside(mesh.centre, mesh.boundary), mesh.name
        for a, b in segments:
            assert _distance(mesh.centre, a, b) >= mesh.radius, (
                f"{mesh.name} overlaps the drawing")
    for i, first in enumerate(found.meshes):
        for second in found.meshes[i + 1:]:
            gap = math.hypot(first.centre[0] - second.centre[0],
                             first.centre[1] - second.centre[1])
            assert gap >= first.radius + second.radius


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_loop5_outputs_unchanged_without_the_option(name: str, load) -> None:
    graph = load(name)
    assert circuitikz.generate(graph) == circuitikz.generate(graph,
                                                             loops=False)
    assert "arc[" not in circuitikz.generate(graph)
    for level in ("short", "standard", "detailed"):
        assert alttext.generate(graph, level) == \
            alttext.generate(graph, level, loops=False)
    assert svgpreview.generate(graph) == svgpreview.generate(graph,
                                                             loops=False)


def test_loop5_latex_draws_one_arrow_per_loop(load) -> None:
    tex = circuitikz.generate(load("loops_grid.kicad_sch"), loops=True)
    assert tex.count("arc[start angle=120, end angle=-150") == 4
    labels = re.findall(r"\{\$i_\{(\d+)\}\$\}", tex)
    assert labels == ["1", "2", "3", "4"]
    stripped = tex.replace(r"\{", "").replace(r"\}", "")
    assert stripped.count("{") == stripped.count("}")
    assert tex.count("[") == tex.count("]")


def test_loop5_latex_turns_each_arrow_its_own_way(load) -> None:
    tex = circuitikz.generate(load("loops_two_sources.kicad_sch"),
                              loops=True)
    arcs = re.findall(r"arc\[start angle=(-?\d+), end angle=(-?\d+)", tex)
    assert arcs == [("120", "-150"), ("60", "330")]


def test_loop5_refused_circuit_draws_no_arrows(load) -> None:
    graph = load("opamp_inverting.kicad_sch")
    assert circuitikz.generate(graph, loops=True) == \
        circuitikz.generate(graph)


def test_loop5_previews_draw_the_loops(load) -> None:
    graph = load("loops_two_sources.kicad_sch")
    svg = svgpreview.generate(graph, loops=True)
    arcs = re.findall(r'<path d="M [^"]* A [\d.]+ [\d.]+ 0 1 ([01]) [^"]*" '
                      r'class="sa-loop"/>', svg)
    assert arcs == ["1", "0"]
    assert svg.count('class="sa-loop sa-filled"') == 2
    assert ">i1<" in svg and ">i2<" in svg
    assert pdfwriter.generate(graph, loops=True) != pdfwriter.generate(graph)


def test_loop5_description(load) -> None:
    graph = load("loops_two.kicad_sch")
    lines = alttext.generate(graph, "standard", loops=True).splitlines()
    assert lines[-4:] == [
        "The drawing marks 2 loop currents for mesh analysis, i1 and i2, "
        "each drawn the way its current flows.",
        "i1 flows clockwise through V1, R1 and R2.",
        "i2 flows clockwise through R2, R3 and R4.",
        "R2 is shared by i1 and i2, which pass through it opposite ways, so "
        "its current is i1 minus i2, taken in the direction of i1."]
    two = alttext.generate(load("loops_two_sources.kicad_sch"), "standard",
                           loops=True).splitlines()
    assert two[-3:] == [
        "i1 flows clockwise through V1, R1 and R2.",
        "i2 flows counterclockwise through R2, V2 and R3.",
        "R2 is shared by i1 and i2, which pass through it the same way, so "
        "its current is i1 plus i2."]
    short = alttext.generate(graph, "short", loops=True).splitlines()
    assert short[-1].startswith("The drawing marks 2 loop currents")
    refused = alttext.generate(load("loops_seven.kicad_sch"), "short",
                               loops=True)
    assert refused.splitlines()[-1].startswith("Loop currents are not shown")


@pytest.mark.slow
@pytest.mark.skipif(not LATEX_AVAILABLE,
                    reason="pdflatex is not available on PATH")
@pytest.mark.parametrize("name", ["loops_two.kicad_sch",
                                  "loops_two_sources.kicad_sch",
                                  "loops_grid.kicad_sch",
                                  "rc_divider.kicad_sch"])
def test_loop5_compiles_with_pdflatex(name: str, load, tmp_path) -> None:
    tex_path = tmp_path / (Path(name).stem + ".tex")
    tex_path.write_text(circuitikz.generate(load(name), loops=True),
                        encoding="utf-8")
    pdf = Renderer().render(str(tex_path), "pdf", str(tmp_path))
    assert Path(pdf).stat().st_size > 0


def test_loop6_cli_flag(fixtures_dir, capsys) -> None:
    code = cli.main([str(fixtures_dir / "loops_two_sources.kicad_sch"),
                     "--check", "--loops"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Loop currents: i1 clockwise, i2 counterclockwise." in out


def test_loop6_pipeline_reports_and_warns(fixtures_dir, tmp_path) -> None:
    shown = run_pipeline(PipelineOptions(
        input_path=str(fixtures_dir / "loops_grid.kicad_sch"),
        output_dir=str(tmp_path), dry_run=True, show_loops=True))
    assert shown.stats.loops == ["i1 clockwise", "i2 clockwise",
                                 "i3 clockwise", "i4 clockwise"]
    assert "i4 flows clockwise through R3, R8 and R9." in shown.alt_text
    assert "arc[" in shown.tikz_code

    refused = run_pipeline(PipelineOptions(
        input_path=str(fixtures_dir / "loops_seven.kicad_sch"),
        output_dir=str(tmp_path), dry_run=True, show_loops=True))
    assert refused.stats.loops == []
    assert any(w.startswith("Loop currents are not drawn: the circuit has "
                            "7 loops") for w in refused.warnings)
    assert any(s.startswith("Loop currents not drawn")
               for s in refused.stats.summary_lines())

    off = run_pipeline(PipelineOptions(
        input_path=str(fixtures_dir / "loops_seven.kicad_sch"),
        output_dir=str(tmp_path), dry_run=True))
    assert not any("Loop" in w for w in off.warnings)
    assert not any("Loop" in s for s in off.stats.summary_lines())


def test_loop6_gui_option() -> None:
    pytest.importorskip("PySide6")
    from schemaccess.gui.main_window import make_pipeline_options
    options = make_pipeline_options("a.kicad_sch", "out", True, True, "All",
                                    show_loops=True)
    assert options.show_loops is True
    assert make_pipeline_options("a.kicad_sch", "out", True, True,
                                 "All").show_loops is False


def test_loop6_generator_is_deterministic() -> None:
    spec = importlib.util.spec_from_file_location(
        "gen_loops", FIXTURES_DIR / "gen_loops.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, text in module.build():
        on_disk = (FIXTURES_DIR / name).read_text(encoding="utf-8")
        assert text == on_disk, f"{name} is stale; run gen_loops.py"


def test_loop7_web_tex_tree_has_the_loop_label_fonts() -> None:
    if not _WEB_TEX.is_dir():
        pytest.skip("web front end not present")
    for font in ("cmmi10.pfb", "cmr7.pfb", "cmr10.pfb"):
        assert (_WEB_TEX / font).is_file(), font


_SCALE = {"": 1.0, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
          "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9,
          "R": 1.0}


def _value(text: str) -> float:
    text = text.strip()
    rkm = re.fullmatch(r"(\d+)([RkKmM])(\d+)", text)
    if rkm:
        return float(f"{rkm.group(1)}.{rkm.group(3)}") * _SCALE[rkm.group(2)]
    number = re.match(r"(\d+(?:\.\d+)?)\s*([fpnuµmkKMG]?)", text)
    return float(number.group(1)) * _SCALE[number.group(2)]


def _solve(matrix: List[List[float]], rhs: List[float]) -> List[float]:
    n = len(rhs)
    rows = [row[:] + [v] for row, v in zip(matrix, rhs)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(n):
            if r != col:
                f = rows[r][col] / rows[col][col]
                rows[r] = [a - f * b for a, b in zip(rows[r], rows[col])]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def _branch_currents(graph: CircuitGraph) -> Dict[str, float]:
    nets = sorted({pin.net_id for c in graph.components.values()
                   for pin in c.pins.values()})
    node = {net: k for k, net in enumerate(nets[1:])}
    carriers = [c for c in graph.components.values()
                if c.ctype in (ComponentType.VOLTAGE_SOURCE,
                               ComponentType.BATTERY, ComponentType.INDUCTOR)]
    size = len(node) + len(carriers)
    g = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size

    def at(pin) -> Optional[int]:
        return node.get(pin.net_id)

    def pins(comp):
        return comp.pins["1"], comp.pins["2"]

    for comp in graph.components.values():
        one, two = pins(comp)
        a, b = at(one), at(two)
        if comp.ctype == ComponentType.RESISTOR:
            y = 1.0 / _value(comp.value)
            for p, q in ((a, b), (b, a)):
                if p is not None:
                    g[p][p] += y
                    if q is not None:
                        g[p][q] -= y
        elif comp.ctype == ComponentType.CURRENT_SOURCE:
            if a is not None:
                rhs[a] -= _value(comp.value)
            if b is not None:
                rhs[b] += _value(comp.value)
    for k, comp in enumerate(carriers):
        row = len(node) + k
        plus, minus = pins(comp)
        volts = 0.0 if comp.ctype == ComponentType.INDUCTOR \
            else _value(comp.value)
        for pin, sign in ((plus, 1.0), (minus, -1.0)):
            n = at(pin)
            if n is not None:
                g[n][row] += sign
                g[row][n] += sign
        rhs[row] = volts
    x = _solve(g, rhs)

    def volt(pin) -> float:
        n = at(pin)
        return 0.0 if n is None else x[n]

    out = {}
    for comp in graph.components.values():
        one, two = pins(comp)
        if comp.ctype == ComponentType.RESISTOR:
            out[comp.ref] = (volt(one) - volt(two)) / _value(comp.value)
        elif comp.ctype == ComponentType.CURRENT_SOURCE:
            out[comp.ref] = _value(comp.value)
        elif comp in carriers:
            out[comp.ref] = x[len(node) + carriers.index(comp)]
        else:
            out[comp.ref] = 0.0
    return out


def _entry(mesh: loops.Mesh, a: Point, b: Point) -> int:
    ring = mesh.boundary
    for i, p in enumerate(ring):
        q = ring[(i + 1) % len(ring)]
        if (p, q) == (a, b):
            return 1 if not mesh.clockwise else -1
        if (p, q) == (b, a):
            return -1 if not mesh.clockwise else 1
    raise AssertionError(f"{mesh.name}: a part is not on its outline")


@pytest.mark.parametrize("name", [
    "loops_two.kicad_sch", "loops_three.kicad_sch", "loops_grid.kicad_sch",
    "loops_two_sources.kicad_sch", "loops_middle_source.kicad_sch",
    "loops_current_source.kicad_sch", "earth_blank_value.kicad_sch",
    "wheatstone.kicad_sch", "rc_divider.kicad_sch"])
def test_loop8_no_drawn_loop_runs_backwards(name: str, load) -> None:
    graph = load(name)
    found = loops.find_meshes(graph)
    assert found.ok, found.reason
    branch = _branch_currents(graph)
    rows, targets = [], []
    for ref, comp in sorted(graph.components.items()):
        one, two = comp.pins["1"].position, comp.pins["2"].position
        row = [0.0] * len(found.meshes)
        for k, mesh in enumerate(found.meshes):
            if ref in mesh.refs:
                row[k] = _entry(mesh, one, two)
        rows.append(row)
        targets.append(branch[ref])
    n = len(found.meshes)
    normal = [[sum(r[i] * r[j] for r in rows) for j in range(n)]
              for i in range(n)]
    rhs = [sum(r[i] * t for r, t in zip(rows, targets)) for i in range(n)]
    currents = _solve(normal, rhs)
    scale = max(abs(t) for t in targets) or 1.0
    for row, target in zip(rows, targets):
        assert abs(sum(a * c for a, c in zip(row, currents)) - target) \
            <= 1e-9 * scale
    for mesh, current in zip(found.meshes, currents):
        assert current >= -1e-9 * scale, (
            f"{name}: {mesh.name} is drawn {mesh.sense} but its current "
            f"runs the other way")

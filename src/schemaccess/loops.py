"""Loop currents (mesh analysis) found on the drawing, for --loops."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .model import CircuitGraph, Component, ComponentType, Point, snap
from .netlist import _spice_value

__all__ = ["MAX_LOOPS", "Mesh", "MeshResult", "find_meshes", "same_way"]

MAX_LOOPS = 4

_EPS = 1e-3

_WIRE_CLEARANCE = 0.9
_BODY_CLEARANCE = 1.8
_LABEL_OFFSET = 1.3
_CHAR_WIDTH = 0.95
_TEXT_HEIGHT = 1.8
_TEXT_MARGIN = 0.5
_MIN_RADIUS = 1.5
_MAX_RADIUS = 5.0
_FILL = 0.85


@dataclass
class Mesh:
    index: int
    refs: List[str]
    centre: Point
    radius: float
    boundary: List[Point] = field(default_factory=list)
    clockwise: bool = True

    @property
    def name(self) -> str:
        return f"i{self.index}"

    @property
    def sense(self) -> str:
        return "clockwise" if self.clockwise else "counterclockwise"


@dataclass
class MeshResult:
    meshes: List[Mesh] = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.meshes) and not self.reason

    def shared(self) -> List[Tuple[str, Mesh, Mesh]]:
        owners: Dict[str, List[Mesh]] = {}
        for mesh in self.meshes:
            for ref in mesh.refs:
                owners.setdefault(ref, []).append(mesh)
        out = []
        for ref, meshes in owners.items():
            if len(meshes) == 2:
                out.append((ref, meshes[0], meshes[1]))
        return sorted(out, key=lambda item: (item[1].index, item[2].index,
                                             item[0]))


def same_way(first: Mesh, second: Mesh) -> bool:
    return first.clockwise != second.clockwise


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_segment(p: Point, a: Point, b: Point) -> bool:
    if not (min(a[0], b[0]) - _EPS <= p[0] <= max(a[0], b[0]) + _EPS
            and min(a[1], b[1]) - _EPS <= p[1] <= max(a[1], b[1]) + _EPS):
        return False
    length = abs(b[0] - a[0]) + abs(b[1] - a[1])
    return abs(_cross(a, b, p)) <= _EPS * max(length, 1.0)


def _inside_segment(p: Point, a: Point, b: Point) -> bool:
    return p != a and p != b and _on_segment(p, a, b)


def _meet(a: Point, b: Point, c: Point, d: Point) -> Optional[Point]:
    if (max(a[0], b[0]) < min(c[0], d[0]) - _EPS
            or max(c[0], d[0]) < min(a[0], b[0]) - _EPS
            or max(a[1], b[1]) < min(c[1], d[1]) - _EPS
            or max(c[1], d[1]) < min(a[1], b[1]) - _EPS):
        return None
    d1, d2 = _cross(c, d, a), _cross(c, d, b)
    d3, d4 = _cross(a, b, c), _cross(a, b, d)
    if ((d1 > _EPS and d2 < -_EPS) or (d1 < -_EPS and d2 > _EPS)) and \
            ((d3 > _EPS and d4 < -_EPS) or (d3 < -_EPS and d4 > _EPS)):
        t = d1 / (d1 - d2)
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    for p, (s, e) in ((a, (c, d)), (b, (c, d)), (c, (a, b)), (d, (a, b))):
        if _on_segment(p, s, e):
            return p
    return None


def _distance(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy)
                     / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _inside(p: Point, polygon: Sequence[Point]) -> bool:
    x, y = p
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def _area(polygon: Sequence[Point]) -> float:
    total = 0.0
    for i in range(len(polygon)):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % len(polygon)]
        total += x1 * y2 - x2 * y1
    return -total / 2.0


class _Union:
    def __init__(self) -> None:
        self.parent: Dict[object, object] = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb, key=repr)] = min(ra, rb, key=repr)


def _loop_count(links: Sequence[Tuple[object, object]]) -> int:
    union = _Union()
    nodes: Set[object] = set()
    for a, b in links:
        nodes.update((a, b))
        union.union(a, b)
    pieces = len({union.find(n) for n in nodes})
    return len(links) - len(nodes) + pieces


@dataclass
class _Edge:
    a: Point
    b: Point
    kind: str
    ref: str = ""


_Obstacle = Tuple[Point, Point, float]


def _visible_length(latex: str) -> int:
    text = re.sub(r"\\[A-Za-z]+", "x", latex)
    return len(re.sub(r"[{}$\\]", "", text))


def _part_obstacles(comp: Component) -> List[_Obstacle]:
    from . import circuitikz

    first, second = circuitikz._bipole_pin_order(comp)
    a, b = first.position, second.position
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    left = (dy / length, -dx / length)
    middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    upright = abs(dx) < abs(dy)

    texts = [(left, len(comp.ref) if comp.shows("Reference") else 0),
             ((-left[0], -left[1]),
              _visible_length(circuitikz._format_value(comp)))]
    found: List[_Obstacle] = [(a, b, _BODY_CLEARANCE)]
    keep = _TEXT_HEIGHT / 2 + _TEXT_MARGIN
    for (nx, ny), chars in texts:
        if not chars:
            continue
        width = chars * _CHAR_WIDTH
        if upright:
            start = (middle[0] + nx * _LABEL_OFFSET, middle[1])
            end = (middle[0] + nx * (_LABEL_OFFSET + width), middle[1])
        else:
            y = middle[1] + ny * (_LABEL_OFFSET + _TEXT_HEIGHT / 2)
            start, end = (middle[0] - width / 2, y), (middle[0] + width / 2, y)
        found.append((start, end, keep))
    return found


def _where(p: Point) -> str:
    return f"near ({p[0]:.1f}, {p[1]:.1f}) mm"


def find_meshes(graph: CircuitGraph) -> MeshResult:
    doc = graph.document
    if doc is None:
        return MeshResult(reason="there is no drawing to place them on")
    if doc.sheets:
        return MeshResult(reason="the design is spread over several sheets")

    parts: List[Component] = []
    for comp in graph.sorted_components():
        if len(comp.pins) > 2:
            return MeshResult(reason=(
                f"{comp.ref} ({comp.ctype.value}) has {len(comp.pins)} "
                f"pins, and loops are only drawn for circuits built from "
                f"two-terminal parts"))
        if len(comp.pins) == 2:
            parts.append(comp)

    expected = _loop_count([
        tuple(pin.net_id for pin in _pins(comp)) for comp in parts])
    if expected == 0:
        return MeshResult(reason="the circuit has no closed loop")
    if expected > MAX_LOOPS:
        return MeshResult(reason=(
            f"the circuit has {expected} loops, and they are drawn for up "
            f"to {MAX_LOOPS} so the picture stays readable"))

    edges, problem = _plane(graph, parts)
    if problem:
        return MeshResult(reason=problem)

    drawn = _Union()
    for edge in edges:
        if edge.kind != "part":
            drawn.union(edge.a, edge.b)
    seen = _loop_count([(drawn.find(e.a), drawn.find(e.b))
                        for e in edges if e.kind == "part"])
    if seen != expected:
        return MeshResult(reason=_mismatch_reason(graph, doc))

    faces = _faces(_prune(edges))
    windows = []
    for face in faces:
        refs = [e.ref for e in face if e.kind == "part"]
        refs = [r for r in refs if refs.count(r) == 1]
        if refs:
            windows.append((face, refs))
    if len(windows) != expected:
        return MeshResult(reason=(
            "the windows on the drawing do not match the circuit's loops"))

    senses, problem = _senses(graph, windows)
    if problem:
        return MeshResult(reason=problem)

    outlines = [[e.a for e in face] for face, _refs in windows]
    obstacles = _obstacles(graph, parts, edges)
    meshes = []
    for i, (face, refs) in enumerate(windows):
        others = outlines[:i] + outlines[i + 1:]
        centre, room = _place(outlines[i], others, obstacles)
        radius = max(_MIN_RADIUS, min(_MAX_RADIUS, _FILL * room))
        clockwise = senses[i] > 0
        meshes.append(Mesh(index=0, refs=_around(face, refs, graph,
                                                 clockwise),
                           centre=centre, radius=radius,
                           boundary=outlines[i], clockwise=clockwise))

    for number, mesh in enumerate(_reading_order(meshes), start=1):
        mesh.index = number
    meshes.sort(key=lambda m: m.index)
    return MeshResult(meshes=meshes)


def _pins(comp: Component):
    return [comp.pins[k] for k in sorted(comp.pins)]


def _plane(graph: CircuitGraph,
           parts: List[Component]) -> Tuple[List[_Edge], str]:
    doc = graph.document
    wires: List[Tuple[Point, Point]] = []
    for wire in doc.wires:
        for a, b in zip(wire.points, wire.points[1:]):
            if a != b:
                wires.append((a, b))

    points: Set[Point] = {p for seg in wires for p in seg}
    points.update(snap(j.x, j.y) for j in doc.junctions)
    for comp in graph.components.values():
        points.update(pin.position for pin in comp.pins.values())
    power_pins: Dict[int, List[Point]] = {}
    net_at: Dict[Point, int] = {p: net.net_id for net in graph.nets
                                for p in net.points}
    for inst in doc.symbols:
        lib = doc.lib_symbol_for(inst)
        if lib is None or not (lib.is_power or inst.reference.startswith("#")):
            continue
        for pin in lib.pins_for_unit(inst.unit):
            pos = inst.pin_position(pin)
            points.add(pos)
            if not inst.reference.startswith("#FLG") and pos in net_at:
                power_pins.setdefault(net_at[pos], []).append(pos)

    edges: List[_Edge] = []
    for a, b in wires:
        cuts = sorted((p for p in points if _inside_segment(p, a, b)),
                      key=lambda p: abs(p[0] - a[0]) + abs(p[1] - a[1]))
        chain = [a] + cuts + [b]
        edges.extend(_Edge(p, q, "wire") for p, q in zip(chain, chain[1:]))

    for comp in parts:
        a, b = (pin.position for pin in _pins(comp))
        if a == b:
            return [], f"{comp.ref}'s two pins are drawn on the same spot"
        for p in points:
            if _inside_segment(p, a, b):
                return [], (f"something meets {comp.ref} between its pins "
                            f"{_where(p)}")
        edges.append(_Edge(a, b, "part", comp.ref))

    joined = _Union()
    for edge in edges:
        if edge.kind == "wire":
            joined.union(edge.a, edge.b)
    for net_id in sorted(power_pins):
        row = sorted(set(power_pins[net_id]))
        if len(row) < 2:
            continue
        if all(abs(p[1] - row[0][1]) < _EPS for p in row):
            row.sort(key=lambda p: p[0])
        elif all(abs(p[0] - row[0][0]) < _EPS for p in row):
            row.sort(key=lambda p: p[1])
        else:
            continue
        for p, q in zip(row, row[1:]):
            if joined.find(p) != joined.find(q):
                joined.union(p, q)
                edges.append(_Edge(p, q, "rail"))

    unique: Dict[Tuple[Point, Point], _Edge] = {}
    for edge in edges:
        key = (min(edge.a, edge.b), max(edge.a, edge.b))
        if key in unique:
            if "part" in (edge.kind, unique[key].kind):
                ref = edge.ref or unique[key].ref
                return [], f"{ref} is drawn on top of a wire or another part"
            continue
        unique[key] = edge
    edges = list(unique.values())

    for i, first in enumerate(edges):
        for second in edges[i + 1:]:
            if {first.a, first.b} & {second.a, second.b}:
                continue
            spot = _meet(first.a, first.b, second.a, second.b)
            if spot is not None:
                what = ("a row of power symbols" if "rail" in
                        (first.kind, second.kind) else "the drawing")
                return [], (f"{what} crosses itself without a junction "
                            f"{_where(spot)}")
    return edges, ""


def _mismatch_reason(graph: CircuitGraph, doc) -> str:
    if doc.labels:
        return ("parts are joined through net labels rather than drawn "
                "wires, so the windows on the page are not the circuit's "
                "loops")
    return ("parts are joined through power symbols that are not in one "
            "row, so the loops cannot be traced on the drawing; draw the "
            "ground as a wire, or line the ground symbols up")


def _prune(edges: List[_Edge]) -> List[_Edge]:
    edges = list(edges)
    while True:
        degree: Dict[Point, int] = {}
        for edge in edges:
            degree[edge.a] = degree.get(edge.a, 0) + 1
            degree[edge.b] = degree.get(edge.b, 0) + 1
        kept = [e for e in edges if degree[e.a] > 1 and degree[e.b] > 1]
        if len(kept) == len(edges):
            return kept
        edges = kept


def _faces(edges: List[_Edge]) -> List[List[_Edge]]:
    around: Dict[Point, List[Point]] = {}
    by_ends: Dict[Tuple[Point, Point], _Edge] = {}
    for edge in edges:
        around.setdefault(edge.a, []).append(edge.b)
        around.setdefault(edge.b, []).append(edge.a)
        by_ends[(edge.a, edge.b)] = by_ends[(edge.b, edge.a)] = edge
    for centre, others in around.items():
        others.sort(key=lambda p: math.atan2(-(p[1] - centre[1]),
                                             p[0] - centre[0]))

    # Each bounded face is walked anticlockwise on screen.
    faces: List[List[_Edge]] = []
    walked: Set[Tuple[Point, Point]] = set()
    for start in sorted(by_ends):
        if start in walked:
            continue
        walk: List[Tuple[Point, Point]] = []
        step = start
        while step not in walked:
            walked.add(step)
            walk.append(step)
            u, v = step
            ring = around[v]
            step = (v, ring[(ring.index(u) - 1) % len(ring)])
        outline = [u for u, _v in walk]
        if _area(outline) > 1e-6:
            faces.append([_oriented(by_ends[s], s) for s in walk])
    return faces


def _oriented(edge: _Edge, step: Tuple[Point, Point]) -> _Edge:
    return _Edge(step[0], step[1], edge.kind, edge.ref)


def _obstacles(graph: CircuitGraph, parts: List[Component],
               edges: List[_Edge]) -> List[_Obstacle]:
    found: List[_Obstacle] = [(e.a, e.b, _WIRE_CLEARANCE)
                              for e in edges if e.kind != "part"]
    for comp in parts:
        found.extend(_part_obstacles(comp))
    for label in graph.document.labels:
        width = len(label.text) * _CHAR_WIDTH
        y = label.y - _TEXT_HEIGHT / 2
        found.append(((label.x, y), (label.x + width, y),
                      _TEXT_HEIGHT / 2 + _TEXT_MARGIN))
    return found


def _centroid(outline: List[Point]) -> Point:
    area = _area(outline)
    if abs(area) < 1e-9:
        return (sum(p[0] for p in outline) / len(outline),
                sum(p[1] for p in outline) / len(outline))
    cx = cy = 0.0
    for i in range(len(outline)):
        x1, y1 = outline[i]
        x2, y2 = outline[(i + 1) % len(outline)]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (-6.0 * area), cy / (-6.0 * area))


def _place(outline: List[Point], others: List[List[Point]],
           obstacles: List[_Obstacle]) -> Tuple[Point, float]:
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    centre = _centroid(outline)
    enough = _MAX_RADIUS / _FILL

    def score(p: Point) -> Tuple[float, float]:
        if not _inside(p, outline) or any(_inside(p, o) for o in others):
            return (-math.inf, 0.0)
        room = min(_distance(p, a, b) - keep for a, b, keep in obstacles)
        return (min(room, enough),
                -math.hypot(p[0] - centre[0], p[1] - centre[1]))

    best_point = centre
    best = score(centre)
    if best[0] >= enough:
        return (round(centre[0], 3), round(centre[1], 3)), best[0]
    step = max(min(right - left, bottom - top) / 24.0, 0.2)
    y = top + step / 2
    while y < bottom:
        x = left + step / 2
        while x < right:
            candidate = score((x, y))
            if candidate > best:
                best, best_point = candidate, (x, y)
            x += step
        y += step
    for _ in range(2):
        cx, cy = best_point
        step /= 4.0
        for i in range(-4, 5):
            for j in range(-4, 5):
                p = (cx + i * step, cy + j * step)
                candidate = score(p)
                if candidate > best:
                    best, best_point = candidate, p
    room = max(best[0], 0.0)
    return (round(best_point[0], 3), round(best_point[1], 3)), room


def _around(face: List[_Edge], refs: List[str], graph: CircuitGraph,
            clockwise: bool) -> List[str]:
    walk = reversed(face) if clockwise else face
    order = [e.ref for e in walk if e.ref in refs]

    def middle(ref: str) -> Point:
        a, b = (pin.position for pin in _pins(graph.components[ref]))
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    first = min(range(len(order)),
                key=lambda i: (middle(order[i])[0], middle(order[i])[1]))
    return order[first:] + order[:first]


_RESISTIVE = {ComponentType.RESISTOR}
_SHORT = {ComponentType.INDUCTOR, ComponentType.FUSE}
_OPEN = {ComponentType.CAPACITOR, ComponentType.CAPACITOR_POLARIZED}
_DC_VOLTAGE = {ComponentType.VOLTAGE_SOURCE, ComponentType.BATTERY}
_CONTROLLED = {ComponentType.CONTROLLED_VOLTAGE_SOURCE,
               ComponentType.CONTROLLED_CURRENT_SOURCE}

_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
       "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12}
_MAGNITUDE = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)"
                        r"(meg|[fpnumkgt])?$", re.IGNORECASE)

_Walk = List[Tuple[str, Point, Point]]


def _number(comp: Component) -> Optional[float]:
    text = _spice_value(comp.value or "", comp.ctype)
    match = _MAGNITUDE.match(text or "")
    if match is None:
        return None
    return float(match.group(1)) * _SI.get((match.group(2) or "").lower(),
                                           1.0)


def _senses(graph: CircuitGraph,
            windows) -> Tuple[List[int], str]:
    from . import circuitikz

    walks: List[_Walk] = []
    for face, refs in windows:
        walks.append([(e.ref, e.b, e.a) for e in reversed(face)
                      if e.kind == "part" and e.ref in refs])
    visits: Dict[str, List[Tuple[int, Point, Point]]] = {}
    for i, walk in enumerate(walks):
        for ref, start, end in walk:
            visits.setdefault(ref, []).append((i, start, end))

    parts = {ref: graph.components[ref] for ref in visits}
    controlled = sorted(r for r, c in parts.items() if c.ctype in _CONTROLLED)
    if controlled:
        return [], (f"which way the loop currents flow depends on the "
                    f"controlled source {controlled[0]}")
    sources = sorted(r for r, c in parts.items() if c.ctype.is_source)
    if not sources:
        return [], "the circuit has no source, so no current flows round it"

    # A source pushes current from its first drawn pin to its second:
    # - to + for a voltage source, + to - (SPICE) for a current source.
    ends = {r: tuple(p.position
                     for p in circuitikz._bipole_pin_order(parts[r]))
            for r in parts}
    pushes: Dict[int, Set[int]] = {}
    for ref in sources:
        for i, start, end in visits[ref]:
            pushes.setdefault(i, set()).add(
                1 if (start, end) == ends[ref] else -1)

    currents, blocker = _solve(parts, walks, visits, ends, len(windows))
    everyone = set().union(*pushes.values())
    senses: List[int] = []
    for i in range(len(windows)):
        if currents is not None:
            scale = max(abs(c) for c in currents)
            if scale > 0 and abs(currents[i]) > 1e-9 * scale:
                senses.append(1 if currents[i] > 0 else -1)
                continue
        own = pushes.get(i, set())
        if len(own) == 1:
            senses.append(next(iter(own)))
        elif not own and len(everyone) == 1:
            senses.append(next(iter(everyone)))
        elif currents is not None:
            senses.append(1)
        else:
            verb = "pushes" if len(sources) == 1 else "push"
            return [], (f"{_join_refs(sources)} {verb} the loop currents "
                        f"opposite ways, and which way each loop goes "
                        f"cannot be worked out because {blocker}")
    return senses, ""


def _join_refs(refs: Sequence[str]) -> str:
    refs = list(refs)
    return refs[0] if len(refs) == 1 else \
        ", ".join(refs[:-1]) + " and " + refs[-1]


def _solve(parts: Dict[str, Component], walks: List[_Walk],
           visits: Dict[str, List[Tuple[int, Point, Point]]],
           ends: Dict[str, Tuple[Point, Point]],
           count: int) -> Tuple[Optional[List[float]], str]:
    fixed = sorted(r for r, c in parts.items()
                   if c.ctype in _OPEN
                   or c.ctype == ComponentType.CURRENT_SOURCE)
    column = {ref: count + k for k, ref in enumerate(fixed)}
    size = count + len(fixed)
    matrix = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size

    def along(ref: str, start: Point, end: Point) -> int:
        return 1 if (start, end) == ends[ref] else -1

    for i, walk in enumerate(walks):
        for ref, start, end in walk:
            comp = parts[ref]
            kind = comp.ctype
            if kind in _SHORT:
                continue
            if ref in column:
                matrix[i][column[ref]] += along(ref, start, end)
                continue
            value = _number(comp)
            if kind in _RESISTIVE and value is not None:
                for j, s, e in visits[ref]:
                    same = (s, e) == (start, end)
                    matrix[i][j] += value if same else -value
            elif kind in _DC_VOLTAGE and value is not None:
                rhs[i] += value * along(ref, start, end)
            elif kind in _RESISTIVE | _DC_VOLTAGE:
                return None, (f"{ref}'s value '{comp.value.strip()}' is not "
                              f"a number")
            else:
                article = "an" if kind.value[:1].lower() in "aeiou" \
                    or kind.value.startswith("LED") else "a"
                return None, f"{ref} is {article} {kind.value}"
    for ref in fixed:
        row = column[ref]
        for j, s, e in visits[ref]:
            matrix[row][j] += along(ref, s, e)
        if parts[ref].ctype == ComponentType.CURRENT_SOURCE:
            value = _number(parts[ref])
            if value is None:
                return None, (f"{ref}'s value "
                              f"'{parts[ref].value.strip()}' is not a number")
            rhs[row] = value
    solution = _gauss(matrix, rhs)
    if solution is None:
        return None, "the loop equations have no single answer"
    return solution[:count], ""


def _gauss(matrix: List[List[float]],
           rhs: List[float]) -> Optional[List[float]]:
    n = len(rhs)
    rows = [row[:] + [value] for row, value in zip(matrix, rhs)]
    scale = max((abs(v) for row in matrix for v in row), default=0.0)
    if scale == 0.0:
        return None
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) <= 1e-12 * scale:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(n):
            if r != col and rows[r][col]:
                factor = rows[r][col] / rows[col][col]
                for c in range(col, n + 1):
                    rows[r][c] -= factor * rows[col][c]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def _reading_order(meshes: List[Mesh]) -> List[Mesh]:
    def top(mesh: Mesh) -> float:
        return min(p[1] for p in mesh.boundary)

    def left(mesh: Mesh) -> float:
        return min(p[0] for p in mesh.boundary)

    rows: List[List[Mesh]] = []
    for mesh in sorted(meshes, key=lambda m: (top(m), left(m))):
        if rows and abs(top(mesh) - top(rows[-1][0])) < 2.6:
            rows[-1].append(mesh)
        else:
            rows.append([mesh])
    return [m for row in rows for m in sorted(row, key=left)]

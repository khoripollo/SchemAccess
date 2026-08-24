"""Build the electrical circuit graph from a parsed schematic document.

Connectivity rules (matching KiCad semantics):

* consecutive points of a wire polyline are connected;
* wires sharing an endpoint are connected;
* a junction connects everything passing through its position, including
  wire interiors;
* a pin, label or wire endpoint lying anywhere on a wire segment connects
  to it;
* two wires *crossing* mid-segment without a junction are NOT connected.

Net names come from power symbols (GND, +5V, VCC...), then global labels,
then hierarchical labels, then local labels; remaining nets get
deterministic names N1, N2, ... ordered by their top-left-most point.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .model import (CircuitGraph, Component, ComponentType, Label, LabelKind,
                    Net, NetKind, PinConnection, Point, SchematicDocument,
                    SymbolInstance, classify, snap)

_EPS = 1e-3  # mm tolerance for point-on-segment tests

_GROUND_NAMES = {"gnd", "gnda", "gndd", "gndref", "gndpwr", "agnd", "dgnd",
                 "earth", "0", "gnds", "vss"}


# ---------------------------------------------------------------------------
# Union-find over snapped points
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self):
        self.parent: Dict[Point, Point] = {}

    def add(self, p: Point) -> None:
        self.parent.setdefault(p, p)

    def find(self, p: Point) -> Point:
        self.add(p)
        root = p
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[p] != root:  # path compression
            self.parent[p], p = root, self.parent[p]
        return root

    def union(self, a: Point, b: Point) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _on_segment(p: Point, a: Point, b: Point) -> bool:
    """True if point *p* lies on segment a-b (inclusive of endpoints)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    if not (min(ax, bx) - _EPS <= px <= max(ax, bx) + _EPS
            and min(ay, by) - _EPS <= py <= max(ay, by) + _EPS):
        return False
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    seg_len = abs(bx - ax) + abs(by - ay)
    return abs(cross) <= _EPS * max(seg_len, 1.0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_graph(doc: SchematicDocument) -> CircuitGraph:
    """Construct the shared :class:`CircuitGraph` from *doc*."""
    graph = CircuitGraph(document=doc)
    graph.warnings.extend(doc.warnings)

    uf = _UnionFind()
    segments: List[Tuple[Point, Point]] = []

    # 1. Wires: register points, connect polyline runs.
    for wire in doc.wires:
        for i in range(len(wire.points) - 1):
            a, b = wire.points[i], wire.points[i + 1]
            uf.add(a)
            uf.add(b)
            uf.union(a, b)
            segments.append((a, b))

    # Spatial buckets so point-on-segment checks stay fast on big schematics.
    by_x: Dict[float, List[int]] = defaultdict(list)
    by_y: Dict[float, List[int]] = defaultdict(list)
    diagonal: List[int] = []
    for idx, (a, b) in enumerate(segments):
        if abs(a[0] - b[0]) <= _EPS:
            by_x[round(a[0], 2)].append(idx)
        elif abs(a[1] - b[1]) <= _EPS:
            by_y[round(a[1], 2)].append(idx)
        else:
            diagonal.append(idx)

    def attach(p: Point) -> None:
        """Union *p* with every wire segment it lies on."""
        uf.add(p)
        candidates: Set[int] = set(diagonal)
        candidates.update(by_x.get(round(p[0], 2), ()))
        candidates.update(by_y.get(round(p[1], 2), ()))
        for idx in candidates:
            a, b = segments[idx]
            if _on_segment(p, a, b):
                uf.union(p, a)
                uf.union(p, b)

    # 2. Junctions connect wire interiors that pass through them.
    for junc in doc.junctions:
        attach(snap(junc.x, junc.y))

    # 3. Symbol pins.
    pin_points: List[Tuple[SymbolInstance, str, str, str, Point]] = []
    for inst in doc.symbols:
        lib = doc.lib_symbol_for(inst)
        if lib is None:
            graph.warnings.append(
                f"No library definition for '{inst.lib_id}' "
                f"({inst.reference}); its pins are unknown.")
            continue
        for pin in lib.pins_for_unit(inst.unit):
            pos = inst.pin_position(pin)
            attach(pos)
            pin_points.append((inst, pin.number, pin.name, pin.etype, pos))

    # 4. Labels attach names at their anchor point.
    for lbl in doc.labels:
        attach(snap(lbl.x, lbl.y))

    # Names contributed by power symbols.
    power_name_at: Dict[Point, str] = {}
    for inst in doc.symbols:
        lib = doc.lib_symbol_for(inst)
        if lib is None or not (lib.is_power or inst.reference.startswith("#")):
            continue
        for pin in lib.pins_for_unit(inst.unit):
            pos = inst.pin_position(pin)
            name = inst.value or inst.lib_id.split(":", 1)[-1]
            if name and not inst.reference.startswith("#FLG"):
                power_name_at[pos] = name

    label_at: Dict[Point, List[Label]] = defaultdict(list)
    for lbl in doc.labels:
        label_at[snap(lbl.x, lbl.y)].append(lbl)

    # 5. Name-based connections (no wires needed): power symbols and
    #    global labels join same-named nets anywhere in the schematic;
    #    local labels join same-named nets on the same sheet (the parser
    #    namespaces them per sheet when flattening); hierarchical labels
    #    join same-named sheet pins.
    anchors: Dict[Tuple[str, str], Point] = {}

    def merge_by_name(scope: str, name: str, p: Point) -> None:
        key = (scope, name)
        if key in anchors:
            uf.union(anchors[key], p)
        else:
            anchors[key] = p

    for p, name in power_name_at.items():
        merge_by_name("global", name, p)
    for lbl in doc.labels:
        p = snap(lbl.x, lbl.y)
        scope = {LabelKind.GLOBAL: "global",
                 LabelKind.HIERARCHICAL: "hier",
                 LabelKind.LOCAL: "local"}[lbl.kind]
        merge_by_name(scope, lbl.text, p)
    for sheet in doc.sheets:
        for name, pos in sheet.pins:
            attach(pos)
            merge_by_name("hier", name, pos)

    # ------------------------------------------------------------------
    # Group points into net candidates.
    # ------------------------------------------------------------------
    groups: Dict[Point, List[Point]] = defaultdict(list)
    for p in list(uf.parent):
        groups[uf.find(p)].append(p)

    nc_points = {snap(nc.x, nc.y) for nc in doc.no_connects}

    # ------------------------------------------------------------------
    # Resolve one (kind, name) per group.
    # ------------------------------------------------------------------
    def resolve(points: List[Point]) -> Tuple[NetKind, str]:
        power, glob, hier, local = [], [], [], []
        for p in points:
            if p in power_name_at:
                power.append(power_name_at[p])
            for lbl in label_at.get(p, ()):
                {LabelKind.GLOBAL: glob, LabelKind.HIERARCHICAL: hier,
                 LabelKind.LOCAL: local}[lbl.kind].append(lbl.text)
        power_names = sorted(set(power))
        ground = [n for n in power_names if n.lower() in _GROUND_NAMES]
        if ground:
            return NetKind.GROUND, ground[0]
        if power:
            names = power_names
            if len(names) > 1:
                graph.warnings.append(
                    f"Net carries multiple power names {names}; "
                    f"using '{names[0]}'.")
            return NetKind.POWER, names[0]
        for bucket in (glob, hier, local):
            if bucket:
                return NetKind.NAMED, sorted(set(bucket))[0]
        return NetKind.ANONYMOUS, ""

    resolved = []
    for root, points in groups.items():
        kind, name = resolve(points)
        resolved.append((kind, name, sorted(points), root))

    # Deterministic net ordering: ground, power rails, named, anonymous;
    # within a category by name then by top-left-most point.
    order = {NetKind.GROUND: 0, NetKind.POWER: 1,
             NetKind.NAMED: 2, NetKind.ANONYMOUS: 3}
    resolved.sort(key=lambda r: (order[r[0]], r[1], r[2][0]))

    root_to_net: Dict[Point, int] = {}
    anon_counter = 0
    for kind, name, points, root in resolved:
        if kind == NetKind.ANONYMOUS:
            anon_counter += 1
            name = f"N{anon_counter}"
        net = Net(net_id=len(graph.nets), name=name, kind=kind, points=points)
        root_to_net[root] = net.net_id
        graph.nets.append(net)

    # ------------------------------------------------------------------
    # Components (multi-unit symbols merge into one component per ref).
    # ------------------------------------------------------------------
    for inst in doc.symbols:
        lib = doc.lib_symbol_for(inst)
        if lib is None:
            continue
        if lib.is_power or inst.reference.startswith("#"):
            continue
        comp = graph.components.get(inst.reference)
        if comp is None:
            unit_pins = lib.pins_for_unit(inst.unit)
            comp = Component(
                ref=inst.reference,
                ctype=classify(inst.lib_id, inst.reference, inst.value,
                               len(unit_pins),
                               [p.name for p in unit_pins],
                               lib.hints),
                value=inst.value,
                lib_id=inst.lib_id,
                position=snap(inst.x, inst.y),
                angle=inst.angle,
                mirror=inst.mirror,
                properties=dict(inst.properties),
                hidden_properties=set(inst.hidden_properties),
            )
            graph.components[inst.reference] = comp
        for pin in lib.pins_for_unit(inst.unit):
            pos = inst.pin_position(pin)
            net_id = root_to_net.get(uf.find(pos), -1)
            comp.pins[pin.number] = PinConnection(
                number=pin.number, name=pin.name, position=pos,
                net_id=net_id, etype=pin.etype)
        # Polarity dots travel with the placed symbol, so they follow its
        # rotation and mirroring like the pins do.
        for dot in lib.dots_for_unit(inst.unit):
            marker = (inst.lib_point(dot.x, dot.y), dot.radius)
            if marker not in comp.dots:
                comp.dots.append(marker)

    # Register pins on their nets, in deterministic order.
    for comp in graph.sorted_components():
        for number in sorted(comp.pins, key=_pin_sort_key):
            pin = comp.pins[number]
            if pin.net_id >= 0:
                graph.nets[pin.net_id].pins.append((comp.ref, number))

    # ------------------------------------------------------------------
    # Diagnostics: dangling pins (unless marked no-connect).
    # ------------------------------------------------------------------
    for comp in graph.sorted_components():
        for number in sorted(comp.pins, key=_pin_sort_key):
            pin = comp.pins[number]
            if pin.net_id < 0:
                continue
            net = graph.nets[pin.net_id]
            if len(net.pins) == 1 and pin.position not in nc_points:
                graph.warnings.append(
                    f"Pin {number} of {comp.ref} appears unconnected.")

    return graph


def _pin_sort_key(number: str):
    return (0, int(number)) if number.isdigit() else (1, number)


# ---------------------------------------------------------------------------
# Convenience: numbered "nodes" for alt text
# ---------------------------------------------------------------------------

def node_names(graph: CircuitGraph) -> Dict[int, str]:
    """Human-friendly node names for alt text.

    Ground nets are called "ground"; power rails keep their rail name;
    every other net that connects two or more pins gets "node 1",
    "node 2", ... in deterministic net order.
    """
    names: Dict[int, str] = {}
    counter = 0
    for net in graph.nets:
        if net.kind == NetKind.GROUND:
            names[net.net_id] = "ground"
        elif net.kind == NetKind.POWER:
            names[net.net_id] = f"the {net.name} rail"
        elif len(net.pins) >= 2:
            counter += 1
            if net.kind == NetKind.NAMED:
                names[net.net_id] = f"node {counter} ({net.name})"
            else:
                names[net.net_id] = f"node {counter}"
        elif net.kind == NetKind.NAMED:
            # A labelled single-pin net is an external port, not a mistake.
            names[net.net_id] = f"the {net.name} terminal"
        else:
            names[net.net_id] = f"an unconnected point ({net.name})"
    return names

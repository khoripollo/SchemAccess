"""Connectivity analyzer: detects circuit structures in a CircuitGraph.

Works purely on the electrical :class:`~schemaccess.model.CircuitGraph`
(no geometry).  Detected patterns include parallel groups, series chains,
voltage dividers, first-order RC/RL filters, Wheatstone bridges, the three
classic op-amp configurations, logic gates and power rails.

All output is deterministic: candidates are sorted explicitly before
pattern matching, so identical graphs always produce identical results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .model import CircuitGraph, Component, ComponentType, NetKind, PinConnection
from .netbuilder import node_names


@dataclass
class Structure:
    """One detected circuit structure (series group, divider, filter...)."""
    kind: str                # e.g. 'series', 'parallel', 'voltage_divider',
                             # 'rc_low_pass', 'rc_high_pass', 'wheatstone',
                             # 'opamp_inverting', 'opamp_non_inverting',
                             # 'opamp_follower', 'logic'
    description: str         # one human-readable sentence
    refs: List[str] = field(default_factory=list)   # participating refs
    nets: List[int] = field(default_factory=list)   # boundary net ids


@dataclass
class CircuitAnalysis:
    structures: List[Structure] = field(default_factory=list)
    parallel_groups: List[List[str]] = field(default_factory=list)
    series_chains: List[List[str]] = field(default_factory=list)
    power_rails: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sorting helpers
# ---------------------------------------------------------------------------

# Presentation order inside a detected group: sources, then resistors,
# inductors, capacitors, then everything else; ties broken by reference.
_PRESENT_RANK: Dict[ComponentType, int] = {
    ComponentType.VOLTAGE_SOURCE: 0,
    ComponentType.BATTERY: 0,
    ComponentType.AC_SOURCE: 0,
    ComponentType.CURRENT_SOURCE: 0,
    ComponentType.RESISTOR: 1,
    ComponentType.POTENTIOMETER: 1,
    ComponentType.INDUCTOR: 2,
    ComponentType.CAPACITOR: 3,
    ComponentType.CAPACITOR_POLARIZED: 3,
}

_POWER_PIN_NAMES = {"V+", "V-", "VS+", "VS-", "VCC", "VDD", "VSS", "VEE",
                    "GND", "VB", "VP", "VN"}


def _ref_key(ref: str) -> Tuple[str, int, str]:
    """Natural sort key for reference designators (R1 < R2 < R10)."""
    prefix = "".join(ch for ch in ref if not ch.isdigit())
    digits = "".join(ch for ch in ref if ch.isdigit())
    return (prefix, int(digits) if digits else 0, ref)


def _pin_key(number: str) -> Tuple[int, int, str]:
    return (0, int(number), number) if number.isdigit() else (1, 0, number)


def _join(items: Sequence[str]) -> str:
    """Join a list into English prose: 'a', 'a and b', 'a, b and c'."""
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze(graph: CircuitGraph) -> CircuitAnalysis:
    """Analyze *graph* and return detected structures (deterministic)."""
    analysis = CircuitAnalysis()
    names = node_names(graph)

    def nname(net_id: int) -> str:
        if net_id < 0:
            return "an unconnected point"
        return names.get(net_id, "an unconnected point")

    def present_key(ref: str) -> Tuple[int, Tuple[str, int, str]]:
        comp = graph.components[ref]
        return (_PRESENT_RANK.get(comp.ctype, 4), _ref_key(ref))

    # Two-terminal components with exactly two distinct, connected nets.
    two_term: Dict[str, Tuple[int, int]] = {}
    for comp in graph.sorted_components():
        if not comp.ctype.is_two_terminal:
            continue
        nets = sorted({p.net_id for p in comp.pins.values() if p.net_id >= 0})
        if len(nets) == 2:
            two_term[comp.ref] = (nets[0], nets[1])

    ground_ids = {n.net_id for n in graph.nets if n.kind == NetKind.GROUND}
    power_ids = {n.net_id for n in graph.nets if n.kind == NetKind.POWER}
    source_ids: Set[int] = set()
    for comp in graph.sorted_components():
        if comp.ctype.is_source:
            source_ids.update(n for n in comp.net_ids() if n >= 0)
    drive_ids = power_ids | source_ids

    # ------------------------------------------------------------------
    # Parallel groups: same unordered pair of nets.
    # ------------------------------------------------------------------
    pair_members: Dict[Tuple[int, int], List[str]] = {}
    for ref in sorted(two_term, key=_ref_key):
        pair_members.setdefault(two_term[ref], []).append(ref)
    for pair in pair_members:
        pair_members[pair].sort(key=present_key)

    for pair in sorted(pair_members):
        refs = pair_members[pair]
        if len(refs) < 2:
            continue
        analysis.parallel_groups.append(list(refs))
        desc = (f"{_join(refs)} are connected in parallel between "
                f"{nname(pair[0])} and {nname(pair[1])}.")
        analysis.structures.append(
            Structure("parallel", desc, list(refs), [pair[0], pair[1]]))

    # ------------------------------------------------------------------
    # Series chains through internal nets of degree exactly two.
    # ------------------------------------------------------------------
    links: Dict[str, List[Tuple[int, str]]] = {ref: [] for ref in two_term}
    for net in graph.nets:
        if len(net.pins) != 2:
            continue
        (ref_a, _pa), (ref_b, _pb) = net.pins
        if ref_a == ref_b or ref_a not in two_term or ref_b not in two_term:
            continue
        if set(two_term[ref_a]) == set(two_term[ref_b]):
            continue  # a parallel pair, not a series junction
        links[ref_a].append((net.net_id, ref_b))
        links[ref_b].append((net.net_id, ref_a))
    for ref in links:
        links[ref].sort(key=lambda t: (t[0], _ref_key(t[1])))

    chains: List[Tuple[List[str], List[int], Optional[Tuple[int, int]]]] = []
    visited: Set[str] = set()

    def outer_net(ref: str) -> int:
        """The net of endpoint *ref* not used by its single series link."""
        used = links[ref][0][0]
        a, b = two_term[ref]
        return b if a == used else a

    for start in sorted(links, key=_ref_key):
        if start in visited or not links[start]:
            continue
        group: Set[str] = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for _net, other in links[cur]:
                if other not in group:
                    group.add(other)
                    stack.append(other)
        visited |= group
        endpoints = [r for r in group if len(links[r]) == 1]
        internals: List[int] = []
        if endpoints:
            first = min(endpoints, key=lambda r: (outer_net(r), _ref_key(r)))
        else:
            first = min(group, key=_ref_key)  # closed loop
        order = [first]
        seen = {first}
        cur = first
        while True:
            advanced = False
            for net_id, other in links[cur]:
                if other in seen:
                    continue
                internals.append(net_id)
                order.append(other)
                seen.add(other)
                cur = other
                advanced = True
                break
            if not advanced:
                break
        if len(order) < 2:
            continue
        boundary: Optional[Tuple[int, int]] = None
        if endpoints:
            boundary = (outer_net(order[0]), outer_net(order[-1]))
        else:
            closing = [n for n, other in links[order[-1]] if other == first]
            if closing:
                internals.append(closing[0])
        chains.append((order, internals, boundary))

    chains.sort(key=lambda c: ((c[2] if c[2] else (10 ** 9, 10 ** 9)),
                               _ref_key(c[0][0])))
    for order, internals, boundary in chains:
        analysis.series_chains.append(list(order))
        joined = _join([nname(n) for n in internals])
        if boundary is not None:
            desc = (f"{_join(order)} are connected in series between "
                    f"{nname(boundary[0])} and {nname(boundary[1])}, "
                    f"joined at {joined}.")
            nets = [boundary[0], boundary[1]]
        else:
            desc = (f"{_join(order)} form a closed series loop, "
                    f"joined at {joined}.")
            nets = list(internals)
        analysis.structures.append(Structure("series", desc, list(order), nets))

    # ------------------------------------------------------------------
    # Voltage dividers: two resistors from a driven net to ground with a
    # tapped midpoint.
    # ------------------------------------------------------------------
    resistors = sorted(
        (r for r in two_term
         if graph.components[r].ctype == ComponentType.RESISTOR),
        key=_ref_key)
    net_degree = {net.net_id: len(net.pins) for net in graph.nets}

    for i, ref_a in enumerate(resistors):
        for ref_b in resistors[i + 1:]:
            shared = set(two_term[ref_a]) & set(two_term[ref_b])
            if len(shared) != 1:
                continue
            mid = shared.pop()
            other_a = [n for n in two_term[ref_a] if n != mid][0]
            other_b = [n for n in two_term[ref_b] if n != mid][0]
            for top_ref, top, bot_ref, bot in (
                    (ref_a, other_a, ref_b, other_b),
                    (ref_b, other_b, ref_a, other_a)):
                if (top in drive_ids and top not in ground_ids
                        and bot in ground_ids):
                    tapped = (net_degree.get(mid, 0) >= 3
                              or graph.nets[mid].kind == NetKind.NAMED)
                    if tapped:
                        desc = (f"{top_ref} and {bot_ref} form a voltage "
                                f"divider between {nname(top)} and ground, "
                                f"with the output tapped at {nname(mid)}.")
                        analysis.structures.append(Structure(
                            "voltage_divider", desc,
                            [top_ref, bot_ref], [top, mid, bot]))
                    break

    # ------------------------------------------------------------------
    # First-order filters: a series element into a shunt element to ground.
    # ------------------------------------------------------------------
    def _refs_of(*ctypes: ComponentType) -> List[str]:
        return sorted((r for r in two_term
                       if graph.components[r].ctype in ctypes), key=_ref_key)

    caps = _refs_of(ComponentType.CAPACITOR, ComponentType.CAPACITOR_POLARIZED)
    inductors = _refs_of(ComponentType.INDUCTOR)

    def find_filters(series_refs: List[str], shunt_refs: List[str],
                     kind: str, label: str) -> None:
        for ser in series_refs:
            net_a, net_b = two_term[ser]
            for shunt in shunt_refs:
                if shunt == ser:
                    continue
                x, y = two_term[shunt]
                if x in ground_ids and y not in ground_ids:
                    junction = y
                elif y in ground_ids and x not in ground_ids:
                    junction = x
                else:
                    continue
                if junction not in (net_a, net_b) or junction in power_ids:
                    continue
                inp = net_b if junction == net_a else net_a
                if inp in ground_ids:
                    continue
                desc = (f"{ser} and {shunt} form an {label} with input at "
                        f"{nname(inp)} and output at {nname(junction)}.")
                analysis.structures.append(Structure(
                    kind, desc, [ser, shunt], [inp, junction]))

    find_filters(resistors, caps, "rc_low_pass", "RC low-pass filter")
    find_filters(caps, resistors, "rc_high_pass", "RC high-pass filter")
    find_filters(inductors, resistors, "rl_low_pass", "RL low-pass filter")
    find_filters(resistors, inductors, "rl_high_pass", "RL high-pass filter")

    # ------------------------------------------------------------------
    # Wheatstone bridge: four arms between two excitation nets via two
    # distinct midpoint nets.
    # ------------------------------------------------------------------
    excitation = sorted(ground_ids | drive_ids)
    all_net_ids = sorted({n for pair in two_term.values() for n in pair})
    for i, top in enumerate(excitation):
        for bottom in excitation[i + 1:]:
            mids = []
            for mid in all_net_ids:
                if mid in (top, bottom):
                    continue
                upper = pair_members.get((min(top, mid), max(top, mid)))
                lower = pair_members.get((min(mid, bottom), max(mid, bottom)))
                if upper and lower:
                    mids.append((mid, upper[0], lower[0]))
            if len(mids) >= 2:
                (m1, up1, low1), (m2, up2, low2) = mids[0], mids[1]
                refs = [up1, low1, up2, low2]
                desc = (f"{_join(refs)} form a Wheatstone bridge between "
                        f"{nname(top)} and {nname(bottom)}, with bridge "
                        f"midpoints at {nname(m1)} and {nname(m2)}.")
                analysis.structures.append(Structure(
                    "wheatstone", desc, refs, [top, bottom, m1, m2]))

    # ------------------------------------------------------------------
    # Op-amp configurations.
    # ------------------------------------------------------------------
    for comp in graph.sorted_components():
        if comp.ctype != ComponentType.OPAMP:
            continue
        structure = _analyze_opamp(graph, comp, two_term, ground_ids,
                                   present_key, nname, analysis.notes)
        if structure is not None:
            analysis.structures.append(structure)

    # ------------------------------------------------------------------
    # Logic gates: one structure listing every gate with its nets.
    # ------------------------------------------------------------------
    gates = [c for c in graph.sorted_components() if c.ctype.is_gate]
    if gates:
        parts: List[str] = []
        for gate in gates:
            ins, outs = _gate_pins(gate)
            in_names = _join([nname(p.net_id) for p in ins]) or "unknown"
            out_names = _join([nname(p.net_id) for p in outs]) or "unknown"
            in_word = "input" if len(ins) == 1 else "inputs"
            parts.append(f"{gate.ref} ({gate.ctype.value}) with {in_word} "
                         f"{in_names} and output {out_names}")
        count = len(gates)
        gate_word = "logic gate" if count == 1 else "logic gates"
        desc = f"The circuit contains {count} {gate_word}: " + "; ".join(
            parts) + "."
        analysis.structures.append(Structure(
            "logic", desc, [g.ref for g in gates], []))

    # ------------------------------------------------------------------
    # Power rails.
    # ------------------------------------------------------------------
    analysis.power_rails = sorted(
        {n.name for n in graph.nets if n.kind == NetKind.POWER})

    return analysis


# ---------------------------------------------------------------------------
# Op-amp helpers
# ---------------------------------------------------------------------------

def _is_power_pin(pin: PinConnection) -> bool:
    return (pin.etype in ("power_in", "power_out")
            or pin.name.strip().upper() in _POWER_PIN_NAMES)


def _opamp_pins(comp: Component) -> Tuple[Optional[PinConnection],
                                          Optional[PinConnection],
                                          Optional[PinConnection]]:
    """Return the (inverting, non-inverting, output) pins, best effort."""
    signal = [comp.pins[k] for k in sorted(comp.pins, key=_pin_key)
              if not _is_power_pin(comp.pins[k])]
    minus = next((p for p in signal if "-" in p.name), None)
    plus = next((p for p in signal if "+" in p.name), None)
    out = next((p for p in signal
                if p.etype == "output" or "OUT" in p.name.upper()), None)
    if out is None:
        remaining = [p for p in signal if p is not minus and p is not plus]
        if len(remaining) == 1:
            out = remaining[0]
    return minus, plus, out


def _analyze_opamp(graph: CircuitGraph, comp: Component,
                   two_term: Dict[str, Tuple[int, int]],
                   ground_ids: Set[int], present_key, nname,
                   notes: List[str]) -> Optional[Structure]:
    """Classify one op-amp as follower / inverting / non-inverting."""
    minus, plus, out = _opamp_pins(comp)
    if (minus is None or plus is None or out is None
            or min(minus.net_id, plus.net_id, out.net_id) < 0):
        notes.append(f"Could not identify the input and output pins of "
                     f"operational amplifier {comp.ref}.")
        return None
    m_net, p_net, o_net = minus.net_id, plus.net_id, out.net_id

    if o_net == m_net:
        desc = (f"{comp.ref} is configured as a voltage follower "
                f"(unity-gain buffer) with input at {nname(p_net)} and "
                f"output at {nname(o_net)}.")
        return Structure("opamp_follower", desc, [comp.ref], [p_net, o_net])

    feedback = sorted((r for r, nets in two_term.items()
                       if set(nets) == {o_net, m_net}), key=present_key)
    if not feedback:
        notes.append(f"No feedback path found for operational amplifier "
                     f"{comp.ref}; its configuration was not recognized.")
        return None
    fb = feedback[0]

    input_legs: List[Tuple[str, int]] = []
    ground_legs: List[str] = []
    for ref in sorted(two_term, key=present_key):
        if ref == fb:
            continue
        a, b = two_term[ref]
        if m_net not in (a, b):
            continue
        other = b if a == m_net else a
        if other == o_net:
            continue
        if other in ground_ids:
            ground_legs.append(ref)
        else:
            input_legs.append((ref, other))

    if p_net in ground_ids and input_legs:
        r_in, src = input_legs[0]
        desc = (f"{comp.ref} is configured as an inverting amplifier: the "
                f"signal enters through {r_in} from {nname(src)} into the "
                f"inverting input, {fb} provides feedback from the output "
                f"at {nname(o_net)}, and the non-inverting input is "
                f"grounded.")
        return Structure("opamp_inverting", desc, [comp.ref, r_in, fb],
                         [src, o_net])
    if p_net not in ground_ids and ground_legs:
        r_g = ground_legs[0]
        desc = (f"{comp.ref} is configured as a non-inverting amplifier: "
                f"the signal drives the non-inverting input at "
                f"{nname(p_net)}, {fb} provides feedback from the output "
                f"at {nname(o_net)}, and {r_g} connects the inverting "
                f"input to ground.")
        return Structure("opamp_non_inverting", desc, [comp.ref, r_g, fb],
                         [p_net, o_net])

    notes.append(f"Operational amplifier {comp.ref} has feedback through "
                 f"{fb} but its configuration was not recognized.")
    return None


# ---------------------------------------------------------------------------
# Logic-gate helpers
# ---------------------------------------------------------------------------

_GATE_INPUT_NAMES = {"A", "B", "C", "D", "E"}
_GATE_OUTPUT_NAMES = {"Y", "Q", "Z", "OUT", "~Y", "O"}


def _gate_pins(comp: Component) -> Tuple[List[PinConnection],
                                         List[PinConnection]]:
    """Split a gate's pins into (inputs, outputs), best effort."""
    ins: List[PinConnection] = []
    outs: List[PinConnection] = []
    for key in sorted(comp.pins, key=_pin_key):
        pin = comp.pins[key]
        if _is_power_pin(pin):
            continue
        name = pin.name.strip().upper()
        if pin.etype == "input" or name in _GATE_INPUT_NAMES \
                or name.startswith("IN"):
            ins.append(pin)
        elif pin.etype == "output" or name in _GATE_OUTPUT_NAMES \
                or "OUT" in name:
            outs.append(pin)
    return ins, outs

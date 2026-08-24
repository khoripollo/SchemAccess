"""Alt-text generator: natural-language circuit descriptions.

Turns a :class:`~schemaccess.model.CircuitGraph` into structured prose for
blind and low-vision readers.  Three detail levels are supported:

* ``short``    - element/node counts plus a one-sentence component list;
* ``standard`` - counts, parallel groups, series chains, remaining
  connections and source polarity;
* ``detailed`` - everything in standard plus detected structures (dividers,
  filters, bridges, op-amp configurations, logic gates, power rails), a
  per-component connection listing and any graph warnings.

Output is deterministic: identical graphs always yield identical text.
Sentences are emitted one per line with no trailing whitespace.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from . import analyzer
from .model import (CircuitGraph, Component, ComponentType, NetKind,
                    PinConnection)
from .netbuilder import node_names

# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------

_UNIT_FOR_TYPE: Dict[ComponentType, str] = {
    ComponentType.RESISTOR: "ohm",
    ComponentType.POTENTIOMETER: "ohm",
    ComponentType.CAPACITOR: "farad",
    ComponentType.CAPACITOR_POLARIZED: "farad",
    ComponentType.INDUCTOR: "henry",
    ComponentType.VOLTAGE_SOURCE: "volt",
    ComponentType.BATTERY: "volt",
    ComponentType.AC_SOURCE: "volt",
    ComponentType.CURRENT_SOURCE: "ampere",
}

# Metric prefixes.  Only 'm' vs 'M' is case-sensitive (milli vs mega);
# every other letter is accepted in either case.
_PREFIX_WORDS = {"T": "tera", "G": "giga", "K": "kilo",
                 "U": "micro", "µ": "micro", "N": "nano", "P": "pico"}

# Unit tokens that may trail the value string; longest first so that
# 'ohm' wins over the single letter forms.
_UNIT_TOKENS = ("ohms", "ohm", "farads", "farad", "henries", "henry",
                "volts", "volt", "amperes", "ampere", "amps", "amp",
                "Ω", "F", "H", "V", "A", "R")

_NUMBER_RE = re.compile(r"^(\d+(?:[.,]\d+)?|[.,]\d+)\s*(.*)$")
_RKM_RE = re.compile(r"^([A-Za-zµΩ]+)(\d+)$")


def _prefix_word(token: str) -> Optional[str]:
    """Return the prefix word for a metric-prefix token ('' for none)."""
    token = token.strip()
    if token == "":
        return ""
    if token.upper() == "MEG":
        return "mega"
    if len(token) == 1:
        if token == "m":
            return "milli"
        if token == "M":
            return "mega"
        return _PREFIX_WORDS.get(token.upper())
    return None


def format_value(value: str, ctype: ComponentType) -> Optional[str]:
    """Turn a KiCad value string into words, using *ctype* for the unit.

    Examples: ``'100'`` (resistor) -> ``'100 Ohm'``; ``'4.7k'`` ->
    ``'4.7 kiloohm'``; ``'22nF'`` -> ``'22 nanofarad'``; ``'5V'`` ->
    ``'5 Volt'``; ``'1MEG'`` -> ``'1 megaohm'``; ``'4k7'`` ->
    ``'4.7 kiloohm'``.  Returns ``None`` when the component type has no
    natural unit or the value is missing/placeholder ('R', 'C', '~', '?',
    '') or unparseable, so callers can simply omit it.
    """
    unit = _UNIT_FOR_TYPE.get(ctype)
    if unit is None:
        return None
    text = (value or "").strip()
    if not text or not any(ch.isdigit() for ch in text):
        return None
    match = _NUMBER_RE.match(text)
    if match is None:
        return None
    number = match.group(1).replace(",", ".")
    if number.startswith("."):
        number = "0" + number
    rest = match.group(2).strip()

    rkm = _RKM_RE.match(rest)
    if rkm is not None:
        letter, fraction = rkm.groups()
        prefix = "" if letter in ("R", "r", "Ω") else _prefix_word(letter)
        if prefix is None:
            return None
        number = f"{number}.{fraction}"
    else:
        prefix = None
        for token in _UNIT_TOKENS:
            if rest.lower().endswith(token.lower()):
                candidate = _prefix_word(rest[:len(rest) - len(token)])
                if candidate is not None:
                    prefix = candidate
                    break
        if prefix is None:
            prefix = _prefix_word(rest)
        if prefix is None:
            return None

    if prefix == "":
        return f"{number} {unit.capitalize()}"
    return f"{number} {prefix}{unit}"


# ---------------------------------------------------------------------------
# Phrase helpers
# ---------------------------------------------------------------------------

# First words that take 'an' despite starting with a consonant letter.
_AN_FIRST_WORDS = {"LED", "NPN", "N-channel", "XOR", "XNOR", "RC", "RL"}


def _article(phrase: str) -> str:
    """Choose 'a' or 'an' for *phrase*."""
    first = phrase.split()[0] if phrase.split() else phrase
    if first in _AN_FIRST_WORDS:
        return "an"
    if phrase[:1] in "aeiouAEIOU8":
        return "an"
    return "a"


def _join(items: Sequence[str]) -> str:
    """Join a list into English prose: 'a', 'a and b', 'a, b and c'."""
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _cap(sentence: str) -> str:
    """Upper-case the first character without touching the rest."""
    return sentence[:1].upper() + sentence[1:] if sentence else sentence


def _pin_key(number: str) -> Tuple[int, int, str]:
    return (0, int(number), number) if number.isdigit() else (1, 0, number)


_PLACEHOLDER_PIN_NAMES = {"", "~", "?"}


def _component_phrase(comp: Component) -> str:
    """'a 100 Ohm resistor labelled R2' / 'a resistor labelled R3'."""
    formatted = format_value(comp.value, comp.ctype)
    type_word = comp.ctype.value
    core = f"{formatted} {type_word}" if formatted else type_word
    return f"{_article(core)} {core} labelled {comp.ref}"


#: Types whose dot marks winding phase rather than terminal polarity.
_WINDING_TYPES = (ComponentType.INDUCTOR, ComponentType.TRANSFORMER)

#: Terminal names for three-terminal devices, by pin name.
_BJT_TERMINALS = {"C": "its collector", "B": "its base", "E": "its emitter"}
_FET_TERMINALS = {"D": "its drain", "G": "its gate", "S": "its source"}


def _pin_label(comp: Component, pin: PinConnection) -> str:
    """Human name for a pin inside a multi-pin component sentence."""
    name = pin.name.strip()
    upper = name.upper()
    if comp.ctype.is_transistor:
        table = (_BJT_TERMINALS
                 if comp.ctype in (ComponentType.TRANSISTOR_NPN,
                                   ComponentType.TRANSISTOR_PNP)
                 else _FET_TERMINALS)
        if upper in table:
            return table[upper]
    if comp.ctype == ComponentType.TRANSFORMER and len(upper) == 2:
        winding = {"A": "primary", "S": "secondary"}.get(upper[0])
        if winding:
            end = "start" if upper[1] == "A" else "end"
            return f"the {end} of its {winding} winding"
    if comp.ctype == ComponentType.OPAMP:
        if upper in ("V+", "VS+", "VCC", "VDD"):
            return "its positive supply pin"
        if upper in ("V-", "VS-", "VSS", "VEE"):
            return "its negative supply pin"
        if "-" in name:
            return "its inverting input"
        if "+" in name:
            return "its non-inverting input"
        if pin.etype == "output" or "OUT" in upper:
            return "its output"
    if name not in _PLACEHOLDER_PIN_NAMES:
        return f"pin {pin.number} ({name})"
    return f"pin {pin.number}"


def _distinct_nets(comp: Component) -> List[int]:
    return sorted({p.net_id for p in comp.pins.values() if p.net_id >= 0})


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _counts_sentence(graph: CircuitGraph) -> str:
    n_elements = len(graph.components)
    n_nodes = sum(1 for net in graph.nets
                  if len(net.pins) >= 2
                  or (net.kind == NetKind.GROUND and net.pins))
    verb = "is" if n_elements == 1 else "are"
    element_word = "element" if n_elements == 1 else "elements"
    node_word = "node" if n_nodes == 1 else "nodes"
    return (f"There {verb} {n_elements} {element_word} and {n_nodes} "
            f"{node_word} in the circuit.")


def _chain_layout(graph: CircuitGraph, chain: List[str]
                  ) -> Tuple[Optional[int], Optional[int], List[int]]:
    """Boundary nets and internal nets of a series chain, in chain order.

    Returns ``(None, None, internals)`` for a closed loop.
    """
    net_sets = [set(_distinct_nets(graph.components[r])) for r in chain]
    internals: List[int] = []
    for i in range(len(chain) - 1):
        shared = sorted(net_sets[i] & net_sets[i + 1])
        internals.append(shared[0] if shared else -1)
    if len(chain) >= 3:
        closing = sorted((net_sets[0] & net_sets[-1]) - set(internals))
        if closing:
            return None, None, internals + [closing[0]]
    first = sorted(net_sets[0] - {internals[0]})
    last = sorted(net_sets[-1] - {internals[-1]})
    first_outer = first[0] if first else internals[0]
    last_outer = last[0] if last else internals[-1]
    return first_outer, last_outer, internals


def _standard_lines(graph: CircuitGraph,
                    analysis: "analyzer.CircuitAnalysis",
                    nname) -> List[str]:
    lines = [_counts_sentence(graph)]
    covered = set()

    # Parallel groups first.
    for group in analysis.parallel_groups:
        comps = [graph.components[r] for r in group]
        nets = _distinct_nets(comps[0])
        between = f"Between {nname(nets[0])} and {nname(nets[1])}"
        phrases = [_component_phrase(c) for c in comps]
        if len(phrases) == 2:
            lines.append(f"{between}, {phrases[0]} is connected in "
                         f"parallel with {phrases[1]}.")
        else:
            lines.append(f"{between}, {_join(phrases)} are connected "
                         f"in parallel.")
        covered.update(group)

    # Then series chains.
    for chain in analysis.series_chains:
        comps = [graph.components[r] for r in chain]
        phrases = [_component_phrase(c) for c in comps]
        first_outer, last_outer, internals = _chain_layout(graph, chain)
        joined_nodes = _join([nname(n) for n in internals])
        if first_outer is None:
            lines.append(f"{_cap(_join(phrases))} are connected in a "
                         f"series loop, joined at {joined_nodes}.")
        else:
            lines.append(f"Between {nname(first_outer)} and "
                         f"{nname(last_outer)}, {phrases[0]} is connected "
                         f"in series with {_join(phrases[1:])}, these "
                         f"elements are connected at {joined_nodes}.")
        covered.update(chain)

    # Remaining components.
    for comp in graph.sorted_components():
        if comp.ref in covered:
            continue
        nets = _distinct_nets(comp)
        if comp.ctype.is_two_terminal:
            if len(nets) == 2:
                lines.append(f"Between {nname(nets[0])} and "
                             f"{nname(nets[1])}, {_component_phrase(comp)} "
                             f"is connected.")
            elif len(nets) == 1:
                lines.append(f"{_cap(_component_phrase(comp))} has both "
                             f"terminals connected to {nname(nets[0])}.")
            else:
                lines.append(f"{_cap(_component_phrase(comp))} is not "
                             f"connected to anything.")
        else:
            parts = []
            for key in sorted(comp.pins, key=_pin_key):
                pin = comp.pins[key]
                parts.append(f"{_pin_label(comp, pin)} connected to "
                             f"{nname(pin.net_id)}")
            if parts:
                lines.append(f"The {comp.ctype.value} labelled {comp.ref} "
                             f"has {_join(parts)}.")
            else:
                lines.append(f"The {comp.ctype.value} labelled {comp.ref} "
                             f"has no pins.")

    # Source polarity sentences.
    sources = [c for c in graph.sorted_components() if c.ctype.is_source]
    for comp in sources:
        pins = [comp.pins[k] for k in sorted(comp.pins, key=_pin_key)]
        pos = next((p for p in pins if "+" in p.name), None)
        if pos is None:
            pos = next((p for p in pins if p.number == "1"), None)
        neg = next((p for p in pins if "-" in p.name and p is not pos), None)
        if neg is None:
            neg = next((p for p in pins if p is not pos), None)
        if pos is None or neg is None:
            continue
        label = "" if len(sources) == 1 else f" labelled {comp.ref}"
        lines.append(f"The positive terminal of the {comp.ctype.value}"
                     f"{label} is connected to {nname(pos.net_id)} and the "
                     f"negative terminal is connected to "
                     f"{nname(neg.net_id)}.")
    return lines


def _short_lines(graph: CircuitGraph) -> List[str]:
    lines = [_counts_sentence(graph)]
    comps = graph.sorted_components()
    if comps:
        phrases = [_component_phrase(c) for c in comps]
        lines.append(f"The circuit contains {_join(phrases)}.")
    else:
        lines.append("The circuit contains no components.")
    return lines


_DETAILED_STRUCTURE_KINDS = (
    "voltage_divider", "rc_low_pass", "rc_high_pass",
    "rl_low_pass", "rl_high_pass", "wheatstone",
    "opamp_inverting", "opamp_non_inverting", "opamp_follower", "logic")


def _detailed_lines(graph: CircuitGraph,
                    analysis: "analyzer.CircuitAnalysis",
                    nname) -> List[str]:
    lines = _standard_lines(graph, analysis, nname)

    extras = [s for s in analysis.structures
              if s.kind in _DETAILED_STRUCTURE_KINDS]
    if extras or analysis.power_rails:
        lines.append("Detected structures:")
        for structure in extras:
            lines.append(structure.description)
        if analysis.power_rails:
            rail_word = ("Power rail" if len(analysis.power_rails) == 1
                         else "Power rails")
            lines.append(f"{rail_word}: {_join(analysis.power_rails)}.")

    if graph.components:
        lines.append("Connections by component:")
        for comp in graph.sorted_components():
            parts = []
            for key in sorted(comp.pins, key=_pin_key):
                pin = comp.pins[key]
                name = pin.name.strip()
                tag = (f"pin {pin.number} ({name})"
                       if name not in _PLACEHOLDER_PIN_NAMES
                       else f"pin {pin.number}")
                parts.append(f"{tag} to {nname(pin.net_id)}")
            listing = "; ".join(parts) if parts else "no pins"
            lines.append(f"{comp.ref} ({comp.ctype.value}): {listing}.")

    # A polarity dot is information a sighted reader gets for free from the
    # drawing, so it has to be stated for a screen-reader user.
    dotted = [c for c in graph.sorted_components() if c.dots]
    if dotted:
        for comp in dotted:
            marker = ("winding-phase dot" if comp.ctype in _WINDING_TYPES
                      else "polarity dot")
            lines.append(
                f"The {comp.ctype.value} labelled {comp.ref} is marked with "
                f"a {marker}.")

    messages = list(analysis.notes) + list(graph.warnings)
    if messages:
        lines.append("Warnings:")
        lines.extend(messages)
    return lines


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate(graph: CircuitGraph, detail: str = "standard") -> str:
    """Return a structured natural-language description of *graph*.

    *detail* is one of ``short``, ``standard``, ``detailed`` (anything
    else falls back to ``standard``).  Output is deterministic for
    identical inputs; sentences are separated by newlines and carry no
    trailing whitespace.
    """
    level = (detail or "standard").strip().lower()
    names = node_names(graph)

    def nname(net_id: int) -> str:
        if net_id < 0:
            return "an unconnected point"
        return names.get(net_id, "an unconnected point")

    if level == "short":
        lines = _short_lines(graph)
    elif level == "detailed":
        analysis = analyzer.analyze(graph)
        lines = _detailed_lines(graph, analysis, nname)
    else:
        analysis = analyzer.analyze(graph)
        lines = _standard_lines(graph, analysis, nname)
    return "\n".join(line.rstrip() for line in lines)

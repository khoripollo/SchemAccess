"""Unit symbols after component values: 1 -> 1 H, 22n -> 22 nF."""

from __future__ import annotations

import dataclasses
import re

from .model import CircuitGraph, ComponentType

__all__ = ["UNITS", "with_unit", "apply"]

UNITS = {
    ComponentType.RESISTOR: "Ω",
    ComponentType.POTENTIOMETER: "Ω",
    ComponentType.CAPACITOR: "F",
    ComponentType.CAPACITOR_POLARIZED: "F",
    ComponentType.INDUCTOR: "H",
    ComponentType.VOLTAGE_SOURCE: "V",
    ComponentType.BATTERY: "V",
    ComponentType.AC_SOURCE: "V",
    ComponentType.CURRENT_SOURCE: "A",
}

_PREFIX = {"": "", "p": "p", "n": "n", "u": "µ", "µ": "µ", "μ": "µ",
           "m": "m", "k": "k", "K": "k", "M": "M", "meg": "M", "Meg": "M",
           "MEG": "M", "G": "G", "R": ""}
_PLAIN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(meg|Meg|MEG|[pnuµμmkKMG])?\s*"
                    r"([Oo]hms?|OHMS?|Ω|Ω|[FHVA])?")
_RKM = re.compile(r"(\d+)([RkKmMuµμnp])(\d+)")

#: siunitx spellings, for the LaTeX drawing.  circuitikz is loaded with
#: its ``siunitx`` option, so a value can be typeset as a real quantity -
#: ``\SI{4.7}{\kilo\ohm}`` - rather than as hand-set characters, which is
#: what gets the spacing and the upright unit letters right.
_SI_UNIT = {"Ω": r"\ohm", "F": r"\farad", "H": r"\henry",
            "V": r"\volt", "A": r"\ampere"}
_SI_PREFIX = {"": "", "p": r"\pico", "n": r"\nano", "µ": r"\micro",
              "m": r"\milli", "k": r"\kilo", "M": r"\mega", "G": r"\giga"}


def quantity(value: str, kind: ComponentType):
    """Split *value* into ``(number, prefix, unit)``, or ``None``.

    Accepts what an engineer actually types in KiCad: ``100``, ``4k7``,
    ``1uF``, ``2.5H``, ``100 Ohm``.  Returns ``None`` for anything that is
    not a quantity for this kind of part - a part number, a blank, a
    value whose unit contradicts the symbol.
    """
    unit = UNITS.get(kind)
    text = (value or "").strip()
    if unit is None:
        return None
    rkm = _RKM.fullmatch(text)
    plain = None if rkm else _PLAIN.fullmatch(text)
    if not (rkm or plain):
        return None
    if rkm:
        number, prefix, given = f"{rkm[1]}.{rkm[3]}", rkm[2], None
    else:
        number, prefix, given = plain[1], plain[2] or "", plain[3]
    if given and ("Ω" if given[0] in "OoΩΩ" else given) != unit:
        return None
    return number, _PREFIX[prefix], unit


def with_unit(value: str, kind: ComponentType) -> str:
    """Normalise *value* for a *kind* of component: ``1uF`` -> ``1 µF``.

    A plain number gets its unit too: a resistor marked ``100`` is 100
    ohms, the same way ``10m`` on an inductor is 10 millihenries.  Values
    that are not quantities come back untouched.
    """
    parsed = quantity(value, kind)
    if parsed is None:
        return value
    number, prefix, unit = parsed
    return f"{number} {prefix}{unit}"


def siunitx(value: str, kind: ComponentType):
    """*value* as a siunitx quantity, or ``None`` if it is not one.

    ``4k7`` on a resistor becomes ``\\SI{4.7}{\\kilo\\ohm}``.
    """
    parsed = quantity(value, kind)
    if parsed is None:
        return None
    number, prefix, unit = parsed
    macro = _SI_UNIT.get(unit)
    if macro is None:
        return None
    return "\\SI{%s}{%s%s}" % (number.replace(",", "."),
                               _SI_PREFIX.get(prefix, ""), macro)


def apply(graph: CircuitGraph) -> CircuitGraph:
    """A copy of *graph* whose values carry units, for drawing only."""
    parts = {ref: dataclasses.replace(comp,
                                      value=with_unit(comp.value, comp.ctype))
             for ref, comp in graph.components.items()}
    return dataclasses.replace(graph, components=parts)

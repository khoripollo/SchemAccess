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


def with_unit(value: str, kind: ComponentType) -> str:
    unit = UNITS.get(kind)
    text = (value or "").strip()
    rkm = _RKM.fullmatch(text)
    plain = None if rkm else _PLAIN.fullmatch(text)
    if unit is None or not (rkm or plain):
        return value
    if rkm:
        number, prefix, given = f"{rkm[1]}.{rkm[3]}", rkm[2], None
    else:
        number, prefix, given = plain[1], plain[2] or "", plain[3]
    if given and ("Ω" if given[0] in "OoΩΩ" else given) != unit:
        return value
    return f"{number} {_PREFIX[prefix]}{unit}"


def apply(graph: CircuitGraph) -> CircuitGraph:
    """A copy of *graph* whose values carry units, for drawing only."""
    parts = {ref: dataclasses.replace(comp,
                                      value=with_unit(comp.value, comp.ctype))
             for ref, comp in graph.components.items()}
    return dataclasses.replace(graph, components=parts)
